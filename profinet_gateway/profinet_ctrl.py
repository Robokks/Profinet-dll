"""profinet_ctrl.py — ctypes wrapper around profinet.dll / libprofinet.so"""

import ctypes
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Callable

# ── Locate the DLL/SO ────────────────────────────────────────────────────────
def _find_lib() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = []
    if sys.platform == "win32":
        candidates = [
            os.path.join(base, "dist", "profinet.dll"),
            os.path.join(base, "profinet.dll"),
            "profinet.dll",
        ]
    else:
        candidates = [
            os.path.join(base, "dist", "libprofinet.so"),
            os.path.join(base, "libprofinet.so"),
            "./libprofinet.so",
        ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return candidates[0]   # will fail with clear error on load

# ── ctypes structures (must match profinet_api.h) ────────────────────────────
class PN_DeviceInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("mac",             ctypes.c_uint8  * 6),
        ("_pad",            ctypes.c_uint8  * 2),
        ("ip_str",          ctypes.c_char   * 16),
        ("name_of_station", ctypes.c_char   * 240),
        ("vendor_id",       ctypes.c_uint16),
        ("device_id",       ctypes.c_uint16),
        ("order_id",        ctypes.c_char   * 64),
    ]

class PN_ARConfig(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("device_mac",        ctypes.c_uint8  * 6),
        ("_pad",              ctypes.c_uint8  * 2),
        ("device_ip",         ctypes.c_char   * 16),
        ("send_clock_factor", ctypes.c_uint16),
        ("reduction_ratio",   ctypes.c_uint8),
        ("_pad2",             ctypes.c_uint8),
        ("watchdog_factor",   ctypes.c_uint16),
        ("_pad3",             ctypes.c_uint8  * 2),
        ("api",               ctypes.c_uint32),
        ("slot",              ctypes.c_uint16),
        ("subslot",           ctypes.c_uint16),
        ("module_ident",      ctypes.c_uint32),
        ("submodule_ident",   ctypes.c_uint32),
    ]

class PN_Stats(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("frames_sent",       ctypes.c_uint64),
        ("frames_received",   ctypes.c_uint64),
        ("missed_cycles",     ctypes.c_uint64),
        ("watchdog_timeouts", ctypes.c_uint64),
        ("cycle_counter",     ctypes.c_uint32),
        ("connected",         ctypes.c_uint8),
        ("cyclic_running",    ctypes.c_uint8),
        ("_pad",              ctypes.c_uint8  * 2),
    ]

# ── Return codes ──────────────────────────────────────────────────────────────
PN_OK = 0
RC_NAMES = {
     0: "PN_OK",
    -1: "PN_ERR_INVALID_PARAM",
    -2: "PN_ERR_NOT_INITIALIZED",
    -4: "PN_ERR_NO_ADAPTER",
    -5: "PN_ERR_PCAP_OPEN",
    -8: "PN_ERR_DCP_TIMEOUT",
    -9: "PN_ERR_DCP_SET_FAIL",
   -10: "PN_ERR_RPC_CONNECT",
   -15: "PN_ERR_NOT_CONNECTED",
   -99: "PN_ERR_INTERNAL",
}

def rc_str(rc: int) -> str:
    return RC_NAMES.get(rc, f"ERR({rc})")

# ── Per-device runtime state ───────────────────────────────────────────────────
@dataclass
class DeviceState:
    config: object          # DeviceConfig from config.py
    handle: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    connected: bool = False
    zsw1: int = 0
    nist: int = 0
    stw1: int = 0
    nsoll: int = 0
    error: str = ""

