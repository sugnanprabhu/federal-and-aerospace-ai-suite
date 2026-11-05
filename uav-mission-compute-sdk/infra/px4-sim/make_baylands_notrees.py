#!/usr/bin/env python3
# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Build a tree-free copy of the Fuel 'baylands' model.

baylands_01/02.DAE contain only foliage; baylands_03.DAE contains the terrain
plus the 'Leaves' and 'Bark' tree geometries. The output model drops the first
two meshes and strips the tree geometries from the third.

Usage: make_baylands_notrees.py <fuel baylands version dir> <output model dir>
"""
import re
import shutil
import sys
from pathlib import Path

TREE_GEOMETRIES = ("Leaves", "Bark")


def strip_geometry(text: str, name: str) -> str:
    text, n_geo = re.subn(
        rf'\s*<geometry id="{name}-lib".*?</geometry>', "", text, flags=re.S
    )
    text, n_node = re.subn(
        rf'\s*<node name="{name}" id="{name}".*?</node>', "", text, flags=re.S
    )
    if n_geo != 1 or n_node != 1:
        raise SystemExit(f"Unexpected mesh layout for '{name}' ({n_geo} geometry, {n_node} node)")
    return text


def main(src: Path, dst: Path) -> None:
    media_src, media_dst = src / "media", dst / "media"
    media_dst.mkdir(parents=True, exist_ok=True)
    for sub in ("Textures", "scripts"):
        if (media_src / sub).is_dir():
            shutil.copytree(media_src / sub, media_dst / sub, dirs_exist_ok=True)

    text = (media_src / "baylands_03.DAE").read_text(encoding="utf-8", errors="surrogateescape")
    for name in TREE_GEOMETRIES:
        text = strip_geometry(text, name)
    (media_dst / "baylands_03.DAE").write_text(text, encoding="utf-8", errors="surrogateescape")

    (dst / "model.config").write_text(
        '<?xml version="1.0"?>\n<model>\n  <name>baylands_notrees</name>\n'
        '  <version>1.0</version>\n  <sdf version="1.5">model.sdf</sdf>\n'
        '  <description>Baylands terrain without trees.</description>\n</model>\n'
    )
    (dst / "model.sdf").write_text(
        '<?xml version="1.0" ?>\n<sdf version="1.5">\n'
        '  <model name="baylands_notrees">\n    <pose>0 0 -1.3 0 0 0</pose>\n'
        '    <static>true</static>\n    <link name="body3">\n'
        '      <visual name="visual"><geometry><mesh>'
        '<uri>model://baylands_notrees/media/baylands_03.DAE</uri></mesh></geometry></visual>\n'
        '      <collision name="collision"><geometry><mesh>'
        '<uri>model://baylands_notrees/media/baylands_03.DAE</uri></mesh></geometry></collision>\n'
        '    </link>\n  </model>\n</sdf>\n'
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
