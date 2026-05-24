"""Light entity tests, including the brightness-floor regression."""

from __future__ import annotations

from homeassistant.components.light import ATTR_BRIGHTNESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    SYSTEM_ELEGANCE,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.light import CentraliteLight

from .conftest import FakeProtocol


def _coord(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE, "port": "/dev/ttyUSB0", "baud": 19200},
        options=options or {},
        title="Bridge",
    )
    entry.add_to_hass(hass)
    return CentraliteCoordinator(hass, entry, FakeProtocol(bulk_loads={}))


async def test_brightness_round_trip(hass):
    coord = _coord(hass)
    coord.data["loads"][1] = {"on": True, "level": 99}
    light = CentraliteLight(coord, 1)
    assert light.is_on is True
    assert light.brightness == 255
    coord.data["loads"][1] = {"on": True, "level": 50}
    assert light.brightness == 128


async def test_off_state(hass):
    coord = _coord(hass)
    coord.data["loads"][1] = {"on": False, "level": 0}
    light = CentraliteLight(coord, 1)
    assert light.is_on is False
    assert light.brightness == 0


async def test_unknown_load_defaults_off(hass):
    light = CentraliteLight(_coord(hass), 99)
    assert light.is_on is False
    assert light.brightness == 0


async def test_turn_on_low_brightness_never_sends_zero(hass):
    """Regression: turn_on(brightness=1) must dim, not turn the load off."""
    coord = _coord(hass)
    light = CentraliteLight(coord, 1)
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 1})
    assert coord.protocol.calls == [("set_load_level", 1, 1, 0)]
    # And 2 -> still 1, not 0.
    coord.protocol.calls.clear()
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 2})
    assert coord.protocol.calls == [("set_load_level", 1, 1, 0)]


async def test_turn_on_full_brightness(hass):
    coord = _coord(hass)
    light = CentraliteLight(coord, 1)
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
    assert coord.protocol.calls == [("set_load_level", 1, 99, 0)]


async def test_turn_on_no_brightness_activates(hass):
    coord = _coord(hass)
    light = CentraliteLight(coord, 1)
    await light.async_turn_on()
    assert coord.protocol.calls == [("activate_load", 1)]


async def test_turn_off(hass):
    coord = _coord(hass)
    light = CentraliteLight(coord, 1)
    await light.async_turn_off()
    assert coord.protocol.calls == [("deactivate_load", 1)]


async def test_name_from_options(hass):
    coord = _coord(hass, {OPT_LOAD_NAMES: {"1": "Kitchen"}})
    assert CentraliteLight(coord, 1)._attr_name == "Kitchen"
    assert CentraliteLight(coord, 2)._attr_name == "Load 002"
