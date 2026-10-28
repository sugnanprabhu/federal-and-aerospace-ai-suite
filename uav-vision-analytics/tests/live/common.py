# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Helpers for the live tests: they drive the running pymavlink stack (make pymav-up) from the host.

Standard library only. Everything that needs a tool the host may not have (ffmpeg, mosquitto_sub, pymavlink)
runs inside the stack's containers with `docker exec`. /tmp is shared between the host and the pipeline-server
container, which is how test files and logs are exchanged.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = "dlstreamer-pipeline-server"
MANAGER = "/home/pipeline-server/scripts/pipeline_manager.py"
MANAGER_PROCESS = "^python3 " + MANAGER


def _env_file_value(key: str, default: str = "") -> str:
    try:
        with open(os.path.join(REPO, ".env"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


HOST_IP = os.environ.get("HOST_IP") or _env_file_value("HOST_IP", "127.0.0.1")
BASE = f"https://{HOST_IP}"


def docker_exec(*args, stdin: str = None, container: str = SERVER, detach: bool = False, timeout: int = 120, check: bool = False):
    command = ["docker", "exec"] + (["-d"] if detach else []) + (["-i"] if stdin is not None else []) + [container] + list(args)
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout, check=check)
    return result


def rest(method: str, path: str, body=None, timeout: int = 30):
    """REST call to the pipeline server through nginx (self-signed certificate, no proxy)."""
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        text = response.read().decode()
    try:
        return json.loads(text)
    except ValueError:
        return text


def running_pipelines() -> list:
    return [p for p in rest("GET", "/pipelines/status") if p.get("state") in ("RUNNING", "QUEUED")]


def stop_all_pipelines(wait: int = 60) -> None:
    for pipeline in running_pipelines():
        try:
            rest("DELETE", "/pipelines/" + pipeline["id"])
        except OSError:
            pass
    deadline = time.time() + wait
    while time.time() < deadline and running_pipelines():
        time.sleep(2)
    time.sleep(10)       # the RTSP paths are released some seconds after the instances report stopped


def frames(path: str, seconds: int = 4) -> int:
    """Frames received from rtsp://localhost:8555/<path> in `seconds` (30 fps stream -> about 30 * seconds)."""
    # The pipeline reports RUNNING a moment before its RTSP stream is served ("no factory for path"), and ffmpeg
    # connects only once, so retry while nothing arrives.
    for _ in range(6):
        result = docker_exec("timeout", "40", "ffmpeg", "-v", "error", "-nostats", "-rtsp_transport", "tcp", "-i",
                             f"rtsp://127.0.0.1:8555/{path}", "-t", str(seconds), "-f", "null", "-", "-progress", "pipe:1")
        counts = re.findall(r"^frame=(\d+)", result.stdout, re.M)
        if counts and int(counts[-1]) > 0:
            return int(counts[-1])
        time.sleep(3)
    return 0


def frames_at_least(path: str, minimum: int, seconds: int = 4) -> int:
    """Frames in `seconds`; a reading below `minimum` is taken once more (a model still loading or a stream that
    just stopped can dip one reading) and the better one counts."""
    got = frames(path, seconds)
    return got if got >= minimum else max(got, frames(path, seconds))


def caption(topic: str, wait: int = 90):
    """First caption text published on the MQTT topic within `wait` seconds, or None."""
    result = docker_exec("timeout", str(wait + 5), "mosquitto_sub", "-t", topic, "-C", "1", "-W", str(wait), container="broker", timeout=wait + 20)
    try:
        return json.loads(result.stdout.strip())["metadata"]["result"]
    except (ValueError, KeyError):
        return None


def pipeline_request(instance_id: str) -> dict:
    return rest("GET", "/pipelines/" + instance_id)["request"]


class Manager:
    """Runs scripts/mavlink_pipeline_manager.py in the pipeline-server container and reads its log."""

    def __init__(self, yaml_text: str, start_now: bool = True):
        self.dir = tempfile.mkdtemp(prefix="uavtest_", dir="/tmp")
        os.chmod(self.dir, 0o777)
        self.config = os.path.join(self.dir, "cameras-pymavlink.yaml")
        self.log_path = os.path.join(self.dir, "manager.log")
        self.start_now = start_now
        self.write(yaml_text)

    def write(self, yaml_text: str) -> None:
        with open(self.config, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(yaml_text))
        os.chmod(self.config, 0o666)

    def log(self) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def start(self) -> "Manager":
        flag = "--start-now" if self.start_now else ""
        docker_exec("bash", "-c", f"HOST_IP={HOST_IP} python3 {MANAGER} {flag} --config {self.config} > {self.log_path} 2>&1", detach=True)
        return self

    def wait_for(self, text: str, count: int = 1, timeout: int = 240) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.log().count(text) >= count or "Giving up" in self.log():
                break
            time.sleep(3)
        return self.log().count(text) >= count

    def status(self) -> str:
        return docker_exec("python3", MANAGER, "--status", "--config", self.config).stdout

    def stop(self, wait: int = 120) -> None:
        docker_exec("pkill", "-TERM", "-f", MANAGER_PROCESS)
        deadline = time.time() + wait
        while time.time() < deadline and docker_exec("pgrep", "-f", MANAGER_PROCESS).stdout.strip():
            time.sleep(2)

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


class LiveTestCase(unittest.TestCase):
    """Base class: needs the stack to be up and idle (no pipelines running) at the start."""

    @classmethod
    def setUpClass(cls):
        if subprocess.run(["docker", "inspect", SERVER], capture_output=True).returncode != 0:
            raise unittest.SkipTest(f"container {SERVER} is not running (make pymav-up)")
        try:
            busy = running_pipelines()
        except OSError as exc:
            raise unittest.SkipTest(f"pipeline server not reachable at {BASE}: {exc}")
        if busy and os.environ.get("UAV_TEST_FORCE") != "1":
            raise unittest.SkipTest(f"{len(busy)} pipeline(s) are running; stop them or set UAV_TEST_FORCE=1 to let the tests stop them")
        stop_all_pipelines()
        docker_exec("pkill", "-TERM", "-f", MANAGER_PROCESS)

    @classmethod
    def tearDownClass(cls):
        stop_all_pipelines()


# --- the simulated vehicle ------------------------------------------------------------------------------------

_MAVLINK_SCRIPT = r'''
import os, sys, time
os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil
act = sys.argv[1]
m = mavutil.mavlink_connection("udpout:mavlink-router:14551", source_system=254, source_component=190)
hb, t0 = None, time.time()
while time.time() - t0 < 15 and not hb:
    m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
    h = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
    if h and h.autopilot != 8 and h.type != 6: hb = h
if not hb: print("NO VEHICLE"); sys.exit(1)
s, c = hb.get_srcSystem(), hb.get_srcComponent()
if act == "state":
    print("armed" if hb.base_mode & 128 else "disarmed"); sys.exit(0)
if act in ("arm", "disarm"):
    m.mav.command_long_send(s, c, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1 if act == "arm" else 0, 0, 0, 0, 0, 0, 0)
    ack = None; end = time.time() + 6
    while time.time() < end and not ack:
        x = m.recv_match(type="COMMAND_ACK", blocking=True, timeout=1)
        if x and x.command == 400: ack = x
    print("ACCEPTED" if ack and ack.result == 0 else "REFUSED"); sys.exit(0)
name = sys.argv[2]
if len(sys.argv) > 3: m.mav.param_set_send(s, c, name.encode(), float(sys.argv[3]), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
else: m.mav.param_request_read_send(s, c, name.encode(), -1)
r, end = None, time.time() + 6
while time.time() < end and not r:
    p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
    if p and p.param_id.rstrip("\x00") == name: r = p
print(r.param_value if r else "NO REPLY")
'''


def vehicle(*args) -> str:
    """Talk to the simulated PX4 through the router's ground-station endpoint (pymavlink inside the pipeline-server container)."""
    result = docker_exec("python3", "-", *args, stdin=_MAVLINK_SCRIPT, timeout=60)
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()[-200:]
