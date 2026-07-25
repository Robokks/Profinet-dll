"""gsdml.py — GSDML XML parser for Profinet device description files"""

import xml.etree.ElementTree as ET
import base64
import os
import io
from dataclasses import dataclass, field
from typing import List, Optional, Dict

# Tkinter PhotoImage helper (imported lazily to allow headless use)
_tk_available = True
try:
    import tkinter as tk
    from PIL import Image, ImageTk
    _pil_available = True
except ImportError:
    _pil_available = False

# GSDML uses a default namespace
_NS_PREFIX = "{http://www.profibus.com/GSDML/DeviceProfile/2.4/GSDML-DeviceProfile}"
_NS_PREFIXES = [
    "{http://www.profibus.com/GSDML/DeviceProfile/2.4/GSDML-DeviceProfile}",
    "{http://www.profibus.com/GSDML/DeviceProfile/2.3/GSDML-DeviceProfile}",
    "{http://www.profibus.com/GSDML/DeviceProfile/2.2/GSDML-DeviceProfile}",
    "{http://www.profibus.com/GSDML/DeviceProfile/2.1/GSDML-DeviceProfile}",
    "",   # no namespace fallback
]

def _find(elem, tag):
    """Find a sub-element trying all known GSDML namespaces."""
    for ns in _NS_PREFIXES:
        found = elem.find(ns + tag)
        if found is not None:
            return found
    return None

def _findall(elem, tag):
    for ns in _NS_PREFIXES:
        found = elem.findall(ns + tag)
        if found:
            return found
    return []

def _attr(elem, *keys, default=""):
    for k in keys:
        v = elem.get(k)
        if v is not None:
            return v
    return default

@dataclass
class SubmoduleInfo:
    ident: int
    name: str
    input_length: int   # bytes device→controller
    output_length: int  # bytes controller→device

@dataclass
class ModuleInfo:
    ident: int
    name: str
    submodules: List[SubmoduleInfo] = field(default_factory=list)

@dataclass
class GSDMLDevice:
    path: str
    vendor_name: str
    device_name: str
    vendor_id: int
    device_id: int
    modules: List[ModuleInfo] = field(default_factory=list)
    # bitmap: base64-encoded PNG bytes or None
    bitmap_data: Optional[bytes] = None

    def display_name(self) -> str:
        return f"{self.device_name} ({os.path.basename(self.path)})"

    def get_module(self, ident: int) -> Optional[ModuleInfo]:
        for m in self.modules:
            if m.ident == ident:
                return m
        return None

    def get_submodule(self, mod_ident: int, sub_ident: int) -> Optional[SubmoduleInfo]:
        m = self.get_module(mod_ident)
        if m:
            for s in m.submodules:
                if s.ident == sub_ident:
                    return s
        return None

