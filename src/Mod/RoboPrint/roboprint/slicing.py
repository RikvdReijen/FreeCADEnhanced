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

"""Slicing a part into layers for robotic additive manufacturing.

Large format robotic printing is not limited to flat layers: a six axis
arm can lay beads on cones, cylinders and free-form surfaces, which is how
overhangs are printed without support and how a part is printed onto an
existing surface. All of those are the same operation on a different
scalar field, so this module has one slicer:

:func:`slice_field`
    marching triangles over a mesh, extracting the iso-contours of any
    scalar field at a list of values

and a set of fields built on top of it:

``planar``
    height along an axis, the familiar flat layers
``cylindrical``
    distance from an axis, layers wrapped around a mandrel
``conical``
    height minus radius over the tangent of a half angle, the conical
    slicing that lets an arm print overhangs support free
``spherical``
    distance from a point
``conformal``
    distance to a reference surface, so layers follow an existing part

The slicer works on a triangle mesh, so it accepts a mesh object, a
tessellated shape, or raw points and triangles. Everything it returns is
plain ``FreeCAD.Vector`` data: a layer is a list of contours, a contour is
a list of points plus a closed flag.
"""

import bisect
import math

from FreeCAD import Vector

__all__ = [
    "SLICING_MODES",
    "Contour",
    "slice_field",
    "slice_mesh",
    "planar_field",
    "cylindrical_field",
    "conical_field",
    "spherical_field",
    "conformal_field",
    "field_for_mode",
    "mesh_of",
    "refine_mesh",
    "max_edge_length",
    "contour_area",
    "contour_normal",
    "orient_contour",
    "nest_contours",
    "resample_contour",
    "smooth_contour",
    "align_seams",
]

SLICING_MODES = ("Planar", "Cylindrical", "Conical", "Spherical", "Conformal")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _normalize(vector, fallback=Vector(0, 0, 1)):
    length = vector.Length
    if length < 1e-12:
        return Vector(fallback)
    return Vector(vector.x / length, vector.y / length, vector.z / length)


def _perpendicular(axis):
    axis = _normalize(axis)
    helper = Vector(1, 0, 0) if abs(axis.x) < 0.9 else Vector(0, 1, 0)
    return _normalize(axis.cross(helper))


class Contour:
    """One closed or open chain of points inside a layer."""

    __slots__ = ("points", "closed")

    def __init__(self, points, closed=False):
        self.points = [Vector(p) for p in points]
        self.closed = bool(closed)
        # One convention throughout: a closed contour does not repeat its
        # first point. Closing is implied, so nothing downstream has to ask
        # whether a given contour happens to repeat it or not.
        while (
            self.closed
            and len(self.points) > 2
            and (self.points[-1] - self.points[0]).Length < 1e-9
        ):
            self.points.pop()

    def __len__(self):
        return len(self.points)

    def __iter__(self):
        return iter(self.points)

    def __repr__(self):
        return "Contour(%d points, %s)" % (len(self.points), "closed" if self.closed else "open")

    def length(self):
        total = 0.0
        for i in range(len(self.points) - 1):
            total += (self.points[i + 1] - self.points[i]).Length
        if self.closed and len(self.points) > 2:
            total += (self.points[0] - self.points[-1]).Length
        return total

    def centroid(self):
        if not self.points:
            return Vector()
        total = Vector()
        for p in self.points:
            total += p
        return total * (1.0 / len(self.points))

    def copy(self):
        return Contour(self.points, self.closed)


# ---------------------------------------------------------------------------
# Mesh input
# ---------------------------------------------------------------------------


