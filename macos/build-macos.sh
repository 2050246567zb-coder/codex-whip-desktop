#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="$ROOT_DIR/desktop"
MAC_DIR="$ROOT_DIR/macos"
VENV_DIR="$MAC_DIR/.venv-macos"
WHISPER_VERSION="1.8.1"
WHISPER_ROOT="$MAC_DIR/.build/whisper.cpp-$WHISPER_VERSION"
WHISPER_ASSET="$DESKTOP_DIR/assets/stt/whispercpp-$WHISPER_VERSION/whisper-cli"
OUTPUT_DIR="$MAC_DIR/dist"

safe_remove_tree() {
  local target="$1"
  local allowed_parent="$2"
  "$PYTHON_BIN" - "$target" "$allowed_parent" <<'PY'
from pathlib import Path
import shutil
import sys

target = Path(sys.argv[1]).resolve()
parent = Path(sys.argv[2]).resolve()
if target == parent or parent not in target.parents:
    raise SystemExit(f"Refusing unsafe recursive removal: {target}")
if target.exists():
    shutil.rmtree(target)
PY
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build must run on macOS."
  exit 2
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "This build targets Apple Silicon (arm64)."
  exit 2
fi

PYTHON_BIN="${CODEX_WHIP_PYTHON:-python3}"
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
import tkinter
print("Python/Tk:", sys.version.split()[0], tkinter.TkVersion)
PY

mkdir -p "$MAC_DIR/.build" "$OUTPUT_DIR" "$(dirname "$WHISPER_ASSET")"

if [[ -n "${CODEX_WHIP_DOUBAO_API_KEY:-}" ]]; then
  mkdir -p "$DESKTOP_DIR/assets/private"
  printf '%s' "$CODEX_WHIP_DOUBAO_API_KEY" > "$DESKTOP_DIR/assets/private/doubao-api-key.txt"
fi

if [[ ! -x "$WHISPER_ASSET" ]]; then
  if ! command -v cmake >/dev/null 2>&1; then
    echo "cmake is required. Install it with Homebrew or from cmake.org."
    exit 3
  fi
  if [[ ! -d "$WHISPER_ROOT/.git" ]]; then
    safe_remove_tree "$WHISPER_ROOT" "$MAC_DIR/.build"
    git clone --depth 1 --branch "v$WHISPER_VERSION" \
      https://github.com/ggml-org/whisper.cpp.git "$WHISPER_ROOT"
  fi
  cmake -S "$WHISPER_ROOT" -B "$WHISPER_ROOT/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_METAL=ON \
    -DWHISPER_COREML=OFF \
    -DBUILD_SHARED_LIBS=OFF
  cmake --build "$WHISPER_ROOT/build" --config Release \
    -j "$(sysctl -n hw.logicalcpu)"
  cp "$WHISPER_ROOT/build/bin/whisper-cli" "$WHISPER_ASSET"
  chmod +x "$WHISPER_ASSET"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip wheel
"$VENV_DIR/bin/python" -m pip install -e "$DESKTOP_DIR[build]"
VERSION="$("$VENV_DIR/bin/python" -c 'from importlib.metadata import version; print(version("codex-whip"))')"

safe_remove_tree "$DESKTOP_DIR/build/CodexWhip" "$DESKTOP_DIR/build"
safe_remove_tree "$DESKTOP_DIR/dist/CodexWhip.app" "$DESKTOP_DIR/dist"
cd "$DESKTOP_DIR"
"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --windowed \
  --name CodexWhip \
  --osx-bundle-identifier com.codexwhip.desktop \
  --add-data "assets:assets" \
  --collect-submodules AppKit \
  --collect-submodules Quartz \
  --collect-submodules Foundation \
  --collect-all sounddevice \
  codex_whip_gui.py

PLIST="$DESKTOP_DIR/dist/CodexWhip.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  "Add :NSBluetoothAlwaysUsageDescription string Codex Whip uses Bluetooth to receive motion and voice data from your physical whip." \
  "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || true

IDENTITY="${CODEX_WHIP_CODESIGN_IDENTITY:--}"
codesign --force --deep --options runtime --sign "$IDENTITY" \
  "$DESKTOP_DIR/dist/CodexWhip.app"
codesign --verify --deep --strict --verbose=2 "$DESKTOP_DIR/dist/CodexWhip.app"

safe_remove_tree "$OUTPUT_DIR/CodexWhip.app" "$OUTPUT_DIR"
cp -R "$DESKTOP_DIR/dist/CodexWhip.app" "$OUTPUT_DIR/CodexWhip.app"

DMG="$OUTPUT_DIR/CodexWhip-$VERSION-Apple-Silicon.dmg"
rm -f "$DMG"
hdiutil create -volname "CodexWhip" -srcfolder "$OUTPUT_DIR/CodexWhip.app" \
  -ov -format UDZO "$DMG"

echo "Built: $OUTPUT_DIR/CodexWhip.app"
echo "Built: $DMG"
ditto -c -k --sequesterRsrc --keepParent "$OUTPUT_DIR/CodexWhip.app" "$OUTPUT_DIR/CodexWhip-$VERSION-Apple-Silicon.zip"
echo "Sanitized factory calibration is bundled; personal secrets and device-specific bias are excluded."
