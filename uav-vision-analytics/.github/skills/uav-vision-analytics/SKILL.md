---
name: uav-vision-analytics
description: >-
  Build an end-to-end UAV object detection, telemetry overlay, and
  vision-language captioning app on Intel hardware using DL Streamer
  Pipeline Server with MAVLink telemetry. USE FOR: UAV/drone vision
  analytics stacks that detect objects from aerial video across one or more
  cameras, overlay live MAVLink telemetry (GPS, altitude, speed, heading) on
  the annotated RTSP stream, run a VLM per camera to publish
  natural-language captions over MQTT, and auto start/stop pipelines on
  drone armed/disarmed state. Supports pymavlink (self-contained, PX4 SITL)
  and UAVSDK (uav-mission-compute-sdk) deployment modes. Cameras, detection
  device, and captioning are declared per camera in a YAML file
  (`configs/cameras-*.yaml`) — adding a camera needs no pipeline edits. RTSP
  streams feed a downstream `uav-ground-station` VSS deployment for
  natural-language search; this skill does not build VSS. DO NOT USE FOR:
  camera analytics without MAVLink, cloud-only deployments, model training,
  or the VSS search stack (`uav-ground-station`).
license: Apache-2.0
compatibility: >-
  Requires Docker + Docker Compose v2, Intel CPU (optionally GPU/NPU).
  pymavlink mode is self-contained (PX4 SITL); UAVSDK mode requires
  uav-mission-compute-sdk running first. Ports 443/80, 8555 (RTSP), 1883
  (MQTT), 14551/udp (MAVLink to a remote QGC, pymavlink mode) must be free; pipeline server is only
  reachable through `nginx` (self-signed TLS). Captioning (VLM) needs an
  extra OpenVINO IR model (`make vlm-model`), CPU/GPU only, never NPU.
---

<!-- SPDX-FileCopyrightText: (C) 2026 Intel Corporation -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# UAV Vision Analytics Skill

Build an end-to-end aerial object detection, telemetry overlay, and
scene-captioning application using Intel DL Streamer Pipeline Server. The
stack detects objects in video from one or more UAV cameras using YOLO11s (or
a custom OpenVINO model), overlays live MAVLink flight telemetry (altitude,
speed, heading, GPS) onto each annotated RTSP stream, optionally runs a
vision-language model (VLM) per camera to publish natural-language scene
captions over MQTT, and automatically starts/stops inference pipelines in
sync with the UAV armed/disarmed state. Which cameras run, on which device,
and whether they caption is declared in one YAML file — pipelines themselves
are a fixed, pre-generated matrix of every `(source, detection device,
caption device)` combination, so the skill never writes per-camera pipeline
strings.

## Architecture Overview

```
configs/cameras-{pymavlink,uavsdk}.yaml  (per-camera: source, detection device/model,
      │                                   captioning on/off + device/model/prompt, overlay)
      ▼
scripts/camera_config.py  — validates the file, picks the matching pipeline name,
      │                      builds the REST start request (camera_config.build_plan)
      ▼
scripts/mavlink_pipeline_manager.py  — re-reads the file on every ARM, starts one
      │                                 pipeline per enabled camera, deletes them on DISARM
      ▼
DL Streamer Pipeline Server  (config-{pymavlink,uavsdk}.json: 9 fixed pipelines; pymavlink adds 2 live-camera ones)
  ├── gvadetect (OpenVINO YOLO11s, CPU/GPU/NPU)
  ├── gvapython (telemetry overlay — altitude, speed, heading, GPS)
  ├── [captioning branch, tee split]
  │     └── gvagenai (VLM, CPU/GPU) → gvametaconvert → gvapython MQTTPublisher → MQTT uav/caption/<camera>
  ├── gvametaconvert → gvametapublish → MQTT
  └── appsink → RTSP :8555 (path uav-<camera>)
         │
         ▼
  nginx reverse proxy (TLS, self-signed) :443/:80 → REST+UI; :8555 → RTSP passthrough
         │
         ▼
  QGC / ffplay / browser          uav-ground-station (separate machine/skill — the
                                   dashboard connects to this host's IP and relays
                                   uav-<camera> RTSP + uav/caption/<camera> MQTT;
                                   its own VSS overlay indexes that RTSP for search.
                                   Out of scope for this skill; see `uav-ground-station`.)

MAVLink/MQTT → Pipeline Manager → start/stop pipelines on ARMED/DISARMED
```

