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

"""Pure geometry algorithms used by the Freeform workbench.

Everything in this module works on plain lists of ``FreeCAD.Vector`` and
returns new lists; nothing here touches a document or the GUI, which keeps
the algorithms easy to unit test headlessly.

Contents
--------
Stroke conditioning
    :func:`remove_duplicates`, :func:`smooth_points`, :func:`chaikin`,
    :func:`simplify_points`, :func:`resample_points`, :func:`condition_stroke`
Shape recognition
    :func:`fit_line`, :func:`fit_plane`, :func:`fit_circle`,
    :func:`recognize_stroke`
Symmetry
    :func:`mirror_point`, :func:`mirror_points`
Subdivision surfaces
    :func:`catmull_clark`, :func:`triangulate_polygons`, :func:`weld_points`
"""

import math

from FreeCAD import Vector

__all__ = [
    "remove_duplicates",
    "smooth_points",
    "chaikin",
    "simplify_points",
    "resample_points",
    "polyline_length",
    "condition_stroke",
    "fit_line",
    "fit_plane",
    "fit_circle",
    "recognize_stroke",
    "mirror_point",
    "mirror_points",
    "catmull_clark",
    "triangulate_polygons",
    "weld_points",
    "polygons_from_shape",
]


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------


def _vec(p):
    """Coerce a tuple, list or Vector into a fresh ``Vector``."""
    if isinstance(p, Vector):
        return Vector(p)
    return Vector(p[0], p[1], p[2])


def _vectors(points):
    return [_vec(p) for p in points]


def _safe_normalize(v, fallback=None):
    length = v.Length
    if length < 1e-12:
        return Vector(fallback) if fallback is not None else Vector(0, 0, 1)
    return Vector(v.x / length, v.y / length, v.z / length)


def _perpendicular(normal):
    """Return an arbitrary unit vector perpendicular to ``normal``."""
    n = _safe_normalize(normal)
    helper = Vector(1, 0, 0) if abs(n.x) < 0.9 else Vector(0, 1, 0)
    return _safe_normalize(n.cross(helper))


# ---------------------------------------------------------------------------
# Stroke conditioning
# ---------------------------------------------------------------------------


def remove_duplicates(points, tolerance=1e-7):
    """Drop consecutive points closer than ``tolerance`` to each other."""
    result = []
    for p in _vectors(points):
        if not result or (p - result[-1]).Length > tolerance:
            result.append(p)
    return result


def polyline_length(points, closed=False):
    """Total length of the polyline through ``points``."""
    pts = _vectors(points)
    if len(pts) < 2:
        return 0.0
    length = sum((pts[i + 1] - pts[i]).Length for i in range(len(pts) - 1))
    if closed:
        length += (pts[0] - pts[-1]).Length
    return length


def smooth_points(points, iterations=1, factor=0.5, closed=False):
    """Laplacian smoothing of a polyline.

    Every interior point is moved towards the midpoint of its neighbours
    by ``factor`` (0 = no change, 1 = full averaging). End points of an
    open polyline are kept fixed so the stroke keeps starting and ending
    where the user drew it.
    """
    pts = _vectors(points)
    n = len(pts)
    if n < 3 or iterations <= 0 or factor <= 0:
        return pts
    factor = min(factor, 1.0)
    for _ in range(int(iterations)):
        new = list(pts)
        rng = range(n) if closed else range(1, n - 1)
        for i in rng:
            prev = pts[i - 1]
            nxt = pts[(i + 1) % n]
            mid = (prev + nxt) * 0.5
            new[i] = pts[i] + (mid - pts[i]) * factor
        pts = new
    return pts


def chaikin(points, iterations=1, closed=False):
    """Chaikin corner cutting; produces a visually smooth polyline.

    Each pass replaces every segment by two points at 1/4 and 3/4 of its
    length. End points of an open polyline are preserved.
    """
    pts = _vectors(points)
    if len(pts) < 3 or iterations <= 0:
        return pts
    for _ in range(int(iterations)):
        new = []
        count = len(pts) if closed else len(pts) - 1
        if not closed:
            new.append(pts[0])
        for i in range(count):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            new.append(a * 0.75 + b * 0.25)
            new.append(a * 0.25 + b * 0.75)
        if not closed:
            new.append(pts[-1])
        pts = new
    return pts


def _point_segment_distance(p, a, b):
    ab = b - a
    ab2 = ab.dot(ab)
    if ab2 < 1e-18:
        return (p - a).Length
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab2))
    return (p - (a + ab * t)).Length


