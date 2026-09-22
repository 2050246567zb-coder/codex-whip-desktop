# Codex Whip product 2.2.56 handoff record

Date: 2026-09-22

## Source revisions

- Windows functional source: `e132b6b85816098c0fa71f83d269bd8d4c73a8b2`
  on `codex/product-windows`
- macOS functional source: `8ac4ab9141add667b07a9634c2ad2bfe70327f02`
  on `codex/product-macos`
- Desktop package version: `2.2.56`
- XIAO nRF52840 Sense firmware version: `0.7.3`

At handoff, the product branches contain the same application and firmware
source. Their expected differences are platform documentation and their branch-
specific packaging workflow.

## User-visible changes in this version

- Normal battery capsule is black to match the whip; charging remains green and
  low battery remains red.
- The main-window whip uses 4x off-screen rendering followed by Lanczos downscale
  for smoother edges while the transparent target overlay retains its native
  color-key-safe path.
- Double-tap recognition on the original XIAO uses the LSM6DS3TR-C hardware tap
  engine documented by ST. The desktop no longer runs the custom raw-motion
  double-tap recognizer as a fallback.
- Double-tap settings expose only minimum impact. The two impacts may occur at
  any separation inside the fixed one-second hardware window.

## Verification evidence

### Windows

- GitHub Actions run:
  <https://github.com/2050246567zb-coder/codex-whip-desktop/actions/runs/35593648937>
- Result: tests passed, product executable built, artifact uploaded.
- Local executable: `产品版-2.2.56.exe`
- Local SHA-256:
  `355C646D7FB7D96FDC05ABA25B021DACD54CDC44B7B5D84C4617813933784CC5`

### macOS Apple Silicon

- GitHub Actions run:
  <https://github.com/2050246567zb-coder/codex-whip-desktop/actions/runs/35593701767>
- Result: package built on `macos-15`, packaged UI synthetic smoke passed, and
  the selected UI/animation/platform regression suites passed.
- Output: ad-hoc signed `.app`, `.dmg` and `.zip` test artifacts.

### Physical XIAO firmware

Firmware `0.7.3` was compiled and uploaded through the XIAO bootloader on
2026-09-22. Post-upload BLE readback returned:

```text
PONG,0.7.3
TAPENGINE,1,ST_AN5130,1.00,1000
POWER,1,ACTIVE,300
```

This verifies the running firmware version, hardware tap engine, one-second tap
window and active power configuration on that controller.

## Validation boundary

Cloud macOS success is package and synthetic-UI evidence only. Before releasing
to other Mac users, verify on the target Apple Silicon Mac with the real XIAO:

- BLE discovery, reconnect and sustained raw motion;
- Accessibility discovery of the currently installed Codex/Claude desktop UI;
- overlay position, frame pacing, full-screen behavior and mouse interaction;
- BlackHole routing and Codex native dictation start/stop;
- real microphone audio, speech services and API credentials;
- physical whip, double-tap and sleep/wake behavior.

Use `macos/MACOS_ACCEPTANCE.md` as the sign-off checklist. Do not treat a CI
artifact as a notarized public release: the current package is ad-hoc signed
unless a Developer ID identity and notarization process are supplied.

## Data and secrets excluded from Git

The repository intentionally excludes local API keys, `.env` files, recordings,
calibration profiles, saved messages, speech models downloaded at runtime,
migration data, build output and user-specific application state.
