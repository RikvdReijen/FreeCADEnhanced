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
"""Parametric primitives placed with both hands.

Hold the trigger, pull your hands apart and a box grows between them; let go
and it stays parametric — every field can still be edited afterwards
(:meth:`Primitive.set_param`), and the primitive only becomes ``Part``
geometry when it is committed (:mod:`xrsketch.to_freecad`).

The parameter names and the axis conventions are deliberately the ones already
defined in ARCHITECTURE.md §2, so a primitive's :meth:`Primitive.shape_dict`
*is* an environment-spec shape: ``cylinder``, ``cone``, ``sphere`` and
``torus`` are +Y aligned, ``plane`` lies in XY and grows along +Z.  That also
means the tessellation is not written a second time here — it goes straight
through :func:`xrenv.spec.tessellate_shape`, the same code the environments
and the Quest renderer use.
"""

import math

from . import vecmath as vm
from .vecmath import Transform

__all__ = [
    "DEFAULT_PARAMS",
    "PARAM_SPECS",
    "PRIMITIVE_KINDS",
    "PlacementSession",
    "Primitive",
]

PRIMITIVE_KINDS = ("box", "sphere", "cylinder", "cone", "torus", "plane",
                   "tube")

#: ``name -> (kind, minimum, maximum)`` where kind is ``"float"``/``"int"``/
#: ``"bool"``/``"vec"``/``"path"``.  Used for validation and for the in-VR
#: parameter dials.
PARAM_SPECS = {
    "size": ("vec", 1e-6, 1.0e6),
    "radius": ("float", 1e-6, 1.0e6),
    "top_radius": ("float", 0.0, 1.0e6),
    "tube_radius": ("float", 1e-6, 1.0e6),
    "height": ("float", 1e-6, 1.0e6),
    "sides": ("int", 3, 512),
    "rings": ("int", 2, 512),
    "sectors": ("int", 3, 512),
    "subdiv": ("vec", 1, 512),
    "caps": ("bool", 0, 1),
    "path": ("path", 0, 0),
}

DEFAULT_PARAMS = {
    "box": {"size": (0.1, 0.1, 0.1)},
    "sphere": {"radius": 0.05, "rings": 16, "sectors": 24},
    "cylinder": {"radius": 0.05, "height": 0.1, "sides": 24, "caps": True},
    "cone": {"radius": 0.05, "top_radius": 0.0, "height": 0.1, "sides": 24},
    "torus": {"radius": 0.08, "tube_radius": 0.02, "sides": 16, "rings": 24},
    "plane": {"size": (0.1, 0.1), "subdiv": (1, 1)},
    "tube": {"path": ((0.0, 0.0, 0.0), (0.0, 0.1, 0.0)), "radius": 0.01,
             "sides": 12},
}

#: the local axis a primitive grows along (ARCHITECTURE.md §2)
AXIS = (0.0, 1.0, 0.0)

_MIN = 1e-6


