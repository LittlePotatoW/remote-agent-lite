from __future__ import annotations

import asyncio
import base64

import pytest

from remote_agent_lite.codex import TurnStream
from remote_agent_lite.images import parse_image
from remote_agent_lite.db import Database
from remote_agent_lite.events import EventBus
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.queueing import JobQueue
from remote_agent_lite.sessions import SessionService


_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"body"
).decode()


class FakeCodex:
    def __init__(self) -> None:
        self.started = 0
        self.images: tuple = ()
        self.prompt = ""

    async def ensure_thread(self, session, cwd):
        return "thread-fake", True

    async def start_turn(self, thread_id, prompt, *, cwd, images=(), client_user_message_id=None):
        self.started += 1
        self.prompt = prompt
        self.images = tuple(images)
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


@pytest.mark.asyncio
async def test_images_travel_with_the_turn_and_are_never_persisted(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    events = EventBus()
    project = await projects.create("Images")
    session = await sessions.create(project["id"])
    project_dir = await projects.project_dir(project["id"])
    before = sorted(p.name for p in project_dir.rglob("*"))

    image = parse_image({"name": "shot.png", "data_url": _PNG_DATA_URL})
    fake = FakeCodex()
    queue = JobQueue(db, settings, projects, sessions, fake, events)
    result = await queue.enqueue(session["id"], "   ", [image])
    await queue._run_job(result["job_id"])

    assert result["message"]["content"] == "请看这张图片。"
    assert fake.prompt == "请看这张图片。"
    assert fake.images == (image,)

    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (result["job_id"],))
    assert job["status"] == "succeeded"
    assert "base64" not in job["prompt"]
    messages = await sessions.messages(session["id"])
    assert all("base64" not in (message["content"] or "") for message in messages)

    # the in-memory copy is released once the job has run
    assert queue._images == {}
    # and nothing was written into the project directory
    assert sorted(p.name for p in project_dir.rglob("*")) == before


@pytest.mark.asyncio
async def test_cancelled_jobs_release_their_images(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    events = EventBus()
    project = await projects.create("Cancelled")
    session = await sessions.create(project["id"])
    image = parse_image({"data_url": _PNG_DATA_URL})
    queue = JobQueue(db, settings, projects, sessions, FakeCodex(), events)
    result = await queue.enqueue(session["id"], "hi", [image])
    assert queue._images
    await queue.cancel(session["id"])
    assert queue._images == {}
