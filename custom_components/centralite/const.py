"""Constants for the Centralite integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "centralite"
MANUFACTURER: Final = "CentraLite Systems"

# System types — selected per config entry
SYSTEM_ELEGANCE: Final = "elegance"
SYSTEM_JETSTREAM: Final = "jetstream"
SYSTEM_ELITE: Final = "elite"  # reserved for legacy eLite

SYSTEM_LABELS: Final[dict[str, str]] = {
    SYSTEM_ELEGANCE: "Elegance",
    SYSTEM_JETSTREAM: "JetStream",
    SYSTEM_ELITE: "eLite",
}

# Config entry data keys
CONF_SYSTEM_TYPE: Final = "system_type"
CONF_PORT: Final = "port"
CONF_BAUD: Final = "baud"
CONF_LOAD_IDS: Final = "load_ids"
CONF_SWITCH_IDS: Final = "switch_ids"  # Elegance: list[int]
CONF_BUTTON_IDS: Final = "button_ids"  # JetStream: list[[device, button]]
CONF_SCENE_IDS: Final = "scene_ids"
# Loads created but disabled-by-default (referenced by a scene/button but never
# named in the .elg). Present in CONF_LOAD_IDS; users can enable them in the UI.
CONF_DISABLED_LOADS: Final = "disabled_loads"

# Options keys
OPT_LOAD_NAMES: Final = "load_names"
OPT_SWITCH_NAMES: Final = "switch_names"
OPT_SCENE_NAMES: Final = "scene_names"
OPT_POLL_INTERVAL: Final = "poll_interval"
# Load indices that are on/off relays (DIMMER=N in the .elg). Exposed as
# on/off lights rather than dimmable. Loads not listed default to dimmable.
OPT_NONDIMMABLE_LOADS: Final = "nondimmable_loads"

# Defaults
DEFAULT_BAUD: Final = 19200
DEFAULT_POLL_INTERVAL: Final = 300  # seconds; 0 or None disables safety poll

# Bus event fired on every physical keypad button activity (tap/press/release).
# device_trigger.py exposes these as HA device triggers; automations can also
# listen for it directly. Data: device_id, type (action), subtype (which button).
EVENT_BUTTON: Final = f"{DOMAIN}_event"
# Event-data key naming which button fired. Paired with CONF_TYPE (the action).
CONF_SUBTYPE: Final = "subtype"


def button_subtype(device_idx: int, button: int) -> str:
    """Stable subtype string identifying one keypad button.

    Shared by the coordinator (when firing EVENT_BUTTON) and device_trigger.py
    (when enumerating triggers) so the two always agree. Elegance switches have
    no separate button index and use button 0.
    """
    return f"device_{device_idx:03d}_button_{button}"
