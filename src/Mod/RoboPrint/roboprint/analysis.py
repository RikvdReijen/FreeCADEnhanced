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

"""Checking a toolpath before it reaches the machine.

Large format beads are ten to forty millimetres wide and a print runs for
hours, so the questions worth answering before pressing start are always
the same: is any bead hanging further over its neighbour below than the
material tolerates, does the tool lean further than the arm allows, does
the nozzle body run into what has already been printed, and how much
material and time will this take.

Nothing here changes the toolpath: every function reports.
"""

import math

from FreeCAD import Vector

__all__ = [
    "bead_area",
    "extrusion_per_millimetre",
    "filament_per_millimetre",
    "compute_overhangs",
    "overhang_report",
    "tilt_report",
    "clearance_report",
    "continuity_report",
    "surface_tolerance_report",
    "cornering_report",
    "quality_report",
    "estimate",
]


def _normalize(vector, fallback=Vector(0, 0, 1)):
    length = vector.Length
    if length < 1e-12:
        return Vector(fallback)
    return Vector(vector.x / length, vector.y / length, vector.z / length)


def _perpendicular(axis):
    axis = _normalize(axis)
    helper = Vector(1, 0, 0) if abs(axis.x) < 0.9 else Vector(0, 1, 0)
    return _normalize(axis.cross(helper))


# ---------------------------------------------------------------------------
# Bead geometry
# ---------------------------------------------------------------------------


def bead_area(width, height):
    """Cross section of one bead, in square millimetres.

    A deposited bead is not a rectangle: it spreads to the layer height in
    the middle and rounds off at the sides, so the usual model is a
    rectangle with two half discs on the ends. For a bead no wider than it
    is tall this collapses to a disc.
    """
    width = float(width)
    height = float(height)
    if width <= 0 or height <= 0:
        return 0.0
    if width <= height:
        return math.pi * (width * 0.5) ** 2
    return (width - height) * height + math.pi * (height * 0.5) ** 2


def extrusion_per_millimetre(width, height):
    """Volume of material laid per millimetre travelled, in cubic millimetres."""
    return bead_area(width, height)


def filament_per_millimetre(width, height, filament_diameter=1.75):
    """Filament length consumed per millimetre travelled.

    Only meaningful for a filament machine; a pellet extruder is driven
    volumetrically instead.
    """
    filament_diameter = float(filament_diameter)
    if filament_diameter <= 0:
        return 0.0
    return bead_area(width, height) / (math.pi * (filament_diameter * 0.5) ** 2)


# ---------------------------------------------------------------------------
# Overhang
# ---------------------------------------------------------------------------


class _PointGrid:
    """A coarse spatial grid for nearest neighbour lookups."""

    def __init__(self, points, cell):
        self.cell = max(float(cell), 1e-6)
        self.grid = {}
        for index, point in enumerate(points):
            self.grid.setdefault(self._key(point), []).append(index)
        self.points = list(points)

    def _key(self, point):
        return (
            int(math.floor(point.x / self.cell)),
            int(math.floor(point.y / self.cell)),
            int(math.floor(point.z / self.cell)),
        )

    def near(self, point, radius=1):
        base = self._key(point)
        found = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    found.extend(self.grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()))
        return found

    def nearest(self, point, max_rings=4):
        best = None
        for ring in range(1, max_rings + 1):
            for index in self.near(point, ring):
                distance = (self.points[index] - point).Length
                if best is None or distance < best[0]:
                    best = (distance, index)
            if best is not None:
                return best
        return best


def _point_segment_distance(point, start, end):
    """Shortest distance from a point to a segment."""
    along = end - start
    length2 = along.dot(along)
    if length2 < 1e-18:
        return (point - start).Length
    t = max(0.0, min(1.0, (point - start).dot(along) / length2))
    return (point - (start + along * t)).Length


