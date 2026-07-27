#!/usr/bin/env python3
"""vfd_sim.py — Pure-Python Profinet VFD slave simulator.

A drop-in replacement for dist/vfd_simulator.exe. It emulates a Profinet IO
device (PROFIdrive Telegram 1 VFD) so you can bring the master gateway to a
live CONNECTED state and exercise IO on a single PC — no second machine, no
real drive, no compiled simulator.

It faithfully mirrors simulator/pn_device.c:
  * RPC/CM server on UDP 34964  — Connect / Write / DControl / Release + CControl
  * DCP server over raw Ethernet — Identify + Set(Name/IP)   (via Npcap)
  * cyclic input frames over raw Ethernet — ZSW1 / NIST_A     (via Npcap)
  * PROFIdrive auto-simulation: STW1 -> ZSW1, NSOLL -> NIST

Same-PC test (recommended):
  1. Run this on the  Npcap Loopback Adapter.
  2. In the master app: Adapter = Npcap Loopback Adapter, device IP = 127.0.0.1.
  The RPC handshake loops over localhost (clears ERR-11); cyclic IO loops over
  the Npcap loopback adapter (gives live ZSW1/NIST).

Usage:
  python simulator/vfd_sim.py                       # interactive adapter pick
  python simulator/vfd_sim.py --adapter "\\Device\\NPF_Loopback"
  python simulator/vfd_sim.py --list                # just list adapters
Options:
  --adapter NAME   \\Device\\NPF_{GUID} to bind (else you pick from a menu)
  --name NAME      station name (default: vfd-simulator)
  --ip  A.B.C.D    IP reported via DCP (default: 192.168.1.50)
  --mac AA:BB:..   device MAC used as source (default: 02:00:00:11:22:33)
  --manual         disable auto-sim; hold ZSW1=0x0F21 / NIST=0 until forced
"""

import argparse
import ctypes
import socket
import struct
import sys
import threading
import time

# ─── Protocol constants (mirror pn_device.c) ────────────────────────────────
ETH_PN            = 0x8892
FID_DCP_IDENTIFY  = 0xFEFE
FID_DCP_IDENTIFY_R = 0xFEFF
FID_DCP_SET       = 0xFEFD
FID_DCP_SET_R     = 0xFEFC
RPC_PORT          = 34964

# RPC wire layout (must match src/rpc_cm.c): 24-byte header, ObjUUID at 24,
# stub payload at 40. ControlCommand lives at payload+20.
RPC_OFF_PAYLOAD   = 40

# Profinet IO object UUID: DEA00001-6C97-11D1-8271-00A02442DF7D (wire order)
PN_OBJ_UUID = bytes([
    0x00, 0x00, 0xa0, 0xde, 0x97, 0x6c, 0xd1, 0x11,
    0x82, 0x71, 0x00, 0xa0, 0x24, 0x42, 0xdf, 0x7d,
])

# ─── byte helpers ───────────────────────────────────────────────────────────
def u16be(b, o):  return (b[o] << 8) | b[o + 1]
def u16le(b, o):  return b[o] | (b[o + 1] << 8)
def put_u16be(b, o, v): b[o] = (v >> 8) & 0xFF; b[o + 1] = v & 0xFF
def put_u16le(b, o, v): b[o] = v & 0xFF; b[o + 1] = (v >> 8) & 0xFF
def put_u32be(b, o, v):
    b[o] = (v >> 24) & 0xFF; b[o + 1] = (v >> 16) & 0xFF
    b[o + 2] = (v >> 8) & 0xFF; b[o + 3] = v & 0xFF


# ═══════════════════════════════════════════════════════════════════════════
# Npcap raw-Ethernet wrapper (ctypes, no scapy) — same wpcap.dll the DLL uses
# ═══════════════════════════════════════════════════════════════════════════
class _timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]

class _pkthdr(ctypes.Structure):
    _fields_ = [("ts", _timeval),
                ("caplen", ctypes.c_uint32),
                ("len", ctypes.c_uint32)]

class _pcap_if(ctypes.Structure):
    pass
_pcap_if._fields_ = [
    ("next",        ctypes.POINTER(_pcap_if)),
    ("name",        ctypes.c_char_p),
    ("description", ctypes.c_char_p),
    ("addresses",   ctypes.c_void_p),
    ("flags",       ctypes.c_uint),
]

PCAP_ERRBUF_SIZE = 256


