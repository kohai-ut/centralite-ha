# JetStream Physical Devices Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after owner approval. This document authorizes no implementation, commit, release, or live HA changes.

**Goal:** Represent each imported/addressed JetStream unit as an independent Home Assistant device, linked through the existing bridge, while preserving entity identities and providing a controlled migration for existing installations.

**Architecture:** Retain one config entry and coordinator per bridge. Register the bridge explicitly, then create independent devices from the union of load IDs and button-device IDs. Keep existing light/switch domains, unique IDs, commands, protocol addressing, and legacy bridge trigger events. Existing users opt into reassociation after an impact preview; new entries use the new organization by default.

**Tech Stack:** Python, HA device/entity registries, config/options flows, existing serialx protocol, pytest-homeassistant-custom-component, Ruff.

**Status:** Revised to incorporate the Opus 5.5 review in `2026-11-04_163150-jetstream-devices-opus-review.md` (H1–H4, M1–M6, L1–L4). Baseline: main at e306481. This revision has not been re-reviewed by Opus. Planning only; implementation tests have not run. Version-floor and targeting changes remain release-approval gates.

**Update 2026-11-15:** added "add a device after setup" (§6A, Task 4A) at the owner's request. This addition has had no design review. main has since moved to v2.0.2 (a0a0854): background per-load level refresh, duplicate Device ID reporting at import, and a README restructure; line references in §3 predate those changes and must be rechecked.

**Tracking issues:** #40 this plan; #42 add a device after setup (§6A); #41 button switch stays on (out of scope here, §1); #43 status reported under a different device ID (open question that may affect §4, see §6A).

**Superseded in part (2026-11-15):** `2026-11-15_213000-v3-release-design.md` sets the release sequence and adds the keypad-button design. Three things in this document change under it: the single-bridge layout is deprecated in v3.0 and converted in v4.0 instead of kept forever (§4, §7); button switches are deprecated, so new installs and added devices get event and button entities instead (§1 out-of-scope list, §6A); and the subentry question in §6A is answered there (stored lists for v3.0). Everything else here stands.

---

## 1. Scope and explicit non-goals

In scope: JetStream physical-device grouping, imported naming, optional area organization, migration, adding and removing a single device after setup (§6A), compatibility tests, user documentation.

Out of scope: switch-to-momentary-button conversion; fixes to the current latched tap state; Elegance device restructuring; automatic room-name inference; changing RS-232 traffic; automatic periodic scans; general reimport; mass entity-ID regeneration; a per-device setup wizard. Preserve current switch turn_on/turn_off behavior. A narrowly scoped removal hook for stale empty unit devices IS in scope for downgrade recovery.

The source extension is `.jts` (JetStream Designer XML), not `.jst`. Preserve the existing input field identifier `elg_text` unless a separately tested flow change is necessary.

## 2. Verified sources and rules

Read these before implementation, and again when selecting the exact supported Core versions. Current contributor docs can be newer than the project's advertised minimum.

1. Naming announcement: https://www.home-assistant.io/blog/2026/11/03/naming-things-in-home-assistant/
   - Existing entity IDs do not change merely because naming changes. Friendly names remain for compatibility for now. Display names may change; this is not permission to rewrite user names.
2. User naming and customization: https://www.home-assistant.io/docs/configuration/customizing-entities/#recommendations-on-entity-naming-and-related-elements
   - Separate device name, entity function, area, and floor. Existing IDs remain unless explicitly recreated. Do not override HA's entity-ID format.
3. Entity naming: https://contributors.home-assistant.io/core/entities/naming/
   - Keep has_entity_name=True. Main entity name=None; short translated functional names for other entities. Device/import-supplied names need not be translated. Never look up an entity by a generated entity ID.
4. Device registry: https://contributors.home-assistant.io/core/integrations/device-registry/
   - Independent physical products behind a hub use connectivity (`via_device_id` in current docs), not parent/child relationships. Child devices represent parts of one physical product. Register the hub first.
5. Entity registry: https://contributors.home-assistant.io/core/entities/registry/ and https://contributors.home-assistant.io/core/integrations/entity-registry/
   - Preserve domain+platform+unique_id and the existing registry row. Core processes the supplied DeviceInfo and re-links existing rows BEFORE skipping disabled entities. This integration constructs entities for every configured ID, so that platform path also handles its disabled rows. Tests must exercise this real path, not just async_added_to_hass.
