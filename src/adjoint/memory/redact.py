"""Redaction — regex-based privacy pass, applied pre- *and* post-LLM in flush.

Patterns come from ``config.memory.redact_patterns`` (defaults bundled in
``config.DEFAULT_REDACT_PATTERNS``). Each match is replaced with
``[REDACTED:<label>]`` where the label is a short, stable identifier derived
from the pattern (so downstream readers can tell classes of secret apart
without leaking the secret itself).

Belt-and-braces applied **twice** during flush: once before the transcript
hits the LLM, once after the LLM returns. The second pass catches the
(unlikely) case where the model reproduces redacted content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Known patterns → human-readable label. Unknown custom patterns get a
# stable hash-derived tag so users can still trace the class of secret.
_KNOWN_LABELS: dict[str, str] = {
    r"sk-ant-[A-Za-z0-9_-]+": "anthropic_api_key",
    r"xox[baprs]-[A-Za-z0-9-]+": "slack_token",
    r"ghp_[A-Za-z0-9]{36,}": "github_pat",
    r"github_pat_[A-Za-z0-9_]{82}": "github_pat_fg",
    r"sk-[A-Za-z0-9]{48}": "openai_key",
    r"sk-proj-[A-Za-z0-9_-]+": "openai_proj_key",
}


def _label_for(pattern: str) -> str:
    if pattern in _KNOWN_LABELS:
        return _KNOWN_LABELS[pattern]
    # Derive a short stable label from the pattern itself.
    safe = re.sub(r"[^A-Za-z0-9]+", "_", pattern).strip("_")
    return f"custom_{safe[:24]}"


@dataclass
class Redactor:
    patterns: list[str]

    def __post_init__(self) -> None:
        self._compiled: list[tuple[re.Pattern[str], str]] = []
        for raw in self.patterns:
            try:
                self._compiled.append((re.compile(raw), _label_for(raw)))
            except re.error:
                # Silently drop invalid user patterns — surfacing errors from
                # a flush hook could block the session.
                continue

    def sanitize(self, text: str) -> str:
        if not text:
            return text
        out = text
        for pat, label in self._compiled:
            out = pat.sub(f"[REDACTED:{label}]", out)
        return out


def from_config(patterns: list[str] | None) -> Redactor:
    return Redactor(patterns=list(patterns or []))
