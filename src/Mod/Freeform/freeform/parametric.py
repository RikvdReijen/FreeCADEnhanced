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

"""Parametric design algorithms in the spirit of Grasshopper.

Pure functions on plain vectors and index lists, no document objects:

Numbers
    :func:`remap`, :func:`attractor_factor`, :func:`evaluate_expression`
Curves
    :func:`frames_along_wire`, :func:`expression_points`
Tessellation
    :func:`delaunay_2d`, :func:`voronoi_2d`, :func:`clip_polygon`
Meshes
    :func:`deform_points`, :func:`relax_mesh`, :func:`mesh_normals`,
    :func:`boundary_vertices`, :func:`mesh_edges`, :func:`dual_mesh`
Populating and panelling
    :func:`populate_2d`, :func:`lloyd_relax`, :func:`panel_cells`
Growth and fields
    :func:`lsystem_string`, :func:`lsystem_segments`, :class:`ImageField`
"""

import math
import random

from FreeCAD import Vector

from . import geometry

__all__ = [
    "remap",
    "attractor_factor",
    "evaluate_expression",
    "expression_points",
    "frames_along_wire",
    "delaunay_2d",
    "voronoi_2d",
    "clip_polygon",
    "deform_points",
    "relax_mesh",
    "mesh_normals",
    "boundary_vertices",
    "DEFORM_MODES",
    "PANEL_PATTERNS",
    "polygon_centroid",
    "lloyd_relax",
    "populate_2d",
    "panel_cells",
    "tween_points",
    "mesh_edges",
    "dual_mesh",
    "lsystem_string",
    "lsystem_segments",
    "read_image",
    "ImageField",
]


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def remap(value, source_min, source_max, target_min, target_max, clamp=True):
    """Map ``value`` from the source range to the target range (Grasshopper 'Remap')."""
    if abs(source_max - source_min) < 1e-12:
        t = 0.0
    else:
        t = (value - source_min) / float(source_max - source_min)
    if clamp:
        t = max(0.0, min(1.0, t))
    return target_min + (target_max - target_min) * t


def attractor_factor(point, attractors, radius, minimum=0.2, maximum=1.0, falloff="linear"):
    """Scale factor for ``point`` depending on its distance to the nearest attractor.

    Points on an attractor get ``minimum``; points at ``radius`` or further
    get ``maximum``. ``falloff`` is ``"linear"``, ``"smooth"`` (smoothstep)
    or ``"inverse"`` (1 - 1/(1+d)).
    """
    if not attractors or radius <= 0:
        return maximum
    distance = min((Vector(point) - Vector(a)).Length for a in attractors)
    t = max(0.0, min(1.0, distance / float(radius)))
    if falloff == "smooth":
        t = t * t * (3.0 - 2.0 * t)
    elif falloff == "inverse":
        t = 1.0 - 1.0 / (1.0 + 6.0 * t)
        t = t / (1.0 - 1.0 / 7.0)
    return minimum + (maximum - minimum) * t


_SAFE_NAMES = {
    name: getattr(math, name)
    for name in (
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "exp",
        "log",
        "log10",
        "sqrt",
        "pow",
        "fabs",
        "floor",
        "ceil",
        "pi",
        "e",
        "hypot",
        "degrees",
        "radians",
    )
}
_SAFE_NAMES.update({"abs": abs, "min": min, "max": max, "round": round})


def evaluate_expression(expression, **variables):
    """Evaluate a math expression such as ``"10*sin(t)"`` safely.

    Only math functions and the given variables are available; builtins are
    disabled. Raises ``ValueError`` for anything that does not evaluate.
    """
    names = dict(_SAFE_NAMES)
    names.update(variables)
    try:
        return float(eval(expression, {"__builtins__": {}}, names))  # pylint: disable=eval-used
    except Exception as exc:
        raise ValueError("cannot evaluate '%s': %s" % (expression, exc)) from exc