def mesh_of(source, tolerance=0.1):
    """Return ``(points, triangles)`` for a mesh object, a shape or raw data.

    ``tolerance`` is the tessellation deviation used when a Part shape has
    to be meshed first.
    """
    if isinstance(source, (tuple, list)) and len(source) == 2:
        points, triangles = source
        return [Vector(p) for p in points], [tuple(t) for t in triangles]
    mesh = getattr(source, "Mesh", None)
    if mesh is not None:
        points, facets = mesh.Topology
        return [Vector(p) for p in points], [tuple(f) for f in facets]
    if hasattr(source, "Topology") and not hasattr(source, "Faces"):
        points, facets = source.Topology
        return [Vector(p) for p in points], [tuple(f) for f in facets]
    shape = getattr(source, "Shape", source)
    if shape is None or not hasattr(shape, "tessellate"):
        raise ValueError("cannot slice %r: no mesh and no shape" % (source,))
    points, triangles = shape.tessellate(tolerance)
    return [Vector(p) for p in points], [tuple(t) for t in triangles]


def max_edge_length(points, triangles):
    """Longest triangle edge in the mesh."""
    longest = 0.0
    for triangle in triangles:
        for k in range(3):
            a = points[triangle[k]]
            b = points[triangle[(k + 1) % 3]]
            longest = max(longest, (b - a).Length)
    return longest


def refine_mesh(points, triangles, target_edge, budget=400000):
    """Subdivide until no edge is longer than ``target_edge``.

    Marching triangles assumes the field varies linearly inside a
    triangle. That holds for planar slicing but not for a curved field, so
    a coarse mesh (a box tessellates to twelve triangles) has to be
    refined first or whole layers come out empty.

    Only edges that are actually too long are split, and a split edge
    splits both triangles sharing it, so the mesh stays watertight with no
    hanging vertices. A tessellation that is already fine over the curved
    part of a shape is therefore left alone and only the long edges on the
    flat faces are brought down, instead of quadrupling the whole mesh.
    The budget caps the result so a tiny layer height cannot exhaust
    memory.
    """
    points = [Vector(p) for p in points]
    triangles = [tuple(t) for t in triangles]
    target_edge = float(target_edge)
    if target_edge <= 0 or not triangles:
        return points, triangles

    def key(i, j):
        return (i, j) if i < j else (j, i)

    while len(triangles) < budget:
        marked = set()
        for a, b, c in triangles:
            for i, j in ((a, b), (b, c), (c, a)):
                if (points[j] - points[i]).Length > target_edge:
                    marked.add(key(i, j))
        if not marked:
            break
        midpoints = {}
        for i, j in marked:
            midpoints[(i, j)] = len(points)
            points.append((points[i] + points[j]) * 0.5)
        refined = []
        for triangle in triangles:
            a, b, c = triangle
            splits = [midpoints.get(key(a, b)), midpoints.get(key(b, c)), midpoints.get(key(c, a))]
            count = sum(1 for split in splits if split is not None)
            if count == 0:
                refined.append(triangle)
                continue
            if count == 3:
                ab, bc, ca = splits
                refined.extend([(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)])
                continue
            # Turn the triangle so the split edges are the ones the pattern
            # below expects: a-b for one split, a-b and b-c for two.
            corners, splits = [a, b, c], list(splits)
            # One split: put it on a-b. Two splits: put the unsplit edge on c-a.
            while (splits[0] is None) if count == 1 else (splits[2] is not None):
                corners = corners[1:] + corners[:1]
                splits = splits[1:] + splits[:1]
            a, b, c = corners
            ab, bc = splits[0], splits[1]
            if count == 1:
                refined.extend([(a, ab, c), (ab, b, c)])
            else:
                refined.extend([(a, ab, bc), (ab, b, bc), (a, bc, c)])
        triangles = refined
    return points, triangles


# ---------------------------------------------------------------------------
# Scalar fields
# ---------------------------------------------------------------------------


def planar_field(axis=Vector(0, 0, 1), origin=Vector(0, 0, 0)):
    """Height measured along ``axis``: the familiar flat layers."""
    axis = _normalize(axis)
    origin = Vector(origin)

    def field(point):
        return (point - origin).dot(axis)

    return field


