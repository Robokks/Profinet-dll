"""device_dialog.py — SYCON.net-style device configuration dialog.

Left navigation (Configuration ▸ General / Modules; Description ▸ Device Info /
Module Info / GSDML Viewer) and a Modules rack where each slot holds a Drive
Object (module) with a telegram (submodule), with running octet totals and a
per-word submodule-details table.
"""

import os
import threading
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, messagebox

from config import DeviceConfig, DriveObject
from gsdml import GSDMLDevice


class DeviceDialog(tk.Toplevel):
    def __init__(self, parent, dev_cfg: DeviceConfig, gsdml: GSDMLDevice,
                 pn_ctrl, adapter: str):
        super().__init__(parent)
        self.title(f"netDevice — Configuration  {gsdml.device_name}")
        self.geometry("940x660")
        self.minsize(820, 560)
        self.grab_set()

        self._cfg = dev_cfg
        self._gsdml = gsdml
        self._pn = pn_ctrl
        self._adapter = adapter
        self._result = False
        self._scan_result = []

        # working copy of the rack
        self._rack = [DriveObject(**vars(d))
                      for d in dev_cfg.effective_drive_objects()]

        self._build_ui()
        self._load_identity()
        self._refresh_rack()
        self._select_nav("Modules")
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window()

    @property
    def accepted(self) -> bool:
        return self._result

    # ── UI scaffold ──────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg="#1a4e8c", height=44); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text=f"IO Device:  {self._gsdml.device_name}", bg="#1a4e8c",
                 fg="white", font=("Arial", 11, "bold")).pack(side="left", padx=12, pady=4)
        tk.Label(hdr, text=f"Vendor 0x{self._gsdml.vendor_id:04X}   "
                           f"Device 0x{self._gsdml.device_id:04X}", bg="#1a4e8c",
                 fg="#aac8f0", font=("Arial", 9)).pack(side="right", padx=12)

        body = ttk.Frame(self); body.pack(fill="both", expand=True)

        # Navigation tree
        navf = ttk.LabelFrame(body, text="Navigation Area")
        navf.pack(side="left", fill="y", padx=6, pady=6)
        self._nav = ttk.Treeview(navf, show="tree", selectmode="browse")
        self._nav.column("#0", width=180, stretch=False)
        self._nav.pack(fill="y", expand=True, padx=2, pady=2)
        cfgn = self._nav.insert("", "end", text="Configuration", open=True)
        self._nav.insert(cfgn, "end", iid="General", text="  General")
        self._nav.insert(cfgn, "end", iid="Modules", text="  Modules")
        desn = self._nav.insert("", "end", text="Description", open=True)
        self._nav.insert(desn, "end", iid="Device Info", text="  Device Info")
        self._nav.insert(desn, "end", iid="Module Info", text="  Module Info")
        self._nav.insert(desn, "end", iid="GSDML Viewer", text="  GSDML Viewer")
        self._nav.bind("<<TreeviewSelect>>", self._on_nav)

        # Right: swappable panes
        self._panes_host = ttk.Frame(body)
        self._panes_host.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
        self._panes = {}
        self._panes["General"] = self._build_general()
        self._panes["Modules"] = self._build_modules()
        self._panes["Device Info"] = self._build_device_info()
        self._panes["Module Info"] = self._build_module_info()
        self._panes["GSDML Viewer"] = self._build_gsdml_viewer()

        # Bottom buttons
        btn = ttk.Frame(self); btn.pack(fill="x", pady=6)
        ttk.Button(btn, text="Cancel", command=self._on_cancel).pack(side="right", padx=6)
        ttk.Button(btn, text="Apply & Close", command=self._on_apply).pack(side="right")

    def _on_nav(self, _e=None):
        sel = self._nav.selection()
        if sel and sel[0] in self._panes:
            self._show_pane(sel[0])

    def _select_nav(self, name):
        self._nav.selection_set(name)
        self._show_pane(name)

    def _show_pane(self, name):
        for p in self._panes.values():
            p.pack_forget()
        self._panes[name].pack(fill="both", expand=True)
        if name == "GSDML Viewer":
            self._populate_gsdml_viewer()

    # ── General pane (identity + scan) ───────────────────────────────────────
    def _build_general(self):
        f = ttk.LabelFrame(self._panes_host, text="General")
        pad = {"padx": 8, "pady": 5}

        # Device variant (DeviceAccessPoint) — a GSDML holds many (CBE20 /
        # CU320-2 PN / CU310-2 PN, per firmware). Pick the one matching the
        # physical control unit.
        ttk.Label(f, text="Device variant:").grid(row=0, column=0, sticky="w", **pad)
        self._dap_ids = [d.id for d in self._gsdml.daps]
        self._dap_names = [d.name for d in self._gsdml.daps]
        self._dap_var = tk.StringVar()
        self._dap_combo = ttk.Combobox(f, textvariable=self._dap_var, state="readonly",
                                       width=38, values=self._dap_names)
        self._dap_combo.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        self._dap_combo.bind("<<ComboboxSelected>>", self._on_dap_change)
        if not self._dap_ids:
            self._dap_combo.configure(state="disabled")
            self._dap_var.set("(no variants in GSDML)")

        ttk.Label(f, text="Station Name:").grid(row=1, column=0, sticky="w", **pad)
        self._name_var = tk.StringVar()
        ttk.Entry(f, textvariable=self._name_var, width=32).grid(row=1, column=1, **pad)
        ttk.Button(f, text="Scan Network", command=self._on_scan).grid(row=1, column=2, **pad)
        ttk.Label(f, text="IP Address:").grid(row=2, column=0, sticky="w", **pad)
        self._ip_var = tk.StringVar()
        ttk.Entry(f, textvariable=self._ip_var, width=20).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(f, text="Subnet Mask:").grid(row=3, column=0, sticky="w", **pad)
        self._subnet_var = tk.StringVar()
        ttk.Entry(f, textvariable=self._subnet_var, width=20).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(f, text="Gateway:").grid(row=4, column=0, sticky="w", **pad)
        self._gw_var = tk.StringVar()
        ttk.Entry(f, textvariable=self._gw_var, width=20).grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(f, text="MAC:").grid(row=5, column=0, sticky="w", **pad)
        self._mac_var = tk.StringVar()
        self._mac_entry = ttk.Entry(f, textvariable=self._mac_var, width=22, state="readonly")
        self._mac_entry.grid(row=5, column=1, sticky="w", **pad)
        self._scan_status = tk.StringVar(value="")
        ttk.Label(f, textvariable=self._scan_status, foreground="gray").grid(
            row=6, column=0, columnspan=3, sticky="w", **pad)
        return f

    def _on_dap_change(self, _e=None):
        idx = self._dap_combo.current()
        if not (0 <= idx < len(self._dap_ids)):
            return
        self._cfg.dap_id = self._dap_ids[idx]
        self._gsdml.select_dap(self._cfg.dap_id)
        # Suggest the variant's default DNS station name if none set yet.
        if not self._name_var.get().strip():
            dns = self._gsdml.dap_dns_name(self._cfg.dap_id)
            if dns:
                self._name_var.set(dns)
        self._refresh_rack()
        if hasattr(self, "_info_variant_var"):
            self._info_variant_var.set(self._gsdml.dap_display_name(self._cfg.dap_id))

    # ── Modules pane (rack + details) ────────────────────────────────────────
    def _build_modules(self):
        f = ttk.Frame(self._panes_host)
        rack = ttk.LabelFrame(f, text="Modules")
        rack.pack(fill="both", expand=True, padx=2, pady=2)

        self._rack_tv = ttk.Treeview(rack, show="tree headings",
                                     columns=("subslot", "io"), height=10)
        self._rack_tv.heading("#0", text="Slot / Module")
        self._rack_tv.heading("subslot", text="Sub-Slot")
        self._rack_tv.heading("io", text="I/O (bytes)")
        self._rack_tv.column("#0", width=360)
        self._rack_tv.column("subslot", width=80, anchor="center")
        self._rack_tv.column("io", width=100, anchor="center")
        self._rack_tv.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb = ttk.Scrollbar(rack, command=self._rack_tv.yview)
        sb.pack(side="right", fill="y")
        self._rack_tv.configure(yscrollcommand=sb.set)
        self._rack_tv.bind("<<TreeviewSelect>>", self._on_rack_select)

        bar = ttk.Frame(f); bar.pack(fill="x", pady=3)
        ttk.Button(bar, text="Add Module", command=self._add_module).pack(side="left", padx=3)
        ttk.Button(bar, text="Add Submodule", command=self._add_submodule).pack(side="left", padx=3)
        ttk.Button(bar, text="Remove", command=self._remove_selected).pack(side="left", padx=3)
        self._len_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self._len_var, foreground="blue").pack(anchor="w", padx=4)

        det = ttk.LabelFrame(f, text="Submodule details")
        det.pack(fill="both", expand=True, padx=2, pady=4)
        self._det_tv = ttk.Treeview(det, show="headings",
                                    columns=("dir", "type", "text"), height=7)
        for c, t, w in (("dir", "Direction", 90), ("type", "Data type", 110),
                        ("text", "Text ID", 320)):
            self._det_tv.heading(c, text=t); self._det_tv.column(c, width=w)
        self._det_tv.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb2 = ttk.Scrollbar(det, command=self._det_tv.yview)
        sb2.pack(side="right", fill="y")
        self._det_tv.configure(yscrollcommand=sb2.set)
        return f

    def _build_device_info(self):
        f = ttk.LabelFrame(self._panes_host, text="Device Info")
        self._info_variant_var = tk.StringVar(
            value=self._gsdml.dap_display_name(getattr(self._cfg, "dap_id", "")))
        rows = [
            ("Device family", self._gsdml.vendor_name and self._gsdml.device_name
                              or self._gsdml.device_name),
            ("Vendor name", self._gsdml.vendor_name or "—"),
            ("Vendor ID", f"0x{self._gsdml.vendor_id:04X}"),
            ("Device ID", f"0x{self._gsdml.device_id:04X}"),
            ("Variants in GSDML", str(len(self._gsdml.daps))),
            ("Modules in catalog", str(len(self._gsdml.modules))),
            ("GSDML file", os.path.basename(self._gsdml.path)),
        ]
        # Selected device variant — dynamic (updates when the combobox changes).
        ttk.Label(f, text="Selected variant:", width=20, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(f, textvariable=self._info_variant_var, foreground="blue").grid(
            row=0, column=1, sticky="w", padx=8, pady=4)
        for i, (k, v) in enumerate(rows, start=1):
            ttk.Label(f, text=k + ":", width=20, anchor="w").grid(row=i, column=0, sticky="w", padx=8, pady=4)
            ttk.Label(f, text=v, foreground="blue").grid(row=i, column=1, sticky="w", padx=8, pady=4)
        return f

    def _build_module_info(self):
        f = ttk.LabelFrame(self._panes_host, text="Module Info (Drive Objects in catalog)")
        tv = ttk.Treeview(f, show="tree headings", columns=("ident", "tel"))
        tv.heading("#0", text="Module (DO)"); tv.column("#0", width=340)
        tv.heading("ident", text="Module Ident"); tv.column("ident", width=140)
        tv.heading("tel", text="Telegrams"); tv.column("tel", width=90, anchor="center")
        tv.pack(fill="both", expand=True, padx=4, pady=4)
        for m in self._gsdml.modules:
            tv.insert("", "end", text=m.name, values=(f"0x{m.ident:08X}", len(m.submodules)))
        return f

    def _build_gsdml_viewer(self):
        f = ttk.LabelFrame(self._panes_host, text="GSDML Viewer")
        self._xml_tv = ttk.Treeview(f, show="tree")
        self._xml_tv.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb = ttk.Scrollbar(f, command=self._xml_tv.yview)
        sb.pack(side="right", fill="y")
        self._xml_tv.configure(yscrollcommand=sb.set)
        self._xml_loaded = False
        return f

    def _populate_gsdml_viewer(self):
        if self._xml_loaded:
            return
        self._xml_loaded = True
        try:
            root = ET.parse(self._gsdml.path).getroot()
        except Exception as e:
            self._xml_tv.insert("", "end", text=f"(cannot parse: {e})")
            return

        def local(tag):
            return tag.split("}", 1)[1] if "}" in tag else tag

        def add(parent, el, depth):
            if depth > 6:
                return
            label = local(el.tag)
            key_attr = el.get("ID") or el.get("TextId") or el.get("Name") or ""
            if key_attr:
                label += f'  "{key_attr}"'
            node = self._xml_tv.insert(parent, "end", text=label, open=(depth < 2))
            for i, ch in enumerate(list(el)):
                if i > 200:
                    self._xml_tv.insert(node, "end", text="… (truncated)")
                    break
                add(node, ch, depth + 1)

        add("", root, 0)

    # ── rack operations ──────────────────────────────────────────────────────
    @staticmethod
    def _is_telegram(sub) -> bool:
        """True for a real telegram sub-slot (excludes the Module Access Point
        and empty sub-module fillers that live at sub-slots 1/2)."""
        n = (sub.name or "").lower()
        return "access point" not in n and "empty sub" not in n

    def _telegrams(self, module):
        """The selectable telegrams of a module (sub-slot 3 candidates)."""
        return [s for s in module.submodules if self._is_telegram(s)]

    def _next_slot(self) -> int:
        # Drive Objects occupy slots 1..N (slot 0 is the Access Point / head).
        used = [d.slot for d in self._rack]
        s = 1
        while s in used:
            s += 1
        return s

    def _refresh_rack(self):
        self._rack_tv.delete(*self._rack_tv.get_children())
        # Slot 0 = the Device Access Point (the control unit head).
        head = self._gsdml.dap_display_name(getattr(self._cfg, "dap_id", ""))
        self._rack_tv.insert("", "end",
                             text=f"Slot 0:  {head} [Access Point]", values=("", ""))
        # Each Drive Object is a module at slot 1..N with three sub-slots:
        # Module Access Point (1), empty sub-module (2), telegram (3).
        for i, d in enumerate(self._rack):
            pid = self._rack_tv.insert("", "end", iid=f"do{i}",
                                       text=f"Slot {d.slot}:  {d.module_name or '(module)'}",
                                       values=("", ""), open=True)
            self._rack_tv.insert(pid, "end",
                                 text="    Sub-slot 1:  Module Access Point",
                                 values=(1, "0/0"))
            self._rack_tv.insert(pid, "end",
                                 text="    Sub-slot 2:  empty sub-module",
                                 values=(2, "0/0"))
            self._rack_tv.insert(pid, "end", iid=f"tel{i}",
                                 text=f"    Sub-slot 3:  {d.submodule_name or '(no telegram)'}",
                                 values=(3, f"{d.input_length}/{d.output_length}"))
        tin = sum(d.input_length for d in self._rack)
        tout = sum(d.output_length for d in self._rack)
        self._len_var.set(f"Use of slots: {len(self._rack) + 1}   |   "
                          f"State of data length: Input {tin} / Output {tout} Octets")

    def _selected_do_index(self):
        sel = self._rack_tv.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid.startswith("do"):
            return int(iid[2:])
        if iid.startswith("tel"):
            return int(iid[3:])
        return None

    def _on_rack_select(self, _e=None):
        idx = self._selected_do_index()
        self._det_tv.delete(*self._det_tv.get_children())
        if idx is None or idx >= len(self._rack):
            return
        d = self._rack[idx]
        sub = self._gsdml.get_submodule(d.module_ident, d.submodule_ident)
        if sub:
            for it in sub.data_items:
                self._det_tv.insert("", "end", values=(it.direction, it.data_type, it.text))

    def _add_module(self):
        if not self._gsdml.modules:
            return
        self._chooser("Add Drive Object (Module)",
                      [m.name for m in self._gsdml.modules],
                      self._do_add_module)

    def _do_add_module(self, idx):
        m = self._gsdml.modules[idx]
        do = DriveObject(slot=self._next_slot(), subslot=3,
                         module_ident=m.ident, module_name=m.name)
        # Default to the module's first standard telegram (skip the Access
        # Point / empty fillers, and prefer a non-PROFIsafe telegram).
        tels = self._telegrams(m)
        pref = [s for s in tels if "profisafe" not in (s.name or "").lower()]
        choice = pref or tels
        if choice:
            s = choice[0]
            do.submodule_ident, do.submodule_name = s.ident, s.name
            do.input_length, do.output_length = s.input_length, s.output_length
        self._rack.append(do)
        self._refresh_rack()

    def _add_submodule(self):
        idx = self._selected_do_index()
        if idx is None or idx >= len(self._rack):
            messagebox.showinfo("Modules", "Select a Drive Object first.", parent=self)
            return
        d = self._rack[idx]
        m = self._gsdml.get_module(d.module_ident)
        if not m:
            return
        tels = self._telegrams(m)
        if not tels:
            return
        self._chooser("Select Telegram (sub-slot 3)",
                      [f"{s.name}  ({s.input_length}/{s.output_length} B)"
                       for s in tels],
                      lambda si: self._do_set_telegram(idx, tels, si))

    def _do_set_telegram(self, do_idx, tels, sub_idx):
        s = tels[sub_idx]
        d = self._rack[do_idx]
        d.subslot = 3
        d.submodule_ident, d.submodule_name = s.ident, s.name
        d.input_length, d.output_length = s.input_length, s.output_length
        self._refresh_rack()
        self._rack_tv.selection_set(f"do{do_idx}")
        self._on_rack_select()

    def _remove_selected(self):
        idx = self._selected_do_index()
        if idx is not None and idx < len(self._rack):
            self._rack.pop(idx)
            self._refresh_rack()

    def _chooser(self, title, items, on_ok):
        win = tk.Toplevel(self); win.title(title); win.grab_set()
        win.geometry("440x340")
        lb = tk.Listbox(win, activestyle="dotbox")
        for it in items:
            lb.insert(tk.END, it)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        if items:
            lb.selection_set(0)

        def ok():
            sel = lb.curselection()
            if sel:
                on_ok(sel[0])
            win.destroy()
        lb.bind("<Double-Button-1>", lambda e: ok())
        ttk.Button(win, text="OK", command=ok).pack(pady=6)

    # ── identity + scan ──────────────────────────────────────────────────────
    def _load_identity(self):
        c = self._cfg
        # Device variant: pick the saved one, else the GSDML's default (first).
        if self._dap_ids:
            did = c.dap_id if c.dap_id in self._dap_ids else self._dap_ids[0]
            c.dap_id = did
            self._gsdml.select_dap(did)
            self._dap_combo.current(self._dap_ids.index(did))
        self._name_var.set(c.station_name)
        self._ip_var.set(c.ip)
        self._subnet_var.set(c.subnet)
        self._gw_var.set(c.gateway)
        self._mac_entry.configure(state="normal")
        self._mac_var.set(c.mac)
        self._mac_entry.configure(state="readonly")

    def _on_scan(self):
        self._scan_status.set("Scanning…")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        try:
            devices = self._pn.dcp_discover(self._adapter, timeout_ms=2000)
        except Exception as e:
            self.after(0, lambda: self._scan_status.set(f"Scan error: {e}"))
            return
        self._scan_result = devices
        self.after(0, self._show_scan_result)

    def _show_scan_result(self):
        from profinet_ctrl import ProfinetCtrl
        devices = self._scan_result
        if not devices:
            self._scan_status.set("No devices found"); return
        win = tk.Toplevel(self); win.title("Discovered Devices"); win.grab_set()
        tv = ttk.Treeview(win, columns=("name", "ip", "mac"), show="headings", height=8)
        for c, t, w in (("name", "Station Name", 200), ("ip", "IP", 120), ("mac", "MAC", 140)):
            tv.heading(c, text=t); tv.column(c, width=w)
        tv.pack(fill="both", expand=True, padx=8, pady=8)
        for d in devices:
            tv.insert("", "end", values=(
                d.name_of_station.decode(errors="replace").rstrip("\x00"),
                d.ip_str.decode(errors="replace").rstrip("\x00"),
                ProfinetCtrl.mac_bytes_to_str(d.mac)))

        def sel():
            s = tv.selection()
            if s:
                v = tv.item(s[0])["values"]
                self._name_var.set(v[0]); self._ip_var.set(v[1])
                self._mac_entry.configure(state="normal")
                self._mac_var.set(v[2]); self._mac_entry.configure(state="readonly")
            win.destroy()
        ttk.Button(win, text="Select", command=sel).pack(pady=6)
        self._scan_status.set(f"Found {len(devices)} device(s)")

    # ── apply / cancel ───────────────────────────────────────────────────────
    def _on_apply(self):
        ip = self._ip_var.get().strip()
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            messagebox.showerror("Validation",
                                 f"IP address '{ip}' is not valid (use 4 numbers).", parent=self)
            return
        c = self._cfg
        idx = self._dap_combo.current()
        if 0 <= idx < len(self._dap_ids):
            c.dap_id = self._dap_ids[idx]
        c.station_name = self._name_var.get().strip()
        c.ip = ip
        c.subnet = self._subnet_var.get().strip() or "255.255.255.0"
        c.gateway = self._gw_var.get().strip() or "192.168.1.1"
        c.mac = self._mac_var.get().strip()
        c.drive_objects = self._rack
        c.sync_legacy_from_rack()
        self._result = True
        self.destroy()

    def _on_cancel(self):
        self._result = False
        self.destroy()
