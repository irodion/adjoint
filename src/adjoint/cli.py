"""adjoint CLI — typer app exposed as ``adjoint`` console script.

M0 commands: ``install``, ``providers list``, ``status``, ``config``.
M1+ commands stub out to explanatory messages until their milestone lands.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import load_config
from .install import apply_install, build_install_plan
from .paths import claude_settings_path, user_paths
from .providers.detect import detect_all


def _resolve_project(project: Path | None) -> Path:
    """Turn a ``--project`` override (or None = cwd) into an absolute path."""
    return (project or Path.cwd()).resolve()


app = typer.Typer(
    name="adjoint",
    help="Cooperative companion for Claude Code. Hooks + MCP + memory extraction.",
    no_args_is_help=True,
    add_completion=False,
)
memory_app = typer.Typer(name="memory", help="Memory pipeline commands (M1).")
run_app = typer.Typer(name="run", help="Detached runs (M4, daemon-required).")
providers_app = typer.Typer(name="providers", help="Installed provider CLIs.")
config_app = typer.Typer(name="config", help="Inspect/edit config.toml.")
events_app = typer.Typer(name="events", help="Audit/trace event stream (M2).")

app.add_typer(memory_app)
app.add_typer(run_app)
app.add_typer(providers_app)
app.add_typer(config_app)
app.add_typer(events_app)

console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"adjoint {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Print version."
    ),
) -> None:
    """adjoint — a cooperative companion for Claude Code."""


# ── install ───────────────────────────────────────────────────────────────


@app.command()
def install(
    project: bool = typer.Option(False, "--project", help="Install into ./.claude/settings.json."),
    user: bool = typer.Option(False, "--user", help="Install into ~/.claude/settings.json."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing adjoint entries."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without writing files."),
    project_path: Path | None = typer.Option(
        None, "--path", help="Override project directory (default: cwd)."
    ),
) -> None:
    """Merge adjoint hooks and MCP server entries into Claude Code settings."""
    if project and user:
        err_console.print("[red]Choose at most one of --project / --user.[/red]")
        raise typer.Exit(2)
    scope = "user" if user else "project"
    path = project_path or (Path.cwd() if scope == "project" else None)

    plan, merged = build_install_plan(scope, path, force=force)

    table = Table(title=f"Install plan — {scope}", show_header=False, box=None)
    table.add_row("target", str(plan.target))
    table.add_row("hooks added", ", ".join(plan.hooks_added) or "—")
    table.add_row("hooks skipped (already present)", ", ".join(plan.hooks_skipped) or "—")
    table.add_row("mcp added", "yes" if plan.mcp_added else "no")
    table.add_row("mcp skipped", "yes" if plan.mcp_skipped else "no")
    console.print(table)

    if dry_run:
        console.print("[dim]--dry-run — no files written.[/dim]")
        console.print_json(data=merged)
        return

    plan = apply_install(plan, merged)

    out = Table(show_header=False, box=None)
    out.add_row("target written", str(plan.target))
    out.add_row("backup", str(plan.backup) if plan.backup else "—")
    out.add_row("~/.adjoint created", "yes" if plan.adjoint_home_created else "already present")
    out.add_row("migrations applied", ", ".join(plan.migrations_applied) or "none (up-to-date)")
    console.print(Panel(out, title="[green]installed[/green]"))
    console.print(
        "\nNext: open Claude Code in this project. Session end will run flush. "
        "For detached runs, run [bold]adjoint serve[/bold]."
    )


# ── providers list ────────────────────────────────────────────────────────


@providers_app.command("list")
def providers_list() -> None:
    """List installed provider CLIs (claude, codex) with versions."""
    detected = detect_all()
    table = Table(title="Providers")
    table.add_column("name")
    table.add_column("available")
    table.add_column("binary")
    table.add_column("version")
    for d in detected:
        table.add_row(
            d.name,
            "[green]yes[/green]" if d.available else "[red]no[/red]",
            d.binary or "—",
            d.version or "—",
        )
    console.print(table)


# ── status ────────────────────────────────────────────────────────────────


def _daemon_status() -> tuple[bool, str]:
    sock = user_paths().daemon_sock
    if not sock.exists():
        return False, "not running (run `adjoint serve`)"
    import socket as _socket

    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(str(sock))
        return True, f"running ({sock})"
    except OSError as exc:
        return False, f"socket present but not responsive ({exc})"


def _hooks_installed(scope: str, project_path: Path | None) -> tuple[int, int]:
    target = claude_settings_path(scope, project_path)
    if not target.is_file():
        return 0, 6
    try:
        data = json.loads(target.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return 0, 6
    hooks = data.get("hooks") or {}
    found = 0
    expected_events = {
        "SessionStart",
        "SessionEnd",
        "PreCompact",
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
    }
    for event, entries in hooks.items():
        if event not in expected_events:
            continue
        for entry in entries or []:
            if any("adjoint-hook-" in (h.get("command") or "") for h in (entry.get("hooks") or [])):
                found += 1
                break
    return found, len(expected_events)


@app.command()
def status(
    scope: str = typer.Option(
        "project", "--scope", help="Which settings.json to inspect (project|user)."
    ),
) -> None:
    """Report hooks installed, daemon state, provider detection, version."""
    project_path = Path.cwd() if scope == "project" else None
    found, total = _hooks_installed(scope, project_path)
    daemon_up, daemon_msg = _daemon_status()
    providers = detect_all()
    provider_str = ", ".join(f"{p.name}={'found' if p.available else 'missing'}" for p in providers)

    t = Table(title=f"adjoint {__version__}", show_header=False, box=None)
    t.add_row("scope", scope)
    t.add_row(
        "hooks installed",
        f"{found}/{total}"
        + (" [green]ok[/green]" if found == total else " [yellow]incomplete[/yellow]"),
    )
    t.add_row("daemon", daemon_msg)
    t.add_row("providers", provider_str)
    t.add_row("~/.adjoint", str(user_paths().root))
    console.print(t)

    if found < total:
        console.print(
            "\n[yellow]Some hooks are missing.[/yellow] Run "
            f"[bold]adjoint install --{scope}[/bold] to wire them up."
        )


# ── config ────────────────────────────────────────────────────────────────


@config_app.command("show")
def config_show() -> None:
    cfg = load_config(Path.cwd())
    console.print_json(data=cfg.model_dump(mode="json"))


@config_app.command("path")
def config_path() -> None:
    console.print(str(user_paths().config_toml))


@config_app.command("edit")
def config_edit() -> None:
    path = user_paths().config_toml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    editor = os.environ.get("EDITOR", "vi")
    os.execvp(editor, [editor, str(path)])


# ── memory / run / events stubs (M1+) ─────────────────────────────────────


def _not_yet(milestone: str) -> None:
    err_console.print(
        Panel(
            f"This command lands in [bold]{milestone}[/bold]. "
            "M0 ships scaffolding only — see project plan for roadmap.",
            title="[yellow]not yet implemented[/yellow]",
        )
    )
    raise typer.Exit(2)


@memory_app.command("flush")
def memory_flush(
    transcript: Path = typer.Option(
        ..., "--transcript", help="Path to Claude Code transcript JSONL."
    ),
    project: Path | None = typer.Option(
        None, "--project", help="Project directory (default: cwd)."
    ),
    reason: str = typer.Option("manual", "--reason", help="session_end | precompact | manual"),
    session_id: str | None = typer.Option(None, "--session-id"),
) -> None:
    """Distil a transcript into today's daily log."""
    from .memory.flush import FlushReason, flush

    if reason not in ("session_end", "precompact", "manual"):
        err_console.print(
            f"[red]--reason must be session_end | precompact | manual, got {reason!r}[/red]"
        )
        raise typer.Exit(2)
    reason_typed: FlushReason = reason  # type: ignore[assignment]

    project_path = _resolve_project(project)
    try:
        result = flush(
            transcript_path=transcript,
            project_path=project_path,
            session_id=session_id,
            reason=reason_typed,
        )
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]flush failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "(unknown)"
    console.print(
        Panel(
            f"turns: [bold]{result.turns}[/bold]\n"
            f"cost:  [bold]{cost}[/bold]\n"
            f"bytes: [bold]{result.bytes_appended}[/bold]\n"
            f"wrote: {result.daily_log}",
            title="[green]flush ok[/green]",
        )
    )


