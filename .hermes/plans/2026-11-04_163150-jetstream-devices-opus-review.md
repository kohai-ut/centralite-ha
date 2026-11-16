# Opus review: JetStream physical-device organization plan

**Plan reviewed:** `.hermes/plans/2026-11-04_163150-jetstream-devices.md` (unchanged by this review)
**Repo baseline:** `main` @ `e306481` (untracked `.hermes/`, `IDEA.md` left untouched)
**Reviewer:** Hermes kanban worker `opus-design`. The runtime reports model **Claude Opus 5.5** (`claude-opus-5-5[1m]`), and the task's `model_override` is `opus`. The runtime-reported identity is Opus 5.5, so no alias mismatch is disclosed.
**Scope:** This review covered the plan only. It used read-only inspection of the repository, the official docs and tagged Core sources. No code was implemented or tested, no hardware was touched and no live HA was contacted. Nothing was committed.
**Verdict:** **CHANGES REQUESTED. Not approved as written.** The core architecture is sound: one entry per bridge, independent unit devices linked to the bridge through `via_device_id`, unique IDs preserved, opt-in migration for existing users and bridge-based legacy triggers. Five findings must be fixed before implementation: H1–H4 and M1. After those revisions the plan is approvable.

---

## Sources actually consulted

Repository (read in full unless noted):
- `custom_components/centralite/__init__.py`, `entity.py`, `light.py`, `switch.py`, `device_trigger.py`, `const.py`, `config_flow.py`, `migrate.py`, `button.py`, `scene.py`
- `coordinator.py` (lines 1–150 skimmed, 150–260 read) and `parsers/jts.py` (key sections)
- `translations/en.json`, `hacs.json`, `manifest.json`, `.github/workflows/validate.yml`
- README grep; test file and function inventory (`tests/test_setup.py`, `tests/test_device_trigger.py`, `tests/test_migrate_registry.py`)

Official docs (fetched 2026-11-04):
- https://www.home-assistant.io/blog/2026/11/03/naming-things-in-home-assistant/
- https://www.home-assistant.io/docs/configuration/customizing-entities/ (incl. recommendations, main entity, hidden/disabled, parent/child)
- https://www.home-assistant.io/docs/organizing/areas/
- https://www.home-assistant.io/docs/automation/targets/
- https://contributors.home-assistant.io/core/entities/naming/
- https://contributors.home-assistant.io/core/entities/registry/
- https://contributors.home-assistant.io/core/integrations/device-registry/
- https://contributors.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/

Tagged Core source (raw GitHub, fetched by tag):
- `2026.5.0` and `2026.11.0`: `helpers/device_registry.py`, `helpers/entity_registry.py`, `helpers/entity_platform.py`, `helpers/entity.py`, `helpers/target.py`, `config_entries.py`
- `2026.8.0`: `helpers/device_registry.py`, used to locate when `DeviceInfo.via_device_id` and `async_get_device_by_identifier` first appear

Line numbers below refer to those tagged files (`dr` = `device_registry.py`, `er` = `entity_registry.py`, `ep` = `entity_platform.py`).

---

## Findings, ranked by severity

### H1 (High). The "retain legacy names" policy causes doubled names after migration

**Evidence**
- `light.py:74-75` sets `_attr_name` to the imported load name (`OPT_LOAD_NAMES`) with `has_entity_name=True` (`entity.py:19`). Today a light displays as "Centralite JetStream" + "Kitchen Island".
- `parsers/jts.py:152` generates every button label as `f"{device_name} Button {n}"`. `switch.py:95-97` uses it as the entity name.
- Plan §5 uses the imported name as the **device** name. If the entity names are retained, a migrated light shows as **"Kitchen Island Kitchen Island"** and a button as **"Kitchen Island Kitchen Island Button 1"**. Per the naming docs, the friendly name is device name + entity name, and 2026.11 surfaces this everywhere.
- Plan §5 ("Existing installation") and Q2 treat these strings as names that must be preserved. They are not user overrides. They are the integration's `original_name`. User overrides live in the registry `name` field and are never touched by changing `_attr_name` or `translation_key`. The customizing-entities doc confirms that changing an entity name does not change its entity ID.

**Required revision**
- Treat integration-provided `original_name` as safe to change. Treat registry `name` (user override) as untouchable.
- In per-device mode:
  - The primary light uses `name=None` (main entity), subject to H3.
  - JetStream buttons use `translation_key="button"` with a `{number}` placeholder.
  - `OPT_SWITCH_NAMES` and `OPT_LOAD_NAMES` become device-name sources only.
