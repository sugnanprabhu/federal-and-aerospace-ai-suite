#!/usr/bin/env bash
# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Unit tests for scripts/camera_config.py. Runs inside the stack's own image (it has PyYAML, pydantic and
# jsonschema), so only Docker and the built image are needed; the running stack is not touched.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
if [[ -z "${PYMAVLINK_IMAGE:-}" ]]; then
  PYMAVLINK_IMAGE="$(grep -E '^DLSTREAMER_PIPELINE_SERVER_IMAGE=' "${REPO_ROOT}/.env" | cut -d= -f2-)-pymavlink"
fi
docker run --rm -e REPO_DIR=/repo -v "${REPO_ROOT}/scripts:/repo/scripts:ro" -v "${REPO_ROOT}/configs:/repo/configs:ro" \
  -v "${HERE}:/work:ro" --entrypoint python3 "${PYMAVLINK_IMAGE}" -B -m unittest discover -s /work -p "test_*.py"
