"""gateway_monitor.py — live TCP/UDP/STM traffic monitor for the gateway.

Shows the raw bytes flowing through the gateway in both directions
(client → EXE outputs, and EXE → client inputs), with frame/byte counters,
throughput, and a live hex dump of the last frame each way.
"""

import time
import tkinter as tk
from tkinter import ttk


def _hexdump(data: bytes, max_bytes: int = 128) -> str:
    if not data:
        return "(no data yet)"
    out = []
    shown = data[:max_bytes]
    for off in range(0, len(shown), 16):
        chunk = shown[off:off + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{off:04X}  {hexs:<47}  {asci}")
    if len(data) > max_bytes:
        out.append(f"… (+{len(data) - max_bytes} more bytes)")
    return "\n".join(out)


class GatewayMonitorWindow(tk.Toplevel):
    def __init__(self, parent, gw_server):
        super().__init__(parent)
        self.title("Gateway Monitor")
        self.geometry("720x560")
        self._gw = gw_server
        self._prev = None          # (t, rx_bytes, tx_bytes) for rate calc
        self._build()
        self._refresh()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        hdr = tk.Frame(self, bg="#37474f", height=42); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="GATEWAY MONITOR", bg="#37474f", fg="white",
                 font=("Arial", 13, "bold")).pack(side="left", padx=12, pady=8)
        self._hdr_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._hdr_var, bg="#37474f", fg="#b0bec5",
                 font=("Arial", 9)).pack(side="right", padx=12)

        self._status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._status_var, font=("Arial", 10, "bold")).pack(
            anchor="w", padx=12, pady=4)

        # RX panel (client -> EXE)
        self._rx_stat = tk.StringVar()
        self._rx_hex = self._make_panel("Client → EXE   (outputs written to devices)", self._rx_stat)
        # TX panel (EXE -> client)
        self._tx_stat = tk.StringVar()
        self._tx_hex = self._make_panel("EXE → Client   (inputs sent to client)", self._tx_stat)

        ttk.Button(self, text="Close", command=self.destroy).pack(pady=6)

    def _make_panel(self, title, stat_var):
        f = ttk.LabelFrame(self, text=title)
        f.pack(fill="both", expand=True, padx=10, pady=4)
        ttk.Label(f, textvariable=stat_var, foreground="#00695c").pack(anchor="w", padx=6, pady=2)
        txt = tk.Text(f, height=8, font=("Courier", 9), wrap="none")
        txt.pack(fill="both", expand=True, padx=6, pady=4)
        txt.configure(state="disabled")
        return txt

    def _set_text(self, widget, s):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, s)
        widget.configure(state="disabled")

    def _refresh(self):
        if not self.winfo_exists():
            return
        m = self._gw.get_monitor()
        proto = m["protocol"]
        self._hdr_var.set(f"{proto}  {m['bind']}:{m['port']}")
        if m["running"]:
            client = m["client"]
            if client:
                self._status_var.set(f"● {proto} listening — client {client[0]}:{client[1]} connected")
            else:
                self._status_var.set(f"● {proto} listening — waiting for client")
        else:
            self._status_var.set("● Gateway stopped")

        # throughput from byte deltas
        now = time.perf_counter()
        rx_bps = tx_bps = 0.0
        if self._prev:
            dt = now - self._prev[0]
            if dt > 0:
                rx_bps = (m["rx_bytes"] - self._prev[1]) / dt
                tx_bps = (m["tx_bytes"] - self._prev[2]) / dt
        self._prev = (now, m["rx_bytes"], m["tx_bytes"])

        self._rx_stat.set(
            f"frames={m['rx_frames']}   bytes={m['rx_bytes']}   "
            f"rate={rx_bps/1024:.1f} KB/s   last frame={len(m['last_rx'])} B")
        self._tx_stat.set(
            f"frames={m['tx_frames']}   bytes={m['tx_bytes']}   "
            f"rate={tx_bps/1024:.1f} KB/s   last frame={len(m['last_tx'])} B")
        self._set_text(self._rx_hex, _hexdump(m["last_rx"]))
        self._set_text(self._tx_hex, _hexdump(m["last_tx"]))

        self.after(300, self._refresh)
