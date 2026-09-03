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
"""Curve networks drawn in the air.

A :class:`Curve3D` is a chain of cubic Beziers held as anchor points with two
relative handles each — the same vocabulary as :class:`xrpaint.vector.Node`
(``corner`` / ``smooth`` / ``symmetric``), lifted into three dimensions.

Reusing the planar curve maths
------------------------------
There is exactly one Bezier implementation in this workbench,
:mod:`xrpaint.curve`, and this module reuses it rather than growing a second
one:

* evaluation, the derivative, de Casteljau splitting and Catmull-Rom
  conversion are *affine in each coordinate*, so a 3D call is two planar calls
  — one on ``(x, y)`` and one on ``(z, 0)`` — and is exact, not an
  approximation.  ``_pair``/``_lift`` do the shuffling.
* freehand fitting goes through :func:`xrpaint.curve.fit_curve`, Schneider's
  algorithm with corner detection, applied *in the plane of the stroke*.  A
  hand-drawn 3D stroke is cut into runs that are planar within ``plane_tol``
  (a recursive split at the point of largest deviation from the best fit
  plane, :func:`xrsketch.vecmath.plane_from_points`), each run is fitted in
  its own plane, and the runs are welded at shared sample points.  A flat
  stroke — the common case, since people draw against a surface or a
  reference plane — is therefore a single planar fit with full corner
  detection, and a genuinely spatial stroke degrades into planar pieces
  instead of into a different algorithm.

Arc length is the one thing that does not decompose per coordinate, so it is
measured by adaptive flattening rather than by the planar quadrature.
"""

import math

from xrpaint import curve as _curve

from . import vecmath as vm

__all__ = [
    "ControlPoint",
    "Curve3D",
    "CurveNetwork",
    "NODE_TYPES",
    "bezier_derivative3",
    "bezier_point3",
    "bezier_split3",
    "bezier_subdivide3",
    "bezier_tangent3",
    "fit_curve3d",
    "flatten_bezier3",
    "join",
    "line_to_bezier3",
    "mirror",
    "offset",
    "project_to_plane",
    "project_to_surface",
    "split",
    "trim",
]

NODE_TYPES = ("corner", "smooth", "symmetric")
_EPS = 1e-12


# --------------------------------------------------------------------------
# 3D Beziers on top of the planar ones
# --------------------------------------------------------------------------

def _pair_xy(bez):
    return [(p[0], p[1]) for p in bez]


def _pair_z(bez):
    return [(p[2], 0.0) for p in bez]


def bezier_point3(bez, t):
    """Evaluate a 3D cubic — two exact planar evaluations."""
    xy = _curve.bezier_point(_pair_xy(bez), t)
    z = _curve.bezier_point(_pair_z(bez), t)
    return (xy[0], xy[1], z[0])


def bezier_derivative3(bez, t):
    xy = _curve.bezier_derivative(_pair_xy(bez), t)
    z = _curve.bezier_derivative(_pair_z(bez), t)
    return (xy[0], xy[1], z[0])


def bezier_tangent3(bez, t):
    d = bezier_derivative3(bez, t)
    if vm.length(d) > _EPS:
        return vm.normalize(d)
    for a, b in ((0, 3), (0, 2), (1, 3), (1, 2)):
        v = vm.sub(bez[b], bez[a])
        if vm.length(v) > _EPS:
            return vm.normalize(v)
    return (0.0, 0.0, 0.0)


def bezier_split3(bez, t):
    """de Casteljau split of a 3D cubic (exact, per coordinate)."""
    lxy, rxy = _curve.bezier_split(_pair_xy(bez), t)
    lz, rz = _curve.bezier_split(_pair_z(bez), t)
    left = tuple((lxy[i][0], lxy[i][1], lz[i][0]) for i in range(4))
    right = tuple((rxy[i][0], rxy[i][1], rz[i][0]) for i in range(4))
    return (left, right)


def bezier_subdivide3(bez, t0=0.0, t1=1.0):
    lxy = _curve.bezier_subdivide(_pair_xy(bez), t0, t1)
    lz = _curve.bezier_subdivide(_pair_z(bez), t0, t1)
    return tuple((lxy[i][0], lxy[i][1], lz[i][0]) for i in range(4))


