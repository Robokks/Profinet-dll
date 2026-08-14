# Handoff — Pure-Python PROFINET master → SINAMICS S120

## Goal
A **PC-based PROFINET IO-Controller in pure Python** (using the `profinet-py`
library, GPL-3.0) that connects to a real **Siemens SINAMICS S120 CU320-2 PN
V5.2** drive and exchanges cyclic process data, bridged to LabVIEW over
TCP/UDP/STM. Repo: `robokks/profinet-dll`, working branch
**`claude/profinet-dll-labview-bHem9`**. App lives in `profinet_gateway_py/`.
Main file being worked on: **`profinet_gateway_py/profinet_ctrl.py`**.

## Status — WORKING through connect + AR, finishing cyclic data
- ✅ **AR Connect established and stable** (no watchdog FAULT) to the real S120.
- 🔵 **Cyclic data** — just fixed the last-known blocker (engineered FrameID
  0x8000). Awaiting a test: does `ZSW1` now read a real value in IO Diagnostic
  and does the drive's "PN: cyclic connection interrupted" alarm clear?

## The device (target)
- Drive station name `driving`, IP `192.168.140.2`, MAC `00:1f:f8:ad:9f:ac`.
- **DeviceID 0x0501, VendorID 0x002A** (SINAMICS S120).
- Variant **CU320-2 PN V5.2**, DAP module ident **0x0002030D**, on the
  **onboard PROFINET interface** (the `_INT` GSDML variants, NOT the CBE20 ones).
- Drive object: **DO VECTOR + Free telegram PZD-32/32 (64/64 bytes)**.
- Commissioned via a **Hilscher cifX** master (RT_CLASS_2). STARTER p0922=999
  (free telegram with BICO).

## How we solved the connect — reverse-engineered from captures
The breakthrough was capturing the **working masters** and matching our Connect
byte-for-byte. Two reference captures (parsed under `/tmp`, originals were user
uploads):
1. **PNIO.dll** (Siemens PN Driver) connecting to VFDs incl. an S120 on another
   machine — gave AR/IOCR/AlarmCR structure.
2. **`hilscher_data.pcapng`** — the **cifX connecting to OUR exact S120**
   (192.168.140.2). This is the definitive reference for every block.

The S120 rejects profinet-py's stock Connect at every block. Each fix below is a
monkeypatch in `profinet_ctrl.py` (methods `_patch_*`), applied per-connect in
`configure_device()`. All are verified byte-for-byte against the cifX capture.

| Layer | profinet-py default | Fix (matches cifX) | Where |
|---|---|---|---|
| RPC ObjectUUID instance | 1 | **0** (retry on `nca_s_unk_if`) | `configure_device` loop |
| AR ARProperties | 0x00000011 (Legacy) | **0x40000011** (Advanced startup) + activity-timeout 200 | `_patch_ar_startup` (patches `PNARBlockRequest`) |
| IOCR | RT_CLASS_1, one API 0, wrong frame layout | **RT_CLASS_2**, drive under **API 0x3A00**, standard RT frame layout (every submod an IODataObject in its providing dir, IOCS in consuming dir), FrameID in=0x8000 out=0xFFFF | `_patch_iocr` (fully replaces `RPCCon._build_iocr_block`; layout in `_iocr_layout`) |
| ExpectedSubmodule | one block, all API 0 | **one block per module**; DAP slot 0 → API 0, drive slot 1 → **API 0x3A00** | `_patch_expected_submodule` (patches `add_submodule` + `to_bytes`) |
| Drive module/telegram idents | CBE variant (0x100100C0 / 0x200003E6) | **INT variant** 0x300100C4 / 0x400003E6 + empty submodule 0x1388 | `_build_expected_slots`, constants `_DRIVE_MODULE_IDENT` etc. |
| AlarmCR | props=0 rtatf=1 ref=1, block 4th | Layer-2 props=0, rtatf=1, rtar=3, **ref=0**, maxdata=200, and **block moved LAST** (after ExpectedSubmodule) | `_patch_alarm_cr` (defers AlarmCR, appends after ExpSubmod) |
| Block order | AR,IOCR,IOCR,AlarmCR,ExpSubmod | **AR,IOCR,IOCR,ExpSubmod,ExpSubmod,AlarmCR** | `_patch_alarm_cr` (deferral trick) |
| Cyclic frame layout | offset-0, 71 bytes | **72-byte, telegram at offset 6**, matches IOCR | `_build_iocr_configs` |
| Cyclic FrameID | from Connect response | **forced 0x8000 both directions** (engineered) | `_build_iocr_configs`, constants `_CYCLIC_FRAME_ID_IN/OUT` |