class Primitive(object):
    """One parametric primitive: a kind, its parameters and a placement."""

    _next_id = [0]

    def __init__(self, kind, params=None, transform=None, name=None):
        kind = str(kind).lower()
        if kind not in PRIMITIVE_KINDS:
            raise ValueError("unknown primitive: %r" % (kind,))
        self.kind = kind
        self.params = dict(DEFAULT_PARAMS[kind])
        if params:
            for k, v in params.items():
                self.set_param(k, v)
        self.transform = transform.copy() if isinstance(transform, Transform) \
            else Transform()
        Primitive._next_id[0] += 1
        self.id = "p%d" % Primitive._next_id[0]
        self.name = name or ("%s%d" % (kind.capitalize(),
                                       Primitive._next_id[0]))

    # -- parameters ------------------------------------------------------
    def set_param(self, name, value):
        """Set one parameter, validated and clamped to its range."""
        if name not in self.params:
            raise KeyError("%s has no parameter %r" % (self.kind, name))
        spec = PARAM_SPECS.get(name)
        if spec is None:
            self.params[name] = value
            return value
        kind, lo, hi = spec
        if kind == "float":
            v = float(value)
            if not math.isfinite(v):
                raise ValueError("%s must be finite" % name)
            v = vm.clamp(v, lo, hi)
        elif kind == "int":
            v = int(vm.clamp(int(value), lo, hi))
        elif kind == "bool":
            v = bool(value)
        elif kind == "vec":
            n = len(self.params[name])
            if len(value) != n:
                raise ValueError("%s takes %d components" % (name, n))
            v = tuple(vm.clamp(float(c), lo, hi) for c in value)
        elif kind == "path":
            pts = [vm.vec3(p) for p in value]
            if len(pts) < 2:
                raise ValueError("a tube path needs at least two points")
            v = tuple(pts)
        else:                                    # pragma: no cover
            v = value
        self.params[name] = v
        return v

    def get_param(self, name):
        return self.params[name]

    def update(self, **kw):
        for k, v in kw.items():
            self.set_param(k, v)
        return self

    # -- placement -------------------------------------------------------
    @classmethod
    def from_two_points(cls, kind, a, b, rotation=vm.IDENTITY_QUAT,
                        thickness=0.25, name=None):
        """Define a primitive by the extent between the two hands.

        The two points are the diagonal of the primitive's bounding volume:
        opposite corners for a box or a plane, the two poles for a sphere or a
        torus, and the two ends of the axis for a cylinder, a cone or a tube —
        which then take their radius from ``thickness`` times the length.
        """
        a = vm.vec3(a)
        b = vm.vec3(b)
        centre = vm.mul(vm.add(a, b), 0.5)
        diff = vm.sub(b, a)
        span = vm.length(diff)
        if span < _MIN:
            raise ValueError("the hands are too close together to place a "
                             "primitive")
        params = {}
        transform = Transform(centre, rotation, 1.0)
        if kind == "box":
            params["size"] = tuple(max(_MIN, abs(c)) for c in diff)
        elif kind == "sphere":
            params["radius"] = span * 0.5
        elif kind == "plane":
            params["size"] = (max(_MIN, abs(diff[0])), max(_MIN, abs(diff[1])))
        elif kind == "torus":
            params["radius"] = span * 0.5
            params["tube_radius"] = max(_MIN, span * 0.5 * thickness)
        elif kind in ("cylinder", "cone"):
            params["height"] = span
            params["radius"] = max(_MIN, span * thickness)
            if kind == "cone":
                params["top_radius"] = 0.0
            transform = Transform(centre, _axis_rotation(diff, rotation), 1.0)
        elif kind == "tube":
            q = _axis_rotation(diff, rotation)
            qi = vm.quat_conjugate(q)
            local = [vm.quat_rotate(qi, vm.sub(p, centre)) for p in (a, b)]
            params["path"] = tuple(local)
            params["radius"] = max(_MIN, span * thickness * 0.5)
            transform = Transform(centre, q, 1.0)
        else:                                    # pragma: no cover
            raise ValueError("unknown primitive: %r" % (kind,))
        return cls(kind, params, transform, name)

    def fit_two_points(self, a, b, thickness=0.25):
        """Re-place an existing primitive between two points, in place."""
        fresh = Primitive.from_two_points(self.kind, a, b, thickness=thickness)
        for k, v in fresh.params.items():
            self.params[k] = v
        self.transform = fresh.transform
        return self

    # -- geometry --------------------------------------------------------
    def shape_dict(self):
        """The ARCHITECTURE.md §2 shape dictionary for this primitive."""
        p = self.params
        if self.kind == "box":
            return {"type": "box", "size": list(p["size"])}
        if self.kind == "sphere":
            return {"type": "sphere", "radius": p["radius"],
                    "rings": p["rings"], "sectors": p["sectors"]}
        if self.kind == "cylinder":
            return {"type": "cylinder", "radius": p["radius"],
                    "height": p["height"], "sides": p["sides"],
                    "caps": p["caps"]}
        if self.kind == "cone":
            return {"type": "cone", "radius": p["radius"],
                    "top_radius": p["top_radius"], "height": p["height"],
                    "sides": p["sides"]}
        if self.kind == "torus":
            return {"type": "torus", "radius": p["radius"],
                    "tube_radius": p["tube_radius"], "sides": p["sides"],
                    "rings": p["rings"]}
        if self.kind == "plane":
            return {"type": "plane", "size": list(p["size"]),
                    "subdiv": [int(p["subdiv"][0]), int(p["subdiv"][1])]}
        return {"type": "tube", "path": [list(q) for q in p["path"]],
                "radius": p["radius"], "sides": p["sides"]}

    def mesh(self, world=True):
        """``(positions, normals, uvs, indices)`` via ``xrenv.spec``.

        Positions are flat triples.  With ``world`` they are pushed through
        the primitive's placement transform.
        """
        from xrenv import spec as _spec
        positions, normals, uvs, indices = _spec.tessellate_shape(
            self.shape_dict())
        if not world:
            return (positions, normals, uvs, indices)
        t = self.transform
        out = []
        for i in range(0, len(positions), 3):
            out.extend(t.apply(positions[i:i + 3]))
        nrm = []
        for i in range(0, len(normals), 3):
            nrm.extend(vm.quat_rotate(t.rotation, normals[i:i + 3]))
        return (out, nrm, uvs, indices)

    def bounds(self, world=True):
        positions, _n, _uv, _i = self.mesh(world)
        if not positions:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = [min(positions[i::3]) for i in range(3)]
        hi = [max(positions[i::3]) for i in range(3)]
        return (tuple(lo), tuple(hi))

    def to_cage(self):
        """A control cage for the primitive, where one makes sense.

        Only ``box`` and ``plane`` have an obvious quad cage; the rest raise,
        because a subdivision cage of a sphere or a torus is a modelling
        decision (how many rings? which pole treatment?) rather than a
        conversion.
        """
        from .subd import cube_cage, grid_cage
        if self.kind == "box":
            sx, sy, sz = self.params["size"]
            cage = cube_cage(1.0)
            cage.vertices = [self.transform.apply(
                (v[0] * sx, v[1] * sy, v[2] * sz)) for v in cage.vertices]
            cage.invalidate()
            return cage
        if self.kind == "plane":
            sx, sy = self.params["size"]
            nu, nv = self.params["subdiv"]
            cage = grid_cage(int(nu), int(nv), (sx, sy),
                             (-0.5 * sx, -0.5 * sy, 0.0))
            cage.vertices = [self.transform.apply(v) for v in cage.vertices]
            cage.invalidate()
            return cage
        raise ValueError("%s has no canonical control cage; place a cage "
                         "explicitly instead" % self.kind)

    # -- serialisation ---------------------------------------------------
    def copy(self):
        p = Primitive(self.kind, None, self.transform, self.name)
        p.params = dict(self.params)
        return p

    def to_dict(self):
        params = {}
        for k, v in self.params.items():
            if isinstance(v, tuple):
                params[k] = [list(c) if isinstance(c, tuple) else c
                             for c in v]
            else:
                params[k] = v
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "params": params, "transform": self.transform.to_dict()}

    @classmethod
    def from_dict(cls, d):
        p = cls(d["kind"], d.get("params"),
                Transform.from_dict(d.get("transform")), d.get("name"))
        if d.get("id"):
            p.id = d["id"]
        return p

    def __repr__(self):
        return "Primitive(%r, %r)" % (self.kind, self.name)