@memory_app.command("compile")
def memory_compile(
    all_: bool = typer.Option(False, "--all", help="Full rebuild; ignore incremental state."),
    since: str | None = typer.Option(None, "--since", help="(reserved — v2)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, no writes."),
    project: Path | None = typer.Option(None, "--project"),
) -> None:
    """Promote daily logs into concept/connection/Q&A articles."""
    from .memory.compile import compile_project

    project_path = _resolve_project(project)
    mode: Literal["incremental", "full"] = "full" if all_ else "incremental"
    try:
        result = compile_project(project_path=project_path, mode=mode, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]compile failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    t = Table(
        title=f"compile ({mode}{', dry-run' if dry_run else ''})", show_header=False, box=None
    )
    t.add_row("dirty dailies", str(len(result.dirty_daily)))
    t.add_row("articles created", ", ".join(result.articles_created) or "—")
    t.add_row("articles updated", ", ".join(result.articles_updated) or "—")
    t.add_row("unchanged (still dirty)", ", ".join(result.articles_unchanged) or "—")
    t.add_row("cost", f"${result.cost_usd:.4f}")
    if result.git_sha:
        t.add_row("git commit", result.git_sha[:12])
    console.print(t)


@memory_app.command("query")
def memory_query(
    question: str = typer.Argument(..., help="The question to answer."),
    project: Path | None = typer.Option(None, "--project"),
) -> None:
    """Answer a natural-language question from the knowledge base."""
    from .memory.query import query_knowledge

    project_path = _resolve_project(project)
    try:
        result = query_knowledge(question, project_path=project_path)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]query failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(result.answer)
    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "(unknown)"
    console.print(f"\n[dim]cost: {cost} · {result.duration_ms}ms[/dim]")


