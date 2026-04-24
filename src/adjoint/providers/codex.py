"""Codex CLI provider — ``codex exec`` headless."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .base import Provider, ProviderNotFoundError, ProviderResult, spawn, which


class CodexProvider:
    name = "codex"

    def binary(self) -> str | None:
        return which("codex")

    def version(self) -> str | None:
        bin_path = self.binary()
        if not bin_path:
            return None
        try:
            cp = spawn([bin_path, "--version"], timeout_seconds=5.0)
        except (OSError, subprocess.SubprocessError):
            return None
        if cp.returncode != 0:
            return None
        return cp.stdout.strip() or None

    def run(
        self,
        prompt: str,
        *,
        model: str | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
        context_files: list[Path] | None = None,
    ) -> ProviderResult:
        bin_path = self.binary()
        if not bin_path:
            raise ProviderNotFoundError("codex CLI not found on PATH")

        # Flags before the positional prompt — some arg parsers (argparse
        # among them) stop consuming flags once a positional appears.
        argv: list[str] = [bin_path, "exec"]
        if model:
            argv += ["--model", model]
        argv.append(prompt)

        start = time.monotonic()
        cp = spawn(argv, cwd=cwd, timeout_seconds=timeout_seconds, recursion_tag="adjoint")
        duration_ms = int((time.monotonic() - start) * 1000)

        return ProviderResult(
            provider=self.name,
            model=model,
            response=cp.stdout,
            exit_code=cp.returncode,
            duration_ms=duration_ms,
            cost_usd=None,
            stderr=cp.stderr,
        )


def provider() -> Provider:
    return CodexProvider()
