# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Record the Gazebo front/bottom/rear cameras as frame-synchronised 1080p30 MP4 files.

Runs inside the camera-bridge container (gz-transport bindings, ffmpeg, OpenCV):

    docker exec -i camera-bridge python3 - --out /tmp/rec --duration 60 < sync_recorder.py

Frames are matched on the simulation timestamp in the Gazebo image header, so
frame N of every file shows the same simulation instant, and the files play at
true 30 fps of simulation time regardless of the real-time factor. Prints
"READY" once frames are flowing and "DONE" after the files are finalised.
"""
import argparse
import os
import subprocess
import sys
import threading
import time

import cv2
import numpy as np
from gz.msgs10.image_pb2 import Image
from gz.transport13 import Node

WORLD = os.getenv("GZ_WORLD", "baylands_multicam")
MODEL = os.getenv("GZ_MODEL", "x500_mono_cam_down_0")
SENSORS = {"front": "imager_forward", "bottom": "imager_nadir", "rear": "imager_rear"}
MAX_PENDING = 600


def topic(sensor: str) -> str:
    return f"/world/{WORLD}/model/{MODEL}/link/camera_link/sensor/{sensor}/image"


def start_encoder(path: str, width: int, height: int, fps: int) -> subprocess.Popen:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-g", str(fps * 2), "-movflags", "+faststart", path,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=60.0, help="video length in seconds")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--go-file", help="discard frames until this file exists, then start recording")
    ap.add_argument("--stop-file", help="finish early once this file exists (duration is then an upper bound)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    total = int(args.duration * args.fps)

    pending = {name: {} for name in SENSORS}  # sim stamp (ns) -> BGR frame
    lock = threading.Lock()
    cond = threading.Condition(lock)

    def make_cb(name):
        def cb(msg):
            w, h, raw = msg.width, msg.height, msg.data
            channels = len(raw) // (w * h)
            if channels not in (3, 4):
                return
            arr = np.frombuffer(raw, np.uint8).reshape((h, w, channels))
            frame = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if channels == 3 else cv2.COLOR_RGBA2BGR)
            stamp = msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nsec
            with cond:
                pending[name][stamp] = frame
                if len(pending[name]) > MAX_PENDING:
                    del pending[name][min(pending[name])]
                cond.notify_all()
        return cb

    node = Node()
    for name, sensor in SENSORS.items():
        if not node.subscribe(Image, topic(sensor), make_cb(name)):
            print(f"cannot subscribe to {topic(sensor)}", file=sys.stderr)
            return 1

    encoders = {}
    written = 0
    last_stamp = -1
    t0 = None
    prev_frames = {}
    repeated = 0
    print("READY", flush=True)
    while written < total and not (args.stop_file and encoders and os.path.exists(args.stop_file)):
        with cond:
            # Oldest simulation instant for which every camera has a frame.
            common = set.intersection(*(set(p) for p in pending.values()))
            common = {s for s in common if s > last_stamp}
            if not common:
                cond.wait(timeout=1.0)
                continue
            stamp = min(common)
            frames = {name: p.pop(stamp) for name, p in pending.items()}
            for p in pending.values():
                for old in [s for s in p if s < stamp]:
                    del p[old]
        last_stamp = stamp
        if args.go_file and not os.path.exists(args.go_file):
            continue
        if not encoders:
            h, w = frames["front"].shape[:2]
            encoders = {n: start_encoder(os.path.join(args.out, f"{n}.mp4"), w, h, args.fps) for n in SENSORS}
        # Place frames on the true simulation timeline: when the renderer drops a frame, repeat the
        # previous one so the video keeps real time (no speed-ups or lap-time drift).
        if t0 is None:
            t0 = stamp
        slot = min(max(round((stamp - t0) * args.fps / 1e9), written), total - 1)
        for name, frame in frames.items():
            for _ in range(slot - written):
                encoders[name].stdin.write(prev_frames[name])
            data = frame.tobytes()
            encoders[name].stdin.write(data)
            prev_frames[name] = data
        repeated += slot - written
        written = slot + 1

    for proc in encoders.values():
        proc.stdin.close()
    for proc in encoders.values():
        proc.wait(timeout=120)
    print("DONE", written, "repeated", repeated, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
