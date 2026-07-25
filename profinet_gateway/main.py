"""main.py — Profinet Gateway Application entry point."""

import sys
import os

# Make sure our package root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config, save_config, AppConfig
from profinet_ctrl import ProfinetCtrl
from gateway_server import GatewayServer
from bridge import Bridge
from ui.main_window import MainWindow


def start_services(cfg: AppConfig, pn: ProfinetCtrl,
                   gw: GatewayServer, br: Bridge, log):
    """Initialize and start all background services."""
    # Start Profinet controller
    if cfg.devices:
        ok = pn.start(cfg.adapter, cfg.devices)
        if not ok:
            log("[APP] Warning: some Profinet devices failed to initialize")
        # Connect each device
        for i in range(len(cfg.devices)):
            pn.configure_device(i)

    # Configure and start gateway
    gw.configure(cfg.gateway.protocol, cfg.gateway.port,
                 cfg.gateway.bind, cfg.devices)
    gw.start()

    # Start bridge
    br.start()


def main():
    cfg = load_config()

    # Use a list cell so log() can reference the app before it's created
    log_lines = []
    _app = [None]

    def log(msg: str):
        print(msg)
        log_lines.append(msg)
        if _app[0] is not None:
            _app[0].log(msg)

    pn = ProfinetCtrl(log_cb=log)
    gw = GatewayServer(log_cb=log)
    br = Bridge(pn, gw, log_cb=log)

    def on_restart():
        """Called after Apply & Restart in ConfigWindow."""
        log("[APP] Restarting services…")
        br.stop()
        gw.stop()
        pn.stop()
        start_services(cfg, pn, gw, br, log)
        log("[APP] Services restarted")

    # Start services with initial config
    start_services(cfg, pn, gw, br, log)

    # Launch GUI — assign to list cell so log() can forward messages to it
    _app[0] = MainWindow(pn, gw, br, cfg, on_restart)

    # Replay buffered log messages into the GUI
    for msg in log_lines:
        _app[0].log(msg)

    _app[0].mainloop()


if __name__ == "__main__":
    main()
