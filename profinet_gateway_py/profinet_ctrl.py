"""profinet_ctrl.py — Profinet IO-Controller backend (profinet-py, pure Python).

Drop-in replacement for the ctypes/DLL version: exposes the SAME `ProfinetCtrl`
class and method contract the UI, bridge and gateway already call, so nothing
in the UI changes. Internally it drives the GPL-3.0 `profinet-py` library:
  DCP discover / set-name / set-ip  → profinet.dcp
  AR establishment (RPC connect)     → profinet.rpc.RPCCon
  cyclic RT IO exchange              → profinet.cyclic.CyclicController

PROFIdrive Telegram-1 mapping: outputs = STW1(word)+NSOLL_A(word),
inputs = ZSW1(word)+NIST_A(word), big-endian on the wire (Profinet order).

NOTE: profinet-py marks cyclic IO as experimental; DCP + acyclic RPC are the
mature paths. Requires Administrator (Windows) / root (Linux) for raw sockets.
"""

import os
import re
import sys
import struct
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Callable

_GUID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")

# Cyclic timing: send_clock_factor=32 → 1 ms base clock, so the cycle time in
# milliseconds equals reduction_ratio. 8 ms balances latency vs. Python jitter
# (profinet-py allows >=1 ms; warns <8 ms). Lower it further only on a fast,
# lightly loaded PC.
_SEND_CLOCK_FACTOR = 32   # 32 x 31.25us = 1 ms send clock (matches cifX)
_CYCLE_MS = 16            # reduction ratio -> 16 ms cycle (matches cifX->S120)
_WATCHDOG_FACTOR = 3      # watchdog / data-hold factor (matches cifX)

# ── Connected-drive hardcoded parameters ─────────────────────────────────────
# Values captured from the working Hilscher cifX -> real S120 Connect
# (192.168.140.2, DeviceID 0x0501, CU320-2 PN V5.2 on the ONBOARD PN interface).
# The drive object is DO VECTOR + Free telegram PZD-32/32, so it uses the
# '_INT' (onboard-interface) module/telegram variants rather than the CBE20
# ones the GSDML dialog picks. Change these if the drive's commissioned config
# (Startdrive/SYCON) differs. Each is still overridable by the matching env var.
_DRIVE_MODULE_IDENT   = 0x300100C4   # IDM_VECTOR_51_INT  (DO VECTOR, FW5.1+, onboard)
_DRIVE_TELEGRAM_IDENT = 0x400003E6   # IDS_TEL998_INT1    (Free telegram PZD-32/32)
_INCLUDE_EMPTY_SUBMOD = 1            # S120 expects the empty sub-module at sub-slot 2
_ALARM_CR_PROPERTIES  = 0            # AlarmCR over Layer-2 (matches cifX->our S120)
_IOCR_RT_CLASS        = 2            # RT_CLASS_2
# For RT_CLASS_2/3 the cyclic FrameIDs are engineered, not returned by the
# Connect response. The cifX<->our-S120 capture uses 0x8000 in BOTH directions
# (drive->controller and controller->drive). We must send AND listen on these.
_CYCLIC_FRAME_ID_IN   = 0x8000       # drive -> controller (input)
_CYCLIC_FRAME_ID_OUT  = 0x8000       # controller -> drive (output)


# ── Result / state containers (shapes the UI reads) ──────────────────────────
@dataclass
class ScanResult:
    name_of_station: bytes
    ip_str: bytes
    mac: bytes            # 6 raw bytes
    netmask: str = ""
    gateway: str = ""
    vendor_name: str = ""
    vendor_id: int = 0
    device_id: int = 0


@dataclass
class Stats:
    frames_sent: int = 0
    frames_received: int = 0
    missed_cycles: int = 0
    watchdog_timeouts: int = 0
    cycle_counter: int = 0
    connected: int = 0
    cyclic_running: int = 0
    # Profinet cyclic timing / latency (microseconds)
    frames_invalid: int = 0
    last_cycle_us: int = 0
    min_cycle_us: int = 0
    max_cycle_us: int = 0
    avg_cycle_us: float = 0.0
    max_jitter_us: int = 0
    consecutive_timeouts: int = 0


@dataclass
class DeviceState:
    config: object
    connected: bool = False
    zsw1: int = 0
    nist: int = 0
    stw1: int = 0
    nsoll: int = 0
    error: str = ""
    # profinet-py runtime objects
    conn: object = None        # rpc.RPCCon
    cyclic: object = None       # cyclic.CyclicController
    src_mac: bytes = b""
    slot: int = 1
    subslot: int = 1
    dos: list = field(default_factory=list)   # [(slot, subslot, out_len, in_len)]


