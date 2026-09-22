from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
from pathlib import Path

from . import __version__
from .app import EventProcessor, sample_event
from .ble_client import BleWhipClient
from .ble_preference import BleDevicePreferenceStore
from .gate import EventGate
from .messages import PromptSelector
from .senders import DryRunSender, create_live_sender
from .settings import Settings, load_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-whip")
    parser.add_argument(
        "--config", type=Path, help="TOML configuration file (defaults are built in)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="listen for the BLE whip")
    run.add_argument(
        "--live",
        action="store_true",
        help="explicitly allow input into the packaged Codex desktop app",
    )

    simulate = subparsers.add_parser("simulate", help="inject one local test event")
    simulate.add_argument("--live", action="store_true", help="send the test to Codex")

    doctor = subparsers.add_parser(
        "doctor", help="check dependencies and Codex window discovery"
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="fail unless exactly one Codex window and an empty composer are ready",
    )
    return parser


def _sender(settings: Settings, live: bool):
    if not live:
        return DryRunSender()
    return create_live_sender(settings.codex)


def _processor(settings: Settings, live: bool) -> EventProcessor:
    return EventProcessor(
        gate=EventGate(settings.events.minimum_interval_seconds),
        prompts=PromptSelector(settings.messages),
        sender=_sender(settings, live),
    )


async def _run(settings: Settings, live: bool) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stop.set)

    mode = "LIVE" if live else "DRY-RUN"
    print(f"Codex Whip {__version__} | {mode}")
    if live:
        print("Live mode is armed: an accepted WHIP event can submit one Codex prompt.")
    client = BleWhipClient(
        settings.ble, device_preference=BleDevicePreferenceStore()
    )
    await client.run(_processor(settings, live).handle, stop)


async def _simulate(settings: Settings, live: bool) -> None:
    await _processor(settings, live).handle(sample_event())


def _doctor(settings: Settings, *, strict: bool = False) -> int:
    report: dict[str, object] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "device_name": settings.ble.device_name,
    }
    failures: list[str] = []
    try:
        import bleak

        report["bleak"] = getattr(bleak, "__version__", "installed")
    except ImportError:
        report["bleak"] = "missing"
        failures.append("bleak is not installed")

    if sys.platform in {"win32", "darwin"}:
        try:
            sender = create_live_sender(settings.codex, prompt_permission=False)
            windows = sender.diagnose()
            report["codex_windows"] = windows
            if strict:
                if len(windows) != 1:
                    failures.append(
                        f"expected exactly one visible Codex window, found {len(windows)}"
                    )
                else:
                    report["codex_ready"] = sender.check_ready()
        except Exception as exc:
            report["codex_windows_error"] = str(exc)
            failures.append(str(exc))
    else:
        report["codex_windows"] = "unsupported platform"
        failures.append(f"unsupported platform: {sys.platform}")

    if strict and failures:
        report["failures"] = failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if strict and failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings(args.config)
    if args.command == "doctor":
        return _doctor(settings, strict=args.strict)
    if args.command == "simulate":
        asyncio.run(_simulate(settings, args.live))
        return 0
    if args.command == "run":
        try:
            asyncio.run(_run(settings, args.live))
        except KeyboardInterrupt:
            pass
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
