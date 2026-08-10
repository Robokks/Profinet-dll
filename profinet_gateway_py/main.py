"""main.py — Profinet Gateway Application entry point."""

import sys
import os
import threading

# Make sure our package root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config, save_config, AppConfig
from profinet_ctrl import ProfinetCtrl
from gateway_server import GatewayServer
from bridge import Bridge
from ui.main_window import MainWindow


def start_services(cfg: AppConfig, pn: ProfinetCtrl,
                   gw: GatewayServer, br: Bridge, log):
    """Initialize and start all background services. The blocking Profinet
    connect (DCP + RPC AR, multi-second timeouts per device) runs on a worker
    thread so the GUI / Apply & Restart never freezes."""
    # Gateway + bridge start immediately (non-blocking).
    gw.configure(cfg.gateway.protocol, cfg.gateway.port,
                 cfg.gateway.bind, cfg.devices,
                 framed=getattr(cfg.gateway, "framed", False),
                 watchdog_ms=getattr(cfg.gateway, "watchdog_ms", 0))
    gw.start()
    br.start()

    if cfg.devices:
        pn.start(cfg.adapter, cfg.devices)   # fast: just creates device state

        def _connect_all():
            for i in range(len(cfg.devices)):
                pn.configure_device(i)        # slow: DCP + RPC AR per device
        threading.Thread(target=_connect_all, daemon=True,
                         name="pn-connect").start()


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

    def apply_realtime():
        """Low-latency tuning from config: 1 ms timer + priority + CPU affinity."""
        try:
            from realtime import enable_realtime
            raw = (getattr(cfg.gateway, "cpu_affinity", "") or "").replace(" ", "")
            aff = None
            if raw:
                try:
                    aff = [int(x) for x in raw.split(",") if x != ""]
                except ValueError:
                    log(f"[RT] bad CPU cores '{raw}' — ignoring")
            enable_realtime(priority=getattr(cfg.gateway, "priority", "high"),
                            affinity=aff, timer_1ms=True, log=log)
        except Exception as e:
            log(f"[RT] tuning unavailable: {e}")

    apply_realtime()

    pn = ProfinetCtrl(log_cb=log)
    gw = GatewayServer(log_cb=log)
    br = Bridge(pn, gw, log_cb=log)

    # One-time self-heal: if config.json holds a stale/mangled adapter name
    # (e.g. the old double-brace \Device\NPF_{{GUID}} bug), rewrite it to the
    # real Npcap device name so the file is permanently clean.
    if cfg.adapter:
        healed = pn.resolve_adapter(cfg.adapter)
        if healed != cfg.adapter:
            cfg.adapter = healed
            try:
                save_config(cfg)
                log("[APP] Cleaned adapter name in config.json")
            except Exception as e:
                log(f"[APP] Could not save cleaned config: {e}")

    def on_restart():
        """Called after Apply & Restart in ConfigWindow."""
        log("[APP] Restarting services…")
        br.stop()
        gw.stop()
        pn.stop()
        apply_realtime()   # re-apply priority / CPU affinity from the new config
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
