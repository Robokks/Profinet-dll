"""config.py — load/save application configuration from config.json"""

import json
import os
from dataclasses import dataclass, field, asdict, fields
from typing import List

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

@dataclass
class DriveObject:
    """One slot in the device rack: a Drive Object (module) + its telegram
    (submodule). A device may hold several (SINAMICS multi-DO)."""
    slot: int = 1
    subslot: int = 1
    module_ident: int = 0
    module_name: str = ""
    submodule_ident: int = 0
    submodule_name: str = ""
    input_length: int = 4     # bytes device→controller
    output_length: int = 4    # bytes controller→device


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
    # Rack of Drive Objects. Legacy single-telegram fields below mirror the
    # first DO for backward compatibility with older config.json / callers.
    drive_objects: List["DriveObject"] = field(default_factory=list)
    slot: int = 1
    subslot: int = 1
    module_ident: int = 0
    submodule_ident: int = 0
    module_name: str = ""
    submodule_name: str = ""
    input_length: int = 4     # bytes from device to controller
    output_length: int = 4    # bytes from controller to device

    # ── rack helpers ─────────────────────────────────────────────────────────
    def effective_drive_objects(self) -> List["DriveObject"]:
        """The rack — falls back to a single DO built from the legacy fields."""
        if self.drive_objects:
            return self.drive_objects
        return [DriveObject(slot=self.slot, subslot=self.subslot,
                            module_ident=self.module_ident,
                            module_name=self.module_name,
                            submodule_ident=self.submodule_ident,
                            submodule_name=self.submodule_name,
                            input_length=self.input_length,
                            output_length=self.output_length)]

    def total_input_length(self) -> int:
        return sum(d.input_length for d in self.effective_drive_objects())

    def total_output_length(self) -> int:
        return sum(d.output_length for d in self.effective_drive_objects())

    def sync_legacy_from_rack(self):
        """Mirror the first DO into the legacy scalar fields (so old code and
        the single-telegram bridge path keep working)."""
        dos = self.effective_drive_objects()
        if dos:
            d = dos[0]
            self.slot, self.subslot = d.slot, d.subslot
            self.module_ident, self.module_name = d.module_ident, d.module_name
            self.submodule_ident, self.submodule_name = d.submodule_ident, d.submodule_name
            self.input_length, self.output_length = d.input_length, d.output_length

@dataclass
class GatewayConfig:
    protocol: str = "TCP"     # "TCP", "UDP" or "STM"
    port: int = 5000
    bind: str = "0.0.0.0"
    framed: bool = False      # per-drive [SOF][id][len][data][EOF] framing

@dataclass
class AppConfig:
    adapter: str = ""
    devices: List[DeviceConfig] = field(default_factory=list)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)

def _known(cls, d: dict) -> dict:
    """Keep only keys that are real fields of the dataclass, so a config.json
    written by a newer build (extra keys) doesn't raise TypeError."""
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in valid}


def load_config() -> AppConfig:
    if not os.path.exists(CONFIG_FILE):
        return AppConfig()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[CFG] config.json unreadable ({e}); using defaults")
        return AppConfig()

    cfg = AppConfig()
    cfg.adapter = data.get("adapter", "")
    try:
        cfg.gateway = GatewayConfig(**_known(GatewayConfig, data.get("gateway", {})))
    except Exception:
        cfg.gateway = GatewayConfig()

    devices = []
    for d in data.get("devices", []):
        try:
            d = dict(d)
            dos = [DriveObject(**_known(DriveObject, x))
                   for x in d.pop("drive_objects", [])]
            dc = DeviceConfig(**_known(DeviceConfig, d))
            dc.drive_objects = dos
            devices.append(dc)
        except Exception as e:
            print(f"[CFG] skipping malformed device entry: {e}")
    cfg.devices = devices
    return cfg


def save_config(cfg: AppConfig):
    data = {
        "adapter": cfg.adapter,
        "gateway": asdict(cfg.gateway),
        "devices": [asdict(d) for d in cfg.devices],
    }
    # atomic write: temp file + replace, so a crash mid-write can't corrupt it
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