@memory_app.command("lint")
def memory_lint(
    cheap: bool = typer.Option(False, "--cheap", help="Skip LLM checks 6 and 7."),
    project: Path | None = typer.Option(None, "--project"),
) -> None:
    """Run the seven KB health checks and write .lint-report.md."""
    from .memory.lint import lint

    project_path = _resolve_project(project)
    try:
        report = lint(project_path=project_path, cheap=cheap)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]lint failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    by_check = report.by_check()
    t = Table(title=f"lint — {report.articles_scanned} articles", show_header=True)
    t.add_column("check")
    t.add_column("count", justify="right")
    for check in (
        "broken_wikilink",
        "orphan",
        "stale",
        "sparse",
        "missing_backlink",
        "near_duplicate",
        "contradiction",
    ):
        t.add_row(check, str(len(by_check.get(check, []))))
    console.print(t)
    console.print(f"[dim]report: knowledge/.lint-report.md · cost ${report.cost_usd:.4f}[/dim]")


@run_app.command("list")
def run_list(status: str | None = typer.Option(None, "--status")) -> None:
    _not_yet("M4")


@run_app.command("status")
def run_status_cmd(id: str = typer.Argument(...)) -> None:
    _not_yet("M4")


@run_app.command("cancel")
def run_cancel(id: str = typer.Argument(...)) -> None:
    _not_yet("M4")


@run_app.command("logs")
def run_logs(
    id: str = typer.Argument(...), follow: bool = typer.Option(False, "-f", "--follow")
) -> None:
    _not_yet("M4")


@events_app.command("tail")
def events_tail(
    n: int = typer.Option(20, "-n"),
    follow: bool = typer.Option(False, "-f", "--follow"),
    type_: str | None = typer.Option(None, "--type"),
) -> None:
    _not_yet("M2")


@app.command()
def serve(
    socket_path: str | None = typer.Option(None, "--socket"),
    http_port: int | None = typer.Option(None, "--http-port"),
    foreground: bool = typer.Option(False, "--foreground"),
) -> None:
    """Start the optional daemon (M4)."""
    _not_yet("M4")


if __name__ == "__main__":  # pragma: no cover
    app()
