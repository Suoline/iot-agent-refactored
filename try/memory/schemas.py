from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryScope = Literal["entity", "device", "room", "user", "home"]
MemoryType = Literal[
    "capability",
    "alias",
    "location",
    "preference",
    "habit",
    "constraint",
    "routine",
    "episode",
    "reflection",
    "layout_relation",
    "safety_rule",
    "stable_state_fact",
]
MemorySource = Literal[
    "ha_registry",
    "ha_state_observation",
    "user_explicit",
    "user_correction",
    "user_behavior",
    "execution_verification",
    "llm_inference",
    "imported_doc",
]
MemoryStatus = Literal[
    "candidate",
    "active",
    "stale",
    "conflicted",
    "superseded",
    "expired",
    "archived",
    "deleted",
]
OperationHint = Literal["add", "add_active", "merge", "revise", "invalidate", "delete"]
ResolutionState = Literal["bound", "downgraded", "unresolved"]
UsageStage = Literal["device_filter", "constraint_filter", "planning", "execution", "verification", "unknown"]
UsageContribution = Literal["helpful", "neutral", "misleading", "unknown"]
TaskOutcome = Literal["success", "partial_success", "failure"]


class EvidenceRef(BaseModel):
    ref_type: Literal["turn", "trace", "doc", "event"]
    ref_id: str
    timestamp: datetime | None = None


class ExtractedMemoryCandidate(BaseModel):
    memory_type: MemoryType
    scope_hint: MemoryScope
    subject_text: str
    predicate: str
    object_text: str
    condition: str | None = None
    action: str | None = None
    room_text: str | None = None
    alias_text: str | None = None
    raw_mentions: list[str] = Field(default_factory=list)
    source: MemorySource
    operation_hint: OperationHint = "add"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    natural_text: str | None = None
    structured_payload: dict[str, Any] = Field(default_factory=dict)


class CandidateResolution(BaseModel):
    scope: MemoryScope
    resolution_state: ResolutionState
    device_id: str | None = None
    entity_id: str | None = None
    room_id: str | None = None
    user_id: str | None = None
    candidate_device_ids: list[str] = Field(default_factory=list)
    candidate_entity_ids: list[str] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    memory_id: str
    scope: MemoryScope
    device_id: str | None = None
    entity_id: str | None = None
    room_id: str | None = None
    user_id: str | None = None
    memory_type: MemoryType
    subject: str
    predicate: str
    object: str
    condition: str | None = None
    action: str | None = None
    natural_text: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    source: MemorySource
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float
    importance: float = 0.5
    half_life_days: int
    positive_hits: int = 0
    negative_hits: int = 0
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    status: MemoryStatus = "candidate"
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    conflicts_with: list[str] = Field(default_factory=list)
    access_count: int = 0
    update_count: int = 0
    last_used_task_id: str | None = None


class MemoryUsageEvent(BaseModel):
    task_id: str
    memory_id: str
    used_stage: UsageStage
    contribution: UsageContribution
    outcome: TaskOutcome
    verification_delta: float | None = None
    note: str = ""


class MatchedMemory(BaseModel):
    memory_id: str
    type: MemoryType
    text: str
    confidence: float
    status: MemoryStatus
    scope: MemoryScope
    device_id: str | None = None
    entity_id: str | None = None
    room_id: str | None = None
    retrieval_score: float = 0.0


class CandidateDevice(BaseModel):
    device_id: str
    name: str
    score: float
    confidence: float
    matched_memories: list[MatchedMemory] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class GlobalConstraint(BaseModel):
    memory_id: str
    text: str
    confidence: float
    memory_type: MemoryType
    scope: MemoryScope


class SearchResultPackage(BaseModel):
    candidate_devices: list[CandidateDevice] = Field(default_factory=list)
    matched_memories: list[MatchedMemory] = Field(default_factory=list)
    global_constraints: list[GlobalConstraint] = Field(default_factory=list)
    should_ask_user: bool = False
    ask_reason: str | None = None