def parse_gsdml(path: str) -> GSDMLDevice:
    """Parse a GSDML .xml file and return a GSDMLDevice."""
    tree = ET.parse(path)
    root = tree.getroot()

    # Strip namespace from tag for detection
    def _ns_tag(elem):
        tag = elem.tag
        if "}" in tag:
            return tag.split("}")[1]
        return tag

    # Detect namespace from root
    global _NS_PREFIX
    if "}" in root.tag:
        _NS_PREFIX = root.tag.split("}")[0] + "}"
    else:
        _NS_PREFIX = ""

    # DeviceIdentity
    dev_id_elem = _find(root, "Device/DeviceIdentity")
    if dev_id_elem is None:
        # try direct
        dev_id_elem = _find(root, "DeviceIdentity")

    vendor_name = ""
    device_name = ""
    vendor_id = 0
    device_id = 0

    if dev_id_elem is not None:
        vendor_id_str = _attr(dev_id_elem, "VendorID", "Vendor_ID", default="0x0000")
        device_id_str = _attr(dev_id_elem, "DeviceID", "Device_ID", default="0x0000")
        try:
            vendor_id = int(vendor_id_str, 16) if vendor_id_str.startswith("0x") else int(vendor_id_str)
        except Exception:
            vendor_id = 0
        try:
            device_id = int(device_id_str, 16) if device_id_str.startswith("0x") else int(device_id_str)
        except Exception:
            device_id = 0
        # InfoText / VendorName
        info = _find(dev_id_elem, "InfoText")
        if info is None:
            info = _find(root, "Device/DeviceIdentity/InfoText")
        vendor_name = _attr(dev_id_elem, "VendorName", default="")

    # DeviceFunction → NameOfStation hint
    dev_func = _find(root, "Device/DeviceFunction")
    if dev_func is None:
        dev_func = _find(root, "DeviceFunction")

    # Try to get device name from multiple places
    dev_access = _find(root, "Device/DeviceAccessPointList/DeviceAccessPointItem")
    if dev_access is not None:
        device_name = _attr(dev_access, "DNS_CompatibleName", "SubnetMask", default="")
    if not device_name:
        # Use filename stem
        device_name = os.path.splitext(os.path.basename(path))[0]

    # ModuleList
    modules: List[ModuleInfo] = []
    module_list = _find(root, "ProfileBody/ApplicationProcess/DeviceAccessPointList")
    if module_list is None:
        module_list = _find(root, "ApplicationProcess/DeviceAccessPointList")

    # Parse ModuleList
    ml = _find(root, "ProfileBody/ApplicationProcess/ModuleList")
    if ml is None:
        ml = _find(root, "ApplicationProcess/ModuleList")

    submodule_map: Dict[int, SubmoduleInfo] = {}  # ident → SubmoduleInfo

    # Parse SubmoduleList first
    sml = _find(root, "ProfileBody/ApplicationProcess/SubmoduleList")
    if sml is None:
        sml = _find(root, "ApplicationProcess/SubmoduleList")
    if sml is not None:
        for smi in _findall(sml, "SubmoduleItem"):
            sub_ident_str = _attr(smi, "ID", "SubmoduleIdentNumber", default="0")
            try:
                sub_ident = int(sub_ident_str, 16) if sub_ident_str.startswith("0x") else int(sub_ident_str)
            except Exception:
                sub_ident = 0
            sub_name = _attr(smi, "SubmoduleName", "Name", default=f"Submodule_{sub_ident:#x}")
            # IOData
            in_len = 0
            out_len = 0
            io_data = _find(smi, "IOData")
            if io_data is not None:
                inp = _find(io_data, "Input")
                if inp is not None:
                    in_len = int(_attr(inp, "Length", default="0"))
                out = _find(io_data, "Output")
                if out is not None:
                    out_len = int(_attr(out, "Length", default="0"))
            submodule_map[sub_ident] = SubmoduleInfo(
                ident=sub_ident,
                name=sub_name,
                input_length=in_len,
                output_length=out_len,
            )

    if ml is not None:
        for mi in _findall(ml, "ModuleItem"):
            mod_ident_str = _attr(mi, "ID", "ModuleIdentNumber", default="0")
            try:
                mod_ident = int(mod_ident_str, 16) if mod_ident_str.startswith("0x") else int(mod_ident_str)
            except Exception:
                mod_ident = 0
            mod_name = _attr(mi, "ModuleName", "Name", default=f"Module_{mod_ident:#x}")
            # Find useable submodules
            subs: List[SubmoduleInfo] = []
            useable = _find(mi, "UseableSubmodules")
            if useable is not None:
                for sr in _findall(useable, "SubmoduleItemRef"):
                    ref_str = _attr(sr, "SubmoduleItemTarget", "ID", default="0")
                    try:
                        ref = int(ref_str, 16) if ref_str.startswith("0x") else int(ref_str)
                    except Exception:
                        ref = 0
                    if ref in submodule_map:
                        subs.append(submodule_map[ref])
            if not subs and submodule_map:
                # fallback: attach all submodules
                subs = list(submodule_map.values())
            modules.append(ModuleInfo(ident=mod_ident, name=mod_name, submodules=subs))

    # If no modules parsed, create a generic one
    if not modules:
        subs = list(submodule_map.values()) if submodule_map else [
            SubmoduleInfo(ident=1, name="Telegram 1 (4B in/out)", input_length=4, output_length=4)
        ]
        modules.append(ModuleInfo(ident=1, name="Default Module", submodules=subs))

    # Bitmap — look in GraphicsList
    bitmap_data: Optional[bytes] = None
    gl = _find(root, "ProfileBody/ApplicationProcess/GraphicsList")
    if gl is None:
        gl = _find(root, "ApplicationProcess/GraphicsList")
    if gl is not None:
        for gi in list(gl):
            data_elem = _find(gi, "GraphicData")
            if data_elem is not None and data_elem.text:
                try:
                    bitmap_data = base64.b64decode(data_elem.text.strip())
                    break
                except Exception:
                    pass

    return GSDMLDevice(
        path=path,
        vendor_name=vendor_name,
        device_name=device_name,
        vendor_id=vendor_id,
        device_id=device_id,
        modules=modules,
        bitmap_data=bitmap_data,
    )


def load_device_image(dev: GSDMLDevice, size=(64, 64)):
    """Return a tkinter PhotoImage for this device (bitmap or default icon)."""
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    default_path = os.path.join(assets_dir, "device_default.png")

    if dev.bitmap_data and _pil_available:
        try:
            img = Image.open(io.BytesIO(dev.bitmap_data))
            img = img.resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            pass

    if _pil_available and os.path.exists(default_path):
        try:
            img = Image.open(default_path).resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            pass

    # Minimal fallback: 64x64 grey PNG via tkinter
    try:
        import tkinter as tk
        return tk.PhotoImage(width=size[0], height=size[1])
    except Exception:
        return None
