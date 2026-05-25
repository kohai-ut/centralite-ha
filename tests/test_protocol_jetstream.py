"""Tests for JetStreamProtocol with a fake serial transport.

Pytest-compatible (asyncio_mode = auto). Also runnable as a standalone
script for local smoke testing without pytest installed.
"""

import asyncio

from custom_components.centralite.protocol import (
    LoadEvent,
    SceneEvent,
    SwitchEvent,
)
from custom_components.centralite.protocol.common import ProtocolError
from custom_components.centralite.protocol.jetstream import JetStreamProtocol


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

    p = JetStreamProtocol("test://", transport_factory=factory)
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


# --- Capability flags ---


async def test_capability_flags():
    p, _r, _w = await _make_protocol()
    try:
        assert p.supports_device_name_query is True
        assert p.supports_scene_push is True
    finally:
        await p.disconnect()


async def test_jetstream_has_inter_command_delay():
    """v1 discovered back-to-back commands corrupted responses; v2 preserves the workaround."""
    from custom_components.centralite.protocol.jetstream import (
        JETSTREAM_INTER_COMMAND_DELAY,
        JetStreamProtocol,
    )
    p = JetStreamProtocol("test://")
    assert p.inter_command_delay == JETSTREAM_INTER_COMMAND_DELAY
    assert p.inter_command_delay > 0


async def test_jetstream_inter_command_delay_can_be_overridden():
    p, _r, _w = await _make_protocol()
    try:
        # _make_protocol uses the default factory; verify the default
        assert p.inter_command_delay == 0.1
    finally:
        await p.disconnect()

    # Now construct with an override
    from custom_components.centralite.protocol.jetstream import JetStreamProtocol

    async def factory(url, baudrate):
        return asyncio.StreamReader(), _FakeWriter()

    p2 = JetStreamProtocol("test://", transport_factory=factory, inter_command_delay=0.5)
    assert p2.inter_command_delay == 0.5


async def test_inter_command_delay_actually_applies():
    """A ~100ms delay should be observable in elapsed time when sending two commands."""
    import time

    p, _r, w = await _make_protocol()
    try:
        # Two sends should take at least 2 * 0.1s = 0.2s due to the delay
        start = time.monotonic()
        await p.activate_load(1)
        await p.activate_load(2)
        elapsed = time.monotonic() - start
        # Allow a generous lower bound (some implementations may have small clock skew)
        assert elapsed >= 0.18, f"expected >=0.18s elapsed, got {elapsed:.3f}s"
        assert bytes(w.buf) == b"^A001^A002"
    finally:
        await p.disconnect()


# --- Send-only commands ---


async def test_activate_load_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.activate_load(5)
        assert bytes(w.buf) == b"^A005"
    finally:
        await p.disconnect()


async def test_activate_scene_sends_correct_bytes():
    p, _r, w = await _make_protocol()
    try:
        await p.activate_scene(7)
        assert bytes(w.buf) == b"^C007"
    finally:
        await p.disconnect()


async def test_tap_switch_default_button():
    p, _r, w = await _make_protocol()
    try:
        await p.tap_switch(44)
        assert bytes(w.buf) == b"^T04401"
    finally:
        await p.disconnect()


async def test_tap_switch_specific_button():
    p, _r, w = await _make_protocol()
    try:
        await p.tap_switch(44, button=3)
        assert bytes(w.buf) == b"^T04403"
    finally:
        await p.disconnect()


async def test_press_switch_auto_releases():
    p, _r, w = await _make_protocol()
    try:
        await p.press_switch(44, button=2)
        assert bytes(w.buf) == b"^P04402^R04402"
    finally:
        await p.disconnect()


async def test_press_switch_can_skip_auto_release():
    p, _r, w = await _make_protocol()
    try:
        await p.press_switch(44, button=2, auto_release=False)
        assert bytes(w.buf) == b"^P04402"
    finally:
        await p.disconnect()


