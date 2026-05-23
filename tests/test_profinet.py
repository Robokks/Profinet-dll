"""
test_profinet.py — Python ctypes test for profinet.dll / libprofinet.so

Usage:
  # Linux (no simulator needed for basic tests):
  sudo python3 tests/test_profinet.py

  # Linux (with vfd_simulator running — full test):
  sudo python3 tests/test_profinet.py --adapter eth0 --sim

  # Windows (run as Administrator):
  python tests\test_profinet.py
  python tests\test_profinet.py --adapter "\\Device\\NPF_{GUID}" --sim

  # Specify a custom DLL/SO path:
  python3 tests/test_profinet.py --lib /path/to/libprofinet.so

Options:
  --lib PATH       Path to profinet.dll or libprofinet.so
  --adapter NAME   Adapter name to use (default: first found)
  --sim            Run full simulator integration tests (requires vfd_simulator)
  --ip IP          IP to assign via DCP SetIP  (default: 192.168.1.50)
  --name NAME      Station name to assign      (default: test-drive)
  -v / --verbose   Show extra detail
"""

import sys
import os
import ctypes
import ctypes.util
import struct
import platform
import argparse
import time

# ──────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Profinet DLL Python tester")
parser.add_argument("--lib",     default=None,           help="Path to DLL/SO")
parser.add_argument("--adapter", default=None,           help="Adapter name")
parser.add_argument("--sim",     action="store_true",    help="Run simulator tests")
parser.add_argument("--ip",      default="192.168.1.50", help="IP for DCP SetIP test")
parser.add_argument("--name",    default="test-drive",   help="Name for DCP SetName test")
parser.add_argument("-v","--verbose", action="store_true")
args = parser.parse_args()

IS_WIN = sys.platform == "win32"

# ──────────────────────────────────────────────────────────────────────────────
# Locate the library
# ──────────────────────────────────────────────────────────────────────────────
def find_library():
    if args.lib:
        return args.lib
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    candidates = []
    if IS_WIN:
        candidates = [
            os.path.join(repo_root, "dist", "profinet.dll"),
            os.path.join(repo_root, "build-win32", "profinet.dll"),
            "profinet.dll",
        ]
    else:
        candidates = [
            os.path.join(repo_root, "dist", "libprofinet.so"),
            os.path.join(repo_root, "build-linux", "libprofinet.so"),
            "./libprofinet.so",
        ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None

# ──────────────────────────────────────────────────────────────────────────────
# ctypes structures  (must match profinet_api.h exactly)
# ──────────────────────────────────────────────────────────────────────────────
class PN_DeviceInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("mac",              ctypes.c_uint8 * 6),
        ("_pad",             ctypes.c_uint8 * 2),
        ("ip_str",           ctypes.c_char  * 16),
        ("name_of_station",  ctypes.c_char  * 240),
        ("vendor_id",        ctypes.c_uint16),
        ("device_id",        ctypes.c_uint16),
        ("order_id",         ctypes.c_char  * 64),
    ]

class PN_ModuleDesc(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("module_ident",     ctypes.c_uint32),
        ("module_name",      ctypes.c_char  * 128),
        ("submodule_ident",  ctypes.c_uint32),
        ("submodule_name",   ctypes.c_char  * 128),
        ("input_length",     ctypes.c_uint16),
        ("output_length",    ctypes.c_uint16),
    ]

