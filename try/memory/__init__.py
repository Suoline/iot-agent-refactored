from __future__ import annotations

from pathlib import Path

from .extractor import HeuristicMemoryExtractor, LLMStructuredMemoryExtractor
from .llm_support import LangChainStructuredInvoker
from .resolver import HeuristicDisambiguator, LLMDisambiguator
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


def create_memory_service(
    db_path: str | Path | None = None,
    *,
    user_id: str = "user.primary",
    extractor=None,
    disambiguator=None,
) -> MemoryService:
    return MemoryService(
        db_path or DEFAULT_DB_PATH,
        user_id=user_id,
        extractor=extractor,
        disambiguator=disambiguator,
    )


__all__ = [
    "CandidateDevice",
    "CandidateResolution",
    "DEFAULT_DB_PATH",
    "EvidenceRef",
    "ExtractedMemoryCandidate",
    "GlobalConstraint",
    "HeuristicDisambiguator",
    "HeuristicMemoryExtractor",
    "LLMDisambiguator",
    "LLMStructuredMemoryExtractor",
    "LangChainStructuredInvoker",
    "MatchedMemory",
    "MemoryRecord",
    "MemoryService",
    "MemoryUsageEvent",
    "SearchResultPackage",
    "create_memory_service",
]
