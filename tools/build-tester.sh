#!/bin/bash
# Build profinet_tester.exe (Win32 GUI tester for profinet.dll)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUILD_DIR="$ROOT/build-tester"
DIST_DIR="$ROOT/dist"

echo "=== Configuring ==="
mkdir -p "$BUILD_DIR"
cmake -S "$ROOT/tester" -B "$BUILD_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-mingw32.cmake" \
    -DCMAKE_BUILD_TYPE=Release

echo "=== Building ==="
cmake --build "$BUILD_DIR" --parallel "$(nproc)"

echo "=== Installing to dist/ ==="
mkdir -p "$DIST_DIR"
cp "$BUILD_DIR/profinet_tester.exe" "$DIST_DIR/"

echo ""
echo "Output: $DIST_DIR/profinet_tester.exe"
file "$DIST_DIR/profinet_tester.exe"
echo ""
echo "Usage: copy profinet_tester.exe and profinet.dll to the same folder on Windows."
