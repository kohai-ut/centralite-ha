"""Shared async serial machinery for the Centralite protocol implementations.

EleganceProtocol and JetStreamProtocol both subclass _BaseSerialProtocol
to inherit:
- serialx transport setup and teardown
- asyncio reader task with CR-framed line splitting
- request/response correlation via a single pending Future + matcher
- callback storage for push events

Subclasses override _dispatch to detect their system's push-event shapes,
add their command-specific methods, and set capability flags.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from . import (
    CentraliteProtocol,
    DisconnectCallback,
    LoadEventCallback,
    SceneEventCallback,
    SwitchEventCallback,
)
from .common import ProtocolError

_LOGGER = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 19200
COMMAND_TIMEOUT = 2.0
READ_MAX_BUFFER = 100
CR = 0x0D
LF = 0x0A

# Inter-command delay: a brief pause after each write before releasing the
# transmit lock. The v1 JetStream integration discovered that without this,
# back-to-back commands to the JetStream bridge would return responses
# correlated to only the LAST command (the bridge or the USB-to-serial
# adapter loses the earlier ones). Possibly USB-adapter-specific, possibly
# a JetStream firmware quirk; the user couldn't isolate it but 100ms made
# the symptom go away. Subclasses override the default per system.
DEFAULT_INTER_COMMAND_DELAY = 0.0

TransportFactory = Callable[
    [str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]
LineMatcher = Callable[[str], bool]


async def _default_transport(
    url: str, baudrate: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a serial port via serialx (the HA-blessed async serial library)."""
    import serialx

    return await serialx.open_serial_connection(
        url, baudrate=baudrate, bytesize=8, parity="N", stopbits=1
    )


def _match_any(_line: str) -> bool:
    return True


class _BaseSerialProtocol(CentraliteProtocol):
    """Shared serial transport + reader-loop + sendrecv machinery."""

    inter_command_delay: float = DEFAULT_INTER_COMMAND_DELAY

    def __init__(
        self,
        url: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        transport_factory: TransportFactory | None = None,
        inter_command_delay: float | None = None,
    ) -> None:
        self._url = url
        self._baudrate = baudrate
        self._transport_factory = transport_factory or _default_transport
        if inter_command_delay is not None:
            self.inter_command_delay = inter_command_delay
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[asyncio.Future[str], LineMatcher] | None = None
        self._load_event_cb: LoadEventCallback | None = None
        self._switch_event_cb: SwitchEventCallback | None = None
        self._scene_event_cb: SceneEventCallback | None = None
        self._disconnect_cb: DisconnectCallback | None = None
        # Set when the reader loop ends unexpectedly. Long-running loops (e.g. a
        # device-name scan) check this to abort instead of treating every probe
        # as an unconfigured-and-silent slot once the link is gone.
        self._lost = False

    async def connect(self) -> None:
        self._lost = False
        self._reader, self._writer = await self._transport_factory(self._url, self._baudrate)
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"centralite-reader-{type(self).__name__}"
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

    def set_scene_event_callback(self, cb: SceneEventCallback) -> None:
        self._scene_event_cb = cb

    def set_disconnect_callback(self, cb: DisconnectCallback) -> None:
        self._disconnect_cb = cb

    async def _send(self, command: str) -> None:
        if self._writer is None:
            raise ProtocolError("Not connected")
        async with self._lock:
            _LOGGER.debug("send: %r", command)
            self._writer.write(command.encode("ascii"))
            await self._writer.drain()
            if self.inter_command_delay > 0:
                await asyncio.sleep(self.inter_command_delay)

    async def _sendrecv(
        self,
        command: str,
        *,
        matches: LineMatcher | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> str:
        """Send a command and wait for a response line.

        If `matches` is provided, only a line that satisfies it fulfills the
        wait; other lines pass through to push-event handling. If `matches`
        is None, the first non-push-event line (anything _dispatch routes to
        _try_fulfill) wins.
        """
        if self._writer is None:
            raise ProtocolError("Not connected")
        matcher = matches or _match_any
        async with self._lock:
            self._pending = (asyncio.get_running_loop().create_future(), matcher)
            try:
                _LOGGER.debug("sendrecv: %r", command)
                self._writer.write(command.encode("ascii"))
                await self._writer.drain()
                try:
                    response = await asyncio.wait_for(self._pending[0], timeout=timeout)
                except asyncio.TimeoutError as e:
                    raise ProtocolError(f"Timeout waiting for response to {command!r}") from e
                _LOGGER.debug("recv: %r", response)
                if self.inter_command_delay > 0:
                    await asyncio.sleep(self.inter_command_delay)
                return response
            finally:
                self._pending = None

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line = await self._read_line()
            except asyncio.CancelledError:
                # Intentional shutdown via disconnect(); not a fault.
                return
            except (asyncio.IncompleteReadError, OSError, ConnectionError) as e:
                _LOGGER.error("Serial reader error; connection lost: %s", e)
                self._notify_disconnect(e)
                return
            except Exception:
                _LOGGER.exception("Unexpected reader error")
                continue
            if line:
                self._dispatch(line)

    def _notify_disconnect(self, error: Exception | None) -> None:
        """Tell anyone waiting that the link dropped.

        Fails the in-flight request (so a command issued just as the cable was
        pulled fails fast instead of waiting the full COMMAND_TIMEOUT) and fires
        the disconnect callback so the coordinator can mark entities
        unavailable.
        """
        self._lost = True
        if self._pending is not None:
            future, _ = self._pending
            if not future.done():
                future.set_exception(ProtocolError(f"Connection lost: {error}"))
        cb = self._disconnect_cb
        if cb is not None:
            try:
                cb(error)
            except Exception:
                _LOGGER.exception("Disconnect callback failed")

    async def _read_line(self) -> str:
        assert self._reader is not None
        buf = bytearray()
        while True:
            byte = await self._reader.readexactly(1)
            if byte[0] == CR:
                return buf.decode("ascii", errors="replace")
            if byte[0] == LF:
                # JetStream terminates lines with CRLF (Elegance uses CR only).
                # CR already returned the line, so skip the trailing LF rather
                # than let it become the first byte of the next line and corrupt
                # length/prefix-based dispatch of the following event.
                continue
            buf.append(byte[0])
            if len(buf) >= READ_MAX_BUFFER:
                _LOGGER.warning(
                    "Read buffer hit %d bytes without CR: %r", READ_MAX_BUFFER, bytes(buf)
                )
                return buf.decode("ascii", errors="replace")

    def _dispatch(self, line: str) -> None:
        """Route a line. Subclasses override to detect push events first."""
        self._try_fulfill(line)

    def _try_fulfill(self, line: str) -> bool:
        """Fulfill the pending request if its matcher accepts the line."""
        if self._pending is None:
            return False
        future, matches = self._pending
        if future.done():
            return False
        if matches(line):
            future.set_result(line)
            return True
        return False
