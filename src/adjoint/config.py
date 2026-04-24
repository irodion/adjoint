"""Configuration — merges ~/.adjoint/config.toml and ./.adjoint/config.toml.

All keys are optional; zero-config install works. Project-level overrides
merge one level deep: an overlay section's keys extend/replace the base
section's keys, rather than the whole section being swapped. Nested dicts
beyond one level are not recursively merged.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .paths import adjoint_home

DEFAULT_REDACT_PATTERNS: list[str] = [
    r"sk-ant-[A-Za-z0-9_-]+",
    r"xox[baprs]-[A-Za-z0-9-]+",
    r"ghp_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{82}",
    r"sk-[A-Za-z0-9]{48}",
    r"sk-proj-[A-Za-z0-9_-]+",
]


class ProviderConfig(BaseModel):
    model: str | None = None
    confirm_cost_over_usd: float = 1.00


class MemoryConfig(BaseModel):
    flush_on_session_end: bool = True
    flush_on_precompact: bool = True
    session_start_injection: bool = True
    enrich_prompts: bool = False
    compile_at: str = "18:00"
    incremental: bool = True
    redact_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_REDACT_PATTERNS))
    query_max_cost_usd: float = 0.10
    index_max_bytes: int = 20 * 1024


class PoliciesConfig(BaseModel):
    dir: str = "~/.adjoint/policies/enabled"
    timeout_ms: int = 500


class DaemonConfig(BaseModel):
    auto_start: bool = False
    socket: str = "~/.adjoint/daemon.sock"
    http_port: int = 0


class AuditConfig(BaseModel):
    enabled: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"


class Config(BaseModel):
    timezone: str | None = None
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "claude": ProviderConfig(model="claude-haiku-4-5"),
            "codex": ProviderConfig(model="gpt-5.3-codex"),
        }
    )
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def model_for(self, provider_name: str) -> str | None:
        """Look up the configured model for a provider (e.g. 'claude'), or None."""
        entry = self.providers.get(provider_name)
        return entry.model if entry else None


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _shallow_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level keys; one level of section-key merging underneath.

    For each top-level key, if both sides carry a dict we produce a new dict
    whose keys are ``base_section | overlay_section`` (overlay wins on key
    collisions). Everything else is replaced wholesale by the overlay value.
    """
    merged: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged_sub = dict(merged[k])
            merged_sub.update(v)
            merged[k] = merged_sub
        else:
            merged[k] = v
    return merged


def load_config(project_dir: Path | None = None) -> Config:
    user_cfg = _load_toml(adjoint_home() / "config.toml")
    project_cfg: dict[str, Any] = {}
    if project_dir is not None:
        project_cfg = _load_toml(project_dir / ".adjoint" / "config.toml")
    merged = _shallow_merge(user_cfg, project_cfg)
    return Config.model_validate(merged)