def line_to_bezier3(a, b):
    a = vm.vec3(a)
    b = vm.vec3(b)
    return (a, vm.lerp(a, b, 1.0 / 3.0), vm.lerp(a, b, 2.0 / 3.0), b)


def _is_flat3(bez, tol):
    p0, p1, p2, p3 = bez
    d = vm.sub(p3, p0)
    n = vm.length(d)
    if n < _EPS:
        return vm.dist(p1, p0) <= tol and vm.dist(p2, p0) <= tol
    d1 = vm.length(vm.cross(vm.sub(p1, p0), d)) / n
    d2 = vm.length(vm.cross(vm.sub(p2, p0), d)) / n
    return max(d1, d2) <= tol


def flatten_bezier3(bez, tol=1e-3, max_depth=20, include_start=True):
    """Adaptively flatten one 3D cubic into a polyline."""
    out = []
    if include_start:
        out.append(vm.vec3(bez[0]))

    def rec(b, depth):
        if depth >= max_depth or _is_flat3(b, tol):
            out.append(vm.vec3(b[3]))
            return
        left, right = bezier_split3(b, 0.5)
        rec(left, depth + 1)
        rec(right, depth + 1)

    rec(tuple(vm.vec3(p) for p in bez), 0)
    return out


def bezier_length3(bez, tol=1e-4):
    """Arc length by adaptive flattening (length is not separable)."""
    pts = flatten_bezier3(bez, tol)
    return sum(vm.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def closest_point_on_bezier3(bez, p, samples=32, refine=24):
    """``(t, point, distance)`` of the closest point on one 3D cubic."""
    p = vm.vec3(p)
    best_t = 0.0
    best_d = float("inf")
    for i in range(samples + 1):
        t = i / float(samples)
        d = vm.dist(bezier_point3(bez, t), p)
        if d < best_d:
            best_d = d
            best_t = t
    step = 1.0 / samples
    lo = max(0.0, best_t - step)
    hi = min(1.0, best_t + step)
    for _ in range(refine):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if vm.dist(bezier_point3(bez, m1), p) < \
                vm.dist(bezier_point3(bez, m2), p):
            hi = m2
        else:
            lo = m1
    t = 0.5 * (lo + hi)
    pt = bezier_point3(bez, t)
    d = vm.dist(pt, p)
    if best_d < d:
        t, pt, d = best_t, bezier_point3(bez, best_t), best_d
    return (t, pt, d)


# --------------------------------------------------------------------------
# freehand fitting
# --------------------------------------------------------------------------

def remove_duplicates3(points, eps=1e-9):
    out = []
    for p in points:
        p = vm.vec3(p)
        if not out or vm.dist(out[-1], p) > eps:
            out.append(p)
    return out


def fit_curve3d(points, error=0.002, corner_angle=60.0, plane_tol=None,
                simplify_tol=0.0, min_run=4):
    """Fit a chain of 3D cubics through sampled ``points``.

    The polyline is first cut into the longest possible runs that are planar
    within ``plane_tol`` (greedy, left to right, consecutive runs sharing their
    boundary sample so the chain is welded), then every run is fitted in its
    own plane by :func:`xrpaint.curve.fit_curve`.  ``error`` and ``plane_tol``
    are in the units of the points (metres in the headset) and ``plane_tol``
    defaults to half of ``error``, so the planarity approximation is never the
    dominant error.
    """
    pts = remove_duplicates3(points)
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        return [line_to_bezier3(pts[0], pts[1])]
    if plane_tol is None:
        plane_tol = 0.5 * float(error)
    out = []
    for run in _planar_runs(pts, float(plane_tol), max(2, int(min_run))):
        out.extend(_fit_planar_run(run, float(error), float(corner_angle),
                                   float(simplify_tol)))
    return out


def _planar_runs(pts, plane_tol, min_run=4):
    """Split a polyline into consecutive runs that are planar within tol."""
    n = len(pts)
    runs = []
    start = 0
    while start < n - 1:
        end = min(start + 2, n - 1)
        while end < n - 1:
            dev = _plane_deviation(pts[start:end + 2])
            if dev is not None and dev > plane_tol:
                break
            end += 1
        if n - 1 - end < min_run and end < n - 1:
            # a stub at the tail would fit badly on its own; absorb it
            end = n - 1
        runs.append(pts[start:end + 1])
        start = end
    return runs


def _plane_deviation(pts):
    plane = vm.plane_from_points(pts)
    return None if plane is None else plane[2]


def _fit_planar_run(pts, error, corner_angle, simplify_tol):
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        return [line_to_bezier3(pts[0], pts[1])]
    plane = vm.plane_from_points(pts)
    if plane is None:
        origin = pts[0]
        normal = vm.normalize(vm.cross(vm.sub(pts[1], pts[0]),
                                       vm.sub(pts[-1], pts[0])),
                              (0.0, 0.0, 1.0))
    else:
        origin, normal, _dev = plane
    _n, u, v = vm.orthonormal_basis(normal, vm.sub(pts[-1], pts[0]))
    flat = [(vm.dot(vm.sub(p, origin), u), vm.dot(vm.sub(p, origin), v))
            for p in pts]
    segs = _curve.fit_curve(flat, error=error, corner_angle=corner_angle,
                            simplify_tol=simplify_tol)
    if not segs:
        return [line_to_bezier3(pts[0], pts[-1])]
    out = []
    for bez in segs:
        out.append(tuple(vm.add(origin, vm.add(vm.mul(u, c[0]),
                                               vm.mul(v, c[1])))
                         for c in bez))
    # the endpoints of the run are known exactly; keep them exact
    first = list(out[0])
    first[0] = pts[0]
    out[0] = tuple(first)
    last = list(out[-1])
    last[3] = pts[-1]
    out[-1] = tuple(last)
    return out


# --------------------------------------------------------------------------
# control points
# --------------------------------------------------------------------------

class ControlPoint(object):
    """An anchor with two relative handles, exactly like a 2D vector node."""

    __slots__ = ("position", "handle_in", "handle_out", "type")

    def __init__(self, position, handle_in=None, handle_out=None,
                 type="corner"):
        if type not in NODE_TYPES:
            raise ValueError("unknown control point type: %r" % (type,))
        self.position = vm.vec3(position)
        self.handle_in = None if handle_in is None else vm.vec3(handle_in)
        self.handle_out = None if handle_out is None else vm.vec3(handle_out)
        self.type = type

    @property
    def point(self):
        """Alias for :attr:`position` (``xrpaint.vector.Node`` vocabulary)."""
        return self.position

    @property
    def in_point(self):
        return vm.add(self.position, self.handle_in or (0.0, 0.0, 0.0))

    @property
    def out_point(self):
        return vm.add(self.position, self.handle_out or (0.0, 0.0, 0.0))

    def copy(self):
        return ControlPoint(self.position, self.handle_in, self.handle_out,
                            self.type)

    def set_position(self, p):
        self.position = vm.vec3(p)
        return self

    def move(self, delta):
        self.position = vm.add(self.position, vm.vec3(delta))
        return self

    def set_in_point(self, p):
        return self.set_handle_in(vm.sub(vm.vec3(p), self.position))

    def set_out_point(self, p):
        return self.set_handle_out(vm.sub(vm.vec3(p), self.position))

    def set_handle_out(self, h):
        self.handle_out = None if h is None else vm.vec3(h)
        if self.handle_out is None:
            return self
        if self.type == "symmetric":
            self.handle_in = vm.neg(self.handle_out)
        elif self.type == "smooth" and self.handle_in is not None:
            L = vm.length(self.handle_in)
            d = vm.normalize(self.handle_out)
            self.handle_in = vm.mul(d, -L) if L > _EPS else (0.0, 0.0, 0.0)
        return self

    def set_handle_in(self, h):
        self.handle_in = None if h is None else vm.vec3(h)
        if self.handle_in is None:
            return self
        if self.type == "symmetric":
            self.handle_out = vm.neg(self.handle_in)
        elif self.type == "smooth" and self.handle_out is not None:
            L = vm.length(self.handle_out)
            d = vm.normalize(self.handle_in)
            self.handle_out = vm.mul(d, -L) if L > _EPS else (0.0, 0.0, 0.0)
        return self

    def set_type(self, type, enforce=True):
        if type not in NODE_TYPES:
            raise ValueError("unknown control point type: %r" % (type,))
        self.type = type
        return self.enforce() if enforce else self

    def enforce(self):
        """Force the handles to satisfy the point's constraint."""
        hin, hout = self.handle_in, self.handle_out
        if self.type == "corner" or (hin is None and hout is None):
            return self
        d_out = vm.normalize(hout) if hout is not None else (0.0, 0.0, 0.0)
        d_in = (vm.normalize(vm.neg(hin)) if hin is not None
                else (0.0, 0.0, 0.0))
        d = vm.add(d_out, d_in)
        if vm.length(d) < _EPS:
            d = d_out if vm.length(d_out) > _EPS else d_in
        d = vm.normalize(d)
        if vm.length(d) < _EPS:
            return self
        if self.type == "symmetric":
            lens = [vm.length(h) for h in (hout, hin) if h is not None]
            L = sum(lens) / len(lens)
            self.handle_out = vm.mul(d, L)
            self.handle_in = vm.mul(d, -L)
        else:
            if hout is not None:
                self.handle_out = vm.mul(d, vm.length(hout))
            if hin is not None:
                self.handle_in = vm.mul(d, -vm.length(hin))
        return self

    def to_dict(self):
        return {"point": list(self.position),
                "in": None if self.handle_in is None else list(self.handle_in),
                "out": (None if self.handle_out is None
                        else list(self.handle_out)),
                "type": self.type}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("point", (0.0, 0.0, 0.0)), d.get("in"), d.get("out"),
                   d.get("type", "corner"))

    def __repr__(self):
        return "ControlPoint(%s, %s)" % (
            tuple(round(c, 5) for c in self.position), self.type)


