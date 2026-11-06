<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# FedAero UAV SDK PoC

PX4 SITL + Gazebo multi-camera UAV simulation with Intel Edge AI vision processing.

## Stack

```text
infra/                     Core simulation + messaging (root compose)
├── px4-sim/               PX4 + Gazebo + 3 cameras (nadir, forward, rear)
├── bridges/               MAVLink ↔ MQTT + camera → RTSP + REST API (port 8080)
│   ├── companion/         MAVLink ↔ MQTT + REST (all camera profiles)
│   ├── camera/            Gazebo → RTSP (sim-camera profile)
│   ├── usb-camera/        V4L2 webcam → RTSP (usb-camera profile)
│   └── realsense-camera/  Intel RealSense D400 → RTSP, IR + depth (realsense-camera profile)
├── mediamtx/              RTSP server (port 8554) for camera streams
├── mosquitto/             MQTT broker (host port 1884) for telemetry + detections
└── metrics-manager/       CPU/mem/GPU/NPU/power → InfluxDB + Grafana (observability profile)

sample-apps/               AI helper + demo app (sample-apps/docker-compose.yml)
├── helpers/
│   └── vision-processor/  RTSP → YOLOv2-tiny-vehicle (OpenVINO GPU) → MQTT detections + RTSP annotated
├── edge-ai-showcase/      Primary demo (port 5002)
└── mission-simulation/    Python mission scripts

mcp-server/                MCP server for AI agent integration
├── server.py              Entry point (discovers tools from tool_configs/)
├── providers/             Handler implementations (anomalib, dlstreamer, telemetry)
└── tool_configs/          YAML tool definitions (mavlink, anomalib, dlstreamer, edge_ai_suites)
```

**Compose files**:
- `docker-compose.yml` — core infra (sim + bridges + MQTT + RTSP + observability). Camera mode via `.env`
- `docker-compose.ethernet.yml` — override for remote FC
- `sample-apps/docker-compose.yml` — AI helper (vision-processor) + edge-ai-showcase (decoupled from infra)

**Makefile targets**: `make up-sim-camera` (Gazebo, 3 cams), `make up-usb-camera` (real V4L2 webcam, 1 cam),
`make up-realsense-camera` (Intel RealSense D400, IR + depth), each with a `-lean` variant that skips the
observability stack; `make apps` (helpers + apps), `make down`, `make apps-down`, `make up-ethernet FC_IP=x.x.x.x`,
`make stats` (per-container CPU/mem snapshot, no Grafana needed)

**Camera mode** (`.env`): defaults to multi-cam (3 cameras). Switch to single-cam, USB, or RealSense by using the
corresponding `make up-*` target (each rewrites the relevant `.env` vars).

**Video Architecture**: Gazebo/USB/RealSense → camera bridge → MediaMTX (RTSP raw) → vision-processor → MQTT (detections JSON + annotated JPEG frames)

## RTSP Streams (MediaMTX)
- `rtsp://localhost:8554/uav-1/nadir` — Raw nadir camera (downward) — sim (Gazebo) and USB modes
- `rtsp://localhost:8554/uav-1/forward` — Raw forward camera — sim mode only
- `rtsp://localhost:8554/uav-1/rear` — Raw rear camera — sim mode only
- `rtsp://localhost:8554/uav-1/ir` — Active-IR stereo stream — RealSense mode only
- `rtsp://localhost:8554/uav-1/depth` — Colorized depth stream — RealSense mode only

> [!NOTE]
> `realsense_camera_bridge.py` only wires up IR + depth today. Color/IMU (Gyro/Accel)/Pose are additional
> `librealsense2` streams the camera hardware may support (model-dependent) but are not currently captured —
> enabling them would require extending the bridge with the corresponding `rs.stream.*` config.

## MQTT Topics
All topics use the pattern `uav/{uav_id}/...` (default `uav_id` = `uav-1`) on broker `localhost:1884`.

