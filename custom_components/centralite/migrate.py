"""One-time entity-registry migration from v1 unique_ids to v2 format.

When a user upgrades from the v1 integrations (kohai-ut/centralite_elegance
or kohai-ut/centralite_jetstream, both archived at v1.0.1) to this v2
integration, their existing entities live in HA's entity_registry with
unique_ids in the legacy format:

    elegance.L001         (light)
    elegance.SW044        (switch)
    elegance.scene4ON     (scene -- becomes a switch in v2)
    elegance.scene4OFF    (the paired OFF entity -- removed in v2)
    jetstream.JSL001      (light)
    jetstream.JSSW04401   (button device 044, button 01)
    jetstream.scene4ON / OFF
    centralite.l001       (older lowercase variants)

This module scans the registry for those patterns, renames them to the
v2 unique_id scheme ({entry_id}_load_001, etc.), and removes the now-
redundant scene*OFF entries. The function is idempotent — subsequent
runs find nothing because migrated entries are no longer orphans and
already use the new format.

Customizations (area, icon, alias, friendly_name override, dashboard
placement) are preserved because we use entity_registry.async_update_entity
rather than removing and recreating.

A Repairs issue is surfaced with the migration log so the user can find
any automations that referenced the old entity_ids (HA cannot auto-
rewrite automation YAML referencing the old IDs).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Outcome of classifying a v1 unique_id."""

    action: Literal["migrate", "delete", "skip"]
    new_suffix: str | None = None


# (pattern, transform) — transform returns the new unique_id suffix, or None
# means "delete this entity (absorbed by the v2 model)."
_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str] | None]] = [
    # Elegance load: elegance.L001 (or elegance.l001) -> load_001
    (
        re.compile(r"^elegance\.L(\d+)$", re.IGNORECASE),
        lambda m: f"load_{int(m.group(1)):03d}",
    ),
    # Elegance switch: elegance.SW044 -> switch_044
    (
        re.compile(r"^elegance\.SW(\d+)$", re.IGNORECASE),
        lambda m: f"switch_{int(m.group(1)):03d}",
    ),
    # Elegance scene OFF: delete (absorbed into scene-switch)
    (re.compile(r"^elegance\.scene(\d+)OFF$", re.IGNORECASE), None),
    # Elegance scene ON: rename to scene_NNN
    (
        re.compile(r"^elegance\.scene(\d+)ON$", re.IGNORECASE),
        lambda m: f"scene_{int(m.group(1)):03d}",
    ),
    # Elegance scene (no ON/OFF suffix): rename
    (
        re.compile(r"^elegance\.scene(\d+)$", re.IGNORECASE),
        lambda m: f"scene_{int(m.group(1)):03d}",
    ),
    # JetStream load: jetstream.JSL001 -> load_001
    (
        re.compile(r"^jetstream\.JSL(\d+)$", re.IGNORECASE),
        lambda m: f"load_{int(m.group(1)):03d}",
    ),
    # JetStream button: jetstream.JSSW00101 -> button_001_01
    (
        re.compile(r"^jetstream\.JSSW(\d{3})(\d{2})$", re.IGNORECASE),
        lambda m: f"button_{int(m.group(1)):03d}_{int(m.group(2)):02d}",
    ),
    # JetStream switch (no button index): jetstream.SW044 -> switch_044
    (
        re.compile(r"^jetstream\.SW(\d+)$", re.IGNORECASE),
        lambda m: f"switch_{int(m.group(1)):03d}",
    ),
    # JetStream scene OFF: delete
    (re.compile(r"^jetstream\.scene(\d+)OFF$", re.IGNORECASE), None),
    # JetStream scene ON: rename
    (
        re.compile(r"^jetstream\.scene(\d+)ON$", re.IGNORECASE),
        lambda m: f"scene_{int(m.group(1)):03d}",
    ),
    (
        re.compile(r"^jetstream\.scene(\d+)$", re.IGNORECASE),
        lambda m: f"scene_{int(m.group(1)):03d}",
    ),
]


def classify(v1_unique_id: str) -> MigrationResult:
    """Determine what to do with a v1-format unique_id.

    Returns a MigrationResult with action in {"migrate", "delete", "skip"}.
    For "migrate", new_suffix is the suffix to append after "{entry_id}_".
    """
    for pattern, transform in _RULES:
        match = pattern.match(v1_unique_id)
        if not match:
            continue
        if transform is None:
            return MigrationResult(action="delete")
        return MigrationResult(action="migrate", new_suffix=transform(match))
    return MigrationResult(action="skip")


async def async_migrate_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate v1 unique_ids in the entity registry to v2 format.

    Idempotent: safe to call on every async_setup_entry.
    """
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.issue_registry import (
        IssueSeverity,
        async_create_issue,
    )

    registry = er.async_get(hass)
    migrated: list[tuple[str, str, str]] = []
    deleted: list[tuple[str, str]] = []

    candidates = [
        ent for ent in list(registry.entities.values()) if ent.config_entry_id is None
    ]

    for ent in candidates:
        result = classify(ent.unique_id)
        if result.action == "skip":
            continue
        if result.action == "delete":
            registry.async_remove(ent.entity_id)
            deleted.append((ent.entity_id, ent.unique_id))
            continue
        # action == "migrate"
        assert result.new_suffix is not None
        new_uid = f"{entry.entry_id}_{result.new_suffix}"
        registry.async_update_entity(
            ent.entity_id,
            new_unique_id=new_uid,
            config_entry_id=entry.entry_id,
        )
        migrated.append((ent.entity_id, ent.unique_id, new_uid))

    if not migrated and not deleted:
        return

    _LOGGER.info(
        "v1 -> v2 migration: %d entities renamed, %d removed",
        len(migrated),
        len(deleted),
    )
    for entity_id, old, new in migrated:
        _LOGGER.info("  %s: %s -> %s", entity_id, old, new)
    for entity_id, old in deleted:
        _LOGGER.info("  %s removed (was %s)", entity_id, old)

    summary_lines = [f"- {eid}: `{old}` -> `{new}`" for eid, old, new in migrated]
    summary_lines += [f"- ~~{eid}~~ (was `{old}`) - removed" for eid, old in deleted]

    async_create_issue(
        hass,
        DOMAIN,
        "v1_migration",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="v1_migration",
        translation_placeholders={
            "migrated_count": str(len(migrated)),
            "deleted_count": str(len(deleted)),
            "summary": "\n".join(summary_lines),
        },
    )