6. Areas: https://www.home-assistant.io/docs/organizing/areas/
   - Entities can override a device area, BUT an entity without its own name (main entity) always uses its device's area. This is critical for migration and remote-load placement.
7. Target resolution: https://www.home-assistant.io/docs/automation/targets/
   - Targeting a parent includes actual child devices. A connectivity link is not such a relationship. Moving entities changes device-target scope, device-label inheritance, and templates inspecting membership.
8. Config flow: https://contributors.home-assistant.io/core/integrations/config-flow/
   - Keep dialogs minimal and translated. The current `import` step name is reserved for YAML migration in current guidance; if changing touched setup screens, rename the user-facing file step to `devices` with corresponding tests/translations. Do not turn this into a broad schema-library migration.
9. Pinned release source checked: https://raw.githubusercontent.com/home-assistant/core/2026.11.0/homeassistant/helpers/device_registry.py
   - Confirms DeviceInfo.via_device_id and compatibility support for legacy via_device; the latter is marked for removal in 2027.8.

Proposed version contract: raise `hacs.json` from HA >=2026.5.0 to **>=2026.11.0**, subject to owner release approval. Use one naming/area/disable model and no old-version adapter. Opus inspected tagged 2026.5, 2026.8 and 2026.11 sources: 2026.5 lacks via_device_id and single-config-entry registry APIs; 2026.11 adds relevant compat-name/area/disable behavior. Although some APIs exist in 2026.8, do not claim this complete design supports it without a separate proposal and tests. Pin a CI matrix for 2026.11.0 plus the latest supported release, resolving compatible Python and test-harness versions for each. Current Python 3.13/unpinned CI is not proof of minimum-version support.

Tagged-source evidence from the review (recheck during implementation): entity_platform.py 2026.11 L1143–1206 performs registry linking before the disabled skip; entity_registry.py L2060–2075 re-derives DEVICE-disabled status on device moves; L2186–2192 clears compat_name on a device move; L716–731 exposes async_preserve_compat_name_as_alias. The review contains full source links and version comparisons. These are inspection findings, not executed migration-test results.

## 3. Existing implementation and constraints

- `custom_components/centralite/parsers/jts.py`: JtsConfig contains loads, scenes, dimmable and buttons. Only active, SendThirdParty devices are returned. `_collect_buttons` keys protocol buttons 1..3 by the PARENT DeviceID, not the inner Button.DeviceID. This is hardware-confirmed and regression-tested.
- `custom_components/centralite/config_flow.py:167-294`: flattens parsed load IDs/button pairs into data and name dictionaries into options. Manual button IDs default to button 1; optional ^N scan only supplies load IDs/names. VERSION=1. No topology choice, location assignment, or recurring reimport UI exists.
- `custom_components/centralite/entity.py`: every entity has has_entity_name=True and identical DeviceInfo identifiers `(DOMAIN, entry_id)`.
- `light.py`: unique ID `{entry_id}_load_{idx:03d}`; imported load name is currently the entity name. Preserve brightness mode and nondimmable options.
- `switch.py`: JetStream unique ID `{entry_id}_button_{device:03d}_{button:02d}`; existing names repeat the imported device label. Scene switches stay on the bridge.
- `__init__.py`: adopts legacy v1 entities before coordinator initialization, then forwards platforms. Bridge creation currently depends on entities. No async_migrate_entry exists.
- `coordinator.py:185-217`: raw centralite_event and device triggers identify the bridge. Registering the bridge before coordinator initialization also removes reliance on a later entity to establish that identity.
- `device_trigger.py`: enumerates all known load/button IDs from a bridge device's config entry. It currently assumes any device supplied belongs to the bridge, so it must distinguish bridge vs physical unit.
- `migrate.py` and `tests/test_migrate_registry.py`: existing v1 adoption is separate from this topology migration and must keep working in sequence.
- `.github/workflows/validate.yml`: Ruff and pytest plus Hassfest/HACS. `translations/en.json` supplies current strings; inspect repository translation conventions before adding entity keys, and add `strings.json` only as required by custom-integration validation rules.

Stored configuration is sufficient to reconstruct grouping for existing users; neither the original XML nor a hardware scan is required. It does NOT reliably encode an HA area/floor or physical product model. A bridge, slave/remote, or keypad-only entry must not be assigned invented capabilities. Existing parser behavior creates a light for every accepted Device; fixing that classification is a separate issue requiring actual export metadata and tests.

