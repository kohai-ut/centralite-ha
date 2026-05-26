# Centralite Lighting for Home Assistant

A modern Home Assistant custom integration for **Centralite Elegance** and **Centralite JetStream** lighting systems. Communicates with the bridge over RS-232.

> [!WARNING]
> **Status: v2.0.0 alpha.** This is the first release of a greenfield rewrite of the legacy
> v1 integrations ([centralite_elegance](https://github.com/kohai-ut/centralite_elegance),
> [centralite_jetstream](https://github.com/kohai-ut/centralite_jetstream), both archived at
> `v1.0.1`). Expect rough edges. Production users on v1.0.1 should keep running v1 until
> v2 has soaked on a test HA instance.

## What this is

CentraLite Systems made commercial lighting controllers that talk to switches and dimmers over a proprietary mesh. CentraLite went out of business; this integration keeps the existing hardware usable by anyone running Home Assistant.

## Supported hardware

| System | Bridge | Notes |
|---|---|---|
| Centralite Elegance | RS-232 bridge (single-system, multi-system planned) | Up to 192 loads, 384 switches, 256 scenes |
| Centralite JetStream | RS-232 bridge | Up to ~199 loads, ~199 switches × 3 buttons, 100 scenes; `.jts` config import |

A 19200-baud USB-to-serial adapter on the HA host (Raspberry Pi, NUC, VM) plugs into the bridge's RS-232 port. `/dev/serial/by-id/...` paths are recommended for stability across reboots.

## Which integration should I use? (vs. the built-in LiteJet integration)

Home Assistant ships a built-in [**LiteJet**](https://www.home-assistant.io/integrations/litejet/) integration (via [`pylitejet`](https://github.com/joncar/pylitejet)). LiteJet is a sibling CentraLite product, and because these systems share a family of RS-232 protocols, the built-in integration also advertises Elegance and JetStream support. Here's how to choose:

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

Both paths can be combined.

After setup, you can edit any entity's display name via HA's standard entity-rename UI on its device card.

### Options

Once configured, **⋮ → Configure** on the integration:

- **Safety-net poll interval** — Elegance only: how often to do a full `^G` query as a fallback for loads not configured for push notifications (default 300s; `0` disables). JetStream has no bulk-state command and is push-only, so this setting has no effect there.

> [!NOTE]
> The bridge's "send CR after responses" Customer Option (Elegance bit #6) must be enabled in the Elegance Programming Software — the integration relies on CR-framed responses and cannot toggle this setting over RS-232.

## Migration from v1

When v2 first loads and detects v1 entities (by their `elegance.L001`/`jetstream.JSL001`/etc. unique_ids), it automatically:

1. Renames them to the v2 format (`{entry_id}_load_001`, etc.)
2. Associates them with the new config entry
3. Removes obsolete `scene*OFF` entries (the v2 scene-switch absorbs both states)
4. Preserves all customizations (area, icon, alias, friendly name override)
5. Surfaces a **Repairs issue** with the full migration log

**You'll need to update any automation YAML** that referenced the old entity IDs — HA cannot rewrite those automatically. The Repairs issue lists every renamed entity for cross-reference.

For a safe, step-by-step upgrade procedure on a single production instance (full backup → migrate → verify → roll back if needed), see [docs/UPGRADE_TESTING.md](docs/UPGRADE_TESTING.md).

## Known limitations

- **Elegance scene state is "commanded only".** The Elegance bridge has no scene-state push event. When you activate a scene from HA, the scene-switch reflects the commanded state. Scenes activated externally (physical button, timed event) won't update the HA state. JetStream does not have this limitation (it pushes `SCN` events).
- **Per-load push events require Customer Options bit set.** In the Elegance Programming Software, each load must be configured for "third party output" individually, OR DIP Switch 5 must be ON (which pushes all loads, one per second). If neither is set, the integration relies on the safety-net poll for updates.
- **`.elg` format is REV 1.1.** Future Centralite software revisions could change the format. The parser fails gracefully with a clear error if the format isn't recognized; fall back to manual ID entry.
- **Device triggers fire on HA-originated button commands too.** A device trigger reacts to the bridge's button-activity report, which JetStream also emits when HA itself taps a button (via a button switch entity), not only on a physical wall press. The protocol doesn't distinguish origin, so if you both expose a button as a switch and trigger on it, the trigger fires on HA-initiated taps as well.
- **Elegance switch import is experimental.** Named keypad buttons in the `.elg` are imported as switch entities, but two things are unverified: (1) the keypad `letter+number` → global switch-index mapping is derived from the protocol's `^H` bitmap layout, not confirmed on hardware — if a switch entity doesn't track its physical button, the mapping needs adjustment; and (2) a switch only reports state if the bridge is actually programmed to emit press/release events for it (the per-switch "send action" flag, written via the Programming Software). Press-via-HA (`^I`) works regardless.

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
