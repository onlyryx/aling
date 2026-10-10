import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path


class GroupSettingsStore:
    """Stores preferences only. No messages, prompts, responses or account credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        def create() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS group_settings ("
                    "group_id INTEGER PRIMARY KEY, "
                    "proactive_enabled INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(proactive_enabled IN (0, 1)))"
                )

        await asyncio.to_thread(create)

    async def is_proactive_enabled(self, group_id: int) -> bool:
        def read() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                row = conn.execute(
                    "SELECT proactive_enabled FROM group_settings WHERE group_id = ?", (group_id,)
                ).fetchone()
            return bool(row and row[0])

        return await asyncio.to_thread(read)

    async def set_proactive_enabled(self, group_id: int, enabled: bool) -> None:
        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "INSERT INTO group_settings VALUES (?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET "
                    "proactive_enabled=excluded.proactive_enabled",
                    (group_id, int(enabled)),
                )

        await asyncio.to_thread(write)
