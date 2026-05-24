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


@dataclass(frozen=True, slots=True)
class ElgConfig:
    """Result of parsing a .elg file."""

    loads: dict[int, str] = field(default_factory=dict)
    scenes: dict[int, str] = field(default_factory=dict)


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
                continue

            scene_match = SCENE_HEADER_RE.match(section)
            if scene_match:
                scenes[int(scene_match.group(1))] = scene_match.group(2).strip()
                continue

            continue

        if current_load is not None and "=" in stripped:
            key, _, value = stripped.partition("=")
            if key.strip() == "NAME":
                loads[current_load] = value.strip()

    return ElgConfig(loads=loads, scenes=scenes)


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
