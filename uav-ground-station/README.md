<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# UAV Ground Station

A minimal, single-container remote viewer for the
[`uav-vision-analytics`](../uav-vision-analytics) stack. Run it on a
**separate machine** — e.g. a ground-station laptop — from the one running
`docker-compose-pymavlink.yml`/`docker-compose-uavsdk.yml`. It never talks to
that host until you enter its IP in the dashboard and press **Connect**.

> [!IMPORTANT]
> This is a *viewer*, not the UAV stack itself. Deploy
> [`uav-vision-analytics`](../uav-vision-analytics#readme) first (standalone/
> pymavlink mode), or [`uav-mission-compute-sdk`](../uav-mission-compute-sdk#readme)
> **plus** `uav-vision-analytics` in SDK mode -- the viewer talks to
> `uav-vision-analytics`'s `/pipelines/status` API and RTSP streams either way,
> the Mission Compute SDK alone has no camera pipelines for it to see. Get at
> least one camera pipeline running, then come back here. See
> [Get Started](../uav-vision-analytics/docs/user-guide/uav-ground-station/get-started.md)
> for the full walkthrough of both sides.


Documentation for UAV Ground Station is maintained under the UAV Blueprint, for publishing
reasons. You can find the source in the
[user guide](../uav-vision-analytics/docs/user-guide/uav-ground-station/index.md)
and the html version on
[Open Edge Platform Docs](https://docs.openedgeplatform.intel.com/dev/edge-ai-suites/ai-suite-federal-and-aerospace/uav-vision-analytics/index.html).

## Quick Start

Prerequisites: Docker with the Compose plugin and `make` on this machine.

```bash
git clone https://github.com/open-edge-platform/federal-and-aerospace-ai-suite.git --branch main
cd federal-and-aerospace-ai-suite/uav-ground-station
make up      # creates .env from template (host IP and devices auto-detected), builds, and starts the container
```

Then open `http://<VIZ_HOST_IP>:8080` (`http://localhost:8080` when
`VIZ_HOST_IP` is `127.0.0.1`), enter the UAV host's IP, and press **Connect**.

```bash
make down    # stop and remove the stack started with make up
make logs    # tail the container's logs
```

Want **Live Natural Language Search** too? Run `make up-vss DEVICE=CPU|GPU|NPU|all`
instead of `make up` -- see [Get Started -- VSS search](docs/user-guide/get-started.md#vss-search)
for the extra setup it needs and `make down-vss`/`make clean-vss-index` for
tearing it down.

See **[Get Started](docs/user-guide/get-started.md)** for: downloading the source two
ways, the UAV-side prerequisite in detail, the VSS search overlay walkthrough,
multiple UAVs in one session, and the Flight Summary report. See
**[Configuration](docs/user-guide/configuration.md)** for every `.env` variable and the
full backend route reference, and **[Release Notes](docs/user-guide/release-notes.md)** for
what each version adds and its known issues.

## What it shows

![UAV Ground Station dashboard with live video, scene captions, and utilization](docs/user-guide/_assets/dashboard-live.gif)

- The live video stream, via **H.264 Relay**: the local uav-gs-backend
  configures a mediamtx sidecar (`docker-compose-mediamtx.yml`, always
  brought up alongside the base stack -- see `make up`; `configs/mediamtx.yml` keeps on-demand sources alive for 60 s) to pull the remote
  UAV host's already-H.264 RTSP stream on demand and re-serve it to the
  browser as WebRTC (WHEP) -- pure remux, no transcoding. Requires the
  remote pipeline's RTSP output to be H.264, as uav-vision-analytics publishes;
  if the remote stream is not H.264, the dashboard shows a placeholder message ("No video
  stream — start this camera's pipeline on the remote host.").
  Each card's expand button (or the player's own full-screen control)
  shows that feed full screen; Esc or "Exit full screen" returns. If a feed
  drops partway through viewing -- for example the remote RTSP publisher
  restarts -- the card notices and recovers on its own within a few
  seconds, without needing a manual page refresh.
- Scene captions (`uav/caption/<camera>` over MQTT, bridged to the browser via
  SSE), shown under the video of cameras whose pipeline has captioning on.
- One card per camera (a running pipeline with an RTSP stream, shown as `uav-<name>`),
  with the devices of its pipeline as badges (detection device, VLM device, `LIVE` for
  a live camera such as a RealSense, overlay off), and the models, prompt and video or
  camera device in the top-right information icon's tooltip, along with the
  pipeline state and name (from the DL Streamer
  Pipeline Server's REST API and the start request of each pipeline).
- CPU/GPU/NPU utilization of the UAV host (proxied from its `metrics-manager`) and of this ground-station machine (the local `uav-gs-metrics-manager` container).
- **Live Natural Language Search**: the **Search Videos** button (top bar, or press `/`) opens a popup where you type a natural-language prompt
  (e.g. "a red car on the road") and get a ranked result list with thumbnails
  next to a clip player (arrow keys move between results, `Esc` closes), from a
  separately deployed
  [Video Search and Summarization (VSS)](https://github.com/open-edge-platform/edge-ai-libraries/tree/release-2026.3.0/sample-applications/video-search-and-summarization)
  search stack -- see [Get Started -- VSS search](docs/user-guide/get-started.md#vss-search).
- An interactive **Architecture** popup: the services on the UAV host, the ground station and the optional VSS stack, color-coded by origin (Open Edge Platform, open source, application). Pick a flow such as live video, captions, live indexing or search to highlight its path, click a service for its role, image and APIs, and see which models run on the NPU, GPU and CPU.
- A **Flight Summary** report (top bar's **Flight Summary** button): pick one or more cameras that are currently captioning and a time window, and get an extractive summary -- an overview paragraph, recurring subjects, and a timeline of de-duplicated scene highlights grouped camera-by-camera (not interleaved by arrival time, so each camera's own story reads start-to-finish) -- built entirely from the MQTT caption stream already flowing for captioning-enabled pipelines. No VSS deployment or LLM call required; see [Get Started -- Flight Summary](docs/user-guide/get-started.md#flight-summary).

![Architecture popup following the live video, scene caption, and utilization flows](docs/user-guide/_assets/architecture-flows.gif)
## Project Structure

```text
uav-ground-station/
├── docker-compose.yml   # viz-backend (FastAPI UI + proxy/bridge APIs)
├── docker-compose-mediamtx.yml  # mediamtx sidecar for H.264 Relay video (always used, see `make up`)
├── configs/mediamtx.yml  # mediamtx settings (WebRTC only, 60 s on-demand timeouts)
├── docker-compose-vss.yml  # optional VSS search containers (make up-vss)
├── docker-compose-nginx.yml  # nginx HTTPS reverse proxy, added by make up-vss
├── configs/nginx/nginx.conf  # nginx proxy config (TLS certs are generated into configs/nginx/ssl/)
├── .env.example         # Copy to .env; all vars have loopback-safe defaults
├── docs/user-guide/get-started.md   # Full deployment walkthrough (both sides, VSS, multi-UAV, Flight Summary)
├── docs/user-guide/configuration.md # Every .env variable and the backend route reference
├── docs/user-guide/release-notes.md # What each version adds, with known issues
└── backend/             # FastAPI app: static UI + proxy/bridge APIs
    ├── main.py
    ├── vss_search.py    # /api/search* proxy to the separate VSS stack
    ├── vss_live_sync.py # keeps VSS live-stream indexing in step with the running pipelines
    ├── uav_naming.py    # host-qualified stream names and the host/camera registry (multi-UAV)
    ├── caption_log.py   # MQTT caption ring buffer, feeds vss_summary.py
    ├── vss_summary.py   # /api/summary* -- Flight Summary report (no VSS needed)
    ├── pdf_writer.py    # dependency-free PDF renderer used by vss_summary.py
    ├── requirements.txt
    ├── Dockerfile
    └── static/           # index.html, css/, js/ (app.js, search.js, summary.js, arch.js), img/
```

### HTTPS with nginx (`make up-vss`)

`make up-vss` also starts an nginx reverse proxy (`docker-compose-nginx.yml`) that terminates TLS and serves the dashboard, `/api/*` and the video viewer pages from one origin at `https://<VIZ_HOST_IP>:8443` (change with `VIZ_NGINX_HTTPS_PORT` in `.env`). A self-signed certificate is generated into `configs/nginx/ssl/` on first use (replace `server.crt`/`server.key` with your own for a trusted one). `viz-backend` and the mediamtx WHEP port are published on loopback only; the WebRTC media ports (8189) are unchanged. `make up` (without search) stays plain HTTP on `VIZ_PORT`.

Container names use the prefix `uav-gs-` (ground station): `uav-gs-backend`, `uav-gs-mediamtx` and `uav-gs-metrics-manager`. The compose service names (`viz-backend`, `mediamtx`, `gs-metrics-manager`) are unchanged.

## Notes

- The UAV host's TLS certificate is self-signed, so the backend does not
  verify it by default (trusted-LAN tool posture, consistent with the
  `-k`/`--insecure` curl examples in `uav-vision-analytics`'s own README) and
  logs a warning at startup. To verify it, set `VIZ_REMOTE_TLS_VERIFY=true`,
  copy the UAV's `uav-vision-analytics/configs/nginx/ssl/server.crt`
  (concatenate several for several UAVs) into `configs/certs/` and set
  `VIZ_REMOTE_TLS_CA_BUNDLE=/certs/<file>.pem` in `.env`. The certificate must
  list the address you connect to in its subject alternative names; `make pymav-up`
  / `make uavsdk-up` in `uav-vision-analytics` generate it (when missing) with
  `localhost`, `127.0.0.1` and the host's `HOST_IP`; delete
  `configs/nginx/ssl/server.*` there to regenerate an old one.
- This project makes outbound, read-only requests to whatever host you enter;
  it never starts/stops pipelines on the remote stack.

## Notice for FFmpeg

FFmpeg is an open source project licensed under LGPL and GPL. See
<https://www.ffmpeg.org/legal.html>. You are solely responsible for determining
if your use of FFmpeg requires any additional licenses. Intel is not
responsible for obtaining any such licenses, nor liable for any licensing fees
due, in connection with your use of FFmpeg.
