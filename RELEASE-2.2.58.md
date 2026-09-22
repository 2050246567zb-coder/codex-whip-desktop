# Codex Whip product 2.2.58 handoff record

Date: 2026-09-22

## Product changes

- A sanitized copy of the current Windows calibration is now the cross-platform
  factory profile. On a fresh install it seeds mounting direction, whip
  thresholds and sensitivity, double-tap templates, and the double-tap impact
  threshold. Existing user files are preserved byte-for-byte.
- Factory data is checksum-verified before import. It excludes API keys,
  recordings, prompt messages, debug captures, Bluetooth addresses, and the
  sensor bias measured for one physical board.

- Settings is one continuous page with no category sidebar. Connection and
  power saving come first; input, recognition, feedback and calibration follow
  in task order.
- Direction/recenter and the old calibration buttons/descriptions are removed
  from normal settings. Whip, double-tap, voice sensitivity and wound frequency
  use direct percentage sliders.
- Message order is a single icon toggle. Message cards use a centered 70% text
  width. Voice input mode choices and native dictation are retired from the
  product flow.
- Voice recognition prefers the configured API, uses local recognition when no
  API is configured, and can fall back to an already-prepared local model after
  a cloud error. Advanced provider and raw parameter controls are available only
  through the in-process developer entry point.
- First use now demonstrates the whip, strike, recording, recognizing, sleep
  and clock states with Chinese captions. Direction calibration then continues
  in the main window instead of opening another window.

## Developer entry point

Normal users do not see advanced parameters. Development automation may call
`CodexWhipWindow.open_developer_settings()` or generate the Tk virtual event
`<<OpenDeveloperSettings>>` on the main window.

## Validation evidence

- Windows unit/UI suite: `python -m pytest desktop/tests -q`.
- Synthetic packaged-style UI smoke: `python -m codex_whip.gui --ui-smoke`.
- Windows executable and macOS Apple Silicon artifacts are produced by their
  branch-specific GitHub Actions workflows.

Cloud CI and synthetic UI evidence do not replace target-Mac and physical-XIAO
acceptance. Firmware remains `0.7.3`; this release changes desktop behavior only.

## Public factory calibration and excluded data

The repository contains only the five files under
`desktop/assets/factory-calibration` listed by its checksum manifest. API keys,
local recordings, messages, downloaded speech models, debug captures, build
output, and device-specific sensor bias remain excluded.
