from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    agent: str
    step: str
    status: Literal["START", "OK", "ERROR", "SKIP", "DEGRADED"]
    message: str
    duration_ms: int = 0
    retry_count: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentFailure(BaseModel):
    status: Literal["ERROR"] = "ERROR"
    error: str
    agent: str
    retryable: bool = False


class RetrievalChunk(BaseModel):
    chunk_id: str
    source_kind: str
    source_ts: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    record_id: str
    kind: str
    ts: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CriticFeedback(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


class GovernanceDecision(BaseModel):
    allowed: bool = True
    degraded: bool = False
    reason: str = ""
    checks: list[str] = Field(default_factory=list)


class PendingSave(BaseModel):
    kind: Literal["log", "note"]
    text: str
    reason: str


class PendingPlan(BaseModel):
    project: str
    summary: str
    goals: list[str] = Field(default_factory=list)
    reason: str = ""


class SharedState(BaseModel):
    run_id: str
    query: str
    source: str = "cli"
    execution_status: Literal["PENDING", "RUNNING", "COMPLETED", "DEGRADED", "REJECTED"] = "PENDING"
    degraded_mode: bool = False
    governance_reason: Optional[str] = None
    retrieved_chunks: list[RetrievalChunk] = Field(default_factory=list)
    memory_context: list[MemoryRecord] = Field(default_factory=list)
    synthesis_output: Optional[str] = None
    synthesis_citations: list[str] = Field(default_factory=list)
    critic_feedback: list[CriticFeedback] = Field(default_factory=list)
    failures: list[AgentFailure] = Field(default_factory=list)
    traces: list[TraceEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeResponse(BaseModel):
    text: str
    run_id: str
    degraded: bool = False
    pending_action: Optional[Union[PendingSave, PendingPlan]] = None
    tool_result: Optional[dict[str, Any]] = None
    state: SharedState
