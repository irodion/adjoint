"""Thin wrapper around ``claude-agent-sdk`` so flush/compile/query share plumbing.

Responsibilities:

* Translate our ``AgentRequest`` into ``ClaudeAgentOptions`` + prompt.
* Collect ``AssistantMessage`` text blocks into a single response string.
* Surface ``total_cost_usd`` from ``ResultMessage``.
* Always inject the recursion guard env var so any hooks fired by the spawned
  ``claude`` subprocess short-circuit immediately.
* ``setting_sources=[]`` by default — adjoint's internal agent calls should
  NEVER load the user's .claude/settings.json, which would (a) risk pulling in
  adjoint's own hooks/MCP and (b) slow down every internal call.

This module is the ONE place we depend on ``claude_agent_sdk`` at runtime, so
tests can swap in a fake ``AgentClient`` without monkey-patching the SDK.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..log import RecursionTag, child_env


@dataclass(frozen=True)
class AgentRequest:
    system: str
    user: str
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 1
    max_budget_usd: float | None = None
    cwd: Path | None = None
    add_dirs: list[Path] | None = None
    recursion_tag: RecursionTag = "adjoint"


@dataclass(frozen=True)
class AgentResponse:
    text: str
    cost_usd: float | None
    duration_ms: int


class AgentClient(Protocol):
    async def complete(self, req: AgentRequest) -> AgentResponse: ...


class ClaudeAgentClient:
    """Default implementation — calls ``claude_agent_sdk.query`` under the hood."""

    async def complete(self, req: AgentRequest) -> AgentResponse:
        # Import lazily so that importing `adjoint.memory.agent` at module load
        # time doesn't require `claude-agent-sdk` to be installed (hooks in M0
        # scaffold don't have it yet).
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=req.system,
            max_turns=req.max_turns,
            allowed_tools=list(req.allowed_tools),
            setting_sources=[],  # do NOT load user's settings.json — see module docstring
            model=req.model,
            max_budget_usd=req.max_budget_usd,
            cwd=str(req.cwd) if req.cwd else None,
            add_dirs=[str(p) for p in (req.add_dirs or [])],
            env=child_env(req.recursion_tag),
        )

        start = time.monotonic()
        chunks: list[str] = []
        cost_usd: float | None = None
        async for message in query(prompt=req.user, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
            elif isinstance(message, ResultMessage):
                cost_usd = getattr(message, "total_cost_usd", None)
        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResponse(text="".join(chunks), cost_usd=cost_usd, duration_ms=duration_ms)


def complete_sync(client: AgentClient, req: AgentRequest) -> AgentResponse:
    """Blocking entry point for sync callers (flush, compile, CLI)."""
    return asyncio.run(client.complete(req))


def default_client() -> AgentClient:
    return ClaudeAgentClient()
