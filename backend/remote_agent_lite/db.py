from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

import aiosqlite

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

#: 定时任务随正文一起发的图片。和聊天里那种一次性图片不同，这些必须活到任务触发为止，
#: 所以只能落库：任务删掉时跟着级联删除。
SCHEDULED_IMAGES_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_task_images (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    data_url TEXT NOT NULL,
    size INTEGER NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_images ON scheduled_task_images(task_id, position);
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

""" + SCHEDULED_TASKS_DDL + SCHEDULED_IMAGES_DDL


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
            await connection.commit()

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
