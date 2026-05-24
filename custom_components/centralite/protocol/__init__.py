"""Centralite protocol abstraction.

CentraliteProtocol ABC plus concrete implementations for Elegance and
JetStream bridges. Selected per config entry based on system type.

The ABC defines operations every Centralite-family bridge supports.
Capability flags (supports_device_name_query, supports_scene_push) let
entities adapt to per-system features (e.g. JetStream's ^N name query
and SCN scene-state push, which Elegance lacks).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LoadEvent:
    """Push event: a load's level changed."""

    idx: int
    level: int


@dataclass(frozen=True, slots=True)
class SwitchEvent:
    """Push event: a physical switch was pressed, released, or tapped.

    `idx` and `board` are used by Elegance (idx is global 1-384, board is the
    board number 0-4). `idx` and `button` are used by JetStream (idx is the
    device 1-199, button is 1-3). The unused field is 0 on the other system.
    """

    idx: int
    action: str
    board: int = 0
    button: int = 0


@dataclass(frozen=True, slots=True)
class SceneEvent:
    """Push event: a scene was activated or deactivated."""

    idx: int
    active: bool


LoadEventCallback = Callable[[LoadEvent], None]
SwitchEventCallback = Callable[[SwitchEvent], None]
SceneEventCallback = Callable[[SceneEvent], None]
# Fired when the serial reader loop ends unexpectedly (cable pulled, adapter
# removed, bridge power-cycled). The argument is the triggering exception, or
# None if the stream simply hit EOF. Not fired on intentional disconnect().
DisconnectCallback = Callable[["Exception | None"], None]


class CentraliteProtocol(ABC):
    """Async protocol for talking to a Centralite bridge over serial."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the serial port and start the reader loop."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop the reader loop and close the serial port."""

    @abstractmethod
    async def activate_load(self, idx: int) -> None: ...

    @abstractmethod
    async def deactivate_load(self, idx: int) -> None: ...

    @abstractmethod
    async def set_load_level(self, idx: int, level: int, rate: int = 0) -> None:
        """Set load to level 0-99 at rate code 0-31."""

    @abstractmethod
    async def get_load_level(self, idx: int) -> int:
        """Return current level 0-99 for one load (Elegance: ^F)."""

    @abstractmethod
    async def get_all_load_states(self) -> dict[int, bool]:
        """Return {load_idx: is_on} for every load (Elegance: ^G)."""

    @abstractmethod
    async def activate_scene(self, idx: int) -> None: ...

    @abstractmethod
    async def deactivate_scene(self, idx: int) -> None: ...

    @abstractmethod
    async def press_switch(self, idx: int, *, auto_release: bool = True) -> None:
        """Simulate a switch press.

        When auto_release is True (default), automatically follow with a
        release after a short delay. Elegance hardware can lock up if a
        press is held indefinitely; auto-release prevents that.
        """

    @abstractmethod
    async def release_switch(self, idx: int) -> None: ...

    @abstractmethod
    async def get_all_switch_states(self) -> dict[int, bool]:
        """Return {switch_idx: is_on} for every switch (Elegance: ^H)."""

    async def get_clock(self) -> datetime:
        """Return the bridge's real-time clock (Elegance: ^K)."""
        raise NotImplementedError

    async def set_clock(self, dt: datetime) -> None:
        """Set the bridge's real-time clock (Elegance: ^L)."""
        raise NotImplementedError

    async def get_device_name(self, idx: int) -> str | None:
        """Query the bridge for a device's stored name.

        JetStream supports this via ^N. Elegance does not — returns None.
        """
        return None

    @abstractmethod
    def set_load_event_callback(self, cb: LoadEventCallback) -> None:
        """Register a callback for spontaneous load-level push events."""

    @abstractmethod
    def set_switch_event_callback(self, cb: SwitchEventCallback) -> None:
        """Register a callback for spontaneous switch press/release events."""

    def set_scene_event_callback(self, cb: SceneEventCallback) -> None:
        """Register a callback for spontaneous scene activate/deactivate events.

        Only fires on bridges with supports_scene_push (JetStream).
        """

    def set_disconnect_callback(self, cb: DisconnectCallback) -> None:
        """Register a callback fired when the connection is lost unexpectedly.

        Lets the coordinator mark entities unavailable instead of leaving them
        showing stale state forever after the serial link drops.
        """

    @property
    def supports_device_name_query(self) -> bool:
        return False

    @property
    def supports_scene_push(self) -> bool:
        return False

    @property
    def supports_bulk_query(self) -> bool:
        """Whether the bridge can report every load's state in one command.

        Elegance has ^G (all loads) and ^H (all switches). JetStream has
        neither — it is push-only, reporting state via spontaneous DEV/ACT/SCN
        output. The coordinator uses this to decide whether to prime initial
        state and run the safety-net poll, or rely purely on push events.
        """
        return True

    @property
    @abstractmethod
    def max_loads(self) -> int: ...

    @property
    @abstractmethod
    def max_switches(self) -> int: ...
