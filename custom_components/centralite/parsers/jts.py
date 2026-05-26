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
state. Configured keypad buttons in each <buttonList> are parsed too (as button
switch entities); physical presses also drive device triggers (device_trigger).
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
    # Configured keypad buttons -> a label. Keyed (device_id, protocol_button)
    # where protocol_button is 1-3 (the .jts numbers buttons 0-7 but only 0-2
    # are ever configured / addressable, mapping 0->1, 1->2, 2->3).
    buttons: dict[tuple[int, int], str] = field(default_factory=dict)


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
    buttons: dict[tuple[int, int], str] = {}

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
            name = (dev.findtext("Name") or "").strip()
            loads[idx] = name
            dimmable[idx] = _is_true(dev.findtext("Dimmer"), default=True)
            _collect_buttons(dev, idx, name, buttons)

    scene_list = root.find("SceneList")
    if scene_list is not None:
        for scene in scene_list.findall("Scene"):
            idx = _as_index(scene.findtext("ID"))
            if idx is None:
                continue
            scenes[idx] = (scene.findtext("Name") or "").strip()

    return JtsConfig(loads=loads, scenes=scenes, dimmable=dimmable, buttons=buttons)


def _button_configured(button: ET.Element) -> bool:
    """True if any of the button's actions has a non-zero BtnAction.

    BtnAction is compared numerically (not as a string) so values like "00" or
    " 0 " are correctly treated as unconfigured.
    """
    for name in ("tap", "pressandhold", "doubletap"):
        action = button.find(name)
        if action is None:
            continue
        raw = (action.findtext("BtnAction") or "").strip()
        if raw.lstrip("-").isdigit() and int(raw) != 0:
            return True
    return False


def _collect_buttons(
    dev: ET.Element, device_idx: int, device_name: str, out: dict[tuple[int, int], str]
) -> None:
    """Add configured keypad buttons for a device to ``out``.

    A button (``<Button>`` with a 0-based ``<ID>``) is "configured" if any of
    its tap/press-and-hold/double-tap actions has a non-zero ``<BtnAction>``.
    Only IDs 0-2 are kept — the protocol addresses buttons 1-3 — mapped ID+1.

    The button is keyed by ``device_idx`` — the **parent** ``<Device>``'s
    ``<DeviceID>`` (the load) — NOT the per-``<Button>`` inner ``<DeviceID>``,
    which is a different number (e.g. a parent load 29 has buttons whose inner
    DeviceID is 22). Hardware confirms the protocol addresses a keypad button by
    the parent device id: pressing a button on load 55 emits ``ACT05501T``
    (device 055, button 1) and ``DEV05500`` (load 055). So do NOT switch this to
    the inner ``<DeviceID>``.
    """
    button_list = dev.find("buttonList")
    if button_list is None:
        return
    for button in button_list.findall("Button"):
        raw_id = (button.findtext("ID") or "").strip()
        if not raw_id.isdigit():
            continue
        bid = int(raw_id)
        if bid > 2:  # not addressable over the third-party protocol
            continue
        if _button_configured(button):
            protocol_button = bid + 1
            label = f"{device_name} Button {protocol_button}" if device_name else ""
            out[(device_idx, protocol_button)] = label