## 4. Proposed identities and ownership

- Keep bridge identifiers exactly `{(DOMAIN, entry.entry_id)}` and retain its HA device ID, user name, labels, area, and disabled state.
- New physical device identifiers: `{(DOMAIN, f"{entry.entry_id}_jetstream_device_{idx:03d}")}`. These are scoped to a bridge, stable across reload/name/area changes, and based on the same address identity already used for entities. Address reassignment remains an existing hardware-identity limitation; do not claim serial-number identity.
- Build device addresses from `CONF_LOAD_IDS` union first elements of `CONF_BUTTON_IDS`; exclude scene IDs. A manual keypad-only device must not gain a synthetic light.
- Register bridge explicitly, then unit devices, before forwarding entities. Keep bridge even when it has no scene entities. Set accurate generic manufacturer/model information only; never infer a model number from dimmable flags.
- Only JetStream lights and JetStream keypad switches change device association. Scenes and every Elegance entity keep their existing grouping. No duplicate compatibility light/switch entities.
- Preserve all existing entity unique IDs, entity IDs, domains, platform, state semantics and registry row IDs. Topology is not a reason to alter `migrate.py` legacy unique-ID rules.
- Single source of truth: `entry.data["jetstream_topology"]` is `"bridge"` or `"per_device"`; absent means `"bridge"`. New JetStream entries explicitly write `"per_device"`. Entity DeviceInfo MUST obey this flag; otherwise first reload would migrate existing installations without consent. No separate naming-policy flag or manual device_id reassociation engine.
- No config-entry VERSION bump or async_migrate_entry is needed for an additive key with a safe missing-key default. If implementation proves a schema marker necessary, use MINOR_VERSION only and test downgrade loading; do not introduce a major bump for this feature.

## 5. Naming and areas

### One naming model in per-device mode

Use `OPT_LOAD_NAMES[str(idx)]` as the physical device's default name, preserving imported text and abbreviations. Scan names use the same stored source. Missing names use a translated `Device {number}` fallback. Do not parse synthetic OPT_SWITCH_NAMES strings to recover a device name. Keep source dictionaries for compatibility; they are not native HA user overrides.

For BOTH new and migrated per-device entries, the main light normally uses name=None, and keypad switches use a translated `Button {number}` with placeholders. Imported names become device context rather than repeated functional names. Scenes and legacy bridge-mode names remain unchanged. Do not discard genuinely distinct custom function names from the registry.

Integration defaults (`original_name`, supplied by `_attr_name`/translation_key) may change without changing entity IDs. User overrides in registry `name` remain untouched. Preserve entity aliases, icons, labels, categories, options, enabled/hidden state and exposure settings. List user-overridden names in the migration preview because their composed display names may still repeat context; give native cleanup instructions. No persisted legacy-naming policy and no automatic trimming of user text.

Before moving an entity whose `compat_name` is set, call the public HA 2026.11 `er.async_preserve_compat_name_as_alias(hass, entity_id)` so the move does not silently discard the old voice name. Preserve existing aliases and exposure configuration; test actual alias results.

### Existing-user area policy

Show effective-area before/after values in preview. Default a newly created unit's device area to the light's explicit area, otherwise the bridge area, otherwise unassigned. A button-only unit defaults to the bridge area. Apply only on initial creation; preserve native user changes on retries.

For a main light, clear its own area override ONLY when it matches the proposed unit area and the confirmed preview authorizes that change. Its effective area is unchanged. Do not create an own-area-without-own-name Repairs issue. If the unit area differs from the old bridge area, preserve the old effective area for buttons without an override by assigning their former bridge area explicitly; existing button overrides remain intact. Verify the unassigned-area edge case in Core: if preserving it cannot be represented without inheritance, require a preview choice rather than silently assigning it.

For remote loads or conflicting placement, the migration preview must offer a per-affected-unit `Keep separate light area` choice: select the device area with a native AreaSelector, retain the light area override, and use explicit translated functional name `Light` rather than main-entity naming. Store only the small set of remote-load exceptions needed to supply that functional name, not a second global naming policy. Revalidate selected area IDs at confirmation; deleted areas require refreshed choices. Never merge conflicting effective locations without consent. User registry names and main-entity overrides take precedence; detect incompatible overrides and require native resolution instead of silently clearing them.

### Room, floor, and label prerequisites

