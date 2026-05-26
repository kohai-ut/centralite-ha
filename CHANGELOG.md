# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0a2] - 2026-05-25

This alpha rolls up all the post-`2.0.0a1` work: the protocol/hardware fixes,
`.elg`/`.jts` import improvements, device triggers, auto-reconnect, the clock
features, and debug logging — plus the **critical `serialx` dependency fix**
without which the integration cannot load on current Home Assistant cores.

### Added

- **Debug visibility into the serial link.** With debug logging on, the reader now logs every inbound line (`rx: ...`) — including spontaneous push events that parse cleanly, which previously produced no log output at all — and the coordinator logs the decoded form of each event (`switch event: idx=… button=… action=…`, `load event: …`, `scene event: …`). Together with the existing `send:`/`recv:` command logs, this makes hardware testing observable: you can watch exactly what the bridge emits when you press a physical keypad and confirm the reported index, which is especially useful for verifying the experimental Elegance switch mapping. See the README "Watching the logs" section for how to enable it.
- **Optional automatic clock sync on connect (Elegance).** A new options toggle, *Sync bridge clock on connect*, sets the bridge's real-time clock to Home Assistant's local time after every successful connection — at startup and after an auto-reconnect. Because it runs only once the serial link is up, it sidesteps the boot-time race an `homeassistant.start` automation would face. Off by default; Elegance-only (the option isn't shown for JetStream). For occasional drift correction the **Sync Clock** button (below) on a monthly automation also works.
- **"Sync Clock" button for Elegance bridges.** A config-category button entity writes Home Assistant's current local time to the bridge's real-time clock (`^L`), keeping the panel's time-of-day schedules aligned with HA. Automate the press (e.g. nightly) to correct clock drift without a trip to the panel. Created only for systems with a settable clock — JetStream has no clock command, so no button appears there (new `supports_clock` capability flag).
- **JetStream `.jts` import now creates button switch entities.** Each device's `<buttonList>` is parsed for configured buttons (any tap/press-hold/double-tap with a non-zero action); button IDs 0-2 map to the protocol's buttons 1-3. These become `CentraliteJetStreamButtonSwitch` entities named `<device> Button N`. (Physical presses already drive device triggers; this adds the HA-controllable button switches.)
- **Automatic reconnect after a serial drop.** A transient disconnect (USB hiccup, bridge power-cycle) previously left every entity unavailable until a manual reload. The coordinator now retries the connection on an exponential backoff (5s → capped at 300s), re-primes state, re-arms the safety poll, and restores availability — and stops cleanly on unload. The safety poll is also cancelled while disconnected (no error spam).
- **Device triggers for physical keypad buttons.** Button activity (JetStream `ACT` tap/press/release; Elegance `P`/`R` press/release) now fires a `centralite_event` bus event, and `device_trigger.py` exposes these as Home Assistant device triggers — automations can run on a physical wall-switch press. Triggers enumerate from the configured buttons (JetStream) / switches (Elegance); the raw event is available for any button.
- **Elegance keypad switch import.** Named keypad buttons in the `.elg` are imported as switch entities with their labels, using the global switch-index mapping `idx = (letter-'A')*24 + number` (input tab A-P × 24 buttons). Switch *state* requires the bridge to be programmed to emit press/release events; press-via-HA works regardless.
- **JetStream `^N` device-name discovery scan.** For JetStream setups without a `.jts`, an optional setup-time scan probes the bridge with `^N` across the device range and imports the names of every device that responds. Uses a short per-probe timeout (unconfigured slots are silent and time out), runs once during config (never on boot), and surfaces a clear error if the bridge can't be reached.
- **JetStream `.jts` config import.** Paste a JetStream Designer `.jts` (XML) export in the config flow to bulk-import devices (as lights, with names and dimmer/on-off type) and scenes — the JetStream equivalent of `.elg` import. Only devices set to report third-party output are imported (others can't be observed over RS-232); physical keypad buttons are not imported as entities yet. Format is auto-detected from the pasted text.
- **Phantom-load filtering on `.elg` import.** A `.elg` lists all 192 load slots, most of which are unused defaults. Only loads that are named, or referenced by a scene or an active keypad button, are created; the rest are skipped. Referenced-but-unnamed loads are created disabled-by-default so they can be enabled in the UI without re-importing. Hand-entered load IDs are always created enabled. (One real config dropped from 192 entities to 73.)
- **Load-type-aware light entities.** The `.elg` parser now reads each load's `DIMMER=Y/N` flag; loads flagged non-dimmable are exposed as on/off lights (`ColorMode.ONOFF`) instead of dimmers with a non-functional brightness slider. (In a typical install most loads are on/off relays.) Manual-ID setups, with no `.elg`, still default to dimmable.
- **"Which integration should I use?" README section** comparing this integration with the built-in Home Assistant LiteJet integration, so LiteJet owners are pointed to the built-in one and Elegance/JetStream owners know to use this.

### Fixed

- **Elegance keypad switch-index formula corrected.** The `.elg` `[letter][number]` → global switch index used `(number-1)*16 + (letter-'A') + 1`, which produced wrong indices (the input layout is tabs × 24 buttons, not 16). It's now `(letter-'A')*24 + number`, confirmed against a real install (B20→44, B22→46, D3→75, E4→100, E10→106, E11→107). This also means a v1→v2 migration now lines up: the imported switch indices match the v1 `SW0xx` entities, so they're adopted instead of orphaned.
- **v1 scene migration no longer leaves orphaned `scene.*` entities.** A v1 scene is a `scene.*` entity, but a v2 scene is a `switch.*` entity (different domain). The migration previously *renamed* the old scene rows' unique_ids, which the entity registry (keyed on domain + platform + unique_id) could never let the v2 switch adopt — so every scene became a stranded, unavailable `scene.*` entity. The migration now **deletes** the old scene entities (both `*ON` and `*OFF`); v2 creates the scene-switch fresh. (The bug slipped through because the migration test created the scene orphan in the `switch` domain instead of the real-world `scene` domain.)
- **`serialx` requirement no longer hard-pins `==1.8.0`.** Current Home Assistant cores bundle `serialx==1.7.3`, and a hard `==1.8.0` pin made the dependency unsolvable on those installs — the requirements step failed and the config flow returned a 500 ("Config flow could not be loaded"). Relaxed to `serialx>=1.7.3`; the integration only uses `open_serial_connection`, which is unchanged across those versions.
- **Device triggers now enumerate for every device's buttons.** They previously listed only from the configured button list, which a `.jts` import never populates — so JetStream users got zero triggers and buttons 2/3 were unreachable. Now each known device offers buttons 1-3 (the protocol's range), so any physical press is trigger-able.
- **Mid-scan disconnect during the `^N` scan surfaces as `scan_failed`** instead of escaping the config flow as an unhandled error; the scan's open-failure path also cleans up a partial connection.
- **Bridge device id is looked up fresh per button event** rather than cached, so a deleted-and-recreated device can't leave triggers pointing at a dead id.
- **Config import routes by selected system type**, not by sniffing the pasted text. Fixes a BOM-prefixed `.jts` being misrouted to the Elegance parser and the wrong parser running when the paste didn't match the chosen system.
- **JetStream `^N` discovery scan hardened**: aborts with a clear error if the link drops mid-scan (was silently returning a partial device list), bounds the `connect()` open with a 10s timeout, and scans the documented 001-096 device range (≈30s) instead of 001-199.
- **Elegance keypad import tolerates stray sections**: a keypad button number outside 1-24 is skipped instead of producing an out-of-range switch index that aborted the entire import.
- **`.jts` parser rejects implausibly large input** before handing it to the XML parser (guards against an accidental huge paste / entity expansion).

- **JetStream CRLF line framing.** JetStream terminates every line with `CR`+`LF`, but the reader split on `CR` only, leaving the `LF` to become the first byte of the next line — which then failed length/prefix dispatch, so every spontaneous `DEV`/`ACT`/`SCN` event after the first was mis-parsed on real hardware. The reader now skips `LF`. (Elegance uses `CR` only and is unaffected.)
- **JetStream `^N` device-name parsing.** The reply format (confirmed on hardware) is `NAM` + the 3-digit device number + the name, e.g. `NAM002GAME RM E-1-E GAME CANS`. The parser stripped only `NAM`, so it returned the device number glued to the name (`002GAME RM…`). It now strips the index too and matches the specific device so an unrelated `NAM` can't fulfill the request.

- **Integration now declares its `serialx` dependency.** `manifest.json` had an empty `requirements`, so a HACS install would fail on first connect because Home Assistant never installed `serialx` (it is not a HA core dependency).
- **Options flow no longer crashes on open.** It assigned the now read-only `OptionsFlow.config_entry`, which raises on HA 2024.11+. Reworked to the current pattern where `self.config_entry` is framework-provided.
- **JetStream is correctly treated as push-only.** JetStream has no bulk-state command (`^G`/`^H` do not exist in its protocol); the old code sent them and timed out on every initial query and safety poll, logging a traceback every interval. Added a `supports_bulk_query` capability flag; the coordinator now skips priming and the safety poll for JetStream and relies on spontaneous `DEV`/`ACT`/`SCN` output.
- **`light.turn_on` with low brightness no longer turns the light off.** Brightness 1-2 rounded down to level 0 (= OFF); now floored to level 1.
- **Serial reader death surfaces as unavailable.** When the link drops, entities now go unavailable (and any in-flight command fails fast) instead of showing stale state forever.
- **v1 migration is scoped per system.** An Elegance entry no longer adopts JetStream v1 orphans (and vice versa) in a mixed install.
- **eLite removed from the config flow.** It had no setup path and produced an entry that could never load.
- **Config-flow import validation.** Out-of-range device IDs are rejected with a clear error; pasting unrecognized text (e.g. a `.jts` file, which is not supported yet) now errors instead of silently importing nothing.

### Removed

- **Dead "Append CR" option.** The integration cannot set the Elegance Customer Option bit over RS-232 (CR framing is a hard requirement, set in the Programming Software), so the toggle did nothing.

### Changed

- CI now runs `ruff` and the full `pytest` suite (previously only hassfest + HACS validation).
- Test suite expanded to cover the coordinator, config/options flow, entities, full registry migration, and setup/unload via `pytest-homeassistant-custom-component`, plus regression tests for each fix above.

## [2.0.0a1] - 2026-05-24

First alpha release of the v2 greenfield rewrite. Unifies the v1 Elegance and JetStream integrations into one HACS-compatible package.

### Added

- **Async protocol layer** with separate Elegance and JetStream implementations behind a common `CentraliteProtocol` ABC. Built on [`serialx`](https://github.com/home-assistant-libs/serialx). Single asyncio reader task with shape-based dispatch and request/response correlation via Futures.
- **DataUpdateCoordinator** (push-primary, no `update_interval`) with optional safety-net `^G` poll for loads not programmed for spontaneous output.
- **Config flow** with system type, serial port, and baud rate. Two-step setup with optional `.elg`/`.jts` bulk-import OR comma-separated device IDs.
- **Options flow** for safety-net poll interval and Elegance Customer Option bit #6 (append CR).
- **Entities**: `CentraliteLight`, `CentraliteEleganceButtonSwitch`, `CentraliteJetStreamButtonSwitch`, `CentraliteSceneSwitch`. Shared `DeviceInfo` per entry; `has_entity_name = True` for auto-composing display names.
- **Scene-as-switch** — single stateful switch per Centralite scene. JetStream uses real `SCN` push state; Elegance uses commanded state (documented limitation).
- **`.elg` parser** with tolerant INI-style parsing. Tested against a real 192-load, 22-scene config.
- **One-time v1 -> v2 migration** of entity-registry unique_ids. Renames v1 entries (`elegance.L001`, `jetstream.JSSW04401`, etc.) to the new index-based scheme. Removes obsolete `scene*OFF` entries. Preserves all user customizations. Surfaces a Repairs issue listing the changes so users can update automations.
- **HACS metadata** (`hacs.json`), GitHub Actions for hassfest + HACS validation, MIT license, comprehensive README, issue templates.
- **Corporate PDFs** preserved in `docs/protocols/` — Elegance and JetStream protocol guides, programming manuals.
- **Test suite** with 113 unit tests covering bit-layout decoders, both protocol implementations (against a fake serial transport), .elg parsing against real-world quirks, and migration classifier idempotency. All tests run as standalone scripts without pytest installed (also pytest-compatible).

### Migration from v1

This is a fresh repository. Users of the v1 integrations should:

1. Install v2 via HACS as a custom repository.
2. Add a Centralite integration via the UI.
3. On first load, v2 detects v1 entities and migrates them in place.
4. Check **Settings → Repairs** for the migration log.
5. Update any automations referencing the old entity IDs (HA does not auto-rewrite YAML).

The v1 repositories are archived at `v1.0.1`:
- [centralite_elegance @ v1.0.1](https://github.com/kohai-ut/centralite_elegance/releases/tag/v1.0.1)
- [centralite_jetstream @ v1.0.1](https://github.com/kohai-ut/centralite_jetstream/releases/tag/v1.0.1)
