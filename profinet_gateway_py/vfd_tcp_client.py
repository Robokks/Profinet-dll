#!/usr/bin/env python3
"""vfd_tcp_client.py — VFD control panel that drives the gateway over TCP/UDP.

This is the "LabVIEW side": it connects to the Profinet Gateway's TCP (or UDP)
server, continuously sends the output frame (STW1 + NSOLL_A per device) and
reads back the input frame (ZSW1 + NIST_A per device), with a live GUI to
start/stop the drive and set the speed setpoint.

Wire format (must match gateway_server.py):
  output frame = concat over devices of  <STW1 u16 LE><NSOLL u16 LE>   (4 B each)
  input  frame = concat over devices of  <ZSW1 u16 LE><NIST  u16 LE>   (4 B each)
TCP is lock-step: send output frame, then read input frame, repeat. The gateway
drops a client that goes quiet >0.1 s, so the poll loop runs continuously.

Run:  python vfd_tcp_client.py
"""

import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk

NSOLL_100PCT = 0x4000   # PROFIdrive: 0x4000 = 100 % nominal speed

# STW1 presets (PROFIdrive control word 1)
STW1_OFF2   = 0x0000   # coast stop
STW1_STOP   = 0x047E   # ready / OFF1 (ON bit cleared)
STW1_RUN    = 0x047F    # full enable + ON
STW1_FAULT_ACK = 0x0080

# ZSW1 status bits (bit -> label)
_ZSW1_BITS = [
    (0, "RDY_ON"), (1, "RDY"), (2, "RUN"), (3, "FAULT"),
    (4, "NO_OFF2"), (5, "NO_OFF3"), (6, "SW_INHIB"), (7, "WARN"),
    (8, "SPD_DEV"), (9, "CTRL_REQ"), (10, "SPD_REACHED"),
]


def decode_zsw1(z: int) -> str:
    return " ".join(name for bit, name in _ZSW1_BITS if z & (1 << bit)) or "—"


