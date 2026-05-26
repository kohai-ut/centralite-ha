"""Tests for EleganceProtocol with a fake serial transport.

Pytest-compatible (asyncio_mode = auto in pyproject.toml). Also runnable as a
standalone script: `python tests/test_protocol_elegance.py` — useful for local
smoke testing without pytest installed.
"""

import asyncio
from datetime import datetime

from custom_components.centralite.protocol import LoadEvent, SwitchEvent
from custom_components.centralite.protocol.common import ProtocolError
from custom_components.centralite.protocol.elegance import (
    EleganceProtocol,
    encode_bcd_clock,
    parse_bcd_clock,
)


class _FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None: ...
    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...


async def _make_protocol():
    reader = asyncio.StreamReader()
    writer = _FakeWriter()

    async def factory(url, baudrate):
        return reader, writer

    p = EleganceProtocol("test://", transport_factory=factory)
    await p.connect()
    return p, reader, writer


async def _feed_after(reader, data, delay=0.01):
    await asyncio.sleep(delay)
    reader.feed_data(data)


async def _expect_raises_async(exc_type, coro):
    try:
        await coro
    except exc_type:
        return
    raise AssertionError(f"Expected {exc_type.__name__}")


def _expect_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"Expected {exc_type.__name__}")


# --- Command-sends-correct-bytes tests ---


async def test_elegance_has_no_inter_command_delay_by_default():
    """Elegance v1 did not need the delay workaround that JetStream did."""
    p, _r, _w = await _make_protocol()
    try:
        assert p.inter_command_delay == 0.0
    finally:
        await p.disconnect()


async def test_activate_load_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.activate_load(5)
        assert bytes(w.buf) == b"^A005"
    finally:
        await p.disconnect()


async def test_deactivate_load_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.deactivate_load(192)
        assert bytes(w.buf) == b"^B192"
    finally:
        await p.disconnect()


async def test_activate_scene_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.activate_scene(4)
        assert bytes(w.buf) == b"^C004"
    finally:
        await p.disconnect()


async def test_deactivate_scene_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.deactivate_scene(99)
        assert bytes(w.buf) == b"^D099"
    finally:
        await p.disconnect()


async def test_set_load_level_with_rate():
    p, _r, w = await _make_protocol()
    try:
        await p.set_load_level(3, 75, 5)
        assert bytes(w.buf) == b"^E0037505"
    finally:
        await p.disconnect()


async def test_set_load_level_default_rate():
    p, _r, w = await _make_protocol()
    try:
        await p.set_load_level(1, 50)
        assert bytes(w.buf) == b"^E0015000"
    finally:
        await p.disconnect()


# --- Request/response tests ---


async def test_get_load_level_returns_parsed_int():
    p, reader, w = await _make_protocol()
    try:
        task = asyncio.create_task(_feed_after(reader, b"42\r"))
        level = await p.get_load_level(10)
        assert level == 42
        assert bytes(w.buf) == b"^F010"
        await task
    finally:
        await p.disconnect()


async def test_get_all_load_states_parses_bitmap():
    p, reader, w = await _make_protocol()
    try:
        response = b"010100" + b"000000" * 7 + b"\r"
        task = asyncio.create_task(_feed_after(reader, response))
        state = await p.get_all_load_states()
        assert state[1] is True
        assert state[9] is True
        assert sum(state.values()) == 2
        assert bytes(w.buf) == b"^G"
        await task
    finally:
        await p.disconnect()


async def test_get_all_switch_states_parses_bitmap():
    p, reader, w = await _make_protocol()
    try:
        response = b"0100" + b"0000" * 23 + b"\r"
        task = asyncio.create_task(_feed_after(reader, response))
        state = await p.get_all_switch_states()
        assert state[1] is True
        assert sum(state.values()) == 1
        assert bytes(w.buf) == b"^H"
        await task
    finally:
        await p.disconnect()


# --- Push event tests ---


