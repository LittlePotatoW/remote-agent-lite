from __future__ import annotations

import asyncio

import pytest

from remote_agent_lite.codex import TurnStream
from remote_agent_lite.db import Database
from remote_agent_lite.events import EventBus
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.queueing import JobQueue
from remote_agent_lite.sessions import SessionService


class FakeCodex:
    def __init__(self) -> None:
        self.started = 0

    async def ensure_thread(self, session, cwd):
        return "thread-fake", True

    async def start_turn(self, thread_id, prompt, *, cwd, client_user_message_id=None):
        self.started += 1
        stream = TurnStream(thread_id=thread_id, turn_id="turn-fake")

        async def produce():
            await asyncio.sleep(0)
            await stream.push_delta("hello ")
            await stream.push_delta("world")
            await stream.complete({"id": "turn-fake", "status": "completed"})

        asyncio.create_task(produce())
        return stream

    async def recover_turn(self, thread_id, cwd):
        return None

    async def interrupt(self, thread_id, turn_id):
        return None


@pytest.mark.asyncio
async def test_queue_serializes_and_persists_turn(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    events = EventBus()
    project = await projects.create("Queue")
    session = await sessions.create(project["id"])
    fake = FakeCodex()
    queue = JobQueue(db, settings, projects, sessions, fake, events)
    result = await queue.enqueue(session["id"], "say hello")
    await queue._run_job(result["job_id"])
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (result["job_id"],))
    assert job["status"] == "succeeded"
    messages = await sessions.messages(session["id"])
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "hello world"
    assert fake.started == 1
