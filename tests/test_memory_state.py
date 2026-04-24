from __future__ import annotations

import json
from pathlib import Path

from adjoint.memory.state import CompileState, sha256_of_file, sha256_of_text


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    s = CompileState()
    s.record_daily("daily/2026-04-24.md", "abc123", 0.03)
    s.record_article(
        "knowledge/concepts/foo.md",
        ["daily/2026-04-24.md"],
        {"daily/2026-04-24.md": "abc123"},
        creating=True,
    )
    s.save(state_path)
    loaded = CompileState.load(state_path)
    assert loaded.daily_logs["daily/2026-04-24.md"].sha256 == "abc123"
    assert loaded.articles["knowledge/concepts/foo.md"].sources == ["daily/2026-04-24.md"]


def test_dirty_detection(tmp_path: Path) -> None:
    base = tmp_path / "project"
    daily = base / "daily" / "2026-04-24.md"
    _write(daily, "hello world")

    s = CompileState()
    # Initially, no state → every daily is dirty.
    assert s.dirty_daily_logs([daily], base) == [daily]

    s.record_daily("daily/2026-04-24.md", sha256_of_file(daily), cost_usd=0.01)
    assert s.dirty_daily_logs([daily], base) == []

    # Mutate the file → becomes dirty again.
    _write(daily, "hello world v2")
    assert s.dirty_daily_logs([daily], base) == [daily]


def test_dirty_article_when_source_hash_stale(tmp_path: Path) -> None:
    base = tmp_path / "project"
    daily_rel = "daily/2026-04-24.md"
    daily = base / daily_rel
    _write(daily, "orig")

    art_rel = "knowledge/concepts/alpha.md"
    _write(base / art_rel, "# Alpha\n")

    s = CompileState()
    s.record_article(art_rel, [daily_rel], {daily_rel: sha256_of_file(daily)}, creating=True)
    assert s.dirty_articles(base) == []

    _write(daily, "mutated")
    assert s.dirty_articles(base) == [art_rel]


def test_canonical_json_output(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    s = CompileState()
    s.record_daily("daily/b.md", "2", None)
    s.record_daily("daily/a.md", "1", None)
    s.save(state_path)
    # Keys should be sorted for diff-friendliness.
    text = state_path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert list(data["daily_logs"].keys()) == ["daily/a.md", "daily/b.md"]


def test_sha256_of_text_matches_file(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    content = "abc"
    p.write_text(content, encoding="utf-8")
    assert sha256_of_text(content) == sha256_of_file(p)