def _axis_rotation(direction, base=vm.IDENTITY_QUAT):
    """Rotation carrying the local +Y axis onto ``direction``."""
    d = vm.normalize(direction)
    if vm.length(d) < 0.5:
        return base
    a = vm.quat_rotate(base, AXIS)
    dot = vm.clamp(vm.dot(a, d), -1.0, 1.0)
    if dot > 1.0 - 1e-12:
        return base
    if dot < -1.0 + 1e-12:
        return vm.quat_mul(vm.quat_from_axis_angle(vm.any_perp(a), math.pi),
                           base)
    axis = vm.cross(a, d)
    return vm.quat_mul(vm.quat_from_axis_angle(axis, math.acos(dot)), base)


class PlacementSession(object):
    """Two-handed placement: one hand anchors, the other sets the extent.

    ``begin`` with the first hand, feed the second hand to ``update`` every
    frame — the primitive exists and is live from the first update — and
    ``commit`` when the trigger is released.
    """

    def __init__(self, kind="box", thickness=0.25):
        self.kind = kind
        self.thickness = float(thickness)
        self.anchor = None
        self.primitive = None

    @property
    def active(self):
        return self.anchor is not None

    def set_kind(self, kind):
        kind = str(kind).lower()
        if kind not in PRIMITIVE_KINDS:
            raise ValueError("unknown primitive: %r" % (kind,))
        self.kind = kind
        return kind

    def begin(self, point):
        self.anchor = vm.vec3(point)
        self.primitive = None
        return self.anchor

    def update(self, point):
        """Grow the primitive to the second hand; returns it, or ``None``."""
        if self.anchor is None:
            return None
        try:
            if self.primitive is None:
                self.primitive = Primitive.from_two_points(
                    self.kind, self.anchor, point, thickness=self.thickness)
            else:
                self.primitive.fit_two_points(self.anchor, point,
                                              self.thickness)
        except ValueError:
            return None
        return self.primitive

    def commit(self):
        prim = self.primitive
        self.anchor = None
        self.primitive = None
        return prim

    def cancel(self):
        self.anchor = None
        self.primitive = None
        return None