NONE are required to import, migrate, connect, or control devices. Areas/floors may be created before or after setup using HA; devices/entities belong to areas, areas belong to floors. No integration-specific floor/level editor, automatic room/floor creation, or duplicate room registry. Do not use suggested_area: Core can create an area from its name.

After new import, use native device editing or bulk Move to area. Later area assignment changes contextual display names, not existing IDs. Users MAY explicitly Recreate entity IDs to adopt their preferred format, but must then review references; this is optional, never an integration migration step. A per-device pre-registration naming/area wizard is deferred, so this release does not promise area-based IDs at first creation.

## 6. New-user flow and required UI

1. Retain bridge connection and `.jts` paste/manual IDs/optional scan, including filters, button addressing and dimmable behavior.
2. Add only a translated organization summary: counts, fallback names, independent-device explanation, locations optional, and native area-assignment instructions. No mandatory per-device wizard or floor/label fields.
3. On confirmation, create the entry with topology `per_device`; register bridge then units before entities. Cancellation must leave no devices, entries, or areas created by the flow.
4. Do not stage/consume setup metadata through entry rewrites in async_setup_entry; the existing update listener can cause reload loops. Do not overwrite native names/areas on reload.

Ongoing naming/organization uses native HA settings. Only existing-user migration needs targeted area-conflict choices (§5/§7). Future reimport and a pre-registration area wizard are separate features.

## 6A. Adding a device after setup (#42)

**Problem.** The device list is fixed at setup. Adding one dimmer today means removing and re-adding the integration; unique IDs contain the entry ID, so every entity gets a new identity and its customizations are lost. A user (pgyrogear) built a local version on 2.0.1; his code has not been received or reviewed and is input, not a base.

**Requirement.** From Configure, add one or more JetStream devices to an existing entry without changing any existing entity, in BOTH topology modes. Elegance is unchanged.

### User flow

1. Configure shows a menu: existing settings, `Organize JetStream devices` (§7, legacy entries only), `Add device`, `Remove device`.
2. Add device asks for: Device ID (the JetStream Designer Device ID, i.e. the RS-232 address), device type, and an optional name.
3. For types with buttons, choose which of buttons 1–3 to expose.
4. Offer `Add another` before saving, so several devices are saved with one reload.
5. Show a summary, save, reload once.

### Device type is an input shorthand, not stored state

The four types map onto data the integration already stores. Do not add a stored "type" field; it would be a second source of truth beside the ID lists.

| Type chosen | CONF_LOAD_IDS | OPT_NONDIMMABLE_LOADS | CONF_BUTTON_IDS |
|---|---|---|---|
| Dimmer | add | no | none |
| Switch (on/off load) | add | add | none |
| Dimmer keypad | add | no | selected buttons |
| Keypad (no load) | no | no | selected buttons |

A keypad-only device gets no light entity (§4 already requires this of the inventory). A name goes to OPT_LOAD_NAMES for the address even when there is no load, because that dictionary is the device-name source in §5; confirm no code treats a name entry as evidence of a load.

### Rules

- Validate before saving: ID within the JetStream range (existing `_check_range`), buttons within 1–3, at least one button for keypad types.
- An ID that is already configured is not an error. Offer to add missing parts to it (e.g. buttons on an existing dimmer). Never remove or retype existing parts from the Add flow.
- Write with one `async_update_entry` and let the existing update listener reload once. No entry rewrites inside `async_setup_entry` (§6 item 4).
- New entities get the same unique-ID formats as imported ones, so a device added here is indistinguishable from an imported one, and a later topology migration (§7) treats both alike.
- In `per_device` mode the new unit device comes from the same inventory helper (Task 2); in `bridge` mode the entities appear under the bridge. No topology-specific storage.
- The flow sends no load-control commands. Optional and off by default: one `^N` name lookup for the entered ID with a bounded timeout, inside the flow only, never at startup. A silent device is not an error.
- The README must state that the device must already be programmed in Designer with Update Third Party and Send T-Party set; the flow cannot check this.

### Remove device

Add needs a counterpart, or a typo in an ID is permanent. Remove device lists configured addresses; removing one deletes its IDs from the stored lists, removes its entities from the registry, and (per_device mode) removes its unit device. Require confirmation naming the entities that will be deleted. This is separate from the stale-empty-device hook in §7, which never deletes working entities.

### Design decision to settle in Task 1: options-flow step vs config subentries