class Npcap:
    def __init__(self):
        self._dll = None
        self._p = None
        cands = ([r"C:\Windows\System32\Npcap\wpcap.dll", "wpcap.dll"]
                 if sys.platform == "win32" else ["libpcap.so.1", "libpcap.so"])
        for c in cands:
            try:
                self._dll = ctypes.CDLL(c)
                break
            except OSError:
                continue
        if not self._dll:
            raise RuntimeError("wpcap.dll not found — install Npcap from https://npcap.com/")
        d = self._dll
        d.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(_pcap_if)), ctypes.c_char_p]
        d.pcap_findalldevs.restype = ctypes.c_int
        d.pcap_freealldevs.argtypes = [ctypes.POINTER(_pcap_if)]
        d.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_char_p]
        d.pcap_open_live.restype = ctypes.c_void_p
        d.pcap_close.argtypes = [ctypes.c_void_p]
        d.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        d.pcap_sendpacket.restype = ctypes.c_int
        d.pcap_next_ex.argtypes = [ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.POINTER(_pkthdr)),
                                   ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        d.pcap_next_ex.restype = ctypes.c_int

    def list_adapters(self):
        out = []
        alldevs = ctypes.POINTER(_pcap_if)()
        errbuf = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        if self._dll.pcap_findalldevs(ctypes.byref(alldevs), errbuf) != 0:
            return out
        d = alldevs
        while d:
            node = d.contents
            name = node.name.decode(errors="replace") if node.name else ""
            desc = node.description.decode(errors="replace") if node.description else ""
            if not desc and "loopback" in name.lower():
                desc = "Npcap Loopback Adapter"
            if name:
                out.append((name, desc or "(no description)"))
            d = node.next
        self._dll.pcap_freealldevs(alldevs)
        return out

    def open(self, name):
        errbuf = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        self._p = self._dll.pcap_open_live(name.encode(), 1514, 1, 10, errbuf)
        if not self._p:
            raise RuntimeError(f"pcap_open_live failed: {errbuf.value.decode(errors='replace')}")

    def send(self, frame: bytes):
        if self._p:
            self._dll.pcap_sendpacket(self._p, frame, len(frame))

    def recv(self, timeout_ms=10):
        """Return one captured frame as bytes, or None on timeout."""
        hdr = ctypes.POINTER(_pkthdr)()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        rc = self._dll.pcap_next_ex(self._p, ctypes.byref(hdr), ctypes.byref(data))
        if rc == 1 and hdr and data:
            n = hdr.contents.caplen
            return bytes(ctypes.cast(data,
                         ctypes.POINTER(ctypes.c_ubyte * n)).contents)
        return None

    def close(self):
        if self._p:
            self._dll.pcap_close(self._p)
            self._p = None


# ═══════════════════════════════════════════════════════════════════════════
# Device state / simulator
# ═══════════════════════════════════════════════════════════════════════════
DEV_IDLE, DEV_LISTENING, DEV_CONNECTED, DEV_CYCLIC = 0, 1, 2, 3
_STATE_NAME = {0: "IDLE", 1: "LISTENING", 2: "CONNECTED", 3: "CYCLIC"}


