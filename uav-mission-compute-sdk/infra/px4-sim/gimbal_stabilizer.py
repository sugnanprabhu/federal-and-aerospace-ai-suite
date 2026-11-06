# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Ideal 3-axis gimbal for the simulated cameras.

Spawns the static `stab_cams_0` model (the three 1080p cameras) and, as the simulation runs, moves
it to the drone position with only the drone heading, dropping roll and pitch. The cameras then stay
level however the airframe banks. Meant to run inside the camera-bridge container (gz-transport
bindings), e.g. `docker exec -d camera-bridge python3 - < gimbal_stabilizer.py`.

Environment: GZ_WORLD, GZ_DRONE_MODEL (x500_mono_cam_down_0), STAB_YAW_TAU and STAB_POS_TAU
(heading / position low-pass time constants in simulation seconds, 0 = follow the drone exactly).
"""
import math
import os
import sys
import threading
import time

from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.entity_factory_pb2 import EntityFactory
from gz.msgs10.pose_pb2 import Pose
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node

WORLD = os.getenv("GZ_WORLD", "citysim_multicam")
DRONE = os.getenv("GZ_DRONE_MODEL", "x500_mono_cam_down_0")
STAB = "stab_cams_0"
STAB_SDF = os.getenv("STAB_SDF", "/px4/Tools/simulation/gz/models/stab_cams/model.sdf")
YAW_TAU = float(os.getenv("STAB_YAW_TAU", "0"))
POS_TAU = float(os.getenv("STAB_POS_TAU", "0"))
UPDATE_DT = 1.0 / 120.0  # simulation seconds between pose updates


def yaw_of(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def spawn(node: Node) -> bool:
    req = EntityFactory()
    req.sdf_filename = STAB_SDF
    req.name = STAB
    ok, rep = node.request(f"/world/{WORLD}/create", req, EntityFactory, Boolean, 5000)
    return bool(ok and rep.data)


def main() -> int:
    node = Node()
    for _ in range(60):
        if spawn(node):
            break
        time.sleep(2)
    else:
        print("could not spawn", STAB, file=sys.stderr)
        return 1
    print("spawned", STAB, flush=True)

    req_node = {"n": node}
    latest = {"msg": None}
    cond = threading.Condition()

    def on_poses(msg: Pose_V) -> None:
        # Only store the sample: gz-transport requests cannot be made from inside a subscription callback.
        with cond:
            latest["msg"] = msg
            cond.notify()

    def worker() -> None:
        last, yaw_f, pos_f = -1.0, None, None
        ok = fail = consecutive = 0
        t_log = time.time()
        while True:
            with cond:
                while latest["msg"] is None:
                    cond.wait()
                msg, latest["msg"] = latest["msg"], None
            drone = next((p for p in msg.pose if p.name == DRONE), None)
            if drone is None:
                continue
            stamp = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
            if stamp - last < UPDATE_DT:
                continue
            dt = stamp - last if last >= 0 else 0.0
            last = stamp
            yaw = yaw_of(drone.orientation)
            if YAW_TAU > 0 and yaw_f is not None:
                err = math.atan2(math.sin(yaw - yaw_f), math.cos(yaw - yaw_f))
                yaw = yaw_f + (1.0 - math.exp(-dt / YAW_TAU)) * err
            yaw_f = yaw
            pos = (drone.position.x, drone.position.y, drone.position.z)
            if POS_TAU > 0 and pos_f is not None:
                alpha = 1.0 - math.exp(-dt / POS_TAU)
                pos = tuple(o + alpha * (n - o) for o, n in zip(pos_f, pos))
            pos_f = pos
            req = Pose()
            req.name = STAB
            req.position.x, req.position.y, req.position.z = pos
            req.orientation.z, req.orientation.w = math.sin(yaw / 2.0), math.cos(yaw / 2.0)
            t0 = time.time()
            good, rep = req_node["n"].request(f"/world/{WORLD}/set_pose", req, Pose, Boolean, 1000)
            lat = time.time() - t0
            if good and rep.data:
                ok += 1
                consecutive = 0
            else:
                fail += 1
                consecutive += 1
                if consecutive == 3:
                    print(f"requests failing (rep={good}, {lat:.2f}s); reconnecting", flush=True)
                    req_node["n"] = Node()
                    consecutive = 0
            if time.time() - t_log > 10:
                print(f"sim={stamp:.1f}s ok={ok} fail={fail} pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                      f"yaw={math.degrees(yaw):.0f}", flush=True)
                t_log = time.time()

    threading.Thread(target=worker, daemon=True).start()
    if not node.subscribe(Pose_V, f"/world/{WORLD}/dynamic_pose/info", on_poses):
        print("subscribe failed", file=sys.stderr)
        return 1
    print("stabilising", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
