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
"""Reference material: blueprint image planes and a measuring tape.

Measuring under miniaturisation
-------------------------------
:mod:`xrenv.scale` shrinks the user by growing the world, so a length picked
off the controllers is **not** the length of the thing being measured.  The
chain is

    viewer units  --(/ world_scale / unit_scale)-->  environment metres
                  --(/ model_scale)-->              document units (mm)

:class:`MeasureTool` walks that chain in :meth:`MeasureTool.to_model`, so a
user standing 12x shrunk on a build plate, whose hands are 1.2 m apart in the
headset, reads **100 mm** — the true size of the part — and not 1200 mm.
Measurements store document units, so the readout does not change when the
user grows or shrinks between taking the measurement and reading it.
"""

import math

from . import vecmath as vm
from .bimanual import view_to_env

__all__ = [
    "ImagePlane",
    "MeasureTool",
    "Measurement",
    "format_angle",
    "format_length",
]

MEASURE_KINDS = ("distance", "angle", "polyline")


def format_length(value, unit="mm", decimals=2):
    """Human readable length; millimetres roll over into metres at 1000."""
    v = float(value)
    if unit == "mm" and abs(v) >= 1000.0:
        return "%.*f m" % (decimals + 1, v / 1000.0)
    return "%.*f %s" % (decimals, v, unit)


def format_angle(radians, decimals=1):
    return "%.*f°" % (decimals, math.degrees(float(radians)))


# --------------------------------------------------------------------------
# image planes
# --------------------------------------------------------------------------

class ImagePlane(object):
    """A blueprint or backdrop image hung in space.

    The plane is a rectangle of ``size`` metres in its own XY plane with +Z as
    its normal — the same convention as the ``plane`` primitive and the
    environment anchors (ARCHITECTURE.md §2).  ``source`` is whatever the host
    needs to find the pixels (a path, a URL, an FCXR image index); nothing here
    decodes it, which is what keeps this module free of the raster code.
    """

    _next_id = [0]

    def __init__(self, source=None, size=(1.0, 1.0), origin=(0.0, 0.0, 0.0),
                 rotation=vm.IDENTITY_QUAT, opacity=0.5, locked=True,
                 visible=True, name=None, resolution=None):
        ImagePlane._next_id[0] += 1
        self.id = "img%d" % ImagePlane._next_id[0]
        self.source = source
        self.size = (max(1e-9, float(size[0])), max(1e-9, float(size[1])))
        self.origin = vm.vec3(origin)
        self.rotation = vm.quat_normalize(rotation)
        self.opacity = vm.clamp(float(opacity), 0.0, 1.0)
        #: reference images default to locked — you line them up once and then
        #: draw over them without knocking them out of place
        self.locked = bool(locked)
        self.visible = bool(visible)
        self.name = name or self.id
        self.resolution = None if resolution is None else (int(resolution[0]),
                                                           int(resolution[1]))

    # -- geometry --------------------------------------------------------
    def normal(self):
        return vm.quat_rotate(self.rotation, (0.0, 0.0, 1.0))

    def axes(self):
        return (vm.quat_rotate(self.rotation, (1.0, 0.0, 0.0)),
                vm.quat_rotate(self.rotation, (0.0, 1.0, 0.0)))

    def corners(self):
        """The four corners, counter-clockwise from the lower left."""
        u, v = self.axes()
        hw = vm.mul(u, self.size[0] * 0.5)
        hh = vm.mul(v, self.size[1] * 0.5)
        o = self.origin
        return [vm.sub(vm.sub(o, hw), hh), vm.add(vm.sub(o, hh), hw),
                vm.add(vm.add(o, hw), hh), vm.sub(vm.add(o, hh), hw)]

    def bounds(self):
        pts = self.corners()
        return (tuple(min(p[i] for p in pts) for i in range(3)),
                tuple(max(p[i] for p in pts) for i in range(3)))

    def uv_at(self, point):
        """UV of a point projected onto the plane; outside is outside [0,1]."""
        u, v = self.axes()
        d = vm.sub(vm.vec3(point), self.origin)
        return (0.5 + vm.dot(d, u) / self.size[0],
                0.5 + vm.dot(d, v) / self.size[1])

    def point_at(self, u, v):
        au, av = self.axes()
        return vm.add(self.origin,
                      vm.add(vm.mul(au, (float(u) - 0.5) * self.size[0]),
                             vm.mul(av, (float(v) - 0.5) * self.size[1])))

    def contains(self, point, tolerance=1e-6):
        d = vm.dot(vm.sub(point, self.origin), self.normal())
        if abs(d) > tolerance:
            return False
        u, v = self.uv_at(point)
        return -1e-9 <= u <= 1.0 + 1e-9 and -1e-9 <= v <= 1.0 + 1e-9

    # -- editing ---------------------------------------------------------
    def set_opacity(self, value):
        self.opacity = vm.clamp(float(value), 0.0, 1.0)
        return self.opacity

    def set_locked(self, value):
        self.locked = bool(value)
        return self.locked

    def fit_to(self, width=None, aspect=None):
        """Resize keeping the aspect ratio of the source pixels."""
        if aspect is None:
            if not self.resolution or not self.resolution[0]:
                aspect = self.size[1] / self.size[0]
            else:
                aspect = self.resolution[1] / float(self.resolution[0])
        w = float(width if width is not None else self.size[0])
        self.size = (max(1e-9, w), max(1e-9, w * float(aspect)))
        return self.size

    def move(self, delta):
        if self.locked:
            return False
        self.origin = vm.add(self.origin, vm.vec3(delta))
        return True

    # -- serialisation ---------------------------------------------------
    def copy(self):
        return ImagePlane.from_dict(self.to_dict())

    def to_dict(self):
        return {"id": self.id, "name": self.name, "source": self.source,
                "size": list(self.size), "origin": list(self.origin),
                "rotation": list(self.rotation), "opacity": self.opacity,
                "locked": self.locked, "visible": self.visible,
                "resolution": (None if self.resolution is None
                               else list(self.resolution))}

    @classmethod
    def from_dict(cls, d):
        img = cls(d.get("source"), d.get("size", (1.0, 1.0)),
                  d.get("origin", (0.0, 0.0, 0.0)),
                  d.get("rotation", vm.IDENTITY_QUAT),
                  d.get("opacity", 0.5), d.get("locked", True),
                  d.get("visible", True), d.get("name"),
                  d.get("resolution"))
        if d.get("id"):
            img.id = d["id"]
        return img

    def __repr__(self):
        return "ImagePlane(%r, %.3gx%.3g m)" % (self.name, self.size[0],
                                                self.size[1])


