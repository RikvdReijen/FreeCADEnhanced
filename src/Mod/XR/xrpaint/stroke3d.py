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
"""Tilt-Brush style 3D strokes: ribbons and tubes swept along a VR path.

The maths is pure Python.  Coin, Mesh and Part are imported lazily inside the
emitter functions so the module stays unit-testable (ARCHITECTURE.md §6).

Frames are propagated with *parallel transport* (rotation minimising frames):
each frame is the previous one rotated by the minimal rotation that carries the
previous tangent onto the current one.  A naive Frenet frame flips its normal
at inflection points, which shows up in VR as the ribbon suddenly twisting;
parallel transport cannot do that.
"""

import math

__all__ = [
    "BRUSH_PROFILES",
    "Geometry",
    "Stroke3D",
    "StrokePoint",
    "StrokeSet",
    "decimate3d",
    "parallel_transport_frames",
]

#: Cross section shapes a stroke can be swept with.
BRUSH_PROFILES = ("ribbon", "tube", "taper", "hull")

_EPS = 1e-12


# --------------------------------------------------------------------------
# 3d vector helpers
# --------------------------------------------------------------------------

def _v3(p):
    return (float(p[0]), float(p[1]), float(p[2]))


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _norm(a):
    n = _len(a)
    if n < _EPS:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _dist(a, b):
    return _len(_sub(a, b))


def _any_perp(t):
    """Some unit vector perpendicular to ``t`` (``t`` assumed unit)."""
    ax = abs(t[0])
    ay = abs(t[1])
    az = abs(t[2])
    if ax <= ay and ax <= az:
        other = (1.0, 0.0, 0.0)
    elif ay <= az:
        other = (0.0, 1.0, 0.0)
    else:
        other = (0.0, 0.0, 1.0)
    v = _cross(t, other)
    n = _len(v)
    if n < _EPS:
        return (1.0, 0.0, 0.0)
    return _mul(v, 1.0 / n)


def _rotate(v, axis, cos_a, sin_a):
    """Rodrigues rotation of ``v`` about the unit ``axis``."""
    return _add(_add(_mul(v, cos_a), _mul(_cross(axis, v), sin_a)),
                _mul(axis, _dot(axis, v) * (1.0 - cos_a)))


def _finite(v):
    for c in v:
        if c != c or c in (float("inf"), float("-inf")):
            return False
    return True


# --------------------------------------------------------------------------
# stroke samples
# --------------------------------------------------------------------------

class StrokePoint(object):
    """One controller sample: position, surface normal, radius, timestamp."""

    __slots__ = ("p", "n", "r", "t")

    def __init__(self, p, n=None, r=0.01, t=0.0):
        self.p = _v3(p)
        self.n = None if n is None else _v3(n)
        self.r = float(r)
        self.t = float(t)

    def copy(self):
        return StrokePoint(self.p, self.n, self.r, self.t)

    def to_dict(self):
        d = {"p": list(self.p), "r": self.r, "t": self.t}
        d["n"] = list(self.n) if self.n is not None else None
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["p"], d.get("n"), float(d.get("r", 0.01)),
                   float(d.get("t", 0.0)))

    def __repr__(self):
        return "StrokePoint(%.4g, %.4g, %.4g, r=%.4g)" % (
            self.p[0], self.p[1], self.p[2], self.r)


# --------------------------------------------------------------------------
# geometry container
# --------------------------------------------------------------------------

class Geometry(object):
    """Indexed polygonal geometry, ready for Coin, Mesh or Part."""

    __slots__ = ("vertices", "normals", "uvs", "faces")

    def __init__(self, vertices=None, normals=None, uvs=None, faces=None):
        self.vertices = list(vertices or [])
        self.normals = list(normals or [])
        self.uvs = list(uvs or [])
        self.faces = list(faces or [])

    def __len__(self):
        return len(self.vertices)

    @property
    def vertex_count(self):
        return len(self.vertices)

    @property
    def face_count(self):
        return len(self.faces)

    def triangles(self):
        """Fan-triangulate every face."""
        out = []
        for f in self.faces:
            for i in range(1, len(f) - 1):
                out.append((f[0], f[i], f[i + 1]))
        return out

    def coord_index(self):
        """Flat ``SoIndexedFaceSet.coordIndex`` list with -1 terminators."""
        out = []
        for f in self.faces:
            out.extend(f)
            out.append(-1)
        return out

    def index_count(self):
        return len(self.coord_index())

    def is_finite(self):
        """True when no vertex or normal contains NaN/inf."""
        for v in self.vertices:
            if not _finite(v):
                return False
        for n in self.normals:
            if not _finite(n):
                return False
        return True

    def bbox(self):
        if not self.vertices:
            return None
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def validate(self):
        """Raise ``ValueError`` when an index is out of range."""
        n = len(self.vertices)
        for f in self.faces:
            if len(f) < 3:
                raise ValueError("degenerate face %r" % (f,))
            for i in f:
                if not (0 <= i < n):
                    raise ValueError("face index %d out of range" % i)
        return True

    def __repr__(self):
        return "Geometry(%d verts, %d faces)" % (len(self.vertices),
                                                 len(self.faces))


