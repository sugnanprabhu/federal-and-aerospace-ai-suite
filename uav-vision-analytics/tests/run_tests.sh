#!/usr/bin/env bash
# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Runs the tests of uav-vision-analytics.
#
#   tests/run_tests.sh                 offline unit tests (camera configuration), no stack needed
#   tests/run_tests.sh --live          also the live tests against the running stack (make pymav-up):
#                                      the nine pipelines, the cameras, shutdown, settings (about 25 minutes)
#   tests/run_tests.sh --live --arm    also the arm/disarm cycle (ARMS THE SIMULATED VEHICLE; only when no
#                                      ground station is connected)
#
# The live tests start and stop pipelines. They refuse to run while pipelines are running unless
# UAV_TEST_FORCE=1 is set. See tests/README.md.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE=false; ARM=false
for arg in "$@"; do
  case "${arg}" in
    --live) LIVE=true ;;
    --arm)  ARM=true ;;
    -h|--help) sed -n '5,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done
status=0
echo "== camera configuration (unit tests)"
"${HERE}/camera_config/run_tests.sh" || status=1
if ${LIVE}; then
  echo "== live: pipelines, cameras, settings"
  (cd "${HERE}/live" && python3 -m unittest -v test_cameras test_pipelines) || status=1
  if ${ARM}; then
    echo "== live: arm/disarm cycle"
    (cd "${HERE}/live" && UAV_TEST_ALLOW_ARM=1 python3 -m unittest -v test_arm_cycle) || status=1
  fi
fi
exit ${status}
