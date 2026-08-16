from __future__ import annotations

from typing import Any

from experiments.memory.schemas import CandidateDevice, MatchedMemory, SearchResultPackage
from experiments.memory.vector_index import _tokens


def _entity_id_from_item(item: dict[str, Any]) -> str | None:
    explicit = item.get("entity_id")
    if explicit:
        return explicit
    candidate = str(item.get("object", ""))
    return candidate if "." in candidate else None


def build_raw_text_package(
    *,
    query: str,
    task_type: str,
    fixture: list[dict[str, Any]],
    conversation_history: list[str],
    world,
    full_history: bool,
) -> SearchResultPackage:
    """Build B1/B4 context without MemoryService records or lifecycle fields."""
    documents = []
    for index, item in enumerate(fixture, start=1):
        text = item.get("natural_text") or " ".join(
            str(item.get(key, "")) for key in ("subject", "predicate", "object")
        ).strip()
        documents.append(
            {"memory_id": f"raw_fixture_{index}", "text": text, "entity_id": _entity_id_from_item(item)}
        )
    documents.extend(
        {"memory_id": f"conversation_{index}", "text": text, "entity_id": None}
        for index, text in enumerate(conversation_history, start=1)
    )
    query_tokens = _tokens(query)
    ranked = []
    for item in documents:
        text_tokens = _tokens(item["text"])
        score = len(query_tokens & text_tokens) / len(query_tokens | text_tokens or {"_"})
        if full_history or score > 0:
            ranked.append((score, item))
    ranked.sort(key=lambda row: (-row[0], row[1]["memory_id"]))
    if not full_history:
        ranked = ranked[:5]
    matches = [
        MatchedMemory(
            memory_id=item["memory_id"], memory_type="raw_history", text=item["text"],
            score=score, raw_confidence=1.0, effective_confidence=1.0, memory_worth=0.0,
            system_status="raw_text", true_status="not_applicable", runtime_status="raw_text",
            layer="not_applicable", in_usable_set=True,
        )
        for score, item in ranked
    ]
    candidates = []
    for score, item in ranked:
        entity_id = item.get("entity_id")
        if entity_id in world.entities:
            candidates.append(CandidateDevice(
                entity_id=entity_id, name=world.entities[entity_id].get("display_name", entity_id),
                score=score, confidence=1.0,
            ))
    candidates.sort(key=lambda item: (-item.score, item.entity_id))
    return SearchResultPackage(
        query=query, matched_memories=matches, candidate_devices=candidates[:5],
        should_ask_user=not bool(candidates),
        ask_reason="raw_text_context_has_no_resolved_entity" if not candidates else None,
        task_type=task_type,
        retrieval_metadata={
            "baseline_context_source": "full_raw_history" if full_history else "raw_text_rag",
            "raw_document_count": len(documents),
            "raw_context_document_ids": [item["memory_id"] for _, item in ranked],
        },
    )
