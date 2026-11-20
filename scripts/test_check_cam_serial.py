import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "check_cam_serial", pathlib.Path(__file__).parent / "check-cam-serial.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ARM_FRAG = "086f4921-8427-47b6-92e0-dbdc87f2267c"
OBSTACLES = "01ca42e4-78b9-425c-ae75-1101b9a7f3e8"
RS = "viam:camera:realsense"


def cfg(serial="746612070153"):
    return {
        "fragments": [
            {"id": ARM_FRAG, "variables": {"arm-name": "arm", "cam-serial-number": serial,
                                           "arm-ip-address": "192.168.1.201"}},
            {"id": OBSTACLES, "variables": {}},
            {"id": "shell"},
        ],
        "services": [{"name": "teleop", "model": "viam:teleop:leader-follower"}],
    }


# --- credentials ---

def test_creds_from_environment_win(tmp_path):
    (tmp_path / "orgkey.txt").write_text("VIAM_API_KEY_ID=file-id\nVIAM_API_KEY=file-key\n")
    env = {"VIAM_API_KEY_ID": "env-id", "VIAM_API_KEY": "env-key"}
    assert m.load_creds(tmp_path, env) == ("env-id", "env-key")


def test_creds_from_orgkey_file(tmp_path):
    (tmp_path / "orgkey.txt").write_text("# minted by bootstrap\nVIAM_API_KEY_ID=file-id\n"
                                         "VIAM_API_KEY= file-key \n")
    assert m.load_creds(tmp_path, {}) == ("file-id", "file-key")


def test_creds_missing_exits(tmp_path):
    with pytest.raises(SystemExit):
        m.load_creds(tmp_path, {})


# --- machine lookup ---

class Robot:
    def __init__(self, name, id):
        self.name, self.id = name, id


def test_find_machine_by_name():
    robots = [Robot("armfarm11", "a"), Robot("armfarm14", "b")]
    assert m.find_machine(robots, "armfarm14").id == "b"


def test_find_machine_refuses_duplicate_names():
    # Two machines with one name is how armfarm11 was misread as offline (2026-11-20).
    robots = [Robot("armfarm11", "a"), Robot("armfarm11", "b")]
    with pytest.raises(LookupError, match="2 machines named armfarm11"):
        m.find_machine(robots, "armfarm11")


def test_find_machine_missing():
    with pytest.raises(LookupError):
        m.find_machine([Robot("armfarm11", "a")], "armfarm99")


# --- config edits ---

def test_serial_variable_found_on_arm_fragment():
    assert m.find_serial_variable(cfg(), {ARM_FRAG}) == (0, "746612070153")


def test_serial_variable_found_on_unknown_fragment_that_declares_it():
    c = cfg()
    c["fragments"][0]["id"] = "some-other-arm-fragment"
    assert m.find_serial_variable(c, {ARM_FRAG}) == (0, "746612070153")


def test_serial_variable_absent_when_arm_fragment_has_none():
    c = cfg()
    del c["fragments"][0]["variables"]["cam-serial-number"]
    assert m.find_serial_variable(c, {ARM_FRAG}) == (0, None)


def test_no_arm_fragment_raises():
    c = cfg()
    c["fragments"] = c["fragments"][1:]
    with pytest.raises(ValueError):
        m.find_serial_variable(c, {ARM_FRAG})


def test_add_and_remove_discovery_service_leave_the_rest_alone():
    before = cfg()
    added = m.add_discovery_service(before, m.TEMP_SERVICE)
    assert added["services"][-1] == {"name": m.TEMP_SERVICE, "api": "rdk:service:discovery",
                                     "model": "viam:realsense:discovery"}
    assert before == cfg()  # input not mutated
    assert m.remove_service(added, m.TEMP_SERVICE) == cfg()


def test_add_discovery_service_to_config_without_services():
    c = cfg()
    del c["services"]
    added = m.add_discovery_service(c, m.TEMP_SERVICE)
    assert [s["name"] for s in added["services"]] == [m.TEMP_SERVICE]
    assert m.remove_service(added, m.TEMP_SERVICE) == c


def test_add_discovery_service_refuses_name_in_use():
    c = m.add_discovery_service(cfg(), m.TEMP_SERVICE)
    with pytest.raises(ValueError):
        m.add_discovery_service(c, m.TEMP_SERVICE)


def test_with_serial_sets_only_that_variable():
    c = cfg()
    new = m.with_serial(c, 0, "927522073265")
    assert new["fragments"][0]["variables"]["cam-serial-number"] == "927522073265"
    assert new["fragments"][0]["variables"]["arm-ip-address"] == "192.168.1.201"
    assert new["fragments"][1:] == c["fragments"][1:]
    assert c == cfg()


def test_with_serial_creates_variables_when_missing():
    c = cfg()
    del c["fragments"][0]["variables"]
    assert m.with_serial(c, 0, "1")["fragments"][0]["variables"] == {"cam-serial-number": "1"}


# --- discovery results and the verdict ---

def test_discovered_serials_skip_other_models_and_recovery():
    found = [("realsense-927522073265", RS, {"serial_number": "927522073265"}),
             ("realsense-ABC-recovery", RS, {"serial_number": "ABC"}),
             ("webcam", "rdk:builtin:webcam", {"video_path": "x"})]
    assert m.discovered_serials(found) == (["927522073265"], ["ABC"])


def test_verdict_match():
    assert m.verdict("927522073265", ["927522073265"], [])[0] == "match"


def test_verdict_mismatch_proposes_the_one_discovered():
    status, proposed, _ = m.verdict("746612070153", ["927522073265"], [])
    assert (status, proposed) == ("mismatch", "927522073265")


def test_verdict_unset_proposes_the_one_discovered():
    status, proposed, _ = m.verdict(None, ["927522073265"], [])
    assert (status, proposed) == ("mismatch", "927522073265")


def test_verdict_none_found():
    status, proposed, msg = m.verdict("746612070153", [], [])
    assert (status, proposed) == ("none", None) and "USB" in msg


def test_verdict_recovery_only():
    status, proposed, _ = m.verdict("746612070153", [], ["ABC"])
    assert (status, proposed) == ("recovery", None)


def test_verdict_several_cameras_never_proposes():
    assert m.verdict("1", ["1", "2"], [])[:2] == ("match", None)
    assert m.verdict("3", ["1", "2"], [])[:2] == ("ambiguous", None)
