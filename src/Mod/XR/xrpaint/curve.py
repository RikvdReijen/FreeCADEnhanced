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
"""Planar curve mathematics for the VR vector editor.

Pure standard library, no Coin and no FreeCAD (ARCHITECTURE.md §6 explicitly
names this module).  Points are plain ``(x, y)`` tuples and a cubic Bezier is a
4-tuple of them, ``(p0, c1, c2, p3)``.

Contents
--------
* evaluation, derivatives, de Casteljau subdivision, arc length, flattening
* Schneider's "An Algorithm for Automatically Fitting Digitized Curves"
  (Graphics Gems, 1990) with corner detection, used to clean up freehand VR
  strokes
* Douglas-Peucker simplification
* Catmull-Rom to Bezier conversion
* path offsetting
* closest point on a curve / path, for node picking with the controller ray
"""

import math

__all__ = [
    "bezier_bbox",
    "bezier_derivative",
    "bezier_length",
    "bezier_point",
    "bezier_second_derivative",
    "bezier_split",
    "bezier_subdivide",
    "bezier_tangent",
    "catmull_rom_to_bezier",
    "closest_point_on_bezier",
    "closest_point_on_path",
    "detect_corners",
    "douglas_peucker",
    "fit_curve",
    "flatten_bezier",
    "flatten_path",
    "line_to_bezier",
    "offset_path",
    "path_length",
    "point_at_length",
    "remove_duplicates",
    "resample_uniform",
]

EPS = 1e-12