- Apply this to new **and** migrated entries. Drop the "legacy naming policy" persisted flag; one topology flag is enough (see H2).
- The preview must list entities whose registry `name` is user-set, because those *will* show duplicated context. For example, a user-renamed light "Kitchen island light" becomes "Kitchen Island Kitchen island light". Offer cleanup directions; never rewrite these names.

### H2 (High). Core already moves entities when `DeviceInfo` changes, so the migration design duplicates it and risks races

**Evidence**
- `ep` 2026.11 L1143–1206 and `ep` 2026.5 L923–979 work the same way. `entity_registry.async_get_or_create(..., device_id=device.id)` runs **before** the `registry_entry.disabled` skip. `er` 2026.11 L1647ff calls `_async_update_entity(..., device_id=device_id)` for an existing row.
- So every entity the platform constructs, **including disabled ones**, has its existing registry row re-pointed to the device in its `DeviceInfo`, with the same row, `entity_id` and customizations. Plan §2.5 says "Disabled entities may never be added, so migration cannot depend solely on entity addition". That is inaccurate for this integration. All configured IDs are constructed (`light.py:39-47`, `switch.py:47-58`), and the disabled check comes after the device link.
- Consequence 1: the opt-in gate must control **what `DeviceInfo` the entities emit**. If `entity.py` emits per-unit `DeviceInfo` before the user consents, every existing install migrates silently on the first reload.
- Consequence 2: a separate "reassociate registry rows" pass in `topology_migrate.py` (plan §7 step 7 and Task 6) fights the platform. Whatever the entities emit on the next load wins.

**Required revision**
- Make one persisted flag in `entry.data` the single source of truth: `jetstream_topology: "bridge" | "per_device"`. A missing key means `"bridge"`. New entries write `"per_device"`.
- `entity.py` chooses `DeviceInfo` from the flag.
- Migration becomes:
  1. Pre-flight checks (H4).
  2. Pre-create unit devices and apply area/label carry-over (M2/M3) with public device-registry APIs.
  3. Set the flag through `async_update_entry`. The existing update listener (`__init__.py:68,84-86`) reloads the entry.
  4. On reload the platform re-links all rows.
  5. Verify and report.
- This is naturally idempotent. Every step is a get-or-create or a no-op on re-run, which removes most of the checkpoint and resume machinery and the "registry writes aren't transactional" risk.
- **Downgrade:** older code emits bridge `DeviceInfo`, so entities move back to the bridge on load and empty unit devices remain. Document this, and implement `async_remove_config_entry_device` so users can delete stale empty unit devices. That function doesn't exist today.

### H3 (High). Main-entity area rules conflict with the plan's "materialize the inherited area as an entity override" approach

**Evidence**
- Areas doc: "An entity without a name of its own always uses the area of its device."
- `er` 2026.11 L1347–1354 `_has_own_area_without_own_name()`, with L3057/L3076. Core 2026.11 raises a **Repairs issue** for every entity that has an `area_id` but no own name.
- Plan §7 step 4 materializes the bridge area as an entity override and keeps light names explicit. Those two choices together mean either a doubled name (H1) or a repair issue per light (if `name=None`). Plan §5 does not reconcile this.

**Required revision: area policy for migrated units**
- **Set the unit's device area** from: the light entity's explicit `area_id` if present, else the bridge's `area_id`, else none. Use `dr.async_update_device(device_id, area_id=...)` on the newly created unit device, before the flag flip.
- Then **clear the light's own `area_id` only if it equals the new device area**, and leave its name as `None`. Effective area is unchanged and no repair issue appears.
- If the light's explicit area differs from what its button entities need, or the owner wants the "remote load" case, keep an explicit translated functional name ("Light") for that light and keep its area override. Make this an explicit preview choice per affected unit, not a heuristic.
- **Button switches:**
  - With no own area, they previously inherited the bridge area and now inherit the unit device area. That is the same value unless the light had an override.
  - When the light override differs from the bridge area, materialize the bridge area on each button switch that had no own area. They have own names, so no repair issue arises.
- Add tests asserting no `homeassistant` own-area-without-own-name issue exists after migration (2026.11).

### H4 (High). The disabled state is re-derived automatically when `device_id` changes

