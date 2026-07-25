# PyInstaller spec — builds ProfinetGateway.exe
# Usage: pyinstaller profinet_gateway/build.spec

import sys, os
block_cipher = None

# Determine DLL path
dist_dir = os.path.join(os.path.dirname(SPECPATH), '..', 'dist')
dll_path  = os.path.join(dist_dir, 'profinet.dll')

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[(dll_path, '.')],
    datas=[
        ('assets', 'assets'),
        ('config.json', '.') if os.path.exists(os.path.join(SPECPATH, 'config.json')) else ('assets', 'assets'),
    ],
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog',
                   'tkinter.messagebox', 'xml.etree.ElementTree'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ProfinetGateway',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
