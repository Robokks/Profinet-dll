"""io_diagnostic.py — Live IO diagnostic window with force-write capability."""

import tkinter as tk
from tkinter import ttk
from typing import List


class IODiagnosticWindow(tk.Toplevel):
    def __init__(self, parent, pn_ctrl, gw_server):
        super().__init__(parent)
        self.title("IO Diagnostic")
        self.geometry("700x520")
        self.resizable(True, True)

        self._pn = pn_ctrl
        self._gw = gw_server
        self._cards: List[_DeviceCard] = []

        self._build_ui()
        self._refresh()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self):
        # Scrollable frame
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._inner = ttk.Frame(canvas)

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)

        win_id = canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))

        self._populate_cards()

        # Bottom close button
        ttk.Button(self, text="Close", command=self.destroy).pack(pady=6)

    def _populate_cards(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._cards.clear()

        n = self._pn.device_count()
        if n == 0:
            ttk.Label(self._inner,
                      text="No devices configured.\nGo to Configuration to add devices.",
                      foreground="gray", font=("Arial", 11)).pack(pady=40)
            return

        for i in range(n):
            ds = self._pn.device_state(i)
            if ds is None:
                continue
            card = _DeviceCard(self._inner, i, ds, self._pn, self._gw)
            card.pack(fill="x", padx=10, pady=6)
            self._cards.append(card)

    def _refresh(self):
        if not self.winfo_exists():
            return
        # Re-populate if device count changed
        if len(self._cards) != self._pn.device_count():
            self._populate_cards()
        for card in self._cards:
            card.refresh()
        self.after(250, self._refresh)


class _DeviceCard(ttk.LabelFrame):
    def __init__(self, parent, idx: int, ds, pn_ctrl, gw_server):
        self._idx   = idx
        self._ds    = ds
        self._pn    = pn_ctrl
        self._gw    = gw_server
        dc = ds.config

        name = dc.station_name or f"Device {idx}"
        super().__init__(parent, text=f"  {name}  ({dc.ip})")
        self._build()

    def _build(self):
        pad = {"padx": 8, "pady": 3}

        # Status row
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=4, sticky="ew", **pad)
        self._status_var = tk.StringVar(value="● CONNECTING")
        self._status_lbl = ttk.Label(top, textvariable=self._status_var,
                                      font=("Arial", 10, "bold"))
        self._status_lbl.pack(side="left")

        stats_btn = ttk.Button(top, text="Stats", command=self._show_stats)
        stats_btn.pack(side="right", padx=4)

        # Active telegram(s) — reflects the current configuration
        self._tel_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self._tel_var, foreground="#5e35b1",
                  font=("Arial", 8)).pack(side="left", padx=14)

        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=6)

        # OUTPUTS section
        ttk.Label(self, text="OUTPUTS  (Controller → Device)",
                  font=("Arial", 9, "bold"), foreground="#1a4e8c").grid(
            row=2, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(self, text="STW1:").grid(row=3, column=0, sticky="w", **pad)
        self._stw1_var = tk.StringVar(value="0x0000")
        ttk.Label(self, textvariable=self._stw1_var, width=8,
                  relief="sunken", anchor="center").grid(row=3, column=1, **pad)
        self._force_stw1_var = tk.StringVar(value="0x047F")
        ttk.Entry(self, textvariable=self._force_stw1_var, width=8).grid(row=3, column=2, **pad)
        ttk.Button(self, text="Force STW1",
                   command=lambda: self._force()).grid(row=3, column=3, **pad)

        ttk.Label(self, text="NSOLL:").grid(row=4, column=0, sticky="w", **pad)
        self._nsoll_var = tk.StringVar(value="0x0000")
        ttk.Label(self, textvariable=self._nsoll_var, width=8,
                  relief="sunken", anchor="center").grid(row=4, column=1, **pad)
        self._force_nsoll_var = tk.StringVar(value="0x2000")
        ttk.Entry(self, textvariable=self._force_nsoll_var, width=8).grid(row=4, column=2, **pad)
        ttk.Button(self, text="Force NSOLL",
                   command=lambda: self._force_nsoll()).grid(row=4, column=3, **pad)

        ttk.Separator(self, orient="horizontal").grid(
            row=5, column=0, columnspan=4, sticky="ew", padx=6)

        # INPUTS section
        ttk.Label(self, text="INPUTS  (Device → Controller)",
                  font=("Arial", 9, "bold"), foreground="#1a6b1a").grid(
            row=6, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(self, text="ZSW1:").grid(row=7, column=0, sticky="w", **pad)
        self._zsw1_var = tk.StringVar(value="0x0000")
        self._zsw1_lbl = ttk.Label(self, textvariable=self._zsw1_var, width=8,
                                    relief="sunken", anchor="center", foreground="green")
        self._zsw1_lbl.grid(row=7, column=1, **pad)
        self._zsw1_bits = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._zsw1_bits, foreground="gray",
                  font=("Arial", 8)).grid(row=7, column=2, columnspan=2, sticky="w", **pad)

        ttk.Label(self, text="NIST:").grid(row=8, column=0, sticky="w", **pad)
        self._nist_var = tk.StringVar(value="0x0000")
        ttk.Label(self, textvariable=self._nist_var, width=8,
                  relief="sunken", anchor="center", foreground="green").grid(row=8, column=1, **pad)
        self._nist_pct = tk.StringVar(value="0%")
        ttk.Label(self, textvariable=self._nist_pct, foreground="gray").grid(
            row=8, column=2, **pad)

    def refresh(self):
        connected = self._pn.is_connected(self._idx)
        if connected:
            self._status_var.set("● CONNECTED")
            self._status_lbl.configure(foreground="green")
        else:
            ds = self._pn.device_state(self._idx)
            err = ds.error if ds else ""
            self._status_var.set(f"● DISCONNECTED  {err}")
            self._status_lbl.configure(foreground="red")

        ds = self._pn.device_state(self._idx)
        if ds:
            # keep header + telegram info in sync with the live config
            dc = ds.config
            self.configure(text=f"  {dc.station_name or f'Device {self._idx}'}  ({dc.ip})")
            self._tel_var.set(self._telegram_text(dc))
            self._stw1_var.set(f"0x{ds.stw1:04X}")
            self._nsoll_var.set(f"0x{ds.nsoll:04X}")
            self._zsw1_var.set(f"0x{ds.zsw1:04X}")
            self._nist_var.set(f"0x{ds.nist:04X}")
            self._zsw1_bits.set(_decode_zsw1(ds.zsw1))
            pct = round(ds.nist * 100 / 0x4000) if ds.nist else 0
            self._nist_pct.set(f"{pct}%")

    @staticmethod
    def _telegram_text(dc) -> str:
        dos = dc.effective_drive_objects()
        parts = []
        for d in dos:
            tel = d.submodule_name or "(no telegram)"
            mod = f"{d.module_name} / " if d.module_name else ""
            parts.append(f"{mod}{tel} [{d.input_length}/{d.output_length} B]")
        total = f"   Σ in {dc.total_input_length()} / out {dc.total_output_length()} B"
        return "Telegram:  " + "   +   ".join(parts) + (total if len(dos) > 1 else "")

    def _force(self):
        try:
            stw1 = int(self._force_stw1_var.get(), 16)
        except ValueError:
            return
        try:
            nsoll = int(self._force_nsoll_var.get(), 16)
        except ValueError:
            nsoll = 0
        self._pn.write_outputs(self._idx, stw1, nsoll)
        # Also update gateway io_data
        self._gw.set_zsw1_nist(self._idx, stw1, nsoll)

    def _force_nsoll(self):
        ds = self._pn.device_state(self._idx)
        stw1 = ds.stw1 if ds else 0
        try:
            nsoll = int(self._force_nsoll_var.get(), 16)
        except ValueError:
            return
        self._pn.write_outputs(self._idx, stw1, nsoll)

    def _show_stats(self):
        stats = self._pn.get_stats(self._idx)
        if not stats:
            return
        win = tk.Toplevel(self)
        win.title(f"Stats — {self._ds.config.station_name or 'Device'}")
        win.resizable(False, False)
        rows = [
            ("Frames Sent",       stats.frames_sent),
            ("Frames Received",   stats.frames_received),
            ("Missed Cycles",     stats.missed_cycles),
            ("Watchdog Timeouts", stats.watchdog_timeouts),
            ("Cycle Counter",     stats.cycle_counter),
            ("Connected",         bool(stats.connected)),
            ("Cyclic Running",    bool(stats.cyclic_running)),
        ]
        for i, (label, val) in enumerate(rows):
            ttk.Label(win, text=label + ":").grid(row=i, column=0, sticky="w", padx=10, pady=3)
            ttk.Label(win, text=str(val)).grid(row=i, column=1, sticky="w", padx=10, pady=3)
        ttk.Button(win, text="Close", command=win.destroy).grid(
            row=len(rows), column=0, columnspan=2, pady=8)


def _decode_zsw1(zsw1: int) -> str:
    bits = []
    if zsw1 & (1 << 0):  bits.append("RDY_SW")
    if zsw1 & (1 << 1):  bits.append("RDY")
    if zsw1 & (1 << 2):  bits.append("RUN")
    if zsw1 & (1 << 3):  bits.append("FAULT")
    if zsw1 & (1 << 4):  bits.append("NO_COAST")
    if zsw1 & (1 << 5):  bits.append("NO_QSTOP")
    if zsw1 & (1 << 8):  bits.append("SPD_DEV")
    if zsw1 & (1 << 10): bits.append("SPD_OK")
    return "  ".join(bits) if bits else "—"
