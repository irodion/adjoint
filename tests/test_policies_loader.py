"""Unit tests for the policies discover/run/compose pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from textwrap import dedent

from adjoint.policies.loader import compose, discover_policies, run_policies
from adjoint.policies.types import PolicyDecision, ToolUseContext


def _ctx(project_dir: Path) -> ToolUseContext:
    return ToolUseContext(
        tool_name="Write",
        tool_input={"file_path": str(project_dir / "x.txt")},
        cwd=project_dir,
        session_id="s1",
        transcript_path=None,
    )


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body).lstrip(), encoding="utf-8")


# ── compose ──────────────────────────────────────────────────────────────


def test_compose_empty_is_allow() -> None:
    assert compose([]).action == "allow"


def test_compose_first_deny_wins() -> None:
    d = compose(
        [
            PolicyDecision(action="allow"),
            PolicyDecision(action="deny", reason="bad"),
            PolicyDecision(action="deny", reason="also bad"),
        ]
    )
    assert d.action == "deny"
    assert d.reason == "bad"


def test_compose_ask_beats_allow_but_not_deny() -> None:
    asked = compose([PolicyDecision(action="allow"), PolicyDecision(action="ask", reason="ok?")])
    assert asked.action == "ask"
    assert asked.reason == "ok?"

    denied = compose(
        [
            PolicyDecision(action="ask", reason="ok?"),
            PolicyDecision(action="deny", reason="no"),
        ]
    )
    assert denied.action == "deny"


def test_compose_modify_and_defer_collapse_to_allow() -> None:
    # M2 treats both reserved actions as allow when they're the only decisions.
    assert compose([PolicyDecision(action="modify")]).action == "allow"
    assert compose([PolicyDecision(action="defer")]).action == "allow"


# ── discover_policies ───────────────────────────────────────────────────


def test_discover_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    assert discover_policies(tmp_path / "nope") == []


def test_discover_skips_underscore_files(tmp_path: Path) -> None:
    _write(
        tmp_path / "alpha.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    _write(tmp_path / "_helper.py", "DATA = 1\n")
    names = [n for n, _ in discover_policies(tmp_path)]
    assert names == ["alpha"]


def test_discover_sorted_by_filename(tmp_path: Path) -> None:
    for name in ("beta.py", "alpha.py", "gamma.py"):
        _write(
            tmp_path / name,
            """
            from adjoint.policies.types import PolicyDecision
            def decide(ctx):
                return PolicyDecision(action="allow")
            """,
        )
    names = [n for n, _ in discover_policies(tmp_path)]
    assert names == ["alpha", "beta", "gamma"]


def test_discover_skips_files_without_decide(tmp_path: Path) -> None:
    _write(
        tmp_path / "has_decide.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    _write(tmp_path / "no_decide.py", "x = 1\n")
    names = [n for n, _ in discover_policies(tmp_path)]
    assert names == ["has_decide"]


def test_module_registered_in_sys_modules(tmp_path: Path, adjoint_home: Path) -> None:
    """Policy modules must land in ``sys.modules`` so self-reference works."""
    import sys

    _write(
        tmp_path / "registered.py",
        """
        import sys
        from adjoint.policies.types import PolicyDecision
        # If sys.modules registration is missing, this raises KeyError at import.
        _ME = sys.modules[__name__]
        assert _ME is not None
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    discover_policies(tmp_path)
    assert "adjoint_policy_registered" in sys.modules


def test_discover_supports_sibling_helper_imports(
    tmp_path: Path, adjoint_home: Path, project_dir: Path
) -> None:
    """A policy must be able to ``from _helper import X`` at module top level."""
    _write(tmp_path / "_policy_rules_helper.py", "RULES = ['a', 'b']\n")
    _write(
        tmp_path / "uses_helper.py",
        """
        from _policy_rules_helper import RULES
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason=f"n={len(RULES)}")
        """,
    )
    policies = discover_policies(tmp_path)
    assert [n for n, _ in policies] == ["uses_helper"]
    decision = policies[0][1](_ctx(project_dir))
    assert decision.action == "deny"
    assert decision.reason == "n=2"


