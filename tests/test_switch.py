"""Switch entity tests: Elegance button, JetStream button, scene switch."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_SCENE_NAMES,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.switch import (
    CentraliteEleganceButtonSwitch,
    CentraliteJetStreamButtonSwitch,
    CentraliteSceneSwitch,
)

from .conftest import FakeProtocol


def _coord(hass, system, *, scene_push=False, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SYSTEM_TYPE: system, "port": "/dev/ttyUSB0", "baud": 19200},
        options=options or {},
        title="Bridge",
    )
    entry.add_to_hass(hass)
    return CentraliteCoordinator(
        hass, entry, FakeProtocol(supports_scene_push=scene_push, bulk_loads={})
    )


async def test_elegance_button_press_release(hass):
    coord = _coord(hass, SYSTEM_ELEGANCE)
    sw = CentraliteEleganceButtonSwitch(coord, 5)
    assert sw.is_on is False
    coord.data["switches"][(5, 0)] = True
    assert sw.is_on is True
    await sw.async_turn_on()
    await sw.async_turn_off()
    assert coord.protocol.calls == [("press_switch", 5), ("release_switch", 5)]


async def test_jetstream_button_taps(hass):
    coord = _coord(hass, SYSTEM_JETSTREAM, scene_push=True)
    sw = CentraliteJetStreamButtonSwitch(coord, 44, 2)
    coord.data["switches"][(44, 2)] = True
    assert sw.is_on is True
    await sw.async_turn_on()
    assert coord.protocol.calls == [("tap_switch", 44, 2)]
    # turn_off is a documented no-op for momentary JetStream buttons.
    coord.protocol.calls.clear()
    await sw.async_turn_off()
    assert coord.protocol.calls == []


async def test_scene_switch_elegance_commanded(hass):
    coord = _coord(hass, SYSTEM_ELEGANCE, options={OPT_SCENE_NAMES: {"3": "Movie"}})
    sw = CentraliteSceneSwitch(coord, 3)
    assert sw._attr_name == "Movie"
    assert sw.is_on is False
    await sw.async_turn_on()
    assert sw.is_on is True  # commanded state reflected immediately (no push)
    assert ("activate_scene", 3) in coord.protocol.calls
    await sw.async_turn_off()
    assert sw.is_on is False


async def test_scene_switch_default_name(hass):
    coord = _coord(hass, SYSTEM_ELEGANCE)
    assert CentraliteSceneSwitch(coord, 7)._attr_name == "Scene 007"