# --------------------------------------------------------------------------
# measurements
# --------------------------------------------------------------------------

class Measurement(object):
    """A measurement whose points are stored in **document units**.

    ``kind`` is ``distance`` (two points), ``angle`` (three points, measured at
    the middle one) or ``polyline`` (any number, with a running total).
    """

    _next_id = [0]

    def __init__(self, kind="distance", points=None, unit="mm", name=None):
        kind = str(kind).lower()
        if kind not in MEASURE_KINDS:
            raise ValueError("unknown measurement: %r" % (kind,))
        Measurement._next_id[0] += 1
        self.id = "m%d" % Measurement._next_id[0]
        self.kind = kind
        self.points = [vm.vec3(p) for p in (points or [])]
        self.unit = unit
        self.name = name or self.id

    # -- building --------------------------------------------------------
    def add_point(self, point):
        self.points.append(vm.vec3(point))
        return self.points[-1]

    def pop_point(self):
        return self.points.pop() if self.points else None

    def clear(self):
        self.points = []
        return self

    @property
    def complete(self):
        need = {"distance": 2, "angle": 3, "polyline": 2}[self.kind]
        return len(self.points) >= need

    # -- values ----------------------------------------------------------
    def segments(self):
        return [vm.dist(self.points[i - 1], self.points[i])
                for i in range(1, len(self.points))]

    def running_total(self):
        """Cumulative length at each point, starting at 0."""
        out = [0.0]
        for seg in self.segments():
            out.append(out[-1] + seg)
        return out

    def total_length(self):
        return sum(self.segments())

    def angle(self):
        """The angle at the middle point, in radians."""
        if len(self.points) < 3:
            raise ValueError("an angle needs three points")
        a = vm.sub(self.points[0], self.points[1])
        b = vm.sub(self.points[2], self.points[1])
        if vm.length(a) < 1e-12 or vm.length(b) < 1e-12:
            raise ValueError("the angle is degenerate: two points coincide")
        return vm.angle_between(a, b)

    def value(self):
        """The headline number: document units, or radians for an angle."""
        if not self.complete:
            raise ValueError("the measurement is not complete yet")
        if self.kind == "angle":
            return self.angle()
        if self.kind == "distance":
            return vm.dist(self.points[0], self.points[1])
        return self.total_length()

    def text(self, decimals=2):
        if not self.complete:
            return "—"
        if self.kind == "angle":
            return format_angle(self.angle(), max(0, decimals - 1))
        return format_length(self.value(), self.unit, decimals)

    def labels(self, decimals=2):
        """Per-segment labels plus the running total, for the in-VR readout."""
        if self.kind == "angle":
            return [self.text(decimals)]
        totals = self.running_total()
        out = []
        for i, seg in enumerate(self.segments()):
            out.append("%s (%s)" % (format_length(seg, self.unit, decimals),
                                    format_length(totals[i + 1], self.unit,
                                                  decimals)))
        return out

    def bounds(self):
        if not self.points:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        return (tuple(min(p[i] for p in self.points) for i in range(3)),
                tuple(max(p[i] for p in self.points) for i in range(3)))

    # -- serialisation ---------------------------------------------------
    def copy(self):
        return Measurement.from_dict(self.to_dict())

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "unit": self.unit, "points": [list(p) for p in self.points]}

    @classmethod
    def from_dict(cls, d):
        m = cls(d.get("kind", "distance"), d.get("points"),
                d.get("unit", "mm"), d.get("name"))
        if d.get("id"):
            m.id = d["id"]
        return m

    def __repr__(self):
        return "Measurement(%s, %d points)" % (self.kind, len(self.points))


