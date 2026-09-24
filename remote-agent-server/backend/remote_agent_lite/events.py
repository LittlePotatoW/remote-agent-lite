from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Event:
    id: int
    event_type: str
    session_id: str | None
    project_id: str | None
    payload: dict[str, Any]

    def to_sse(self) -> str:
        lines = [f"id: {self.id}", f"event: {self.event_type}"]
        if self.session_id:
            lines.append(f"x-session-id: {self.session_id}")
        if self.project_id:
            lines.append(f"x-project-id: {self.project_id}")
        data = json.dumps(
            {"type": self.event_type, "session_id": self.session_id,
             "project_id": self.project_id, **self.payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(f"data: {data}")
        lines.append("")
        return "\n".join(lines) + "\n"


def resync_event(*, session_id: str | None = None) -> Event:
    """提示订阅者「你漏过事件了，请重新拉取状态」。"""
    return Event(
        id=0,
        event_type="resync",
        session_id=session_id,
        project_id=None,
        payload={"reason": "queue_overflow"},
    )


class EventBus:
    def __init__(self, *, max_queue: int = 1000):
        self.max_queue = max_queue
        # 每个订阅者一个队列，值表示「该订阅者是否因为队列满丢过事件」。
        self._subscribers: dict[asyncio.Queue[Event], bool] = {}
        self._counter = 0
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.max_queue)
        async with self._lock:
            self._subscribers[queue] = False
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.pop(queue, None)

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        async with self._lock:
            self._counter += 1
            event = Event(
                id=self._counter,
                event_type=event_type,
                session_id=session_id,
                project_id=project_id,
                payload=payload or {},
            )
            subscribers = list(self._subscribers)
        for queue in subscribers:
            self._offer(queue, event)

    def _offer(self, queue: asyncio.Queue[Event], event: Event) -> None:
        """放入事件；队列满时丢最旧事件，并标记该订阅者需要重新同步。"""
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
        if queue in self._subscribers:
            self._subscribers[queue] = True

    def needs_resync(self, queue: asyncio.Queue[Event]) -> bool:
        return self._subscribers.get(queue, False)

    async def take_resync(self, queue: asyncio.Queue[Event]) -> bool:
        """读取并清除「需要重新同步」标记。"""
        async with self._lock:
            if not self._subscribers.get(queue, False):
                return False
            self._subscribers[queue] = False
            return True