class VfdSim:
    def __init__(self, adapter, name, ip, mac, auto_sim):
        self.pcap = Npcap()
        self.pcap.open(adapter)

        self.local_mac = bytes(int(x, 16) for x in mac.split(":"))
        self.station_name = name
        self.vendor_id = 0x002A
        self.device_id = 0x0001
        ip_parts = [int(x) for x in ip.split(".")]
        self.ip      = struct.pack("BBBB", *ip_parts)          # stored little-endian on wire? see below
        self.subnet  = struct.pack("BBBB", 255, 255, 255, 0)
        self.gateway = struct.pack("BBBB", 192, 168, 1, 1)

        self.auto_sim = auto_sim
        self.stw1 = 0
        self.nsoll = 0
        self.zsw1 = 0x0F21     # ready to switch on
        self.nist = 0

        # AR / cyclic negotiated params
        self.ar_uuid = bytes(16)
        self.session_key = 0
        self.ctrl_mac = bytes(6)
        self.output_frame_id = 0
        self.input_frame_id = 0
        self.iocr_ref_out = 0
        self.iocr_ref_in = 0
        self.send_clock_factor = 128
        self.data_length = 4

        self.state = DEV_LISTENING
        self.ctrl_addr = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cyclic_started = False
        self._ccid = 0x10000000
        self.frames_rx = 0
        self.frames_tx = 0

        self.rpc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rpc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rpc_sock.bind(("0.0.0.0", RPC_PORT))
        self.rpc_sock.settimeout(0.02)

    # ── logging ──
    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)

    # ── PROFIdrive auto-sim (mirror compute_profidrive) ──
    def _compute_profidrive(self):
        on = bool(self.stw1 & 0x0001)
        en = bool(self.stw1 & 0x0008)
        sp = bool(self.stw1 & 0x0040)
        if on and en and sp:
            self.zsw1, self.nist = 0x0F37, self.nsoll
        elif on:
            self.zsw1, self.nist = 0x0F31, 0
        else:
            self.zsw1, self.nist = 0x0F21, 0

    # ═══ DCP (raw Ethernet) ═══
    def _dcp_identify_response(self, req):
        f = bytearray(512)
        pos = 0
        f[pos:pos+6] = req[6:12]; pos += 6          # dst = controller src
        f[pos:pos+6] = self.local_mac; pos += 6     # src
        f[pos] = 0x88; f[pos+1] = 0x92; pos += 2
        f[pos] = 0xFE; f[pos+1] = 0xFF; pos += 2    # FrameID 0xFEFF
        f[pos] = 0x05; pos += 1                      # ServiceID
        f[pos] = 0x01; pos += 1                      # ServiceType response
        f[pos:pos+4] = req[18:22]; pos += 4          # XID echo
        f[pos] = 0; f[pos+1] = 0; pos += 2           # ResponseDelay
        data_len_off = pos; pos += 2                 # DataLength placeholder
        data_start = pos

        name = self.station_name.encode()
        f[pos] = 0x02; f[pos+1] = 0x02; pos += 2
        put_u16be(f, pos, len(name) + 2); pos += 2
        f[pos] = 0; f[pos+1] = 0; pos += 2
        f[pos:pos+len(name)] = name; pos += len(name)
        if len(name) & 1: f[pos] = 0; pos += 1

        f[pos] = 0x01; f[pos+1] = 0x02; pos += 2     # IP block
        put_u16be(f, pos, 14); pos += 2
        f[pos] = 0; f[pos+1] = 0; pos += 2
        f[pos:pos+4] = self.ip; pos += 4
        f[pos:pos+4] = self.subnet; pos += 4
        f[pos:pos+4] = self.gateway; pos += 4

        f[pos] = 0x02; f[pos+1] = 0x07; pos += 2     # DeviceID block
        put_u16be(f, pos, 6); pos += 2
        f[pos] = 0; f[pos+1] = 0; pos += 2
        put_u16be(f, pos, self.vendor_id); pos += 2
        put_u16be(f, pos, self.device_id); pos += 2

        put_u16be(f, data_len_off, pos - data_start)
        while pos < 60: pos += 1
        self.pcap.send(bytes(f[:pos]))
        self.frames_tx += 1

    def _dcp_handle_identify(self, buf, n):
        if n < 26: return
        if buf[14] != 0x05 or buf[15] != 0x00: return
        data_len = u16be(buf, 22)
        pos, end = 26, min(26 + data_len, n)
        respond = False
        while pos + 4 <= end:
            opt, sub = buf[pos], buf[pos+1]
            blk_len = u16be(buf, pos+2)
            pos += 4
            if opt == 0xFF and sub == 0xFF:
                respond = True; break
            if opt == 0x02 and sub == 0x02:
                nl = blk_len - 2 if blk_len > 2 else 0
                off = pos + 2
                if off + nl <= end:
                    if buf[off:off+nl] == self.station_name.encode():
                        respond = True
                if not self.station_name:
                    respond = True
            pos += blk_len
            if blk_len & 1: pos += 1
        if respond:
            self._dcp_identify_response(buf)
            self.log("DCP Identify -> responded")

    def _dcp_handle_set(self, buf, n):
        if n < 26: return
        if buf[0:6] != self.local_mac: return
        if buf[14] != 0x04 or buf[15] != 0x00: return
        data_len = u16be(buf, 22)
        pos, end = 26, min(26 + data_len, n)
        while pos + 4 <= end:
            opt, sub = buf[pos], buf[pos+1]
            blk_len = u16be(buf, pos+2)
            pos += 4
            if opt == 0x01 and sub == 0x02 and blk_len >= 14:
                self.ip      = bytes(buf[pos+2:pos+6])
                self.subnet  = bytes(buf[pos+6:pos+10])
                self.gateway = bytes(buf[pos+10:pos+14])
                self.log(f"DCP SetIP: {self.ip[0]}.{self.ip[1]}.{self.ip[2]}.{self.ip[3]}")
            elif opt == 0x02 and sub == 0x02 and blk_len >= 2:
                nl = min(blk_len - 2, 239)
                with self._lock:
                    self.station_name = bytes(buf[pos+2:pos+2+nl]).decode(errors="replace")
                self.log(f'DCP SetName: "{self.station_name}"')
            pos += blk_len
            if blk_len & 1: pos += 1

        r = bytearray(60)
        r[0:6] = buf[6:12]; r[6:12] = self.local_mac
        r[12] = 0x88; r[13] = 0x92
        r[14] = 0xFE; r[15] = 0xFC
        r[16] = 0x04; r[17] = 0x01
        r[18:22] = buf[18:22]
        r[24] = 0x00; r[25] = 0x04
        r[26] = 0xFF; r[27] = 0xFF
        r[28] = 0x00; r[29] = 0x03
        self.pcap.send(bytes(r))
        self.frames_tx += 1

    def _cyclic_rx(self, buf, n):
        if n < 22: return
        if not (buf[16] & 0x80): return       # IOPS not GOOD
        with self._lock:
            self.stw1  = buf[17] | (buf[18] << 8)
            self.nsoll = buf[19] | (buf[20] << 8)
            if self.auto_sim:
                self._compute_profidrive()
        self.frames_rx += 1

    # ═══ threads ═══
    def _pkt_thread(self):
        self.log("Packet thread started (DCP + cyclic RX)")
        while not self._stop.is_set():
            buf = self.pcap.recv(10)
            if not buf or len(buf) < 16:
                continue
            if buf[12] != 0x88 or buf[13] != 0x92:
                continue
            fid = u16be(buf, 14)
            if fid == FID_DCP_IDENTIFY:
                self._dcp_handle_identify(buf, len(buf))
            elif fid == FID_DCP_SET:
                self._dcp_handle_set(buf, len(buf))
            elif self.state == DEV_CYCLIC and fid == self.output_frame_id:
                self._cyclic_rx(buf, len(buf))
        self.log("Packet thread stopped")

    def _cyclic_thread(self):
        period = max(self.send_clock_factor * 31250, 1_000_000) / 1e9  # seconds
        self.log(f"Cyclic thread started (period={period*1000:.1f} ms)")
        cc = 0
        nxt = time.perf_counter() + period
        while not self._stop.is_set():
            now = time.perf_counter()
            if now < nxt:
                time.sleep(min(period, nxt - now))
                continue
            nxt += period

            f = bytearray(60)
            f[0:6] = self.ctrl_mac
            f[6:12] = self.local_mac
            f[12] = 0x88; f[13] = 0x92
            f[14] = (self.input_frame_id >> 8) & 0xFF
            f[15] = self.input_frame_id & 0xFF
            f[16] = 0x80                                  # IOPS GOOD
            with self._lock:
                f[17] = self.zsw1 & 0xFF
                f[18] = (self.zsw1 >> 8) & 0xFF
                f[19] = self.nist & 0xFF
                f[20] = (self.nist >> 8) & 0xFF
            f[21] = 0x80                                  # IOCS GOOD
            f[22] = (cc >> 8) & 0xFF
            f[23] = cc & 0xFF
            cc = (cc + 1) & 0xFFFF
            f[24] = 0x35                                  # DataStatus valid+run+primary
            f[25] = 0x00
            self.pcap.send(bytes(f))
            self.frames_tx += 1
        self.log("Cyclic thread stopped")

    # ═══ RPC handlers ═══
    def _rpc_hdr(self, out, callid, opnum, pkt_type):
        for i in range(24): out[i] = 0
        out[0] = 4; out[2] = pkt_type; out[3] = 0x22; out[4] = 0x10
        out[12:16] = callid
        put_u16le(out, 22, opnum)
        return 24

    def _rpc_connect(self, buf, n, frm):
        payload = buf[RPC_OFF_PAYLOAD:]
        plen = len(payload)
        if plen < 4: return
        self.ar_uuid = bytes(16); self.session_key = 0
        self.output_frame_id = self.input_frame_id = 0
        self.iocr_ref_out = self.iocr_ref_in = 0
        self.send_clock_factor = 128; self.data_length = 4

        pos = 0
        while pos + 6 <= plen:
            blk_type = u16be(payload, pos)
            blk_len = u16be(payload, pos + 2)
            if blk_len == 0: break
            ds = pos + 4
            # This master's BlockLength counts the body AFTER the 2 version
            # bytes, so the whole block spans type(2)+len(2)+version(2)+body
            # = 6 + blk_len, not 4 + blk_len.
            be = pos + 6 + blk_len
            if be > plen: break
            if blk_type == 0x0101 and blk_len >= 28:
                self.ar_uuid = bytes(payload[ds+4:ds+20])
                self.session_key = u16be(payload, ds+20)
                self.ctrl_mac = bytes(payload[ds+22:ds+28])
            elif blk_type == 0x0102 and blk_len >= 16:
                # IOCRBlockReq body: ver(2) IOCRType(2) IOCRRef(2) LT(2)
                #   Properties(4) DataLength(2) FrameID(2) SendClock(2) ...
                iocr_type = u16be(payload, ds+2)
                iocr_ref  = u16be(payload, ds+4)
                data_len  = u16be(payload, ds+12)
                frame_id  = u16be(payload, ds+14)
                clock_f   = u16be(payload, ds+16)
                if iocr_type == 1:
                    self.output_frame_id = frame_id
                    self.iocr_ref_out = iocr_ref
                    self.data_length = data_len
                    self.send_clock_factor = clock_f or 128
                elif iocr_type == 2:
                    self.input_frame_id = frame_id
                    self.iocr_ref_in = iocr_ref
            pos = be   # master packs blocks contiguously (no even padding)

        self.log(f"RPC Connect: ctrl_mac={':'.join('%02X' % b for b in self.ctrl_mac)}")
        self.log(f"  OutputFrameID=0x{self.output_frame_id:04X} "
                 f"InputFrameID=0x{self.input_frame_id:04X} "
                 f"ClockFactor={self.send_clock_factor}")

        r = bytearray(512)
        rp = self._rpc_hdr(r, buf[12:16], 0, 2)
        r[rp:rp+16] = PN_OBJ_UUID; rp += 16
        r[rp:rp+4] = b"\x00\x00\x00\x00"; rp += 4

        # ARBlockRes 0x8101
        bs = rp; put_u16be(r, rp, 0x8101); rp += 2
        blo = rp; rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 0x0006); rp += 2
        r[rp:rp+16] = self.ar_uuid; rp += 16
        put_u16be(r, rp, self.session_key); rp += 2
        r[rp:rp+6] = self.local_mac; rp += 6
        put_u16be(r, rp, 0x8894); rp += 2
        put_u16be(r, rp, 0); rp += 2
        put_u16be(r, blo, rp - bs - 4)

        # IOCRBlockRes output (0x8102 / type 1)
        bs = rp; put_u16be(r, rp, 0x8102); rp += 2
        blo = rp; rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 0x0001); rp += 2
        put_u16be(r, rp, self.iocr_ref_out); rp += 2
        put_u16be(r, rp, self.output_frame_id); rp += 2
        put_u16be(r, blo, rp - bs - 4)

        # IOCRBlockRes input (type 2)
        bs = rp; put_u16be(r, rp, 0x8102); rp += 2
        blo = rp; rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 0x0002); rp += 2
        put_u16be(r, rp, self.iocr_ref_in); rp += 2
        put_u16be(r, rp, self.input_frame_id); rp += 2
        put_u16be(r, blo, rp - bs - 4)

        # AlarmCRBlockRes 0x8103
        bs = rp; put_u16be(r, rp, 0x8103); rp += 2
        blo = rp; rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 0x0001); rp += 2
        put_u16be(r, rp, 0x0001); rp += 2
        put_u16be(r, rp, 200); rp += 2
        put_u16be(r, blo, rp - bs - 4)

        # ModuleDiffBlock 0x8104
        bs = rp; put_u16be(r, rp, 0x8104); rp += 2
        blo = rp; rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 1); rp += 2
        put_u32be(r, rp, 0); rp += 4
        put_u16be(r, rp, 0); rp += 2
        put_u16be(r, blo, rp - bs - 4)

        r[8] = rp & 0xFF; r[9] = (rp >> 8) & 0xFF   # frag_length
        self.ctrl_addr = frm
        self.state = DEV_CONNECTED
        self.rpc_sock.sendto(bytes(r[:rp]), frm)

    def _rpc_write(self, buf, n, frm):
        r = bytearray(128)
        rp = self._rpc_hdr(r, buf[12:16], 5, 2)
        r[rp:rp+16] = PN_OBJ_UUID; rp += 16
        r[rp:rp+4] = b"\x00\x00\x00\x00"; rp += 4
        put_u16be(r, rp, 0x8008); rp += 2
        put_u16be(r, rp, 0x003C); rp += 2
        rp += 60
        r[8] = rp & 0xFF; r[9] = (rp >> 8) & 0xFF
        self.rpc_sock.sendto(bytes(r[:rp]), frm)

    def _rpc_dcontrol(self, buf, n, frm):
        # Master DControl is 64 bytes: payload@40, ControlCommand@payload+20
        if n < RPC_OFF_PAYLOAD + 22:
            return
        payload = buf[RPC_OFF_PAYLOAD:]
        cmd = u16be(payload, 20)

        if cmd & 0x0004:      # ApplicationReady
            self.state = DEV_CYCLIC
            self.log("DControl ApplicationReady — starting cyclic TX")
            if not self._cyclic_started:
                self._cyclic_started = True
                threading.Thread(target=self._cyclic_thread, daemon=True).start()

        r = bytearray(256)
        rp = self._rpc_hdr(r, buf[12:16], 2, 2)
        r[rp:rp+16] = PN_OBJ_UUID; rp += 16
        r[rp:rp+4] = b"\x00\x00\x00\x00"; rp += 4
        put_u16be(r, rp, 0x8110); rp += 2
        put_u16be(r, rp, 28); rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        r[rp] = 0x00; r[rp+1] = 0x00; rp += 2
        r[rp:rp+16] = self.ar_uuid; rp += 16
        put_u16be(r, rp, self.session_key); rp += 2
        r[rp] = 0x00; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, cmd); rp += 2
        put_u16be(r, rp, 0); rp += 2
        r[8] = rp & 0xFF; r[9] = (rp >> 8) & 0xFF
        self.rpc_sock.sendto(bytes(r[:rp]), frm)

        if cmd & 0x0004:      # send CControl request back to controller
            # Mirror the master's DControl layout exactly: 24-byte header
            # (opnum=CCONTROL, ptype=REQUEST), ObjUUID space at 24, payload at
            # 40 with ControlCommand at payload+20 (absolute 60).
            self._ccid = (self._ccid + 1) & 0xFFFFFFFF
            cc = bytearray(RPC_OFF_PAYLOAD + 24)
            self._rpc_hdr(cc, struct.pack("<I", self._ccid), 3, 0)
            cp = RPC_OFF_PAYLOAD
            cc[cp:cp+16] = self.ar_uuid; cp += 16          # payload+0
            put_u16be(cc, cp, self.session_key); cp += 2    # payload+16
            put_u16be(cc, cp, 0x0000); cp += 2              # payload+18
            put_u16be(cc, cp, 0x0004); cp += 2              # payload+20 = AppReady
            put_u16be(cc, cp, 0x0000); cp += 2              # payload+22
            cc[8] = cp & 0xFF; cc[9] = (cp >> 8) & 0xFF     # frag_length
            self.rpc_sock.sendto(bytes(cc[:cp]), self.ctrl_addr)
            self.log("CControl(ApplicationReady) sent to controller")

    def _rpc_release(self, buf, frm):
        self._stop_cyclic()
        self.state = DEV_LISTENING
        self.log("RPC Release — back to LISTENING")
        r = bytearray(128)
        rp = self._rpc_hdr(r, buf[12:16], 1, 2)
        r[rp:rp+16] = PN_OBJ_UUID; rp += 16
        r[rp:rp+4] = b"\x00\x00\x00\x00"; rp += 4
        put_u16be(r, rp, 0x8111); rp += 2
        put_u16be(r, rp, 28); rp += 2
        r[rp] = 0x01; r[rp+1] = 0x00; rp += 2
        r[rp] = 0x00; r[rp+1] = 0x00; rp += 2
        r[rp:rp+16] = self.ar_uuid; rp += 16
        put_u16be(r, rp, self.session_key); rp += 2
        r[rp] = 0x00; r[rp+1] = 0x00; rp += 2
        put_u16be(r, rp, 0x0010); rp += 2
        put_u16be(r, rp, 0); rp += 2
        r[8] = rp & 0xFF; r[9] = (rp >> 8) & 0xFF
        self.rpc_sock.sendto(bytes(r[:rp]), frm)

    def _stop_cyclic(self):
        # cyclic thread checks self._stop; for a full stop we reset the flag
        # only on shutdown. Here just mark not-cyclic so RX ignores frames.
        self._cyclic_started = False

    def _rpc_thread(self):
        self.log(f"RPC thread started (UDP :{RPC_PORT})")
        while not self._stop.is_set():
            try:
                buf, frm = self.rpc_sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(buf) < 24:
                continue
            buf = bytearray(buf)
            opnum = u16le(buf, 22)
            if   opnum == 0: self._rpc_connect(buf, len(buf), frm)
            elif opnum == 1: self._rpc_release(buf, frm)
            elif opnum == 2: self._rpc_dcontrol(buf, len(buf), frm)
            elif opnum == 5: self._rpc_write(buf, len(buf), frm)
            else: self.log(f"RPC unknown opnum {opnum}")
        self.log("RPC thread stopped")

    # ═══ lifecycle ═══
    def start(self):
        threading.Thread(target=self._pkt_thread, daemon=True).start()
        threading.Thread(target=self._rpc_thread, daemon=True).start()
        self.log(f"Device started — MAC={':'.join('%02X' % b for b in self.local_mac)}, "
                 f"name='{self.station_name}'")

    def stop(self):
        self._stop.set()
        time.sleep(0.1)
        try: self.rpc_sock.close()
        except Exception: pass
        self.pcap.close()

    def status_line(self):
        with self._lock:
            return (f"state={_STATE_NAME[self.state]:9s} "
                    f"STW1=0x{self.stw1:04X} NSOLL=0x{self.nsoll:04X} | "
                    f"ZSW1=0x{self.zsw1:04X} NIST=0x{self.nist:04X} | "
                    f"rx={self.frames_rx} tx={self.frames_tx}")


