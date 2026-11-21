<!-- SPDX-FileCopyrightText: (C) 2026 Intel Corporation -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Deployment Reference — UAV Vision Analytics

## Docker Compose Service Map

### pymavlink mode (`docker-compose-pymavlink.yml`)

| Service | Image | Ports | Role |
|---------|-------|-------|------|
| `dlstreamer-pipeline-server` | `${DLSTREAMER_PIPELINE_SERVER_IMAGE}`-pymavlink (built inline with `pip install pymavlink`) | *(none — reached only via `nginx`)* | AI inference + RTSP output |
| `broker` | `eclipse-mosquitto:2.0.22` | *(none — reached only via `nginx`)* | MQTT broker for detection metadata and captions |
| `px4` | `px4io/px4-sitl:v1.18.0-beta1-660-g61907962c9` | — | PX4 SITL flight controller simulator (requires `10040_sihsim_quadx.post` volume mount) |
| `mavlink-router` | custom build | `${MAVLINK_UDP_PORT:-14551}/udp` | Routes MAVLink :14550 → :14541 broadcast; also republishes UDP to the host for a remote QGroundControl |
| `metrics-manager` | `intel/metrics-manager:2026.3.0-*` | — | CPU/GPU/NPU/power metrics collection |
| `nginx` | `nginx:1.27-alpine` | `80`, `443`, `8555`, `1883` | Reverse proxy / TLS termination (self-signed cert) for REST+UI (`80`→`443` redirect) and RTSP; raw MQTT passthrough on `1883` |

`dlstreamer-pipeline-server` and `broker` publish **no** ports to the host directly —
every port a client touches (`80`/`443` REST+UI, `8555` RTSP, `1883` MQTT) is published
by `nginx` only, bound to `${HOST_IP:-127.0.0.1}`.

Every service sets `container_name: uav-va-<service>` (e.g. `uav-va-dlstreamer-pipeline-server`,
`uav-va-nginx`, `uav-va-broker`); `docker exec`/`docker logs` and the Makefile use those names,
never the bare service name. Both modes use the same names and host ports, so only one mode runs
at a time.
### px4 Service (pymavlink mode)

The `px4` service MUST mount the airframe configuration file that tells PX4 which
MAVLink target to use. Without this mount PX4 SITL sends to its default
endpoint (within the container) and mavlink-router never receives packets,
causing the pipeline manager to block forever on `Waiting for heartbeat`.

```
{{STACK_DIR}}/10040_sihsim_quadx.post:
{{STACK_DIR}}/10016_none_iris.post:      # identical content; used when PX4 connects to AirSim
mavlink start -u 14541 -t $(getent hosts mavlink-router | awk '{print $1}')
```

`px4-select-sim.sh` (the container entrypoint) picks the simulator at start: with `PX4_SIM_MODEL=auto`
it runs `none_iris` (PX4 connects to AirSim over TCP 4560) if AirSim's RPC port (`41451`) answers at
`PX4_SIM_HOST_ADDR`, otherwise `sihsim_quadx`. It must probe the RPC port, never `4560` (AirSim accepts a
single connection there). `PX4_SIM_HOST_ADDR` must be AirSim's `LocalHostIp`. See
`docs/user-guide/how-to-guides/connect-airsim.md`.

