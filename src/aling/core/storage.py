import asyncio
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, fields, replace
from datetime import date
from pathlib import Path

FEATURES = frozenset({"ai", "web", "meme", "memory", "search", "online_meme"})
SEARCH_PURPOSES = frozenset({"technical", "ordinary_meme", "gaming", "hobby", "practical"})


@dataclass(frozen=True)
class SearchLimits:
    task_seconds: int = 60
    work_seconds: int = 57
    max_tool_rounds: int = 3
    max_model_requests: int = 5
    max_external_operations: int = 4
    max_searches: int = 1
    max_results: int = 3
    request_timeout_seconds: int = 10
    instance_concurrency: int = 2
    group_concurrency: int = 1
    user_cooldown_seconds: int = 15
    user_hourly_limit: int = 10
    group_hourly_limit: int = 30
    instance_hourly_limit: int = 100


@dataclass(frozen=True)
class SearchSourceRule:
    source_id: str
    purpose: str
    host: str
    path_prefix: str = "/"
    kind: str = "page"
    port: int | None = None
    allowed_query_params: frozenset[str] = frozenset()
    scheme: str = "https"


@dataclass(frozen=True)
class DeploymentSearchPolicy:
    allowed_purposes: frozenset[str] = frozenset({"technical", "ordinary_meme"})
    sources: tuple[SearchSourceRule, ...] = ()
    limits: SearchLimits = SearchLimits()
    revision: int = 0


