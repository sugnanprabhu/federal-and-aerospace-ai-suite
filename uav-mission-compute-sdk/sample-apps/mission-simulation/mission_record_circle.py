# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Fly a circular mission and record the front, bottom (nadir) and rear cameras to three
frame-synchronised 1920x1080 @ 30 fps MP4 files.

Uses the companion-bridge REST API (arm, takeoff, circle_start/stop, land) and MQTT position
telemetry (the circle follows simulation time, so it works at any simulation speed). Recording is
done by sync_recorder.py inside the camera-bridge container straight from the Gazebo
camera topics, matching frames on simulation time. Requires a running
`make up-sim-camera` stack and the docker CLI.

Environment: REST_API_HOST/PORT (localhost:8080), MQTT_BROKER_HOST/PORT (localhost:1884),
UAV_ID (uav-1).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

REST = f"http://{os.getenv('REST_API_HOST', 'localhost')}:{os.getenv('REST_API_PORT', '8080')}"
CONTAINER = os.getenv("CAMERA_BRIDGE_CONTAINER", "camera-bridge")
CONTAINER_COMPANION = os.getenv("COMPANION_BRIDGE_CONTAINER", "companion-bridge")
RECORDER = Path(__file__).with_name("sync_recorder.py")
UAV_ID = os.getenv("UAV_ID", "uav-1")
CAMERAS = ("front", "bottom", "rear")


def post(action: str, **body):
    resp = requests.post(f"{REST}/action/{action}", json=body, timeout=30)
    resp.raise_for_status()


def arm_with_retry(timeout: float = 180.0) -> None:
    """Arm, retrying while PX4 still reports failed preflight checks (it is slow right after a restart)."""
    deadline = time.time() + timeout
    while True:
        try:
            post("arm")
            return
        except requests.HTTPError:
            if time.time() > deadline:
                raise
            time.sleep(5)