Docker Compose fragment:
```yaml
  px4:
    image: px4io/px4-sitl:v1.18.0-beta1-660-g61907962c9
    hostname: px4-sitl
    container_name: uav-va-px4
    entrypoint: ["bash", "/opt/px4/bin/px4-select-sim.sh"]
    environment:
      - PX4_SIM_MODEL=${PX4_SIM_MODEL:-auto}
      - PX4_SIM_HOST_ADDR=${PX4_SIM_HOST_ADDR:-host.docker.internal}
      - AIRSIM_API_PORT=${AIRSIM_API_PORT:-41451}
    volumes:
      - ./px4-select-sim.sh:/opt/px4/bin/px4-select-sim.sh:ro
      - ./10040_sihsim_quadx.post:/opt/px4/etc/init.d-posix/airframes/10040_sihsim_quadx.post
      - ./10016_none_iris.post:/opt/px4/etc/init.d-posix/airframes/10016_none_iris.post
    stdin_open: true
    tty: true
    networks:
      - app_network
    depends_on:
      - mavlink-router
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

---

### UAVSDK mode (`docker-compose-uavsdk.yml`)

| Service | Image | Ports | Role |
|---------|-------|-------|------|
| `dlstreamer-pipeline-server` | `${DLSTREAMER_PIPELINE_SERVER_IMAGE}` | *(none — reached only via `nginx`)* | AI inference + RTSP output; `ARM_SOURCE=mqtt` reads the SDK's MQTT status topic, `CAMERAS_CONFIG=configs/cameras-uavsdk.yaml` |
| `metrics-manager` | `intel/metrics-manager:2026.3.0-rc1` | — | CPU/GPU/NPU/power metrics collection (container `uav-va-metrics-manager`) |
| `nginx` | `nginx:1.27-alpine` | `80`, `443`, `8555` | Reverse proxy / TLS termination; this mode has no `broker`/`px4`/`mavlink-router` services — MQTT (caption/telemetry) is the SDK's own broker, reached via `host.docker.internal:${MQTT_BROKER_PORT:-1883}` |

No `broker`, `px4` or `mavlink-router` service exists in this mode — telemetry arrives over
the SDK's MQTT topic and captions/detections publish back to the SDK's own broker
(`host.docker.internal`), not a broker in this compose file.

**Prerequisite:** `uav-mission-compute-sdk` must already be running.

---

## mavlink-router Self-Containment (pymavlink mode)

> [!NOTE]
> The shipped mainline `uav-vision-analytics` repo's own
> `docker-compose-pymavlink.yml` points `mavlink-router`'s build `context` at
> `../uav-mission-compute-sdk/infra/px4-sim/mavlink-router` — a sibling
> checkout. That only works there because the whole
> `federal-and-aerospace-ai-suite` monorepo (with `uav-mission-compute-sdk`
> next to it) is assumed to be cloned together. This skill instead generates
> `{{STACK_DIR}}` as a standalone directory with no such sibling guaranteed,
> so it MUST vendor the two files below into the stack and build locally —
> never copy the sibling-repo path verbatim into a generated stack.

The pymavlink stack this skill generates MUST be fully self-contained and
buildable without any sibling repository being checked out. Never set the
`mavlink-router` service's build `context` to a path outside `{{STACK_DIR}}` —
that path will not exist for a standalone `{{STACK_DIR}}` and `docker compose
up` fails with `unable to prepare context: path ... not found`.

Always copy both files into the generated stack and build from the local
directory:

```
{{STACK_DIR}}/mavlink-router/
├── Dockerfile     # builds mavlink-router from source (ubuntu:24.04 base)
└── main.conf      # routing config (may be stack-specific, see TELEMETRY.md)
```

```yaml
mavlink-router:
  build:
    context: ./mavlink-router
    dockerfile: Dockerfile
    args:
      http_proxy:  ${http_proxy:-}
      https_proxy: ${https_proxy:-}
      no_proxy:    ${no_proxy:-localhost,127.0.0.0/8}
      NO_PROXY:    ${NO_PROXY:-localhost,127.0.0.0/8}
  container_name: uav-va-mavlink-router
  restart: unless-stopped
  volumes:
    - ./mavlink-router/main.conf:/etc/mavlink-router/main.conf
  networks:
    - app_network
