# JetStream Load-Level Refresh (Issue #35) Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after owner approval. This document authorizes no implementation, commit, release, or live HA changes.

**Goal:** After startup and after a reconnect, read each configured JetStream load's level with `^F` so Home Assistant shows real on/off and brightness, without delaying startup or reconnect.

**Architecture:** A cancellable background task owned by the config entry queries loads one at a time. Replies update state through the existing `DEV` load callback, so no new state-writing code is needed. Setup and reconnect never await the refresh.

**Tech Stack:** Python, existing serialx protocol, `ConfigEntry.async_create_background_task`, pytest-homeassistant-custom-component, Ruff.

**Status:** Approved for implementation by the owner on 2026-11-15, with the timeout, budget and retry-pass changes in §3. Baseline: main at e306481. Source: https://github.com/kohai-ut/centralite-ha/issues/35.

---

## 1. Facts this plan rests on

- JetStream has no bulk-state command. Both JetStream manuals in `docs/protocols/` list only `^A ^B ^C ^D ^E ^F ^N ^T ^P ^R`, `Ping`, `inc`, `dec`. `^F` (one device) is the only documented state read.
- `JetStreamProtocol.get_load_level()` already exists (`protocol/jetstream.py:121-133`). Its `DEV` reply fires the load callback and fulfills the request (`jetstream.py:277-280`), so `coordinator._on_load_event` updates on/off and level.
- The issue reporter confirmed on hardware that `^F001` returns `DEV00100`. This has not been verified on the owner's bridge.
- `_prime_loads()` is awaited inside `async_init()` (`coordinator.py:101`) and `_try_reconnect()` (`coordinator.py:258`). Anything slow there blocks config-entry setup or keeps entities unavailable.
- `get_load_level()` uses the 2-second `COMMAND_TIMEOUT` and has no timeout argument. Each write also holds the lock for the 0.1 s inter-command delay.

## 2. Why the issue's patch is not merged as written

- It awaits every query inside setup. Each silent load costs 2 s; a dead or silent bridge could hold startup for minutes.
- It logs a warning with a traceback per failed load, on every reconnect.
- It selects JetStream with `isinstance`, which the test `FakeProtocol` cannot exercise.

## 3. Design

### Non-blocking rule (the hard requirement)

- `async_init()` and `_try_reconnect()` only *start* the refresh. They never await it.
- The task is created with `self.config_entry.async_create_background_task(...)`. HA does not wait for background tasks at startup and cancels them when the entry unloads.
- Only one refresh runs at a time. Starting a new one cancels the old one. `async_shutdown()` and `_on_disconnect()` cancel it too.

### Refresh loop

- Runs only when the protocol reports a new capability flag `supports_per_load_query` (default `False`; `True` for JetStream). Follow the existing `supports_bulk_query` pattern in `protocol/__init__.py`. Elegance keeps its bulk prime and safety poll unchanged.
- Iterates `CONF_LOAD_IDS` in order, one `get_load_level(idx, timeout=...)` at a time.
- Stops immediately if the coordinator is shutting down or the link is down.
- Limits, as named constants in `coordinator.py`:
  - `LOAD_REFRESH_TIMEOUT = 2.0` s per load. The bridge is on RS-232 and must ask each device over the mesh, so replies may be slow. This matches the existing `COMMAND_TIMEOUT` the issue reporter's working patch ran with. The refresh is in the background, so the longer wait does not affect startup.
  - `LOAD_REFRESH_PROBE_COUNT = 5`: if the first 5 queries get no reply at all, stop. The bridge is not answering (about 10 s, in the background).
  - `LOAD_REFRESH_BUDGET = 300` s total, then stop.
- A silent load after the probe phase is skipped, not fatal. Keypads, slaves and the RS-232 bridge entry are imported as loads today and may never answer; they must not end the refresh early.
- One retry pass: after the first pass, re-ask only the loads that did not answer, once, within the same budget. This covers a slow or dropped mesh reply without tuning. No retry pass if the probe phase stopped the refresh.
- User commands are not starved: the lock is taken per query, so a light command waits at most one query (up to 2 s, and only behind a silent device) during a refresh.
- The timeout is fixed, not self-adjusting. Revisit only if measured reply times on real hardware justify it.

### Logging

- Per-load failure: `debug`, no traceback.
- One `info` summary at the end: refreshed count, silent count, and why it stopped if it stopped early.

### Reconnect behavior

- Availability is restored as it is today, then the refresh starts. Until a load is re-read it shows its last known state, which is the current behavior, so there is no regression.

### Out of scope

- Periodic polling of JetStream loads.
- An options toggle to disable the refresh. Add only if hardware testing shows a need.
- Any change to Elegance, entity structure, or the device-organization plan.
- Raising the minimum HA version. This should ship before that change so users on older HA get it.

