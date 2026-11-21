# Upgrading to v3

Version 3 changes how a **JetStream** system appears in Home Assistant. **Elegance is not changed.**

Nothing moves when you update. An existing JetStream bridge keeps its current layout until you choose to move it.

## What is different

| | Before (v2) | After moving (v3) |
|---|---|---|
| Devices | One device, the bridge, holding every light and button | One device per physical JetStream unit, linked to the bridge |
| Lights | `light.*` under the bridge | The same `light.*` entities, each on its own device. Entity IDs do not change |
| Keypad buttons | A `switch.*` per button, which stayed "on" after the first tap | An event entity **Button N** (presses at the wall) and a button entity **Tap button N** (press it from Home Assistant) |
| Scenes | `switch.*` under the bridge | Unchanged |
| Adding a device | Remove and re-add the integration | **Configure** → Add a device |

The keypad button switches are the one thing that goes away. If you use them in automations, Node-RED flows or dashboards, those need to point at the new entities after you move.

## Before you move

1. **Take a backup** (Settings → System → Backups). Going back after the move means restoring it.
2. Check whether anything uses the button switches (`switch.…_button_1` and so on) or targets the whole bridge device. The move screen lists the Home Assistant automations and scripts it can find. **It cannot see Node-RED flows, dashboards or templates.**

Things that keep working without any change:

- anything that uses a light or scene by its entity ID;
- automations that use the button device triggers on the bridge;
- anything listening for the `centralite_event` event.

## Moving

1. Go to **Settings → Devices & services → Centralite**, and choose **Configure** on the JetStream bridge.
2. Choose **Move to one device per JetStream unit**.
3. Read the preview. It shows how many devices will be created, the switches that will be removed, and what uses them.
4. Tick the box and submit. The integration reloads once.

Afterwards:

- Each light is on a device named from your JetStream project. A light that had its own area keeps it; the others start in the bridge's area.
- Rename a device or move it to another area from its device page. The light takes its name from the device.
- A light you had renamed yourself keeps your name. To use the device's name instead, turn on **Use device name** in the light's settings.

A notice under **Settings → Repairs** reminds you while a bridge is still on the old layout. In version 4.0, bridges that have not moved will be moved automatically.

## Using the new button entities

- **Button N** (event) records each press at the wall: a single press for a tap, and a long press start and end for a hold. A double tap is two single presses. Use it as an automation trigger.
- **Tap button N** presses the button from Home Assistant. JetStream then does whatever that button is programmed to do. A tap sent this way does not fire the event entity.
- **Hold button N** is off by default. It holds the button for two seconds and releases it, for buttons programmed with a press-and-hold action. Enable it on the device page if you need it.

## Adding, changing and removing devices

After moving, **Configure** offers:

- **Add a device**: the Device ID from JetStream Designer, its type (dimmer, on/off switch, keypad only) and which buttons to add. You can queue several before saving.
- **Add several devices**: one per line, for example `81, dimmer, 1, 3`.
- **Change a device** between dimmer and on/off.
- **Remove a device**, after a confirmation listing what will be deleted. **Delete** in the ⋮ menu on a device's own page does the same.

A device must already be programmed in JetStream Designer, with **Send T-Party** on and **Update Third Party** done on the bridge.

## Lights that do not answer

JetStream is asked for each light's level one at a time after a restart or reload. A unit with a weak radio link may not answer. Its light then keeps the state Home Assistant last knew, until the unit next reports.

## Requirements

Version 3 needs Home Assistant 2026.12 or newer. On an older Home Assistant, stay on v2.0.x.
