from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import wave
from pathlib import Path

from bleak import BleakClient, BleakScanner


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop" / "src"))

from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.models import AudioChunk, AudioEnd, AudioStart, DeviceMessage
from codex_whip.protocol import LineDecoder
from codex_whip.voice import VoiceAudioAssembler, WhisperCppTranscriber


def speak_test_phrase() -> None:
    try:
        wav = Path(os.environ["TEMP"]) / "codex-whip-voice-synthetic.wav"
        environment = os.environ.copy()
        environment["CODEX_WHIP_TEST_WAV"] = str(wav)
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "$p=$env:CODEX_WHIP_TEST_WAV; "
                "$s=New-Object System.Media.SoundPlayer $p; $s.PlaySync()",
            ],
            env=environment,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        print(f"SPEAKER_WARNING={exc}")


async def run(output: Path, *, speak: bool) -> int:
    device = await BleakScanner.find_device_by_name("CodexWhip", timeout=12)
    if device is None:
        raise RuntimeError("CodexWhip BLE device not found")
    messages: asyncio.Queue[object] = asyncio.Queue()
    decoder = LineDecoder()

    def notification(_sender: object, data: bytearray) -> None:
        for message in decoder.feed(data):
            messages.put_nowait(message)

    assembler = VoiceAudioAssembler()
    async with BleakClient(device) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notification)

        async def command(text: str) -> None:
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC, (text + "\n").encode("ascii"), response=False
            )
            await asyncio.sleep(0.08)

        await command("PING")
        await command("VOICE,1")
        await command("VOICE,START,800,7000")
        speaker_started = False
        end: AudioEnd | None = None
        while end is None:
            message = await asyncio.wait_for(messages.get(), timeout=15)
            if isinstance(message, DeviceMessage):
                print(f"DEVICE={message.raw}")
            elif isinstance(message, AudioStart):
                assembler.begin(message)
                print(f"AUDIO_START={message.session},{message.sample_rate},{message.codec}")
                if speak and not speaker_started:
                    speaker_started = True
                    threading.Thread(target=speak_test_phrase, daemon=True).start()
            elif isinstance(message, AudioChunk):
                assembler.add(message)
                if message.sequence % 50 == 0:
                    print(f"AUDIO_CHUNK={message.sequence},{message.sample_count}")
            elif isinstance(message, AudioEnd):
                end = message
        await command("VOICE,0")
        await client.stop_notify(NUS_TX_CHARACTERISTIC)

    fallback_rate = assembler.start.sample_rate if assembler.start is not None else 16000
    fallback_pcm = bytes(assembler.pcm)
    try:
        sample_rate, pcm = assembler.finish(end)
    except ValueError:
        if end.reason != "NO_SPEECH":
            raise
        sample_rate, pcm = fallback_rate, fallback_pcm
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)
    print(f"AUDIO_END={end.session},{end.total_samples},{end.reason}")
    print(f"WAV={output.resolve()}")
    samples = [
        int.from_bytes(pcm[index:index + 2], "little", signed=True)
        for index in range(0, len(pcm), 2)
    ]
    mean_abs = sum(abs(value) for value in samples) // max(1, len(samples))
    print(f"LEVEL={mean_abs},{max((abs(value) for value in samples), default=0)}")
    if end.reason != "NO_SPEECH":
        print(f"TRANSCRIPT={WhisperCppTranscriber().transcribe(sample_rate, pcm)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "voice-smoke.wav")
    parser.add_argument("--speak", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.output, speak=args.speak))


if __name__ == "__main__":
    raise SystemExit(main())
