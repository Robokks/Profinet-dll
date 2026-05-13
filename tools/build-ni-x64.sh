#!/bin/bash
# Build libprofinet.so for NI Linux RT x86-64 (PXI-8840, PXI-8821, cRIO-9049)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUILD_DIR="$ROOT/build-ni-x64"
DIST_DIR="$ROOT/dist/ni-linux-rt-x64"

echo "=== Configuring ==="
mkdir -p "$BUILD_DIR"
cmake -S "$ROOT" -B "$BUILD_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-ni-x64.cmake" \
    -DCMAKE_BUILD_TYPE=Release

echo "=== Building ==="
cmake --build "$BUILD_DIR" --parallel "$(nproc)"

echo "=== Installing to dist/ ==="
mkdir -p "$DIST_DIR"
cp "$BUILD_DIR/libprofinet.so" "$DIST_DIR/"

echo ""
echo "Output: $DIST_DIR/libprofinet.so"
file "$DIST_DIR/libprofinet.so"
echo ""
echo "Exported symbols:"
nm -D "$DIST_DIR/libprofinet.so" | grep " T PN_"