def expression_points(x_expr, y_expr, z_expr, t_min=0.0, t_max=1.0, count=50):
    """Points of the parametric curve (x(t), y(t), z(t)) for ``count`` values of t."""
    count = max(2, int(count))
    pts = []
    for i in range(count):
        t = t_min + (t_max - t_min) * i / float(count - 1)
        pts.append(
            Vector(
                evaluate_expression(x_expr, t=t),
                evaluate_expression(y_expr, t=t),
                evaluate_expression(z_expr, t=t),
            )
        )
    return pts


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------


def frames_along_wire(wire, count=None, spacing=None, up=Vector(0, 0, 1), closed=None):
    """Evenly spaced frames along ``wire`` as ``(point, tangent, normal, binormal)``.

    ``normal`` is ``up`` projected perpendicular to the tangent (a stable
    "rotation minimising" frame for most strokes); ``binormal`` completes the
    right handed triad. Give either ``count`` or ``spacing``. For a closed
    wire the last frame (coinciding with the first) is dropped.
    """
    total = wire.Length
    if closed is None:
        closed = wire.isClosed()
    if count is None:
        if not spacing or spacing <= 0:
            raise ValueError("frames_along_wire needs count or spacing")
        count = max(2, int(round(total / float(spacing))) + 1)
    count = max(2, int(count))
    edges = wire.OrderedEdges if hasattr(wire, "OrderedEdges") else wire.Edges
    frames = []
    last = count - 1 if closed else count
    for k in range(last):
        target = total * k / float(count - 1)
        remaining = target
        edge = edges[-1]
        for e in edges:
            if remaining <= e.Length + 1e-9:
                edge = e
                break
            remaining -= e.Length
        remaining = max(0.0, min(remaining, edge.Length))
        param = edge.getParameterByLength(remaining)
        point = edge.valueAt(param)
        tangent = edge.tangentAt(param)
        if edge.Orientation == "Reversed":
            tangent = tangent * -1.0
        tangent = geometry._safe_normalize(tangent)
        normal = Vector(up) - tangent * Vector(up).dot(tangent)
        if normal.Length < 1e-9:
            normal = geometry._perpendicular(tangent)
        normal.normalize()
        binormal = tangent.cross(normal)
        frames.append((point, tangent, normal, binormal))
    return frames


# ---------------------------------------------------------------------------
# Delaunay / Voronoi (2D, points given as (x, y) tuples)
# ---------------------------------------------------------------------------


