"""Natural-language question answering over the knowledge base.

The agent is given:

* The current ``knowledge/index.md`` as leading context (fast, full-overview).
* ``Read``, ``Glob``, ``Grep`` filesystem tools scoped to ``knowledge/`` —
  no Write, no Edit, no Bash.
* A hard spend cap from ``config.memory.query_max_cost_usd`` (default $0.10).

We deliberately do not use embeddings or a vector store: at <2000 articles
the index + grep strategy is both faster and more honest (it points at
real files the user can open).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config, load_config
from ..log import get_logger, log_event
from ..paths import UserPaths, user_paths
from .agent import AgentClient, AgentRequest, complete_sync, default_client

QUERY_SYSTEM_PROMPT = """\
You answer questions using a local Obsidian-style markdown knowledge base.

You have access to Read, Glob, and Grep scoped to the knowledge/ directory.
Use the provided index first; open specific articles only when needed to
answer the question.

Rules:
- Ground every claim in the articles you actually read. If you didn't read
  it, don't cite it.
- Quote short snippets with > blockquotes when they answer the question
  directly.
- End with a ``## Sources`` section listing every article you read, as
  `[[<relative/path>]]`. If you read none, say so.
- If the KB has no relevant information, say that plainly rather than
  speculating.
"""


@dataclass(frozen=True)
class QueryResult:
    answer: str
    cost_usd: float | None
    duration_ms: int


def query_knowledge(
    question: str,
    *,
    project_path: Path,
    config: Config | None = None,
    client: AgentClient | None = None,
    paths: UserPaths | None = None,
) -> QueryResult:
    cfg = config or load_config(project_path)
    agent = client or default_client()
    up = paths or user_paths()
    pp = up.project(project_path)
    pp.ensure()

    index_text = (
        pp.knowledge_index.read_text(encoding="utf-8")
        if pp.knowledge_index.is_file()
        else "_(knowledge/index.md not yet generated — run `adjoint memory compile` first)_"
    )

    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Knowledge index (knowledge/index.md):\n---\n{index_text}\n---\n\n"
        "Answer the question using the KB. Open specific articles with Read "
        "as needed."
    )

    req = AgentRequest(
        system=QUERY_SYSTEM_PROMPT,
        user=user_prompt,
        model=cfg.model_for("claude"),
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=10,
        max_budget_usd=cfg.memory.query_max_cost_usd,
        cwd=pp.knowledge_dir,
        add_dirs=[pp.knowledge_dir],
        recursion_tag="adjoint_query",
    )
    resp = complete_sync(agent, req)

    logger = get_logger("memory.query")
    log_event(
        logger,
        "query.ok",
        question=question[:200],
        cost_usd=resp.cost_usd,
        duration_ms=resp.duration_ms,
    )
    return QueryResult(
        answer=resp.text.strip(),
        cost_usd=resp.cost_usd,
        duration_ms=resp.duration_ms,
    )
