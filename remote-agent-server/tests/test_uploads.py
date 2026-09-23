from __future__ import annotations

import pytest

from remote_agent_lite.db import Database
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.storage import UploadService


@pytest.mark.asyncio
async def test_chunked_upload_and_resume(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    project = await projects.create("Uploads")
    uploads = UploadService(db, projects, settings)
    payload = b"hello world!"
    upload = await uploads.init(project["id"], "hello.txt", len(payload))
    assert upload["total_parts"] == 3
    await uploads.put_part(upload["upload_id"], 0, payload[:5])
    await uploads.put_part(upload["upload_id"], 1, payload[5:10])
    status = await db.fetchone(
        "SELECT received_parts FROM upload_sessions WHERE id = ?", (upload["upload_id"],)
    )
    assert status["received_parts"] == 2
    await uploads.put_part(upload["upload_id"], 2, payload[10:])
    result = await uploads.complete(upload["upload_id"])
    assert result["path"] == "uploads/hello.txt"
    project_dir = await projects.project_dir(project["id"])
    assert (project_dir / "uploads" / "hello.txt").read_bytes() == payload
