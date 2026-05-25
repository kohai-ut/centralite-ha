"""JetStreamProtocol: RS-232 protocol for Centralite JetStream bridges.

Implements the third-party protocol documented in
docs/protocols/jetstream-rs232-bridge.pdf.

Differences vs Elegance:
- Extra commands: ^N (device name query), ^T (tap switch), inc/dec
  (increment/decrement load), Ping (returns Hello).
- Switches are addressed as (device_idx, button_idx); buttons 1-3 per device.
- Push events use different prefixes:
    DEV ddd ll       len 8   - load level change
    ACT ddd bb T/P/R len 9   - button tap/press/release
    SCN sss 1/0      len 7   - scene activate/deactivate
- supports_device_name_query = True
- supports_scene_push = True (Elegance has no scene push)

Note: command-completion responses for ^A/^B/^E/^F arrive in DEV format
(i.e. as push events), so the dispatcher fires the load callback AND
fulfills any pending request for the same line. For ^F specifically, the
caller provides a matcher to ignore unrelated DEV pushes.
"""

from __future__ import annotations

import logging

from . import LoadEvent, SceneEvent, SwitchEvent
from ._base import COMMAND_TIMEOUT, _BaseSerialProtocol
from .common import ProtocolError

_LOGGER = logging.getLogger(__name__)

# The third-party protocol table (jetstream-rs232-bridge.pdf p.8) documents the
# device field "ddd" as 001-096, but the JetStream network supports up to ~200
# devices (jetstream-installation-programming.pdf) and real installs address
# higher numbers. We keep the permissive 199 bound: the bridge silently ignores
# any device number it doesn't recognize, so an over-high bound costs nothing,
# whereas an over-tight one would refuse to control legitimate devices.
# Scenes are documented as 001-100.
JETSTREAM_MAX_LOADS = 199
JETSTREAM_MAX_SCENES = 100
JETSTREAM_MAX_SWITCHES = 199
JETSTREAM_MAX_BUTTONS_PER_SWITCH = 3

# The v1 JetStream integration discovered that back-to-back commands caused
# the bridge to return all responses correlated to only the last command.
# A ~100ms pause between commands made the symptom disappear. Possibly a
# USB-to-serial-adapter quirk, possibly a JetStream firmware quirk. Either
# way, preserving the workaround.
JETSTREAM_INTER_COMMAND_DELAY = 0.1

# Per-probe timeout for the ^N discovery scan. Unconfigured devices are silent,
# so most probes hit this timeout; keep it short so scanning the whole range
# stays in the tens-of-seconds range rather than minutes. A configured device
# replies well under this on a healthy link.
SCAN_PROBE_TIMEOUT = 0.3

DEV_PREFIX = "DEV"
ACT_PREFIX = "ACT"
SCN_PREFIX = "SCN"
NAM_PREFIX = "NAM"
HELLO_RESPONSE = "Hello"

DEV_LEN = 8  # "DEV" + 3 idx + 2 level
ACT_LEN = 9  # "ACT" + 3 idx + 2 button + 1 action
SCN_LEN = 7  # "SCN" + 3 idx + 1 state


_ACTION_MAP = {"T": "tap", "P": "press", "R": "release"}


