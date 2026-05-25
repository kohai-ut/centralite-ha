"""Parser for Centralite Elegance .elg config files.

The .elg format is INI-style plain text exported by the Centralite Elegance
Programming Software (REV 1.1 as of the sample we tested). It contains
sections like [LOAD nnn] with NAME=... key-value bodies, and scene names
embedded in section headers like [SCENE nn- Friendly Scene Name].

This parser extracts what the integration needs (load names indexed by load
number, and scene names indexed by scene number) and ignores everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")
LOAD_HEADER_RE = re.compile(r"^LOAD\s+(\d+)$")
SCENE_HEADER_RE = re.compile(r"^SCENE\s+(\d+)-\s*(.+)$")
# A keypad button section header is a letter A-P plus a button number, e.g. [A1].
BUTTON_HEADER_RE = re.compile(r"^[A-P]\d+$")
# Inside a scene body, each controlled load is listed as "LOAD_053- Some Name".
SCENE_LOAD_RE = re.compile(r"^LOAD_(\d+)-")


@dataclass(frozen=True, slots=True)
class ElgConfig:
    """Result of parsing a .elg file.

    `dimmable` maps each load number to whether it is a dimmer (DIMMER=Y) or a
    plain on/off relay (DIMMER=N). Most loads in a real install are relays, so
    this lets the light platform expose them as on/off lights instead of giving
    every load a meaningless brightness slider. Loads with no DIMMER key default
    to True (dimmable) to preserve the prior all-dimmable behavior.

    `referenced_loads` is the set of load numbers actually used somewhere in the
    config — controlled by a scene or assigned to an active keypad button. A
    `.elg` lists all 192 load slots (most are unnamed, unused defaults), so the
    config flow creates only loads that are named OR referenced, skipping the
    phantom slots.
    """

    loads: dict[int, str] = field(default_factory=dict)
    scenes: dict[int, str] = field(default_factory=dict)
    dimmable: dict[int, bool] = field(default_factory=dict)
    referenced_loads: set[int] = field(default_factory=set)


def _referenced_load_ids(text: str) -> set[int]:
    """Collect loads used by any scene or assigned to any active keypad button.

    Scene bodies list controlled loads as ``LOAD_nnn- name``. Keypad button
    sections (``[A1]`` .. ``[P24]``) assign a target with ``LOAD/SCENE=L`` and
    ``#=nnn`` and are gated by ``ACTIVE=1``. We group by section first so a
    button's multi-line fields can be evaluated together.
    """
    referenced: set[int] = set()
    section: str | None = None
    fields: dict[str, str] = {}

    def flush_button() -> None:
        if section and BUTTON_HEADER_RE.match(section):
            if (
                fields.get("LOAD/SCENE") == "L"
                and fields.get("ACTIVE") == "1"
                and fields.get("#", "").isdigit()
            ):
                referenced.add(int(fields["#"]))

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        section_match = SECTION_RE.match(line)
        if section_match:
            flush_button()
            section = section_match.group(1).strip()
            fields = {}
            continue
        if section is None:
            continue
        if section.startswith("SCENE "):
            load_match = SCENE_LOAD_RE.match(line.strip())
            if load_match:
                referenced.add(int(load_match.group(1)))
        elif BUTTON_HEADER_RE.match(section) and "=" in line:
            key, _, value = line.strip().partition("=")
            fields[key.strip()] = value.strip()

    flush_button()  # finalize the last section
    return referenced


def parse_elg(text: str) -> ElgConfig:
    """Parse a .elg INI-style file and return load and scene name mappings.

    Loads: every [LOAD nnn] section contributes one entry, keyed on the load
    number, with the NAME= value (or empty string if NAME is missing/blank).

    Scenes: every [SCENE nn- name] section header contributes one entry,
    keyed on the scene number, with the friendly name parsed from the
    header itself.

    Lines that don't match either pattern are ignored. The parser is
    tolerant: malformed sections don't raise, they're skipped.
    """
    loads: dict[int, str] = {}
    scenes: dict[int, str] = {}
    dimmable: dict[int, bool] = {}

    current_load: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            continue

        section_match = SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1).strip()
            current_load = None

            load_match = LOAD_HEADER_RE.match(section)
            if load_match:
                current_load = int(load_match.group(1))
                loads.setdefault(current_load, "")
                dimmable.setdefault(current_load, True)
                continue

            scene_match = SCENE_HEADER_RE.match(section)
            if scene_match:
                scenes[int(scene_match.group(1))] = scene_match.group(2).strip()
                continue

            continue

        if current_load is not None and "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key == "NAME":
                loads[current_load] = value.strip()
            elif key == "DIMMER":
                # Values seen: "Y" / "N". Treat anything starting with Y as
                # dimmable; everything else (N, blank) as on/off.
                dimmable[current_load] = value.strip().upper().startswith("Y")

    return ElgConfig(
        loads=loads,
        scenes=scenes,
        dimmable=dimmable,
        referenced_loads=_referenced_load_ids(text),
    )


def parse_csv_ids(text: str) -> list[int]:
    """Parse a comma-separated list of integer IDs from user input."""
    if not text.strip():
        return []
    ids: set[int] = set()
    for token in text.split(","):
        t = token.strip()
        if not t:
            continue
        ids.add(int(t))
    return sorted(ids)