# --------------------------------------------------------------------------
# the curve
# --------------------------------------------------------------------------

class Curve3D(object):
    """A chain of cubic Bezier segments in space."""

    _next_id = [0]

    def __init__(self, points=None, closed=False, name=None):
        self.points = list(points or [])
        self.closed = bool(closed)
        Curve3D._next_id[0] += 1
        self.id = "c%d" % Curve3D._next_id[0]
        self.name = name or self.id

    # -- construction ----------------------------------------------------
    @classmethod
    def from_beziers(cls, beziers, closed=False, smooth_tol=1e-6, name=None):
        """Build control points from a chain of cubics."""
        beziers = [tuple(vm.vec3(p) for p in b) for b in beziers]
        if not beziers:
            return cls([], closed, name)
        pts = []
        n = len(beziers)
        for i, bez in enumerate(beziers):
            cp = ControlPoint(bez[0])
            cp.handle_out = vm.sub(bez[1], bez[0])
            if i > 0:
                prev = beziers[i - 1]
                cp.handle_in = vm.sub(prev[2], prev[3])
            elif closed:
                prev = beziers[-1]
                cp.handle_in = vm.sub(prev[2], prev[3])
            _classify(cp, smooth_tol)
            pts.append(cp)
        if not closed:
            last = ControlPoint(beziers[-1][3])
            last.handle_in = vm.sub(beziers[-1][2], beziers[-1][3])
            pts.append(last)
        return cls(pts, closed, name)

    @classmethod
    def from_points(cls, points, closed=False, smooth=True, tension=0.5,
                    name=None):
        """Place control points by hand.

        ``smooth`` runs the points through
        :func:`xrpaint.curve.catmull_rom_to_bezier` (exact per coordinate, so
        the 3D result is the planar algorithm applied twice); otherwise the
        points are joined by straight segments.
        """
        pts = remove_duplicates3(points)
        if len(pts) < 2:
            return cls([ControlPoint(p) for p in pts], False, name)
        if not smooth:
            beziers = [line_to_bezier3(pts[i], pts[i + 1])
                       for i in range(len(pts) - 1)]
            if closed:
                beziers.append(line_to_bezier3(pts[-1], pts[0]))
            return cls.from_beziers(beziers, closed, name=name)
        xy = _curve.catmull_rom_to_bezier([(p[0], p[1]) for p in pts], closed,
                                          tension)
        z = _curve.catmull_rom_to_bezier([(p[2], 0.0) for p in pts], closed,
                                         tension)
        beziers = []
        for a, b in zip(xy, z):
            beziers.append(tuple((a[i][0], a[i][1], b[i][0])
                                 for i in range(4)))
        return cls.from_beziers(beziers, closed, name=name)

    @classmethod
    def from_freehand(cls, samples, error=0.002, corner_angle=60.0,
                      closed=False, plane_tol=None, name=None):
        """Fit a hand-drawn stroke (see :func:`fit_curve3d`)."""
        pts = remove_duplicates3(samples)
        if closed and len(pts) > 2 and vm.dist(pts[0], pts[-1]) > 0.0:
            pts.append(pts[0])
        beziers = fit_curve3d(pts, error=error, corner_angle=corner_angle,
                              plane_tol=plane_tol)
        return cls.from_beziers(beziers, closed, name=name)

    def copy(self):
        c = Curve3D([p.copy() for p in self.points], self.closed, self.name)
        return c

    # -- structure -------------------------------------------------------
    def __len__(self):
        return len(self.points)

    def __iter__(self):
        return iter(self.points)

    def segment_count(self):
        n = len(self.points)
        if n < 2:
            return 0
        return n if self.closed else n - 1

    def to_beziers(self):
        out = []
        n = len(self.points)
        for i in range(self.segment_count()):
            a = self.points[i]
            b = self.points[(i + 1) % n]
            p0 = a.position
            p3 = b.position
            c1 = vm.add(p0, a.handle_out or vm.mul(vm.sub(p3, p0), 1.0 / 3.0))
            c2 = vm.add(p3, b.handle_in or vm.mul(vm.sub(p0, p3), 1.0 / 3.0))
            out.append((p0, c1, c2, p3))
        return out

    def append_point(self, point, handle_in=None, handle_out=None,
                     type="corner"):
        cp = point if isinstance(point, ControlPoint) else \
            ControlPoint(point, handle_in, handle_out, type)
        self.points.append(cp)
        return cp

    def remove_point(self, index):
        if not (0 <= index < len(self.points)):
            raise IndexError("no such control point: %r" % (index,))
        return self.points.pop(index)

    def insert_point(self, segment, t):
        """Split a segment at ``t``, inserting a control point."""
        segs = self.to_beziers()
        if not (0 <= segment < len(segs)):
            raise IndexError("no such segment: %r" % (segment,))
        left, right = bezier_split3(segs[segment],
                                   vm.clamp(float(t), 0.0, 1.0))
        n = len(self.points)
        a = self.points[segment]
        b = self.points[(segment + 1) % n]
        a.handle_out = vm.sub(left[1], left[0])
        b.handle_in = vm.sub(right[2], right[3])
        cp = ControlPoint(left[3], vm.sub(left[2], left[3]),
                          vm.sub(right[1], right[0]), "smooth")
        self.points.insert(segment + 1, cp)
        return segment + 1

    def close(self):
        self.closed = True
        return self

    def open(self):
        self.closed = False
        return self

    def reverse(self):
        self.points.reverse()
        for cp in self.points:
            cp.handle_in, cp.handle_out = cp.handle_out, cp.handle_in
        return self

    # -- evaluation ------------------------------------------------------
    def evaluate(self, segment, t):
        segs = self.to_beziers()
        if not segs:
            return self.points[0].position if self.points else (0.0, 0.0, 0.0)
        segment = int(vm.clamp(segment, 0, len(segs) - 1))
        return bezier_point3(segs[segment], vm.clamp(float(t), 0.0, 1.0))

    def point_at(self, s):
        """Evaluate at the global parameter ``s`` in ``[0, segment_count]``."""
        n = self.segment_count()
        if n == 0:
            return self.points[0].position if self.points else (0.0, 0.0, 0.0)
        s = vm.clamp(float(s), 0.0, float(n))
        i = min(int(math.floor(s)), n - 1)
        return self.evaluate(i, s - i)

    def tangent_at(self, segment, t):
        segs = self.to_beziers()
        if not segs:
            return (0.0, 0.0, 0.0)
        segment = int(vm.clamp(segment, 0, len(segs) - 1))
        return bezier_tangent3(segs[segment], vm.clamp(float(t), 0.0, 1.0))

    def flatten(self, tol=1e-3):
        pts = []
        for i, bez in enumerate(self.to_beziers()):
            pts.extend(flatten_bezier3(bez, tol, include_start=(i == 0)))
        if not pts and self.points:
            pts = [self.points[0].position]
        return pts

    def sample(self, count=32):
        """``count + 1`` points evenly spaced in parameter (not arc length)."""
        n = self.segment_count()
        if n == 0:
            return [self.points[0].position] * (count + 1) if self.points \
                else []
        return [self.point_at(n * i / float(count)) for i in range(count + 1)]

    def length(self, tol=1e-4):
        return sum(bezier_length3(b, tol) for b in self.to_beziers())

    def bbox(self):
        pts = self.flatten(1e-3)
        if not pts:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return (lo, hi)

    def start_point(self):
        return self.points[0].position if self.points else None

    def end_point(self):
        return self.points[-1].position if self.points else None

    def closest_point(self, p, samples=32):
        """``(segment, t, point, distance)`` nearest to ``p``."""
        best = (0, 0.0, (0.0, 0.0, 0.0), float("inf"))
        for i, bez in enumerate(self.to_beziers()):
            t, pt, d = closest_point_on_bezier3(bez, p, samples)
            if d < best[3]:
                best = (i, t, pt, d)
        return best

    def is_planar(self, tol=1e-6):
        """``(origin, normal)`` when the curve is planar, else ``None``."""
        pts = self.flatten(max(tol, 1e-4))
        plane = vm.plane_from_points(pts)
        if plane is None:
            if len(pts) < 2:
                return None
            return (pts[0], vm.any_perp(vm.normalize(vm.sub(pts[-1], pts[0]))))
        origin, normal, dev = plane
        if dev > tol:
            return None
        return (origin, normal)

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {"id": self.id, "name": self.name, "closed": self.closed,
                "points": [p.to_dict() for p in self.points]}

    @classmethod
    def from_dict(cls, d):
        c = cls([ControlPoint.from_dict(p) for p in d.get("points", [])],
                bool(d.get("closed", False)), d.get("name"))
        if d.get("id"):
            c.id = d["id"]
        return c

    def __repr__(self):
        return "Curve3D(%r, %d points%s)" % (self.name, len(self.points),
                                             ", closed" if self.closed else "")


