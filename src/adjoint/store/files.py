"""Filesystem helpers for markdown daily logs and knowledge articles."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def daily_log_path(daily_dir: Path, date: str | None = None) -> Path:
    d = date or today_utc()
    return daily_dir / f"{d}.md"


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)