Home Assistant has config subentries: one entry holds several things that share its connection, each with its own add/change flow. That is the native shape of "add a device to a hub".

Proposed: use an options-flow menu step writing to the existing ID lists, NOT subentries, for this release. Reasons: imported devices live in the ID lists, so subentries would create two storage models or force a migration of every existing device; the inventory in §4 has one source; and it works on the current minimum version. Revisit subentries only if the owner wants per-device reconfigure screens.

Only the glossary definition of subentries was checked for this note. Read the subentry flow docs and tagged source before finalizing, and record the decision with the owner.

### Interaction with #43

#43 reports two lights whose status arrives under a different ID than the command ID. If that has a general cause, a device may need two addresses (command, status), which changes what "Device ID" means in this flow and in §4's identifiers. Do not finalize the Add device form or the unit-device identifier until #43 is understood. If it stays specific to one installation, the form is unchanged.

## 7. Existing-user migration: controlled, resumable, no reimport

Existing entries have no topology key and remain bridge-grouped until the user confirms `Organize JetStream devices` in Configure/options. New entries explicitly select per-device mode. No schema/version migration is required. DeviceInfo is derived from the flag on every load; never emit per-unit DeviceInfo for an unconsented legacy entry.

### Preview and consent

Add an options menu preserving existing polling/clock controls. JetStream alone gains organization preview/confirmation; Elegance is unchanged. Preview must state:
- Number of unit devices and entities to move; no reimport/ID change.
- Bridge-targeted actions will no longer include moved entities merely via connectivity.
- Bridge area/labels/disabled state previously inherited by entities may need preservation or review.
- Bridge-bound keypad device triggers remain supported.
- Entity-based automations remain; device-membership templates, custom cards, voice names, device conditions/actions can change.
- Backup recommended. Provide affected entity list and native editing directions; do not claim exhaustive automatic discovery of references in all YAML/templates/external systems.
- List known references using the public `automation.automations_with_device(hass, bridge_device_id)` and `script.scripts_with_device(hass, bridge_device_id)` helpers after verifying their pinned-version signatures. This is a useful aid, NOT exhaustive coverage of YAML packages/templates/dashboards/external clients, and zero matches is not proof of safety or a reason to block.
- Show user-set names, effective-area proposals and any remote-load exceptions; preserve compat-name aliases before moving.

User choices: proceed, postpone (no registry mutation), or optionally review/edit device areas. Existing bridge-wide action targets must be changed by the user to explicit entity lists, suitable groups, or selected physical devices before proceeding. Do not rewrite automations, create fake child devices, intercept global light services, or add duplicate entities to disguise this behavior change. If zero-disruption bridge targeting is required by the owner, this rollout is blocked until a different supported design is approved.

### Migration algorithm

1. Require the JetStream entry to be loaded and its bridge enabled. Refuse migration while the bridge is disabled, with a translated explanation: Core re-derives DEVICE-disabled entities on device moves and could enable them. Never directly assign entity disabled_by. Preserve USER/INTEGRATION disables and hidden state through the normal platform path.
2. Preview without mutation; scope inventory and registry lookups by config entry AND system. Existing v1 adoption remains first in normal setup; do not newly adopt unrelated orphan entities. Capture only bounded before/after information needed for preview and verification, not a registry-copy/checkpoint database.
3. After consent, get-or-create the intended unit devices with stable identifiers. On INITIAL creation apply approved device areas and union bridge labels into unit-device labels, not into every entity. Never reapply carry-over to an already existing device on retry; user edits/removals must stick. Future bridge-label changes do not propagate. Keep bridge metadata for scenes and legacy triggers.
4. Preserve compat-name aliases and apply confirmed area adjustments/remote-load exceptions (§5). Use public APIs; recheck input areas and preconditions at confirmation. Do not clear a user registry name or rewrite automations.
5. Set the single topology flag using async_update_entry after preparation; let the existing listener reload ONCE. No entry rewrites during setup and no manual entity device_id updates. On load, entity DeviceInfo causes Core to re-link all constructed configured entities, including disabled ones. No deletion/recreation, duplicate entities, separate reassociation engine, or second naming flag.
6. Verify actual associations after the platform setups finish; verify counts/identities and retained areas/aliases/state. On failure surface an actionable Repairs issue and retry the deterministic flag-driven setup. Never claim get-or-create makes all preparation atomic: test interruption before and after flag update and customization between retries. Retain only a minimal completion/report marker if needed to avoid repeated notifications, not a per-row migration journal.
7. Produce a one-time translated summary with moved counts, targeting changes, unresolved/conflicting areas and naming cleanup directions. Deliberately unassigned areas are not errors. Keep bridge ID and legacy event payload; do not duplicate events.

