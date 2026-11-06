#!/usr/bin/env python3
# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Generate worlds/citysim_multicam.sdf from the osrf/citysim city layout.

Usage: make_citysim_world.py /path/to/citysim/worlds/simple_city.world [citysim_traffic.json] > citysim_multicam.sdf

The optional JSON (roads, parked vehicles, traffic cars) is what traffic_mover.py needs to drive the cars.

citysim (https://github.com/osrf/citysim, Apache-2.0) targets Gazebo Classic. This script keeps its
layout (road grid, sidewalks, buildings, cars, street furniture) and converts it for Gazebo
Harmonic: classic `<road>` elements become flat boxes, models become Gazebo Fuel includes, the
heightmap terrain becomes a flat ground plane, and trees, tall structures near the flight path and
the fire truck are dropped. Pedestrians are added along the sidewalks.
"""
import json
import math
import random
import re
import sys
import xml.etree.ElementTree as ET

FUEL = "https://fuel.gazebosim.org/1.0/OpenRobotics/models/"
Z_SHIFT = 5.01  # citysim places the city on terrain at z=5.01; our ground is z=0

# citysim model name -> Fuel model name
FUEL_NAMES = {
    "ambulance": "Ambulance", "apartment": "Apartment", "bus": "Bus", "cafe_table": "Cafe table",
    "cardboard_box": "Cardboard box", "cinder_block": "Cinder Block", "cinder_block_2": "Cinder Block 2",
    "construction_barrel": "Construction Barrel", "construction_cone": "Construction Cone",
    "drc_practice_orange_jersey_barrier": "DRC Practice: Orange Jersey Barrier", "dumpster": "Dumpster",
    "fast_food": "Fast Food", "fire_hydrant": "Fire hydrant", "fire_station": "Fire Station",
    "fountain": "Fountain", "gazebo": "Gazebo", "grocery_store": "Grocery store", "hatchback": "Hatchback",
    "hatchback_blue": "Hatchback blue", "hatchback_red": "Hatchback red", "house_1": "House 1",
    "house_2": "House 2", "house_3": "House 3", "lamp_post": "Lamp Post", "law_office": "Law office",
    "osrf_first_office": "OSRF First Office", "parking_garage": "Parking garage", "pickup": "Pickup",
    "playground": "Playground", "police_station": "Police station", "post_office": "Post office",
    "postbox": "Postbox", "salon": "Salon", "school": "School",
    "stop_sign": "Stop Sign", "suv": "SUV", "telephone_pole": "Telephone pole", "thrift_shop": "Thrift shop",
}
# Dropped: trees, fire truck, stop lights (nested model://stop_light does not resolve from the Fuel cache), tall structures that could meet the 20 m flight altitude, water/pier
# features and models that are not on Fuel.
VEHICLE_RADIUS = {"pickup": 3.2, "hatchback": 2.6, "hatchback_red": 2.6, "hatchback_blue": 2.6,
                  "suv": 2.8, "ambulance": 3.6, "bus": 6.5}
PEOPLE = ["Standing person", "Walking person", "Casual female", "Rescue Randy"]
KEEP_CLEAR = 6.0  # metres around the spawn point (0, 0)
VEHICLE_GAP = 2.5
N_PEOPLE = 70
# Cars driving along the roads near the circular flight path, so every camera has something to see.
TRAFFIC_MIN_R, TRAFFIC_MAX_R = 8.0, 75.0
TRAFFIC_STEP = 9.0
LANE_OFFSET = 2.1
LANE_CLEAR = 1.7  # half-width of a lane kept free of parked vehicles
TRAFFIC_AREA_R = 85.0
TRAFFIC_MODELS = ["hatchback", "hatchback_red", "hatchback_blue", "suv", "pickup", "hatchback"]
HEADER_BOX = """    <model name='{name}'>
      <static>true</static>
      <pose>{x:.3f} {y:.3f} {z:.3f} 0 0 {yaw:.4f}</pose>
      <link name='link'>
        <visual name='visual'>
          <geometry><box><size>{sx:.3f} {sy:.3f} {sz:.3f}</size></box></geometry>
          <material><ambient>{c}</ambient><diffuse>{c}</diffuse><specular>0.02 0.02 0.02 1</specular></material>
        </visual>
      </link>
    </model>
"""

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->
<!--
  citysim_multicam - city layout from https://github.com/osrf/citysim (Apache-2.0) converted for
  Gazebo Harmonic by make_citysim_world.py. Generated file; edit the generator instead.
-->
<sdf version='1.9'>
  <world name='citysim_multicam'>

    <physics type="ode">
      <max_step_size>0.004</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>250</real_time_update_rate>
    </physics>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>6e-06 2.3e-05 -4.2e-05</magnetic_field>
    <atmosphere type='adiabatic'/>

    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>37.412173071650805</latitude_deg>
      <longitude_deg>-121.998878727967</longitude_deg>
      <elevation>38</elevation>
    </spherical_coordinates>

    <scene>
      <ambient>0.7 0.7 0.7 1</ambient>
      <grid>false</grid>
      <sky>
        <clouds>true</clouds>
      </sky>
      <shadows>0</shadows>
    </scene>

    <light name='sunUTC' type='directional'>
      <pose>0 0 500 0 -0 0</pose>
      <cast_shadows>false</cast_shadows>
      <intensity>1</intensity>
      <direction>0 0 -1</direction>
      <diffuse>0.904 0.904 0.904 1</diffuse>
      <specular>0.271 0.271 0.271 1</specular>
      <attenuation>
        <range>2000</range>
        <linear>0</linear>
        <constant>1</constant>
        <quadratic>0</quadratic>
      </attenuation>
    </light>

    <model name='ground'>
      <static>true</static>
      <link name='link'>
        <collision name='collision'>
          <geometry><plane><normal>0 0 1</normal><size>800 800</size></plane></geometry>
        </collision>
        <visual name='visual'>
          <geometry><plane><normal>0 0 1</normal><size>800 800</size></plane></geometry>
          <material><ambient>0.30 0.45 0.22 1</ambient><diffuse>0.30 0.45 0.22 1</diffuse>
          <specular>0.01 0.01 0.01 1</specular></material>
        </visual>
      </link>
    </model>
"""


def nums(text):
    return [float(v) for v in text.split()]


def segment_boxes(name, pts, width, z, height, color):
    out = []
    for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
        length = math.hypot(x2 - x1, y2 - y1)
        out.append(HEADER_BOX.format(
            name=f"{name}_{i}", x=(x1 + x2) / 2, y=(y1 + y2) / 2, z=z, yaw=math.atan2(y2 - y1, x2 - x1),
            sx=length, sy=width, sz=height, c=color))
    return out


def main(path, traffic_json=None):
    world = ET.parse(path).getroot().find("world")
    out = [HEAD]
    n = 0

    # Roads (classic <road> is not supported in gz-sim): grey strips with a yellow centre line.
    for road in world.findall("road"):
        pts = [tuple(nums(p.text)[:2]) for p in road.findall("point")]
        width = float(road.findtext("width"))
        sandy = road.get("name") == "sandy_path"
        out += segment_boxes(road.get("name"), pts, width, 0.02, 0.04, "0.62 0.52 0.36 1" if sandy else "0.16 0.16 0.17 1")
        if not sandy:
            out += segment_boxes(road.get("name") + "_line", pts, 0.18, 0.045, 0.01, "0.9 0.75 0.1 1")

    sidewalk_boxes = []
    for model in world.findall("model"):
        name = model.get("name")
        pose = nums(model.findtext("pose"))
        if name.startswith("sidewalk"):
            poly = model.find(".//visual/geometry/polyline")
            xs = [nums(p.text)[0] + pose[0] for p in poly.findall("point")]
            ys = [nums(p.text)[1] + pose[1] for p in poly.findall("point")]
            box = (min(xs), max(xs), min(ys), max(ys))
            sidewalk_boxes.append(box)
            out.append(HEADER_BOX.format(
                name=name, x=(box[0] + box[1]) / 2, y=(box[2] + box[3]) / 2, z=0.075, yaw=0,
                sx=box[1] - box[0], sy=box[3] - box[2], sz=0.15, c="0.62 0.62 0.60 1"))
        elif name.startswith("asphalt_plane"):
            size = nums(model.find(".//visual//size").text)
            out.append(HEADER_BOX.format(
                name=name, x=pose[0], y=pose[1], z=0.02, yaw=pose[5], sx=size[0], sy=size[1], sz=0.04,
                c="0.16 0.16 0.17 1"))

    roads = [(road.get("name"), [tuple(nums(p.text)[:2]) for p in road.findall("point")], float(road.findtext("width")))
             for road in world.findall("road") if road.get("name") != "sandy_path"]

    def in_traffic_lane(x, y):
        """True for a spot on a lane centre line (road centre +/- LANE_OFFSET) inside the traffic area."""
        if math.hypot(x, y) > TRAFFIC_AREA_R:
            return False
        for _, pts, width in roads:
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            across, along = (x - x0, y) if abs(x0 - x1) < 1e-6 else (y - y0, x)
            lo, hi = (min(y0, y1), max(y0, y1)) if abs(x0 - x1) < 1e-6 else (min(x0, x1), max(x0, x1))
            if lo <= along <= hi and any(abs(across - side * LANE_OFFSET) < LANE_CLEAR for side in (1, -1)):
                return True
        return False

    # Fuel includes, placed where citysim puts them.
    vehicles = []
    placed = []
    for inc in world.findall("include"):
        m = re.match(r"model://(.+)", inc.findtext("uri"))
        key = m.group(1) if m else ""
        name = inc.findtext("name")
        pose = nums(inc.findtext("pose"))
        if key not in FUEL_NAMES:
            continue
        x, y, z = pose[0], pose[1], pose[2] - Z_SHIFT
        if abs(z) > 3 or math.hypot(x, y) < KEEP_CLEAR:
            continue
        if key in VEHICLE_RADIUS:
            if in_traffic_lane(x, y):
                continue  # a car parked in a lane would block the moving traffic
            r = VEHICLE_RADIUS[key]
            if any(math.hypot(x - vx, y - vy) < r + vr + VEHICLE_GAP for vx, vy, vr in vehicles):
                continue
            vehicles.append((x, y, r))
        placed.append((name, FUEL_NAMES[key], (x, y, z, pose[3], pose[4], pose[5])))

    parked = [{"x": x, "y": y, "r": r} for x, y, r in vehicles]
    traffic = []
    rng_t = random.Random(11)
    count = 0
    for rname, pts, width in roads:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        length = math.hypot(x1 - x0, y1 - y0)
        dx, dy = (x1 - x0) / length, (y1 - y0) / length
        cross = [q for q in roads if q[0] != rname]
        for k in range(int(length / TRAFFIC_STEP)):
            t = k * TRAFFIC_STEP + rng_t.uniform(-1.0, 1.0)
            cx, cy = x0 + dx * t, y0 + dy * t
            if any(min(abs(cx - c[1][0][0]) if abs(c[1][0][0] - c[1][1][0]) < 1e-6 else 99,
                       abs(cy - c[1][0][1]) if abs(c[1][0][1] - c[1][1][1]) < 1e-6 else 99) < 7.5
                   for c in cross):
                continue
            for side in (1, -1):
                x, y = cx - dy * LANE_OFFSET * side, cy + dx * LANE_OFFSET * side
                if not TRAFFIC_MIN_R < math.hypot(x, y) < TRAFFIC_MAX_R or rng_t.random() < 0.1:
                    continue
                key = rng_t.choice(TRAFFIC_MODELS)
                r = VEHICLE_RADIUS[key]
                if any(math.hypot(x - vx, y - vy) < r + vr + VEHICLE_GAP for vx, vy, vr in vehicles):
                    continue
                vehicles.append((x, y, r))
                count += 1
                yaw = math.atan2(dy, dx) + (0.0 if side == 1 else math.pi)
                placed.append((f"traffic_{count}", FUEL_NAMES[key], (x, y, 0.04, 0, 0, yaw)))
                traffic.append({"name": f"traffic_{count}", "x": round(x, 3), "y": round(y, 3), "z": 0.04, "yaw": round(yaw, 4), "r": r})

    for model in world.findall("model"):
        if model.get("name").startswith("gas_station"):
            p = nums(model.findtext("pose"))
            placed.append((model.get("name"), "Gas Station", (p[0], p[1], p[2] - Z_SHIFT, p[3], p[4], p[5])))

    for name, fuel, p in placed:
        out.append(f"""    <include>
      <uri>{FUEL}{fuel}</uri>
      <name>{name}</name>
      <static>true</static>
      <pose>{p[0]:.3f} {p[1]:.3f} {p[2]:.3f} {p[3]:.4f} {p[4]:.4f} {p[5]:.4f}</pose>
    </include>
""")

    # Pedestrians on the sidewalks.
    rng = random.Random(7)
    walkers = []
    strips = [b for b in sidewalk_boxes if min(b[1] - b[0], b[3] - b[2]) < 8]
    for _ in range(20000):
        if len(walkers) >= N_PEOPLE:
            break
        b = rng.choice(strips)
        x, y = rng.uniform(b[0], b[1]), rng.uniform(b[2], b[3])
        if math.hypot(x, y) < KEEP_CLEAR or any(math.hypot(x - wx, y - wy) < 7 for wx, wy in walkers):
            continue
        walkers.append((x, y))
        n += 1
        out.append(f"""    <include>
      <uri>{FUEL}{PEOPLE[n % len(PEOPLE)]}</uri>
      <name>person_{n}</name>
      <static>true</static>
      <pose>{x:.3f} {y:.3f} 0.15 0 0 {rng.uniform(-3.14, 3.14):.3f}</pose>
    </include>
""")

    if traffic_json:
        with open(traffic_json, "w", encoding="utf-8") as fh:
            json.dump({"cars": traffic, "parked": parked,
                       "roads": [{"p0": pts[0], "p1": pts[-1], "width": width} for _, pts, width in roads]}, fh, indent=1)

    out.append("  </world>\n</sdf>\n")
    sys.stdout.write("".join(out))
    sys.stderr.write(f"{len(placed)} models, {len(walkers)} people, {len(vehicles)} vehicles\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
