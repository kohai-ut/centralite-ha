# Centralite Lighting for Home Assistant

A Home Assistant custom integration for **Centralite Elegance** and **Centralite JetStream** lighting systems. Communicates with the bridge over RS-232.

> **Status: v2.0.0 in active development.** Greenfield rewrite of the v1 integrations. Not yet released; not yet HACS-installable.

## Supported hardware

- **Centralite Elegance** — RS-232 bridge, single-system (and eventually multi-system) configurations
- **Centralite JetStream** — RS-232 bridge

CentraLite Systems is no longer in business. Both product lines are community-maintained by a handful of independent installers.

## Features (planned)

- Native HA config flow — UI setup, no YAML required
- Async serial communication via `serialx`
- DataUpdateCoordinator with push-primary updates and a safety-net poll
- Bulk import of friendly names from `.elg` (Elegance) and `.jts` (JetStream) export files
- One switch per scene — no more `-ON` / `-OFF` entity pairs
- HACS-compatible (custom repository at v2.0; default-listed once stable)

## Installation

Coming with the v2.0.0 release. Until then this repo is a build-in-progress.

If you currently run the v1 integration, it remains available at:
- [kohai-ut/centralite_elegance @ v1.0.1](https://github.com/kohai-ut/centralite_elegance/releases/tag/v1.0.1)
- [kohai-ut/centralite_jetstream @ v1.0.1](https://github.com/kohai-ut/centralite_jetstream/releases/tag/v1.0.1)

## Development

Design and phase breakdown live in a private planning document; see commits and `CHANGELOG.md` for shipped work.

## License

MIT — see [LICENSE](LICENSE).