def _classify(cp, tol=1e-6):
    hin, hout = cp.handle_in, cp.handle_out
    if hin is None or hout is None:
        cp.type = "corner"
        return cp
    li, lo = vm.length(hin), vm.length(hout)
    if li < tol or lo < tol:
        cp.type = "corner"
        return cp
    if vm.length(vm.cross(hin, hout)) / (li * lo) > tol or \
            vm.dot(hin, hout) > 0.0:
        cp.type = "corner"
    elif abs(li - lo) <= tol * max(li, lo):
        cp.type = "symmetric"
    else:
        cp.type = "smooth"
    return cp


# --------------------------------------------------------------------------
# curve operations
# --------------------------------------------------------------------------

def split(curve, segment, t):
    """Split a curve in two at ``(segment, t)``; returns two curves."""
    segs = curve.to_beziers()
    if not segs:
        raise ValueError("cannot split an empty curve")
    if curve.closed:
        raise ValueError("open the curve before splitting it")
    segment = int(vm.clamp(segment, 0, len(segs) - 1))
    t = vm.clamp(float(t), 0.0, 1.0)
    left_bez, right_bez = bezier_split3(segs[segment], t)
    left = segs[:segment] + [left_bez]
    right = [right_bez] + segs[segment + 1:]
    return (Curve3D.from_beziers(left, name=curve.name + "_a"),
            Curve3D.from_beziers(right, name=curve.name + "_b"))