### Hardcoded constants (top of `profinet_ctrl.py`) — all for THIS drive
```
_SEND_CLOCK_FACTOR = 32      _CYCLE_MS = 16      _WATCHDOG_FACTOR = 3   (32/16/3 = 16ms cycle, matches cifX)
_DRIVE_MODULE_IDENT   = 0x300100C4   # IDM_VECTOR_51_INT
_DRIVE_TELEGRAM_IDENT = 0x400003E6   # IDS_TEL998_INT1 (Free PZD-32/32)
_INCLUDE_EMPTY_SUBMOD = 1
_ALARM_CR_PROPERTIES  = 0            # Layer-2
_IOCR_RT_CLASS        = 2            # RT_CLASS_2
_CYCLIC_FRAME_ID_IN   = 0x8000       _CYCLIC_FRAME_ID_OUT = 0x8000
```
Everything is also overridable by `PN_*` env vars (parsed by `_envint`, which
tolerates junk). **IMPORTANT for testing: make sure NO leftover `PN_*` env vars
are set in the PyCharm run config** — they override the constants and caused
confusion (e.g. a stale `PN_ALARM_PROPS=2`).

## Key gotchas learned
- **The drive must be FREE** — if the cifX/SYCON is online holding the AR, our
  Connect is refused with "Error in Parameter API". Stop SYCON before testing.
- profinet-py's cyclic IO is "experimental, not tested with real devices" — we
  had to replace/patch its IOCR builder, frame layout, and frame IDs.
- Every PNIO Connect reject is decoded in-app: `_decode_reject` + the
  `[PN] Device reject bytes` log line. Field errors follow Wireshark's
  `pn_io_error_code2` tables (e.g. EC1=4/EC2=5 = ExpectedSubmodule/API).

## What to do next (current task)
1. **Test the FrameID fix** (just pushed, commit "force cyclic FrameID 0x8000").
   Run (as admin, no PN_* env vars). Expect log
   `[PN] Cyclic FrameID in=0x8000 out=0x8000`.
2. Check **IO Diagnostic**: does `ZSW1` read a real value (e.g. 0x0E31/0xEF31)
   and does STARTER's "cyclic connection interrupted" alarm clear?
3. If data flows: test writing STW1 (0x047E→0x047F) + NSOLL and confirm the
   drive responds. Then the LabVIEW→TCP→gateway→drive path is complete.
4. If still 0x0000 / interrupted, remaining suspects, in order:
   - **RT frame data-status / cycle-counter** handling in
     `profinet.cyclic.CyclicController` (send clock 32 / reduction 16). Compare
     our sent RT frames vs the cifX's cyclic frames in `hilscher_data.pcapng`
     (frame data-status byte, cycle counter step, timing).
   - **RT_CLASS_2 needs a sync domain** — a pure software master can't do
     hardware IRT sync; the drive may require synchronized frames. This is the
     fundamental risk. If unsynchronized RT_CLASS_2 won't carry data, the
     realistic path is driving the cifX hardware from Python instead.
   - Verify our IOPS bytes are GOOD in the sent output frame and that we send at
     ~16ms with low jitter (the app's bridge loop showed max 37ms jitter — the
     profinet CyclicController has its own tx loop; check it isn't starved).

## Verifying against the reference capture
Parse `hilscher_data.pcapng` (pcapng, little-endian). The cifX MAC is
`00:02:a2:a5:ea:d1`, drive `00:1f:f8:ad:9f:ac`. The Connect is a UDP/34964
DCE-RPC REQUEST (opnum 0) to 192.168.140.2, 562 bytes; cyclic frames are
ethertype 0x8892 frameID 0x8000. Match our emitted bytes to these.

## Git / workflow
- Commit + push to `claude/profinet-dll-labview-bHem9` only.
- Commit trailer used:
  `Co-Authored-By: Claude <noreply@anthropic.com>` and a `Claude-Session:` line.
- Do NOT open a PR unless asked.