No casual reverse toggle. Full rollback is a pre-upgrade backup restore. Source inspection suggests older code will emit bridge DeviceInfo and re-link rows back on load, leaving empty unit devices; it will NOT restore all area/label/name metadata. Test downgrade → upgrade in staging before documenting that limited behavior as supported. Add async_remove_config_entry_device to permit removal of stale EMPTY unit devices only; protect the bridge and active configured units and never cascade-delete working entities.

## 8. Trigger compatibility

Keep `centralite_event` payload exactly bridge-based (device_id, type, subtype) and emit once per incoming ACT event. Existing trigger definitions must still enumerate, attach, and fire on the bridge, including HA-originated taps as today.

For new physical device pages, classify the exact stable identifiers and enumerate only that address's buttons 1–3. In async_attach_trigger resolve the bridge ID and match the SAME bridge event's device_id/type/subtype, not the unit device ID; otherwise saved unit triggers never fire. Use device.config_entry_id with config-entry/system validation; unrelated or malformed device identifiers return no triggers. Store the bridge ID returned by explicit registration on the coordinator instead of its deprecated identifier lookup. Keep safe behavior if the device disappears/recreates; test reload/re-registration. Verify generic built-in device actions/conditions too. One ACT emits one bus event: an automation with both bridge and unit triggers can run once PER matching trigger, not magically once overall.

## 9. Implementation tasks (TDD; no commits unless requested)

For each code task: write focused failing tests, run them to establish the failure, make the smallest change, rerun focused tests, then run the full relevant suite. No expected pass counts are asserted before execution.

### Task 1: Pin the compatibility contract
- Confirm the proposed HA 2026.11.0 minimum with the owner before release changes; inspect tagged APIs and compatible Python/test-harness requirements, recording exact versions.
- Files: `hacs.json`, `.github/workflows/validate.yml`; pin the minimum/latest supported matrix. No legacy-version adapter.
- Add real platform/target tests proving automatic relinking before disabled skipping, connectivity vs child targeting, main-entity area Repairs, compat aliases and DEVICE-disable re-derivation.
- Exit: minimum version, dependencies and matrix are explicit; source inspection is backed by executed tests.

### Task 2: Build deterministic device inventory helpers
- Create `custom_components/centralite/jetstream_devices.py` for address union, stable identifiers, source-name lookup and identifier classification (no I/O in entity properties).
- Create `tests/test_jetstream_devices.py`.
- Cover load+buttons share one device, multiple bridges with identical addresses, button-only, scene-only, blank names, duplicate names, manual IDs, scan IDs, ignored/inactive entries, and malformed data handling without reinterpreting addresses.
- Cover a configured load that is disabled by the integration: create its unit device even with zero enabled entities. Do not invent light IDs from disabled-load metadata alone if absent from the configured load list; treat inconsistent stored data explicitly.
- Parser changes only if inventory cannot be reconstructed without them; preserve inner-vs-parent DeviceID regression.

### Task 3: Register bridge and associate JetStream entities
- Modify `__init__.py`, `entity.py`, `light.py`, `switch.py`; leave Elegance paths unchanged.
- Create `tests/test_device_topology.py`; extend existing `tests/test_setup.py` for lifecycle/setup regressions.
- Cover explicit empty bridge, pre-entity registration, correct hub linkage, scenes on bridge, switch domains/commands unchanged, dimming unchanged, unload/reload, connect failure/retry, disabled entries and no duplicates.
- Test the topology flag: missing/bridge retains old DeviceInfo and default names; per_device changes associations through Core. No consent means no unit reassociation. Store bridge ID on coordinator; no deprecated lookups.

### Task 4: Implement structured names and summary-only new setup
- Modify `config_flow.py`, `const.py`, `translations/en.json`; add/modify `strings.json` only after checking current custom-integration convention and Hassfest.
- Add translated functional/fallback names and placeholders; imported user/source text remains verbatim.
- Extend `tests/test_config_flow.py`, `tests/test_light.py`, `tests/test_switch.py` and topology tests.
- Cover zero areas/floors, summary counts, cancellation without registry writes, custom entity-ID formats, new per_device default, translated main/functional names, no repeated integration-default names, preserved registry user names and native device edits after reload. Legacy bridge-mode names remain unchanged. No forced entity-ID suggestions, suggested_area, staged setup metadata, or setup-time entry rewrites.

