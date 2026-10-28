<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Tests

| File | What it tests | How to run |
| --- | --- | --- |
| `dashboard_cards.test.js` | The real dashboard (`index.html` + `app.js`) in a simulated browser (jsdom) against a scripted backend: one card per camera, device badges, caption panel and subscription per camera topic, relay per stream path, cameras appearing, disappearing and changing their pipeline, a host that becomes unreachable | `cd tests && npm install && npm test` |
| `test_camera_record.py` | What the dashboard learns from a pipeline's start request, for video and live (RealSense, USB) cameras; runs in the backend image, no UAV host needed (command in the file) | see the file |
| `test_backend_live.py` | The backend against a UAV host whose cameras are running: discovery and details, pipeline info, rejection of bad input, the captions stream of a camera's topic, the relay path, and (with `aiortc`) real WebRTC playback of every camera through the relay | `tests/run_backend_tests.sh <UAV_HOST>` |

Start the cameras on the UAV host first (`make start-rtsp` and arm, or `python3 pipeline_manager.py --start-now` in the
pipeline-server container). `run_backend_tests.sh` uses a throwaway backend container from the working tree on a free local port;
the running dashboard is untouched. The relay it configures (`relay-<host>-<camera>` paths in the local mediamtx) is harmless
and replaced the next time the dashboard connects.
