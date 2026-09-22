#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
cli="$repo/.tools/arduino-cli-macos/arduino-cli"
config="$repo/.tools/arduino-macos.yaml"
[[ -x "$cli" && -f "$config" ]] || { echo 'Run scripts/setup-firmware-macos.sh first.' >&2; exit 1; }
# Seeed's build recipes invoke `python`, which stock macOS no longer provides.
mkdir -p "$repo/.tools/firmware-python"
python_bin="$repo/macos/.venv-macos/bin/python"
[[ -x "$python_bin" ]] || python_bin=$(command -v python3)
ln -sf "$python_bin" "$repo/.tools/firmware-python/python"
export PATH="$repo/.tools/firmware-python:$PATH"
"$cli" compile --config-file "$config" --fqbn Seeeduino:nrf52:xiaonRF52840Sense \
  --output-dir "$repo/build/firmware-macos" "$repo/firmware/codex_whip"
