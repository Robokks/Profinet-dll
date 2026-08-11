# Building `ProfinetGateway.exe` (single click)

The gateway is a **Windows** GUI app (Tkinter + Npcap raw Ethernet), so the
EXE **must be built on a Windows PC**. PyInstaller is not a cross-compiler —
building on Linux/Mac produces a Linux/Mac binary, not a `.exe`.

---

## 1. One-click build

1. Copy the whole `profinet_gateway_py` folder onto a **Windows** machine.
2. Make sure **Python 3.9+** is installed
   ([python.org](https://www.python.org/downloads/) → tick **"Add Python to
   PATH"** during install).
3. **Double-click `build.bat`.**

`build.bat` creates a virtual environment, installs the dependencies +
PyInstaller, and runs the build. When it finishes you get:

```
dist\ProfinetGateway\ProfinetGateway.exe
```

That's it. Ship the **entire `dist\ProfinetGateway\` folder** (the `.exe`
needs the DLLs and data files next to it), not just the single `.exe`.

---

## 2. Running the EXE

- **Right-click → Run as administrator.** Raw Ethernet (DCP discovery +
  cyclic IO) needs admin rights.
- **[Npcap](https://npcap.com/#download)** must be installed in
  **"WinPcap API-compatible mode"** (a checkbox in the Npcap installer).
  Without it no network adapters appear.

---

## 3. What gets bundled

`build.spec` handles the tricky parts automatically:

| Item | Why it's needed |
|---|---|
| `collect_all("profinet")` | profinet-py loads submodules dynamically |
| `collect_all("construct")` | binary-struct parser used by profinet-py |
| `collect_submodules("ui")` | all the Tkinter windows/tabs |
| `assets/` folder | icons / images used by the UI |
| `PIL` hidden imports | optional GSDML device bitmaps |
| `console=False` | GUI app — no black console window |

The GSDML files themselves are **not** bundled — the app loads them from disk
at runtime, so keep a `gsdml\` folder next to the EXE (or point the config at
wherever your `.xml` GSDMLs live).

---

## 4. Manual build (if you don't want the .bat)

From a Windows command prompt, inside `profinet_gateway_py`:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm build.spec
```

Output: `dist\ProfinetGateway\ProfinetGateway.exe`.

---

## 5. Single-file EXE (optional)

The default build is **one-folder** (fast startup, easy to inspect). If you'd
rather ship one big `.exe`, change the tail of `build.spec` to a one-file
`EXE(...)` block:

```python
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="ProfinetGateway",
    debug=False, strip=False, upx=False,
    runtime_tmpdir=None,
    console=False,
)
# (remove the COLLECT block)
```

Trade-off: a one-file EXE unpacks to a temp dir on every launch, so it starts
a few seconds slower. One-folder is recommended for a device tool.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `Python not found` | Install Python 3.9+, tick "Add to PATH", reopen the window |
| Build OK but EXE shows no adapters | Install Npcap in **WinPcap-compatible** mode; run as admin |
| `ModuleNotFoundError` at runtime | Add the missing module to `hiddenimports` in `build.spec` and rebuild |
| Antivirus flags the EXE | Common false positive for PyInstaller EXEs; whitelist it or sign it |
| Slow first launch | Expected for one-file builds; use the one-folder (default) build |
