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

"""Turning layers into a robot toolpath.

The unit of a toolpath is the :class:`PrintPoint`: a position, the tool
axis to hold there, and the process values that belong to it. A
:class:`Path` is a run of print points the machine executes without
stopping extrusion; a travel path moves between them dry.

What this module does, in the order the pipeline runs:

perimeters
    inset copies of every layer contour, one per bead
infill
    a pattern clipped to the layer, lifted back onto the layer surface so
    it still works when the layer is a cone or follows a curved base
ordering
    nearest neighbour sorting so the head does not cross the part
spiralization
    joining the perimeters of every layer into one continuous helix, which
    is what large format printing wants: no stops, no seams, no retracts
tool orientation
    fixed, along the layer normal, or tilted by a lead angle, with a limit
    on how far the tool may lean
"""

import math

import Part
from FreeCAD import Vector

from . import slicing

__all__ = [
    "INFILL_PATTERNS",
    "ORIENTATION_MODES",
    "PrintPoint",
    "Path",
    "field_gradient",
    "offset_contour",
    "offset_layer",
    "infill_paths",
    "order_paths",
    "spiralize",
    "apply_tool_axes",
    "generate_toolpath",
    "toolpath_length",
    "smooth_orientations",
    "corner_compensation",
    "reinforce_overhangs",
    "path_corners",
]

INFILL_PATTERNS = ("None", "Lines", "Grid", "Triangles", "Concentric")
ORIENTATION_MODES = ("Fixed", "LayerNormal", "Tilted")


def _normalize(vector, fallback=Vector(0, 0, 1)):
    length = vector.Length
    if length < 1e-12:
        return Vector(fallback)
    return Vector(vector.x / length, vector.y / length, vector.z / length)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class PrintPoint:
    """One position of the nozzle, with the tool axis and process values."""

    __slots__ = (
        "position",
        "axis",
        "layer",
        "width",
        "height",
        "speed",
        "extrusion",
        "overhang",
        "travel",
    )

    def __init__(self, position, axis=Vector(0, 0, 1), layer=0, width=0.0, height=0.0, speed=0.0):
        self.position = Vector(position)
        self.axis = Vector(axis)
        self.layer = int(layer)
        self.width = float(width)
        self.height = float(height)
        self.speed = float(speed)
        # a multiplier on the nominal flow, which is what the deposition
        # modifiers below change when they reinforce a region
        self.extrusion = 1.0
        self.overhang = 0.0
        self.travel = False

    def __repr__(self):
        return "PrintPoint(%.2f, %.2f, %.2f)" % (
            self.position.x,
            self.position.y,
            self.position.z,
        )

    def copy(self):
        point = PrintPoint(
            self.position, self.axis, self.layer, self.width, self.height, self.speed
        )
        point.extrusion = self.extrusion
        point.overhang = self.overhang
        point.travel = self.travel
        return point


class Path:
    """A run of print points executed without interrupting extrusion."""

    __slots__ = ("points", "closed", "travel", "layer", "role")

    def __init__(self, points=None, closed=False, travel=False, layer=0, role="perimeter"):
        self.points = list(points or [])
        self.closed = bool(closed)
        self.travel = bool(travel)
        self.layer = int(layer)
        self.role = role

    def __len__(self):
        return len(self.points)

    def __iter__(self):
        return iter(self.points)

    def __repr__(self):
        return "Path(%d points, %s, %s)" % (
            len(self.points),
            self.role,
            "travel" if self.travel else "extruding",
        )

    def positions(self):
        return [p.position for p in self.points]

    def length(self):
        total = 0.0
        points = self.points
        for i in range(len(points) - 1):
            total += (points[i + 1].position - points[i].position).Length
        if self.closed and len(points) > 2:
            total += (points[0].position - points[-1].position).Length
        return total


# ---------------------------------------------------------------------------
# Layer geometry
# ---------------------------------------------------------------------------


