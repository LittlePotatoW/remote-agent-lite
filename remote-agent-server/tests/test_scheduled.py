from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from remote_agent_lite.db import Database
from remote_agent_lite.images import ChatImage
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


#: 1x1 的透明 PNG，够通过魔数校验，也不用往仓库里塞图片文件。
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeQueue:
    """只记录「到点发了什么」，不碰真正的 codex。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, list[ChatImage]]] = []

    async def enqueue(self, session_id: str, prompt: str, images=None) -> dict:
        self.sent.append((session_id, prompt, list(images or [])))
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
    assert [(sid, text) for sid, text, _ in queue.sent] == [(session["id"], "提醒我周五发周报")]
    assert queue.sent[0][2] == []
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


def test_scheduled_task_with_images(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]
        url = f"/api/sessions/{session['id']}/scheduled-tasks"

        # 纯图片任务：正文可以空着，和输入框里只发图一样
        created = client.post(
            url,
            json={
                "prompt": "",
                "kind": "daily",
                "hour": 9,
                "minute": 0,
                "images": [{"name": "shot.png", "data_url": PNG_DATA_URL}],
            },
        )
        assert created.status_code == 200, created.text
        task = created.json()["task"]
        assert task["image_count"] == 1
        assert task["title"] == "定时任务"

        listed = client.get(url).json()["tasks"]
        assert listed[0]["image_count"] == 1

        # 正文和图片都没有 → 拒绝
        assert client.post(url, json={"prompt": "", "kind": "daily"}).status_code == 400
        # 不是图片的 base64 → 拒绝
        bad = client.post(
            url,
            json={
                "prompt": "x",
                "kind": "daily",
                "images": [{"name": "x.txt", "data_url": "data:text/plain;base64,aGVsbG8="}],
            },
        )
        assert bad.status_code == 400

        # 删任务时图片跟着级联删除，不留垃圾
        assert client.delete(f"/api/scheduled-tasks/{task['id']}").status_code == 200
        rows = client.get(url).json()["tasks"]
        assert rows == []


@pytest.mark.asyncio
async def test_images_are_stored_and_resent_every_time(settings) -> None:
    db, _, tasks, session = await _setup_services(settings)
    photo = ChatImage(name="shot.png", mime="image/png", data_url=PNG_DATA_URL, size=70)
    task = await tasks.create(
        session["id"], "", kind="daily", hour=9, minute=0, images=[photo]
    )
    assert task["image_count"] == 1
    assert await tasks.images_for(task["id"]) == [photo]

    queue = FakeQueue()
    runner = ScheduledRunner(tasks, queue, tick_seconds=0.01)  # type: ignore[arg-type]
    fired_at = utcnow()
    await _make_due(tasks, task["id"], fired_at - timedelta(minutes=1))
    assert await runner.run_due_once(now=fired_at) == [task["id"]]
    assert queue.sent[0][2] == [photo]

    # 循环任务每次到点都会把同一批图片再发一遍
    again = utcnow() + timedelta(days=1)
    await _make_due(tasks, task["id"], again - timedelta(minutes=1))
    assert await runner.run_due_once(now=again) == [task["id"]]
    assert len(queue.sent) == 2
    assert queue.sent[1][2] == [photo]

    # 删掉任务，图片行也跟着没了
    await tasks.delete(task["id"])
    assert await db.scalar("SELECT COUNT(*) FROM scheduled_task_images") == 0


def test_scheduled_task_can_be_edited(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()["project"]
        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]
        url = f"/api/sessions/{session['id']}/scheduled-tasks"

        created = client.post(
            url,
            json={"prompt": "原始内容", "kind": "once", "month": 8, "day": 15, "hour": 7},
        )
        assert created.status_code == 200, created.text
        task = created.json()["task"]
        assert task["image_count"] == 0

        edited = client.put(
            f"/api/scheduled-tasks/{task['id']}",
            json={
                "prompt": "改过之后的内容",
                "kind": "weekly",
                "weekday": 2,
                "hour": 21,
                "minute": 30,
                "images": [{"name": "shot.png", "data_url": PNG_DATA_URL}],
            },
        )
        assert edited.status_code == 200, edited.text
        updated = edited.json()["task"]
        assert updated["prompt"] == "改过之后的内容"
        assert updated["kind"] == "weekly"
        assert updated["weekday"] == 2
        assert (updated["hour"], updated["minute"]) == (21, 30)
        assert updated["month"] is None and updated["day"] is None
        assert updated["image_count"] == 1
        # 标题属于另一个入口（重命名），编辑正文不会顺手改掉它
        assert updated["title"] == task["title"]

        # 编辑后的图片会存在库里，编辑表单可以取回来回填
        photos = client.get(f"/api/scheduled-tasks/{task['id']}/images").json()["images"]
        assert [photo["data_url"] for photo in photos] == [PNG_DATA_URL]

        # 再编辑一次：图片是整体替换，不是追加
        again = client.put(
            f"/api/scheduled-tasks/{task['id']}",
            json={"prompt": "第二次编辑", "kind": "daily", "hour": 8, "minute": 0},
        )
        assert again.status_code == 200, again.text
        assert again.json()["task"]["image_count"] == 0
        assert client.get(f"/api/scheduled-tasks/{task['id']}/images").json()["images"] == []

        # 正文和图片同时为空 → 拒绝
        empty = client.put(
            f"/api/scheduled-tasks/{task['id']}", json={"prompt": "", "kind": "daily"}
        )
        assert empty.status_code == 400
        # 非法时间类型 → 拒绝
        bad_kind = client.put(
            f"/api/scheduled-tasks/{task['id']}",
            json={"prompt": "x", "kind": "interval", "hour": 8},
        )
        assert bad_kind.status_code == 422
        # 任务不存在 → 404
        assert (
            client.put("/api/scheduled-tasks/missing", json={"prompt": "x", "kind": "daily"}).status_code
            == 404
        )
        assert client.get("/api/scheduled-tasks/missing/images").status_code == 404


@pytest.mark.asyncio
async def test_editing_recomputes_the_next_run(settings) -> None:
    _, _, tasks, session = await _setup_services(settings)
    task = await tasks.create(session["id"], "旧内容", kind="daily", hour=9, minute=0)
    # 模拟「这条循环任务的上次时间点已经过去」
    await _make_due(tasks, task["id"], utcnow() - timedelta(days=2))

    edited = await tasks.update(
        task["id"], "新内容", kind="daily", hour=6, minute=15
    )
    upcoming = datetime.fromisoformat(edited["next_run_at"].replace("Z", "+00:00"))
    assert upcoming > utcnow()
    assert (upcoming.astimezone(local_timezone()).hour, upcoming.astimezone(local_timezone()).minute) == (6, 15)
    assert edited["prompt"] == "新内容"
    assert edited["last_run_at"] is None

    with pytest.raises(FileNotFoundError):
        await tasks.update("missing", "x", kind="daily")