def cylindrical_field(axis=Vector(0, 0, 1), origin=Vector(0, 0, 0)):
    """Distance from the axis: layers wrapped around a mandrel."""
    axis = _normalize(axis)
    origin = Vector(origin)

    def field(point):
        d = point - origin
        return (d - axis * d.dot(axis)).Length

    return field


def conical_field(angle=30.0, axis=Vector(0, 0, 1), origin=Vector(0, 0, 0), inward=False):
    """Conical layers, the slicing that prints overhangs without support.

    ``angle`` is the half angle of the cone in degrees, measured from the
    plane perpendicular to ``axis``; 0 degrees is the same as planar
    slicing. ``inward`` flips the cone so it opens downwards.
    """
    axis = _normalize(axis)
    origin = Vector(origin)
    slope = math.tan(math.radians(float(angle)))
    if inward:
        slope = -slope

    def field(point):
        d = point - origin
        height = d.dot(axis)
        radius = (d - axis * height).Length
        return height - radius * slope

    return field


def spherical_field(centre=Vector(0, 0, 0)):
    """Distance from a point: layers as nested spherical shells."""
    centre = Vector(centre)

    def field(point):
        return (point - centre).Length

    return field


def conformal_field(surface_points, surface_triangles=None, axis=Vector(0, 0, 1)):
    """Distance to a reference surface, so layers follow an existing part.

    The surface is given as its tessellation. The distance is signed along
    ``axis`` when a point sits above the surface, which keeps the field
    monotonic for the build-up direction.
    """
    points = [Vector(p) for p in surface_points]
    if not points:
        raise ValueError("the reference surface has no points")
    axis = _normalize(axis)
    # a coarse grid over the surface keeps the nearest point search cheap
    extent = 0.0
    for p in points:
        extent = max(extent, abs(p.x), abs(p.y), abs(p.z))
    cell = max(extent / 32.0, 1e-6)
    grid = {}
    for index, p in enumerate(points):
        key = (int(p.x / cell), int(p.y / cell), int(p.z / cell))
        grid.setdefault(key, []).append(index)

    def nearest(point):
        base = (int(point.x / cell), int(point.y / cell), int(point.z / cell))
        best, ring = None, 0
        while best is None and ring < 64:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if ring and max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for index in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                            distance = (points[index] - point).Length
                            if best is None or distance < best[0]:
                                best = (distance, index)
            ring += 1
        return best or (0.0, 0)

    def field(point):
        distance, index = nearest(point)
        sign = 1.0 if (point - points[index]).dot(axis) >= 0 else -1.0
        return distance * sign

    return field


def field_for_mode(mode, axis=Vector(0, 0, 1), origin=Vector(0, 0, 0), angle=30.0, surface=None):
    """The scalar field of a named slicing mode (see :data:`SLICING_MODES`)."""
    if mode == "Planar":
        return planar_field(axis, origin)
    if mode == "Cylindrical":
        return cylindrical_field(axis, origin)
    if mode == "Conical":
        return conical_field(angle, axis, origin)
    if mode == "Spherical":
        return spherical_field(origin)
    if mode == "Conformal":
        if surface is None:
            raise ValueError("conformal slicing needs a reference surface")
        points, _ = mesh_of(surface)
        return conformal_field(points, axis=axis)
    raise ValueError("unknown slicing mode %r" % (mode,))


# ---------------------------------------------------------------------------
# The slicer
# ---------------------------------------------------------------------------