def _circumcircle(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-14:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    r2 = (ax - ux) ** 2 + (ay - uy) ** 2
    return ux, uy, r2


def delaunay_2d(points):
    """Delaunay triangulation (Bowyer-Watson) of 2D points.

    ``points`` is a list of ``(x, y)`` pairs; returns triangles as index
    triples with counter clockwise winding.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    n = len(pts)
    if n < 3:
        return []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    big = 20.0 * span
    super_tri = [(cx - big, cy - big), (cx + big, cy - big), (cx, cy + big)]
    all_pts = pts + super_tri
    triangles = [(n, n + 1, n + 2)]
    circles = {(n, n + 1, n + 2): _circumcircle(*super_tri)}
    for i, p in enumerate(pts):
        bad = []
        for tri in triangles:
            circ = circles[tri]
            if circ is None:
                continue
            if (p[0] - circ[0]) ** 2 + (p[1] - circ[1]) ** 2 <= circ[2] * (1 + 1e-12):
                bad.append(tri)
        edge_count = {}
        for tri in bad:
            for k in range(3):
                a, b = tri[k], tri[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                edge_count[key] = edge_count.get(key, 0) + 1
        boundary = [edge for edge, count in edge_count.items() if count == 1]
        for tri in bad:
            triangles.remove(tri)
            del circles[tri]
        for a, b in boundary:
            tri = (a, b, i)
            circ = _circumcircle(all_pts[a], all_pts[b], all_pts[i])
            if circ is None:
                continue
            triangles.append(tri)
            circles[tri] = circ
    result = []
    for tri in triangles:
        if any(v >= n for v in tri):
            continue
        a, b, c = (all_pts[v] for v in tri)
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if area < 0:
            tri = (tri[0], tri[2], tri[1])
        result.append(tri)
    return result


def clip_polygon(polygon, clip):
    """Sutherland-Hodgman clipping of ``polygon`` by the convex ``clip`` polygon (2D)."""

    def inside(p, a, b):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-12

    def intersect(p, q, a, b):
        dx1, dy1 = q[0] - p[0], q[1] - p[1]
        dx2, dy2 = b[0] - a[0], b[1] - a[1]
        denominator = dx1 * dy2 - dy1 * dx2
        if abs(denominator) < 1e-14:
            return q
        t = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / denominator
        return (p[0] + dx1 * t, p[1] + dy1 * t)

    output = list(polygon)
    # make the clip polygon counter clockwise
    area = 0.0
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        area += a[0] * b[1] - b[0] * a[1]
    clip = list(clip) if area >= 0 else list(reversed(clip))
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        source = output
        output = []
        if not source:
            break
        prev = source[-1]
        for point in source:
            if inside(point, a, b):
                if not inside(prev, a, b):
                    output.append(intersect(prev, point, a, b))
                output.append(point)
            elif inside(prev, a, b):
                output.append(intersect(prev, point, a, b))
            prev = point
    return output


def voronoi_2d(points, bounds):
    """Voronoi cells of 2D ``points`` clipped to the convex ``bounds`` polygon.

    Returns one polygon (list of ``(x, y)``) per input point, in input
    order; a cell may be empty when its seed lies outside ``bounds``.
    Uses the circumcentres of the Delaunay dual for interior cells and the
    half plane construction for a robust clip at the boundary.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    cells = []
    for i, p in enumerate(pts):
        cell = list(bounds)
        for j, q in enumerate(pts):
            if i == j or not cell:
                continue
            # half plane of points closer to p than to q
            mx, my = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
            dx, dy = q[0] - p[0], q[1] - p[1]
            # boundary line through m perpendicular to pq, oriented so that
            # p is on the left: direction (-dy, dx)
            a = (mx, my)
            b = (mx - dy, my + dx)
            cell = _clip_half_plane(cell, a, b)
        cells.append(cell)
    return cells


def _clip_half_plane(polygon, a, b):
    """Keep the part of ``polygon`` left of the directed line a->b."""

    def inside(p):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-12

    def intersect(p, q):
        dx1, dy1 = q[0] - p[0], q[1] - p[1]
        dx2, dy2 = b[0] - a[0], b[1] - a[1]
        denominator = dx1 * dy2 - dy1 * dx2
        if abs(denominator) < 1e-14:
            return q
        t = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / denominator
        return (p[0] + dx1 * t, p[1] + dy1 * t)

    if not polygon:
        return []
    output = []
    prev = polygon[-1]
    for point in polygon:
        if inside(point):
            if not inside(prev):
                output.append(intersect(prev, point))
            output.append(point)
        elif inside(prev):
            output.append(intersect(prev, point))
        prev = point
    return output


# ---------------------------------------------------------------------------
# Mesh deformers
# ---------------------------------------------------------------------------

DEFORM_MODES = ("Twist", "Taper", "Bend", "Stretch", "Wave", "Noise", "Flow")


def _axis_frame(axis, direction=None):
    """Right handed frame around ``axis``; ``u`` follows ``direction`` when given."""
    axis = geometry._safe_normalize(Vector(axis))
    u = None
    if direction is not None:
        u = Vector(direction) - axis * Vector(direction).dot(axis)
        if u.Length < 1e-9:
            u = None
    u = geometry._safe_normalize(u) if u is not None else geometry._perpendicular(axis)
    v = axis.cross(u)
    return axis, u, v


def deform_points(
    points, mode, amount=1.0, origin=Vector(0, 0, 0), axis=Vector(0, 0, 1), **options
):
    """Deform ``points`` (the classic Grasshopper / modifier stack operations).

    Coordinates are measured along ``axis`` from ``origin`` (``h``) and
    radially from it (``u``, ``v``). ``amount`` means:

    - ``Twist``:   degrees of rotation per unit height
    - ``Taper``:   scale factor reached at ``height`` (1 = no change)
    - ``Bend``:    total bend angle in degrees over ``height``
    - ``Stretch``: scale along the axis
    - ``Wave``:    amplitude of a sine displacement along the axis
                   (``wavelength`` option, default = height / 2)

    ``Bend`` and ``Wave`` move the points towards the ``direction`` option
    (any vector not parallel to the axis); without it a perpendicular
    direction is chosen automatically.
    - ``Noise``:   amplitude of random displacement (``seed`` option)
    - ``Flow``:    ignored; points are mapped from the axis onto the
                   ``frames`` option (see :func:`frames_along_wire`) so the
                   object bends along a curve

    ``height`` (option) is the extent of the object along the axis and
    defaults to the extent of the points.
    """
    pts = [Vector(p) for p in points]
    if not pts:
        return pts
    axis, u, v = _axis_frame(axis, options.get("direction"))
    origin = Vector(origin)
    local = []
    for p in pts:
        d = p - origin
        local.append((d.dot(u), d.dot(v), d.dot(axis)))
    heights = [h for _, _, h in local]
    h_min, h_max = min(heights), max(heights)
    height = float(options.get("height") or (h_max - h_min) or 1.0)
    out = []
    if mode == "Twist":
        for x, y, h in local:
            angle = math.radians(amount) * (h - h_min)
            c, s = math.cos(angle), math.sin(angle)
            out.append(origin + u * (c * x - s * y) + v * (s * x + c * y) + axis * h)
    elif mode == "Taper":
        for x, y, h in local:
            t = (h - h_min) / height
            factor = 1.0 + (amount - 1.0) * t
            out.append(origin + u * (x * factor) + v * (y * factor) + axis * h)
    elif mode == "Bend":
        angle_total = math.radians(amount)
        if abs(angle_total) < 1e-9:
            return pts
        radius = height / angle_total
        for x, y, h in local:
            theta = (h - h_min) / height * angle_total
            # bend in the u/axis plane around a centre at u = radius
            new_u = radius - (radius - x) * math.cos(theta)
            new_h = h_min + (radius - x) * math.sin(theta)
            out.append(origin + u * new_u + v * y + axis * new_h)
    elif mode == "Stretch":
        for x, y, h in local:
            out.append(origin + u * x + v * y + axis * (h_min + (h - h_min) * amount))
    elif mode == "Wave":
        wavelength = float(options.get("wavelength") or height / 2.0 or 1.0)
        for x, y, h in local:
            offset = amount * math.sin(2 * math.pi * (h - h_min) / wavelength)
            out.append(origin + u * (x + offset) + v * y + axis * h)
    elif mode == "Noise":
        rng = random.Random(int(options.get("seed", 0)))
        normals = options.get("normals")
        for i, p in enumerate(pts):
            if normals is not None and i < len(normals):
                out.append(p + normals[i] * rng.uniform(-amount, amount))
            else:
                out.append(
                    p + Vector(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)) * amount
                )
    elif mode == "Flow":
        frames = options.get("frames")
        if not frames:
            raise ValueError("Flow needs frames along a curve")
        for x, y, h in local:
            t = (h - h_min) / height * (len(frames) - 1)
            index = min(int(t), len(frames) - 2)
            fraction = t - index
            p0, t0, n0, b0 = frames[index]
            p1, t1, n1, b1 = frames[index + 1]
            point = p0 + (p1 - p0) * fraction
            normal = geometry._safe_normalize(n0 + (n1 - n0) * fraction)
            binormal = geometry._safe_normalize(b0 + (b1 - b0) * fraction)
            out.append(point + normal * x + binormal * y)
    else:
        raise ValueError("unknown deform mode %r" % mode)
    return out