**Evidence**
- `er` 2026.11 L151–171 `_DERIVED_DISABLED_BY = {CONFIG_ENTRY, DEVICE, None}`.
- `er` 2026.11 L2060–2075: on any `device_id` change, an entity whose `disabled_by` is derived is re-derived from the **new** device.
- An entity disabled only because the bridge device was disabled (`disabled_by=DEVICE`) is therefore **silently re-enabled** when moved to an enabled unit device.
- `disabled_by=USER` and `disabled_by=INTEGRATION` are preserved (not in the derived set). That covers `CONF_DISABLED_LOADS` (`light.py:38,44`) and user disables.
- 2026.5 behaves differently (`er` 2026.5 `_async_update_entity` only re-derives on config-entry change). This is another divergence across the version range.

**Required revision**
- Pre-flight: if the bridge device is disabled, **refuse migration** with a translated explanation ("enable the bridge first, or disable the unit devices you don't want after migrating"). This is simpler and more honest than copying the disable onto N unit devices.
- Never pass `disabled_by` for entities.
- Test: bridge disabled → migration refused. Entity USER-disabled, INTEGRATION-disabled and hidden → still disabled/hidden after migration.

### M1 (Medium, release-blocking). The version floor is not credible at 2026.5; set the minimum to 2026.11 (or at least 2026.8)

**Evidence**
- `dr` 2026.5: `DeviceInfo` has only `via_device: tuple` (L108). There is no `via_device_id` key, no `async_get_device_by_identifier`, no `ChildDeviceInfo`, and no `DeviceEntry.config_entry_id`; it still has `config_entries: set` (L328).
- `dr` 2026.8 adds `DeviceInfo.via_device_id` (L139) and `async_get_device_by_identifier` (L1558).
- `dr` 2026.11 marks `via_device` deprecated for 2027.8 (L143). `DeviceEntry.config_entries` access is reported with `breaks_in_ha_version="2027.10.0"` (L547–559), and `async_get_device` is deprecated (L1788ff).
- `er`: 2026.5 is storage minor 22. 2026.11 is minor 24, which adds `compat_name`, the dynamic full-name computation, own-area-without-name repairs and derived disabled_by on device change. Naming, area and disable behaviour differ across the advertised range, so the migration would have to be designed and tested twice.
- Existing code already uses APIs deprecated in current Core: `coordinator.py:214` `async_get_device(identifiers=...)` and `device_trigger.py:76` `next(iter(device.config_entries))`.
- CI (`validate.yml`) installs unpinned `pytest-homeassistant-custom-component`, so it tests only "latest". Nothing currently proves 2026.5 support.

**Required revision**
- Raise `hacs.json` `homeassistant` to `2026.11.0` (recommended: one naming model, one disable model) or at minimum `2026.8.0`.
- Use `via_device_id`, `async_get_device_by_identifier((DOMAIN, entry_id), entry.entry_id)` and `device.config_entry_id` directly, with no adapter.
- Pin a CI matrix to the declared minimum plus the latest. Drop the "isolated version adapter" option from Task 1; it adds code for a range nobody tests.

### M2 (Medium). Label carry-over: copy to unit devices, not to every entity

**Evidence:** targets doc: "If the label is on a device … the matching entities of that device … are part of the target." Copying bridge labels onto the **unit devices** keeps label-target membership equivalent for moved entities and adds fewer rows. Copying onto each entity (plan §7 step 5) pollutes per-entity settings.

**Revision**
- Union the bridge labels into each new unit device's labels once, at device creation only. Never re-apply on retry, so user removals stick.
- The preview states that future bridge-label edits won't propagate.

### M3 (Medium). Using a bridge device target is a real behavior break, and the preview cannot detect it

**Evidence:** `helpers/target.py` 2026.11 L186–190 expands a device target to its **child devices** only, not `via_device` dependents. A `device_id: <bridge>` action currently hits every light and switch. After migration it hits scenes only. The plan already acknowledges this (§7 Preview and Consent), which is correct.

**Revision**
- Keep the opt-in.
- Add one cheap, honest aid: scan `automation`/`script` entities' `device_id` references using the public `automation.automations_with_device(hass, bridge_device_id)` and `script.scripts_with_device(hass, bridge_device_id)` helpers. List matches in the preview as "known references". Explicitly state that YAML packages, templates, dashboards and external tools aren't covered.
- Don't block on zero matches.

### M4 (Medium). The staged setup-area design causes a reload loop and auto-creates areas