def compute_overhangs(paths, layer_height, axis=Vector(0, 0, 1), search=3.0):
    """Set ``overhang`` on every print point, in degrees, and return the values.

    The overhang of a bead is how far it hangs over the bead below it: the
    sideways distance to the nearest bead of the previous layer, measured
    against the layer height. Zero degrees sits squarely on top, forty
    five degrees hangs over by one layer height, ninety degrees has
    nothing underneath at all.

    The distance is taken to the nearest *segment* of the layer below, not
    to its nearest point, so the answer does not change when the same path
    is sampled more or less finely. A bead that falls inside the outline
    below is resting on it and counts as supported, so only material that
    steps outward is reported.
    """
    axis = _normalize(axis)
    layer_height = max(float(layer_height), 1e-9)
    by_layer = {}
    for path in paths:
        if path.travel:
            continue
        by_layer.setdefault(path.layer, []).append(path)
    values = []
    for layer in sorted(by_layer):
        below = by_layer.get(layer - 1)
        points = [p for path in by_layer[layer] for p in path.points]
        if not below:
            for point in points:
                point.overhang = 0.0
                values.append(0.0)
            continue
        # flatten the layer below into segments, indexed by their midpoint
        segments = []
        for path in below:
            positions = [p.position for p in path.points]
            if path.closed and len(positions) > 2:
                positions = positions + [positions[0]]
            for start, end in zip(positions[:-1], positions[1:]):
                segments.append((start, end))
        if not segments:
            for point in points:
                point.overhang = 90.0
                values.append(90.0)
            continue
        # the closed outline of the layer below, to tell a bead that steps
        # outward from one that steps inward onto solid material
        frame_u = _perpendicular(axis)
        frame_v = axis.cross(frame_u)

        def to_2d(position, u=frame_u, v=frame_v):
            return (position.dot(u), position.dot(v))

        outlines = []
        for path in below:
            if path.closed and len(path.points) >= 3:
                outlines.append([to_2d(p.position) for p in path.points])

        def supported(position):
            if not outlines:
                return False
            x, y = to_2d(position)
            inside = False
            for polygon in outlines:
                count = len(polygon)
                for i in range(count):
                    x0, y0 = polygon[i]
                    x1, y1 = polygon[(i + 1) % count]
                    if (y0 > y) != (y1 > y):
                        if x0 + (y - y0) / (y1 - y0) * (x1 - x0) > x:
                            inside = not inside
            return inside

        cell = max(layer_height * search, 1e-3)
        grid = {}

        def key(position, cell=cell):
            return (
                int(math.floor(position.x / cell)),
                int(math.floor(position.y / cell)),
                int(math.floor(position.z / cell)),
            )

        for index, (start, end) in enumerate(segments):
            # register both ends and the middle so a long segment is found
            for sample in (start, end, (start + end) * 0.5):
                grid.setdefault(key(sample), set()).add(index)
        for point in points:
            base = key(point.position)
            candidates = set()
            ring = 1
            while not candidates and ring <= 4:
                for dx in range(-ring, ring + 1):
                    for dy in range(-ring, ring + 1):
                        for dz in range(-ring, ring + 1):
                            candidates |= grid.get(
                                (base[0] + dx, base[1] + dy, base[2] + dz), set()
                            )
                ring += 1
            if not candidates:
                point.overhang = 90.0
                values.append(90.0)
                continue
            # measure sideways only: project everything onto the layer plane
            flat = point.position - axis * point.position.dot(axis)
            best = None
            for index in candidates:
                start, end = segments[index]
                flat_start = start - axis * start.dot(axis)
                flat_end = end - axis * end.dot(axis)
                distance = _point_segment_distance(flat, flat_start, flat_end)
                if best is None or distance < best:
                    best = distance
            if supported(point.position):
                best = 0.0
            point.overhang = math.degrees(math.atan2(best, layer_height))
            values.append(point.overhang)
    return values