def field_gradient(field, point, step=0.05):
    """Numerical gradient of a scalar field: the local layer normal."""
    dx = field(point + Vector(step, 0, 0)) - field(point - Vector(step, 0, 0))
    dy = field(point + Vector(0, step, 0)) - field(point - Vector(0, step, 0))
    dz = field(point + Vector(0, 0, step)) - field(point - Vector(0, 0, step))
    return _normalize(Vector(dx, dy, dz))


def project_to_field(point, field, value, direction=Vector(0, 0, 1), span=1000.0, steps=40):
    """Slide ``point`` along ``direction`` until the field reaches ``value``.

    This is how a pattern drawn on a flat plane is lifted back onto a
    curved layer. Returns ``None`` when the field never reaches the value
    inside ``span``.
    """
    direction = _normalize(direction)
    start = field(point)
    if abs(start - value) < 1e-9:
        return Vector(point)
    low, high = -span, span
    fa = field(point + direction * low) - value
    fb = field(point + direction * high) - value
    if (fa < 0) == (fb < 0):
        return None
    for _ in range(int(steps)):
        middle = (low + high) * 0.5
        fm = field(point + direction * middle) - value
        if (fm < 0) == (fa < 0):
            low, fa = middle, fm
        else:
            high = middle
    return point + direction * ((low + high) * 0.5)


def _contour_wire(contour):
    points = contour.points
    if len(points) < 2:
        return None
    loop = points + [points[0]] if contour.closed else points
    try:
        return Part.makePolygon(loop)
    except Part.OCCError:
        return None


def offset_contour(contour, distance, normal):
    """Offset one contour inside its own layer.

    A planar contour is offset exactly with the kernel. A contour on a
    curved layer is offset by moving every point along the in-layer
    normal, which is correct for gentle curvature and may self intersect
    on a tight concave corner.
    """
    points = contour.points
    if len(points) < 3 or abs(distance) < 1e-12:
        return [contour.copy()]
    normal = _normalize(normal)
    planar = all(abs((p - points[0]).dot(normal)) < 1e-6 for p in points)
    if planar and contour.closed:
        wire = _contour_wire(contour)
        if wire is not None:
            try:
                result = wire.makeOffset2D(distance)
                out = []
                for offset_wire in result.Wires:
                    vertexes = [v.Point for v in offset_wire.OrderedVertexes]
                    if len(vertexes) >= 3:
                        out.append(slicing.Contour(vertexes, True))
                return out
            except Exception:  # pylint: disable=broad-except
                # the kernel raises several error types when an offset
                # collapses, which simply means the contour is too small
                return []
    # walk the contour and push each point sideways inside the layer
    count = len(points)
    moved = []
    for i in range(count):
        if contour.closed:
            before, after = points[i - 1], points[(i + 1) % count]
        else:
            before = points[max(0, i - 1)]
            after = points[min(count - 1, i + 1)]
        tangent = after - before
        if tangent.Length < 1e-12:
            continue
        sideways = _normalize(normal.cross(tangent))
        moved.append(points[i] + sideways * distance)
    if len(moved) < 3:
        return []
    return [slicing.Contour(moved, contour.closed)]


def offset_layer(layer, normal, distance):
    """Offset a whole layer inwards, offsetting holes the other way."""
    groups = slicing.nest_contours(layer, normal)
    result = []
    for outer, holes in groups:
        # every contour wound the same way, then the outline shrinks and
        # the holes grow, which is what removing a bead of material does
        slicing.orient_contour(outer, normal, True)
        result.extend(offset_contour(outer, -abs(distance), normal))
        for hole in holes:
            slicing.orient_contour(hole, normal, True)
            result.extend(offset_contour(hole, abs(distance), normal))
    open_contours = [c for c in layer if not c.closed]
    result.extend(c.copy() for c in open_contours)
    return result


# ---------------------------------------------------------------------------
# Infill
# ---------------------------------------------------------------------------


