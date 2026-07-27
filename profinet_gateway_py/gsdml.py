"""gsdml.py — GSDML parser (profinet-py backed) with full telegram listing.

Drop-in replacement for the DLL-version gsdml.py: same GSDMLDevice /
ModuleInfo / SubmoduleInfo dataclasses and the same parse_gsdml() /
load_device_image() entry points, so the UI is untouched.

Protocol-critical data (module ident, submodule ident, input/output byte
lengths) comes from profinet-py's profinet.gsdml.load_gsdml(). profinet-py does
NOT resolve GSDML TextId references into human-readable names, so a light
ElementTree pass adds: TextId->text resolution, DeviceIdentity, and the device
bitmap (GraphicsList). The Submodule list = EVERY telegram the GSDML defines
(Standard Telegram 1..32, SIEMENS Telegram 111/350/352/353/370, Free/Flexible).
"""

import os
import io
import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Dict

try:
    from PIL import Image, ImageTk
    _pil_available = True
except ImportError:
    _pil_available = False


# ── Dataclasses (identical shape to the DLL-version parser) ──────────────────
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


# ── ElementTree helpers (namespace-agnostic) ─────────────────────────────────
def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _iter(root, name: str):
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


def _first(root, name: str):
    for el in _iter(root, name):
        return el
    return None


def _to_int(s: str, default: int = 0) -> int:
    if not s:
        return default
    try:
        s = s.strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except (ValueError, TypeError):
        return default


def _build_text_map(root) -> Dict[str, str]:
    """TextId -> human text. A GSDML ExternalTextList has a <PrimaryLanguage>
    (English) plus <Language xml:lang=..> translations that reuse the same
    TextIds. Read only PrimaryLanguage so names resolve in English — a plain
    last-wins over every <Text> would otherwise pick the last language block
    (e.g. Chinese)."""
    tmap: Dict[str, str] = {}
    prim = _first(root, "PrimaryLanguage")
    scope = prim if prim is not None else root
    for t in _iter(scope, "Text"):
        tid = t.get("TextId")
        val = t.get("Value")
        if tid and val is not None and tid not in tmap:
            tmap[tid] = val
    return tmap


def _resolve_name(item, tmap: Dict[str, str]) -> str:
    """Resolve a Module/Submodule item's display name via its <Name TextId>.

    Looks for a <Name .../> (often under <ModuleInfo>) with a TextId (resolved
    through tmap) or a literal Value/Name attribute. Falls back to the item's
    own Name/ID attribute.
    """
    for nm in _iter(item, "Name"):
        tid = nm.get("TextId")
        if tid and tid in tmap:
            return tmap[tid]
        val = nm.get("Value")
        if val:
            return val
        if nm.text and nm.text.strip():
            return nm.text.strip()
    for k in ("Name", "ID"):
        v = item.get(k)
        if v:
            return v
    return ""


def _extract_names_and_identity(path: str):
    """Return (id2name, identity, bitmap) parsed straight from the XML.

    id2name: maps a Module/Submodule item's ID attribute -> resolved name.
    identity: (vendor_name, device_name, vendor_id, device_id).
    bitmap: base64-decoded PNG bytes from GraphicsList, or None.
    """
    id2name: Dict[str, str] = {}
    vendor_name = device_name = ""
    vendor_id = device_id = 0
    bitmap: Optional[bytes] = None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return id2name, (vendor_name, device_name, vendor_id, device_id), bitmap

    tmap = _build_text_map(root)

    for tag in ("ModuleItem", "SubmoduleItem", "VirtualSubmoduleItem",
                "DeviceAccessPointItem"):
        for item in _iter(root, tag):
            iid = item.get("ID")
            if iid:
                id2name[iid] = _resolve_name(item, tmap)

    di = _first(root, "DeviceIdentity")
    if di is not None:
        vendor_id = _to_int(di.get("VendorID", "0"))
        device_id = _to_int(di.get("DeviceID", "0"))
        vendor_name = di.get("VendorName", "") or ""
        info = _first(di, "InfoText")
        if info is not None and not vendor_name:
            vendor_name = info.get("Value", "") or ""

    dap = _first(root, "DeviceAccessPointItem")
    if dap is not None:
        device_name = dap.get("DNS_CompatibleName", "") or ""
    if not device_name:
        device_name = os.path.splitext(os.path.basename(path))[0]

    # 1. Embedded base64 <GraphicData> (some GSDMLs)
    for gd in _iter(root, "GraphicData"):
        if gd.text:
            try:
                bitmap = base64.b64decode(gd.text.strip())
                break
            except Exception:
                pass

    # 2. External bitmap file referenced by <GraphicItem GraphicFile="..."/>
    #    (Siemens SINAMICS etc.) — the file (e.g. GSDML-002A-0501-S120.bmp)
    #    lives next to the .xml; GraphicFile has no extension.
    if bitmap is None:
        target = None
        for ref in _iter(root, "GraphicItemRef"):
            if ref.get("Type") == "DeviceSymbol":
                target = ref.get("GraphicItemTarget")
                break
        gfile = None
        for gi in _iter(root, "GraphicItem"):
            if target and gi.get("ID") == target and gi.get("GraphicFile"):
                gfile = gi.get("GraphicFile")
                break
        if gfile is None:  # fallback: first GraphicItem with a file
            for gi in _iter(root, "GraphicItem"):
                if gi.get("GraphicFile"):
                    gfile = gi.get("GraphicFile")
                    break
        if gfile:
            bitmap = _read_graphic_file(os.path.dirname(os.path.abspath(path)), gfile)

    return id2name, (vendor_name, device_name, vendor_id, device_id), bitmap