def trim(curve, s0, s1):
    """Keep the part of a curve between two global parameters."""
    segs = curve.to_beziers()
    n = len(segs)
    if n == 0:
        raise ValueError("cannot trim an empty curve")
    s0 = vm.clamp(float(s0), 0.0, float(n))
    s1 = vm.clamp(float(s1), 0.0, float(n))
    if s1 < s0:
        s0, s1 = s1, s0
    if s1 - s0 < 1e-12:
        raise ValueError("trim range is empty")
    i0 = min(int(math.floor(s0)), n - 1)
    i1 = min(int(math.floor(s1)), n - 1)
    t0 = s0 - i0
    t1 = s1 - i1
    if i0 == i1:
        out = [bezier_subdivide3(segs[i0], t0, t1)]
    else:
        out = [bezier_subdivide3(segs[i0], t0, 1.0)]
        out.extend(segs[i0 + 1:i1])
        if t1 > 1e-12:
            out.append(bezier_subdivide3(segs[i1], 0.0, t1))
    return Curve3D.from_beziers(out, name=curve.name + "_trim")


def join(a, b, tolerance=1e-6, weld=True):
    """Join two curves end to end, flipping either if that is what meets.

    Returns a new curve, or ``None`` when no pair of endpoints is within
    ``tolerance``.  When the *other* two ends meet as well the result is a
    closed curve, so joining the halves of a loop closes it.
    """
    if a.closed or b.closed:
        return None
    sa, ea = a.start_point(), a.end_point()
    sb, eb = b.start_point(), b.end_point()
    if None in (sa, ea, sb, eb):
        return None
    options = [
        (vm.dist(ea, sb), False, False),
        (vm.dist(ea, eb), False, True),
        (vm.dist(sa, sb), True, False),
        (vm.dist(sa, eb), True, True),
    ]
    options.sort(key=lambda o: o[0])
    d, flip_a, flip_b = options[0]
    if d > tolerance:
        return None
    ca = a.copy()
    cb = b.copy()
    if flip_a:
        ca.reverse()
    if flip_b:
        cb.reverse()
    beziers = ca.to_beziers()
    tail = cb.to_beziers()
    if weld and beziers and tail:
        first = list(tail[0])
        first[0] = beziers[-1][3]
        tail[0] = tuple(first)
    segments = beziers + tail
    closed = (len(segments) > 1
              and vm.dist(segments[0][0], segments[-1][3]) <= tolerance)
    if closed and weld:
        # welding the loop shut as well, so the seam is one point
        last = list(segments[-1])
        last[3] = segments[0][0]
        segments[-1] = tuple(last)
    return Curve3D.from_beziers(segments, closed,
                                name="%s+%s" % (a.name, b.name))


