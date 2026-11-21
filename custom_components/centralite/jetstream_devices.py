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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .const import (
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    OPT_SWITCH_NAMES,
    SYSTEM_JETSTREAM,
)

CONF_TOPOLOGY: Final = "jetstream_topology"
TOPOLOGY_BRIDGE: Final = "bridge"
TOPOLOGY_PER_DEVICE: Final = "per_device"

_UNIT_MARKER: Final = "_jetstream_device_"

# Limits of the JetStream RS-232 protocol (kept here so this module stays free
# of serial imports; a test pins them to the protocol's own constants).
MAX_DEVICE_ID: Final = 199
MAX_BUTTON: Final = 3

# What the user says a device is when adding it. Only a way of filling in the
# stored lists; the type itself is never stored.
TYPE_DIMMER: Final = "dimmer"
TYPE_SWITCH: Final = "switch"
TYPE_KEYPAD: Final = "keypad"
DEVICE_TYPES: Final = (TYPE_DIMMER, TYPE_SWITCH, TYPE_KEYPAD)

# Words accepted for each type in batch entry.
_TYPE_WORDS: Final = {
    "dimmer": TYPE_DIMMER,
    "dimmable": TYPE_DIMMER,
    "switch": TYPE_SWITCH,
    "on/off": TYPE_SWITCH,
    "onoff": TYPE_SWITCH,
    "keypad": TYPE_KEYPAD,
    "keypad only": TYPE_KEYPAD,
}


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


# --- Editing the device list after setup -----------------------------------


class DeviceEditError(ValueError):
    """A requested change to the device list is not valid.

    ``code`` is a stable key the options flow turns into a translated message;
    ``line`` is the 1-based line of a batch entry, when there is one.
    """

    def __init__(self, code: str, *, line: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.line = line


@dataclass(frozen=True)
class DeviceAddition:
    """One device the user asked to add."""

    device_id: int
    device_type: str
    buttons: frozenset[int] = frozenset()
    name: str | None = None


def _button_pairs(data: Mapping[str, Any]) -> set[tuple[int, int]]:
    return {
        (pair[0], pair[1])
        for pair in data.get(CONF_BUTTON_IDS, [])
        if isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(part, int) for part in pair)
    }


def _validate(addition: DeviceAddition) -> None:
    if addition.device_type not in DEVICE_TYPES:
        raise DeviceEditError("bad_type")
    if not 1 <= addition.device_id <= MAX_DEVICE_ID:
        raise DeviceEditError("id_out_of_range")
    if any(not 1 <= button <= MAX_BUTTON for button in addition.buttons):
        raise DeviceEditError("bad_button")
    if addition.device_type == TYPE_KEYPAD and not addition.buttons:
        raise DeviceEditError("keypad_needs_button")


def merge_additions(
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    additions: Iterable[DeviceAddition],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """New entry data and options with the additions merged in.

    Adding never changes what an address already has: an existing load keeps
    its dimmer or on/off setting and its name, and existing buttons stay. An
    address that is already configured can only gain parts.
    """
    loads = {idx for idx in data.get(CONF_LOAD_IDS, []) if isinstance(idx, int)}
    disabled = set(data.get(CONF_DISABLED_LOADS, []))
    nondimmable = set(options.get(OPT_NONDIMMABLE_LOADS, []))
    pairs = _button_pairs(data)
    names = dict(options.get(OPT_LOAD_NAMES, {}))
    known = loads | {device for device, _ in pairs}
    seen: set[int] = set()

    for addition in additions:
        _validate(addition)
        idx = addition.device_id
        if idx in seen:
            raise DeviceEditError("duplicate_id")
        seen.add(idx)

        if addition.device_type != TYPE_KEYPAD:
            if idx not in loads:
                loads.add(idx)
                if addition.device_type == TYPE_SWITCH:
                    nondimmable.add(idx)
            # Asking for a load by ID is an explicit request for it, so it is
            # no longer one the import chose to leave disabled.
            disabled.discard(idx)
        pairs.update((idx, button) for button in addition.buttons)
        if addition.name and addition.name.strip() and idx not in known:
            names[str(idx)] = addition.name.strip()

    new_data = dict(data)
    new_data[CONF_LOAD_IDS] = sorted(loads)
    new_data[CONF_BUTTON_IDS] = [list(pair) for pair in sorted(pairs)]
    if CONF_DISABLED_LOADS in data or disabled:
        new_data[CONF_DISABLED_LOADS] = sorted(disabled)
    new_options = dict(options)
    new_options[OPT_NONDIMMABLE_LOADS] = sorted(nondimmable)
    new_options[OPT_LOAD_NAMES] = names
    return new_data, new_options


def parse_batch(text: str) -> list[DeviceAddition]:
    """Parse batch entry: one device per line as ``ID, type[, button...]``.

    For example ``81, dimmer, 1, 3`` is a dimmer at address 81 with buttons 1
    and 3. Blank lines are skipped. The first bad line is reported by number.
    """
    additions: list[DeviceAddition] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        fields = [part.strip() for part in raw.split(",")]
        try:
            if len(fields) < 2:
                raise ValueError("missing type")
            device_type = _TYPE_WORDS[fields[1].lower()]
            additions.append(
                DeviceAddition(
                    int(fields[0]),
                    device_type,
                    frozenset(int(part) for part in fields[2:] if part),
                )
            )
        except (KeyError, ValueError) as err:
            raise DeviceEditError("bad_line", line=number) from err
    if not additions:
        raise DeviceEditError("empty")
    return additions


def change_load_type(
    data: Mapping[str, Any], options: Mapping[str, Any], idx: int, *, dimmable: bool
) -> dict[str, Any]:
    """New options with one load set to dimmer or on/off."""
    if idx not in data.get(CONF_LOAD_IDS, []):
        raise DeviceEditError("not_a_load")
    nondimmable = set(options.get(OPT_NONDIMMABLE_LOADS, []))
    if dimmable:
        nondimmable.discard(idx)
    else:
        nondimmable.add(idx)
    new_options = dict(options)
    new_options[OPT_NONDIMMABLE_LOADS] = sorted(nondimmable)
    return new_options


def remove_unit(
    data: Mapping[str, Any], options: Mapping[str, Any], idx: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """New entry data and options with one address removed entirely."""
    if idx not in unit_addresses(data):
        raise DeviceEditError("unknown_device")
    new_data = dict(data)
    new_data[CONF_LOAD_IDS] = [i for i in data.get(CONF_LOAD_IDS, []) if i != idx]
    new_data[CONF_BUTTON_IDS] = [
        list(pair) for pair in sorted(_button_pairs(data)) if pair[0] != idx
    ]
    if CONF_DISABLED_LOADS in data:
        new_data[CONF_DISABLED_LOADS] = [
            i for i in data[CONF_DISABLED_LOADS] if i != idx
        ]
    new_options = dict(options)
    new_options[OPT_NONDIMMABLE_LOADS] = [
        i for i in options.get(OPT_NONDIMMABLE_LOADS, []) if i != idx
    ]
    new_options[OPT_LOAD_NAMES] = {
        key: value
        for key, value in options.get(OPT_LOAD_NAMES, {}).items()
        if key != str(idx)
    }
    if OPT_SWITCH_NAMES in options:
        prefix = f"{idx:03d}."
        new_options[OPT_SWITCH_NAMES] = {
            key: value
            for key, value in options[OPT_SWITCH_NAMES].items()
            if not key.startswith(prefix)
        }
    return new_data, new_options
