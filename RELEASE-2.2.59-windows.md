# Codex Whip Windows product 2.2.59 handoff record

Date: 2026-09-22

## Product boundary

- Windows development continues on `codex/product-windows`.
- macOS development continues on `codex/product-macos`.
- Platform-native window automation, overlays, audio, permissions and packaging
  may differ. `firmware/codex_whip` and `firmware/tests` must remain identical.

## Shared firmware 0.7.4

- One firmware image supports Windows and macOS.
- After BLE connection, the desktop detects `sys.platform` and sends a host
  profile only when firmware advertises `CAPS,HOST_PROFILE,1`.
- The firmware selects the appropriate BLE connection interval and motion batch
  size, then exposes `HOST` and `LINK` diagnostics. Older firmware remains
  compatible because it never receives the new command.
- Both platform workflows compare the shared firmware trees before packaging.

## Validation evidence

- Full desktop suite: `python -m pytest desktop/tests -q` passed locally.
- XIAO nRF52840 Sense firmware 0.7.4 compiled locally: 208140 bytes flash
  (25%) and 42468 bytes RAM (17%).
- Windows packaging and synthetic UI smoke are performed by the Windows GitHub
  Actions workflow.

Cloud CI and synthetic tests do not replace physical-controller acceptance.
