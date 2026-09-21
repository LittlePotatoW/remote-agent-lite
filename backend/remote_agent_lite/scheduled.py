"""会话定时任务：到点就给这个会话发一条消息。

调度器只做一件事 —— 替你在输入框里按下发送键。过期之后走的是和手动发消息
完全相同的路径（同一个 `JobQueue`、同一个单 worker、同一套沙箱与流式输出），
所以这里没有权限模型、没有审批、没有独立的执行通道。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database
from .queueing import JobQueue
from .utils import iso, new_id, parse_iso, utcnow


logger = logging.getLogger(__name__)

#: 调度器扫描间隔（秒）。20 秒对分钟级精度足够，也不占 CPU。
TICK_SECONDS = 20.0
#: 任务标题（列表第一行）取正文前多少字。
TITLE_CHARS = 24
#: 周期任务的最小间隔：再小就会把单 worker 队列灌满。
MIN_INTERVAL_SECONDS = 60


def local_timezone():
    """服务器本地时区；表单里填的时间按它解释。"""
    return datetime.now().astimezone().tzinfo or UTC


def parse_when(value: str | None) -> datetime:
    """把表单时间解析成时间点：带时区就照用，不带就按服务器本地时间算。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("请选择执行时间")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("时间格式不对") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone())
    return parsed.astimezone(UTC)


def task_title(prompt: str) -> str:
    text = " ".join(prompt.split())
    return text[:TITLE_CHARS] or "定时任务"


class ScheduledTaskService:
    def __init__(self, db: Database):
        self.db = db

    async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM scheduled_tasks WHERE session_id = ?
            ORDER BY pinned DESC, COALESCE(pinned_at, '') DESC, next_run_at ASC
            """,
            (session_id,),
        )
        return [self._row(row) for row in rows]

    async def get(self, task_id: str) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        if not row:
            raise FileNotFoundError("定时任务不存在")
        return self._row(row)

    async def create(
        self,
        session_id: str,
        prompt: str,
        *,
        kind: str,
        run_at: str | None = None,
        interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("内容不能为空")
        now = utcnow()
        interval: int | None = None
        scheduled_at: str
        if kind == "once":
            scheduled_at = iso(parse_when(run_at))
        elif kind == "interval":
            seconds = int(interval_seconds or 0)
            if seconds < MIN_INTERVAL_SECONDS:
                raise ValueError("间隔至少要 1 分钟")
            interval = seconds
            scheduled_at = iso(now + timedelta(seconds=seconds))
        else:
            raise ValueError("不支持的时间类型")

        task_id = new_id()
        stamp = iso(now)
        await self.db.execute(
            """
            INSERT INTO scheduled_tasks(
                id, session_id, title, prompt, kind, run_at, interval_seconds,
                next_run_at, enabled, pinned, pinned_at, last_run_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                session_id,
                task_title(text),
                text,
                kind,
                run_at if kind == "once" else None,
                interval,
                scheduled_at,
                stamp,
                stamp,
            ),
        )
        return await self.get(task_id)

    async def rename(self, task_id: str, title: str) -> dict[str, Any]:
        clean = title.strip()[:80]
        if not clean:
            raise ValueError("标题不能为空")
        await self.get(task_id)
        await self.db.execute(
            "UPDATE scheduled_tasks SET title = ?, updated_at = ? WHERE id = ?",
            (clean, iso(), task_id),
        )
        return await self.get(task_id)

    async def set_pinned(self, task_id: str, pinned: bool) -> dict[str, Any]:
        await self.get(task_id)
        await self.db.execute(
            "UPDATE scheduled_tasks SET pinned = ?, pinned_at = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, iso() if pinned else None, iso(), task_id),
        )
        return await self.get(task_id)

    async def delete(self, task_id: str) -> None:
        await self.get(task_id)
        await self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))

    async def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = iso(now or utcnow())
        rows = await self.db.fetchall(
            """
            SELECT * FROM scheduled_tasks
            WHERE enabled = 1 AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (moment,),
        )
        return [self._row(row) for row in rows]

    async def after_fire(self, task: dict[str, Any], fired_at: datetime) -> None:
        """跑完一条任务：一次性的直接删掉，周期任务推进到下一个未来时刻。"""
        if task["kind"] == "once":
            await self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task["id"],))
            return
        interval = max(int(task["interval_seconds"] or 0), MIN_INTERVAL_SECONDS)
        planned = parse_iso(task["next_run_at"]) or fired_at
        nxt = planned + timedelta(seconds=interval)
        # 服务器关机/断网期间错过的那些周期不补跑，直接跳到下一个未来时刻，
        # 否则重启后会一口气补一堆历史任务。
        guard = 0
        while nxt <= fired_at and guard < 100_000:
            nxt += timedelta(seconds=interval)
            guard += 1
        stamp = iso(fired_at)
        await self.db.execute(
            """
            UPDATE scheduled_tasks
            SET next_run_at = ?, last_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (iso(nxt), stamp, stamp, task["id"]),
        )

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        data["pinned"] = bool(data.get("pinned"))
        if data.get("interval_seconds") is not None:
            data["interval_seconds"] = int(data["interval_seconds"])
        return data


class ScheduledRunner:
    """每 20 秒扫一次到期任务，到点就调 `queue.enqueue`。"""

    def __init__(
        self,
        tasks: ScheduledTaskService,
        queue: JobQueue,
        *,
        tick_seconds: float = TICK_SECONDS,
    ):
        self.tasks = tasks
        self.queue = queue
        self.tick_seconds = tick_seconds
        self._loop_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if not self._loop_task:
            return
        self._loop_task.cancel()
        try:
            await self._loop_task
        except asyncio.CancelledError:
            pass
        self._loop_task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # 单次异常不能让调度器整个死掉
                logger.exception("scheduled task tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def run_due_once(self, *, now: datetime | None = None) -> list[str]:
        """扫一次到期任务，返回本轮处理过的任务 id（便于测试）。"""
        moment = now or utcnow()
        handled: list[str] = []
        for task in await self.tasks.due(moment):
            try:
                await self.queue.enqueue(task["session_id"], task["prompt"])
            except FileNotFoundError:
                # 会话已经不在了，任务跟着清掉
                await self.tasks.delete(task["id"])
                handled.append(task["id"])
                continue
            except RuntimeError as exc:
                # 配额/磁盘这类临时问题：这一轮不推进，下个 tick 再试
                logger.warning("定时任务 %s 暂时不能入队：%s", task["id"], exc)
                continue
            await self.tasks.after_fire(task, moment)
            handled.append(task["id"])
        return handled
