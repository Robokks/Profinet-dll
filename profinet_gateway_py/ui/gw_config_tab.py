"""gw_config_tab.py — Gateway (TCP/UDP server) configuration tab."""

import tkinter as tk
from tkinter import ttk
from config import GatewayConfig


class GwConfigTab(ttk.Frame):
    def __init__(self, parent, gw_cfg: GatewayConfig):
        super().__init__(parent)
        self._cfg = gw_cfg
        self._build_ui()
        self._load_values()

    @property
    def gateway_config(self) -> GatewayConfig:
        return GatewayConfig(
            protocol=self._proto_var.get(),
            port=self._port_var.get(),
            bind=self._bind_var.get().strip() or "0.0.0.0",
            framed=bool(self._framed_var.get()),
        )

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frame = ttk.LabelFrame(self, text="TCP / UDP Server Settings")
        frame.pack(fill="x", padx=10, pady=10)

        # Protocol
        ttk.Label(frame, text="Protocol:").grid(row=0, column=0, sticky="w", **pad)
        self._proto_var = tk.StringVar(value="TCP")
        proto_frame = ttk.Frame(frame)
        proto_frame.grid(row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(proto_frame, text="TCP", variable=self._proto_var,
                         value="TCP").pack(side="left", padx=4)
        ttk.Radiobutton(proto_frame, text="UDP", variable=self._proto_var,
                         value="UDP").pack(side="left", padx=4)
        ttk.Radiobutton(proto_frame, text="STM (LabVIEW stream)",
                         variable=self._proto_var, value="STM").pack(side="left", padx=4)

        # Port
        ttk.Label(frame, text="Port:").grid(row=1, column=0, sticky="w", **pad)
        self._port_var = tk.IntVar(value=5000)
        ttk.Spinbox(frame, from_=1024, to=65535, textvariable=self._port_var,
                    width=8).grid(row=1, column=1, sticky="w", **pad)

        # Bind address
        ttk.Label(frame, text="Bind Address:").grid(row=2, column=0, sticky="w", **pad)
        self._bind_var = tk.StringVar(value="0.0.0.0")
        ttk.Entry(frame, textvariable=self._bind_var, width=18).grid(
            row=2, column=1, sticky="w", **pad)

        # Per-drive framing
        ttk.Label(frame, text="Framing:").grid(row=3, column=0, sticky="w", **pad)
        self._framed_var = tk.IntVar(value=0)
        ttk.Checkbutton(frame, text="Per-drive header  [SOF AA55][DriveID][Len][data][EOF 55AA]",
                        variable=self._framed_var).grid(row=3, column=1, sticky="w", **pad)

        # Info
        info = ttk.LabelFrame(self, text="Frame Layout")
        info.pack(fill="x", padx=10, pady=6)
        ttk.Label(info, text=(
            "Each device contributes output_length bytes (client→EXE) and\n"
            "input_length bytes (EXE→client) in config order.\n\n"
            "Example — 2 devices, Telegram 1 (4B each):\n"
            "  TX frame (client→EXE): [Dev0: STW1 2B, NSOLL 2B][Dev1: STW1 2B, NSOLL 2B] = 8B\n"
            "  RX frame (EXE→client): [Dev0: ZSW1 2B, NIST  2B][Dev1: ZSW1 2B, NIST  2B] = 8B\n"
            "Values are BIG-ENDIAN 16-bit words (Profinet wire order).\n\n"
            "Protocols:\n"
            "  TCP — request/response: client sends output frame, gets input frame.\n"
            "  UDP — datagram: send output frame, receive input frame.\n"
            "  STM — LabVIEW streaming: each message is [4-byte BE length][frame],\n"
            "        full-duplex (EXE streams inputs continuously; client streams\n"
            "        outputs). Read/Write with the NI STM library or plain TCP\n"
            "        Read (4-byte length, then that many bytes).\n\n"
            "Per-drive framing (optional): each drive is wrapped as\n"
            "  [SOF AA 55][DriveID 1B][Len 2B BE][data Len B][EOF 55 AA]\n"
            "so the client can locate and validate each drive's block."
        ), justify="left", foreground="gray").pack(padx=10, pady=8, anchor="w")

    def _load_values(self):
        self._proto_var.set(self._cfg.protocol)
        self._port_var.set(self._cfg.port)
        self._bind_var.set(self._cfg.bind)
        self._framed_var.set(1 if getattr(self._cfg, "framed", False) else 0)
