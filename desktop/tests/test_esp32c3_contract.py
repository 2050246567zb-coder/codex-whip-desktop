"""Static port invariants plus real desktop parser tests; not hardware tests."""
from pathlib import Path
import base64
import re
import struct
import pytest
from codex_whip.protocol import LineDecoder, parse_line

ROOT = Path(__file__).resolve().parents[2]
PORT = ROOT / 'firmware/codex_whip_esp32c3'

def test_actual_firmware_version_enables_desktop_features():
    from codex_whip.gui import CodexWhipWindow
    source = (PORT/'codex_whip_esp32c3.ino').read_text()
    version = re.search(r'kFirmwareVersion\[\] = "([^"]+)"', source).group(1)
    for required in ((0,3,1), (0,4,0), (0,5,0), (0,6,0)):
        assert CodexWhipWindow._version_at_least(version, required)

def test_two_sample_low_latency_packet_matches_desktop_parser():
    source = (PORT/'codex_whip_esp32c3.ino').read_text()
    count = int(re.search(r'kRawBatchSamples = (\d+)', source).group(1))
    assert count == 2
    packed = b''.join(struct.pack('<Hhhhhhh', i*10,10,-20,30,0,0,1000) for i in range(count))
    batch = parse_line('RAW5,1,1000,'+base64.b64encode(packed).decode())
    assert len(batch.frames) == 2
    assert batch.frames[-1].timestamp_ms == 1010

@pytest.mark.parametrize('name', ['whip_detector.h', 'voice_audio.h'])
def test_shared_algorithms_are_unchanged(name):
    assert (PORT/name).read_text() == (ROOT/'firmware/codex_whip'/name).read_text()

def test_all_existing_command_branches_retained():
    old = (ROOT/'firmware/codex_whip/codex_whip.ino').read_text()
    new = (PORT/'codex_whip_esp32c3.ino').read_text()
    commands = set(re.findall(r'command == "([^"]+)"', old))
    # XIAO-only opt-in low power, gated explicitly by the desktop capability.
    xiao_only = {'POWERGET', 'POWERTEST', 'POWERHOLD', 'POWER,0', 'POWER,1'}
    assert commands - xiao_only <= set(re.findall(r'command == "([^"]+)"', new))
    for prefix in ('VOICE,START,', 'CFG,'):
        assert f'command.startsWith("{prefix}")' in new

def test_pins_and_transport_contract():
    hw = (PORT/'c3_hardware.h').read_text()
    assert 'kSda = 0, kScl = 1, kBclk = 10, kWs = 20, kMicData = 6' in hw
    assert 'cfg.gpio_cfg.bclk=gpio_num_t(kBclk)' in hw
    assert 'cfg.gpio_cfg.ws=gpio_num_t(kWs)' in hw
    from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
    assert NUS_RX_CHARACTERISTIC in hw and NUS_TX_CHARACTERISTIC in hw
    assert 'chan.dma_frame_num=511' in hw
    assert 511*8 <= 4092
    assert 'Serial.setTxTimeoutMs(0)' in (PORT/'codex_whip_esp32c3.ino').read_text()

@pytest.mark.parametrize('mtu_payload', [20, 97, 244])
def test_c3_raw_and_audio_fragmentation(mtu_payload):
    packed=b''.join(struct.pack('<Hhhhhhh',t,10,-20,30,0,0,1000) for t in (0,10,20,30))
    lines=[
        'PONG,0.7.0-c3',
        'RAW5,1,1000,'+base64.b64encode(packed).decode(),
        'WHIP2,1,1100.0,2.00,150,70.0,0.800,0.900,10,50.0',
        'VOICE,START,1,16000,IMA_ADPCM4',
        'AUD1,1,0,300,0,0,'+base64.b64encode(bytes(150)).decode(),
        'VOICE,END,1,300,SILENCE',
    ]
    data=('\n'.join(lines)+'\n').encode()
    decoder=LineDecoder()
    actual=[]
    for i in range(0,len(data),mtu_payload):
        actual.extend(decoder.feed(data[i:i+mtu_payload]))
    assert actual == [parse_line(line) for line in lines]
