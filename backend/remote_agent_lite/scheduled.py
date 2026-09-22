"""会话定时任务：到点就给这个会话发一条消息。

调度器只做一件事 —— 替你在输入框里按下发送键。过期之后走的是和手动发消息
完全相同的路径（同一个 `JobQueue`、同一个单 worker、同一套沙箱与流式输出），
所以这里没有权限模型、没有审批、没有独立的执行通道。

时间按「月/日/星期/时/分」拆开存，不带年份，全部按服务器本地时区解释：
一次性任务过了就顺延到明年；每月任务遇到当月没有这一天（比如 2 月的 31 号）
就跳过这个月；错过的时间点不补跑，直接跳到下一次。
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database
from .queueing import JobQueue
from .utils import iso, new_id, utcnow


logger = logging.getLogger(__name__)

#: 调度器扫描间隔（秒）。20 秒对分钟级精度足够，也不占 CPU。
TICK_SECONDS = 20.0
#: 任务标题（列表第一行）取正文前多少字。
TITLE_CHARS = 24
#: 支持的时间类型（和数据库 CHECK 约束一致）。
KINDS = ("once", "daily", "weekly", "monthly")
#: 找下一次出现的兜底上限，防止「2 月 30 日」这种非法日期把循环卡死。
LOOKAHEAD_YEARS = 9
LOOKAHEAD_MONTHS = 48


def local_timezone():
    """服务器本地时区；表单里填的时间按它解释。"""
    return datetime.now().astimezone().tzinfo or UTC


def _local(moment: datetime) -> datetime:
    return moment.astimezone(local_timezone())


def _clean_int(value: Any, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}不对") from exc
    if number < low or number > high:
        raise ValueError(f"{label}不对")
    return number


def next_occurrence(
    kind: str,
    *,
    month: int | None = None,
    day: int | None = None,
    weekday: int | None = None,
    hour: int = 9,
    minute: int = 0,
    after: datetime | None = None,
) -> datetime:
    """算出 `after`（默认现在）之后最近的一次执行时刻，返回 UTC 时间。

    一律「严格晚于 after」，所以错过的那些时间点不会被补跑。
    """
    now = _local(after or utcnow())
    if kind == "once":
        if month is None or day is None:
            raise ValueError("请选择日期")
        for year in range(now.year, now.year + LOOKAHEAD_YEARS):
            try:
                moment = datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
            except ValueError:
                continue  # 这一年没有这一天，看下一年
            if moment > now:
                return moment.astimezone(UTC)
        raise ValueError("这个日期不存在")
    if kind == "daily":
        moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment <= now:
            moment += timedelta(days=1)
        return moment.astimezone(UTC)
    if kind == "weekly":
        if weekday is None:
            raise ValueError("请选择星期")
        target = (weekday + 6) % 7  # 0=周日 … 6=周六 -> Python 的 周一=0 … 周日=6
        moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        moment += timedelta(days=(target - now.weekday()) % 7)
        if moment <= now:
            moment += timedelta(days=7)
        return moment.astimezone(UTC)
    if kind == "monthly":
        if day is None:
            raise ValueError("请选择日期")
        for step in range(LOOKAHEAD_MONTHS + 1):
            year = now.year + (now.month - 1 + step) // 12
            mon = (now.month - 1 + step) % 12 + 1
            if day > calendar.monthrange(year, mon)[1]:
                continue  # 这个月没有这一天，跳过
            moment = datetime(year, mon, day, hour, minute, tzinfo=now.tzinfo)
            if moment > now:
                return moment.astimezone(UTC)
        raise ValueError("这个日期不存在")
    raise ValueError("不支持的时间类型")


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
        month: int | None = None,
        day: int | None = None,
        weekday: int | None = None,
        hour: int = 9,
        minute: int = 0,
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("内容不能为空")
        if kind not in KINDS:
            raise ValueError("不支持的时间类型")
        hour = _clean_int(hour, 0, 23, "小时")
        minute = _clean_int(minute, 0, 59, "分钟")
        month = _clean_int(month, 1, 12, "月份") if kind == "once" else None
        day = (
            _clean_int(day, 1, 31, "日期") if kind in ("once", "monthly") else None
        )
        weekday = _clean_int(weekday, 0, 6, "星期") if kind == "weekly" else None
        now = utcnow()
        scheduled_at = iso(
            next_occurrence(
                kind,
                month=month,
                day=day,
                weekday=weekday,
                hour=hour,
                minute=minute,
                after=now,
            )
        )
        task_id = new_id()
        stamp = iso(now)
        await self.db.execute(
            """
            INSERT INTO scheduled_tasks(
                id, session_id, title, prompt, kind, month, day, weekday, hour,
                minute, next_run_at, enabled, pinned, pinned_at, last_run_at,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                session_id,
                task_title(text),
                text,
                kind,
                month,
                day,
                weekday,
                hour,
                minute,
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
        """跑完一条任务：一次性的直接删掉，循环任务推进到下一个未来时刻。"""
        if task["kind"] == "once":
            await self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task["id"],))
            return
        # 以「实际跑的时刻」为基准往后找：关机/断网期间错过的那些不补跑，
        # 直接跳到下一个未来时刻，否则重启后会一口气补一堆历史任务。
        nxt = next_occurrence(
            task["kind"],
            month=task.get("month"),
            day=task.get("day"),
            weekday=task.get("weekday"),
            hour=int(task["hour"]),
            minute=int(task["minute"]),
            after=fired_at,
        )
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
        for key in ("month", "day", "weekday", "hour", "minute"):
            if data.get(key) is not None:
                data[key] = int(data[key])
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
