"""Parser for Centralite JetStream Designer .jts config files (XML).

A .jts is an XML document (root <GulfStreamCL>) exported by JetStream Designer.
Unlike the Elegance .elg — which lists every load slot, most of them empty
defaults — a .jts contains only devices the installer actually added, so there
is no phantom-slot problem: every device is real.

We extract what the integration needs:
- loads:    {device_id: name}      from <DeviceList>/<Device>
- dimmable: {device_id: is_dimmer} from each device's <Dimmer> flag
- scenes:   {scene_id: name}       from <SceneList>/<Scene>

Only devices exposed to third-party control (<SendThirdParty>true) and active
(<Active> not "false") are returned — others can't be observed or controlled
over RS-232, so creating entities for them would just yield perpetually-unknown
state. Physical keypad buttons (<buttonList>) are intentionally not parsed here;
they belong as device triggers, not switch entities.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# A real .jts export is a few hundred KB. Reject anything wildly larger before
# handing it to ElementTree — a cheap guard against an accidental huge paste or
# a maliciously entity-expanding document.
_MAX_JTS_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class JtsConfig:
    """Result of parsing a .jts file."""

    loads: dict[int, str] = field(default_factory=dict)
    scenes: dict[int, str] = field(default_factory=dict)
    dimmable: dict[int, bool] = field(default_factory=dict)


def _is_true(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() == "true"


def _as_index(value: str | None) -> int | None:
    """Parse a positive integer id, or None if missing/invalid/non-positive."""
    if value is None:
        return None
    text = value.strip()
    if not text.lstrip("-").isdigit():
        return None
    num = int(text)
    return num if num > 0 else None


def parse_jts(text: str) -> JtsConfig:
    """Parse a .jts XML string into a JtsConfig.

    Raises ValueError if the text is not well-formed XML (callers map this to a
    user-facing "couldn't parse" error). The text is encoded to bytes before
    parsing so an embedded ``encoding=`` declaration doesn't trip ElementTree's
    "Unicode strings with encoding declaration are not supported" check.
    """
    if len(text) > _MAX_JTS_BYTES:
        raise ValueError("Pasted .jts is implausibly large; refusing to parse")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"Not well-formed .jts XML: {exc}") from exc

    loads: dict[int, str] = {}
    dimmable: dict[int, bool] = {}
    scenes: dict[int, str] = {}

    device_list = root.find("DeviceList")
    if device_list is not None:
        for dev in device_list.findall("Device"):
            idx = _as_index(dev.findtext("DeviceID"))
            if idx is None:
                continue
            if not _is_true(dev.findtext("SendThirdParty")):
                continue  # not exposed to RS-232; unusable here
            if not _is_true(dev.findtext("Active"), default=True):
                continue
            loads[idx] = (dev.findtext("Name") or "").strip()
            dimmable[idx] = _is_true(dev.findtext("Dimmer"), default=True)

    scene_list = root.find("SceneList")
    if scene_list is not None:
        for scene in scene_list.findall("Scene"):
            idx = _as_index(scene.findtext("ID"))
            if idx is None:
                continue
            scenes[idx] = (scene.findtext("Name") or "").strip()

    return JtsConfig(loads=loads, scenes=scenes, dimmable=dimmable)