# ---------------------------------------------------------------------------
# Mesh relaxation (a tiny Kangaroo)
# ---------------------------------------------------------------------------


def boundary_vertices(faces):
    """Indices of vertices on edges with a single adjacent face."""
    edge_count = {}
    for face in faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            edge_count[key] = edge_count.get(key, 0) + 1
    result = set()
    for (a, b), count in edge_count.items():
        if count == 1:
            result.add(a)
            result.add(b)
    return result


def relax_mesh(points, faces, iterations=20, strength=0.5, fixed=None, keep_boundary=True):
    """Laplacian relaxation: every free vertex moves towards its neighbours.

    With the boundary kept fixed this converges towards a minimal
    (soap film) surface, the classic form finding result. ``fixed`` is an
    optional set of extra vertex indices to keep in place.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    neighbours = [set() for _ in range(n)]
    for face in faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            neighbours[a].add(b)
            neighbours[b].add(a)
    locked = set(fixed or ())
    if keep_boundary:
        locked |= boundary_vertices(faces)
    strength = max(0.0, min(1.0, float(strength)))
    for _ in range(max(0, int(iterations))):
        new = list(pts)
        for i in range(n):
            if i in locked or not neighbours[i]:
                continue
            centre = Vector()
            for j in neighbours[i]:
                centre += pts[j]
            centre *= 1.0 / len(neighbours[i])
            new[i] = pts[i] + (centre - pts[i]) * strength
        pts = new
    return pts


def mesh_normals(points, faces):
    """Area weighted vertex normals of a polygon mesh."""
    normals = [Vector() for _ in points]
    for face in faces:
        if len(face) < 3:
            continue
        a, b, c = points[face[0]], points[face[1]], points[face[2]]
        n = (b - a).cross(c - a)
        for i in face:
            normals[i] += n
    return [geometry._safe_normalize(n, Vector(0, 0, 1)) for n in normals]


# ---------------------------------------------------------------------------
# Point populating and centroidal relaxation
# ---------------------------------------------------------------------------


def polygon_centroid(polygon):
    """Area centroid of a 2D polygon (falls back to the vertex average)."""
    area = 0.0
    cx = cy = 0.0
    n = len(polygon)
    for i in range(n):
        x0, y0 = polygon[i]
        x1, y1 = polygon[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-12:
        return (sum(p[0] for p in polygon) / n, sum(p[1] for p in polygon) / n)
    area *= 0.5
    return (cx / (6.0 * area), cy / (6.0 * area))


def lloyd_relax(points, bounds, iterations=3):
    """Move 2D points to the centroids of their Voronoi cells.

    A few passes turn a random scatter into an evenly spaced (centroidal
    Voronoi) arrangement, which is what most panelling wants.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    for _ in range(max(0, int(iterations))):
        cells = voronoi_2d(pts, bounds)
        moved = []
        for point, cell in zip(pts, cells):
            moved.append(polygon_centroid(cell) if len(cell) >= 3 else point)
        pts = moved
    return pts