## Deployment Modes

| Mode | Compose file | Telemetry source | Camera file | When to use |
|------|-------------|-----------------|-------------|-------------|
| **pymavlink** | `docker-compose-pymavlink.yml` | MAVLink UDP :14541 via mavlink-router from PX4 SITL | `configs/cameras-pymavlink.yaml` | Self-contained simulation |
| **uavsdk** | `docker-compose-uavsdk.yml` | MQTT `uav/{id}/telemetry/status` from SDK | `configs/cameras-uavsdk.yaml` | Integration with uav-mission-compute-sdk |

## How to Use This Skill

1. Read this file end-to-end.
2. Ask the questions in ONE batched message (defaults shown in brackets); accept
   `go` / `defaults` / empty to proceed.
3. Validate parameters before generating files.
4. Load reference files on demand — **do not load all up front**.
5. Generate the application files and verify against the completion criteria.

## Reference Files (load on demand)

| File | Load when authoring |
|------|-------------------|
| [`references/CAMERAS.md`](references/CAMERAS.md) | `configs/cameras-*.yaml` schema, `camera_config.py`, pipeline-name derivation, adding/editing cameras |
| [`references/PIPELINE.md`](references/PIPELINE.md) | DL Streamer config.json (pipeline matrix), REST launcher, payload format, captioning branch wiring |
| [`references/CAPTIONING.md`](references/CAPTIONING.md) | VLM scene captioning: `gvagenai`, `make vlm-model`, MQTT caption topics, prompt/token tuning |
| [`references/TELEMETRY.md`](references/TELEMETRY.md) | MAVLink/UAVSDK telemetry overlay (gvapython), pipeline manager scripts |
| [`references/DEPLOY.md`](references/DEPLOY.md) | Docker Compose services (incl. nginx, metrics-manager), env vars, Makefile targets, volumes, device access |
| [`references/MODEL.md`](references/MODEL.md) | YOLO11s download + OpenVINO export, custom detection-model substitution |
| [`references/TESTS.md`](references/TESTS.md) | pytest structure, REST API tests, RTSP stream validation, MQTT/caption checks |
| [`benchmark.md`](benchmark.md) | Evaluation baseline: agent output with the skill loaded compared with no skill |

## Parameters (from invoking prompt)

| Param | Purpose |
|-------|---------|
| `{{DEPLOYMENT_MODE}}` | `pymavlink` \| `uavsdk` |
| `{{CAMERAS}}` | List of cameras, each with `{{VIDEO_SOURCE}}`, `{{DEVICE}}`, `{{CAPTIONING}}` (see below) |
| `{{VIDEO_SOURCE}}` (per camera) | `file` (looped `.avi`/`.mp4`/`.mkv`/`.ts` from `resources/videos`) \| `device` (RealSense/USB `/dev/video*`) \| `rtsp` (SDK RTSP stream, uavsdk mode only) |
| `{{DEVICE}}` (per camera) | `cpu` \| `gpu` \| `npu` — detection device for that camera |
| `{{CAPTIONING}}` (per camera) | `off` \| `cpu` \| `gpu` — whether/where the VLM captions that camera (never `npu`) |
| `{{CAPTION_PROMPT}}` / `{{CAPTION_MAX_TOKENS}}` (per camera) | VLM prompt (letters, digits, spaces, `, . ? ' -` only) and max new tokens |
| `{{MODEL}}` | `yolo11s` (default) \| path to custom OpenVINO IR `.xml` |
| `{{VLM_MODEL}}` | `InternVL2-1B` (default) \| another OpenVINO-IR-exportable VLM id |
| `{{UAV_ID}}` | UAV identifier for UAVSDK MQTT topic, e.g. `uav-1` |
| `{{STACK_DIR}}` | output directory for the new application stack |
| `{{OVERLAY_NAME}}` | label shown in the telemetry overlay, e.g. `MyUAV-CPU` |

## Questions (single batched prompt)

1. Deployment mode [`pymavlink`] (`pymavlink` or `uavsdk`)
2. Cameras (one or more) — for each: id, source (`file`/`device`/`rtsp`), detection device
   (`cpu`/`gpu`/`npu`), captioning (`off`/`cpu`/`gpu`) [default: 1 camera, `file`, `npu`, captioning `off`]