def mirror(curve, origin=(0.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0), name=None):
    """Mirror a curve across a plane (winding is reversed with it)."""
    n = vm.normalize(normal)
    if vm.length(n) < 0.5:
        raise ValueError("mirror plane needs a non-zero normal")
    out = Curve3D([], curve.closed, name or (curve.name + "_mirror"))
    for cp in curve.points:
        p = vm.reflect_point(cp.position, origin, n)
        hin = (None if cp.handle_in is None
               else vm.reflect_vector(cp.handle_in, n))
        hout = None if cp.handle_out is None else \
            vm.reflect_vector(cp.handle_out, n)
        out.points.append(ControlPoint(p, hin, hout, cp.type))
    return out


def project_to_plane(curve, origin=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0),
                     direction=None, name=None):
    """Project a curve onto a plane, along ``direction`` or the normal."""
    n = vm.normalize(normal)
    if vm.length(n) < 0.5:
        raise ValueError("projection plane needs a non-zero normal")
    d = None
    if direction is not None:
        d = vm.normalize(direction)
        if vm.length(d) < 0.5 or abs(vm.dot(d, n)) < 1e-9:
            raise ValueError("projection direction is parallel to the plane")

    def push(p):
        if d is None:
            return vm.sub(p, vm.mul(n, vm.dot(vm.sub(p, origin), n)))
        k = vm.dot(vm.sub(origin, p), n) / vm.dot(d, n)
        return vm.add(p, vm.mul(d, k))

    beziers = []
    for bez in curve.to_beziers():
        beziers.append(tuple(push(c) for c in bez))
    return Curve3D.from_beziers(beziers, curve.closed,
                                name=name or (curve.name + "_proj"))


