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
