# Upgrading from v1 — a safe test runbook

This is a step-by-step procedure for upgrading an existing v1 install
(`centralite_elegance` / `centralite_jetstream`, archived at `v1.0.1`) to
this v2 integration **on a single production Home Assistant instance**, with a
full backup as the rollback. It also shows how to exercise the clean-install
("fresh import") path in the same window.

If you're a brand-new user with no v1 install, you don't need any of this —
just add the integration and import your `.elg`/`.jts` (see the main README).

## The one constraint that drives everything

**The serial port is exclusive.** Only one process can hold the bridge's
serial device (`/dev/ttyUSB0`, or better, a stable
`/dev/serial/by-id/usb-Prolific...` path) at a time. You therefore **cannot**
run two Home Assistant instances — or a v1 and a v2 config entry — against the
same bridge simultaneously. Testing is sequential and windowed, not parallel.
Also make sure nothing else (Node-RED, a serial console, an `.elg` export from
the Centralite software) is touching the port during the window.

## What's already covered by the test suite (don't re-test on hardware)

The automated suite covers, against real-world config files: `.elg`/`.jts`
parsing, phantom-load filtering, dimmable/on-off typing, and the v1→v2
unique_id migration classifier (idempotent). Hardware testing only needs to
confirm two things the suite can't:

1. The integration actually talks to **your** bridge (toggles loads, receives
   push events, runs scenes).
2. Migration preserves **your specific** automations, dashboards, and entity
   customizations.

## Pre-flight

1. **Settings → System → Backups → Create backup** (full). Name it
   `pre-centralite-v2`. **Download a copy to your PC** as off-box insurance.
2. Note a baseline to compare against afterward: a few Centralite `entity_id`s
   that have an area / icon / alias set, and one or two automations that
   reference Centralite entities.

## Phase 1 — Migration (the real upgrade path)

The migration adopts v1 entities that are **still in the entity registry** but
whose integration is gone (orphans). The trick is to free the serial port
without losing those entities:

> **Remove the v1 integration _code_, but do NOT delete the v1 _config entry_.**
> Deleting the config entry in the UI purges its entities — and then there is
> nothing left to migrate (you'd get a fresh install, losing history). Removing
> only the code leaves the entry failing to load and its entities orphaned in
> the registry, which is exactly what migration consumes.

1. In **HACS**, add this v2 repo as a custom repository and install it. Don't
   restart yet.
2. **Remove the v1 integration code.** How depends on how v1 was installed:
   - **Manual install** (the v1 integrations were copied into
     `custom_components/` by hand) — delete the `custom_components/centralite_elegance`
     and `custom_components/centralite_jetstream` folders via the Samba share,
     SSH/Terminal, or the File Editor add-on.
   - **HACS install** — uninstall the `centralite_elegance` /
     `centralite_jetstream` downloads in HACS.

   Either way, **leave the v1 config entries in Devices & Services untouched.**

   **If your v1 was YAML-configured**, also remove its `configuration.yaml`
   entries now — v2 is **UI-only and has no YAML configuration**:
   - Delete the `centralite:` and/or `centralite-jetstream:` blocks (the ones
     with a `port:`). This matters most for `centralite:` — v2 reuses the
     `centralite` domain but is config-entry-only, so a leftover `centralite:`
     key throws *"The centralite integration does not support YAML
     configuration"* at startup.
   - In your `logger:` block, drop any v1 lines
     (`custom_components.centralite-jetstream.*`) and the redundant per-platform
     lines; a single `custom_components.centralite: debug` covers all of v2
     (children like `…protocol._base` and `…coordinator` inherit it).
   - Check your `!include` files for stray v1 platform entries:
     `grep -rn "platform: centralite" /config/*.yaml` — remove any you find.

   Restart Home Assistant. The v1 entries now show "integration not found" and
   their entities orphan in the registry — which is what migration consumes.
3. **Settings → Devices & Services → Add Integration → Centralite.** Choose the
   system type and the serial port; optionally paste your `.elg`/`.jts` for
   friendly names. On load, v2 renames the orphaned `elegance.*` /
   `jetstream.*` unique_ids to the v2 scheme and adopts them. The port is free
   because v1 isn't loaded. **v2 is configured entirely here — no YAML.** If you
   run more than one bridge (e.g. an Elegance *and* a JetStream), add the
   integration once per bridge, as separate config entries pointing at each
   port.
4. **Verify:**
   - **Settings → Repairs** → the migration issue lists every rename and the
     removed `scene*OFF` entries. The count should match your entity count.
   - **`entity_id`s are preserved** (migration changes the `unique_id`, not the
     `entity_id`), so automations/dashboards keep resolving. The *only* real
     break is anything that referenced a removed scene `*OFF` entity — fix
     those.
   - Spot-check that your noted area / icon / alias survived.
   - **Hardware:** toggle a few real loads from HA; press a physical keypad and
     confirm the entity updates (push) and a device trigger fires; activate a
     scene. Then enable the **Sync bridge clock on connect** option (Configure),
     reload, and confirm the bridge clock was set (or press the **Sync Clock**
     button).
5. Delete the now-empty v1 config entries (their entities have moved to v2).

## Phase 2 — Fresh-import path (optional; do it last)

Because the restore below reverts everything, you can rehearse the clean-install
path at no cost right before rolling back: remove the v2 entry, add a new v2
entry pasting your `.elg`/`.jts` (no migration this time), and confirm it builds
the right entities/scenes/types and toggles a light. Mostly belt-and-suspenders
— the parsers are already unit-tested against real files.

## Rollback

**Settings → System → Backups → `pre-centralite-v2` → Restore** (full). This
reboots back to your known-good v1 state and reverts the config, entity
registry, and HACS in one shot.

> This full restore is the **only** clean revert. Migration's `unique_id`
> renames make an in-place downgrade to v1 impossible (v1 would no longer
> recognize its own entities), so don't attempt one — restore the backup
> instead.
