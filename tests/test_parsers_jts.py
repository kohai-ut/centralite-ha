"""Tests for the .jts (JetStream Designer XML) parser.

Pure XML parsing — no Home Assistant needed. Runnable standalone:
    PYTHONPATH=. python tests/test_parsers_jts.py
"""

from custom_components.centralite.parsers.jts import JtsConfig, parse_jts


def _device(did, name, dimmer="true", thirdparty="true", active="true"):
    return f"""\
    <Device>
      <DeviceID>{did}</DeviceID>
      <Name>{name}</Name>
      <Dimmer>{dimmer}</Dimmer>
      <SendThirdParty>{thirdparty}</SendThirdParty>
      <Active>{active}</Active>
    </Device>"""


def _doc(devices="", scenes=""):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GulfStreamCL>
  <DeviceList>
{devices}
  </DeviceList>
  <SceneList>
{scenes}
  </SceneList>
</GulfStreamCL>"""


def test_empty_doc():
    assert parse_jts(_doc()) == JtsConfig()


def test_basic_devices_and_scenes():
    xml = _doc(
        devices=_device(2, "GAME RM E-1-E GAME CANS") + "\n" + _device(3, "Hall", dimmer="false"),
        scenes="<Scene><ID>1</ID><Name>All On</Name></Scene>",
    )
    cfg = parse_jts(xml)
    assert cfg.loads == {2: "GAME RM E-1-E GAME CANS", 3: "Hall"}
    assert cfg.dimmable == {2: True, 3: False}
    assert cfg.scenes == {1: "All On"}


def test_skips_non_thirdparty_devices():
    """A device not exposed to RS-232 can't be observed/controlled — drop it."""
    xml = _doc(devices=_device(2, "Visible") + "\n" + _device(3, "Hidden", thirdparty="false"))
    cfg = parse_jts(xml)
    assert cfg.loads == {2: "Visible"}


def test_skips_inactive_and_zero_ids():
    xml = _doc(
        devices=(
            _device(2, "Active")
            + "\n" + _device(4, "Inactive", active="false")
            + "\n" + _device(0, "Unassigned")
        )
    )
    assert parse_jts(xml).loads == {2: "Active"}


def test_buttons_parsed_and_mapped():
    """Configured buttons (ID 0-2) map to protocol buttons 1-3; rest skipped."""
    device = """\
    <Device>
      <DeviceID>5</DeviceID><Name>Den</Name>
      <Dimmer>true</Dimmer><SendThirdParty>true</SendThirdParty><Active>true</Active>
      <buttonList>
        <Button><ID>0</ID><tap><BtnAction>1</BtnAction></tap></Button>
        <Button><ID>1</ID><tap><BtnAction>0</BtnAction></tap>
          <pressandhold><BtnAction>2</BtnAction></pressandhold></Button>
        <Button><ID>2</ID><tap><BtnAction>0</BtnAction></tap></Button>
        <Button><ID>5</ID><tap><BtnAction>3</BtnAction></tap></Button>
      </buttonList>
    </Device>"""
    cfg = parse_jts(_doc(devices=device))
    # ID0 configured (tap) -> btn1; ID1 configured (hold) -> btn2;
    # ID2 has no non-zero action (skip); ID5 out of protocol range (skip).
    assert set(cfg.buttons) == {(5, 1), (5, 2)}
    assert cfg.buttons[(5, 1)] == "Den Button 1"


def test_button_action_compared_numerically():
    """BtnAction '00' / ' 0 ' are numerically zero -> not configured."""
    device = """\
    <Device>
      <DeviceID>5</DeviceID><Name>Den</Name>
      <SendThirdParty>true</SendThirdParty><Active>true</Active>
      <buttonList>
        <Button><ID>0</ID><tap><BtnAction>00</BtnAction></tap></Button>
        <Button><ID>1</ID><tap><BtnAction> 0 </BtnAction></tap></Button>
      </buttonList>
    </Device>"""
    assert parse_jts(_doc(devices=device)).buttons == {}


def test_buttons_skipped_for_non_thirdparty_device():
    device = """\
    <Device>
      <DeviceID>5</DeviceID><Name>Hidden</Name>
      <SendThirdParty>false</SendThirdParty><Active>true</Active>
      <buttonList><Button><ID>0</ID><tap><BtnAction>1</BtnAction></tap></Button></buttonList>
    </Device>"""
    assert parse_jts(_doc(devices=device)).buttons == {}


def test_unnamed_device_kept_with_empty_name():
    """JetStream devices are all real; an unnamed one is still a real load."""
    cfg = parse_jts(_doc(devices=_device(5, "")))
    assert cfg.loads == {5: ""}
    assert cfg.dimmable == {5: True}


def test_bad_xml_raises_valueerror():
    for bad in ("not xml at all", "<GulfStreamCL><unclosed>"):
        try:
            parse_jts(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_oversized_input_rejected():
    """A wildly oversized paste is refused before ElementTree sees it."""
    huge = "<GulfStreamCL>" + ("<x/>" * 3_000_000)  # ~12 MB > 8 MB cap
    try:
        parse_jts(huge)
    except ValueError:
        return
    raise AssertionError("expected ValueError for oversized input")


def test_handles_encoding_declaration():
    """A str carrying an encoding= declaration must not trip ElementTree."""
    cfg = parse_jts(_doc(devices=_device(7, "Den")))
    assert cfg.loads == {7: "Den"}


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
    print(f"\nPassed: {passed}, Failed: {len(failed)}")
    if failed:
        for name, tb in failed:
            print(f"\n--- FAIL: {name} ---\n{tb}")
        sys.exit(1)