### Task 4A: Add and remove a device after setup (§6A, #42)
- Precondition: the subentry-vs-options decision and the #43 question are settled (§6A).
- Modify `config_flow.py` (options flow becomes a menu; existing polling/clock step preserved unchanged), `const.py`, `translations/en.json`. Put the type-to-storage mapping and merge logic in a pure helper (in `jetstream_devices.py` from Task 2) so it is testable without a flow.
- Extend `tests/test_config_flow.py`, `tests/test_jetstream_devices.py`, `tests/test_setup.py`.
- Cover each of the four types; batch add with one reload; out-of-range ID; no button chosen for a keypad; already-configured ID gains buttons without losing anything; existing entities keep entity_id, unique_id and registry row after an add; keypad-only creates no light; both topology modes; Elegance entries do not offer the menu items; cancel writes nothing.
- Remove: confirmation lists entities; entities and unit device are removed; other devices untouched; removing the last part of an address removes its name entry.
- Optional `^N` lookup: answered, silent, and disconnected cases, with the flow never hanging.
- Review pgyrogear's implementation, if received, against these rules before writing code; adopt ideas, not the patch wholesale.

### Task 5: Add opt-in migration preview and safety checks
- Modify `config_flow.py`, `const.py`; create `custom_components/centralite/topology_migrate.py` for preview/preparation/verification helpers, NOT a registry-reassociation engine. Keep legacy v1 adoption separate. No async_migrate_entry or major VERSION bump required.
- Create `tests/test_topology_migrate.py`; extend `tests/test_config_flow.py`.
- Cover missing flag remains bridge until consent, new entries default per_device, scene-only config, postpone/cancel without mutation, JetStream-only menu and existing options retained. Refuse unloaded entry/disabled bridge. List known automation/script references without claiming completeness. Cover user-name warnings, effective-area comparisons, remote-load choices, deleted-area confirmation, unassigned-area conflict, and backup/restore guidance.

### Task 6: Prepare devices, flip topology and let Core relink
- Implement §7 using public APIs: create devices with approved areas and one-time bridge-label carry-over; preserve compat_name aliases; apply approved area adjustments and remote-load exceptions; update flag; reload once; verify; report. No per-row checkpoint store/manual device_id moves/global naming flag.
- Use real platform setup with prepopulated registries, including disabled/hidden/customized entities. Assert unchanged entity_id, unique_id, registry row ID, user name, icon, options, category, visibility, exposure and entity labels; aliases retain old values plus required compat aliases. Compare effective areas and target membership against the confirmed preview, not raw metadata equality where approved inheritance changes are intentional.
- Cover interruption before/after flag flip, retry after user customization, no reapplication of removed device labels, duplicate names, deleted areas, USER/INTEGRATION disables, hidden entities, bridge-disabled refusal and no accidental enable, all-disabled unit, no own-area-without-own-name Repairs, explicit remote-load name/area, reload twice, two bridges, mixed Elegance, v1 adoption then topology, no migration scan or load-control commands, no unrelated registry writes.
- Add scoped async_remove_config_entry_device in `__init__.py` and tests for empty stale unit removal vs protected bridge/active units. Exercise old-version DeviceInfo relink and downgrade → upgrade with preserved identities; full rollback still requires backup.
- Do not report completion before platform association verification succeeds; exercise failure/retry and one-time summary behavior without introducing setup-triggered reload loops.

### Task 7: Preserve and expose triggers
- Modify `device_trigger.py`, coordinator.py only as needed for registered bridge lookup; extend `tests/test_device_trigger.py`, `tests/test_coordinator.py`.
- Assert old bridge trigger payload/config unchanged; unit triggers filtered; one raw event per ACT, each matching automation fires once; taps initiated from HA still behave as documented; no triggers from unrelated devices.
- Save a unit-based trigger and prove attach resolves bridge device_id; test cross-entry rejection and an automation with two matching triggers (once per trigger is expected, not deduplication).