def populate_2d(bounds, count, seed=0, inside=None, relax=0, weights=None):
    """Scatter ``count`` points inside the 2D ``bounds`` polygon.

    ``inside`` is an optional predicate rejecting candidates. ``weights``
    is an optional callable returning 0..1 for a point: higher values make
    a candidate more likely to be kept, which is how an attractor or an
    image drives the density. ``relax`` Lloyd passes even them out
    afterwards (which discards the weighting, so use one or the other).
    """
    rng = random.Random(int(seed))
    xs = [p[0] for p in bounds]
    ys = [p[1] for p in bounds]
    points = []
    attempts = 0
    limit = max(200, 200 * int(count))
    while len(points) < int(count) and attempts < limit:
        attempts += 1
        candidate = (rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys)))
        if inside is not None and not inside(candidate):
            continue
        if weights is not None and rng.random() > max(0.0, min(1.0, weights(candidate))):
            continue
        points.append(candidate)
    if relax:
        points = lloyd_relax(points, bounds, relax)
        if inside is not None:
            points = [p for p in points if inside(p)]
    return points


# ---------------------------------------------------------------------------
# Panelling patterns on a UV grid
# ---------------------------------------------------------------------------

PANEL_PATTERNS = ("Quad", "Triangle", "Diamond", "Hexagon", "Brick")


