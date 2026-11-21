"""Pure tests for adding, changing and removing JetStream devices in entry data."""

from __future__ import annotations

import pytest

from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SYSTEM_TYPE,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    OPT_SWITCH_NAMES,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.jetstream_devices import (
    TYPE_DIMMER,
    TYPE_KEYPAD,
    TYPE_SWITCH,
    DeviceAddition,
    DeviceEditError,
    change_load_type,
    merge_additions,
    parse_batch,
    remove_unit,
)


def _data(**extra):
    base = {
        CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
        CONF_LOAD_IDS: [1, 2],
        CONF_BUTTON_IDS: [[1, 1]],
        CONF_SCENE_IDS: [7],
    }
    base.update(extra)
    return base


def _options(**extra):
    base = {OPT_LOAD_NAMES: {"1": "Kitchen", "2": "Hall"}, OPT_NONDIMMABLE_LOADS: [2]}
    base.update(extra)
    return base


# --- merge_additions -------------------------------------------------------


def test_add_dimmer():
    data, options = merge_additions(
        _data(), _options(), [DeviceAddition(81, TYPE_DIMMER)]
    )
    assert data[CONF_LOAD_IDS] == [1, 2, 81]
    assert options[OPT_NONDIMMABLE_LOADS] == [2]
    assert data[CONF_BUTTON_IDS] == [[1, 1]]


def test_add_on_off_switch():
    data, options = merge_additions(
        _data(), _options(), [DeviceAddition(81, TYPE_SWITCH)]
    )
    assert data[CONF_LOAD_IDS] == [1, 2, 81]
    assert options[OPT_NONDIMMABLE_LOADS] == [2, 81]


def test_add_dimmer_with_buttons():
    data, _ = merge_additions(
        _data(), _options(), [DeviceAddition(81, TYPE_DIMMER, frozenset({1, 3}))]
    )
    assert data[CONF_LOAD_IDS] == [1, 2, 81]
    assert data[CONF_BUTTON_IDS] == [[1, 1], [81, 1], [81, 3]]


def test_add_keypad_only_creates_no_load():
    data, options = merge_additions(
        _data(), _options(), [DeviceAddition(90, TYPE_KEYPAD, frozenset({2}))]
    )
    assert data[CONF_LOAD_IDS] == [1, 2]
    assert data[CONF_BUTTON_IDS] == [[1, 1], [90, 2]]
    assert options[OPT_NONDIMMABLE_LOADS] == [2]


def test_name_is_stored_for_a_new_device_only():
    data, options = merge_additions(
        _data(),
        _options(),
        [
            DeviceAddition(81, TYPE_DIMMER, name="Porch"),
            DeviceAddition(1, TYPE_DIMMER, name="Renamed"),
        ],
    )
    assert options[OPT_LOAD_NAMES] == {"1": "Kitchen", "2": "Hall", "81": "Porch"}
    assert data[CONF_LOAD_IDS] == [1, 2, 81]


def test_existing_device_gains_buttons_and_loses_nothing():
    data, options = merge_additions(
        _data(), _options(), [DeviceAddition(2, TYPE_DIMMER, frozenset({1, 2}))]
    )
    assert data[CONF_LOAD_IDS] == [1, 2]
    assert data[CONF_BUTTON_IDS] == [[1, 1], [2, 1], [2, 2]]
    # Adding never retypes: load 2 stays on/off even though "dimmer" was chosen.
    assert options[OPT_NONDIMMABLE_LOADS] == [2]


def test_adding_a_load_that_was_imported_disabled_drops_the_disable():
    data, _ = merge_additions(
        _data(**{CONF_DISABLED_LOADS: [2, 5]}),
        _options(),
        [DeviceAddition(2, TYPE_DIMMER)],
    )
    assert data[CONF_DISABLED_LOADS] == [5]


def test_inputs_are_not_mutated():
    data, options = _data(), _options()
    merge_additions(data, options, [DeviceAddition(81, TYPE_SWITCH, frozenset({1}))])
    assert data == _data()
    assert options == _options()


