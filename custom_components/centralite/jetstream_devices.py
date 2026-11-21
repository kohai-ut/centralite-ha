"""Pure helpers describing JetStream physical devices (no Home Assistant, no I/O).

A JetStream "unit" is one physical product on the mesh (a dimmer, a switch, a
keypad), addressed by its Device ID on the RS-232 protocol. The config entry
stores units only implicitly, as load IDs and (device, button) pairs; these
helpers rebuild the unit list from that data so the device registry, entities
and triggers all agree on it.

Which layout an entry uses is one flag in its data. Absent means the original
single-bridge layout, so an existing install is never reorganised without the
user asking for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .const import (
    CONF_BUTTON_IDS,
    CONF_LOAD_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    SYSTEM_JETSTREAM,
)

CONF_TOPOLOGY: Final = "jetstream_topology"
TOPOLOGY_BRIDGE: Final = "bridge"
TOPOLOGY_PER_DEVICE: Final = "per_device"

_UNIT_MARKER: Final = "_jetstream_device_"


def is_per_device(data: Mapping[str, Any]) -> bool:
    """True when this entry puts each JetStream unit on its own device."""
    return (
        data.get(CONF_SYSTEM_TYPE) == SYSTEM_JETSTREAM
        and data.get(CONF_TOPOLOGY) == TOPOLOGY_PER_DEVICE
    )


def button_device_ids(data: Mapping[str, Any]) -> set[int]:
    """Device IDs that have at least one configured button.

    Malformed pairs are skipped, never guessed at: an address is a wire
    address, so a wrong one would talk to a different device.
    """
    ids: set[int] = set()
    for pair in data.get(CONF_BUTTON_IDS, []):
        if (
            isinstance(pair, (list, tuple))
            and len(pair) == 2
            and all(isinstance(part, int) for part in pair)
        ):
            ids.add(pair[0])
    return ids


def unit_addresses(data: Mapping[str, Any]) -> list[int]:
    """Every unit address in the entry: loads plus devices with buttons.

    Scenes are not units; they stay on the bridge.
    """
    loads = {idx for idx in data.get(CONF_LOAD_IDS, []) if isinstance(idx, int)}
    return sorted(loads | button_device_ids(data))


def unit_identifier(entry_id: str, idx: int) -> tuple[str, str]:
    """Device-registry identifier for one unit, scoped to its bridge entry."""
    return (DOMAIN, f"{entry_id}{_UNIT_MARKER}{idx:03d}")


def parse_unit_identifier(entry_id: str, identifier: tuple[str, str]) -> int | None:
    """Unit address for one of this entry's unit identifiers, else None."""
    domain, value = identifier
    prefix = f"{entry_id}{_UNIT_MARKER}"
    if domain != DOMAIN or not value.startswith(prefix):
        return None
    tail = value[len(prefix) :]
    return int(tail) if tail.isascii() and tail.isdigit() else None


def unit_name(options: Mapping[str, Any], idx: int) -> str | None:
    """The imported name for a unit, verbatim, or None if it has none."""
    name = options.get(OPT_LOAD_NAMES, {}).get(str(idx))
    if isinstance(name, str) and name.strip():
        return name
    return None