**Evidence**
- `__init__.py:68,84-86` reloads the entry on **any** entry update.
- Plan §6.6 stores temporary setup metadata and then "consumes it once". Consuming it with `async_update_entry` during `async_setup_entry` triggers a second reload mid-setup.
- `DeviceInfo.suggested_area` takes an **area name** and calls `ar.async_get_or_create(name)` (`dr` 2026.11 L2706–2714), which creates areas. That violates plan §5 "no automatic area creation".

**Revision**
- Either cut the per-device setup form from v1 (preferred, see L1), or store `area_id`s keyed by address in `entry.data` and apply them with `async_update_device(area_id=...)` only on devices **created** in this setup (check before/after `async_get_or_create`).
- Never rewrite entry data during setup. Leftover staged data is harmless because it's only applied on creation.
- Never use `suggested_area`.

### M5 (Medium). Voice compatibility is lost when the device changes

**Evidence**
- `er` 2026.11 L2186–2192: `compat_name` is cleared whenever `device_id` changes.
- `er` L716–731: `async_preserve_compat_name_as_alias(hass, entity_id)` is the documented hook for integrations to keep the pre-1.24 name as the primary alias.

**Revision**
- Before the flag flip, call `er.async_preserve_compat_name_as_alias` for each moved entity whose `compat_name` is set (a public 2026.11 API, another reason for M1).
- Test that exposed entities keep their alias.

### M6 (Medium). Device-trigger scoping and the bridge lookup

**Evidence**
- `device_trigger.py:73-99` assumes the device belongs to a bridge and enumerates every address.
- `device_trigger.py:109-121` matches on `device_id` from the trigger config. A trigger saved against a **unit** device will never match, because `coordinator.py:197-204` fires with the bridge's `device_id`.

**Revision**
- Classify the device by identifier:
  - Bridge: `(DOMAIN, entry_id)`. Unchanged enumeration.
  - Unit: `(DOMAIN, f"{entry_id}_jetstream_device_{idx:03d}")`. Enumerate only that address's buttons 1–3.
  - Anything else: no triggers.
- In `async_attach_trigger`, for a unit device resolve the bridge `device_id` and listen with `{device_id: bridge_id, type, subtype}`. One bus event results, with no duplicates.
- Use `device.config_entry_id`.
- Store the bridge device ID returned by explicit registration in `async_setup_entry` on the coordinator, replacing `_bridge_device_id()`'s deprecated lookup (`coordinator.py:206-217`). The plan says this broadly; make it concrete.
- Test: a trigger on a unit device fires once per ACT. The same automation with both bridge and unit triggers fires once per trigger.

### L1 (Low–Medium, complexity). Cut or defer the per-device setup organization form

The only benefit of the form is getting the area into **new** entity IDs at first registration. Natively, users get area-aware names immediately via device areas, plus bulk **Move to area** and **Recreate entity IDs** (customizing-entities doc). Dozens of dialogs, deleted-area validation and staging state are the plan's largest UI and test surface.

**Recommendation:** v1 ships the summary step only, with an "Assign areas later" explanation and a link to Recreate entity IDs. Track the per-device form as a follow-up if users ask for it. Owner gate Q3.

### L2 (Low). No config-entry version bump is needed

A missing `jetstream_topology` key defaults to `"bridge"`, so `async_migrate_entry` isn't required.

If one is added anyway, use a `MINOR_VERSION` bump only. Per `config_entries.py` 2026.11 L1306–1330, older code without `async_migrate_entry` accepts a newer minor on the same major version, so downgrade still loads. A `VERSION` bump would make old code refuse the entry.

### L3 (Low). Inventory edge cases to pin in tests

- **Union of addresses:** `CONF_LOAD_IDS` ∪ first elements of `CONF_BUTTON_IDS` (matches `device_trigger.py:87-88`).
- **Disabled loads:** an address present only in `CONF_DISABLED_LOADS` still gets a unit device. Its light stays INTEGRATION-disabled, so the device appears with 0 enabled entities. Acceptable, but document it.
- **Manual and scan setup:** manual `switch_ids_csv` IDs default to button 1 (`config_flow.py:273`). Scan-only names come from `^N` (`config_flow.py:257-259`).
- **Device name source:** use `OPT_LOAD_NAMES[str(idx)]`, falling back to a translated "Device {number}". Don't parse `OPT_SWITCH_NAMES` labels (they are synthetic, `jts.py:152`).
- **Device metadata:** set `manufacturer=MANUFACTURER` and `model="JetStream device"` (generic), and `via_device_id=bridge.id`.

