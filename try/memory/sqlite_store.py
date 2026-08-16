from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .schemas import EvidenceRef, MemoryRecord, MemoryUsageEvent


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL,
                    device_id TEXT,
                    entity_id TEXT,
                    room_id TEXT,
                    user_id TEXT,
                    memory_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_text TEXT NOT NULL,
                    condition_text TEXT,
                    action_text TEXT,
                    natural_text TEXT NOT NULL,
                    structured_payload TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_refs TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    half_life_days INTEGER NOT NULL,
                    positive_hits INTEGER NOT NULL,
                    negative_hits INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    observed_at TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    status TEXT NOT NULL,
                    supersedes TEXT NOT NULL,
                    superseded_by TEXT,
                    conflicts_with TEXT NOT NULL,
                    access_count INTEGER NOT NULL,
                    update_count INTEGER NOT NULL,
                    last_used_task_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    task_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_usage_events (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    used_stage TEXT NOT NULL,
                    contribution TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    verification_delta REAL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ha_sync_runs (
                    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(
                    memory_id UNINDEXED,
                    natural_text,
                    subject,
                    predicate,
                    object_text,
                    condition_text
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_records(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_records(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_source ON memory_records(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_device ON memory_records(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_entity ON memory_records(entity_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_room ON memory_records(room_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_subject_predicate ON memory_records(subject, predicate)")

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            scope=row["scope"],
            device_id=row["device_id"],
            entity_id=row["entity_id"],
            room_id=row["room_id"],
            user_id=row["user_id"],
            memory_type=row["memory_type"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object_text"],
            condition=row["condition_text"],
            action=row["action_text"],
            natural_text=row["natural_text"],
            structured_payload=_json_loads(row["structured_payload"], {}),
            source=row["source"],
            evidence_refs=[EvidenceRef(**item) for item in _json_loads(row["evidence_refs"], [])],
            confidence=row["confidence"],
            importance=row["importance"],
            half_life_days=row["half_life_days"],
            positive_hits=row["positive_hits"],
            negative_hits=row["negative_hits"],
            created_at=_dt_from_str(row["created_at"]) or datetime.now(),
            updated_at=_dt_from_str(row["updated_at"]) or datetime.now(),
            last_accessed_at=_dt_from_str(row["last_accessed_at"]),
            observed_at=_dt_from_str(row["observed_at"]),
            valid_from=_dt_from_str(row["valid_from"]),
            valid_until=_dt_from_str(row["valid_until"]),
            status=row["status"],
            supersedes=_json_loads(row["supersedes"], []),
            superseded_by=row["superseded_by"],
            conflicts_with=_json_loads(row["conflicts_with"], []),
            access_count=row["access_count"],
            update_count=row["update_count"],
            last_used_task_id=row["last_used_task_id"],
        )

    def upsert_record(self, record: MemoryRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_records (
                    memory_id, scope, device_id, entity_id, room_id, user_id,
                    memory_type, subject, predicate, object_text, condition_text, action_text,
                    natural_text, structured_payload, source, evidence_refs,
                    confidence, importance, half_life_days, positive_hits, negative_hits,
                    created_at, updated_at, last_accessed_at, observed_at, valid_from, valid_until,
                    status, supersedes, superseded_by, conflicts_with,
                    access_count, update_count, last_used_task_id
                ) VALUES (
                    :memory_id, :scope, :device_id, :entity_id, :room_id, :user_id,
                    :memory_type, :subject, :predicate, :object_text, :condition_text, :action_text,
                    :natural_text, :structured_payload, :source, :evidence_refs,
                    :confidence, :importance, :half_life_days, :positive_hits, :negative_hits,
                    :created_at, :updated_at, :last_accessed_at, :observed_at, :valid_from, :valid_until,
                    :status, :supersedes, :superseded_by, :conflicts_with,
                    :access_count, :update_count, :last_used_task_id
                )
                ON CONFLICT(memory_id) DO UPDATE SET
                    scope=excluded.scope,
                    device_id=excluded.device_id,
                    entity_id=excluded.entity_id,
                    room_id=excluded.room_id,
                    user_id=excluded.user_id,
                    memory_type=excluded.memory_type,
                    subject=excluded.subject,
                    predicate=excluded.predicate,
                    object_text=excluded.object_text,
                    condition_text=excluded.condition_text,
                    action_text=excluded.action_text,
                    natural_text=excluded.natural_text,
                    structured_payload=excluded.structured_payload,
                    source=excluded.source,
                    evidence_refs=excluded.evidence_refs,
                    confidence=excluded.confidence,
                    importance=excluded.importance,
                    half_life_days=excluded.half_life_days,
                    positive_hits=excluded.positive_hits,
                    negative_hits=excluded.negative_hits,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    last_accessed_at=excluded.last_accessed_at,
                    observed_at=excluded.observed_at,
                    valid_from=excluded.valid_from,
                    valid_until=excluded.valid_until,
                    status=excluded.status,
                    supersedes=excluded.supersedes,
                    superseded_by=excluded.superseded_by,
                    conflicts_with=excluded.conflicts_with,
                    access_count=excluded.access_count,
                    update_count=excluded.update_count,
                    last_used_task_id=excluded.last_used_task_id
                """,
                {
                    "memory_id": record.memory_id,
                    "scope": record.scope,
                    "device_id": record.device_id,
                    "entity_id": record.entity_id,
                    "room_id": record.room_id,
                    "user_id": record.user_id,
                    "memory_type": record.memory_type,
                    "subject": record.subject,
                    "predicate": record.predicate,
                    "object_text": record.object,
                    "condition_text": record.condition,
                    "action_text": record.action,
                    "natural_text": record.natural_text,
                    "structured_payload": _json_dumps(record.structured_payload),
                    "source": record.source,
                    "evidence_refs": _json_dumps([item.model_dump(mode="json") for item in record.evidence_refs]),
                    "confidence": record.confidence,
                    "importance": record.importance,
                    "half_life_days": record.half_life_days,
                    "positive_hits": record.positive_hits,
                    "negative_hits": record.negative_hits,
                    "created_at": _dt_to_str(record.created_at),
                    "updated_at": _dt_to_str(record.updated_at),
                    "last_accessed_at": _dt_to_str(record.last_accessed_at),
                    "observed_at": _dt_to_str(record.observed_at),
                    "valid_from": _dt_to_str(record.valid_from),
                    "valid_until": _dt_to_str(record.valid_until),
                    "status": record.status,
                    "supersedes": _json_dumps(record.supersedes),
                    "superseded_by": record.superseded_by,
                    "conflicts_with": _json_dumps(record.conflicts_with),
                    "access_count": record.access_count,
                    "update_count": record.update_count,
                    "last_used_task_id": record.last_used_task_id,
                },
            )
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (record.memory_id,))
            conn.execute(
                """
                INSERT INTO memory_fts(memory_id, natural_text, subject, predicate, object_text, condition_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id,
                    record.natural_text,
                    record.subject,
                    record.predicate,
                    record.object,
                    record.condition or "",
                ),
            )

    def get_record(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def delete_record(self, memory_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM memory_records WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))

    def list_records(
        self,
        *,
        statuses: list[str] | None = None,
        memory_types: list[str] | None = None,
        scope: str | None = None,
        source: str | None = None,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if memory_types:
            clauses.append(f"memory_type IN ({','.join('?' for _ in memory_types)})")
            params.extend(memory_types)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if source:
            clauses.append("source = ?")
            params.append(source)
        sql = "SELECT * FROM memory_records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def find_records(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        scope: str | None = None,
        memory_type: str | None = None,
        device_id: str | None = None,
        entity_id: str | None = None,
        room_id: str | None = None,
        statuses: list[str] | None = None,
        source: str | None = None,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if room_id is not None:
            clauses.append("room_id = ?")
            params.append(room_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        sql = "SELECT * FROM memory_records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search_fts(self, query: str, limit: int = 10) -> list[str]:
        prepared = " ".join(part for part in query.strip().split() if part)
        if not prepared:
            return []
        # Treat user/LLM text as a literal FTS phrase. Without quoting, text
        # such as "Error code: 400" is parsed as a column-qualified query and
        # can raise "no such column" for the token after the colon.
        prepared = '"' + prepared.replace('"', '""') + '"'
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT memory_id
                FROM memory_fts
                WHERE memory_fts MATCH ?
                ORDER BY bm25(memory_fts)
                LIMIT ?
                """,
                (prepared, limit),
            ).fetchall()
        return [row["memory_id"] for row in rows]

    def touch_access(self, memory_id: str, task_id: str | None, accessed_at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE memory_records
                SET last_accessed_at = ?, access_count = access_count + 1, last_used_task_id = COALESCE(?, last_used_task_id)
                WHERE memory_id = ?
                """,
                (_dt_to_str(accessed_at), task_id, memory_id),
            )

    def add_event(
        self,
        event_type: str,
        *,
        memory_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_events(event_type, memory_id, task_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, memory_id, task_id, _json_dumps(payload or {}), _dt_to_str(created_at)),
            )

    def add_usage_event(self, event: MemoryUsageEvent, created_at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_usage_events(
                    task_id, memory_id, used_stage, contribution, outcome, verification_delta, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.task_id,
                    event.memory_id,
                    event.used_stage,
                    event.contribution,
                    event.outcome,
                    event.verification_delta,
                    event.note,
                    _dt_to_str(created_at),
                ),
            )

    def add_sync_run(self, started_at: datetime, finished_at: datetime, summary: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ha_sync_runs(started_at, finished_at, summary) VALUES (?, ?, ?)",
                (_dt_to_str(started_at), _dt_to_str(finished_at), _json_dumps(summary)),
            )