```

The `Dockerfile` source (clones and builds `mavlink-router` from GitHub) can
be copied from any existing pymavlink stack's `mavlink-router/Dockerfile` in
this repo, or reused as-is:

```dockerfile
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG http_proxy
ARG https_proxy
ARG no_proxy
ENV http_proxy=${http_proxy} https_proxy=${https_proxy} no_proxy=${no_proxy}

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential pkg-config \
    libssl-dev meson ninja-build python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/mavlink-router/mavlink-router.git /src \
    && cd /src \
    && git submodule update --init --recursive \
    && meson setup build . -Dsystemdsystemunitdir=/usr/lib/systemd/system \
    && ninja -C build \
    && ninja -C build install \
    && find /src -delete

ENV http_proxy= https_proxy= no_proxy=

COPY main.conf /etc/mavlink-router/main.conf

CMD ["mavlink-routerd", "-c", "/etc/mavlink-router/main.conf"]
```

The `main.conf` bind-mounted at runtime overrides the one baked in at build
time, so stack-specific routing (e.g. broadcast vs. point-to-point UDP
endpoints, see `references/TELEMETRY.md`) always takes effect.

---

## DL Streamer Pipeline Server Docker Compose Fragment

```yaml
dlstreamer-pipeline-server:
  build:
    context: .
    dockerfile_inline: |
      FROM ${DLSTREAMER_PIPELINE_SERVER_IMAGE}
      RUN pip install --no-cache-dir pymavlink    # pymavlink mode only
  image: ${DLSTREAMER_PIPELINE_SERVER_IMAGE}-pymavlink
  container_name: uav-va-dlstreamer-pipeline-server
  environment:
    - http_proxy=${http_proxy}
    - https_proxy=${https_proxy}
    - no_proxy=${no_proxy},${HOST_IP}
    - NO_PROXY=${no_proxy},${HOST_IP}
    - ENABLE_RTSP=true
    - RTSP_PORT=8555
    - APPEND_PIPELINE_NAME_TO_PUBLISHER_TOPIC=true
    - RUN_MODE=EVA
    - EMIT_SOURCE_AND_DESTINATION=true
    - REST_SERVER_PORT=8081
    - SERVICE_NAME=dlstreamer-pipeline-server
    - MQTT_HOST=broker
    - MQTT_PORT=1883
    - ENABLE_OPEN_TELEMETRY=true
    - ZE_ENABLE_ALT_DRIVERS=libze_intel_npu.so    # NPU
    - NPU_DEVICE=${NPU_DEVICE:-/dev/null}
  volumes:
    - dlstreamer-pipeline-server-pipeline-root:/var/cache/pipeline_root:uid=1999,gid=1999
    - "./resources:/home/pipeline-server/resources"
    - "./configs/config-pymavlink.json:/home/pipeline-server/config.json"
    - "./gvapython/telemetry-overlay-pymavlink.py:/home/pipeline-server/gvapython/telemetry-overlay-pymavlink.py"
    - "./scripts/mavlink_pipeline_manager.py:/home/pipeline-server/scripts/pipeline_manager.py"
    - "./scripts/camera_config.py:/home/pipeline-server/scripts/camera_config.py:ro"
    # The folder with cameras-pymavlink.yaml is mounted, not the file: editors save by
    # replacing the file, and a single-file mount would keep showing the old one.
    - "./configs:/home/pipeline-server/configs:rw"
    - "/run/udev:/run/udev:ro"
    - "/tmp:/tmp"
  group_add:
    # GPU/NPU/camera device GIDs, auto-detected by 'make init' / scripts/update_device_gids.sh
    - "${GPU_CARD_GID:-44}"
    - "${GPU_RENDER_GID:-109}"
    - "${NPU_GID:-65501}"
    - "${CAMERA_GID:-65502}"
  device_cgroup_rules:
    - "c 189:* rmw"
    - "c 209:* rmw"
    - "a 189:* rwm"
  devices:
    - ${GPU_DEVICE:-/dev/dri/card0}:${GPU_DEVICE:-/dev/dri/card0}
    - ${GPU_RENDER_DEVICE:-/dev/dri/renderD128}:${GPU_RENDER_DEVICE:-/dev/dri/renderD128}
    - ${NPU_DEVICE:-/dev/null}:${NPU_DEVICE:-/dev/null}
    # live cameras (RealSense, USB webcams): same host path appears in the container, so the
    # path in configs/cameras-pymavlink.yaml is the host path
    - ${CAMERA_DEVICE_1:-/dev/null}:${CAMERA_DEVICE_1:-/dev/null}
    - ${CAMERA_DEVICE_2:-/dev/null}:${CAMERA_DEVICE_2:-/dev/null}
    - ${CAMERA_DEVICE_3:-/dev/null}:${CAMERA_DEVICE_3:-/dev/null}
    - ${CAMERA_DEVICE_4:-/dev/null}:${CAMERA_DEVICE_4:-/dev/null}
  networks:
    - app_network
  # Not "on-failure": a pipeline-server abort (e.g. a GPU runtime error while loading several
  # models) can still exit 0, which "on-failure" would not restart. A manual `docker stop`
  # still stays stopped.
  restart: unless-stopped
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

