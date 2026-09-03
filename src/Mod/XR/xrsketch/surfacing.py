# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Surfaces built from curves: loft, revolve, sweep, Coons patch, extrude.

Every constructor returns a :class:`SurfaceMesh` — a ``(nv + 1) x (nu + 1)``
grid of points that can be evaluated, turned into a control cage
(:meth:`SurfaceMesh.to_cage`) or drawn straight away.  That is the
representation the headset needs; :func:`to_part` is the separate step that
turns one into real FreeCAD geometry.

Honest mapping to ``Part``
--------------------------
:func:`to_part` converts a surface into an OCC shape *only where the mapping is
faithful*:

============== ============================================================
extrude        ``BSplineCurve.toShape().extrude(v)`` — exact
revolve        ``shape.revolve(point, axis, angle)`` — exact
loft (ruled)   ``Part.makeLoft(wires, ruled=True)`` — matches the linear
               interpolation between sections used here
============== ============================================================

A Coons patch, a two-rail sweep and a lofted surface with smooth (non-ruled)
sections have **no faithful ``Part`` equivalent**: OCC would build a B-spline
surface through the same boundary data, and that surface is not the same
surface — it agrees on the boundary and differs in the middle.  Rather than
quietly hand back something that is nearly right, :func:`to_part` raises
:class:`UnsupportedMapping` and names the alternative,
:func:`to_mesh_shape`, which is explicitly an approximation.
"""

import math

from xrpaint import stroke3d as _stroke3d

from . import vecmath as vm
from .curves import Curve3D
from .subd import Cage

__all__ = [
    "SurfaceMesh",
    "UnsupportedMapping",
    "coons_patch",
    "extrude",
    "loft",
    "revolve",
    "sweep",
    "sweep_two_rails",
    "to_mesh_shape",
    "to_part",
]

_EPS = 1e-12


class UnsupportedMapping(RuntimeError):
    """Raised when a surface has no faithful ``Part`` counterpart."""


# --------------------------------------------------------------------------
# sampling helpers
# --------------------------------------------------------------------------

def _as_points(obj, count):
    """Uniform arc-length samples of a curve, or a resampled point list."""
    if isinstance(obj, Curve3D):
        pts = obj.flatten(1e-4)
        if obj.closed and pts and vm.dist(pts[0], pts[-1]) > 1e-12:
            pts = pts + [pts[0]]
    else:
        pts = [vm.vec3(p) for p in obj]
    if len(pts) < 2:
        raise ValueError("a section needs at least two points")
    return _resample(pts, count)


def _resample(points, count):
    """``count + 1`` points evenly spaced along a polyline by arc length."""
    count = max(1, int(count))
    lengths = [0.0]
    for i in range(1, len(points)):
        lengths.append(lengths[-1] + vm.dist(points[i - 1], points[i]))
    total = lengths[-1]
    if total < _EPS:
        return [vm.vec3(points[0])] * (count + 1)
    out = []
    j = 0
    for i in range(count + 1):
        target = total * i / float(count)
        while j < len(lengths) - 2 and lengths[j + 1] < target:
            j += 1
        seg = lengths[j + 1] - lengths[j]
        t = 0.0 if seg < _EPS else (target - lengths[j]) / seg
        out.append(vm.lerp(points[j], points[j + 1], t))
    return out


def _check_finite(points, what):
    for p in points:
        for c in p:
            if not math.isfinite(c):
                raise ValueError("%s contains a non-finite coordinate" % what)


# --------------------------------------------------------------------------
# the surface
# --------------------------------------------------------------------------

class SurfaceMesh(object):
    """A quad grid of points, ``grid[v][u]``, plus the maths to use it."""

    def __init__(self, grid, closed_u=False, closed_v=False, kind="mesh",
                 name=None, provenance=None):
        rows = [[vm.vec3(p) for p in row] for row in grid]
        if len(rows) < 2 or len(rows[0]) < 2:
            raise ValueError("a surface needs at least a 2x2 grid, got %dx%d"
                             % (len(rows), len(rows[0]) if rows else 0))
        width = len(rows[0])
        for row in rows:
            if len(row) != width:
                raise ValueError("the surface grid is ragged")
            _check_finite(row, "the surface grid")
        self.grid = rows
        self.closed_u = bool(closed_u)
        self.closed_v = bool(closed_v)
        self.kind = kind
        self.name = name or kind
        #: how the surface was built, for :func:`to_part`
        self.provenance = dict(provenance or {})

    # -- shape -----------------------------------------------------------
    @property
    def nu(self):
        return len(self.grid[0]) - 1

    @property
    def nv(self):
        return len(self.grid) - 1

    def points(self):
        return [p for row in self.grid for p in row]

    def index(self, iu, iv):
        return iv * (self.nu + 1) + iu

    def quads(self):
        out = []
        for iv in range(self.nv):
            for iu in range(self.nu):
                out.append((self.index(iu, iv), self.index(iu + 1, iv),
                            self.index(iu + 1, iv + 1),
                            self.index(iu, iv + 1)))
        return out

    def triangles(self):
        out = []
        for a, b, c, d in self.quads():
            out.append((a, b, c))
            out.append((a, c, d))
        return out

    def to_cage(self, weld_tolerance=1e-9):
        """A :class:`xrsketch.subd.Cage` of the grid, seams welded."""
        cage = Cage(self.points(), self.quads())
        if weld_tolerance > 0.0:
            cage.weld(weld_tolerance)
        return cage

    def bounds(self):
        pts = self.points()
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return (lo, hi)

    # -- evaluation ------------------------------------------------------
    def evaluate(self, u, v):
        """Bilinear evaluation, ``u`` and ``v`` in ``[0, 1]``."""
        u = vm.clamp(float(u), 0.0, 1.0) * self.nu
        v = vm.clamp(float(v), 0.0, 1.0) * self.nv
        iu = min(int(math.floor(u)), self.nu - 1)
        iv = min(int(math.floor(v)), self.nv - 1)
        fu = u - iu
        fv = v - iv
        a = vm.lerp(self.grid[iv][iu], self.grid[iv][iu + 1], fu)
        b = vm.lerp(self.grid[iv + 1][iu], self.grid[iv + 1][iu + 1], fu)
        return vm.lerp(a, b, fv)

    def normal(self, u, v, eps=1e-4):
        du = vm.sub(self.evaluate(min(1.0, u + eps), v),
                    self.evaluate(max(0.0, u - eps), v))
        dv = vm.sub(self.evaluate(u, min(1.0, v + eps)),
                    self.evaluate(u, max(0.0, v - eps)))
        return vm.normalize(vm.cross(du, dv), (0.0, 0.0, 1.0))

    def closest_point(self, p, refine=3):
        """``(point, (u, v), distance)`` of the closest point on the grid."""
        p = vm.vec3(p)
        best = None
        for iv, row in enumerate(self.grid):
            for iu, q in enumerate(row):
                d = vm.dist(q, p)
                if best is None or d < best[2]:
                    best = (q, (iu / float(self.nu), iv / float(self.nv)), d)
        u, v = best[1]
        du = 1.0 / self.nu
        dv = 1.0 / self.nv
        for _ in range(max(0, int(refine))):
            du *= 0.5
            dv *= 0.5
            for cu in (u - du, u, u + du):
                for cv in (v - dv, v, v + dv):
                    cu2 = vm.clamp(cu, 0.0, 1.0)
                    cv2 = vm.clamp(cv, 0.0, 1.0)
                    q = self.evaluate(cu2, cv2)
                    d = vm.dist(q, p)
                    if d < best[2]:
                        best = (q, (cu2, cv2), d)
            u, v = best[1]
        return best

    def project_point(self, p, direction=None):
        """Project onto the surface along ``direction``, or to the nearest
        point when no direction is given (or the ray misses)."""
        if direction is not None and vm.length(direction) > _EPS:
            hit = self.raycast(p, direction)
            if hit is not None:
                return hit[0]
            hit = self.raycast(p, vm.neg(direction))
            if hit is not None:
                return hit[0]
        return self.closest_point(p)[0]

    def raycast(self, origin, direction):
        """First triangle hit as ``(point, distance)``, or ``None``."""
        o = vm.vec3(origin)
        d = vm.normalize(direction)
        if vm.length(d) < 0.5:
            return None
        pts = self.points()
        best = None
        for tri in self.triangles():
            hit = _ray_triangle(o, d, pts[tri[0]], pts[tri[1]], pts[tri[2]])
            if hit is not None and (best is None or hit[1] < best[1]):
                best = hit
        return best

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {"kind": self.kind, "name": self.name,
                "closed_u": self.closed_u, "closed_v": self.closed_v,
                "grid": [[list(p) for p in row] for row in self.grid]}

    @classmethod
    def from_dict(cls, d):
        return cls(d["grid"], d.get("closed_u", False),
                   d.get("closed_v", False), d.get("kind", "mesh"),
                   d.get("name"))

    def __repr__(self):
        return "SurfaceMesh(%r, %dx%d)" % (self.kind, self.nu + 1,
                                           self.nv + 1)


def _ray_triangle(o, d, a, b, c):
    """Moller-Trumbore; returns ``(point, t)`` for ``t > 0``."""
    e1 = vm.sub(b, a)
    e2 = vm.sub(c, a)
    h = vm.cross(d, e2)
    det = vm.dot(e1, h)
    if abs(det) < 1e-12:
        return None
    inv = 1.0 / det
    s = vm.sub(o, a)
    u = inv * vm.dot(s, h)
    if u < -1e-9 or u > 1.0 + 1e-9:
        return None
    q = vm.cross(s, e1)
    v = inv * vm.dot(d, q)
    if v < -1e-9 or u + v > 1.0 + 1e-9:
        return None
    t = inv * vm.dot(e2, q)
    if t <= 1e-9:
        return None
    return (vm.add(o, vm.mul(d, t)), t)


# --------------------------------------------------------------------------
# constructors
# --------------------------------------------------------------------------

def loft(sections, samples=32, closed_sections=False, closed_loop=False,
         name=None):
    """Skin a surface through a sequence of section curves.

    Sections are resampled to a common number of points by arc length and
    joined by straight lines, i.e. a *ruled* loft — which is what
    ``Part.makeLoft(..., ruled=True)`` builds too, so this one maps faithfully.
    """
    if sections is None or len(sections) < 2:
        raise ValueError("a loft needs at least two sections")
    rows = []
    for section in sections:
        rows.append(_as_points(section, samples))
    if closed_loop:
        rows.append(list(rows[0]))
    return SurfaceMesh(rows, closed_u=closed_sections, closed_v=closed_loop,
                       kind="loft", name=name,
                       provenance={"sections": list(sections),
                                   "ruled": True})


def revolve(profile, axis_point=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0),
            angle=2.0 * math.pi, segments=32, samples=32, name=None):
    """Revolve a profile about an axis."""
    a = vm.normalize(axis)
    if vm.length(a) < 0.5:
        raise ValueError("revolve needs a non-zero axis")
    segments = int(segments)
    if segments < 1:
        raise ValueError("revolve needs at least one segment")
    angle = float(angle)
    if abs(angle) < 1e-9:
        raise ValueError("revolve needs a non-zero angle")
    section = _as_points(profile, samples)
    o = vm.vec3(axis_point)
    rows = []
    for i in range(segments + 1):
        th = angle * i / float(segments)
        q = vm.quat_from_axis_angle(a, th)
        rows.append([vm.add(o, vm.quat_rotate(q, vm.sub(p, o)))
                     for p in section])
    full = abs(abs(angle) - 2.0 * math.pi) < 1e-9
    return SurfaceMesh(rows, closed_u=False, closed_v=full, kind="revolve",
                       name=name,
                       provenance={"profile": profile, "axis_point": o,
                                   "axis": a, "angle": angle})


def sweep(profile, rail, samples=32, stations=32, scale=None, name=None):
    """Sweep a profile along one rail.

    Frames come from
    :func:`xrpaint.stroke3d.parallel_transport_frames`
    — the same rotation-minimising frames the 3D brush strokes use — so the
    profile does not spin at inflection points.  ``scale`` may be a callable
    ``f(t) -> factor`` for a tapering sweep.
    """
    section = _as_points(profile, samples)
    path = _as_points(rail, stations)
    if len(path) < 2:
        raise ValueError("a sweep needs a rail with at least two points")
    tangents, normals, binormals = _stroke3d.parallel_transport_frames(path)
    origin = _centroid(section)
    t0, n0, b0 = tangents[0], normals[0], binormals[0]
    local = []
    for p in section:
        d = vm.sub(p, origin)
        local.append((vm.dot(d, n0), vm.dot(d, b0), vm.dot(d, t0)))
    rows = []
    for i, base in enumerate(path):
        f = 1.0
        if scale is not None:
            f = float(scale(i / float(len(path) - 1)))
        row = []
        for x, y, z in local:
            row.append(vm.add(base,
                              vm.add(vm.add(vm.mul(normals[i], x * f),
                                            vm.mul(binormals[i], y * f)),
                                     vm.mul(tangents[i], z * f))))
        rows.append(row)
    return SurfaceMesh(rows, kind="sweep", name=name,
                       provenance={"profile": profile, "rail": rail})


def sweep_two_rails(profile, rail_a, rail_b, samples=32, stations=32,
                    name=None):
    """Sweep a profile between two rails.

    At every station the profile is placed so that its first point sits on
    ``rail_a`` and its last on ``rail_b``, scaled by the ratio of the rail
    separation to the profile's own span and rotated by the minimal rotation
    carrying one onto the other.  This is the classic two-rail sweep; it has
    no faithful ``Part`` counterpart (see :func:`to_part`).
    """
    section = _as_points(profile, samples)
    ra = _as_points(rail_a, stations)
    rb = _as_points(rail_b, stations)
    span = vm.sub(section[-1], section[0])
    span_len = vm.length(span)
    if span_len < _EPS:
        raise ValueError("a two-rail sweep needs a profile with two distinct "
                         "ends")
    rows = []
    for i in range(len(ra)):
        a = ra[i]
        b = rb[i]
        target = vm.sub(b, a)
        L = vm.length(target)
        if L < _EPS:
            raise ValueError("the two rails touch at station %d" % i)
        factor = L / span_len
        q = _rotation_between(span, target)
        rows.append([vm.add(a, vm.mul(vm.quat_rotate(q, vm.sub(p, section[0])),
                                      factor))
                     for p in section])
    return SurfaceMesh(rows, kind="sweep2", name=name,
                       provenance={"profile": profile, "rails": (rail_a,
                                                                 rail_b)})


def extrude(section, vector, segments=1, samples=32, name=None):
    """Extrude a curve along a straight vector."""
    v = vm.vec3(vector)
    if vm.length(v) < _EPS:
        raise ValueError("extrude needs a non-zero vector")
    segments = int(segments)
    if segments < 1:
        raise ValueError("extrude needs at least one segment")
    base = _as_points(section, samples)
    rows = []
    for i in range(segments + 1):
        f = i / float(segments)
        rows.append([vm.add(p, vm.mul(v, f)) for p in base])
    closed = isinstance(section, Curve3D) and section.closed
    return SurfaceMesh(rows, closed_u=closed, kind="extrude", name=name,
                       provenance={"section": section, "vector": v})


def coons_patch(boundaries, nu=16, nv=16, tolerance=None, name=None):
    """A bilinearly blended Coons patch from three or four boundary curves.

    The boundaries are chained by their endpoints and then

        ``S(u,v) = (1-v)·B0(u) + v·B2(u) + (1-u)·B3(v) + u·B1(v)
                   - bilinear(corners)``

    which interpolates all four boundaries *exactly*.  Three curves are
    accepted by degenerating the fourth boundary to the shared corner, giving
    a triangular patch with one pole.
    """
    if boundaries is None or len(boundaries) not in (3, 4):
        raise ValueError("a Coons patch needs three or four boundary curves")
    nu = max(1, int(nu))
    nv = max(1, int(nv))
    loops = [_as_points(b, max(nu, nv) * 2) for b in boundaries]
    if tolerance is None:
        extent = 0.0
        for pts in loops:
            for p in pts:
                extent = max(extent, vm.length(p))
        tolerance = max(1e-9, extent * 1e-6)
    ordered = _order_boundary(loops, tolerance)
    if len(ordered) == 3:
        bottom, right, top = ordered
        left = [top[-1]] * 2
        top = list(reversed(top))
    else:
        bottom, right, top, left = ordered
        top = list(reversed(top))
        left = list(reversed(left))
    b0 = _resample(bottom, nu)
    b2 = _resample(top, nu)
    b3 = _resample(left, nv)
    b1 = _resample(right, nv)
    p00, p10 = b0[0], b0[-1]
    p01, p11 = b2[0], b2[-1]
    grid = []
    for j in range(nv + 1):
        v = j / float(nv)
        row = []
        for i in range(nu + 1):
            u = i / float(nu)
            lc = vm.add(vm.mul(b0[i], 1.0 - v), vm.mul(b2[i], v))
            ld = vm.add(vm.mul(b3[j], 1.0 - u), vm.mul(b1[j], u))
            bl = vm.add(
                vm.add(vm.mul(p00, (1.0 - u) * (1.0 - v)),
                       vm.mul(p10, u * (1.0 - v))),
                vm.add(vm.mul(p01, (1.0 - u) * v), vm.mul(p11, u * v)))
            row.append(vm.sub(vm.add(lc, ld), bl))
        grid.append(row)
    return SurfaceMesh(grid, kind="coons", name=name,
                       provenance={"boundaries": list(boundaries)})


def _order_boundary(loops, tolerance):
    """Chain boundary point lists head to tail; flips them as needed."""
    remaining = list(loops)
    chain = [remaining.pop(0)]
    while remaining:
        tail = chain[-1][-1]
        best = None
        for i, pts in enumerate(remaining):
            for flipped in (False, True):
                cand = list(reversed(pts)) if flipped else pts
                d = vm.dist(tail, cand[0])
                if best is None or d < best[0]:
                    best = (d, i, cand)
        if best[0] > tolerance:
            raise ValueError("the boundary curves do not form a closed loop "
                             "(gap of %.6g)" % best[0])
        chain.append(best[2])
        remaining.pop(best[1])
    if vm.dist(chain[-1][-1], chain[0][0]) > tolerance:
        raise ValueError("the boundary curves do not close up (gap of %.6g)"
                         % vm.dist(chain[-1][-1], chain[0][0]))
    return chain


def _centroid(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n)


def _rotation_between(a, b):
    na = vm.normalize(a)
    nb = vm.normalize(b)
    if vm.length(na) < 0.5 or vm.length(nb) < 0.5:
        return vm.IDENTITY_QUAT
    d = vm.clamp(vm.dot(na, nb), -1.0, 1.0)
    if d > 1.0 - 1e-12:
        return vm.IDENTITY_QUAT
    if d < -1.0 + 1e-12:
        return vm.quat_from_axis_angle(vm.any_perp(na), math.pi)
    axis = vm.cross(na, nb)
    return vm.quat_from_axis_angle(axis, math.acos(d))


# --------------------------------------------------------------------------
# FreeCAD conversion
# --------------------------------------------------------------------------

#: which surface kinds have an exact ``Part`` counterpart
FAITHFUL_KINDS = ("extrude", "revolve", "loft")


def to_part(surface, tolerance=1e-6):
    """Convert a surface to a ``Part`` shape, or say why it cannot be.

    Raises :class:`UnsupportedMapping` for the kinds listed in the module
    docstring; use :func:`to_mesh_shape` when an approximation is acceptable
    and you want to be seen to have chosen one.
    """
    kind = surface.kind
    if kind not in FAITHFUL_KINDS:
        raise UnsupportedMapping(
            "a %r surface has no faithful Part equivalent: OCC would fit a "
            "B-spline surface through the same data, which agrees on the "
            "boundary and differs in the interior. Use to_mesh_shape() for an "
            "explicit approximation." % (kind,))
    try:
        import Part
        import FreeCAD
    except Exception as exc:                     # pragma: no cover - host
        raise UnsupportedMapping("Part is not importable here (%s)" % exc)
    vec = FreeCAD.Vector
    prov = surface.provenance

    if kind == "extrude":
        wire = _wire_from_points(Part, vec, surface.grid[0],
                                 _is_closed(surface.grid[0], tolerance))
        return wire.extrude(vec(*prov["vector"]))
    if kind == "revolve":
        wire = _wire_from_points(Part, vec, surface.grid[0],
                                 _is_closed(surface.grid[0], tolerance))
        return wire.revolve(vec(*prov["axis_point"]), vec(*prov["axis"]),
                            math.degrees(prov["angle"]))
    wires = []
    for row in surface.grid:
        wires.append(_wire_from_points(Part, vec, row,
                                       _is_closed(row, tolerance)))
    return Part.makeLoft(wires, False, True)


def _is_closed(points, tolerance):
    return len(points) > 2 and vm.dist(points[0], points[-1]) <= tolerance


def _wire_from_points(Part, vec, points, closed):
    pts = [vec(*p) for p in points]
    if closed and vm.dist(points[0], points[-1]) <= 1e-12:
        pts = pts[:-1]
    spline = Part.BSplineCurve()
    spline.interpolate(pts, PeriodicFlag=bool(closed))
    return Part.Wire([spline.toShape()])


def to_mesh_shape(surface, as_mesh=True):
    """An explicit triangle-mesh approximation of any surface.

    With ``as_mesh`` this is a ``Mesh.Mesh``; otherwise a ``Part.Shell`` of
    planar triangles.  Either way it is an approximation of the evaluated
    grid, not a parametric surface — that is the point of the separate name.
    """
    pts = surface.points()
    tris = surface.triangles()
    if as_mesh:
        import Mesh
        facets = []
        for a, b, c in tris:
            facets.append([pts[a], pts[b], pts[c]])
        return Mesh.Mesh([tuple(v) for f in facets for v in f])
    import Part
    import FreeCAD
    vec = FreeCAD.Vector
    faces = []
    for a, b, c in tris:
        poly = Part.makePolygon([vec(*pts[a]), vec(*pts[b]), vec(*pts[c]),
                                 vec(*pts[a])])
        faces.append(Part.Face(poly))
    return Part.makeShell(faces)