@dataclass(frozen=True)
class GroupSearchPolicy:
    # None inherits the deployment policy; explicit sets can only narrow it.
    allowed_purposes: frozenset[str] | None = None
    source_ids: frozenset[str] | None = None
    limits: SearchLimits | None = None
    meme_mode: str = "local"
    revision: int = 0


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

    def __init__(self, path: Path, *, superusers: set[int] | frozenset[int] = frozenset()) -> None:
        self.path = path
        self.superusers = frozenset(superusers)

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
                if version > 4:
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
                    "feature TEXT NOT NULL CHECK(feature IN "
                    "('ai', 'web', 'meme', 'memory', 'search', 'online_meme')), "
                    "enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)), "
                    "PRIMARY KEY (group_id, feature))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS deployment_feature_settings ("
                    "feature TEXT PRIMARY KEY CHECK(feature IN "
                    "('ai', 'web', 'meme', 'memory', 'search', 'online_meme')), "
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
                if version < 4:
                    for table, columns, definition in (
                        (
                            "group_feature_settings",
                            "group_id, feature, enabled",
                            "group_id INTEGER NOT NULL CHECK(group_id > 0), "
                            "feature TEXT NOT NULL CHECK(feature IN "
                            "('ai','web','meme','memory','search','online_meme')), "
                            "enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), "
                            "PRIMARY KEY(group_id, feature)",
                        ),
                        (
                            "deployment_feature_settings",
                            "feature, enabled",
                            "feature TEXT PRIMARY KEY CHECK(feature IN "
                            "('ai','web','meme','memory','search','online_meme')), "
                            "enabled INTEGER NOT NULL CHECK(enabled IN (0,1))",
                        ),
                    ):
                        conn.execute(f"CREATE TABLE {table}_v4 ({definition})")
                        conn.execute(
                            f"INSERT INTO {table}_v4 ({columns}) SELECT {columns} FROM {table}"
                        )
                        conn.execute(f"DROP TABLE {table}")
                        conn.execute(f"ALTER TABLE {table}_v4 RENAME TO {table}")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS deployment_search_policy ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                    "policy_json TEXT NOT NULL, "
                    "revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0), "
                    "search_consent INTEGER NOT NULL DEFAULT 0 CHECK(search_consent IN (0,1)), "
                    "ai_tools_enabled INTEGER NOT NULL DEFAULT 0 CHECK(ai_tools_enabled IN (0,1)), "
                    "ai_tools_seeded INTEGER NOT NULL DEFAULT 0 CHECK(ai_tools_seeded IN (0,1)))"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deployment_search_policy(singleton, policy_json) "
                    "VALUES(1, ?)",
                    (_policy_json(DeploymentSearchPolicy()),),
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS group_search_policy ("
                    "group_id INTEGER PRIMARY KEY CHECK(group_id>0), policy_json TEXT NOT NULL, "
                    "revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0))"
                )
                conn.execute(
                    "INSERT INTO schema_version (singleton, version) VALUES (1, 4) "
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
            return bool(
                global_enabled
                and (
                    default_enabled and feature not in {"search", "online_meme"}
                    if row is None
                    else row[0]
                )
            )

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
                    "SELECT group_id FROM proactive_schedules UNION "
                    "SELECT group_id FROM group_search_policy ORDER BY group_id"
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

    async def get_deployment_search_policy(self) -> DeploymentSearchPolicy:
        def read() -> DeploymentSearchPolicy:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                document, revision = conn.execute(
                    "SELECT policy_json, revision FROM deployment_search_policy WHERE singleton=1"
                ).fetchone()
            return _deployment_policy(document, revision)

        return await asyncio.to_thread(read)

    async def set_deployment_search_policy(self, policy: DeploymentSearchPolicy) -> int:
        policy = _validate_deployment_policy(policy)
        document = _policy_json(policy)

        def write() -> int:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE deployment_search_policy SET policy_json=?, revision=revision+1 "
                    "WHERE singleton=1",
                    (document,),
                )
                return conn.execute(
                    "SELECT revision FROM deployment_search_policy WHERE singleton=1"
                ).fetchone()[0]

        return await asyncio.to_thread(write)

    async def get_group_search_policy(self, group_id: int) -> GroupSearchPolicy:
        _validate_group_id(group_id)

        def read() -> GroupSearchPolicy:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                row = conn.execute(
                    "SELECT policy_json, revision FROM group_search_policy WHERE group_id=?",
                    (group_id,),
                ).fetchone()
            return _group_policy(*row) if row else GroupSearchPolicy()

        return await asyncio.to_thread(read)

    async def set_group_search_policy(self, group_id: int, policy: GroupSearchPolicy) -> int:
        _validate_group_id(group_id)
        _validate_group_policy(policy)
        document = _policy_json(policy)

        def write() -> int:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                deployment = _deployment_policy(
                    *conn.execute(
                        "SELECT policy_json, revision FROM deployment_search_policy "
                        "WHERE singleton=1"
                    ).fetchone()
                )
                if (
                    policy.allowed_purposes is not None
                    and not policy.allowed_purposes <= deployment.allowed_purposes
                ):
                    raise ValueError("Group purposes exceed deployment policy")
                if policy.source_ids is not None and not policy.source_ids <= {
                    source.source_id for source in deployment.sources
                }:
                    raise ValueError("Group sources exceed deployment policy")
                if policy.limits is not None:
                    _validate_limits(policy.limits, ceiling=deployment.limits)
                conn.execute(
                    "INSERT INTO group_search_policy(group_id, policy_json, revision) "
                    "VALUES(?, ?, 1) "
                    "ON CONFLICT(group_id) DO UPDATE SET policy_json=excluded.policy_json, "
                    "revision=revision+1",
                    (group_id, document),
                )
                return conn.execute(
                    "SELECT revision FROM group_search_policy WHERE group_id=?", (group_id,)
                ).fetchone()[0]

        return await asyncio.to_thread(write)

    async def get_search_consent(self) -> bool:
        def read() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                return bool(
                    conn.execute(
                        "SELECT search_consent FROM deployment_search_policy WHERE singleton=1"
                    ).fetchone()[0]
                )

        return await asyncio.to_thread(read)

    async def set_search_consent(self, owner_id: int, acknowledged: bool) -> None:
        self._require_owner(owner_id)
        _validate_boolean(acknowledged)

        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "UPDATE deployment_search_policy SET search_consent=?, revision=revision+1 "
                    "WHERE singleton=1",
                    (int(acknowledged),),
                )

        await asyncio.to_thread(write)

    async def get_ai_tools_enabled(self, default: bool) -> bool:
        _validate_boolean(default)

        def seed_and_read() -> bool:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE deployment_search_policy SET ai_tools_enabled=?, ai_tools_seeded=1 "
                    "WHERE singleton=1 AND ai_tools_seeded=0",
                    (int(default),),
                )
                return bool(
                    conn.execute(
                        "SELECT ai_tools_enabled FROM deployment_search_policy WHERE singleton=1"
                    ).fetchone()[0]
                )

        return await asyncio.to_thread(seed_and_read)

    async def set_ai_tools_enabled(self, owner_id: int, enabled: bool) -> None:
        self._require_owner(owner_id)
        _validate_boolean(enabled)

        def write() -> None:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute(
                    "UPDATE deployment_search_policy SET ai_tools_enabled=?, ai_tools_seeded=1 "
                    "WHERE singleton=1",
                    (int(enabled),),
                )

        await asyncio.to_thread(write)

    def _require_owner(self, owner_id: int) -> None:
        _validate_group_id(owner_id)
        if owner_id not in self.superusers:
            raise PermissionError("Only configured superusers can change owner settings")

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

    async def list_schedule_claim_dates(self) -> dict[int, str]:
        def read() -> dict[int, str]:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                rows = conn.execute(
                    "SELECT group_id, last_attempted_date FROM proactive_schedule_claims"
                ).fetchall()
            return {group_id: last_attempted_date for group_id, last_attempted_date in rows}

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


def _validate_boolean(value: bool) -> None:
    if type(value) is not bool:
        raise ValueError("State must be boolean")


def _validate_string_set(value: frozenset[str], *, choices: frozenset[str] | None = None) -> None:
    if not isinstance(value, frozenset) or any(not isinstance(item, str) for item in value):
        raise ValueError("Policy values must be a frozenset of strings")
    if choices is not None and not value <= choices:
        raise ValueError("Unknown policy purpose")


