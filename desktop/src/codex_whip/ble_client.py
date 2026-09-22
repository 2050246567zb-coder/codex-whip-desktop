from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .models import AudioStart, AudioEnd, DeviceMessage, ProtocolMessage
from .protocol import LineDecoder, ProtocolError
from .settings import BleSettings

NUS_RX_CHARACTERISTIC = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_CHARACTERISTIC = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
BATTERY_POLL_SECONDS = 1.0


def host_profile_command(platform: str) -> str:
    host = {"darwin": "MACOS", "win32": "WINDOWS", "linux": "LINUX"}.get(platform, "COMPATIBLE")
    return f"HOST,{host}"

MessageHandler = Callable[[ProtocolMessage], Awaitable[None]]
LogHandler = Callable[[str], None]
StateHandler = Callable[[str], None]


async def drain_message_queue(
    queue: asyncio.Queue[ProtocolMessage],
    handler: MessageHandler,
    limit: int = 32,
) -> int:
    drained = 0
    while drained < max(1, limit):
        try:
            message = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await handler(message)
        drained += 1
    return drained


class BleWhipClient:
    def __init__(
        self,
        settings: BleSettings,
        log_handler: LogHandler = print,
        state_handler: StateHandler | None = None,
        command_queue: asyncio.Queue[str] | None = None,
        device_handler: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._log = log_handler
        self._state_handler = state_handler
        self._command_queue = command_queue
        self._device_handler = device_handler

    def _set_state(self, state: str) -> None:
        if self._state_handler is not None:
            self._state_handler(state)

    async def run(self, handler: MessageHandler, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                self._set_state("scanning")
                self._log(f"[BLE] scanning for {self._settings.device_name!r}...")
                device = await BleakScanner.find_device_by_name(
                    self._settings.device_name,
                    timeout=self._settings.scan_timeout_seconds,
                )
                if device is None:
                    self._set_state("not_found")
                    self._log("[BLE] device not found")
                else:
                    await self._run_connection(device, handler, stop)
            except (BleakError, OSError, asyncio.TimeoutError) as exc:
                self._set_state("error")
                self._log(f"[BLE] connection error: {exc}")

            if not stop.is_set():
                await self._wait_or_stop(self._settings.reconnect_seconds, stop)

    async def _run_connection(
        self, device: object, handler: MessageHandler, stop: asyncio.Event
    ) -> None:
        disconnected = asyncio.Event()
        queue: asyncio.Queue[ProtocolMessage] = asyncio.Queue()
        decoder = LineDecoder()
        host_profile_pending = False
        host_profile_sent = False
        link_report_at: float | None = None

        def on_disconnect(_: BleakClient) -> None:
            logging.getLogger(__name__).info("BLE link disconnected")
            disconnected.set()

        def on_notification(_: object, data: bytearray) -> None:
            nonlocal host_profile_pending
            try:
                for message in decoder.feed(data):
                    if isinstance(message, DeviceMessage):
                        if message.kind == "CAPS" and message.fields == ("HOST_PROFILE", "1"):
                            host_profile_pending = True
                        elif message.kind in {"HOST", "LINK", "TAP2", "TAPENGINE"}:
                            logging.getLogger(__name__).info("BLE %s: %s", message.kind, ",".join(message.fields))
                    if isinstance(message, AudioStart):
                        logging.getLogger(__name__).info("BLE audio start: session=%s rate=%s", message.session, message.sample_rate)
                    elif isinstance(message, AudioEnd):
                        logging.getLogger(__name__).info("BLE audio end: session=%s samples=%s reason=%s", message.session, message.total_samples, message.reason)
                    queue.put_nowait(message)
            except ProtocolError as exc:
                queue.put_nowait(
                    DeviceMessage(kind="PROTOCOL_ERROR", fields=(str(exc),), raw="")
                )

        async with BleakClient(device, disconnected_callback=on_disconnect) as client:
            # Select per-board calibration before accepting any sensor frames.
            if self._device_handler is not None:
                self._device_handler(str(getattr(device, "address", "")))
            self._set_state("connected")
            self._log(f"[BLE] connected to {self._settings.device_name}")
            await client.start_notify(NUS_TX_CHARACTERISTIC, on_notification)
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC, b"PING\n", response=False
            )
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC, b"ARM,1\n", response=False
            )
            next_battery_poll = asyncio.get_running_loop().time() + BATTERY_POLL_SECONDS

            while client.is_connected and not stop.is_set():
                now = asyncio.get_running_loop().time()
                # Negotiate once per connection and only with firmware that
                # advertises support. Old firmware never receives unknown HOST.
                if host_profile_pending and not host_profile_sent:
                    await self._write_command(client, host_profile_command(sys.platform))
                    host_profile_sent = True
                    link_report_at = now + 2.0
                if link_report_at is not None and now >= link_report_at:
                    await self._write_command(client, "LINK")
                    link_report_at = None
                if now >= next_battery_poll:
                    await self._write_command(client, "BATTERY")
                    next_battery_poll = now + BATTERY_POLL_SECONDS
                # Continuous IMU/audio notifications must not starve control
                # writes (threshold sync, RAW mode, recording commands).
                if self._command_queue is not None:
                    try:
                        command = self._command_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    else:
                        await self._write_command(client, command)
                # Audio arrives in short bursts. Drain an existing burst before
                # allocating another set of wait tasks so Windows notifications
                # do not back up long enough to stall the peripheral TX queue.
                drained = await drain_message_queue(queue, handler)
                if drained:
                    await asyncio.sleep(0)
                    if disconnected.is_set() or stop.is_set():
                        break
                    continue

                queue_task = asyncio.create_task(queue.get())
                disconnect_task = asyncio.create_task(disconnected.wait())
                stop_task = asyncio.create_task(stop.wait())
                command_task = (
                    asyncio.create_task(self._command_queue.get())
                    if self._command_queue is not None
                    else None
                )
                battery_task = asyncio.create_task(asyncio.sleep(max(
                    0.0, next_battery_poll - asyncio.get_running_loop().time()
                )))
                tasks = {queue_task, disconnect_task, stop_task}
                tasks.add(battery_task)
                if command_task is not None:
                    tasks.add(command_task)
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if queue_task in done:
                    await handler(queue_task.result())
                if (
                    command_task is not None
                    and command_task in done
                    and client.is_connected
                ):
                    await self._write_command(client, command_task.result())
                if battery_task in done and client.is_connected:
                    await self._write_command(client, "BATTERY")
                    next_battery_poll = (
                        asyncio.get_running_loop().time() + BATTERY_POLL_SECONDS
                    )
                if disconnect_task in done or stop_task in done:
                    break

            if client.is_connected:
                with contextlib.suppress(BleakError):
                    await client.stop_notify(NUS_TX_CHARACTERISTIC)
        self._set_state("disconnected")
        self._log("[BLE] disconnected")

    async def _write_command(self, client: BleakClient, command: str) -> None:
        command = command.strip()
        if not command:
            return
        try:
            payload = (command + "\n").encode("ascii", errors="strict")
        except UnicodeEncodeError:
            self._log("[BLE] refused non-ASCII device command")
            return
        await client.write_gatt_char(NUS_RX_CHARACTERISTIC, payload, response=False)
        await asyncio.sleep(0.025)

    @staticmethod
    async def _wait_or_stop(seconds: float, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