@pytest.mark.parametrize(
    ("addition", "code"),
    [
        (DeviceAddition(0, TYPE_DIMMER), "id_out_of_range"),
        (DeviceAddition(200, TYPE_DIMMER), "id_out_of_range"),
        (DeviceAddition(81, TYPE_DIMMER, frozenset({4})), "bad_button"),
        (DeviceAddition(81, TYPE_KEYPAD), "keypad_needs_button"),
        (DeviceAddition(81, "toaster"), "bad_type"),
    ],
)
def test_invalid_additions_are_rejected(addition, code):
    with pytest.raises(DeviceEditError) as err:
        merge_additions(_data(), _options(), [addition])
    assert err.value.code == code


def test_same_id_twice_in_one_batch_is_rejected():
    with pytest.raises(DeviceEditError) as err:
        merge_additions(
            _data(),
            _options(),
            [DeviceAddition(81, TYPE_DIMMER), DeviceAddition(81, TYPE_SWITCH)],
        )
    assert err.value.code == "duplicate_id"


# --- parse_batch -----------------------------------------------------------


def test_parse_batch_lines():
    text = "81, dimmer, 1, 3\n\n  82 , on/off\n90, keypad, 2\n83, dimmable\n"
    assert parse_batch(text) == [
        DeviceAddition(81, TYPE_DIMMER, frozenset({1, 3})),
        DeviceAddition(82, TYPE_SWITCH),
        DeviceAddition(90, TYPE_KEYPAD, frozenset({2})),
        DeviceAddition(83, TYPE_DIMMER),
    ]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("81, dimmer\nabc, dimmer", 2),
        ("81", 1),
        ("81, dimmer\n\n82, lamp", 3),
        ("81, dimmer, x", 1),
    ],
)
def test_parse_batch_reports_the_bad_line(text, line):
    with pytest.raises(DeviceEditError) as err:
        parse_batch(text)
    assert err.value.code == "bad_line"
    assert err.value.line == line


def test_parse_batch_empty_is_rejected():
    with pytest.raises(DeviceEditError) as err:
        parse_batch("  \n ")
    assert err.value.code == "empty"


# --- change_load_type ------------------------------------------------------


def test_change_dimmer_to_on_off_and_back():
    options = change_load_type(_data(), _options(), 1, dimmable=False)
    assert options[OPT_NONDIMMABLE_LOADS] == [1, 2]
    options = change_load_type(_data(), options, 1, dimmable=True)
    assert options[OPT_NONDIMMABLE_LOADS] == [2]


def test_change_rejects_a_device_with_no_load():
    with pytest.raises(DeviceEditError) as err:
        change_load_type(_data(), _options(), 44, dimmable=False)
    assert err.value.code == "not_a_load"


# --- remove_unit -----------------------------------------------------------


def test_remove_unit_clears_every_trace_of_the_address():
    data = _data(**{CONF_DISABLED_LOADS: [1], CONF_BUTTON_IDS: [[1, 1], [1, 2], [2, 1]]})
    options = _options(**{OPT_SWITCH_NAMES: {"001.01": "K B1", "002.01": "H B1"}})
    new_data, new_options = remove_unit(data, options, 1)
    assert new_data[CONF_LOAD_IDS] == [2]
    assert new_data[CONF_BUTTON_IDS] == [[2, 1]]
    assert new_data[CONF_DISABLED_LOADS] == []
    assert new_data[CONF_SCENE_IDS] == [7]
    assert new_options[OPT_LOAD_NAMES] == {"2": "Hall"}
    assert new_options[OPT_SWITCH_NAMES] == {"002.01": "H B1"}
    assert new_options[OPT_NONDIMMABLE_LOADS] == [2]


def test_remove_unknown_unit_is_rejected():
    with pytest.raises(DeviceEditError) as err:
        remove_unit(_data(), _options(), 150)
    assert err.value.code == "unknown_device"
