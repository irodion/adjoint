"""Pydantic models mirroring the SQLite schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["pending", "running", "completed", "failed", "cancelled", "timeout"]


class Run(BaseModel):
    id: str
    name: str
    status: RunStatus
    command: dict[str, Any]
    cwd: str
    provider: str
    exit_code: int | None = None
    cost_usd: float | None = None
    started_at: datetime
    ended_at: datetime | None = None


class Event(BaseModel):
    id: int | None = None
    run_id: str | None = None
    session_id: str | None = None
    ts: datetime | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class KnowledgeArticle(BaseModel):
    title: str
    kind: Literal["concept", "connection", "qa"]
    tags: list[str] = Field(default_factory=list)
    created: str
    updated: str
    sources: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    body: str = ""


class CompileState(BaseModel):
    daily_log_path: str
    sha256: str
    last_compiled_at: datetime | None = None
    cost_usd: float | None = None
