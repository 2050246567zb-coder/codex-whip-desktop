# Codex Whip platform development guide

This repository contains the desktop companion and firmware for the physical
Codex Whip controller. Product branches are deliberately separated by target
platform:

| Branch | Purpose |
| --- | --- |
| `codex/product-windows` | Windows product source, Windows UI/automation/audio and Windows packaging |
| `codex/product-macos` | Apple Silicon macOS product source, native integrations and macOS packaging |

The application logic and configuration schemas are shared. The product firmware
under `firmware/codex_whip` and its tests under `firmware/tests` must be byte-for-byte
identical on both product branches. Platform-specific
window automation, overlays, virtual audio devices, permissions and packaging
are implemented separately so each platform can use its native APIs.

Firmware does not guess the host from a BLE address or MTU. After connecting,
the desktop detects `sys.platform` and automatically negotiates `HOST,WINDOWS`,
`HOST,MACOS`, `HOST,LINUX`, or `HOST,COMPATIBLE`. Firmware 0.8.3 then selects
the matching connection interval and motion batch policy. This is invisible to
the user and preserves compatibility with older firmware that does not advertise
the `HOST_PROFILE` capability.

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
- `macos/dist/CodexWhip-2.2.68-Apple-Silicon.dmg`
- `macos/dist/CodexWhip-2.2.68-Apple-Silicon.zip`

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
2.2.64. Voice input uses a configured speech API first and the local recognizer
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
2.2.64 double-tap behavior is defined only for the original XIAO nRF52840 Sense
and its onboard LSM6DS3TR-C.

## Shared firmware contract

- Product firmware: `0.8.3`
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

Every firmware change must be a platform-neutral commit transferred to both
product branches before either branch is released. Both GitHub build workflows
run `scripts/verify-shared-firmware.py` against the opposite product branch and
refuse to package when the two firmware trees differ. Platform build/upload
scripts may differ; the firmware source and tests may not.

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

Develop OS-specific work only on its product branch. Put shared desktop changes
in separate commits that can be cherry-picked deliberately. Firmware commits
are always shared and must be transferred to both branches before packaging.
Before transferring a change:

```bash
git status --short
git fetch origin
git rebase origin/codex/product-macos
```

Do not commit build output from `dist/`, `build/`, `macos/dist/`, local virtual
environments or user data. GitHub Actions builds downloadable test artifacts
from each product branch.

See the platform-specific `RELEASE-2.2.59-*.md` records for source revisions,
validation evidence and the remaining physical-device acceptance boundary.
