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

## GOLDEN REFERENCE — cifX → OUR S120 (192.168.140.2) Connect, verbatim bytes
The working Connect our request must match byte-for-byte (controller-specific
fields — AR ARUUID, CMInitiatorMac `00:02:a2:a5:ea:d1`, CMInitiatorObjectUUID,
station name "controller" — will differ and that's fine). Block order below is
the exact wire order.

```
[ARBlockReq]  (ARProperties 0x40000011 Advanced, timeout 0x00c8=200)
01 01 00 40 01 00 00 01 0e 92 d4 be 36 81 4a 4f ac f0 c9 02 62 2d 46 32 00 00
00 02 a2 a5 ea d1 de a0 00 00 6c 97 11 d1 82 71 00 00 02 03 01 1e 40 00 00 11
00 c8 88 92 00 0a 63 6f 6e 74 72 6f 6c 6c 65 72

[IOCRBlockReq input]  (type1, ref 0x1000, RT_CLASS_2, datalen 0x48, FrameID 0x8000, sendclk 0x20, reduc 0x10, wd/hold 0x03, 2 APIs)
01 02 00 6a 01 00 00 01 10 00 88 92 00 00 00 02 00 48 80 00 00 20 00 10 00 01
00 00 ff ff ff ff 00 03 00 03 c0 00 00 00 00 00 00 00 00 02 00 00 00 00 00 04
00 00 00 01 00 00 00 00 80 00 00 01 00 00 80 01 00 02 00 00 80 02 00 03 00 00
00 00 3a 00 00 03 00 01 00 01 00 04 00 01 00 02 00 05 00 01 00 03 00 06 00 01
00 01 00 03 00 47

[IOCRBlockReq output]  (type2, ref 0x2000, FrameID 0xffff)
01 02 00 6a 01 00 00 02 20 00 88 92 00 00 00 02 00 48 ff ff 00 20 00 10 00 01
00 00 ff ff ff ff 00 03 00 03 c0 00 00 00 00 00 00 00 00 02 00 00 00 00 00 00
00 04 00 00 00 01 00 00 00 00 80 00 00 01 00 00 80 01 00 02 00 00 80 02 00 03
00 00 3a 00 00 01 00 01 00 03 00 06 00 03 00 01 00 01 00 04 00 01 00 02 00 05
00 01 00 03 00 47

[ExpectedSubmoduleBlockReq DAP]  (API 0, slot 0, module 0x0002030d, 4 submods)
01 04 00 4a 01 00 00 01 00 00 00 00 00 00 00 02 03 0d 00 00 00 04 00 01 00 00
00 02 00 00 00 01 00 00 01 01 80 00 00 00 00 03 00 00 00 01 00 00 01 01 80 01
00 00 00 04 00 00 00 01 00 00 01 01 80 02 00 00 00 05 00 00 00 01 00 00 01 01

[ExpectedSubmoduleBlockReq drive]  (API 0x00003a00, slot 1, module 0x300100c4;
  submods: subslot1 MAP 0xffff, subslot2 empty 0x1388, subslot3 telegram 0x400003e6 64/64)
01 04 00 42 01 00 00 01 00 00 3a 00 00 01 30 01 00 c4 00 00 00 03 00 01 00 00
ff ff 00 00 00 01 00 00 01 01 00 02 00 00 13 88 00 00 00 01 00 00 01 01 00 03
40 00 03 e6 00 03 00 01 00 40 01 01 00 02 00 40 01 01

[AlarmCRBlockReq]  (Layer-2 LT 0x8892, props 0, rtatf 1, rtar 3, ref 0, maxdata 0x00c8=200)  — comes LAST
01 03 00 16 01 00 00 01 88 92 00 00 00 00 00 01 00 03 00 00 00 c8 c0 00 a0 00
```

### Cyclic (RT) frames — ethertype 0x8892
- Drive → controller (INPUT): FrameID **0x8000**
- Controller → drive (OUTPUT): FrameID **0x8000**
- Frame body: FrameID(2) + C_SDU payload(72) + cycle_counter(2) + data_status(1)
  + transfer_status(1). Our output frame layout (72 B): telegram output data at
  offset 6..69, IOPS byte 70, IOCS byte 71; DAP/MAP/empty IOCS at 0..5. Input
  frame is the mirror (telegram input data at offset 6).

## Uploaded pcaps (user can re-upload on request)
- `40c2501a-hilscher_data.pcapng` — **cifX → our S120** (the golden reference above).
- `bede648c-py_.pcapng` — our profinet-py app's own attempt (for diffing).
- Earlier: `13de5802-profinet_py.pcapng`, `pniot111.pcap`/`pniot1231.pcap`
  (Siemens PNIO.dll to VFDs incl. an S120 at 192.168.161.11, DeviceID 0x0501).