# ── ProfinetCtrl ──────────────────────────────────────────────────────────────
class ProfinetCtrl:
    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log = log_cb or print
        self._lib = None
        self._devices: List[DeviceState] = []
        self._lock = threading.Lock()
        self._load_lib()

    def _load_lib(self):
        lib_path = _find_lib()
        try:
            if sys.platform == "win32":
                self._lib = ctypes.WinDLL(lib_path)
            else:
                self._lib = ctypes.CDLL(lib_path)
            self._bind()
            self._log(f"[PN] Loaded {lib_path}")
        except OSError as e:
            self._log(f"[PN] ERROR loading library: {e}")
            self._lib = None

    def _bind(self):
        L = self._lib
        def fn(name, res, args):
            try:
                f = getattr(L, name)
                f.restype = res
                f.argtypes = args
                return f
            except AttributeError:
                self._log(f"[PN] WARN: symbol {name} not found")
                return None

        AdapterArr = (ctypes.c_char * 256) * 16

        self._PN_GetVersion       = fn("PN_GetVersion",       None,
                                       [ctypes.c_char_p, ctypes.c_uint32])
        self._PN_EnumAdapters     = fn("PN_EnumerateAdapters", ctypes.c_int32,
                                       [AdapterArr, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32)])
        self._PN_GetAdapterName   = fn("PN_GetAdapterName",   ctypes.c_int32,
                                       [ctypes.c_int32, ctypes.c_char_p, ctypes.c_int32])
        self._PN_Initialize       = fn("PN_Initialize",       ctypes.c_int32,
                                       [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)])
        self._PN_Shutdown         = fn("PN_Shutdown",         ctypes.c_int32,
                                       [ctypes.c_void_p])
        self._PN_GetLastError     = fn("PN_GetLastError",     ctypes.c_int32,
                                       [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32])
        self._PN_DCPDiscover      = fn("PN_DCPDiscover",      ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.POINTER(PN_DeviceInfo),
                                        ctypes.c_int32,
                                        ctypes.POINTER(ctypes.c_int32),
                                        ctypes.c_uint32])
        self._PN_DCPSetIP         = fn("PN_DCPSetIP",         ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.c_uint8 * 6,
                                        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p])
        self._PN_DCPSetName       = fn("PN_DCPSetName",       ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.c_uint8 * 6,
                                        ctypes.c_char_p])
        self._PN_Connect          = fn("PN_Connect",          ctypes.c_int32,
                                       [ctypes.c_void_p, ctypes.POINTER(PN_ARConfig)])
        self._PN_Disconnect       = fn("PN_Disconnect",       ctypes.c_int32,
                                       [ctypes.c_void_p])
        self._PN_IsConnected      = fn("PN_IsConnected",      ctypes.c_int32,
                                       [ctypes.c_void_p])
        self._PN_DriveSetpoint    = fn("PN_DriveSetpoint",    ctypes.c_int32,
                                       [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16])
        self._PN_DriveStatus      = fn("PN_DriveStatus",      ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint16),
                                        ctypes.POINTER(ctypes.c_uint16)])
        self._PN_WriteOutputs     = fn("PN_WriteOutputs",     ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint8),
                                        ctypes.c_uint16])
        self._PN_ReadInputs       = fn("PN_ReadInputs",       ctypes.c_int32,
                                       [ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint8),
                                        ctypes.c_uint16])
        self._PN_GetStats         = fn("PN_GetStats",         ctypes.c_int32,
                                       [ctypes.c_void_p, ctypes.POINTER(PN_Stats)])

    def get_version(self) -> str:
        if not self._lib or not self._PN_GetVersion:
            return "?"
        buf = ctypes.create_string_buffer(64)
        self._PN_GetVersion(buf, 64)
        return buf.value.decode(errors="replace")

    def enumerate_adapters(self) -> List[str]:
        if not self._lib or not self._PN_EnumAdapters:
            return []
        AdapterArr = (ctypes.c_char * 256) * 16
        names = AdapterArr()
        count = ctypes.c_int32(0)
        self._PN_EnumAdapters(names, 16, ctypes.byref(count))
        return [bytes(names[i]).rstrip(b"\x00").decode(errors="replace")
                for i in range(count.value)]

    def start(self, adapter: str, dev_configs: list) -> bool:
        """Initialize one handle per device config, then configure each."""
        if not self._lib:
            return False
        self.stop()

        # Diagnose adapter before trying to open it
        available = self.enumerate_adapters()
        if adapter and adapter not in available:
            self._log(f"[PN] WARNING: saved adapter not found in Npcap list!")
            self._log(f"[PN]   Saved  : {adapter}")
            self._log(f"[PN]   Available ({len(available)}): "
                      + (", ".join(available) if available else "(none — Npcap not installed?)"))
            self._log("[PN] Open Configuration, re-select the adapter and Apply & Restart.")

        with self._lock:
            self._devices = []
        ok = True
        for dc in dev_configs:
            ds = DeviceState(config=dc)
            handle = ctypes.c_void_p(0)
            adapter_b = adapter.encode() if adapter else None
            rc = self._PN_Initialize(adapter_b, ctypes.byref(handle))
            if rc != PN_OK:
                ds.error = rc_str(rc)
                self._log(f"[PN] PN_Initialize failed for '{dc.station_name}': {rc_str(rc)}")
                if rc == -5:  # PN_ERR_PCAP_OPEN
                    self._log(f"[PN]   → pcap_open_live failed on adapter: {adapter!r}")
                    self._log(f"[PN]   → Run app as Administrator OR reinstall Npcap")
                    self._log(f"[PN]       without 'Restrict to Admins' option.")
                ok = False
            else:
                ds.handle = handle
                self._log(f"[PN] Initialized handle for {dc.station_name}")
            with self._lock:
                self._devices.append(ds)
        return ok

    def _get_last_error(self, handle) -> str:
        if not self._PN_GetLastError or not handle:
            return ""
        buf = ctypes.create_string_buffer(512)
        self._PN_GetLastError(handle, buf, 512)
        return buf.value.decode(errors="replace")

    def configure_device(self, idx: int) -> bool:
        """DCP SetName + SetIP + RPC Connect for device at index idx."""
        with self._lock:
            if idx >= len(self._devices):
                return False
            ds = self._devices[idx]
        dc = ds.config
        if not ds.handle:
            return False

        mac_bytes = self._str_to_mac(dc.mac)
        mac_arr = (ctypes.c_uint8 * 6)(*mac_bytes)

        # DCP SetName
        if dc.station_name and dc.mac:
            rc = self._PN_DCPSetName(ds.handle, mac_arr, dc.station_name.encode())
            if rc != PN_OK:
                self._log(f"[PN] DCPSetName failed: {rc_str(rc)}")

        # DCP SetIP
        if dc.ip and dc.mac:
            rc = self._PN_DCPSetIP(ds.handle, mac_arr,
                                   dc.ip.encode(), dc.subnet.encode(), dc.gateway.encode())
            if rc != PN_OK:
                self._log(f"[PN] DCPSetIP failed: {rc_str(rc)}")

        # RPC Connect
        ar = PN_ARConfig()
        for i, b in enumerate(mac_bytes):
            ar.device_mac[i] = b
        ar.device_ip         = dc.ip.encode()
        ar.send_clock_factor = 128
        ar.reduction_ratio   = 1
        ar.watchdog_factor   = 3
        ar.api               = 0
        ar.slot              = dc.slot
        ar.subslot           = dc.subslot
        ar.module_ident      = dc.module_ident
        ar.submodule_ident   = dc.submodule_ident

        rc = self._PN_Connect(ds.handle, ctypes.byref(ar))
        if rc != PN_OK:
            ds.error = rc_str(rc)
            ds.connected = False
            self._log(f"[PN] Connect failed for {dc.station_name}: {rc_str(rc)} — {self._get_last_error(ds.handle)}")
            return False

        ds.connected = True
        ds.error = ""
        self._log(f"[PN] Connected to {dc.station_name} ({dc.ip})")
        return True

    def dcp_discover(self, adapter: str, timeout_ms: int = 2000) -> List[PN_DeviceInfo]:
        """Scan for Profinet devices on the network."""
        if not self._lib or not self._PN_DCPDiscover:
            return []
        handle = ctypes.c_void_p(0)
        adapter_b = adapter.encode() if adapter else None
        rc = self._PN_Initialize(adapter_b, ctypes.byref(handle))
        if rc != PN_OK or not handle.value:
            return []
        devices = (PN_DeviceInfo * 32)()
        count = ctypes.c_int32(0)
        self._PN_DCPDiscover(handle, devices, 32, ctypes.byref(count), timeout_ms)
        result = [devices[i] for i in range(count.value)]
        self._PN_Shutdown(handle)
        return result

    def write_outputs(self, idx: int, stw1: int, nsoll: int):
        with self._lock:
            if idx >= len(self._devices):
                return
            ds = self._devices[idx]
        if not ds.handle or not ds.connected:
            return
        ds.stw1 = stw1
        ds.nsoll = nsoll
        self._PN_DriveSetpoint(ds.handle, stw1, nsoll)

    def write_raw_outputs(self, idx: int, data: bytes):
        with self._lock:
            if idx >= len(self._devices):
                return
            ds = self._devices[idx]
        if not ds.handle or not ds.connected:
            return
        arr = (ctypes.c_uint8 * len(data))(*data)
        self._PN_WriteOutputs(ds.handle, arr, len(data))

    def read_inputs(self, idx: int):
        """Returns (zsw1, nist) or (0, 0)."""
        with self._lock:
            if idx >= len(self._devices):
                return 0, 0
            ds = self._devices[idx]
        if not ds.handle or not ds.connected:
            return 0, 0
        zsw1 = ctypes.c_uint16(0)
        nist = ctypes.c_uint16(0)
        rc = self._PN_DriveStatus(ds.handle, ctypes.byref(zsw1), ctypes.byref(nist))
        if rc == PN_OK:
            ds.zsw1 = zsw1.value
            ds.nist  = nist.value
        return ds.zsw1, ds.nist

    def read_raw_inputs(self, idx: int, length: int) -> bytes:
        with self._lock:
            if idx >= len(self._devices):
                return bytes(length)
            ds = self._devices[idx]
        if not ds.handle or not ds.connected:
            return bytes(length)
        arr = (ctypes.c_uint8 * length)()
        rc = self._PN_ReadInputs(ds.handle, arr, length)
        if rc == PN_OK:
            return bytes(arr)
        return bytes(length)

    def is_connected(self, idx: int) -> bool:
        with self._lock:
            if idx >= len(self._devices):
                return False
            return self._devices[idx].connected

    def device_count(self) -> int:
        with self._lock:
            return len(self._devices)

    def device_state(self, idx: int) -> Optional[DeviceState]:
        with self._lock:
            if idx < len(self._devices):
                return self._devices[idx]
            return None

    def get_stats(self, idx: int) -> Optional[PN_Stats]:
        with self._lock:
            if idx >= len(self._devices):
                return None
            ds = self._devices[idx]
        if not ds.handle:
            return None
        stats = PN_Stats()
        rc = self._PN_GetStats(ds.handle, ctypes.byref(stats))
        return stats if rc == PN_OK else None

    def stop(self):
        with self._lock:
            devices = list(self._devices)
            self._devices = []
        for ds in devices:
            if ds.handle and ds.handle.value:
                if self._PN_Disconnect:
                    self._PN_Disconnect(ds.handle)
                if self._PN_Shutdown:
                    self._PN_Shutdown(ds.handle)
                ds.handle = ctypes.c_void_p(0)
                ds.connected = False
        self._log("[PN] Stopped all devices")

    @staticmethod
    def _str_to_mac(mac_str: str):
        """Convert 'AA:BB:CC:DD:EE:FF' to list of 6 ints."""
        try:
            return [int(x, 16) for x in mac_str.split(":")]
        except Exception:
            return [0, 0, 0, 0, 0, 0]

    @staticmethod
    def mac_bytes_to_str(mac) -> str:
        return ":".join(f"{b:02X}" for b in mac)