def _chain_segments(segments, tolerance=1e-5):
    """Chain unordered segments into contours, closing loops where possible."""
    if not segments:
        return []
    inverse = 1.0 / tolerance

    def key(point):
        return (round(point.x * inverse), round(point.y * inverse), round(point.z * inverse))

    adjacency = {}
    for index, (a, b) in enumerate(segments):
        adjacency.setdefault(key(a), []).append((index, 0))
        adjacency.setdefault(key(b), []).append((index, 1))

    used = [False] * len(segments)
    contours = []

    def walk(start_index, start_end):
        """Follow segments from one end of ``start_index``."""
        chain = []
        index, end = start_index, start_end
        while True:
            used[index] = True
            a, b = segments[index]
            head, tail = (a, b) if end == 0 else (b, a)
            if not chain:
                chain.append(head)
            chain.append(tail)
            candidates = adjacency.get(key(tail), ())
            nxt = None
            for candidate_index, candidate_end in candidates:
                if not used[candidate_index]:
                    nxt = (candidate_index, candidate_end)
                    break
            if nxt is None:
                return chain
            index, end = nxt

    for index in range(len(segments)):
        if used[index]:
            continue
        forward = walk(index, 0)
        # try to extend backwards from the original start point
        start = forward[0]
        backward = []
        for candidate_index, candidate_end in adjacency.get(key(start), ()):
            if not used[candidate_index]:
                backward = walk(candidate_index, candidate_end)
                break
        if backward:
            points = list(reversed(backward))[:-1] + forward
        else:
            points = forward
        closed = len(points) > 3 and (points[0] - points[-1]).Length <= tolerance * 10
        if closed:
            points = points[:-1]
        if len(points) >= 2:
            contours.append(Contour(points, closed))
    return contours


def slice_field(points, triangles, field, values, tolerance=1e-5):
    """Iso-contours of ``field`` over a triangle mesh, one layer per value.

    This is marching triangles: every triangle crossed by the iso value
    contributes one segment, and the segments are chained into contours.
    Returns a list of layers, each a list of :class:`Contour`.
    """
    scalars = [field(p) for p in points]
    values = list(values)
    # Only a triangle whose field range straddles a value can contribute a
    # segment to that layer, so bucket the triangles by the layers they can
    # reach instead of rescanning the whole mesh once per layer. On a refined
    # mesh each triangle spans about one layer, which turns the slice from
    # layers x triangles into something close to linear.
    order = sorted(range(len(values)), key=lambda i: values[i])
    sorted_values = [values[i] for i in order]
    buckets = [[] for _ in values]
    for triangle in triangles:
        a, b, c = triangle[0], triangle[1], triangle[2]
        fa, fb, fc = scalars[a], scalars[b], scalars[c]
        low = bisect.bisect_left(sorted_values, min(fa, fb, fc))
        high = bisect.bisect_right(sorted_values, max(fa, fb, fc))
        for position in range(low, high):
            buckets[order[position]].append(triangle)
    layers = []
    for index, value in enumerate(values):
        segments = []
        for triangle in buckets[index]:
            a, b, c = triangle[0], triangle[1], triangle[2]
            fa, fb, fc = scalars[a] - value, scalars[b] - value, scalars[c] - value
            # a triangle contributes a segment when the value is crossed
            crossings = []
            for (i, fi), (j, fj) in (((a, fa), (b, fb)), ((b, fb), (c, fc)), ((c, fc), (a, fa))):
                if (fi < 0) != (fj < 0):
                    t = fi / (fi - fj)
                    crossings.append(points[i] + (points[j] - points[i]) * t)
            if len(crossings) == 2 and (crossings[0] - crossings[1]).Length > tolerance:
                segments.append((crossings[0], crossings[1]))
        layers.append(_chain_segments(segments, tolerance))
    return layers


