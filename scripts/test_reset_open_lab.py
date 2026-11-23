import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "reset_open_lab", pathlib.Path(__file__).parent / "reset-open-lab.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FRAGS = [{"id": "arm-frag", "variables": {"arm-ip-address": "192.168.1.215"}},
         {"id": m.OBSTACLES_FRAGMENT, "variables": {}},
         {"id": "shell"}]


def cfg():
    return {
        "fragments": FRAGS,
        "fragment_mods": [{"fragment_id": "arm-frag", "mods": []}],
        "components": [
            {"name": "overview-cam", "model": "rdk:builtin:webcam",
             "attributes": {"video_path": "/dev/video38", "format": "YUYV",
                            "width_px": 640, "height_px": 480},
             "service_configs": [{"type": "data_manager"}]},
            {"name": "leader-arm", "model": "devrel:so101:arm"},
            {"name": "microphone-3", "model": "viam:system-audio:microphone",
             "attributes": {"device_id": "x"}},
        ],
        "services": [{"name": "teleop"}, {"name": "data_manager"}],
        "modules": [{"name": "viam_system-audio", "module_id": "viam:system-audio"},
                    {"name": "viam_teleop", "module_id": "viam:teleop"}],
        "packages": [{"name": "model-1"}],
    }


def test_keeps_fragments_mic_and_repoints_webcam():
    new, removed = m.build_open_lab_config(cfg(), "usb-Cam-video-index0")
    assert new["fragments"] == FRAGS
    assert "fragment_mods" not in new
    names = [c["name"] for c in new["components"]]
    assert names == ["overview-cam", "microphone-3"]
    cam = new["components"][0]
    assert cam["attributes"] == {"video_path": "usb-Cam-video-index0", "format": "YUYV",
                                 "width_px": 640, "height_px": 480}
    assert "service_configs" not in cam
    assert [x["module_id"] for x in new["modules"]] == ["viam:system-audio"]
    assert "services" not in new and "packages" not in new
    assert removed["components"] == ["leader-arm"]
    assert removed["services"] == ["teleop", "data_manager"]
    assert removed["modules"] == ["viam_teleop"]
    assert removed["packages"] == ["model-1"]
    assert removed["fragment_mods"] is True


def test_adds_overview_cam_when_absent():
    c = cfg()
    c["components"] = [x for x in c["components"] if x["name"] != "overview-cam"]
    new, _ = m.build_open_lab_config(c, "usb-Cam-video-index0")
    assert new["components"][0]["attributes"] == {"video_path": "usb-Cam-video-index0"}


def test_no_webcam_drops_overview_cam():
    new, removed = m.build_open_lab_config(cfg(), None)
    assert [c["name"] for c in new["components"]] == ["microphone-3"]
    assert "overview-cam" in removed["components"]


def test_refuses_without_obstacles_fragment():
    c = cfg()
    c["fragments"] = [f for f in FRAGS if f["id"] != m.OBSTACLES_FRAGMENT]
    with pytest.raises(ValueError):
        m.build_open_lab_config(c, None)


def test_pick_webcam_skips_realsense():
    found = [("rdk:builtin:webcam", {"video_path": "pci-0000:00:0d.0-usb-0:2.4:1.3-video-index0"}),
             ("rdk:builtin:webcam", {"video_path": "usb-Intel_R__RealSense_TM__Depth_Camera_435_x-video-index0"}),
             ("rdk:builtin:webcam", {"video_path": "usb-USB_Camera_USB_Camera_1-video-index0"})]
    assert m.pick_webcam(found) == "usb-USB_Camera_USB_Camera_1-video-index0"
    assert m.pick_webcam(found[:2]) is None
