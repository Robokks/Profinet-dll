"""config_window.py — Configuration window with Profinet + Gateway tabs."""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

from config import AppConfig, save_config
from ui.pn_config_tab import PnConfigTab
from ui.gw_config_tab import GwConfigTab


class ConfigWindow(tk.Toplevel):
    def __init__(self, parent, cfg: AppConfig, pn_ctrl, on_restart: Callable):
        super().__init__(parent)
        self.title("Configuration")
        self.geometry("900x620")
        self.resizable(True, True)
        self.grab_set()

        self._cfg        = cfg
        self._pn         = pn_ctrl
        self._on_restart = on_restart

        self._adapter_var = tk.StringVar(value=cfg.adapter)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)

        # Tab 1: Profinet
        self._pn_tab = PnConfigTab(notebook, pn_ctrl, self._adapter_var)
        notebook.add(self._pn_tab, text="  Profinet Configuration  ")

        # Tab 2: Gateway
        self._gw_tab = GwConfigTab(notebook, cfg.gateway)
        notebook.add(self._gw_tab, text="  Gateway Configuration  ")

        # Load existing devices into canvas
        self._pn_tab.load_from_configs(cfg.devices)

        # Bottom buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=8)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Apply & Restart",
                   command=self._apply_restart).pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _apply_restart(self):
        dev_cfgs = self._pn_tab.device_configs
        if not dev_cfgs:
            if not messagebox.askyesno("No Devices",
                                        "No devices on canvas. Save anyway?",
                                        parent=self):
                return

        new_cfg = AppConfig(
            adapter=self._adapter_var.get(),
            devices=dev_cfgs,
            gateway=self._gw_tab.gateway_config,
        )
        save_config(new_cfg)

        self._cfg.adapter = new_cfg.adapter
        self._cfg.devices = new_cfg.devices
        self._cfg.gateway = new_cfg.gateway

        self.destroy()
        self._on_restart()
