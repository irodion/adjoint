"""Provider protocol and shared subprocess helpers.

Security invariants enforced here:

* ``subprocess.Popen`` is always called with an **argv list** and
  ``shell=False``. The test suite asserts there is no ``shell=True`` site
  anywhere under ``src/``.
* The recursion guard env var ``CLAUDE_INVOKED_BY`` is propagated to every
  provider subprocess so nested ``claude`` calls can't re-trigger hooks.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..log import RecursionTag, child_env


class ProviderNotFoundError(RuntimeError):
    """Raised when a provider CLI binary is not on PATH."""


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str | None
    response: str
    exit_code: int
    duration_ms: int
    cost_usd: float | None = None
    stderr: str = ""


class Provider(Protocol):
    name: str

    def binary(self) -> str | None:
        """Absolute path to the CLI binary, or None if not installed."""

    def version(self) -> str | None:
        """Version string as reported by the CLI, or None if unavailable."""

    def run(
        self,
        prompt: str,
        *,
        model: str | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
        context_files: list[Path] | None = None,
    ) -> ProviderResult:
        """Run the provider against ``prompt`` and return its response."""


def which(binary_name: str) -> str | None:
    return shutil.which(binary_name)


def spawn(
    argv: list[str],
    *,
    stdin_text: str | None = None,
    cwd: Path | None = None,
    timeout_seconds: float = 60.0,
    recursion_tag: RecursionTag = "adjoint",
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` as a one-shot subprocess. Never uses a shell.

    Always propagates the recursion guard env var.
    """
    if not argv:
        raise ValueError("argv must be non-empty")
    env = child_env(recursion_tag)
    return subprocess.run(  # noqa: S603 — argv list, shell=False is the whole point
        argv,
        input=stdin_text,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
