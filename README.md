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