# --------------------------------------------------------------------------
# tiny 2d vector helpers (tuples in, tuples out)
# --------------------------------------------------------------------------

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _mul(a, s):
    return (a[0] * s, a[1] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _hypot(a):
    return math.hypot(a[0], a[1])


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _norm(a):
    n = math.hypot(a[0], a[1])
    if n < EPS:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def _perp(a):
    return (-a[1], a[0])


# --------------------------------------------------------------------------
# cubic Bezier basics
# --------------------------------------------------------------------------

def bezier_point(bez, t):
    """Evaluate the cubic at ``t`` in [0, 1]."""
    p0, p1, p2, p3 = bez
    mt = 1.0 - t
    a = mt * mt * mt
    b = 3.0 * mt * mt * t
    c = 3.0 * mt * t * t
    d = t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def bezier_derivative(bez, t):
    """First derivative dB/dt (a vector, not normalised)."""
    p0, p1, p2, p3 = bez
    mt = 1.0 - t
    a = 3.0 * mt * mt
    b = 6.0 * mt * t
    c = 3.0 * t * t
    return (a * (p1[0] - p0[0]) + b * (p2[0] - p1[0]) + c * (p3[0] - p2[0]),
            a * (p1[1] - p0[1]) + b * (p2[1] - p1[1]) + c * (p3[1] - p2[1]))


def bezier_second_derivative(bez, t):
    p0, p1, p2, p3 = bez
    mt = 1.0 - t
    a = 6.0 * mt
    b = 6.0 * t
    return (a * (p2[0] - 2.0 * p1[0] + p0[0])
            + b * (p3[0] - 2.0 * p2[0] + p1[0]),
            a * (p2[1] - 2.0 * p1[1] + p0[1])
            + b * (p3[1] - 2.0 * p2[1] + p1[1]))


def bezier_tangent(bez, t):
    """Unit tangent; falls back to neighbouring control points when the
    derivative degenerates (repeated control points)."""
    d = bezier_derivative(bez, t)
    if _hypot(d) > EPS:
        return _norm(d)
    p0, p1, p2, p3 = bez
    for a, b in ((p0, p3), (p0, p2), (p1, p3), (p1, p2)):
        v = _sub(b, a)
        if _hypot(v) > EPS:
            return _norm(v)
    return (0.0, 0.0)


def bezier_split(bez, t):
    """de Casteljau split; returns the two sub curves."""
    p0, p1, p2, p3 = bez
    p01 = _lerp(p0, p1, t)
    p12 = _lerp(p1, p2, t)
    p23 = _lerp(p2, p3, t)
    p012 = _lerp(p01, p12, t)
    p123 = _lerp(p12, p23, t)
    p0123 = _lerp(p012, p123, t)
    return ((p0, p01, p012, p0123), (p0123, p123, p23, p3))


def bezier_subdivide(bez, t0=0.0, t1=1.0):
    """The sub curve covering the parameter range [t0, t1]."""
    if t1 < t0:
        t0, t1 = t1, t0
    if t0 <= 0.0 and t1 >= 1.0:
        return tuple(bez)
    if t0 <= 0.0:
        return bezier_split(bez, t1)[0]
    if t1 >= 1.0:
        return bezier_split(bez, t0)[1]
    right = bezier_split(bez, t0)[1]
    tt = (t1 - t0) / (1.0 - t0)
    return bezier_split(right, tt)[0]


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def bezier_bbox(bez):
    """Tight axis aligned bounding box ``(xmin, ymin, xmax, ymax)``."""
    xs = [bez[0][0], bez[3][0]]
    ys = [bez[0][1], bez[3][1]]
    for axis, acc in ((0, xs), (1, ys)):
        p0 = bez[0][axis]
        p1 = bez[1][axis]
        p2 = bez[2][axis]
        p3 = bez[3][axis]
        a = -p0 + 3.0 * p1 - 3.0 * p2 + p3
        b = 2.0 * (p0 - 2.0 * p1 + p2)
        c = p1 - p0
        # derivative of the cubic is 3*(a t^2 + b t + c)
        for t in _quad_roots(a, b, c):
            if 0.0 < t < 1.0:
                acc.append(bezier_point(bez, t)[axis])
    return (min(xs), min(ys), max(xs), max(ys))


def _quad_roots(a, b, c):
    out = []
    if abs(a) < EPS:
        if abs(b) > EPS:
            out.append(-c / b)
        return out
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return out
    sq = math.sqrt(disc)
    out.append((-b + sq) / (2.0 * a))
    out.append((-b - sq) / (2.0 * a))
    return out


# --------------------------------------------------------------------------
# length and flattening
# --------------------------------------------------------------------------

# 16 point Gauss-Legendre nodes/weights on [-1, 1]
_GL16 = (
    (-0.0950125098376374, 0.1894506104550685),
    (0.0950125098376374, 0.1894506104550685),
    (-0.2816035507792589, 0.1826034150449236),
    (0.2816035507792589, 0.1826034150449236),
    (-0.4580167776572274, 0.1691565193950025),
    (0.4580167776572274, 0.1691565193950025),
    (-0.6178762444026438, 0.1495959888165767),
    (0.6178762444026438, 0.1495959888165767),
    (-0.7554044083550030, 0.1246289712555339),
    (0.7554044083550030, 0.1246289712555339),
    (-0.8656312023878318, 0.0951585116824928),
    (0.8656312023878318, 0.0951585116824928),
    (-0.9445750230732326, 0.0622535239386479),
    (0.9445750230732326, 0.0622535239386479),
    (-0.9894009349916499, 0.0271524594117541),
    (0.9894009349916499, 0.0271524594117541),
)


def bezier_length(bez, t0=0.0, t1=1.0, samples=None):
    """Arc length over [t0, t1] by 16 point Gauss-Legendre quadrature.

    ``samples`` forces a plain polyline approximation with that many segments
    instead (used by the tests to cross-check the quadrature).
    """
    if t1 < t0:
        t0, t1 = t1, t0
    if samples:
        prev = bezier_point(bez, t0)
        total = 0.0
        for i in range(1, samples + 1):
            t = t0 + (t1 - t0) * i / float(samples)
            cur = bezier_point(bez, t)
            total += _dist(prev, cur)
            prev = cur
        return total
    half = 0.5 * (t1 - t0)
    mid = 0.5 * (t0 + t1)
    total = 0.0
    for x, w in _GL16:
        d = bezier_derivative(bez, mid + half * x)
        total += w * math.hypot(d[0], d[1])
    return total * half


def _is_flat(bez, tol):
    p0, p1, p2, p3 = bez
    d = _sub(p3, p0)
    n = _hypot(d)
    if n < EPS:
        return (_dist(p1, p0) <= tol) and (_dist(p2, p0) <= tol)
    d1 = abs(_cross(_sub(p1, p0), d)) / n
    d2 = abs(_cross(_sub(p2, p0), d)) / n
    return max(d1, d2) <= tol


def flatten_bezier(bez, tol=0.1, max_depth=20, include_start=True):
    """Adaptively flatten one cubic into a polyline within ``tol``."""
    out = []
    if include_start:
        out.append(tuple(bez[0]))

    def rec(b, depth):
        if depth >= max_depth or _is_flat(b, tol):
            out.append(tuple(b[3]))
            return
        left, right = bezier_split(b, 0.5)
        rec(left, depth + 1)
        rec(right, depth + 1)

    rec(tuple(bez), 0)
    return out


def flatten_path(beziers, tol=0.1):
    """Flatten a list of cubics into a single polyline."""
    pts = []
    for i, b in enumerate(beziers):
        seg = flatten_bezier(b, tol, include_start=(i == 0))
        pts.extend(seg)
    return pts


def path_length(beziers):
    return sum(bezier_length(b) for b in beziers)


def point_at_length(beziers, s, tol=1e-9):
    """Point at arc length ``s`` along a list of cubics.  Returns
    ``(index, t, point)``."""
    if not beziers:
        return (0, 0.0, (0.0, 0.0))
    remaining = max(0.0, float(s))
    for i, b in enumerate(beziers):
        L = bezier_length(b)
        if remaining > L and i < len(beziers) - 1:
            remaining -= L
            continue
        if L <= EPS:
            return (i, 0.0, tuple(b[0]))
        lo, hi = 0.0, 1.0
        target = min(remaining, L)
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if bezier_length(b, 0.0, mid) < target:
                lo = mid
            else:
                hi = mid
            if hi - lo < tol:
                break
        t = 0.5 * (lo + hi)
        return (i, t, bezier_point(b, t))
    return (len(beziers) - 1, 1.0, tuple(beziers[-1][3]))


def line_to_bezier(p0, p1):
    """A straight cubic with handles at the thirds."""
    return (tuple(p0), _lerp(p0, p1, 1.0 / 3.0), _lerp(p0, p1, 2.0 / 3.0),
            tuple(p1))


def resample_uniform(points, step):
    """Resample a polyline to a uniform arc length step."""
    pts = [tuple(p) for p in points]
    if len(pts) < 2 or step <= 0:
        return pts
    out = [pts[0]]
    acc = 0.0
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        seg = _dist(a, b)
        if seg < EPS:
            continue
        pos = 0.0
        while acc + (seg - pos) >= step:
            pos += step - acc
            acc = 0.0
            out.append(_lerp(a, b, pos / seg))
        acc += seg - pos
    if _dist(out[-1], pts[-1]) > EPS:
        out.append(pts[-1])
    return out


def remove_duplicates(points, eps=1e-9):
    """Drop consecutive duplicated points."""
    out = []
    for p in points:
        p = (float(p[0]), float(p[1]))
        if not out or _dist(out[-1], p) > eps:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# Douglas-Peucker
# --------------------------------------------------------------------------

def douglas_peucker(points, tol):
    """Classic polyline simplification; endpoints are always preserved."""
    pts = [tuple(p) for p in points]
    n = len(pts)
    if n <= 2 or tol <= 0.0:
        return list(pts)
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        a = pts[i0]
        b = pts[i1]
        ab = _sub(b, a)
        L = _hypot(ab)
        best = -1.0
        besti = -1
        for i in range(i0 + 1, i1):
            p = pts[i]
            if L < EPS:
                d = _dist(p, a)
            else:
                d = abs(_cross(_sub(p, a), ab)) / L
            if d > best:
                best = d
                besti = i
        if best > tol and besti > 0:
            keep[besti] = True
            stack.append((i0, besti))
            stack.append((besti, i1))
    return [pts[i] for i in range(n) if keep[i]]


# --------------------------------------------------------------------------
# Catmull-Rom
# --------------------------------------------------------------------------

def catmull_rom_to_bezier(points, closed=False, tension=0.5):
    """Convert an interpolating Catmull-Rom spline to cubic Bezier segments.

    ``tension`` 0.5 is the classic (centripetal-free) uniform Catmull-Rom.
    """
    pts = [tuple(p) for p in points]
    n = len(pts)
    if n < 2:
        return []
    if n == 2 and not closed:
        return [line_to_bezier(pts[0], pts[1])]
    out = []
    count = n if closed else n - 1
    for i in range(count):
        if closed:
            p0 = pts[(i - 1) % n]
            p1 = pts[i % n]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n]
        else:
            p0 = pts[i - 1] if i > 0 else pts[0]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[n - 1]
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3.0,
              p1[1] + (p2[1] - p0[1]) * tension / 3.0)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3.0,
              p2[1] - (p3[1] - p1[1]) * tension / 3.0)
        out.append((p1, c1, c2, p2))
    return out


