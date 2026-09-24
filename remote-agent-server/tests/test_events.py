from __future__ import annotations

import asyncio
from types import SimpleNamespace

from remote_agent_lite.api import events as events_endpoint
from remote_agent_lite.events import EventBus


def _drain(queue: asyncio.Queue) -> list:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def test_queue_overflow_keeps_newest_and_flags_resync() -> None:
    bus = EventBus(max_queue=2)
    queue = await bus.subscribe()

    for index in range(5):
        await bus.publish("message.delta", {"delta": str(index)})

    # 队列满时丢最旧、留最新，并标记订阅者需要重新同步
    assert bus.needs_resync(queue) is True
    assert [event.payload["delta"] for event in _drain(queue)] == ["3", "4"]

    assert await bus.take_resync(queue) is True
    assert bus.needs_resync(queue) is False
    assert await bus.take_resync(queue) is False


async def test_no_resync_flag_without_overflow() -> None:
    bus = EventBus(max_queue=4)
    queue = await bus.subscribe()
    await bus.publish("message.delta", {"delta": "x"})

    assert bus.needs_resync(queue) is False
    assert len(_drain(queue)) == 1


async def test_unsubscribe_forgets_resync_state() -> None:
    bus = EventBus(max_queue=1)
    queue = await bus.subscribe()
    await bus.publish("a", {})
    await bus.publish("b", {})
    assert bus.needs_resync(queue) is True

    await bus.unsubscribe(queue)
    assert bus.needs_resync(queue) is False


async def test_publish_without_subscribers_is_noop() -> None:
    bus = EventBus(max_queue=1)
    await bus.publish("message.delta", {"delta": "x"})


class _FakeRequest:
    """只满足 /api/events 生成器所需的最小请求对象。"""

    def __init__(self, app_state) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(ral=app_state))

    async def is_disconnected(self) -> bool:
        return False


async def test_events_endpoint_emits_resync_after_overflow() -> None:
    # 用小队列制造溢出，验证 SSE 端点会把「需要重新同步」告诉客户端
    bus = EventBus(max_queue=2)
    request = _FakeRequest(SimpleNamespace(events=bus))

    response = await events_endpoint(request, session_id=None, _=None)
    iterator = response.body_iterator
    assert await iterator.__anext__() == ": connected\n\n"
    assert len(bus._subscribers) == 1

    for index in range(5):
        await bus.publish("message.delta", {"delta": str(index)})

    first = await asyncio.wait_for(iterator.__anext__(), timeout=5)
    assert "event: resync" in first
    await iterator.aclose()
    assert not bus._subscribers