async def test_increment_load():
    p, _r, w = await _make_protocol()
    try:
        await p.increment_load(5, value=10, rate=2)
        assert bytes(w.buf) == b"inc 5 10 2"
    finally:
        await p.disconnect()


async def test_decrement_load():
    p, _r, w = await _make_protocol()
    try:
        await p.decrement_load(5, value=10, rate=2)
        assert bytes(w.buf) == b"dec 5 10 2"
    finally:
        await p.disconnect()


# --- Query commands with matchers ---


async def test_get_load_level_with_dev_response():
    p, reader, w = await _make_protocol()
    try:
        task = asyncio.create_task(_feed_after(reader, b"DEV01055\r"))
        level = await p.get_load_level(10)
        assert level == 55
        assert bytes(w.buf) == b"^F010"
        await task
    finally:
        await p.disconnect()


async def test_get_load_level_ignores_unrelated_dev_push():
    """An unrelated DEV push during get_load_level should not fulfill the request."""
    p, reader, w = await _make_protocol()
    try:
        events = []
        p.set_load_event_callback(events.append)

        async def feed_unrelated_then_correct():
            await asyncio.sleep(0.01)
            reader.feed_data(b"DEV02099\r")  # wrong idx
            await asyncio.sleep(0.01)
            reader.feed_data(b"DEV01077\r")  # correct idx

        task = asyncio.create_task(feed_unrelated_then_correct())
        level = await p.get_load_level(10)
        assert level == 77
        # Both DEV pushes fire the callback
        assert events == [LoadEvent(idx=20, level=99), LoadEvent(idx=10, level=77)]
        await task
    finally:
        await p.disconnect()


async def test_get_device_name():
    """Real reply format: NAM + 3-digit device + space-padded name + CRLF."""
    p, reader, w = await _make_protocol()
    try:
        # Exact bytes captured from a real JetStream bridge.
        task = asyncio.create_task(
            _feed_after(reader, b"NAM002GAME RM E-1-E GAME CANS   \r\n")
        )
        name = await p.get_device_name(2)
        assert name == "GAME RM E-1-E GAME CANS"  # NAM + "002" stripped, padding trimmed
        assert bytes(w.buf) == b"^N002"
        await task
    finally:
        await p.disconnect()


async def test_get_device_name_empty_returns_none():
    """A configured-but-unnamed device echoes NAM + index with no name."""
    p, reader, w = await _make_protocol()
    try:
        task = asyncio.create_task(_feed_after(reader, b"NAM001   \r\n"))
        name = await p.get_device_name(1)
        assert name is None
        await task
    finally:
        await p.disconnect()


async def test_get_device_name_ignores_wrong_device():
    """A NAM for a different device must not fulfill this request."""
    p, reader, w = await _make_protocol()
    try:
        async def feed():
            await asyncio.sleep(0.01)
            reader.feed_data(b"NAM099Some Other Device\r\n")  # wrong idx
            await asyncio.sleep(0.01)
            reader.feed_data(b"NAM001Hall Lights\r\n")  # the one we asked for
        task = asyncio.create_task(feed())
        name = await p.get_device_name(1)
        assert name == "Hall Lights"
        await task
    finally:
        await p.disconnect()


async def test_crlf_framed_events_both_parse():
    """JetStream ends lines with CRLF; a stray LF must not corrupt the next line.

    Without LF handling in the reader, the second event would be read as
    "\\nACT04401T" and fail length/prefix dispatch.
    """
    p, reader, _w = await _make_protocol()
    try:
        loads, switches = [], []
        p.set_load_event_callback(loads.append)
        p.set_switch_event_callback(switches.append)
        reader.feed_data(b"DEV00550\r\nACT04401T\r\n")
        await asyncio.sleep(0.02)
        assert loads == [LoadEvent(idx=5, level=50)]
        assert switches == [SwitchEvent(idx=44, action="tap", button=1)]
    finally:
        await p.disconnect()


