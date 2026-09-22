#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_PYTHON="$ROOT_DIR/macos/.venv-macos/bin/python"
if [[ -x "$DEFAULT_PYTHON" ]]; then
  PYTHON_BIN="${CODEX_WHIP_PYTHON:-$DEFAULT_PYTHON}"
else
  PYTHON_BIN="${CODEX_WHIP_PYTHON:-python3}"
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This verification must run on macOS."
  exit 2
fi

"$PYTHON_BIN" -m pytest -q "$ROOT_DIR/desktop/tests" --ignore="$ROOT_DIR/desktop/tests/test_windows_scoring.py"
PYTHONPATH="$ROOT_DIR/desktop/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -m codex_whip.cli doctor --strict

APP="$ROOT_DIR/macos/dist/CodexWhip.app"
if [[ -d "$APP" ]]; then
  codesign --verify --deep --strict --verbose=2 "$APP"
  echo "APP_SIGNATURE=PASS"
else
  echo "APP_SIGNATURE=SKIPPED (build the app first)"
fi
