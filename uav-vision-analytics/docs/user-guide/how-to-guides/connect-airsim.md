<!--
SPDX-FileCopyrightText: (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->
# Connect PX4 to AirSim (Unreal Engine)

In the standalone (pymavlink) deployment the `px4` container can fly either of two simulators:

| Mode | PX4 airframe | Physics / sensors from | Use it for |
| --- | --- | --- | --- |
| `sihsim_quadx` | `10040_sihsim_quadx` | PX4's built-in SIH physics | Standalone use with no external simulator (the default when AirSim is not running) |
| `none_iris` | `10016_none_iris` | [AirSim](https://github.com/Cosys-Lab/Cosys-AirSim) running in Unreal Engine | Flying a drone in an Unreal world while the application analyses its camera streams |

The `px4` container chooses between them **once, when it starts**, so no compose edit is needed to switch.

## How PX4 and AirSim connect

PX4 `none_iris` is the TCP **client**; AirSim listens on TCP port `4560` of the address configured as
`LocalHostIp` in its `settings.json`. PX4 dials that address and retries until AirSim accepts, then
exchanges simulated sensor data and actuator commands in lockstep. MAVLink telemetry still flows to
`mavlink-router` exactly as in the SIH mode, so the pipeline server and QGroundControl work unchanged.

`px4-select-sim.sh` is the container entrypoint. With `PX4_SIM_MODEL=auto` it checks whether AirSim's RPC
API port (default `41451`) accepts a connection at `PX4_SIM_HOST_ADDR`:

- reachable → `none_iris`, connected to AirSim;
- not reachable → `sihsim_quadx`, standalone.

> [!IMPORTANT]
> AirSim accepts only **one** connection on port `4560`. The script therefore probes the RPC port and
> never `4560`; a connection to `4560` that is not PX4 would use up the accept and leave PX4 on a dead
> socket (PX4 logs `Simulator connected` but never receives sensor data and never becomes ready).

## Configure

Set these in `.env` (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `PX4_SIM_MODEL` | `auto` | `auto`, or force `none_iris` / `sihsim_quadx` with no probing |
| `PX4_SIM_HOST_ADDR` | empty (`host.docker.internal`) | Address of the machine running AirSim: the `LocalHostIp` value from AirSim's `settings.json`. AirSim listens only on that address, so it cannot be guessed. `host.docker.internal` is the Docker host itself, which is only right when AirSim runs on that same machine |
| `AIRSIM_API_PORT` | `41451` | AirSim RPC port used for detection |

Example for AirSim on a Windows host with PX4 in Docker on WSL2:

```text
PX4_SIM_HOST_ADDR=10.223.23.15      # "LocalHostIp" in Documents\AirSim\settings.json
```

The AirSim side needs no changes beyond the usual PX4 vehicle settings (`"VehicleType": "PX4Multirotor"`,
`"UseTcp": true`, `"TcpPort": 4560`, `"LocalHostIp"` set to the address above). Windows Firewall must allow
inbound TCP from the Docker host to the game executable.

## Run

1. Start the Unreal game (or packaged build) and wait until AirSim is listening.
2. Start or restart PX4 so it makes its choice **after** the game is up:

   ```bash
   make pymav-up                 # first start
   docker restart uav-va-px4     # any later game launch
   ```

3. Check the choice and the link:

   ```bash
   docker logs uav-va-px4 2>&1 | grep -E "select-sim|SYS_AUTOSTART|Simulator connected|Ready for takeoff"
   ```

   Expected with AirSim running:

   ```text
   [select-sim] AirSim reachable at 10.223.23.15:41451 -> none_iris
   INFO  [init] found model autostart file as SYS_AUTOSTART=10016
   INFO  [simulator_mavlink] Simulator connected on TCP port 4560.
   INFO  [commander] Ready for takeoff!
   ```

   You may see `Preflight Fail: system power unavailable` or `no heading reference` for a few
   seconds first; they clear once the estimator converges.

Always start the game **before** PX4 and restart PX4 whenever the game restarts. A PX4 that was started
earlier can hold a dead socket and the vehicle will only hover.

## Connect QGroundControl

`mavlink-router` publishes UDP `14551` on `HOST_IP`. In QGroundControl add a UDP link with server address
`<HOST_IP>:14551` (see the [QGroundControl guide](./qgroundcontrol.md#connect-qgroundcontrol-from-another-machine)).
The same link works in both simulator modes. On WSL2 use the WSL address as `HOST_IP`; `127.0.0.1` on
Windows does not reach UDP ports inside WSL.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Log says `AirSim not reachable ... -> sihsim_quadx` with the game running | `PX4_SIM_HOST_ADDR` is empty or wrong. Set it to AirSim's `LocalHostIp`, then `docker restart uav-va-px4`. Confirm the RPC port is reachable from the Docker host: `timeout 3 bash -c 'exec 3<>/dev/tcp/<addr>/41451' && echo open` |
| `Waiting for simulator to accept connection on TCP port 4560` forever | AirSim is not listening on that address or a firewall blocks it. Check `LocalHostIp` and the inbound rule for the game executable |
| `Simulator connected` but never `Ready for takeoff`, or the drone only hovers | PX4 connected to a stale or consumed socket. Restart the game, then restart PX4 |
| Standalone start with no game takes the SIH path | Expected. Start the game first and restart `px4` to switch to AirSim |
