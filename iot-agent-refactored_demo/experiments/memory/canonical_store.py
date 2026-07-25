from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .schemas import MemoryEdge, MemoryRecord


class CanonicalStore:
    """SQLite-backed source of truth for experiment memories and audit events."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    natural_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_edges (
                    edge_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert(self, record: MemoryRecord, *, event_type: str = "upsert"):
        payload = record.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_records
                    (memory_id, memory_type, status, layer, natural_text, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    memory_type=excluded.memory_type,
                    status=excluded.status,
                    layer=excluded.layer,
                    natural_text=excluded.natural_text,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.memory_id,
                    record.memory_type,
                    record.status,
                    record.layer,
                    record.natural_text,
                    json.dumps(payload, ensure_ascii=False),
                    record.updated_at.isoformat(),
                ),
            )
            conn.execute(
                "INSERT INTO memory_events(event_type, memory_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    event_type,
                    record.memory_id,
                    json.dumps(payload, ensure_ascii=False),
                    record.updated_at.isoformat(),
                ),
            )

    def upsert_edge(self, edge: MemoryEdge):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_edges(edge_id, payload_json) VALUES (?, ?)",
                (edge.edge_id, json.dumps(edge.model_dump(mode="json"), ensure_ascii=False)),
            )

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM memory_records WHERE memory_id=?",
                (memory_id,),
            ).fetchone()
        return MemoryRecord.model_validate(json.loads(row["payload_json"])) if row else None

    def list(self, *, include_deleted: bool = False) -> list[MemoryRecord]:
        query = "SELECT payload_json FROM memory_records"
        args: tuple = ()
        if not include_deleted:
            query += " WHERE status != ?"
            args = ("deleted",)
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [MemoryRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def delete_index_record(self, memory_id: str):
        self.log_event("index_delete", memory_id, {})

    def log_event(self, event_type: str, memory_id: str | None, payload: dict):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_events(event_type, memory_id, payload_json, created_at) VALUES (?, ?, ?, datetime('now'))",
                (event_type, memory_id, json.dumps(payload, ensure_ascii=False)),
            )

    def events(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, memory_id, payload_json, created_at FROM memory_events ORDER BY event_id"
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "memory_id": row["memory_id"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