3. Detection model [`yolo11s`] (or path to a custom OpenVINO IR `.xml` file)
4. VLM model [`InternVL2-1B`] (only asked if any camera has captioning enabled)
5. Output directory [`./uav-stack`]
6. UAV ID (UAVSDK mode only) [`uav-1`]
7. Also deploy the remote viewer/dashboard (`uav-ground-station`) to watch this
   stack's video/captions from another machine, optionally with natural-language
   video search? [no] (`no` / `basic` / `with VSS search`)

If (7) is anything other than `no`, complete this skill first, then hand off
to the separate **`uav-ground-station`** skill, passing this stack's
reachable host IP as its `VIZ_COMPANION_HOST` — do not fold its steps into
this skill or skip straight to `docker compose up` on the ground-station
repo without that skill's own guardrails (VSS env-var checks, model-download
warning, etc.). There is no "physical camera" or other deployment *mode*
toggle in `uav-ground-station` itself — camera/source choices belong
entirely to this skill's `configs/cameras-*.yaml` (see `references/CAMERAS.md`).

## Parameter Validation (enforce BEFORE file generation)

| Param | Rule | Failure |
|-------|------|---------|
| `DEPLOYMENT_MODE` | `pymavlink`\|`uavsdk` | wrong compose file selected |
| Camera `id` | `^[a-z][a-z0-9_]{0,31}$` | RTSP path / MQTT topic / `model-instance-id` invalid |
| Camera source | `file`\|`device`\|`rtsp` (`rtsp` only in `uavsdk` mode) | pipeline not in the matrix, `camera_config.py` rejects the file |
| Camera `detection.device` | `cpu`\|`gpu`\|`npu` | no matching `uav_object_detection_*`/`uav_camera_*` pipeline |
| Camera `captioning.device` | `off`\|`cpu`\|`gpu` (never `npu`) | `gvagenai` has no NPU plugin — pipeline fails at start |
| `MODEL` | ends in `.xml`, file exists (if custom) | DL Streamer Pipeline Server fails to load model |
| `VLM_MODEL` | OpenVINO IR folder exists under `resources/ov_models/<device>/<model>/` for every device a camera captions on | `gvagenai` fails to load, pipeline start errors |
| `captioning.prompt` | `^[A-Za-z0-9 ,.?'-]+$` | pipeline parameter pattern rejects the start request |
| `UAV_ID` | `^[A-Za-z0-9_-]{1,32}$`, no spaces | MQTT topic / RTSP URL invalid |

## Supported Use Cases

| Use case | `DEPLOYMENT_MODE` | Cameras | Notes |
|----------|------------------|---------|-------|
| PX4 SITL sim, one looped video, CPU detection, no captions | `pymavlink` | 1× `file`/`cpu`/off | Minimal stack |
| PX4 SITL sim, three cameras, NPU detection + GPU captions | `pymavlink` | 3× `file`/`npu`/`gpu` | Matches the shipped `cameras-pymavlink.yaml` |
| Intel RealSense/USB camera, GPU detection | `pymavlink` | 1× `device`/`gpu`/off | Needs `CAMERA_DEVICE_1`..`_4` in `.env` |
| SDK integration, 3-camera (nadir/forward/rear) with captions | `uavsdk` | 3× `rtsp`/mixed device/`gpu` | Matches the shipped `cameras-uavsdk.yaml`; requires `uav-mission-compute-sdk` running |
| Custom detection model, one camera, captioning off | `pymavlink` | 1× `file`/`cpu`/off | Swap `detection.model` only |

## Execution Guardrails

- Before generating files: verify all parameters pass validation.
- Before `make pymav-up` or `make uavsdk-up`: check ports 80, 443, 8555, 1883 are free.
- The two modes are mutually exclusive: both use the same `uav-va-*` container names and
  host ports. Run `make pymav-down` (or `make uavsdk-down`) before starting the other mode.
  Going from UAVSDK to pymavlink also needs `make down` in `uav-mission-compute-sdk`: its
  MQTT broker binds `1883` and `make pymav-up` fails with "port is already allocated".
  Camera edits go in the active mode's file (`cameras-pymavlink.yaml` / `cameras-uavsdk.yaml`).
