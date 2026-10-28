# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The arming flow of `make start-rtsp`: cameras start when the simulated vehicle is armed and stop when disarmed.

This ARMS AND DISARMS THE SIMULATED VEHICLE, so it only runs with UAV_TEST_ALLOW_ARM=1 and only when no ground
station (QGroundControl) is connected. The PX4 auto-disarm timer is raised for the test and restored afterwards.

  * the manager waits quietly while disarmed and picks up edits to the file (valid ones applied, broken ones refused)
  * arming starts the enabled cameras; an edit while armed is only noted
  * disarming stops them; arming again straight away works and then applies the edit
"""
import os
import time
import unittest

import common

CAMERAS = """
    version: 1
    cameras:
      - id: front_camera
        overlay: {overlay}
        detection: {{device: npu}}
        captioning: {{enabled: true, device: gpu, prompt: "{prompt}"}}
      - id: bottom_camera
        video: uav_sample_bottom.avi
        detection: {{device: {bottom}}}
"""


def cameras(overlay="true", prompt="Describe the road.", bottom="npu"):
    return CAMERAS.format(overlay=overlay, prompt=prompt, bottom=bottom)


@unittest.skipUnless(os.environ.get("UAV_TEST_ALLOW_ARM") == "1", "arms the simulated vehicle: set UAV_TEST_ALLOW_ARM=1")
class ArmCycle(common.LiveTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        state = common.vehicle("state")
        if state != "disarmed":
            raise unittest.SkipTest(f"the vehicle is {state!r}, not disarmed")
        cls.original_disarm = common.vehicle("param", "COM_DISARM_PRFLT")
        common.vehicle("param", "COM_DISARM_PRFLT", "300")

    @classmethod
    def tearDownClass(cls):
        common.vehicle("disarm")
        common.vehicle("param", "COM_DISARM_PRFLT", str(float(cls.original_disarm)) if cls.original_disarm.replace(".", "").isdigit() else "10")
        super().tearDownClass()

    def test_arm_disarm_and_edits(self):
        manager = common.Manager(cameras(), start_now=False).start()
        try:
            self.assertTrue(manager.wait_for("Monitoring ARMED/DISARMED", timeout=90), manager.log())
            self.assertEqual(common.running_pipelines(), [], "nothing may run while disarmed")

            manager.write(cameras(bottom="gpu"))                                    # valid edit while disarmed
            self.assertTrue(manager.wait_for("uav_object_detection_gpu ", timeout=30), manager.log())
            manager.write("version: 1\ncameras:\n  - id: front_camera\n    detection: {device: tpu}\n")   # broken edit
            self.assertTrue(manager.wait_for("keeping the previous configuration", timeout=30), manager.log())
            manager.write(cameras(bottom="gpu"))

            self.assertEqual(common.vehicle("arm"), "ACCEPTED")
            self.assertTrue(manager.wait_for("is RUNNING", count=2, timeout=300), manager.log())
            for path in ("uav-front-camera", "uav-bottom-camera"):
                self.assertGreaterEqual(common.frames(path), 90, path)
            self.assertTrue(all(line.rstrip().endswith("yes") for line in manager.status().splitlines()[1:]), manager.status())

            manager.write(cameras(overlay="false", prompt="Count the cars.", bottom="gpu"))   # edit while armed
            self.assertTrue(manager.wait_for("applies the next time the vehicle is armed", timeout=30), manager.log())
            self.assertIn("NO", manager.status().splitlines()[1])

            self.assertEqual(common.vehicle("disarm"), "ACCEPTED")
            self.assertTrue(manager.wait_for("Stop camera", count=2, timeout=120), manager.log())
            self.assertEqual(common.vehicle("arm"), "ACCEPTED")                                # re-arm straight away
            self.assertTrue(manager.wait_for("is RUNNING", count=4, timeout=300), manager.log())
            self.assertNotIn("Giving up", manager.log())
            self.assertTrue(all(line.rstrip().endswith("yes") for line in manager.status().splitlines()[1:]), manager.status())
            front = [p for p in common.running_pipelines() if common.pipeline_request(p["id"])["destination"]["frame"]["path"] == "uav-front-camera"][0]
            request = common.pipeline_request(front["id"])
            self.assertIs(request["destination"]["frame"].get("overlay"), False)
            self.assertEqual(request["parameters"]["captioner_prompt"], "Count the cars.")
            self.assertEqual(common.vehicle("disarm"), "ACCEPTED")
            time.sleep(5)
        finally:
            manager.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
