from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from remote_agent_lite.db import Database
from remote_agent_lite.main import create_app
from remote_agent_lite.scheduled import (
    ScheduledRunner,
    ScheduledTaskService,
    local_timezone,
    next_occurrence,
)
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


async def _make_due(tasks: ScheduledTaskService, task_id: str, moment: datetime) -> None:
    """把 next_run_at 拨到过去，模拟「时间到了」。"""
    await tasks.db.execute(
        "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?", (iso(moment), task_id)
    )


def test_scheduled_task_api_roundtrip(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]

        created = client.post(
            f"/api/sessions/{session['id']}/scheduled-tasks",
            json={
                "prompt": "每天早上汇总昨天的 git 提交，并提醒我待办",
                "kind": "once",
                "month": 8,
                "day": 15,
                "hour": 7,
                "minute": 30,
            },
        )
        assert created.status_code == 200, created.text
        task = created.json()["task"]
        assert task["kind"] == "once"
        assert task["title"] == "每天早上汇总昨天的 git 提交，并提醒我待办"
        assert task["pinned"] is False
        assert (task["month"], task["day"], task["hour"], task["minute"]) == (8, 15, 7, 30)
        expected = next_occurrence("once", month=8, day=15, hour=7, minute=30)
        assert task["next_run_at"] == iso(expected)
        # 月份/日/时间原样存下来，而且永远落在未来（今年这天过了就顺延到明年）
        planned = datetime.fromisoformat(task["next_run_at"].replace("Z", "+00:00")).astimezone(
            local_timezone()
        )
        assert (planned.month, planned.day, planned.hour, planned.minute) == (8, 15, 7, 30)
        assert planned > datetime.now(local_timezone())
        assert planned.year >= datetime.now(local_timezone()).year

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
        # 一次性任务必须给月/日
        assert client.post(url, json={"prompt": "x", "kind": "once", "hour": 9, "minute": 0}).status_code == 400
        # 旧的 interval 类型已经没有了
        assert client.post(url, json={"prompt": "x", "kind": "interval"}).status_code == 422
        # 字段越界交给模型校验
        assert client.post(url, json={"prompt": "x", "kind": "daily", "hour": 99}).status_code == 422
        assert client.post(url, json={"prompt": "x", "kind": "weekly", "weekday": 9}).status_code == 422
        # 2 月 30 日这种日期不存在
        assert (
            client.post(
                url, json={"prompt": "x", "kind": "once", "month": 2, "day": 30, "hour": 9, "minute": 0}
            ).status_code
            == 400
        )
        assert client.post("/api/sessions/nope/scheduled-tasks", json={"prompt": "x"}).status_code == 404


def test_weekly_and_monthly_next_occurrence() -> None:
    # 2026-09-22 是周二
    start = datetime(2026, 9, 22, 8, 0, tzinfo=local_timezone())
    friday = next_occurrence("weekly", weekday=5, hour=18, minute=0, after=start)
    local = friday.astimezone(local_timezone())
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 25, 18, 0)

    # 每月 31 号：2 月没有这一天，跳过，直接落到 3 月 31 日
    february = datetime(2026, 2, 5, 10, 0, tzinfo=local_timezone())
    end_of_month = next_occurrence("monthly", day=31, hour=9, minute=0, after=february)
    local = end_of_month.astimezone(local_timezone())
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 3, 31, 9, 0)

    # 每天：同一天里时间还没到就用今天
    morning = datetime(2026, 9, 22, 8, 0, tzinfo=local_timezone())
    today = next_occurrence("daily", hour=9, minute=0, after=morning).astimezone(local_timezone())
    assert (today.day, today.hour) == (22, 9)
    evening = next_occurrence("daily", hour=9, minute=0, after=today).astimezone(local_timezone())
    assert (evening.day, evening.hour) == (23, 9)

    with pytest.raises(ValueError):
        next_occurrence("once", month=2, day=30, hour=9, minute=0, after=start)


