# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""A neutral photo studio: seamless cyclorama, soft lights, reference grid.

One of the two distraction-free fallbacks.  Life size (``user_scale`` 1.0),
so the document sits in front of the user exactly as big as it really is.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from ._common import (
    IDENT,
    PLATE_ROT,
    SpecBuilder,
    rot_mul,
    rot_x,
    rot_y,
    rot_z,
    srgb,
)

ENVIRONMENT_ID = "studio"
ENVIRONMENT_NAME = "Photo studio (cyclorama)"
ENVIRONMENT_DESCRIPTION = (
    "A seamless white cyclorama with soft key and fill lights and a metre "
    "reference grid on the floor.  Life size and deliberately plain."
)

ROOM_W = 10.0
ROOM_D = 10.0
ROOM_H = 4.5

CYC_FRONT = -4.0
CYC_BACK = 3.5
CYC_TOP = 3.8
CYC_RADIUS = 1.5


def _cyc_profile() -> List[List[float]]:
    """Cove profile in (z, y); extruded along X it becomes the cyclorama."""
    pts: List[List[float]] = [[CYC_FRONT, 0.0], [CYC_BACK - CYC_RADIUS, 0.0]]
    steps = 10
    cx, cy = CYC_BACK - CYC_RADIUS, CYC_RADIUS
    for i in range(1, steps + 1):
        a = math.pi * 0.5 * i / steps
        pts.append([round(cx + CYC_RADIUS * math.sin(a), 5),
                    round(cy - CYC_RADIUS * math.cos(a), 5)])
    pts.append([CYC_BACK, CYC_TOP])
    pts.append([CYC_BACK + 0.25, CYC_TOP])
    pts.append([CYC_BACK + 0.25, -0.20])
    pts.append([CYC_FRONT, -0.20])
    return pts


def _softbox(b: SpecBuilder, name: str, at, aim_deg: float, size=(1.2, 1.6),
             parent=None) -> None:
    """A softbox on a stand, aimed by rotating about Y."""
    frame = b.mat("light_frame")
    diff = b.mat("diffusion")
    grp = b.group(name, at=at, rot=rot_y(aim_deg), parent=parent)
    b.box(name + "_body", (size[0], size[1], 0.30), (0.0, 0.0, 0.20), frame, parent=grp)
    b.box(name + "_diffuser", (size[0] - 0.06, size[1] - 0.06, 0.02),
          (0.0, 0.0, 0.04), diff, parent=grp)
    b.box(name + "_grid", (size[0] - 0.10, size[1] - 0.10, 0.05),
          (0.0, 0.0, 0.015), b.mat("light_frame"), parent=grp)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.cylinder(name + "_rod", 0.012, 0.42,
                       (sx * size[0] * 0.28, sy * size[1] * 0.28, 0.22), frame,
                       rot=rot_x(90.0), sides=8, parent=grp)
    b.cylinder(name + "_yoke", 0.020, 0.36, (0.0, -size[1] * 0.5 - 0.02, 0.24),
               frame, rot=rot_z(90.0), sides=10, parent=grp)


def _stand(b: SpecBuilder, name: str, at, height: float, parent=None) -> None:
    frame = b.mat("light_frame")
    grp = b.group(name, at=at, parent=parent)
    b.cylinder(name + "_column", 0.026, height, (0.0, height * 0.5, 0.0), frame,
               sides=12, parent=grp)
    b.cylinder(name + "_column_upper", 0.018, height * 0.5,
               (0.0, height * 0.9, 0.0), frame, sides=12, parent=grp)
    for k in range(3):
        a = 120.0 * k
        b.cylinder(name + "_leg", 0.012, 0.62,
                   (0.24 * math.cos(math.radians(a)), 0.20,
                    0.24 * math.sin(math.radians(a))), frame,
                   rot=rot_mul(rot_y(-a), rot_z(-34.0)), sides=8, parent=grp)
        b.cylinder(name + "_foot", 0.022, 0.02,
                   (0.44 * math.cos(math.radians(a)), 0.01,
                    0.44 * math.sin(math.radians(a))), b.mat("rubber"),
                   sides=10, parent=grp)
    b.cylinder(name + "_collar", 0.032, 0.05, (0.0, height * 0.62, 0.0),
               b.mat("rubber"), sides=12, parent=grp)


