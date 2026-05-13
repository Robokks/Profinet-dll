#!/usr/bin/env bash
# Build the VFD Slave Simulator (32-bit Windows EXE) using MinGW-w64.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUILD_DIR="$ROOT/build-simulator"
DIST_DIR="$ROOT/dist"

TOOLCHAIN="$ROOT/cmake/toolchain-mingw32.cmake"

mkdir -p "$BUILD_DIR" "$DIST_DIR"

cmake -S "$ROOT/simulator" -B "$BUILD_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=Release \
    -DNPCAP_SDK_DIR="$ROOT/third_party/npcap-sdk"

cmake --build "$BUILD_DIR" --parallel

cp "$BUILD_DIR/vfd_simulator.exe" "$DIST_DIR/"
echo "Built: $DIST_DIR/vfd_simulator.exe"