- For UAVSDK mode: confirm `uav-mission-compute-sdk` stack is running first.
- Never hardcode secrets — use `.env` variables for `HOST_IP`, device GIDs, credentials.
- Use `make model` to download/export the detection model, and (if any camera captions)
  `make vlm-model` to download/export the VLM, before starting the stack.
- Always quote shell variables: `"$HOST_IP"`, `"$MODEL_PATH"`.
- Never add, remove or rename pipelines to add a camera — the fixed matrix in
  `config-{pymavlink,uavsdk}.json` (3 detection devices × {no caption, cpu, gpu} = 9
  `uav_object_detection_*`; pymavlink also ships `uav_camera_npu[_gpu]` for live cameras)
  already covers every combination (see `references/CAMERAS.md` and `references/PIPELINE.md`).
  Adding a camera means adding an entry to `configs/cameras-*.yaml`, never editing
  `config.json`.
- Container names come from `container_name:`, not the service name: `uav-va-dlstreamer-pipeline-server`,
  `uav-va-nginx`, `uav-va-metrics-manager` (plus `uav-va-broker`, `uav-va-px4`, `uav-va-mavlink-router`
  in pymavlink mode). Use them in every `docker exec`/`docker logs`.
- Local REST calls go through nginx and must bypass any shell proxy; model downloads keep it:
  ```bash
  curl -k -s --noproxy '*' "https://${HOST_IP}/pipelines/status"   # local nginx: bypass proxy
  make vlm-model                                                   # host download: keep proxy env
  ```
  Never write `http_proxy=` (empty) into `.env`.
- Run `make check-cameras` after every edit to `configs/cameras-*.yaml` — a file with
  errors is refused and the previous configuration keeps running, so a silent typo
  stops a camera without surfacing until `check-cameras`/`cameras-status` is run.
- Captioning devices are `cpu`/`gpu` only — never generate `captioning.device: npu`,
  `gvagenai` has no NPU plugin.
- `dlstreamer-pipeline-server` MUST NOT publish `8081`/`8555` to the host via `ports:` —
  it is reached only through `nginx` (self-signed TLS on `443`, `80` redirects, raw TCP
  passthrough for RTSP on `8555`).
- For pymavlink mode (details in `references/DEPLOY.md`): the `mavlink-router` build `context`
  MUST be `./mavlink-router` inside `{{STACK_DIR}}` (never a sibling repo), and the `px4`
  service MUST mount `10040_sihsim_quadx.post` and `10016_none_iris.post` and start through
  `px4-select-sim.sh` (AirSim when reachable, else SIH) — without the post files PX4 never routes
  MAVLink and the pipeline manager blocks forever waiting for a heartbeat.
- Never put active proxy vars in `.env.example` (an empty `http_proxy=` overrides the system
  proxy and breaks `make model` / `make vlm-model`); use commented examples: `# http_proxy=`.
- VSS live search is a `uav-ground-station` feature, not built here — for "search my drone
  video" point to its `make up-vss` and the `vss-live-search` guide. This skill ends at a
  running `uav-<camera>` RTSP stream (plus `uav/caption/<camera>` MQTT if captioning is on).

## Generated File Layout

