#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
cli="$repo/.tools/arduino-cli-macos/arduino-cli"
config="$repo/.tools/arduino-macos.yaml"
build="$repo/build/firmware-macos"
[[ -f "$build/codex_whip.ino.zip" ]] || { echo 'Compile firmware first.' >&2; exit 1; }
if [[ $# -gt 1 ]]; then echo 'Usage: upload-firmware-macos.sh [/dev/cu.usbmodem…]' >&2; exit 1; fi
port=${1:-}
if [[ -z "$port" ]]; then
  inventory=$("$cli" board list --config-file "$config" --format json)
  port=$(printf '%s' "$inventory" | python3 -c '
import json,sys
ports=json.load(sys.stdin).get("detected_ports", [])
matched=[p["port"]["address"] for p in ports
         if any(b.get("fqbn")=="Seeeduino:nrf52:xiaonRF52840Sense"
                for b in p.get("matching_boards", []))]
if len(matched)!=1:
    raise SystemExit("Connect exactly one XIAO nRF52840 Sense, or specify its verified USB port.")
print(matched[0])')
fi
[[ "$port" == /dev/cu.usbmodem* && -e "$port" ]] || { echo 'Expected a connected XIAO USB serial port.' >&2; exit 1; }
# Never auto-select Bluetooth serial or an unrelated board.
mkdir -p "$repo/.tools/firmware-python"
python_bin="$repo/macos/.venv-macos/bin/python"
[[ -x "$python_bin" ]] || python_bin=$(command -v python3)
ln -sf "$python_bin" "$repo/.tools/firmware-python/python"
export PATH="$repo/.tools/firmware-python:$PATH"
log=$(mktemp "$repo/.tools/firmware-upload.XXXXXX")
trap 'rm -f "$log"' EXIT
"$cli" upload --config-file "$config" --fqbn Seeeduino:nrf52:xiaonRF52840Sense \
  --port "$port" --input-dir "$build" "$repo/firmware/codex_whip" 2>&1 | tee "$log"
if ! grep -q 'Device programmed\.' "$log"; then
  echo 'Uploader did not confirm Device programmed.; do not assume flashing succeeded.' >&2
  exit 1
fi