def slice_mesh(
    source,
    mode="Planar",
    layer_height=2.0,
    axis=Vector(0, 0, 1),
    origin=Vector(0, 0, 0),
    angle=30.0,
    surface=None,
    tolerance=0.1,
    first_layer=None,
    max_layers=20000,
    refine=True,
):
    """Slice ``source`` into layers using one of :data:`SLICING_MODES`.

    Returns ``(layers, values)``: the contours per layer and the field
    value each layer was taken at. ``first_layer`` offsets the first
    value, which is how the first bead is placed onto the build plate.
    A curved field needs a mesh finer than the layer height, so the mesh
    is refined first unless ``refine`` is off.
    """
    points, triangles = mesh_of(source, tolerance)
    if not triangles:
        raise ValueError("nothing to slice")
    if refine and mode != "Planar":
        points, triangles = refine_mesh(points, triangles, float(layer_height))
    field = field_for_mode(mode, axis, origin, angle, surface)
    scalars = [field(p) for p in points]
    low, high = min(scalars), max(scalars)
    layer_height = float(layer_height)
    if layer_height <= 0:
        raise ValueError("the layer height must be positive")
    start = low + (float(first_layer) if first_layer is not None else layer_height * 0.5)
    count = int((high - start) / layer_height) + 1
    if count < 1:
        raise ValueError("the layer height is larger than the part")
    if count > max_layers:
        raise ValueError(
            "slicing would produce %d layers, more than the %d allowed" % (count, max_layers)
        )
    values = [start + i * layer_height for i in range(count)]
    layers = slice_field(points, triangles, field, values, tolerance=1e-5)
    # Marching triangles emits one point per crossed edge, so a refined mesh
    # gives hundreds of nearly collinear points per loop. Dropping the ones
    # that say nothing keeps the contour within the tessellation's own
    # accuracy and makes everything downstream an order of magnitude faster.
    deviation = max(float(tolerance), 1e-4)
    layers = [[simplify_contour(contour, deviation) for contour in layer] for layer in layers]
    return layers, values


# ---------------------------------------------------------------------------
# Contour utilities
# ---------------------------------------------------------------------------


def simplify_contour(contour, deviation):
    """Drop points that lie within ``deviation`` of the chord they span.

    Ramer-Douglas-Peucker, run on the open chain of a closed loop so the
    seam point is kept.
    """
    points = contour.points
    if deviation <= 0 or len(points) < 3:
        return contour
    keep = [True] + [False] * (len(points) - 2) + [True]
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        start, end = points[first], points[last]
        span = end - start
        length = span.Length
        worst, at = -1.0, first
        for index in range(first + 1, last):
            offset = points[index] - start
            if length > 1e-12:
                distance = offset.cross(span).Length / length
            else:
                distance = offset.Length
            if distance > worst:
                worst, at = distance, index
        if worst > deviation:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    simplified = [p for p, wanted in zip(points, keep) if wanted]
    if contour.closed and len(simplified) > 3:
        # Both ends of the chain were forced to stay; on a loop the last one
        # may sit on the closing segment, where it says nothing.
        start, last, before = simplified[0], simplified[-1], simplified[-2]
        span = start - before
        length = span.Length
        offset = last - before
        distance = offset.cross(span).Length / length if length > 1e-12 else offset.Length
        if distance <= deviation:
            simplified.pop()
    if len(simplified) < (3 if contour.closed else 2):
        return contour
    return Contour(simplified, contour.closed)


def contour_normal(contour):
    """Best fit normal of a contour, from its enclosed area vector."""
    points = contour.points if isinstance(contour, Contour) else [Vector(p) for p in contour]
    if len(points) < 3:
        return Vector(0, 0, 1)
    area = Vector()
    for i in range(len(points)):
        area += points[i].cross(points[(i + 1) % len(points)])
    return _normalize(area)


def contour_area(contour, normal=None):
    """Signed area of a contour about ``normal`` (positive when counter clockwise)."""
    points = contour.points if isinstance(contour, Contour) else [Vector(p) for p in contour]
    if len(points) < 3:
        return 0.0
    area = Vector()
    for i in range(len(points)):
        area += points[i].cross(points[(i + 1) % len(points)])
    if normal is None:
        normal = _normalize(area)
    return area.dot(_normalize(normal)) * 0.5


def orient_contour(contour, normal, counter_clockwise=True):
    """Reverse ``contour`` when needed so it winds the requested way."""
    area = contour_area(contour, normal)
    if (area < 0) == counter_clockwise:
        contour.points.reverse()
    return contour


