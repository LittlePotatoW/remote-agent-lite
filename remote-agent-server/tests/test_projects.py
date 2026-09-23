from __future__ import annotations

import pytest

from remote_agent_lite.db import Database
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.sessions import SessionService


@pytest.mark.asyncio
async def test_project_create_delete_and_pin(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)

    project = await projects.create("Demo Project")
    directory = await projects.project_dir(project["id"])
    assert (directory / "AGENTS.md").exists()
    assert (directory / "uploads").is_dir()
    assert project["pinned"] is False

    session = await sessions.create(project["id"])
    assert session["title"] == "新对话"

    await sessions.set_pinned(session["id"], True)
    listed = await sessions.list_for_project(project["id"])
    assert listed[0]["id"] == session["id"]
    assert listed[0]["pinned"] is True

    await projects.set_pinned(project["id"], True)
    assert (await projects.get(project["id"]))["pinned"] is True

    await projects.rename(project["id"], "Renamed")
    assert (await projects.get(project["id"]))["name"] == "Renamed"

    await projects.delete(project["id"])
    assert not directory.exists()
    assert await projects.list_active() == []
    assert await db.fetchall("SELECT * FROM sessions") == []
    with pytest.raises(FileNotFoundError):
        await projects.get(project["id"])


@pytest.mark.asyncio
async def test_pinned_projects_sort_first(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)

    first = await projects.create("First")
    second = await projects.create("Second")
    ordered = [item["id"] for item in await projects.list_active()]
    assert ordered == [second["id"], first["id"]]

    await projects.set_pinned(first["id"], True)
    ordered = [item["id"] for item in await projects.list_active()]
    assert ordered == [first["id"], second["id"]]


@pytest.mark.asyncio
async def test_session_job_status_is_reported(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)

    project = await projects.create("Jobs")
    session = await sessions.create(project["id"])
    message = await sessions.add_message(session["id"], "user", "hello")
    await db.execute(
        """
        INSERT INTO jobs(id, session_id, project_id, message_id, prompt, status, created_at)
        VALUES('job-1', ?, ?, ?, 'hello', 'running', '2026-01-01T00:00:00.000Z')
        """,
        (session["id"], project["id"], message["id"]),
    )
    listed = await sessions.list_all()
    assert listed[0]["job_status"] == "running"
