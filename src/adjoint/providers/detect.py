"""Detect installed provider CLIs — used by ``adjoint providers list``."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Provider
from .claude import provider as claude_provider
from .codex import provider as codex_provider


@dataclass
class DetectedProvider:
    name: str
    binary: str | None
    version: str | None
    available: bool


def all_providers() -> list[Provider]:
    return [claude_provider(), codex_provider()]


def detect_all() -> list[DetectedProvider]:
    out: list[DetectedProvider] = []
    for p in all_providers():
        binary = p.binary()
        version = p.version() if binary else None
        out.append(
            DetectedProvider(
                name=p.name,
                binary=binary,
                version=version,
                available=binary is not None,
            )
        )
    return out
