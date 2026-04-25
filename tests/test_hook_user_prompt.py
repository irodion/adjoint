"""End-to-end test for the UserPromptSubmit hook — opt-in wikilink injection."""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import write_article


def _payload(project_dir: Path, prompt: str) -> str:
    return json.dumps(
        {
            "session_id": "s",
            "transcript_path": str(project_dir / "x.jsonl"),
            "cwd": str(project_dir),
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
    )


def _enable_enrich(project_dir: Path) -> None:
    cfg = project_dir / ".adjoint" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[memory]\nenrich_prompts = true\n", encoding="utf-8")


def _seed_concept(project_dir: Path, slug: str, title: str) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    write_article(
        pp.concepts_dir / f"{slug}.md",
        title=title,
        tags=["test"],
        created="2026-04-01",
        updated="2026-04-01",
        body="Short explanation.",
    )


def test_disabled_by_default(project_dir: Path, run_hook_bin) -> None:
    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "tell me about prompt caching"),
    )
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_injects_wikilink_when_enabled(adjoint_home: Path, project_dir: Path, run_hook_bin) -> None:
    _enable_enrich(project_dir)
    _seed_concept(project_dir, "prompt-caching", "Prompt Caching")

    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "How does prompt caching work?"),
    )
    assert cp.returncode == 0
    assert cp.stdout != ""
    out = json.loads(cp.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "[[concepts/prompt-caching]]" in hso["additionalContext"]


def test_no_match_is_passthrough(adjoint_home: Path, project_dir: Path, run_hook_bin) -> None:
    _enable_enrich(project_dir)
    _seed_concept(project_dir, "prompt-caching", "Prompt Caching")
    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "What is the meaning of life?"),
    )
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_empty_prompt_is_passthrough(adjoint_home: Path, project_dir: Path, run_hook_bin) -> None:
    _enable_enrich(project_dir)
    _seed_concept(project_dir, "prompt-caching", "Prompt Caching")
    cp = run_hook_bin("adjoint-hook-user-prompt", _payload(project_dir, ""))
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_enrichment_handles_articles_larger_than_read_window(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """The limited-read optimisation must still surface titles when the
    article body dwarfs the 2 KB head buffer."""
    from adjoint.paths import user_paths

    _enable_enrich(project_dir)
    pp = user_paths().project(project_dir)
    pp.ensure()
    body = "lorem ipsum " * 1000  # ≈12 KB — well past the head read window
    write_article(
        pp.concepts_dir / "prompt-caching.md",
        title="Prompt Caching",
        tags=["test"],
        created="2026-04-01",
        updated="2026-04-01",
        body=body,
    )
    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "How does prompt caching work?"),
    )
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert "[[concepts/prompt-caching]]" in out["hookSpecificOutput"]["additionalContext"]


def test_enrichment_matches_short_technical_acronyms(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """Acronyms like MCP / WAL / uv are common concept titles in this codebase.

    The previous 4-char minimum filtered them out entirely so enrichment
    silently produced no matches for the most relevant entries.
    """
    _enable_enrich(project_dir)
    _seed_concept(project_dir, "mcp-tools", "MCP Tools")
    _seed_concept(project_dir, "wal-mode", "WAL Mode")
    _seed_concept(project_dir, "uv-install", "uv install")

    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "How does MCP work?"),
    )
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert "[[concepts/mcp-tools]]" in out["hookSpecificOutput"]["additionalContext"]

    cp2 = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(project_dir, "Run uv install on the project"),
    )
    assert cp2.returncode == 0
    out2 = json.loads(cp2.stdout)
    assert "[[concepts/uv-install]]" in out2["hookSpecificOutput"]["additionalContext"]


def test_enrichment_finds_kb_from_nested_cwd(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """Sessions launched in <repo>/sub must still pick up the repo's KB.

    Without ``find_project_root``, ``project_hash(<repo>/sub)`` would key a
    different, empty project and enrichment would silently produce no
    context. The ``.adjoint/`` dir created by ``_enable_enrich`` serves as
    the project-root marker.
    """
    _enable_enrich(project_dir)
    _seed_concept(project_dir, "prompt-caching", "Prompt Caching")
    nested = project_dir / "sub" / "deep"
    nested.mkdir(parents=True)
    cp = run_hook_bin(
        "adjoint-hook-user-prompt",
        _payload(nested, "How does prompt caching work?"),
    )
    assert cp.returncode == 0
    assert cp.stdout != ""
    out = json.loads(cp.stdout)
    assert "[[concepts/prompt-caching]]" in out["hookSpecificOutput"]["additionalContext"]
