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


class EventBus:
    def __init__(self, *, max_queue: int = 1000):
        self.max_queue = max_queue
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._counter = 0
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.max_queue)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

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
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

