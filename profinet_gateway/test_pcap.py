"""test_pcap.py — Standalone Npcap probe.

Bypasses profinet.dll entirely and calls Npcap's wpcap.dll directly to find
out WHY pcap_open_live fails. Prints the exact pcap error string per adapter.

Run (from Windows, ideally as Administrator):
    python profinet_gateway\\test_pcap.py

Interpreting the result:
  * If open_live FAILS here too  -> the problem is Npcap / permissions / adapter,
    NOT profinet.dll. The printed error text names the fix.
  * If open_live SUCCEEDS here but the app still fails -> the problem IS in
    profinet.dll and we fix it there.
"""

import ctypes
import os
import platform
import sys

PCAP_ERRBUF_SIZE = 256


# ── pcap struct (only the fields we need) ────────────────────────────────────
class pcap_if(ctypes.Structure):
    pass


pcap_if._fields_ = [
    ("next",        ctypes.POINTER(pcap_if)),
    ("name",        ctypes.c_char_p),
    ("description", ctypes.c_char_p),
    ("addresses",   ctypes.c_void_p),
    ("flags",       ctypes.c_uint),
]


def load_wpcap():
    """Load wpcap.dll the same way frameio_load_npcap does in the DLL."""
    candidates = [
        r"C:\Windows\System32\Npcap\wpcap.dll",
        "wpcap.dll",
    ]
    for path in candidates:
        try:
            dll = ctypes.CDLL(path)   # wpcap is __cdecl
            print(f"[OK] Loaded wpcap: {path}")
            return dll
        except OSError as e:
            print(f"[..] Could not load {path}: {e}")
    return None


def main():
    print("=" * 70)
    print("Npcap standalone probe (bypasses profinet.dll)")
    print("=" * 70)
    print(f"Python arch : {platform.architecture()[0]}   "
          f"(profinet.dll is 32-bit -> this should read '32bit')")
    print(f"Python exe  : {sys.executable}")

    # Admin check (Windows only)
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        print(f"Admin rights: {'YES' if is_admin else 'NO  (right-click -> Run as administrator)'}")
    except Exception:
        print("Admin rights: (not on Windows?)")
    print("-" * 70)

    wp = load_wpcap()
    if not wp:
        print("\n[FAIL] wpcap.dll not found. Install Npcap from https://npcap.com/")
        return

    # ── bind functions ──────────────────────────────────────────────────────
    wp.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(pcap_if)),
                                    ctypes.c_char_p]
    wp.pcap_findalldevs.restype = ctypes.c_int
    wp.pcap_freealldevs.argtypes = [ctypes.POINTER(pcap_if)]
    wp.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_char_p]
    wp.pcap_open_live.restype = ctypes.c_void_p
    wp.pcap_close.argtypes = [ctypes.c_void_p]

    # ── findalldevs ─────────────────────────────────────────────────────────
    alldevs = ctypes.POINTER(pcap_if)()
    errbuf = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
    if wp.pcap_findalldevs(ctypes.byref(alldevs), errbuf) != 0:
        print(f"\n[FAIL] pcap_findalldevs: {errbuf.value.decode(errors='replace')}")
        return

    names = []
    d = alldevs
    while d:
        dev = d.contents
        name = dev.name.decode(errors="replace") if dev.name else "(null)"
        desc = dev.description.decode(errors="replace") if dev.description else ""
        names.append(name)
        d = dev.next

    print(f"\nFound {len(names)} adapter(s). Trying pcap_open_live on each:\n")

    # ── open_live per adapter — THE key test ────────────────────────────────
    ok_any = False
    d = alldevs
    while d:
        dev = d.contents
        name = dev.name.decode(errors="replace") if dev.name else "(null)"
        desc = dev.description.decode(errors="replace") if dev.description else ""
        eb = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        handle = wp.pcap_open_live(dev.name, 1514, 1, 1000, eb)
        if handle:
            print(f"  OPEN OK    {name}")
            print(f"             {desc}")
            wp.pcap_close(handle)
            ok_any = True
        else:
            print(f"  OPEN FAIL  {name}")
            print(f"             {desc}")
            print(f"             ERROR -> {eb.value.decode(errors='replace')}")
        print()
        d = dev.next

    wp.pcap_freealldevs(alldevs)

    print("=" * 70)
    if ok_any:
        print("RESULT: At least one adapter opened OK.")
        print("  -> Npcap + permissions are FINE. If the app still fails on that")
        print("     same adapter, the problem is in profinet.dll (or the saved")
        print("     adapter name). Copy an 'OPEN OK' name into the app's Adapter box.")
    else:
        print("RESULT: EVERY adapter failed to open.")
        print("  -> This is NOT profinet.dll. It is Npcap / permissions.")
        print("     Read the ERROR text above. Most common fixes:")
        print("       * Run this and the app AS ADMINISTRATOR, or")
        print("       * Reinstall Npcap and UNCHECK")
        print("         'Restrict Npcap driver's access to Administrators only'")
        print("       * Make sure the Npcap service is running (sc query npcap)")
    print("=" * 70)


if __name__ == "__main__":
    main()
