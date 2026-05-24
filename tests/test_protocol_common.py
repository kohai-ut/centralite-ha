"""Unit tests for bit-layout decoders in protocol/common.py.

Layouts tested against docs/protocols/elegance-rs232-protocol.pdf pages 6-7.
"""

import pytest

from custom_components.centralite.protocol.common import (
    LOAD_BITMAP_HEX_LEN,
    SWITCH_BITMAP_HEX_LEN,
    parse_load_bitmap,
    parse_switch_bitmap,
)


class TestParseLoadBitmap:
    def test_all_off(self):
        result = parse_load_bitmap("0" * LOAD_BITMAP_HEX_LEN)
        assert len(result) == 192
        assert all(v is False for v in result.values())

    def test_all_on(self):
        result = parse_load_bitmap("F" * LOAD_BITMAP_HEX_LEN)
        assert len(result) == 192
        assert all(v is True for v in result.values())

    def test_load_1_only(self):
        response = "010000" + "000000" * 7
        result = parse_load_bitmap(response)
        assert result[1] is True
        assert sum(result.values()) == 1

    def test_load_8_only(self):
        response = "800000" + "000000" * 7
        result = parse_load_bitmap(response)
        assert result[8] is True
        assert sum(result.values()) == 1

    def test_load_9_only(self):
        response = "000100" + "000000" * 7
        result = parse_load_bitmap(response)
        assert result[9] is True
        assert sum(result.values()) == 1

    def test_load_24_only(self):
        response = "000080" + "000000" * 7
        result = parse_load_bitmap(response)
        assert result[24] is True
        assert sum(result.values()) == 1

    def test_load_25_first_of_board_2(self):
        response = "000000" + "010000" + "000000" * 6
        result = parse_load_bitmap(response)
        assert result[25] is True
        assert sum(result.values()) == 1

    def test_load_192_last(self):
        response = "000000" * 7 + "000080"
        result = parse_load_bitmap(response)
        assert result[192] is True
        assert sum(result.values()) == 1

    def test_byte_value_0xab(self):
        response = "ab0000" + "000000" * 7
        result = parse_load_bitmap(response)
        assert result[1] is True
        assert result[2] is True
        assert result[3] is False
        assert result[4] is True
        assert result[5] is False
        assert result[6] is True
        assert result[7] is False
        assert result[8] is True

    def test_uppercase_and_lowercase_hex_equivalent(self):
        upper = parse_load_bitmap("DEADBEEFCAFE" + "000000" * 6)
        lower = parse_load_bitmap("deadbeefcafe" + "000000" * 6)
        assert upper == lower

    def test_wrong_length_too_short(self):
        with pytest.raises(ValueError, match="Expected 48"):
            parse_load_bitmap("00")

    def test_wrong_length_too_long(self):
        with pytest.raises(ValueError, match="Expected 48"):
            parse_load_bitmap("0" * 50)

    def test_invalid_hex_raises(self):
        with pytest.raises(ValueError):
            parse_load_bitmap("ZZ" + "0" * 46)


class TestParseSwitchBitmap:
    def test_all_off(self):
        result = parse_switch_bitmap("0" * SWITCH_BITMAP_HEX_LEN)
        assert len(result) == 384
        assert all(v is False for v in result.values())

    def test_all_on(self):
        result = parse_switch_bitmap("F" * SWITCH_BITMAP_HEX_LEN)
        assert len(result) == 384
        assert all(v is True for v in result.values())

    def test_switch_1_only(self):
        response = "0100" + "0000" * 23
        result = parse_switch_bitmap(response)
        assert result[1] is True
        assert sum(result.values()) == 1

    def test_switch_8_only(self):
        response = "8000" + "0000" * 23
        result = parse_switch_bitmap(response)
        assert result[8] is True
        assert sum(result.values()) == 1

    def test_switch_9_only(self):
        response = "0001" + "0000" * 23
        result = parse_switch_bitmap(response)
        assert result[9] is True
        assert sum(result.values()) == 1

    def test_switch_16_only(self):
        response = "0080" + "0000" * 23
        result = parse_switch_bitmap(response)
        assert result[16] is True
        assert sum(result.values()) == 1

    def test_switch_17_first_of_entry_2(self):
        response = "0000" + "0100" + "0000" * 22
        result = parse_switch_bitmap(response)
        assert result[17] is True
        assert sum(result.values()) == 1

    def test_switch_384_last(self):
        response = "0000" * 23 + "0080"
        result = parse_switch_bitmap(response)
        assert result[384] is True
        assert sum(result.values()) == 1

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="Expected 96"):
            parse_switch_bitmap("00")

    def test_invalid_hex_raises(self):
        with pytest.raises(ValueError):
            parse_switch_bitmap("XXXX" + "0" * 92)
