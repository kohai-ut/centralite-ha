# Centralite Protocol Documentation

Manufacturer documentation for the Centralite Elegance and JetStream lighting systems, preserved here for the small community still maintaining these systems. CentraLite Systems, Inc. ceased operations.

## Files

| File | Description | Source |
|---|---|---|
| [elegance-rs232-protocol.pdf](elegance-rs232-protocol.pdf) | Elegance third-party RS-232 protocol — commands `^A`–`^M`, push events (`^Knnnll`, `Psnnn`, `Rsnnn`), bulk-state bit layouts (`^G`/`^H`). **Authoritative source for `custom_components/centralite/protocol/elegance.py`.** | CentraLite Systems, 2006 (firmware 07.00) |
| [elegance-programming-guide.pdf](elegance-programming-guide.pdf) | Elegance hardware programming reference. How loads, scenes, and switches are configured via the Elegance Programming Software. | CentraLite Systems |
| [jetstream-rs232-bridge.pdf](jetstream-rs232-bridge.pdf) | JetStream RS-232 Bridge user guide — commands, spontaneous output (`DEV`, `ACT`, `SCN`), discovery/capture procedures. **Authoritative source for `custom_components/centralite/protocol/jetstream.py`.** | CentraLite Systems, 2008 |
| [jetstream-installation-programming.pdf](jetstream-installation-programming.pdf) | JetStream installation and programming guide (60 pages). Hardware setup, device pairing. | CentraLite Systems |

## How to use

When implementing or debugging a protocol method, the relevant PDF is the source of truth. Code in `custom_components/centralite/protocol/` cites manual page numbers in comments where the wire format is non-obvious — e.g. the bit layout of the `^G` response.
