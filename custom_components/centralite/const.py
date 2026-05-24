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

# Options keys
OPT_LOAD_NAMES: Final = "load_names"
OPT_SWITCH_NAMES: Final = "switch_names"
OPT_SCENE_NAMES: Final = "scene_names"
OPT_POLL_INTERVAL: Final = "poll_interval"

# Defaults
DEFAULT_BAUD: Final = 19200
DEFAULT_POLL_INTERVAL: Final = 300  # seconds; 0 or None disables safety poll