def _layer_frame(layer, fallback_normal=Vector(0, 0, 1)):
    """Origin and axes of the best fit plane of a layer."""
    points = [p for contour in layer for p in contour.points]
    if len(points) < 3:
        return Vector(), Vector(1, 0, 0), Vector(0, 1, 0), _normalize(fallback_normal)
    origin = Vector()
    for p in points:
        origin += p
    origin *= 1.0 / len(points)
    normal = Vector()
    for contour in layer:
        normal += slicing.contour_normal(contour) * abs(slicing.contour_area(contour))
    normal = _normalize(normal, fallback_normal)
    u = slicing._perpendicular(normal)
    return origin, u, normal.cross(u), normal


def _point_in_polygons(x, y, polygons):
    """Even-odd test of a 2D point against a list of 2D polygons."""
    inside = False
    for polygon in polygons:
        count = len(polygon)
        for i in range(count):
            x0, y0 = polygon[i]
            x1, y1 = polygon[(i + 1) % count]
            if (y0 > y) != (y1 > y):
                crossing = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
                if crossing > x:
                    inside = not inside
    return inside


def _clip_line(y, polygons, x_min, x_max):
    """Spans of the horizontal line at ``y`` that fall inside the polygons."""
    crossings = []
    for polygon in polygons:
        count = len(polygon)
        for i in range(count):
            x0, y0 = polygon[i]
            x1, y1 = polygon[(i + 1) % count]
            if (y0 > y) != (y1 > y):
                crossings.append(x0 + (y - y0) / (y1 - y0) * (x1 - x0))
    crossings.sort()
    spans = []
    for i in range(0, len(crossings) - 1, 2):
        start, end = crossings[i], crossings[i + 1]
        if end - start > 1e-9:
            spans.append((max(start, x_min), min(end, x_max)))
    return [s for s in spans if s[1] - s[0] > 1e-9]


def infill_paths(
    layer, spacing, pattern="Lines", angle=45.0, field=None, value=0.0, axis=Vector(0, 0, 1)
):
    """Infill a layer with a pattern, clipped to its contours.

    The pattern is drawn on the best fit plane of the layer. When a
    ``field`` is given the points are lifted back onto the real layer
    surface, which is what keeps infill usable on conical and conformal
    layers.
    """
    if pattern in (None, "None") or spacing <= 0:
        return []
    closed = [c for c in layer if c.closed and len(c.points) >= 3]
    if not closed:
        return []
    origin, u, v, normal = _layer_frame(layer)

    def to_2d(point):
        d = point - origin
        return (d.dot(u), d.dot(v))

    def to_3d(x, y):
        return origin + u * x + v * y

    polygons = [[to_2d(p) for p in contour.points] for contour in closed]
    if pattern == "Concentric":
        contours = []
        current = layer
        for _ in range(200):
            current = offset_layer(current, normal, spacing)
            current = [c for c in current if c.closed and len(c.points) >= 3]
            if not current:
                break
            contours.extend(current)
        return [
            Path(
                [PrintPoint(p) for p in contour.points],
                closed=contour.closed,
                role="infill",
            )
            for contour in contours
        ]

    directions = [float(angle)]
    if pattern == "Grid":
        directions.append(float(angle) + 90.0)
    elif pattern == "Triangles":
        directions.extend([float(angle) + 60.0, float(angle) + 120.0])

    paths = []
    for direction in directions:
        radians = math.radians(direction)
        cos, sin = math.cos(radians), math.sin(radians)
        # rotate the polygons so the pattern is always horizontal lines
        rotated = [
            [(x * cos + y * sin, -x * sin + y * cos) for x, y in polygon] for polygon in polygons
        ]
        rys = [p[1] for polygon in rotated for p in polygon]
        rxs = [p[0] for polygon in rotated for p in polygon]
        start = math.ceil(min(rys) / spacing) * spacing
        line = start
        flip = False
        while line <= max(rys):
            for span in _clip_line(line, rotated, min(rxs), max(rxs)):
                a, b = span if not flip else (span[1], span[0])
                ends = []
                for x in (a, b):
                    point = to_3d(x * cos - line * sin, x * sin + line * cos)
                    if field is not None:
                        lifted = project_to_field(point, field, value, axis)
                        point = lifted if lifted is not None else point
                    ends.append(point)
                paths.append(Path([PrintPoint(p) for p in ends], role="infill"))
            flip = not flip
            line += spacing
    return paths


