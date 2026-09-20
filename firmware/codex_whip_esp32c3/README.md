# ESP32-C3 firmware 0.7.7

0.7.7 uses the selected free same-edge pins: microphone SCK/BCLK on GPIO10,
WS on GPIO20, SD unchanged on GPIO6. This replaces the unflashed 0.7.6
GPIO7/8 proposal and the original GPIO4/5 wiring. GPIO7 (board LED) and
GPIO8/9 (boot straps) are left unused. GPIO20 must not simultaneously be
used as UART RX; this build uses USB CDC for diagnostics.
No desktop update is required (产品版 2.2.34 compatible). Disconnect all power
before moving wires. This revision is not yet flashed or microphone-tested.
Verify startup and recording after wiring; compilation is not hardware acceptance.

0.7.5 adds bounded BLE link tuning and two-sample/20ms RAW batches. This
revision preserves those latency improvements and all gesture algorithms.

0.7.2 makes USB log output non-blocking so an unopened/stalled serial monitor
cannot delay BLE commands and IMU data.

0.7.1 fixes desktop 2.2.32 compatibility: PONG/STATUS now use a numeric
version. The earlier `0.7.0-c3` suffix made the desktop disable RAW/voice and
parameter synchronization even though a manually requested RAW stream worked.

Separate port for Codex Whip desktop 2.2.32. Original XIAO sketch is untouched.
This is a compile/protocol-validated candidate, not hardware acceptance.

## Wiring (3.3V logic only)

| Module | Pin | C3 GPIO / supply |
|---|---|---|
| MPU6050 | SDA / SCL | 0 / 1 |
| MPU6050 | VCC / GND / AD0 | 3.3V / GND / GND |
| ICS-43434 | SCK / WS / SD | 10 / 20 / 6 |
| ICS-43434 | VDD / GND / L/R | 3.3V / GND / GND |

USB-C power initially. No battery/charger assumptions. MPU INT/XDA/XCL unused.

## Build

Arduino ESP32 core **3.3.11**, NimBLE-Arduino **2.5.0**.
For a standalone ZIP extraction, install Arduino CLI on PATH first, then
install the listed ESP32 core and NimBLE library through Arduino tools.
Select ESP32C3 Dev Module, 4MB flash, USB CDC On Boot enabled,
Huge APP partition (no OTA). Run `scripts/compile-esp32c3.ps1` from project root.
Outputs are under `build/firmware-esp32c3/`.
The Arduino sketch is `codex_whip_esp32c3.ino`; upload only to ESP32-C3,
never XIAO nRF52840. No flashing is performed by the build script.

## Compatibility

- Same advertised name `CodexWhip`, Nordic UART service/RX/TX UUIDs.
- Same PING/STATUS/ARM/CAL, CFG acknowledgement and query, LEARN and RAW commands.
- Same WHIP2/REJECT2/SAMPLE3 events and detector header as XIAO.
- MPU6050: ±2000 dps, ±16g, 500Hz sampling with hardware DLPF;
  timestamped RAW5 at ~100Hz in two-sample batches. Units remain dps and g.
- Desktop keeps direction calibration, 3-second auto-centering, double-tap
  recognition, gesture learning, sensitivity, animation and message sending.
- ICS-43434: 16kHz, standard I2S, 32-bit stereo slots, left slot captured;
  DC removal and fixed 4x digital gain, saturating PCM16, IMA ADPCM AUD1.
- Same VOICE start/end/session/sequence protocol; silence/max-time/no-speech,
  cancel, overflow and disconnect handling. No speech recognition on the MCU.
- Recording temporarily pauses motion streaming, as on the existing XIAO.
- USB 115200 accepts the same line commands, but audio/RAW require BLE.
- Wi-Fi and battery charging are not implemented. No OTA updater.

## Before using with real sending enabled

1. Close old desktop/serial monitors; keep old XIAO powered off (same BLE name).
2. Flash after verifying wiring. Confirm `PONG,0.7.7` via PING on serial/BLE.
   `FATAL,IMU_NOT_FOUND` means check power, SDA/SCL, AD0 and actual chip identity.
3. `PING`, `STATUS`, `CFG,GET`, `SELFTEST`: SELFTEST should finish with 12/12.
   Do not run `TEST` while real sending is enabled (it emits a WHIP event).
4. Desktop reconnect must acknowledge all thresholds and enable RAW/VOICE.
5. Re-do mounting/gesture/tap calibration for this new sensor and installation;
   old mechanical calibration is not a guarantee for a different assembly.
6. Verify stationary stability, direction, light/strong taps, slow rotation vs
   whipping, and 3-second recenter. Do not blindly relax thresholds to hide loss.
7. Test speech/silence/cancel/re-record and 15-second capture without missing
   chunks, TX_FAILED, clipping or BUFFER_OVERFLOW. Adjust gain only from samples.
8. Test reconnect and Windows/macOS separately. MTU defaults to 23 and fragments
   records; larger negotiated MTU greatly improves sustained audio throughput.

No battery protection/charging, true position tracking or magnetometer heading
is implied. The IMU follows orientation, not absolute XYZ location.
