from __future__ import annotations

from typing import Any

from .db import Database
from .utils import iso


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, key: str, default: str | None = None) -> str | None:
        row = await self.db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    async def get_int(self, key: str, default: int = 0) -> int:
        value = await self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    async def set(self, key: str, value: Any) -> None:
        await self.db.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value), iso()),
        )

    async def delete(self, key: str) -> None:
        await self.db.execute("DELETE FROM settings WHERE key = ?", (key,))

