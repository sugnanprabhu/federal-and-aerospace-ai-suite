<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Tests

| Folder | What it tests | Needs |
| --- | --- | --- |
| `camera_config/` | `scripts/camera_config.py`: schema, validation, pipeline choice, the start requests, and that every request satisfies the parameter schema of the pipelines in `configs/config-pymavlink.json` | Docker and the built image only |
| `live/` | The running stack: the nine video pipelines, the cameras of `configs/cameras-pymavlink.yaml`, shutdown, settings, bad files, the arm cycle | `make pymav-up`, an idle stack |

```bash
tests/run_tests.sh                  # offline unit tests
tests/run_tests.sh --live           # + live tests (about 25 minutes)
tests/run_tests.sh --live --arm     # + arm/disarm cycle (arms the SIMULATED vehicle)
```

Run one live module or test directly: `cd tests/live && python3 -m unittest -v test_cameras.Settings`.

## Live tests

* They use only the Python standard library on the host; `ffmpeg`, `mosquitto_sub` and `pymavlink` are used inside the
  stack's containers (`docker exec`), so nothing else has to be installed. `/tmp` is shared with the pipeline-server
  container.
* They start and stop pipelines and refuse to run while pipelines are running. `UAV_TEST_FORCE=1` lets them stop whatever
  is running first.
* "Streaming" always means counting real frames from the RTSP stream (a pipeline can report `RUNNING` without video).
* `test_arm_cycle.py` arms and disarms the simulated vehicle, raises PX4's auto-disarm timer and restores it. It only runs
  with `UAV_TEST_ALLOW_ARM=1` (set by `--arm`) and skips if the vehicle is not disarmed. Do not run it while
  QGroundControl is flying the vehicle.

## Writing a camera test

`common.Manager(yaml_text).start()` runs the manager on a temporary `cameras-pymavlink.yaml`; `wait_for("is RUNNING")`, `status()`,
`write()` (edit the file) and `cleanup()` cover the usual steps; `common.frames(path)`, `common.caption(topic)` and
`common.pipeline_request(id)` check the result.
