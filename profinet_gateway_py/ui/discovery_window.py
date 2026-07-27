"""discovery_window.py — Network Discovery (TIA "Accessible devices" style).

Scan the network for Profinet devices, pick one, assign its IP address and
station name, and flash its identification LED. All operations are DCP and run
on worker threads so the UI never blocks.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading

from profinet_ctrl import ProfinetCtrl


class DiscoveryWindow(tk.Toplevel):
    def __init__(self, parent, pn_ctrl, cfg):
        super().__init__(parent)
        self.title("Network Discovery — Accessible Devices")
        self.geometry("760x480")
        self.minsize(680, 420)

        self._pn      = pn_ctrl
        self._cfg     = cfg
        self._adapter = cfg.adapter
        self._results = []          # list[ScanResult]

        self._build_ui()
        self.after(200, self._on_scan)   # auto-scan on open

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg="#00838f", height=42)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="NETWORK DISCOVERY", bg="#00838f", fg="white",
                 font=("Arial", 13, "bold")).pack(side="left", padx=12, pady=8)

        # Adapter selection row
        arow = ttk.Frame(self); arow.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(arow, text="Adapter:").pack(side="left")
        self._adapter_names = []
        self._adapter_display = tk.StringVar()
        self._adapter_combo = ttk.Combobox(arow, textvariable=self._adapter_display,
                                            state="readonly", width=58)
        self._adapter_combo.pack(side="left", padx=4)
        self._adapter_combo.bind("<<ComboboxSelected>>", self._on_adapter_select)
        ttk.Button(arow, text="Refresh", command=self._refresh_adapters).pack(side="left", padx=4)

        top = ttk.Frame(self); top.pack(fill="x", padx=10, pady=6)
        ttk.Button(top, text="Scan Network", command=self._on_scan).pack(side="left")
        self._status = tk.StringVar(value="Ready.")
        ttk.Label(top, textvariable=self._status, foreground="gray").pack(side="left", padx=12)

        self._refresh_adapters()

        # Device table
        cols = ("name", "ip", "mac", "vendor", "device")
        tv = ttk.Treeview(self, columns=cols, show="headings", height=9)
        for c, txt, w in (("name", "Station Name", 200), ("ip", "IP Address", 120),
                          ("mac", "MAC", 150), ("vendor", "Vendor", 140),
                          ("device", "Device ID", 90)):
            tv.heading(c, text=txt); tv.column(c, width=w)
        tv.pack(fill="both", expand=True, padx=10, pady=4)
        tv.bind("<<TreeviewSelect>>", self._on_select)
        self._tv = tv

        # Edit / action panel
        panel = ttk.LabelFrame(self, text="Selected Device")
        panel.pack(fill="x", padx=10, pady=6)
        pad = {"padx": 6, "pady": 3}

        ttk.Label(panel, text="Station Name:").grid(row=0, column=0, sticky="w", **pad)
        self._name_var = tk.StringVar()
        ttk.Entry(panel, textvariable=self._name_var, width=26).grid(row=0, column=1, **pad)
        ttk.Button(panel, text="Set Name", command=self._on_set_name).grid(row=0, column=2, **pad)

        ttk.Label(panel, text="IP Address:").grid(row=1, column=0, sticky="w", **pad)
        self._ip_var = tk.StringVar()
        ttk.Entry(panel, textvariable=self._ip_var, width=18).grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(panel, text="Set IP", command=self._on_set_ip).grid(row=1, column=2, **pad)

        ttk.Label(panel, text="Subnet:").grid(row=2, column=0, sticky="w", **pad)
        self._subnet_var = tk.StringVar(value="255.255.255.0")
        ttk.Entry(panel, textvariable=self._subnet_var, width=18).grid(row=2, column=1, sticky="w", **pad)
        ttk.Button(panel, text="Flash LED", command=self._on_flash).grid(row=2, column=2, **pad)

        ttk.Label(panel, text="Gateway:").grid(row=3, column=0, sticky="w", **pad)
        self._gw_var = tk.StringVar(value="0.0.0.0")
        ttk.Entry(panel, textvariable=self._gw_var, width=18).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(panel, text="MAC:").grid(row=3, column=2, sticky="e", **pad)
        self._mac_var = tk.StringVar()
        ttk.Label(panel, textvariable=self._mac_var, foreground="blue").grid(
            row=3, column=3, sticky="w", **pad)

    # ── adapter dropdown ────────────────────────────────────────────────────
    def _refresh_adapters(self):
        verbose = self._pn.enumerate_adapters_verbose()
        self._adapter_names = [n for (n, _d) in verbose]
        labels = [f"{d}   —   {n}" for (n, d) in verbose]
        self._adapter_combo["values"] = labels
        if not self._adapter_names:
            self._status.set("No adapters found — is Npcap installed?")
            return
        # prefer the configured adapter, else the first
        want = self._pn.resolve_adapter(self._adapter) if self._adapter else ""
        idx = self._adapter_names.index(want) if want in self._adapter_names else 0
        self._adapter = self._adapter_names[idx]
        self._adapter_combo.current(idx)
        self._adapter_display.set(labels[idx])

    def _on_adapter_select(self, _e=None):
        idx = self._adapter_combo.current()
        if 0 <= idx < len(self._adapter_names):
            self._adapter = self._adapter_names[idx]
            self._status.set(f"Adapter: {self._adapter}")

    # ── selection ───────────────────────────────────────────────────────────
    def _selected(self):
        sel = self._tv.selection()
        if not sel:
            return None
        idx = self._tv.index(sel[0])
        if 0 <= idx < len(self._results):
            return self._results[idx]
        return None

    def _on_select(self, _e=None):
        d = self._selected()
        if not d:
            return
        self._name_var.set(d.name_of_station.decode(errors="replace").rstrip("\x00"))
        self._ip_var.set(d.ip_str.decode(errors="replace").rstrip("\x00"))
        self._mac_var.set(ProfinetCtrl.mac_bytes_to_str(d.mac))
        if d.netmask:
            self._subnet_var.set(d.netmask)
        if d.gateway:
            self._gw_var.set(d.gateway)

    # ── scan ────────────────────────────────────────────────────────────────
    def _on_scan(self):
        self._status.set("Scanning…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            devices = self._pn.dcp_discover(self._adapter, timeout_ms=3000)
        except Exception as e:
            self.after(0, lambda: self._status.set(f"Scan error: {e}"))
            return
        self.after(0, self._fill_table, devices)

    def _fill_table(self, devices):
        self._results = devices
        self._tv.delete(*self._tv.get_children())
        for d in devices:
            self._tv.insert("", "end", values=(
                d.name_of_station.decode(errors="replace").rstrip("\x00"),
                d.ip_str.decode(errors="replace").rstrip("\x00"),
                ProfinetCtrl.mac_bytes_to_str(d.mac),
                d.vendor_name or (f"0x{d.vendor_id:04X}" if d.vendor_id else ""),
                f"0x{d.device_id:04X}" if d.device_id else ""))
        self._status.set(f"Found {len(devices)} device(s)." if devices
                         else "No devices found.")

    # ── actions (each on a worker thread) ───────────────────────────────────
    def _run(self, label, fn):
        d = self._selected()
        if not d:
            messagebox.showinfo("Network Discovery", "Select a device first.", parent=self)
            return
        mac = ProfinetCtrl.mac_bytes_to_str(d.mac)
        self._status.set(f"{label}…")

        def worker():
            ok = False
            try:
                ok = fn(mac)
            except Exception as e:
                self.after(0, lambda: self._status.set(f"{label} error: {e}"))
                return
            self.after(0, lambda: self._status.set(
                f"{label}: {'OK' if ok else 'no response'}"))
            if ok and label != "Flash LED":
                self.after(600, self._on_scan)   # refresh after a change
        threading.Thread(target=worker, daemon=True).start()

    def _on_set_name(self):
        name = self._name_var.get().strip()
        if not name:
            messagebox.showerror("Network Discovery", "Station name required.", parent=self)
            return
        self._run("Set Name", lambda mac: self._pn.dcp_set_name(self._adapter, mac, name))

    def _on_set_ip(self):
        ip = self._ip_var.get().strip()
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            messagebox.showerror("Network Discovery",
                                 f"IP '{ip}' is not a valid 4-part address.", parent=self)
            return
        sn = self._subnet_var.get().strip() or "255.255.255.0"
        gw = self._gw_var.get().strip() or "0.0.0.0"
        self._run("Set IP", lambda mac: self._pn.dcp_set_ip(self._adapter, mac, ip, sn, gw))

    def _on_flash(self):
        self._run("Flash LED", lambda mac: self._pn.dcp_flash_led(self._adapter, mac, 3000))
