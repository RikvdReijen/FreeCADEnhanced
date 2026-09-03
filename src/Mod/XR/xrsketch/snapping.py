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
"""Snapping, shared by every sketch tool.

Vocabulary follows :class:`xrpaint.vector.SnapEngine`: a
:class:`SnapSettings` says what is armed, :meth:`SnapEngine.snap` returns a
:class:`SnapResult` whose ``kind`` is ``None`` when nothing was in range, and
``ORDER`` fixes the priority.  This one is three dimensional and adds face
centres, curve endpoints and symmetry planes.

The snap radius
---------------
``SnapSettings.radius`` is expressed in **metres of hand travel at 1:1**, not
in model units, which is what makes it independent of how the model is being
viewed.  When the user is miniaturised by ``user_scale`` (see
:mod:`xrenv.scale`) the world is drawn ``user_scale`` times larger, so one
centimetre of hand movement covers ``1/user_scale`` centimetres of the model —
and the radius in model units is ``radius / user_scale``.  A user shrunk 12x to
stand on a build plate therefore snaps 12x more finely, which is exactly what
walking up to the detail is for.

Everything in this module is a pure function of its arguments.
"""

import math

from . import vecmath as vm

__all__ = [
    "SnapEngine",
    "SnapResult",
    "SnapSettings",
    "SnapTargets",
    "snap_angle",
    "snap_to_grid",
    "snap_to_plane",
]


def snap_to_grid(point, size):
    """Nearest point of an axis aligned grid of pitch ``size``."""
    if size is None or size <= 0.0:
        return vm.vec3(point)
    return tuple(round(float(c) / size) * size for c in point[:3])


def snap_angle(point, origin, step, plane_normal=None):
    """Rotate ``point`` about ``origin`` onto the nearest angular increment.

    The angle is measured inside the plane through ``origin`` with normal
    ``plane_normal``; the component along the normal is preserved.  When no
    normal is given the plane that best contains the direction is used, i.e.
    the principal axis with the smallest component.  Returns ``None`` when the
    direction is degenerate or ``step`` is not positive.
    """
    if step is None or step <= 0.0:
        return None
    p = vm.vec3(point)
    o = vm.vec3(origin)
    v = vm.sub(p, o)
    if vm.length(v) < 1e-12:
        return None
    if plane_normal is None:
        axis = min(range(3), key=lambda i: abs(v[i]))
        n = tuple(1.0 if i == axis else 0.0 for i in range(3))
    else:
        n = vm.normalize(plane_normal)
        if vm.length(n) < 0.5:
            return None
    out_of_plane = vm.dot(v, n)
    planar = vm.sub(v, vm.mul(n, out_of_plane))
    r = vm.length(planar)
    if r < 1e-12:
        return None
    u, w = _plane_axes(n)
    a = math.atan2(vm.dot(planar, w), vm.dot(planar, u))
    snapped = round(a / step) * step
    new_planar = vm.add(vm.mul(u, math.cos(snapped) * r),
                        vm.mul(w, math.sin(snapped) * r))
    return vm.add(o, vm.add(new_planar, vm.mul(n, out_of_plane)))


def _plane_axes(normal):
    u = vm.any_perp(normal)
    w = vm.cross(normal, u)
    return (u, vm.normalize(w))


def snap_to_plane(point, origin, normal):
    """Perpendicular projection of ``point`` onto a plane."""
    n = vm.normalize(normal)
    if vm.length(n) < 0.5:
        return vm.vec3(point)
    d = vm.dot(vm.sub(point, origin), n)
    return vm.sub(point, vm.mul(n, d))