## 4. Tasks (TDD; no commits unless requested)

For each task: write the failing test, run it to see it fail, make the smallest change, rerun.

### Task 1: Capability flag and per-call timeout
- Modify `custom_components/centralite/protocol/__init__.py`: add `supports_per_load_query` returning `False`.
- Modify `custom_components/centralite/protocol/jetstream.py`: return `True`; add keyword `timeout: float = COMMAND_TIMEOUT` to `get_load_level` and pass it to `_sendrecv` (same shape as `get_device_name`).
- Test in `tests/test_protocol_jetstream.py`: flag is true; a short timeout raises `ProtocolError` quickly; the existing unrelated-`DEV` test still passes.
- Test in `tests/test_protocol_elegance.py`: flag is false.

### Task 2: Extend the test fake
- Modify `tests/conftest.py` `FakeProtocol`: add `supports_per_load` flag, a `load_levels: dict[int, int]` map, and a set of silent IDs. `get_load_level(idx, *, timeout=...)` records the call, fires `load_cb(LoadEvent(idx, level))` for known IDs, and raises `ProtocolError` for silent ones.
- Keep the default behavior so existing tests are unaffected.

### Task 3: Background refresh in the coordinator
- Modify `custom_components/centralite/coordinator.py`: add the constants, `_load_refresh_task`, `_start_load_refresh()`, `_cancel_load_refresh()` and `_refresh_loads()`.
- Call `_start_load_refresh()` at the end of `async_init()` and in the success branch of `_try_reconnect()` after availability is restored.
- Cancel in `async_shutdown()` and `_on_disconnect()`.
- Update the `async_init` and `_prime_loads` docstrings, which say JetStream state is push-only.
- Tests in `tests/test_coordinator.py` (use a JetStream-shaped entry with `CONF_LOAD_IDS`; wait with `hass.async_block_till_done(wait_background_tasks=True)`):
  1. `async_init()` returns before any `get_load_level` call completes (fake blocks on an `asyncio.Event`).
  2. After the task runs, `coord.data["loads"]` holds the fake's levels and on/off values.
  3. A silent load in the middle is skipped and later loads are still read.
  4. First 5 loads silent: refresh stops after 5 calls.
  4a. A load silent on the first pass and answering on the retry pass ends up with the right state; a load silent on both is asked exactly twice.
  5. Budget exceeded: refresh stops (patch the constant small).
  6. Shutdown mid-refresh cancels the task; no calls afterwards.
  7. Disconnect mid-refresh cancels the task.
  8. Reconnect starts a new refresh and restores availability without waiting for it.
  9. A second start while one is running leaves exactly one task.
  10. Bulk-query (Elegance-shaped) protocol: `get_load_level` is never called; existing prime and poll tests still pass.

### Task 4: Docs
- Modify `README.md`: features list and the options note at line 92 (JetStream is still not polled, but is now read once at startup and reconnect); troubleshooting entry for wrong initial state.
- Modify `CHANGELOG.md`. Credit the issue reporter.
- Leave `translations/en.json` line 48 as is; the poll setting still does not apply to JetStream.

## 5. Validation

Automated, in an isolated environment (use `uv` with a verified Python; the host's bare `pip` is mismatched):
- `python -m pytest tests/test_coordinator.py tests/test_protocol_jetstream.py tests/test_protocol_elegance.py`
- `python -m pytest`
- `ruff check custom_components/ tests/`

Hardware check on the owner's JetStream bridge, with debug logging on, in a window the owner chooses. Requires separate approval because it restarts or reloads the integration:
1. Leave several lights at different levels. Restart HA. Confirm states and brightness are correct without touching a switch.
2. From the debug log, measure real `^F` reply times. Adjust `LOAD_REFRESH_TIMEOUT` if replies approach 1 s.
3. Note which configured IDs never answer (expected candidates: `-SLAVE` entries, keypads, the RS-232 bridge entry) and confirm the refresh continues past them.
4. Operate a light from HA during the refresh and confirm it responds.
5. Unplug and replug the serial adapter. Confirm entities come back promptly and levels then correct themselves.
6. Confirm HA startup time is unchanged with the bridge unplugged.

Acceptance gates:
- Setup and reconnect never wait on the refresh (tests 1 and 8, hardware step 6).
- Elegance behavior unchanged.
- No per-load warnings or tracebacks in normal logs.
- No minimum-HA-version change in this release.

## 6. Open decisions for the owner

1. Release vehicle: a small 2.0.2 (recommended) or bundled with later work. A version bump and release need explicit approval.
2. Whether to reply on issue #35 with the planned approach before implementing.
3. Timeout value, after hardware step 2.