def _read_graphic_file(base_dir: str, gfile: str) -> Optional[bytes]:
    """Read an external GSDML bitmap file. GraphicFile usually has no
    extension, so try common image extensions, case-insensitively."""
    names = [gfile]
    if "." not in os.path.basename(gfile):
        names = [gfile + ext for ext in (".bmp", ".png", ".gif", ".jpg", ".jpeg")]
    try:
        listing = {fn.lower(): fn for fn in os.listdir(base_dir)}
    except OSError:
        listing = {}
    for cand in names:
        p = os.path.join(base_dir, cand)
        if os.path.exists(p):
            try:
                with open(p, "rb") as fh:
                    return fh.read()
            except OSError:
                pass
        real = listing.get(os.path.basename(cand).lower())
        if real:
            try:
                with open(os.path.join(base_dir, real), "rb") as fh:
                    return fh.read()
            except OSError:
                pass
    return None


# ── Main entry point ─────────────────────────────────────────────────────────
def parse_gsdml(path: str) -> GSDMLDevice:
    """Parse a GSDML file into a GSDMLDevice, listing every telegram."""
    from profinet.gsdml import load_gsdml   # profinet-py (GPL-3.0)

    id2name, (vendor_name, device_name, vendor_id, device_id), bitmap = \
        _extract_names_and_identity(path)

    dev = load_gsdml(path)
    if vendor_id == 0:
        vendor_id = getattr(dev, "vendor_id", 0)
    if device_id == 0:
        device_id = getattr(dev, "device_id", 0)

    catalog = dev.submodule_catalog  # {id: GSDMLSubmodule}

    def _sub_info(sub) -> SubmoduleInfo:
        return SubmoduleInfo(
            ident=sub.submodule_ident,
            name=id2name.get(sub.id, sub.id),
            input_length=sub.input_length,
            output_length=sub.output_length,
        )

    modules: List[ModuleInfo] = []
    for mid, m in dev.modules.items():
        telegrams: List[SubmoduleInfo] = []
        seen = set()
        # inline (Virtual)SubmoduleItems on the module
        for sub in getattr(m, "submodules", []):
            if sub.id not in seen:
                telegrams.append(_sub_info(sub)); seen.add(sub.id)
        # UseableSubmodules -> resolve against the catalog
        for sub_id in getattr(m, "useable_submodules", {}) or {}:
            sub = catalog.get(sub_id)
            if sub is not None and sub.id not in seen:
                telegrams.append(_sub_info(sub)); seen.add(sub.id)
        # a module that references nothing gets the whole catalog
        if not telegrams:
            for sub in catalog.values():
                telegrams.append(_sub_info(sub))
        modules.append(ModuleInfo(
            ident=m.module_ident,
            name=id2name.get(m.id, m.id),
            submodules=telegrams,
        ))

    # No modules at all: expose every catalog telegram under a default module.
    if not modules:
        telegrams = [_sub_info(s) for s in catalog.values()]
        if not telegrams:
            telegrams = [SubmoduleInfo(1, "Standard Telegram 1", 4, 4)]
        modules.append(ModuleInfo(ident=1, name="Default Module",
                                  submodules=telegrams))

    return GSDMLDevice(
        path=path,
        vendor_name=vendor_name,
        device_name=device_name,
        vendor_id=vendor_id,
        device_id=device_id,
        modules=modules,
        bitmap_data=bitmap,
    )


def load_device_image(dev: GSDMLDevice, size=(64, 64)):
    """Return a tkinter PhotoImage for this device (bitmap or default icon)."""
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    default_path = os.path.join(assets_dir, "device_default.png")

    if dev.bitmap_data and _pil_available:
        try:
            img = Image.open(io.BytesIO(dev.bitmap_data)).resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            pass
    if _pil_available and os.path.exists(default_path):
        try:
            img = Image.open(default_path).resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            pass
    try:
        import tkinter as tk
        return tk.PhotoImage(width=size[0], height=size[1])
    except Exception:
        return None