class PN_ARConfig(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("device_mac",         ctypes.c_uint8  * 6),
        ("_pad",               ctypes.c_uint8  * 2),
        ("device_ip",          ctypes.c_char   * 16),
        ("send_clock_factor",  ctypes.c_uint16),
        ("reduction_ratio",    ctypes.c_uint8),
        ("_pad2",              ctypes.c_uint8),
        ("watchdog_factor",    ctypes.c_uint16),
        ("_pad3",              ctypes.c_uint8  * 2),
        ("api",                ctypes.c_uint32),
        ("slot",               ctypes.c_uint16),
        ("subslot",            ctypes.c_uint16),
        ("module_ident",       ctypes.c_uint32),
        ("submodule_ident",    ctypes.c_uint32),
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

# ──────────────────────────────────────────────────────────────────────────────
# Return-code names
# ──────────────────────────────────────────────────────────────────────────────
RC_NAMES = {
     0: "PN_OK",
    -1: "PN_ERR_INVALID_PARAM",
    -2: "PN_ERR_NOT_INITIALIZED",
    -3: "PN_ERR_ALREADY_INIT",
    -4: "PN_ERR_NO_ADAPTER",
    -5: "PN_ERR_PCAP_OPEN",
    -6: "PN_ERR_GSDML_NOT_FOUND",
    -7: "PN_ERR_GSDML_PARSE",
    -8: "PN_ERR_DCP_TIMEOUT",
    -9: "PN_ERR_DCP_SET_FAIL",
   -10: "PN_ERR_RPC_CONNECT",
   -11: "PN_ERR_RPC_TIMEOUT",
   -12: "PN_ERR_CYCLIC_ALREADY",
   -13: "PN_ERR_CYCLIC_NOT_RUNNING",
   -14: "PN_ERR_BUFFER_TOO_SMALL",
   -15: "PN_ERR_NOT_CONNECTED",
   -99: "PN_ERR_INTERNAL",
}

def rc_str(rc):
    return RC_NAMES.get(rc, f"UNKNOWN({rc})")

# ──────────────────────────────────────────────────────────────────────────────
# Test harness
# ──────────────────────────────────────────────────────────────────────────────
passed = 0
failed = 0

def check(cond, label, detail=""):
    global passed, failed
    if cond:
        print(f"  \033[32mPASS\033[0m  {label}")
        passed += 1
    else:
        print(f"  \033[31mFAIL\033[0m  {label}" + (f"  ({detail})" if detail else ""))
        failed += 1
    return cond

def section(title):
    print(f"\n[{title}]")

# ──────────────────────────────────────────────────────────────────────────────
# Load the DLL
# ──────────────────────────────────────────────────────────────────────────────
section("0  Library Load")

lib_path = find_library()
check(lib_path is not None, "library file found",
      "use --lib /path/to/profinet.dll  or  libprofinet.so")
if lib_path is None:
    print("\nCannot continue — library not found.")
    sys.exit(1)

print(f"       Path: {lib_path}")

try:
    if IS_WIN:
        # 32-bit __stdcall DLL
        pn = ctypes.WinDLL(lib_path)
    else:
        pn = ctypes.CDLL(lib_path)
    loaded = True
except OSError as e:
    loaded = False
    pn = None
    print(f"       Error: {e}")

check(loaded, "ctypes loaded the library")
if not loaded:
    print("\nCannot continue — library failed to load.")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# Bind function signatures
# ──────────────────────────────────────────────────────────────────────────────
def bind(name, restype, argtypes):
    try:
        fn = getattr(pn, name)
        fn.restype  = restype
        fn.argtypes = argtypes
        return fn
    except AttributeError:
        print(f"  WARN  symbol not found: {name}")
        return None

PN_GetVersion       = bind("PN_GetVersion",       None,
                            [ctypes.c_char_p, ctypes.c_uint32])
PN_EnumerateAdapters= bind("PN_EnumerateAdapters", ctypes.c_int32,
                            [(ctypes.c_char * 256) * 8, ctypes.c_int32,
                             ctypes.POINTER(ctypes.c_int32)])
PN_GetAdapterName   = bind("PN_GetAdapterName",    ctypes.c_int32,
                            [ctypes.c_int32, ctypes.c_char_p, ctypes.c_int32])
PN_Initialize       = bind("PN_Initialize",        ctypes.c_int32,
                            [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)])
PN_Shutdown         = bind("PN_Shutdown",          ctypes.c_int32,
                            [ctypes.c_void_p])
PN_GetLastError     = bind("PN_GetLastError",      ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32])
PN_DCPDiscover      = bind("PN_DCPDiscover",       ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.POINTER(PN_DeviceInfo),
                             ctypes.c_int32,
                             ctypes.POINTER(ctypes.c_int32),
                             ctypes.c_uint32])
PN_GetDiscoveredDevice = bind("PN_GetDiscoveredDevice", ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.c_int32,
                             ctypes.POINTER(PN_DeviceInfo)])
PN_DCPSetIP         = bind("PN_DCPSetIP",          ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.c_uint8 * 6,
                             ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p])
PN_DCPSetName       = bind("PN_DCPSetName",        ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.c_uint8 * 6,
                             ctypes.c_char_p])
PN_IsConnected      = bind("PN_IsConnected",       ctypes.c_int32,
                            [ctypes.c_void_p])