### Task 8: End-to-end upgrade and documentation
- Modify README.md, CHANGELOG.md, docs/UPGRADE_TESTING.md; add a focused user guide `docs/JETSTREAM_DEVICES.md` when implementing.
- Correct README:49 single-device grouping and :81 keypad-import claims. Explain physical vs bridge vs actual child devices, no room/floor prerequisites, native bulk assignment, optional ID recreation/reference risks, main/remote-load names and areas, unchanged user overrides, compat aliases, device targeting, retained switches, disabled-bridge refusal, consent, minimum HA 2026.11, backup vs limited code downgrade, and stale-device cleanup. Do not advertise a deferred area-first wizard.
- Run full HA fixture setup/import/migration with a sanitized representative `.jts` fixture; no production export or registry/credential files committed. Hardware testing is opt-in in a separate maintenance window, never part of unattended tests.

## 10. Validation commands and release gates

Run in a compatible isolated Python environment; use `uv` with a verified Python version instead of the host's mismatched bare pip. CI currently installs pytest-homeassistant-custom-component, serialx and ruff. Pin/resolve compatible versions per Task 1, not an invented package set.

Focused proposed commands after implementing tests:
- `python -m pytest tests/test_jetstream_devices.py tests/test_device_topology.py tests/test_topology_migrate.py tests/test_config_flow.py tests/test_device_trigger.py tests/test_migrate_registry.py`
- `python -m pytest`
- `ruff check custom_components/ tests/`
- Repository Hassfest and HACS validation jobs.

Run on the proposed minimum HA 2026.11.0 and latest supported release using pinned compatible harnesses. Include real entity-platform/registry/service-target tests; fake protocol tests alone do not prove migration. Staging UI checks: new import without areas, native bulk area assignment, customized existing install with consent, cancellation, known-reference preview, bridge/physical-device targeting, labels/floors/areas, remote-load exception, no area Repairs, compat aliases, old/new triggers, restart/reconnect, disabled-bridge refusal, all-disabled unit, downgrade → upgrade, empty-device removal, backup restore. No production HA changes without separate authorization.

Acceptance gates:
- No Elegance behavior or entity domain/command changes.
- No reimport or serial scan required for existing users.
- New/old entity identity assertions pass, no duplicates including disabled rows.
- No areas/floors required; summary-only setup and native bulk assignment documented. Targeted migration area-conflict choices validated; no auto-created areas or mandatory organization wizard.
- Old bridge trigger automations remain working without duplicate events.
- No silent device-target, effective area, label or enable-state changes: migration consent, explicit policy and tests required.
- Documentation does not describe this as universally zero-disruption. Missing fixtures/source-version confirmation are blockers, not evidence of success.
- Release notes separate device organization from a future momentary-button proposal.
- A device added after setup is indistinguishable from an imported one; adding never changes an existing entity; removal requires confirmation (§6A).

## 11. Review disposition and remaining release gates

All Opus findings are incorporated as planning revisions, not claims of successful implementation:
- H1: distinguish integration defaults from untouched user names; remove legacy naming policy.
- H2: one topology flag drives DeviceInfo; Core relinks rows, including disabled rows; scoped stale-device removal and downgrade tests.
- H3: device-area default plus approved light-override cleanup, explicit remote-load exceptions and no area/name Repairs.
- H4: refuse migration from a disabled bridge; never manipulate entity disabled_by to force the move.
- M1: propose HA >=2026.11.0 and pinned minimum/latest CI; no version adapter.
- M2/M3: one-time labels on unit devices; known automation/script reference list and honest targeting warning.
- M4/M5/M6: no suggested_area or setup entry rewrites; preserve compat-name aliases; unit triggers attach to unchanged bridge events.
- L1/L2/L3/L4: defer setup wizard, no unnecessary version bump, inventory/disabled/manual/scan edge tests, README corrections.

Before implementation/release, confirm these owner-facing policies: minimum HA 2026.11.0; existing-user opt-in with acknowledged bridge-target scope change; summary-only new setup; conflict-specific separate light-area choice instead of forced merging; add-device via an options-flow step rather than config subentries; whether Remove device ships with Add device. Approval to edit this plan does not authorize code, release, or live migration.

Evidence gates remain: sanitized representative `.jts` fixture; actual pinned-version tests and staging migrate/reload/downgrade/upgrade/restore; safe recovery from interruption during preparation; unassigned-area and user main-entity override handling. The Opus review was source/document inspection, not test execution, and the revised plan has not received a second approval. Do not mark these gates passed from plan edits alone.
