import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

FEATURES = frozenset({"ai", "web", "meme", "memory"})


@dataclass(frozen=True)
class ScheduledMessage:
    group_id: int
    hour: int
    minute: int
    text: str
    owner_id: int
    last_attempted_date: str | None


class GroupSettingsStore:
    """Stores explicit group preferences and scheduled text, never chat history."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        def create() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                    "version INTEGER NOT NULL CHECK(version >= 0))"
                )
                version_row = conn.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()
                version = version_row[0] if version_row else 0
                if version > 3:
                    raise RuntimeError("Database schema is newer than this ALING version")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS group_settings ("
                    "group_id INTEGER PRIMARY KEY, "
                    "proactive_enabled INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(proactive_enabled IN (0, 1)))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS group_feature_settings ("
                    "group_id INTEGER NOT NULL CHECK(group_id > 0), "
                    "feature TEXT NOT NULL CHECK(feature IN ('ai', 'web', 'meme', 'memory')), "
                    "enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)), "
                    "PRIMARY KEY (group_id, feature))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS deployment_feature_settings ("
                    "feature TEXT PRIMARY KEY CHECK(feature IN ('ai', 'web', 'meme', 'memory')), "
                    "enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS proactive_schedules ("
                    "group_id INTEGER PRIMARY KEY CHECK(group_id > 0), "
                    "hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23), "
                    "minute INTEGER NOT NULL CHECK(minute BETWEEN 0 AND 59), "
                    "message TEXT NOT NULL CHECK(length(trim(message)) BETWEEN 1 AND 500), "
                    "owner_id INTEGER NOT NULL CHECK(owner_id > 0), "
                    "last_attempted_date TEXT, "
                    "CHECK(instr(message, char(0)) = 0))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS proactive_schedule_claims ("
                    "group_id INTEGER PRIMARY KEY CHECK(group_id > 0), "
                    "last_attempted_date TEXT NOT NULL, last_occurrence_date TEXT NOT NULL)"
                )
                if version < 3:
                    conn.execute(
                        "INSERT OR IGNORE INTO proactive_schedule_claims "
                        "(group_id, last_attempted_date, last_occurrence_date) "
                        "SELECT group_id, last_attempted_date, last_attempted_date "
                        "FROM proactive_schedules "
                        "WHERE last_attempted_date IS NOT NULL"
                    )
                conn.execute(
                    "INSERT INTO schema_version (singleton, version) VALUES (1, 3) "
                    "ON CONFLICT(singleton) DO UPDATE SET version=excluded.version"
                )
                conn.commit()

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
                    "INSERT INTO group_settings (group_id, proactive_enabled) VALUES (?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET "
                    "proactive_enabled=excluded.proactive_enabled",
                    (group_id, int(enabled)),
                )

        await asyncio.to_thread(write)

    async def is_feature_enabled(
        self, group_id: int, feature: str, *, global_enabled: bool, default_enabled: bool = True
    ) -> bool:
        _validate_group_id(group_id)
        _validate_feature(feature)

        def read() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                row = conn.execute(
                    "SELECT enabled FROM group_feature_settings WHERE group_id = ? AND feature = ?",
                    (group_id, feature),
                ).fetchone()
            return bool(global_enabled and (default_enabled if row is None else row[0]))

        return await asyncio.to_thread(read)

    async def set_feature_enabled(self, group_id: int, feature: str, enabled: bool) -> None:
        _validate_group_id(group_id)
        _validate_feature(feature)
        if type(enabled) is not bool:
            raise ValueError("Feature state must be boolean")

        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "INSERT INTO group_feature_settings (group_id, feature, enabled) "
                    "VALUES (?, ?, ?) ON CONFLICT(group_id, feature) DO UPDATE SET "
                    "enabled=excluded.enabled",
                    (group_id, feature, int(enabled)),
                )

        await asyncio.to_thread(write)

    async def list_configured_group_ids(self) -> list[int]:
        def read() -> list[int]:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                rows = conn.execute(
                    "SELECT group_id FROM group_settings UNION "
                    "SELECT group_id FROM group_feature_settings UNION "
                    "SELECT group_id FROM proactive_schedules ORDER BY group_id"
                ).fetchall()
            return [row[0] for row in rows]

        return await asyncio.to_thread(read)

    async def get_group_feature_settings(self, group_id: int) -> dict[str, bool]:
        _validate_group_id(group_id)

        def read() -> dict[str, bool]:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                rows = conn.execute(
                    "SELECT feature, enabled FROM group_feature_settings WHERE group_id = ?",
                    (group_id,),
                ).fetchall()
            return {feature: bool(enabled) for feature, enabled in rows}

        return await asyncio.to_thread(read)

    async def get_global_feature_settings(self) -> dict[str, bool]:
        def read() -> dict[str, bool]:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                rows = conn.execute(
                    "SELECT feature, enabled FROM deployment_feature_settings"
                ).fetchall()
            return {feature: bool(enabled) for feature, enabled in rows}

        return await asyncio.to_thread(read)

    async def set_global_feature_enabled(self, feature: str, enabled: bool) -> None:
        _validate_feature(feature)
        if type(enabled) is not bool:
            raise ValueError("Feature state must be boolean")

        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "INSERT INTO deployment_feature_settings (feature, enabled) VALUES (?, ?) "
                    "ON CONFLICT(feature) DO UPDATE SET enabled=excluded.enabled",
                    (feature, int(enabled)),
                )

        await asyncio.to_thread(write)

    async def get_schedule(self, group_id: int) -> ScheduledMessage | None:
        _validate_group_id(group_id)

        def read() -> ScheduledMessage | None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                row = conn.execute(
                    "SELECT group_id, hour, minute, message, owner_id, last_attempted_date "
                    "FROM proactive_schedules WHERE group_id = ?",
                    (group_id,),
                ).fetchone()
            return ScheduledMessage(*row) if row else None

        return await asyncio.to_thread(read)

    async def list_schedules(self) -> list[ScheduledMessage]:
        def read() -> list[ScheduledMessage]:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                rows = conn.execute(
                    "SELECT group_id, hour, minute, message, owner_id, last_attempted_date "
                    "FROM proactive_schedules ORDER BY group_id"
                ).fetchall()
            return [ScheduledMessage(*row) for row in rows]

        return await asyncio.to_thread(read)

    async def set_schedule(
        self, group_id: int, hour: int, minute: int, text: str, *, owner_id: int
    ) -> None:
        _validate_group_id(group_id)
        _validate_group_id(owner_id)
        if type(hour) is not int or not 0 <= hour <= 23:
            raise ValueError("Schedule hour must be between 0 and 23")
        if type(minute) is not int or not 0 <= minute <= 59:
            raise ValueError("Schedule minute must be between 0 and 59")
        if not isinstance(text, str) or not text.strip() or len(text) > 500 or "\x00" in text:
            raise ValueError("Schedule text must contain 1 to 500 characters")

        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "INSERT INTO proactive_schedules "
                    "(group_id, hour, minute, message, owner_id) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET hour=excluded.hour, "
                    "minute=excluded.minute, message=excluded.message, owner_id=excluded.owner_id",
                    (group_id, hour, minute, text.strip(), owner_id),
                )

        await asyncio.to_thread(write)

    async def delete_schedule(self, group_id: int) -> bool:
        _validate_group_id(group_id)

        def delete() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                cursor = conn.execute(
                    "DELETE FROM proactive_schedules WHERE group_id = ?", (group_id,)
                )
                return cursor.rowcount > 0

        return await asyncio.to_thread(delete)

    async def claim_schedule(
        self,
        group_id: int,
        local_date: str,
        *,
        expected: ScheduledMessage | None = None,
        occurrence_date: str | None = None,
    ) -> bool:
        _validate_group_id(group_id)
        try:
            local_date = date.fromisoformat(local_date).isoformat()
            occurrence_date = date.fromisoformat(occurrence_date or local_date).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("Schedule date must use ISO format") from exc

        def claim() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                claimed = conn.execute(
                    "SELECT last_attempted_date, last_occurrence_date "
                    "FROM proactive_schedule_claims WHERE group_id = ?",
                    (group_id,),
                ).fetchone()
                if claimed and (claimed[0] >= local_date or claimed[1] >= occurrence_date):
                    return False
                if expected is None:
                    cursor = conn.execute(
                        "UPDATE proactive_schedules SET last_attempted_date = ? "
                        "WHERE group_id = ? AND (last_attempted_date IS NULL OR "
                        "last_attempted_date != ?)",
                        (local_date, group_id, local_date),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE proactive_schedules SET last_attempted_date = ? "
                        "WHERE group_id = ? AND hour = ? AND minute = ? AND message = ? "
                        "AND (last_attempted_date IS NULL OR last_attempted_date != ?)",
                        (
                            local_date,
                            group_id,
                            expected.hour,
                            expected.minute,
                            expected.text,
                            local_date,
                        ),
                    )
                if cursor.rowcount != 1:
                    return False
                conn.execute(
                    "INSERT INTO proactive_schedule_claims "
                    "(group_id, last_attempted_date, last_occurrence_date) "
                    "VALUES (?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
                    "last_attempted_date=excluded.last_attempted_date, "
                    "last_occurrence_date=excluded.last_occurrence_date",
                    (group_id, local_date, occurrence_date),
                )
                return True

        return await asyncio.to_thread(claim)


def _validate_group_id(group_id: int) -> None:
    if type(group_id) is not int or group_id <= 0:
        raise ValueError("Group and user IDs must be positive integers")


def _validate_feature(feature: str) -> None:
    if feature not in FEATURES:
        raise ValueError("Unknown feature")
