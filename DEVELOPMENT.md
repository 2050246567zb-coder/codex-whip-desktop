# Codex Whip 2.2.57 development guide

This repository contains the desktop companion and firmware for the physical
Codex Whip controller. Product branches are deliberately separated by target
platform:

| Branch | Purpose |
| --- | --- |
| `codex/product-windows` | Windows product source, Windows packaging and XIAO firmware development |
| `codex/product-macos` | Apple Silicon macOS product source and macOS packaging |

The application logic and configuration schemas are shared. Platform-specific
window automation, overlays, virtual audio devices, permissions and packaging
are implemented separately so each platform can use its native APIs.

## Start development on a Mac

Clone the private repository and check out the macOS product branch:

```bash
git clone https://github.com/2050246567zb-coder/codex-whip-desktop.git
cd codex-whip-desktop
git switch codex/product-macos
```

Install the Apple Silicon build dependencies:

```bash
brew install python@3.12 python-tk@3.12 cmake
export CODEX_WHIP_PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
bash macos/build-macos.sh
```

The build script creates `macos/.venv-macos`, compiles the pinned
`whisper.cpp` 1.8.1 runtime when needed, and produces:

- `macos/dist/CodexWhip.app`
- `macos/dist/CodexWhip-2.2.57-Apple-Silicon.dmg`
- `macos/dist/CodexWhip-2.2.57-Apple-Silicon.zip`

For an editable development environment with the test dependencies:

```bash
macos/.venv-macos/bin/python -m pip install -e 'desktop[dev,build]'
macos/.venv-macos/bin/python -m pytest desktop/tests -q
macos/.venv-macos/bin/codex-whip-gui
```

Run the packaged synthetic UI smoke test without BLE or message sending:

```bash
macos/dist/CodexWhip.app/Contents/MacOS/CodexWhip \
  --ui-smoke --output "$PWD/macos/test-results/ui"
```

`bash macos/verify-macos.sh` additionally runs strict Codex discovery. Use it
only after opening the Codex desktop app on an empty conversation and granting
the required permissions.

## macOS permissions and local setup

On the target Mac, grant CodexWhip:

- Bluetooth permission, for the physical `CodexWhip` BLE peripheral;
- Accessibility permission, for finding the target window, controlling Codex
  dictation and submitting an already confirmed draft;
- any audio permission requested by the selected voice-input path.

Native dictation and virtual-audio routing are retired from the product flow in
2.2.57. Voice input uses a configured speech API first and the local recognizer
when cloud recognition is not configured or an already-prepared local fallback
is available.

Personal data is not stored in Git. Normal macOS state lives in:

```text
~/Library/Application Support/CodexWhip/
```

Use `CODEX_WHIP_DATA_DIR` to isolate test data. Never commit that directory,
API keys, recordings, calibration profiles, message profiles or migration data.

## Architecture map

```text
firmware/codex_whip/             XIAO nRF52840 Sense product firmware
desktop/src/codex_whip/
  gui.py                         application lifecycle and BLE event routing
  ble_client.py, protocol.py     Nordic UART BLE transport and line protocol
  sensor_pose.py                 fused relative orientation and screen mapping
  motion_v3.py                   learned whip trajectory matching
  effects.py, whip_drawing.py    overlay, rope physics, wounds and rendering
  interface.py                   main product window
  settings_window.py             settings navigation and controls
  voice.py, cloud_speech.py      recording, filtering and speech services
  virtual_microphone.py          platform virtual-audio routing
  macos_api.py                   macOS Accessibility/AppKit/Quartz adapter
macos/build-macos.sh             Apple Silicon package build
macos/MACOS_ACCEPTANCE.md        real-Mac and real-controller acceptance list
.github/workflows/macos.yml      cloud build and synthetic UI regression
```

The ESP32-C3 + MPU6050 files remain an experimental hardware port. Product
2.2.57 double-tap behavior is defined only for the original XIAO nRF52840 Sense
and its onboard LSM6DS3TR-C.

## Firmware contract used by 2.2.57

- Product firmware: `0.7.3`
- BLE local name: `CodexWhip`
- Nordic UART service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- IMU: onboard LSM6DS3TR-C, ±16 g, ±2000 dps, 416 Hz
- Double-tap engine: ST AN5130 Shock/Quiet/Duration hardware state machine
- Double-tap window: fixed at 1000 ms
- Desktop-exposed tap setting: minimum impact only; hardware threshold is
  rounded upward in 0.5 g steps

After changing protocol behavior, update firmware and desktop tests together.
At minimum, keep `desktop/tests/test_xiao_tap_contract.py` and the firmware
version gate passing.

On Windows, compile and upload the product firmware with:

```powershell
.\scripts\setup-arduino-cli.ps1
.\scripts\compile-firmware.ps1
.\scripts\upload-firmware.ps1 -Port COM8
```

Replace `COM8` with the actual XIAO serial port. Close every desktop listener
and serial monitor before uploading or running direct BLE diagnostics.

## Cross-platform change rules

1. Put shared behavior in `desktop/src/codex_whip` and isolate OS calls behind
   the existing platform adapters.
2. Keep configuration schemas readable by both product branches. Add migrations
   before changing a persisted schema.
3. Never silently replace a missing platform capability. Display an unavailable
   state or reject the operation instead of using an unsafe fallback.
4. Run unit tests and synthetic UI smoke on both platforms. A cloud runner does
   not prove Bluetooth, Accessibility, window targeting, audio routing or actual
   controller latency.
5. Complete `macos/MACOS_ACCEPTANCE.md` on the target Mac before calling a macOS
   build ready for users.

## Recommended Git workflow

Develop macOS-specific work on `codex/product-macos`. Keep commits small enough
to cherry-pick shared changes into `codex/product-windows`, and keep platform
adapters in separate commits when possible. Before transferring a change:

```bash
git status --short
git fetch origin
git rebase origin/codex/product-macos
```

Do not commit build output from `dist/`, `build/`, `macos/dist/`, local virtual
environments or user data. GitHub Actions builds downloadable test artifacts
from each product branch.

See `RELEASE-2.2.57.md` for the exact source revisions and validation evidence
for this handoff.
