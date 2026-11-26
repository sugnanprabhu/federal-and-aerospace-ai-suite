# PX4 SITL in Docker: standalone or with Unreal / Cosys-AirSim

Hand-off guide for configuring the `px4io/px4-sitl` image on a new machine. It is self-contained.
Everything below was verified on Windows 11 + WSL2 (Ubuntu 24.04, Docker 29) with UE 5.8, Cosys-AirSim and PX4 v1.18-beta.
Not verified: QGroundControl itself (only a raw MAVLink client), a full mission under Docker PX4 (see "Known issue"), HITL.

## 1. Concepts

| Mode | Airframe | Physics from | Needs |
| --- | --- | --- | --- |
| Standalone | `10040_sihsim_quadx` | PX4's built-in SIH | Nothing but Docker |
| AirSim | `10016_none_iris` | AirSim in Unreal | The game running first |

- The image contains both airframes. `PX4_SIM_MODEL` selects one.
- In AirSim mode **PX4 is the TCP client**. AirSim listens on TCP `4560` of the address set as `LocalHostIp` in its
  `settings.json` (not `0.0.0.0`). PX4 gets that address from `PX4_SIM_HOST_ADDR`.
- Ready marker in the PX4 log: `Ready for takeoff!`. In the game log: `Connected to SITL over TCP.`

## 2. Rules that cost time to learn

1. **Start the game first, then PX4.** Restart PX4 every time the game restarts. A PX4 started earlier holds a dead socket
   and the drone only hovers.
2. **Never probe TCP 4560.** AirSim accepts exactly one connection; a probe consumes it and PX4 shows
   `Simulator connected` but never gets sensor data. To detect AirSim, probe the RPC port `41451` (opens about 4 s after 4560).
3. `PX4_SIM_HOST_ADDR` must be AirSim's `LocalHostIp`. If it is empty or `host.docker.internal`, that is the Docker host
   (the WSL VM on Windows), not Windows, so detection fails and the auto mode silently falls back to SIH.
4. Check for stale game processes before every test (`Get-Process MilitaryBase`, or the game's name;
   `Get-NetTCPConnection -LocalPort 4560,41451`). A stale instance holds the ports and corrupts results.
5. Only one `mavlink-router` can own UDP 14550/14551 per host. Do not run two stacks that publish them.
6. WSL needs the proxy to build images (`http_proxy=https_proxy=http://<proxy>`); inside the container no proxy is needed.

## 3. Option A: minimal compose (AirSim only, host network)

`docker-compose.yml`:

```yaml
services:
  px4:
    image: px4io/px4-sitl:${PX4_TAG:-latest}
    container_name: px4
    network_mode: host
    restart: "no"
    stdin_open: true
    environment:
      PX4_SIM_MODEL: none_iris
      PX4_SIM_HOST_ADDR: ${PX4_SIM_HOST_ADDR:-10.223.23.15}   # AirSim LocalHostIp
```

Run (inside WSL / Linux): `docker compose up -d`, then after every game launch `docker compose restart px4`,
`docker compose logs -f px4` until `Ready for takeoff!`. Standalone instead: set `PX4_SIM_MODEL: sihsim_quadx`
and drop `PX4_SIM_HOST_ADDR`.
With host networking PX4's offboard API is on `127.0.0.1:14540` and its GCS link on `127.0.0.1:14550`.

## 4. Option B: auto-select (one container, works with or without the game)

Add this entrypoint script as `px4-select-sim.sh` (LF line endings!):

```bash
#!/bin/bash
# Chooses the PX4 simulator at container start, then hands over to the image's entrypoint.
host="${PX4_SIM_HOST_ADDR:-host.docker.internal}"
api_port="${AIRSIM_API_PORT:-41451}"
mode="${PX4_SIM_MODEL:-auto}"

if [ "$mode" = "auto" ]; then
	if timeout 3 bash -c "exec 3<>/dev/tcp/${host}/${api_port}" 2>/dev/null; then
		mode=none_iris
		echo "[select-sim] AirSim reachable at ${host}:${api_port} -> none_iris"
	else
		mode=sihsim_quadx
		echo "[select-sim] AirSim not reachable at ${host}:${api_port} -> sihsim_quadx (standalone)"
	fi
fi

export PX4_SIM_MODEL="$mode"
[ "$mode" = "none_iris" ] && export PX4_SIM_HOSTNAME="$host"
exec /opt/px4/bin/px4-entrypoint.sh "$@"
```

Compose additions to the `px4` service:

```yaml
    entrypoint: ["bash", "/opt/px4/bin/px4-select-sim.sh"]
    environment:
      - PX4_SIM_MODEL=${PX4_SIM_MODEL:-auto}              # auto | none_iris | sihsim_quadx
      - PX4_SIM_HOST_ADDR=${PX4_SIM_HOST_ADDR:-host.docker.internal}
      - AIRSIM_API_PORT=${AIRSIM_API_PORT:-41451}
    volumes:
      - ./px4-select-sim.sh:/opt/px4/bin/px4-select-sim.sh:ro
```