# --------------------------------------------------------------------------
# frames and decimation
# --------------------------------------------------------------------------

def parallel_transport_frames(points, initial_normal=None):
    """Return ``(tangents, normals, binormals)`` for a 3D polyline.

    ``points`` must already be free of duplicates.  The first normal is the
    component of ``initial_normal`` perpendicular to the first tangent (any
    perpendicular when it is not given or degenerates); every later normal is
    the previous one rotated by the minimal rotation between the tangents.
    """
    pts = [_v3(p) for p in points]
    n = len(pts)
    if n == 0:
        return [], [], []
    if n == 1:
        t = (0.0, 0.0, 1.0)
        nb = _any_perp(t)
        return [t], [nb], [_cross(t, nb)]

    tangents = []
    for i in range(n):
        if i == 0:
            d = _sub(pts[1], pts[0])
        elif i == n - 1:
            d = _sub(pts[n - 1], pts[n - 2])
        else:
            d = _sub(pts[i + 1], pts[i - 1])
        t = _norm(d)
        if _len(t) < 0.5:
            t = tangents[-1] if tangents else (0.0, 0.0, 1.0)
        tangents.append(t)

    if initial_normal is not None:
        nrm = _v3(initial_normal)
        nrm = _sub(nrm, _mul(tangents[0], _dot(nrm, tangents[0])))
        if _len(nrm) < 1e-8:
            nrm = _any_perp(tangents[0])
        else:
            nrm = _norm(nrm)
    else:
        nrm = _any_perp(tangents[0])

    normals = [nrm]
    for i in range(1, n):
        t0 = tangents[i - 1]
        t1 = tangents[i]
        axis = _cross(t0, t1)
        s = _len(axis)
        c = max(-1.0, min(1.0, _dot(t0, t1)))
        prev = normals[-1]
        if s < 1e-9:
            if c > 0.0:
                cur = prev
            else:
                # 180 degree turn: mirror through the plane of the tangent
                cur = _mul(prev, -1.0)
        else:
            axis = _mul(axis, 1.0 / s)
            cur = _rotate(prev, axis, c, s)
        # re-orthogonalise against drift
        cur = _sub(cur, _mul(t1, _dot(cur, t1)))
        if _len(cur) < 1e-9:
            cur = _any_perp(t1)
        else:
            cur = _norm(cur)
        normals.append(cur)

    binormals = [_norm(_cross(tangents[i], normals[i])) for i in range(n)]
    for i in range(n):
        if _len(binormals[i]) < 0.5:
            binormals[i] = _any_perp(tangents[i])
    return tangents, normals, binormals


def decimate3d(points, tol):
    """Douglas-Peucker on a 3D polyline; returns the surviving indices."""
    n = len(points)
    if n <= 2 or tol <= 0.0:
        return list(range(n))
    pts = [_v3(p) for p in points]
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
        L = _len(ab)
        best = -1.0
        besti = -1
        for i in range(i0 + 1, i1):
            ap = _sub(pts[i], a)
            if L < _EPS:
                d = _len(ap)
            else:
                d = _len(_cross(ap, ab)) / L
            if d > best:
                best = d
                besti = i
        if best > tol and besti > 0:
            keep[besti] = True
            stack.append((i0, besti))
            stack.append((besti, i1))
    return [i for i in range(n) if keep[i]]


# --------------------------------------------------------------------------
# the stroke
# --------------------------------------------------------------------------