# ---------------------------------------------------------------------------
# Ordering, spiralization and tool orientation
# ---------------------------------------------------------------------------


def order_paths(paths, start=Vector(0, 0, 0)):
    """Sort paths nearest first, reversing open ones when that is closer."""
    remaining = list(paths)
    ordered = []
    cursor = Vector(start)
    while remaining:
        best, best_distance, best_reverse = None, None, False
        for path in remaining:
            if not path.points:
                continue
            head = (path.points[0].position - cursor).Length
            tail = (path.points[-1].position - cursor).Length
            if best_distance is None or min(head, tail) < best_distance:
                best = path
                best_reverse = tail < head and not path.closed
                best_distance = min(head, tail)
        if best is None:
            break
        remaining.remove(best)
        if best_reverse:
            best.points.reverse()
        ordered.append(best)
        cursor = best.points[-1].position if not best.closed else best.points[0].position
    return ordered


def spiralize(paths, layer_values=None):
    """Join a stack of closed paths into one continuous helix.

    Large format printing wants the extruder to never stop: the classic
    trick is to ramp the height of every loop by one layer over its own
    length so the end of a loop meets the start of the next.
    """
    loops = [p for p in paths if p.closed and len(p.points) >= 3]
    if len(loops) < 2:
        return list(paths)
    spiral = Path(role="spiral", layer=loops[0].layer)
    for index, loop in enumerate(loops[:-1]):
        nxt = loops[index + 1]
        # start the next loop at the point closest to where this one ends
        shift = min(
            range(len(nxt.points)),
            key=lambda i: (nxt.points[i].position - loop.points[-1].position).Length,
        )
        nxt.points = nxt.points[shift:] + nxt.points[:shift]
        rise = nxt.points[0].position - loop.points[0].position
        count = len(loop.points)
        for position, point in enumerate(loop.points):
            blended = point.copy()
            blended.position = point.position + rise * (position / float(count))
            spiral.points.append(blended)
    spiral.points.extend(p.copy() for p in loops[-1].points)
    return [spiral]