Notes: the image has bash, timeout, getent, awk and perl, but **no nc or python3**, hence the `/dev/tcp` probe.
`PX4_SIM_HOSTNAME` takes priority over `PX4_SIM_HOST_ADDR` in PX4's rc. The choice is made once at start, so restart the
container after (re)starting the game. The reference implementation (with docs) is the `uav-vision-analytics` project,
branch `feat/px4-airsim-auto-sim`, file `docs/user-guide/how-to-guides/connect-airsim.md`.

If PX4 sits on a Docker bridge network next to a `mavlink-router` service (that project's layout), each airframe also needs a
post file that links PX4 to the router, mounted into `/opt/px4/etc/init.d-posix/airframes/`
(`10040_sihsim_quadx.post` and `10016_none_iris.post`, identical):

```text
mavlink start -u 14541 -t $(getent hosts mavlink-router | awk '{print $1}')
```

Host-network setups (Option A) do not need these.

## 5. AirSim / Unreal side (no project changes needed)

`Documents\AirSim\settings.json` (Windows): a PX4 vehicle with TCP, `LocalHostIp` = this machine's address reachable from
WSL/Docker, `TcpPort` 4560, `ControlPortLocal` 14540, `ControlPortRemote` 14580, `UseSerial` false, `LockStep` true.
Keep the default `Parameters` block out if the newer PX4 image lacks a param (AirSim aborts in `sendParams`).
Windows Firewall needs an **inbound Allow** on TCP for the game executable (Public profile if that is the active one).
Do not use SSH tunnels for normal runs: the extra hop jitters lockstep and the EKF never converges
(`High Accelerometer Bias`, arming denied).

## 6. QGroundControl via mavlink-router

Direct PX4 -> Windows UDP pushes were blocked by inbound filtering on the WSL interface, so QGC connects **out** to a router.

```yaml
  mavlink-router:
    build:
      context: ./px4/mavlink-router
      args: { http_proxy: "${http_proxy:-}", https_proxy: "${https_proxy:-}" }
    image: mavlink-router:local
    container_name: mavlink-router
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./px4/mavlink-router/main.conf:/etc/mavlink-router/main.conf:ro
```

`px4/mavlink-router/main.conf`:

```ini
[General]
TcpServerPort=0
[UdpEndpoint px4]
Mode=Server
Address=0.0.0.0
Port=14550
[UdpEndpoint qgc]
Mode=Server
Address=0.0.0.0
Port=14551
```

`px4/mavlink-router/Dockerfile`: `FROM ubuntu:24.04`, apt-install `git ca-certificates build-essential pkg-config libssl-dev meson ninja-build python3-pip`,
`git clone --depth 1 https://github.com/mavlink-router/mavlink-router.git /src`, `git submodule update --init --recursive`,
`meson setup build . -Dsystemdsystemunitdir=/usr/lib/systemd/system && ninja -C build && ninja -C build install`,
copy `main.conf` to `/etc/mavlink-router/main.conf`, `CMD ["mavlink-routerd","-c","/etc/mavlink-router/main.conf"]`.
The copy in this repo is `px4/mavlink-router/`.

QGC: Application Settings -> Comm Links -> Add -> Type UDP -> Server Addresses -> Add `<WSL-IP>:14551` (`hostname -I` in WSL;
it can change). `127.0.0.1:14551` from Windows does **not** work with WSL NAT unless localhost forwarding is set up for UDP.
A heartbeat from a raw client to `<WSL-IP>:14551` returned live telemetry; QGC itself is untested.

## 7. Mission script

`circle_mission.py` (MAVSDK, offboard) connects to UDP `14540`. With host networking PX4 publishes on `127.0.0.1:14540`; run
it from WSL (`udpin://0.0.0.0:14540`). Use `mavsdk` < 4 (v4+ has a different API). A leftover `mavsdk_server` on 14540 makes
the next mission attach to a dead session (`COMMAND_DENIED`, `bind error: Address in use`); kill it first.
Order: game, PX4, mission. `run_sim.ps1 -Packaged -Docker` automates it on the reference machine.

## 8. Verify

```bash
docker logs px4 2>&1 | grep -E "select-sim|SYS_AUTOSTART|Simulator connected|Ready for takeoff"
```

AirSim mode shows `SYS_AUTOSTART=10016`, `Simulator connected on TCP port 4560`, `Ready for takeoff!` (and
`Connected to SITL over TCP.` in the game log). Standalone shows `SYS_AUTOSTART=10040`. A transient
`Preflight Fail: system power unavailable` / `no heading reference` before ready is normal.

## 9. Known issue

With Docker PX4 and the packaged game, two mission runs stalled at "Taking off" with a persistent
`Preflight Fail: system power unavailable` (the same happened on the source-built PX4). The connection tests (no mission) reached
`Ready for takeoff!` fine. Ideas not yet tried: pin an older `PX4_TAG`; set the supply-check circuit breaker (`CBRK_SUPPLY_CHK`)
via the mission script; wait longer for the EKF.

## 10. HITL (different thing)

Neither the Docker image nor a source SITL build does HITL. It needs a real Pixhawk with stock PX4, the HIL quadcopter airframe
(`SYS_AUTOSTART=1001`) and AirSim serial settings (`UseSerial`, `SerialPort`, `LockStep`; check the Cosys-AirSim docs). Props off, USB power only.