def simplify_points(points, tolerance=0.1):
    """Ramer-Douglas-Peucker simplification in 3D (iterative, no recursion)."""
    pts = _vectors(points)
    n = len(pts)
    if n < 3 or tolerance <= 0:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        a, b = pts[first], pts[last]
        best_index = -1
        best_dist = -1.0
        for i in range(first + 1, last):
            d = _point_segment_distance(pts[i], a, b)
            if d > best_dist:
                best_dist = d
                best_index = i
        if best_dist > tolerance:
            keep[best_index] = True
            stack.append((first, best_index))
            stack.append((best_index, last))
    return [p for p, k in zip(pts, keep) if k]


def resample_points(points, count=None, spacing=None, closed=False):
    """Resample a polyline to ``count`` points or to a fixed ``spacing``.

    Points are placed at equal arc-length intervals along the original
    polyline. Exactly one of ``count`` or ``spacing`` must be given.
    """
    pts = _vectors(points)
    if closed and len(pts) > 1 and (pts[0] - pts[-1]).Length > 1e-9:
        pts = pts + [Vector(pts[0])]
    if len(pts) < 2:
        return pts
    total = polyline_length(pts)
    if total < 1e-12:
        return [pts[0]]
    if count is None and spacing is None:
        raise ValueError("resample_points needs count or spacing")
    if count is None:
        count = max(2, int(round(total / float(spacing))) + 1)
    count = max(2, int(count))
    # cumulative lengths
    cumulative = [0.0]
    for i in range(1, len(pts)):
        cumulative.append(cumulative[-1] + (pts[i] - pts[i - 1]).Length)
    result = [Vector(pts[0])]
    seg = 0
    for k in range(1, count - 1):
        target = total * k / float(count - 1)
        while seg < len(pts) - 2 and cumulative[seg + 1] < target:
            seg += 1
        seg_len = cumulative[seg + 1] - cumulative[seg]
        t = 0.0 if seg_len < 1e-12 else (target - cumulative[seg]) / seg_len
        result.append(pts[seg] + (pts[seg + 1] - pts[seg]) * t)
    result.append(Vector(pts[-1]))
    if closed:
        result.pop()  # do not repeat the first point
    return result


def condition_stroke(points, smoothing=2, tolerance=0.0, closed=False, min_points=2):
    """Standard pipeline used for a raw mouse stroke.

    1. drop duplicate points
    2. Laplacian smooth ``smoothing`` times
    3. simplify with ``tolerance`` (skipped when 0)
    """
    pts = remove_duplicates(points)
    if len(pts) < min_points:
        return pts
    if smoothing:
        pts = smooth_points(pts, iterations=smoothing, factor=0.5, closed=closed)
    if tolerance > 0:
        pts = simplify_points(pts, tolerance)
    return pts


# ---------------------------------------------------------------------------
# Least squares fitting / shape recognition
# ---------------------------------------------------------------------------


def _centroid(pts):
    c = Vector()
    for p in pts:
        c += p
    return c * (1.0 / len(pts))


