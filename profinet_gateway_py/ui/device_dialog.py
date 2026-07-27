"""device_dialog.py — Double-click device configuration dialog."""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
from typing import Optional

from config import DeviceConfig
from gsdml import GSDMLDevice


class DeviceDialog(tk.Toplevel):
    def __init__(self, parent, dev_cfg: DeviceConfig, gsdml: GSDMLDevice,
                 pn_ctrl, adapter: str):
        super().__init__(parent)
        self.title(f"Device Configuration — {gsdml.device_name}")
        self.resizable(False, False)
        self.grab_set()

        self._cfg    = dev_cfg
        self._gsdml  = gsdml
        self._pn     = pn_ctrl
        self._adapter = adapter
        self._result  = False
        self._scan_result: list = []

        self._build_ui()
        self._load_values()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window()

    @property
    def accepted(self) -> bool:
        return self._result

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # ── Identity section ──────────────────────────────────────────────────
        id_frame = ttk.LabelFrame(self, text="Device Identity")
        id_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        ttk.Label(id_frame, text="Station Name:").grid(row=0, column=0, sticky="w", **pad)
        self._name_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self._name_var, width=32).grid(row=0, column=1, **pad)

        ttk.Label(id_frame, text="IP Address:").grid(row=1, column=0, sticky="w", **pad)
        self._ip_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self._ip_var, width=18).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(id_frame, text="Subnet Mask:").grid(row=2, column=0, sticky="w", **pad)
        self._subnet_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self._subnet_var, width=18).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(id_frame, text="Gateway:").grid(row=3, column=0, sticky="w", **pad)
        self._gw_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self._gw_var, width=18).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(id_frame, text="MAC:").grid(row=4, column=0, sticky="w", **pad)
        self._mac_var = tk.StringVar()
        self._mac_entry = ttk.Entry(id_frame, textvariable=self._mac_var, width=20, state="readonly")
        self._mac_entry.grid(row=4, column=1, sticky="w", **pad)

        scan_btn = ttk.Button(id_frame, text="Scan Network", command=self._on_scan)
        scan_btn.grid(row=4, column=2, **pad)

        self._scan_status = tk.StringVar(value="")
        ttk.Label(id_frame, textvariable=self._scan_status, foreground="gray").grid(
            row=5, column=0, columnspan=3, sticky="w", **pad)

        # ── Module / Slot section ─────────────────────────────────────────────
        mod_frame = ttk.LabelFrame(self, text="Module Configuration")
        mod_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        ttk.Label(mod_frame, text="Slot:").grid(row=0, column=0, sticky="w", **pad)
        self._slot_var = tk.IntVar(value=1)
        ttk.Spinbox(mod_frame, from_=1, to=64, textvariable=self._slot_var, width=6).grid(
            row=0, column=1, sticky="w", **pad)

        ttk.Label(mod_frame, text="Sub-Slot:").grid(row=0, column=2, sticky="w", **pad)
        self._subslot_var = tk.IntVar(value=1)
        ttk.Spinbox(mod_frame, from_=1, to=64, textvariable=self._subslot_var, width=6).grid(
            row=0, column=3, sticky="w", **pad)

        ttk.Label(mod_frame, text="Module (Slot 1):").grid(row=1, column=0, sticky="w", **pad)
        self._module_var = tk.StringVar()
        self._module_combo = ttk.Combobox(mod_frame, textvariable=self._module_var,
                                           state="readonly", width=34)
        self._module_combo.grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        self._module_combo.bind("<<ComboboxSelected>>", self._on_module_change)

        ttk.Label(mod_frame, text="Submodule:").grid(row=2, column=0, sticky="w", **pad)
        self._submod_var = tk.StringVar()
        self._submod_combo = ttk.Combobox(mod_frame, textvariable=self._submod_var,
                                           state="readonly", width=34)
        self._submod_combo.grid(row=2, column=1, columnspan=3, sticky="w", **pad)
        self._submod_combo.bind("<<ComboboxSelected>>", self._on_submodule_change)

        self._io_info_var = tk.StringVar(value="")
        ttk.Label(mod_frame, textvariable=self._io_info_var, foreground="blue").grid(
            row=3, column=0, columnspan=4, sticky="w", **pad)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Apply & Close", command=self._on_apply).pack(side="left", padx=8)

        # Populate module combos
        self._populate_modules()

    def _populate_modules(self):
        names = [f"0x{m.ident:08X} — {m.name}" for m in self._gsdml.modules]
        self._module_combo["values"] = names
        self._modules = self._gsdml.modules
        self._submodules = []

    def _on_module_change(self, _event=None):
        idx = self._module_combo.current()
        if idx < 0 or idx >= len(self._modules):
            return
        mod = self._modules[idx]
        self._submodules = mod.submodules
        names = [f"0x{s.ident:08X} — {s.name}" for s in mod.submodules]
        self._submod_combo["values"] = names
        if names:
            self._submod_combo.current(0)
            self._on_submodule_change()

    def _on_submodule_change(self, _event=None):
        idx = self._submod_combo.current()
        if idx < 0 or idx >= len(self._submodules):
            return
        sub = self._submodules[idx]
        self._io_info_var.set(
            f"IO Data: {sub.output_length}B out (ctrl→dev)  /  {sub.input_length}B in (dev→ctrl)"
        )

    def _load_values(self):
        cfg = self._cfg
        self._name_var.set(cfg.station_name)
        self._ip_var.set(cfg.ip)
        self._subnet_var.set(cfg.subnet)
        self._gw_var.set(cfg.gateway)
        self._mac_entry.configure(state="normal")
        self._mac_var.set(cfg.mac)
        self._mac_entry.configure(state="readonly")
        self._slot_var.set(cfg.slot)
        self._subslot_var.set(cfg.subslot)

        # Select module
        for i, m in enumerate(self._modules):
            if m.ident == cfg.module_ident:
                self._module_combo.current(i)
                self._on_module_change()
                # Select submodule
                for j, s in enumerate(self._submodules):
                    if s.ident == cfg.submodule_ident:
                        self._submod_combo.current(j)
                        self._on_submodule_change()
                        break
                break
        else:
            if self._modules:
                self._module_combo.current(0)
                self._on_module_change()

    # ── DCP Scan ──────────────────────────────────────────────────────────────
    def _on_scan(self):
        self._scan_status.set("Scanning… (2s)")
        self.update()
        t = threading.Thread(target=self._do_scan, daemon=True)
        t.start()

    def _do_scan(self):
        try:
            devices = self._pn.dcp_discover(self._adapter, timeout_ms=2000)
        except Exception as e:
            self.after(0, lambda: self._scan_status.set(f"Scan error: {e}"))
            return
        self._scan_result = devices
        self.after(0, self._show_scan_result)

    def _show_scan_result(self):
        devices = self._scan_result
        if not devices:
            self._scan_status.set("No devices found")
            return

        win = tk.Toplevel(self)
        win.title("Discovered Devices")
        win.grab_set()

        cols = ("name", "ip", "mac")
        tv = ttk.Treeview(win, columns=cols, show="headings", height=8)
        tv.heading("name", text="Station Name")
        tv.heading("ip",   text="IP Address")
        tv.heading("mac",  text="MAC")
        tv.column("name", width=200)
        tv.column("ip",   width=120)
        tv.column("mac",  width=140)
        tv.pack(fill="both", expand=True, padx=8, pady=8)

        from profinet_ctrl import ProfinetCtrl
        for d in devices:
            name = d.name_of_station.decode(errors="replace").rstrip("\x00")
            ip   = d.ip_str.decode(errors="replace").rstrip("\x00")
            mac  = ProfinetCtrl.mac_bytes_to_str(d.mac)
            tv.insert("", "end", values=(name, ip, mac))

        def _select():
            sel = tv.selection()
            if not sel:
                return
            vals = tv.item(sel[0])["values"]
            self._name_var.set(vals[0])
            self._ip_var.set(vals[1])
            self._mac_entry.configure(state="normal")
            self._mac_var.set(vals[2])
            self._mac_entry.configure(state="readonly")
            self._scan_status.set(f"Selected: {vals[0]}")
            win.destroy()

        ttk.Button(win, text="Select", command=_select).pack(pady=6)
        self._scan_status.set(f"Found {len(devices)} device(s)")

    # ── Apply / Cancel ────────────────────────────────────────────────────────
    def _on_apply(self):
        # Validate IP — must be a clean 4-octet dotted quad (the DLL rejects
        # anything else with PN_ERR_INVALID_PARAM).
        ip = self._ip_var.get().strip()
        if not ip:
            messagebox.showerror("Validation", "IP address is required.", parent=self)
            return
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            messagebox.showerror(
                "Validation",
                f"IP address '{ip}' is not valid.\n\n"
                f"Use four numbers separated by dots, e.g. 127.0.0.1",
                parent=self)
            return

        cfg = self._cfg
        cfg.station_name = self._name_var.get().strip()
        cfg.ip      = ip
        cfg.subnet  = self._subnet_var.get().strip() or "255.255.255.0"
        cfg.gateway = self._gw_var.get().strip() or "192.168.1.1"
        cfg.mac     = self._mac_var.get().strip()
        cfg.slot    = self._slot_var.get()
        cfg.subslot = self._subslot_var.get()

        mod_idx = self._module_combo.current()
        sub_idx = self._submod_combo.current()
        if mod_idx >= 0 and mod_idx < len(self._modules):
            mod = self._modules[mod_idx]
            cfg.module_ident = mod.ident
            cfg.module_name  = mod.name
            if sub_idx >= 0 and sub_idx < len(self._submodules):
                sub = self._submodules[sub_idx]
                cfg.submodule_ident = sub.ident
                cfg.submodule_name  = sub.name
                cfg.input_length    = sub.input_length
                cfg.output_length   = sub.output_length

        self._result = True
        self.destroy()

    def _on_cancel(self):
        self._result = False
        self.destroy()
