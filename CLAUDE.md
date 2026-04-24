# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**adjoint** is a cooperative companion for Claude Code: hook scripts + stdio MCP server + optional daemon, plus a memory-extraction pipeline that distils session transcripts into an Obsidian-style markdown knowledge base. It is deliberately *not* a workflow platform — it ships primitives (`second_opinion`, `variants`, `memory_query`, detached runs) and leaves plan/review/implement flows to the user.

v1 is **Claude Code-only as a hook host**; other CLIs (`codex`, etc.) are used as providers via subprocess.

## Commands

```bash
# one-time setup
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e '.[all,dev]'
.venv/bin/pre-commit install

# run everything the commit hook would run
.venv/bin/pre-commit run --all-files

# individual tools (each mirrors a pre-commit hook)
.venv/bin/ruff check src/ tests/            # lint
.venv/bin/ruff format src/ tests/           # format
.venv/bin/mypy                              # types (reads [tool.mypy] files=)
.venv/bin/bandit -c pyproject.toml -r src/  # security

# tests — the ADJOINT_HOME env-override fixture isolates filesystem state
.venv/bin/python -m pytest                  # all
.venv/bin/python -m pytest tests/test_memory_compile.py -q
.venv/bin/python -m pytest -k "incremental" # single test by substring

# exercise the CLI surface (re-run after code changes editable-installs)
.venv/bin/adjoint --help
.venv/bin/adjoint install --project --dry-run
.venv/bin/adjoint status
.venv/bin/adjoint providers list
```

`uv run` prefixes also work. The tests install hook binaries as console scripts (`adjoint-hook-session-start`, etc.) and invoke them via `subprocess`, so an editable install must be live before `pytest`.

## Big picture

Three co-equal tiers, each usable without the others:

1. **Hook scripts** — installed as pinned console binaries (`adjoint-hook-*`) that Claude Code invokes per lifecycle event. Budgets are tight (300 ms – 2 s); every hook **fails open**. Never treat a hook failure as blocking; adjoint is **not a security boundary**.
2. **stdio MCP server** (`adjoint-mcp`) — the IDE agent calls tools during a turn.
3. **Optional daemon** (`adjoint serve`) — unlocks detached runs, scheduled compile, shared event stream.

Hooks + MCP run standalone. Daemon tools return `DaemonNotRunningError` with the exact start command when the socket is absent; **no auto-start**.

### Storage layout

State lives in `~/.adjoint/` (override with `ADJOINT_HOME` env var — every test uses this). Per-project scope is `~/.adjoint/projects/<sha1(abs_path)[:12]>/`. The user's repo is never polluted with tool-specific dirs.

- `events.db` — SQLite WAL for runs, events, compile_state, knowledge_log
- `projects/<hash>/daily/YYYY-MM-DD.md` — append-only session extracts
- `projects/<hash>/knowledge/{concepts,connections,qa,index.md}` — git-backed KB (each `compile` is a commit)
- `projects/<hash>/state.json` — sha256 dependency graph for incremental compile

### Memory pipeline (the anchor feature)

```
SessionStart → inject knowledge/index.md as additionalContext
     ... session ...
PreCompact / SessionEnd → spawn detached `adjoint memory flush` via Popen(start_new_session=True)
     flush → last-30-turns / 15 000-char tail of transcript → redact (pre-LLM) → Claude Agent SDK call (allowed_tools=[]) → redact (post-LLM) → append to daily/YYYY-MM-DD.md
     compile (incremental, sha256-gated) → for each dirty daily, extract concept candidates → merge/create articles → regenerate index.md → git commit
```

`compile.py` deliberately keeps filesystem tools off the LLM side: Python owns all I/O and slug-based identity; the LLM is a pure text transformer. This is cheaper and easier to test than giving an agent Write permission.

Two passes in `compile_project`:
1. **Pass 1** — LLM extraction + merge/create for articles whose source dailies are dirty.
2. **Pass 2** — deterministic `## Backlinks` regeneration using a cached single read of every article (`_load_articles_on_disk` shared with `_collect_backlinks`). Pass 2 only writes when rendered output differs from disk, so incremental no-op runs produce no git commit.

### Recursion guard (load-bearing)