# --------------------------------------------------------------------------
# Schneider curve fitting
# --------------------------------------------------------------------------

def detect_corners(points, angle_deg=60.0, min_len=1e-9, window=1,
                   min_gap=2):
    """Indices of points where the polyline turns by more than ``angle_deg``.

    The turn angle is measured between the incoming and the outgoing direction,
    so 0 degrees is perfectly straight and 90 degrees is a right angle corner.
    Endpoints are never reported.  ``window`` widens the direction estimate
    over several samples, which suppresses false corners on noisy VR input;
    ``min_gap`` merges corners that land on neighbouring samples.
    """
    pts = [tuple(p) for p in points]
    n = len(pts)
    if n < 3:
        return []
    window = max(1, int(window))
    thresh = math.cos(math.radians(_clamp_deg(angle_deg)))
    raw = []
    for i in range(1, n - 1):
        j0 = max(0, i - window)
        j1 = min(n - 1, i + window)
        a = _sub(pts[i], pts[j0])
        b = _sub(pts[j1], pts[i])
        if _hypot(a) < min_len or _hypot(b) < min_len:
            continue
        c = _dot(_norm(a), _norm(b))
        # c == 1 -> straight, c == -1 -> full reversal
        if c < thresh:
            raw.append((i, c))
    # collapse runs of neighbouring detections down to the sharpest one
    out = []
    group = []
    for idx, c in raw:
        if group and idx - group[-1][0] > max(1, int(min_gap)):
            out.append(min(group, key=lambda g: g[1])[0])
            group = []
        group.append((idx, c))
    if group:
        out.append(min(group, key=lambda g: g[1])[0])
    return out


