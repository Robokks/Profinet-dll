"""config.py — load/save application configuration from config.json"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

@dataclass
class DeviceConfig:
    gsdml_path: str = ""
    station_name: str = ""
    ip: str = "192.168.1.50"
    subnet: str = "255.255.255.0"
    gateway: str = "192.168.1.1"
    mac: str = ""              # filled after DCP scan
    canvas_x: int = 100
    canvas_y: int = 100
    slot: int = 1
    subslot: int = 1
    module_ident: int = 0
    submodule_ident: int = 0
    module_name: str = ""
    submodule_name: str = ""
    input_length: int = 4     # bytes from device to controller
    output_length: int = 4    # bytes from controller to device

@dataclass
class GatewayConfig:
    protocol: str = "TCP"     # "TCP" or "UDP"
    port: int = 5000
    bind: str = "0.0.0.0"

@dataclass
class AppConfig:
    adapter: str = ""
    devices: List[DeviceConfig] = field(default_factory=list)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)

def load_config() -> AppConfig:
    if not os.path.exists(CONFIG_FILE):
        return AppConfig()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = AppConfig()
        cfg.adapter = data.get("adapter", "")
        cfg.gateway = GatewayConfig(**data.get("gateway", {}))
        cfg.devices = [DeviceConfig(**d) for d in data.get("devices", [])]
        return cfg
    except Exception:
        return AppConfig()

def save_config(cfg: AppConfig):
    data = {
        "adapter": cfg.adapter,
        "gateway": asdict(cfg.gateway),
        "devices": [asdict(d) for d in cfg.devices],
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