@pytest.mark.asyncio
async def test_once_task_fires_and_disappears(settings) -> None:
    _, _, tasks, session = await _setup_services(settings)
    task = await tasks.create(
        session["id"], "提醒我周五发周报", kind="once", month=12, day=31, hour=23, minute=0
    )
    await _make_due(tasks, task["id"], utcnow() - timedelta(minutes=1))
    queue = FakeQueue()
    runner = ScheduledRunner(tasks, queue, tick_seconds=0.01)  # type: ignore[arg-type]

    assert await runner.run_due_once() == [task["id"]]
    assert queue.sent == [(session["id"], "提醒我周五发周报")]
    assert await tasks.list_for_session(session["id"]) == []
    # 已经跑过的一次性任务不会再被扫出来
    assert await runner.run_due_once() == []


@pytest.mark.asyncio
async def test_recurring_task_skips_missed_runs(settings) -> None:
    _, _, tasks, session = await _setup_services(settings)
    task = await tasks.create(session["id"], "看一眼部署状态", kind="daily", hour=9, minute=0)
    queue = FakeQueue()
    runner = ScheduledRunner(tasks, queue, tick_seconds=0.01)  # type: ignore[arg-type]

    # 服务器关机三天：只补发一条，然后跳到未来，不刷屏
    fired_at = utcnow()
    await _make_due(tasks, task["id"], fired_at - timedelta(days=3))
    assert await runner.run_due_once(now=fired_at) == [task["id"]]
    assert len(queue.sent) == 1
    fresh = await tasks.get(task["id"])
    upcoming = datetime.fromisoformat(fresh["next_run_at"].replace("Z", "+00:00"))
    assert upcoming > fired_at
    assert upcoming.astimezone(local_timezone()).hour == 9
    assert await runner.run_due_once(now=fired_at) == []


@pytest.mark.asyncio
async def test_task_follows_session_deletion(settings) -> None:
    _, sessions, tasks, session = await _setup_services(settings)
    await tasks.create(session["id"], "定时内容", kind="monthly", day=1, hour=9, minute=0)
    assert await tasks.list_for_session(session["id"]) != []
    await sessions.delete(session["id"])
    assert await tasks.list_for_session(session["id"]) == []


@pytest.mark.asyncio
async def test_legacy_table_is_migrated(settings) -> None:
    """老版本用 run_at/interval_seconds 存时间，开机时要能平滑换到新表。"""
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    sessions = SessionService(db)
    project = await projects.create("老项目")
    session = await sessions.create(project["id"], "老会话")

    when = utcnow() + timedelta(days=2)
    await db.execute("DROP TABLE scheduled_tasks")
    await db.execute(
        """
        CREATE TABLE scheduled_tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('once', 'interval')),
            run_at TEXT,
            interval_seconds INTEGER,
            next_run_at TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            pinned INTEGER NOT NULL DEFAULT 0,
            pinned_at TEXT,
            last_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    stamp = iso(utcnow())
    await db.execute(
        """
        INSERT INTO scheduled_tasks VALUES('keep', ?, '老的一次性', '内容', 'once', ?, NULL,
            ?, 1, 0, NULL, NULL, ?, ?)
        """,
        (session["id"], iso(when), iso(when), stamp, stamp),
    )
    await db.execute(
        """
        INSERT INTO scheduled_tasks VALUES('drop', ?, '老的每隔', '内容', 'interval', NULL, 3600,
            ?, 1, 0, NULL, NULL, ?, ?)
        """,
        (session["id"], iso(when), stamp, stamp),
    )

    await db.init()  # 再开一次机，触发迁移
    tasks = ScheduledTaskService(db)
    rows = await tasks.list_for_session(session["id"])
    assert [row["id"] for row in rows] == ["keep"]
    kept = rows[0]
    local = when.astimezone(local_timezone())
    assert (kept["month"], kept["day"], kept["hour"], kept["minute"]) == (
        local.month,
        local.day,
        local.hour,
        local.minute,
    )
    assert kept["kind"] == "once"