def nest_contours(contours, normal):
    """Group contours into ``(outer, [holes])`` pairs by enclosed area.

    Contours are sorted by absolute area and each one is assigned to the
    smallest larger contour that contains its centroid, which is the usual
    slicer rule for telling an island from a hole.
    """
    frame_u = _perpendicular(normal)
    frame_v = _normalize(normal).cross(frame_u)

    def to_2d(point):
        return (point.dot(frame_u), point.dot(frame_v))

    def inside(point, polygon):
        x, y = to_2d(point)
        result = False
        for i in range(len(polygon)):
            x0, y0 = to_2d(polygon[i])
            x1, y1 = to_2d(polygon[(i + 1) % len(polygon)])
            if (y0 > y) != (y1 > y):
                crossing = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
                if crossing > x:
                    result = not result
        return result

    closed = [c for c in contours if c.closed and len(c.points) >= 3]
    order = sorted(
        range(len(closed)), key=lambda i: abs(contour_area(closed[i], normal)), reverse=True
    )
    parents = {}
    for position, index in enumerate(order):
        centroid = closed[index].centroid()
        for larger in order[:position]:
            if inside(centroid, closed[larger].points):
                parents[index] = larger
                break
    groups = []
    for index in order:
        if index in parents:
            continue
        holes = [closed[j] for j, parent in parents.items() if parent == index]
        groups.append((closed[index], holes))
    return groups


def resample_contour(contour, spacing):
    """Resample a contour to points at most ``spacing`` apart."""
    points = contour.points
    if len(points) < 2 or spacing <= 0:
        return contour.copy()
    loop = points + [points[0]] if contour.closed else points
    result = [Vector(loop[0])]
    carry = 0.0
    for i in range(len(loop) - 1):
        start, end = loop[i], loop[i + 1]
        segment = (end - start).Length
        if segment < 1e-12:
            continue
        direction = (end - start) * (1.0 / segment)
        position = spacing - carry
        while position < segment:
            result.append(start + direction * position)
            position += spacing
        carry = (segment - (position - spacing)) % spacing
        result.append(Vector(end))
    if contour.closed and len(result) > 1:
        result.pop()
    return Contour(result, contour.closed)


def smooth_contour(contour, iterations=1, factor=0.5):
    """Laplacian smoothing of a contour; closed contours move every point."""
    points = [Vector(p) for p in contour.points]
    count = len(points)
    if count < 3 or iterations <= 0:
        return Contour(points, contour.closed)
    factor = max(0.0, min(1.0, float(factor)))
    for _ in range(int(iterations)):
        moved = list(points)
        span = range(count) if contour.closed else range(1, count - 1)
        for i in span:
            middle = (points[i - 1] + points[(i + 1) % count]) * 0.5
            moved[i] = points[i] + (middle - points[i]) * factor
        points = moved
    return Contour(points, contour.closed)


def align_seams(layers, mode="Aligned", axis=Vector(0, 0, 1)):
    """Rotate every closed contour so its seam sits where we want it.

    ``Aligned`` keeps the seam above the seam of the layer below, which is
    the cheapest to print. ``Nearest`` does the same but starts from the
    point closest to the previous layer's end, minimising travel.
    ``Scattered`` spreads the seam around the part so it does not build up
    into a visible scar.
    """
    reference = None
    for index, layer in enumerate(layers):
        for contour in layer:
            if not contour.closed or len(contour.points) < 3:
                continue
            if mode == "Scattered":
                shift = int(index * max(1, len(contour.points) / 7.0)) % len(contour.points)
            else:
                target = reference if reference is not None else contour.points[0]
                shift = min(
                    range(len(contour.points)),
                    key=lambda i: (contour.points[i] - target).Length,
                )
            contour.points = contour.points[shift:] + contour.points[:shift]
            if mode != "Scattered":
                reference = contour.points[0] if mode == "Aligned" else contour.points[-1]
    return layers
