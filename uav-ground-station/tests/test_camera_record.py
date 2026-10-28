# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""What the dashboard learns about a camera from its pipeline's start request (no UAV host needed).

Needs the backend's dependencies, so run it in the backend image (make up builds it):
  docker run --rm -v $PWD/../backend:/app:ro -v $PWD:/t:ro --entrypoint python3 uav-ground-station-backend:local /t/test_camera_record.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.environ.get("BACKEND_DIR", "/app"))
import main  # noqa: E402


def record(pipeline, parameters, **frame):
    request = {"pipeline": {"name": "user_defined_pipelines", "version": pipeline}, "parameters": parameters}
    return main._camera_record({"state": "RUNNING"}, request, "uav-realsense-front", {"path": "uav-realsense-front", **frame})  # pylint: disable=protected-access


class CameraRecord(unittest.TestCase):
    def test_video_camera(self):
        info = record("uav_object_detection_npu_gpu", {"video": "uav_sample_front.avi", "mqtt_topic": "uav/caption/front_camera"})
        self.assertEqual((info["video"], info["device"], info["camera_size"]), ("uav_sample_front.avi", None, None))
        self.assertTrue(info["captioning"])

    def test_live_camera(self):
        info = record("uav_camera_npu", {"device": "/dev/video4", "camera_width": 1280, "camera_height": 720})
        self.assertEqual((info["video"], info["device"], info["camera_size"]), (None, "/dev/video4", "1280x720"))
        self.assertEqual((info["detection_device"], info["captioning"], info["mqtt_topic"]), ("npu", False, None))

    def test_rtsp_camera(self):
        info = record("uav_object_detection_cpu", {"rtsp_url": "rtsp://host.docker.internal:8554/uav-1/nadir"})
        self.assertEqual((info["rtsp_url"], info["video"], info["device"]), ("rtsp://host.docker.internal:8554/uav-1/nadir", None, None))

    def test_live_camera_with_captions_and_no_overlay(self):
        info = record("uav_camera_gpu_cpu", {"device": "/dev/video2", "camera_width": 640, "camera_height": 480}, overlay=False)
        self.assertEqual((info["camera_size"], info["vlm_device"], info["overlay"], info["mqtt_topic"]),
                         ("640x480", "cpu", False, "uav/caption/realsense_front"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
