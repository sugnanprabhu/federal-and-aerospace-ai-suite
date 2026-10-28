# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/camera_config.py (run with tests/camera_config/run_tests.sh)."""
import copy
import json
import os
import sys
import tempfile
import unittest

import jsonschema
import yaml

REPO = os.environ.get("REPO_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import camera_config as cc  # noqa: E402

CAMERA = {"id": "front_camera"}


def _pipelines(name):
    with open(os.path.join(REPO, "configs", name), encoding="utf-8") as handle:
        return {p["name"]: p for p in json.load(handle)["config"]["pipelines"]}


MAIN = _pipelines("config-pymavlink.json")
SDK = _pipelines("config-uavsdk.json")
REALSENSE = _pipelines("config-pymavlink-realsense.json")
PIPELINES = set(MAIN)


def config(*cameras):
    return cc.parse_config({"version": 1, "cameras": [dict(c) for c in cameras]})


def plan_of(camera: dict) -> cc.CameraPlan:
    return cc.build_plan(config(camera), check_files=False)[0]


class DefaultsAndNames(unittest.TestCase):
    def test_shipped_file_is_valid(self):
        cfg = cc.load_config(os.path.join(REPO, "configs", "cameras-pymavlink.yaml"))
        self.assertEqual([c.id for c in cfg.cameras], ["front_camera", "bottom_camera", "back_camera", "realsense_camera"])
        plans = {p.camera_id: p for p in cc.build_plan(cfg, check_files=False, pipelines=PIPELINES)}
        for name in ("front_camera", "bottom_camera"):
            self.assertEqual(plans[name].pipeline, "uav_object_detection_npu_gpu")
        self.assertEqual(plans["back_camera"].pipeline, "uav_object_detection_npu")   # ships with captioning off
        live = cfg.cameras[3]                                     # the live camera (enabled or not, depending on the host)
        self.assertTrue(live.device and live.video is None)
        if live.enabled:
            self.assertTrue(plans["realsense_camera"].pipeline.startswith("uav_camera_"))

    def test_pipeline_name_for_every_combination(self):
        for det in ("cpu", "gpu", "npu"):
            self.assertEqual(plan_of({**CAMERA, "detection": {"device": det}}).pipeline, f"uav_object_detection_{det}")
            for vlm in ("cpu", "gpu"):
                camera = {**CAMERA, "detection": {"device": det}, "captioning": {"enabled": True, "device": vlm}}
                self.assertEqual(plan_of(camera).pipeline, f"uav_object_detection_{det}_{vlm}")

    def test_captioning_off_ignores_vlm_settings(self):
        plan = plan_of({**CAMERA, "captioning": {"enabled": False, "device": "cpu"}})
        self.assertEqual(plan.pipeline, "uav_object_detection_npu")
        self.assertNotIn("captioner_prompt", plan.request["parameters"])
        self.assertIsNone(plan.mqtt_topic)


class PlanContent(unittest.TestCase):
    def test_request_shape(self):
        plan = plan_of({**CAMERA, "video": "uav_sample_front.avi", "captioning": {"enabled": True, "prompt": "Count the cars."}})
        frame = plan.request["destination"]["frame"]
        self.assertEqual((frame["type"], frame["path"], frame["encoding"]), ("rtsp", "uav-front-camera", "h264"))
        self.assertNotIn("overlay", frame)                                  # on by default
        params = plan.request["parameters"]
        self.assertEqual(params["video"], "uav_sample_front.avi")
        self.assertEqual(params["captioner_prompt"], "Count the cars.")
        self.assertEqual(params["captioner_model_path"], f"{cc.RESOURCES_DIR}/ov_models/gpu/InternVL2-1B")
        self.assertEqual(params["mqtt_topic"], "uav/caption/front_camera")
        self.assertEqual(params["detection_model"], f"{cc.RESOURCES_DIR}/models/yolo11s/yolo11s_openvino_model/yolo11s.xml")

    def test_overlay_off_is_sent_to_the_destination(self):
        self.assertIs(plan_of({**CAMERA, "overlay": False}).request["destination"]["frame"]["overlay"], False)

    def test_overrides_for_stream_path_and_topic(self):
        plan = plan_of({**CAMERA, "rtsp_path": "my-stream", "mqtt_topic": "uav/caption/mine", "captioning": {"enabled": True}})
        self.assertEqual((plan.rtsp_path, plan.mqtt_topic), ("my-stream", "uav/caption/mine"))

    def test_disabled_cameras_are_not_planned(self):
        plans = cc.build_plan(config(CAMERA, {"id": "b_camera", "enabled": False}), check_files=False)
        self.assertEqual([p.camera_id for p in plans], ["front_camera"])

    def test_model_instances_are_shared_only_for_the_same_device_and_model(self):
        plans = cc.build_plan(config(
            {"id": "a"}, {"id": "b"}, {"id": "c", "detection": {"model": "other/model/other.xml"}}, {"id": "d", "detection": {"device": "gpu"}}),
            check_files=False)
        ids = {p.camera_id: p.request["parameters"]["detection_instance_id"] for p in plans}
        self.assertEqual(ids["a"], ids["b"])
        self.assertEqual(len({ids["a"], ids["c"], ids["d"]}), 3)
        self.assertEqual(ids["a"], "instnpu_yolo11s")

    def test_missing_files_are_reported(self):
        with tempfile.TemporaryDirectory() as resources:
            os.makedirs(f"{resources}/videos")
            with self.assertRaises(cc.ConfigError) as ctx:
                cc.build_plan(config({**CAMERA, "captioning": {"enabled": True}}), resources_dir=resources)
            message = str(ctx.exception)
            self.assertIn("video not found", message)
            self.assertIn("detection model not found", message)
            self.assertIn("captioning model not found", message)


class Validation(unittest.TestCase):
    def assertRejected(self, data, fragment):
        with self.assertRaises(cc.ConfigError) as ctx:
            cc.parse_config(data)
        self.assertIn(fragment, str(ctx.exception))

    def cam(self, **fields):
        return {"version": 1, "cameras": [{**CAMERA, **fields}]}

    def test_vlm_cannot_run_on_npu(self):
        self.assertRejected(self.cam(captioning={"enabled": True, "device": "npu"}), "cameras.0.captioning.device")

    def test_unknown_detection_device(self):
        self.assertRejected(self.cam(detection={"device": "tpu"}), "cameras.0.detection.device")

    def test_misspelled_keys_are_errors(self):
        self.assertRejected(self.cam(overlays=False), "Extra inputs are not permitted")
        self.assertRejected(self.cam(captioning={"enable": True}), "captioning.enable")

    def test_ids(self):
        for bad in ("Front", "1cam", "a b", "a/b", "", "x" * 33):
            self.assertRejected({"version": 1, "cameras": [{"id": bad}]}, "cameras.0.id")

    def test_videos_cannot_escape_the_folder(self):
        for bad in ("../x.avi", "/etc/passwd.avi", "a/b.avi", "clip.exe", "clip"):
            self.assertRejected(self.cam(video=bad), "cameras.0.video")

    def test_models_cannot_escape_the_models_folder(self):
        for bad in ("../x.xml", "/abs/m.xml", "a/../b.xml", "m.bin", "a b.xml"):
            self.assertRejected(self.cam(detection={"model": bad}), "cameras.0.detection.model")
        self.assertRejected(self.cam(captioning={"enabled": True, "model": "../x"}), "cameras.0.captioning.model")

    def test_prompt_cannot_break_the_pipeline_string(self):
        for bad in ('say "hi"', "a{b}", "a\nb", "a;b", "a\\b", ""):
            self.assertRejected(self.cam(captioning={"enabled": True, "prompt": bad}), "captioning.prompt")

    def test_token_limits(self):
        for bad in (0, 4097):
            self.assertRejected(self.cam(captioning={"enabled": True, "max_new_tokens": bad}), "max_new_tokens")

    def test_duplicates(self):
        self.assertRejected({"version": 1, "cameras": [{"id": "a"}, {"id": "a"}]}, "duplicate camera id 'a'")
        self.assertRejected({"version": 1, "cameras": [{"id": "a", "rtsp_path": "s"}, {"id": "b", "rtsp_path": "s"}]}, "duplicate RTSP path 's'")

    def test_topic_and_path_patterns(self):
        self.assertRejected(self.cam(mqtt_topic="other/topic"), "mqtt_topic")
        self.assertRejected(self.cam(rtsp_path="a/b"), "rtsp_path")

    def test_version_and_shape(self):
        self.assertRejected({"version": 2, "cameras": [{"id": "a"}]}, "unsupported version 2")
        self.assertRejected({"version": 1, "cameras": []}, "cameras")
        self.assertRejected(["not", "a", "mapping"], "must be a mapping")

    def test_every_problem_is_listed(self):
        with self.assertRaises(cc.ConfigError) as ctx:
            cc.parse_config(self.cam(video="../x.avi", detection={"device": "tpu"}, overlays=1))
        self.assertGreaterEqual(len(str(ctx.exception).splitlines()), 3)


class Loading(unittest.TestCase):
    def write(self, text, suffix=".yaml"):
        handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_yaml_and_json_files(self):
        self.assertEqual(cc.load_config(self.write("version: 1\ncameras:\n  - id: a\n")).cameras[0].id, "a")
        self.assertEqual(cc.load_config(self.write('{"version": 1, "cameras": [{"id": "b"}]}', ".json")).cameras[0].id, "b")

    def test_yaml_booleans_do_not_turn_devices_into_booleans(self):
        # YAML 1.1 reads no/off/on as booleans; the schema must reject them instead of accepting a wrong type
        with self.assertRaises(cc.ConfigError):
            cc.load_config(self.write("version: 1\ncameras:\n  - id: a\n    detection: {device: on}\n"))

    def test_unreadable_and_broken_files(self):
        with self.assertRaises(cc.ConfigError) as ctx:
            cc.load_config("/nonexistent/cameras-pymavlink.yaml")
        self.assertIn("cannot read", str(ctx.exception))
        with self.assertRaises(cc.ConfigError) as ctx:
            cc.load_config(self.write("cameras: [ {"))
        self.assertIn("not valid YAML", str(ctx.exception))

    def test_unsafe_yaml_tags_are_not_executed(self):
        with self.assertRaises(cc.ConfigError):
            cc.load_config(self.write("version: 1\ncameras: !!python/object/apply:os.system ['true']\n"))

    def test_json_schema_for_forms(self):
        schema = cc.json_schema()
        self.assertIn("cameras", schema["properties"])
        self.assertEqual(sorted(schema["$defs"]["Detection"]["properties"]["device"]["enum"]), ["cpu", "gpu", "npu"])
        self.assertEqual(sorted(schema["$defs"]["Captioning"]["properties"]["device"]["enum"]), ["cpu", "gpu"])


class MatchesThePipelineDefinitions(unittest.TestCase):
    """Every request this module builds must satisfy the parameter schema of the pipeline it targets."""

    @classmethod
    def setUpClass(cls):
        cls.pipelines = MAIN

    def test_all_pipelines_exist(self):
        video = {f"uav_object_detection_{d}" for d in ("cpu", "gpu", "npu")} | {
            f"uav_object_detection_{d}_{v}" for d in ("cpu", "gpu", "npu") for v in ("cpu", "gpu")}
        camera = {n.replace("uav_object_detection_", "uav_camera_") for n in video}
        self.assertEqual(video | {"uav_camera_npu", "uav_camera_npu_gpu"}, set(self.pipelines))
        self.assertEqual(camera, set(REALSENSE))

    def test_requests_satisfy_the_pipeline_schemas(self):
        for det in ("cpu", "gpu", "npu"):
            for vlm in (None, "cpu", "gpu"):
                camera = {**CAMERA, "detection": {"device": det}, "captioning": {"enabled": vlm is not None, "device": vlm or "gpu"}}
                plan = plan_of(camera)
                schema = self.pipelines[plan.pipeline]["parameters"]
                try:
                    jsonschema.validate(plan.request["parameters"], schema)
                except jsonschema.ValidationError as exc:
                    self.fail(f"{plan.pipeline}: {exc.message}")

    def test_every_parameter_sent_is_known_to_the_pipeline(self):
        for plan in cc.build_plan(config(CAMERA, {"id": "b", "captioning": {"enabled": True}}), check_files=False):
            unknown = set(plan.request["parameters"]) - set(self.pipelines[plan.pipeline]["parameters"]["properties"])
            self.assertFalse(unknown, f"{plan.pipeline}: {unknown}")

    def test_shared_pipelines_schemas_reject_bad_values(self):
        schema = self.pipelines["uav_object_detection_npu_gpu"]["parameters"]
        for key, bad in (("video", "../x.avi"), ("captioner_prompt", 'a"b'), ("mqtt_topic", "x/y"), ("detection_instance_id", "A b")):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({key: bad}, copy.deepcopy(schema))


class LiveCameras(unittest.TestCase):
    DEV = "/dev/video4"

    def live(self, **extra):
        return {"id": "realsense_front", "device": self.DEV, **extra}

    def test_device_replaces_video_in_the_request(self):
        params = plan_of(self.live()).request["parameters"]
        self.assertEqual((params["device"], params["camera_width"], params["camera_height"], params["camera_fps"]), (self.DEV, 1280, 720, 30))
        self.assertNotIn("video", params)

    def test_size_can_be_set(self):
        params = plan_of(self.live(width=640, height=480)).request["parameters"]
        self.assertEqual((params["camera_width"], params["camera_height"]), (640, 480))

    def test_frame_rate_can_be_set_and_is_always_sent(self):
        # without a framerate in the camera caps, v4l2src settled on 5 fps once a captioning branch was added
        self.assertEqual(plan_of(self.live(fps=15)).request["parameters"]["camera_fps"], 15)
        for name, pipeline in {**MAIN, **REALSENSE}.items():
            if name.startswith("uav_camera_"):
                self.assertIn("framerate={parameters[camera_fps]}/1", pipeline["pipeline"], name)
        with self.assertRaisesRegex(cc.ConfigError, "live camera"):
            config({"id": "f", "fps": 15})

    def test_a_file_camera_sends_no_device(self):
        params = plan_of({"id": "f"}).request["parameters"]
        self.assertEqual(params["video"], "uav_sample_front.avi")
        self.assertFalse({"device", "camera_width", "camera_height", "camera_fps"} & set(params))

    def test_video_and_device_are_exclusive(self):
        with self.assertRaisesRegex(cc.ConfigError, "only one of"):
            config(self.live(video="uav_sample_front.avi"))

    def test_size_needs_a_device(self):
        with self.assertRaisesRegex(cc.ConfigError, "live camera"):
            config({"id": "f", "width": 640})

    def test_device_names(self):
        for good in ("/dev/video0", "/dev/video36", "/dev/v4l/by-id/usb-Intel_R__RealSense-video-index4",
                     "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2:1.0-video-index0"):
            config(self.live(device=good))
        for bad in ("/dev/sda", "/dev/video", "video0", "/dev/v4l/by-id/../../sda", "/dev/video0; rm", "/tmp/video0"):
            with self.assertRaises(cc.ConfigError, msg=bad):
                config(self.live(device=bad))

    def test_a_device_can_serve_one_camera_only(self):
        with self.assertRaisesRegex(cc.ConfigError, "duplicate camera device"):
            config(self.live(), {**self.live(), "id": "other"})

    def test_missing_device_is_an_error_by_default(self):
        with self.assertRaisesRegex(cc.ConfigError, "not found"):
            cc.build_plan(config(self.live(device="/dev/video99")), check_files=True)

    def test_missing_device_is_left_out_when_asked(self):
        with tempfile.TemporaryDirectory() as root:
            for sub in ("videos", "models/yolo11s/yolo11s_openvino_model"):
                os.makedirs(os.path.join(root, sub))
            open(os.path.join(root, "videos", "uav_sample_front.avi"), "w").close()
            open(os.path.join(root, "models/yolo11s/yolo11s_openvino_model/yolo11s.xml"), "w").close()
            unavailable = []
            plans = cc.build_plan(config({"id": "f"}, self.live(device="/dev/video99")), resources_dir=root, unavailable=unavailable)
        self.assertEqual([p.camera_id for p in plans], ["f"])
        self.assertEqual(len(unavailable), 1)
        self.assertIn("realsense_front", unavailable[0])

    def test_a_node_that_is_not_a_device_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIn("not a camera device", cc._device_problem(root))  # pylint: disable=protected-access
        self.assertIsNone(cc._device_problem("/dev/null"))  # pylint: disable=protected-access

    def test_requests_satisfy_every_pipeline_schema(self):
        pipelines = {**MAIN, **REALSENSE}
        for det in ("cpu", "gpu", "npu"):
            for vlm in (None, "cpu", "gpu"):
                plan = plan_of(self.live(detection={"device": det}, captioning={"enabled": vlm is not None, "device": vlm or "gpu"}))
                jsonschema.validate(plan.request["parameters"], pipelines[plan.pipeline]["parameters"])
                unknown = set(plan.request["parameters"]) - set(pipelines[plan.pipeline]["parameters"]["properties"])
                self.assertFalse(unknown, unknown)

    def test_live_cameras_use_their_own_pipelines(self):
        for det in ("cpu", "gpu", "npu"):
            self.assertEqual(plan_of(self.live(detection={"device": det})).pipeline, f"uav_camera_{det}")
            for vlm in ("cpu", "gpu"):
                camera = self.live(detection={"device": det}, captioning={"enabled": True, "device": vlm})
                self.assertEqual(plan_of(camera).pipeline, f"uav_camera_{det}_{vlm}")

    def test_the_pipeline_files_have_clean_separate_sources(self):
        pipelines = {**MAIN, **REALSENSE}
        self.assertEqual(len(pipelines), 18)
        self.assertEqual(sorted(n for n in MAIN if n.startswith("uav_camera_")), ["uav_camera_npu", "uav_camera_npu_gpu"])
        for name, pipeline in pipelines.items():
            text = pipeline["pipeline"].replace("{% raw %}", "").replace("{% endraw %}", "")
            self.assertNotIn("{%", text, f"{name}: no template logic in the pipelines")
            live = name.startswith("uav_camera_")
            self.assertEqual(text.startswith("v4l2src device={parameters[device]}"), live, name)
            self.assertEqual(text.startswith("multifilesrc"), not live, name)
            props = pipeline["parameters"]["properties"]
            self.assertEqual("device" in props, live, name)
            self.assertEqual("video" in props, not live, name)
        # a camera pipeline is its video twin with only the source and its parameters replaced
        for name, pipeline in pipelines.items():
            if not name.startswith("uav_camera_"):
                continue
            twin = pipelines["uav_object_detection_" + name[len("uav_camera_"):]]
            self.assertEqual(pipeline["pipeline"].split(" ! videoconvert ! video/x-raw,format=NV12", 1)[1],
                             twin["pipeline"].split(" ! identity sync=true", 1)[1], name)
        for name in ("uav_camera_npu", "uav_camera_npu_gpu"):          # the two shipped ones are the reference ones
            self.assertEqual(MAIN[name], REALSENSE[name])

    def test_a_camera_needs_its_pipeline_in_the_servers_config(self):
        plans = cc.build_plan(config(self.live(captioning={"enabled": True})), check_files=False, pipelines=PIPELINES)
        self.assertEqual(plans[0].pipeline, "uav_camera_npu_gpu")
        with self.assertRaisesRegex(cc.ConfigError, "uav_camera_gpu is not in the pipeline server's config.json.*config-pymavlink-realsense.json"):
            cc.build_plan(config(self.live(detection={"device": "gpu"})), check_files=False, pipelines=PIPELINES)
        with self.assertRaisesRegex(cc.ConfigError, "uav_object_detection_cpu_cpu is not"):
            cc.build_plan(config({"id": "f", "detection": {"device": "cpu"}, "captioning": {"enabled": True, "device": "cpu"}}),
                          check_files=False, pipelines={"uav_object_detection_cpu"})
        cc.build_plan(config(self.live(detection={"device": "gpu"})), check_files=False)      # not checked without a list


class RtspCameras(unittest.TestCase):
    URL = "rtsp://host.docker.internal:8554/{uav_id}/nadir"

    def rtsp(self, **extra):
        return {"id": "nadir_camera", "rtsp": self.URL, **extra}

    def test_uav_id_is_a_parameter_with_the_default_uav_1(self):
        os.environ.pop("UAV_ID", None)
        params = plan_of(self.rtsp()).request["parameters"]
        self.assertEqual(params["rtsp_url"], "rtsp://host.docker.internal:8554/uav-1/nadir")
        self.assertNotIn("video", params)
        os.environ["UAV_ID"] = "uav-7"
        try:
            self.assertEqual(plan_of(self.rtsp()).request["parameters"]["rtsp_url"], "rtsp://host.docker.internal:8554/uav-7/nadir")
            self.assertEqual(plan_of(self.rtsp()).source_url, "rtsp://host.docker.internal:8554/uav-7/nadir")
            os.environ["UAV_ID"] = "uav 7; x"
            with self.assertRaisesRegex(cc.ConfigError, "UAV_ID"):
                plan_of(self.rtsp())
        finally:
            os.environ.pop("UAV_ID", None)

    def test_same_pipeline_names_as_the_video_cameras(self):
        self.assertEqual(plan_of(self.rtsp(detection={"device": "gpu"})).pipeline, "uav_object_detection_gpu")
        self.assertEqual(plan_of(self.rtsp(captioning={"enabled": True})).pipeline, "uav_object_detection_npu_gpu")

    def test_only_one_source(self):
        for other in ({"video": "a.avi"}, {"device": "/dev/video0"}):
            with self.assertRaisesRegex(cc.ConfigError, "only one of"):
                config(self.rtsp(**other))
        with self.assertRaisesRegex(cc.ConfigError, "live camera"):
            config(self.rtsp(width=640))

    def test_urls(self):
        config(self.rtsp(rtsp="rtsp://10.1.2.3:8554/uav-1/forward"))
        for bad in ("http://x/a", "rtsp://user:pw@host/a", "rtsp://host/a b", "rtsp://host:8554/{other}/x", "rtsp://host/{uav_id", "rtsp://host", "file:///etc/passwd"):
            with self.assertRaises(cc.ConfigError, msg=bad):
                config(self.rtsp(rtsp=bad))

    def test_the_sdk_pipelines_are_the_same_names_with_an_rtsp_source(self):
        self.assertEqual(set(SDK), {n for n in MAIN if n.startswith("uav_object_detection_")})
        for name, pipeline in SDK.items():
            text = pipeline["pipeline"].replace("{% raw %}", "").replace("{% endraw %}", "")
            self.assertTrue(text.startswith("rtspsrc location={parameters[rtsp_url]}"), name)
            self.assertNotIn("{%", text)
            self.assertIn("telemetry-overlay-uavsdk.py", text)
            self.assertNotIn("pymavlink", text)
            props = pipeline["parameters"]["properties"]
            self.assertIn("rtsp_url", props)
            self.assertNotIn("video", props)
            twin = MAIN[name]["pipeline"].replace("{% raw %}", "").replace("{% endraw %}", "")
            twin = twin.split(" ! identity sync=true", 1)[1].replace("telemetry-overlay-pymavlink.py", "telemetry-overlay-uavsdk.py")
            tail = text.split(" ! decodebin3", 1)[1]
            # the SDK's H.264 (4:4:4 from its raw-video encoder) must be converted to NV12 before the analytics elements
            self.assertTrue(tail.startswith(" ! videoconvert ! video/x-raw,format=NV12"), name)
            self.assertEqual(tail[len(" ! videoconvert ! video/x-raw,format=NV12"):], twin, name)

    def test_the_shipped_sdk_cameras_satisfy_the_sdk_pipelines(self):
        cfg = cc.load_config(os.path.join(REPO, "configs", "cameras-uavsdk.yaml"))
        plans = cc.build_plan(cfg, check_files=False, pipelines=set(SDK))
        self.assertEqual([p.camera_id for p in plans], ["nadir_camera", "forward_camera", "rear_camera"])
        expected = [f"uav_object_detection_{c.detection.device}" + (f"_{c.captioning.device}" if c.captioning.enabled else "") for c in cfg.cameras]
        self.assertEqual([p.pipeline for p in plans], expected)       # whichever of the cameras have captions switched on
        self.assertEqual([c.detection.device for c in cfg.cameras], ["cpu", "gpu", "npu"])
        for plan in plans:
            jsonschema.validate(plan.request["parameters"], SDK[plan.pipeline]["parameters"])
            self.assertTrue(plan.source_url.startswith("rtsp://host.docker.internal:8554/uav-1/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
