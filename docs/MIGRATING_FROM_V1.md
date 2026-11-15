# Migrating from the v1 integrations

This page is only for people upgrading from the separate v1 integrations. For a fresh install, follow the [README](../README.md).

Upgrading from the v1 integrations (`centralite_elegance` / `centralite_jetstream`, archived at `v1.0.1`)? v2 **adopts your existing entities in place.** When a v2 entry first loads, it detects v1 entities (by their `elegance.L001` / `jetstream.JSL001` unique_ids) and automatically:

1. **Renames lights, switches, and buttons** to the v2 format, **preserving their `entity_id` and all customizations** (area, icon, alias, friendly-name override, dashboard placement). They keep working untouched.
2. **Deletes the old `scene.*` entities (both `*ON` and `*OFF`).** In v2 a scene is a **`switch.` entity** (one stateful scene-switch, no ON/OFF pair) — a different domain from v1's `scene.*`, so the old entity can't be carried over; v2 recreates it fresh.
3. Surfaces a **Repairs issue** with the full migration log.

## Upgrade steps

Do this in one maintenance window. The bridge's serial port can only be held by one integration at a time, so v1 and v2 can't both run against it.

**First, take a full backup** (Settings → System → Backups → Create backup) and download a copy — it's your only clean rollback (see below).

1. **Remove the v1 integration _code_ — but keep its entities.** This frees the serial port and leaves the v1 entities orphaned in the registry, which is exactly what the migration adopts.
   - **Manual install:** from your config directory (`/config` in the Terminal & SSH add-on), back up then delete the v1 folders:
     ```bash
     cd /config
     tar czf centralite-v1-$(date +%F).tar.gz custom_components/centralite custom_components/centralite-jetstream
     rm -rf custom_components/centralite custom_components/centralite-jetstream
     ```
     (Adjust the names to match your install — a v1 from the archived repos is `centralite_elegance` / `centralite_jetstream`.)

   > **Do NOT delete the v1 _config entry_** in Devices & Services — that purges its entities and leaves nothing to migrate. Remove only the *code*. (A YAML-configured v1 has no config entry anyway.)
   >
   > **Order matters — remove v1 _before_ installing v2.** v1 Elegance uses the `centralite` domain, i.e. the *same* `custom_components/centralite` folder v2 installs into, so installing v2 first and then deleting that folder would delete v2.

2. **Remove the v1 YAML config** (v2 is UI-only — it has no YAML configuration):
   - Delete the `centralite:` and/or `centralite-jetstream:` blocks (the ones with a `port:`) from `configuration.yaml`. The `centralite:` one is critical — leaving it throws *"The centralite integration does not support YAML configuration"* at startup.
   - Drop any v1 `logger:` lines; a single `custom_components.centralite: debug` covers all of v2.
   - `grep -rn "platform: centralite" /config/*.yaml` and remove any stray platform entries.

3. **Restart Home Assistant.** Your v1 entities are now orphaned in the registry (any v1 config entry shows "integration not found").

4. **Install v2 via HACS** as a custom repository (see [Installation](../README.md#installation-hacs-custom-repository) above), then **restart**.

5. **Settings → Devices & Services → Add Integration → Centralite.** Pick the system type and serial port, and optionally paste your `.elg`/`.jts` for friendly names. On load, v2 renames the orphaned unique_ids to its scheme and adopts them. **Add it once per bridge** if you run both an Elegance and a JetStream.

## After migrating

- **Scenes** moved to the `switch.` domain with new `entity_id`s. Update any automation/script/dashboard that called `scene.turn_on(scene.<name>)` to use `switch.turn_on` / `switch.turn_off` on the new entity. A scene's area/icon customization doesn't carry across the domain change. The Repairs log lists every scene by its old name.
- **Automation YAML** referencing renamed IDs isn't rewritten automatically — use the Repairs issue as your cross-reference.
- The old v1 `scene.*` entities, and any now-empty v1 config entries, can be deleted.
- Tip: You can see any 'unavailable' orphaned devices using Developer Tools in HA.

## Rollback

**Settings → System → Backups → your backup → Restore** (full) reverts config, entity registry, and HACS in one shot. This is the **only** clean revert — migration's `unique_id` renames make an in-place v1 downgrade impossible, so don't attempt one.

For the full walkthrough — including a post-migration verification checklist and a no-risk way to rehearse the whole thing on a single production box — see **[UPGRADE_TESTING.md](UPGRADE_TESTING.md)**.
