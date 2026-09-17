# macOS UI test build (2.2.22)

This is an Apple Silicon test build, not a notarized public release.
Private repository: https://github.com/2050246567zb-coder/codex-whip-desktop

## What the build does

- Compiles whisper.cpp and packages Python/Tk on a native macOS 15 arm64 runner.
- Ad-hoc signs and verifies the app, producing a DMG and a metadata-preserving ZIP.
- Excludes personal migration profiles, recordings, and learned calibration data.
- Runs UI, clock, morphing text, slider, settings and effect regression tests.
- Starts the packaged executable in isolated synthetic UI mode. This mode uses
  a temporary data directory and disables BLE, Codex discovery and sending.
- Exports screenshots and callback errors; screenshot capture failures are
  reported separately and must not be treated as successful visual validation.

## Try it on a Mac

Open the DMG and copy CodexWhip.app to Applications. This test build is not
notarized, so macOS may require explicit user approval in Privacy & Security.
Do not disable Gatekeeper globally.

To inspect the synthetic states without a physical handle, run:

```sh
/Applications/CodexWhip.app/Contents/MacOS/CodexWhip --ui-smoke --output "$HOME/Desktop/Whip-UI-test"
```

The synthetic run closes automatically. Launch normally for interactive use.

## Still requires hands-on acceptance

- Animation smoothness and interruption with a real pointer, Retina scaling,
  and multiple monitors (automated assertions are not frame-rate evidence).
- Real Codex overlay compositing, window following and Accessibility permissions.
- BLE connection, physical gestures, voice transfer and actual message delivery.
- First launch of the downloaded artifact on a separate Mac with quarantine.

Never interpret a successful build or synthetic UI pass as verification of these.
