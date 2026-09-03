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
"""Interior of a large format CO2 laser cutter.

The user stands on the honeycomb bed of a 1300 x 900 mm machine while
modelling.  The full beam path is modelled — tube, mirrors 1/2/3, focus lens,
air assist — so the miniaturised user can walk along it.

Frame of reference (metres, Y up, right handed):

* origin at the centre of the cabinet floor
* ``-Z`` is the front (the lid opens towards the user), ``+Z`` the back where
  the laser tube and the exhaust plenum live
* the honeycomb bed surface sits at ``y = BED_TOP``
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from ._common import (
    IDENT,
    PLATE_ROT,
    SpecBuilder,
    angle_profile,
    channel_profile,
    look_rotation,
    rot_mul,
    rot_x,
    rot_y,
    rot_z,
    slot_profile,
    srgb,
)

ENVIRONMENT_ID = "laser_cutter"
ENVIRONMENT_NAME = "CO2 laser cutter (bed interior)"
ENVIRONMENT_DESCRIPTION = (
    "Stand on the honeycomb bed of a large format CO2 laser while you model. "
    "Knife-edge slats, X gantry, focus head with air assist, the full three "
    "mirror beam path, glowing tube and extraction plenum, at 1:9 scale."
)

# --- principal dimensions --------------------------------------------------

CAB_W = 1.60          # X, cabinet interior width
CAB_D = 1.20          # Z, cabinet interior depth
CAB_H = 0.60          # Y, cabinet interior height

HALF_W = CAB_W / 2.0
HALF_D = CAB_D / 2.0

BED_W = 1.30          # working area, X
BED_D = 0.90          # working area, Z
BED_TOP = 0.120       # honeycomb top surface

GANTRY_Y = 0.300      # X beam centre height
HEAD_X = -0.180       # parked head position
GANTRY_Z = -0.060

TUBE_Y = 0.430        # laser tube axis height
TUBE_LEN = 1.30

USER_SCALE = 9.0      # 1.65 m eye height reads as 0.18 m inside


# ---------------------------------------------------------------------------


def _materials(b: SpecBuilder) -> None:
    b.material("cabinet_steel", srgb(0.22, 0.23, 0.25), metallic=0.80, roughness=0.45)
    b.material("panel_paint", srgb(0.14, 0.15, 0.17), metallic=0.20, roughness=0.60)
    b.material("alu_extrusion", srgb(0.60, 0.61, 0.63), metallic=0.90, roughness=0.30)
    b.material("alu_bright", srgb(0.78, 0.79, 0.81), metallic=0.94, roughness=0.18)
    b.material("honeycomb_alu", srgb(0.46, 0.44, 0.40), metallic=0.72, roughness=0.62)
    b.material("slat_steel", srgb(0.34, 0.33, 0.32), metallic=0.78, roughness=0.66)
    b.material("black_anod", srgb(0.08, 0.08, 0.09), metallic=0.45, roughness=0.40)
    b.material("dark_plastic", srgb(0.12, 0.12, 0.13), roughness=0.52)
    b.material("grey_plastic", srgb(0.40, 0.41, 0.43), roughness=0.58)
    b.material("white_plastic", srgb(0.88, 0.88, 0.87), roughness=0.46)
    b.material("acrylic_tint", srgb(0.55, 0.14, 0.12, 0.24), roughness=0.05)
    b.material("mirror_si", srgb(0.92, 0.90, 0.82), metallic=1.0, roughness=0.02)
    b.material("lens_znse", srgb(0.92, 0.72, 0.30, 0.42), metallic=0.35, roughness=0.04)
    b.material("glass_tube", srgb(0.80, 0.86, 0.90, 0.16), roughness=0.04)
    b.material("plasma", srgb(0.72, 0.35, 0.95), roughness=0.10,
               emissive=[0.65, 0.28, 0.95])
    b.material("beam_red", srgb(1.0, 0.10, 0.06), roughness=0.10,
               emissive=[1.0, 0.06, 0.03])
    b.material("coolant_tube", srgb(0.30, 0.55, 0.80, 0.55), roughness=0.25)
    b.material("belt_rubber", srgb(0.05, 0.05, 0.06), roughness=0.80)
    b.material("brass", srgb(0.78, 0.62, 0.28), metallic=0.94, roughness=0.26)
    b.material("copper", srgb(0.74, 0.42, 0.24), metallic=0.94, roughness=0.30)
    b.material("chrome", srgb(0.86, 0.87, 0.89), metallic=1.0, roughness=0.06)
    b.material("steel", srgb(0.55, 0.56, 0.58), metallic=0.90, roughness=0.32)
    b.material("air_hose", srgb(0.20, 0.20, 0.22), roughness=0.70)
    b.material("wire_black", srgb(0.05, 0.05, 0.05), roughness=0.68)
    b.material("pcb_green", srgb(0.06, 0.28, 0.14), roughness=0.42)
    b.material("led_white", srgb(1.0, 0.98, 0.94), roughness=0.25,
               emissive=[0.95, 0.94, 0.90])
    b.material("label_yellow", srgb(0.93, 0.77, 0.05), roughness=0.55)
    b.material("label_red", srgb(0.75, 0.09, 0.07), roughness=0.55)
    b.material("label_text", srgb(0.04, 0.04, 0.04), roughness=0.60)
    b.material("soot", srgb(0.09, 0.08, 0.07), roughness=0.94)
    b.material("plywood", srgb(0.72, 0.56, 0.34), roughness=0.72)


# ---------------------------------------------------------------------------


def _cabinet(b: SpecBuilder) -> None:
    steel = b.mat("cabinet_steel")
    paint = b.mat("panel_paint")
    alu = b.mat("alu_extrusion")
    grp = b.group("cabinet")
    prof = slot_profile(0.040, slot=0.010, depth=0.010, chamfer=0.003)
    stand = rot_x(-90.0)
    lie_x = rot_y(90.0)

    # 4040 frame: uprights and rails
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.extrusion("frame_column", prof, CAB_H,
                        (sx * (HALF_W - 0.025), CAB_H * 0.5, sz * (HALF_D - 0.025)),
                        alu, rot=stand, parent=grp)
    for y in (0.024, CAB_H - 0.024):
        for sz in (-1, 1):
            b.extrusion("frame_rail_x", prof, CAB_W - 0.09,
                        (0.0, y, sz * (HALF_D - 0.025)), alu, rot=lie_x, parent=grp)
        for sx in (-1, 1):
            b.extrusion("frame_rail_z", prof, CAB_D - 0.09,
                        (sx * (HALF_W - 0.025), y, 0.0), alu, parent=grp)

    # sheet metal skin
    for sx in (-1, 1):
        b.box("side_panel", (0.003, CAB_H - 0.02, CAB_D - 0.02),
              (sx * (HALF_W - 0.002), CAB_H * 0.5, 0.0), steel, parent=grp)
        for i in range(7):
            b.cylinder("side_rivet", 0.0035, 0.003,
                       (sx * (HALF_W - 0.005), 0.05 + i * 0.08, -HALF_D + 0.05),
                       b.mat("alu_bright"), rot=rot_z(90.0), sides=8, parent=grp)
    b.box("back_panel", (CAB_W - 0.02, CAB_H - 0.02, 0.003),
          (0.0, CAB_H * 0.5, HALF_D - 0.002), steel, parent=grp)
    b.box("front_apron", (CAB_W - 0.02, 0.090, 0.003),
          (0.0, 0.046, -HALF_D + 0.002), paint, parent=grp)
    b.box("floor_pan", (CAB_W - 0.02, 0.004, CAB_D - 0.02), (0.0, 0.002, 0.0),
          steel, parent=grp)

    # crumb tray sitting on the floor
    b.box("crumb_tray", (BED_W + 0.04, 0.010, BED_D + 0.04), (0.0, 0.030, 0.0),
          b.mat("slat_steel"), parent=grp)
    for sz in (-1, 1):
        b.box("crumb_tray_lip", (BED_W + 0.04, 0.030, 0.004),
              (0.0, 0.048, sz * (BED_D * 0.5 + 0.020)), b.mat("slat_steel"), parent=grp)
    for sx in (-1, 1):
        b.box("crumb_tray_lip", (0.004, 0.030, BED_D + 0.04),
              (sx * (BED_W * 0.5 + 0.020), 0.048, 0.0), b.mat("slat_steel"), parent=grp)
    b.box("crumb_tray_handle", (0.120, 0.014, 0.020), (0.0, 0.052, -BED_D * 0.5 - 0.024),
          b.mat("chrome"), parent=grp)

    # castors
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.cylinder("castor", 0.030, 0.020,
                       (sx * (HALF_W - 0.06), -0.030, sz * (HALF_D - 0.06)),
                       b.mat("dark_plastic"), rot=rot_z(90.0), sides=14, parent=grp)
            b.box("castor_yoke", (0.020, 0.030, 0.036),
                  (sx * (HALF_W - 0.06), -0.006, sz * (HALF_D - 0.06)),
                  b.mat("steel"), parent=grp)


def _bed(b: SpecBuilder) -> None:
    honey = b.mat("honeycomb_alu")
    slat = b.mat("slat_steel")
    alu = b.mat("alu_extrusion")
    grp = b.group("bed")

    # honeycomb tray
    b.honeycomb("honeycomb_bed", (BED_W, BED_D), 0.032, 0.0018, 0.020,
                (0.0, BED_TOP - 0.010, 0.0), honey, rot=PLATE_ROT, parent=grp)
    for sz in (-1, 1):
        b.box("honeycomb_rim", (BED_W + 0.012, 0.024, 0.006),
              (0.0, BED_TOP - 0.010, sz * (BED_D * 0.5 + 0.003)), alu, parent=grp)
    for sx in (-1, 1):
        b.box("honeycomb_rim", (0.006, 0.024, BED_D + 0.012),
              (sx * (BED_W * 0.5 + 0.003), BED_TOP - 0.010, 0.0), alu, parent=grp)

    # knife edge slats carrying the tray
    knife = [[-0.0015, 0.0], [0.0015, 0.0], [0.0015, 0.024], [0.0, 0.032], [-0.0015, 0.024]]
    nslats = 26
    for i in range(nslats):
        x = -BED_W * 0.5 + 0.02 + i * (BED_W - 0.04) / (nslats - 1)
        # the profile lies in XY and extrudes along +Z, which is exactly a
        # slat standing on edge and running front to back
        b.extrusion("knife_slat", knife, BED_D - 0.02, (x, BED_TOP - 0.052, 0.0),
                    slat, parent=grp)
        b.box("slat_notch", (0.004, 0.006, BED_D - 0.02), (x, BED_TOP - 0.038, 0.0),
              b.mat("soot"), parent=grp)
    for sz in (-1, 1):
        b.extrusion("slat_carrier", channel_profile(0.030, 0.020, 0.003), BED_W - 0.02,
                    (0.0, BED_TOP - 0.070, sz * (BED_D * 0.5 - 0.020)), alu,
                    rot=rot_y(90.0), parent=grp)

    # bed lift screws in the four corners
    for sx in (-1, 1):
        for sz in (-1, 1):
            x, z = sx * (BED_W * 0.5 - 0.03), sz * (BED_D * 0.5 - 0.03)
            b.cylinder("bed_lift_screw", 0.006, 0.070, (x, BED_TOP - 0.075, z),
                       b.mat("chrome"), sides=12, parent=grp)
            b.cylinder("bed_lift_nut", 0.011, 0.012, (x, BED_TOP - 0.048, z),
                       b.mat("brass"), sides=12, parent=grp)
            b.cylinder("bed_lift_sprocket", 0.016, 0.006, (x, BED_TOP - 0.100, z),
                       b.mat("steel"), sides=16, parent=grp)
    b.tube("bed_lift_chain",
           [[-BED_W * 0.5 + 0.03, BED_TOP - 0.100, -BED_D * 0.5 + 0.03],
            [BED_W * 0.5 - 0.03, BED_TOP - 0.100, -BED_D * 0.5 + 0.03],
            [BED_W * 0.5 - 0.03, BED_TOP - 0.100, BED_D * 0.5 - 0.03],
            [-BED_W * 0.5 + 0.03, BED_TOP - 0.100, BED_D * 0.5 - 0.03],
            [-BED_W * 0.5 + 0.03, BED_TOP - 0.100, -BED_D * 0.5 + 0.03]],
           0.0035, b.mat("steel"), sides=6, parent=grp)

    # a half cut sheet of ply left on the bed, with a scorched kerf
    b.box("workpiece_ply", (0.400, 0.004, 0.300), (0.180, BED_TOP + 0.002, 0.150),
          b.mat("plywood"), parent=grp)
    b.box("workpiece_kerf", (0.280, 0.0012, 0.003), (0.180, BED_TOP + 0.0045, 0.120),
          b.mat("soot"), parent=grp)
    for k in range(6):
        b.cylinder("cut_disc", 0.022, 0.004, (0.070 + k * 0.048, BED_TOP + 0.002, 0.230),
                   b.mat("plywood"), sides=18, parent=grp)


def _motion(b: SpecBuilder) -> None:
    alu = b.mat("alu_extrusion")
    bright = b.mat("alu_bright")
    black = b.mat("black_anod")
    belt = b.mat("belt_rubber")
    steel = b.mat("steel")
    grp = b.group("motion")

    # Y rails along the sides
    for sx in (-1, 1):
        x = sx * (HALF_W - 0.070)
        b.box("y_rail", (0.024, 0.016, CAB_D - 0.14), (x, GANTRY_Y - 0.030, 0.0),
              steel, parent=grp)
        b.box("y_rail_base", (0.040, 0.020, CAB_D - 0.14), (x, GANTRY_Y - 0.048, 0.0),
              alu, parent=grp)
        for i in range(12):
            z = -HALF_D + 0.09 + i * (CAB_D - 0.18) / 11.0
            b.cylinder("y_rail_bolt", 0.004, 0.004, (x, GANTRY_Y - 0.021, z),
                       steel, sides=6, parent=grp)
        b.box("y_carriage", (0.048, 0.024, 0.070), (x, GANTRY_Y - 0.026, GANTRY_Z),
              bright, parent=grp)
        # Y belt with teeth
        b.box("y_belt", (0.003, 0.010, CAB_D - 0.16), (x - sx * 0.020, GANTRY_Y - 0.030, 0.0),
              belt, parent=grp)
        n = 64
        for i in range(n):
            z = -HALF_D + 0.08 + i * (CAB_D - 0.16) / (n - 1.0)
            b.box("y_belt_tooth", (0.0018, 0.009, 0.005),
                  (x - sx * 0.0225, GANTRY_Y - 0.030, z), black, parent=grp)
        # Y idler and drive pulley
        b.cylinder("y_idler", 0.014, 0.014, (x - sx * 0.020, GANTRY_Y - 0.030, -HALF_D + 0.06),
                   bright, sides=16, parent=grp)
        b.cylinder("y_drive_pulley", 0.014, 0.014, (x - sx * 0.020, GANTRY_Y - 0.030, HALF_D - 0.06),
                   steel, sides=16, parent=grp)
        b.box("y_motor", (0.056, 0.056, 0.056),
              (x - sx * 0.020, GANTRY_Y - 0.085, HALF_D - 0.06), black, parent=grp)
        for k in range(5):
            b.box("y_motor_lam", (0.058, 0.004, 0.058),
                  (x - sx * 0.020, GANTRY_Y - 0.105 + k * 0.010, HALF_D - 0.06),
                  black, parent=grp)
        b.cylinder("y_motor_shaft", 0.005, 0.030,
                   (x - sx * 0.020, GANTRY_Y - 0.050, HALF_D - 0.06), b.mat("chrome"),
                   sides=10, parent=grp)
        b.box("y_belt_tensioner", (0.024, 0.024, 0.030),
              (x - sx * 0.020, GANTRY_Y - 0.030, -HALF_D + 0.086), bright, parent=grp)
        b.cylinder("y_tension_screw", 0.003, 0.036,
                   (x - sx * 0.020, GANTRY_Y - 0.030, -HALF_D + 0.108), b.mat("chrome"),
                   rot=rot_x(90.0), sides=8, parent=grp)

    # X gantry beam spanning the machine
    gan = b.group("x_gantry", at=(0.0, GANTRY_Y, GANTRY_Z), parent=grp)
    b.extrusion("gantry_beam", slot_profile(0.060, slot=0.012, depth=0.014, chamfer=0.004),
                CAB_W - 0.150, (0.0, 0.0, 0.0), alu, rot=rot_y(90.0), parent=gan)
    b.box("gantry_rail", (CAB_W - 0.16, 0.014, 0.022), (0.0, -0.038, -0.016),
          steel, parent=gan)
    for i in range(16):
        x = -(CAB_W - 0.20) * 0.5 + i * (CAB_W - 0.20) / 15.0
        b.cylinder("gantry_rail_bolt", 0.0035, 0.004, (x, -0.030, -0.016), steel,
                   sides=6, parent=gan)
    b.box("x_belt", (CAB_W - 0.17, 0.010, 0.003), (0.0, -0.012, -0.030), belt, parent=gan)
    n = 72
    for i in range(n):
        x = -(CAB_W - 0.19) * 0.5 + i * (CAB_W - 0.19) / (n - 1.0)
        b.box("x_belt_tooth", (0.005, 0.009, 0.0018), (x, -0.012, -0.0325),
              black, parent=gan)
    for sx in (-1, 1):
        b.box("gantry_end_plate", (0.014, 0.070, 0.070), (sx * (CAB_W - 0.150) * 0.5, 0.0, 0.0),
              bright, parent=gan)
        b.cylinder("x_idler", 0.013, 0.012, (sx * ((CAB_W - 0.150) * 0.5 - 0.014), -0.012, -0.030),
                   bright, rot=rot_x(90.0), sides=16, parent=gan)
    b.box("x_motor", (0.052, 0.052, 0.052), ((CAB_W - 0.150) * 0.5 + 0.030, 0.0, 0.0),
          black, parent=gan)
    b.cylinder("x_motor_shaft", 0.005, 0.026, ((CAB_W - 0.150) * 0.5 + 0.004, 0.0, 0.0),
               b.mat("chrome"), rot=rot_z(90.0), sides=10, parent=gan)

    # drag chain following the gantry
    for i in range(18):
        t = i / 17.0
        x = -0.62 + t * 0.62
        y = GANTRY_Y + 0.048 - 0.020 * math.sin(math.pi * t)
        b.box("drag_chain_link", (0.026, 0.018, 0.030), (x, y, GANTRY_Z + 0.048),
              black, parent=grp)


def _head_and_optics(b: SpecBuilder) -> None:
    bright = b.mat("alu_bright")
    black = b.mat("black_anod")
    mirror = b.mat("mirror_si")
    grp = b.group("optics")

    # --- mirror 1: back left corner, fed by the tube -----------------------
    m1 = b.group("mirror_1", at=(-HALF_W + 0.090, TUBE_Y, HALF_D - 0.090), parent=grp)
    b.box("m1_mount", (0.050, 0.050, 0.014), (0.0, 0.0, 0.0), bright,
          rot=rot_y(45.0), parent=m1)
    b.cylinder("m1_surface", 0.0125, 0.004, (0.0, 0.0, -0.008), mirror,
               rot=rot_mul(rot_y(45.0), rot_x(90.0)), sides=20, parent=m1)
    for k in range(3):
        a = 120.0 * k
        b.cylinder("m1_adjuster", 0.004, 0.020,
                   (0.020 * math.cos(math.radians(a)), 0.020 * math.sin(math.radians(a)), 0.012),
                   b.mat("chrome"), rot=rot_x(90.0), sides=8, parent=m1)
    b.box("m1_bracket", (0.020, 0.060, 0.020), (0.0, -0.040, 0.010), black, parent=m1)

    # --- mirror 2: on the Y carriage, left end of the gantry ---------------
    m2 = b.group("mirror_2", at=(-HALF_W + 0.090, GANTRY_Y, GANTRY_Z), parent=grp)
    b.box("m2_mount", (0.044, 0.044, 0.014), (0.0, 0.0, 0.0), bright,
          rot=rot_y(-45.0), parent=m2)
    b.cylinder("m2_surface", 0.0110, 0.004, (0.006, 0.0, 0.0), mirror,
               rot=rot_mul(rot_y(-45.0), rot_x(90.0)), sides=20, parent=m2)
    for k in range(3):
        a = 120.0 * k + 40.0
        b.cylinder("m2_adjuster", 0.0035, 0.018,
                   (-0.006 + 0.016 * math.cos(math.radians(a)),
                    0.016 * math.sin(math.radians(a)), 0.0),
                   b.mat("chrome"), rot=rot_z(90.0), sides=8, parent=m2)
    b.box("m2_carrier", (0.030, 0.050, 0.050), (-0.020, 0.0, 0.0), black, parent=m2)

    # --- the laser head: mirror 3, focus lens, air assist ------------------
    head = b.group("laser_head", at=(HEAD_X, GANTRY_Y, GANTRY_Z), parent=grp)
    b.box("head_carriage", (0.070, 0.070, 0.056), (0.0, 0.0, 0.0), bright, parent=head)
    b.box("head_carriage_rear", (0.056, 0.050, 0.014), (0.0, 0.0, 0.022), black, parent=head)
    b.box("m3_housing", (0.046, 0.046, 0.046), (0.0, 0.020, -0.010), black,
          rot=rot_y(0.0), parent=head)
    b.cylinder("m3_surface", 0.0110, 0.004, (0.0, 0.020, -0.010), mirror,
               rot=rot_mul(rot_z(0.0), rot_x(-45.0)), sides=20, parent=head)
    for k in range(3):
        a = 120.0 * k
        b.cylinder("m3_adjuster", 0.0035, 0.016,
                   (0.016 * math.cos(math.radians(a)), 0.040,
                    -0.010 + 0.016 * math.sin(math.radians(a))),
                   b.mat("chrome"), sides=8, parent=head)
    # focus lens tube
    b.cylinder("lens_tube", 0.016, 0.052, (0.0, -0.024, -0.010), black, sides=20, parent=head)
    for k in range(4):
        b.torus("lens_tube_knurl", 0.0168, 0.0012, (0.0, -0.010 - k * 0.010, -0.010),
                black, rot=IDENT, sides=6, rings=20, parent=head)
    b.cylinder("focus_lens", 0.0125, 0.003, (0.0, -0.030, -0.010), b.mat("lens_znse"),
               sides=20, parent=head)
    b.cylinder("lens_retainer", 0.0150, 0.004, (0.0, -0.038, -0.010), b.mat("brass"),
               sides=20, parent=head)
    b.cone("air_assist_cone", 0.016, 0.005, 0.026, (0.0, -0.062, -0.010), b.mat("brass"),
           rot=rot_x(180.0), sides=18, parent=head)
    b.cylinder("air_assist_nozzle", 0.0040, 0.010, (0.0, -0.080, -0.010), b.mat("brass"),
               sides=14, parent=head)
    b.box("air_inlet_barb", (0.008, 0.008, 0.016), (0.018, -0.056, -0.010),
          b.mat("brass"), rot=rot_z(25.0), parent=head)
    b.box("focus_thumbscrew", (0.008, 0.008, 0.008), (0.018, -0.020, -0.010),
          b.mat("chrome"), parent=head)
    b.box("head_led_ring", (0.032, 0.004, 0.032), (0.0, -0.070, -0.010),
          b.mat("led_white"), parent=head)

    # air assist hose from the compressor at the back, following the gantry
    b.tube("air_hose",
           [[HEAD_X + 0.020, GANTRY_Y - 0.050, GANTRY_Z - 0.010],
            [HEAD_X + 0.060, GANTRY_Y + 0.020, GANTRY_Z + 0.020],
            [0.20, GANTRY_Y + 0.060, GANTRY_Z + 0.050],
            [0.45, GANTRY_Y + 0.040, HALF_D - 0.120],
            [0.52, 0.140, HALF_D - 0.070]],
           0.0055, b.mat("air_hose"), sides=8, parent=grp)
    b.tube("head_wiring",
           [[HEAD_X + 0.026, GANTRY_Y + 0.028, GANTRY_Z + 0.024],
            [0.10, GANTRY_Y + 0.052, GANTRY_Z + 0.048],
            [0.50, GANTRY_Y + 0.052, GANTRY_Z + 0.048]],
           0.0045, b.mat("wire_black"), sides=6, parent=grp)

    # --- the visible beam path and the red aiming dot ----------------------
    b.cylinder("beam_tube_to_m1", 0.0012, TUBE_LEN * 0.5 - 0.06,
               ((-HALF_W + 0.090 + (-HALF_W + 0.090 + TUBE_LEN * 0.5)) * 0.5, TUBE_Y,
                HALF_D - 0.090),
               b.mat("beam_red"), rot=rot_z(90.0), sides=6, parent=grp)
    b.cylinder("beam_m1_to_m2", 0.0012, TUBE_Y - GANTRY_Y,
               (-HALF_W + 0.090, (TUBE_Y + GANTRY_Y) * 0.5, HALF_D - 0.090),
               b.mat("beam_red"), sides=6, parent=grp)
    b.cylinder("beam_m2_carriage", 0.0012, HALF_D - 0.090 - GANTRY_Z,
               (-HALF_W + 0.090, GANTRY_Y, (HALF_D - 0.090 + GANTRY_Z) * 0.5),
               b.mat("beam_red"), rot=rot_x(90.0), sides=6, parent=grp)
    b.cylinder("beam_m2_to_m3", 0.0012, HEAD_X + HALF_W - 0.090,
               ((HEAD_X - HALF_W + 0.090) * 0.5, GANTRY_Y, GANTRY_Z),
               b.mat("beam_red"), rot=rot_z(90.0), sides=6, parent=grp)
    b.cylinder("beam_down_to_work", 0.0010, GANTRY_Y - BED_TOP - 0.020,
               (HEAD_X, (GANTRY_Y + BED_TOP + 0.020) * 0.5 - 0.010, GANTRY_Z - 0.010),
               b.mat("beam_red"), sides=6, parent=grp)
    b.cylinder("aiming_dot", 0.0045, 0.0008, (HEAD_X, BED_TOP + 0.001, GANTRY_Z - 0.010),
               b.mat("beam_red"), sides=16, parent=grp)
    b.sphere("aiming_dot_glow", 0.0075, (HEAD_X, BED_TOP + 0.002, GANTRY_Z - 0.010),
             b.mat("beam_red"), rings=6, sectors=12, parent=grp)


def _tube_and_cooling(b: SpecBuilder) -> None:
    glass = b.mat("glass_tube")
    plasma = b.mat("plasma")
    black = b.mat("black_anod")
    grp = b.group("laser_tube", at=(0.0, TUBE_Y, HALF_D - 0.090))

    b.cylinder("tube_envelope", 0.040, TUBE_LEN, (0.0, 0.0, 0.0), glass,
               rot=rot_z(90.0), sides=24, parent=grp)
    b.cylinder("tube_bore", 0.0075, TUBE_LEN - 0.10, (0.0, 0.0, 0.0), plasma,
               rot=rot_z(90.0), sides=16, parent=grp)
    for i in range(9):
        x = -TUBE_LEN * 0.5 + 0.12 + i * (TUBE_LEN - 0.24) / 8.0
        b.torus("tube_glow_ring", 0.0110, 0.0030, (x, 0.0, 0.0), plasma,
                rot=rot_z(90.0), sides=8, rings=16, parent=grp)
    for sx in (-1, 1):
        b.cylinder("tube_end_cap", 0.044, 0.030, (sx * (TUBE_LEN * 0.5 + 0.010), 0.0, 0.0),
                   black, rot=rot_z(90.0), sides=20, parent=grp)
        b.cylinder("tube_water_port", 0.008, 0.030,
                   (sx * (TUBE_LEN * 0.5 - 0.030), 0.030, 0.0), glass, sides=12, parent=grp)
        b.cylinder("tube_electrode", 0.010, 0.040, (sx * (TUBE_LEN * 0.5 - 0.010), 0.0, 0.0),
                   b.mat("copper"), rot=rot_z(90.0), sides=12, parent=grp)
    # output window and high voltage lead
    b.cylinder("tube_output_window", 0.012, 0.004, (-TUBE_LEN * 0.5 - 0.028, 0.0, 0.0),
               b.mat("lens_znse"), rot=rot_z(90.0), sides=18, parent=grp)
    b.tube("hv_lead",
           [[TUBE_LEN * 0.5 + 0.020, 0.0, 0.0], [TUBE_LEN * 0.5 + 0.060, -0.060, -0.020],
            [TUBE_LEN * 0.5 + 0.070, -0.220, -0.040]],
           0.005, b.mat("wire_black"), sides=8, parent=grp)

    # cradles
    for sx in (-1, 1):
        b.extrusion("tube_cradle", angle_profile(0.060, 0.070, 0.006), 0.020,
                    (sx * 0.400, -0.062, 0.0), b.mat("alu_bright"),
                    rot=rot_y(90.0), parent=grp)
        b.torus("tube_clamp", 0.043, 0.004, (sx * 0.400, 0.0, 0.0), black,
                rot=rot_z(90.0), sides=6, rings=20, parent=grp)

    # water cooling loop
    b.tube("coolant_in",
           [[-TUBE_LEN * 0.5 + 0.030, 0.045, 0.0], [-TUBE_LEN * 0.5 + 0.030, 0.110, 0.030],
            [-0.30, 0.130, 0.050], [-0.30, -0.240, 0.050]],
           0.007, b.mat("coolant_tube"), sides=8, parent=grp)
    b.tube("coolant_out",
           [[TUBE_LEN * 0.5 - 0.030, 0.045, 0.0], [TUBE_LEN * 0.5 - 0.030, 0.110, 0.030],
            [0.30, 0.130, 0.050], [0.30, -0.240, 0.050]],
           0.007, b.mat("coolant_tube"), sides=8, parent=grp)
    for k in range(6):
        b.torus("coolant_clip", 0.0085, 0.0016,
                (-0.30 + 0.60 * (k % 2), 0.060 - 0.060 * (k // 2), 0.050),
                b.mat("dark_plastic"), rot=rot_x(90.0), sides=6, rings=12, parent=grp)


def _exhaust(b: SpecBuilder) -> None:
    steel = b.mat("cabinet_steel")
    grey = b.mat("grey_plastic")
    grp = b.group("exhaust")

    # plenum spanning the back of the bed
    b.box("exhaust_plenum", (BED_W + 0.06, 0.110, 0.070),
          (0.0, BED_TOP + 0.035, HALF_D - 0.150), steel, parent=grp)
    for i in range(22):
        x = -BED_W * 0.5 + 0.02 + i * (BED_W - 0.04) / 21.0
        b.box("plenum_slot", (0.024, 0.070, 0.005), (x, BED_TOP + 0.035, HALF_D - 0.186),
              b.mat("soot"), parent=grp)
    b.box("plenum_taper", (0.200, 0.110, 0.060), (0.0, BED_TOP + 0.035, HALF_D - 0.100),
          steel, parent=grp)
    b.cylinder("exhaust_spigot", 0.048, 0.070, (0.0, BED_TOP + 0.035, HALF_D - 0.045),
               steel, rot=rot_x(90.0), sides=20, parent=grp)
    for k in range(5):
        b.torus("duct_rib", 0.052, 0.005, (0.0, BED_TOP + 0.035, HALF_D - 0.075 + k * 0.014),
                grey, rot=rot_x(90.0), sides=6, rings=18, parent=grp)
    b.box("extraction_fan", (0.140, 0.140, 0.090), (0.0, BED_TOP + 0.035, HALF_D - 0.020),
          b.mat("dark_plastic"), parent=grp)
    for k in range(7):
        a = 360.0 * k / 7.0
        b.box("extraction_blade", (0.056, 0.006, 0.048),
              (0.032 * math.cos(math.radians(a)),
               BED_TOP + 0.035 + 0.032 * math.sin(math.radians(a)), HALF_D - 0.020),
              grey, rot=rot_mul(rot_z(a), rot_x(30.0)), parent=grp)
    b.torus("fan_guard", 0.062, 0.004, (0.0, BED_TOP + 0.035, HALF_D - 0.062), grey,
            rot=rot_x(90.0), sides=6, rings=24, parent=grp)
    for k in range(8):
        b.box("fan_guard_bar", (0.120, 0.003, 0.003),
              (0.0, BED_TOP + 0.035, HALF_D - 0.062), grey,
              rot=rot_mul(rot_x(90.0), rot_y(22.5 * k)), parent=grp)


def _lid_and_panel(b: SpecBuilder) -> None:
    acrylic = b.mat("acrylic_tint")
    black = b.mat("black_anod")
    steel = b.mat("steel")
    grey = b.mat("grey_plastic")
    grp = b.group("lid_and_controls")

    # tinted acrylic lid, hinged at the back, standing slightly open
    lid = b.group("safety_lid", at=(0.0, CAB_H - 0.030, HALF_D - 0.040),
                  rot=rot_x(-8.0), parent=grp)
    b.box("lid_acrylic", (CAB_W - 0.10, 0.006, CAB_D - 0.14), (0.0, 0.0, -CAB_D * 0.5 + 0.07),
          acrylic, parent=lid)
    for sx in (-1, 1):
        b.box("lid_edge_x", (0.016, 0.016, CAB_D - 0.14),
              (sx * (CAB_W - 0.10) * 0.5, 0.0, -CAB_D * 0.5 + 0.07), black, parent=lid)
    for dz in (0.0, -(CAB_D - 0.14)):
        b.box("lid_edge_z", (CAB_W - 0.07, 0.016, 0.016), (0.0, 0.0, dz), black, parent=lid)
    for sx in (-1, 1):
        b.cylinder("lid_hinge", 0.010, 0.040, (sx * 0.30, 0.0, 0.0), steel,
                   rot=rot_z(90.0), sides=14, parent=lid)
        b.cylinder("lid_gas_strut", 0.008, 0.180, (sx * 0.52, -0.060, -0.120),
                   b.mat("chrome"), rot=rot_x(40.0), sides=12, parent=lid)
    b.box("lid_handle", (0.180, 0.020, 0.026), (0.0, -0.008, -CAB_D + 0.135), black, parent=lid)

    # interlock switch and its striker
    b.box("interlock_switch", (0.024, 0.030, 0.016), (-0.42, CAB_H - 0.060, HALF_D - 0.100),
          grey, parent=grp)
    b.box("interlock_striker", (0.008, 0.020, 0.008), (-0.42, CAB_H - 0.038, HALF_D - 0.100),
          b.mat("chrome"), parent=grp)
    b.tube("interlock_wiring",
           [[-0.42, CAB_H - 0.075, HALF_D - 0.100], [-0.46, CAB_H - 0.180, HALF_D - 0.060],
            [-0.60, CAB_H - 0.260, HALF_D - 0.060]],
           0.003, b.mat("wire_black"), sides=6, parent=grp)

    # control panel on the front right
    panel = b.group("control_panel", at=(HALF_W - 0.150, 0.056, -HALF_D + 0.010), parent=grp)
    b.box("panel_face", (0.220, 0.090, 0.006), (0.0, 0.0, 0.0), grey, rot=rot_x(18.0),
          parent=panel)
    b.box("panel_display", (0.090, 0.044, 0.003), (-0.055, 0.010, -0.006),
          b.mat("led_white"), rot=rot_x(18.0), parent=panel)
    b.text("panel_text", "READY\n1300X900", 0.008, 0.001, (-0.055, 0.012, -0.009),
           b.mat("label_text"), rot=rot_x(18.0), parent=panel)
    for i in range(4):
        for j in range(2):
            b.cylinder("panel_button", 0.008, 0.005,
                       (0.020 + i * 0.022, 0.022 - j * 0.030, -0.006),
                       b.mat("white_plastic") if (i + j) % 2 else b.mat("dark_plastic"),
                       rot=rot_mul(rot_x(18.0), rot_x(90.0)), sides=12, parent=panel)
    b.cylinder("jog_dial", 0.016, 0.010, (0.096, 0.006, -0.008), b.mat("dark_plastic"),
               rot=rot_mul(rot_x(18.0), rot_x(90.0)), sides=18, parent=panel)
    b.cylinder("estop_base", 0.020, 0.010, (0.096, -0.032, -0.008), b.mat("label_yellow"),
               rot=rot_mul(rot_x(18.0), rot_x(90.0)), sides=18, parent=panel)
    b.cylinder("estop_button", 0.017, 0.012, (0.096, -0.034, -0.014), b.mat("label_red"),
               rot=rot_mul(rot_x(18.0), rot_x(90.0)), sides=18, parent=panel)
    b.cylinder("key_switch", 0.008, 0.008, (-0.100, -0.026, -0.006), b.mat("chrome"),
               rot=rot_mul(rot_x(18.0), rot_x(90.0)), sides=12, parent=panel)
    b.box("panel_pcb", (0.200, 0.070, 0.002), (0.0, 0.0, 0.010), b.mat("pcb_green"),
          rot=rot_x(18.0), parent=panel)


def _extras(b: SpecBuilder) -> None:
    grey = b.mat("grey_plastic")
    black = b.mat("black_anod")
    chrome = b.mat("chrome")
    grp = b.group("extras")

    # rotary attachment, stowed along the right wall
    rot_att = b.group("rotary_attachment", at=(HALF_W - 0.090, 0.070, 0.150), parent=grp)
    b.box("rotary_base", (0.070, 0.024, 0.340), (0.0, 0.0, 0.0), b.mat("alu_bright"),
          parent=rot_att)
    b.box("rotary_headstock", (0.070, 0.070, 0.060), (0.0, 0.046, -0.140), black,
          parent=rot_att)
    b.box("rotary_tailstock", (0.060, 0.060, 0.040), (0.0, 0.040, 0.140), black,
          parent=rot_att)
    for k in range(4):
        b.cylinder("rotary_roller", 0.020, 0.050, (0.0, 0.030, -0.060 + k * 0.055),
                   grey, rot=rot_z(90.0), sides=16, parent=rot_att)
    b.cylinder("rotary_motor", 0.024, 0.060, (0.0, 0.046, -0.190), black,
               rot=rot_x(90.0), sides=16, parent=rot_att)
    b.tube("rotary_cable",
           [[0.0, 0.046, -0.220], [-0.030, 0.020, -0.260], [-0.060, 0.010, -0.300]],
           0.005, b.mat("wire_black"), sides=6, parent=rot_att)

    # spare lens tin and allen keys on the apron
    b.cylinder("lens_tin", 0.024, 0.012, (-0.520, 0.070, -HALF_D + 0.070), chrome,
               sides=16, parent=grp)
    for k in range(3):
        b.box("allen_key", (0.004, 0.004, 0.070), (-0.470 + k * 0.012, 0.062, -HALF_D + 0.080),
              chrome, rot=rot_y(6.0 * k), parent=grp)

    # LED strips lighting the bed, along both long edges of the lid frame
    for sz in (-1, 1):
        b.box("bed_led_strip", (BED_W, 0.008, 0.012),
              (0.0, CAB_H - 0.055, sz * (BED_D * 0.5 + 0.060)), b.mat("led_white"),
              parent=grp)
        for i in range(20):
            x = -BED_W * 0.5 + 0.02 + i * (BED_W - 0.04) / 19.0
            b.box("bed_led_chip", (0.008, 0.003, 0.008),
                  (x, CAB_H - 0.060, sz * (BED_D * 0.5 + 0.060)), b.mat("led_white"),
                  parent=grp)

    # warning labels
    b.box("label_class4_plate", (0.180, 0.070, 0.002), (-0.400, 0.300, HALF_D - 0.006),
          b.mat("label_yellow"), rot=rot_y(180.0), parent=grp)
    b.text("label_class4", "DANGER\nCLASS 4 LASER\nINVISIBLE RADIATION", 0.011, 0.0015,
           (-0.400, 0.300, HALF_D - 0.009), b.mat("label_text"), rot=rot_y(180.0),
           parent=grp)
    b.box("label_hv_plate", (0.120, 0.050, 0.002), (0.560, 0.300, HALF_D - 0.006),
          b.mat("label_red"), rot=rot_y(180.0), parent=grp)
    b.text("label_hv", "HIGH VOLTAGE\n20KV", 0.010, 0.0015,
           (0.560, 0.300, HALF_D - 0.009), b.mat("label_text"), rot=rot_y(180.0), parent=grp)
    b.text("label_eyewear", "WEAR EYE PROTECTION", 0.009, 0.0012,
           (0.0, 0.062, -HALF_D + 0.006), b.mat("label_text"), parent=grp)
    b.text("label_bed", "1300 X 900", 0.014, 0.0012,
           (-0.480, BED_TOP + 0.0015, -0.380), b.mat("label_text"), rot=PLATE_ROT, parent=grp)
    b.text("label_extract", "EXTRACTION", 0.010, 0.0012,
           (0.300, BED_TOP + 0.120, HALF_D - 0.152), b.mat("label_text"),
           rot=rot_y(180.0), parent=grp)

    # scattered fixing screws around the cabinet
    for i in range(20):
        a = 2.0 * math.pi * i / 20.0
        b.cylinder("cabinet_screw", 0.004, 0.003,
                   ((HALF_W - 0.02) * math.cos(a), 0.030 + 0.040 * (i % 4),
                    (HALF_D - 0.02) * math.sin(a)), b.mat("steel"), sides=6, parent=grp)


def _lighting(b: SpecBuilder) -> None:
    # LED strips along the lid frame
    b.spot((0.0, CAB_H - 0.060, -BED_D * 0.5 - 0.060), (0.0, -0.95, 0.32),
           color=(1.0, 0.98, 0.94), intensity=1.2, cutoff_deg=72.0, rng=1.4)
    b.spot((0.0, CAB_H - 0.060, BED_D * 0.5 + 0.060), (0.0, -0.95, -0.32),
           color=(1.0, 0.98, 0.94), intensity=1.0, cutoff_deg=72.0, rng=1.4)
    # cool ambient bounce inside the painted cabinet
    b.point((0.0, CAB_H * 0.55, 0.0), color=(0.50, 0.54, 0.62), intensity=0.55, rng=2.0)
    b.directional((0.25, -0.86, -0.45), color=(0.72, 0.74, 0.78), intensity=0.45)
    b.directional((-0.40, -0.30, 0.86), color=(0.34, 0.38, 0.50), intensity=0.26)
    # the plasma inside the tube, and the cutting point
    b.point((0.0, TUBE_Y, HALF_D - 0.090), color=(0.70, 0.30, 1.0), intensity=0.60, rng=1.2)
    b.point((HEAD_X, BED_TOP + 0.010, GANTRY_Z - 0.010), color=(1.0, 0.18, 0.06),
            intensity=0.55, rng=0.35)


# ---------------------------------------------------------------------------


def build() -> Dict[str, Any]:
    """Generate the CO2 laser cutter environment spec."""
    b = SpecBuilder(
        ENVIRONMENT_ID,
        ENVIRONMENT_NAME,
        ENVIRONMENT_DESCRIPTION,
        user_scale=USER_SCALE,
        bounds=(CAB_W, CAB_D, CAB_H),
        spawn=(-0.20, BED_TOP, -0.180),
        ambient=(0.045, 0.048, 0.058),
    )
    _materials(b)
    _lighting(b)
    b.anchor("bed_surface", (0.0, BED_TOP, 0.0), (BED_W, BED_D))
    b.anchor("cabinet_centre", (0.0, CAB_H * 0.5, 0.0), (CAB_W, CAB_D))

    _cabinet(b)
    _bed(b)
    _motion(b)
    _head_and_optics(b)
    _tube_and_cooling(b)
    _exhaust(b)
    _lid_and_panel(b)
    _extras(b)
    return b.build()
