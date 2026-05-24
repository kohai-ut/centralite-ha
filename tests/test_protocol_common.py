"""Unit tests for bit-layout decoders in protocol/common.py.

Layouts tested against docs/protocols/elegance-rs232-protocol.pdf pages 6-7.
Pytest-compatible (bare asserts). Also runnable as a standalone script.
"""

from custom_components.centralite.protocol.common import (
    LOAD_BITMAP_HEX_LEN,
    SWITCH_BITMAP_HEX_LEN,
    parse_load_bitmap,
    parse_switch_bitmap,
)


def _expect_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"Expected {exc_type.__name__}")


# --- parse_load_bitmap ---


def test_load_all_off():
    result = parse_load_bitmap("0" * LOAD_BITMAP_HEX_LEN)
    assert len(result) == 192
    assert all(v is False for v in result.values())


def test_load_all_on():
    result = parse_load_bitmap("F" * LOAD_BITMAP_HEX_LEN)
    assert len(result) == 192
    assert all(v is True for v in result.values())


def test_load_1_only():
    response = "010000" + "000000" * 7
    result = parse_load_bitmap(response)
    assert result[1] is True
    assert sum(result.values()) == 1


def test_load_8_only():
    response = "800000" + "000000" * 7
    result = parse_load_bitmap(response)
    assert result[8] is True
    assert sum(result.values()) == 1


def test_load_9_only():
    response = "000100" + "000000" * 7
    result = parse_load_bitmap(response)
    assert result[9] is True
    assert sum(result.values()) == 1


def test_load_24_only():
    response = "000080" + "000000" * 7
    result = parse_load_bitmap(response)
    assert result[24] is True
    assert sum(result.values()) == 1


def test_load_25_first_of_board_2():
    response = "000000" + "010000" + "000000" * 6
    result = parse_load_bitmap(response)
    assert result[25] is True
    assert sum(result.values()) == 1


def test_load_192_last():
    response = "000000" * 7 + "000080"
    result = parse_load_bitmap(response)
    assert result[192] is True
    assert sum(result.values()) == 1


def test_load_byte_0xab():
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


def test_load_hex_case_insensitive():
    upper = parse_load_bitmap("DEADBEEFCAFE" + "000000" * 6)
    lower = parse_load_bitmap("deadbeefcafe" + "000000" * 6)
    assert upper == lower


def test_load_wrong_length_too_short():
    _expect_raises(ValueError, parse_load_bitmap, "00")


def test_load_wrong_length_too_long():
    _expect_raises(ValueError, parse_load_bitmap, "0" * 50)


def test_load_invalid_hex():
    _expect_raises(ValueError, parse_load_bitmap, "ZZ" + "0" * 46)


# --- parse_switch_bitmap ---


def test_switch_all_off():
    result = parse_switch_bitmap("0" * SWITCH_BITMAP_HEX_LEN)
    assert len(result) == 384
    assert all(v is False for v in result.values())


def test_switch_all_on():
    result = parse_switch_bitmap("F" * SWITCH_BITMAP_HEX_LEN)
    assert len(result) == 384
    assert all(v is True for v in result.values())


def test_switch_1_only():
    response = "0100" + "0000" * 23
    result = parse_switch_bitmap(response)
    assert result[1] is True
    assert sum(result.values()) == 1


def test_switch_8_only():
    response = "8000" + "0000" * 23
    result = parse_switch_bitmap(response)
    assert result[8] is True
    assert sum(result.values()) == 1


def test_switch_9_only():
    response = "0001" + "0000" * 23
    result = parse_switch_bitmap(response)
    assert result[9] is True
    assert sum(result.values()) == 1


def test_switch_16_only():
    response = "0080" + "0000" * 23
    result = parse_switch_bitmap(response)
    assert result[16] is True
    assert sum(result.values()) == 1


def test_switch_17_first_of_entry_2():
    response = "0000" + "0100" + "0000" * 22
    result = parse_switch_bitmap(response)
    assert result[17] is True
    assert sum(result.values()) == 1


def test_switch_384_last():
    response = "0000" * 23 + "0080"
    result = parse_switch_bitmap(response)
    assert result[384] is True
    assert sum(result.values()) == 1


def test_switch_wrong_length():
    _expect_raises(ValueError, parse_switch_bitmap, "00")


def test_switch_invalid_hex():
    _expect_raises(ValueError, parse_switch_bitmap, "XXXX" + "0" * 92)


# --- Standalone smoke-test runner ---


if __name__ == "__main__":
    import sys
    import traceback

    tests = sorted(
        (n, t) for n, t in dict(globals()).items()
        if n.startswith("test_") and callable(t)
    )

    passed = 0
    failed: list[tuple[str, str]] = []
    for name, t in tests:
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
