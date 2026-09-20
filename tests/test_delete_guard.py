from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from remote_agent_lite import api as api_module
from remote_agent_lite.main import create_app


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


def _insert_running_job(app, session_id: str, project_id: str) -> str:
    async def _insert() -> str:
        ral = app.state.ral
        message = await ral.sessions.add_message(session_id, "user", "hello")
        job_id = "job-running-selftest"
        await ral.db.execute(
            """
            INSERT INTO jobs(id, session_id, project_id, message_id, prompt, status, created_at)
            VALUES(?, ?, ?, ?, 'hello', 'running', '2026-01-01T00:00:00.000Z')
            """,
            (job_id, session_id, project_id, message["id"]),
        )
        return job_id

    return asyncio.run(_insert())


def _finish_job(app, job_id: str) -> None:
    async def _finish() -> None:
        await app.state.ral.db.execute(
            "UPDATE jobs SET status = 'succeeded', finished_at = ? WHERE id = ?",
            ("2026-01-01T00:01:00.000Z", job_id),
        )

    asyncio.run(_finish())


def test_delete_project_is_rejected_while_job_runs(settings, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "DELETE_IDLE_TIMEOUT_SECONDS", 0.3)
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Delete Guard"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]
        job_id = _insert_running_job(app, session["id"], project["id"])

        blocked = client.delete(f"/api/projects/{project['id']}")
        assert blocked.status_code == 409
        assert "还有任务在运行" in blocked.json()["detail"]
        # 拒绝删除时目录必须原封不动
        assert (settings.projects_dir / project["slug"]).exists()

        _finish_job(app, job_id)
        assert client.delete(f"/api/projects/{project['id']}").status_code == 200
        assert not (settings.projects_dir / project["slug"]).exists()


def test_delete_session_is_rejected_while_job_runs(settings, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "DELETE_IDLE_TIMEOUT_SECONDS", 0.3)
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Session Guard"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]
        job_id = _insert_running_job(app, session["id"], project["id"])

        blocked = client.delete(f"/api/sessions/{session['id']}")
        assert blocked.status_code == 409
        assert "还有任务在运行" in blocked.json()["detail"]
        assert client.get(f"/api/sessions/{session['id']}/status").status_code == 200

        _finish_job(app, job_id)
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 200
        assert client.get(f"/api/sessions/{session['id']}/status").status_code == 404
