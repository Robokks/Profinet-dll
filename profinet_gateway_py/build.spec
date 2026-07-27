# PyInstaller spec for the Profinet Gateway (profinet-py backend).
#
# Build (from the profinet_gateway_py folder, with the venv active):
#     pip install pyinstaller
#     pyinstaller build.spec
# Output: dist/ProfinetGateway/ProfinetGateway.exe   (one-folder)
#
# profinet-py + construct are pure Python but load submodules dynamically,
# so collect them wholesale. Pillow is optional (device bitmaps).

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Resolve paths relative to this spec file so it works from any CWD.
HERE = SPECPATH  # PyInstaller injects the spec's directory

datas, binaries, hiddenimports = [], [], []
for pkg in ("profinet", "construct"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# our own package modules + assets
hiddenimports += collect_submodules("ui")
datas += [(os.path.join(HERE, "assets"), "assets")]

block_cipher = None

a = Analysis(
    [os.path.join(HERE, "main.py")],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["PIL", "PIL.Image", "PIL.ImageTk"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ProfinetGateway",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app — no console window
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="ProfinetGateway",
)