class SnapSettings(object):
    """Which snaps are armed and how strong they are."""

    __slots__ = ("enabled", "grid", "grid_size", "vertex", "midpoint",
                 "face_center", "curve_end", "tangent", "angle", "angle_step",
                 "symmetry", "radius")

    def __init__(self, enabled=True, grid=True, grid_size=0.01, vertex=True,
                 midpoint=True, face_center=True, curve_end=True,
                 tangent=True, angle=True, angle_step=math.pi / 12.0,
                 symmetry=True, radius=0.02):
        self.enabled = bool(enabled)
        self.grid = bool(grid)
        self.grid_size = float(grid_size)
        self.vertex = bool(vertex)
        self.midpoint = bool(midpoint)
        self.face_center = bool(face_center)
        self.curve_end = bool(curve_end)
        self.tangent = bool(tangent)
        self.angle = bool(angle)
        self.angle_step = float(angle_step)
        self.symmetry = bool(symmetry)
        self.radius = float(radius)

    def effective_radius(self, user_scale=1.0):
        """The radius in model units for a user shrunk by ``user_scale``."""
        try:
            s = float(user_scale)
        except (TypeError, ValueError):
            s = 1.0
        if not math.isfinite(s) or s <= 0.0:
            s = 1.0
        return self.radius / s

    def copy(self):
        s = SnapSettings()
        for name in self.__slots__:
            setattr(s, name, getattr(self, name))
        return s

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)


class SnapResult(object):
    """The outcome of a snap query; ``kind is None`` means "no snap"."""

    __slots__ = ("point", "kind", "target", "index", "distance")

    def __init__(self, point, kind=None, target=None, index=None,
                 distance=0.0):
        self.point = vm.vec3(point)
        self.kind = kind
        self.target = target
        self.index = index
        self.distance = float(distance)

    @property
    def snapped(self):
        return self.kind is not None

    def __iter__(self):
        return iter(self.point)

    def __repr__(self):
        return "SnapResult(%s, %s)" % (
            tuple(round(c, 5) for c in self.point), self.kind)


class SnapTargets(object):
    """Geometry a snap query may latch onto.

    Populated by the tools (or by :meth:`from_objects`) rather than by
    reaching into the scene from here, which keeps this module free of any
    dependency on the rest of ``xrsketch``.
    """

    __slots__ = ("vertices", "edges", "faces", "curve_ends", "tangents",
                 "symmetry_planes")

    def __init__(self):
        self.vertices = []          # (point, owner)
        self.edges = []             # (a, b, owner)
        self.faces = []             # (list_of_points, owner)
        self.curve_ends = []        # (point, owner)
        self.tangents = []          # (point, direction, owner)
        self.symmetry_planes = []   # (origin, normal, owner)

    # -- population -------------------------------------------------------
    def add_vertex(self, point, owner=None):
        self.vertices.append((vm.vec3(point), owner))
        return self

    def add_edge(self, a, b, owner=None):
        self.edges.append((vm.vec3(a), vm.vec3(b), owner))
        return self

    def add_face(self, points, owner=None):
        pts = [vm.vec3(p) for p in points]
        if len(pts) >= 3:
            self.faces.append((pts, owner))
        return self

    def add_curve_end(self, point, tangent=None, owner=None):
        self.curve_ends.append((vm.vec3(point), owner))
        if tangent is not None and vm.length(tangent) > 1e-12:
            self.tangents.append((vm.vec3(point), vm.normalize(tangent),
                                  owner))
        return self

    def add_symmetry_plane(self, origin, normal, owner=None):
        n = vm.normalize(normal)
        if vm.length(n) > 0.5:
            self.symmetry_planes.append((vm.vec3(origin), n, owner))
        return self

    def add_cage(self, cage, owner=None):
        """Vertices, edges and faces of a :class:`xrsketch.subd.Cage`."""
        verts = list(getattr(cage, "vertices", []) or [])
        for v in verts:
            self.add_vertex(v, owner)
        for face in getattr(cage, "faces", []) or []:
            pts = [verts[i] for i in face]
            self.add_face(pts, owner)
            for k in range(len(face)):
                a = verts[face[k]]
                b = verts[face[(k + 1) % len(face)]]
                self.add_edge(a, b, owner)
        return self

    def add_curve(self, curve, owner=None):
        """Endpoints, control points and end tangents of a curve."""
        points = getattr(curve, "points", None)
        if points is None:
            return self
        for cp in points:
            self.add_vertex(getattr(cp, "position", cp), owner)
        if not points or getattr(curve, "closed", False):
            return self
        try:
            self.add_curve_end(points[0].position, curve.tangent_at(0, 0.0),
                               owner)
            self.add_curve_end(points[-1].position,
                               curve.tangent_at(len(points) - 2, 1.0), owner)
        except Exception:
            self.add_curve_end(points[0].position, None, owner)
            self.add_curve_end(points[-1].position, None, owner)
        return self

    def add_objects(self, objects):
        for obj in objects or []:
            data = getattr(obj, "data", obj)
            if hasattr(data, "faces") and hasattr(data, "vertices"):
                self.add_cage(data, obj)
            elif hasattr(data, "points"):
                self.add_curve(data, obj)
            elif hasattr(data, "corners"):
                try:
                    self.add_face(data.corners(), obj)
                except Exception:
                    pass
        return self

    @classmethod
    def from_objects(cls, objects):
        return cls().add_objects(objects)

    def __repr__(self):
        return ("SnapTargets(%d verts, %d edges, %d faces, %d ends)"
                % (len(self.vertices), len(self.edges), len(self.faces),
                   len(self.curve_ends)))