PN_Connect          = bind("PN_Connect",           ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.POINTER(PN_ARConfig)])
PN_Disconnect       = bind("PN_Disconnect",        ctypes.c_int32,
                            [ctypes.c_void_p])
PN_DriveSetpoint    = bind("PN_DriveSetpoint",     ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16])
PN_DriveStatus      = bind("PN_DriveStatus",       ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint16),
                             ctypes.POINTER(ctypes.c_uint16)])
PN_WriteOutputs     = bind("PN_WriteOutputs",      ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint8),
                             ctypes.c_uint16])
PN_ReadInputs       = bind("PN_ReadInputs",        ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint8),
                             ctypes.c_uint16])
PN_GetStats         = bind("PN_GetStats",          ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.POINTER(PN_Stats)])
PN_ResetStats       = bind("PN_ResetStats",        ctypes.c_int32,
                            [ctypes.c_void_p])
PN_LoadGSDML        = bind("PN_LoadGSDML",         ctypes.c_int32,
                            [ctypes.c_void_p, ctypes.c_char_p])
PN_GetModuleList    = bind("PN_GetModuleList",     ctypes.c_int32,
                            [ctypes.c_void_p,
                             ctypes.POINTER(PN_ModuleDesc),
                             ctypes.c_int32,
                             ctypes.POINTER(ctypes.c_int32)])

# ──────────────────────────────────────────────────────────────────────────────
# Helper: get last error string
# ──────────────────────────────────────────────────────────────────────────────
def last_error(handle):
    if PN_GetLastError and handle:
        buf = ctypes.create_string_buffer(512)
        PN_GetLastError(handle, buf, 512)
        return buf.value.decode(errors="replace")
    return ""

# ──────────────────────────────────────────────────────────────────────────────
# Test 1: PN_GetVersion
# ──────────────────────────────────────────────────────────────────────────────
section("1  PN_GetVersion")

ver_buf = ctypes.create_string_buffer(64)
PN_GetVersion(ver_buf, 64)
ver_str = ver_buf.value.decode(errors="replace")
check(len(ver_str) > 0, "version string non-empty")
check("." in ver_str,   "version has dot separator (major.minor.patch)")
print(f"       Version: {ver_str}")

# ──────────────────────────────────────────────────────────────────────────────
# Test 2: PN_EnumerateAdapters
# ──────────────────────────────────────────────────────────────────────────────
section("2  PN_EnumerateAdapters")

AdapterArray = (ctypes.c_char * 256) * 8
names = AdapterArray()
count = ctypes.c_int32(0)
rc = PN_EnumerateAdapters(names, 8, ctypes.byref(count))
check(rc == 0,        "return code == PN_OK", rc_str(rc))
check(count.value > 0,"at least one adapter found")
print(f"       Found {count.value} adapter(s):")
adapter_names = []
for i in range(count.value):
    n = bytes(names[i]).rstrip(b"\x00").decode(errors="replace")
    adapter_names.append(n)
    print(f"         [{i}] {n}")

# Test PN_GetAdapterName (single accessor)
if count.value > 0:
    name_buf = ctypes.create_string_buffer(256)
    rc2 = PN_GetAdapterName(0, name_buf, 256)
    n0 = name_buf.value.decode(errors="replace")
    check(rc2 == 0 and len(n0) > 0, "PN_GetAdapterName(0) returns non-empty name")
    if args.verbose:
        print(f"       PN_GetAdapterName(0) = {n0}")

# Decide which adapter to use
chosen_adapter = args.adapter
if chosen_adapter is None and len(adapter_names) > 0:
    chosen_adapter = adapter_names[0]
print(f"\n       Using adapter: {chosen_adapter}")

# ──────────────────────────────────────────────────────────────────────────────
# Test 3: PN_Initialize / PN_Shutdown
# ──────────────────────────────────────────────────────────────────────────────
section("3  PN_Initialize / PN_Shutdown")

handle = ctypes.c_void_p(0)
adapter_bytes = chosen_adapter.encode() if chosen_adapter else None
rc = PN_Initialize(adapter_bytes, ctypes.byref(handle))
check(rc == 0,             "PN_Initialize returns PN_OK", rc_str(rc))
check(handle.value != 0,   "out_handle is non-NULL")

