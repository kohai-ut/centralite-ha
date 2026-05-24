"""EleganceProtocol: RS-232 protocol implementation for Centralite Elegance bridges.

Implements the third-party protocol documented in
docs/protocols/elegance-rs232-protocol.pdf (firmware 07.00, 2006).
Single-system command set (^A-^M). Multi-system support (lowercase
commands with board prefix) is a future extension.

Wire format: 19200 baud 8N1 by default. ASCII commands prefixed with ^.
Responses and spontaneous push events are CR-terminated when the bridge's
"send CR after data" Customer Option (bit #6) is enabled. The reader
strips the CR; that option must be enabled in the Elegance Programming
Software for the integration to work.

The reader loop dispatches each line by shape:
    len 5 starting with P or R     -> switch press/release push event
    len 7 starting with ^K         -> load level change push event
    everything else                -> command response (routed to pending future)

Distinguishing ^K push (7 chars) from ^K clock response (14 BCD chars) is
length-based, even though the prefix collides.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from . import (
    CentraliteProtocol,
    LoadEvent,
    LoadEventCallback,
    SwitchEvent,
    SwitchEventCallback,
)
from .common import (
    MAX_LOADS,
    MAX_SWITCHES,
    ProtocolError,
    parse_load_bitmap,
    parse_switch_bitmap,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 19200
COMMAND_TIMEOUT = 2.0
PRESS_RELEASE_DELAY = 0.1
READ_MAX_BUFFER = 100
CR = 0x0D

MAX_SCENES = 256


def encode_bcd_clock(dt: datetime) -> str:
    """Encode datetime as ssmmhhwwddmmyy (14 ASCII digits, BCD-style).

    Day of week per protocol: 1=Sunday ... 7=Saturday.
    Year is 2 digits (00-99); the encoding assumes 21st century.
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
        ss = int(s[0:2])
        mm = int(s[2:4])
        hh = int(s[4:6])
        dd = int(s[8:10])
        mo = int(s[10:12])
        yy = int(s[12:14])
        return datetime(2000 + yy, mo, dd, hh, mm, ss)
    except ValueError as e:
        raise ProtocolError(f"Bad BCD clock content: {s!r}") from e


# Transport factory type: returns (reader, writer) when called.
# Default uses serialx; tests inject a fake.
TransportFactory = "Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]"


async def _default_transport(
    url: str, baudrate: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    import serialx

    return await serialx.open_serial_connection(
        url, baudrate=baudrate, bytesize=8, parity="N", stopbits=1
    )


class EleganceProtocol(CentraliteProtocol):
    """Async Elegance bridge protocol."""

    def __init__(
        self,
        url: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self._url = url
        self._baudrate = baudrate
        self._transport_factory = transport_factory or _default_transport
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pending_response: asyncio.Future[str] | None = None
        self._load_event_cb: LoadEventCallback | None = None
        self._switch_event_cb: SwitchEventCallback | None = None

    @property
    def max_loads(self) -> int:
        return MAX_LOADS

    @property
    def max_switches(self) -> int:
        return MAX_SWITCHES

    async def connect(self) -> None:
        self._reader, self._writer = await self._transport_factory(self._url, self._baudrate)
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="centralite-elegance-reader"
        )

    async def disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None

    def set_load_event_callback(self, cb: LoadEventCallback) -> None:
        self._load_event_cb = cb

    def set_switch_event_callback(self, cb: SwitchEventCallback) -> None:
        self._switch_event_cb = cb

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

    async def _send(self, command: str) -> None:
        if self._writer is None:
            raise ProtocolError("Not connected")
        async with self._lock:
            _LOGGER.debug("send: %r", command)
            self._writer.write(command.encode("ascii"))
            await self._writer.drain()

    async def _sendrecv(self, command: str, *, timeout: float = COMMAND_TIMEOUT) -> str:
        if self._writer is None:
            raise ProtocolError("Not connected")
        async with self._lock:
            self._pending_response = asyncio.get_running_loop().create_future()
            try:
                _LOGGER.debug("sendrecv: %r", command)
                self._writer.write(command.encode("ascii"))
                await self._writer.drain()
                try:
                    response = await asyncio.wait_for(self._pending_response, timeout=timeout)
                except asyncio.TimeoutError as e:
                    raise ProtocolError(f"Timeout waiting for response to {command!r}") from e
                _LOGGER.debug("recv: %r", response)
                return response
            finally:
                self._pending_response = None

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line = await self._read_line()
            except asyncio.CancelledError:
                return
            except (asyncio.IncompleteReadError, OSError, ConnectionError):
                _LOGGER.exception("Reader error; ending loop")
                return
            except Exception:
                _LOGGER.exception("Unexpected reader error")
                continue
            if line:
                self._dispatch(line)

    async def _read_line(self) -> str:
        assert self._reader is not None
        buf = bytearray()
        while True:
            byte = await self._reader.readexactly(1)
            if byte[0] == CR:
                return buf.decode("ascii", errors="replace")
            buf.append(byte[0])
            if len(buf) >= READ_MAX_BUFFER:
                _LOGGER.warning("Read buffer hit %d bytes without CR: %r", READ_MAX_BUFFER, bytes(buf))
                return buf.decode("ascii", errors="replace")

    def _dispatch(self, line: str) -> None:
        if len(line) == 5 and line[0] in ("P", "R"):
            self._dispatch_switch_event(line)
            return
        if len(line) == 7 and line.startswith("^K"):
            self._dispatch_load_event(line)
            return
        self._fulfill_response(line)

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

    def _fulfill_response(self, line: str) -> None:
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_result(line)
        else:
            _LOGGER.debug("Unsolicited response (no pending request): %r", line)

    def _validate_load_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_LOADS:
            raise ValueError(f"load idx must be 1-{MAX_LOADS}, got {idx}")

    def _validate_switch_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_SWITCHES:
            raise ValueError(f"switch idx must be 1-{MAX_SWITCHES}, got {idx}")

    def _validate_scene_idx(self, idx: int) -> None:
        if not 1 <= idx <= MAX_SCENES:
            raise ValueError(f"scene idx must be 1-{MAX_SCENES}, got {idx}")
