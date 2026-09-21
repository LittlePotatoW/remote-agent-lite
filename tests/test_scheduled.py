from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from remote_agent_lite.db import Database
from remote_agent_lite.main import create_app
from remote_agent_lite.scheduled import ScheduledRunner, ScheduledTaskService, parse_when
from remote_agent_lite.sessions import SessionService
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.utils import iso, utcnow


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


class FakeQueue:
    """只记录「到点发了什么」，不碰真正的 codex。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def enqueue(self, session_id: str, prompt: str) -> dict:
        self.sent.append((session_id, prompt))
        return {"job_id": "job-fake"}


async def _setup_services(settings):
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    project = await projects.create("定时项目")
    session = await sessions.create(project["id"], "定时会话")
    tasks = ScheduledTaskService(db)
    return db, sessions, tasks, session


def test_scheduled_task_api_roundtrip(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    future_local = (utcnow().astimezone() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]

        created = client.post(
            f"/api/sessions/{session['id']}/scheduled-tasks",
            json={"prompt": "每天早上汇总昨天的 git 提交，并提醒我待办", "kind": "once", "run_at": future_local},
        )
        assert created.status_code == 200
        task = created.json()["task"]
        assert task["kind"] == "once"
        assert task["title"] == "每天早上汇总昨天的 git 提交，并提醒我待办"
        assert task["pinned"] is False
        assert task["interval_seconds"] is None
        expected = parse_when(future_local)
        assert task["next_run_at"] == iso(expected)

        # 会话列表要带上「有没有待执行的定时任务」，前端拿它渲染行尾时钟
        overview = client.get("/api/overview").json()["projects"]
        assert overview[0]["sessions"][0]["scheduled_pending"] == 1

        listed = client.get(f"/api/sessions/{session['id']}/scheduled-tasks").json()["tasks"]
        assert [item["id"] for item in listed] == [task["id"]]

        renamed = client.patch(
            f"/api/scheduled-tasks/{task['id']}", json={"title": "早上汇报"}
        ).json()["task"]
        assert renamed["title"] == "早上汇报"

        pinned = client.patch(
            f"/api/scheduled-tasks/{task['id']}", json={"pinned": True}
        ).json()["task"]
        assert pinned["pinned"] is True

        assert client.delete(f"/api/scheduled-tasks/{task['id']}").status_code == 200
        assert client.get(f"/api/sessions/{session['id']}/scheduled-tasks").json()["tasks"] == []
        overview = client.get("/api/overview").json()["projects"]
        assert overview[0]["sessions"][0]["scheduled_pending"] == 0

        assert client.delete("/api/scheduled-tasks/does-not-exist").status_code == 404


def test_scheduled_task_rejects_bad_input(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]
        url = f"/api/sessions/{session['id']}/scheduled-tasks"
        assert client.post(url, json={"prompt": "x", "kind": "once"}).status_code == 400
        assert client.post(url, json={"prompt": "x", "kind": "interval"}).status_code == 400
        assert (
            client.post(url, json={"prompt": "x", "kind": "interval", "interval_seconds": 5}).status_code
            == 422
        )
        assert client.post("/api/sessions/nope/scheduled-tasks", json={"prompt": "x"}).status_code == 404


@pytest.mark.asyncio
async def test_once_task_fires_and_disappears(settings) -> None:
    _, sessions, tasks, session = await _setup_services(settings)
    due = utcnow() - timedelta(minutes=1)
    task = await tasks.create(
        session["id"], "提醒我周五发周报", kind="once", run_at=iso(due)
    )
    queue = FakeQueue()
    runner = ScheduledRunner(tasks, queue, tick_seconds=0.01)  # type: ignore[arg-type]

    assert await runner.run_due_once() == [task["id"]]
    assert queue.sent == [(session["id"], "提醒我周五发周报")]
    assert await tasks.list_for_session(session["id"]) == []
    # 已经跑过的一次性任务不会再被扫出来
    assert await runner.run_due_once() == []


@pytest.mark.asyncio
async def test_interval_task_advances_without_drift(settings) -> None:
    _, sessions, tasks, session = await _setup_services(settings)
    task = await tasks.create(
        session["id"], "看一眼部署状态", kind="interval", interval_seconds=3600
    )
    queue = FakeQueue()
    runner = ScheduledRunner(tasks, queue, tick_seconds=0.01)  # type: ignore[arg-type]

    first_due = parse_when(task["next_run_at"])
    assert await runner.run_due_once(now=first_due + timedelta(seconds=1)) == [task["id"]]
    advanced = await tasks.get(task["id"])
    # 下一次是「上次计划时间 + 1 小时」，不按 tick 时刻累加
    assert advanced["next_run_at"] == iso(first_due + timedelta(hours=1))
    assert await runner.run_due_once(now=first_due + timedelta(seconds=2)) == []

    # 服务器关机很久：只补发一条，然后跳到未来，不刷屏
    await tasks.db.execute(
        "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
        (iso(utcnow() - timedelta(days=3)), task["id"]),
    )
    assert await runner.run_due_once() == [task["id"]]
    assert len(queue.sent) == 2
    fresh = await tasks.get(task["id"])
    assert parse_when(fresh["next_run_at"]) > utcnow()
    assert await runner.run_due_once() == []


@pytest.mark.asyncio
async def test_task_follows_session_deletion(settings) -> None:
    _, sessions, tasks, session = await _setup_services(settings)
    await tasks.create(session["id"], "定时内容", kind="interval", interval_seconds=60)
    assert await tasks.list_for_session(session["id"]) != []
    await sessions.delete(session["id"])
    assert await tasks.list_for_session(session["id"]) == []