**Telemetry** (published by `companion-bridge`):
- `uav/{id}/telemetry/position` — GPS position (lat, lon, altitude)
- `uav/{id}/telemetry/attitude` — Roll, pitch, yaw
- `uav/{id}/telemetry/battery` — Voltage, remaining %
- `uav/{id}/telemetry/velocity` — NED velocity
- `uav/{id}/telemetry/gps` — GPS fix/satellite info
- `uav/{id}/telemetry/status` — Armed state, flight mode, connection
- `uav/{id}/telemetry/#` — Wildcard, all telemetry subtopics

**Camera / Vision**:
- `uav/{id}/camera/{cam}/frame` — Raw camera frame (JPEG bytes, legacy MQTT mode)
- `uav/{id}/camera/{cam}/detections` — JSON detections from vision-processor
- `uav/{id}/camera/{cam}/processed` — Annotated frame with bounding boxes
- `uav/{id}/camera/+/detections` — Wildcard, all cameras' detections
- `uav/{id}/camera/+/frame` — Wildcard, all cameras' raw frames

**Commands & Scenescape**:
- `uav/{id}/command` — Legacy command channel (arm/disarm/etc.)
- `scenescape/data/camera/{ss_camera_id}` — 3D fusion data published by scenescape-adapter

Listen example:
```bash
mosquitto_sub -h localhost -p 1884 -t "uav/uav-1/telemetry/#" -v
```

## Commands
- `/start-stack` — Start UAV + apps
- `/switch-camera-mode` — Switch between `sim`, `usb`, and `realsense` camera profiles
- `/validate-infra` — Check PX4, MQTT, cameras
- `/capture-camera` — Grab frames for debug
- `/cleanup-stack` — Stop and clean up running Docker resources

## Startup Order
1. `make up-sim-camera` — Core infra (PX4 sim → bridges → MQTT/RTSP → observability)
2. `make apps` — AI helper (vision-processor) + edge-ai-showcase dashboard

## Running Missions
```bash
make deps          # one-time, from repo root: creates .venv
cd sample-apps/mission-simulation
MQTT_BROKER_HOST=localhost MQTT_BROKER_PORT=1884 \
REST_API_HOST=localhost REST_API_PORT=8080 \
../../.venv/bin/python mission_1_survey.py
```

## Key Gotchas
1. After PX4 restarts, restart bridges manually
2. Cameras only publish when UAV is armed (RTSP streams pause/resume)
3. PX4 auto-disarms ~20s after landing — don't call `disarm()` manually
4. Apps use `uav-mission-compute-sdk_default` network to reach infra containers
5. RTSP mode: Set `USE_RTSP=false` in docker-compose to revert to MQTT frame-by-frame
6. MediaMTX health: `docker exec vision-processor-multicam curl -sf http://mediamtx:9997/v3/paths/list`
7. Pipeline lifecycle: camera-bridge and vision-processor tear down pipelines on disarm and rebuild on re-arm (no manual restart needed)

## City World + 1080p Circle Recordings
Three synchronised 1920x1080@30 videos (front, bottom, rear) of a smooth circular flight over the citysim world,
saved in `recordings/<name>/` (`front.mp4`, `bottom.mp4`, `rear.mp4`, `rear_unrotated.mp4`).

- **World**: `infra/px4-sim/worlds/citysim_multicam.sdf`, generated by
  `python3 infra/px4-sim/make_citysim_world.py /tmp/citysim/worlds/simple_city.world infra/px4-sim/worlds/citysim_traffic.json > infra/px4-sim/worlds/citysim_multicam.sdf`
  (needs `git clone https://github.com/osrf/citysim /tmp/citysim`). It has no trees, parked cars, 70 static people
  and `traffic_*` cars in the lanes. The JSON feeds `traffic_mover.py`. Parked cars standing in a lane are dropped.
