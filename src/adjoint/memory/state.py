"""Incremental-compile state — sha256 dependency graph in ``state.json``.

Two mappings:

* ``daily_logs[path] = {sha256, last_compiled_at, cost_usd}`` — one entry per
  day we have ever compiled. "Dirty" daily = current sha256 != recorded sha256
  (or missing from map). Reference is full-rebuild every time; we do not.

* ``articles[path] = {sources: [daily_path, ...], source_hashes: {path: sha}, ...}``
  — for each article we remember the daily logs that produced its current
  content, and the hash each daily had when we last wrote the article. Any
  mismatch marks the article dirty.

The map is canonical JSON (sorted keys, 2-space indent) so diffs stay tidy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class DailyEntry:
    sha256: str
    last_compiled_at: str | None = None
    cost_usd: float | None = None


@dataclass
class ArticleEntry:
    sources: list[str] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)
    created: str = field(default_factory=_utcnow_iso)
    updated: str = field(default_factory=_utcnow_iso)


@dataclass
class CompileState:
    daily_logs: dict[str, DailyEntry] = field(default_factory=dict)
    articles: dict[str, ArticleEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> CompileState:
        if not path.is_file():
            return cls()
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8") or "{}")
        state = cls()
        for k, v in (data.get("daily_logs") or {}).items():
            state.daily_logs[k] = DailyEntry(**v)
        for k, v in (data.get("articles") or {}).items():
            state.articles[k] = ArticleEntry(**v)
        return state

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "daily_logs": {k: asdict(v) for k, v in sorted(self.daily_logs.items())},
            "articles": {k: asdict(v) for k, v in sorted(self.articles.items())},
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ── dirty-set computation ────────────────────────────────────────────

    def dirty_daily_logs(self, daily_files: list[Path], base_dir: Path) -> list[Path]:
        """Return daily files whose current content differs from recorded hash."""
        dirty: list[Path] = []
        for p in daily_files:
            rel = str(p.relative_to(base_dir))
            cur = sha256_of_file(p)
            rec = self.daily_logs.get(rel)
            if rec is None or rec.sha256 != cur:
                dirty.append(p)
        return dirty

    def dirty_articles(self, base_dir: Path) -> list[str]:
        """Articles whose recorded source hashes no longer match what's on disk.

        A single source (e.g. ``daily/2026-04-24.md``) is often referenced by
        many articles; we memoise ``sha256_of_file`` per-run so each file is
        read from disk exactly once regardless of how many articles cite it.
        """
        hash_cache: dict[str, str | None] = {}
        out: list[str] = []
        for art_path, entry in self.articles.items():
            for src, old_hash in entry.source_hashes.items():
                if src not in hash_cache:
                    full = base_dir / src
                    hash_cache[src] = sha256_of_file(full) if full.is_file() else None
                current = hash_cache[src]
                if current is None or current != old_hash:
                    out.append(art_path)
                    break
        return out

    def record_daily(self, rel_path: str, sha: str, cost_usd: float | None) -> None:
        self.daily_logs[rel_path] = DailyEntry(
            sha256=sha,
            last_compiled_at=_utcnow_iso(),
            cost_usd=cost_usd,
        )

    def record_article(
        self,
        art_rel_path: str,
        sources: list[str],
        source_hashes: dict[str, str],
        *,
        creating: bool,
    ) -> None:
        now = _utcnow_iso()
        existing = self.articles.get(art_rel_path)
        created = existing.created if (existing and not creating) else now
        self.articles[art_rel_path] = ArticleEntry(
            sources=sources,
            source_hashes=dict(source_hashes),
            created=created,
            updated=now,
        )