Every adjoint subprocess sets `CLAUDE_INVOKED_BY` to a value in the `RecursionTag` Literal (`adjoint`, `adjoint_flush`, `adjoint_compile`, `adjoint_query`, `adjoint_second_opinion`, `adjoint_variants`, `adjoint_run`). Hooks short-circuit when they see any of these. This is what prevents e.g. a flush subprocess → `claude` → fires `SessionEnd` → spawns *another* flush.

Use `log.child_env(tag)` when building subprocess env and `log.is_recursive_invocation()` at hook entry.

Tags flow through:
- `hooks/_runtime.run_hook` → checks `is_recursive_invocation()` before running handler.
- `hooks/_flush_spawn.spawn_flush` → `Popen(env=child_env("adjoint_flush"))`.
- `memory/agent.AgentRequest.recursion_tag` → propagated into `claude-agent-sdk` via `env=`.
- `providers/base.spawn` → same.

### Provider subprocess discipline

`providers/base.spawn()` and `hooks/_flush_spawn.spawn_flush()` are the two subprocess seams. Both use argv-list + `shell=False`. `tests/test_security.py` AST-walks every `.py` under `src/` and fails if any `subprocess` call uses `shell=True`. **Do not introduce `shell=True`.**

`bandit` skips B404/B603/B606/B607 repo-wide (see `pyproject.toml` comment) because they fire on every subprocess call in a codebase that intentionally uses subprocess; the real security invariant is the AST test.

### Shared primitives to reuse (not duplicate)

Before adding regex or parser logic in `memory/`, check `memory/_shared.py`:

- `parse_frontmatter(text) -> (fm, body)`
- `strip_backlinks(body)`
- `wikilink_targets(text) -> set[str]`
- `first_paragraph(body) -> str`
- `KINDS` / `Kind` — the tuple/Literal for `"concept" | "connection" | "qa"`

These were extracted from three separate modules; don't re-inline them.

### Hook settings format

`src/adjoint/bundled/settings.hooks.json` and `settings.mcp.json` are merged into the target `.claude/settings.json` by `install.build_install_plan` + `apply_install`:

- `hooks.<Event>` lists are **appended to** (detected via `adjoint-hook-` substring on the command); `--force` overwrites the adjoint entry only.
- `mcpServers.adjoint` is set; other user-defined servers are preserved.
- A `.bak.<unix-ts>` backup is created **only when content actually changes** — idempotent reinstalls don't leave junk files.

### CLI ↔ module boundary

`cli.py` does rich output + typer glue; all work happens in `memory.*`, `install`, `providers.*`. Module-level imports in `cli.py` are kept minimal so `--version` and `--help` don't pull in `claude-agent-sdk`. The `from .memory.{flush,compile,query,lint} import …` statements live **inside** the command handlers by design.

### Milestone status

What's live (M0 + M1): `install`, `status`, `providers list`, `config show|edit|path`, `memory flush|compile|query|lint`, SessionStart/SessionEnd/PreCompact hooks, the SQLite schema + migration runner.

Stubbed (exit with an explanatory message — `_not_yet(milestone)` in `cli.py`):
- M2 — policy loader + real PreToolUse/PostToolUse/UserPromptSubmit, `adjoint events tail`
- M3 — stdio MCP tools (`adjoint-mcp` currently exits 1)
- M4 — daemon (`adjoint serve`, `adjoint run *`)

Don't route new work through these stubs; build out the corresponding module instead.

## Test conventions

- `tests/conftest.py` provides `adjoint_home` (sets `ADJOINT_HOME=$tmp_path/adjoint-home`), `project_dir`, autouse `_no_recursion_marker`, and the shared `write_article` helper.
- `tests/fake_agent.py::FakeAgent` implements the `AgentClient` protocol with a response queue — any memory test that would otherwise call `claude-agent-sdk` must pass `client=FakeAgent().enqueue(...)` into `flush()` / `compile_project()` / `lint()` / etc.
- Hook subprocess tests locate binaries via `Path(sys.executable).parent / "adjoint-hook-*"`, which works for both `pytest` in the editable venv and future CI.

## Config

`~/.adjoint/config.toml` is optional (zero-config works). Project override at `./.adjoint/config.toml` shallow-merges over user config (top-level section keys replace). `Config.model_for(provider_name)` is the one-liner to resolve the configured model string — don't re-implement `cfg.providers.get(...)` plumbing at call sites.