def test_discover_does_not_leak_sys_path(tmp_path: Path, adjoint_home: Path) -> None:
    """``sys.path`` must not grow after discovery finishes."""
    import sys

    _write(
        tmp_path / "simple.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    before = list(sys.path)
    discover_policies(tmp_path)
    assert sys.path == before


def test_discover_survives_import_error(tmp_path: Path, adjoint_home: Path) -> None:
    _write(
        tmp_path / "ok.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    _write(tmp_path / "broken.py", "raise RuntimeError('boom')\n")
    names = [n for n, _ in discover_policies(tmp_path)]
    assert names == ["ok"]


# ── run_policies ────────────────────────────────────────────────────────


def test_run_policies_composes_deny(tmp_path: Path, project_dir: Path, adjoint_home: Path) -> None:
    _write(
        tmp_path / "allow.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="allow")
        """,
    )
    _write(
        tmp_path / "deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="nope")
        """,
    )
    policies = discover_policies(tmp_path)
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=500)
    assert decision.action == "deny"
    assert decision.reason == "nope"


def test_run_policies_timeout_is_allow(
    tmp_path: Path, project_dir: Path, adjoint_home: Path
) -> None:
    _write(
        tmp_path / "slow.py",
        """
        import time
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            time.sleep(2.0)
            return PolicyDecision(action="deny", reason="would deny if not for timeout")
        """,
    )
    policies = discover_policies(tmp_path)
    t0 = time.monotonic()
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=100)
    elapsed = time.monotonic() - t0
    assert decision.action == "allow"
    # Lower bound: the timeout must actually wait the budget, not skip it.
    # Upper bound: but never the full 2 s sleep the policy requested.
    assert 0.09 <= elapsed < 1.0, f"expected ~100ms wait, got {elapsed:.3f}s"


def test_run_policies_exception_is_allow(
    tmp_path: Path, project_dir: Path, adjoint_home: Path
) -> None:
    _write(
        tmp_path / "boom.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            raise ValueError("boom")
        """,
    )
    policies = discover_policies(tmp_path)
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=500)
    assert decision.action == "allow"


def test_run_policies_short_circuits_on_deny(
    tmp_path: Path, project_dir: Path, adjoint_home: Path
) -> None:
    """An early deny must win without waiting on later slow allow policies.

    A naïve sequential walk would run all five policies (4 × 0.4 s = 1.6 s),
    pushing past the 2 s outer hook deadline and letting fail-open silently
    promote the deny to allow. The short-circuit returns immediately.
    """
    _write(
        tmp_path / "a_deny.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="deny", reason="early")
        """,
    )
    for name in ("b_slow", "c_slow", "d_slow", "e_slow"):
        _write(
            tmp_path / f"{name}.py",
            """
            import time
            from adjoint.policies.types import PolicyDecision
            def decide(ctx):
                time.sleep(0.4)
                return PolicyDecision(action="allow")
            """,
        )
    policies = discover_policies(tmp_path)
    t0 = time.monotonic()
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=500)
    elapsed = time.monotonic() - t0
    assert decision.action == "deny"
    assert decision.reason == "early"
    # Returned without waiting on the four slow allows.
    assert elapsed < 0.3, f"expected near-immediate return, got {elapsed:.3f}s"


def test_run_policies_short_circuits_on_ask(
    tmp_path: Path, project_dir: Path, adjoint_home: Path
) -> None:
    """An ask is decisive enough to short-circuit too — surfacing it beats
    risking the outer hook deadline that would silently fail-open."""
    _write(
        tmp_path / "a_ask.py",
        """
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            return PolicyDecision(action="ask", reason="confirm")
        """,
    )
    _write(
        tmp_path / "b_slow.py",
        """
        import time
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            time.sleep(0.4)
            return PolicyDecision(action="allow")
        """,
    )
    policies = discover_policies(tmp_path)
    t0 = time.monotonic()
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=500)
    elapsed = time.monotonic() - t0
    assert decision.action == "ask"
    assert elapsed < 0.3


def test_run_policies_bad_return_is_allow(
    tmp_path: Path, project_dir: Path, adjoint_home: Path
) -> None:
    _write(
        tmp_path / "bad.py",
        """
        def decide(ctx):
            return "not a decision"
        """,
    )
    policies = discover_policies(tmp_path)
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=500)
    assert decision.action == "allow"


def test_timeout_uses_daemon_thread(tmp_path: Path, project_dir: Path, adjoint_home: Path) -> None:
    """Hung policies must not keep the hook process alive.

    If the worker were non-daemon (as ``ThreadPoolExecutor`` workers are), the
    atexit handler would join it and hang the interpreter. Daemon threads die
    with the process. Assert the thread we spawned is a daemon.
    """
    import threading

    _write(
        tmp_path / "hang.py",
        """
        import time
        from adjoint.policies.types import PolicyDecision
        def decide(ctx):
            time.sleep(10.0)
            return PolicyDecision(action="deny")
        """,
    )
    policies = discover_policies(tmp_path)
    pre = {t.ident for t in threading.enumerate()}
    decision = run_policies(_ctx(project_dir), policies, timeout_ms=50)
    assert decision.action == "allow"
    new_threads = [t for t in threading.enumerate() if t.ident not in pre]
    assert new_threads, "expected at least one still-alive worker"
    assert all(t.daemon for t in new_threads), "policy worker must be daemon"
