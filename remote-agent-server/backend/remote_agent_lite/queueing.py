from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Sequence

from .codex import CodexClient, CodexError, TurnStream
from .config import Settings
from .db import Database
from .events import EventBus
from .images import ChatImage
from .projects import ProjectService
from .sessions import DEFAULT_TITLE, SessionService
from .utils import iso, new_id


logger = logging.getLogger(__name__)


#: Used when a turn carries images but no text, so history still reads sensibly.
IMAGE_ONLY_PROMPT = "请看这张图片。"


class JobQueue:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        projects: ProjectService,
        sessions: SessionService,
        codex: CodexClient,
        events: EventBus,
    ):
        self.db = db
        self.settings = settings
        self.projects = projects
        self.sessions = sessions
        self.codex = codex
        self.events = events
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[Any] | None = None
        self._running_job_id: str | None = None
        self._stopping = False
        self._recovered_tasks: set[asyncio.Task[Any]] = set()
        # Inline chat images live here only until the job runs. They are never
        # written to the database, to project storage or to logs.
        self._images: dict[str, list[ChatImage]] = {}

    async def start(self) -> None:
        await self.recover()
        self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    async def enqueue(
        self,
        session_id: str,
        prompt: str,
        images: Sequence[ChatImage] | None = None,
    ) -> dict[str, Any]:
        session = await self.sessions.get(session_id)
        project = await self.projects.get(session["project_id"])
        ok, reason = await self.projects.can_accept_bytes(project["id"], 0)
        if not ok:
            raise RuntimeError(reason)
        job_id = new_id()
        text = prompt.strip() or IMAGE_ONLY_PROMPT
        message = await self.sessions.add_message(
            session_id, "user", text, status="completed"
        )
        now = iso()
        await self.db.execute(
            """
            INSERT INTO jobs(id, session_id, project_id, message_id, prompt, status, created_at)
            VALUES(?, ?, ?, ?, ?, 'queued', ?)
            """,
            (job_id, session_id, project["id"], message["id"], text, now),
        )
        if images:
            self._images[job_id] = list(images)
        if session["title"] == DEFAULT_TITLE and session["last_message_seq"] == 0:
            title = " ".join(text.split())[:30] or DEFAULT_TITLE
            await self.sessions.rename(session_id, title)
        await self.events.publish(
            "turn.status",
            {"job_id": job_id, "status": "queued", "message_id": message["id"]},
            session_id=session_id,
            project_id=project["id"],
        )
        await self.events.publish(
            "session.status",
            {"status": "queued", "job_id": job_id},
            session_id=session_id,
            project_id=project["id"],
        )
        await self._queue.put(job_id)
        return {"job_id": job_id, "message": message, "status": "queued"}

    async def cancel(self, session_id: str) -> dict[str, Any]:
        session = await self.sessions.get(session_id)
        running = await self.db.fetchone(
            """
            SELECT * FROM jobs WHERE session_id = ? AND status = 'running'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        )
        if running:
            if session.get("thread_id"):
                try:
                    result = await self.codex.thread_read(session["thread_id"], include_turns=True)
                    thread = result.get("thread") or {}
                    active = next(
                        (
                            turn
                            for turn in reversed(thread.get("turns") or [])
                            if turn.get("status") == "inProgress"
                        ),
                        None,
                    )
                    if active and active.get("id"):
                        await self.codex.interrupt(session["thread_id"], active["id"])
                except CodexError:
                    pass
            return {"status": "cancelling", "job_id": running["id"]}
        queued = await self.db.fetchall(
            """
            SELECT id FROM jobs WHERE session_id = ? AND status = 'queued'
            ORDER BY created_at
            """,
            (session_id,),
        )
        for row in queued:
            self._images.pop(row["id"], None)
            await self._mark_job(row["id"], "cancelled", "cancelled before start")
        return {"status": "cancelled", "count": len(queued)}

    async def status(self, session_id: str) -> dict[str, Any]:
        running = await self.db.fetchone(
            """
            SELECT * FROM jobs WHERE session_id = ? AND status = 'running'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        )
        queued = await self.db.fetchall(
            """
            SELECT COUNT(*) AS count FROM jobs
            WHERE status = 'queued' AND created_at <= COALESCE(
                (SELECT created_at FROM jobs WHERE status = 'running' LIMIT 1),
                '9999'
            )
            """
        )
        return {
            "running_job_id": running["id"] if running else None,
            "queued_jobs": int(queued[0]["count"] if queued else 0),
            "global_running_job_id": self._running_job_id,
        }

    async def recover(self) -> None:
        queued = await self.db.fetchall(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at"
        )
        running = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status = 'running' ORDER BY created_at"
        )
        for row in running:
            recovered = await self._recover_running_job(row)
            if not recovered:
                await self._mark_job(row["id"], "interrupted", "server restarted")
                await self.db.execute(
                    """
                    UPDATE messages SET status = 'failed', error = ?
                    WHERE job_id = ? AND role = 'assistant'
                    """,
                    ("server restarted", row["id"]),
                )
        for row in queued:
            await self._queue.put(row["id"])

    async def _recover_running_job(self, job: Any) -> bool:
        try:
            session = await self.sessions.get(job["session_id"])
            project = await self.projects.get(job["project_id"])
            if not session.get("thread_id"):
                return False
            stream = await self.codex.recover_turn(
                session["thread_id"], await self.projects.project_dir(project["id"])
            )
            if not stream:
                return False
            task = asyncio.create_task(self._finish_recovered(job, session, project, stream))
            self._recovered_tasks.add(task)
            task.add_done_callback(self._recovered_tasks.discard)
            return True
        except Exception:
            logger.exception("failed to recover running job %s", job["id"])
            return False

    async def _finish_recovered(
        self,
        job: Any,
        session: dict[str, Any],
        project: dict[str, Any],
        stream: TurnStream,
    ) -> None:
        message_id = await self.db.scalar(
            """
            SELECT id FROM messages
            WHERE job_id = ? AND role = 'assistant'
            ORDER BY seq DESC LIMIT 1
            """,
            (job["id"],),
        )
        if not message_id:
            message = await self.sessions.add_message(
                session["id"], "assistant", "", status="streaming", job_id=job["id"]
            )
            message_id = message["id"]
        await self._consume_stream(job, session, project, message_id, stream, recovered=True)

    async def _worker_loop(self) -> None:
        while not self._stopping:
            if self._recovered_tasks:
                await asyncio.gather(*list(self._recovered_tasks), return_exceptions=True)
            job_id = await self._queue.get()
            self._running_job_id = job_id
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job %s failed unexpectedly", job_id)
                await self._mark_job(job_id, "failed", "internal queue error")
            finally:
                self._running_job_id = None
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        # A job recovered after a restart has no images any more: they only ever
        # existed in memory, so recovery simply runs it as a text-only turn.
        images = self._images.pop(job_id, [])
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not job or job["status"] != "queued":
            return
        session = await self.sessions.get(job["session_id"])
        project = await self.projects.get(job["project_id"])
        ok, reason = await self.projects.can_accept_bytes(project["id"], 0)
        if not ok:
            await self._fail_job(job, reason)
            return
        await self._set_job_running(job_id)
        try:
            project_dir = await self.projects.project_dir(project["id"])
            thread_id, recreated = await self.codex.ensure_thread(session, project_dir)
            if recreated:
                await self.sessions.set_thread(session["id"], thread_id)
                await self.sessions.add_message(
                    session["id"],
                    "system",
                    "已创建新的 Codex 会话上下文。",
                    status="completed",
                )
            message = await self.sessions.add_message(
                session["id"], "assistant", "", status="streaming", job_id=job_id
            )
            await self.events.publish(
                "turn.status",
                {"job_id": job_id, "status": "running", "message_id": message["id"]},
                session_id=session["id"],
                project_id=project["id"],
            )
            stream = await self.codex.start_turn(
                thread_id,
                job["prompt"],
                cwd=project_dir,
                images=images,
                client_user_message_id=job["id"],
            )
            await self._consume_stream(job, session, project, message["id"], stream)
        except Exception as exc:
            logger.exception("job %s failed", job_id)
            message_id = locals().get("message", {}).get("id")
            if not message_id:
                failure = await self.sessions.add_message(
                    session["id"], "assistant", "", status="failed", job_id=job_id
                )
                message_id = failure["id"]
                await self.sessions.update_message(
                    message_id, content="", status="failed", error=str(exc)
                )
            await self._fail_job(job, str(exc), message_id=message_id)

    async def _consume_stream(
        self,
        job: Any,
        session: dict[str, Any],
        project: dict[str, Any],
        message_id: str,
        stream: TurnStream,
        *,
        recovered: bool = False,
    ) -> None:
        accumulated = ""
        last_persist = asyncio.get_running_loop().time()
        last_publish = last_persist
        pending_delta = ""
        seq = await self._message_seq(message_id)
        # 超时要从第一个增量就开始算：原来只在最后等 stream.done，
        # 而它要等流结束才会完成，等于超时几乎永远不会触发。
        deadline = last_persist + self.settings.codex_turn_timeout_seconds
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                delta = await stream.next_delta(timeout=remaining)
                if delta is None:
                    break
                accumulated += delta
                pending_delta += delta
                now = asyncio.get_running_loop().time()
                if len(pending_delta) >= 512 or (
                    now - last_publish >= self.settings.sse_coalesce_ms / 1000.0
                ):
                    await self.events.publish(
                        "message.delta",
                        {"message_id": message_id, "delta": pending_delta, "seq": seq},
                        session_id=session["id"],
                        project_id=project["id"],
                    )
                    pending_delta = ""
                    last_publish = now
                if now - last_persist >= 1.0:
                    await self.sessions.update_message(message_id, content=accumulated)
                    last_persist = now
            if pending_delta:
                await self.events.publish(
                    "message.delta",
                    {"message_id": message_id, "delta": pending_delta, "seq": seq},
                    session_id=session["id"],
                    project_id=project["id"],
                )
            result = await asyncio.wait_for(
                stream.done,
                timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
            )
        except asyncio.TimeoutError:
            logger.warning("turn %s timed out; interrupting", stream.turn_id)
            await self._interrupt_stream(stream)
            await stream.fail("turn timed out")
            result = await stream.done
        except Exception as exc:
            await stream.fail(str(exc))
            result = await stream.done
        final_text = result.text or accumulated
        await self.sessions.update_message(
            message_id,
            content=final_text,
            status="completed" if result.status == "succeeded" else "failed",
            error=result.error,
        )
        if result.status == "succeeded":
            await self._complete_job(job, project, message_id, final_text, recovered)
        else:
            status = "interrupted" if result.status == "interrupted" else "failed"
            await self._mark_job(job["id"], status, result.error or status)
            await self.events.publish(
                "message.completed",
                {
                    "message_id": message_id,
                    "content": final_text,
                    "status": result.status,
                    "error": result.error,
                },
                session_id=session["id"],
                project_id=project["id"],
            )
            await self.events.publish(
                "turn.status",
                {"job_id": job["id"], "status": status, "error": result.error},
                session_id=session["id"],
                project_id=project["id"],
            )

    async def _interrupt_stream(self, stream: TurnStream) -> None:
        """尽力打断服务端仍在跑的 turn；失败只记录日志，不改变超时的结论。"""
        if not stream.turn_id:
            return
        try:
            await self.codex.interrupt(stream.thread_id, stream.turn_id)
        except Exception:
            logger.warning("failed to interrupt turn %s", stream.turn_id, exc_info=True)

    async def _complete_job(
        self,
        job: Any,
        project: dict[str, Any],
        message_id: str,
        final_text: str,
        recovered: bool,
    ) -> None:
        message = await self.db.fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
        session = await self.sessions.get(job["session_id"])
        await self._mark_job(job["id"], "succeeded", None)
        await self.events.publish(
            "message.completed",
            {
                "message_id": message_id,
                "content": final_text,
                "status": "succeeded",
            },
            session_id=session["id"],
            project_id=project["id"],
        )
        await self.events.publish(
            "turn.status",
            {"job_id": job["id"], "status": "succeeded", "message_id": message_id},
            session_id=session["id"],
            project_id=project["id"],
        )
        await self.events.publish(
            "files.changed", {"reason": "turn_completed"}, project_id=project["id"]
        )
        await self.events.publish(
            "session.status", {"status": "idle"}, session_id=session["id"], project_id=project["id"]
        )

    async def _set_job_running(self, job_id: str) -> None:
        await self.db.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
            (iso(), job_id),
        )

    async def _mark_job(self, job_id: str, status: str, error: str | None) -> None:
        await self.db.execute(
            "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, iso(), job_id),
        )

    async def _fail_job(
        self, job: Any, error: str, *, message_id: str | None = None
    ) -> None:
        self._images.pop(job["id"], None)
        await self._mark_job(job["id"], "failed", error)
        if message_id:
            await self.sessions.update_message(message_id, status="failed", error=error)
        await self.events.publish(
            "turn.status",
            {"job_id": job["id"], "status": "failed", "error": error},
            session_id=job["session_id"],
            project_id=job["project_id"],
        )

    async def _message_seq(self, message_id: str) -> int:
        value = await self.db.scalar("SELECT seq FROM messages WHERE id = ?", (message_id,))
        return int(value or 0)
