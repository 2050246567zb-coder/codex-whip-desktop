import asyncio

from codex_whip.ble_client import drain_message_queue
from codex_whip.models import DeviceMessage


def message(value: str) -> DeviceMessage:
    return DeviceMessage("TEST", (value,), f"TEST,{value}")


def test_drain_message_queue_processes_existing_audio_burst_in_order() -> None:
    queue: asyncio.Queue[DeviceMessage] = asyncio.Queue()
    for value in ("1", "2", "3"):
        queue.put_nowait(message(value))
    received: list[str] = []

    async def handler(item: DeviceMessage) -> None:
        received.append(item.fields[0])

    drained = asyncio.run(drain_message_queue(queue, handler))

    assert drained == 3
    assert received == ["1", "2", "3"]
    assert queue.empty()


def test_drain_message_queue_honors_burst_limit() -> None:
    queue: asyncio.Queue[DeviceMessage] = asyncio.Queue()
    for value in ("1", "2", "3"):
        queue.put_nowait(message(value))

    async def handler(_item: DeviceMessage) -> None:
        return None

    drained = asyncio.run(drain_message_queue(queue, handler, limit=2))

    assert drained == 2
    assert queue.qsize() == 1
