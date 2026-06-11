from __future__ import annotations

from .schemas import MemoryRecord
from .sqlite_store import SqliteMemoryStore


class SQLiteFTSIndex:
    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def refresh_record(self, record: MemoryRecord) -> None:
        self.store.upsert_record(record)

    def search(self, query: str, limit: int = 10) -> list[str]:
        return self.store.search_fts(query, limit=limit)

