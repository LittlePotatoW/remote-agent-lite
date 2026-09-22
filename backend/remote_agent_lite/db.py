from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

import aiosqlite

from .utils import iso, parse_iso, utcnow


logger = logging.getLogger(__name__)


#: 定时任务表。时间按「月/日/星期/时/分」拆开存，没有年份：
#:   once    一次性，month + day + hour + minute，过了就顺延到明年
#:   daily   每天 hour:minute
#:   weekly  每周 weekday 的 hour:minute（0=周日 … 6=周六）
#:   monthly 每月 day 日的 hour:minute（当月没有这一天就跳过这个月）
SCHEDULED_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('once', 'daily', 'weekly', 'monthly')),
    month INTEGER,
    day INTEGER,
    weekday INTEGER,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    pinned INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_tasks(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_session ON scheduled_tasks(session_id, next_run_at);
"""

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    window_start TEXT NOT NULL,
    locked_until TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    pinned INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    thread_id TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_message_seq INTEGER NOT NULL DEFAULT 0,
    last_read_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    job_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT,
    chunk_size INTEGER NOT NULL,
    total_parts INTEGER NOT NULL,
    received_parts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS upload_parts (
    upload_id TEXT NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
    part_index INTEGER NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (upload_id, part_index)
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_uploads_project ON upload_sessions(project_id, status);

""" + SCHEDULED_TASKS_DDL


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.path)

    async def _prepare(self, connection: aiosqlite.Connection) -> None:
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as connection:
            await self._prepare(connection)
            await connection.executescript(SCHEMA)
            await self._migrate_scheduled_tasks(connection)
            await connection.commit()

    async def _migrate_scheduled_tasks(self, connection: aiosqlite.Connection) -> None:
        """把最早的「run_at / interval_seconds」表换成「月日时分」表。

        旧表的列和 CHECK 约束都不一样，SQLite 改不了，只能重建。能对上时间的一次性
        任务平移过来；每隔 N 小时的周期任务在新模型里没有对应物，直接丢弃。
        """
        cursor = await connection.execute("PRAGMA table_info(scheduled_tasks)")
        columns = {row[1] for row in await cursor.fetchall()}
        if not columns or "hour" in columns:
            return
        await connection.execute("DROP INDEX IF EXISTS idx_scheduled_due")
        await connection.execute("DROP INDEX IF EXISTS idx_scheduled_session")
        await connection.execute("ALTER TABLE scheduled_tasks RENAME TO scheduled_tasks_v1")
        await connection.executescript(SCHEDULED_TASKS_DDL)
        now = iso(utcnow())
        kept = dropped = 0
        rows = await (await connection.execute("SELECT * FROM scheduled_tasks_v1")).fetchall()
        for row in rows:
            data = dict(row)
            moment = parse_iso(data.get("run_at")) if data.get("kind") == "once" else None
            if moment is None or iso(moment) <= now:
                dropped += 1
                continue
            local = moment.astimezone()
            await connection.execute(
                """
                INSERT INTO scheduled_tasks(
                    id, session_id, title, prompt, kind, month, day, weekday,
                    hour, minute, next_run_at, enabled, pinned, pinned_at,
                    last_run_at, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 'once', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["session_id"],
                    data["title"],
                    data["prompt"],
                    local.month,
                    local.day,
                    local.hour,
                    local.minute,
                    data["next_run_at"],
                    data.get("enabled", 1),
                    data.get("pinned", 0),
                    data.get("pinned_at"),
                    data.get("last_run_at"),
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            kept += 1
        await connection.execute("DROP TABLE scheduled_tasks_v1")
        logger.warning("定时任务表已升级：保留 %d 条一次性任务，丢弃 %d 条旧周期任务", kept, dropped)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        async with self.connect() as connection:
            await self._prepare(connection)
            await connection.execute(sql, params)
            await connection.commit()

    async def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> None:
        async with self.connect() as connection:
            await self._prepare(connection)
            await connection.executemany(sql, params)
            await connection.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.connect() as connection:
            await self._prepare(connection)
            cursor = await connection.execute(sql, params)
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connect() as connection:
            await self._prepare(connection)
            cursor = await connection.execute(sql, params)
            return list(await cursor.fetchall())

    async def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        if row is None:
            return None
        return row[0]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self.connect() as connection:
            await self._prepare(connection)
            try:
                await connection.execute("BEGIN IMMEDIATE")
                yield connection
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def close(self) -> None:
        return None