def apply_tool_axes(
    paths,
    mode="LayerNormal",
    field=None,
    fixed_axis=Vector(0, 0, 1),
    lead_angle=0.0,
    max_tilt=45.0,
):
    """Set the tool axis of every print point.

    ``Fixed`` holds one axis, the way a three axis machine prints.
    ``LayerNormal`` stands the tool on the layer surface, which is what a
    six axis arm is for. ``Tilted`` leans it forward along the path by
    ``lead_angle``. ``max_tilt`` limits how far the tool may lean away
    from ``fixed_axis``, which keeps a program inside the reach of the
    machine.
    """
    fixed_axis = _normalize(fixed_axis)
    limit = math.cos(math.radians(max(0.0, min(180.0, float(max_tilt)))))
    for path in paths:
        points = path.points
        for index, point in enumerate(points):
            if mode == "Fixed" or field is None:
                axis = Vector(fixed_axis)
            else:
                axis = field_gradient(field, point.position)
                if axis.dot(fixed_axis) < 0:
                    axis = axis * -1.0
            if mode == "Tilted" and abs(lead_angle) > 1e-9 and len(points) > 1:
                after = points[min(len(points) - 1, index + 1)].position
                before = points[max(0, index - 1)].position
                tangent = after - before
                if tangent.Length > 1e-9:
                    tangent = _normalize(tangent - axis * tangent.dot(axis))
                    radians = math.radians(lead_angle)
                    axis = _normalize(axis * math.cos(radians) + tangent * math.sin(radians))
            if axis.dot(fixed_axis) < limit:
                # lean back until the tool is inside the allowed cone
                sideways = axis - fixed_axis * axis.dot(fixed_axis)
                if sideways.Length > 1e-9:
                    sideways = _normalize(sideways)
                    angle = math.radians(float(max_tilt))
                    axis = _normalize(fixed_axis * math.cos(angle) + sideways * math.sin(angle))
                else:
                    axis = Vector(fixed_axis)
            point.axis = axis
    return paths


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def generate_toolpath(
    layers,
    values=None,
    bead_width=8.0,
    layer_height=4.0,
    perimeters=2,
    infill_pattern="None",
    infill_spacing=0.0,
    infill_angle=45.0,
    point_spacing=0.0,
    smoothing=0,
    orientation="LayerNormal",
    field=None,
    axis=Vector(0, 0, 1),
    lead_angle=0.0,
    max_tilt=45.0,
    speed=3000.0,
    spiral=False,
):
    """Build the paths of every layer, in print order.

    Returns a flat list of :class:`Path`. ``values`` are the field values
    of the layers, needed to lift infill onto a curved layer.
    """
    result = []
    cursor = Vector(0, 0, 0)
    for index, layer in enumerate(layers):
        if not layer:
            continue
        value = values[index] if values and index < len(values) else 0.0
        _, _, _, normal = _layer_frame(layer, axis)
        layer_paths = []
        outline = [c.copy() for c in layer]
        current = outline
        for ring in range(max(1, int(perimeters))):
            # The bead is laid along its centre line, so the first wall runs
            # half a bead inside the surface and every further wall a whole
            # bead further in. Without this the part comes out a bead too big.
            current = offset_layer(current, normal, bead_width * 0.5 if ring == 0 else bead_width)
            if not current:
                if ring == 0:
                    # A feature narrower than a bead has no room for the
                    # offset; one bead down the middle of it is the best
                    # the nozzle can do, so print the outline itself.
                    current = outline
                else:
                    break
            for contour in current:
                shaped = contour
                if point_spacing > 0:
                    shaped = slicing.resample_contour(shaped, point_spacing)
                if smoothing:
                    shaped = slicing.smooth_contour(shaped, smoothing)
                if len(shaped.points) < 2:
                    continue
                layer_paths.append(
                    Path(
                        [
                            PrintPoint(p, axis, index, bead_width, layer_height, speed)
                            for p in shaped.points
                        ],
                        closed=shaped.closed,
                        layer=index,
                        role="perimeter" if ring == 0 else "inset",
                    )
                )
        if infill_pattern not in (None, "None"):
            # Spacing of zero means beads that just touch, which is a solid fill.
            spacing = infill_spacing if infill_spacing > 0 else bead_width
            inner = offset_layer(current, normal, bead_width * 0.5) if current else []
            for path in infill_paths(
                inner or current,
                spacing,
                infill_pattern,
                infill_angle + index * 90.0,
                field=field,
                value=value,
                axis=axis,
            ):
                for point in path.points:
                    point.layer = index
                    point.width = bead_width
                    point.height = layer_height
                    point.speed = speed
                path.layer = index
                layer_paths.append(path)
        layer_paths = order_paths(layer_paths, cursor)
        if layer_paths and layer_paths[-1].points:
            cursor = layer_paths[-1].points[-1].position
        result.extend(layer_paths)
    if spiral:
        result = spiralize([p for p in result if p.role in ("perimeter", "spiral")])
    apply_tool_axes(result, orientation, field, axis, lead_angle, max_tilt)
    return result


def smooth_orientations(paths, max_change=10.0, passes=2):
    """Limit how fast the tool axis may turn between two print points.

    A six axis arm cannot snap its wrist around between one bead point and
    the next: an orientation that jumps is what makes a program stutter or
    hit a singularity. Each pass pulls any step larger than ``max_change``
    degrees back towards its neighbours.
    """
    limit = math.cos(math.radians(max(0.0, float(max_change))))
    for _ in range(max(0, int(passes))):
        for path in paths:
            points = path.points
            for index in range(1, len(points)):
                previous = _normalize(points[index - 1].axis)
                current = _normalize(points[index].axis)
                if current.dot(previous) >= limit:
                    continue
                blended = previous + current
                if blended.Length < 1e-9:
                    continue
                points[index].axis = _normalize(blended)
    return paths