def _covariance(pts, centroid):
    xx = xy = xz = yy = yz = zz = 0.0
    for p in pts:
        d = p - centroid
        xx += d.x * d.x
        xy += d.x * d.y
        xz += d.x * d.z
        yy += d.y * d.y
        yz += d.y * d.z
        zz += d.z * d.z
    return [[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]


def _jacobi_eigen(matrix, sweeps=50):
    """Eigen decomposition of a symmetric 3x3 matrix (Jacobi rotations).

    Returns ``(eigenvalues, eigenvectors)`` where ``eigenvectors[i]`` is the
    unit vector belonging to ``eigenvalues[i]``; both sorted descending.
    """
    a = [row[:] for row in matrix]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(sweeps):
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        if off < 1e-15:
            break
        for p in range(3):
            for q in range(p + 1, 3):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                sign = 1.0 if theta >= 0 else -1.0
                t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(3):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(3):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(3):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    values = [a[0][0], a[1][1], a[2][2]]
    vectors = [Vector(v[0][i], v[1][i], v[2][i]) for i in range(3)]
    order = sorted(range(3), key=lambda i: values[i], reverse=True)
    return [values[i] for i in order], [vectors[i] for i in order]


def fit_line(points):
    """Least squares line through ``points``.

    Returns ``(origin, direction, max_deviation)`` where ``origin`` is the
    centroid, ``direction`` a unit vector and ``max_deviation`` the largest
    distance of any input point from the fitted line.
    """
    pts = _vectors(points)
    if len(pts) < 2:
        raise ValueError("fit_line needs at least two points")
    centroid = _centroid(pts)
    _, vectors = _jacobi_eigen(_covariance(pts, centroid))
    direction = _safe_normalize(vectors[0], pts[-1] - pts[0])
    # orient along the stroke direction
    if direction.dot(pts[-1] - pts[0]) < 0:
        direction = direction * -1.0
    deviation = 0.0
    for p in pts:
        d = p - centroid
        deviation = max(deviation, (d - direction * d.dot(direction)).Length)
    return centroid, direction, deviation


def fit_plane(points):
    """Least squares plane through ``points``.

    Returns ``(origin, normal, max_deviation)``.
    """
    pts = _vectors(points)
    if len(pts) < 3:
        raise ValueError("fit_plane needs at least three points")
    centroid = _centroid(pts)
    _, vectors = _jacobi_eigen(_covariance(pts, centroid))
    normal = _safe_normalize(vectors[2])
    deviation = max(abs((p - centroid).dot(normal)) for p in pts)
    return centroid, normal, deviation


def fit_circle(points):
    """Least squares circle through (roughly coplanar) ``points``.

    Uses the algebraic (Kasa) fit in the best-fit plane, which is fast and
    robust enough for hand drawn strokes.

    Returns a dict with ``center``, ``normal``, ``radius``, ``max_deviation``
    (largest radial error), ``coverage`` (angle swept by the points, in
    radians) and ``start_angle``/``end_angle`` measured in the plane frame.
    """
    pts = _vectors(points)
    if len(pts) < 3:
        raise ValueError("fit_circle needs at least three points")
    origin, normal, _ = fit_plane(pts)
    u = _perpendicular(normal)
    v = normal.cross(u)
    # 2D coordinates in the plane frame
    xs, ys = [], []
    for p in pts:
        d = p - origin
        xs.append(d.dot(u))
        ys.append(d.dot(v))
    n = float(len(pts))
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    syy = sum(y * y for y in ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxxx = sum(x * x * x for x in xs)
    syyy = sum(y * y * y for y in ys)
    sxyy = sum(x * y * y for x, y in zip(xs, ys))
    sxxy = sum(x * x * y for x, y in zip(xs, ys))
    # Solve the normal equations for x^2 + y^2 + a x + b y + c = 0
    m = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
    rhs = [-(sxxx + sxyy), -(sxxy + syyy), -(sxx + syy)]
    sol = _solve3(m, rhs)
    if sol is None:
        raise ValueError("degenerate circle fit")
    a, b, c = sol
    cx, cy = -a / 2.0, -b / 2.0
    r2 = cx * cx + cy * cy - c
    if r2 <= 0:
        raise ValueError("degenerate circle fit")
    radius = math.sqrt(r2)
    center = origin + u * cx + v * cy
    deviation = 0.0
    angles = []
    for x, y in zip(xs, ys):
        deviation = max(deviation, abs(math.hypot(x - cx, y - cy) - radius))
        angles.append(math.atan2(y - cy, x - cx))
    # angular coverage: unwrap along the stroke
    coverage = 0.0
    for i in range(1, len(angles)):
        delta = angles[i] - angles[i - 1]
        while delta > math.pi:
            delta -= 2 * math.pi
        while delta < -math.pi:
            delta += 2 * math.pi
        coverage += delta
    if coverage < 0:
        # make the frame right handed with respect to the stroke direction
        normal = normal * -1.0
        v = v * -1.0
        angles = [-ang for ang in angles]
        coverage = -coverage
    return {
        "center": center,
        "normal": normal,
        "radius": radius,
        "max_deviation": deviation,
        "coverage": coverage,
        "start_angle": angles[0],
        "end_angle": angles[-1],
        "u": u,
        "v": v,
    }


def _solve3(m, rhs):
    """Solve a 3x3 linear system by Gaussian elimination; None if singular."""
    a = [m[i][:] + [rhs[i]] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-14:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(3):
            if r != col:
                f = a[r][col] / a[col][col]
                for k in range(col, 4):
                    a[r][k] -= f * a[col][k]
    return [a[i][3] / a[i][i] for i in range(3)]


def recognize_stroke(points, tolerance=None, closed=False):
    """Classify a hand drawn stroke as a line, circle or arc.

    ``tolerance`` is the maximum allowed deviation; by default 2 % of the
    stroke length. Returns a tuple ``(kind, data)`` where ``kind`` is one
    of ``"line"``, ``"circle"``, ``"arc"`` or ``None``:

    - ``"line"``:   ``data = (start, end)``
    - ``"circle"``: ``data = (center, normal, radius)``
    - ``"arc"``:    ``data = (start, mid, end)`` (three points on the arc)
    """
    pts = remove_duplicates(points)
    if len(pts) < 2:
        return None, None
    length = polyline_length(pts, closed=closed)
    if tolerance is None:
        tolerance = 0.02 * length
    if len(pts) == 2:
        return "line", (pts[0], pts[-1])
    origin, direction, line_dev = fit_line(pts)
    if line_dev <= tolerance:
        start = origin + direction * (pts[0] - origin).dot(direction)
        end = origin + direction * (pts[-1] - origin).dot(direction)
        return "line", (start, end)
    try:
        circ = fit_circle(pts)
    except ValueError:
        return None, None
    if circ["max_deviation"] > tolerance:
        return None, None
    coverage = circ["coverage"]
    gap = (pts[0] - pts[-1]).Length
    if closed or coverage > 1.75 * math.pi or (gap <= 4 * tolerance and coverage > 1.5 * math.pi):
        return "circle", (circ["center"], circ["normal"], circ["radius"])

    # arc: project first, middle and last points onto the circle
    def on_circle(angle):
        return (
            circ["center"]
            + circ["u"] * (circ["radius"] * math.cos(angle))
            + circ["v"] * (circ["radius"] * math.sin(angle))
        )

    start_angle = circ["start_angle"]
    end_angle = start_angle + coverage
    mid_angle = start_angle + coverage / 2.0
    return "arc", (on_circle(start_angle), on_circle(mid_angle), on_circle(end_angle))


# ---------------------------------------------------------------------------
# Symmetry
# ---------------------------------------------------------------------------


def mirror_point(point, origin=Vector(0, 0, 0), normal=Vector(1, 0, 0)):
    """Reflect ``point`` across the plane defined by ``origin`` and ``normal``."""
    p = _vec(point)
    n = _safe_normalize(_vec(normal))
    o = _vec(origin)
    distance = (p - o).dot(n)
    return p - n * (2.0 * distance)


def mirror_points(points, origin=Vector(0, 0, 0), normal=Vector(1, 0, 0)):
    """Reflect every point across a plane (see :func:`mirror_point`)."""
    return [mirror_point(p, origin, normal) for p in points]


# ---------------------------------------------------------------------------
# Subdivision surfaces
# ---------------------------------------------------------------------------


def weld_points(points, faces, tolerance=1e-6):
    """Merge coincident points and re-index ``faces`` accordingly."""
    pts = _vectors(points)
    grid = {}
    remap = []
    unique = []
    inv = 1.0 / tolerance if tolerance > 0 else 1e6
    for p in pts:
        key = (round(p.x * inv), round(p.y * inv), round(p.z * inv))
        idx = grid.get(key)
        if idx is None:
            idx = len(unique)
            grid[key] = idx
            unique.append(p)
        remap.append(idx)
    new_faces = []
    for face in faces:
        mapped = []
        for i in face:
            j = remap[i]
            if not mapped or mapped[-1] != j:
                mapped.append(j)
        if len(mapped) > 1 and mapped[0] == mapped[-1]:
            mapped.pop()
        if len(mapped) >= 3:
            new_faces.append(mapped)
    return unique, new_faces


def catmull_clark(points, faces, iterations=1, keep_boundary=True):
    """Catmull-Clark subdivision of a polygon mesh.

    ``points`` is a list of vectors, ``faces`` a list of index lists (any
    polygon size, quads and triangles both fine). Returns the refined
    ``(points, faces)`` where every face is a quad.

    Boundary edges (edges with a single adjacent face) are handled with the
    standard crease rules so open surfaces keep a clean rim: boundary edge
    points are edge midpoints and boundary vertices are smoothed only
    along the boundary (or kept fixed if ``keep_boundary`` is True).
    """
    pts = _vectors(points)
    fcs = [list(f) for f in faces]
    for _ in range(max(0, int(iterations))):
        pts, fcs = _catmull_clark_once(pts, fcs, keep_boundary)
    return pts, fcs


def _catmull_clark_once(points, faces, keep_boundary):
    n_points = len(points)
    # face points
    face_points = []
    for face in faces:
        c = Vector()
        for i in face:
            c += points[i]
        face_points.append(c * (1.0 / len(face)))

    # edge -> [face indices]
    edge_faces = {}
    for fi, face in enumerate(faces):
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            edge_faces.setdefault(key, []).append(fi)

    # edge points
    edge_points = {}
    edge_index = {}
    for key, adjacent in edge_faces.items():
        a, b = key
        if len(adjacent) == 2:
            ep = (
                points[a] + points[b] + face_points[adjacent[0]] + face_points[adjacent[1]]
            ) * 0.25
        else:
            ep = (points[a] + points[b]) * 0.5
        edge_points[key] = ep

    # per-vertex adjacency
    vertex_faces = [[] for _ in range(n_points)]
    vertex_edges = [[] for _ in range(n_points)]
    for fi, face in enumerate(faces):
        for i in face:
            vertex_faces[i].append(fi)
    for key in edge_faces:
        vertex_edges[key[0]].append(key)
        vertex_edges[key[1]].append(key)

    moved = []
    for vi in range(n_points):
        p = points[vi]
        boundary_edges = [e for e in vertex_edges[vi] if len(edge_faces[e]) == 1]
        if not vertex_faces[vi]:
            moved.append(Vector(p))
            continue
        if boundary_edges:
            if keep_boundary or len(boundary_edges) != 2:
                moved.append(Vector(p))
            else:
                # crease rule: 3/4 vertex + 1/8 each boundary neighbour
                acc = p * 0.75
                for e in boundary_edges:
                    other = e[1] if e[0] == vi else e[0]
                    acc += points[other] * 0.125
                moved.append(acc)
            continue
        n = float(len(vertex_faces[vi]))
        f_avg = Vector()
        for fi in vertex_faces[vi]:
            f_avg += face_points[fi]
        f_avg *= 1.0 / n
        r_avg = Vector()
        for e in vertex_edges[vi]:
            r_avg += (points[e[0]] + points[e[1]]) * 0.5
        r_avg *= 1.0 / len(vertex_edges[vi])
        moved.append((f_avg + r_avg * 2.0 + p * (n - 3.0)) * (1.0 / n))

    out_points = list(moved)
    face_point_index = []
    for fp in face_points:
        face_point_index.append(len(out_points))
        out_points.append(fp)
    for key, ep in edge_points.items():
        edge_index[key] = len(out_points)
        out_points.append(ep)

    out_faces = []
    for fi, face in enumerate(faces):
        m = len(face)
        for k in range(m):
            v = face[k]
            nxt = face[(k + 1) % m]
            prv = face[(k - 1) % m]
            e_next = (v, nxt) if v < nxt else (nxt, v)
            e_prev = (prv, v) if prv < v else (v, prv)
            out_faces.append([v, edge_index[e_next], face_point_index[fi], edge_index[e_prev]])
    return out_points, out_faces


def triangulate_polygons(faces):
    """Fan-triangulate polygon index lists into triangles."""
    tris = []
    for face in faces:
        for k in range(1, len(face) - 1):
            tris.append((face[0], face[k], face[k + 1]))
    return tris


def polygons_from_shape(shape, tolerance=1e-6):
    """Extract a polygon mesh ``(points, faces)`` from a Part shape.

    Planar faces bounded by straight edges become one polygon each (a box
    yields six quads, which is what a subdivision cage wants). Any other
    face is tessellated into triangles.
    """
    points = []
    faces = []
    for face in shape.Faces:
        polygon = None
        try:
            surface_type = face.Surface.__class__.__name__
            straight = all(
                e.Curve.__class__.__name__ in ("Line", "LineSegment") for e in face.Edges
            )
            if surface_type == "Plane" and straight and len(face.Wires) == 1:
                polygon = [v.Point for v in face.OuterWire.OrderedVertexes]
                # Make the polygon winding follow the face normal. Face.normalAt()
                # already accounts for the face orientation, so it must not be
                # flipped again for a reversed face.
                normal = face.normalAt(0, 0)
                area = Vector()
                for i in range(len(polygon)):
                    area += polygon[i].cross(polygon[(i + 1) % len(polygon)])
                if area.dot(normal) < 0:
                    polygon.reverse()
        except Exception:  # pylint: disable=broad-except
            polygon = None
        if polygon and len(polygon) >= 3:
            base = len(points)
            points.extend(polygon)
            faces.append([base + i for i in range(len(polygon))])
        else:
            verts, tris = face.tessellate(0.1)
            base = len(points)
            points.extend(verts)
            # tessellate() already winds the triangles to match the face normal
            for t in tris:
                faces.append([base + t[0], base + t[1], base + t[2]])
    return weld_points(points, faces, tolerance)
