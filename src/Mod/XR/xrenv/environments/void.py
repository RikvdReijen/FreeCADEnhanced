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
"""A dark void with a horizon grid — the minimum-distraction environment.

Nothing but a ground plane grid, a horizon ring and the world axes, so
nothing competes with the model.  Life size (``user_scale`` 1.0).
"""

from __future__ import annotations

import math
from typing import Any, Dict

from ._common import SpecBuilder, rot_x, rot_z, srgb

ENVIRONMENT_ID = "void"
ENVIRONMENT_NAME = "Void (horizon grid)"
ENVIRONMENT_DESCRIPTION = (
    "A dark, empty world with a metre grid fading to a horizon ring and the "
    "world axes marked.  Nothing to distract from the model."
)

EXTENT = 60.0
HEIGHT = 24.0


def build() -> Dict[str, Any]:
    b = SpecBuilder(
        ENVIRONMENT_ID,
        ENVIRONMENT_NAME,
        ENVIRONMENT_DESCRIPTION,
        user_scale=1.0,
        bounds=(EXTENT, EXTENT, HEIGHT),
        spawn=(0.0, 0.0, 0.0),
        ambient=(0.03, 0.03, 0.04),
    )

    b.material("void_ground", srgb(0.045, 0.048, 0.055), roughness=0.95)
    b.material("grid_minor", srgb(0.16, 0.19, 0.24), roughness=0.60,
               emissive=[0.03, 0.05, 0.07])
    b.material("grid_major", srgb(0.24, 0.34, 0.46), roughness=0.55,
               emissive=[0.05, 0.10, 0.16])
    b.material("horizon", srgb(0.20, 0.32, 0.48), roughness=0.40,
               emissive=[0.08, 0.16, 0.26])
    b.material("axis_x", srgb(0.72, 0.18, 0.16), roughness=0.45,
               emissive=[0.30, 0.04, 0.03])
    b.material("axis_y", srgb(0.22, 0.62, 0.24), roughness=0.45,
               emissive=[0.05, 0.26, 0.06])
    b.material("axis_z", srgb(0.18, 0.36, 0.78), roughness=0.45,
               emissive=[0.03, 0.09, 0.32])
    b.material("beacon", srgb(0.55, 0.60, 0.70), roughness=0.35,
               emissive=[0.20, 0.24, 0.32])

    # a single cool key plus a dim fill; the grid does the rest
    b.directional((-0.30, -0.90, -0.32), color=(0.55, 0.60, 0.70), intensity=0.55)
    b.directional((0.40, -0.20, 0.90), color=(0.20, 0.24, 0.34), intensity=0.25)
    b.point((0.0, 2.2, 0.0), color=(0.35, 0.42, 0.55), intensity=0.40, rng=14.0)

    b.anchor("grid_origin", (0.0, 0.0, 0.0), (4.0, 4.0))

    # ground and grids
    b.plane("ground", (EXTENT, EXTENT), (0.0, -0.01, 0.0), b.mat("void_ground"),
            subdiv=(4, 4))
    b.grid("grid_minor", (40.0, 40.0), 1.0, 0.012, (0.0, 0.0, 0.0), b.mat("grid_minor"))
    b.grid("grid_major", (40.0, 40.0), 5.0, 0.030, (0.0, 0.004, 0.0), b.mat("grid_major"))

    # horizon rings
    for r, tr in ((14.0, 0.020), (20.0, 0.026), (26.0, 0.034)):
        b.torus("horizon_ring", r, tr, (0.0, 0.01, 0.0), b.mat("horizon"),
                sides=6, rings=72)

    # world axes
    b.cylinder("axis_x_rod", 0.018, 6.0, (3.0, 0.02, 0.0), b.mat("axis_x"),
               rot=rot_z(90.0), sides=10)
    b.cone("axis_x_tip", 0.05, 0.0, 0.20, (6.1, 0.02, 0.0), b.mat("axis_x"),
           rot=rot_z(-90.0), sides=12)
    b.cylinder("axis_y_rod", 0.018, 3.0, (0.0, 1.5, 0.0), b.mat("axis_y"), sides=10)
    b.cone("axis_y_tip", 0.05, 0.0, 0.20, (0.0, 3.1, 0.0), b.mat("axis_y"), sides=12)
    b.cylinder("axis_z_rod", 0.018, 6.0, (0.0, 0.02, 3.0), b.mat("axis_z"),
               rot=rot_x(90.0), sides=10)
    b.cone("axis_z_tip", 0.05, 0.0, 0.20, (0.0, 0.02, 6.1), b.mat("axis_z"),
           rot=rot_x(90.0), sides=12)
    b.sphere("origin_marker", 0.06, (0.0, 0.02, 0.0), b.mat("beacon"),
             rings=8, sectors=14)

    # four faint beacons so the user keeps their bearings while turning
    for k in range(4):
        a = math.radians(45.0 + 90.0 * k)
        b.cylinder("beacon_post", 0.03, 1.6,
                   (18.0 * math.cos(a), 0.8, 18.0 * math.sin(a)), b.mat("beacon"),
                   sides=8)
        b.sphere("beacon_lamp", 0.10,
                 (18.0 * math.cos(a), 1.7, 18.0 * math.sin(a)), b.mat("horizon"),
                 rings=8, sectors=12)
    return b.build()
