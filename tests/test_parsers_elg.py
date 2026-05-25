"""Tests for the .elg INI parser."""

from custom_components.centralite.parsers.elg import (
    ElgConfig,
    parse_csv_ids,
    parse_elg,
)


def test_empty_input():
    result = parse_elg("")
    assert result == ElgConfig()


def test_single_load_with_name():
    text = """\
[LOAD 001]
  NAME=Upstairs Hall Recessed Lights
  DIMMER=Y
"""
    result = parse_elg(text)
    assert result.loads == {1: "Upstairs Hall Recessed Lights"}
    assert result.scenes == {}


def test_load_with_empty_name():
    text = """\
[LOAD 007]
  NAME=
  DIMMER=Y
"""
    result = parse_elg(text)
    assert result.loads == {7: ""}


def test_load_with_no_name_key():
    text = """\
[LOAD 099]
  DIMMER=N
"""
    result = parse_elg(text)
    assert result.loads == {99: ""}


def test_scene_name_from_header():
    text = """\
[SCENE 04- Upstairs Path]
  some body content
[SCENE 09- Office Scene]
"""
    result = parse_elg(text)
    assert result.scenes == {4: "Upstairs Path", 9: "Office Scene"}


def test_combined_loads_and_scenes():
    text = """\
[LOAD 001]
  NAME=Hall
  DIMMER=Y

[LOAD 002]
  NAME=Master Bedroom

[SCENE 04- Upstairs Path]

[SCENE 99- Reload]
"""
    result = parse_elg(text)
    assert result.loads == {1: "Hall", 2: "Master Bedroom"}
    assert result.scenes == {4: "Upstairs Path", 99: "Reload"}


def test_ignores_unknown_sections():
    text = """\
[Settings]
[Multiple System]
False

[LOAD 001]
  NAME=Hall

[VISIBLE PANELS]
8

[Unknown Section]
  some=value
"""
    result = parse_elg(text)
    assert result.loads == {1: "Hall"}
    assert result.scenes == {}


def test_crlf_line_endings():
    text = "[LOAD 001]\r\n  NAME=Hall\r\n"
    result = parse_elg(text)
    assert result.loads == {1: "Hall"}


def test_scene_name_with_dashes():
    text = "[SCENE 18- Jetstream - Send All Off Command]\n"
    result = parse_elg(text)
    assert result.scenes == {18: "Jetstream - Send All Off Command"}


def test_real_world_excerpt():
    # Stripped excerpt mirroring the actual .elg structure with its various quirks.
    text = """\
[CentraLite Elegance Data File]
REV 1.1

[Settings]

[LOAD 001]
  NAME=Upstairs Hall Recessed Lights
  DIMMER=Y
  ALL ON=Y
  PRESET LEVEL=050

[LOAD 002]
  NAME=Upstairs West Rm - Closet Light
  DIMMER=Y

[LOAD 007]
  NAME=
  DIMMER=Y

[SCENE 04- Upstairs Path]
  body line one
  body line two

[SCENE 06- Great Room Scene 1]
"""
    result = parse_elg(text)
    assert result.loads == {
        1: "Upstairs Hall Recessed Lights",
        2: "Upstairs West Rm - Closet Light",
        7: "",
    }
    assert result.scenes == {
        4: "Upstairs Path",
        6: "Great Room Scene 1",
    }


# --- DIMMER / load-type tests ---


def test_dimmer_flag_parsed():
    text = """\
[LOAD 001]
  NAME=Recessed
  DIMMER=Y
[LOAD 002]
  NAME=Closet
  DIMMER=N
"""
    result = parse_elg(text)
    assert result.dimmable == {1: True, 2: False}


def test_dimmer_defaults_true_when_absent():
    """A load with no DIMMER key defaults to dimmable (prior behavior)."""
    result = parse_elg("[LOAD 005]\n  NAME=Mystery\n")
    assert result.dimmable == {5: True}


def test_dimmer_lowercase_and_blank():
    text = "[LOAD 001]\n  DIMMER=y\n[LOAD 002]\n  DIMMER=\n[LOAD 003]\n  DIMMER=N\n"
    result = parse_elg(text)
    assert result.dimmable == {1: True, 2: False, 3: False}


# --- referenced-load tests (scene + keypad button) ---


def test_referenced_loads_from_scene():
    text = """\
[LOAD 001]
  NAME=Hall
[LOAD 018]
  NAME=
[SCENE 04- Path]
  NAME=Path
  NUMLOADS=02
  LOAD_001- Hall
  DIM_LEVEL=080
  RATE=003
  LOAD_018- Landing
  DIM_LEVEL=100
  RATE=000
"""
    result = parse_elg(text)
    assert result.referenced_loads == {1, 18}


def test_referenced_loads_from_active_button_only():
    """LOAD/SCENE=L with ACTIVE=1 counts; scene-target or inactive buttons don't."""
    text = """\
[A1]
  LOAD/SCENE=L
  #=12
  ACTIVE=1
[A2]
  LOAD/SCENE=L
  #=99
  ACTIVE=0
[A3]
  LOAD/SCENE=S
  #=4
  ACTIVE=1
"""
    result = parse_elg(text)
    assert result.referenced_loads == {12}  # not 99 (inactive), not 4 (scene)


def test_referenced_loads_empty_when_none():
    assert parse_elg("[LOAD 001]\n  NAME=Solo\n").referenced_loads == set()


# --- named keypad switch extraction ---


def test_named_switches_mapping():
    """Named keypad buttons map to a global switch index; unnamed ones are skipped."""
    text = (
        "[A1]\n  NAME=\n  LOAD/SCENE=L\n  #=12\n"           # unnamed -> skip
        "[B1]\n  NAME=Front Entry Right\n  LOAD/SCENE=L\n"   # (1-1)*16+1+1 = 2
        "[E4]\n  NAME=North Garage Lights\n  LOAD/SCENE=L\n"  # (4-1)*16+4+1 = 53
    )
    result = parse_elg(text)
    assert result.switches == {2: "Front Entry Right", 53: "North Garage Lights"}


def test_switches_empty_when_no_keypads():
    assert parse_elg("[LOAD 1]\n  NAME=Solo\n").switches == {}


def test_named_switches_skips_out_of_range_button():
    """A stray [A99] (button# > 24) must be skipped, not poison the whole import."""
    text = "[A99]\n  NAME=Bogus\n[B1]\n  NAME=Real\n"
    assert parse_elg(text).switches == {2: "Real"}


# --- parse_csv_ids tests ---


def test_csv_ids_empty():
    assert parse_csv_ids("") == []
    assert parse_csv_ids("   ") == []


def test_csv_ids_basic():
    assert parse_csv_ids("1,2,3") == [1, 2, 3]


def test_csv_ids_with_whitespace():
    assert parse_csv_ids("  1 , 2 ,  3 ") == [1, 2, 3]


def test_csv_ids_deduplicates_and_sorts():
    assert parse_csv_ids("3,1,2,1,3") == [1, 2, 3]


def test_csv_ids_ignores_empty_tokens():
    assert parse_csv_ids("1,,2,,,3") == [1, 2, 3]


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
