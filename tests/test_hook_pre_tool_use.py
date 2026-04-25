"""End-to-end subprocess tests for the PreToolUse hook.

Exercises the real ``adjoint-hook-pre-tool-use`` binary, dropping policy files
into ``$ADJOINT_HOME/policies/enabled/`` and asserting on the JSON emitted on
stdout.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

HOOK_BIN = "adjoint-hook-pre-tool-use"


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body).lstrip(), encoding="utf-8")


def _payload(project_dir: Path, tool_name: str, tool_input: dict) -> str:
    return json.dumps(
        {
            "session_id": "sess-1",
            "transcript_path": str(project_dir / "nonexistent.jsonl"),
            "cwd": str(project_dir),
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def test_resolve_policies_dir_default_honors_adjoint_home(adjoint_home: Path) -> None:
    """Default config string routes through user_paths(); ADJOINT_HOME is honored."""
    from adjoint.config import PoliciesConfig
    from adjoint.hooks.pre_tool_use import _resolve_policies_dir
    from adjoint.paths import user_paths

    default = PoliciesConfig.model_fields["dir"].default
    assert _resolve_policies_dir(default, Path("/tmp")) == user_paths().policies_enabled
    assert str(adjoint_home) in str(_resolve_policies_dir(default, Path("/tmp")))


def test_resolve_policies_dir_absolute_override(tmp_path: Path) -> None:
    """Absolute override is expanduser'd and returned verbatim."""
    from adjoint.hooks.pre_tool_use import _resolve_policies_dir

    target = tmp_path / "custom-policies"
    assert _resolve_policies_dir(str(target), Path("/somewhere/else")) == target
    assert _resolve_policies_dir("~/some-custom", Path("/tmp")).is_absolute()


def test_resolve_policies_dir_relative_anchored_to_project(tmp_path: Path) -> None:
    """Relative override resolves against the project cwd, not the hook's cwd."""
    from adjoint.hooks.pre_tool_use import _resolve_policies_dir

    project = tmp_path / "proj"
    project.mkdir()
    resolved = _resolve_policies_dir("custompol", project)
    assert resolved == (project / "custompol").resolve()
    assert resolved.is_absolute()


