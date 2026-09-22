#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
version=1.8.1
source_dir="$repo/macos/.build/whisper.cpp-$version"
asset="$repo/desktop/assets/stt/whispercpp-$version/whisper-cli"
[[ $(uname -s) == Darwin ]] || { echo 'Run this script on macOS.' >&2; exit 1; }
cmake_bin=${CODEX_WHIP_CMAKE:-}
if [[ -z "$cmake_bin" ]]; then
  if [[ -x "$repo/macos/.venv-macos/bin/cmake" ]]; then
    cmake_bin="$repo/macos/.venv-macos/bin/cmake"
  else cmake_bin=$(command -v cmake || true); fi
fi
[[ -n "$cmake_bin" ]] || { echo 'Install CMake first (e.g. python -m pip install cmake in the project venv).' >&2; exit 1; }
mkdir -p "$(dirname "$source_dir")" "$(dirname "$asset")"
if [[ ! -d "$source_dir/.git" ]]; then
  git clone --depth 1 --branch "v$version" https://github.com/ggml-org/whisper.cpp.git "$source_dir"
fi
"$cmake_bin" -S "$source_dir" -B "$source_dir/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON \
  -DWHISPER_COREML=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF
"$cmake_bin" --build "$source_dir/build" --config Release --target whisper-cli -j "$(sysctl -n hw.logicalcpu)"
cp "$source_dir/build/bin/whisper-cli" "$asset"
chmod +x "$asset"
"$asset" --help >/dev/null 2>&1
echo "Native speech runtime ready: $asset"
