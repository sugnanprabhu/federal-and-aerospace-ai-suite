# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The cameras of configs/cameras-pymavlink.yaml through the manager, without a vehicle (--start-now).

  * all shipped cameras start one after the other and stream at the same time, each with its own caption topic
  * --status agrees with the file, and notices when the file asks for something else
  * overlay and prompt settings reach the pipeline and the output
  * a broken or invalid file never replaces a working configuration, and an unusable file at start-up is refused
  * SIGTERM (docker stop) stops the cameras and the manager exits
  * a video prepared with scripts/convert_video.sh works as a camera
"""
import os
import subprocess
import time
import unittest

import common

SHIPPED = os.path.join(common.REPO, "configs", "cameras-pymavlink.yaml")


def _shipped():
    import yaml
    with open(SHIPPED, encoding="utf-8") as handle:
        return [c for c in yaml.safe_load(handle)["cameras"] if c.get("enabled", True)]


def shipped_cameras():
    """Ids of the enabled cameras of the shipped file (a disabled one, such as the live camera, does not stream)."""
    return [c["id"] for c in _shipped()]


def shipped_captioning_cameras():
    """Ids of the enabled cameras that have captioning switched on."""
    return [c["id"] for c in _shipped() if (c.get("captioning") or {}).get("enabled", False)]


class DefaultCameras(common.LiveTestCase):
    def test_shipped_cameras_stream_together(self):
        with open(SHIPPED, encoding="utf-8") as handle:
            manager = common.Manager(handle.read()).start()
        try:
            cameras = shipped_cameras()
            self.assertTrue(manager.wait_for("is RUNNING", count=len(cameras), timeout=420), manager.log())
            self.assertNotIn("Giving up", manager.log())
            for camera in cameras:
                path = "uav-" + camera.replace("_", "-")
                self.assertGreaterEqual(common.frames_at_least(path, 90), 90, f"{path} is not streaming")
            for camera in shipped_captioning_cameras():
                self.assertTrue(common.caption(f"uav/caption/{camera}"), f"no caption for {camera}")
            for line in manager.status().splitlines()[1:]:
                self.assertTrue(line.rstrip().endswith("yes"), line)
            self.assertEqual(len(common.running_pipelines()), len(cameras))
        finally:
            manager.cleanup()

    def test_sigterm_stops_the_cameras(self):
        manager = common.Manager("version: 1\ncameras:\n  - id: a_camera\n    detection: {device: npu}\n").start()
        try:
            self.assertTrue(manager.wait_for("is RUNNING"), manager.log())
            manager.stop()
            self.assertIn("Stopping the cameras before exit", manager.log())
            self.assertEqual(common.docker_exec("pgrep", "-f", common.MANAGER_PROCESS).stdout.strip(), "")
            time.sleep(5)
            self.assertEqual(common.running_pipelines(), [])
        finally:
            manager.cleanup()


class Settings(common.LiveTestCase):
    def test_overlay_and_prompt_reach_the_pipeline(self):
        manager = common.Manager("""
            version: 1
            cameras:
              - id: test_camera
                overlay: false
                detection: {device: npu}
                captioning: {enabled: true, device: gpu, prompt: "Count the cars and the buses.", max_new_tokens: 40}
            """).start()
        try:
            self.assertTrue(manager.wait_for("is RUNNING"), manager.log())
            request = common.pipeline_request(common.running_pipelines()[0]["id"])
            self.assertIs(request["destination"]["frame"].get("overlay"), False)
            self.assertEqual(request["parameters"]["captioner_prompt"], "Count the cars and the buses.")
            self.assertEqual(request["parameters"]["captioner_max_new_tokens"], 40)
            self.assertEqual(request["parameters"]["mqtt_topic"], "uav/caption/test_camera")
            self.assertEqual(request["parameters"]["overlay_label"], "test-camera-NPU-GPU-Caption")
            # connect to the stream first: the first client of a stream that nobody has watched for a while is
            # served late (it was measured at 10 s after 90 s idle), which a caption wait of a minute would cause
            self.assertGreaterEqual(common.frames_at_least("uav-test-camera", 90), 90)
            self.assertTrue(common.caption("uav/caption/test_camera"))
        finally:
            manager.cleanup()

    def test_status_notices_a_changed_file(self):
        manager = common.Manager("version: 1\ncameras:\n  - id: test_camera\n    detection: {device: gpu}\n").start()
        try:
            self.assertTrue(manager.wait_for("is RUNNING"), manager.log())
            self.assertIn("yes", manager.status().splitlines()[1])
            manager.write("version: 1\ncameras:\n  - id: test_camera\n    overlay: false\n    detection: {device: gpu}\n")
            self.assertIn("NO", manager.status().splitlines()[1])
        finally:
            manager.cleanup()

    def test_converted_user_video(self):
        source, clip = os.path.join(common.REPO, "resources", "videos", "uav_sample_front.avi"), "uavtest_clip"
        mp4, avi = (os.path.join(common.REPO, "resources", "videos", f"{clip}.{ext}") for ext in ("mp4", "avi"))
        try:
            subprocess.run([os.path.join(common.REPO, "scripts", "convert_video.sh"), source, "-o", mp4.replace(".mp4", "_src.avi"), "-n", "250", "-f"],
                           check=True, capture_output=True)
            os.replace(mp4.replace(".mp4", "_src.avi"), avi)
            manager = common.Manager(f"version: 1\ncameras:\n  - id: test_camera\n    video: {clip}.avi\n    detection: {{device: gpu}}\n").start()
            try:
                self.assertTrue(manager.wait_for("is RUNNING"), manager.log())
                self.assertGreaterEqual(common.frames_at_least("uav-test-camera", 90), 90)
            finally:
                manager.cleanup()
        finally:
            for path in (mp4, avi, mp4.replace(".mp4", "_src.avi")):
                if os.path.exists(path):
                    os.unlink(path)


class BrokenFiles(common.LiveTestCase):
    def test_unusable_file_at_start_is_refused(self):
        manager = common.Manager("version: 1\ncameras:\n  - id: Bad Id\n", start_now=True).start()
        try:
            self.assertTrue(manager.wait_for("No usable camera configuration", timeout=60), manager.log())
            time.sleep(3)
            self.assertEqual(common.docker_exec("pgrep", "-f", common.MANAGER_PROCESS).stdout.strip(), "")
            self.assertEqual(common.running_pipelines(), [])
        finally:
            manager.cleanup()

    def test_check_cameras_reports_every_problem(self):
        manager = common.Manager("version: 1\ncameras:\n  - id: a\n    video: ../x.avi\n    detection: {device: tpu}\n    overlays: 1\n")
        try:
            result = common.docker_exec("python3", "/home/pipeline-server/scripts/camera_config.py", "check", manager.config)
            self.assertEqual(result.returncode, 1)
            for fragment in ("cameras.0.video", "cameras.0.detection.device", "cameras.0.overlays"):
                self.assertIn(fragment, result.stderr)
        finally:
            manager.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