class Position:
    """Latest GPS position from MQTT telemetry; lets waits follow the vehicle at any sim speed."""

    def __init__(self):
        self.lat = self.lon = self.rel_alt = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self._on_message
        self.client.connect(os.getenv("MQTT_BROKER_HOST", "localhost"), int(os.getenv("MQTT_BROKER_PORT", "1884")))
        self.client.subscribe(f"uav/{UAV_ID}/telemetry/position")
        self.client.loop_start()

    def _on_message(self, _client, _userdata, msg):
        data = json.loads(msg.payload)
        self.lat, self.lon = data["latitude_deg"], data["longitude_deg"]
        self.rel_alt = data["relative_altitude_m"]

    def wait(self, predicate, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.rel_alt is not None and predicate(self):
                return True
            time.sleep(0.2)
        return False

    def offset_from(self, lat0: float, lon0: float):
        return ((self.lat - lat0) * 111_320.0, (self.lon - lon0) * 111_320.0 * math.cos(math.radians(lat0)))


STABILIZER = Path(__file__).resolve().parents[2] / "infra" / "px4-sim" / "gimbal_stabilizer.py"
TRAFFIC_MOVER = Path(__file__).resolve().parents[2] / "infra" / "px4-sim" / "traffic_mover.py"
TRAFFIC_DATA = Path(__file__).resolve().parents[2] / "infra" / "px4-sim" / "worlds" / "citysim_traffic.json"
STAB_MODEL = "stab_cams_0"
GO_FILE = "/tmp/record_go"
STOP_FILE = "/tmp/record_stop"


def start_stabilizer(yaw_tau: float, pos_tau: float) -> None:
    """Run the level-camera gimbal in the camera-bridge container (stays up until the container restarts)."""
    subprocess.run(["docker", "exec", CONTAINER, "pkill", "-f", "gimbal_stabilizer"], check=False)
    code = STABILIZER.read_text(encoding="utf-8")
    subprocess.run(["docker", "exec", "-i", CONTAINER, "sh", "-c", "cat > /tmp/gimbal_stabilizer.py"],
                   input=code, text=True, check=True)
    env = ["-e", f"GZ_WORLD={os.getenv('GZ_WORLD', 'citysim_multicam')}",
           "-e", f"STAB_YAW_TAU={yaw_tau}", "-e", f"STAB_POS_TAU={pos_tau}"]
    subprocess.run(["docker", "exec", "-d", *env, CONTAINER, "sh", "-c",
                    "python3 /tmp/gimbal_stabilizer.py > /tmp/gimbal_stabilizer.log 2>&1"], check=True)
    for _ in range(60):
        done = subprocess.run(["docker", "exec", CONTAINER, "grep", "-q", "stabilising", "/tmp/gimbal_stabilizer.log"])
        if done.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("gimbal stabilizer did not start")


def start_traffic() -> None:
    """Run traffic_mover.py in the camera-bridge container so the traffic_* cars drive along the roads."""
    subprocess.run(["docker", "exec", CONTAINER, "pkill", "-f", "traffic_mover"], check=False)
    for src, dst in ((TRAFFIC_MOVER, "/tmp/traffic_mover.py"), (TRAFFIC_DATA, "/tmp/traffic.json")):
        subprocess.run(["docker", "exec", "-i", CONTAINER, "sh", "-c", f"cat > {dst}"],
                       input=src.read_text(encoding="utf-8"), text=True, check=True)
    env = ["-e", f"GZ_WORLD={os.getenv('GZ_WORLD', 'citysim_multicam')}"]
    subprocess.run(["docker", "exec", "-d", *env, CONTAINER, "sh", "-c",
                    "python3 /tmp/traffic_mover.py /tmp/traffic.json > /tmp/traffic_mover.log 2>&1"], check=True)
    for _ in range(60):
        done = subprocess.run(["docker", "exec", CONTAINER, "grep", "-q", "moving traffic", "/tmp/traffic_mover.log"])
        if done.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("traffic mover did not start")


def start_recorder(duration: float, fps: int, model: str | None = None) -> subprocess.Popen:
    """Run sync_recorder.py in the camera-bridge container and wait until frames flow."""
    env = ["-e", f"GZ_MODEL={model}"] if model else []
    cmd = ["docker", "exec", "-i", *env, CONTAINER, "python3", "-", "--out", "/tmp/recordings",
           "--duration", str(duration), "--fps", str(fps), "--go-file", GO_FILE,
           "--stop-file", STOP_FILE]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(RECORDER.read_text(encoding="utf-8"))
    proc.stdin.close()
    return proc


SMOOTH_PARAMS = {"MPC_ACC_HOR": 0.6, "MPC_ACC_HOR_MAX": 1.0, "MPC_JERK_AUTO": 0.8, "MPC_TILTMAX_AIR": 12.0, "MPC_YAWRAUTO_MAX": 12.0, "MPC_YAWRAUTO_ACC": 3.0}


def set_smooth_flight_params(cruise: float) -> None:
    """Gentle speed/acceleration limits so the body-fixed cameras do not pitch/roll abruptly."""
    code = (
        "import asyncio,warnings;warnings.simplefilter('ignore')\n"
        "from mavsdk import System\n"
        "async def m():\n"
        " s=System();await s.connect(system_address='udpin://0.0.0.0:14540')\n"
        f" for n,v in {({**SMOOTH_PARAMS, 'MPC_XY_CRUISE': cruise})!r}.items():\n"
        "  try: await s.param.set_param_float(n,v);print(n,await s.param.get_param_float(n))\n"
        "  except Exception as e: print(n,'FAILED',e)\n"
        "asyncio.run(m())\n"
    )
    subprocess.run(["docker", "exec", CONTAINER_COMPANION, "python3", "-c", code], check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="recordings", help="output directory")
    ap.add_argument("--altitude", type=float, default=11.5)
    ap.add_argument("--radius", type=float, default=35.0)
    ap.add_argument("--duration", type=float, default=60.0, help="video length in seconds (stream time)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--stabilize", action="store_true",
                    help="record from level (roll/pitch-free) gimbal cameras; needs PX4_MODEL_DIR=multi_cam_stab")
    ap.add_argument("--traffic", action="store_true", help="drive the traffic_* cars along the roads (citysim world)")
    ap.add_argument("--stab-yaw-tau", type=float, default=0.0, help="heading low-pass for the gimbal, sim seconds")
    ap.add_argument("--stab-pos-tau", type=float, default=0.0, help="position low-pass for the gimbal, sim seconds")
    args = ap.parse_args()

    out_dir = Path(args.out)

    if args.traffic:
        start_traffic()
    if args.stabilize:
        start_stabilizer(args.stab_yaw_tau, args.stab_pos_tau)
    pos = Position()
    # One lap per video duration so the clip loops seamlessly.
    set_smooth_flight_params(2 * math.pi * args.radius / args.duration)
    print("Arming and taking off...")
    arm_with_retry()
    time.sleep(3)
    post("takeoff", altitude=5.0)
    if not pos.wait(lambda p: p.rel_alt >= 4.0, timeout=300):
        print("Takeoff timed out", file=sys.stderr)
        post("land")
        return 1
    time.sleep(2)

    # Offboard moving-setpoint circle (circle3.py style); lap time follows simulation time.
    recorder = None
    post("circle_start", radius=args.radius, altitude=args.altitude, lap_s=args.duration)
    try:
        # Arm the recorder, then release it exactly when the drone completes the ease-in lap and
        # crosses its start pose again, so one 60 s lap makes a seamless loop.
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", GO_FILE, STOP_FILE], check=False)
        # Upper bound only: the clip is cut when the drone has flown exactly one more lap.
        recorder = start_recorder(args.duration * 1.5, args.fps, STAB_MODEL if args.stabilize else None)
        deadline = time.time() + 1500
        while time.time() < deadline:
            st = requests.get(f"{REST}/state", timeout=10).json()["state"]
            if st.get("circle_phase") == "failed":
                raise RuntimeError("circle failed")
            if st.get("circle_phase") == "circle" and st.get("circle_theta", 0.0) >= 2 * math.pi:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("circle did not complete its first lap")
        subprocess.run(["docker", "exec", CONTAINER, "touch", GO_FILE], check=True)
        print("Recording one lap", flush=True)
        last_print = 0.0
        while recorder.poll() is None:
            st = requests.get(f"{REST}/state", timeout=10).json()["state"]
            theta = st.get("circle_theta", 0.0)
            if theta >= 4 * math.pi:
                subprocess.run(["docker", "exec", CONTAINER, "touch", STOP_FILE], check=True)
                recorder.wait(timeout=300)
                break
            if time.time() - last_print > 10:
                print(f"theta={math.degrees(theta):.0f}deg alt={pos.rel_alt:.1f}", flush=True)
                last_print = time.time()
            time.sleep(0.1)
    finally:
        if recorder is not None:
            recorder.wait(timeout=300)
        post("circle_stop")
        print("Landing...")
        post("land")

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in CAMERAS:
        subprocess.run(["docker", "cp", f"{CONTAINER}:/tmp/recordings/{name}.mp4", str(out_dir / f"{name}.mp4")], check=True)
    for name in CAMERAS:
        print(f"saved {out_dir / (name + '.mp4')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
