"""Full async_migrate_entries tests against a real HA entity registry.

test_migrate.py covers the pure `classify` rules. This covers the registry
mutation: rename + reassign, scene-OFF deletion, idempotency, and the
system-scoping regression (an Elegance entry must not adopt JetStream orphans).
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import CONF_SYSTEM_TYPE, DOMAIN, SYSTEM_ELEGANCE
from custom_components.centralite.migrate import async_migrate_entries


def _entry(hass, system=SYSTEM_ELEGANCE):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SYSTEM_TYPE: system, "port": "/dev/ttyUSB0", "baud": 19200},
    )
    entry.add_to_hass(hass)
    return entry


def _orphan(registry, platform, unique_id, domain="light"):
    """Create a v1-style orphan entity (no config entry attached)."""
    return registry.async_get_or_create(domain, platform, unique_id)


async def test_renames_and_reassigns_elegance_load(hass):
    registry = er.async_get(hass)
    ent = _orphan(registry, "elegance", "elegance.L001")
    entry = _entry(hass)
    await async_migrate_entries(hass, entry)
    migrated = registry.async_get(ent.entity_id)
    assert migrated.unique_id == f"{entry.entry_id}_load_001"
    assert migrated.config_entry_id == entry.entry_id


async def test_scenes_are_deleted_not_renamed(hass):
    """v1 scenes are `scene.*` entities; v2 scenes are `switch.*`. Renaming the
    scene-domain row's unique_id can't let the v2 switch adopt it (registry keys
    on domain+platform+unique_id), so it would orphan an unavailable `scene.*`.
    Both ON and OFF must be DELETED. Uses domain="scene" to match real v1 —
    the earlier test used "switch" and so never exercised the domain mismatch."""
    registry = er.async_get(hass)
    on = _orphan(registry, "elegance", "elegance.scene4ON", domain="scene")
    off = _orphan(registry, "elegance", "elegance.scene4OFF", domain="scene")
    plain = _orphan(registry, "elegance", "elegance.scene7", domain="scene")
    entry = _entry(hass)
    await async_migrate_entries(hass, entry)
    assert registry.async_get(on.entity_id) is None
    assert registry.async_get(off.entity_id) is None
    assert registry.async_get(plain.entity_id) is None


async def test_does_not_adopt_other_systems_orphans(hass):
    """Regression: an Elegance entry must leave JetStream orphans untouched."""
    registry = er.async_get(hass)
    eleg = _orphan(registry, "elegance", "elegance.L001")
    jet = _orphan(registry, "jetstream", "jetstream.JSL001")
    entry = _entry(hass, system=SYSTEM_ELEGANCE)
    await async_migrate_entries(hass, entry)

    assert registry.async_get(eleg.entity_id).config_entry_id == entry.entry_id
    # JetStream orphan is left exactly as it was.
    untouched = registry.async_get(jet.entity_id)
    assert untouched.unique_id == "jetstream.JSL001"
    assert untouched.config_entry_id is None


async def test_idempotent_second_run_is_noop(hass):
    registry = er.async_get(hass)
    ent = _orphan(registry, "elegance", "elegance.L001")
    entry = _entry(hass)
    await async_migrate_entries(hass, entry)
    new_uid = registry.async_get(ent.entity_id).unique_id
    # Second run: already migrated (has config_entry_id), so nothing changes.
    await async_migrate_entries(hass, entry)
    assert registry.async_get(ent.entity_id).unique_id == new_uid


async def test_unrelated_integration_left_alone(hass):
    registry = er.async_get(hass)
    other = _orphan(registry, "hue", "hue.0017880100000000")
    entry = _entry(hass)
    await async_migrate_entries(hass, entry)
    assert registry.async_get(other.entity_id).unique_id == "hue.0017880100000000"
    assert registry.async_get(other.entity_id).config_entry_id is None