def panel_cells(count_u, count_v, pattern="Quad"):
    """Polygons of a panelling pattern in the unit square.

    Returns a list of polygons, each a list of ``(u, v)`` pairs in 0..1,
    ready to be mapped through a surface. ``count_u`` and ``count_v`` are
    the number of cells in each direction.
    """
    count_u = max(1, int(count_u))
    count_v = max(1, int(count_v))
    du = 1.0 / count_u
    dv = 1.0 / count_v
    cells = []
    if pattern == "Quad":
        for i in range(count_u):
            for j in range(count_v):
                u, v = i * du, j * dv
                cells.append([(u, v), (u + du, v), (u + du, v + dv), (u, v + dv)])
    elif pattern == "Triangle":
        for i in range(count_u):
            for j in range(count_v):
                u, v = i * du, j * dv
                a, b = (u, v), (u + du, v)
                c, d = (u + du, v + dv), (u, v + dv)
                if (i + j) % 2:
                    cells.append([a, b, c])
                    cells.append([a, c, d])
                else:
                    cells.append([a, b, d])
                    cells.append([b, c, d])
    elif pattern == "Diamond":
        # diamonds centred on every cell corner of a half-offset lattice
        for i in range(count_u + 1):
            for j in range(count_v + 1):
                u, v = i * du, j * dv
                if (i + j) % 2:
                    continue
                cells.append(
                    [
                        (max(0.0, u - du), v),
                        (u, max(0.0, v - dv)),
                        (min(1.0, u + du), v),
                        (u, min(1.0, v + dv)),
                    ]
                )
    elif pattern == "Brick":
        for j in range(count_v):
            offset = (du / 2.0) if j % 2 else 0.0
            for i in range(count_u):
                u = i * du + offset
                v = j * dv
                u0, u1 = max(0.0, u), min(1.0, u + du)
                if u1 - u0 < 1e-9:
                    continue
                cells.append([(u0, v), (u1, v), (u1, v + dv), (u0, v + dv)])
                if offset and i == count_u - 1:
                    cells.append([(0.0, v), (offset, v), (offset, v + dv), (0.0, v + dv)])
    elif pattern == "Hexagon":
        # pointy topped hexagons on a staggered lattice
        for j in range(count_v):
            for i in range(count_u):
                cu = (i + (0.5 if j % 2 else 0.0)) * du
                cv = (j + 0.5) * dv
                hexagon = []
                for k in range(6):
                    angle = math.pi / 2.0 + k * math.pi / 3.0
                    hu = cu + du * 0.58 * math.cos(angle)
                    hv = cv + dv * 0.58 * math.sin(angle)
                    hexagon.append((min(1.0, max(0.0, hu)), min(1.0, max(0.0, hv))))
                hexagon = [p for k, p in enumerate(hexagon) if p != hexagon[k - 1]]
                if len(hexagon) >= 3:
                    cells.append(hexagon)
    else:
        raise ValueError("unknown panel pattern %r" % pattern)
    return cells


# ---------------------------------------------------------------------------
# Tweening
# ---------------------------------------------------------------------------


def tween_points(first, second, factor=0.5, samples=None):
    """Blend two point lists, resampling them to a common count first."""
    count = int(samples) if samples else max(len(first), len(second))
    a = geometry.resample_points(first, count=count)
    b = geometry.resample_points(second, count=count)
    factor = float(factor)
    return [p + (q - p) * factor for p, q in zip(a, b)]


# ---------------------------------------------------------------------------
# Mesh topology helpers
# ---------------------------------------------------------------------------


def mesh_edges(faces):
    """Unique undirected edges of a polygon mesh as sorted index pairs."""
    edges = set()
    for face in faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            if a != b:
                edges.add((a, b) if a < b else (b, a))
    return sorted(edges)


