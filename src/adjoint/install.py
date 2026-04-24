"""Install/uninstall logic for Claude Code settings.json merging.

``adjoint install`` is the only command that mutates ``.claude/settings.json``.
It is idempotent: running twice produces the same file. A ``.bak.<unix-ts>``
backup is created whenever the target file exists and is non-empty.

Merge semantics:
* ``hooks.<Event>`` is a list. We append an adjoint entry if one is not
  already present (detected by matching the ``adjoint-hook-*`` command
  substring). User's existing entries are preserved.
* ``mcpServers`` is a dict. We set the ``adjoint`` key; other servers are
  preserved. With ``--force`` we overwrite an existing ``adjoint`` entry.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import bundled_dir, claude_settings_path, user_paths


@dataclass
class InstallPlan:
    target: Path
    backup: Path | None
    hooks_added: list[str] = field(default_factory=list)
    hooks_skipped: list[str] = field(default_factory=list)
    mcp_added: bool = False
    mcp_skipped: bool = False
    adjoint_home_created: bool = False
    migrations_applied: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _backup(path: Path) -> Path | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    stamp = int(time.time())
    bak = path.with_suffix(path.suffix + f".bak.{stamp}")
    shutil.copy2(path, bak)
    return bak


def _entry_mentions_adjoint(entry: dict[str, Any]) -> bool:
    for item in entry.get("hooks", []) or []:
        cmd = item.get("command", "") or ""
        if "adjoint-hook-" in cmd:
            return True
    return False


def _merge_hooks(
    existing: dict[str, Any],
    bundled: dict[str, Any],
    force: bool,
) -> tuple[dict[str, Any], list[str], list[str]]:
    added: list[str] = []
    skipped: list[str] = []
    out_hooks: dict[str, Any] = dict(existing.get("hooks", {}))
    for event, new_entries in bundled.get("hooks", {}).items():
        cur = list(out_hooks.get(event, []))
        has_adjoint = any(_entry_mentions_adjoint(e) for e in cur if isinstance(e, dict))
        if has_adjoint and not force:
            skipped.append(event)
            continue
        if has_adjoint and force:
            cur = [e for e in cur if not (isinstance(e, dict) and _entry_mentions_adjoint(e))]
        cur.extend(new_entries)
        out_hooks[event] = cur
        added.append(event)
    result = dict(existing)
    result["hooks"] = out_hooks
    return result, added, skipped


def _merge_mcp(
    existing: dict[str, Any],
    bundled: dict[str, Any],
    force: bool,
) -> tuple[dict[str, Any], bool, bool]:
    out_servers: dict[str, Any] = dict(existing.get("mcpServers", {}))
    new_servers: dict[str, Any] = bundled.get("mcpServers", {})
    added = False
    skipped = False
    for name, cfg in new_servers.items():
        if name in out_servers and not force:
            skipped = True
            continue
        out_servers[name] = cfg
        added = True
    result = dict(existing)
    if out_servers:
        result["mcpServers"] = out_servers
    return result, added, skipped


def build_install_plan(
    scope: str,
    project_path: Path | None = None,
    *,
    force: bool = False,
) -> tuple[InstallPlan, dict[str, Any]]:
    target = claude_settings_path(scope, project_path)
    existing = _read_json(target)

    bundled = bundled_dir()
    hooks_bundle = json.loads((bundled / "settings.hooks.json").read_text(encoding="utf-8"))
    mcp_bundle = json.loads((bundled / "settings.mcp.json").read_text(encoding="utf-8"))

    merged, h_added, h_skipped = _merge_hooks(existing, hooks_bundle, force)
    merged, m_added, m_skipped = _merge_mcp(merged, mcp_bundle, force)

    plan = InstallPlan(
        target=target,
        backup=None,
        hooks_added=h_added,
        hooks_skipped=h_skipped,
        mcp_added=m_added,
        mcp_skipped=m_skipped,
    )
    return plan, merged


def apply_install(plan: InstallPlan, merged: dict[str, Any]) -> InstallPlan:
    # 1. Prepare ~/.adjoint/ layout + migrations *before* wiring hooks so there
    # is no runtime race between migration and first hook insert.
    up = user_paths()
    created = not up.root.exists()
    up.ensure()

    # Copy bundled policies into disabled/ (idempotent).
    src_policies = bundled_dir() / "policies"
    if src_policies.is_dir():
        for py in src_policies.glob("*.py"):
            dest = up.policies_disabled / py.name
            if not dest.exists():
                shutil.copy2(py, dest)

    from .store.sqlite import run_migrations

    applied = run_migrations()

    # 2. Back up existing settings.json (only if content will change) and write.
    existing_text = plan.target.read_text(encoding="utf-8") if plan.target.is_file() else ""
    new_text = json.dumps(merged, indent=2) + "\n"
    if existing_text.strip() != new_text.strip():
        plan.backup = _backup(plan.target)
        _write_json(plan.target, merged)

    plan.adjoint_home_created = created
    plan.migrations_applied = applied
    return plan
