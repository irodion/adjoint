"""Verifies SessionStart hook injects knowledge/index.md via additionalContext."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _hook_bin() -> str:
    return str(Path(sys.executable).parent / "adjoint-hook-session-start")


def test_session_start_injects_index_when_present(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    index_text = "# Knowledge Index\n\n## Active Action Items\n- ship M1 milestone\n"
    pp.knowledge_index.write_text(index_text, encoding="utf-8")

    payload = json.dumps(
        {
            "session_id": "s1",
            "transcript_path": "/tmp/nonexistent.jsonl",
            "cwd": str(project_dir),
            "hook_event_name": "SessionStart",
        }
    )
    env = {
        "ADJOINT_HOME": str(adjoint_home),
        "PATH": "/usr/bin:/bin",
    }
    cp = subprocess.run(
        [_hook_bin()],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr
    out = json.loads(cp.stdout)
    hso = out.get("hookSpecificOutput", {})
    assert hso.get("hookEventName") == "SessionStart"
    assert "ship M1 milestone" in hso.get("additionalContext", "")


def test_session_start_noops_when_index_missing(adjoint_home: Path, project_dir: Path) -> None:
    payload = json.dumps(
        {"session_id": "s1", "cwd": str(project_dir), "hook_event_name": "SessionStart"}
    )
    env = {"ADJOINT_HOME": str(adjoint_home), "PATH": "/usr/bin:/bin"}
    cp = subprocess.run(
        [_hook_bin()],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0
    # With no index and no handler result, stdout is empty.
    assert cp.stdout.strip() == ""
