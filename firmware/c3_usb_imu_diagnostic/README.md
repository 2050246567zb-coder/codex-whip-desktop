# ESP32-C3 USB-only MPU6050 isolation test

Temporary diagnostic, not product firmware. No Bluetooth/Wi-Fi stack is initialized;
no microphone or desktop motion algorithms are loaded. The normal app cannot connect.
SDA GPIO0, SCL GPIO1, MPU address 0x68. Serial 115200.

Commands: PROBE, INIT, SLOW (restart host at 100 kHz), FAST (400 kHz),
RELEASE (end Wire, configure both pins as inputs with weak pull-ups; never drive high).
After RELEASE, use SLOW/FAST or restart before further I2C commands.
SAMPLE fields: timestamp ms, ax/ay/az raw, gx/gy/gz raw (2048 LSB/g, 16.4 LSB/dps).
STATS includes successful samples, read errors, data-not-ready, and digital pin levels.
Digital levels are not voltage or continuity measurements.

Build: `scripts/compile-esp32c3.ps1 -SketchName c3_usb_imu_diagnostic -AsciiDataDrive Q:`
Serial capture: `scripts/read-c3-usb-diagnostic.ps1 -Command PROBE -Seconds 20 -LogPath output/capture.log`

## 2026-09-20 actual device results

ESP32-C3 COM10, base MAC E8:3D:C1:8D:D9:44. Normal application was not running.
Original 0.7.4 source and binaries preserved in
`output/firmware-backup-074-20260920-141524`.
Original application binary SHA256:
`FB2EEB6D996D39642FA07CCF344B74865D02FB82BA012CB71EE8E55B6AD82BB7`.

Both diagnostic builds compiled and flashed with esptool hash verification.
Final diagnostic: 323976 bytes program, 15220 bytes static RAM.

1. Default 400 kHz / no Bluetooth: Wire.begin succeeded, WHO_AM_I read failed;
   both 0x68 and 0x69 probes timed out (error 5). Zero motion samples.
2. End/restart host at 100 kHz: same probe timeouts and identity read failure.
3. Wire.end returned true; GPIO0/1 set INPUT_PULLUP: SDA=1, SCL=0 persisted
   throughout the 8-second capture. No active I2C transactions after RELEASE.

Logs: `output/c3-usb-baseline-400k.log`, `output/c3-usb-restart-100k.log`,
`output/c3-usb-released-pins.log`.

Conclusion: failure reproduces without BLE, microphone, gesture recognition or desktop
code. Low SCL persists after releasing the host peripheral. This is not proof of a
specific bad solder joint or defective module. Wiring, sensor side, host pin and power
state still need isolation/measurement. It also does not rule out a historical transient
or prove other defects in production firmware are harmless.

At this point in the isolation test the device remained on USB diagnostic firmware, pins released.
Do not interpret absent Bluetooth advertising as a new Bluetooth failure.
Next physical step requires power-off before disconnecting the SCL wire, then repeat
RELEASE to distinguish the host pin from the connected wire/module. No live soldering.

Restore (when requested / diagnostic no longer needed):
`.tools/arduino-cli-1.5.1/arduino-cli.exe upload --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc,PartitionScheme=huge_app --port COM10 --input-dir output/firmware-backup-074-20260920-141524/firmware-esp32c3 firmware/codex_whip_esp32c3`

## Follow-up: user disconnected green SCL lead

User confirmed disconnecting the lead as requested (host GPIO1 side).
`RELEASE` returned `wire_end=1,SDA=1,SCL=1`, remaining high throughout the
8-second capture. Log: `output/c3-usb-scl-disconnected.log`.
Buffered pre-command lines contained earlier low values and are not evidence of
a new intermittent failure. Compare only confirmed post-command readings.
Host GPIO1 can read high with its weak pull-up when isolated. This substantially
narrows the persistent low to the connected wire/module side or a contact at that
joint, but does not prove the host pin passes all electrical or I2C tests, nor
prove the sensor IC itself is defective. Next separate the wire and module with
power removed and use continuity/resistance measurements if available.

## Product firmware restored (latest device state)

User reported the green lead had a bad solder joint and requested restoration.
After USB recovery was verified (1984 samples in ~20 seconds at 400 kHz), restored
the exact backed-up 0.7.4 product binaries above to COM10; flash hash verification
passed. No detection thresholds, desktop settings or calibration profiles were edited.

Post-restore live BLE verification: CodexWhip E8:3D:C1:8D:D9:46 advertised,
connected at MTU 247, PONG 0.7.4; MPU WHO_AM_I=0x68 and bus error count=0 at
diagnostic read. RAW1 delivered 400 batches / 1600 frames across 15.99 seconds,
with no decode errors. Test ended with RAW0 and disconnected, releasing BLE for
the desktop application. Evidence: `output/c3-product-restored-074.json`.
This was a predominantly stationary stream test, not a new end-to-end physical
whip/voice/UI acceptance test. The board is no longer on USB-only firmware.
