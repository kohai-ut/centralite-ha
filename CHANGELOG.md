# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding: HACS-ready directory layout, `manifest.json`, `hacs.json`, MIT license
- GitHub Actions workflow running `hassfest` and HACS validation on every push
- Empty module skeletons for protocol abstraction (Elegance + JetStream), entity layer, coordinator, config flow, parsers, and one-time migration helper

## Legacy v1

The v2 codebase is a greenfield rewrite. For v1 history see:

- [kohai-ut/centralite_elegance @ v1.0.1](https://github.com/kohai-ut/centralite_elegance/releases/tag/v1.0.1) — Elegance integration
- [kohai-ut/centralite_jetstream @ v1.0.1](https://github.com/kohai-ut/centralite_jetstream/releases/tag/v1.0.1) — JetStream integration
