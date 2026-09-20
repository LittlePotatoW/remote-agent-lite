from __future__ import annotations

import pytest

from remote_agent_lite.codex import TurnStream
from remote_agent_lite.db import Database
from remote_agent_lite.events import EventBus
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.queueing import JobQueue
from remote_agent_lite.sessions import SessionService


class SilentCodex:
    """既不产生增量也不结束的 Codex：用来验证超时路径。"""

    def __init__(self) -> None:
        self.interrupted: list[tuple[str, str]] = []

    async def ensure_thread(self, session, cwd):
        return "thread-timeout", True

    async def start_turn(self, thread_id, prompt, *, cwd, client_user_message_id=None):
        return TurnStream(thread_id=thread_id, turn_id="turn-timeout")

    async def interrupt(self, thread_id, turn_id):
        self.interrupted.append((thread_id, turn_id))

    async def recover_turn(self, thread_id, cwd):
        return None


@pytest.mark.asyncio
async def test_turn_timeout_interrupts_codex_and_fails_job(settings) -> None:
    settings = settings.with_overrides(codex_turn_timeout_seconds=1)
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    project = await projects.create("Timeout")
    session = await sessions.create(project["id"])
    codex = SilentCodex()
    queue = JobQueue(db, settings, projects, sessions, codex, EventBus())

    result = await queue.enqueue(session["id"], "hang forever")
    await queue._run_job(result["job_id"])

    # 超时必须真的去打断服务端的 turn，而不是只在本地记一笔
    assert codex.interrupted == [("thread-timeout", "turn-timeout")]
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (result["job_id"],))
    assert job["status"] == "failed"
    assert "timed out" in job["error"]

    messages = await sessions.messages(session["id"])
    assistant = [item for item in messages if item["role"] == "assistant"]
    assert assistant[-1]["status"] == "failed"
    assert assistant[-1]["error"] == "turn timed out"