if rc != 0:
    print(f"       Error: {rc_str(rc)}")
    print("\n=== Results: {} passed, {} failed ===".format(passed, failed))
    sys.exit(1 if failed > 0 else 0)

# A second independent handle on same adapter is allowed (both open the pcap)
handle2 = ctypes.c_void_p(0)
rc2 = PN_Initialize(adapter_bytes, ctypes.byref(handle2))
check(rc2 == 0 and handle2.value != 0, "second PN_Initialize (independent handle) succeeds")
if handle2.value:
    PN_Shutdown(handle2)
    handle2 = ctypes.c_void_p(0)

# ──────────────────────────────────────────────────────────────────────────────
# Test 4: PN_GetLastError on fresh handle (should be empty / OK)
# ──────────────────────────────────────────────────────────────────────────────
section("4  PN_GetLastError")

err_buf = ctypes.create_string_buffer(512)
rc = PN_GetLastError(handle, err_buf, 512)
check(rc == 0, "PN_GetLastError returns PN_OK")
if args.verbose:
    print(f"       Last error: '{err_buf.value.decode(errors='replace')}'")

# ──────────────────────────────────────────────────────────────────────────────
# Test 5: PN_IsConnected (should be 0 before Connect)
# ──────────────────────────────────────────────────────────────────────────────
section("5  PN_IsConnected (before Connect)")

rc = PN_IsConnected(handle)
check(rc == 0, "PN_IsConnected returns 0 when not connected", f"got {rc}")

# ──────────────────────────────────────────────────────────────────────────────
# Test 6: PN_GetStats (before any cyclic)
# ──────────────────────────────────────────────────────────────────────────────
section("6  PN_GetStats (idle)")

stats = PN_Stats()
rc = PN_GetStats(handle, ctypes.byref(stats))
check(rc == 0,               "PN_GetStats returns PN_OK", rc_str(rc))
check(stats.connected == 0,  "connected == 0 when idle")
check(stats.cyclic_running == 0, "cyclic_running == 0 when idle")
if args.verbose:
    print(f"       frames_sent={stats.frames_sent} "
          f"frames_received={stats.frames_received} "
          f"missed={stats.missed_cycles}")

rc = PN_ResetStats(handle)
check(rc == 0, "PN_ResetStats returns PN_OK", rc_str(rc))

# ──────────────────────────────────────────────────────────────────────────────
# Test 7: PN_WriteOutputs / PN_ReadInputs (no connection → expect error)
# ──────────────────────────────────────────────────────────────────────────────
section("7  PN_WriteOutputs / PN_ReadInputs (not connected)")

data_out = (ctypes.c_uint8 * 4)(0x47, 0x00, 0x10, 0x00)
rc = PN_WriteOutputs(handle, data_out, 4)
check(rc != 0, "PN_WriteOutputs returns error when not connected", rc_str(rc))

data_in = (ctypes.c_uint8 * 4)()
rc = PN_ReadInputs(handle, data_in, 4)
check(rc != 0, "PN_ReadInputs returns error when not connected", rc_str(rc))

# ──────────────────────────────────────────────────────────────────────────────
# Test 8: PN_DriveStatus (not connected → expect error)
# ──────────────────────────────────────────────────────────────────────────────
section("8  PN_DriveSetpoint / PN_DriveStatus (not connected)")

rc = PN_DriveSetpoint(handle, 0x047F, 0x2000)
check(rc != 0, "PN_DriveSetpoint returns error when not connected", rc_str(rc))

zsw1 = ctypes.c_uint16(0)
nist = ctypes.c_uint16(0)
rc = PN_DriveStatus(handle, ctypes.byref(zsw1), ctypes.byref(nist))
check(rc != 0, "PN_DriveStatus returns error when not connected", rc_str(rc))

# ──────────────────────────────────────────────────────────────────────────────
# Test 9: PN_DCPDiscover (short window — may find 0 devices without simulator)
# ──────────────────────────────────────────────────────────────────────────────
section("9  PN_DCPDiscover (2s window)")

devices = (PN_DeviceInfo * 8)()
dev_count = ctypes.c_int32(0)
timeout = 2000 if args.sim else 500   # shorter timeout if not expecting simulator

print(f"       Waiting {timeout}ms for Profinet devices ...")
rc = PN_DCPDiscover(handle, devices, 8, ctypes.byref(dev_count), timeout)
check(rc == 0, "PN_DCPDiscover returns PN_OK", rc_str(rc))
print(f"       Found {dev_count.value} device(s)")