class DevicePanel(ttk.LabelFrame):
    """One VFD (Telegram 1): STW1/NSOLL controls + ZSW1/NIST live display."""

    def __init__(self, parent, idx: int):
        super().__init__(parent, text=f"VFD {idx + 1}")
        self.idx = idx
        self.stw1 = 0x0000
        self.nsoll = 0x0000
        self._build()

    def _build(self):
        pad = {"padx": 4, "pady": 3}

        # ── Outputs: STW1 ──
        out = ttk.Frame(self); out.grid(row=0, column=0, sticky="nw", **pad)
        ttk.Label(out, text="STW1 (control):", font=("Arial", 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Button(out, text="OFF2 Coast", width=11,
                   command=lambda: self._set_stw1(STW1_OFF2)).grid(row=1, column=0, **pad)
        ttk.Button(out, text="STOP (OFF1)", width=11,
                   command=lambda: self._set_stw1(STW1_STOP)).grid(row=1, column=1, **pad)
        ttk.Button(out, text="RUN", width=8,
                   command=lambda: self._set_stw1(STW1_RUN)).grid(row=1, column=2, **pad)
        ttk.Button(out, text="Fault Ack", width=9,
                   command=self._fault_ack).grid(row=1, column=3, **pad)
        ttk.Label(out, text="STW1 hex:").grid(row=2, column=0, sticky="e", **pad)
        self._stw1_var = tk.StringVar(value="0x0000")
        e = ttk.Entry(out, textvariable=self._stw1_var, width=10)
        e.grid(row=2, column=1, sticky="w", **pad)
        e.bind("<Return>", self._stw1_from_entry)
        ttk.Button(out, text="Set", width=5,
                   command=self._stw1_from_entry).grid(row=2, column=2, sticky="w", **pad)

        # ── Outputs: NSOLL ──
        spd = ttk.Frame(self); spd.grid(row=1, column=0, sticky="nw", **pad)
        ttk.Label(spd, text="NSOLL_A (setpoint):", font=("Arial", 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w")
        self._pct_var = tk.DoubleVar(value=0.0)
        self._scale = ttk.Scale(spd, from_=0, to=100, orient="horizontal", length=220,
                                variable=self._pct_var, command=self._nsoll_from_scale)
        self._scale.grid(row=1, column=0, columnspan=3, sticky="w", **pad)
        self._pct_lbl = ttk.Label(spd, text="0.0 %", width=8)
        self._pct_lbl.grid(row=1, column=3, **pad)
        ttk.Label(spd, text="NSOLL hex:").grid(row=2, column=0, sticky="e", **pad)
        self._nsoll_var = tk.StringVar(value="0x0000")
        e2 = ttk.Entry(spd, textvariable=self._nsoll_var, width=10)
        e2.grid(row=2, column=1, sticky="w", **pad)
        e2.bind("<Return>", self._nsoll_from_entry)
        ttk.Button(spd, text="Set", width=5,
                   command=self._nsoll_from_entry).grid(row=2, column=2, sticky="w", **pad)

        # ── Inputs: ZSW1 / NIST (live) ──
        inp = ttk.Frame(self); inp.grid(row=2, column=0, sticky="nw", **pad)
        ttk.Label(inp, text="ZSW1 (status):", font=("Arial", 9, "bold")).grid(
            row=0, column=0, sticky="w", **pad)
        self._zsw1_var = tk.StringVar(value="0x0000")
        ttk.Label(inp, textvariable=self._zsw1_var, foreground="blue",
                  font=("Courier", 10)).grid(row=0, column=1, sticky="w", **pad)
        self._zbits_var = tk.StringVar(value="—")
        ttk.Label(inp, textvariable=self._zbits_var, foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(inp, text="NIST_A:", font=("Arial", 9, "bold")).grid(
            row=2, column=0, sticky="w", **pad)
        self._nist_var = tk.StringVar(value="0x0000  (0.0 %)")
        ttk.Label(inp, textvariable=self._nist_var, foreground="blue",
                  font=("Courier", 10)).grid(row=2, column=1, sticky="w", **pad)

    # ── output helpers (called from GUI thread) ──
    def _set_stw1(self, v):
        self.stw1 = v & 0xFFFF
        self._stw1_var.set(f"0x{self.stw1:04X}")

    def _fault_ack(self):
        # pulse the fault-ack bit on top of the current word
        self._set_stw1(self.stw1 | STW1_FAULT_ACK)
        self.after(300, lambda: self._set_stw1(self.stw1 & ~STW1_FAULT_ACK))

    def _stw1_from_entry(self, _e=None):
        try:
            self.stw1 = int(self._stw1_var.get().strip(), 16) & 0xFFFF
        except ValueError:
            pass
        self._stw1_var.set(f"0x{self.stw1:04X}")

    def _nsoll_from_scale(self, _v=None):
        pct = self._pct_var.get()
        self.nsoll = int(round(pct / 100.0 * NSOLL_100PCT)) & 0xFFFF
        self._pct_lbl.configure(text=f"{pct:.1f} %")
        self._nsoll_var.set(f"0x{self.nsoll:04X}")

    def _nsoll_from_entry(self, _e=None):
        try:
            self.nsoll = int(self._nsoll_var.get().strip(), 16) & 0xFFFF
            self._pct_var.set(self.nsoll * 100.0 / NSOLL_100PCT)
            self._pct_lbl.configure(text=f"{self._pct_var.get():.1f} %")
        except ValueError:
            pass
        self._nsoll_var.set(f"0x{self.nsoll:04X}")

    # ── input update (called via after() from worker) ──
    def update_inputs(self, zsw1: int, nist: int):
        self._zsw1_var.set(f"0x{zsw1:04X}")
        self._zbits_var.set(decode_zsw1(zsw1))
        # NIST is signed 16-bit (negative = reverse)
        s = nist - 0x10000 if nist >= 0x8000 else nist
        self._nist_var.set(f"0x{nist:04X}  ({s * 100.0 / NSOLL_100PCT:.1f} %)")


class VfdTcpClient(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VFD Control — Gateway TCP/UDP Client")
        self.geometry("560x560")

        self._sock = None
        self._connected = False
        self._worker = None
        self._panels = []
        self._tx = 0
        self._rx = 0
        # round-trip latency (network delay) stats, milliseconds
        self._rtt = 0.0
        self._rtt_min = float("inf")
        self._rtt_max = 0.0
        self._rtt_avg = 0.0
        self._lock = threading.Lock()

        self._build()
        self._rebuild_panels()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()

    def _build(self):
        hdr = tk.Frame(self, bg="#5e35b1", height=42); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="VFD CONTROL — TCP/UDP CLIENT", bg="#5e35b1", fg="white",
                 font=("Arial", 13, "bold")).pack(side="left", padx=12, pady=8)

        conn = ttk.Frame(self); conn.pack(fill="x", padx=10, pady=6)
        ttk.Label(conn, text="Host:").pack(side="left")
        self._host_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(conn, textvariable=self._host_var, width=14).pack(side="left", padx=4)
        ttk.Label(conn, text="Port:").pack(side="left")
        self._port_var = tk.StringVar(value="5000")
        ttk.Entry(conn, textvariable=self._port_var, width=6).pack(side="left", padx=4)
        self._proto_var = tk.StringVar(value="TCP")
        ttk.Combobox(conn, textvariable=self._proto_var, values=("TCP", "UDP", "STM"),
                     state="readonly", width=5).pack(side="left", padx=4)
        ttk.Label(conn, text="Devices:").pack(side="left", padx=(8, 0))
        self._ndev_var = tk.IntVar(value=1)
        ttk.Spinbox(conn, from_=1, to=8, width=4, textvariable=self._ndev_var,
                    command=self._rebuild_panels).pack(side="left", padx=4)
        # bytes/device — must match the gateway's telegram (e.g. Tel1=4, Tel111=24,
        # Free PZD-32/32=64). Only the first word pair (STW1/NSOLL, ZSW1/NIST) is
        # driven from the GUI; the rest is zero-padded / ignored.
        ttk.Label(conn, text="Out B:").pack(side="left", padx=(8, 0))
        self._outb_var = tk.IntVar(value=4)
        ttk.Spinbox(conn, from_=4, to=512, increment=2, width=5,
                    textvariable=self._outb_var).pack(side="left", padx=2)
        ttk.Label(conn, text="In B:").pack(side="left")
        self._inb_var = tk.IntVar(value=4)
        ttk.Spinbox(conn, from_=4, to=512, increment=2, width=5,
                    textvariable=self._inb_var).pack(side="left", padx=2)
        self._conn_btn = ttk.Button(conn, text="Connect", command=self._toggle)
        self._conn_btn.pack(side="left", padx=8)

        st = ttk.Frame(self); st.pack(fill="x", padx=10)
        self._status_var = tk.StringVar(value="● Disconnected")
        self._status_lbl = ttk.Label(st, textvariable=self._status_var,
                                     font=("Arial", 10, "bold"), foreground="red")
        self._status_lbl.pack(side="left")
        self._stat_var = tk.StringVar(value="")
        ttk.Label(st, textvariable=self._stat_var, foreground="gray").pack(side="right")

        # scrollable device area
        body = ttk.Frame(self); body.pack(fill="both", expand=True, padx=10, pady=6)
        self._canvas = tk.Canvas(body, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._inner = ttk.Frame(self._canvas)
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _rebuild_panels(self):
        if self._connected:
            return  # don't change frame size mid-session
        for p in self._panels:
            p.destroy()
        self._panels = []
        n = max(1, int(self._ndev_var.get()))
        for i in range(n):
            p = DevicePanel(self._inner, i)
            p.pack(fill="x", pady=4)
            self._panels.append(p)

    # ── connection ──
    def _toggle(self):
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        host = self._host_var.get().strip()
        try:
            port = int(self._port_var.get())
        except ValueError:
            self._status_var.set("● Bad port"); return
        proto = self._proto_var.get()
        try:
            if proto in ("TCP", "STM"):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((host, port))
                s.settimeout(1.0)
            else:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(1.0)
                s._udp_target = (host, port)
        except OSError as e:
            self._status_var.set(f"● Connect failed: {e}")
            return
        self._sock = s
        self._connected = True
        with self._lock:
            self._rtt = self._rtt_avg = self._rtt_max = 0.0
            self._rtt_min = float("inf")
        self._conn_btn.configure(text="Disconnect")
        self._status_var.set(f"● Connected {proto} {host}:{port}")
        self._status_lbl.configure(foreground="green")
        self._worker = threading.Thread(target=self._poll_loop, args=(proto,), daemon=True)
        self._worker.start()

    def _disconnect(self):
        self._connected = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass
            self._sock = None
        self._conn_btn.configure(text="Connect")
        self._status_var.set("● Disconnected")
        self._status_lbl.configure(foreground="red")

    def _poll_loop(self, proto):
        n = len(self._panels)
        # per-device output/input byte counts must match the gateway's telegram
        out_b = max(4, int(self._outb_var.get()))
        in_b = max(4, int(self._inb_var.get()))
        out_size = n * out_b
        inp_size = n * in_b
        sock = self._sock
        while self._connected and sock is not None:
            try:
                # STW1/NSOLL big-endian (Profinet wire order) in the first
                # word pair; rest of each device's slot zero-padded.
                frame = b"".join(
                    struct.pack(">HH", p.stw1 & 0xFFFF, p.nsoll & 0xFFFF).ljust(out_b, b"\x00")
                    for p in self._panels)
                t0 = time.perf_counter()
                if proto == "STM":
                    # [4B BE length][frame] both directions
                    sock.sendall(struct.pack(">I", len(frame)) + frame)
                    hdr = self._recv_exact(sock, 4)
                    if hdr is None:
                        break
                    ln = struct.unpack(">I", hdr)[0]
                    data = self._recv_exact(sock, ln)
                    if data is None:
                        break
                elif proto == "TCP":
                    sock.sendall(frame)
                    data = self._recv_exact(sock, inp_size)
                    if data is None:
                        break
                else:
                    sock.sendto(frame, sock._udp_target)
                    data, _ = sock.recvfrom(4096)
                rtt = (time.perf_counter() - t0) * 1000.0   # network delay, ms
                with self._lock:
                    self._tx += 1; self._rx += 1
                    self._rtt = rtt
                    self._rtt_min = min(self._rtt_min, rtt)
                    self._rtt_max = max(self._rtt_max, rtt)
                    self._rtt_avg = rtt if self._rtt_avg == 0 else self._rtt_avg * 0.9 + rtt * 0.1
                # first word pair of each device's input slot = ZSW1 / NIST
                vals = []
                for i in range(n):
                    off = i * in_b
                    if off + 4 <= len(data):
                        z, nist = struct.unpack_from(">HH", data, off)
                        vals.append((z, nist))
                    else:
                        vals.append((0, 0))
                self.after(0, self._apply_inputs, vals)
            except socket.timeout:
                continue
            except OSError:
                break
            time.sleep(0.03)
        self.after(0, self._disconnect)

    def _apply_inputs(self, vals):
        for p, (z, nist) in zip(self._panels, vals):
            p.update_inputs(z, nist)

    @staticmethod
    def _recv_exact(sock, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except socket.timeout:
                if buf:
                    continue
                return None
        return buf

    def _refresh_status(self):
        with self._lock:
            if self._rtt_min == float("inf"):
                rtt = "delay: —"
            else:
                rtt = (f"delay(ms) cur {self._rtt:.2f} / avg {self._rtt_avg:.2f} / "
                       f"min {self._rtt_min:.2f} / max {self._rtt_max:.2f}")
            self._stat_var.set(f"tx={self._tx}  rx={self._rx}    {rtt}")
        self.after(300, self._refresh_status)

    def _on_close(self):
        self._disconnect()
        self.destroy()


if __name__ == "__main__":
    VfdTcpClient().mainloop()