async def test_push_load_event_fires_callback():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_load_event_callback(events.append)
        reader.feed_data(b"^K00150\r")
        await asyncio.sleep(0.02)
        assert events == [LoadEvent(idx=1, level=50)]
    finally:
        await p.disconnect()


async def test_push_switch_event_press():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_switch_event_callback(events.append)
        reader.feed_data(b"P0044\r")
        await asyncio.sleep(0.02)
        assert events == [SwitchEvent(idx=44, action="press", board=0)]
    finally:
        await p.disconnect()


async def test_push_switch_event_release():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_switch_event_callback(events.append)
        reader.feed_data(b"R0046\r")
        await asyncio.sleep(0.02)
        assert events == [SwitchEvent(idx=46, action="release", board=0)]
    finally:
        await p.disconnect()


async def test_push_event_interleaved_with_command_response():
    """A push event during a pending command should not fulfill the response future."""
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_load_event_callback(events.append)

        async def feed_event_then_response():
            await asyncio.sleep(0.01)
            reader.feed_data(b"^K00250\r")
            await asyncio.sleep(0.01)
            reader.feed_data(b"77\r")

        task = asyncio.create_task(feed_event_then_response())
        level = await p.get_load_level(10)
        assert level == 77
        assert events == [LoadEvent(idx=2, level=50)]
        await task
    finally:
        await p.disconnect()


# --- Switch quirk: auto-release ---


async def test_press_switch_auto_releases_by_default():
    p, _r, w = await _make_protocol()
    try:
        await p.press_switch(7)
        assert bytes(w.buf) == b"^I007^J007"
    finally:
        await p.disconnect()


async def test_press_switch_can_skip_auto_release():
    p, _r, w = await _make_protocol()
    try:
        await p.press_switch(7, auto_release=False)
        assert bytes(w.buf) == b"^I007"
    finally:
        await p.disconnect()


# --- Validation tests ---


async def test_validate_load_idx_too_low():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.activate_load(0))
    finally:
        await p.disconnect()


async def test_validate_load_idx_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.activate_load(193))
    finally:
        await p.disconnect()


async def test_validate_set_load_level_out_of_range():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.set_load_level(1, 100))
        await _expect_raises_async(ValueError, p.set_load_level(1, -1))
        await _expect_raises_async(ValueError, p.set_load_level(1, 50, 32))
    finally:
        await p.disconnect()


async def test_validate_switch_idx_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.press_switch(385, auto_release=False))
    finally:
        await p.disconnect()


async def test_validate_scene_idx_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.activate_scene(257))
    finally:
        await p.disconnect()


# --- Timeout test ---


async def test_command_timeout_raises_protocol_error():
    p, _r, _w = await _make_protocol()
    try:
        # Patch the per-command timeout via a fresh sendrecv call.
        # Don't feed any response. Should time out within 2s default.
        async def attempt():
            await p.get_load_level(1)

        await _expect_raises_async(ProtocolError, attempt())
    finally:
        await p.disconnect()


# --- Capability flags ---


async def test_elegance_supports_bulk_query():
    """Elegance has ^G/^H, so the coordinator primes + polls it."""
    p, _r, _w = await _make_protocol()
    try:
        assert p.supports_bulk_query is True
    finally:
        await p.disconnect()


# --- Reader death / disconnect notification (shared _base behavior) ---


async def test_reader_death_fires_disconnect_callback():
    """When the serial stream errors, the disconnect callback must fire once."""
    p, reader, _w = await _make_protocol()
    try:
        seen: list[Exception | None] = []
        p.set_disconnect_callback(seen.append)
        # feed_eof makes the next readexactly raise IncompleteReadError.
        reader.feed_eof()
        await asyncio.sleep(0.02)
        assert len(seen) == 1
    finally:
        await p.disconnect()