found_sim = False
sim_device = None

for i in range(dev_count.value):
    d = devices[i]
    mac_str = ":".join(f"{b:02X}" for b in d.mac)
    ip      = d.ip_str.decode(errors="replace")
    name    = d.name_of_station.decode(errors="replace")
    vid     = d.vendor_id
    did     = d.device_id
    print(f"         [{i}] MAC={mac_str}  IP={ip}  name='{name}'  "
          f"VendorID=0x{vid:04X}  DeviceID=0x{did:04X}")
    if name in ("vfd-simulator", args.name, "test-drive"):
        found_sim = True
        sim_device = d

if args.sim:
    check(dev_count.value > 0, "at least one device found (simulator must be running)")
    check(found_sim, "vfd-simulator device found")
else:
    # Without simulator we can't require a device — just check the call succeeded
    check(rc == 0, "DCP scan completed without error (no simulator required)")

# ──────────────────────────────────────────────────────────────────────────────
# Tests 10-12 require the simulator — skip if --sim not given or not found
# ──────────────────────────────────────────────────────────────────────────────
if not args.sim:
    print("\n  (Skipping simulator tests — use --sim to enable)")
    print("  Hint: start vfd_simulator.exe on this machine then re-run with --sim")
elif not found_sim and dev_count.value == 0:
    print("\n  (Skipping simulator tests — no devices discovered)")
else:
    # Use first discovered device if simulator wasn't identified by name
    if sim_device is None and dev_count.value > 0:
        sim_device = devices[0]

    sim_mac  = (ctypes.c_uint8 * 6)(*list(sim_device.mac))
    sim_name = sim_device.name_of_station.decode(errors="replace")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 10: PN_DCPSetName
    # ──────────────────────────────────────────────────────────────────────────
    section("10  PN_DCPSetName")

    new_name = args.name.encode()
    rc = PN_DCPSetName(handle, sim_mac, new_name)
    check(rc == 0, f"PN_DCPSetName → '{args.name}'", rc_str(rc))
    if rc != 0:
        print(f"       Error: {last_error(handle)}")

    time.sleep(0.5)

    # Re-discover to verify name was applied
    rc = PN_DCPDiscover(handle, devices, 8, ctypes.byref(dev_count), 2000)
    name_updated = False
    for i in range(dev_count.value):
        n = devices[i].name_of_station.decode(errors="replace")
        if n == args.name:
            name_updated = True
    check(name_updated, f"device reports new name '{args.name}' after SetName")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 11: PN_DCPSetIP
    # ──────────────────────────────────────────────────────────────────────────
    section("11  PN_DCPSetIP")

    rc = PN_DCPSetIP(handle, sim_mac,
                     args.ip.encode(),
                     b"255.255.255.0",
                     b"192.168.1.1")
    check(rc == 0, f"PN_DCPSetIP → {args.ip}", rc_str(rc))
    if rc != 0:
        print(f"       Error: {last_error(handle)}")

    time.sleep(0.5)

    # Re-discover and verify
    rc = PN_DCPDiscover(handle, devices, 8, ctypes.byref(dev_count), 2000)
    ip_updated = False
    for i in range(dev_count.value):
        ip = devices[i].ip_str.decode(errors="replace")
        if ip == args.ip:
            ip_updated = True
    check(ip_updated, f"device reports new IP '{args.ip}' after SetIP")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 12: PN_Connect + cyclic PROFIdrive exchange
    # ──────────────────────────────────────────────────────────────────────────
    section("12  PN_Connect + PROFIdrive cyclic exchange")

    ar = PN_ARConfig()
    for i in range(6):
        ar.device_mac[i] = sim_device.mac[i]
    ar.device_ip         = args.ip.encode()
    ar.send_clock_factor = 128      # 1 ms cycle
    ar.reduction_ratio   = 1
    ar.watchdog_factor   = 3
    ar.api               = 0
    ar.slot              = 1
    ar.subslot           = 1
    ar.module_ident      = 0
    ar.submodule_ident   = 0

    rc = PN_Connect(handle, ctypes.byref(ar))
    check(rc == 0, "PN_Connect returns PN_OK", rc_str(rc))
    if rc != 0:
        print(f"       Error: {last_error(handle)}")
    else:
        time.sleep(0.2)
        rc = PN_IsConnected(handle)
        check(rc == 1, "PN_IsConnected returns 1 after Connect", f"got {rc}")

        # Send drive enable setpoint
        STW1_FULL_ENABLE = 0x047F
        NSOLL_50PCT      = 0x2000
        rc = PN_DriveSetpoint(handle, STW1_FULL_ENABLE, NSOLL_50PCT)
        check(rc == 0, "PN_DriveSetpoint(STW1=0x047F, NSOLL=0x2000)", rc_str(rc))

        # Wait a few cycles then read back status
        time.sleep(0.3)
        zsw1 = ctypes.c_uint16(0)
        nist = ctypes.c_uint16(0)
        rc = PN_DriveStatus(handle, ctypes.byref(zsw1), ctypes.byref(nist))
        check(rc == 0, "PN_DriveStatus returns PN_OK", rc_str(rc))
        if rc == 0:
            print(f"       ZSW1 = 0x{zsw1.value:04X}  NIST_A = 0x{nist.value:04X}")
            check(zsw1.value != 0, "ZSW1 is non-zero (simulator responding)")
            # With auto-sim: ZSW1 should be 0x0F37 (drive running)
            if zsw1.value == 0x0F37:
                print("       ZSW1=0x0F37 ✓  drive running at setpoint")
            check(nist.value == NSOLL_50PCT,
                  f"NIST_A == NSOLL_A (0x{NSOLL_50PCT:04X}) — instant follow",
                  f"got 0x{nist.value:04X}")

        # Stats while connected
        stats2 = PN_Stats()
        PN_GetStats(handle, ctypes.byref(stats2))
        check(stats2.cyclic_running == 1, "stats.cyclic_running == 1 during exchange")
        check(stats2.frames_sent > 0,     "stats.frames_sent > 0")
        check(stats2.frames_received > 0, "stats.frames_received > 0")
        if args.verbose:
            print(f"       TX={stats2.frames_sent} RX={stats2.frames_received} "
                  f"missed={stats2.missed_cycles} cc={stats2.cycle_counter}")

        # Stop drive (OFF1)
        PN_DriveSetpoint(handle, 0x0000, 0x0000)
        time.sleep(0.2)

        # Disconnect
        rc = PN_Disconnect(handle)
        check(rc == 0, "PN_Disconnect returns PN_OK", rc_str(rc))
        time.sleep(0.1)
        rc = PN_IsConnected(handle)
        check(rc == 0, "PN_IsConnected returns 0 after Disconnect", f"got {rc}")

