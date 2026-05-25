"""Tests for the Sync Clock button (Elegance ^K/^L clock set).

The button only exists on bridges with a settable clock (supports_clock).
Pressing it writes HA's current local time to the bridge via set_clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_LOAD_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)

from .conftest import FakeProtocol

_ELEGANCE_PROTO = "custom_components.centralite.protocol.elegance.EleganceProtocol"
_JETSTREAM_PROTO = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"


def _entry(hass, system):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: system,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_LOAD_IDS: [1],
        },
        title="Bridge",
        unique_id=f"{system}@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def test_clock_button_created_for_elegance(hass):
    entry = _entry(hass, SYSTEM_ELEGANCE)
    proto = FakeProtocol(supports_clock=True, bulk_loads={1: True})
    with patch(_ELEGANCE_PROTO, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("button.bridge_sync_clock") is not None
    # Config-category so it sits under the device's configuration controls.
    ent = er.async_get(hass).async_get("button.bridge_sync_clock")
    assert ent.entity_category is EntityCategory.CONFIG


async def test_clock_button_absent_for_jetstream(hass):
    """JetStream has no clock command, so the button must not be created."""
    entry = _entry(hass, SYSTEM_JETSTREAM)
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(_JETSTREAM_PROTO, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("button.bridge_sync_clock") is None


async def test_press_sets_clock_to_local_now(hass):
    # Use a non-UTC zone so we can prove the button sends LOCAL wall-clock
    # time, not UTC — a regression to dt_util.utcnow() must fail this test.
    await hass.config.async_set_time_zone("America/Chicago")
    fixed_local = datetime(2026, 5, 24, 13, 30, 0, tzinfo=ZoneInfo("America/Chicago"))

    entry = _entry(hass, SYSTEM_ELEGANCE)
    proto = FakeProtocol(supports_clock=True, bulk_loads={1: True})
    with patch(_ELEGANCE_PROTO, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch(
            "custom_components.centralite.button.dt_util.now",
            return_value=fixed_local,
        ):
            await hass.services.async_call(
                "button",
                "press",
                {"entity_id": "button.bridge_sync_clock"},
                blocking=True,
            )

    clock_calls = [c for c in proto.calls if c[0] == "set_clock"]
    assert len(clock_calls) == 1
    sent = clock_calls[0][1]
    assert isinstance(sent, datetime)
    # Local, not UTC: Chicago is offset from UTC, and the wall-clock fields
    # (what encode_bcd_clock transmits) are the local ones.
    assert sent.utcoffset() != timedelta(0)
    assert (sent.hour, sent.minute) == (13, 30)