class Stroke3D(object):
    """A painted-in-the-air stroke (ARCHITECTURE.md §4 ``strokes3d`` entry)."""

    def __init__(self, brush="ribbon", color=(0.0, 0.0, 0.0, 1.0), width=0.01,
                 points=None, sides=8, min_step=None):
        if brush not in BRUSH_PROFILES:
            raise ValueError("unknown brush profile: %r" % (brush,))
        self.brush = brush
        self.color = [float(c) for c in color]
        while len(self.color) < 4:
            self.color.append(1.0)
        self.width = float(width)
        self.points = list(points or [])
        self.sides = max(3, int(sides))
        #: samples closer together than this are dropped on the way in
        self.min_step = (self.width * 0.25) if min_step is None \
            else float(min_step)

    # -- building --------------------------------------------------------
    def add_point(self, p, n=None, pressure=1.0, t=0.0, radius=None,
                  force=False):
        """Append a sample; returns the :class:`StrokePoint` or ``None``.

        Samples closer than :attr:`min_step` to the previous one are dropped
        (they carry no shape information and make the frames unstable).
        """
        p = _v3(p)
        if radius is None:
            radius = self.width * 0.5 * max(0.02, float(pressure))
        if self.points and not force:
            if _dist(self.points[-1].p, p) < self.min_step:
                return None
        sp = StrokePoint(p, n, radius, t)
        self.points.append(sp)
        return sp

    def __len__(self):
        return len(self.points)

    def positions(self):
        return [sp.p for sp in self.points]

    def length(self):
        pts = self.positions()
        return sum(_dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))

    def decimate(self, tol=None):
        """Drop samples that do not change the shape by more than ``tol``."""
        if tol is None:
            tol = self.width * 0.1
        idx = decimate3d(self.positions(), tol)
        self.points = [self.points[i] for i in idx]
        return self

    def clean_points(self, eps=None):
        """Deduplicated samples; the caller never sees zero length segments."""
        if eps is None:
            eps = max(1e-9, self.min_step * 1e-3)
        out = []
        for sp in self.points:
            if not _finite(sp.p):
                continue
            if out and _dist(out[-1].p, sp.p) <= eps:
                # keep the largest radius of a duplicated cluster
                if sp.r > out[-1].r:
                    out[-1] = sp
                continue
            out.append(sp)
        return out

    # -- geometry --------------------------------------------------------
    def build_geometry(self, profile=None, sides=None, caps=True,
                       taper=None, thickness=0.25):
        """Sweep the cross section along the path.

        Returns a :class:`Geometry`.  Vertex/face counts for ``n`` distinct
        samples:

        ============ ================== =========================
        profile      vertices           faces
        ============ ================== =========================
        ribbon       ``2 * n``          ``n - 1``
        hull         ``4 * n``          ``4 * (n - 1)`` (+2 caps)
        tube         ``sides * n``      ``sides * (n - 1)`` (+2 caps)
        taper        ``sides * n``      ``sides * (n - 1)``
        ============ ================== =========================

        A path that collapses to a single distinct point yields a single quad
        billboard (4 vertices, 1 face); an empty path yields empty geometry.
        Neither ever contains NaN.
        """
        profile = profile or self.brush
        if profile not in BRUSH_PROFILES:
            raise ValueError("unknown brush profile: %r" % (profile,))
        sides = self.sides if sides is None else max(3, int(sides))
        pts = self.clean_points()
        if not pts:
            return Geometry()
        if len(pts) == 1:
            return self._billboard(pts[0])

        positions = [sp.p for sp in pts]
        tangents, normals, binormals = parallel_transport_frames(
            positions, pts[0].n)

        # arc length for the v texture coordinate and for tapering
        acc = [0.0]
        for i in range(1, len(positions)):
            acc.append(acc[-1] + _dist(positions[i - 1], positions[i]))
        total = acc[-1] if acc[-1] > _EPS else 1.0

        radii = []
        for i, sp in enumerate(pts):
            r = abs(sp.r)
            if profile == "taper" or taper:
                s = acc[i] / total
                # smooth taper towards both ends
                r *= math.sin(math.pi * min(1.0, max(0.0, s))) ** 0.5 \
                    if 0.0 < s < 1.0 else 0.0
            radii.append(r)
        if profile == "taper" or taper:
            # never emit an exactly zero ring, it makes degenerate faces
            eps_r = max(1e-9, self.width * 1e-4)
            radii = [max(r, eps_r) for r in radii]

        if profile == "ribbon":
            return self._build_ribbon(positions, normals, binormals, radii,
                                      acc, total)
        if profile == "hull":
            return self._build_section(positions, normals, binormals, radii,
                                       acc, total, self._hull_section(
                                           thickness), caps)
        section = self._circle_section(sides)
        return self._build_section(positions, normals, binormals, radii, acc,
                                   total, section,
                                   caps and profile != "taper")

    def _billboard(self, sp):
        r = max(abs(sp.r), 1e-9)
        n = _norm(sp.n) if sp.n is not None else (0.0, 0.0, 1.0)
        if _len(n) < 0.5:
            n = (0.0, 0.0, 1.0)
        u = _any_perp(n)
        v = _norm(_cross(n, u))
        p = sp.p
        verts = [_add(p, _add(_mul(u, -r), _mul(v, -r))),
                 _add(p, _add(_mul(u, r), _mul(v, -r))),
                 _add(p, _add(_mul(u, r), _mul(v, r))),
                 _add(p, _add(_mul(u, -r), _mul(v, r)))]
        return Geometry(verts, [n] * 4,
                        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                        [(0, 1, 2, 3)])

    def _build_ribbon(self, positions, normals, binormals, radii, acc, total):
        verts = []
        norms = []
        uvs = []
        for i, p in enumerate(positions):
            b = binormals[i]
            r = radii[i]
            verts.append(_add(p, _mul(b, -r)))
            verts.append(_add(p, _mul(b, r)))
            norms.append(normals[i])
            norms.append(normals[i])
            v = acc[i] / total
            uvs.append((0.0, v))
            uvs.append((1.0, v))
        faces = []
        for i in range(len(positions) - 1):
            a = 2 * i
            faces.append((a, a + 1, a + 3, a + 2))
        return Geometry(verts, norms, uvs, faces)

    def _circle_section(self, sides):
        out = []
        for k in range(sides):
            a = 2.0 * math.pi * k / sides
            out.append((math.cos(a), math.sin(a)))
        return out

    def _hull_section(self, thickness):
        t = max(0.02, float(thickness))
        return [(-1.0, -t), (1.0, -t), (1.0, t), (-1.0, t)]

    def _build_section(self, positions, normals, binormals, radii, acc, total,
                       section, caps):
        m = len(section)
        verts = []
        norms = []
        uvs = []
        for i, p in enumerate(positions):
            nb = normals[i]
            bb = binormals[i]
            r = radii[i]
            v = acc[i] / total
            for k, (cx, cy) in enumerate(section):
                off = _add(_mul(bb, cx * r), _mul(nb, cy * r))
                verts.append(_add(p, off))
                d = _norm(off)
                norms.append(d if _len(d) > 0.5 else nb)
                uvs.append((k / float(m), v))
        faces = []
        n = len(positions)
        for i in range(n - 1):
            b0 = i * m
            b1 = (i + 1) * m
            for k in range(m):
                k2 = (k + 1) % m
                faces.append((b0 + k, b0 + k2, b1 + k2, b1 + k))
        if caps and m >= 3:
            faces.append(tuple(range(m - 1, -1, -1)))
            base = (n - 1) * m
            faces.append(tuple(range(base, base + m)))
        return Geometry(verts, norms, uvs, faces)

    # -- §4 JSON ---------------------------------------------------------
    def to_dict(self):
        return {
            "brush": self.brush,
            "color": list(self.color),
            "width": self.width,
            "points": [sp.to_dict() for sp in self.points],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("brush", "ribbon"),
                   d.get("color", (0.0, 0.0, 0.0, 1.0)),
                   float(d.get("width", 0.01)),
                   [StrokePoint.from_dict(p) for p in d.get("points", [])])

    # -- emitters (lazy imports) ----------------------------------------
    def to_coin(self, geometry=None, two_sided=True):
        """Build an ``SoSeparator`` holding an ``SoIndexedFaceSet``."""
        from pivy.coin import (SoSeparator, SoCoordinate3, SoIndexedFaceSet,
                               SoMaterial, SoNormal, SoNormalBinding,
                               SoShapeHints, SoTextureCoordinate2)
        geo = geometry or self.build_geometry()
        sep = SoSeparator()
        hints = SoShapeHints()
        hints.vertexOrdering = SoShapeHints.COUNTERCLOCKWISE
        if two_sided:
            hints.shapeType = SoShapeHints.UNKNOWN_SHAPE_TYPE
        sep.addChild(hints)
        mat = SoMaterial()
        mat.diffuseColor.setValue(self.color[0], self.color[1], self.color[2])
        if len(self.color) > 3 and self.color[3] < 1.0:
            mat.transparency.setValue(1.0 - self.color[3])
        sep.addChild(mat)
        coords = SoCoordinate3()
        for i, v in enumerate(geo.vertices):
            coords.point.set1Value(i, v[0], v[1], v[2])
        sep.addChild(coords)
        if geo.normals:
            nrm = SoNormal()
            for i, v in enumerate(geo.normals):
                nrm.vector.set1Value(i, v[0], v[1], v[2])
            sep.addChild(nrm)
            binding = SoNormalBinding()
            binding.value = SoNormalBinding.PER_VERTEX_INDEXED
            sep.addChild(binding)
        if geo.uvs:
            tc = SoTextureCoordinate2()
            for i, uv in enumerate(geo.uvs):
                tc.point.set1Value(i, uv[0], uv[1])
            sep.addChild(tc)
        fs = SoIndexedFaceSet()
        idx = geo.coord_index()
        fs.coordIndex.setValues(0, len(idx), idx)
        sep.addChild(fs)
        return sep

    def to_freecad_mesh(self, geometry=None):
        """Build a ``Mesh.Mesh`` from the swept geometry."""
        try:
            import Mesh
        except ImportError as exc:
            raise RuntimeError(
                "FreeCAD's Mesh module is unavailable; a 3D stroke cannot be "
                "converted to document geometry outside FreeCAD") from exc
        geo = geometry or self.build_geometry()
        facets = []
        for a, b, c in geo.triangles():
            facets.append([geo.vertices[a], geo.vertices[b], geo.vertices[c]])
        return Mesh.Mesh(facets)

    def to_part_shape(self, geometry=None, solid=False, tolerance=1e-6):
        """Build a ``Part`` shell (optionally a solid) from the geometry."""
        try:
            import Part
        except ImportError as exc:
            raise RuntimeError(
                "FreeCAD's Part module is unavailable; a 3D stroke cannot be "
                "converted to a shape outside FreeCAD") from exc
        geo = geometry or self.build_geometry()
        faces = []
        for f in geo.faces:
            pts = [geo.vertices[i] for i in f]
            # drop repeated consecutive points, they break makePolygon
            uniq = []
            for p in pts:
                if not uniq or _dist(uniq[-1], p) > tolerance:
                    uniq.append(p)
            if len(uniq) < 3:
                continue
            try:
                wire = Part.makePolygon([Part.Vector(*p) for p in uniq]
                                        + [Part.Vector(*uniq[0])])
                faces.append(Part.Face(wire))
            except Exception:
                # non planar quad: split it into two triangles
                for i in range(1, len(uniq) - 1):
                    tri = [uniq[0], uniq[i], uniq[i + 1], uniq[0]]
                    try:
                        wire = Part.makePolygon(
                            [Part.Vector(*p) for p in tri])
                        faces.append(Part.Face(wire))
                    except Exception:
                        pass
        if not faces:
            return None
        shell = Part.makeShell(faces)
        if solid:
            try:
                return Part.makeSolid(shell)
            except Exception:
                return shell
        return shell


# --------------------------------------------------------------------------
# a set of strokes
# --------------------------------------------------------------------------

class StrokeSet(object):
    """The ``strokes3d`` array of the §4 paint manifest."""

    def __init__(self, strokes=None):
        self.strokes = list(strokes or [])

    def __len__(self):
        return len(self.strokes)

    def __iter__(self):
        return iter(self.strokes)

    def __getitem__(self, i):
        return self.strokes[i]

    def add(self, stroke):
        self.strokes.append(stroke)
        return stroke

    def remove(self, stroke):
        if stroke in self.strokes:
            self.strokes.remove(stroke)

    def clear(self):
        self.strokes = []

    def to_list(self):
        return [s.to_dict() for s in self.strokes]

    @classmethod
    def from_list(cls, data):
        return cls([Stroke3D.from_dict(d) for d in (data or [])])

    def to_coin(self):
        from pivy.coin import SoSeparator
        sep = SoSeparator()
        for s in self.strokes:
            sep.addChild(s.to_coin())
        return sep

    def __repr__(self):
        return "StrokeSet(%d strokes)" % (len(self.strokes),)
