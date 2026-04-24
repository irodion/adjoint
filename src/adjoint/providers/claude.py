"""Claude Code CLI provider — ``claude -p`` in headless JSON mode."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base import Provider, ProviderNotFoundError, ProviderResult, spawn, which


class ClaudeProvider:
    name = "claude"

    def binary(self) -> str | None:
        return which("claude")

    def version(self) -> str | None:
        bin_path = self.binary()
        if not bin_path:
            return None
        try:
            cp = spawn([bin_path, "--version"], timeout_seconds=5.0)
        except Exception:
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
            raise ProviderNotFoundError("claude CLI not found on PATH")

        argv = [bin_path, "-p", prompt, "--output-format", "json"]
        if model:
            argv += ["--model", model]
        if context_files:
            for p in context_files:
                argv += ["--add-dir", str(p.parent if p.is_file() else p)]

        start = time.monotonic()
        cp = spawn(argv, cwd=cwd, timeout_seconds=timeout_seconds, recursion_tag="adjoint")
        duration_ms = int((time.monotonic() - start) * 1000)

        response = cp.stdout
        cost_usd: float | None = None
        try:
            payload = json.loads(cp.stdout) if cp.stdout.strip() else {}
            response = payload.get("result") or payload.get("response") or cp.stdout
            cost_usd = payload.get("total_cost_usd") or payload.get("cost_usd")
        except json.JSONDecodeError:
            pass

        return ProviderResult(
            provider=self.name,
            model=model,
            response=response,
            exit_code=cp.returncode,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            stderr=cp.stderr,
        )


def provider() -> Provider:
    return ClaudeProvider()
