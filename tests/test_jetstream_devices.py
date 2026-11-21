"""Tests for the pure JetStream device-inventory helpers (no Home Assistant)."""

from __future__ import annotations

from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.jetstream_devices import (
    CONF_TOPOLOGY,
    TOPOLOGY_BRIDGE,
    TOPOLOGY_PER_DEVICE,
    is_per_device,
    parse_unit_identifier,
    unit_addresses,
    unit_identifier,
    unit_name,
)


def _data(**extra):
    return {CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM, **extra}


def test_addresses_are_loads_plus_button_devices_sorted_and_unique():
    data = _data(
        **{CONF_LOAD_IDS: [30, 5], CONF_BUTTON_IDS: [[5, 1], [44, 1], [44, 3]]}
    )
    assert unit_addresses(data) == [5, 30, 44]


def test_scene_ids_are_not_devices():
    data = _data(**{CONF_LOAD_IDS: [1], CONF_SCENE_IDS: [7, 8]})
    assert unit_addresses(data) == [1]


def test_addresses_empty_when_nothing_configured():
    assert unit_addresses(_data()) == []


def test_malformed_button_pairs_are_ignored_not_reinterpreted():
    data = _data(**{CONF_BUTTON_IDS: [[9, 1], [], "x", [None, 2], [12]]})
    assert unit_addresses(data) == [9]


def test_identifier_is_scoped_to_the_entry_and_zero_padded():
    assert unit_identifier("abc", 7) == (DOMAIN, "abc_jetstream_device_007")
    assert unit_identifier("abc", 7) != unit_identifier("xyz", 7)


def test_parse_identifier_round_trips():
    assert parse_unit_identifier("abc", unit_identifier("abc", 44)) == 44


def test_parse_identifier_rejects_bridge_other_entry_and_garbage():
    assert parse_unit_identifier("abc", (DOMAIN, "abc")) is None
    assert parse_unit_identifier("abc", unit_identifier("xyz", 44)) is None
    assert parse_unit_identifier("abc", ("other", "abc_jetstream_device_044")) is None
    assert parse_unit_identifier("abc", (DOMAIN, "abc_jetstream_device_4x")) is None


def test_unit_name_uses_the_imported_load_name_verbatim():
    options = {OPT_LOAD_NAMES: {"17": "FAMILY W-2-"}}
    assert unit_name(options, 17) == "FAMILY W-2-"


def test_unit_name_falls_back_when_missing_or_blank():
    assert unit_name({}, 17) is None
    assert unit_name({OPT_LOAD_NAMES: {"17": "   "}}, 17) is None


def test_topology_defaults_to_bridge_when_key_absent():
    assert is_per_device(_data()) is False
    assert is_per_device(_data(**{CONF_TOPOLOGY: TOPOLOGY_BRIDGE})) is False
    assert is_per_device(_data(**{CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE})) is True


def test_topology_never_applies_to_elegance():
    data = {CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE, CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE}
    assert is_per_device(data) is False