def _validate_limits(limits: SearchLimits, *, ceiling: SearchLimits | None = None) -> None:
    ceiling = ceiling or SearchLimits()
    if not isinstance(limits, SearchLimits):
        raise ValueError("Expected structured search limits")
    for field in fields(limits):
        value, maximum = getattr(limits, field.name), getattr(ceiling, field.name)
        if type(value) is not int or value <= 0:
            raise ValueError("Search limits must be positive integers")
        if field.name == "user_cooldown_seconds":
            if value < maximum:
                raise ValueError("Search cooldown cannot be reduced")
        elif value > maximum:
            raise ValueError("Search limit exceeds maximum")
    if limits.work_seconds > limits.task_seconds - 3:
        raise ValueError("Search deadline must reserve three seconds for sending")


def _validate_deployment_policy(policy: DeploymentSearchPolicy) -> DeploymentSearchPolicy:
    if not isinstance(policy, DeploymentSearchPolicy):
        raise ValueError("Expected structured deployment search policy")
    _validate_string_set(policy.allowed_purposes, choices=SEARCH_PURPOSES)
    _validate_limits(policy.limits)
    if not isinstance(policy.sources, tuple) or len(policy.sources) > 100:
        raise ValueError("Expected at most 100 source rules")
    sources = []
    identifiers = set()
    for source in policy.sources:
        if not isinstance(source, SearchSourceRule):
            raise ValueError("Expected structured source rule")
        if not isinstance(source.source_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", source.source_id
        ):
            raise ValueError("Invalid source ID")
        if source.source_id in identifiers:
            raise ValueError("Duplicate source ID")
        identifiers.add(source.source_id)
        if (
            not isinstance(source.purpose, str)
            or not isinstance(source.kind, str)
            or not isinstance(source.scheme, str)
            or source.scheme not in {"http", "https"}
            or source.purpose not in policy.allowed_purposes
            or source.kind not in {"page", "image"}
        ):
            raise ValueError("Invalid source purpose, scheme, or kind")
        if source.kind == "image" and source.purpose != "ordinary_meme":
            raise ValueError("Image sources require ordinary_meme purpose")
        if not isinstance(source.host, str):
            raise ValueError("Invalid source host")
        try:
            host = source.host.removesuffix(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Invalid source host") from exc
        if len(host) > 253 or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host
        ):
            raise ValueError("Expected an exact source hostname")
        prefix = source.path_prefix
        if (
            not isinstance(prefix, str)
            or not prefix.startswith("/")
            or len(prefix) > 500
            or any(char in prefix for char in ("%", "\\", "?", "#"))
            or any(ord(char) < 33 or ord(char) == 127 for char in prefix)
            or any(part in {".", ".."} for part in prefix.split("/"))
            or "//" in prefix
        ):
            raise ValueError("Invalid source path prefix")
        if source.port is not None and (
            type(source.port) is not int or not 1 <= source.port <= 65535
        ):
            raise ValueError("Invalid source port")
        _validate_string_set(source.allowed_query_params)
        if any(
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key)
            or key.lower()
            in {
                "token",
                "access_token",
                "key",
                "api_key",
                "password",
                "secret",
                "signature",
                "sig",
                "auth",
            }
            for key in source.allowed_query_params
        ):
            raise ValueError("Invalid or sensitive source query parameter")
        sources.append(replace(source, host=host))
    return replace(policy, sources=tuple(sources))


def _validate_group_policy(policy: GroupSearchPolicy) -> None:
    if not isinstance(policy, GroupSearchPolicy):
        raise ValueError("Expected structured group search policy")
    if policy.allowed_purposes is not None:
        _validate_string_set(policy.allowed_purposes, choices=SEARCH_PURPOSES)
    if policy.source_ids is not None:
        _validate_string_set(policy.source_ids)
    if policy.limits is not None:
        _validate_limits(policy.limits)
    if not isinstance(policy.meme_mode, str) or policy.meme_mode not in {"local", "auto", "online"}:
        raise ValueError("Invalid meme mode")


def _policy_json(policy: DeploymentSearchPolicy | GroupSearchPolicy) -> str:
    document = asdict(policy)
    del document["revision"]
    return json.dumps(
        document,
        default=lambda value: sorted(value) if isinstance(value, frozenset) else value,
        sort_keys=True,
        separators=(",", ":"),
    )


def _deployment_policy(document: str, revision: int) -> DeploymentSearchPolicy:
    data = json.loads(document)
    data["allowed_purposes"] = frozenset(data["allowed_purposes"])
    data["sources"] = tuple(
        SearchSourceRule(
            **{**source, "allowed_query_params": frozenset(source["allowed_query_params"])}
        )
        for source in data["sources"]
    )
    data["limits"] = SearchLimits(**data["limits"])
    return _validate_deployment_policy(DeploymentSearchPolicy(**data, revision=revision))


def _group_policy(document: str, revision: int) -> GroupSearchPolicy:
    data = json.loads(document)
    for key in ("allowed_purposes", "source_ids"):
        if data[key] is not None:
            data[key] = frozenset(data[key])
    if data["limits"] is not None:
        data["limits"] = SearchLimits(**data["limits"])
    policy = GroupSearchPolicy(**data, revision=revision)
    _validate_group_policy(policy)
    return policy
