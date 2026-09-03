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
"""A small maker workshop: bench, pegboard, shelving and a window.

Life size, and the only fallback environment with a real work surface — its
``worktable`` anchor is a primary anchor, so a document can be dropped onto
the bench.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from ._common import (
    IDENT,
    PLATE_ROT,
    SpecBuilder,
    angle_profile,
    channel_profile,
    rot_mul,
    rot_x,
    rot_y,
    rot_z,
    srgb,
)

ENVIRONMENT_ID = "workshop"
ENVIRONMENT_NAME = "Maker workshop"
ENVIRONMENT_DESCRIPTION = (
    "A small workshop with a beech workbench, pegboard of tools, steel "
    "shelving and daylight through a window.  Life size, with the bench top "
    "as a drop target for the document."
)

ROOM_W = 6.0
ROOM_D = 5.0
ROOM_H = 2.9

HALF_W = ROOM_W / 2.0
HALF_D = ROOM_D / 2.0

BENCH_TOP = 0.92
BENCH_W = 2.40
BENCH_D = 0.75
BENCH_Z = HALF_D - 0.45


def _materials(b: SpecBuilder) -> None:
    b.material("floor_concrete", srgb(0.44, 0.44, 0.43), roughness=0.90)
    b.material("wall_paint", srgb(0.78, 0.78, 0.75), roughness=0.88)
    b.material("ceiling", srgb(0.88, 0.88, 0.87), roughness=0.92)
    b.material("beech", srgb(0.74, 0.58, 0.36), roughness=0.62)
    b.material("beech_dark", srgb(0.55, 0.40, 0.22), roughness=0.66)
    b.material("steel_frame", srgb(0.28, 0.29, 0.32), metallic=0.75, roughness=0.45)
    b.material("galv_steel", srgb(0.62, 0.64, 0.66), metallic=0.82, roughness=0.42)
    b.material("pegboard", srgb(0.82, 0.72, 0.52), roughness=0.75)
    b.material("tool_steel", srgb(0.68, 0.69, 0.72), metallic=0.90, roughness=0.28)
    b.material("tool_handle_red", srgb(0.68, 0.14, 0.12), roughness=0.60)
    b.material("tool_handle_blue", srgb(0.14, 0.30, 0.62), roughness=0.60)
    b.material("tool_handle_yellow", srgb(0.85, 0.68, 0.10), roughness=0.60)
    b.material("black_plastic", srgb(0.08, 0.08, 0.09), roughness=0.55)
    b.material("glass_window", srgb(0.72, 0.82, 0.88, 0.16), roughness=0.04)
    b.material("lamp_white", srgb(1.0, 0.98, 0.94), roughness=0.30,
               emissive=[0.95, 0.93, 0.88])
    b.material("cardboard", srgb(0.66, 0.52, 0.36), roughness=0.86)
    b.material("plastic_bin", srgb(0.20, 0.42, 0.66), roughness=0.55)
    b.material("label_text", srgb(0.12, 0.12, 0.14), roughness=0.70)


def _room(b: SpecBuilder) -> None:
    b.plane("floor", (ROOM_W, ROOM_D), (0.0, 0.0, 0.0), b.mat("floor_concrete"),
            subdiv=(6, 5))
    b.plane("ceiling", (ROOM_W, ROOM_D), (0.0, ROOM_H, 0.0), b.mat("ceiling"),
            rot=rot_x(90.0), subdiv=(3, 3))
    b.box("wall_back", (ROOM_W, ROOM_H, 0.08), (0.0, ROOM_H * 0.5, HALF_D),
          b.mat("wall_paint"))
    b.box("wall_front", (ROOM_W, ROOM_H, 0.08), (0.0, ROOM_H * 0.5, -HALF_D),
          b.mat("wall_paint"))
    for sx in (-1, 1):
        b.box("wall_side", (0.08, ROOM_H, ROOM_D), (sx * HALF_W, ROOM_H * 0.5, 0.0),
              b.mat("wall_paint"))
    # skirting
    for sz in (-1, 1):
        b.box("skirting", (ROOM_W, 0.10, 0.02), (0.0, 0.05, sz * (HALF_D - 0.05)),
              b.mat("beech_dark"))

    # window on the left wall
    win = b.group("window", at=(-HALF_W + 0.05, 1.60, -0.60))
    b.box("window_glass", (0.02, 1.10, 1.60), (0.0, 0.0, 0.0), b.mat("glass_window"),
          parent=win)
    for sy in (-1, 1):
        b.box("window_frame_h", (0.06, 0.06, 1.72), (0.0, sy * 0.58, 0.0),
              b.mat("wall_paint"), parent=win)
    for sz in (-1, 1):
        b.box("window_frame_v", (0.06, 1.22, 0.06), (0.0, 0.0, sz * 0.83),
              b.mat("wall_paint"), parent=win)
    b.box("window_mullion", (0.04, 1.10, 0.04), (0.0, 0.0, 0.0),
          b.mat("wall_paint"), parent=win)
    b.box("window_sill", (0.16, 0.04, 1.80), (0.06, -0.60, 0.0), b.mat("beech"),
          parent=win)

    # fluorescent battens
    for sx in (-1, 1):
        b.box("light_batten", (0.12, 0.10, 2.40), (sx * 1.4, ROOM_H - 0.10, 0.0),
              b.mat("galv_steel"))
        b.box("light_tube", (0.07, 0.03, 2.30), (sx * 1.4, ROOM_H - 0.16, 0.0),
              b.mat("lamp_white"))


def _bench(b: SpecBuilder) -> None:
    beech = b.mat("beech")
    steel = b.mat("steel_frame")
    grp = b.group("workbench", at=(0.0, 0.0, BENCH_Z))

    b.box("bench_top", (BENCH_W, 0.05, BENCH_D), (0.0, BENCH_TOP - 0.025, 0.0),
          beech, parent=grp)
    b.box("bench_top_edge", (BENCH_W + 0.02, 0.02, 0.03),
          (0.0, BENCH_TOP - 0.045, -BENCH_D * 0.5 - 0.005), b.mat("beech_dark"),
          parent=grp)
    for i in range(9):
        b.box("bench_top_stave", (BENCH_W, 0.052, 0.004),
              (0.0, BENCH_TOP - 0.025, -BENCH_D * 0.5 + 0.04 + i * 0.085),
              b.mat("beech_dark"), parent=grp)
    # legs and rails
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.box("bench_leg", (0.07, BENCH_TOP - 0.05, 0.07),
                  (sx * (BENCH_W * 0.5 - 0.09), (BENCH_TOP - 0.05) * 0.5,
                   sz * (BENCH_D * 0.5 - 0.09)), steel, parent=grp)
            b.box("bench_foot", (0.10, 0.02, 0.10),
                  (sx * (BENCH_W * 0.5 - 0.09), 0.01, sz * (BENCH_D * 0.5 - 0.09)),
                  b.mat("black_plastic"), parent=grp)
        b.box("bench_rail_z", (0.05, 0.05, BENCH_D - 0.20),
              (sx * (BENCH_W * 0.5 - 0.09), 0.22, 0.0), steel, parent=grp)
    for sz in (-1, 1):
        b.box("bench_rail_x", (BENCH_W - 0.22, 0.05, 0.05),
              (0.0, 0.22, sz * (BENCH_D * 0.5 - 0.09)), steel, parent=grp)
    # lower shelf
    b.box("bench_shelf", (BENCH_W - 0.20, 0.025, BENCH_D - 0.18), (0.0, 0.26, 0.0),
          b.mat("beech_dark"), parent=grp)

    # drawer bank
    for i in range(3):
        y = 0.78 - i * 0.16
        b.box("drawer_front", (0.44, 0.14, 0.02), (0.72, y, -BENCH_D * 0.5 + 0.01),
              b.mat("galv_steel"), parent=grp)
        b.box("drawer_handle", (0.20, 0.02, 0.03), (0.72, y, -BENCH_D * 0.5 - 0.015),
              b.mat("tool_steel"), parent=grp)
    b.box("drawer_case", (0.48, 0.50, BENCH_D - 0.10), (0.72, 0.62, 0.02),
          b.mat("galv_steel"), parent=grp)

    # bench vice
    vice = b.group("vice", at=(-0.86, BENCH_TOP, -BENCH_D * 0.5 + 0.10), parent=grp)
    b.box("vice_body", (0.18, 0.11, 0.14), (0.0, 0.055, 0.0), b.mat("steel_frame"),
          parent=vice)
    b.box("vice_jaw_fixed", (0.16, 0.07, 0.02), (0.0, 0.09, -0.07),
          b.mat("tool_steel"), parent=vice)
    b.box("vice_jaw_moving", (0.16, 0.07, 0.02), (0.0, 0.09, -0.13),
          b.mat("tool_steel"), parent=vice)
    b.cylinder("vice_screw", 0.014, 0.24, (0.0, 0.055, -0.14), b.mat("tool_steel"),
               rot=rot_x(90.0), sides=12, parent=vice)
    b.cylinder("vice_handle", 0.010, 0.22, (0.0, 0.055, -0.26), b.mat("tool_steel"),
               rot=rot_z(90.0), sides=10, parent=vice)
    for sx in (-1, 1):
        b.sphere("vice_handle_ball", 0.018, (sx * 0.11, 0.055, -0.26),
                 b.mat("tool_steel"), rings=8, sectors=12, parent=vice)

    # things left on the bench
    b.box("cutting_mat", (0.60, 0.004, 0.45), (0.30, BENCH_TOP + 0.002, 0.02),
          b.mat("plastic_bin"), parent=grp)
    b.cylinder("coffee_mug", 0.042, 0.10, (-0.30, BENCH_TOP + 0.05, 0.24),
               b.mat("lamp_white"), sides=16, parent=grp)
    b.torus("coffee_mug_handle", 0.030, 0.007, (-0.36, BENCH_TOP + 0.05, 0.24),
            b.mat("lamp_white"), rot=rot_y(90.0), sides=6, rings=16, parent=grp)
    b.box("caliper", (0.20, 0.012, 0.05), (0.05, BENCH_TOP + 0.008, 0.28),
          b.mat("tool_steel"), rot=rot_y(12.0), parent=grp)
    b.box("caliper_display", (0.05, 0.004, 0.03), (0.10, BENCH_TOP + 0.016, 0.28),
          b.mat("black_plastic"), rot=rot_y(12.0), parent=grp)
    for k in range(4):
        b.cylinder("screwdriver", 0.008, 0.18,
                   (-0.55 + k * 0.05, BENCH_TOP + 0.010, 0.30),
                   b.mat("tool_steel"), rot=rot_mul(rot_z(90.0), rot_y(6.0 * k)),
                   sides=8, parent=grp)
        b.cylinder("screwdriver_grip", 0.014, 0.09,
                   (-0.62 + k * 0.05, BENCH_TOP + 0.012, 0.30),
                   b.mat("tool_handle_red" if k % 2 else "tool_handle_blue"),
                   rot=rot_mul(rot_z(90.0), rot_y(6.0 * k)), sides=10, parent=grp)

    # task lamp clamped to the back edge
    lamp = b.group("task_lamp", at=(-0.95, BENCH_TOP, BENCH_D * 0.5 - 0.06), parent=grp)
    b.box("lamp_clamp", (0.07, 0.09, 0.05), (0.0, 0.0, 0.0), b.mat("black_plastic"),
          parent=lamp)
    b.cylinder("lamp_arm_lower", 0.012, 0.42, (0.06, 0.22, -0.06), b.mat("galv_steel"),
               rot=rot_x(18.0), sides=8, parent=lamp)
    b.cylinder("lamp_arm_upper", 0.012, 0.40, (0.22, 0.48, -0.22), b.mat("galv_steel"),
               rot=rot_mul(rot_z(-52.0), rot_x(10.0)), sides=8, parent=lamp)
    b.cone("lamp_shade", 0.10, 0.05, 0.13, (0.40, 0.56, -0.32), b.mat("galv_steel"),
           rot=rot_mul(rot_z(-24.0), rot_x(30.0)), sides=16, parent=lamp)
    b.sphere("lamp_bulb", 0.035, (0.42, 0.50, -0.36), b.mat("lamp_white"),
             rings=8, sectors=12, parent=lamp)


def _pegboard(b: SpecBuilder) -> None:
    peg = b.mat("pegboard")
    steel = b.mat("tool_steel")
    grp = b.group("pegboard_wall", at=(0.0, 1.55, HALF_D - 0.05))

    b.box("pegboard_panel", (2.40, 1.10, 0.012), (0.0, 0.0, 0.0), peg, parent=grp)
    for r in range(9):
        for c in range(20):
            b.cylinder("pegboard_hole", 0.008, 0.014,
                       (-1.14 + c * 0.12, -0.48 + r * 0.12, 0.0),
                       b.mat("black_plastic"), rot=rot_x(90.0), sides=6, parent=grp)
    # hanging tools
    for i in range(6):
        x = -1.05 + i * 0.16
        b.box("spanner", (0.030, 0.24, 0.008), (x, 0.28, -0.020), steel, parent=grp)
        b.torus("spanner_ring", 0.024, 0.006, (x, 0.16, -0.020), steel,
                rot=rot_x(90.0), sides=6, rings=14, parent=grp)
    for i in range(5):
        x = 0.10 + i * 0.13
        b.cylinder("chisel_blade", 0.010, 0.16, (x, 0.30, -0.018), steel,
                   sides=8, parent=grp)
        b.cylinder("chisel_handle", 0.016, 0.11, (x, 0.16, -0.018),
                   b.mat("tool_handle_yellow"), sides=10, parent=grp)
    b.box("hammer_head", (0.11, 0.04, 0.04), (-0.75, -0.16, -0.026), steel, parent=grp)
    b.cylinder("hammer_handle", 0.014, 0.30, (-0.75, -0.34, -0.026),
               b.mat("beech"), sides=10, parent=grp)
    b.box("saw_blade", (0.46, 0.11, 0.004), (0.55, -0.20, -0.020), steel, parent=grp)
    b.box("saw_handle", (0.11, 0.14, 0.020), (0.83, -0.20, -0.024),
          b.mat("tool_handle_blue"), parent=grp)
    for i in range(4):
        b.box("plier", (0.05, 0.17, 0.014), (-0.25 + i * 0.09, -0.22, -0.020),
              steel, parent=grp)
        b.box("plier_grip", (0.045, 0.07, 0.018), (-0.25 + i * 0.09, -0.34, -0.020),
              b.mat("tool_handle_red"), parent=grp)
    b.text("pegboard_label", "TOOLS", 0.07, 0.004, (0.0, 0.46, -0.010),
           b.mat("label_text"), parent=grp)


def _shelving(b: SpecBuilder) -> None:
    galv = b.mat("galv_steel")
    grp = b.group("shelving", at=(HALF_W - 0.30, 0.0, -0.60))

    for sx in (-1, 1):
        for sz in (-1, 1):
            b.extrusion("shelf_upright", angle_profile(0.04, 0.04, 0.004), 2.10,
                        (sx * 0.24, 1.05, sz * 0.55), galv, rot=rot_x(-90.0),
                        parent=grp)
    for i in range(5):
        y = 0.20 + i * 0.45
        b.box("shelf_deck", (0.54, 0.018, 1.16), (0.0, y, 0.0), galv, parent=grp)
        b.box("shelf_lip", (0.54, 0.03, 0.014), (0.0, y + 0.020, -0.58), galv,
              parent=grp)
    # stock on the shelves
    for i in range(4):
        y = 0.24 + i * 0.45
        b.box("storage_box", (0.30, 0.18, 0.34), (0.0, y + 0.10, -0.32),
              b.mat("cardboard"), parent=grp)
        b.box("storage_bin", (0.24, 0.14, 0.30), (0.0, y + 0.08, 0.28),
              b.mat("plastic_bin"), parent=grp)
        b.text("storage_label", "PARTS", 0.035, 0.002, (0.0, y + 0.10, -0.492),
               b.mat("label_text"), rot=rot_y(180.0), parent=grp)
    for k in range(6):
        b.cylinder("stock_bar", 0.012, 1.05, (-0.20 + k * 0.05, 2.06, 0.0),
                   b.mat("tool_steel"), rot=rot_x(90.0), sides=8, parent=grp)


def _extras(b: SpecBuilder) -> None:
    grp = b.group("extras")

    # a rolling stool
    stool = b.group("stool", at=(-1.30, 0.0, 0.30), parent=grp)
    b.cylinder("stool_seat", 0.18, 0.05, (0.0, 0.62, 0.0), b.mat("black_plastic"),
               sides=20, parent=stool)
    b.cylinder("stool_column", 0.028, 0.56, (0.0, 0.32, 0.0), b.mat("galv_steel"),
               sides=12, parent=stool)
    for k in range(5):
        a = math.radians(72.0 * k)
        b.box("stool_leg", (0.22, 0.03, 0.05),
              (0.11 * math.cos(a), 0.06, 0.11 * math.sin(a)), b.mat("black_plastic"),
              rot=rot_y(-math.degrees(a)), parent=stool)
        b.cylinder("stool_castor", 0.026, 0.02,
                   (0.22 * math.cos(a), 0.026, 0.22 * math.sin(a)),
                   b.mat("black_plastic"), rot=rot_z(90.0), sides=10, parent=stool)

    # a shop vacuum and its hose
    b.cylinder("shop_vac_body", 0.20, 0.42, (2.10, 0.21, 1.20), b.mat("plastic_bin"),
               sides=20, parent=grp)
    b.cylinder("shop_vac_head", 0.21, 0.16, (2.10, 0.50, 1.20), b.mat("black_plastic"),
               sides=20, parent=grp)
    b.tube("shop_vac_hose",
           [[2.10, 0.56, 1.10], [1.90, 0.75, 0.90], [1.55, 0.85, 0.60],
            [1.30, 0.95, 0.40]],
           0.030, b.mat("black_plastic"), sides=8, parent=grp)

    # a broom leaning in the corner and a bin
    b.cylinder("broom_handle", 0.014, 1.40, (-2.55, 0.80, 2.10), b.mat("beech"),
               rot=rot_x(-8.0), sides=8, parent=grp)
    b.box("broom_head", (0.30, 0.06, 0.07), (-2.55, 0.06, 2.02), b.mat("beech_dark"),
          parent=grp)
    b.cone("waste_bin", 0.17, 0.21, 0.44, (2.40, 0.22, -1.60), b.mat("galv_steel"),
           sides=18, parent=grp)

    # a sheet of stock leaning against the wall
    for k in range(3):
        b.box("stock_sheet", (1.10, 1.50, 0.016), (-1.90 + k * 0.05, 0.76, 2.30),
              b.mat("beech"), rot=rot_x(-7.0), parent=grp)

    b.text("workshop_sign", "WORKSHOP", 0.09, 0.006, (0.0, 2.45, HALF_D - 0.055),
           b.mat("label_text"), rot=rot_y(180.0), parent=grp)


def build() -> Dict[str, Any]:
    b = SpecBuilder(
        ENVIRONMENT_ID,
        ENVIRONMENT_NAME,
        ENVIRONMENT_DESCRIPTION,
        user_scale=1.0,
        bounds=(ROOM_W, ROOM_D, ROOM_H),
        spawn=(0.0, 0.0, BENCH_Z - 1.10),
        ambient=(0.16, 0.17, 0.19),
    )
    _materials(b)

    # daylight from the window plus the overhead battens
    b.directional((0.78, -0.52, 0.35), color=(1.0, 0.98, 0.94), intensity=0.80)
    b.directional((-0.30, -0.90, -0.30), color=(0.68, 0.72, 0.80), intensity=0.40)
    b.point((-1.4, ROOM_H - 0.20, 0.0), color=(0.95, 0.96, 1.0), intensity=0.55, rng=6.0)
    b.point((1.4, ROOM_H - 0.20, 0.0), color=(0.95, 0.96, 1.0), intensity=0.55, rng=6.0)
    b.spot((-0.55, BENCH_TOP + 0.56, BENCH_Z + 0.30), (0.35, -0.88, -0.32),
           color=(1.0, 0.96, 0.88), intensity=0.9, cutoff_deg=48.0, rng=2.0)

    b.anchor("worktable", (0.0, BENCH_TOP, BENCH_Z), (BENCH_W - 0.30, BENCH_D - 0.15))
    b.anchor("floor_centre", (0.0, 0.0, 0.0), (2.0, 2.0))

    _room(b)
    _bench(b)
    _pegboard(b)
    _shelving(b)
    _extras(b)
    return b.build()
