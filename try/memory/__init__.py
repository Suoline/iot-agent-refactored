from __future__ import annotations

from pathlib import Path

from .schemas import (
    CandidateDevice,
    CandidateResolution,
    EvidenceRef,
    ExtractedMemoryCandidate,
    GlobalConstraint,
    MatchedMemory,
    MemoryRecord,
    MemoryUsageEvent,
    SearchResultPackage,
)
from .service import MemoryService

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PACKAGE_ROOT / "runtime" / "memory_v1.sqlite3"


def create_memory_service(db_path: str | Path | None = None, *, user_id: str = "user.primary") -> MemoryService:
    return MemoryService(db_path or DEFAULT_DB_PATH, user_id=user_id)


__all__ = [
    "CandidateDevice",
    "CandidateResolution",
    "DEFAULT_DB_PATH",
    "EvidenceRef",
    "ExtractedMemoryCandidate",
    "GlobalConstraint",
    "MatchedMemory",
    "MemoryRecord",
    "MemoryService",
    "MemoryUsageEvent",
    "SearchResultPackage",
    "create_memory_service",
]
