#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$REPO_DIR/build-win32"

echo "=== Profinet DLL — Windows 32-bit Cross-Compile ==="
echo "Repo: $REPO_DIR"

# Check toolchain
if ! command -v i686-w64-mingw32-gcc &>/dev/null; then
    echo "ERROR: MinGW-w64 not found. Install with:"
    echo "  sudo apt-get install -y mingw-w64 cmake"
    exit 1
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake "$REPO_DIR" \
    -DCMAKE_TOOLCHAIN_FILE="$REPO_DIR/cmake/toolchain-mingw32.cmake" \
    -DCMAKE_BUILD_TYPE=Release \
    -DNPCAP_SDK_DIR="$REPO_DIR/third_party/npcap-sdk" \
    "$@"

make -j"$(nproc)" VERBOSE=0

echo ""
echo "=== Build complete ==="
echo "Output: $BUILD_DIR/profinet.dll"
ls -lh "$BUILD_DIR/profinet.dll" 2>/dev/null || echo "WARNING: profinet.dll not found"