# ──────────────────────────────────────────────────────────────────────────────
# Test 13: Structure size sanity (catch packing bugs early)
# ──────────────────────────────────────────────────────────────────────────────
section("13  ctypes structure size check")

check(ctypes.sizeof(PN_DeviceInfo) == 332,
      f"sizeof(PN_DeviceInfo) == 332",
      f"got {ctypes.sizeof(PN_DeviceInfo)}")
check(ctypes.sizeof(PN_ModuleDesc) == 268,
      f"sizeof(PN_ModuleDesc) == 268",
      f"got {ctypes.sizeof(PN_ModuleDesc)}")
check(ctypes.sizeof(PN_ARConfig)   == 48,
      f"sizeof(PN_ARConfig) == 48",
      f"got {ctypes.sizeof(PN_ARConfig)}")
check(ctypes.sizeof(PN_Stats)      == 40,
      f"sizeof(PN_Stats) == 40",
      f"got {ctypes.sizeof(PN_Stats)}")

# ──────────────────────────────────────────────────────────────────────────────
# Shutdown
# ──────────────────────────────────────────────────────────────────────────────
section("14  PN_Shutdown")

rc = PN_Shutdown(handle)
check(rc == 0, "PN_Shutdown returns PN_OK", rc_str(rc))
handle = ctypes.c_void_p(0)   # null out — use-after-free is UB

# Shutdown with NULL handle should return error (not crash)
rc2 = PN_Shutdown(ctypes.c_void_p(0))
check(rc2 != 0, "PN_Shutdown(NULL) returns error", rc_str(rc2))

# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
color = "\033[32m" if failed == 0 else "\033[31m"
print(f"{color}Results: {passed} passed, {failed} failed\033[0m")
print("="*50)
sys.exit(0 if failed == 0 else 1)
