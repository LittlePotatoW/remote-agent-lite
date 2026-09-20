from __future__ import annotations

from typing import Any

from .db import Database
from .utils import iso, new_id


DEFAULT_TITLE = "新对话"


_SESSION_COLUMNS = """
    s.*,
    (SELECT COUNT(*) FROM messages m
     WHERE m.session_id = s.id AND m.seq > s.last_read_seq) AS unread_count,
    (SELECT m.content FROM messages m
     WHERE m.session_id = s.id ORDER BY m.seq DESC LIMIT 1) AS last_message,
    (SELECT m.status FROM messages m
     WHERE m.session_id = s.id ORDER BY m.seq DESC LIMIT 1) AS last_message_status,
    (SELECT j.status FROM jobs j
     WHERE j.session_id = s.id AND j.status IN ('running', 'queued')
     ORDER BY CASE j.status WHEN 'running' THEN 0 ELSE 1 END, j.created_at
     LIMIT 1) AS job_status
"""


class SessionService:
    def __init__(self, db: Database):
        self.db = db

    async def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"""
            SELECT {_SESSION_COLUMNS}
            FROM sessions s
            WHERE s.project_id = ?
            ORDER BY s.pinned DESC, COALESCE(s.pinned_at, '') DESC, s.updated_at DESC
            """,
            (project_id,),
        )
        return [self._row(row) for row in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"""
            SELECT {_SESSION_COLUMNS}
            FROM sessions s
            ORDER BY s.project_id, s.pinned DESC, COALESCE(s.pinned_at, '') DESC,
                     s.updated_at DESC
            """
        )
        return [self._row(row) for row in rows]

    async def get(self, session_id: str) -> dict[str, Any]:
        row = await self.db.fetchone(
            f"SELECT {_SESSION_COLUMNS} FROM sessions s WHERE s.id = ?", (session_id,)
        )
        if not row:
            raise FileNotFoundError("对话不存在")
        return self._row(row)

    async def create(self, project_id: str, title: str | None = None) -> dict[str, Any]:
        session_id = new_id()
        now = iso()
        clean_title = (title or DEFAULT_TITLE).strip()[:80] or DEFAULT_TITLE
        await self.db.execute(
            """
            INSERT INTO sessions(
                id, project_id, title, thread_id, pinned, pinned_at,
                created_at, updated_at, last_message_seq, last_read_seq
            )
            VALUES(?, ?, ?, NULL, 0, NULL, ?, ?, 0, 0)
            """,
            (session_id, project_id, clean_title, now, now),
        )
        return await self.get(session_id)

    async def copy_messages(self, source_id: str, target_id: str) -> int:
        """Copy the visible transcript into another session (display only)."""

        await self.get(source_id)
        await self.get(target_id)
        rows = await self.db.fetchall(
            """
            SELECT role, content, status FROM messages
            WHERE session_id = ? ORDER BY seq ASC
            """,
            (source_id,),
        )
        copied = 0
        for row in rows:
            content = row["content"] or ""
            # 半截的助手消息（还在流式或已失败且没内容）复制过去只会是个空壳。
            if row["role"] == "assistant" and not content.strip():
                continue
            await self.add_message(target_id, row["role"], content, status="completed")
            copied += 1
        return copied

    async def rename(self, session_id: str, title: str) -> dict[str, Any]:
        clean_title = title.strip()[:80]
        if not clean_title:
            raise ValueError("标题不能为空")
        await self.get(session_id)
        await self.db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (clean_title, iso(), session_id),
        )
        return await self.get(session_id)

    async def set_pinned(self, session_id: str, pinned: bool) -> dict[str, Any]:
        await self.get(session_id)
        await self.db.execute(
            "UPDATE sessions SET pinned = ?, pinned_at = ? WHERE id = ?",
            (1 if pinned else 0, iso() if pinned else None, session_id),
        )
        return await self.get(session_id)

    async def set_thread(self, session_id: str, thread_id: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET thread_id = ?, updated_at = ? WHERE id = ?",
            (thread_id, iso(), session_id),
        )

    async def delete(self, session_id: str) -> None:
        await self.get(session_id)
        await self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    async def next_seq(self, session_id: str) -> int:
        current = await self.db.scalar(
            "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?", (session_id,)
        )
        return int(current or 0) + 1

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        status: str = "completed",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        message_id = new_id()
        seq = await self.next_seq(session_id)
        now = iso()
        completed_at = now if status == "completed" else None
        await self.db.execute(
            """
            INSERT INTO messages(id, session_id, seq, role, content, status, job_id,
                                 created_at, completed_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, seq, role, content, status, job_id, now, completed_at),
        )
        await self.db.execute(
            "UPDATE sessions SET updated_at = ?, last_message_seq = ? WHERE id = ?",
            (now, seq, session_id),
        )
        return {
            "id": message_id,
            "session_id": session_id,
            "seq": seq,
            "role": role,
            "content": content,
            "status": status,
            "job_id": job_id,
            "created_at": now,
            "completed_at": completed_at,
        }

    async def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if content is not None:
            assignments.append("content = ?")
            params.append(content)
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
            if status == "completed":
                assignments.append("completed_at = ?")
                params.append(iso())
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if not assignments:
            return
        params.append(message_id)
        await self.db.execute(
            f"UPDATE messages SET {', '.join(assignments)} WHERE id = ?", tuple(params)
        )

    async def messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        await self.get(session_id)
        limit = max(1, min(limit, 300))
        if after_seq is not None:
            rows = await self.db.fetchall(
                """
                SELECT * FROM messages WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (session_id, after_seq, limit),
            )
        elif before_seq is not None:
            rows = await self.db.fetchall(
                """
                SELECT * FROM messages WHERE session_id = ? AND seq < ?
                ORDER BY seq DESC LIMIT ?
                """,
                (session_id, before_seq, limit),
            )
            rows = list(reversed(rows))
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            )
            rows = list(reversed(rows))
        return [dict(row) for row in rows]

    async def mark_read(self, session_id: str, seq: int) -> None:
        await self.db.execute(
            "UPDATE sessions SET last_read_seq = MAX(last_read_seq, ?) WHERE id = ?",
            (seq, session_id),
        )

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["pinned"] = bool(data.get("pinned"))
        if "unread_count" in data:
            data["unread_count"] = int(data["unread_count"] or 0)
        return data