def project_to_surface(curve, surface, samples=48, direction=None,
                       error=None, name=None):
    """Project a curve onto a surface and refit it.

    ``surface`` may be a plane given as ``(origin, normal)``, handled exactly
    by :func:`project_to_plane` — or anything with a ``project_point(p,
    direction)`` or ``closest_point(p)`` method, such as
    :class:`xrsketch.surfacing.SurfaceMesh`.  In the second case the curve is
    sampled, the samples are pushed onto the surface and the result refitted,
    which is an approximation whose accuracy is set by ``samples``.
    """
    if isinstance(surface, (tuple, list)) and len(surface) == 2:
        return project_to_plane(curve, surface[0], surface[1], direction,
                                name)
    push = getattr(surface, "project_point", None)
    if push is None:
        near = getattr(surface, "closest_point", None)
        if near is None:
            raise TypeError("surface has neither project_point() nor "
                            "closest_point()")

        def push(p, direction=None):
            return near(p)[0]
    pts = []
    for p in curve.sample(max(4, int(samples))):
        q = push(p, direction) if direction is not None else push(p)
        if q is None:
            continue
        pts.append(vm.vec3(q))
    if len(pts) < 2:
        raise ValueError("the curve does not project onto the surface")
    if error is None:
        lo, hi = curve.bbox()
        error = max(1e-6, 0.01 * vm.dist(lo, hi))
    return Curve3D.from_beziers(fit_curve3d(pts, error=error,
                                            corner_angle=0.0),
                                curve.closed,
                                name=name or (curve.name + "_onsurf"))


