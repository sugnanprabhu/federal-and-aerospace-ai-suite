#!/bin/bash
# Chooses the PX4 simulator at container start, then hands over to the image's entrypoint.
#   PX4_SIM_MODEL=auto (default): none_iris (connect to AirSim) if AirSim is reachable,
#                                 otherwise sihsim_quadx (built-in physics, standalone).
#   PX4_SIM_MODEL=none_iris | sihsim_quadx | ...: used as given, no probing.
# AirSim is detected through its RPC API port (default 41451). Do NOT probe TCP 4560: AirSim
# accepts a single connection there, and a probe would consume it and leave PX4 on a dead socket.
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
