#!/bin/bash

# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0


cd /px4 || exit 1

# Remove persisted params so defaults take effect on every boot
rm -f build/px4_sitl_default/rootfs/parameters.bson
rm -f build/px4_sitl_default/rootfs/parameters_backup.bson

# Purge old flight logs to prevent disk fill
rm -rf build/px4_sitl_default/rootfs/log/*
rm -rf build/px4_sitl_default/tmp/rootfs/log/*

echo "=== PX4 SITL + Gazebo Harmonic (Multi-Camera Perimeter Security) ==="

# Reuse the mono_cam_down airframe — multi_cam model.sdf is mounted over
# the mono_cam path at runtime via docker-compose volume mounts.
export PX4_GZ_WORLD="${PX4_GZ_WORLD:-baylands_multicam}"
export HEADLESS=1

if [ "$PX4_GZ_WORLD" = "citysim_multicam" ]; then
    # Download the world's Fuel models up front, then convert Ogre1 material scripts (not supported
    # by Gazebo's ogre2 renderer, so buildings render black) into PBR albedo textures.
    world_sdf="/px4/Tools/simulation/gz/worlds/${PX4_GZ_WORLD}.sdf"
    grep -o 'https://fuel.gazebosim.org/[^<]*' "$world_sdf" | sort -u | while read -r uri; do
        gz fuel download -u "$uri" >/dev/null 2>&1 || echo "WARNING: could not download $uri"
    done
    python3 - <<'PY'
import glob
import os
import re

root = os.path.expanduser("~/.gz/fuel/fuel.gazebosim.org/openrobotics/models")
for sdf in glob.glob(os.path.join(root, "*", "*", "model.sdf")):
    base = os.path.dirname(sdf)
    textures = [f for f in glob.glob(os.path.join(base, "materials", "textures", "*"))
                if "diffuse" in os.path.basename(f).lower()]
    text = open(sdf, encoding="utf-8").read()
    pattern = re.compile(r"<material>(?:(?!</material>).)*?<script>.*?</material>", re.S)
    if not textures or not pattern.search(text):
        continue
    pbr = ("<material><diffuse>1 1 1 1</diffuse><pbr><metal><albedo_map>%s</albedo_map>"
           "</metal></pbr></material>" % sorted(textures)[0])
    open(sdf, "w", encoding="utf-8").write(pattern.sub(lambda _: pbr, text))
PY
fi

exec make px4_sitl gz_x500_mono_cam_down 2>&1 | grep --line-buffered -v "pxh>"