async def test_reader_death_fails_pending_request_fast():
    """A command in flight when the link drops must fail immediately, not hang."""
    p, reader, _w = await _make_protocol()
    try:
        async def kill_after():
            await asyncio.sleep(0.01)
            reader.feed_eof()

        task = asyncio.create_task(kill_after())
        # No response is fed; without the disconnect path this would wait the
        # full 2s COMMAND_TIMEOUT. It should raise well under that.
        start = asyncio.get_event_loop().time()
        await _expect_raises_async(ProtocolError, p.get_load_level(1))
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0, f"expected fast failure, took {elapsed:.2f}s"
        await task
    finally:
        await p.disconnect()


async def test_intentional_disconnect_does_not_fire_callback():
    """disconnect() cancels the reader; that is not a fault and must stay quiet."""
    p, _r, _w = await _make_protocol()
    seen: list[Exception | None] = []
    p.set_disconnect_callback(seen.append)
    await p.disconnect()
    await asyncio.sleep(0.02)
    assert seen == []


# --- BCD clock helpers (sync) ---


def test_encode_bcd_clock():
    # 2024-03-15 (Friday) 14:30:45.
    # Python weekday: Fri=4 → dow = (4+1)%7+1 = 6.
    dt = datetime(2024, 3, 15, 14, 30, 45)
    assert encode_bcd_clock(dt) == "453014" + "06" + "150324"


def test_parse_bcd_clock():
    dt = parse_bcd_clock("45301406150324")
    assert dt == datetime(2024, 3, 15, 14, 30, 45)


def test_bcd_roundtrip():
    dt = datetime(2026, 5, 24, 14, 12, 33)
    decoded = parse_bcd_clock(encode_bcd_clock(dt))
    assert (
        decoded.year,
        decoded.month,
        decoded.day,
        decoded.hour,
        decoded.minute,
        decoded.second,
    ) == (2026, 5, 24, 14, 12, 33)


def test_parse_bcd_clock_bad_length():
    _expect_raises(ProtocolError, parse_bcd_clock, "0000")


def test_dow_encoding_sunday_is_one():
    # Python: Sun=6 → dow = (6+1)%7+1 = 1
    dt = datetime(2024, 3, 17, 0, 0, 0)  # Sunday
    assert encode_bcd_clock(dt)[6:8] == "01"


def test_dow_encoding_saturday_is_seven():
    # Python: Sat=5 → dow = (5+1)%7+1 = 7
    dt = datetime(2024, 3, 16, 0, 0, 0)  # Saturday
    assert encode_bcd_clock(dt)[6:8] == "07"


async def test_inbound_line_logged_at_debug():
    """Every received line is logged (rx:) so the raw bridge traffic is visible
    when debug logging is on — the key window for verifying, e.g., which index
    a physical keypad reports. Uses a manual handler (not the caplog fixture)
    so this stays runnable under the standalone smoke-test runner."""
    import logging

    p, reader, _w = await _make_protocol()
    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logger = logging.getLogger("custom_components.centralite.protocol._base")
    prev = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        reader.feed_data(b"P044\r")  # a physical switch-press push event
        await asyncio.sleep(0.02)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)
        await p.disconnect()
    assert any("rx:" in m and "P044" in m for m in seen), seen


# --- Standalone smoke-test runner ---

if __name__ == "__main__":
    import sys
    import traceback

    g = dict(globals())
    async_tests = sorted(
        (n, t) for n, t in g.items()
        if n.startswith("test_") and asyncio.iscoroutinefunction(t)
    )
    sync_tests = sorted(
        (n, t) for n, t in g.items()
        if n.startswith("test_") and not asyncio.iscoroutinefunction(t)
    )

    passed = 0
    failed: list[tuple[str, str]] = []

    for name, t in async_tests:
        try:
            asyncio.run(t())
        except Exception:
            failed.append((name, traceback.format_exc()))
        else:
            passed += 1
            print(f"OK  {name}")

    for name, t in sync_tests:
        try:
            t()
        except Exception:
            failed.append((name, traceback.format_exc()))
        else:
            passed += 1
            print(f"OK  {name}")

    print()
    print(f"Passed: {passed}, Failed: {len(failed)}")
    if failed:
        for name, tb in failed:
            print(f"\n--- FAIL: {name} ---")
            print(tb)
        sys.exit(1)
