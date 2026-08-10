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
# milliseconds equals reduction_ratio. 16 ms is a safe default for a gateway.
_SEND_CLOCK_FACTOR = 32
_CYCLE_MS = 16
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

            # 2. AR connect with one IOCR slot per Drive Object in the rack
            dos = dc.effective_drive_objects()
            io_slots = [rpc.IOSlot(slot=d.slot, subslot=d.subslot,
                                   input_length=d.input_length,
                                   output_length=d.output_length,
                                   module_ident=d.module_ident,
                                   submodule_ident=d.submodule_ident)
                        for d in dos]
            ds.dos = [(d.slot, d.subslot, d.output_length, d.input_length) for d in dos]
            ds.slot, ds.subslot = dos[0].slot, dos[0].subslot
            conn = rpc.RPCCon(info)
            setup = rpc.IOCRSetup(slots=io_slots,
                                  send_clock_factor=_SEND_CLOCK_FACTOR,
                                  reduction_ratio=_CYCLE_MS,
                                  watchdog_factor=_WATCHDOG_FACTOR,
                                  data_hold_factor=_WATCHDOG_FACTOR)
            result = conn.connect(src, iocr_setup=setup)
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
