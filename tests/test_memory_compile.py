"""Compile integration — daily log → articles, incremental idempotency, git commit."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from adjoint.memory.compile import compile_project

from .fake_agent import FakeAgent


def _write_daily(project_dir: Path, date: str, body: str) -> Path:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    p = pp.daily_dir / f"{date}.md"
    p.write_text(body, encoding="utf-8")
    return p


EXTRACTION_BASIC = json.dumps(
    [
        {
            "kind": "concept",
            "slug": "prompt-caching",
            "title": "Prompt Caching",
            "tags": ["anthropic", "performance"],
            "summary": "Anthropic's prompt caching reduces input token cost by 90% on repeated prefixes. Cache TTL is 5 minutes.",
            "related": ["context-window"],
        },
        {
            "kind": "concept",
            "slug": "context-window",
            "title": "Context Window",
            "tags": ["anthropic"],
            "summary": "The context window is the span of tokens Claude considers in one request. Opus 4.7 supports 1M tokens.",
            "related": [],
        },
    ]
)


def test_compile_creates_articles_and_backlinks_and_commits(
    adjoint_home: Path, project_dir: Path
) -> None:
    _write_daily(
        project_dir,
        "2026-04-24",
        "# session\n\n## Context\nTalked about caching.\n\n## Decisions\n- use prompt caching\n",
    )
    # target_articles iterates alphabetically, so context-window is rendered
    # first, then prompt-caching.
    agent = FakeAgent().enqueue(
        EXTRACTION_BASIC,
        "The context window is the span of tokens Claude considers in one request. "
        "Related: [[prompt-caching]].",
        "Anthropic's prompt caching reduces input token cost on repeated prefixes. "
        "Related: [[context-window]].",
    )

    result = compile_project(project_path=project_dir, client=agent)
    assert sorted(result.articles_created) == [
        "knowledge/concepts/context-window.md",
        "knowledge/concepts/prompt-caching.md",
    ]
    assert result.articles_updated == []
    assert result.git_sha is not None

    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    caching = (pp.root / "knowledge/concepts/prompt-caching.md").read_text(encoding="utf-8")
    # Frontmatter present.
    assert caching.startswith("---\ntitle: Prompt Caching")
    assert "kind: concept" in caching
    assert "- daily/2026-04-24.md" in caching
    # Body is from the merge call.
    assert "prompt caching" in caching.lower()
    # context-window.md wikilinks to prompt-caching → its Backlinks must list context-window.
    assert "## Backlinks" in caching
    assert "[[concepts/context-window.md]]" in caching

    # Knowledge index regenerated.
    assert pp.knowledge_index.is_file()
    assert "# Knowledge Index" in pp.knowledge_index.read_text(encoding="utf-8")

    # State recorded.
    from adjoint.memory.state import CompileState

    state = CompileState.load(pp.state_json)
    assert "daily/2026-04-24.md" in state.daily_logs
    assert "knowledge/concepts/prompt-caching.md" in state.articles


def test_compile_is_incremental_when_no_files_change(adjoint_home: Path, project_dir: Path) -> None:
    _write_daily(project_dir, "2026-04-24", "content")
    agent = FakeAgent().enqueue(EXTRACTION_BASIC, "body1", "body2")
    compile_project(project_path=project_dir, client=agent)

    # Second run with no file changes → no dirty daily, no LLM calls.
    agent2 = FakeAgent()  # empty queue — any call would AssertionError
    result = compile_project(project_path=project_dir, client=agent2)
    assert result.dirty_daily == []
    assert result.articles_created == []
    assert result.articles_updated == []
    assert agent2.calls == []
    # No new git commit when nothing to commit.
    assert result.git_sha is None


def test_compile_dry_run_does_not_mutate(adjoint_home: Path, project_dir: Path) -> None:
    _write_daily(project_dir, "2026-04-24", "content")
    agent = FakeAgent().enqueue(EXTRACTION_BASIC)  # only the extraction call
    result = compile_project(project_path=project_dir, client=agent, dry_run=True)
    assert result.dirty_daily == ["daily/2026-04-24.md"]
    # No articles written.
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    assert not any(pp.concepts_dir.glob("*.md"))
    # No state written (dry run returns before save).
    assert (
        not pp.state_json.is_file()
        or "knowledge/concepts/prompt-caching.md" not in pp.state_json.read_text()
    )


def test_compile_git_commit_has_meaningful_message(adjoint_home: Path, project_dir: Path) -> None:
    _write_daily(project_dir, "2026-04-24", "content")
    agent = FakeAgent().enqueue(EXTRACTION_BASIC, "body1", "body2")
    compile_project(project_path=project_dir, client=agent)

    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    cp = subprocess.run(
        ["git", "-C", str(pp.knowledge_dir), "log", "--format=%s", "-n", "1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert cp.stdout.startswith("compile: ")
    assert "new=2" in cp.stdout
