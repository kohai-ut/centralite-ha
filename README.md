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
| Centralite JetStream | RS-232 bridge | Up to ~199 loads, ~199 switches × 3 buttons, 100 scenes; supports on-device name query |

A 19200-baud USB-to-serial adapter on the HA host (Raspberry Pi, NUC, VM) plugs into the bridge's RS-232 port. `/dev/serial/by-id/...` paths are recommended for stability across reboots.

## Which integration should I use? (vs. the built-in LiteJet integration)

Home Assistant ships a built-in [**LiteJet**](https://www.home-assistant.io/integrations/litejet/) integration (via [`pylitejet`](https://github.com/joncar/pylitejet)). LiteJet is a sibling CentraLite product, and because these systems share a family of RS-232 protocols, the built-in integration also advertises Elegance and JetStream support. Here's how to choose:

| Your system | Use |
|---|---|
| **LiteJet** | The [built-in LiteJet integration](https://www.home-assistant.io/integrations/litejet/) — it's purpose-built for LiteJet and well maintained. This integration does **not** target LiteJet. |
| **JetStream** | **This integration.** JetStream has no bulk-state command (`^G`/`^H`), which the LiteJet library requires to connect, so the built-in integration cannot talk to a JetStream bridge. This integration speaks JetStream's native `DEV`/`ACT`/`SCN` protocol. |
| **Elegance** | **This integration** is recommended, especially for larger installs. The built-in integration handles Elegance only through the shared LiteJet command set: it reads just the first 48 loads (this integration handles the full 192-load address space) and mis-parses Elegance's physical-switch/keypad events. For a small, loads-only Elegance setup the built-in one may suffice. |

In short: **LiteJet → built-in; Elegance / JetStream → here.** If you're not sure which CentraLite system you have, check the controller's model or the programming software it shipped with.

## Features

- **Native HA config flow** — UI setup, no YAML editing required
- **Async serial** via the HA-blessed [`serialx`](https://github.com/home-assistant-libs/serialx) library
- **Push-primary updates** with an optional safety-net `^G` poll for loads not programmed for spontaneous output
- **One switch per scene** — no more `-ON`/`-OFF` entity pairs (v1 limitation removed)
- **Bulk friendly-name import** from your Centralite `.elg` (Elegance) export
- **JetStream on-device name query** via the bridge's `^N` command — no PC config file needed
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

- **Paste your `.elg` (Elegance) or `.jts` (JetStream) config file** — the integration parses your friendly names and the list of configured loads and scenes automatically. Export `.elg` from the Centralite Elegance Programming Software; export `.jts` from JetStream Designer. (`.jts` import is on the roadmap; for JetStream right now use the on-device `^N` query — names come from the bridge itself.)
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

## Known limitations

- **Elegance scene state is "commanded only".** The Elegance bridge has no scene-state push event. When you activate a scene from HA, the scene-switch reflects the commanded state. Scenes activated externally (physical button, timed event) won't update the HA state. JetStream does not have this limitation (it pushes `SCN` events).
- **Per-load push events require Customer Options bit set.** In the Elegance Programming Software, each load must be configured for "third party output" individually, OR DIP Switch 5 must be ON (which pushes all loads, one per second). If neither is set, the integration relies on the safety-net poll for updates.
- **`.elg` format is REV 1.1.** Future Centralite software revisions could change the format. The parser fails gracefully with a clear error if the format isn't recognized; fall back to manual ID entry.

## Troubleshooting

- **"Failed to connect to Centralite bridge"** — check the serial port path with `dmesg | grep tty` after plugging in the USB adapter. Confirm the bridge LED activity. Confirm "send CR after responses" (Elegance Customer Options bit #6) is enabled.
- **Lights show wrong initial state** — the bridge may not have the "spontaneous output" flag set per load. Enable it in the Elegance Programming Software, OR rely on the safety-net poll.
- **Entities missing after upgrade from v1** — check Settings → Repairs for the migration log. If entities are still missing, file a bug with your `core.entity_registry` JSON (with secrets redacted).

## Development

Protocol reference docs are in [`docs/protocols/`](docs/protocols/) — manufacturer PDFs for both Elegance and JetStream, preserved here since CentraLite is no longer in business.

The pure protocol/parser/migration tests run without Home Assistant installed.
Run them from the repo root with the repo on the import path:

```
PYTHONPATH=. python tests/test_protocol_common.py
PYTHONPATH=. python tests/test_protocol_elegance.py
PYTHONPATH=. python tests/test_protocol_jetstream.py
PYTHONPATH=. python tests/test_parsers_elg.py
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
