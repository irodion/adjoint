"""In-memory fake of ``adjoint.memory.agent.AgentClient`` for tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from adjoint.memory.agent import AgentRequest, AgentResponse


@dataclass
class FakeAgent:
    """Return canned responses in order.

    Each entry is either (a) a string — returned as the response text, or
    (b) a callable taking the AgentRequest and returning the response text.
    Cost defaults to 0.01 per call; override via ``cost_per_call``.
    """

    responses: deque[str | Callable[[AgentRequest], str]] = field(default_factory=deque)
    cost_per_call: float = 0.01
    calls: list[dict[str, Any]] = field(default_factory=list)

    def enqueue(self, *resps: str | Callable[[AgentRequest], str]) -> FakeAgent:
        self.responses.extend(resps)
        return self

    async def complete(self, req: AgentRequest) -> AgentResponse:
        self.calls.append(
            {
                "system": req.system,
                "user": req.user,
                "allowed_tools": list(req.allowed_tools),
                "model": req.model,
                "recursion_tag": req.recursion_tag,
                "cwd": str(req.cwd) if req.cwd else None,
            }
        )
        if not self.responses:
            raise AssertionError("FakeAgent called but no response was queued")
        nxt = self.responses.popleft()
        text = nxt(req) if callable(nxt) else nxt
        return AgentResponse(text=text, cost_usd=self.cost_per_call, duration_ms=1)
