# Handoff — Pure-Python PROFINET master → SINAMICS S120

## Goal
A **PC-based PROFINET IO-Controller in pure Python** (using the `profinet-py`
library, GPL-3.0) that connects to a real **Siemens SINAMICS S120 CU320-2 PN
V5.2** drive and exchanges cyclic process data, bridged to LabVIEW over
TCP/UDP/STM. Repo: `robokks/profinet-dll`, working branch
**`claude/profinet-dll-labview-bHem9`**. App lives in `profinet_gateway_py/`.
Main file being worked on: **`profinet_gateway_py/profinet_ctrl.py`**.

## Status — software stack COMPLETE & CORRECT; blocked by the drive's mandatory RT_CLASS_2 sync
Everything a software master controls is now proven right against two working
masters (cifX + Siemens PNIO.dll). The remaining wall is **hardware**: this S120,
as commissioned, requires **RT_CLASS_2 with a sync domain (PTCP)** that a pure
software master cannot provide. Both software paths are now exhausted:

- **RTC2 (matches the cifX exactly):** Connect ✅, AR ✅. Our cyclic frames are
  byte-for-byte the cifX (VLAN-tagged, FrameID 0x8000, 96 B, data_status 0x35,
  IOCS 0x80 from frame #1). Captures show the drive **accepts our frames and
  ramps its provider up** (IOPS/IOCS 00→40→80, real **ZSW1=0x4000** decoded)
  — then, right at the ramp→operational transition (ds 0x35→0x15), it sends an
  **RTA ERR-PDU (FrameID 0xFE01)** and tears down: STARTER logs
  `1980: PN: cyclic connection interrupted`. It aborts in <1 cycle, i.e. at the
  **sync check**, not a slow watchdog.
- **RTC1 fallback:** **Connect REJECTED** — `IODConnectRes ErrorCode1=0x02
  (IOCRBlockReq) ErrorCode2=0x07 (IOCRProperties/RTClass)`. The drive refuses a
  non-RTC2 AR outright; its commissioned config mandates RT_CLASS_2.

**Conclusion:** a pure-Python master cannot sustain cyclic data with this drive
as it is commissioned. RTC2 needs sub-µs-class PTCP sync (Python jitter is ~14 ms
on a 16 ms cycle — orders of magnitude off), and RTC1 is refused. See "Paths
forward" below.

### Fixes that ARE done and verified (keep them — they're all correct)
- VLAN tagging both ways (`_patch_cyclic_vlan`), input-IOCS collision removed,
  offset-0 IOCS written (`_patch_cyclic_iocs`), cyclic started before
  PrmEnd/AppReady, IOCS good from frame #1, `PN_CYCLIC_MODE` switch, and the
  Connect-reject decoder now names the faulty block/field automatically.

## Paths forward (the drive needs to change, or use hardware)
1. **Re-commission the drive for plain RT (RT_CLASS_1), not IRT/RTC2.** The free
   telegram PZD-32/32 speed/torque use case does **not** need isochronous (IRT);
   IRT was set up by the cifX engineering. If the S120's PROFINET is reconfigured
   to standard RT (remove it from the sync domain / disable isochronous) in
   Startdrive/TIA/SYCON, the drive should then ACCEPT our RTC1 AR — and all the
   code above is already ready (`PN_CYCLIC_MODE=rtc1`). **Best option if the
   drive/app allows it.**
2. **Drive the cifX hardware from Python.** The cifX does the RTC2/IRT sync in
   silicon; feed it process data via its host API (cifX/netX driver). Pragmatic
   industrial fallback; the pure-Python RT path is abandoned.

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
| **Cyclic VLAN tag** | none (bare 0x8892), RX socket filtered on 0x8892, RX parser rejects 0x8100 | **802.1Q tag 0x8100 TCI 0xC000** on TX; RX socket unfiltered (ETH_P_ALL) + tag stripped before parse | `_patch_cyclic_vlan`, constant `_CYCLIC_VLAN_TCI` |
| **Input-CR IOCS objects** | added, and collide on `(1,3)` | **omitted from the input config** (RX keys by slot/subslot; the IOCS entry overwrote the 64 data bytes with `b""`) | `_build_iocr_configs` |
| **IOCS at frame offset 0** | never written (`if iocs_offset > 0`) | **written** — key IOCS objects off `data_length == 0` | `_patch_cyclic_iocs` |
| **Watchdog FAULT** | stops sending output frames | **`max_consecutive_timeouts=0`** — never FAULT, keep providing outputs | `configure_device` |
| **Cyclic start order** | cyclic started AFTER PrmEnd + ApplicationReady | **start cyclic right after Connect**, before PrmEnd/AppReady | `configure_device` |

### Hardcoded constants (top of `profinet_ctrl.py`) — all for THIS drive
```
_SEND_CLOCK_FACTOR = 32      _CYCLE_MS = 16      _WATCHDOG_FACTOR = 3   (32/16/3 = 16ms cycle, matches cifX)
_DRIVE_MODULE_IDENT   = 0x300100C4   # IDM_VECTOR_51_INT
_DRIVE_TELEGRAM_IDENT = 0x400003E6   # IDS_TEL998_INT1 (Free PZD-32/32)
_INCLUDE_EMPTY_SUBMOD = 1
_ALARM_CR_PROPERTIES  = 0            # Layer-2
_IOCR_RT_CLASS        = 2            # RT_CLASS_2
_CYCLIC_FRAME_ID_IN   = 0x8000       _CYCLIC_FRAME_ID_OUT = 0x8000
_CYCLIC_VLAN_TCI      = 0xC000       # 802.1Q PCP=6 VID=0 on cyclic frames
```
Cyclic-specific env overrides: `PN_CYCLIC_VLAN=0` sends untagged frames again,
`PN_CYCLIC_VLAN_TCI` changes the tag.

### Trying both RT classes — `PN_CYCLIC_MODE`
One switch flips the whole coherent config so both approaches can be tested
without hand-setting six vars (`_cyclic_profile`):
- `PN_CYCLIC_MODE=rtc2` (default) — **matches the cifX→our-S120 capture**:
  RT_CLASS_2, IOCR FrameID in=0x8000/out=0xFFFF, cyclic FrameID 0x8000 both
  ways, 802.1Q-tagged. First choice (the drive's commissioned config).
- `PN_CYCLIC_MODE=rtc1` — **unsynchronised RT_CLASS_1 fallback** a pure-software
  master can drive without a PTCP sync domain: RT_CLASS_1, FrameID 0xC000 both
  ways, tagged. Try this if rtc2 keeps aborting after ~2 frames (RTC2 hardware
  sync). Add `PN_CYCLIC_VLAN=0` to also drop the tag (untagged RT_CLASS_1).
Any individual `PN_*` var still overrides a single field. The active mode is
logged at connect: `[PN] Cyclic mode = RTC2 …`.
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
The FrameID-0x8000 fix was tested and was **not** sufficient: the AR connected
("cyclic running") but IO Diagnostic still showed ZSW1/NIST 0x0000 and the log
showed `Watchdog: 3 consecutive timeouts, entering FAULT state`. Diffing our
frames against the cifX capture found the real cause — **the cyclic frames are
802.1Q VLAN-tagged** — plus three related defects. All four are now fixed and
verified offline (our emitted output frame matches the cifX golden frame
byte-for-byte in framing; a golden tagged input frame decodes to real ZSW1/NIST):

1. `_patch_cyclic_vlan` — TX tagged 0x8100/TCI 0xC000; RX socket unfiltered so
   tagged frames are delivered; tag stripped before parsing (untagged still OK).
2. `_build_iocr_configs` — input config no longer carries IOCS objects that
   collided on `(1,3)` and blanked the 64 data bytes.
3. `_patch_cyclic_iocs` — IOCS at frame offset 0 is now written (was stuck BAD).
4. `max_consecutive_timeouts=0` — a watchdog blip no longer stops TX.

**What the `py_.pcapng` capture proved** (our app → S120, filter on the drive
MAC `00:1f:f8:ad:9f:ac`):
- Our OUTPUT frames: 1419 on the wire, VLAN-tagged, FrameID 0x8000, 96 B,
  data_status 0x35 — i.e. **framing is correct**.
- Drive INPUT frames: **only 2**, at t and t+16 ms (data_status 0x35, valid),
  then a **FrameID 0xFE01 alarm** ~3 ms after our first output frame, then
  silence. STARTER shows `1980: PN: cyclic connection interrupted(0)`.
- Our stats read `frames_received=0` because those 2 drive frames arrived
  **before** our CyclicController opened its RX socket — we started ~85 ms late.

**Next: run it on the drive** (as admin, no `PN_*` env vars, SYCON offline).
1. Does `ZSW1` read a real value (e.g. 0x0E31/0xEF31), do the Stats show
   `frames_received` climbing, and does STARTER's "cyclic connection
   interrupted" alarm stay clear?
2. If yes: write STW1 (0x047E→0x047F) + NSOLL and confirm the drive responds —
   that completes the LabVIEW→TCP→gateway→drive path.
3. If the drive STILL aborts (0xFE01 / alarm 1980) shortly after connect, this
   is the **RT_CLASS_2 synchronization** requirement — an RTC2 provider expects
   phase-aligned frames from a sync master (PTCP), which a software master can't
   supply. Levers, in order:
   - **RT_CLASS_1 fallback**: re-declare the IOCR as RTC1 — `PN_IOCR_RTCLASS=1`,
     `PN_IOCR_FRAMEID_IN`/`_OUT` in the 0xC000 range (e.g. 0xC000), and
     `PN_CYCLIC_FRAMEID_IN`/`_OUT` to match. RTC1 is unsynchronized and is what a
     pure-Python master can actually drive. Risk: the S120 may reject a non-RTC2
     Connect (it was commissioned RTC2 by the cifX) — test and read the reject.
   - Reduce startup latency further (start RT even earlier / warm the sockets).
   - If neither works, unsynchronized RTC2 genuinely can't carry data here and
     the realistic path is driving the cifX hardware from Python.

## Verifying against the reference capture
Parse `hilscher_data.pcapng` (pcapng, little-endian). The cifX MAC is
`00:02:a2:a5:ea:d1`, our S120 `00:1f:f8:ad:9f:ac`. The Connect is a UDP/34964
DCE-RPC REQUEST (opnum 0) to 192.168.140.2, 562 bytes.

**The capture contains TWO devices** — our S120 and a **Festo** drive
`00:0e:f0:ab:30:df` (which uses FrameID 0x8001). Always filter on our S120's MAC
or you will read the wrong device's frames. Our case is the single S120 only.

Cyclic frames are **VLAN-tagged**: outer ethertype 0x8100, inner 0x8892,
FrameID 0x8000, 96 bytes on the wire. In Wireshark:
`vlan && eth.addr==00:1f:f8:ad:9f:ac`. Note pcapng EPB packet data starts at
block-body offset 20.

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

### Cyclic (RT) frames — VLAN-tagged, ethertype 0x8100 → 0x8892
Verified directly against `hilscher_data.pcapng`, filtered to OUR S120
(`00:1f:f8:ad:9f:ac`; the capture also contains a **Festo** drive
`00:0e:f0:ab:30:df` — ignore it). Every FrameID-0x8000 frame in **both**
directions is **802.1Q priority-tagged**, 96 bytes on the wire:

```
dst(6) src(6) 8100 C000 8892 | FrameID(2) | C_SDU(72) | cycle(2) ds(1) ts(1)
                  ^^^^ TCI: PCP=6, VID=0  (= the declared IOCRTagHeader 0xC000)
```
- Counts in the capture: cifX→S120 tagged ×1904, S120→cifX tagged ×953. Zero
  untagged 0x8000 frames. **This was the bug that kept inputs at 0x0000**:
  profinet-py opened the RX socket filtered on `ether proto 0x8892` and its
  parser bailed on ethertype 0x8100, so every input frame was discarded, and it
  sent untagged frames the drive's consumer ignored → "cyclic connection
  interrupted".
- Golden OUTPUT C_SDU: `80 80 80 80 80 80` (six IOCS) + telegram[6..69] +
  IOPS@70=0x80 + IOCS@71=0x80. data_status **0x35**, transfer_status 0x00.
- Golden INPUT C_SDU: `40 40 40 40 40 40` + telegram[6..69] + IOPS@70=0x40 +
  IOCS@71=0x80. (0x40 = GOOD at *slot* level; 0x80 = GOOD at subslot level —
  both are "good", the drive just reports at a different hierarchy level.)
- cycle_counter steps by SCF*RR = 32*16 = **512** per frame; profinet-py already
  does this correctly.

## Uploaded pcaps (user can re-upload on request)
- `40c2501a-hilscher_data.pcapng` — **cifX → our S120** (the golden reference above).
- `bede648c-py_.pcapng` — our profinet-py app's own attempt (for diffing).
- Earlier: `13de5802-profinet_py.pcapng`, `pniot111.pcap`/`pniot1231.pcap`
  (Siemens PNIO.dll to VFDs incl. an S120 at 192.168.161.11, DeviceID 0x0501).
