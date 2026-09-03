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
"""Interior of a Bambu Lab X1 Carbon style CoreXY printer.

The user is shrunk to about a sixth of a metre tall and stands on the PEI
build plate while modelling.  Everything is dimensioned for a 256 x 256 x 256
mm build volume in a 350 mm cube chamber, so the machine reads correctly at
human eye height once ``user_scale`` is applied.

Frame of reference (spec convention: metres, Y up, right handed):

* origin at the centre of the chamber floor
* ``-Z`` is the front (glass door), ``+Z`` the back panel
* ``+Y`` is up; the build plate surface sits at ``y = BED_TOP``
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

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

ENVIRONMENT_ID = "bambu_x1c"
ENVIRONMENT_NAME = "Bambu Lab X1 Carbon (chamber interior)"
ENVIRONMENT_DESCRIPTION = (
    "Stand on the PEI build plate of a CoreXY printer while you model. "
    "Aluminium frame, tempered glass, CoreXY gantry, full toolhead, AMS "
    "spool feeder and chamber lighting, at 1:11 miniature scale."
)

# --- principal dimensions --------------------------------------------------

CHAMBER_W = 0.350          # X, interior width
CHAMBER_D = 0.350          # Z, interior depth
CHAMBER_H = 0.420          # Y, interior height

HALF_W = CHAMBER_W / 2.0
HALF_D = CHAMBER_D / 2.0

EXTRUSION = 0.020          # 2020 frame profile
FRAME_X = HALF_W - EXTRUSION / 2.0 - 0.005   # 0.16
FRAME_Z = HALF_D - EXTRUSION / 2.0 - 0.005

BED_TOP = 0.054            # PEI surface height
PLATE = 0.256              # build plate edge length
BED_PLATE = 0.270          # heated bed edge length

GANTRY_Y = 0.362           # CoreXY plane
RAIL_Y = GANTRY_Y - 0.014

TOOLHEAD_X = 0.030         # parked toolhead position
TOOLHEAD_Z = 0.040

USER_SCALE = 11.0          # 1.65 m eye height reads as 0.15 m inside


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------


def _materials(b: SpecBuilder) -> None:
    b.material("anodised_alu", srgb(0.30, 0.31, 0.33), metallic=0.88, roughness=0.34)
    b.material("alu_bright", srgb(0.72, 0.73, 0.75), metallic=0.92, roughness=0.22)
    b.material("sheet_steel", srgb(0.20, 0.21, 0.23), metallic=0.85, roughness=0.42)
    b.material("steel", srgb(0.55, 0.56, 0.58), metallic=0.92, roughness=0.30)
    b.material("chrome", srgb(0.85, 0.86, 0.88), metallic=1.0, roughness=0.07)
    b.material("brass", srgb(0.78, 0.62, 0.28), metallic=0.95, roughness=0.24)
    b.material("copper", srgb(0.76, 0.42, 0.24), metallic=0.95, roughness=0.28)
    b.material("black_plastic", srgb(0.07, 0.07, 0.08), roughness=0.55)
    b.material("dark_plastic", srgb(0.13, 0.13, 0.14), roughness=0.50)
    b.material("grey_plastic", srgb(0.42, 0.43, 0.45), roughness=0.60)
    b.material("white_plastic", srgb(0.90, 0.90, 0.89), roughness=0.48)
    b.material("glass_front", srgb(0.72, 0.80, 0.78, 0.14), metallic=0.0, roughness=0.03)
    b.material("glass_top", srgb(0.78, 0.84, 0.86, 0.11), metallic=0.0, roughness=0.03)
    b.material("pei_plate", srgb(0.36, 0.33, 0.28), metallic=0.35, roughness=0.86,
               texture="checker")
    b.material("bed_alu", srgb(0.24, 0.25, 0.27), metallic=0.70, roughness=0.55)
    b.material("silicone_heater", srgb(0.55, 0.13, 0.10), roughness=0.70)
    b.material("pcb_green", srgb(0.06, 0.28, 0.14), roughness=0.42)
    b.material("belt_rubber", srgb(0.05, 0.05, 0.06), roughness=0.80)
    b.material("carbon_fibre", srgb(0.10, 0.10, 0.11), metallic=0.25, roughness=0.30,
               texture="checker")
    b.material("ptfe", srgb(0.94, 0.94, 0.92), roughness=0.35)
    b.material("silicone_sock", srgb(0.75, 0.16, 0.13), roughness=0.72)
    b.material("wire_black", srgb(0.05, 0.05, 0.05), roughness=0.65)
    b.material("wire_red", srgb(0.62, 0.09, 0.08), roughness=0.65)
    b.material("wire_white", srgb(0.85, 0.85, 0.85), roughness=0.65)
    b.material("led_bar", srgb(1.0, 0.97, 0.90), roughness=0.25,
               emissive=[1.0, 0.95, 0.86])
    b.material("hot_metal", srgb(0.30, 0.12, 0.06), metallic=0.6, roughness=0.55,
               emissive=[0.55, 0.14, 0.03])
    b.material("lidar_glow", srgb(0.10, 0.45, 0.30), roughness=0.25,
               emissive=[0.05, 0.55, 0.28])
    b.material("label_yellow", srgb(0.92, 0.76, 0.06), roughness=0.55)
    b.material("label_text", srgb(0.04, 0.04, 0.04), roughness=0.60)
    b.material("filament_a", srgb(0.85, 0.18, 0.16), roughness=0.55)
    b.material("filament_b", srgb(0.12, 0.42, 0.82), roughness=0.55)
    b.material("filament_c", srgb(0.18, 0.66, 0.30), roughness=0.55)
    b.material("filament_d", srgb(0.94, 0.94, 0.94), roughness=0.55)
    b.material("rubber_foot", srgb(0.04, 0.04, 0.05), roughness=0.90)


# ---------------------------------------------------------------------------
# small reusable assemblies
# ---------------------------------------------------------------------------


def _screw_ring(b: SpecBuilder, name: str, positions: Sequence[Sequence[float]],
                radius: float, mat: int, rot=IDENT, parent=None) -> None:
    for p in positions:
        b.cylinder(name, radius, radius * 0.9, p, mat, rot=rot, sides=6, parent=parent)


def _nema17(b: SpecBuilder, name: str, at: Sequence[float], mat_body: int, mat_shaft: int,
            mat_wire: int, shaft_rot=IDENT, size: float = 0.042,
            length: float = 0.040, parent=None) -> Dict[str, Any]:
    """Stepper motor with cooling fins, shaft, boss and a connector pigtail."""
    grp = b.group(name, at=at, rot=shaft_rot, parent=parent)
    b.box(name + "_body", (size, length, size), (0, 0, 0), mat_body, parent=grp)
    # stacked laminations
    for i in range(6):
        y = -length * 0.5 + length * (0.18 + 0.13 * i)
        b.box(name + "_lam", (size * 1.02, 0.0022, size * 1.02), (0, y, 0),
              mat_body, parent=grp)
    b.cylinder(name + "_boss", 0.011, 0.004, (0, length * 0.5 + 0.002, 0),
               b.mat("alu_bright"), sides=16, parent=grp)
    b.cylinder(name + "_shaft", 0.0025, 0.024, (0, length * 0.5 + 0.014, 0),
               mat_shaft, sides=12, parent=grp)
    # mounting screws on the front face
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.cylinder(name + "_screw", 0.0022, 0.003,
                       (sx * 0.0155, length * 0.5 + 0.0015, sz * 0.0155),
                       b.mat("steel"), sides=6, parent=grp)
    b.box(name + "_conn", (0.012, 0.005, 0.008), (0, -length * 0.5 - 0.002, 0.008),
          b.mat("white_plastic"), parent=grp)
    b.tube(name + "_pigtail",
           [[0, -length * 0.5 - 0.004, 0.008], [0, -length * 0.5 - 0.020, 0.020],
            [0.02, -length * 0.5 - 0.030, 0.040]],
           0.0018, mat_wire, sides=6, parent=grp)
    return grp


def _belt_run(b: SpecBuilder, name: str, axis: str, a: float, bnd: float,
              cross: Tuple[float, float], width: float, mat_belt: int,
              mat_tooth: int, pitch: float = 0.008, parent=None) -> int:
    """A straight belt run with visible teeth.

    ``axis`` is ``"x"`` or ``"z"``; ``cross`` gives the two other coordinates
    (``(y, z)`` for an X run, ``(x, y)`` for a Z run).
    """
    length = abs(bnd - a)
    mid = (a + bnd) * 0.5
    thickness = 0.0014
    tooth = 0.0009
    if axis == "x":
        y, z = cross
        b.box(name, (length, thickness, width), (mid, y, z), mat_belt, parent=parent)
    else:
        x, y = cross
        b.box(name, (thickness, width, length), (x, y, mid), mat_belt, parent=parent)
    n = 0
    count = max(2, int(length / pitch))
    for i in range(count):
        t = a + (bnd - a) * (i + 0.5) / count
        if axis == "x":
            y, z = cross
            b.box(name + "_tooth", (pitch * 0.55, tooth, width * 0.92),
                  (t, y - thickness * 0.5 - tooth * 0.5, z), mat_tooth, parent=parent)
        else:
            x, y = cross
            b.box(name + "_tooth", (tooth, width * 0.92, pitch * 0.55),
                  (x - thickness * 0.5 - tooth * 0.5, y, t), mat_tooth, parent=parent)
        n += 1
    return n + 1


def _linear_rail(b: SpecBuilder, name: str, axis: str, a: float, bnd: float,
                 cross: Tuple[float, float], mat_rail: int, mat_car: int,
                 carriages: Sequence[float] = (), parent=None) -> None:
    """An MGN12 style profile rail with carriages."""
    length = abs(bnd - a)
    mid = (a + bnd) * 0.5
    rw, rh = 0.012, 0.008
    if axis == "x":
        y, z = cross
        b.box(name, (length, rh, rw), (mid, y, z), mat_rail, parent=parent)
        b.box(name + "_groove", (length, 0.002, rw * 1.06), (mid, y, z), mat_car, parent=parent)
        nb = max(2, int(length / 0.05))
        for i in range(nb):
            t = a + (bnd - a) * (i + 0.5) / nb
            b.cylinder(name + "_bolt", 0.0022, 0.002, (t, y + rh * 0.5, z),
                       b.mat("steel"), sides=6, parent=parent)
        for c in carriages:
            b.box(name + "_carriage", (0.030, 0.011, 0.024), (c, y + 0.001, z),
                  mat_car, parent=parent)
            b.box(name + "_car_top", (0.024, 0.003, 0.026), (c, y + 0.008, z),
                  mat_car, parent=parent)
    else:
        x, y = cross
        b.box(name, (rw, rh, length), (x, y, mid), mat_rail, parent=parent)
        b.box(name + "_groove", (rw * 1.06, 0.002, length), (x, y, mid), mat_car, parent=parent)
        nb = max(2, int(length / 0.05))
        for i in range(nb):
            t = a + (bnd - a) * (i + 0.5) / nb
            b.cylinder(name + "_bolt", 0.0022, 0.002, (x, y + rh * 0.5, t),
                       b.mat("steel"), sides=6, parent=parent)
        for c in carriages:
            b.box(name + "_carriage", (0.024, 0.011, 0.030), (x, y + 0.001, c),
                  mat_car, parent=parent)
            b.box(name + "_car_top", (0.026, 0.003, 0.024), (x, y + 0.008, c),
                  mat_car, parent=parent)


# ---------------------------------------------------------------------------
# sub assemblies
# ---------------------------------------------------------------------------


def _frame(b: SpecBuilder) -> None:
    alu = b.mat("anodised_alu")
    steel = b.mat("steel")
    grp = b.group("frame")
    prof = slot_profile(EXTRUSION)

    # four vertical corner extrusions (profile lies in XY, extruded along Z,
    # so a -90 deg rotation about X stands them upright)
    stand = rot_x(-90.0)
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.extrusion("frame_column", prof, CHAMBER_H - 0.02,
                        (sx * FRAME_X, CHAMBER_H / 2.0, sz * FRAME_Z), alu,
                        rot=stand, parent=grp)

    # horizontal rails, top and bottom, front-back and left-right
    lie_x = rot_y(90.0)
    for y in (0.012, CHAMBER_H - 0.012):
        for sz in (-1, 1):
            b.extrusion("frame_rail_x", prof, 2 * FRAME_X - EXTRUSION,
                        (0.0, y, sz * FRAME_Z), alu, rot=lie_x, parent=grp)
        for sx in (-1, 1):
            b.extrusion("frame_rail_z", prof, 2 * FRAME_Z - EXTRUSION,
                        (sx * FRAME_X, y, 0.0), alu, parent=grp)

    # mid height stiffener across the back
    b.extrusion("frame_stiffener", prof, 2 * FRAME_X - EXTRUSION,
                (0.0, 0.20, FRAME_Z), alu, rot=lie_x, parent=grp)

    # cast corner gussets and their bolts
    gus = angle_profile(0.026, 0.026, 0.004)
    for sx in (-1, 1):
        for sz in (-1, 1):
            for y, flip in ((0.028, 1.0), (CHAMBER_H - 0.028, -1.0)):
                rz = 0.0 if sx > 0 else 180.0
                b.extrusion("frame_gusset", gus, 0.016,
                            (sx * (FRAME_X - 0.012), y, sz * (FRAME_Z - 0.004)),
                            b.mat("alu_bright"),
                            rot=rot_mul(rot_z(rz), rot_x(0.0 if flip > 0 else 180.0)),
                            parent=grp)
                for k in range(3):
                    b.cylinder("frame_bolt", 0.0025, 0.004,
                               (sx * (FRAME_X - 0.006 - 0.008 * (k % 2)),
                                y + flip * 0.008,
                                sz * (FRAME_Z - 0.010) + 0.006 * (k - 1)),
                               steel, sides=6, parent=grp)

    # rubber feet under the machine
    for sx in (-1, 1):
        for sz in (-1, 1):
            b.cylinder("machine_foot", 0.011, 0.008,
                       (sx * (FRAME_X - 0.01), -0.004, sz * (FRAME_Z - 0.01)),
                       b.mat("rubber_foot"), sides=12, parent=grp)


def _panels(b: SpecBuilder) -> None:
    sheet = b.mat("sheet_steel")
    dark = b.mat("dark_plastic")
    grey = b.mat("grey_plastic")
    grp = b.group("panels")
    t = 0.0014

    # left / right sheet metal panels with a folded return flange
    for sx in (-1, 1):
        b.box("side_panel", (t, CHAMBER_H - 0.02, CHAMBER_D - 0.012),
              (sx * (HALF_W - t), CHAMBER_H / 2.0, 0.0), sheet, parent=grp)
        for sz in (-1, 1):
            b.box("side_panel_bend", (0.012, CHAMBER_H - 0.02, t),
                  (sx * (HALF_W - 0.007), CHAMBER_H / 2.0, sz * (HALF_D - 0.008)),
                  sheet, parent=grp)
        for sy in (0.016, CHAMBER_H - 0.016):
            b.box("side_panel_bend", (0.010, t, CHAMBER_D - 0.012),
                  (sx * (HALF_W - 0.006), sy, 0.0), sheet, parent=grp)
        # rivet line
        for i in range(9):
            z = -HALF_D + 0.03 + i * (CHAMBER_D - 0.06) / 8.0
            for y in (0.030, CHAMBER_H - 0.030):
                b.cylinder("panel_rivet", 0.0018, 0.0016,
                           (sx * (HALF_W - t - 0.0008), y, z), b.mat("alu_bright"),
                           rot=rot_z(90.0), sides=8, parent=grp)

    # back panel with pressed vent louvres
    b.box("back_panel", (CHAMBER_W - 0.012, CHAMBER_H - 0.02, t),
          (0.0, CHAMBER_H / 2.0, HALF_D - t), sheet, parent=grp)
    for i in range(12):
        y = 0.10 + i * 0.016
        b.box("back_louvre", (0.140, 0.006, 0.004), (0.06, y, HALF_D - 0.004),
              dark, rot=rot_x(18.0), parent=grp)
    for i in range(10):
        z_ang = i * 36.0
        b.box("back_grill_bar", (0.062, 0.0022, 0.0022),
              (-0.075, 0.150, HALF_D - 0.005), grey, rot=rot_z(z_ang), parent=grp)
    b.torus("back_grill_ring", 0.032, 0.0022, (-0.075, 0.150, HALF_D - 0.005),
            grey, rot=rot_x(90.0), sides=6, rings=24, parent=grp)

    # floor pan and its stiffening ribs
    b.box("floor_pan", (CHAMBER_W - 0.012, 0.003, CHAMBER_D - 0.012),
          (0.0, 0.0015, 0.0), sheet, parent=grp)
    for i in range(5):
        x = -0.12 + i * 0.06
        b.box("floor_rib", (0.010, 0.006, CHAMBER_D - 0.04), (x, 0.006, 0.0),
              sheet, parent=grp)

    # ceiling frame the top glass drops into
    for sx in (-1, 1):
        b.box("lid_frame_x", (0.014, 0.008, CHAMBER_D - 0.02),
              (sx * (HALF_W - 0.012), CHAMBER_H - 0.006, 0.0), dark, parent=grp)
    for sz in (-1, 1):
        b.box("lid_frame_z", (CHAMBER_W - 0.02, 0.008, 0.014),
              (0.0, CHAMBER_H - 0.006, sz * (HALF_D - 0.012)), dark, parent=grp)


def _door_and_lid(b: SpecBuilder) -> None:
    glass_f = b.mat("glass_front")
    glass_t = b.mat("glass_top")
    dark = b.mat("dark_plastic")
    steel = b.mat("steel")
    chrome = b.mat("chrome")
    grp = b.group("enclosure_glazing")

    # front tempered glass door
    door = b.group("front_door", at=(0.0, 0.0, -HALF_D + 0.004), parent=grp)
    b.box("door_glass", (0.300, 0.330, 0.004), (0.0, 0.190, 0.0), glass_f, parent=door)
    b.box("door_frame_top", (0.312, 0.010, 0.008), (0.0, 0.360, 0.0), dark, parent=door)
    b.box("door_frame_bottom", (0.312, 0.010, 0.008), (0.0, 0.020, 0.0), dark, parent=door)
    for sx in (-1, 1):
        b.box("door_frame_side", (0.010, 0.350, 0.008), (sx * 0.151, 0.190, 0.0),
              dark, parent=door)
    # hinges on the left edge
    for y in (0.055, 0.190, 0.325):
        b.cylinder("door_hinge_barrel", 0.0055, 0.020, (-0.152, y, 0.006), steel,
                   sides=12, parent=door)
        b.box("door_hinge_leaf", (0.020, 0.014, 0.003), (-0.142, y, 0.010),
              steel, parent=door)
        b.cylinder("door_hinge_pin", 0.0018, 0.024, (-0.152, y, 0.006), chrome,
                   sides=8, parent=door)
    # handle and magnetic catch
    b.box("door_handle", (0.012, 0.070, 0.016), (0.140, 0.200, -0.010), chrome, parent=door)
    b.cylinder("door_handle_post", 0.0045, 0.012, (0.140, 0.234, -0.004), chrome,
               rot=rot_x(90.0), sides=10, parent=door)
    b.cylinder("door_handle_post", 0.0045, 0.012, (0.140, 0.166, -0.004), chrome,
               rot=rot_x(90.0), sides=10, parent=door)
    for y in (0.100, 0.280):
        b.box("door_magnet", (0.010, 0.016, 0.005), (0.146, y, 0.006), steel, parent=door)
    # perimeter gasket
    b.tube("door_gasket",
           [[-0.150, 0.022, 0.004], [0.150, 0.022, 0.004], [0.150, 0.358, 0.004],
            [-0.150, 0.358, 0.004], [-0.150, 0.022, 0.004]],
           0.0022, b.mat("black_plastic"), sides=6, parent=door)

    # top glass lid
    lid = b.group("top_lid", at=(0.0, CHAMBER_H - 0.012, 0.0), parent=grp)
    b.box("lid_glass", (0.300, 0.004, 0.300), (0.0, 0.0, 0.0), glass_t, parent=lid)
    for sx in (-1, 1):
        b.box("lid_edge_x", (0.008, 0.008, 0.308), (sx * 0.152, 0.0, 0.0), dark, parent=lid)
    for sz in (-1, 1):
        b.box("lid_edge_z", (0.308, 0.008, 0.008), (0.0, 0.0, sz * 0.152), dark, parent=lid)
    for sx in (-1, 1):
        b.cylinder("lid_hinge", 0.004, 0.016, (sx * 0.090, 0.004, 0.150), steel,
                   rot=rot_z(90.0), sides=10, parent=lid)
    b.box("lid_handle", (0.060, 0.008, 0.012), (0.0, 0.004, -0.146), dark, parent=lid)


def _bed(b: SpecBuilder) -> None:
    bed_alu = b.mat("bed_alu")
    pei = b.mat("pei_plate")
    steel = b.mat("steel")
    chrome = b.mat("chrome")
    grp = b.group("bed_assembly")

    plate_y = BED_TOP - 0.0004
    b.box("build_plate_pei", (PLATE, 0.0008, PLATE), (0.0, plate_y, 0.0), pei, parent=grp)
    b.plane("build_plate_texture", (PLATE - 0.002, PLATE - 0.002),
            (0.0, BED_TOP + 0.0002, 0.0), pei, subdiv=(8, 8), parent=grp)
    # magnetic sheet and heated aluminium bed
    b.box("magnetic_sheet", (PLATE + 0.002, 0.0010, PLATE + 0.002),
          (0.0, plate_y - 0.0009, 0.0), b.mat("black_plastic"), parent=grp)
    b.box("heated_bed", (BED_PLATE, 0.0060, BED_PLATE),
          (0.0, plate_y - 0.0044, 0.0), bed_alu, parent=grp)
    b.box("bed_heater_pad", (BED_PLATE - 0.010, 0.0012, BED_PLATE - 0.010),
          (0.0, plate_y - 0.0080, 0.0), b.mat("silicone_heater"), parent=grp)
    # serpentine heater trace visible through the silicone
    trace: List[List[float]] = []
    y_tr = plate_y - 0.0086
    for i in range(9):
        x = -0.115 + i * 0.0288
        z0, z1 = (-0.115, 0.115) if i % 2 == 0 else (0.115, -0.115)
        trace.append([x, y_tr, z0])
        trace.append([x, y_tr, z1])
    b.tube("bed_heater_trace", trace, 0.0012, b.mat("copper"), sides=6, parent=grp)
    b.box("bed_thermistor", (0.006, 0.003, 0.004), (0.0, plate_y - 0.0090, -0.02),
          b.mat("white_plastic"), parent=grp)

    # bed carrier frame underneath
    for sx in (-1, 1):
        b.extrusion("bed_carrier_x", channel_profile(0.018, 0.012, 0.002), BED_PLATE - 0.01,
                    (sx * 0.115, plate_y - 0.017, 0.0), bed_alu, rot=rot_mul(rot_x(180.0), IDENT),
                    parent=grp)
    for sz in (-1, 1):
        b.extrusion("bed_carrier_z", channel_profile(0.018, 0.012, 0.002), BED_PLATE - 0.01,
                    (0.0, plate_y - 0.017, sz * 0.115), bed_alu,
                    rot=rot_mul(rot_y(90.0), rot_x(180.0)), parent=grp)

    # levelling mounts: post, spring, thumb wheel
    for sx in (-1, 1):
        for sz in (-1, 1):
            px, pz = sx * 0.105, sz * 0.105
            b.cylinder("bed_mount_post", 0.0035, 0.020, (px, plate_y - 0.020, pz),
                       steel, sides=10, parent=grp)
            for k in range(6):
                b.torus("bed_spring_coil", 0.0055, 0.0009,
                        (px, plate_y - 0.028 + k * 0.0022, pz), chrome,
                        rot=rot_x(90.0), sides=6, rings=12, parent=grp)
            b.cylinder("bed_thumbwheel", 0.0075, 0.004, (px, plate_y - 0.033, pz),
                       b.mat("grey_plastic"), sides=12, parent=grp)
            b.cylinder("bed_mount_washer", 0.0060, 0.0012, (px, plate_y - 0.0125, pz),
                       chrome, sides=10, parent=grp)

    # wiring harness running to the back of the machine
    b.tube("bed_harness",
           [[0.0, plate_y - 0.012, 0.10], [0.0, plate_y - 0.030, 0.14],
            [0.02, plate_y - 0.038, HALF_D - 0.03], [0.06, 0.020, HALF_D - 0.02]],
           0.0035, b.mat("wire_black"), sides=8, parent=grp)
    b.tube("bed_harness_power",
           [[-0.01, plate_y - 0.012, 0.10], [-0.01, plate_y - 0.032, 0.15],
            [0.01, plate_y - 0.040, HALF_D - 0.03]],
           0.0022, b.mat("wire_red"), sides=6, parent=grp)
    b.box("bed_connector", (0.016, 0.008, 0.010), (0.0, plate_y - 0.012, 0.115),
          b.mat("white_plastic"), parent=grp)


def _z_axis(b: SpecBuilder) -> None:
    steel = b.mat("steel")
    chrome = b.mat("chrome")
    brass = b.mat("brass")
    alu = b.mat("anodised_alu")
    grp = b.group("z_axis")

    for sx in (-1, 1):
        x = sx * (FRAME_X - 0.016)
        z = FRAME_Z - 0.020
        # lead screw with a modelled thread helix
        b.cylinder("z_leadscrew", 0.0040, 0.320, (x, 0.170, z), chrome, sides=14, parent=grp)
        helix: List[List[float]] = []
        turns, per_turn = 26, 6
        for k in range(turns * per_turn + 1):
            a = 2.0 * math.pi * k / per_turn
            helix.append([x + 0.0042 * math.cos(a), 0.030 + 0.0080 * k / per_turn,
                          z + 0.0042 * math.sin(a)])
        b.tube("z_leadscrew_thread", helix, 0.0009, chrome, sides=5, parent=grp)
        # anti backlash nut block riding the screw
        b.box("z_nut_block", (0.020, 0.014, 0.020), (x, BED_TOP - 0.030, z), alu, parent=grp)
        b.cylinder("z_nut_brass", 0.0072, 0.016, (x, BED_TOP - 0.030, z), brass,
                   sides=14, parent=grp)
        b.cylinder("z_nut_flange", 0.0110, 0.0025, (x, BED_TOP - 0.020, z), brass,
                   sides=14, parent=grp)
        for k in range(3):
            b.torus("z_antibacklash_spring", 0.0092, 0.0007,
                    (x, BED_TOP - 0.0245 + k * 0.0018, z), chrome,
                    rot=rot_x(90.0), sides=6, rings=12, parent=grp)
        # coupler and motor at the bottom
        b.cylinder("z_coupler", 0.0080, 0.020, (x, 0.020, z), b.mat("alu_bright"),
                   sides=14, parent=grp)
        for k in range(6):
            b.box("z_coupler_slot", (0.018, 0.0012, 0.004), (x, 0.014 + k * 0.0022, z),
                  b.mat("dark_plastic"), rot=rot_y(30.0 * k), parent=grp)
        _nema17(b, "z_motor", (x, -0.014, z), b.mat("dark_plastic"), chrome,
                b.mat("wire_black"), parent=grp)

    # vertical profile rails guiding the bed carrier
    for sx in (-1, 1):
        x = sx * (FRAME_X - 0.004)
        z = -0.030
        b.box("z_rail_profile", (0.008, CHAMBER_H - 0.080, 0.012),
              (x, CHAMBER_H / 2.0 - 0.010, z), steel, parent=grp)
        for i in range(7):
            b.cylinder("z_rail_bolt", 0.0022, 0.002,
                       (x - sx * 0.004, 0.040 + i * 0.048, z), steel,
                       rot=rot_z(90.0), sides=6, parent=grp)
        for cy in (BED_TOP - 0.030, BED_TOP - 0.070):
            b.box("z_carriage", (0.012, 0.030, 0.024), (x - sx * 0.006, cy, z),
                  b.mat("alu_bright"), parent=grp)
            b.box("z_carriage_bracket", (0.018, 0.024, 0.004),
                  (x - sx * 0.014, cy, z), alu, parent=grp)


def _corexy(b: SpecBuilder) -> None:
    alu = b.mat("alu_bright")
    dark = b.mat("dark_plastic")
    cf = b.mat("carbon_fibre")
    steel = b.mat("steel")
    belt = b.mat("belt_rubber")
    grp = b.group("corexy")

    # Y rails, left and right, running front to back
    for sx in (-1, 1):
        _linear_rail(b, "y_rail", "z", -FRAME_Z + 0.012, FRAME_Z - 0.012,
                     (sx * (FRAME_X - 0.008), RAIL_Y), steel, alu,
                     carriages=(TOOLHEAD_Z,), parent=grp)

    # the carbon fibre X gantry rods
    for dz in (-0.011, 0.011):
        b.cylinder("x_gantry_rod", 0.0040, 2 * FRAME_X - 0.03,
                   (0.0, GANTRY_Y, TOOLHEAD_Z + dz), cf, rot=rot_z(90.0), sides=14,
                   parent=grp)
    for sx in (-1, 1):
        b.box("x_gantry_end", (0.022, 0.026, 0.040),
              (sx * (FRAME_X - 0.016), GANTRY_Y - 0.002, TOOLHEAD_Z), alu, parent=grp)
        b.box("x_gantry_end_cap", (0.006, 0.020, 0.036),
              (sx * (FRAME_X - 0.004), GANTRY_Y - 0.002, TOOLHEAD_Z), dark, parent=grp)

    # belt runs: two long Z runs per side plus the X runs across the gantry
    for sx in (-1, 1):
        x = sx * (FRAME_X - 0.016)
        _belt_run(b, "belt_y", "z", -FRAME_Z + 0.020, FRAME_Z - 0.020,
                  (x, GANTRY_Y + 0.010), 0.006, belt, dark, parent=grp)
        _belt_run(b, "belt_y_return", "z", -FRAME_Z + 0.020, FRAME_Z - 0.020,
                  (x - sx * 0.010, GANTRY_Y + 0.010), 0.006, belt, dark, parent=grp)
    for dz in (-0.008, 0.008):
        _belt_run(b, "belt_x", "x", -FRAME_X + 0.024, FRAME_X - 0.024,
                  (GANTRY_Y + 0.012, TOOLHEAD_Z + dz), 0.006, belt, dark, parent=grp)

    # idler pulley stacks in the four corners
    for sx in (-1, 1):
        for sz in (-1, 1):
            x = sx * (FRAME_X - 0.016)
            z = sz * (FRAME_Z - 0.018)
            b.cylinder("idler_shaft", 0.0022, 0.030, (x, GANTRY_Y + 0.010, z),
                       b.mat("chrome"), sides=10, parent=grp)
            for dy in (0.002, 0.016):
                b.cylinder("idler_pulley", 0.0072, 0.0075,
                           (x, GANTRY_Y + dy, z), alu, sides=16, parent=grp)
                b.cylinder("idler_flange", 0.0086, 0.0008,
                           (x, GANTRY_Y + dy + 0.0042, z), alu, sides=16, parent=grp)
                b.cylinder("idler_flange", 0.0086, 0.0008,
                           (x, GANTRY_Y + dy - 0.0042, z), alu, sides=16, parent=grp)
            b.box("idler_bracket", (0.018, 0.030, 0.006), (x, GANTRY_Y + 0.010, z + sz * 0.008),
                  dark, parent=grp)

    # drive pulleys and the two CoreXY stepper motors at the back corners
    for sx in (-1, 1):
        x = sx * (FRAME_X - 0.016)
        z = FRAME_Z - 0.018
        b.cylinder("drive_pulley", 0.0082, 0.0080, (x, GANTRY_Y + 0.026, z),
                   b.mat("steel"), sides=18, parent=grp)
        for k in range(20):
            a = 2.0 * math.pi * k / 20.0
            b.box("drive_pulley_tooth", (0.0012, 0.0075, 0.0022),
                  (x + 0.0080 * math.cos(a), GANTRY_Y + 0.026, z + 0.0080 * math.sin(a)),
                  b.mat("steel"), rot=rot_y(-math.degrees(a)), parent=grp)
        _nema17(b, "corexy_motor", (x, GANTRY_Y + 0.056, z), dark,
                b.mat("chrome"), b.mat("wire_black"), shaft_rot=rot_x(180.0),
                size=0.042, length=0.034, parent=grp)

    # belt tensioners on the front idlers
    for sx in (-1, 1):
        x = sx * (FRAME_X - 0.016)
        z = -FRAME_Z + 0.018
        b.box("belt_tensioner", (0.014, 0.014, 0.024), (x, GANTRY_Y + 0.010, z - 0.006),
              alu, parent=grp)
        b.cylinder("tensioner_screw", 0.0018, 0.018, (x, GANTRY_Y + 0.010, z - 0.016),
                   b.mat("chrome"), rot=rot_x(90.0), sides=8, parent=grp)
        b.cylinder("tensioner_nut", 0.0034, 0.0028, (x, GANTRY_Y + 0.010, z - 0.024),
                   steel, rot=rot_x(90.0), sides=6, parent=grp)

    # gantry cable chain following the toolhead
    links = 14
    for i in range(links):
        t = i / float(links - 1)
        x = -FRAME_X + 0.03 + t * (TOOLHEAD_X + FRAME_X - 0.03)
        y = GANTRY_Y + 0.030 - 0.010 * math.sin(math.pi * t)
        b.box("cable_chain_link", (0.010, 0.008, 0.012), (x, y, TOOLHEAD_Z + 0.026),
              dark, parent=grp)


def _toolhead(b: SpecBuilder) -> None:
    dark = b.mat("dark_plastic")
    alu = b.mat("alu_bright")
    steel = b.mat("steel")
    brass = b.mat("brass")
    grp = b.group("toolhead", at=(TOOLHEAD_X, GANTRY_Y, TOOLHEAD_Z))

    # carriage and shroud
    b.box("toolhead_carriage", (0.052, 0.044, 0.040), (0.0, -0.006, 0.0), alu, parent=grp)
    b.box("toolhead_shroud", (0.058, 0.056, 0.046), (0.0, -0.030, 0.0), dark, parent=grp)
    b.box("toolhead_front_cover", (0.056, 0.050, 0.004), (0.0, -0.030, -0.024),
          b.mat("black_plastic"), parent=grp)
    for sx in (-1, 1):
        b.box("toolhead_bearing_block", (0.010, 0.014, 0.036), (sx * 0.026, 0.000, 0.0),
              alu, parent=grp)

    # extruder: motor, gears, filament path
    _nema17(b, "extruder_motor", (-0.018, -0.014, 0.026), dark, steel,
            b.mat("wire_black"), shaft_rot=rot_x(90.0), size=0.028, length=0.026,
            parent=grp)
    b.cylinder("extruder_gear_large", 0.0090, 0.0060, (-0.004, -0.014, 0.014),
               steel, rot=rot_x(90.0), sides=18, parent=grp)
    for k in range(18):
        a = 2.0 * math.pi * k / 18.0
        b.box("extruder_gear_tooth", (0.0013, 0.0060, 0.0018),
              (-0.004 + 0.0090 * math.cos(a), -0.014, 0.014 + 0.0090 * math.sin(a)),
              steel, rot=rot_y(-math.degrees(a)), parent=grp)
    b.cylinder("extruder_gear_small", 0.0045, 0.0060, (0.008, -0.014, 0.014),
               brass, rot=rot_x(90.0), sides=14, parent=grp)
    b.cylinder("extruder_idler", 0.0055, 0.0050, (0.008, -0.014, 0.001),
               b.mat("grey_plastic"), rot=rot_x(90.0), sides=14, parent=grp)
    b.box("extruder_lever", (0.006, 0.020, 0.008), (0.016, -0.014, 0.006), dark, parent=grp)

    # heat break, heatsink with fins, heater block, nozzle, sock
    b.cylinder("hotend_heatsink", 0.0090, 0.024, (0.0, -0.048, -0.004), alu,
               sides=16, parent=grp)
    for i in range(10):
        b.cylinder("hotend_fin", 0.0118, 0.0011, (0.0, -0.038 - i * 0.0022, -0.004),
                   alu, sides=16, parent=grp)
    b.cylinder("hotend_heatbreak", 0.0030, 0.010, (0.0, -0.064, -0.004),
               b.mat("chrome"), sides=12, parent=grp)
    b.box("hotend_heater_block", (0.018, 0.012, 0.014), (0.0, -0.073, -0.004),
          b.mat("hot_metal"), parent=grp)
    b.cylinder("hotend_cartridge", 0.0030, 0.020, (0.0, -0.073, -0.004),
               b.mat("hot_metal"), rot=rot_z(90.0), sides=10, parent=grp)
    b.box("hotend_thermistor", (0.004, 0.004, 0.006), (0.007, -0.070, -0.004),
          b.mat("white_plastic"), parent=grp)
    b.box("hotend_sock", (0.020, 0.014, 0.016), (0.0, -0.0735, -0.004),
          b.mat("silicone_sock"), parent=grp)
    b.cone("nozzle", 0.0050, 0.0008, 0.008, (0.0, -0.0835, -0.004), brass,
           rot=rot_x(180.0), sides=14, parent=grp)
    b.cylinder("nozzle_tip", 0.0006, 0.0016, (0.0, -0.0885, -0.004), brass,
               sides=8, parent=grp)

    # part cooling fan, blades and duct
    fan = b.group("part_cooling_fan", at=(-0.020, -0.046, 0.0), parent=grp)
    b.box("fan_housing", (0.026, 0.026, 0.012), (0.0, 0.0, 0.0), dark, parent=fan)
    b.cylinder("fan_hub", 0.0035, 0.010, (0.0, 0.0, 0.0), b.mat("black_plastic"),
               rot=rot_x(90.0), sides=12, parent=fan)
    for k in range(7):
        a = 360.0 * k / 7.0
        b.box("fan_blade", (0.0100, 0.0018, 0.0090),
              (0.0075 * math.cos(math.radians(a)), 0.0075 * math.sin(math.radians(a)), 0.0),
              b.mat("grey_plastic"), rot=rot_mul(rot_z(a), rot_x(28.0)), parent=fan)
    b.box("fan_grill", (0.024, 0.024, 0.0015), (0.0, 0.0, -0.007), dark, parent=fan)
    b.extrusion("cooling_duct",
                [[-0.014, 0.0], [0.014, 0.0], [0.010, -0.026], [-0.010, -0.026]],
                0.012, (0.010, -0.062, 0.0), dark, parent=grp)
    for sx in (-1, 1):
        b.box("duct_outlet", (0.006, 0.004, 0.010), (sx * 0.011, -0.082, -0.002),
              dark, parent=grp)

    # hotend cooling fan on the back face
    b.box("hotend_fan", (0.024, 0.024, 0.010), (0.014, -0.044, 0.020), dark, parent=grp)
    for k in range(7):
        a = 360.0 * k / 7.0
        b.box("hotend_fan_blade", (0.0090, 0.0016, 0.0080),
              (0.014 + 0.0068 * math.cos(math.radians(a)),
               -0.044 + 0.0068 * math.sin(math.radians(a)), 0.020),
              b.mat("grey_plastic"), rot=rot_mul(rot_z(a), rot_x(26.0)), parent=grp)

    # filament cutter, lidar / eddy current sensor, PCB and ribbon cable
    b.box("filament_cutter_body", (0.010, 0.014, 0.008), (0.022, -0.026, 0.010),
          b.mat("grey_plastic"), parent=grp)
    b.box("filament_cutter_blade", (0.002, 0.010, 0.006), (0.022, -0.036, 0.010),
          b.mat("chrome"), parent=grp)
    b.cylinder("filament_cutter_pivot", 0.0018, 0.010, (0.022, -0.026, 0.010),
               steel, rot=rot_x(90.0), sides=8, parent=grp)
    b.box("lidar_housing", (0.020, 0.014, 0.012), (-0.024, -0.070, -0.010), dark, parent=grp)
    b.cylinder("lidar_lens", 0.0035, 0.003, (-0.024, -0.076, -0.014),
               b.mat("lidar_glow"), rot=rot_x(90.0), sides=12, parent=grp)
    b.box("lidar_laser_line", (0.014, 0.0008, 0.0008), (-0.024, -0.078, -0.016),
          b.mat("lidar_glow"), parent=grp)
    b.box("eddy_sensor", (0.012, 0.004, 0.010), (0.020, -0.078, 0.004),
          b.mat("pcb_green"), parent=grp)
    b.box("toolhead_pcb", (0.044, 0.030, 0.0016), (0.0, -0.026, 0.024),
          b.mat("pcb_green"), parent=grp)
    for i in range(8):
        b.box("toolhead_pcb_part", (0.004, 0.003, 0.003),
              (-0.016 + i * 0.0046, -0.020, 0.0262), b.mat("black_plastic"), parent=grp)
    b.box("ribbon_connector", (0.020, 0.005, 0.004), (0.0, -0.038, 0.026),
          b.mat("white_plastic"), parent=grp)

    # PTFE feed and the ribbon cable heading for the gantry chain
    b.tube("ptfe_tube",
           [[0.008, 0.006, 0.014], [0.008, 0.030, 0.020], [0.000, 0.044, 0.030]],
           0.0021, b.mat("ptfe"), sides=8, parent=grp)
    b.tube("toolhead_ribbon",
           [[0.0, -0.022, 0.028], [0.0, 0.010, 0.032], [-0.006, 0.030, 0.030]],
           0.0028, b.mat("wire_black"), sides=6, parent=grp)


def _ams(b: SpecBuilder) -> None:
    """A four slot AMS style feeder mounted above the chamber, seen through the lid."""
    dark = b.mat("dark_plastic")
    grey = b.mat("grey_plastic")
    white = b.mat("white_plastic")
    grp = b.group("ams", at=(0.0, CHAMBER_H - 0.070, HALF_D - 0.052))

    b.box("ams_case", (0.300, 0.100, 0.090), (0.0, 0.0, 0.0), dark, parent=grp)
    b.box("ams_lid", (0.296, 0.006, 0.086), (0.0, 0.052, 0.0), grey, parent=grp)
    b.box("ams_front", (0.300, 0.096, 0.004), (0.0, 0.0, -0.046),
          b.mat("black_plastic"), parent=grp)
    b.box("ams_window", (0.240, 0.050, 0.002), (0.0, 0.006, -0.049),
          b.mat("glass_front"), parent=grp)

    fil = ("filament_a", "filament_b", "filament_c", "filament_d")
    for i in range(4):
        x = -0.108 + i * 0.072
        spool = b.group("ams_spool", at=(x, 0.0, 0.0), parent=grp)
        for sz in (-1, 1):
            b.cylinder("spool_flange", 0.0335, 0.0025, (0.0, 0.0, sz * 0.0175),
                       b.mat("black_plastic"), rot=rot_x(90.0), sides=20, parent=spool)
            for k in range(6):
                a = 60.0 * k
                b.box("spool_spoke_hole", (0.010, 0.0030, 0.010),
                      (0.020 * math.cos(math.radians(a)), 0.020 * math.sin(math.radians(a)),
                       sz * 0.0175), dark, rot=rot_z(a), parent=spool)
        b.cylinder("spool_hub", 0.0130, 0.032, (0.0, 0.0, 0.0), grey,
                   rot=rot_x(90.0), sides=18, parent=spool)
        b.cylinder("spool_filament", 0.0320, 0.030, (0.0, 0.0, 0.0), b.mat(fil[i]),
                   rot=rot_x(90.0), sides=24, parent=spool)
        b.torus("spool_filament_edge", 0.0320, 0.0016, (0.0, 0.0, 0.0), b.mat(fil[i]),
                rot=rot_x(90.0), sides=6, rings=24, parent=spool)
        b.box("ams_feeder", (0.020, 0.014, 0.016), (0.0, -0.040, 0.026), grey, parent=spool)
        b.cylinder("ams_feed_gear", 0.0050, 0.005, (0.0, -0.040, 0.020),
                   b.mat("steel"), rot=rot_x(90.0), sides=12, parent=spool)
        b.box("ams_rfid", (0.008, 0.006, 0.002), (0.020, -0.030, 0.030),
              b.mat("pcb_green"), parent=spool)
        b.tube("ams_ptfe",
               [[0.0, -0.048, 0.026], [0.0, -0.058, 0.010], [-x * 0.6, -0.062, -0.010],
                [-x, -0.066, -0.030]],
               0.0022, b.mat("ptfe"), sides=6, parent=spool)

    b.box("ams_hub", (0.070, 0.024, 0.030), (0.0, -0.062, -0.034), grey, parent=grp)
    b.box("ams_buffer", (0.044, 0.020, 0.024), (0.090, -0.062, -0.030), dark, parent=grp)
    for k in range(3):
        b.cylinder("ams_buffer_roller", 0.0060, 0.018, (0.076 + k * 0.014, -0.062, -0.030),
                   white, rot=rot_x(90.0), sides=12, parent=grp)
    b.tube("ams_to_toolhead",
           [[0.0, -0.076, -0.036], [0.0, -0.120, -0.060], [TOOLHEAD_X * 0.4, -0.180, -0.070],
            [TOOLHEAD_X, GANTRY_Y - CHAMBER_H + 0.070 + 0.050, TOOLHEAD_Z - HALF_D + 0.052]],
           0.0026, b.mat("ptfe"), sides=8, parent=grp)


def _chamber_details(b: SpecBuilder) -> None:
    dark = b.mat("dark_plastic")
    grey = b.mat("grey_plastic")
    white = b.mat("white_plastic")
    steel = b.mat("steel")
    grp = b.group("details")

    # LED light bar across the front of the ceiling
    bar = b.group("led_bar", at=(0.0, CHAMBER_H - 0.020, -HALF_D + 0.030), parent=grp)
    b.box("led_bar_body", (0.280, 0.012, 0.016), (0.0, 0.0, 0.0), b.mat("alu_bright"),
          parent=bar)
    b.box("led_bar_diffuser", (0.276, 0.004, 0.012), (0.0, -0.007, 0.0),
          b.mat("led_bar"), parent=bar)
    for i in range(14):
        x = -0.130 + i * 0.020
        b.box("led_chip", (0.005, 0.0015, 0.005), (x, -0.0092, 0.0), b.mat("led_bar"),
              parent=bar)
    b.tube("led_wiring",
           [[0.138, 0.0, 0.0], [0.150, 0.0, 0.020], [0.150, 0.010, 0.120]],
           0.0018, b.mat("wire_white"), sides=6, parent=bar)

    # chamber camera in the top left corner
    cam = b.group("chamber_camera", at=(-HALF_W + 0.026, CHAMBER_H - 0.028, -HALF_D + 0.026),
                  parent=grp)
    b.box("camera_body", (0.020, 0.016, 0.018), (0.0, 0.0, 0.0), dark, parent=cam)
    b.cylinder("camera_barrel", 0.0055, 0.010, (0.006, -0.004, -0.006),
               b.mat("black_plastic"), rot=look_rotation((0.5, -0.4, -0.77)), sides=14,
               parent=cam)
    b.sphere("camera_lens", 0.0040, (0.009, -0.006, -0.010), b.mat("glass_front"),
             rings=8, sectors=12, parent=cam)
    b.box("camera_mount", (0.008, 0.010, 0.008), (-0.010, 0.004, 0.006), grey, parent=cam)

    # exhaust fan and activated carbon filter on the back wall
    ex = b.group("exhaust", at=(-0.075, 0.150, HALF_D - 0.022), parent=grp)
    b.box("exhaust_housing", (0.078, 0.078, 0.026), (0.0, 0.0, 0.0), dark, parent=ex)
    b.cylinder("exhaust_hub", 0.0090, 0.020, (0.0, 0.0, 0.0), b.mat("black_plastic"),
               rot=rot_x(90.0), sides=14, parent=ex)
    for k in range(7):
        a = 360.0 * k / 7.0
        b.box("exhaust_blade", (0.030, 0.0025, 0.024),
              (0.017 * math.cos(math.radians(a)), 0.017 * math.sin(math.radians(a)), 0.0),
              grey, rot=rot_mul(rot_z(a), rot_x(30.0)), parent=ex)
    b.box("carbon_filter", (0.080, 0.080, 0.020), (0.0, 0.0, -0.024), grey, parent=ex)
    b.honeycomb("carbon_filter_media", (0.072, 0.072), 0.006, 0.0012, 0.018,
                (0.0, 0.0, -0.024), b.mat("black_plastic"), rot=IDENT, parent=ex)
    b.tube("exhaust_duct",
           [[0.0, 0.040, 0.006], [0.0, 0.090, 0.010], [0.030, 0.130, 0.012]],
           0.016, grey, sides=12, parent=ex)

    # purge chute and poop bin at the back right
    chute = b.group("purge_chute", at=(0.120, 0.030, HALF_D - 0.040), parent=grp)
    b.extrusion("chute_wall", [[-0.020, 0.0], [0.020, 0.0], [0.026, 0.050], [-0.026, 0.050]],
                0.002, (0.0, 0.024, -0.018), grey, rot=rot_x(-18.0), parent=chute)
    for sx in (-1, 1):
        b.box("chute_side", (0.002, 0.052, 0.036), (sx * 0.023, 0.024, 0.0), grey,
              rot=rot_x(-18.0), parent=chute)
    b.box("poop_bin", (0.056, 0.028, 0.048), (0.0, -0.014, 0.010), dark, parent=chute)
    b.box("poop_bin_lip", (0.060, 0.004, 0.052), (0.0, 0.002, 0.010), b.mat("black_plastic"),
          parent=chute)
    for k in range(3):
        b.cylinder("purged_filament", 0.0035, 0.010,
                   (-0.014 + k * 0.014, -0.020, 0.006 + 0.004 * k),
                   b.mat("filament_a" if k == 0 else ("filament_b" if k == 1 else "filament_c")),
                   rot=rot_z(80.0 - 30.0 * k), sides=8, parent=chute)

    # LCD and control knob on the front right pillar
    panel = b.group("control_panel", at=(HALF_W - 0.014, 0.090, -HALF_D + 0.014), parent=grp)
    b.box("lcd_bezel", (0.010, 0.052, 0.048), (0.0, 0.0, 0.0), dark, parent=panel)
    b.box("lcd_screen", (0.002, 0.038, 0.036), (-0.006, 0.004, 0.0),
          b.mat("led_bar"), parent=panel)
    b.text("lcd_text", "X1C\nREADY", 0.006, 0.0006, (-0.008, 0.006, 0.0),
           b.mat("label_text"), rot=rot_y(-90.0), parent=panel)
    b.cylinder("control_knob", 0.0090, 0.008, (-0.008, -0.020, 0.0), grey,
               rot=rot_z(90.0), sides=16, parent=panel)
    b.cylinder("control_knob_cap", 0.0070, 0.002, (-0.013, -0.020, 0.0),
               b.mat("black_plastic"), rot=rot_z(90.0), sides=16, parent=panel)
    for k in range(3):
        b.cylinder("panel_led", 0.0018, 0.001, (-0.006, 0.028, -0.014 + k * 0.014),
                   b.mat("led_bar"), rot=rot_z(90.0), sides=8, parent=panel)

    # power supply under the floor pan, seen through the grille
    psu = b.group("power_supply", at=(-0.090, 0.024, HALF_D - 0.060), parent=grp)
    b.box("psu_case", (0.110, 0.040, 0.056), (0.0, 0.0, 0.0), b.mat("alu_bright"), parent=psu)
    b.box("psu_terminal_strip", (0.030, 0.010, 0.006), (0.038, 0.0, -0.030),
          b.mat("white_plastic"), parent=psu)
    for i in range(6):
        b.box("psu_vent", (0.002, 0.028, 0.040), (-0.050 + i * 0.006, 0.0, 0.0),
              b.mat("dark_plastic"), parent=psu)
    b.cylinder("psu_fan", 0.016, 0.008, (-0.040, 0.0, 0.030), b.mat("black_plastic"),
               rot=rot_x(90.0), sides=14, parent=psu)

    # main board tray on the floor, with its heat sinks and connectors
    board = b.group("mainboard", at=(0.070, 0.014, HALF_D - 0.055), parent=grp)
    b.box("mainboard_pcb", (0.110, 0.0018, 0.080), (0.0, 0.0, 0.0), b.mat("pcb_green"),
          parent=board)
    for i in range(4):
        b.box("driver_heatsink", (0.014, 0.010, 0.014), (-0.036 + i * 0.024, 0.006, 0.020),
              b.mat("alu_bright"), parent=board)
        for k in range(4):
            b.box("heatsink_fin", (0.0016, 0.010, 0.014),
                  (-0.040 + i * 0.024 + k * 0.004, 0.006, 0.020), b.mat("alu_bright"),
                  parent=board)
    for i in range(6):
        b.box("board_connector", (0.010, 0.006, 0.005), (-0.044 + i * 0.018, 0.004, -0.030),
              white, parent=board)
    b.box("board_mcu", (0.014, 0.002, 0.014), (0.020, 0.002, -0.004),
          b.mat("black_plastic"), parent=board)

    # cable chain running to the bed
    for i in range(12):
        t = i / 11.0
        z = HALF_D - 0.045 - t * 0.10
        y = 0.014 + 0.020 * math.sin(math.pi * t)
        b.box("bed_cable_link", (0.012, 0.008, 0.010), (0.0, y, z), dark, parent=grp)

    # warning labels
    b.box("label_plate_hot", (0.052, 0.020, 0.0008), (-0.060, 0.020, -HALF_D + 0.006),
          b.mat("label_yellow"), parent=grp)
    b.text("label_hot_text", "HOT SURFACE", 0.005, 0.0006,
           (-0.060, 0.020, -HALF_D + 0.0072), b.mat("label_text"), parent=grp)
    b.box("label_plate_volt", (0.040, 0.018, 0.0008), (0.100, 0.020, HALF_D - 0.006),
          b.mat("label_yellow"), rot=rot_y(180.0), parent=grp)
    b.text("label_volt_text", "230V AC", 0.005, 0.0006,
           (0.100, 0.020, HALF_D - 0.0072), b.mat("label_text"), rot=rot_y(180.0),
           parent=grp)
    b.text("label_serial", "X1C-256\nBUILD VOLUME 256MM", 0.0042, 0.0005,
           (HALF_W - 0.0025, 0.240, 0.060), b.mat("label_text"), rot=rot_y(-90.0),
           parent=grp)
    b.text("label_caution", "CAUTION\nMOVING PARTS", 0.0045, 0.0005,
           (-HALF_W + 0.0025, 0.300, -0.040), b.mat("label_text"), rot=rot_y(90.0),
           parent=grp)

    # a scatter of frame screws so the machine reads as assembled hardware
    for i in range(24):
        a = 2.0 * math.pi * i / 24.0
        x = (HALF_W - 0.010) * math.cos(a)
        z = (HALF_D - 0.010) * math.sin(a)
        b.cylinder("panel_screw", 0.0020, 0.0018, (x, 0.026 + 0.014 * (i % 3), z),
                   steel, sides=6, parent=grp)


def _lighting(b: SpecBuilder) -> None:
    # the LED bar, modelled as a wide spot from the front of the ceiling
    b.spot((0.0, CHAMBER_H - 0.024, -HALF_D + 0.032), (0.0, -0.92, 0.40),
           color=(1.0, 0.97, 0.90), intensity=1.4, cutoff_deg=68.0, rng=0.6)
    # soft bounce off the chamber walls
    b.point((0.0, CHAMBER_H * 0.62, 0.0), color=(0.62, 0.66, 0.74), intensity=0.55, rng=0.9)
    b.point((0.0, BED_TOP + 0.05, -HALF_D + 0.06), color=(0.50, 0.54, 0.62),
            intensity=0.35, rng=0.7)
    # key light through the glass door, and a cool fill from the back
    b.directional((0.35, -0.80, 0.48), color=(0.85, 0.86, 0.88), intensity=0.55)
    b.directional((-0.30, -0.35, -0.88), color=(0.42, 0.48, 0.60), intensity=0.30)
    # the hot nozzle glow
    b.point((TOOLHEAD_X, GANTRY_Y - 0.086, TOOLHEAD_Z - 0.004),
            color=(1.0, 0.32, 0.08), intensity=0.30, rng=0.12)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build() -> Dict[str, Any]:
    """Generate the Bambu X1C environment spec."""
    b = SpecBuilder(
        ENVIRONMENT_ID,
        ENVIRONMENT_NAME,
        ENVIRONMENT_DESCRIPTION,
        user_scale=USER_SCALE,
        bounds=(CHAMBER_W, CHAMBER_D, CHAMBER_H),
        spawn=(0.0, BED_TOP, 0.045),
        ambient=(0.055, 0.058, 0.065),
    )
    _materials(b)
    _lighting(b)
    b.anchor("build_plate", (0.0, BED_TOP, 0.0), (PLATE, PLATE))
    b.anchor("purge_bin", (0.120, 0.044, HALF_D - 0.030), (0.056, 0.048))
    b.anchor("chamber_centre", (0.0, CHAMBER_H * 0.5, 0.0), (CHAMBER_W, CHAMBER_D))

    _frame(b)
    _panels(b)
    _door_and_lid(b)
    _bed(b)
    _z_axis(b)
    _corexy(b)
    _toolhead(b)
    _ams(b)
    _chamber_details(b)
    return b.build()


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import json

    spec = build()
    print(json.dumps({"id": spec["id"], "parts": sum(1 for _ in [])}, indent=2))