- **Smooth, jerk-free video recipe** (all of it matters; this is what produced v21):
  - *Cameras* (`infra/px4-sim/models/stab_cams/model.sdf`): 1920x1080, `update_rate` 30, `always_on`, near 0.1 / far 3000.
    Forward looks 45° down (FOV 1.2), rear looks 45° rear-down (FOV 1.2), nadir is tilted 30° forward (FOV 1.3).
    The link carries `Ry(90°)` like the drone's own `camera_link`.
  - *Recording* (`sample-apps/mission-simulation/sync_recorder.py`): the three cameras are written together on
    simulation time (exactly 30 fps whatever the real-time factor), x264 `-preset veryfast -crf 20 -g 60`, yuv420p.
  - *Flight* (`mission_record_circle.py`): offboard moving-setpoint circle, radius 28.6 m, altitude 15 m, one lap
    per `--duration` (90 s gives about 2 m/s) so the clip loops. The first lap is flown without recording, then
    exactly one lap (theta 2π to 4π) is recorded. PX4 limits are set first (`SMOOTH_PARAMS`): `MPC_ACC_HOR` 0.6,
    `MPC_ACC_HOR_MAX` 1.0, `MPC_JERK_AUTO` 0.8, `MPC_TILTMAX_AIR` 12, `MPC_YAWRAUTO_MAX` 12, `MPC_YAWRAUTO_ACC` 3,
    and `MPC_XY_CRUISE` = 2πr/duration.
  - *Gimbal*: heading low-pass `--stab-yaw-tau 0.5` and position low-pass `--stab-pos-tau 0.3` (sim seconds). Without
    the gimbal the body-fixed cameras bank with the drone and wobble; a 60 s lap with no filtering (v22) showed a bad
    ending, so use the 90 s lap.
- **Stabilised cameras**: `infra/px4-sim/gimbal_stabilizer.py` keeps the cameras (static model `stab_cams_0`) level and
  following only the drone position and heading. Needs `PX4_MODEL_DIR=multi_cam_stab` and `CAM_GZ_MODEL=stab_cams_0`.
  The bottom camera is tilted 30° forward: a pure top-down view is out of distribution for COCO yolo11s (~0 detections).
  The rear camera comes out upside down, so rotate it with `ffmpeg -vf hflip,vflip`.
- **Moving traffic**: `infra/px4-sim/traffic_mover.py` (run in the camera-bridge container) drives `traffic_*` with
  `set_pose_vector`. Cars only move forward, follow the car ahead (IDM) and stop at alternating traffic lights.
  People are never moved (static models would look like skating).
- **Record**: `sample-apps/mission-simulation/mission_record_circle.py --out recordings/<name> --duration 90 --radius 28.6 --altitude 15 --stabilize --traffic --stab-yaw-tau 0.5 --stab-pos-tau 0.3`
  (add `--traffic` only for the citysim world). It records from simulation time, so a slow simulator is fine.
- **Bring-up**: `make up-sim-camera` builds images and hangs if `repositories.intel.com` is unreachable. Use
  `docker compose ... --profile sim-camera up -d --no-build` with `PX4_START_SCRIPT=start_px4_multicam.sh PX4_MODEL_DIR=multi_cam_stab PX4_WORLD_FILE=citysim_multicam.sdf GZ_WORLD=citysim_multicam CAMERA_IDS=nadir,forward,rear VISION_CAMERA_IDS=nadir,forward,rear CAM_GZ_MODEL=stab_cams_0`.
- **Before every run**: restart `px4-gazebo` (a second spawn of `stab_cams_0` is not handled), then restart the
  bridges, and wait for `docker exec px4-gazebo sh -c 'cd /px4/build/px4_sitl_default/rootfs && ../bin/px4-commander check'`
  to print `Preflight check: OK`. Arming earlier returns `500 COMMAND_DENIED`.
- **CPU**: the world plus three 1080p cameras runs the simulator at about 0.4x real time. Stop other CPU-heavy
  containers (e.g. the `uav-va-*` pipeline server) while recording.
- **gz-transport (Python)**: a node that subscribes cannot make service requests reliably, and requests must not be made
  inside a subscription callback. Subscribe in a worker thread or child process and request from another node.
- **Detection check**: yolo11s from `uav-vision-analytics/resources/models/yolo11s/`. Confidence 0.5 gives about 5-10% non-vehicle
  detections (mostly rooftops read as airplane/keyboard/laptop). `uav-vision-analytics` configs use `gvadetect threshold=0.5`.
- **Install as vision-analytics samples**: `cd ../uav-vision-analytics && ./scripts/convert_video.sh <mp4> -o resources/videos/uav_sample_{front,bottom,back}.avi -n 1750 -f`
  (rear.mp4 -> `back`), then `make start-rtsp` in a terminal (it needs a TTY and stays running).