> [!IMPORTANT]
> `dlstreamer-pipeline-server` must NOT publish `8081`/`8555` to the host via `ports:`.
> It is reached only through the `nginx` reverse-proxy / TLS-termination service below
> (the only service that publishes ports to the host), with a self-signed cert and an
> HTTP→HTTPS redirect:
>
> ```yaml
> nginx:
>   image: nginx:1.27-alpine
>   container_name: uav-va-nginx
>   restart: unless-stopped
>   volumes:
>     - ./configs/nginx/nginx-pymavlink.conf:/etc/nginx/nginx.conf:ro
>     - ./configs/nginx/ssl:/etc/nginx/ssl:ro
>   ports:
>     - "${HOST_IP:-127.0.0.1}:80:80"       # HTTP -> 301 redirect to HTTPS
>     - "${HOST_IP:-127.0.0.1}:443:443"     # HTTPS reverse proxy -> dlstreamer-pipeline-server:8081 (+ metrics-manager)
>     - "${HOST_IP:-127.0.0.1}:8555:8555"   # raw TCP passthrough (stream {} block) -> dlstreamer-pipeline-server:8555
>   networks:
>     - app_network
>   depends_on:
>     - dlstreamer-pipeline-server
> ```
>
> Binding to `${HOST_IP:-127.0.0.1}` (not `0.0.0.0`) scopes the published ports to the
> specific interface in `HOST_IP` instead of every interface on the host.

**For UAVSDK mode** mount the UAVSDK overlay and manager instead:
```yaml
    - "./gvapython/telemetry-overlay-uavsdk.py:/home/pipeline-server/gvapython/telemetry-overlay-uavsdk.py"
    - "./scripts/mavlink_pipeline_manager.py:/home/pipeline-server/scripts/pipeline_manager.py"
    - "./scripts/camera_config.py:/home/pipeline-server/scripts/camera_config.py:ro"
    - "./configs:/home/pipeline-server/configs:rw"          # cameras-uavsdk.yaml (CAMERAS_CONFIG), ARM_SOURCE=mqtt
```
And set `UAV_ID` env var (default `uav-1`).

---

## .env Variables (generate `.env.example`, never commit `.env` itself)

