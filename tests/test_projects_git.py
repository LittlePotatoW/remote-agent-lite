from __future__ import annotations

import pytest

from remote_agent_lite.db import Database
from remote_agent_lite.git_ops import GitService
from remote_agent_lite.projects import ProjectService


@pytest.mark.asyncio
async def test_project_create_snapshot_and_restore(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    git = GitService(settings)
    projects = ProjectService(db, settings, git)
    project = await projects.create("Demo Project")
    directory = await projects.project_dir(project["id"])
    assert (directory / "AGENTS.md").exists()
    assert (directory / "uploads").is_dir()
    first = await git.log(directory)
    assert first
    (directory / "main.py").write_text("print('hello')\n", encoding="utf-8")
    snapshot = await git.snapshot(directory, "add main")
    assert snapshot and snapshot["commit"]
    (directory / "main.py").write_text("print('break')\n", encoding="utf-8")
    await git.restore(directory, snapshot["commit"])
    assert (directory / "main.py").read_text(encoding="utf-8") == "print('hello')\n"


@pytest.mark.asyncio
async def test_trash_restore_and_purge(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    git = GitService(settings)
    projects = ProjectService(db, settings, git)
    project = await projects.create("Trash Me")
    directory = await projects.project_dir(project["id"])
    await projects.trash(project["id"])
    assert not directory.exists()
    assert len(await projects.list_trash()) == 1
    await projects.restore(project["id"])
    assert directory.exists()
    await projects.trash(project["id"])
    await projects.purge(project["id"])
    assert await projects.list_trash() == []

