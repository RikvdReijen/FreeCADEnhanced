# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Writing a toolpath out for a machine.

Every post-processor takes the same input, the list of paths, and turns
each print point into a target in the machine's own language. They differ
only in how a frame is written down:

CSV
    position, quaternion, tool axis and the process values, one row per
    point. The plain text interchange format to reach for when nothing
    else fits, and the one a robot vendor's own tooling can usually read.
G-code
    three axis (``X Y Z E``) or five axis (``X Y Z`` plus two rotary
    words). What a gantry or a printer controller expects.
KUKA KRL
    ``LIN {X, Y, Z, A, B, C}`` targets, the A B C being the yaw, pitch and
    roll KUKA uses.
ABB RAPID
    ``MoveL`` with a ``robtarget``, whose orientation is a quaternion.
Universal Robots script
    ``movel(p[x, y, z, rx, ry, rz])`` with the rotation as a rotation
    vector, and metres rather than millimetres.

The tool frame convention is the usual one for a deposition head: the
tool Z axis points from the nozzle towards the work, so it is the
opposite of the print point's tool axis, and the tool X axis follows the
direction of travel.
"""

import inspect
import math

import FreeCAD
from FreeCAD import Vector

__all__ = [
    "POST_PROCESSORS",
    "tool_frames",
    "write_csv",
    "write_gcode",
    "write_krl",
    "write_rapid",
    "write_urscript",
    "post_process",
]


def _normalize(vector, fallback=Vector(0, 0, 1)):
    length = vector.Length
    if length < 1e-12:
        return Vector(fallback)
    return Vector(vector.x / length, vector.y / length, vector.z / length)


def _iter_points(paths):
    """Walk the toolpath yielding ``(path, index, point, is_first)``."""
    for path in paths:
        points = list(path.points)
        if path.closed and len(points) > 2:
            points = points + [points[0]]
        for index, point in enumerate(points):
            yield path, index, point, index == 0


def tool_frames(paths, flip=True):
    """Turn every print point into a full tool frame.

    Returns a list of ``(point, placement)``. The placement's Z axis is
    the approach direction of the nozzle and its X axis the direction of
    travel, so the rotation is fully determined rather than left free
    about the tool axis.
    """
    frames = []
    for path in paths:
        points = list(path.points)
        if path.closed and len(points) > 2:
            points = points + [points[0]]
        for index, point in enumerate(points):
            axis = _normalize(point.axis)
            approach = axis * -1.0 if flip else Vector(axis)
            if len(points) > 1:
                after = points[min(len(points) - 1, index + 1)].position
                before = points[max(0, index - 1)].position
                travel = after - before
            else:
                travel = Vector(1, 0, 0)
            x_axis = travel - approach * travel.dot(approach)
            if x_axis.Length < 1e-9:
                helper = Vector(1, 0, 0) if abs(approach.x) < 0.9 else Vector(0, 1, 0)
                x_axis = helper - approach * helper.dot(approach)
            x_axis = _normalize(x_axis, Vector(1, 0, 0))
            y_axis = approach.cross(x_axis)
            rotation = FreeCAD.Rotation(x_axis, y_axis, approach, "ZXY")
            frames.append((point, FreeCAD.Placement(point.position, rotation)))
    return frames


# ---------------------------------------------------------------------------
# Plain interchange
# ---------------------------------------------------------------------------


def write_csv(paths, separator=",", decimals=4, **_options):
    """One row per print point: position, quaternion, axis and process values."""
    fmt = "%%.%df" % int(decimals)
    header = separator.join(
        [
            "index",
            "path",
            "layer",
            "role",
            "travel",
            "x",
            "y",
            "z",
            "qx",
            "qy",
            "qz",
            "qw",
            "axis_x",
            "axis_y",
            "axis_z",
            "width",
            "height",
            "speed",
            "extrusion",
            "overhang",
        ]
    )
    lines = [header]
    index = 0
    path_index = -1
    # tool_frames flattens the paths, so walk the paths alongside it to
    # keep each row's path identity
    frames = iter(tool_frames(paths))
    for path in paths:
        path_index += 1
        count = len(path.points) + (1 if path.closed and len(path.points) > 2 else 0)
        for _ in range(count):
            point, placement = next(frames)
            q = placement.Rotation.Q
            values = [
                str(index),
                str(path_index),
                str(point.layer),
                path.role,
                "1" if path.travel else "0",
                fmt % point.position.x,
                fmt % point.position.y,
                fmt % point.position.z,
                fmt % q[0],
                fmt % q[1],
                fmt % q[2],
                fmt % q[3],
                fmt % point.axis.x,
                fmt % point.axis.y,
                fmt % point.axis.z,
                fmt % point.width,
                fmt % point.height,
                fmt % point.speed,
                fmt % point.extrusion,
                fmt % point.overhang,
            ]
            lines.append(separator.join(values))
            index += 1
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# G-code
# ---------------------------------------------------------------------------


def write_gcode(
    paths,
    axes=3,
    filament_diameter=1.75,
    volumetric=False,
    travel_speed=9000.0,
    rotary_words=("A", "B"),
    header=None,
    footer=None,
    relative_extrusion=False,
    **_options,
):
    """G-code for a gantry or a printer controller.

    ``axes`` of 3 writes ``X Y Z E``; 5 adds two rotary words holding the
    tool direction, by default ``A`` (tilt away from the build axis) and
    ``B`` (rotation about it). ``volumetric`` makes ``E`` cubic
    millimetres instead of filament length, which is what a pellet
    extruder wants.
    """
    from . import analysis

    lines = []
    lines.extend(
        header
        or [
            "; RoboPrint",
            "; %d axis output" % int(axes),
            "G21 ; millimetres",
            "G90 ; absolute positions",
        ]
    )
    lines.append("M83" if relative_extrusion else "M82")
    lines.append("G92 E0")
    total_e = 0.0
    previous = None
    for path in paths:
        points = list(path.points)
        if path.closed and len(points) > 2:
            points = points + [points[0]]
        for index, point in enumerate(points):
            words = ["G0" if (path.travel or (index == 0 and previous is not None)) else "G1"]
            words.append("X%.3f" % point.position.x)
            words.append("Y%.3f" % point.position.y)
            words.append("Z%.3f" % point.position.z)
            if int(axes) >= 5:
                axis = _normalize(point.axis)
                tilt = math.degrees(math.acos(max(-1.0, min(1.0, axis.z))))
                spin = math.degrees(math.atan2(axis.y, axis.x))
                words.append("%s%.3f" % (rotary_words[0], tilt))
                words.append("%s%.3f" % (rotary_words[1], spin))
            moving = words[0] == "G1" and previous is not None and not path.travel
            if moving:
                distance = (point.position - previous).Length
                if volumetric:
                    per_mm = analysis.bead_area(point.width, point.height)
                else:
                    per_mm = analysis.filament_per_millimetre(
                        point.width, point.height, filament_diameter
                    )
                delta = distance * per_mm * point.extrusion
                total_e = delta if relative_extrusion else total_e + delta
                words.append("E%.5f" % total_e)
            speed = travel_speed if path.travel or not moving else (point.speed or 0.0)
            if speed:
                words.append("F%.1f" % speed)
            lines.append(" ".join(words))
            previous = point.position
    lines.extend(footer or ["M104 S0 ; heater off", "M84 ; motors off"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Robot languages
# ---------------------------------------------------------------------------


def write_krl(
    paths, name="ROBOPRINT", tool=1, base=0, velocity=0.05, approximation=1.0, **_options
):
    """KUKA Robot Language: ``LIN`` targets with A, B, C Euler angles."""
    lines = [
        "DEF %s ( )" % name,
        "  ; generated by RoboPrint",
        "  $TOOL = TOOL_DATA[%d]" % int(tool),
        "  $BASE = BASE_DATA[%d]" % int(base),
        "  $VEL.CP = %.4f" % float(velocity),
        "  $APO.CDIS = %.3f" % float(approximation),
        "  BAS(#INITMOV, 0)",
    ]
    for point, placement in tool_frames(paths):
        yaw, pitch, roll = placement.Rotation.toEuler()
        lines.append(
            "  LIN {X %.3f, Y %.3f, Z %.3f, A %.3f, B %.3f, C %.3f} C_DIS"
            % (
                point.position.x,
                point.position.y,
                point.position.z,
                yaw,
                pitch,
                roll,
            )
        )
    lines.append("END")
    return "\n".join(lines) + "\n"


def write_rapid(
    paths,
    name="RoboPrint",
    module="RoboPrintModule",
    tool="tExtruder",
    work_object="wobj0",
    zone="z1",
    speed_data=None,
    **_options,
):
    """ABB RAPID: a ``MoveL`` per point, orientation as a quaternion."""
    lines = [
        "MODULE %s" % module,
        "    ! generated by RoboPrint",
        "    PROC %s()" % name,
    ]
    for point, placement in tool_frames(paths):
        q = placement.Rotation.Q  # x, y, z, w
        speed = speed_data or ("v%d" % max(5, int(round((point.speed or 300.0) / 60.0))))
        lines.append(
            "        MoveL [[%.3f,%.3f,%.3f],[%.6f,%.6f,%.6f,%.6f],[0,0,0,0],"
            "[9E9,9E9,9E9,9E9,9E9,9E9]], %s, %s, %s\\WObj:=%s;"
            % (
                point.position.x,
                point.position.y,
                point.position.z,
                q[3],
                q[0],
                q[1],
                q[2],
                speed,
                zone,
                tool,
                work_object,
            )
        )
    lines.append("    ENDPROC")
    lines.append("ENDMODULE")
    return "\n".join(lines) + "\n"


def write_urscript(paths, acceleration=0.5, blend=0.001, **_options):
    """Universal Robots script: ``movel`` with a rotation vector, in metres."""
    lines = ["# generated by RoboPrint", "def roboprint():"]
    for point, placement in tool_frames(paths):
        rotation = placement.Rotation
        angle = rotation.Angle
        axis = rotation.Axis
        rx, ry, rz = axis.x * angle, axis.y * angle, axis.z * angle
        speed = max(0.001, (point.speed or 300.0) / 60000.0)
        lines.append(
            "    movel(p[%.6f, %.6f, %.6f, %.6f, %.6f, %.6f], a=%.3f, v=%.4f, r=%.4f)"
            % (
                point.position.x / 1000.0,
                point.position.y / 1000.0,
                point.position.z / 1000.0,
                rx,
                ry,
                rz,
                float(acceleration),
                speed,
                float(blend),
            )
        )
    lines.append("end")
    lines.append("roboprint()")
    return "\n".join(lines) + "\n"


POST_PROCESSORS = {
    "CSV": (write_csv, ".csv"),
    "G-code (3 axis)": (lambda paths, **kw: write_gcode(paths, axes=3, **kw), ".gcode"),
    "G-code (5 axis)": (lambda paths, **kw: write_gcode(paths, axes=5, **kw), ".gcode"),
    "KUKA KRL": (write_krl, ".src"),
    "ABB RAPID": (write_rapid, ".mod"),
    "UR Script": (write_urscript, ".script"),
}


def post_process(paths, flavour="CSV", path=None, **options):
    """Run a named post-processor, optionally writing it to ``path``.

    Returns the generated text.
    """
    entry = POST_PROCESSORS.get(flavour)
    if entry is None:
        raise ValueError(
            "unknown post-processor %r; available: %s"
            % (flavour, ", ".join(sorted(POST_PROCESSORS)))
        )
    writer, suffix = entry
    # The caller passes everything it knows; each writer takes the subset it needs.
    accepted = inspect.signature(writer).parameters
    text = writer(paths, **{k: v for k, v in options.items() if k in accepted})
    if path:
        if not str(path).lower().endswith(suffix):
            path = str(path) + suffix
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text
