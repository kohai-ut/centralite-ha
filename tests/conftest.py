"""Shared fixtures for the Home Assistant integration tests.

These tests need the HA test harness:
    pip install pytest-homeassistant-custom-component serialx

The pure protocol/parser/migration tests (test_protocol_*, test_parsers_*,
test_migrate) do NOT import this module's HA fixtures and still run standalone
with `PYTHONPATH=. python tests/test_*.py`.
"""

from __future__ import annotations

import pytest

from custom_components.centralite.protocol import CentraliteProtocol
from custom_components.centralite.protocol.common import ProtocolError


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA discover custom_components/centralite during tests."""
    yield


class FakeProtocol(CentraliteProtocol):
    """In-memory CentraliteProtocol for coordinator/entity/setup tests.

    Records outbound calls in `calls`, lets tests fire push events through the
    stored callbacks, and toggles capability flags so both the Elegance-shaped
    (bulk + commanded-scene) and JetStream-shaped (push-only + scene-push)
    behaviors can be exercised without real serial hardware.
    """

    def __init__(
        self,
        *,
        supports_bulk: bool = True,
        supports_scene_push: bool = False,
        bulk_loads: dict[int, bool] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self._bulk = supports_bulk
        self._scene_push = supports_scene_push
        self._bulk_loads = bulk_loads or {}
        # Mutable so tests can toggle it between reconnect attempts.
        self.connect_error = connect_error
        self.connect_count = 0
        self.calls: list[tuple] = []
        self.connected = False
        self.load_cb = None
        self.switch_cb = None
        self.scene_cb = None
        self.disconnect_cb = None

    async def connect(self) -> None:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def activate_load(self, idx: int) -> None:
        self.calls.append(("activate_load", idx))

    async def deactivate_load(self, idx: int) -> None:
        self.calls.append(("deactivate_load", idx))

    async def set_load_level(self, idx: int, level: int, rate: int = 0) -> None:
        self.calls.append(("set_load_level", idx, level, rate))

    async def get_load_level(self, idx: int) -> int:
        return 0

    async def get_all_load_states(self) -> dict[int, bool]:
        if not self._bulk:
            raise ProtocolError("push-only: no bulk query")
        return dict(self._bulk_loads)

    async def activate_scene(self, idx: int) -> None:
        self.calls.append(("activate_scene", idx))

    async def deactivate_scene(self, idx: int) -> None:
        self.calls.append(("deactivate_scene", idx))

    async def press_switch(self, idx: int, *, auto_release: bool = True) -> None:
        self.calls.append(("press_switch", idx))

    async def release_switch(self, idx: int) -> None:
        self.calls.append(("release_switch", idx))

    async def tap_switch(self, idx: int, *, button: int = 1) -> None:
        self.calls.append(("tap_switch", idx, button))

    async def get_all_switch_states(self) -> dict[int, bool]:
        if not self._bulk:
            raise ProtocolError("push-only: no bulk query")
        return {}

    def set_load_event_callback(self, cb) -> None:
        self.load_cb = cb

    def set_switch_event_callback(self, cb) -> None:
        self.switch_cb = cb

    def set_scene_event_callback(self, cb) -> None:
        self.scene_cb = cb

    def set_disconnect_callback(self, cb) -> None:
        self.disconnect_cb = cb

    @property
    def supports_scene_push(self) -> bool:
        return self._scene_push

    @property
    def supports_bulk_query(self) -> bool:
        return self._bulk

    @property
    def max_loads(self) -> int:
        return 192

    @property
    def max_switches(self) -> int:
        return 384