# ═══════════════════════════════════════════════════════════════════════════
def pick_adapter(npcap):
    adapters = npcap.list_adapters()
    if not adapters:
        print("No adapters found — is Npcap installed?")
        sys.exit(1)
    print("\nAvailable adapters:")
    for i, (name, desc) in enumerate(adapters):
        print(f"  [{i}] {desc}")
        print(f"      {name}")
    # default to loopback if present
    default = 0
    for i, (name, _d) in enumerate(adapters):
        if "loopback" in name.lower():
            default = i
            break
    sel = input(f"\nSelect adapter number [{default}]: ").strip()
    idx = int(sel) if sel else default
    return adapters[idx][0]


def main():
    ap = argparse.ArgumentParser(description="Pure-Python Profinet VFD simulator")
    ap.add_argument("--adapter")
    ap.add_argument("--name", default="vfd-simulator")
    ap.add_argument("--ip", default="192.168.1.50")
    ap.add_argument("--mac", default="02:00:00:11:22:33")
    ap.add_argument("--manual", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    npcap = Npcap()
    if args.list:
        for name, desc in npcap.list_adapters():
            print(f"{desc}\n    {name}")
        return

    adapter = args.adapter or pick_adapter(npcap)

    print("=" * 68)
    print("  Python Profinet VFD Simulator")
    print("=" * 68)
    print(f"  Adapter : {adapter}")
    print(f"  Station : {args.name}")
    print(f"  DCP IP  : {args.ip}")
    print(f"  MAC     : {args.mac}")
    print(f"  Auto-sim: {'OFF (manual)' if args.manual else 'ON'}")
    print("=" * 68)
    print("  Point the master at device IP 127.0.0.1 (same PC) and Apply&Restart.")
    print("  Ctrl+C to quit.\n")

    try:
        sim = VfdSim(adapter, args.name, args.ip, args.mac, not args.manual)
    except Exception as e:
        print(f"\nFAILED to start: {e}")
        if "bind" in str(e).lower() or "address" in str(e).lower():
            print("UDP 34964 already in use — close the other simulator/instance.")
        sys.exit(1)

    sim.start()
    try:
        while True:
            time.sleep(1.0)
            print("  " + sim.status_line(), flush=True)
    except KeyboardInterrupt:
        print("\nStopping…")
        sim.stop()


if __name__ == "__main__":
    main()