```
{{STACK_DIR}}/
├── docker-compose-pymavlink.yml     # or docker-compose-uavsdk.yml
├── 10040_sihsim_quadx.post          # PX4 airframe MAVLink routing, SIH mode (pymavlink only)
├── 10016_none_iris.post             # same routing for the AirSim (none_iris) mode (pymavlink only)
├── px4-select-sim.sh                # px4 entrypoint: AirSim if reachable, else SIH (pymavlink only)
├── .env.example                     # template; `make init` creates the local env file from it
├── Makefile                         # init, model, vlm-model, stack up/down, pipeline start/stop
├── configs/
│   ├── cameras-{{MODE}}.yaml              # per-camera source/device/captioning declarations
│   ├── config-{{MODE}}.json               # fixed DL Streamer Pipeline Server pipeline matrix
│   ├── nginx/
│   │   ├── nginx-{{MODE}}.conf            # reverse proxy + TLS termination + RTSP passthrough
│   │   └── ssl/                           # self-signed cert/key
│   ├── mosquitto/                         # broker config (pymavlink only)
│   └── mavlink-router/                    # router config (pymavlink only)
├── gvapython/
│   └── telemetry-overlay-{{MODE}}.py      # gvapython telemetry overlay
├── scripts/
│   ├── camera_config.py                   # loads/validates cameras-*.yaml, builds the start-request plan
│   ├── mavlink_pipeline_manager.py        # armed/disarmed pipeline lifecycle, reads camera_config
│   └── convert_video.sh                   # prepares a video file for the `video:` source
├── mavlink-router/
│   ├── Dockerfile                         # self-contained build (pymavlink only — never reference an external path)
│   └── main.conf                          # mavlink-router config (pymavlink only)
├── resources/
│   ├── models/yolo11s/                    # exported OpenVINO detection model
│   ├── ov_models/{cpu,gpu}/InternVL2-1B/  # exported OpenVINO VLM (only if any camera captions)
│   └── videos/*.avi                       # sample video(s) (file source cameras)
└── tests/                             # conftest.py, test_stack_up.py, test_pipeline_start.py,
                                       # test_rtsp_stream.py, test_mavlink_trigger.py,
                                       # test_captioning.py (only if any camera captions)
```

## Template Variable Substitution

Every `{{VAR}}` in generated code MUST be substituted with its concrete value
before writing the file — literal `{{...}}` left in `cameras-*.yaml`, `config.json`,
docker-compose, or scripts is a syntax error (or, for `cameras-*.yaml`, a
`camera_config.py` validation failure).

## Completion Criteria (all must pass)

1. `make init` succeeds: `.env` created with auto-detected `HOST_IP` and GPU/NPU device paths.
2. `make model` succeeds: OpenVINO IR detection model present at
   `resources/models/yolo11s/yolo11s_openvino_model/yolo11s.xml`.
3. If any camera has captioning enabled: `make vlm-model` succeeds — OpenVINO IR VLM
   present at `resources/ov_models/<device>/<model>/` for every captioning device used.
4. `make check-cameras` reports the camera file as valid and prints the expected
   pipeline/stream-path/detection+captioning table with no unavailable-pipeline warnings.
5. `make pymav-up` (or `make uavsdk-up`) → all containers `running`, including `nginx`.
6. A GET on `https://localhost/pipelines` (client accepting the self-signed certificate)
   returns the registered pipeline definitions.
7. Pipeline manager starts with `make start-rtsp` (run in an interactive terminal —
   it uses `docker exec -it` and silently does nothing from a non-interactive
   context) and connects to MAVLink/MQTT. The vehicle is armed from QGroundControl
   (takeoff) or the SDK `arm`/`takeoff` API. To start the cameras without arming
   (quick tests, automation), run `docker exec -d -e HOST_IP="$HOST_IP"
   uav-va-dlstreamer-pipeline-server python3
   /home/pipeline-server/scripts/pipeline_manager.py --start-now`; in UAVSDK mode the
   SDK camera streams exist only while the vehicle is armed and flying.
8. On ARMED signal: pipelines start; RTSP streams appear at `:8555` as `uav-<camera>`.
9. `ffplay -rtsp_transport tcp rtsp://localhost:8555/uav-<camera>` shows annotated video with telemetry overlay.
10. For a captioning-enabled camera: `uav/caption/<camera>` on MQTT receives a new caption
    roughly every 5 seconds once the pipeline is running. If `mosquitto_sub` is not installed
    on the host, read it from the broker container: `docker exec uav-va-broker
    mosquitto_sub -t 'uav/caption/#' -C 3` (pymavlink) or `docker exec mqtt-broker ...` (UAVSDK).
11. On DISARMED signal: all pipeline instances are deleted.
12. On UAVSDK mode: pipelines start only after RTSP probe confirms streams are live.
13. `make pymav-down` (or `make uavsdk-down`) cleanly stops all containers.
14. `pytest -q tests/` passes all tests.
15. If question 7 above was anything other than `no`: the `uav-ground-station`
    skill was invoked separately afterward, with this stack's reachable IP passed
    as its `VIZ_COMPANION_HOST`, and its own completion criteria were met
    (dashboard reachable, camera card(s) visible once armed, Search panel present
    only if `with VSS search` was chosen).
