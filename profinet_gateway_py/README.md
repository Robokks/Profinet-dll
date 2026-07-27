# Profinet Gateway — profinet-py backend

Same application and UI as `profinet_gateway/`, but the Profinet controller
layer is driven by the pure-Python **[profinet-py](https://github.com/f0rw4rd/profinet-py)**
library instead of the custom `profinet.dll`. No C DLL, no ctypes, no Npcap
struct matching — and the GSDML parser lists **every telegram** defined in a
device's GSDML (Standard Telegram 1…32, SIEMENS Telegram 111/350/352/353/370,
Free/Flexible), with human-readable names.

## Install
```
pip install -r requirements.txt
```

## Run
```
python main.py
```
Requires **Administrator (Windows)** / **root (Linux)** for raw Ethernet
(DCP + cyclic IO). On Windows, Npcap must be installed in *WinPcap
API-compatible* mode.

## What changed vs the DLL version
Only two modules differ; the entire UI (`ui/*`), `gateway_server.py`,
`bridge.py`, `config.py`, `main.py` and `assets/` are identical:
- `profinet_ctrl.py` — same `ProfinetCtrl` class/interface, now backed by
  `profinet.dcp` / `profinet.rpc.RPCCon` / `profinet.cyclic.CyclicController`.
- `gsdml.py` — same dataclasses, now backed by `profinet.gsdml.load_gsdml`
  (with added TextId name resolution + device bitmap).

## License
`profinet-py` is **GPL-3.0** (dual-licensed; a commercial license is required
for a closed-source product). This folder therefore inherits GPL obligations.

## Status note
`profinet-py` marks its **cyclic IO exchange as experimental** ("not tested
with real devices"). DCP discovery/set and the acyclic RPC connect are the
mature paths. Validate cyclic IO against your real SINAMICS drive.
