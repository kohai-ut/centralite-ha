# Centralite Lighting for Home Assistant

A modern Home Assistant custom integration for **Centralite Elegance** and **Centralite JetStream** lighting systems. Communicates with the bridge over RS-232.

> [!WARNING]
> **Status: v2.0.0 alpha.** This is the first release of a greenfield rewrite of the legacy
> v1 integrations ([centralite_elegance](https://github.com/kohai-ut/centralite_elegance),
> [centralite_jetstream](https://github.com/kohai-ut/centralite_jetstream), both archived at
> `v1.0.1`). Expect rough edges, but this version is already better.

## What this is

CentraLite Systems made commercial lighting controllers that talk to switches and dimmers over a proprietary mesh. CentraLite went out of business; this integration keeps the existing hardware usable by anyone running Home Assistant.

## Supported hardware

| System | Bridge | Notes |
|---|---|---|
| Centralite Elegance | RS-232 bridge (single-system, multi-system planned) | Up to 192 loads, 384 switches, 256 scenes |
| Centralite JetStream | RS-232 bridge | Up to ~199 loads, ~199 switches × 3 buttons, 100 scenes; `.jts` config import |

A 19200-baud USB-to-serial adapter on the HA host (Raspberry Pi, NUC, VM) plugs into the bridge's RS-232 port. `/dev/serial/by-id/...` paths are recommended for stability across reboots.

## Which integration should I use? (vs. the built-in LiteJet integration)

Home Assistant ships a built-in [**LiteJet**](https://www.home-assistant.io/integrations/litejet/) integration (via [`pylitejet`](https://github.com/joncar/pylitejet)). LiteJet is a sibling CentraLite product, and because these systems share a family of RS-232 protocols, the built-in integration also advertises Elegance and JetStream support but it is not full support. Here's how to choose:

| Your system | Use |
|---|---|
| **LiteJet** | The [built-in LiteJet integration](https://www.home-assistant.io/integrations/litejet/) — it's purpose-built for LiteJet and well maintained. This integration does **not** target LiteJet. |
| **JetStream** | **This integration.** JetStream has no bulk-state command (`^G`/`^H`), which the LiteJet library requires to connect, so the built-in integration cannot talk to a JetStream bridge. This integration speaks JetStream's native `DEV`/`ACT`/`SCN` protocol. |
| **Elegance** | **This integration** is recommended, especially for larger installs. The built-in integration handles Elegance only through the shared LiteJet command set: it reads just the first 48 loads (this integration handles the full 192-load address space), mis-parses Elegance's physical-switch/keypad events, and exposes every load as a dimmer. This integration reads each load's dimmable/on-off type from your `.elg`. For a small, loads-only Elegance setup the built-in one may suffice. |

In short: **LiteJet → built-in; Elegance / JetStream → here.** If you're not sure which CentraLite system you have, check the controller's model or the programming software it shipped with.

## Features

- **Native HA config flow** — UI setup, no YAML editing required
- **Async serial** via the HA-blessed [`serialx`](https://github.com/home-assistant-libs/serialx) library
- **Push-primary updates** with an optional safety-net `^G` poll for loads not programmed for spontaneous output
- **One switch per scene** — no more `-ON`/`-OFF` entity pairs (v1 limitation removed)
- **Config import** — bulk-import friendly names, scenes, and the device list from your Centralite `.elg` (Elegance) or `.jts` (JetStream) export
- **JetStream device discovery** — no config file? Optionally scan the bridge at setup (`^N`) to pull in device names directly. One-time, never on boot
- **Load-type aware** — loads marked non-dimmable in your config are exposed as on/off lights, not fake dimmers with a slider that does nothing
- **Skips unused load slots (Elegance)** — a `.elg` lists all 192 load slots, most of them empty defaults. Only loads that are named or used in a scene/keypad become entities; referenced-but-unnamed loads are created disabled (enable them anytime in HA), and unused slots are skipped entirely. (A `.jts` lists only real devices, so nothing to skip there.)
- **Device triggers for keypad buttons** — physical button presses (JetStream tap/press/release; Elegance press/release) fire HA device triggers, so automations can run on a wall-switch press. The raw `centralite_event` bus event is also available for any button.
- **Sync Clock button (Elegance)** — a button entity that sets the bridge's real-time clock to HA's current time, so the panel's time-of-day schedules stay aligned. Automate the press (e.g. nightly) to correct clock drift. (JetStream has no clock command, so the button only appears on Elegance.) There's also an options toggle, **Sync bridge clock on connect**, that does this automatically after each connection (startup + reconnect) — race-free, no automation needed.
- **DeviceInfo + has_entity_name** — single device card with all entities grouped underneath, names auto-compose
- **HACS-compatible** — install as a custom repository (default-listing planned once stable)
- **One-time migration from v1** — preserves areas, aliases, icons, and dashboard placements

## Installation (HACS custom repository)

Until v2 ships to the HACS default repository:

1. In HA, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/kohai-ut/centralite-ha` with category **Integration**.
3. Click **Download** on the Centralite Lighting entry.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for **Centralite**.

### Staying up to date

HACS manages updates: when a new version is published, it shows an **Update** badge on the Centralite entry — open it, click **Update**, and **restart Home Assistant**. Your configuration and entities are preserved across updates.

While v2 is in **alpha**, releases are published as GitHub *pre-releases*, which HACS hides by default. Turn them on once so you actually get the update notifications: in HACS open the **Centralite Lighting** entry → ⋮ → **Redownload**, enable **Show beta versions**, and pick the newest. After that, each new alpha shows up as a normal update. (Once v2 reaches a stable release, the beta toggle is no longer needed.)

## Configuration

The config flow has two steps:

### Step 1 — Bridge connection

- **System type**: Elegance / JetStream
- **Serial port**: e.g. `/dev/serial/by-id/usb-Prolific...` or `/dev/ttyUSB0`
- **Baud rate**: defaults to 19200

### Step 2 — Names and devices

You have two options for telling HA which loads/scenes/switches you have and what they're named:

- **Paste your `.elg` (Elegance) or `.jts` (JetStream) config file** — the integration parses your friendly names and the list of configured loads and scenes automatically. Export `.elg` from the Centralite Elegance Programming Software; export `.jts` from JetStream Designer. (For JetStream, only devices set to report third-party output are imported, since the others can't be observed over RS-232. Physical keypad buttons aren't imported as entities yet.)
- **Or enter comma-separated IDs** for loads, scenes, switches if you don't have an export file. Friendly names can be set per-entity in HA's UI afterwards.

Both paths can be combined. (Using the config file approach is the most tested.)

After setup, you can edit any entity's display name via HA's standard entity-rename UI on its device card.

### Options

Once configured, **⋮ → Configure** on the integration:

- **Safety-net poll interval** — Elegance only: how often to do a full `^G` query as a fallback for loads not configured for push notifications (default 300s; `0` disables). JetStream has no bulk-state command and is push-only, so this setting has no effect there.

> [!NOTE]
> The bridge's "send CR after responses" Customer Option (Elegance bit #6) must be enabled in the Elegance Programming Software — the integration relies on CR-framed responses and cannot toggle this setting over RS-232.

## Migration from v1

Upgrading from the v1 integrations (`centralite_elegance` / `centralite_jetstream`, archived at `v1.0.1`)? v2 **adopts your existing entities in place.** When a v2 entry first loads, it detects v1 entities (by their `elegance.L001` / `jetstream.JSL001` unique_ids) and automatically:

1. **Renames lights, switches, and buttons** to the v2 format, **preserving their `entity_id` and all customizations** (area, icon, alias, friendly-name override, dashboard placement). They keep working untouched.
2. **Deletes the old `scene.*` entities (both `*ON` and `*OFF`).** In v2 a scene is a **`switch.` entity** (one stateful scene-switch, no ON/OFF pair) — a different domain from v1's `scene.*`, so the old entity can't be carried over; v2 recreates it fresh.
3. Surfaces a **Repairs issue** with the full migration log.

### Upgrade steps

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

4. **Install v2 via HACS** as a custom repository (see [Installation](#installation-hacs-custom-repository) above) — select **v2.0.0a3 or later** — then **restart**.

5. **Settings → Devices & Services → Add Integration → Centralite.** Pick the system type and serial port, and optionally paste your `.elg`/`.jts` for friendly names. On load, v2 renames the orphaned unique_ids to its scheme and adopts them. **Add it once per bridge** if you run both an Elegance and a JetStream.

### After migrating

- **Scenes** moved to the `switch.` domain with new `entity_id`s. Update any automation/script/dashboard that called `scene.turn_on(scene.<name>)` to use `switch.turn_on` / `switch.turn_off` on the new entity. A scene's area/icon customization doesn't carry across the domain change. The Repairs log lists every scene by its old name.
- **Automation YAML** referencing renamed IDs isn't rewritten automatically — use the Repairs issue as your cross-reference.
- The old v1 `scene.*` entities, and any now-empty v1 config entries, can be deleted.
- Tip: You can see any 'unavailable' orphaned devices using Developer Tools in HA.

### Rollback

**Settings → System → Backups → your backup → Restore** (full) reverts config, entity registry, and HACS in one shot. This is the **only** clean revert — migration's `unique_id` renames make an in-place v1 downgrade impossible, so don't attempt one.

For the full walkthrough — including a post-migration verification checklist and a no-risk way to rehearse the whole thing on a single production box — see **[docs/UPGRADE_TESTING.md](docs/UPGRADE_TESTING.md)**.

## Known limitations

- **Elegance scene state is "commanded only".** The Elegance bridge has no scene-state push event. When you activate a scene from HA, the scene-switch reflects the commanded state. Scenes activated externally (physical button, timed event) won't update the HA state. JetStream does not have this limitation (it pushes `SCN` events).
- **Per-load push events require Customer Options bit set.** In the Elegance Programming Software, each load must be configured for "third party output" individually, OR DIP Switch 5 must be ON (which pushes all loads, one per second). If neither is set, the integration relies on the safety-net poll for updates.
- **`.elg` format is REV 1.1.** The parser fails gracefully with a clear error if the format isn't recognized; fall back to manual ID entry.
- **Device triggers fire on HA-originated button commands too.** A device trigger reacts to the bridge's button-activity report, which JetStream also emits when HA itself taps a button (via a button switch entity), not only on a physical wall press. The protocol doesn't distinguish origin, so if you both expose a button as a switch and trigger on it, the trigger fires on HA-initiated taps as well.
- **Elegance switch state requires the per-switch "send action" flag.** Named keypad buttons in the `.elg` are imported as switch entities. The keypad `letter+number` → global switch-index mapping (`idx = (letter-'A')*24 + number`) is **confirmed** against a real install. The remaining caveat is *state*: a switch only reports on/off if the bridge is actually programmed to emit press/release events for it (the per-switch "send action" flag, set in the Programming Software). Press-via-HA (`^I`) works regardless.
- **Single-system Elegance only (multi-system / Elegance XL not yet supported).** A single Elegance or Elegance XL master panel — up to 192 loads, 384 switches, 256 scenes — is fully supported. Multi-system installs (2–4 panels chained, addressing up to 768 loads) are **not** yet: the load cap is fixed at 192 and the `^G`/`^H` bulk-state decoders assume a single board. The protocol path is understood — multi-system uses the per-system `^gs`/`^hs` status queries plus the global 001–768 command index — so adding it is a contained, opt-in change, **but it can't be verified without a 2–4 panel system to test against.** If you have one and want to help, please open an issue.

## Troubleshooting

- **"Failed to connect to Centralite bridge"** — check the serial port path with `dmesg | grep tty` after plugging in the USB adapter. Confirm the bridge LED activity. Confirm "send CR after responses" (Elegance Customer Options bit #6) is enabled.
- **Lights show wrong initial state** — the bridge may not have the "spontaneous output" flag set per load. Enable it in the Elegance Programming Software, OR rely on the safety-net poll.
- **Entities missing after upgrade from v1** — check Settings → Repairs for the migration log. If entities are still missing, file a bug with your `core.entity_registry` JSON (with secrets redacted).

### Watching the logs (debug logging)

For hardware testing — verifying a physical keypad's index, watching push events, or diagnosing a connection — turn on debug logging:

- **From the UI (easiest):** Settings → Devices & Services → Centralite → the three-dot menu → **Enable debug logging**. Reproduce the behavior (press the switch, toggle the load), then **Disable debug logging** to download a log file scoped to this integration.
- **From `configuration.yaml`** (persists across restarts):
  ```yaml
  logger:
    default: info
    logs:
      custom_components.centralite: debug
  ```

What you'll see at debug level:

- `rx: 'P044'` / `rx: 'ACT044...'` — **every raw line the bridge emits**, including spontaneous events from physical button presses and load changes. This is the ground truth for what the hardware is actually sending.
- `send:` / `sendrecv:` / `recv:` — commands HA sends and their responses.
- `switch event: idx=44 button=1 action=tap` / `load event:` / `scene event:` — the **decoded** interpretation of each push event. Compare the `idx` here against the physical device you operated to confirm the mapping (handy for the experimental Elegance switch import).

## Development

Protocol reference docs are in [`docs/protocols/`](docs/protocols/) — manufacturer PDFs for both Elegance and JetStream, preserved here since CentraLite is no longer in business.

The pure protocol/parser/migration tests run without Home Assistant installed.
Run them from the repo root with the repo on the import path:

```
PYTHONPATH=. python tests/test_protocol_common.py
PYTHONPATH=. python tests/test_protocol_elegance.py
PYTHONPATH=. python tests/test_protocol_jetstream.py
PYTHONPATH=. python tests/test_parsers_elg.py
PYTHONPATH=. python tests/test_parsers_jts.py
PYTHONPATH=. python tests/test_migrate.py
```

The integration tests (coordinator, config flow, entities, migration) need the
Home Assistant test harness:

```
pip install pytest-homeassistant-custom-component serialx
pytest
```

`pyproject.toml` sets `pythonpath = ["."]`, so `pytest` from the repo root finds
the package without `PYTHONPATH`. The standalone scripts above need it because
they import `custom_components...` directly.

## License

MIT — see [LICENSE](LICENSE).

## Legacy v1

- [kohai-ut/centralite_elegance @ v1.0.1](https://github.com/kohai-ut/centralite_elegance/releases/tag/v1.0.1) — Elegance integration
- [kohai-ut/centralite_jetstream @ v1.0.1](https://github.com/kohai-ut/centralite_jetstream/releases/tag/v1.0.1) — JetStream integration