def overhang_report(paths, layer_height, limit=45.0, axis=Vector(0, 0, 1)):
    """Summarise how far the toolpath hangs over itself."""
    values = compute_overhangs(paths, layer_height, axis)
    if not values:
        return {"points": 0, "max": 0.0, "mean": 0.0, "over_limit": 0, "fraction": 0.0}
    over = [v for v in values if v > limit]
    return {
        "points": len(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "over_limit": len(over),
        "fraction": len(over) / float(len(values)),
        "limit": float(limit),
    }


# ---------------------------------------------------------------------------
# Machine limits
# ---------------------------------------------------------------------------


def tilt_report(paths, reference=Vector(0, 0, 1), limit=45.0):
    """How far the tool leans away from ``reference``, in degrees."""
    reference = _normalize(reference)
    angles = []
    for path in paths:
        for point in path.points:
            cosine = max(-1.0, min(1.0, _normalize(point.axis).dot(reference)))
            angles.append(math.degrees(math.acos(cosine)))
    if not angles:
        return {"points": 0, "max": 0.0, "mean": 0.0, "over_limit": 0, "fraction": 0.0}
    over = [a for a in angles if a > limit]
    return {
        "points": len(angles),
        "max": max(angles),
        "mean": sum(angles) / len(angles),
        "over_limit": len(over),
        "fraction": len(over) / float(len(angles)),
        "limit": float(limit),
    }


def clearance_report(paths, tool_radius=25.0, tool_length=120.0, samples=4, ignore=30.0):
    """Look for the nozzle body running into what has already been printed.

    The tool is modelled as a cylinder standing on the nozzle along the
    tool axis. For every print point the cylinder is sampled and each
    sample is checked against the points printed before it. Points printed
    less than ``ignore`` millimetres of path ago are skipped, since the
    bead just laid down is always under the nozzle.

    This is a cheap proximity test, not a solid collision check: it finds
    the obvious crashes, not every one.
    """
    printed = []
    order = []
    for path in paths:
        if path.travel:
            continue
        for point in path.points:
            printed.append(point.position)
            order.append(point)
    if len(printed) < 3:
        return {"points": len(printed), "hits": 0, "fraction": 0.0, "first": None}
    cell = max(float(tool_radius), 1e-3)
    grid = _PointGrid(printed, cell)
    hits = 0
    first = None
    samples = max(1, int(samples))
    skip = max(1, int(ignore))
    for index, point in enumerate(order):
        axis = _normalize(point.axis)
        collided = False
        for step in range(1, samples + 1):
            centre = point.position + axis * (tool_length * step / float(samples))
            for other in grid.near(centre):
                if other >= index - skip:
                    continue
                if (printed[other] - centre).Length < tool_radius:
                    collided = True
                    break
            if collided:
                break
        if collided:
            hits += 1
            if first is None:
                first = Vector(point.position)
    return {
        "points": len(printed),
        "hits": hits,
        "fraction": hits / float(len(printed)),
        "first": first,
        "tool_radius": float(tool_radius),
        "tool_length": float(tool_length),
    }


# ---------------------------------------------------------------------------
# The four toolpath quality metrics
# ---------------------------------------------------------------------------


def continuity_report(paths):
    """How often extrusion is interrupted.

    Large format printing wants one continuous bead: every stop is a
    blob, a stringing risk and a weak point. This counts the separate
    extruding paths and how far the head travels dry between them.
    """
    extruding = [p for p in paths if not p.travel and len(p.points) >= 2]
    if not extruding:
        return {"paths": 0, "interruptions": 0, "travel_length": 0.0, "printing_length": 0.0}
    travel = 0.0
    for first, second in zip(extruding[:-1], extruding[1:]):
        travel += (second.points[0].position - first.points[-1].position).Length
    printing = sum(p.length() for p in extruding)
    return {
        "paths": len(extruding),
        "interruptions": len(extruding) - 1,
        "travel_length": travel,
        "printing_length": printing,
        "travel_ratio": travel / printing if printing else 0.0,
    }


def cornering_report(paths, threshold=60.0):
    """How many sharp corners the path takes, where the bead deforms."""
    from . import toolpath as _toolpath

    corners = 0
    points = 0
    sharpest = 0.0
    for path in paths:
        if path.travel:
            continue
        points += len(path.points)
        found = _toolpath.path_corners(path, threshold)
        corners += len(found)
        for index in found:
            before = path.points[index].position - path.points[index - 1].position
            after = (
                path.points[(index + 1) % len(path.points)].position - path.points[index].position
            )
            if before.Length > 1e-9 and after.Length > 1e-9:
                cosine = max(-1.0, min(1.0, _normalize(before).dot(_normalize(after))))
                sharpest = max(sharpest, math.degrees(math.acos(cosine)))
    return {
        "points": points,
        "corners": corners,
        "sharpest": sharpest,
        "threshold": float(threshold),
        "fraction": corners / float(points) if points else 0.0,
    }


def surface_tolerance_report(paths, shape, samples=400):
    """How far the toolpath strays from the surface of the model.

    A layer that is too coarse, or an offset that collapsed, shows up here
    as a large deviation. Points are sampled evenly to keep the check
    quick on a path with hundreds of thousands of points.
    """
    positions = [p.position for path in paths if not path.travel for p in path.points]
    if not positions or shape is None:
        return {"points": 0, "max": 0.0, "mean": 0.0}
    step = max(1, len(positions) // max(1, int(samples)))
    sampled = positions[::step]
    import Part

    # Measure against the skin, not the solid: a point inside a solid is
    # zero away from it, which would hide every deviation that matters.
    skin = shape
    try:
        if shape.Faces:
            skin = Part.Compound(shape.Faces)
    except Exception:  # pylint: disable=broad-except
        skin = shape
    distances = []
    for position in sampled:
        try:
            distances.append(skin.distToShape(Part.Vertex(position))[0])
        except Exception:  # pylint: disable=broad-except
            continue
    if not distances:
        return {"points": 0, "max": 0.0, "mean": 0.0}
    return {
        "points": len(distances),
        "max": max(distances),
        "mean": sum(distances) / len(distances),
    }


def quality_report(
    paths,
    layer_height,
    shape=None,
    overhang_limit=45.0,
    tilt_limit=45.0,
    corner_threshold=60.0,
    reference=Vector(0, 0, 1),
):
    """The four checks worth running before a program leaves the desk.

    Overhang, cornering, surface tolerance and continuity are the metrics
    the commercial robotic slicers report, so they are the ones worth
    matching.
    """
    report = {
        "overhang": overhang_report(paths, layer_height, overhang_limit, reference),
        "cornering": cornering_report(paths, corner_threshold),
        "continuity": continuity_report(paths),
        "tilt": tilt_report(paths, reference, tilt_limit),
    }
    if shape is not None:
        report["surface_tolerance"] = surface_tolerance_report(paths, shape)
    return report


# ---------------------------------------------------------------------------
# Time and material
# ---------------------------------------------------------------------------


def estimate(paths, speed=3000.0, travel_speed=9000.0, density=1.24):
    """Length, volume, mass and time of a toolpath.

    ``speed`` and ``travel_speed`` are in millimetres per minute, the unit
    G-code uses; ``density`` is in grams per cubic centimetre, 1.24 being
    about right for PLA. Per point speeds override the default when set.
    """
    printing = travel = 0.0
    volume = 0.0
    seconds = 0.0
    for path in paths:
        points = path.points
        if len(points) < 2:
            continue
        pairs = list(zip(points[:-1], points[1:]))
        if path.closed and len(points) > 2:
            pairs.append((points[-1], points[0]))
        for start, end in pairs:
            distance = (end.position - start.position).Length
            if path.travel:
                travel += distance
                rate = travel_speed
            else:
                printing += distance
                volume += distance * bead_area(start.width, start.height) * start.extrusion
                rate = start.speed or speed
            if rate > 0:
                seconds += distance / (rate / 60.0)
    grams = volume * 1e-3 * float(density)
    return {
        "printing_length": printing,
        "travel_length": travel,
        "volume": volume,
        "mass": grams,
        "seconds": seconds,
        "hours": seconds / 3600.0,
    }
