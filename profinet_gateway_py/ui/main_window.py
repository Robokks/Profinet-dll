"""main_window.py — Main application window."""

import tkinter as tk
from tkinter import ttk
import threading
import time
from typing import Callable


class MainWindow(tk.Tk):
    def __init__(self, pn_ctrl, gw_server, bridge, cfg, on_restart: Callable):
        super().__init__()
        self.title("Profinet Gateway")
        self.geometry("680x400")
        self.resizable(True, True)

        self._pn         = pn_ctrl
        self._gw         = gw_server
        self._bridge     = bridge
        self._cfg        = cfg
        self._on_restart = on_restart

        self._build_ui()
        self._refresh()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ── Title bar ─────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg="#1a4e8c", height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="PROFINET GATEWAY", bg="#1a4e8c", fg="white",
                 font=("Arial", 14, "bold")).pack(side="left", padx=14, pady=10)
        ver = self._pn.get_version()
        tk.Label(hdr, text=f"v{ver}", bg="#1a4e8c", fg="#aac8f0",
                 font=("Arial", 9)).pack(side="right", padx=14)

        # ── Status panel ──────────────────────────────────────────────────────
        status_frame = ttk.LabelFrame(self, text="Runtime Status")
        status_frame.pack(fill="x", padx=10, pady=8)

        # Gateway status
        gw_row = ttk.Frame(status_frame)
        gw_row.pack(fill="x", padx=8, pady=2)
        ttk.Label(gw_row, text="Gateway:", width=14, anchor="w").pack(side="left")
        self._gw_status_var = tk.StringVar(value="● Starting…")
        self._gw_lbl = ttk.Label(gw_row, textvariable=self._gw_status_var,
                                  font=("Arial", 10, "bold"))
        self._gw_lbl.pack(side="left")

        # Profinet devices
        self._dev_frame = ttk.Frame(status_frame)
        self._dev_frame.pack(fill="x", padx=8, pady=2)
        self._dev_status_vars = []
        self._dev_labels = []

        # ── Action buttons ────────────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=16)

        config_btn = tk.Button(btn_frame, text="Configuration",
                               font=("Arial", 13), width=18, height=2,
                               bg="#1a4e8c", fg="white", relief="flat",
                               activebackground="#2462b0",
                               command=self._open_config)
        config_btn.grid(row=0, column=0, padx=16)

        diag_btn = tk.Button(btn_frame, text="IO Diagnostic",
                             font=("Arial", 13), width=18, height=2,
                             bg="#2e7d32", fg="white", relief="flat",
                             activebackground="#3d9940",
                             command=self._open_diagnostic)
        diag_btn.grid(row=0, column=1, padx=16)

        discovery_btn = tk.Button(btn_frame, text="Network Discovery",
                                  font=("Arial", 13), width=18, height=2,
                                  bg="#00838f", fg="white", relief="flat",
                                  activebackground="#00a0b0",
                                  command=self._open_discovery)
        discovery_btn.grid(row=0, column=2, padx=16)

        monitor_btn = tk.Button(btn_frame, text="Gateway Monitor",
                                font=("Arial", 13), width=18, height=2,
                                bg="#455a64", fg="white", relief="flat",
                                activebackground="#607d8b",
                                command=self._open_monitor)
        monitor_btn.grid(row=1, column=0, columnspan=3, pady=(12, 0))

        # ── Log ──────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self._log_text = tk.Text(log_frame, height=6, state="disabled",
                                  font=("Courier", 8), wrap="word")
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)
        sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)

    def log(self, msg: str):
        """Append message to log widget (thread-safe)."""
        self.after(0, self._append_log, msg)

    def _append_log(self, msg: str):
        self._log_text.configure(state="normal")
        self._log_text.insert(tk.END, msg + "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    def _refresh(self):
        if not self.winfo_exists():
            return

        # Gateway status
        if self._gw.is_running:
            proto = self._cfg.gateway.protocol
            port  = self._cfg.gateway.port
            client = " — client connected" if self._gw.client_connected else ""
            self._gw_status_var.set(f"● {proto}:{port} listening{client}")
            self._gw_lbl.configure(foreground="green")
        else:
            self._gw_status_var.set("● Gateway stopped")
            self._gw_lbl.configure(foreground="red")

        # Device statuses
        n = self._pn.device_count()
        # Rebuild device rows if count changed
        if len(self._dev_status_vars) != n:
            for w in self._dev_frame.winfo_children():
                w.destroy()
            self._dev_status_vars.clear()
            self._dev_labels.clear()
            for i in range(n):
                ds = self._pn.device_state(i)
                name = ds.config.station_name if ds else f"Device {i}"
                row = ttk.Frame(self._dev_frame)
                row.pack(fill="x", pady=1)
                ttk.Label(row, text=f"  {name}:", width=20, anchor="w").pack(side="left")
                var = tk.StringVar()
                lbl = ttk.Label(row, textvariable=var, font=("Arial", 10, "bold"))
                lbl.pack(side="left")
                self._dev_status_vars.append(var)
                self._dev_labels.append(lbl)

        for i in range(n):
            if i >= len(self._dev_status_vars):
                break
            connected = self._pn.is_connected(i)
            if connected:
                self._dev_status_vars[i].set("● CONNECTED")
                self._dev_labels[i].configure(foreground="green")
            else:
                ds = self._pn.device_state(i)
                err = f" ({ds.error})" if ds and ds.error else ""
                self._dev_status_vars[i].set(f"● DISCONNECTED{err}")
                self._dev_labels[i].configure(foreground="red")

        self.after(500, self._refresh)

    def _open_config(self):
        from ui.config_window import ConfigWindow
        ConfigWindow(self, self._cfg, self._pn, self._on_restart)

    def _open_diagnostic(self):
        from ui.io_diagnostic import IODiagnosticWindow
        IODiagnosticWindow(self, self._pn, self._gw)

    def _open_discovery(self):
        from ui.discovery_window import DiscoveryWindow
        DiscoveryWindow(self, self._pn, self._cfg)

    def _open_monitor(self):
        from ui.gateway_monitor import GatewayMonitorWindow
        GatewayMonitorWindow(self, self._gw)

    def _on_close(self):
        self._bridge.stop()
        self._gw.stop()
        self._pn.stop()
        self.destroy()
