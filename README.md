# Centralite Lighting for Home Assistant

A modern Home Assistant custom integration for **Centralite Elegance** and **Centralite JetStream** lighting systems. Communicates with the bridge over RS-232.

> [!NOTE]
> **Status: stable (v2).** A greenfield rewrite of the legacy v1 integrations
> ([centralite_elegance](https://github.com/kohai-ut/centralite_elegance),
> [centralite_jetstream](https://github.com/kohai-ut/centralite_jetstream), both archived at
> `v1.0.1`), validated end-to-end against real Elegance and JetStream hardware. Considerably
> more capable than v1; see [Known limitations](#known-limitations) for what isn't covered yet.

## What this is

CentraLite Systems made commercial lighting controllers that talk to switches and dimmers over a proprietary mesh. CentraLite went out of business; this integration keeps the existing hardware usable by anyone running Home Assistant.

## Supported hardware

| System | Bridge | Notes |
|---|---|---|
| Centralite Elegance | RS-232 bridge (single-system, multi-system planned) | Up to 192 loads, 384 switches, 256 scenes |
| Centralite JetStream | RS-232 bridge | Up to ~199 loads, ~199 switches × 3 buttons, 100 scenes; `.jts` config import |

A USB-to-serial adapter on the HA host (Raspberry Pi, NUC, VM) plugs into the bridge's RS-232 port. Adapters with FTDI and Prolific chips are both known to work.

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

## Before you start

Three things have to be right before Home Assistant can talk to the lights: the serial connection, the port path, and a few settings in CentraLite's own programming software. Most "it connects but nothing updates" problems come from the last one.

### 1. Serial connection

| Setting | Value |
|---|---|
| Baud rate | 19200 (the bridge default) |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Flow control | None |

The integration only asks for the baud rate; the rest are fixed. The JetStream bridge uses a three-wire DB9 connection (pin 2 transmit, pin 3 receive, pin 5 ground).

### 2. Find the serial port path

Use the stable `/dev/serial/by-id/...` path, not `/dev/ttyUSB0`. The `ttyUSB` number can change after a reboot or when another USB device is plugged in.

1. Plug the USB-to-serial adapter into the HA host.
2. Open **Settings → System → Hardware → ⋮ → All hardware**.
3. Find the adapter (often listed as `ttyUSB0`) and expand it.
4. Copy the full path that starts with `/dev/serial/by-id/`, for example `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10R1M6X-if00-port0`.

Paste the whole path, including the `/dev/serial/by-id/` prefix, into the integration's serial port field. If the adapter is not listed, unplug it, plug it back in, and reopen the list.

### 3a. JetStream: prepare the system in JetStream Designer

The JetStream bridge sends nothing to Home Assistant until third-party output is switched on, once for the bridge and once for each device. Do this in JetStream Designer, connected to your existing network through its USB programming bridge. Back up your Designer project first.

1. Select the **RS-232 Bridge** in the device list and check **Update Third Party**.
2. Check that the bridge's baud rate is 19200 (**Settings** on the bridge page). If you change it, click **Submit**.
3. For each light you want in Home Assistant, open the device, check **Send T-Party**, and click **Send Device**. Wait for it to finish before moving to the next one.
   - To set every device at once, use **Global Settings** and enable third-party reporting there.
4. Save the project, then export the `.jts` file for the import step below.

> [!IMPORTANT]
> Saving the project does not program the hardware. A setting only takes effect once it has been sent to the device (**Send Device** or **Submit**). Likewise, importing a `.jts` into Home Assistant only tells Home Assistant what exists; it changes nothing on the JetStream system.

The **Send T-Party** checkbox only appears after **Update Third Party** is checked on the bridge. Only devices with **Send T-Party** checked are imported from the `.jts`.

### 3b. Elegance: prepare the system in the Elegance Programming Software

- **Send CR after responses** (Customer Options bit #6) must be enabled. The integration relies on CR-framed responses and cannot change this setting over RS-232.
- **Load updates:** set each load to "third party output", or turn DIP switch 5 ON (which reports all loads, one per second). Without either, Home Assistant only learns of changes from the safety-net poll.
- **Switch state:** a keypad button only reports press and release if its "send action" flag is set. Pressing it from Home Assistant works either way.

## Installation (HACS custom repository)

Until v2 ships to the HACS default repository:

1. In HA, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/kohai-ut/centralite-ha` with category **Integration**.
3. Click **Download** on the Centralite Lighting entry.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for **Centralite**.

### Staying up to date

HACS manages updates: when a new version is published, it shows an **Update** badge on the Centralite entry — open it, click **Update**, and **restart Home Assistant**. Your configuration and entities are preserved across updates.

## Configuration

The config flow has two steps:

### Step 1 — Bridge connection

- **System type**: Elegance / JetStream
- **Serial port**: the full `/dev/serial/by-id/...` path (see [Find the serial port path](#2-find-the-serial-port-path))
- **Baud rate**: defaults to 19200

### Step 2 — Names and devices

You have two options for telling HA which loads/scenes/switches you have and what they're named:

- **Paste your `.elg` (Elegance) or `.jts` (JetStream) config file** — the integration parses your friendly names and the list of configured loads and scenes automatically. Export `.elg` from the Centralite Elegance Programming Software; export `.jts` from JetStream Designer. (For JetStream, only devices with **Send T-Party** checked are imported, since the others can't be observed over RS-232. Configured keypad buttons on those devices are imported as switch entities.)
- **Or enter comma-separated IDs** for loads, scenes, switches if you don't have an export file. Use the real device IDs from the programming software and keep any gaps: the first five devices are not necessarily 1 to 5. Friendly names can be set per-entity in HA's UI afterwards.

Both paths can be combined. (Using the config file approach is the most tested.)

After setup, you can edit any entity's display name via HA's standard entity-rename UI on its device card.

### Verify it works

1. Change a light's brightness from Home Assistant. The physical light should follow.
2. Operate the same light at its wall switch. Home Assistant should update its on/off state and brightness shortly after.
3. If step 1 works but step 2 does not, the device is not reporting to the bridge. See [Troubleshooting](#troubleshooting).

For a closer look, turn on [debug logging](#watching-the-logs-debug-logging) and repeat the test. On JetStream, a working light produces lines like these:

| Log line | Meaning |
|---|---|
| `rx: 'DEV10399'` | Device 103 reports level 99 (full on) |
| `rx: 'DEV10300'` | Device 103 reports off |
| `rx: 'ACT10301T'` | Device 103, button 1, tapped |

A `send:` line with no `rx:` line after it means Home Assistant sent the command but the bridge reported nothing back.

### Options

Once configured, **⋮ → Configure** on the integration:

- **Safety-net poll interval** — Elegance only: how often to do a full `^G` query as a fallback for loads not configured for push notifications (default 300s; `0` disables). JetStream has no bulk-state command and is push-only, so this setting has no effect there.

> [!NOTE]
> The bridge's "send CR after responses" Customer Option (Elegance bit #6) must be enabled in the Elegance Programming Software — the integration relies on CR-framed responses and cannot toggle this setting over RS-232.

## Known limitations

- **Elegance scene state is "commanded only".** The Elegance bridge has no scene-state push event. When you activate a scene from HA, the scene-switch reflects the commanded state. Scenes activated externally (physical button, timed event) won't update the HA state. JetStream does not have this limitation (it pushes `SCN` events).
- **Per-load push events require Customer Options bit set.** In the Elegance Programming Software, each load must be configured for "third party output" individually, OR DIP Switch 5 must be ON (which pushes all loads, one per second). If neither is set, the integration relies on the safety-net poll for updates.
- **`.elg` format is REV 1.1.** The parser fails gracefully with a clear error if the format isn't recognized; fall back to manual ID entry.
- **Device triggers fire on HA-originated button commands too.** A device trigger reacts to the bridge's button-activity report, which JetStream also emits when HA itself taps a button (via a button switch entity), not only on a physical wall press. The protocol doesn't distinguish origin, so if you both expose a button as a switch and trigger on it, the trigger fires on HA-initiated taps as well.
- **Elegance switch state requires the per-switch "send action" flag.** Named keypad buttons in the `.elg` are imported as switch entities. The keypad `letter+number` → global switch-index mapping (`idx = (letter-'A')*24 + number`) is **confirmed** against a real install. The remaining caveat is *state*: a switch only reports on/off if the bridge is actually programmed to emit press/release events for it (the per-switch "send action" flag, set in the Programming Software). Press-via-HA (`^I`) works regardless.
- **Single-system Elegance only (multi-system / Elegance XL not yet supported).** A single Elegance or Elegance XL master panel — up to 192 loads, 384 switches, 256 scenes — is fully supported. Multi-system installs (2–4 panels chained, addressing up to 768 loads) are **not** yet: the load cap is fixed at 192 and the `^G`/`^H` bulk-state decoders assume a single board. The protocol path is understood — multi-system uses the per-system `^gs`/`^hs` status queries plus the global 001–768 command index — so adding it is a contained, opt-in change, **but it can't be verified without a 2–4 panel system to test against.** If you have one and want to help, please open an issue.

## Troubleshooting

- **"Failed to connect to Centralite bridge"** — check the serial port path (see [Find the serial port path](#2-find-the-serial-port-path)) and that nothing else is using the port. Confirm the bridge LED activity. Confirm "send CR after responses" (Elegance Customer Options bit #6) is enabled.
- **Lights show wrong initial state** — the bridge may not have the "spontaneous output" flag set per load. Enable it in the Elegance Programming Software, OR rely on the safety-net poll.
- **JetStream dimmers respond a few times and then stop, or state never updates** — the device's third-party reporting has not reached the hardware, even if the box looks checked in Designer. For each affected light in JetStream Designer: uncheck **Send T-Party**, click **Send Device** and wait; then check it again, click **Send Device** and wait. Confirm **Update Third Party** is still checked on the RS-232 bridge. This resolved unreliable dimmers on a 31-device system (reported by a user; it is not in CentraLite's manuals).
- **A JetStream device is missing after import** — check that **Send T-Party** is checked for it and that the `.jts` was exported after you changed it. Also check for two devices sharing one Device ID in the project: only one of them is imported. Give each device a unique ID in Designer and export again.
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

## Migration from v1

Upgrading from the separate v1 integrations (`centralite_elegance` / `centralite_jetstream`)? v2 adopts your existing entities in place, but the steps must be done in a specific order. Follow **[docs/MIGRATING_FROM_V1.md](docs/MIGRATING_FROM_V1.md)** before installing.

## Credits

The JetStream Designer preparation steps, the serial-port walkthrough and the Send T-Party fix draw on a [setup guide by pgyrogear](https://community.home-assistant.io/t/centralite-jetstream/1029391/2) on the Home Assistant community forum.

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
