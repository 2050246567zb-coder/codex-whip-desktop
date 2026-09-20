from array import array

import pytest

from codex_whip.virtual_microphone import (
    VirtualMicrophoneBridge,
    VirtualMicrophoneError,
    select_virtual_output,
    upsample_16k_to_48k,
)


def test_device_selection_is_platform_specific_and_fail_closed():
    devices = [
        {"name": "Built-in Speakers", "max_output_channels": 2, "hostapi": 0,
         "default_samplerate": 48000},
        {"name": "BlackHole 2ch", "max_output_channels": 2, "hostapi": 0,
         "default_samplerate": 48000},
        {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_output_channels": 2,
         "hostapi": 1, "default_samplerate": 48000},
    ]
    assert select_virtual_output(devices, platform="darwin").name == "BlackHole 2ch"
    assert select_virtual_output(devices, platform="win32").name.startswith("CABLE Input")
    with pytest.raises(VirtualMicrophoneError):
        select_virtual_output(devices[:1], platform="win32")


def test_linear_resampler_keeps_exact_ratio_and_chunk_state():
    pcm = array("h", [0, 300, 600]).tobytes()
    converted, previous = upsample_16k_to_48k(pcm)
    values = array("h")
    values.frombytes(converted)
    assert list(values) == [0, 0, 0, 100, 200, 300, 400, 500, 600]
    follow, previous = upsample_16k_to_48k(array("h", [900]).tobytes(), previous)
    values = array("h")
    values.frombytes(follow)
    assert list(values) == [700, 800, 900]
    assert previous == 900


class _Stream:
    def __init__(self):
        self.blocks = []
        self.started = False
        self.closed = False

    def start(self): self.started = True
    def write(self, block): self.blocks.append(bytes(block))
    def stop(self): self.started = False
    def close(self): self.closed = True


class _Backend:
    def __init__(self): self.stream = _Stream()
    def query_devices(self):
        return [{"name": "BlackHole 2ch", "max_output_channels": 2,
                 "hostapi": 0, "default_samplerate": 48000}]
    def RawOutputStream(self, **kwargs):
        assert kwargs["samplerate"] == 48000
        assert kwargs["channels"] == 1
        return self.stream


def test_bridge_streams_lead_audio_and_tail_silence():
    backend = _Backend()
    bridge = VirtualMicrophoneBridge(backend_factory=lambda: backend, platform="darwin")
    device = bridge.start(gain=2)
    bridge.write(array("h", [100, 200]).tobytes())
    bridge.finish()
    assert device.name == "BlackHole 2ch"
    assert backend.stream.closed
    assert sum(map(len, backend.stream.blocks)) >= 9600 * 2 + 2 * 3 * 2
