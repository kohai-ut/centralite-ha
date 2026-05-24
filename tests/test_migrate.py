"""Tests for the v1 -> v2 unique_id migration classifier.

Only the pure pattern-matching layer is tested here (`classify`).
The full async_migrate_entries function depends on HA's entity_registry
and is exercised only when the integration is loaded in a real HA env.
"""

from custom_components.centralite.migrate import MigrationResult, classify


def _expect_migrate(uid: str, suffix: str) -> None:
    r = classify(uid)
    assert r.action == "migrate", f"{uid!r} -> {r}"
    assert r.new_suffix == suffix, f"{uid!r} -> {r}"


def _expect_delete(uid: str) -> None:
    r = classify(uid)
    assert r.action == "delete", f"{uid!r} -> {r}"


def _expect_skip(uid: str) -> None:
    r = classify(uid)
    assert r.action == "skip", f"{uid!r} -> {r}"


# --- Elegance loads ---


def test_elegance_load_basic():
    _expect_migrate("elegance.L001", "load_001")


def test_elegance_load_high_number():
    _expect_migrate("elegance.L192", "load_192")


def test_elegance_load_case_insensitive():
    _expect_migrate("elegance.l001", "load_001")
    _expect_migrate("ELEGANCE.L001", "load_001")


def test_elegance_load_zero_pads():
    _expect_migrate("elegance.L5", "load_005")


# --- Elegance switches ---


def test_elegance_switch_basic():
    _expect_migrate("elegance.SW044", "switch_044")


def test_elegance_switch_high_number():
    _expect_migrate("elegance.SW384", "switch_384")


# --- Elegance scenes ---


def test_elegance_scene_on_renames():
    _expect_migrate("elegance.scene4ON", "scene_004")


def test_elegance_scene_off_deletes():
    _expect_delete("elegance.scene4OFF")


def test_elegance_scene_no_suffix_renames():
    _expect_migrate("elegance.scene7", "scene_007")


def test_elegance_scene_high_number():
    _expect_migrate("elegance.scene99ON", "scene_099")


# --- JetStream loads ---


def test_jetstream_load_basic():
    _expect_migrate("jetstream.JSL001", "load_001")


def test_jetstream_load_lowercase():
    _expect_migrate("jetstream.jsl001", "load_001")


# --- JetStream buttons (device + button index) ---


def test_jetstream_button_basic():
    _expect_migrate("jetstream.JSSW04401", "button_044_01")


def test_jetstream_button_zero_padding():
    _expect_migrate("jetstream.JSSW00103", "button_001_03")


def test_jetstream_button_lowercase():
    _expect_migrate("jetstream.jssw04401", "button_044_01")


# --- JetStream simple switch (no button index) ---


def test_jetstream_switch_basic():
    _expect_migrate("jetstream.SW044", "switch_044")


# --- JetStream scenes ---


def test_jetstream_scene_on_renames():
    _expect_migrate("jetstream.scene4ON", "scene_004")


def test_jetstream_scene_off_deletes():
    _expect_delete("jetstream.scene4OFF")


def test_jetstream_scene_no_suffix():
    _expect_migrate("jetstream.scene12", "scene_012")


# --- Skipping non-matching IDs ---


def test_unknown_format_skipped():
    _expect_skip("foo.bar")


def test_empty_string_skipped():
    _expect_skip("")


def test_v2_format_skipped_idempotent():
    """Already-migrated entries (v2 format) should not re-match v1 patterns."""
    _expect_skip("abc123def456_load_001")
    _expect_skip("abc123def456_scene_004")
    _expect_skip("abc123def456_button_044_01")


def test_other_integration_unique_ids_skipped():
    _expect_skip("light.kitchen_lamp")
    _expect_skip("zwave.node_123")
    _expect_skip("hue.0017880100000000")


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
