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
whose integration is gone (orphans). The trick is to free the serial port and
remove the v1 code *without* losing those entities:

> **Remove the v1 integration _code_, but do NOT delete any v1 _config entry_.**
> If your v1 was set up through the UI (it has a config entry), deleting that
> entry purges its entities and there's nothing left to migrate. Removing only
> the code leaves the entities orphaned in the registry — which is what
> migration consumes. (A **YAML-configured** v1 has no config entry at all,
> just entities in the registry; the same orphan-and-migrate flow applies, and
> there's nothing in Devices & Services to leave alone.)

> **Order matters — remove v1 _before_ installing v2.** v1 Elegance used the
> `centralite` domain, so its code folder is `custom_components/centralite` —
> **the exact folder v2 installs into.** If you install v2 first and then delete
> `custom_components/centralite`, you delete v2. So: remove v1, *then* install
> v2. (If your v1 Elegance folder is instead named `centralite_elegance`, there
> is no collision and order is flexible — but removing-first works either way.)

1. **Back up and remove the v1 code.** From your config directory (`/config` in
   the Terminal & SSH add-on). Adjust the folder names to match your install —
   a YAML-configured v1 Elegance is `custom_components/centralite`; a v1
   installed from the archived repos is `custom_components/centralite_elegance`
   / `centralite_jetstream`:
   ```bash
   cd /config
   tar czf centralite-v1-$(date +%F).tar.gz           custom_components/centralite
   tar czf centralite-jetstream-v1-$(date +%F).tar.gz custom_components/centralite-jetstream
   tar tzf centralite-v1-$(date +%F).tar.gz | head     # verify before deleting
   rm -rf custom_components/centralite custom_components/centralite-jetstream
   ```
   (Installed v1 via HACS instead? Uninstall the downloads in HACS. File Editor
   or Samba work in place of the shell.) **Leave any v1 config entries in
   Devices & Services untouched.**
2. **Remove the v1 YAML configuration** (v2 is **UI-only and has no YAML
   configuration**):
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
3. **Restart Home Assistant.** With the v1 code and YAML gone, its entities now
   sit orphaned in the registry (any v1 config entry shows "integration not
   found") — which is what migration consumes.
4. **Install v2 via HACS** as a custom repository:
   1. HACS → the ⋮ menu (top-right) → **Custom repositories**.
   2. **Repository:** `https://github.com/kohai-ut/centralite-ha`
   3. **Type:** `Integration` (it installs to `custom_components/`).
   4. **Add**, close the dialog, then open the new *Centralite* entry in HACS →
      **Download**. **Use `v2.0.0a3` or later** — earlier alphas have migration
      bugs, and `a2` can't even load on current HA cores. It lands in the
      now-clean `custom_components/centralite`.
   5. **Restart Home Assistant** so the new integration is registered.
5. **Settings → Devices & Services → Add Integration → Centralite.** Choose the
   system type and the serial port; optionally paste your `.elg`/`.jts` for
   friendly names. On load, v2 renames the orphaned `elegance.*` /
   `jetstream.*` unique_ids to the v2 scheme and adopts them. The port is free
   because v1 isn't loaded. **v2 is configured entirely here — no YAML.** If you
   run more than one bridge (e.g. an Elegance *and* a JetStream), add the
   integration once per bridge, as separate config entries pointing at each
   port.

   > A **broken/placeholder integration icon** here is cosmetic — a brand-icon
   > quirk, not a setup failure. It doesn't affect the config flow or the
   > entities. (If the config flow itself errors with *"Config flow could not be
   > loaded: 500"*, that's different — check the log for a `serialx` dependency
   > conflict and ensure you're on a build with `serialx>=1.7.3` in the
   > manifest.)
6. **Verify the migration *adopted*, didn't *duplicate*.** This is the heart of
   the test — repeat it per bridge (Elegance and JetStream each get their own
   entry and their own migration):
   - **Settings → Repairs** → open the migration issue. It lists every renamed
     load/switch/button and every removed scene; the renamed count should be in
     the ballpark of your v1 entity count.
   - **Lights, switches, and buttons keep their `entity_id`** (`light.l001`,
     `switch.sw044`, `switch.jssw01401`, …) plus their area/icon/alias — they're
     the *same* entities, adopted in place. On the device page they should be
     **available**, not a fresh duplicate set with brand-new ids.
   - **Orphan sanity check:** Developer Tools → States, look for old Centralite
     entities stuck **`unavailable`**. On a current build there should be none
     (except scenes, below). A full shadow set of unavailable `light.*`/`switch.*`
     means the migration didn't adopt — confirm you're on **`v2.0.0a3`+** (older
     alphas orphaned JetStream entities and mis-mapped Elegance switch indices).
   - **Scenes change identity (expected).** A v1 scene is a `scene.*` entity; a
     v2 scene is a **`switch.`** entity. The migration *deletes* the old
     `scene.*` rows and v2 creates new scene-switches with new ids. So HA's
     "Scenes" panel is empty (correct), scene control lives under `switch.*`, and
     **automations using `scene.turn_on(scene.x)` must move to `switch.turn_on`**
     on the new entity. A scene's area/icon customization does not carry across
     the domain change.
   - **Elegance keypad switches** map to the global index
     `(letter-'A')*24 + number` (input tab A–F/A–P × 24 buttons). If a switch
     entity doesn't match the physical button you expect, you're on a pre-a3
     build.
   - **Hardware (what the test suite can't check):** toggle a few real loads and
     confirm the right fixtures respond; activate a scene-switch; press a
     physical keypad and confirm the entity updates (requires the per-load/
     per-device "send third-party output" flag set in the programming software)
     and that a device trigger fires. Keep **debug logging** on and watch the
     `rx:` / `switch event:` / `load event:` lines (README → "Watching the
     logs") — that's how you confirm which index a physical button reports.
   - Optionally enable **Sync bridge clock on connect** (Elegance → Configure)
     and confirm the panel clock is set, or press the **Sync Clock** button.
7. Delete any now-empty v1 config entries (their entities have moved to v2). A
   YAML-configured v1 has none, so skip this.

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

## Re-migrating after a failed earlier attempt

If you migrated on an older alpha and hit orphaned/duplicate entities (e.g.
unavailable `light.*`/`switch.*` shadowing a working set, or Elegance switches at
the wrong index): **restore the `pre-centralite-v2` backup**, update HACS to
**`v2.0.0a3`+**, and re-run Phase 1 from the top. The migration is idempotent and
runs fresh against the restored v1 registry, so the second pass adopts cleanly.