def _clamp_deg(a):
    a = float(a)
    if a < 0.0:
        return 0.0
    if a > 180.0:
        return 180.0
    return a


def _chord_length_parameterize(pts):
    u = [0.0]
    for i in range(1, len(pts)):
        u.append(u[i - 1] + _dist(pts[i], pts[i - 1]))
    total = u[-1]
    if total < EPS:
        return [i / float(max(1, len(pts) - 1)) for i in range(len(pts))]
    return [v / total for v in u]


def _b0(u):
    return (1.0 - u) ** 3


def _b1(u):
    return 3.0 * u * (1.0 - u) ** 2


def _b2(u):
    return 3.0 * u * u * (1.0 - u)


def _b3(u):
    return u ** 3


def _generate_bezier(pts, u, t1, t2):
    """Least squares fit of one cubic with prescribed end tangents."""
    n = len(pts)
    a0 = []
    a1 = []
    for i in range(n):
        a0.append(_mul(t1, _b1(u[i])))
        a1.append(_mul(t2, _b2(u[i])))
    c00 = c01 = c11 = x0 = x1 = 0.0
    first = pts[0]
    last = pts[-1]
    for i in range(n):
        c00 += _dot(a0[i], a0[i])
        c01 += _dot(a0[i], a1[i])
        c11 += _dot(a1[i], a1[i])
        tmp = _sub(pts[i], _add(_add(_mul(first, _b0(u[i])),
                                     _mul(first, _b1(u[i]))),
                                _add(_mul(last, _b2(u[i])),
                                     _mul(last, _b3(u[i])))))
        x0 += _dot(a0[i], tmp)
        x1 += _dot(a1[i], tmp)
    det_c0_c1 = c00 * c11 - c01 * c01
    det_c0_x = c00 * x1 - c01 * x0
    det_x_c1 = x0 * c11 - c01 * x1
    alpha_l = 0.0 if abs(det_c0_c1) < EPS else det_x_c1 / det_c0_c1
    alpha_r = 0.0 if abs(det_c0_c1) < EPS else det_c0_x / det_c0_c1
    seg_len = _dist(first, last)
    epsilon = 1.0e-6 * seg_len
    if alpha_l < epsilon or alpha_r < epsilon:
        d = seg_len / 3.0
        return (first, _add(first, _mul(t1, d)), _add(last, _mul(t2, d)),
                last)
    return (first, _add(first, _mul(t1, alpha_l)),
            _add(last, _mul(t2, alpha_r)), last)


