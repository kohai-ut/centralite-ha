"""Shared protocol helpers: ASCII framing, hex parsing, bit-layout decoding.

The bulk-state queries (^G for loads, ^H for switches) return hex bitmaps
with the specific layout documented in the Elegance protocol manual
(docs/protocols/elegance-rs232-protocol.pdf, page 6). These helpers decode
those bitmaps into {idx: bool} dicts. JetStream uses the same format for
its equivalent bulk queries; the helpers are reused.
"""

from __future__ import annotations


class ProtocolError(RuntimeError):
    """A response from the bridge could not be parsed or didn't arrive."""


LOAD_BITMAP_HEX_LEN = 48
LOADS_PER_BOARD = 24
MAX_BOARDS = 8
MAX_LOADS = LOADS_PER_BOARD * MAX_BOARDS

SWITCH_BITMAP_HEX_LEN = 96
STARS_PER_ENTRY = 16
SWITCH_ENTRIES = 24
MAX_SWITCHES = STARS_PER_ENTRY * SWITCH_ENTRIES


def parse_load_bitmap(hex_response: str) -> dict[int, bool]:
    """Parse a ^G response into {load_idx: is_on}.

    Format (Elegance manual p.6):
        48 hex digits split into 8 boards of 6 digits each.
        Within one board's 6-digit chunk:
            digits 0-1 (least-significant byte) -> loads 1-8 of that board
            digits 2-3 (middle byte)            -> loads 9-16
            digits 4-5 (most-significant byte)  -> loads 17-24
        Within each byte, bit 0 is the lowest-numbered load.

    Global load idx = board_idx * 24 + local_idx, where board_idx is 0-7
    and local_idx is 1-24, giving global idx 1-192.
    """
    if len(hex_response) != LOAD_BITMAP_HEX_LEN:
        raise ValueError(
            f"Expected {LOAD_BITMAP_HEX_LEN} hex digits, got {len(hex_response)}: "
            f"{hex_response!r}"
        )

    result: dict[int, bool] = {}
    for board in range(MAX_BOARDS):
        chunk = hex_response[board * 6 : board * 6 + 6]
        byte_pairs = (chunk[0:2], chunk[2:4], chunk[4:6])
        for pair_idx, hex_pair in enumerate(byte_pairs):
            byte_value = int(hex_pair, 16)
            for bit in range(8):
                local_idx = pair_idx * 8 + bit + 1
                global_idx = board * LOADS_PER_BOARD + local_idx
                result[global_idx] = bool(byte_value & (1 << bit))
    return result


def parse_switch_bitmap(hex_response: str) -> dict[int, bool]:
    """Parse a ^H response into {switch_idx: is_on}.

    Format (Elegance manual pp.6-7):
        96 hex digits split into 24 entries of 4 digits each.
        Within one entry's 4-digit chunk:
            digits 0-1 (least-significant byte) -> STARS 1A-1D, 2A-2D (positions 0-7)
            digits 2-3 (most-significant byte)  -> STARS 3A-4D (positions 8-15)
        Each switch entry covers 16 STARS positions.

    Global switch idx = entry_idx * 16 + stars_position + 1, giving 1-384.
    """
    if len(hex_response) != SWITCH_BITMAP_HEX_LEN:
        raise ValueError(
            f"Expected {SWITCH_BITMAP_HEX_LEN} hex digits, got {len(hex_response)}: "
            f"{hex_response!r}"
        )

    result: dict[int, bool] = {}
    for entry in range(SWITCH_ENTRIES):
        chunk = hex_response[entry * 4 : entry * 4 + 4]
        byte_pairs = (chunk[0:2], chunk[2:4])
        for pair_idx, hex_pair in enumerate(byte_pairs):
            byte_value = int(hex_pair, 16)
            for bit in range(8):
                stars_position = pair_idx * 8 + bit
                switch_idx = entry * STARS_PER_ENTRY + stars_position + 1
                result[switch_idx] = bool(byte_value & (1 << bit))
    return result