def dual_mesh(points, faces):
    """The dual of a polygon mesh: one vertex per face, one face per vertex.

    Boundary vertices are skipped, so an open mesh keeps a clean rim. This
    is the operation behind honeycomb-like panelling of a triangulated
    surface.
    """
    centres = []
    for face in faces:
        centre = Vector()
        for i in face:
            centre += points[i]
        centres.append(centre * (1.0 / len(face)))
    vertex_faces = {}
    for fi, face in enumerate(faces):
        for i in face:
            vertex_faces.setdefault(i, []).append(fi)
    boundary = boundary_vertices(faces)
    dual_faces = []
    for vertex, adjacent in vertex_faces.items():
        if vertex in boundary or len(adjacent) < 3:
            continue
        origin = points[vertex]
        normal = Vector()
        for fi in adjacent:
            normal += centres[fi] - origin
        normal = geometry._safe_normalize(normal)
        u = geometry._perpendicular(normal)
        v = normal.cross(u)

        def angle_of(fi, origin=origin, u=u, v=v):
            d = centres[fi] - origin
            return math.atan2(d.dot(v), d.dot(u))

        dual_faces.append(sorted(adjacent, key=angle_of))
    return centres, dual_faces


# ---------------------------------------------------------------------------
# L-systems
# ---------------------------------------------------------------------------


def lsystem_string(axiom, rules, generations=3, limit=200000):
    """Expand an L-system axiom with ``rules`` (a dict of symbol -> string)."""
    current = axiom
    for _ in range(max(0, int(generations))):
        current = "".join(rules.get(ch, ch) for ch in current)
        if len(current) > limit:
            raise ValueError("the L-system grew beyond %d symbols" % limit)
    return current


def lsystem_segments(
    symbols,
    step=10.0,
    angle=25.0,
    origin=Vector(0, 0, 0),
    direction=Vector(0, 0, 1),
    step_scale=1.0,
    angle_scale=1.0,
):
    """Turn an L-system string into 3D segments with a turtle.

    Symbols: ``F``/``G`` draw forward, ``f`` moves without drawing,
    ``+``/``-`` yaw, ``&``/``^`` pitch, ``\\``/``/`` roll, ``|`` turn back,
    ``[``/``]`` push and pop the state. Every branch level multiplies the
    step by ``step_scale`` and the angle by ``angle_scale``.

    Returns ``(segments, depth)`` pairs where a segment is
    ``(start, end)`` and ``depth`` is the bracket nesting level.
    """
    import FreeCAD

    heading = geometry._safe_normalize(Vector(direction))
    up = geometry._perpendicular(heading)
    left = heading.cross(up)
    position = Vector(origin)
    state = []
    depth = 0
    segments = []
    for symbol in symbols:
        scale = step_scale**depth
        turn = math.radians(angle * (angle_scale**depth))

        def rotate(vector, axis, radians):
            return FreeCAD.Rotation(Vector(axis), math.degrees(radians)).multVec(Vector(vector))

        if symbol in "FG":
            end = position + heading * (step * scale)
            segments.append(((Vector(position), end), depth))
            position = end
        elif symbol == "f":
            position = position + heading * (step * scale)
        elif symbol in "+-&^\\/|":
            if symbol == "|":
                axis, radians = up, math.pi
            elif symbol in "+-":
                axis, radians = up, turn if symbol == "+" else -turn
            elif symbol in "&^":
                axis, radians = left, turn if symbol == "&" else -turn
            else:
                axis, radians = heading, turn if symbol == "\\" else -turn
            heading = rotate(heading, axis, radians)
            up = rotate(up, axis, radians)
            left = rotate(left, axis, radians)
        elif symbol == "[":
            state.append((Vector(position), Vector(heading), Vector(up), Vector(left), depth))
            depth += 1
        elif symbol == "]":
            if state:
                position, heading, up, left, depth = state.pop()
    return segments


# ---------------------------------------------------------------------------
# Images as fields
# ---------------------------------------------------------------------------


