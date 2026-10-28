# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Every one of the nine pipelines, run as a single camera through the manager (--start-now).

For each detection device (cpu, gpu, npu) the detection-only pipeline, and with each VLM device (cpu, gpu) the
detection + captioning pipeline: it must start without retries, publish real video frames (not just report
RUNNING) and, when captioning, deliver a caption on the camera's own MQTT topic. Takes about 15 minutes.
"""
import unittest

import common

# frames received in 4 s at 30 fps is about 120; the CPU pipelines cannot keep up, so the bar is lower
MIN_FRAMES = {"cpu": 50, "gpu": 90, "npu": 90}

CASES = [(det, None) for det in ("cpu", "gpu", "npu")] + [(det, vlm) for det in ("cpu", "gpu", "npu") for vlm in ("cpu", "gpu")]


class NinePipelines(common.LiveTestCase):
    def run_camera(self, det, vlm):
        yaml_text = f"""
            version: 1
            cameras:
              - id: test_camera
                video: uav_sample_front.avi
                detection: {{device: {det}}}
                captioning: {{enabled: {str(vlm is not None).lower()}, device: {vlm or 'gpu'}, prompt: "Describe the road and the vehicles."}}
            """
        manager = common.Manager(yaml_text).start()
        try:
            self.assertTrue(manager.wait_for("is RUNNING"), "the camera did not reach RUNNING:\n" + manager.log())
            self.assertNotIn("Retrying", manager.log(), "the start needed a retry:\n" + manager.log())
            expected = f"uav_object_detection_{det}" + (f"_{vlm}" if vlm else "")
            self.assertIn(f"with '{expected}'", manager.log())
            got = common.frames_at_least("uav-test-camera", MIN_FRAMES[det])
            self.assertGreaterEqual(got, MIN_FRAMES[det], f"only {got} frames in 4 s")
            text = None
            if vlm:
                text = common.caption("uav/caption/test_camera")
                self.assertTrue(text, "no caption arrived on uav/caption/test_camera")
            print(f"  {expected}: {got} frames in 4 s" + (f", caption: {text[:60]!r}" if text else ""), flush=True)
            self.assertIn("yes", manager.status().splitlines()[1].split("  ")[-1], manager.status())
        finally:
            manager.cleanup()
            common.stop_all_pipelines()

    def test_all_combinations(self):
        for det, vlm in CASES:
            with self.subTest(pipeline=f"uav_object_detection_{det}" + (f"_{vlm}" if vlm else "")):
                self.run_camera(det, vlm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