def path_corners(path, threshold=60.0):
    """Indices of the points where the path turns more sharply than ``threshold``."""
    points = path.points
    count = len(points)
    if count < 3:
        return []
    corners = []
    span = range(count) if path.closed else range(1, count - 1)
    for index in span:
        before = points[index].position - points[index - 1].position
        after = points[(index + 1) % count].position - points[index].position
        if before.Length < 1e-9 or after.Length < 1e-9:
            continue
        cosine = max(-1.0, min(1.0, _normalize(before).dot(_normalize(after))))
        turn = math.degrees(math.acos(cosine))
        if turn >= threshold:
            corners.append(index)
    return corners


def corner_compensation(paths, threshold=60.0, speed_factor=0.5, extrusion_factor=1.15, spread=2):
    """Slow down and thicken the bead through sharp corners.

    Material keeps flowing while the arm decelerates into a corner, so a
    corner comes out rounded on the outside and starved on the inside.
    Commercial robotic slicers compensate by dropping the speed and adding
    material there; ``spread`` is how many points either side of the
    corner are affected.
    """
    touched = 0
    for path in paths:
        if path.travel:
            continue
        points = path.points
        count = len(points)
        for index in path_corners(path, threshold):
            for offset in range(-int(spread), int(spread) + 1):
                position = index + offset
                if path.closed:
                    position %= count
                elif position < 0 or position >= count:
                    continue
                weight = 1.0 - abs(offset) / float(spread + 1)
                point = points[position]
                point.speed = point.speed * (1.0 - weight * (1.0 - speed_factor))
                point.extrusion *= 1.0 + weight * (extrusion_factor - 1.0)
                touched += 1
    return touched


def reinforce_overhangs(
    paths, limit=45.0, extrusion_factor=1.25, speed_factor=0.6, width_factor=1.0
):
    """Ramp the flow up and the speed down where the path hangs over itself.

    This is the deposition modifier half of the analyse, filter, modify
    loop the commercial tools expose: overhang analysis marks the points,
    this reinforces them, and the ramp is gradual so the bead does not
    change abruptly. Run :func:`roboprint.analysis.compute_overhangs`
    first to fill in the overhang of each point.
    """
    touched = 0
    limit = float(limit)
    for path in paths:
        if path.travel:
            continue
        for point in path.points:
            if point.overhang <= limit:
                continue
            # ramp in over the range between the limit and vertical
            weight = min(1.0, (point.overhang - limit) / max(90.0 - limit, 1e-6))
            point.extrusion *= 1.0 + weight * (extrusion_factor - 1.0)
            point.speed = point.speed * (1.0 - weight * (1.0 - speed_factor))
            point.width *= 1.0 + weight * (width_factor - 1.0)
            touched += 1
    return touched


def add_travel_moves(paths, clearance=0.0, speed=9000.0):
    """Insert a lift, a cross and a descent between printing paths.

    Without this the nozzle goes straight from the end of one path to the
    start of the next at printing height, dragging through whatever is in
    the way. Lifting along the tool axis is the robotic equivalent of a
    z-hop: on a leaning nozzle "up" is the tool axis, not the build axis.
    ``clearance`` of zero leaves the direct move alone.
    """
    if clearance <= 0:
        return list(paths)
    printing = [p for p in paths if not p.travel]
    if len(printing) < 2:
        return list(paths)
    result = []
    for index, path in enumerate(printing):
        result.append(path)
        if index + 1 >= len(printing):
            break
        following = printing[index + 1]
        last = path.points[0] if path.closed else path.points[-1]
        first = following.points[0]
        lift = _normalize(last.axis) * float(clearance)
        drop = _normalize(first.axis) * float(clearance)
        corners = [last.position + lift, first.position + drop]
        if (corners[0] - corners[1]).Length < 1e-9:
            continue
        points = [
            PrintPoint(position, Vector(first.axis), following.layer, speed=float(speed))
            for position in corners
        ]
        for point in points:
            point.travel = True
        result.append(Path(points, closed=False, travel=True, layer=following.layer, role="travel"))
    return result


def toolpath_length(paths, extruding_only=True):
    """Total length of the toolpath."""
    return sum(p.length() for p in paths if not (extruding_only and p.travel))
