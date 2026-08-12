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
_SEND_CLOCK_FACTOR = 32
_CYCLE_MS = 8
_WATCHDOG_FACTOR = 6


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
            setup = rpc.IOCRSetup(slots=io_slots,
                                  send_clock_factor=_SEND_CLOCK_FACTOR,
                                  reduction_ratio=_CYCLE_MS,
                                  watchdog_factor=_WATCHDOG_FACTOR,
                                  data_hold_factor=_WATCHDOG_FACTOR)

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

            # 3. Cyclic controller
            in_cfg, out_cfg = build_iocr_configs(
                io_slots, result.input_frame_id, result.output_frame_id,
                send_clock_factor=_SEND_CLOCK_FACTOR, reduction_ratio=_CYCLE_MS,
                watchdog_factor=_WATCHDOG_FACTOR)
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

    def _patch_alarm_cr(self, conn):
        """Adjust the AR's AlarmCR block to values a real SINAMICS accepts.
        profinet-py sends bare minimums (RTATimeoutFactor=1, RTARetries=3,
        MaxAlarmDataLength=200, AlarmCRProperties=0) that the drive rejects
        (Connect error AlarmCR). Every field is overridable by env var so the
        working combination can be found without recompiling:

          PN_ALARM_PROPS    AlarmCRProperties (default 0; bit0=priority, bit1=transport)
          PN_ALARM_MAXDATA  MaxAlarmDataLength (default 1432 = spec max)
          PN_ALARM_RTATF    RTATimeoutFactor   (default 1)
          PN_ALARM_RTAR     RTARetries         (default 3)
        """
        try:
            from profinet.rpc import PNAlarmCRBlockReq as A
            props = int(os.environ.get("PN_ALARM_PROPS", "0"), 0)
            maxdata = int(os.environ.get("PN_ALARM_MAXDATA", "1432"), 0)
            rtatf = int(os.environ.get("PN_ALARM_RTATF",
                        str(A.DEFAULT_RTA_TIMEOUT_FACTOR)), 0)
            rtar = int(os.environ.get("PN_ALARM_RTAR",
                       str(A.DEFAULT_RTA_RETRIES)), 0)
            # These are read at build time from the class, so patching them here
            # changes what _build_alarm_cr_block() emits.
            A.DEFAULT_MAX_ALARM_DATA_LENGTH = maxdata
            A.DEFAULT_RTA_TIMEOUT_FACTOR = rtatf
            A.DEFAULT_RTA_RETRIES = rtar
            priority = props & 0x1
            transport = (props >> 1) & 0x1
            orig = conn._build_alarm_cr_block
            conn._build_alarm_cr_block = (
                lambda t=transport, p=priority: orig(t, p))
            self._log(f"[PN] AlarmCR: props=0x{props:04X} maxdata={maxdata} "
                      f"rtatf={rtatf} rtar={rtar}")
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
                mid = mod_id_by_ident.get(d.module_ident)
                tel = sub_id_by_ident.get(d.submodule_ident)
                if mid is None or tel is None:
                    raise RuntimeError(
                        f"module 0x{d.module_ident:08X}/telegram "
                        f"0x{d.submodule_ident:08X} not found in GSDML")
                slot_assignment[slot] = mid
                sa = {3: tel}
                mod = gd.modules.get(mid)
                allowed = getattr(mod, "allowed_subslots", {}) or {}
                # Fill sub-slot 2 with the 'empty sub-module' when the module
                # permits it there (SINAMICS DOs, matching SYCON.net).
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
