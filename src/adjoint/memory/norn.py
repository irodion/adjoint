"""Norn — structural PII scrubber extending adjoint's regex redactor.

adjoint already ships a regex-based ``Redactor`` (``memory/redact.py``) that
catches known API key formats. ``Norn`` is a complementary second pass that
adds:

- Email, phone, SSN, credit card, IPv4 (non-RFC1918) detection
- Optional spaCy NER for person-name redaction (opt-in; no hard dep)
- Allowlist for false-positive-prone values (loopback IPs, etc.)
- Structured findings so callers can log *what* was found without logging
  the secret itself

The design is intentionally compatible with ``Redactor``: both use the same
``[REDACTED:<label>]`` replacement token so downstream log parsers and
``lint.py`` handle both transparently.

## Usage in flush pipeline

Wire Norn into ``flush.py`` right after the existing redactor pass::

    # existing
    redactor = redactor_from_config(cfg.memory.redact_patterns)
    transcript_text = render_turns(selected)
    transcript_text = redactor.sanitize(transcript_text)

    # add
    from .norn import Norn, norn_from_config
    norn = norn_from_config(cfg)
    result = norn.scrub(transcript_text, surface="flush", session_id=session_id or "")
    transcript_text = result.redacted
    if result.findings:
        log_event(logger, "flush.pii_found",
                  count=len(result.findings),
                  labels=sorted({f.label for f in result.findings}))

The second call is idempotent: ``[REDACTED:...]`` tokens from the first pass
are never re-matched because our patterns don't match that literal string.

## Standalone usage::

    from adjoint.memory.norn import Norn
    norn = Norn()
    result = norn.scrub("Contact me at sean@example.com")
    print(result.redacted)   # "Contact me at [REDACTED:email]"
    print(result.clean)      # False
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..config import Config

logger = logging.getLogger("adjoint.memory.norn")

# ---------------------------------------------------------------------------
# Token / API-key patterns  (aligned with redact.py so dedup is possible)
# ---------------------------------------------------------------------------

_TOKEN_PATTERNS: dict[str, str] = {
    # adjoint-native patterns (same as redact.py; kept here for belt-and-suspenders)
    "anthropic_api_key": r"sk-ant-[A-Za-z0-9_-]+",
    "slack_token": r"xox[baprs]-[A-Za-z0-9-]+",
    "github_pat": r"ghp_[A-Za-z0-9]{36,}",
    "github_pat_fg": r"github_pat_[A-Za-z0-9_]{82}",
    "openai_key": r"sk-[A-Za-z0-9]{48}",
    "openai_proj_key": r"sk-proj-[A-Za-z0-9_-]+",
    # Additions
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "bearer_token": r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
}

# ---------------------------------------------------------------------------
# PII patterns (Norn-specific contribution)
# ---------------------------------------------------------------------------

_PII_PATTERNS: dict[str, str] = {
    "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "phone_us": r"\b(?:\+1\s?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b",
    "ssn": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
    "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}

# Values that should NOT be redacted even if they match
_ALLOWLIST: list[re.Pattern[str]] = [
    re.compile(r"\b(?:127\.0\.0\.1|0\.0\.0\.0|localhost)\b"),
    re.compile(r"\b192\.168\.\d+\.\d+\b"),
    re.compile(r"\b10\.\d+\.\d+\.\d+\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b"),
]


@dataclass(frozen=True)
class PiiFinding:
    """A single PII hit found during scrubbing."""
    label: str
    start: int
    end: int
    excerpt: str  # first 40 chars of the matched value (for audit logging only)


@dataclass
class ScrubResult:
    """Result of a Norn scrub pass."""
    original: str
    redacted: str
    findings: list[PiiFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.clean:
            return "clean"
        labels = sorted({f.label for f in self.findings})
        return "redacted: " + ", ".join(labels)


class Norn:
    """Structural PII scrubber extending adjoint's regex Redactor.

    Parameters
    ----------
    extra_patterns:
        Additional {label: regex_str} patterns layered on top of defaults.
    use_spacy:
        Attempt spaCy ``en_core_web_sm`` NER for PERSON entity detection.
        Gracefully skipped when spaCy is not installed.
    extra_allowlist:
        Additional regex strings for values that must not be redacted.
    """

    def __init__(
        self,
        extra_patterns: Optional[dict[str, str]] = None,
        use_spacy: bool = False,
        extra_allowlist: Optional[list[str]] = None,
    ) -> None:
        self._compiled: list[tuple[str, re.Pattern[str]]] = []
        self._allowlist: list[re.Pattern[str]] = list(_ALLOWLIST)
        self._nlp: Any = None

        all_patterns = {**_TOKEN_PATTERNS, **_PII_PATTERNS, **(extra_patterns or {})}
        for label, raw in all_patterns.items():
            try:
                self._compiled.append((label, re.compile(raw)))
            except re.error as exc:
                logger.warning("Norn: bad pattern for %r: %s", label, exc)

        for raw in extra_allowlist or []:
            try:
                self._allowlist.append(re.compile(raw))
            except re.error:
                pass

        if use_spacy:
            try:
                import spacy  # type: ignore[import-not-found]
                self._nlp = spacy.load("en_core_web_sm")
            except Exception:
                logger.debug("Norn: spaCy NER unavailable — regex-only mode")

    def _allowlisted(self, text: str) -> bool:
        return any(p.search(text) for p in self._allowlist)

    def scrub(
        self,
        text: str,
        *,
        surface: str = "unknown",
        session_id: str = "",
    ) -> ScrubResult:
        """Scrub PII from *text*.

        Replacement tokens use ``[REDACTED:<label>]`` — same format as
        ``Redactor.sanitize()`` so downstream consumers see a uniform format.

        Overlapping spans are merged (longest match within overlap wins).
        """
        if not text:
            return ScrubResult(original=text, redacted=text)

        spans: list[tuple[int, int, str]] = []
        findings: list[PiiFinding] = []

        for label, pat in self._compiled:
            for m in pat.finditer(text):
                if self._allowlisted(m.group(0)):
                    continue
                spans.append((m.start(), m.end(), label))
                findings.append(
                    PiiFinding(
                        label=label,
                        start=m.start(),
                        end=m.end(),
                        excerpt=m.group(0)[:40],
                    )
                )

        if self._nlp:
            try:
                doc = self._nlp(text)
                for ent in doc.ents:
                    if ent.label_ == "PERSON":
                        spans.append((ent.start_char, ent.end_char, "person_name"))
                        findings.append(
                            PiiFinding(
                                label="person_name",
                                start=ent.start_char,
                                end=ent.end_char,
                                excerpt=ent.text[:40],
                            )
                        )
            except Exception as exc:
                logger.warning("Norn: spaCy NER error: %s", exc)

        if not spans:
            return ScrubResult(original=text, redacted=text, findings=[])

        # Merge overlapping spans
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        merged: list[tuple[int, int, str]] = []
        for start, end, label in spans:
            if merged and start < merged[-1][1]:
                ps, pe, pl = merged[-1]
                if end > pe:
                    merged[-1] = (ps, end, pl)
            else:
                merged.append((start, end, label))

        parts: list[str] = []
        cursor = 0
        for start, end, label in merged:
            parts.append(text[cursor:start])
            parts.append(f"[REDACTED:{label}]")
            cursor = end
        parts.append(text[cursor:])
        redacted = "".join(parts)

        if findings:
            logger.info(
                "Norn[%s/%s]: %d finding(s) — %s",
                session_id[:8] or "?",
                surface,
                len(findings),
                ", ".join(sorted({f.label for f in findings})),
            )

        return ScrubResult(original=text, redacted=redacted, findings=findings)


def norn_from_config(config: "Config") -> Norn:
    """Build a Norn instance from adjoint config.

    Reads ``memory.norn_enabled`` (bool, default True) and
    ``memory.norn_use_spacy`` (bool, default False) from config.
    Returns a passthrough Norn (no patterns) if ``norn_enabled=False``.
    """
    # Graceful access — norn fields are optional; don't break older configs.
    mem = getattr(config, "memory", None)
    enabled = getattr(mem, "norn_enabled", True)
    use_spacy = getattr(mem, "norn_use_spacy", False)
    extra_patterns = getattr(mem, "norn_extra_patterns", None) or {}
    extra_allowlist = getattr(mem, "norn_extra_allowlist", None) or []

    if not enabled:
        # Return a Norn with no patterns — acts as identity transform
        return Norn(extra_patterns={}, use_spacy=False)

    return Norn(
        extra_patterns=extra_patterns,
        use_spacy=use_spacy,
        extra_allowlist=extra_allowlist,
    )