class SnapEngine(object):
    """Grid / vertex / midpoint / face / curve / tangent / angle / symmetry.

    ``snap()`` collects every candidate inside the effective radius and
    returns the best one in :attr:`ORDER` priority, breaking ties by distance.
    """

    #: priority, strongest first
    ORDER = ("vertex", "curve_end", "midpoint", "face_center", "tangent",
             "symmetry", "angle", "grid")

    def __init__(self, settings=None):
        self.settings = settings or SnapSettings()

    def snap(self, point, targets=None, origin=None, user_scale=1.0,
             exclude=None, plane_normal=None):
        """Snap ``point``; returns a :class:`SnapResult`.

        ``origin`` is the anchor an angle snap measures from (the previous
        point of the curve being drawn, usually); without it angle snapping is
        skipped.  ``exclude`` drops candidates whose owner is that object.
        """
        s = self.settings
        p = vm.vec3(point)
        if not s.enabled:
            return SnapResult(p)
        radius = s.effective_radius(user_scale)
        if radius <= 0.0:
            return SnapResult(p)
        cands = []

        def offer(kind, pt, owner=None, index=None):
            d = vm.dist(pt, p)
            if d <= radius:
                cands.append((kind, vm.vec3(pt), d, owner, index))

        if targets is not None:
            if s.vertex:
                for i, (v, owner) in enumerate(targets.vertices):
                    if owner is not None and owner is exclude:
                        continue
                    offer("vertex", v, owner, i)
            if s.curve_end:
                for i, (v, owner) in enumerate(targets.curve_ends):
                    if owner is not None and owner is exclude:
                        continue
                    offer("curve_end", v, owner, i)
            if s.midpoint:
                for i, (a, b, owner) in enumerate(targets.edges):
                    if owner is not None and owner is exclude:
                        continue
                    offer("midpoint", vm.mul(vm.add(a, b), 0.5), owner, i)
            if s.face_center:
                for i, (pts, owner) in enumerate(targets.faces):
                    if owner is not None and owner is exclude:
                        continue
                    offer("face_center", _centroid(pts), owner, i)
            if s.tangent:
                for i, (base, direction, owner) in enumerate(targets.tangents):
                    if owner is not None and owner is exclude:
                        continue
                    t = vm.dot(vm.sub(p, base), direction)
                    if t <= 0.0:
                        continue
                    offer("tangent", vm.add(base, vm.mul(direction, t)),
                          owner, i)
            if s.symmetry:
                for i, (o, n, owner) in enumerate(targets.symmetry_planes):
                    if owner is not None and owner is exclude:
                        continue
                    offer("symmetry", snap_to_plane(p, o, n), owner, i)
        if s.angle and origin is not None:
            pt = snap_angle(p, origin, s.angle_step, plane_normal)
            if pt is not None:
                offer("angle", pt)
        if s.grid and s.grid_size > 0.0:
            offer("grid", snap_to_grid(p, s.grid_size))
        if not cands:
            return SnapResult(p)
        rank = dict((k, i) for i, k in enumerate(self.ORDER))
        cands.sort(key=lambda c: (rank.get(c[0], 99), c[2]))
        kind, pt, d, owner, index = cands[0]
        return SnapResult(pt, kind, owner, index, d)

    def __repr__(self):
        return "SnapEngine(radius=%.4g)" % (self.settings.radius,)


def _centroid(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n)