def read_image(path):
    """Read a PNG, PGM or PPM image into ``(width, height, rows)``.

    ``rows`` holds one list of 0..1 brightness values per row, top first.
    Only the common non-interlaced 8 and 16 bit PNG colour types are
    supported, which covers what image editors export by default.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _read_png(data)
    if data[:2] in (b"P2", b"P5", b"P3", b"P6"):
        return _read_pnm(data)
    raise ValueError("unsupported image format: %s" % path)


def _read_pnm(data):
    tokens = []
    index = 2
    binary = data[:2] in (b"P5", b"P6")
    channels = 3 if data[:2] in (b"P3", b"P6") else 1
    while len(tokens) < 3:
        while index < len(data) and data[index : index + 1].isspace():
            index += 1
        if data[index : index + 1] == b"#":
            while index < len(data) and data[index : index + 1] != b"\n":
                index += 1
            continue
        start = index
        while index < len(data) and not data[index : index + 1].isspace():
            index += 1
        tokens.append(int(data[start:index]))
    width, height, maximum = tokens
    index += 1
    rows = []
    if binary:
        for y in range(height):
            row = []
            for x in range(width):
                base = index + (y * width + x) * channels
                pixel = data[base : base + channels]
                row.append(sum(pixel) / float(channels * maximum))
            rows.append(row)
    else:
        values = [int(v) for v in data[index:].split()]
        for y in range(height):
            row = []
            for x in range(width):
                base = (y * width + x) * channels
                row.append(sum(values[base : base + channels]) / float(channels * maximum))
            rows.append(row)
    return width, height, rows


_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _read_png(data):
    import struct
    import zlib

    index = 8
    width = height = depth = colour = None
    palette = None
    chunks = []
    while index < len(data):
        (length,) = struct.unpack(">I", data[index : index + 4])
        kind = data[index + 4 : index + 8]
        payload = data[index + 8 : index + 8 + length]
        index += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", payload)
            if interlace:
                raise ValueError("interlaced PNG images are not supported")
            if colour not in _PNG_CHANNELS or depth not in (8, 16):
                raise ValueError("unsupported PNG colour type %d / depth %d" % (colour, depth))
        elif kind == b"PLTE":
            palette = [payload[i : i + 3] for i in range(0, len(payload), 3)]
        elif kind == b"IDAT":
            chunks.append(payload)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(b"".join(chunks))
    channels = _PNG_CHANNELS[colour]
    sample = depth // 8
    stride = width * channels * sample
    maximum = float((1 << depth) - 1)
    previous = bytearray(stride)
    rows = []
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        step = channels * sample
        for i in range(stride):
            left = line[i - step] if i >= step else 0
            above = previous[i]
            upper_left = previous[i - step] if i >= step else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + above) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (left + above) // 2) & 0xFF
            elif filter_type == 4:
                p = left + above - upper_left
                pa, pb, pc = abs(p - left), abs(p - above), abs(p - upper_left)
                nearest = left if (pa <= pb and pa <= pc) else (above if pb <= pc else upper_left)
                line[i] = (line[i] + nearest) & 0xFF
            elif filter_type != 0:
                raise ValueError("unknown PNG filter %d" % filter_type)
        previous = line
        row = []
        for x in range(width):
            base = x * channels * sample
            if colour == 3:
                entry = palette[line[base]] if palette else b"\x00\x00\x00"
                row.append(sum(entry) / (3 * 255.0))
                continue
            values = []
            for c in range(channels if colour not in (4, 6) else channels - 1):
                pixel = line[base + c * sample : base + (c + 1) * sample]
                values.append(int.from_bytes(pixel, "big") / maximum)
            row.append(sum(values) / float(len(values)))
        rows.append(row)
    return width, height, rows


class ImageField:
    """Samples the brightness of an image over a rectangle in the plane."""

    def __init__(self, path, invert=False):
        self.width, self.height, self.rows = read_image(path)
        self.invert = bool(invert)

    def sample_uv(self, u, v):
        """Brightness at ``(u, v)`` in 0..1, with v = 0 at the bottom."""
        u = max(0.0, min(1.0, float(u)))
        v = max(0.0, min(1.0, float(v)))
        x = min(self.width - 1, int(u * self.width))
        y = min(self.height - 1, int((1.0 - v) * self.height))
        value = self.rows[y][x]
        return 1.0 - value if self.invert else value