class JetStreamProtocol(_BaseSerialProtocol):
    """Async JetStream bridge protocol."""

    inter_command_delay = JETSTREAM_INTER_COMMAND_DELAY

    @property
    def max_loads(self) -> int:
        return JETSTREAM_MAX_LOADS

    @property
    def max_switches(self) -> int:
        return JETSTREAM_MAX_SWITCHES

    @property
    def supports_device_name_query(self) -> bool:
        return True

    @property
    def supports_scene_push(self) -> bool:
        return True

    @property
    def supports_bulk_query(self) -> bool:
        # JetStream has no ^G/^H. The command table (jetstream-rs232-bridge.pdf
        # p.8) offers only ^F (per-device level) for state; everything else
        # arrives as spontaneous DEV/ACT/SCN output. State is push-only.
        return False

    async def activate_load(self, idx: int) -> None:
        self._validate_load_idx(idx)
        await self._send(f"^A{idx:03d}")

    async def deactivate_load(self, idx: int) -> None:
        self._validate_load_idx(idx)
        await self._send(f"^B{idx:03d}")

    async def set_load_level(self, idx: int, level: int, rate: int = 0) -> None:
        self._validate_load_idx(idx)
        if not 0 <= level <= 99:
            raise ValueError(f"level must be 0-99, got {level}")
        if not 0 <= rate <= 31:
            raise ValueError(f"rate must be 0-31, got {rate}")
        await self._send(f"^E{idx:03d}{level:02d}{rate:02d}")

    async def get_load_level(self, idx: int) -> int:
        """Send ^F and wait for a DEV response specifically for this idx."""
        self._validate_load_idx(idx)
        expected_prefix = f"{DEV_PREFIX}{idx:03d}"

        def matches(line: str) -> bool:
            return len(line) == DEV_LEN and line.startswith(expected_prefix)

        response = await self._sendrecv(f"^F{idx:03d}", matches=matches)
        try:
            return int(response[6:8])
        except ValueError as e:
            raise ProtocolError(f"Bad ^F response: {response!r}") from e

    async def get_all_load_states(self) -> dict[int, bool]:
        # JetStream has no bulk load-state command (no ^G). Earlier code sent
        # ^G and waited for a 48-hex Elegance-shaped reply that never comes,
        # timing out after COMMAND_TIMEOUT on every call. The coordinator gates
        # on supports_bulk_query (False here) and never calls this; the raise is
        # a guard against a future caller reintroducing the timeout.
        raise ProtocolError("JetStream has no bulk load-state query; state is push-only")

    async def activate_scene(self, idx: int) -> None:
        self._validate_scene_idx(idx)
        await self._send(f"^C{idx:03d}")

    async def deactivate_scene(self, idx: int) -> None:
        self._validate_scene_idx(idx)
        await self._send(f"^D{idx:03d}")

    async def press_switch(
        self, idx: int, *, button: int = 1, auto_release: bool = True
    ) -> None:
        """Press a switch button.

        JetStream-specific kwarg: `button` (1-3). The CentraliteProtocol ABC
        defines press_switch(idx) only; JetStream extends with button selection.
        auto_release here is a no-op (^P is followed naturally by the user
        physically releasing); included for ABC compatibility.
        """
        self._validate_switch_idx(idx)
        self._validate_button_idx(button)
        await self._send(f"^P{idx:03d}{button:02d}")
        if auto_release:
            await self.release_switch(idx, button=button)

    async def release_switch(self, idx: int, *, button: int = 1) -> None:
        self._validate_switch_idx(idx)
        self._validate_button_idx(button)
        await self._send(f"^R{idx:03d}{button:02d}")

    async def tap_switch(self, idx: int, *, button: int = 1) -> None:
        """JetStream ^T: simulate a complete tap (press+release in one command).

        Historical note: v1 sent ^T followed immediately by ^R, which the
        user observed caused brightness on the affected load to drop to 1.
        Likely root cause was the missing inter-command delay (see
        JETSTREAM_INTER_COMMAND_DELAY). We only send ^T here; the
        ^P+^R press/release path is the one with the auto-release pair,
        and it respects the inter-command delay between writes.
        """
        self._validate_switch_idx(idx)
        self._validate_button_idx(button)
        await self._send(f"^T{idx:03d}{button:02d}")

    async def get_all_switch_states(self) -> dict[int, bool]:
        # No ^H on JetStream either (see get_all_load_states). Button state
        # arrives via spontaneous ACT events keyed (device, button).
        raise ProtocolError("JetStream has no bulk switch-state query; state is push-only")

    async def get_device_name(
        self, idx: int, *, timeout: float = COMMAND_TIMEOUT
    ) -> str | None:
        """Query the bridge for a device's stored friendly name (JetStream-only).

        Verified reply format on hardware: ``NAM`` + the 3-digit device number +
        the space-padded name, e.g. ``NAM002GAME RM E-1-E GAME CANS   `` (then
        CRLF, stripped by the reader). We match the specific device so an
        unrelated NAM can't fulfill this request, and strip both the ``NAM``
        prefix and the echoed index before returning the name.

        Returns None if the device is unconfigured: on real hardware an
        unconfigured index stays silent, so the request just times out. A short
        `timeout` keeps a discovery scan over empty slots from dragging.
        """
        self._validate_load_idx(idx)
        expected = f"{NAM_PREFIX}{idx:03d}"

        def matches(line: str) -> bool:
            return line.startswith(expected)

        try:
            response = await self._sendrecv(
                f"^N{idx:03d}", matches=matches, timeout=timeout
            )
        except ProtocolError:
            return None
        name = response[len(expected) :].strip()
        return name or None

    async def scan_device_names(
        self,
        *,
        start: int = 1,
        end: int = JETSTREAM_MAX_LOADS,
        timeout: float = SCAN_PROBE_TIMEOUT,
    ) -> dict[int, str]:
        """Discover configured devices by querying ``^N`` across a range.

        JetStream has no "list devices" command, so we probe each index. A
        configured device replies with its name; an unconfigured one is silent
        and times out at `timeout` (kept short so empty slots don't stall the
        scan). Returns {device_idx: name} for every index that responded with a
        non-empty name. Devices with an empty stored name are skipped (no useful
        name to import); use a config file to capture those.
        """
        found: dict[int, str] = {}
        for idx in range(start, end + 1):
            name = await self.get_device_name(idx, timeout=timeout)
            if name:
                found[idx] = name
        return found

    async def increment_load(self, idx: int, value: int = 1, rate: int = 0) -> None:
        self._validate_load_idx(idx)
        await self._send(f"inc {idx} {value} {rate}")

    async def decrement_load(self, idx: int, value: int = 1, rate: int = 0) -> None:
        self._validate_load_idx(idx)
        await self._send(f"dec {idx} {value} {rate}")

    async def ping(self) -> bool:
        """Send Ping; return True if the bridge responds with Hello."""

        def matches(line: str) -> bool:
            return line == HELLO_RESPONSE

        try:
            response = await self._sendrecv("Ping", matches=matches)
        except ProtocolError:
            return False
        return response == HELLO_RESPONSE

    def _dispatch(self, line: str) -> None:
        # Push events on JetStream:
        #   DEV ddd ll          len 8
        #   ACT ddd bb T/P/R    len 9
        #   SCN sss 1/0         len 7
        # Each may also be the response to a pending command, so fire callback
        # AND attempt to fulfill the pending request.
        if len(line) == DEV_LEN and line.startswith(DEV_PREFIX):
            self._dispatch_load_event(line)
            self._try_fulfill(line)
            return
        if len(line) == ACT_LEN and line.startswith(ACT_PREFIX):
            self._dispatch_switch_event(line)
            self._try_fulfill(line)
            return
        if len(line) == SCN_LEN and line.startswith(SCN_PREFIX):
            self._dispatch_scene_event(line)
            self._try_fulfill(line)
            return
        # Direct responses: NAMxxx, Hello, bulk hex (^G/^H).
        self._try_fulfill(line)

    def _dispatch_load_event(self, line: str) -> None:
        cb = self._load_event_cb
        if cb is None:
            return
        try:
            idx = int(line[3:6])
            level = int(line[6:8])
        except ValueError:
            _LOGGER.warning("Malformed DEV event: %r", line)
            return
        try:
            cb(LoadEvent(idx=idx, level=level))
        except Exception:
            _LOGGER.exception("Load event callback failed for %r", line)

    def _dispatch_switch_event(self, line: str) -> None:
        cb = self._switch_event_cb
        if cb is None:
            return
        try:
            idx = int(line[3:6])
            button = int(line[6:8])
        except ValueError:
            _LOGGER.warning("Malformed ACT event: %r", line)
            return
        action = _ACTION_MAP.get(line[8])
        if action is None:
            _LOGGER.warning("Unknown ACT action %r in %r", line[8], line)
            return
        try:
            cb(SwitchEvent(idx=idx, action=action, button=button))
        except Exception:
            _LOGGER.exception("Switch event callback failed for %r", line)

    def _dispatch_scene_event(self, line: str) -> None:
        cb = self._scene_event_cb
        if cb is None:
            return
        try:
            idx = int(line[3:6])
        except ValueError:
            _LOGGER.warning("Malformed SCN event: %r", line)
            return
        state_char = line[6]
        if state_char not in ("0", "1"):
            _LOGGER.warning("Unknown SCN state %r in %r", state_char, line)
            return
        try:
            cb(SceneEvent(idx=idx, active=state_char == "1"))
        except Exception:
            _LOGGER.exception("Scene event callback failed for %r", line)

    def _validate_load_idx(self, idx: int) -> None:
        if not 1 <= idx <= JETSTREAM_MAX_LOADS:
            raise ValueError(f"load idx must be 1-{JETSTREAM_MAX_LOADS}, got {idx}")

    def _validate_switch_idx(self, idx: int) -> None:
        if not 1 <= idx <= JETSTREAM_MAX_SWITCHES:
            raise ValueError(f"switch idx must be 1-{JETSTREAM_MAX_SWITCHES}, got {idx}")

    def _validate_scene_idx(self, idx: int) -> None:
        if not 1 <= idx <= JETSTREAM_MAX_SCENES:
            raise ValueError(f"scene idx must be 1-{JETSTREAM_MAX_SCENES}, got {idx}")

    def _validate_button_idx(self, button: int) -> None:
        if not 1 <= button <= JETSTREAM_MAX_BUTTONS_PER_SWITCH:
            raise ValueError(
                f"button must be 1-{JETSTREAM_MAX_BUTTONS_PER_SWITCH}, got {button}"
            )
