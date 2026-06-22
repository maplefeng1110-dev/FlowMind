"""Minimal SQLite sink for the ``save_db`` DSL action.

Each row stores the extracted payload as JSON. The table name is sanitized to an
identifier-safe string because table names cannot be parameterized in SQL.
"""
from __future__ import annotations

import json
from typing import Any


def _safe_table(name: str) -> str:
    cleaned = "".join(ch for ch in (name or "") if ch.isalnum() or ch == "_")
    return cleaned or "rpa_results"


class SqliteResultWriter:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def write(self, table: str, rows: Any) -> int:
        import aiosqlite

        safe = _safe_table(table)
        if rows is None:
            records: list = []
        elif isinstance(rows, list):
            records = rows
        else:
            records = [rows]

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS {safe} ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "payload TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            for record in records:
                await db.execute(
                    f"INSERT INTO {safe} (payload) VALUES (?)",
                    (json.dumps(record, ensure_ascii=False),),
                )
            await db.commit()
        return len(records)
