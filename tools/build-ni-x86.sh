#!/bin/bash
# Build libprofinet.so for NI Linux RT x86 32-bit (cRIO 9030–9039)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUILD_DIR="$ROOT/build-ni-x86"
DIST_DIR="$ROOT/dist/ni-linux-rt-x86"

echo "=== Installing x86 cross-compiler (if needed) ==="
if ! command -v i686-linux-gnu-gcc &>/dev/null; then
    sudo apt-get install -y gcc-i686-linux-gnu g++-i686-linux-gnu
fi

echo "=== Configuring ==="
mkdir -p "$BUILD_DIR"
cmake -S "$ROOT" -B "$BUILD_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-ni-x86.cmake" \
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