async def test_ping_returns_true_on_hello():
    p, reader, w = await _make_protocol()
    try:
        task = asyncio.create_task(_feed_after(reader, b"Hello\r"))
        result = await p.ping()
        assert result is True
        assert bytes(w.buf) == b"Ping"
        await task
    finally:
        await p.disconnect()


async def test_jetstream_has_no_bulk_query_capability():
    """JetStream has no ^G/^H; it must advertise push-only state."""
    p, _r, _w = await _make_protocol()
    try:
        assert p.supports_bulk_query is False
    finally:
        await p.disconnect()


async def test_get_all_load_states_raises_not_supported():
    """get_all_load_states must fail fast, not send ^G and wait for a timeout."""
    p, _r, w = await _make_protocol()
    try:
        await _expect_raises_async(ProtocolError, p.get_all_load_states())
        # Crucially, it must NOT have written a ^G the bridge will ignore.
        assert bytes(w.buf) == b""
    finally:
        await p.disconnect()


async def test_get_all_switch_states_raises_not_supported():
    p, _r, w = await _make_protocol()
    try:
        await _expect_raises_async(ProtocolError, p.get_all_switch_states())
        assert bytes(w.buf) == b""
    finally:
        await p.disconnect()


# --- Push event tests ---


async def test_dev_push_fires_load_callback():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_load_event_callback(events.append)
        reader.feed_data(b"DEV00550\r")
        await asyncio.sleep(0.02)
        assert events == [LoadEvent(idx=5, level=50)]
    finally:
        await p.disconnect()


async def test_act_push_fires_switch_callback_tap():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_switch_event_callback(events.append)
        reader.feed_data(b"ACT04401T\r")
        await asyncio.sleep(0.02)
        assert events == [SwitchEvent(idx=44, action="tap", button=1)]
    finally:
        await p.disconnect()


async def test_act_push_fires_switch_callback_press():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_switch_event_callback(events.append)
        reader.feed_data(b"ACT04402P\r")
        await asyncio.sleep(0.02)
        assert events == [SwitchEvent(idx=44, action="press", button=2)]
    finally:
        await p.disconnect()


async def test_act_push_fires_switch_callback_release():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_switch_event_callback(events.append)
        reader.feed_data(b"ACT04403R\r")
        await asyncio.sleep(0.02)
        assert events == [SwitchEvent(idx=44, action="release", button=3)]
    finally:
        await p.disconnect()


async def test_scn_push_fires_scene_callback_active():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_scene_event_callback(events.append)
        reader.feed_data(b"SCN0041\r")
        await asyncio.sleep(0.02)
        assert events == [SceneEvent(idx=4, active=True)]
    finally:
        await p.disconnect()


async def test_scn_push_fires_scene_callback_inactive():
    p, reader, _w = await _make_protocol()
    try:
        events = []
        p.set_scene_event_callback(events.append)
        reader.feed_data(b"SCN0040\r")
        await asyncio.sleep(0.02)
        assert events == [SceneEvent(idx=4, active=False)]
    finally:
        await p.disconnect()


# --- Validation tests ---


async def test_validate_button_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.tap_switch(44, button=4))
    finally:
        await p.disconnect()


async def test_validate_load_idx_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.activate_load(200))
    finally:
        await p.disconnect()


async def test_validate_scene_idx_too_high():
    p, _r, _w = await _make_protocol()
    try:
        await _expect_raises_async(ValueError, p.activate_scene(101))
    finally:
        await p.disconnect()


# --- Standalone smoke-test runner ---

if __name__ == "__main__":
    import sys
    import traceback

    g = dict(globals())
    async_tests = sorted(
        (n, t) for n, t in g.items()
        if n.startswith("test_") and asyncio.iscoroutinefunction(t)
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

    print()
    print(f"Passed: {passed}, Failed: {len(failed)}")
    if failed:
        for name, tb in failed:
            print(f"\n--- FAIL: {name} ---")
            print(tb)
        sys.exit(1)
