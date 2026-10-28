#!/usr/bin/env bash
# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Backend tests against a UAV host whose cameras are running. Starts a throwaway backend container from the working
# tree (image uav-ground-station-backend:local, built by `make up`) on a free local port, so the running dashboard is
# not touched, runs tests/test_backend_live.py against it and removes the container.
#
#   tests/run_backend_tests.sh <UAV_HOST>
#
# The relay and WebRTC checks need the mediamtx sidecar of `make up` to be running (they skip otherwise); the WebRTC
# test also needs `pip install aiortc` on this machine.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
UAV_HOST="${1:?usage: $0 <UAV_HOST>}"
IMAGE="${BACKEND_IMAGE:-uav-ground-station-backend:local}"
NAME="viz-backend-test-$$"
NETWORK="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' viz-backend 2>/dev/null || true)"
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT
docker image inspect "${IMAGE}" >/dev/null 2>&1 || { echo "image ${IMAGE} not found; run 'make up' first" >&2; exit 1; }
docker run -d --name "${NAME}" ${NETWORK:+--network "${NETWORK}"} -p 127.0.0.1::8080 \
  -e MEDIAMTX_API_BASE="${MEDIAMTX_API_BASE:-http://mediamtx:9997}" -e DEFAULT_REMOTE_HOST="${UAV_HOST}" \
  -v "${ROOT}/backend:/app:ro" "${IMAGE}" >/dev/null
PORT="$(docker port "${NAME}" 8080/tcp | head -1 | sed 's/.*://')"
for _ in $(seq 1 30); do curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:${PORT}/api/health" && break; sleep 1; done
BACKEND_URL="http://127.0.0.1:${PORT}" UAV_HOST="${UAV_HOST}" python3 "${HERE}/test_backend_live.py" -v