def build() -> Dict[str, Any]:
    b = SpecBuilder(
        ENVIRONMENT_ID,
        ENVIRONMENT_NAME,
        ENVIRONMENT_DESCRIPTION,
        user_scale=1.0,
        bounds=(ROOM_W, ROOM_D, ROOM_H),
        spawn=(0.0, 0.0, -1.4),
        ambient=(0.30, 0.30, 0.32),
    )

    b.material("cyc_white", srgb(0.92, 0.92, 0.92), roughness=0.92)
    b.material("floor_grey", srgb(0.55, 0.56, 0.58), roughness=0.80)
    b.material("grid_line", srgb(0.30, 0.32, 0.36), roughness=0.70)
    b.material("grid_major", srgb(0.18, 0.36, 0.55), roughness=0.65)
    b.material("light_frame", srgb(0.16, 0.16, 0.17), metallic=0.35, roughness=0.45)
    b.material("diffusion", srgb(1.0, 0.99, 0.97), roughness=0.90,
               emissive=[0.95, 0.94, 0.90])
    b.material("rubber", srgb(0.08, 0.08, 0.09), roughness=0.88)
    b.material("chrome", srgb(0.85, 0.86, 0.88), metallic=1.0, roughness=0.10)
    b.material("truss_alu", srgb(0.68, 0.69, 0.71), metallic=0.88, roughness=0.30)
    b.material("chart_grey", srgb(0.46, 0.46, 0.46), roughness=0.85)
    b.material("chart_white", srgb(0.94, 0.94, 0.94), roughness=0.85)
    b.material("chart_black", srgb(0.05, 0.05, 0.05), roughness=0.85)
    b.material("chart_red", srgb(0.72, 0.16, 0.14), roughness=0.85)
    b.material("chart_green", srgb(0.20, 0.56, 0.28), roughness=0.85)
    b.material("chart_blue", srgb(0.16, 0.32, 0.68), roughness=0.85)
    b.material("label_text", srgb(0.22, 0.23, 0.26), roughness=0.75)

    # broad, soft lighting from three quarters plus a top fill
    b.directional((-0.45, -0.72, -0.53), color=(1.0, 0.99, 0.96), intensity=0.85)
    b.directional((0.62, -0.50, -0.60), color=(0.80, 0.84, 0.92), intensity=0.45)
    b.directional((0.0, -1.0, 0.15), color=(0.90, 0.92, 0.95), intensity=0.35)
    b.point((0.0, 2.6, -1.2), color=(1.0, 0.98, 0.94), intensity=0.55, rng=8.0)

    b.anchor("stage_centre", (0.0, 0.0, 0.0), (4.0, 4.0))
    b.anchor("turntable", (0.0, 0.30, 0.0), (1.2, 1.2))

    # --- the cyclorama ----------------------------------------------------
    # profile is authored in (z, y); rot_y(-90) sends the extrusion axis
    # along -X, so the cove sweeps the full width of the room
    b.extrusion("cyclorama", _cyc_profile(), ROOM_W, (0.0, 0.0, 0.0),
                b.mat("cyc_white"), rot=rot_y(-90.0))
    for sx in (-1, 1):
        b.box("side_flat", (0.25, CYC_TOP + 0.2, ROOM_D * 0.75),
              (sx * (ROOM_W * 0.5 - 0.12), (CYC_TOP - 0.2) * 0.5, 0.6),
              b.mat("cyc_white"))

    # --- floor and reference grid ----------------------------------------
    b.plane("floor", (ROOM_W, ROOM_D), (0.0, -0.002, 0.0), b.mat("floor_grey"),
            subdiv=(10, 10))
    b.grid("reference_grid", (8.0, 8.0), 0.5, 0.010, (0.0, 0.004, -0.4),
           b.mat("grid_line"))
    b.grid("reference_grid_major", (8.0, 8.0), 2.0, 0.024, (0.0, 0.006, -0.4),
           b.mat("grid_major"))
    b.cylinder("origin_marker", 0.06, 0.006, (0.0, 0.008, 0.0), b.mat("grid_major"),
               sides=24)

    # --- turntable pedestal ----------------------------------------------
    b.cylinder("pedestal", 0.55, 0.28, (0.0, 0.14, 0.0), b.mat("cyc_white"), sides=32)
    b.cylinder("pedestal_top", 0.58, 0.02, (0.0, 0.29, 0.0), b.mat("chart_white"),
               sides=32)
    b.torus("pedestal_ring", 0.575, 0.008, (0.0, 0.30, 0.0), b.mat("chrome"),
            sides=6, rings=32)

    # --- lights -----------------------------------------------------------
    _stand(b, "stand_key", (-2.4, 0.0, -2.0), 2.1)
    _softbox(b, "softbox_key", (-2.4, 2.1, -2.0), 48.0, (1.4, 1.9))
    _stand(b, "stand_fill", (2.6, 0.0, -1.6), 1.8)
    _softbox(b, "softbox_fill", (2.6, 1.8, -1.6), -42.0, (1.1, 1.5))
    _stand(b, "stand_rim", (1.8, 0.0, 2.4), 2.4)
    _softbox(b, "softbox_rim", (1.8, 2.4, 2.4), 200.0, (0.8, 1.1))

    # overhead truss carrying a strip light
    for sx in (-1, 1):
        b.cylinder("truss_chord", 0.030, 7.0, (sx * 0.24, ROOM_H - 0.55, -0.4),
                   b.mat("truss_alu"), rot=rot_z(90.0), sides=10)
    for i in range(12):
        z = -3.6 + i * 0.6
        b.cylinder("truss_web", 0.014, 0.5, (0.0, ROOM_H - 0.55, z),
                   b.mat("truss_alu"), rot=rot_mul(rot_z(90.0), rot_x(30.0 * (i % 2))),
                   sides=6)
    b.box("strip_light", (5.2, 0.10, 0.22), (0.0, ROOM_H - 0.72, -0.4),
          b.mat("light_frame"))
    b.box("strip_light_diffuser", (5.0, 0.03, 0.18), (0.0, ROOM_H - 0.78, -0.4),
          b.mat("diffusion"))

    # --- colour reference chart on a small easel --------------------------
    chart = b.group("colour_chart", at=(1.6, 0.0, -0.9), rot=rot_y(-28.0))
    b.box("chart_board", (0.44, 0.30, 0.014), (0.0, 0.62, 0.0), b.mat("chart_black"),
          parent=chart)
    swatches = ("chart_white", "chart_grey", "chart_black", "chart_red",
                "chart_green", "chart_blue")
    for i, key in enumerate(swatches):
        col = i % 3
        row = i // 3
        b.box("chart_swatch", (0.12, 0.12, 0.004),
              (-0.14 + col * 0.14, 0.70 - row * 0.14, -0.010), b.mat(key), parent=chart)
    b.cylinder("chart_leg", 0.014, 0.62, (-0.16, 0.31, 0.02), b.mat("light_frame"),
               sides=8, parent=chart)
    b.cylinder("chart_leg", 0.014, 0.62, (0.16, 0.31, 0.02), b.mat("light_frame"),
               sides=8, parent=chart)
    b.cylinder("chart_leg", 0.014, 0.66, (0.0, 0.33, -0.16), b.mat("light_frame"),
               rot=rot_x(-14.0), sides=8, parent=chart)
    b.text("chart_label", "REFERENCE", 0.045, 0.003, (0.0, 0.50, -0.008),
           b.mat("label_text"), parent=chart)

    # a discreet scale label on the floor
    b.text("scale_label", "1 M GRID", 0.10, 0.004, (0.0, 0.010, -3.1),
           b.mat("label_text"), rot=PLATE_ROT)
    return b.build()
