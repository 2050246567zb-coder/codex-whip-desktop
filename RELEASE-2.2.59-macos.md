# Codex Whip 2.2.59 — macOS

Date: 2026-09-22. Mac source branch: `codex/product-macos`.

## Changes

- Native transparent AppKit overlays fix black backgrounds and drawing trails; nonactivating input panels support dragging, clicks, right-click clock interaction and Control-click.
- Overlay status animations reuse the home-page animation implementation. The offscreen Tk scene now retains its Tk parent, and native rendering supports clock numerals as well as vector geometry and transparent images.
- Faster frame scheduling and bounded display-only pose continuation improve motion response without changing sensor recognition input.
- Mac notification sounds initialize AppKit on the UI thread, avoiding a Tk crash observed during packaged recording-state tests. Ad-hoc packages disable hardened library validation, while Developer ID builds retain hardened runtime.
- Native Whisper 1.8.1 build helper with embedded Metal shaders; verified local speech model setup and clearer model preparation errors. Voice diagnostics log lifecycle events without recording recognized text.
- Firmware 0.7.4 negotiates a host profile with the desktop app, selects connection parameters and batches appropriate to the negotiated BLE interval, and moves transmission off the IMU sampling loop. Platforms identify themselves through an explicit application handshake; unknown hosts use a compatible default.
- Windows and macOS now evolve on separate product branches. The firmware source and firmware tests remain one shared contract; both platform workflows reject packaging when their firmware trees differ.
- Mac firmware setup, compilation and USB upload scripts are included. Existing local calibration is preserved.

## Build and distribution

Run `bash macos/build-macos.sh` on Apple Silicon with Python 3.12, Tk, CMake and Xcode Command Line Tools. The macOS GitHub Actions workflow also builds the app and uploads DMG/ZIP artifacts. Without a Developer ID certificate these are ad-hoc signed development builds, not notarized releases.

For source development, run `bash macos/prepare-speech.sh` first. The app downloads and verifies the speech model when local recognition is enabled. Do not commit local recordings, API credentials, device profiles or downloaded models.

## Validation

Native overlay integration covers right-click routing, transparent frame clearing, clock text, recording, recognizing, connecting and sleeping animations. The desktop regression suite is run on macOS excluding Windows-specific ABI tests. Local Whisper has transcribed the upstream sample successfully. Firmware compilation and physical-board upload were verified earlier in this development session; motion latency was subsequently accepted by the user. Physical double-tap-to-speech recognition still requires user acceptance.