def _compute_max_error(pts, bez, u):
    max_dist = 0.0
    split = len(pts) // 2
    for i in range(1, len(pts) - 1):
        p = bezier_point(bez, u[i])
        d = _dist(p, pts[i])
        d2 = d * d
        if d2 >= max_dist:
            max_dist = d2
            split = i
    return math.sqrt(max_dist), split


def _newton_raphson_root(bez, p, u):
    q = bezier_point(bez, u)
    d1 = bezier_derivative(bez, u)
    d2 = bezier_second_derivative(bez, u)
    diff = _sub(q, p)
    num = _dot(diff, d1)
    den = _dot(d1, d1) + _dot(diff, d2)
    if abs(den) < EPS:
        return u
    return u - num / den


def _reparameterize(pts, u, bez):
    return [_newton_raphson_root(bez, pts[i], u[i]) for i in range(len(pts))]


def _fit_cubic(pts, t1, t2, error, depth=0, max_depth=24):
    n = len(pts)
    if n < 2:
        return []
    if n == 2:
        d = _dist(pts[0], pts[1]) / 3.0
        return [(pts[0], _add(pts[0], _mul(t1, d)),
                 _add(pts[1], _mul(t2, d)), pts[1])]
    u = _chord_length_parameterize(pts)
    bez = _generate_bezier(pts, u, t1, t2)
    max_err, split = _compute_max_error(pts, bez, u)
    if max_err < error:
        return [bez]
    # Newton-Raphson reparameterisation before giving up and splitting; the
    # original Graphics Gems code only tried this for near misses, but always
    # trying it costs little and keeps the segment count much lower.
    if depth < max_depth:
        for _ in range(20):
            u_prime = _reparameterize(pts, u, bez)
            cand = _generate_bezier(pts, u_prime, t1, t2)
            cand_err, cand_split = _compute_max_error(pts, cand, u_prime)
            if cand_err < error:
                return [cand]
            if cand_err >= max_err:
                break
            bez, max_err, split, u = cand, cand_err, cand_split, u_prime
    if depth >= max_depth:
        return [bez]
    if split <= 0 or split >= n - 1:
        split = n // 2
    tc = _norm(_sub(pts[split - 1], pts[split + 1]))
    if _hypot(tc) < EPS:
        tc = _norm(_sub(pts[split - 1], pts[split]))
    left = _fit_cubic(pts[:split + 1], t1, tc, error, depth + 1, max_depth)
    right = _fit_cubic(pts[split:], _mul(tc, -1.0), t2, error, depth + 1,
                       max_depth)
    return left + right