| Var | Set by | Purpose |
| --- | --- | --- |
| `HOST_IP` | `make init` (auto-detects LAN IP) | Binds nginx's published ports (`80`/`443`/`8555`/`1883`) and is used in RTSP/REST URLs. `127.0.0.1` restricts to loopback (breaks remote QGroundControl/VLC/ffplay). |
| `DLSTREAMER_PIPELINE_SERVER_IMAGE` | generated, pinned | e.g. `intel/dlstreamer-pipeline-server:2026.3.0-ubuntu24-rc1` |
| `GPU_DEVICE`, `GPU_RENDER_DEVICE` | `make init` (auto-detect) or manual | GPU device paths, e.g. `/dev/dri/card0`, `/dev/dri/renderD128` |
| `NPU_DEVICE` | `make init` (auto-detect, `/dev/null` if absent) | NPU device path, e.g. `/dev/accel/accel0` |
| `GPU_CARD_GID`, `GPU_RENDER_GID`, `NPU_GID`, `CAMERA_GID` | written by `scripts/update_device_gids.sh` on `pymav-up`/`uavsdk-up` | `group_add` values so the container can access the devices; never set these by hand |
| `CAMERA_DEVICE_1`..`_4` | `make init` (first USB MJPG/YUYV node) or manual | Live-camera device paths matched by `device:` in `cameras-*.yaml` |
| `UAV_ID` | manual, uavsdk mode only | Identifies the vehicle in SDK MQTT topics / RTSP URLs, default `uav-1` |
| `MAVLINK_UDP_PORT` | manual, pymavlink mode only | Host UDP port a remote QGroundControl connects to, default `14551` |
| `http_proxy` / `https_proxy` / `no_proxy` | commented out by default | Only uncomment if the host sits behind a corporate proxy — an empty active value silently breaks `make model` / `make vlm-model` |

Only generate variables the stack actually consumes; do not invent extra ones.

---

## Makefile Targets

Generate a `Makefile` exposing at least: `init`, `model`, `vlm-model` (if any
camera captions), `pymav-build`/`pymav-up`/`pymav-down` (pymavlink mode) or
`uavsdk-up`/`uavsdk-down` (uavsdk mode), `start-rtsp`, `check-cameras`,
`cameras-status`. Mirror the real target behaviour (see the shipped
`uav-vision-analytics/Makefile` for the exact recipes) rather than inventing
new semantics — in particular:

- `init` must create `.env` from `.env.example` and auto-detect `HOST_IP`,
  `GPU_DEVICE`/`GPU_RENDER_DEVICE`, `NPU_DEVICE`, `CAMERA_DEVICE_1`, never
  overwriting an existing `.env`.
- `check-cameras` / `cameras-status` must accept `CAMERAS=<file>` to point at
  a non-default camera file.
- `model` / `vlm-model` must not re-download if the target files already exist,
  and `vlm-model` must show the model's license and require confirmation
  (`ACCEPT_LICENSE=y` to skip) before downloading.

---

## Network Architecture

### pymavlink

```
PX4 SITL ──MAVLink──▶ mavlink-router (:14550 server → :14541 broadcast)
                                           │
                DL Streamer Pipeline Server ◀─UDP :14541─┘
                             │
                        ┌────┤
                        │    └──▶ RTSP :8555 → QGC / ffplay -rtsp_transport tcp rtsp://...
                        │
                    MQTT :1883 ──▶ Mosquitto broker
```

### UAVSDK

```
uav-mission-compute-sdk:
  PX4+Gazebo → companion-bridge → MQTT broker (:1883)
                               → RTSP server (:8554) [camera streams]

DL Streamer Pipeline Server container:
  MQTT subscriber → on ARMED → POST pipelines
  rtspsrc ← RTSP (:8554) [nadir/forward/rear]
  appsink → RTSP output :8555
```

---

## Device Group IDs

The `group_add` list must include the numeric GIDs for `/dev/dri` (GPU) and
`/dev/accel` (NPU). Check on the host:

```bash
stat -c %g /dev/dri/render*
stat -c %g /dev/accel/accel*
```

Update the `group_add` list in the compose file accordingly.

---

## Volumes

```yaml
volumes:
  dlstreamer-pipeline-server-pipeline-root:
    driver: local
    driver_opts:
      type: tmpfs
      device: tmpfs
```

The pipeline root is a tmpfs (in-memory) volume, reset on every container
recreation. Always use `docker compose up -d --force-recreate` (not `restart`)
when changing `config.json` — plain `restart` keeps the stale tmpfs.