class ProfinetCtrl:
    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log = log_cb or print
        self._devices: List[DeviceState] = []
        self._lock = threading.Lock()
        self._adapter = ""
        self._ok = self._check_lib()

    def _check_lib(self) -> bool:
        try:
            import profinet  # noqa: F401
            self._log("[PN] profinet-py backend loaded")
            return True
        except Exception as e:
            self._log(f"[PN] ERROR: profinet-py not available: {e}")
            self._log("[PN] Install with: pip install profinet-py")
            return False

    # ── version / adapters ──────────────────────────────────────────────────
    def get_version(self) -> str:
        try:
            from importlib.metadata import version
            return "profinet-py " + version("profinet-py")
        except Exception:
            return "profinet-py"

    def enumerate_adapters(self) -> List[str]:
        return [n for (n, _d) in self.enumerate_adapters_verbose()]

    def enumerate_adapters_verbose(self):
        """Return [(name, description), ...]."""
        if sys.platform == "win32":
            return self._win_adapters()
        out = []
        try:
            for n in sorted(os.listdir("/sys/class/net")):
                out.append((n, "loopback" if n == "lo" else "network interface"))
        except Exception:
            out = [("eth0", "network interface")]
        return out

    def _win_adapters(self):
        """Windows: enumerate NPF adapters + descriptions via wpcap."""
        import ctypes
        result = []
        try:
            dll = None
            for c in (r"C:\Windows\System32\Npcap\wpcap.dll", "wpcap.dll"):
                try:
                    dll = ctypes.CDLL(c); break
                except OSError:
                    continue
            if not dll:
                return result

            class _if(ctypes.Structure):
                pass
            _if._fields_ = [("next", ctypes.POINTER(_if)),
                            ("name", ctypes.c_char_p),
                            ("description", ctypes.c_char_p),
                            ("addresses", ctypes.c_void_p),
                            ("flags", ctypes.c_uint)]
            dll.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(_if)),
                                             ctypes.c_char_p]
            dll.pcap_findalldevs.restype = ctypes.c_int
            dll.pcap_freealldevs.argtypes = [ctypes.POINTER(_if)]
            alldevs = ctypes.POINTER(_if)()
            errbuf = ctypes.create_string_buffer(256)
            if dll.pcap_findalldevs(ctypes.byref(alldevs), errbuf) != 0:
                return result
            d = alldevs
            while d:
                node = d.contents
                name = node.name.decode(errors="replace") if node.name else ""
                desc = node.description.decode(errors="replace") if node.description else ""
                if "loopback" in name.lower() and not desc:
                    desc = "Npcap Loopback Adapter"
                if name:
                    result.append((name, desc or "(no description)"))
                d = node.next
            dll.pcap_freealldevs(alldevs)
        except Exception as e:
            self._log(f"[PN] adapter enumeration error: {e}")
        return result

    def resolve_adapter(self, adapter: str) -> str:
        """Heal a stale adapter name against the live list (GUID match)."""
        if not adapter:
            return adapter
        available = self.enumerate_adapters()
        if adapter in available:
            return adapter
        m = _GUID_RE.search(adapter)
        if m:
            guid = m.group(0).upper()
            for name in available:
                nm = _GUID_RE.search(name)
                if nm and nm.group(0).upper() == guid:
                    self._log(f"[PN] Auto-corrected adapter: {adapter} -> {name}")
                    return name
        self._log(f"[PN] WARNING: adapter '{adapter}' not in list: {available}")
        return adapter

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self, adapter: str, dev_configs: list) -> bool:
        if not self._ok:
            return False
        self.stop()
        self._adapter = self.resolve_adapter(adapter)
        with self._lock:
            self._devices = [DeviceState(config=dc, slot=dc.slot, subslot=dc.subslot)
                             for dc in dev_configs]
        self._log(f"[PN] Backend started on adapter '{self._adapter}' "
                  f"with {len(dev_configs)} device(s)")
        return True

    def configure_device(self, idx: int) -> bool:
        """DCP set name/IP, RPC AR connect, and start cyclic exchange."""
        ds = self._get(idx)
        if ds is None:
            return False
        dc = ds.config
        if not dc.station_name:
            ds.connected = False
            ds.error = "no station name — configure it"
            self._log(f"[PN] Device at {dc.ip}: no station name set — open "
                      f"Configuration, set the station name, Apply & Restart.")
            return False
        conn = ctrl = None
        cap = self._rpc_capture_begin()
        self._patch_ar_startup()
        self._patch_iocr()
        self._patch_expected_submodule()
        try:
            from profinet import dcp, rpc
            from profinet.rt import build_iocr_configs
            from profinet.cyclic import CyclicController
            from profinet.util import ethernet_socket, get_mac, s2mac

            src = get_mac(self._macname(self._adapter))
            ds.src_mac = src

            # 1. DCP: assign name + IP if we have the device MAC
            sock = ethernet_socket(self._adapter, 3)
            try:
                if dc.mac:
                    target = dc.mac.lower()
                    if dc.station_name:
                        dcp.set_param(sock, src, target, "name", dc.station_name)
                        self._log(f"[PN] DCP SetName '{dc.station_name}' -> {target}")
                    if dc.ip:
                        dcp.set_ip(sock, src, target, dc.ip, dc.subnet, dc.gateway)
                        self._log(f"[PN] DCP SetIP {dc.ip} -> {target}")
                info = rpc.get_station_info(sock, src, dc.station_name)
                self._log(f"[PN] Resolved '{dc.station_name}' at {info.ip} ({info.mac})")
            finally:
                sock.close()

            # 2. AR connect. The ExpectedSubmoduleBlock MUST mirror the device's
            #    real topology or a device rejects the Connect: the Device Access
            #    Point at slot 0 (head + PDEV interface/ports) AND, for each Drive
            #    Object, its Module Access Point (sub-slot 1) + empty sub-module
            #    (sub-slot 2) + the telegram (sub-slot 3). profinet-py only sends
            #    the slots we give it, so we build the whole topology from the
            #    GSDML for the selected variant.
            dos = dc.effective_drive_objects()
            io_slots, do_map = self._build_expected_slots(dc)
            # Process-data mapping = the telegram sub-slots (slot 0 / MAP / empty
            # carry no cyclic data).
            ds.dos = do_map
            ds.slot, ds.subslot = do_map[0][0], do_map[0][1]
            # IOCR timing. Defaults are profinet-py's (1ms send clock, 8x
            # reduction). Env-overridable so we can match the drive's engineered
            # send clock if it rejects on timing (PNIO.dll used 128/16, wd=3).
            sc = self._envint("PN_SEND_CLOCK", _SEND_CLOCK_FACTOR)
            rr = self._envint("PN_REDUCTION", _CYCLE_MS)
            wd = self._envint("PN_WATCHDOG", _WATCHDOG_FACTOR)
            setup = rpc.IOCRSetup(slots=io_slots,
                                  send_clock_factor=sc,
                                  reduction_ratio=rr,
                                  watchdog_factor=wd,
                                  data_hold_factor=wd)

            # The RPC ObjectUUID's instance number (ObjectUUID_LocalIndex) is 1
            # by convention, but PROFIdrive profile devices (SINAMICS) can
            # require 0 — a mismatch is rejected as nca_s_unk_if (0x1C010003).
            # profinet-py hardcodes 1, so try 1 first, then 0 on that reject.
            conn = result = None
            last_exc = None
            for inst in (1, 0):
                try:
                    conn = rpc.RPCCon(info)
                    if inst != 1:
                        # object UUID = prefix(10) + instance(2) + device(2)+vendor(2)
                        ou = conn.remote_object_uuid
                        conn.remote_object_uuid = (ou[:10]
                                                   + inst.to_bytes(2, "big") + ou[12:])
                    # SINAMICS rejects AlarmCRProperties=0 (ErrorCode1=3 AlarmCR,
                    # ErrorCode2=6). Force Priority=1 (0x0001); Transport stays 0
                    # (Layer-2 RT), reserved bits 0 — so 0x0001 is the only other
                    # valid value. profinet-py hardcodes 0.
                    self._patch_alarm_cr(conn)
                    result = conn.connect(src, iocr_setup=setup)
                    if inst != 1:
                        self._log(f"[PN] Connected with ObjectUUID instance {inst}")
                    break
                except Exception as ce:
                    last_exc = ce
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                    if "unk_if" in str(ce).lower() or "reject" in str(ce).lower():
                        if inst == 1:
                            self._log("[PN] instance-1 Connect refused (unk_if); "
                                      "retrying with ObjectUUID instance 0…")
                            continue
                    raise
            if conn is None:
                raise last_exc if last_exc else RuntimeError("connect failed")
            if not result or not getattr(result, "has_cyclic", False):
                ds.error = "no cyclic AR"
                ds.connected = False
                conn.close()
                self._log(f"[PN] Connect failed for {dc.station_name}: no cyclic AR")
                return False
            conn.prm_end()
            conn.application_ready()

            # 3. Cyclic controller — build the frame layout ourselves so the
            #    cyclic frame matches the IOCR we declared to the device
            #    (telegram data at frame offset 6, 72-byte frame). profinet-py's
            #    build_iocr_configs uses its own (offset-0) layout and would
            #    misalign the data / drop the AR.
            in_cfg, out_cfg = self._build_iocr_configs(
                io_slots, result.input_frame_id, result.output_frame_id)
            ctrl = CyclicController(self._adapter, src, s2mac(info.mac),
                                    in_cfg, out_cfg)
            ctrl.start()

            ds.conn = conn
            ds.cyclic = ctrl
            ds.connected = True
            ds.error = ""
            self._log(f"[PN] Connected to {dc.station_name} ({info.ip}) — cyclic running")
            return True
        except Exception as e:
            ds.connected = False
            ds.error = str(e)
            self._log(f"[PN] Connect failed for {getattr(dc, 'station_name', '?')}: {e}")
            msg = str(e).lower()
            if "reject" in msg or "fault" in msg:
                # Dump the exact request we sent and the device's reject bytes,
                # then decode the DCE/RPC reject code — this pinpoints the cause.
                self._rpc_capture_report(cap)
                self._log("[PN] HINT: the device refused the AR. Check that (1) the "
                          "selected Device variant matches the real control unit and "
                          "firmware, (2) the drive object(s)/telegram match the drive's "
                          "commissioned config (Startdrive/STARTER), and (3) no other "
                          "controller (PLC) already holds a connection to this device.")
            # clean up any half-open resources so a failed reconnect doesn't leak
            try:
                if ctrl is not None:
                    ctrl.stop()
            except Exception:
                pass
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            return False
        finally:
            self._rpc_capture_end(cap)

    # ── DCE/RPC reject diagnostics ───────────────────────────────────────────
    # DCE 1.1 connectionless reject status codes (facility 0x1c). PROFINET
    # devices send one of these in a reject PDU when they refuse the Connect.
    _NCA_REJECT = {
        0x1C010001: "nca_s_comm_failure",
        0x1C010002: "nca_s_op_rng_error (unknown operation number)",
        0x1C010003: "nca_s_unk_if (unknown interface UUID)",
        0x1C010006: "nca_s_wrong_boot_time",
        0x1C010009: "nca_s_you_crashed",
        0x1C01000B: "nca_s_proto_error",
        0x1C01000C: "nca_s_who_are_you_failed",
        0x1C010013: "nca_s_out_args_too_big",
        0x1C010014: "nca_s_server_too_busy",
        0x1C010015: "nca_s_manager_not_entered",
        0x1C01001B: "nca_s_wrong_kind_of_bindings",
    }

    @staticmethod
    def _envint(name, default):
        """Parse an integer env var, tolerating trailing junk (e.g. when several
        KEY=VALUE pairs get pasted into one PyCharm field). Takes the first
        whitespace/';'-separated token; supports 0x hex."""
        v = os.environ.get(name)
        if v is None or not v.strip():
            return default
        tok = v.strip().replace(";", " ").split()[0]
        try:
            return int(tok, 0)
        except ValueError:
            return default

    def _patch_ar_startup(self):
        """Set ARProperties StartupMode = Advanced (bit 30) and the CMInitiator
        activity-timeout to 200, matching Siemens' own PN Driver (PNIO.dll).
        profinet-py hardcodes ARProperties=0x00000011 (Legacy startup) and
        timeout=100; a real SINAMICS S120 V5.x needs Advanced startup, and in
        Legacy mode it rejects the AR (surfacing as an AlarmCR error). Patch the
        module-global PNARBlockRequest so connect() emits the Advanced value.

          PN_AR_STARTUP=legacy   to force Legacy (default advanced)
        """
        try:
            import profinet.rpc as _r
            if getattr(_r, "_ar_startup_patched", False):
                return
            if os.environ.get("PN_AR_STARTUP", "advanced").lower() == "legacy":
                return
            orig = _r.PNARBlockRequest

            def wrapped(*a, **k):
                a = list(a)
                if len(a) > 6 and isinstance(a[6], int):
                    a[6] = a[6] | 0x40000000          # ARProperties: Advanced startup
                elif "ar_properties" in k:
                    k["ar_properties"] = k["ar_properties"] | 0x40000000
                if len(a) > 7 and isinstance(a[7], int):
                    a[7] = 0x00C8                       # CMInitiatorActivityTimeoutFactor=200
                return orig(*a, **k)

            wrapped.fmt_size = orig.fmt_size            # connect() reads this
            _r.PNARBlockRequest = wrapped
            _r._ar_startup_patched = True
            self._log("[PN] AR startup mode = Advanced (ARProperties bit30), "
                      "activity-timeout=200 (matches Siemens PN Driver)")
        except Exception as e:
            self._log(f"[PN] WARN: could not set Advanced startup ({e})")

    def _patch_expected_submodule(self):
        """Emit one ExpectedSubmoduleBlockReq per module, matching Siemens' PN
        Driver (PNIO.dll). profinet-py packs every module into a single block
        with NumberOfAPIs=N (repeating API=0); the S120 rejects that at field 5
        (API). PNIO sends a separate block per module (DAP, then each drive
        object), each NumberOfAPIs=1. Patch to_bytes to split accordingly."""
        try:
            import profinet.rpc as _r
            if getattr(_r, "_exp_submod_patched", False):
                return
            cls = _r.ExpectedSubmoduleBlockReq
            orig = cls.to_bytes
            orig_add = cls.add_submodule
            drive_api = self._envint("PN_DRIVE_API", 0x00003A00)

            def patched_add(self, api, slot, subslot, module_ident, submodule_ident,
                            submodule_type=0, input_length=0, output_length=0):
                # PROFIdrive drive objects (slot >= 1) live under API 0x3A00;
                # only the DAP/PDEV at slot 0 uses API 0. profinet-py hardcodes 0.
                if slot != 0:
                    api = drive_api
                return orig_add(self, api, slot, subslot, module_ident,
                                submodule_ident, submodule_type, input_length, output_length)

            def patched_to_bytes(self):
                # One ExpectedSubmoduleBlockReq per module (matches PN Driver).
                if len(self.apis) <= 1:
                    return orig(self)
                saved = self.apis
                out = b""
                try:
                    for entry in saved:
                        self.apis = [entry]     # one module -> one block
                        out += orig(self)
                finally:
                    self.apis = saved
                return out

            cls.add_submodule = patched_add
            cls.to_bytes = patched_to_bytes
            _r._exp_submod_patched = True
            self._log(f"[PN] ExpectedSubmodule: one block per module, drive "
                      f"objects under API 0x{drive_api:04X} (matches PN Driver)")
        except Exception as e:
            self._log(f"[PN] WARN: could not adjust ExpectedSubmodule ({e})")

    @staticmethod
    def _iocr_layout(slots):
        """Compute the standard PROFINET RT frame layout (matching the cifX /
        real S120) for both directions. Returns (in_objs,in_iocs,in_len,
        out_objs,out_iocs,out_len) where *_objs = [(slot,subslot,offset,dlen)]
        (IODataObjects) and *_iocs = [(slot,subslot,offset)].

        Input CR (device->controller): EVERY sub-module is an IODataObject
          (its input_length data + 1 IOPS byte; 0-I/O sub-modules contribute
          just the IOPS). Sub-modules with output get a trailing IOCS.
        Output CR (controller->device): sub-modules with output are
          IODataObjects; all others get an IOCS in place, then output
          sub-modules get a trailing IOCS. profinet-py's own layout differs
          and a real S120 rejects it."""
        in_objs = []; in_iocs = []; off = 0
        for s in slots:
            in_objs.append((s.slot, s.subslot, off, s.input_length))
            off += s.input_length + 1
        for s in slots:
            if s.output_length > 0:
                in_iocs.append((s.slot, s.subslot, off)); off += 1
        in_len = max(40, off)

        out_objs = []; out_iocs = []; off = 0
        for s in slots:
            if s.output_length > 0:
                out_objs.append((s.slot, s.subslot, off, s.output_length))
                off += s.output_length + 1
            else:
                out_iocs.append((s.slot, s.subslot, off)); off += 1
        for s in slots:
            if s.output_length > 0:
                out_iocs.append((s.slot, s.subslot, off)); off += 1
        out_len = max(40, off)
        return in_objs, in_iocs, in_len, out_objs, out_iocs, out_len

    def _build_iocr_configs(self, slots, in_frame_id, out_frame_id):
        """Build the cyclic IOCRConfigs matching the IOCR we declared to the
        device (same _iocr_layout): telegram data at frame offset 6, IOPS/IOCS
        placed to make a 72-byte frame. Returns (input_cfg, output_cfg)."""
        from profinet.rt import IOCRConfig, IODataObject
        (in_objs, in_iocs, in_len, out_objs, out_iocs,
         out_len) = self._iocr_layout(slots)

        def cfg(iocr_type, ref, frame_id, objs, iocs, dlen):
            io = []
            for (sl, ss, off, dl) in objs:
                io.append(IODataObject(slot=sl, subslot=ss, frame_offset=off,
                                       data_length=dl, iops_offset=off + dl,
                                       iocs_offset=0))
            for (sl, ss, off) in iocs:
                io.append(IODataObject(slot=sl, subslot=ss, frame_offset=0,
                                       data_length=0, iops_offset=0,
                                       iocs_offset=off))
            return IOCRConfig(iocr_type=iocr_type, iocr_reference=ref,
                              frame_id=frame_id,
                              send_clock_factor=_SEND_CLOCK_FACTOR,
                              reduction_ratio=_CYCLE_MS, phase=1,
                              watchdog_factor=_WATCHDOG_FACTOR,
                              data_length=dlen, objects=io)

        # RT_CLASS_2 cyclic FrameIDs are engineered (0x8000 both ways for this
        # drive), not the values returned by the Connect response. Force them.
        fid_in = self._envint("PN_CYCLIC_FRAMEID_IN", _CYCLIC_FRAME_ID_IN)
        fid_out = self._envint("PN_CYCLIC_FRAMEID_OUT", _CYCLIC_FRAME_ID_OUT)
        in_cfg = cfg(1, 1, fid_in, in_objs, in_iocs, in_len)
        out_cfg = cfg(2, 2, fid_out, out_objs, out_iocs, out_len)
        self._log(f"[PN] Cyclic FrameID in=0x{fid_in:04X} out=0x{fid_out:04X} "
                  f"(engineered, from cifX capture)")
        return in_cfg, out_cfg

    def _patch_iocr(self):
        """Replace profinet-py's IOCR block builder so the IOCR matches the real
        S120 (from the cifX capture): RT_CLASS_2, drive data grouped under API
        0x3A00 (DAP/PDEV under API 0), and the standard RT frame layout (every
        sub-module an IODataObject in its providing direction, IOCS in the
        consuming direction). profinet-py groups everything under API 0 with a
        different layout, which the S120 rejects (ExpectedSubmodule/IOCR API
        cross-check). Verified to reproduce the cifX IOCR byte-for-byte."""
        try:
            import struct as _st
            import profinet.rpc as _r
            if getattr(_r, "_iocr_patched", False):
                return
            drive_api = self._envint("PN_DRIVE_API", 0x00003A00)
            rtclass = self._envint("PN_IOCR_RTCLASS", _IOCR_RT_CLASS)
            fid_in = self._envint("PN_IOCR_FRAMEID_IN", 0x8000)
            fid_out = self._envint("PN_IOCR_FRAMEID_OUT", 0xFFFF)
            api_of = lambda slot: 0 if slot == 0 else drive_api

            def build(self, iocr_type, iocr_reference, setup):
                (in_objs, in_iocs, in_len, out_objs, out_iocs,
                 out_len) = ProfinetCtrl._iocr_layout(setup.slots)
                if iocr_type == 1:
                    objs, iocs, dlen, ref, fid = in_objs, in_iocs, in_len, 0x1000, fid_in
                else:
                    objs, iocs, dlen, ref, fid = out_objs, out_iocs, out_len, 0x2000, fid_out
                # group by API, API 0 first
                grouped = {}
                for (sl, ss, o, dl) in objs:
                    grouped.setdefault(api_of(sl), ([], []))[0].append((sl, ss, o))
                for (sl, ss, o) in iocs:
                    grouped.setdefault(api_of(sl), ([], []))[1].append((sl, ss, o))
                apisec = b""
                for api in sorted(grouped):
                    od, ic = grouped[api]
                    apisec += _st.pack(">IH", api, len(od))
                    for (sl, ss, o) in od:
                        apisec += _st.pack(">HHH", sl, ss, o)
                    apisec += _st.pack(">H", len(ic))
                    for (sl, ss, o) in ic:
                        apisec += _st.pack(">HHH", sl, ss, o)
                hdr = _st.pack(">HH", iocr_type, ref)
                hdr += _st.pack(">H", 0x8892)                 # LT
                hdr += _st.pack(">I", rtclass & 0xFFFFFFFF)   # IOCRProperties
                hdr += _st.pack(">HH", dlen, fid)
                hdr += _st.pack(">HH", setup.send_clock_factor, setup.reduction_ratio)
                hdr += _st.pack(">HH", 1, 0)                  # phase, sequence
                hdr += _st.pack(">I", 0xFFFFFFFF)             # frame_send_offset
                hdr += _st.pack(">HH", setup.watchdog_factor, setup.data_hold_factor)
                hdr += _st.pack(">H", 0xC000)                 # IOCRTagHeader
                hdr += b"\x00" * 6                            # multicast MAC
                hdr += _st.pack(">H", len(grouped))           # NumberOfAPIs
                body = hdr + apisec
                return _st.pack(">HHBB", 0x0102, len(body) + 2, 1, 0) + body

            _r.RPCCon._build_iocr_block = build
            _r._iocr_patched = True
            self._log(f"[PN] IOCR: RT_CLASS_{rtclass}, drive data under API "
                      f"0x{drive_api:04X}, standard RT frame layout "
                      f"(matches cifX/real S120)")
        except Exception as e:
            self._log(f"[PN] WARN: could not replace IOCR builder ({e})")

    def _patch_alarm_cr(self, conn):
        """Match the AlarmCR block to Siemens' PN Driver (PNIO.dll), which
        connects to these drives successfully. Captured working values:
        AlarmCRProperties=0, RTATimeoutFactor=2, RTARetries=3,
        LocalAlarmReference=2, MaxAlarmDataLength=200. profinet-py's defaults
        differ (RTATimeoutFactor=1, LocalAlarmReference=1). Overridable by env:

          PN_ALARM_PROPS   AlarmCRProperties  (default 0)
          PN_ALARM_MAXDATA MaxAlarmDataLength (default 200, matching PNIO.dll)
          PN_ALARM_RTATF   RTATimeoutFactor   (default 2, matching PNIO.dll)
          PN_ALARM_RTAR    RTARetries         (default 3)
          PN_ALARM_REF     LocalAlarmReference(default 2, matching PNIO.dll)
        """
        try:
            from profinet.rpc import PNAlarmCRBlockReq as A
            # Match the AlarmCR the cifX sends OUR S120 exactly: Layer-2
            # (props=0, LT=0x8892), RTATimeoutFactor=1, RTARetries=3,
            # LocalAlarmReference=0, MaxAlarmDataLength=200.
            props = self._envint("PN_ALARM_PROPS", _ALARM_CR_PROPERTIES)
            maxdata = self._envint("PN_ALARM_MAXDATA", 200)
            rtatf = self._envint("PN_ALARM_RTATF", 1)
            rtar = self._envint("PN_ALARM_RTAR", 3)
            alarm_ref = self._envint("PN_ALARM_REF", 0)
            A.DEFAULT_MAX_ALARM_DATA_LENGTH = maxdata
            A.DEFAULT_RTA_TIMEOUT_FACTOR = rtatf
            A.DEFAULT_RTA_RETRIES = rtar
            conn._alarm_ref = alarm_ref                 # LocalAlarmReference
            priority = props & 0x1
            transport = (props >> 1) & 0x1
            orig_alarm = conn._build_alarm_cr_block
            orig_exp = conn._build_expected_submodule_block

            # PROFINET / the S120 expects block order:
            #   AR, IOCR, IOCR, ExpectedSubmodule..., AlarmCR
            # profinet-py emits the AlarmCR BEFORE the ExpectedSubmodule. Defer
            # it: _build_alarm_cr_block stores the bytes and returns nothing, and
            # _build_expected_submodule_block appends it after the ExpSubmod
            # blocks — so the AlarmCR ends up last, matching the cifX.
            def deferred_alarm(t=transport, p=priority):
                self._deferred_alarm_cr = orig_alarm(t, p)
                return b""

            def exp_then_alarm(setup):
                data = orig_exp(setup)
                return data + getattr(self, "_deferred_alarm_cr", b"")

            conn._build_alarm_cr_block = deferred_alarm
            conn._build_expected_submodule_block = exp_then_alarm
            self._log(f"[PN] AlarmCR: props=0x{props:04X} maxdata={maxdata} "
                      f"rtatf={rtatf} rtar={rtar} ref={alarm_ref}; block order "
                      f"AR,IOCR,ExpSubmod,AlarmCR (matches cifX)")
        except Exception as e:
            self._log(f"[PN] WARN: could not adjust AlarmCR block ({e})")

    def _rpc_capture_begin(self):
        """Attach a DEBUG capture to profinet-py's RPC logger so a failed
        connect can print the exact request + device response bytes into the
        normal log (no PN_DEBUG env var needed)."""
        import logging
        try:
            handler = logging.Handler()
            handler.setLevel(logging.DEBUG)
            handler._msgs = []
            handler.emit = lambda rec, h=handler: h._msgs.append(rec.getMessage())
            lg = logging.getLogger("profinet")
            handler._lg = lg
            handler._old_level = lg.level
            handler._old_prop = lg.propagate
            lg.setLevel(logging.DEBUG)
            lg.addHandler(handler)
            return handler
        except Exception:
            return None

    def _rpc_capture_end(self, cap):
        try:
            if cap is not None:
                cap._lg.removeHandler(cap)
                cap._lg.setLevel(cap._old_level)
        except Exception:
            pass

    def _rpc_capture_report(self, cap):
        if cap is None:
            return
        try:
            req = resp = None
            for m in cap._msgs:
                if m.startswith("RPC request ("):
                    req = m
                elif m.startswith("RPC response raw"):
                    resp = m
            if req:
                self._log("[PN] AR Connect request bytes: "
                          + req.split(": ", 1)[-1].strip())
            if resp:
                hexpart = resp.split(": ", 1)[-1].replace("...", "").strip()
                self._log("[PN] Device reject bytes: " + hexpart)
                self._decode_reject(hexpart)
            else:
                self._log("[PN] (no device response captured — likely a timeout "
                          "or the reject came before logging; set PN_DEBUG=1 for full trace)")
        except Exception:
            pass

    def _decode_reject(self, hexpart):
        """Best-effort decode of a DCE/RPC reject PDU status code."""
        try:
            b = bytes.fromhex(hexpart.replace(" ", ""))
        except Exception:
            return
        if len(b) < 2:
            return
        ptype = b[1]
        if ptype != 6:  # 6 = REJECT
            self._log(f"[PN] (response packet type 0x{ptype:02X}, not a plain reject)")
            return
        # Connectionless DCE/RPC header is 80 bytes; the reject body starts with
        # a 4-byte status. Endianness follows DREP (byte 4); try both, prefer a
        # known code.
        if len(b) >= 84:
            le = int.from_bytes(b[80:84], "little")
            be = int.from_bytes(b[80:84], "big")
            code = le if le in self._NCA_REJECT else (be if be in self._NCA_REJECT else le)
            name = self._NCA_REJECT.get(code)
            self._log(f"[PN] DCE/RPC reject status = 0x{code:08X}"
                      + (f"  ({name})" if name else "  (unrecognized — paste this line to me)"))
            if code == 0x1C010003:  # nca_s_unk_if
                self._log("[PN] -> The device's RPC runtime does not accept this AR "
                          "Connect. Our interface/object UUIDs are the standard "
                          "PROFINET values, so this is a stack-level incompatibility "
                          "(profinet-py vs this device). To fix it, capture a WORKING "
                          "master's Connect to this drive and compare the DCE/RPC "
                          "header (object UUID, interface version).")

    def _build_expected_slots(self, dc):
        """Build the full ExpectedSubmodule slot list for the AR, matching the
        device's real topology, and return (io_slots, do_map).

        io_slots (list of rpc.IOSlot):
          - slot 0: Device Access Point head + PDEV interface/port sub-modules
          - slot N (per Drive Object, N=1..): Module Access Point (sub-slot 1),
            empty sub-module (sub-slot 2, when the module allows it) and the
            selected telegram (sub-slot 3).
        do_map (list of (slot, subslot, out_len, in_len)): the data-bearing
          telegram sub-slots, used by the cyclic read/write path.

        Driven by the device's GSDML for the selected variant. Drive Objects are
        renumbered to consecutive slots 1..N (SINAMICS places the first DO at
        slot 1). Falls back to a DAP-head + telegram-only layout if the GSDML
        can't drive it (e.g. a non-SINAMICS device or a missing file); a real
        device may reject that fallback, which is logged."""
        from profinet import rpc
        dos = dc.effective_drive_objects()
        path = getattr(dc, "gsdml_path", "") or ""
        dap_id = getattr(dc, "dap_id", "") or None
        try:
            if not path or not os.path.exists(path):
                raise RuntimeError("no GSDML file on record")
            from profinet.gsdml import load_gsdml
            gd = load_gsdml(path)
            if dap_id is not None and not any(d.id == dap_id for d in gd.daps):
                dap_id = None
            mod_id_by_ident = {m.module_ident: mid for mid, m in gd.modules.items()}
            sub_id_by_ident = {s.submodule_ident: sid
                               for sid, s in gd.submodule_catalog.items()}

            slot_assignment = {}
            submodule_assignment = {}
            do_map = []
            for i, d in enumerate(dos):
                slot = i + 1
                # A CU320-2 PN on its ONBOARD interface uses the '_INT' module/
                # telegram variants (e.g. IDM_VECTOR_51_INT 0x300100C4 /
                # IDS_TEL998_INT1 0x400003E6), not the CBE20 ones the dialog may
                # have picked. Override the idents to match the drive's real
                # commissioned config (from the cifX capture).
                m_ident = self._envint("PN_DRIVE_MODULE", _DRIVE_MODULE_IDENT)
                t_ident = self._envint("PN_DRIVE_TELEGRAM", _DRIVE_TELEGRAM_IDENT)
                mid = mod_id_by_ident.get(m_ident)
                tel = sub_id_by_ident.get(t_ident)
                if mid is None or tel is None:
                    raise RuntimeError(
                        f"module 0x{m_ident:08X}/telegram "
                        f"0x{t_ident:08X} not found in GSDML")
                slot_assignment[slot] = mid
                sa = {3: tel}
                # The real S120 Connect (cifX capture) includes the 'empty
                # sub-module' (0x1388) at sub-slot 2. Include it by default;
                # PN_INCLUDE_EMPTY=0 drops it.
                if self._envint("PN_INCLUDE_EMPTY", _INCLUDE_EMPTY_SUBMOD):
                    mod = gd.modules.get(mid)
                    allowed = getattr(mod, "allowed_subslots", {}) or {}
                    if "IDS_EMPTY" in allowed and 2 in allowed["IDS_EMPTY"]:
                        sa[2] = "IDS_EMPTY"
                submodule_assignment[slot] = sa
                do_map.append((slot, 3, d.output_length, d.input_length))

            slots = gd.build_io_slots(slot_assignment=slot_assignment,
                                      submodule_assignment=submodule_assignment,
                                      dap_id=dap_id)
            io_slots = [rpc.IOSlot(slot=s.slot, subslot=s.subslot,
                                   input_length=s.input_length,
                                   output_length=s.output_length,
                                   module_ident=s.module_ident,
                                   submodule_ident=s.submodule_ident)
                        for s in slots]
            self._log(f"[PN] AR topology from GSDML: {len(io_slots)} sub-module(s) "
                      f"for {len(dos)} Drive Object(s), variant {dap_id or 'default'}")
            return io_slots, do_map
        except Exception as e:
            self._log(f"[PN] WARN: could not build full AR topology from GSDML "
                      f"({e}); using DAP-head + telegram fallback — a real device "
                      f"may reject this.")
            head = self._dap_head_slots(dc)
            do_slots = [rpc.IOSlot(slot=d.slot, subslot=d.subslot,
                                   input_length=d.input_length,
                                   output_length=d.output_length,
                                   module_ident=d.module_ident,
                                   submodule_ident=d.submodule_ident)
                        for d in dos]
            do_map = [(d.slot, d.subslot, d.output_length, d.input_length)
                      for d in dos]
            return head + do_slots, do_map

    def _dap_head_slots(self, dc):
        """Slot-0 IOSlots (DAP head + PDEV interface/ports) for the device's
        selected variant, read from its GSDML. Returns [] if the GSDML can't be
        read. Used by the fallback path in _build_expected_slots."""
        from profinet import rpc
        path = getattr(dc, "gsdml_path", "") or ""
        dap_id = getattr(dc, "dap_id", "") or None
        if not path or not os.path.exists(path):
            return []
        try:
            from profinet.gsdml import load_gsdml
            gdev = load_gsdml(path)
            if dap_id is not None and not any(d.id == dap_id for d in gdev.daps):
                dap_id = None
            slots = [s for s in gdev.build_io_slots(dap_id=dap_id) if s.slot == 0]
            return [rpc.IOSlot(slot=s.slot, subslot=s.subslot,
                               input_length=s.input_length,
                               output_length=s.output_length,
                               module_ident=s.module_ident,
                               submodule_ident=s.submodule_ident)
                    for s in slots]
        except Exception:
            return []

    def dcp_discover(self, adapter: str, timeout_ms: int = 2000) -> List[ScanResult]:
        if not self._ok:
            return []
        adapter = self.resolve_adapter(adapter)
        out: List[ScanResult] = []
        try:
            from profinet import dcp
            from profinet.util import ethernet_socket, get_mac
            sock = ethernet_socket(adapter, 3)
            try:
                src = get_mac(self._macname(adapter))
                dcp.send_discover(sock, src)
                resp = dcp.read_response(sock, src, timeout_sec=max(1, timeout_ms // 1000))
                for mac, blocks in resp.items():
                    d = dcp.DCPDeviceDescription(mac, blocks)
                    mac_raw = (bytes(int(x, 16) for x in d.mac.split(":"))
                               if d.mac else b"\x00" * 6)
                    out.append(ScanResult(
                        name_of_station=(d.name or "").encode(),
                        ip_str=(d.ip or "").encode(),
                        mac=mac_raw,
                        netmask=getattr(d, "netmask", "") or "",
                        gateway=getattr(d, "gateway", "") or "",
                        vendor_name=getattr(d, "vendor_name", "") or "",
                        vendor_id=getattr(d, "vendor_id", 0) or 0,
                        device_id=getattr(d, "device_id", 0) or 0))
            finally:
                sock.close()
        except Exception as e:
            self._log(f"[PN] DCP discover error: {e}")
        return out

    # ── DCP actions (Network Discovery window) ──────────────────────────────
    def _dcp_socket(self, adapter: str):
        """Open a raw socket + fetch controller MAC for a DCP action."""
        from profinet.util import ethernet_socket, get_mac
        adapter = self.resolve_adapter(adapter)
        sock = ethernet_socket(adapter, 3)
        return sock, get_mac(self._macname(adapter))

    def dcp_set_name(self, adapter: str, mac: str, name: str) -> bool:
        if not self._ok:
            return False
        try:
            from profinet import dcp
            sock, src = self._dcp_socket(adapter)
            try:
                ok = dcp.set_param(sock, src, mac.lower(), "name", name)
            finally:
                sock.close()
            self._log(f"[PN] DCP SetName '{name}' -> {mac}: {'OK' if ok else 'no response'}")
            return bool(ok)
        except Exception as e:
            self._log(f"[PN] DCP SetName error: {e}")
            return False

    def dcp_set_ip(self, adapter: str, mac: str, ip: str,
                   subnet: str, gateway: str, permanent: bool = True) -> bool:
        if not self._ok:
            return False
        try:
            from profinet import dcp
            sock, src = self._dcp_socket(adapter)
            try:
                ok = dcp.set_ip(sock, src, mac.lower(), ip, subnet, gateway,
                                permanent=permanent)
            finally:
                sock.close()
            self._log(f"[PN] DCP SetIP {ip} -> {mac}: {'OK' if ok else 'no response'}")
            return bool(ok)
        except Exception as e:
            self._log(f"[PN] DCP SetIP error: {e}")
            return False

    def dcp_flash_led(self, adapter: str, mac: str, duration_ms: int = 3000) -> bool:
        if not self._ok:
            return False
        try:
            from profinet import dcp
            sock, src = self._dcp_socket(adapter)
            try:
                ok = dcp.signal_device(sock, src, mac.lower(), duration_ms=duration_ms)
            finally:
                sock.close()
            self._log(f"[PN] DCP Flash LED -> {mac}: {'OK' if ok else 'no response'}")
            return bool(ok)
        except Exception as e:
            self._log(f"[PN] DCP Flash LED error: {e}")
            return False

    # ── cyclic IO ───────────────────────────────────────────────────────────
    def write_outputs(self, idx: int, stw1: int, nsoll: int):
        ds = self._get(idx)
        if not ds or not ds.connected or not ds.cyclic:
            return
        ds.stw1, ds.nsoll = stw1, nsoll
        try:
            out_len = max(4, ds.config.output_length)
            data = struct.pack(">HH", stw1 & 0xFFFF, nsoll & 0xFFFF)
            if out_len > 4:
                data = data + b"\x00" * (out_len - 4)
            ds.cyclic.set_output_data(ds.slot, ds.subslot, data)
        except Exception as e:
            self._log(f"[PN] write_outputs error: {e}")

    def write_raw_outputs(self, idx: int, data: bytes):
        ds = self._get(idx)
        if not ds or not ds.connected or not ds.cyclic:
            return
        try:
            data = bytes(data)
            if not ds.dos:
                ds.cyclic.set_output_data(ds.slot, ds.subslot, data)
                return
            off = 0
            for (slot, subslot, out_len, _in_len) in ds.dos:
                if out_len <= 0:
                    continue
                chunk = data[off:off + out_len].ljust(out_len, b"\x00")
                ds.cyclic.set_output_data(slot, subslot, chunk)
                off += out_len
        except Exception as e:
            pass  # hot loop — do not spam the log (state shown via get_stats)

    def read_inputs(self, idx: int):
        ds = self._get(idx)
        if not ds or not ds.connected or not ds.cyclic:
            return (ds.zsw1, ds.nist) if ds else (0, 0)
        try:
            data = ds.cyclic.get_input_data(ds.slot, ds.subslot)
            if data and len(data) >= 4:
                ds.zsw1, ds.nist = struct.unpack_from(">HH", data, 0)
        except Exception as e:
            self._log(f"[PN] read_inputs error: {e}")
        return ds.zsw1, ds.nist

    def read_raw_inputs(self, idx: int, length: int) -> bytes:
        ds = self._get(idx)
        if not ds or not ds.connected or not ds.cyclic:
            return bytes(length)
        try:
            if not ds.dos:
                data = ds.cyclic.get_input_data(ds.slot, ds.subslot)
                return (bytes(data[:length]).ljust(length, b"\x00")
                        if data else bytes(length))
            out = bytearray()
            for (slot, subslot, _out_len, in_len) in ds.dos:
                if in_len <= 0:
                    continue
                data = ds.cyclic.get_input_data(slot, subslot) or b""
                out += bytes(data[:in_len]).ljust(in_len, b"\x00")
            return bytes(out[:length]).ljust(length, b"\x00")
        except Exception as e:
            pass  # hot loop — do not spam the log
        return bytes(length)

    # ── queries ─────────────────────────────────────────────────────────────
    def is_connected(self, idx: int) -> bool:
        ds = self._get(idx)
        return bool(ds and ds.connected)

    def device_count(self) -> int:
        with self._lock:
            return len(self._devices)

    def device_state(self, idx: int) -> Optional[DeviceState]:
        return self._get(idx)

    def get_stats(self, idx: int) -> Optional[Stats]:
        ds = self._get(idx)
        if not ds:
            return None
        st = Stats(connected=1 if ds.connected else 0)
        c = ds.cyclic
        if c is not None:
            cs = getattr(c, "stats", None)
            st.cyclic_running = 1 if getattr(c, "is_running", False) else 0
            if cs is not None:
                st.frames_sent = getattr(cs, "frames_sent", 0)
                st.frames_received = getattr(cs, "frames_received", 0)
                st.missed_cycles = getattr(cs, "frames_missed", 0)
                st.frames_invalid = getattr(cs, "frames_invalid", 0)
                st.cycle_counter = getattr(cs, "frames_sent", 0)
                # cyclic latency / jitter
                st.last_cycle_us = int(getattr(cs, "last_cycle_time_us", 0) or 0)
                st.min_cycle_us = int(getattr(cs, "min_cycle_time_us", 0) or 0)
                st.max_cycle_us = int(getattr(cs, "max_cycle_time_us", 0) or 0)
                st.max_jitter_us = int(getattr(cs, "max_jitter_us", 0) or 0)
                st.consecutive_timeouts = int(getattr(cs, "consecutive_timeouts", 0) or 0)
                cnt = getattr(cs, "_cycle_count", 0) or 0
                st.avg_cycle_us = (getattr(cs, "_cycle_time_sum_us", 0) / cnt) if cnt else 0.0
        return st

    def stop(self):
        with self._lock:
            devices = list(self._devices)
            self._devices = []
        for ds in devices:
            try:
                if ds.cyclic is not None:
                    ds.cyclic.stop()
            except Exception:
                pass
            try:
                if ds.conn is not None:
                    ds.conn.close()
            except Exception:
                pass
            ds.connected = False
        if devices:
            self._log("[PN] Stopped all devices")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _get(self, idx: int) -> Optional[DeviceState]:
        with self._lock:
            if 0 <= idx < len(self._devices):
                return self._devices[idx]
            return None

    @staticmethod
    def _macname(adapter: str) -> str:
        """profinet-py's get_mac() cannot match a full '\\Device\\NPF_{GUID}'
        path (it matches friendly name / description / bare {GUID}). Reduce an
        NPF path to the bare {GUID} so get_mac succeeds; pass anything else
        (Linux 'eth0', friendly names) through unchanged."""
        if sys.platform == "win32":
            m = _GUID_RE.search(adapter or "")
            if m:
                return "{" + m.group(0) + "}"
        return adapter

    @staticmethod
    def mac_bytes_to_str(mac) -> str:
        return ":".join(f"{b:02X}" for b in mac)
