#!/bin/bash
# Build libprofinet.so for NI Linux RT ARM (cRIO 9067/9068/9074/9082)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUILD_DIR="$ROOT/build-ni-arm"
DIST_DIR="$ROOT/dist/ni-linux-rt-arm"

echo "=== Installing ARM cross-compiler (if needed) ==="
if ! command -v arm-linux-gnueabihf-gcc &>/dev/null; then
    sudo apt-get install -y gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf
fi

echo "=== Configuring ==="
mkdir -p "$BUILD_DIR"
cmake -S "$ROOT" -B "$BUILD_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-ni-arm.cmake" \
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