def offset(curve, distance, normal=None, tol=None, name=None):
    """Offset a *planar* curve inside its own plane.

    Delegates to :func:`xrpaint.curve.offset_path`, so the behaviour matches
    the 2D vector editor exactly.  A non-planar curve raises ``ValueError``
    rather than returning a plausible-looking wrong answer: offsetting a
    spatial curve is only defined once you say which surface it should stay
    on, and that is :func:`project_to_surface`'s job.
    """
    plane = curve.is_planar(tol if tol is not None else 1e-6)
    if plane is None and normal is None:
        raise ValueError("offset needs a planar curve, or an explicit plane "
                         "normal to offset within")
    if normal is not None:
        n = vm.normalize(normal)
        origin = curve.start_point() or (0.0, 0.0, 0.0)
    else:
        origin, n = plane
    if vm.length(n) < 0.5:
        raise ValueError("offset plane needs a non-zero normal")
    _n, u, v = vm.orthonormal_basis(n)
    flat = []
    for bez in curve.to_beziers():
        flat.append(tuple((vm.dot(vm.sub(c, origin), u),
                           vm.dot(vm.sub(c, origin), v)) for c in bez))
    out2 = _curve.offset_path(flat, float(distance))
    if not out2:
        raise ValueError("the offset collapsed")
    beziers = []
    for bez in out2:
        beziers.append(tuple(vm.add(origin, vm.add(vm.mul(u, c[0]),
                                                   vm.mul(v, c[1])))
                             for c in bez))
    return Curve3D.from_beziers(beziers, curve.closed,
                                name=name or (curve.name + "_offset"))


# --------------------------------------------------------------------------
# networks
# --------------------------------------------------------------------------

class CurveNetwork(object):
    """A bag of curves plus the endpoint queries a curve network needs."""

    def __init__(self, curves=None):
        self.curves = list(curves or [])

    def __len__(self):
        return len(self.curves)

    def __iter__(self):
        return iter(self.curves)

    def add(self, curve):
        self.curves.append(curve)
        return curve

    def remove(self, curve):
        if curve in self.curves:
            self.curves.remove(curve)
            return True
        return False

    def by_id(self, curve_id):
        for c in self.curves:
            if c.id == curve_id:
                return c
        return None

    def endpoints(self):
        """``(curve, which, point)`` for every free end."""
        out = []
        for c in self.curves:
            if c.closed or not c.points:
                continue
            out.append((c, 0, c.start_point()))
            out.append((c, 1, c.end_point()))
        return out

    def junctions(self, tolerance=1e-6):
        """Groups of endpoints that meet, as lists of ``(curve, which)``."""
        ends = self.endpoints()
        used = [False] * len(ends)
        groups = []
        for i, (c, w, p) in enumerate(ends):
            if used[i]:
                continue
            group = [(c, w)]
            used[i] = True
            for j in range(i + 1, len(ends)):
                if used[j]:
                    continue
                if vm.dist(p, ends[j][2]) <= tolerance:
                    used[j] = True
                    group.append((ends[j][0], ends[j][1]))
            if len(group) > 1:
                groups.append(group)
        return groups

    def join_all(self, tolerance=1e-6):
        """Repeatedly join curves whose ends meet; returns how many joins."""
        joins = 0
        changed = True
        while changed:
            changed = False
            for i in range(len(self.curves)):
                for j in range(i + 1, len(self.curves)):
                    merged = join(self.curves[i], self.curves[j], tolerance)
                    if merged is None:
                        continue
                    self.curves = ([self.curves[k]
                                    for k in range(len(self.curves))
                                    if k not in (i, j)] + [merged])
                    joins += 1
                    changed = True
                    break
                if changed:
                    break
        return joins

    def to_dict(self):
        return {"curves": [c.to_dict() for c in self.curves]}

    @classmethod
    def from_dict(cls, d):
        return cls([Curve3D.from_dict(c) for c in d.get("curves", [])])

    def __repr__(self):
        return "CurveNetwork(%d curves)" % (len(self.curves),)