def fit_curve(points, error=1.0, corner_angle=60.0, simplify_tol=0.0,
              return_corners=False):
    """Fit a chain of cubic Beziers through noisy sampled ``points``.

    This is Schneider's algorithm (Graphics Gems I) with a corner detection
    pre-pass: the polyline is first cut at sharp turns, and every run is fitted
    independently so corners are not smoothed away.

    Returns the list of cubics, or ``(cubics, corner_indices)`` when
    ``return_corners`` is true, where the indices refer to the *output* node
    sequence (0 = first node, ``len(cubics)`` = last node).
    """
    pts = remove_duplicates(points, 1e-9)
    if simplify_tol > 0.0:
        pts = douglas_peucker(pts, simplify_tol)
    if len(pts) < 2:
        return ([], []) if return_corners else []
    if len(pts) == 2:
        segs = [line_to_bezier(pts[0], pts[1])]
        return (segs, []) if return_corners else segs

    cuts = detect_corners(pts, corner_angle) if corner_angle else []
    bounds = [0] + list(cuts) + [len(pts) - 1]
    out = []
    corners = []
    for k in range(len(bounds) - 1):
        i0 = bounds[k]
        i1 = bounds[k + 1]
        run = pts[i0:i1 + 1]
        if len(run) < 2:
            continue
        t1 = _norm(_sub(run[1], run[0]))
        t2 = _norm(_sub(run[-2], run[-1]))
        if _hypot(t1) < EPS:
            t1 = _norm(_sub(run[-1], run[0]))
        if _hypot(t2) < EPS:
            t2 = _norm(_sub(run[0], run[-1]))
        segs = _fit_cubic(run, t1, t2, float(error))
        if not segs:
            segs = [line_to_bezier(run[0], run[-1])]
        if out:
            corners.append(len(out))
        out.extend(segs)
    if return_corners:
        return out, corners
    return out


# --------------------------------------------------------------------------
# offsetting
# --------------------------------------------------------------------------

def offset_path(beziers, distance, tol=0.05, flatten_tol=None, error=None):
    """Offset a chain of cubics by ``distance`` (left of travel is positive).

    The curve is flattened, the samples are pushed along their normals and the
    result is refitted, which is accurate enough for a VR stroke outline and
    never explodes on self intersections the way an analytic offset does.
    """
    if not beziers:
        return []
    if flatten_tol is None:
        flatten_tol = max(1e-4, abs(distance) * 0.02, tol)
    if error is None:
        error = max(1e-4, abs(distance) * 0.05, tol)
    samples = []
    for i, b in enumerate(beziers):
        n = max(4, int(bezier_length(b) / max(flatten_tol, 1e-6)) + 1)
        n = min(n, 256)
        start = 0 if i == 0 else 1
        for k in range(start, n + 1):
            t = k / float(n)
            p = bezier_point(b, t)
            tg = bezier_tangent(b, t)
            nrm = _perp(tg)
            samples.append(_add(p, _mul(nrm, distance)))
    samples = remove_duplicates(samples, 1e-9)
    if len(samples) < 2:
        return []
    return fit_curve(samples, error=error, corner_angle=0.0)


# --------------------------------------------------------------------------
# closest point (node picking)
# --------------------------------------------------------------------------

def closest_point_on_bezier(bez, p, samples=32, refine=24):
    """Closest point on one cubic.  Returns ``(t, point, distance)``."""
    p = (float(p[0]), float(p[1]))
    best_t = 0.0
    best_d = float("inf")
    for i in range(samples + 1):
        t = i / float(samples)
        d = _dist(bezier_point(bez, t), p)
        if d < best_d:
            best_d = d
            best_t = t
    step = 1.0 / samples
    lo = max(0.0, best_t - step)
    hi = min(1.0, best_t + step)
    for _ in range(refine):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if _dist(bezier_point(bez, m1), p) < _dist(bezier_point(bez, m2), p):
            hi = m2
        else:
            lo = m1
    t = 0.5 * (lo + hi)
    pt = bezier_point(bez, t)
    d = _dist(pt, p)
    if best_d < d:
        t = best_t
        pt = bezier_point(bez, t)
        d = best_d
    return (t, pt, d)


def closest_point_on_path(beziers, p, samples=32):
    """Closest point on a chain.  Returns ``(index, t, point, distance)``."""
    best = (0, 0.0, (0.0, 0.0), float("inf"))
    for i, b in enumerate(beziers):
        t, pt, d = closest_point_on_bezier(b, p, samples)
        if d < best[3]:
            best = (i, t, pt, d)
    return best
