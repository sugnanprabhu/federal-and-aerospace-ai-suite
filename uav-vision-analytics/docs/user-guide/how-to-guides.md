# How to use UAV Vision Analytics

These guides cover what the application can do and how to operate it as
shipped: viewing streams, running the ground station, deployment modes,
testing, and troubleshooting. To change what the application does —
cameras, models, pipelines, overlay, search tuning — see
[How to Customize](./how-to-customize.md).

## Ground Station

- [QGroundControl](./how-to-guides/qgroundcontrol.md) — Configure QGroundControl
  to receive the UAV Vision Analytics video stream
- [Multi-UAV Operations in the Ground Station](./how-to-guides/multi-uav-ground-station.md) —
  Connect multiple UAVs, per-UAV tabs, and the per-host Flight Summary
- [Search Live Video with VSS](./how-to-guides/vss-live-search.md) —
  How live indexing and UAV/camera-scoped search work in the ground station

## Deployment and Operations

- [Switch Between Pymavlink and UAV Mission Compute SDK Deployment Modes](./how-to-guides/switch-deployment-mode.md) —
  Architecture differences and the `HOST_IP` binding requirement
- [Enable NPU Inference](./how-to-guides/enable-npu-inference.md) — NPU
  enablement end-to-end, including common failure modes
- [Test Against a Simulated, Continuously-Armed Mission](./how-to-guides/simulate-continuous-mission.md) —
  Keep PX4 SITL armed during interactive pipeline testing
- [Connect PX4 to AirSim (Unreal Engine)](./how-to-guides/connect-airsim.md) —
  Fly an AirSim drone instead of the built-in SIH physics; the simulator is chosen automatically at start
- [View and Interpret Detection Logs (JSONL)](./how-to-guides/view-detection-logs.md) —
  Tail, filter, and correlate the per-pipeline detection metadata

## Reference

- [Makefile Reference](./how-to-guides/makefile.md) — Shorthand targets for
  model setup, stack management, and pipeline control
- [Troubleshooting](./how-to-guides/troubleshooting.md) — Common issues and
  fixes for deployment, inference, and streaming problems

<!--hide_directive
:::{toctree}
:hidden:

QGroundControl <./how-to-guides/qgroundcontrol.md>
Multi-UAV Operations in the Ground Station <./how-to-guides/multi-uav-ground-station.md>
Search Live Video with VSS <./how-to-guides/vss-live-search.md>
Switch Deployment Modes <./how-to-guides/switch-deployment-mode.md>
Enable NPU Inference <./how-to-guides/enable-npu-inference.md>
Simulate a Continuous Mission <./how-to-guides/simulate-continuous-mission.md>
Connect PX4 to AirSim <./how-to-guides/connect-airsim.md>
View Detection Logs <./how-to-guides/view-detection-logs.md>
Makefile Reference <./how-to-guides/makefile.md>
Troubleshooting <./how-to-guides/troubleshooting.md>
:::
hide_directive-->
