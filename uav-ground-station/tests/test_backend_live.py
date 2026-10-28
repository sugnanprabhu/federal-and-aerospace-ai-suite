# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The ground-station backend against a UAV host whose cameras are running.

Environment:
  BACKEND_URL   backend under test (default http://127.0.0.1:8080)
  UAV_HOST      address of the UAV host (required); start its cameras first (make start-rtsp, or --start-now)
  WHEP_BASE     mediamtx WHEP base URL, only for the WebRTC test (default http://<hostname of BACKEND_URL>:8889)

Standard library only (the WebRTC test also needs aiortc and skips without it).
"""
import asyncio
import json
import os
import re
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080").rstrip("/")
UAV_HOST = os.environ.get("UAV_HOST", "")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, timeout: int = 30):
    request = urllib.request.Request(BACKEND + path, method=method)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode()
            return response.status, (json.loads(body) if body[:1] in "{[" else body)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def host_query(**extra) -> str:
    return urllib.parse.urlencode({"host": UAV_HOST, **extra})


@unittest.skipUnless(UAV_HOST, "set UAV_HOST to the address of a UAV host with running cameras")
class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status, body = call("GET", "/api/detect-pipelines?" + host_query())
        if status != 200 or not body.get("reachable"):
            raise unittest.SkipTest(f"UAV host {UAV_HOST} not reachable through the backend: {status} {body}")
        cls.cameras = body["cameras"]
        if not cls.cameras:
            raise unittest.SkipTest(f"no cameras are running on {UAV_HOST}")

    def test_cameras_are_discovered_with_details(self):
        for cam in self.cameras:
            self.assertRegex(cam["camera"], r"^[A-Za-z0-9_-]{1,48}$")
            self.assertEqual(cam["state"], "RUNNING")
            self.assertEqual(cam["title"], re.sub(r"[-_]", " ", cam["camera"][4:] if cam["camera"].startswith("uav-") else cam["camera"]))
            self.assertIn(cam["detection_device"], ("cpu", "gpu", "npu"))
            self.assertIsInstance(cam["overlay"], bool)
            if cam["captioning"]:
                self.assertIn(cam["vlm_device"], ("cpu", "gpu"))
                self.assertRegex(cam["mqtt_topic"], r"^uav/caption/[A-Za-z0-9_-]+$")
            else:
                self.assertIsNone(cam["mqtt_topic"])
        self.assertEqual([c["camera"] for c in self.cameras], sorted(c["camera"] for c in self.cameras))

    def test_pipeline_info_agrees_with_discovery(self):
        for cam in self.cameras:
            status, info = call("GET", "/api/pipeline-info?" + host_query(camera=cam["camera"]))
            self.assertEqual(status, 200)
            self.assertTrue(info["rtsp_available"])
            for key in ("pipeline_name", "captioning", "vlm_device", "detection_device", "overlay"):
                self.assertEqual(info[key], cam[key], key)
            self.assertEqual(info["caption_topic"], cam["mqtt_topic"])

    def test_unknown_camera_is_reported_not_failed(self):
        status, info = call("GET", "/api/pipeline-info?" + host_query(camera="uav-does-not-exist"))
        self.assertEqual(status, 200)
        self.assertTrue(info["reachable"])
        self.assertFalse(info["rtsp_available"])
        self.assertFalse(info["captioning"])

    def test_bad_input_is_rejected(self):
        for method, path in (
            ("GET", "/api/pipeline-info?" + host_query(camera="../etc")),
            ("GET", "/api/pipeline-info?" + host_query(camera="a b")),
            ("GET", "/api/captions-stream?" + host_query(topic="other/topic")),
            ("GET", "/api/captions-stream?" + host_query(topic="uav/caption/a%0Ab")),
            ("POST", "/api/relay-path?" + host_query(camera="a/b")),
            ("GET", "/api/pipeline-info?" + host_query(device="cpu")),         # the old device parameter is gone
        ):
            with self.subTest(path=path):
                self.assertEqual(call(method, path)[0], 422)

    def test_captions_stream_uses_the_cameras_topic(self):
        captioning = [c for c in self.cameras if c["captioning"]]
        if not captioning:
            self.skipTest("no captioning camera is running")
        cam = captioning[0]
        request = urllib.request.Request(BACKEND + "/api/captions-stream?" + host_query(topic=cam["mqtt_topic"]))
        got, deadline = b"", time.time() + 100
        with OPENER.open(request, timeout=110) as response:
            while time.time() < deadline and b"\n\ndata:" not in got and not got.count(b"data:") >= 2:
                got += response.read1(4096) if hasattr(response, "read1") else response.read(1)
        text = got.decode()
        self.assertIn("event: connected", text)
        self.assertIn(json.dumps({"topic": cam["mqtt_topic"]}), text)
        payload = re.findall(r"^data: (\{.*\})$", text, re.M)
        self.assertGreaterEqual(len(payload), 2, "no caption arrived:\n" + text[:300])
        self.assertTrue(json.loads(payload[1])["metadata"]["result"])

    def test_relay_path_is_per_camera(self):
        for cam in self.cameras:
            status, body = call("POST", "/api/relay-path?" + host_query(camera=cam["camera"]))
            if status == 502:
                self.skipTest("the mediamtx relay is not reachable from the backend")
            self.assertEqual((status, body), (200, {"path": f"relay-{cam['camera']}"}))

    def test_dashboard_files_are_served(self):
        for path in ("/", "/js/app.js", "/css/styles.css"):
            self.assertEqual(call("GET", path)[0], 200, path)


@unittest.skipUnless(UAV_HOST, "set UAV_HOST")
class Relay(unittest.TestCase):
    def test_every_camera_plays_smoothly_over_webrtc(self):
        try:
            from aiortc import RTCPeerConnection, RTCSessionDescription
        except ImportError:
            self.skipTest("aiortc is not installed (pip install aiortc)")
        status, body = call("GET", "/api/detect-pipelines?" + host_query())
        cameras = body["cameras"] if status == 200 else []
        if not cameras:
            self.skipTest("no cameras are running")
        whep = os.environ.get("WHEP_BASE") or f"http://{urllib.parse.urlparse(BACKEND).hostname}:8889"

        async def play(cam):
            call("POST", "/api/relay-path?" + host_query(camera=cam))
            pc = RTCPeerConnection()
            pc.addTransceiver("video", direction="recvonly")
            stamps, first = [], asyncio.Event()

            @pc.on("track")
            def on_track(track):
                async def read():
                    while True:
                        try:
                            await track.recv()
                        except Exception:
                            return
                        stamps.append(time.monotonic())
                        first.set()
                asyncio.ensure_future(read())

            await pc.setLocalDescription(await pc.createOffer())
            request = urllib.request.Request(f"{whep}/relay-{cam}/whep", data=pc.localDescription.sdp.encode(),
                                             headers={"Content-Type": "application/sdp"}, method="POST")
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: OPENER.open(request, timeout=15))
            await pc.setRemoteDescription(RTCSessionDescription(sdp=response.read().decode(), type="answer"))
            await asyncio.wait_for(first.wait(), timeout=40)
            await asyncio.sleep(15)
            await pc.close()
            return stamps

        async def main():
            return await asyncio.gather(*(play(c["camera"]) for c in cameras))

        for cam, stamps in zip(cameras, asyncio.run(main())):
            gaps = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]
            rate = len(stamps) / (stamps[-1] - stamps[0])
            self.assertGreater(rate, 24, f"{cam['camera']}: {rate:.1f} fps")
            self.assertLess(max(gaps), 400, f"{cam['camera']}: a {max(gaps):.0f} ms gap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
