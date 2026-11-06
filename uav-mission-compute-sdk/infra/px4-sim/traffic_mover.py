# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Drives the `traffic_*` cars of the city world like real traffic.

The cars are static models, so they are moved with the world `set_pose_vector` service. Each car
stays in its lane and only ever drives forward. Cars follow the car ahead with the Intelligent
Driver Model (smooth acceleration and braking, a safe gap, no overtaking) and obey simple traffic
lights at every junction: east-west and north-south get green in turn with an all-red clearance, so
cars on crossing roads never meet. Cars that drive off the far end of a lane (the edge of the
simulated area or of the road) re-enter at its start, behind the car there if any. A lane that a
parked vehicle sticks into is not driven; its cars stay parked.

Meant to run inside the camera-bridge container (gz-transport bindings), e.g.
`docker exec -d camera-bridge python3 /tmp/traffic_mover.py /tmp/traffic.json`.
The JSON comes from `make_citysim_world.py <world> <json>`: cars (initial poses), parked vehicles
and roads.

Environment: GZ_WORLD, TRAFFIC_SPEED (m/s, free-flow speed, default 9), TRAFFIC_WRAP_R (m, simulated
area radius around the spawn, default 80), TRAFFIC_RATE (pose updates per simulation second),
TRAFFIC_GREEN / TRAFFIC_CLEAR (green and all-red seconds).
"""
import json
import math
import multiprocessing
import os
import sys
import time

from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.clock_pb2 import Clock
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node

WORLD = os.getenv("GZ_WORLD", "citysim_multicam")
SPEED = float(os.getenv("TRAFFIC_SPEED", "9"))
WRAP_R = float(os.getenv("TRAFFIC_WRAP_R", "80"))
RATE = float(os.getenv("TRAFFIC_RATE", "60"))
GREEN = float(os.getenv("TRAFFIC_GREEN", "30"))
CLEAR = float(os.getenv("TRAFFIC_CLEAR", "3"))
WARMUP = 90.0  # seconds simulated before the first pose is published, so traffic is already flowing

CAR_LEN = 5.4  # bumper to bumper, used for gaps
FRONT = CAR_LEN / 2.0
ACCEL, DECEL, MIN_GAP, HEADWAY, EXPONENT = 1.6, 2.2, 2.5, 1.4, 4.0
LANE_CLEARANCE = 1.7  # a parked vehicle closer than this to a lane centre line blocks the lane
STOP_MARGIN = 1.5  # stop line distance from the near edge of the crossing road


class Lane:
    """Cars on one lane, driving along `d` over the ring [0, span) (u = distance from the lane start)."""

    def __init__(self, d, lat, q_lo, q_hi, horizontal, sign):
        self.d, self.lat, self.q_lo, self.span = d, lat, q_lo, q_hi - q_lo
        self.horizontal, self.sign = horizontal, sign
        self.blocked = False
        self.cars = []
        self.stops = []  # u of the stop line (for the car centre) in front of each crossing road

    def axis(self) -> int:
        return 0 if self.horizontal else 1  # 0 = east-west, 1 = north-south


def build(data: dict) -> tuple[list[Lane], list[dict]]:
    roads = []
    for r in data["roads"]:
        (x0, y0), (x1, y1) = r["p0"], r["p1"]
        vertical = abs(x0 - x1) < 1e-6
        roads.append({"vertical": vertical, "c": x0 if vertical else y0,
                      "lo": min(y0, y1) if vertical else min(x0, x1),
                      "hi": max(y0, y1) if vertical else max(x0, x1), "w": r["width"]})
    lanes: dict[tuple, Lane] = {}
    frozen = []
    for car in data["cars"]:
        d = (round(math.cos(car["yaw"])), round(math.sin(car["yaw"])))
        horizontal = d[0] != 0
        sign = d[0] if horizontal else d[1]
        lat = round(car["y"] if horizontal else car["x"], 2)  # coordinate across the lane
        along = sign * (car["x"] if horizontal else car["y"])
        road = next((r for r in roads if r["vertical"] != horizontal and abs(r["c"] - lat) < r["w"] / 2), None)
        if road is None:
            frozen.append(car)
            continue
        key = (d, lat)
        if key not in lanes:
            half = math.sqrt(max(WRAP_R**2 - lat**2, 0.0))
            ends = sorted([sign * road["lo"], sign * road["hi"]])
            q_lo, q_hi = max(-half, ends[0]), min(half, ends[1])
            for r in roads:  # keep the re-entry point of the ring out of junctions
                if r["vertical"] == horizontal:
                    qc, keep = sign * r["c"], r["w"] / 2.0 + STOP_MARGIN + CAR_LEN
                    if abs(q_lo - qc) < keep:
                        q_lo = qc + keep
                    if abs(q_hi - qc) < keep:
                        q_hi = qc - keep
            lane = Lane(d, lat, q_lo, q_hi, horizontal, sign)
            for p in data["parked"]:
                across = p["y"] if horizontal else p["x"]
                pos = sign * (p["x"] if horizontal else p["y"])
                if abs(across - lat) < LANE_CLEARANCE and lane.q_lo - 3 < pos < lane.q_lo + lane.span + 3:
                    lane.blocked = True
            lanes[key] = lane
        lane = lanes[key]
        car["u"] = along - lane.q_lo
        if 0.0 <= car["u"] < lane.span:
            lane.cars.append(car)
        else:
            frozen.append(car)
    usable = []
    for lane in lanes.values():
        if lane.blocked:
            frozen += lane.cars
            continue
        for r in roads:
            if r["vertical"] == lane.horizontal:  # roads crossing this lane
                qc = lane.sign * r["c"]
                if lane.q_lo < qc < lane.q_lo + lane.span:
                    lane.stops.append(qc - lane.q_lo - r["w"] / 2.0 - STOP_MARGIN - FRONT)
        lane.stops.sort()
        kept = []
        for car in sorted(lane.cars, key=lambda c: c["u"]):
            if not kept or car["u"] - kept[-1]["u"] >= CAR_LEN + MIN_GAP:
                kept.append(car)
            else:
                frozen.append(car)
        lane.cars = kept
        for i, car in enumerate(kept):
            car["v"] = 0.0
            car["v0"] = SPEED * (0.9 + 0.2 * ((i * 5 + int(abs(lane.lat))) % 7) / 6.0)
        usable.append(lane)
    return usable, frozen


def light_green(axis: int, t: float) -> bool:
    ph = t % (2 * (GREEN + CLEAR))
    return ph < GREEN if axis == 0 else GREEN + CLEAR <= ph < 2 * GREEN + CLEAR


def step(lanes: list[Lane], t: float, dt: float) -> None:
    for lane in lanes:
        n = len(lane.cars)
        green = light_green(lane.axis(), t)
        acc = []
        for i, car in enumerate(lane.cars):
            v = car["v"]
            a = ACCEL * (1.0 - (v / car["v0"]) ** EXPONENT)
            gap = dv = None
            if n > 1:
                lead = lane.cars[(i + 1) % n]
                gap, dv = (lead["u"] - car["u"]) % lane.span - CAR_LEN, v - lead["v"]
            if not green:
                stop_u = next((s for s in lane.stops if s >= car["u"]), None)
                if stop_u is not None:
                    sg = stop_u - car["u"]
                    # Brake for the red light unless it is too late to stop comfortably
                    if sg > 0.5 and v * v / (2.0 * sg) < 4.5 and (gap is None or sg < gap):
                        gap, dv = sg, v
            if gap is not None:
                s_star = MIN_GAP + max(0.0, v * HEADWAY + v * dv / (2.0 * math.sqrt(ACCEL * DECEL)))
                a -= ACCEL * (s_star / max(gap, 0.1)) ** 2
            acc.append(max(a, -9.0))
        for car, a in zip(lane.cars, acc):
            car["v"] = max(0.0, car["v"] + a * dt)  # never reverses
            car["u"] += car["v"] * dt
        if n and lane.cars[-1]["u"] >= lane.span:  # leaves the far end, re-enters at the start
            last = lane.cars.pop()
            last["u"] -= lane.span
            lane.cars.insert(0, last)


def write_pose(car: dict, lane: Lane, pose) -> None:
    along = (lane.q_lo + car["u"]) * lane.sign
    pose.name = car["name"]
    pose.position.x = along if lane.horizontal else lane.lat
    pose.position.y = lane.lat if lane.horizontal else along
    pose.position.z = car["z"]
    pose.orientation.z, pose.orientation.w = math.sin(car["yaw"] / 2.0), math.cos(car["yaw"] / 2.0)


def main() -> int:
    with open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/traffic.json", encoding="utf-8") as fh:
        lanes, frozen = build(json.load(fh))
    print(f"{sum(len(ln.cars) for ln in lanes)} cars driving on {len(lanes)} lanes, "
          f"{len(frozen)} stay parked", flush=True)
    dt = 1.0 / RATE
    t_model = 0.0
    while t_model < WARMUP:
        step(lanes, t_model, dt)
        t_model += dt

    # The simulation clock is read in a child process: a gz-transport node that also subscribes
    # cannot make service requests reliably.
    sim_time = multiprocessing.Value("d", -1.0)
    multiprocessing.Process(target=clock_reader, args=(sim_time,), daemon=True).start()
    node = Node()
    print("moving traffic", flush=True)
    last_sim, ok, fail, t_log = None, 0, 0, time.time()
    while True:
        time.sleep(0.5 / RATE)
        t = sim_time.value
        if t < 0:
            continue
        if last_sim is None or t < last_sim:  # first sample or simulation restarted
            last_sim = t
        n_steps = int((t - last_sim) * RATE)
        if n_steps < 1:
            continue
        for _ in range(min(n_steps, 600)):
            step(lanes, t_model, dt)
            t_model += dt
        last_sim += n_steps * dt
        req = Pose_V()
        for ln in lanes:
            for car in ln.cars:
                write_pose(car, ln, req.pose.add())
        good, rep = node.request(f"/world/{WORLD}/set_pose_vector", req, Pose_V, Boolean, 1000)
        ok, fail = (ok + 1, fail) if good and rep.data else (ok, fail + 1)
        if time.time() - t_log > 10:
            speeds = [c["v"] for ln in lanes for c in ln.cars]
            print(f"sim={t:.1f}s ok={ok} fail={fail} mean_v={sum(speeds) / len(speeds):.1f} "
                  f"stopped={sum(1 for s in speeds if s < 0.1)}", flush=True)
            t_log = time.time()


def clock_reader(sim_time) -> None:
    def on_clock(msg: Clock) -> None:
        sim_time.value = msg.sim.sec + msg.sim.nsec * 1e-9

    node = Node()
    if not node.subscribe(Clock, "/clock", on_clock):
        print("subscribe failed", file=sys.stderr, flush=True)
        return
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
