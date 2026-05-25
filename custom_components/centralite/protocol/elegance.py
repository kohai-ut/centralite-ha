"""EleganceProtocol: RS-232 protocol for Centralite Elegance bridges.

Implements the third-party protocol documented in
docs/protocols/elegance-rs232-protocol.pdf (firmware 07.00, 2006).
Single-system command set (^A-^M). Multi-system (lowercase commands
with a board prefix) is a future extension.

Wire format: 19200 baud 8N1 by default. ASCII commands prefixed with ^.
Responses and spontaneous push events are CR-terminated when the bridge's
"send CR after data" Customer Option (bit #6) is enabled. That option
MUST be enabled in the Elegance Programming Software for the integration
to work.

Push-event shapes:
    P s nnn       len 5  - switch press
    R s nnn       len 5  - switch release
    ^K nnn ll     len 7  - load level change

The ^K push event collides in prefix with the ^K clock-get response (14
BCD chars); length disambiguates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import LoadEvent, SwitchEvent
from ._base import _BaseSerialProtocol
from .common import (
    MAX_LOADS,
    MAX_SWITCHES,
    ProtocolError,
    parse_load_bitmap,
    parse_switch_bitmap,
)

_LOGGER = logging.getLogger(__name__)

PRESS_RELEASE_DELAY = 0.1
MAX_SCENES = 256


def encode_bcd_clock(dt: datetime) -> str:
    """Encode datetime as ssmmhhwwddmmyy (14 ASCII digits).

    Day-of-week per protocol: 1=Sunday ... 7=Saturday. Python weekday()
    is Mon=0..Sun=6, so dow = (weekday + 1) % 7 + 1.
    Year is 2 digits, assumes 21st century on decode.
    """
    dow = (dt.weekday() + 1) % 7 + 1
    return (
        f"{dt.second:02d}"
        f"{dt.minute:02d}"
        f"{dt.hour:02d}"
        f"{dow:02d}"
        f"{dt.day:02d}"
        f"{dt.month:02d}"
        f"{dt.year % 100:02d}"
    )


def parse_bcd_clock(s: str) -> datetime:
    """Parse a 14-digit BCD clock response into a naive datetime (assumes 21st century)."""
    if len(s) != 14:
        raise ProtocolError(f"Bad BCD clock length: {s!r}")
    try:
        return datetime(
            2000 + int(s[12:14]),
            int(s[10:12]),
            int(s[8:10]),
            int(s[4:6]),
            int(s[2:4]),
            int(s[0:2]),
        )
    except ValueError as e:
        raise ProtocolError(f"Bad BCD clock content: {s!r}") from e


class EleganceProtocol(_BaseSerialProtocol):
    """Async Elegance bridge protocol."""

    @property
    def max_loads(self) -> int:
        return MAX_LOADS

    @property
    def max_switches(self) -> int:
        return MAX_SWITCHES

    @property
    def supports_clock(self) -> bool:
        return True

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
        self._validate_load_idx(idx)
        response = await self._sendrecv(f"^F{idx:03d}")
        try:
            return int(response.strip())
        except ValueError as e:
            raise ProtocolError(f"Bad ^F response: {response!r}") from e

    async def get_all_load_states(self) -> dict[int, bool]:
        response = await self._sendrecv("^G")
        return parse_load_bitmap(response.strip())

    async def activate_scene(self, idx: int) -> None:
        self._validate_scene_idx(idx)
        await self._send(f"^C{idx:03d}")

    async def deactivate_scene(self, idx: int) -> None:
        self._validate_scene_idx(idx)
        await self._send(f"^D{idx:03d}")

    async def press_switch(self, idx: int, *, auto_release: bool = True) -> None:
        self._validate_switch_idx(idx)
        await self._send(f"^I{idx:03d}")
        if auto_release:
            await asyncio.sleep(PRESS_RELEASE_DELAY)
            await self.release_switch(idx)

    async def release_switch(self, idx: int) -> None:
        self._validate_switch_idx(idx)
        await self._send(f"^J{idx:03d}")

    async def get_all_switch_states(self) -> dict[int, bool]:
        response = await self._sendrecv("^H")
        return parse_switch_bitmap(response.strip())

    async def get_clock(self) -> datetime:
        response = await self._sendrecv("^K")
        return parse_bcd_clock(response.strip())

    async def set_clock(self, dt: datetime) -> None:
        await self._send(f"^L{encode_bcd_clock(dt)}")

    def _dispatch(self, line: str) -> None:
        if len(line) == 5 and line[0] in ("P", "R"):
            self._dispatch_switch_event(line)
            return
        if len(line) == 7 and line.startswith("^K"):
            self._dispatch_load_event(line)
            return
        self._try_fulfill(line)

    def _dispatch_switch_event(self, line: str) -> None:
        cb = self._switch_event_cb
        if cb is None:
            return
        try:
            board = int(line[1])
            idx = int(line[2:5])
        except ValueError:
            _LOGGER.warning("Malformed switch event: %r", line)
            return
        action = "press" if line[0] == "P" else "release"
        try:
            cb(SwitchEvent(idx=idx, action=action, board=board))
        except Exception:
            _LOGGER.exception("Switch event callback failed for %r", line)

    def _dispatch_load_event(self, line: str) -> None:
        cb = self._load_event_cb
        if cb is None:
            return
        try:
            idx = int(line[2:5])
            level = int(line[5:7])
        except ValueError:
            _LOGGER.warning("Malformed load event: %r", line)
            return
        try:
            cb(LoadEvent(idx=idx, level=level))
        except Exception:
            _LOGGER.exception("Load event callback failed for %r", line)

    def _validate_load_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_LOADS:
            raise ValueError(f"load idx must be 1-{MAX_LOADS}, got {idx}")

    def _validate_switch_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_SWITCHES:
            raise ValueError(f"switch idx must be 1-{MAX_SWITCHES}, got {idx}")

    def _validate_scene_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_SCENES:
            raise ValueError(f"scene idx must be 1-{MAX_SCENES}, got {idx}")
