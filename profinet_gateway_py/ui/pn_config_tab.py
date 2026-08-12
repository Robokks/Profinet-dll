"""pn_config_tab.py — Profinet Configuration tab (adapter, GSDML list, canvas work pane)."""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from gsdml import GSDMLDevice, parse_gsdml, load_device_image
from config import DeviceConfig


@dataclass
class CanvasDevice:
    gsdml: GSDMLDevice
    cfg: DeviceConfig
    canvas_id: int = 0       # image item id on canvas
    label_id: int = 0        # text label item id
    box_id: int = 0          # background box rectangle
    photo: object = None     # tk PhotoImage (keep reference)


class PnConfigTab(ttk.Frame):
    def __init__(self, parent, pn_ctrl, adapter_var: tk.StringVar):
        super().__init__(parent)
        self._pn           = pn_ctrl
        self._adapter_var  = adapter_var
        self._gsdml_list:  List[GSDMLDevice]  = []
        self._canvas_devs: List[CanvasDevice] = []
        self._drag_src: Optional[GSDMLDevice] = None
        self._drag_ghost: Optional[int] = None
        self._build_ui()

    # ── Public accessors ──────────────────────────────────────────────────────
    @property
    def device_configs(self) -> List[DeviceConfig]:
        return [cd.cfg for cd in self._canvas_devs]

    def load_from_configs(self, configs: List[DeviceConfig]):
        """Re-populate canvas from saved config."""
        for cfg in configs:
            if not os.path.exists(cfg.gsdml_path):
                continue
            try:
                gsdml = parse_gsdml(cfg.gsdml_path, dap_id=getattr(cfg, "dap_id", ""))
            except Exception:
                continue
            # Add to GSDML list if not present
            if not any(g.path == cfg.gsdml_path for g in self._gsdml_list):
                self._gsdml_list.append(gsdml)
                self._gsdml_listbox.insert(tk.END, gsdml.display_name())
            self._add_device_to_canvas(gsdml, cfg.canvas_x, cfg.canvas_y, existing_cfg=cfg)

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Top: adapter row ──────────────────────────────────────────────────
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        ttk.Label(top, text="Adapter:").pack(side="left")
        # Display shows friendly names; _adapter_names[i] is the raw NPF name saved.
        self._adapter_names: List[str] = []
        self._adapter_display = tk.StringVar()
        self._adapter_combo = ttk.Combobox(top, textvariable=self._adapter_display,
                                            state="readonly", width=60)
        self._adapter_combo.pack(side="left", padx=4)
        self._adapter_combo.bind("<<ComboboxSelected>>", self._on_adapter_select)
        ttk.Button(top, text="Refresh", command=self._refresh_adapters).pack(side="left", padx=4)

        # ── Left: GSDML list ──────────────────────────────────────────────────
        left = ttk.LabelFrame(self, text="GSDML Devices")
        left.grid(row=1, column=0, sticky="ns", padx=(6, 2), pady=4)
        left.rowconfigure(0, weight=1)

        self._gsdml_listbox = tk.Listbox(left, width=28, selectmode=tk.SINGLE,
                                          exportselection=False)
        self._gsdml_listbox.grid(row=0, column=0, sticky="ns", padx=4, pady=4)
        sb = ttk.Scrollbar(left, command=self._gsdml_listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._gsdml_listbox.config(yscrollcommand=sb.set)

        ttk.Button(left, text="Load GSDML…", command=self._load_gsdml).grid(
            row=1, column=0, columnspan=2, pady=4)
        ttk.Button(left, text="Remove", command=self._remove_gsdml).grid(
            row=2, column=0, columnspan=2, pady=(0, 4))

        # Drag from listbox
        self._gsdml_listbox.bind("<ButtonPress-1>",  self._lb_press)
        self._gsdml_listbox.bind("<B1-Motion>",      self._lb_drag)
        self._gsdml_listbox.bind("<ButtonRelease-1>", self._lb_release)

        # ── Right: canvas work pane ───────────────────────────────────────────
        right = ttk.LabelFrame(self, text="Network Topology — drag devices here")
        right.grid(row=1, column=1, sticky="nsew", padx=(2, 6), pady=4)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(right, bg="#f5f5f5", cursor="crosshair")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        # Canvas scrollbars
        hbar = ttk.Scrollbar(right, orient="horizontal", command=self._canvas.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        vbar = ttk.Scrollbar(right, orient="vertical", command=self._canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set,
                                scrollregion=(0, 0, 1200, 900))

        # Drop on canvas
        self._canvas.bind("<ButtonRelease-1>", self._canvas_drop)
        # Double-click to configure
        self._canvas.bind("<Double-Button-1>", self._canvas_dblclick)
        # Right-click to delete
        self._canvas.bind("<Button-3>", self._canvas_right_click)
        self._canvas.bind("<Configure>", lambda e: self._draw_bus())

        self._bus_y = 60
        self._draw_bus()

        # Populate adapters
        self._refresh_adapters()

    # ── netDevice-style Profinet bus ─────────────────────────────────────────
    def _draw_bus(self):
        """Green Profinet bus line with the PC controller at the left."""
        c = self._canvas
        c.delete("bus")
        width = max(c.winfo_width(), 1180)
        y = self._bus_y
        # controller box
        c.create_rectangle(16, y - 22, 96, y + 22, fill="#1a4e8c", outline="#12385f",
                            tags="bus")
        c.create_text(56, y, text="PC\nController", fill="white",
                      font=("Arial", 8, "bold"), tags="bus")
        # bus line
        c.create_line(96, y, width - 20, y, fill="#2e7d32", width=3, tags="bus")
        c.tag_lower("bus")
        self._redraw_connectors()

    def _redraw_connectors(self):
        c = self._canvas
        c.delete("conn")
        for cd in self._canvas_devs:
            x, y = cd.cfg.canvas_x, cd.cfg.canvas_y
            c.create_line(x, self._bus_y, x, y - 34, fill="#2e7d32", width=2, tags="conn")
        c.tag_lower("conn")

    def _label_for(self, name: str, desc: str) -> str:
        """Friendly dropdown label: 'Intel Ethernet — \\Device\\NPF_{GUID}'."""
        short = desc if desc else "(no description)"
        return f"{short}   —   {name}"

    def _refresh_adapters(self):
        verbose = self._pn.enumerate_adapters_verbose()
        self._adapter_names = [n for (n, _d) in verbose]
        labels = [self._label_for(n, d) for (n, d) in verbose]
        self._adapter_combo["values"] = labels

        # Heal a stale/mangled saved name (e.g. the double-brace bug) so the
        # box shows — and Apply & Restart saves — the real Npcap device name.
        current = self._adapter_var.get()
        if current:
            fixed = self._pn.resolve_adapter(current)
            if fixed != current:
                self._adapter_var.set(fixed)
                current = fixed

        if not self._adapter_names:
            return
        # Select the saved adapter (or first) and reflect its friendly label.
        if current in self._adapter_names:
            idx = self._adapter_names.index(current)
        else:
            idx = 0
            self._adapter_var.set(self._adapter_names[0])
        self._adapter_combo.current(idx)
        self._adapter_display.set(labels[idx])

    def _on_adapter_select(self, _event=None):
        idx = self._adapter_combo.current()
        if 0 <= idx < len(self._adapter_names):
            self._adapter_var.set(self._adapter_names[idx])

    # ── GSDML list management ─────────────────────────────────────────────────
    def _load_gsdml(self):
        paths = filedialog.askopenfilenames(
            title="Load GSDML file(s)",
            filetypes=[("GSDML files", "*.xml *.gsd *.gsdml"), ("All files", "*.*")]
        )
        for path in paths:
            if any(g.path == path for g in self._gsdml_list):
                continue
            try:
                gsdml = parse_gsdml(path)
                self._gsdml_list.append(gsdml)
                self._gsdml_listbox.insert(tk.END, gsdml.display_name())
            except Exception as e:
                messagebox.showerror("Parse Error", f"Failed to parse {path}:\n{e}")

    def _remove_gsdml(self):
        sel = self._gsdml_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self._gsdml_list.pop(idx)
        self._gsdml_listbox.delete(idx)

    # ── Drag from listbox → canvas ────────────────────────────────────────────
    def _lb_press(self, event):
        self._drag_src = None
        idx = self._gsdml_listbox.nearest(event.y)
        if 0 <= idx < len(self._gsdml_list):
            self._drag_src = self._gsdml_list[idx]
            self._gsdml_listbox.selection_clear(0, tk.END)
            self._gsdml_listbox.selection_set(idx)

    def _lb_drag(self, event):
        pass  # could draw a ghost label

    def _lb_release(self, _event):
        pass  # drop handled on canvas

    def _canvas_drop(self, event):
        if self._drag_src is None:
            return
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        # Check if dropped on existing device (ignore if so)
        items = self._canvas.find_overlapping(x-32, y-32, x+32, y+32)
        for item in items:
            if any(cd.canvas_id == item for cd in self._canvas_devs):
                self._drag_src = None
                return
        self._add_device_to_canvas(self._drag_src, int(x), int(y))
        self._drag_src = None

    def _add_device_to_canvas(self, gsdml: GSDMLDevice, x: int, y: int,
                               existing_cfg: Optional[DeviceConfig] = None):
        # keep devices below the Profinet bus so connectors read cleanly
        y = max(y, self._bus_y + 90)
        # netDevice-style box behind the device
        box_id = self._canvas.create_rectangle(x-46, y-40, x+46, y+52,
                                                fill="white", outline="#9aa7b4")
        photo = load_device_image(gsdml, size=(64, 64))
        if photo:
            img_id = self._canvas.create_image(x, y, image=photo, anchor="center")
        else:
            img_id = self._canvas.create_rectangle(x-32, y-32, x+32, y+32,
                                                    fill="#4a90d9", outline="#2c5f8a")
        lbl_id = self._canvas.create_text(x, y + 40,
                                           text=gsdml.device_name, font=("Arial", 8))

        cfg = existing_cfg or DeviceConfig(
            gsdml_path=gsdml.path,
            dap_id=gsdml.selected_dap_id,
            canvas_x=x, canvas_y=y,
            module_ident=gsdml.modules[0].ident if gsdml.modules else 0,
            submodule_ident=(gsdml.modules[0].submodules[0].ident
                             if gsdml.modules and gsdml.modules[0].submodules else 0),
            input_length=(gsdml.modules[0].submodules[0].input_length
                          if gsdml.modules and gsdml.modules[0].submodules else 4),
            output_length=(gsdml.modules[0].submodules[0].output_length
                           if gsdml.modules and gsdml.modules[0].submodules else 4),
        )
        cfg.canvas_x = x
        cfg.canvas_y = y

        cd = CanvasDevice(gsdml=gsdml, cfg=cfg,
                          canvas_id=img_id, label_id=lbl_id, box_id=box_id, photo=photo)
        self._canvas_devs.append(cd)

        # Enable drag on canvas item
        for it in (img_id, lbl_id, box_id):
            self._canvas.tag_bind(it, "<B1-Motion>", lambda e, c=cd: self._move_device(e, c))
        self._redraw_connectors()

    def _move_device(self, event, cd: CanvasDevice):
        x = self._canvas.canvasx(event.x)
        y = max(self._canvas.canvasy(event.y), self._bus_y + 90)
        self._canvas.coords(cd.canvas_id, x, y)
        self._canvas.coords(cd.label_id, x, y + 40)
        self._canvas.coords(cd.box_id, x-46, y-40, x+46, y+52)
        cd.cfg.canvas_x = int(x)
        cd.cfg.canvas_y = int(y)
        self._redraw_connectors()

    def _canvas_dblclick(self, event):
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        items = self._canvas.find_overlapping(x-32, y-32, x+32, y+32)
        for cd in self._canvas_devs:
            if cd.canvas_id in items or cd.label_id in items:
                self._open_device_dialog(cd)
                return

    def _open_device_dialog(self, cd: CanvasDevice):
        from ui.device_dialog import DeviceDialog
        dlg = DeviceDialog(self, cd.cfg, cd.gsdml, self._pn, self._adapter_var.get())
        if dlg.accepted:
            # Update label
            self._canvas.itemconfig(cd.label_id,
                                     text=cd.cfg.station_name or cd.gsdml.device_name)

    def _canvas_right_click(self, event):
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        items = self._canvas.find_overlapping(x-32, y-32, x+32, y+32)
        for i, cd in enumerate(self._canvas_devs):
            if cd.canvas_id in items or cd.label_id in items:
                if messagebox.askyesno("Remove Device",
                                        f"Remove '{cd.gsdml.device_name}' from canvas?",
                                        parent=self):
                    self._canvas.delete(cd.canvas_id)
                    self._canvas.delete(cd.label_id)
                    self._canvas.delete(cd.box_id)
                    self._canvas_devs.pop(i)
                    self._redraw_connectors()
                return