### L4 (Low). README/docs

Plan Task 8 correctly flags README:81 ("Physical keypad buttons aren't imported as entities yet") as stale. README:49 ("single device card with all entities grouped underneath") must also change for per-device mode.

---

## Assessment by topic requested

| Topic | Assessment |
|---|---|
| Fresh `.jts` import | Sound after H1 (main-entity light, translated buttons) and L1 (summary-only UI). New IDs will be `light.<device>` and `switch.<device>_button_n`, plus area if set before registration. This is correct per the naming docs. |
| Existing-user migration | Opt-in is correct. Re-architect around the DeviceInfo flag (H2), with pre-flight (H4), device area/label carry-over (H3/M2), alias preservation (M5) and known-reference preview (M3). |
| Stable entity/unique IDs | Preserved. The same `(domain, platform, unique_id)` row is re-pointed by the platform (`er` L1647ff), so no unique ID changes. `migrate.py` v1 adoption runs first (`__init__.py:58`) and is unaffected. |
| Device vs child device | `via_device_id` is correct: each JetStream unit is an independent product (device-registry doc, "Do not mix this up with `via_device_id`"). Child devices would wrongly put JetStream units inside bridge targets. Do not use them. |
| Rooms/floors prerequisites | None are required. Agree with plan §5. |
| Optional UI | Defer the per-device form (L1, M4). |
| Main-entity area inheritance | Plan is under-specified and conflicts with 2026.11 repairs. Use H3. |
| Bridge/device targeting | A real break; consent plus the M3 aid. Agree with the no-fake-children and no-duplicate-entities stance. |
| Triggers | Keep the bridge event; add unit-scoped enumeration that attaches to the bridge ID (M6). |
| Labels / disable state | M2 / H4. |
| Idempotency / recovery | Achieved structurally by H2. Rollback = backup restore. Code downgrade auto-relinks to the bridge and leaves empty unit devices, so add `async_remove_config_entry_device`. |
| Tests | Plan coverage lists are thorough. Trim Task 6 to the H2 design. Add: no own-area-without-name repair; DEVICE-disabled pre-flight; compat alias; unit trigger once-only; downgrade relink; two bridges with identical addresses; and pinned-version CI matrix (M1). |
| Complexity | Plan is over-built: two policy flags, a resumable checkpoint store, a per-device setup wizard, a version adapter and a separate reassociation engine. H2 + M1 + L1 + L2 remove most of it. |

## Recommended plan revisions (summary checklist)

1. §2/§4: Replace the "disabled entities may never be added" rationale with the platform-relink fact (H2). Make `entry.data["jetstream_topology"]` the single switch read by `entity.py`.
2. §5: Delete the "legacy naming policy". Change `original_name` in per-device mode for migrated and new entries alike; never touch registry `name` (H1).
3. §7 algorithm: rewrite as pre-flight (bridge enabled, entry loaded), then create unit devices with area/labels, preserve compat aliases, flip flag/reload, verify, then the summary issue (H2–H4, M2, M5).
4. §6: Summary-only setup step in v1; no `suggested_area`; no entry rewrites during setup (L1, M4).
5. §8: Unit-trigger attaches to the bridge ID; scoped classification; stored bridge device ID (M6).
6. Task 1 / `hacs.json`: raise the minimum to 2026.11.0 (or 2026.8.0), drop the adapter, pin the CI matrix (M1).
7. Task 5: drop `async_migrate_entry` unless needed; minor bump only (L2).
8. Add `async_remove_config_entry_device` for empty unit devices (H2 downgrade path).

## Unresolved gates (owner decisions and evidence still required)

1. **Minimum HA version:** 2026.11.0 (recommended) vs 2026.8.0. 2026.5 is not supportable without large adapter code.
2. **Q1 opt-in migration:** agree. Confirm the owner accepts that bridge device targets change after consent.
3. **Q2 naming:** replace with H1 (integration defaults change; user overrides untouched).
4. **Q3 setup UI:** recommend deferring the per-device form.
5. **Remote-load area exception (H3):** whether the preview offers per-unit "keep separate light area" choices or always merges into the device area.
6. **Evidence still missing:** a sanitized representative `.jts` fixture; a pinned-version test run on the minimum and on 2026.11; a staging HA run of migrate → reload → downgrade → upgrade. None of these were run in this review.
