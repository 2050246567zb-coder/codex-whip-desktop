#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
cli_dir="$repo/.tools/arduino-cli-macos"
config="$repo/.tools/arduino-macos.yaml"
version=1.5.1
[[ $(uname -s) == Darwin ]] || { echo 'This setup requires macOS.' >&2; exit 1; }
case $(uname -m) in
  arm64) architecture=ARM64 ;;
  x86_64) architecture=64bit ;;
  *) echo 'Unsupported Mac architecture.' >&2; exit 1 ;;
esac
mkdir -p "$cli_dir"
if [[ ! -x "$cli_dir/arduino-cli" ]]; then
  archive="arduino-cli_${version}_macOS_${architecture}.tar.gz"
  base="https://github.com/arduino/arduino-cli/releases/download/v${version}"
  curl -fL "$base/$archive" -o "$cli_dir/$archive"
  curl -fL "$base/${version}-checksums.txt" -o "$cli_dir/checksums.txt"
  python3 - "$cli_dir/$archive" "$cli_dir/checksums.txt" <<'PY'
import hashlib, sys
from pathlib import Path
archive, checksums = map(Path, sys.argv[1:])
expected = next(line.split()[0] for line in checksums.read_text().splitlines()
                if line.split()[-1] == archive.name)
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('Arduino CLI checksum mismatch')
PY
  tar -xzf "$cli_dir/$archive" -C "$cli_dir" arduino-cli
fi
if [[ ! -f "$config" ]]; then
  python3 - "$repo" "$config" <<'PY'
import json, sys
from pathlib import Path
root, config = map(Path, sys.argv[1:])
config.write_text(json.dumps({
    'board_manager': {'additional_urls': ['https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json']},
    'directories': {'data': str(root / '.arduino-data'),
                    'downloads': str(root / '.arduino-data/staging'),
                    'user': str(root / '.arduino-user')},
}))
PY
fi
"$cli_dir/arduino-cli" core update-index --config-file "$config"
"$cli_dir/arduino-cli" core install 'Seeeduino:nrf52@1.1.13' --config-file "$config"
"$cli_dir/arduino-cli" lib install 'Seeed Arduino LSM6DS3@2.0.5' --config-file "$config"