class MeasureTool(object):
    """Turns controller positions into measurements in document units.

    ``scale_controller`` is an :class:`xrenv.scale.ScaleController` (or
    anything with ``world_scale``, ``world_offset`` and ``unit_scale``);
    ``model_scale`` is environment metres per document unit, i.e. the ``scale``
    of the :class:`~xrenv.scale.FitTransform` the document was placed with —
    0.001 for a millimetre document sitting in the world at 1:1.
    """

    def __init__(self, scale_controller=None, model_scale=0.001, unit="mm"):
        self.scale = scale_controller
        self.model_scale = float(model_scale)
        if self.model_scale <= 0.0:
            raise ValueError("model_scale must be positive")
        self.unit = unit
        self.current = None
        self.finished = []

    # -- conversions -----------------------------------------------------
    def to_model(self, view_point):
        """Viewer-space point -> document units."""
        if self.scale is None:
            env = vm.vec3(view_point)
        else:
            env = view_to_env(self.scale, vm.vec3(view_point))
        return vm.mul(env, 1.0 / self.model_scale)

    def to_view(self, model_point):
        env = vm.mul(vm.vec3(model_point), self.model_scale)
        if self.scale is None:
            return env
        s = self.scale.world_scale * self.scale.unit_scale
        o = self.scale.world_offset
        return (env[0] * s + o[0], env[1] * s + o[1], env[2] * s + o[2])

    def model_length(self, view_length):
        """A length measured in viewer units, in document units."""
        if self.scale is None:
            return float(view_length) / self.model_scale
        s = self.scale.world_scale * self.scale.unit_scale
        return float(view_length) / (s * self.model_scale)

    # -- tape ------------------------------------------------------------
    def begin(self, kind="distance", view_point=None):
        self.current = Measurement(kind, unit=self.unit)
        if view_point is not None:
            self.add(view_point)
        return self.current

    def add(self, view_point, model_point=None):
        """Add a point, given in viewer space (or already in model space)."""
        if self.current is None:
            self.begin()
        p = (self.to_model(view_point) if model_point is None
             else vm.vec3(model_point))
        self.current.add_point(p)
        return p

    def undo_point(self):
        return self.current.pop_point() if self.current is not None else None

    def commit(self):
        m = self.current
        self.current = None
        if m is None or not m.complete:
            return None
        self.finished.append(m)
        return m

    def cancel(self):
        self.current = None
        return None

    def clear(self):
        self.finished = []
        self.current = None

    def readout(self, decimals=2):
        """What the label above the tape should say right now."""
        if self.current is None or not self.current.complete:
            return "—"
        return self.current.text(decimals)

    def __repr__(self):
        return "MeasureTool(%d finished)" % (len(self.finished),)