def test_no_policies_dir_is_passthrough(project_dir: Path, run_hook_bin) -> None:
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_deny_policy_emits_permission_deny(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    _write(
        adjoint_home / "policies" / "enabled" / "deny_all.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="test deny")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"] == "test deny"


def test_ask_policy_emits_permission_ask(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    _write(
        adjoint_home / "policies" / "enabled" / "ask_all.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="ask", reason="confirm?")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "confirm?"


def test_allow_policy_emits_permission_allow(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """An explicit ``allow`` proactively approves the call so Claude Code
    skips its normal user-confirm UI — that's the documented intent of
    starter policies like ``safe_bash`` allowing non-dangerous Bash."""
    _write(
        adjoint_home / "policies" / "enabled" / "allow_all.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow", reason="trusted")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert hso["permissionDecisionReason"] == "trusted"


def test_no_decision_falls_through(adjoint_home: Path, project_dir: Path, run_hook_bin) -> None:
    """Policies that all error out / return reserved actions yield no
    decisive opinion; the hook must fall through (no ``permissionDecision``)
    so Claude Code's normal flow runs."""
    _write(
        adjoint_home / "policies" / "enabled" / "broken.py",
        """
        def decide(ctx):
            raise RuntimeError("boom")
        """,
    )
    _write(
        adjoint_home / "policies" / "enabled" / "deferring.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="defer")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    assert cp.stdout == "", f"expected fall-through, got {cp.stdout!r}"


def test_raising_policy_fails_open(adjoint_home: Path, project_dir: Path, run_hook_bin) -> None:
    _write(
        adjoint_home / "policies" / "enabled" / "boom.py",
        """
        def decide(ctx):
            raise RuntimeError("boom")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_deny_beats_allow_when_combined(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    enabled = adjoint_home / "policies" / "enabled"
    _write(
        enabled / "a_allow.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    _write(
        enabled / "b_deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="composed")
        """,
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_configured_policies_dir_is_honored(
    tmp_path: Path, project_dir: Path, run_hook_bin
) -> None:
    """``cfg.policies.dir`` pointing at a custom path must be respected."""
    custom = tmp_path / "my-policies"
    _write(
        custom / "deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="from custom dir")
        """,
    )
    (project_dir / ".adjoint").mkdir()
    (project_dir / ".adjoint" / "config.toml").write_text(
        f'[policies]\ndir = "{custom}"\n',
        encoding="utf-8",
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "from custom dir"


def test_pretooluse_finds_repo_config_from_subdir(project_dir: Path, run_hook_bin) -> None:
    """Hook launched from <repo>/sub/dir must still pick up repo-root policies.

    Without ``find_project_root``, ``load_config(<sub>)`` and
    ``_resolve_policies_dir`` would miss the project's config and policies
    silently — the deny would never fire.
    """
    _write(
        project_dir / "custompol" / "deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="repo-rooted")
        """,
    )
    (project_dir / ".adjoint").mkdir()
    (project_dir / ".adjoint" / "config.toml").write_text(
        '[policies]\ndir = "custompol"\n',
        encoding="utf-8",
    )
    nested = project_dir / "sub" / "dir"
    nested.mkdir(parents=True)
    cp = run_hook_bin(HOOK_BIN, _payload(nested, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "repo-rooted"


def test_relative_policies_dir_anchors_to_project(project_dir: Path, run_hook_bin) -> None:
    """``[policies] dir = "custompol"`` resolves under the project, not the hook cwd."""
    _write(
        project_dir / "custompol" / "deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="from relative dir")
        """,
    )
    (project_dir / ".adjoint").mkdir()
    (project_dir / ".adjoint" / "config.toml").write_text(
        '[policies]\ndir = "custompol"\n',
        encoding="utf-8",
    )
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Bash", {"command": "ls"}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "from relative dir"


def test_tool_input_is_deep_frozen_across_policies(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """A mutating policy must not be able to alter the view a later policy sees.

    Without recursive freezing, ``tool_input["edits"][0]["old_string"] = ..."``
    would silently change the value the next policy reads, making composition
    order-dependent.
    """
    enabled = adjoint_home / "policies" / "enabled"
    _write(
        enabled / "a_mutator.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            try:
                ctx.tool_input["edits"][0]["old_string"] = "PWNED"
            except TypeError:
                pass
            try:
                ctx.tool_input["edits"].append({"old_string": "x", "new_string": "y"})
            except (AttributeError, TypeError):
                pass
            return PolicyDecision(action="allow")
        """,
    )
    _write(
        enabled / "z_observer.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            edits = ctx.tool_input["edits"]
            first = edits[0]["old_string"]
            count = len(edits)
            if first != "original" or count != 1:
                return PolicyDecision(
                    action="deny",
                    reason=f"mutation visible: first={first!r} count={count}",
                )
            return PolicyDecision(action="allow")
        """,
    )
    payload = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PreToolUse",
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "/tmp/x.txt",
                "edits": [{"old_string": "original", "new_string": "n"}],
            },
        }
    )
    cp = run_hook_bin(HOOK_BIN, payload)
    assert cp.returncode == 0
    # Observer's deny would fire if the mutation leaked across policies; the
    # observer's ``allow`` is now emitted explicitly when no mutation is seen.
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow", (
        f"observer saw mutation: {cp.stdout!r}"
    )


def test_bundled_no_writes_outside_repo(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """End-to-end smoke of the shipped bundled policy."""
    from adjoint.install import apply_install, build_install_plan

    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)

    disabled = adjoint_home / "policies" / "disabled" / "no_writes_outside_repo.py"
    assert disabled.is_file(), "bundled policy should be copied by install"
    enabled = adjoint_home / "policies" / "enabled"
    enabled.mkdir(parents=True, exist_ok=True)
    (enabled / "no_writes_outside_repo.py").symlink_to(disabled)

    outside = project_dir.parent / "outside.txt"
    cp = run_hook_bin(HOOK_BIN, _payload(project_dir, "Write", {"file_path": str(outside)}))
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def _allow(stdout: str) -> None:
        assert stdout, "expected an explicit allow JSON, got empty"
        out = json.loads(stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    inside = project_dir / "inside.txt"
    cp2 = run_hook_bin(HOOK_BIN, _payload(project_dir, "Write", {"file_path": str(inside)}))
    assert cp2.returncode == 0
    _allow(cp2.stdout)

    # Relative path: must be anchored against ctx.cwd, not the hook's own cwd.
    cp3 = run_hook_bin(HOOK_BIN, _payload(project_dir, "Write", {"file_path": "inside.txt"}))
    assert cp3.returncode == 0
    _allow(cp3.stdout)

    # Nested launch: even when Claude starts in <repo>/sub/dir, a write to
    # <repo>/rootfile.txt is still in-repo and must be allowed. Without the
    # find_project_root walk-up, ctx.cwd would be the subdir and rootfile
    # would be flagged as outside.
    nested = project_dir / "sub" / "dir"
    nested.mkdir(parents=True, exist_ok=True)
    rootfile = project_dir / "rootfile.txt"
    cp4 = run_hook_bin(HOOK_BIN, _payload(nested, "Write", {"file_path": str(rootfile)}))
    assert cp4.returncode == 0
    _allow(cp4.stdout)
