# adjoint

A cooperative companion for Claude Code. Hooks + MCP primitives + automated memory extraction, delivered as a single `uv`-installable Python package.

- **Hooks-first**: six co-equal hook roles (audit, policy, enrichment, side-effect, routing, tracing).
- **MCP primitives, not workflows**: `second_opinion`, `variants`, `memory_query`, detached runs. You own plan/review/implement.
- **Obsidian-friendly memory**: every session is distilled into a markdown knowledge base with `[[wikilinks]]`.
- **Optional daemon**: default is hook scripts + stdio MCP, no long-running process. `adjoint serve` unlocks detached runs.

Status: **alpha, under active construction.** See `docs/plan.md` / project plan for milestone scope.

## Install

```bash
uv tool install adjoint
cd ~/my-project
adjoint install --project
```

## Hook surface

Six Claude Code hooks land in `.claude/settings.json` on install:
`SessionStart` / `SessionEnd` / `PreCompact` drive the memory pipeline;
`PreToolUse` runs user policies; `PostToolUse` writes the audit stream;
`UserPromptSubmit` injects related concepts into prompts (opt-in).

### Policies

Drop a Python file at `~/.adjoint/policies/enabled/*.py` to allow, ask, or deny tool calls. Each module exports a top-level `decide(ctx)`:

```python
from adjoint.policies.types import PolicyDecision, ToolUseContext

def decide(ctx: ToolUseContext) -> PolicyDecision:
    if ctx.tool_name == "Bash" and "rm -rf" in ctx.tool_input.get("command", ""):
        return PolicyDecision(action="ask", reason="please confirm `rm -rf`")
    return PolicyDecision(action="allow")
```

Composition is `deny > ask > allow`. Each policy runs with a per-policy timeout (`policies.timeout_ms`, default 500 ms) and fails open on any error — adjoint is **not** a security boundary.

`adjoint install` copies three starter examples into `~/.adjoint/policies/disabled/` (`no_writes_outside_repo`, `safe_bash`, `log_only`); symlink one into `enabled/` to activate.

### Audit

PostToolUse records every tool invocation to `~/.adjoint/events.db`. Tail the stream:

```bash
adjoint events tail -n 20
adjoint events tail --type hook. -f      # follow, hook.* events only
```

Disable storage entirely with `[audit] enabled = false` in `~/.adjoint/config.toml`.

## Development

```bash
uv venv .venv --python 3.12
uv pip install -e '.[all,dev]'
uv run pre-commit install
```

The pre-commit suite runs on every `git commit` (and via `uv run pre-commit run --all-files`):

| Hook | Purpose |
|---|---|
| `ruff check --fix` | lint with auto-fix |
| `ruff format` | code formatter |
| `mypy` | static type check (`src/adjoint` tree) |
| `bandit` | security scan |
| `pre-commit-hooks` | trailing whitespace, EOF, JSON/YAML/TOML syntax, merge conflicts, large files |

Test suite: `uv run pytest`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
