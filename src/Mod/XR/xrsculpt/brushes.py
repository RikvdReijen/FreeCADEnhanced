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
"""Sculpt brushes.

Pure maths over :class:`xrsculpt.mesh.SculptMesh`; no Coin, no FreeCAD, no
numpy required.  The shape of this module deliberately mirrors
:mod:`xrpaint.brush`: parameters, a falloff table, a stroke resampler, and one
function that lays a stamp down.

============  ===============================================================
kind          what it does
============  ===============================================================
``draw``      offsets along the *stroke* normal -- the plain "add clay" brush
``inflate``   offsets along each vertex's own normal, so it swells
``clay``      flattens to a plane held slightly above the surface, then draws
              into it: the build-up brush
``flatten``   moves vertices onto the local plane, both ways
``scrape``    moves only the vertices *above* the plane down onto it
``pinch``     pulls vertices towards the stroke axis (negative strength pushes
              them apart, which is the contrast/magnify variant)
``smooth``    Laplacian relaxation towards the one-ring centroid, optionally
              only tangentially so the enclosed volume is preserved
``grab``      translates the whole falloff region by the controller delta
``snake_hook``like grab, but the region follows the tip and pinches behind it
``crease``    pinches towards the axis *and* pushes in along the normal
``erase``     reduces what is already stored in the active layer towards zero
============  ===============================================================

Units.  ``strength`` is dimensionless.  For the brushes that add material
(``draw``, ``inflate``, ``clay``, ``crease``) the displacement is
``strength * falloff * radius``, so a brush behaves the same on a 2 mm detail
and a 2 m body.  For the brushes that move towards a target (``flatten``,
``scrape``, ``pinch``, ``smooth``, ``erase``) ``strength * falloff`` is the
fraction of the way to that target, which keeps them unconditionally stable:
with ``strength <= 1`` no vertex can overshoot, so smoothing converges instead
of ringing.  ``grab`` and ``snake_hook`` move by ``falloff`` times the
controller delta.

Every brush writes into a :class:`xrsculpt.layers.SculptLayer` and consults a
:class:`xrsculpt.masking.VertexMask`; neither is required.
"""

import math

__all__ = [
    "BRUSH_KINDS",
    "FALLOFFS",
    "PRESETS",
    "PRESSURE_CURVES",
    "BrushParams",
    "Dab",
    "StrokeSampler",
    "apply_dab",
    "apply_pressure_curve",
    "falloff",
    "preset",
    "resample_stroke",
]

BRUSH_KINDS = (
    "draw",
    "inflate",
    "clay",
    "flatten",
    "scrape",
    "pinch",
    "smooth",
    "grab",
    "snake_hook",
    "crease",
    "erase",
)

PRESSURE_CURVES = ("linear", "soft", "hard", "square", "constant")

_EPS = 1e-12


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# --------------------------------------------------------------------------
# falloff curves
# --------------------------------------------------------------------------

def _f_smooth(t):
    s = 1.0 - t
    return s * s * (3.0 - 2.0 * s)


def _f_sphere(t):
    return math.sqrt(max(0.0, 1.0 - t * t))


def _f_root(t):
    return math.sqrt(max(0.0, 1.0 - t))


def _f_sharp(t):
    s = 1.0 - t
    return s * s


def _f_linear(t):
    return 1.0 - t


def _f_constant(t):
    return 1.0


#: ``name -> f(t)`` for ``t`` in ``[0, 1]``.  Every curve is bounded by
#: ``f(0) == 1`` and ``f(1) == 0`` (``constant`` steps to zero exactly at the
#: rim) and is monotonically non-increasing in between.
FALLOFFS = {
    "smooth": _f_smooth,
    "sphere": _f_sphere,
    "root": _f_root,
    "sharp": _f_sharp,
    "linear": _f_linear,
    "constant": _f_constant,
}

FALLOFF_NAMES = tuple(sorted(FALLOFFS))


def falloff(name, t):
    """Evaluate a named falloff curve at ``t = distance / radius``."""
    fn = FALLOFFS.get(name)
    if fn is None:
        raise ValueError("unknown falloff curve: %r" % (name,))
    if t <= 0.0:
        return 1.0
    if t >= 1.0:
        return 0.0
    return _clamp(fn(float(t)), 0.0, 1.0)


# --------------------------------------------------------------------------
# pressure
# --------------------------------------------------------------------------

def apply_pressure_curve(p, curve="linear"):
    """Map a raw trigger value 0..1 through a response curve."""
    p = _clamp(float(p), 0.0, 1.0)
    if isinstance(curve, (int, float)):
        gamma = float(curve)
        return p if gamma <= 0.0 else p ** gamma
    if curve == "linear" or curve is None:
        return p
    if curve == "soft":
        return p * p
    if curve == "hard":
        return math.sqrt(p)
    if curve == "square":
        return p * p * p
    if curve == "constant":
        return 1.0 if p > 0.0 else 0.0
    raise ValueError("unknown pressure curve: %r" % (curve,))


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------

class BrushParams(object):
    """Everything that describes a sculpt brush."""

    __slots__ = (
        "kind", "radius", "strength", "falloff", "spacing", "invert",
        "plane_offset", "pressure_curve", "size_pressure",
        "strength_pressure", "volume_preserving", "crease_pinch",
        "smooth_passes", "name",
    )

    def __init__(self, kind="draw", radius=0.05, strength=0.3,
                 falloff="smooth", spacing=0.15, invert=False,
                 plane_offset=0.0, pressure_curve="linear",
                 size_pressure=False, strength_pressure=True,
                 volume_preserving=False, crease_pinch=0.5, smooth_passes=1,
                 name=None):
        if kind not in BRUSH_KINDS:
            raise ValueError("unknown sculpt brush: %r" % (kind,))
        if falloff not in FALLOFFS:
            raise ValueError("unknown falloff curve: %r" % (falloff,))
        self.kind = kind
        self.radius = max(_EPS, float(radius))
        self.strength = float(strength)
        self.falloff = falloff
        self.spacing = max(0.01, float(spacing))
        self.invert = bool(invert)
        self.plane_offset = float(plane_offset)
        self.pressure_curve = pressure_curve
        self.size_pressure = bool(size_pressure)
        self.strength_pressure = bool(strength_pressure)
        self.volume_preserving = bool(volume_preserving)
        self.crease_pinch = float(crease_pinch)
        self.smooth_passes = max(1, int(smooth_passes))
        self.name = name or kind

    def copy(self, **overrides):
        kw = dict((k, getattr(self, k)) for k in self.__slots__)
        kw["name"] = overrides.pop("name", self.name)
        kw.update(overrides)
        return BrushParams(**kw)

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        name = d.pop("name", None)
        obj = cls(**d)
        if name:
            obj.name = name
        return obj

    def signed_strength(self):
        return -self.strength if self.invert else self.strength

    def __eq__(self, other):
        if not isinstance(other, BrushParams):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self):
        return "BrushParams(%s, r=%.4g, s=%.3g, %s)" % (
            self.kind, self.radius, self.strength, self.falloff)


#: Named factory presets; ``preset(name)`` returns a fresh copy.
PRESETS = {
    "draw":       dict(kind="draw", strength=0.25, falloff="smooth"),
    "inflate":    dict(kind="inflate", strength=0.2, falloff="smooth"),
    "clay":       dict(kind="clay", strength=0.4, falloff="smooth",
                       plane_offset=0.05),
    "clay_strips": dict(kind="clay", strength=0.55, falloff="sharp",
                        plane_offset=0.08, spacing=0.08),
    "flatten":    dict(kind="flatten", strength=0.5, falloff="smooth"),
    "scrape":     dict(kind="scrape", strength=0.5, falloff="sharp",
                       plane_offset=-0.02),
    "pinch":      dict(kind="pinch", strength=0.4, falloff="sharp"),
    "contrast":   dict(kind="pinch", strength=0.4, falloff="sharp",
                       invert=True),
    "smooth":     dict(kind="smooth", strength=0.5, falloff="smooth",
                       spacing=0.08),
    "polish":     dict(kind="smooth", strength=0.4, falloff="smooth",
                       volume_preserving=True),
    "grab":       dict(kind="grab", strength=1.0, falloff="root",
                       spacing=0.02),
    "snake_hook": dict(kind="snake_hook", strength=1.0, falloff="root",
                       spacing=0.02, crease_pinch=0.3),
    "crease":     dict(kind="crease", strength=0.35, falloff="sharp",
                       crease_pinch=0.6),
    "erase":      dict(kind="erase", strength=0.5, falloff="smooth"),
}


def preset(name):
    """Return a fresh :class:`BrushParams` for a named factory preset."""
    if name not in PRESETS:
        raise KeyError("no such sculpt brush preset: %r" % (name,))
    kw = dict(PRESETS[name])
    kw["name"] = name
    return BrushParams(**kw)


# --------------------------------------------------------------------------
# stroke resampling
# --------------------------------------------------------------------------

class Dab(object):
    """One brush imprint: where, which way, how hard."""

    __slots__ = ("center", "normal", "direction", "radius", "strength",
                 "pressure", "distance", "time")

    def __init__(self, center, normal=(0.0, 0.0, 1.0),
                 direction=(0.0, 0.0, 0.0), radius=0.05, strength=0.3,
                 pressure=1.0, distance=0.0, time=0.0):
        self.center = (float(center[0]), float(center[1]), float(center[2]))
        self.normal = _unit(normal) if normal is not None else (0.0, 0.0, 1.0)
        self.direction = (float(direction[0]), float(direction[1]),
                          float(direction[2]))
        self.radius = float(radius)
        self.strength = float(strength)
        self.pressure = float(pressure)
        self.distance = float(distance)
        self.time = float(time)

    def copy(self, **kw):
        d = Dab(kw.get("center", self.center), kw.get("normal", self.normal),
                kw.get("direction", self.direction),
                kw.get("radius", self.radius),
                kw.get("strength", self.strength),
                kw.get("pressure", self.pressure),
                kw.get("distance", self.distance), kw.get("time", self.time))
        return d

    def __repr__(self):
        return "Dab(%.4g, %.4g, %.4g, r=%.4g, s=%.3g)" % (
            self.center[0], self.center[1], self.center[2], self.radius,
            self.strength)


class StrokeSampler(object):
    """Resamples a 3D controller path into evenly spaced :class:`Dab` s.

    The VR runtime delivers a handful of poses per frame; a fast sweep can jump
    several brush radii between two of them.  The sampler walks the segment
    between consecutive poses emitting a dab every ``spacing * 2 * radius``
    metres and carries the leftover distance across calls, so the spacing along
    the whole stroke stays uniform however jerky the input was.
    """

    def __init__(self, params):
        self.params = params
        self._last = None      # (point, normal, pressure)
        self._acc = 0.0
        self._total = 0.0
        self.active = False

    # -- helpers ---------------------------------------------------------
    def radius_for(self, pressure):
        p = self.params
        if not p.size_pressure:
            return p.radius
        return max(_EPS, p.radius * (0.15 + 0.85 * pressure))

    def strength_for(self, pressure):
        p = self.params
        s = p.signed_strength()
        return s * pressure if p.strength_pressure else s

    def spacing_for(self, radius):
        return max(1e-9, self.params.spacing * 2.0 * radius)

    def _make(self, point, normal, pressure, direction, distance, time):
        return Dab(point, normal, direction, self.radius_for(pressure),
                   self.strength_for(pressure), pressure, distance, time)

    # -- event API -------------------------------------------------------
    def begin(self, point, normal=None, pressure=1.0, time=0.0):
        p = apply_pressure_curve(pressure, self.params.pressure_curve)
        self._last = (_v3(point), normal, p)
        self._acc = 0.0
        self._total = 0.0
        self.active = True
        return [self._make(_v3(point), normal, p, (0.0, 0.0, 0.0), 0.0, time)]

    def move(self, point, normal=None, pressure=1.0, time=0.0):
        if not self.active:
            return self.begin(point, normal, pressure, time)
        p1 = apply_pressure_curve(pressure, self.params.pressure_curve)
        p_new = _v3(point)
        p_old, n_old, p0 = self._last
        seg = _dist(p_new, p_old)
        out = []
        if seg <= 1e-12:
            self._last = (p_new, normal if normal is not None else n_old, p1)
            return out
        step_dir = _mul(_sub(p_new, p_old), 1.0 / seg)
        pos = 0.0
        while True:
            mid = p0 + (p1 - p0) * ((pos + 1e-15) / seg)
            step = self.spacing_for(self.radius_for(mid))
            need = step - self._acc
            if pos + need > seg:
                self._acc += seg - pos
                break
            pos += need
            t = pos / seg
            point_t = _add(p_old, _mul(step_dir, pos))
            pr = p0 + (p1 - p0) * t
            nrm = _lerp_normal(n_old, normal, t)
            self._total += step
            out.append(self._make(point_t, nrm, pr, _mul(step_dir, step),
                                  self._total, time))
            self._acc = 0.0
        self._last = (p_new, normal if normal is not None else n_old, p1)
        return out

    def end(self):
        self.active = False
        self._last = None
        self._acc = 0.0
        return []


def resample_stroke(points, params):
    """Resample a whole path at once.

    ``points`` is a sequence of ``(x, y, z)``, ``(point, normal)`` or
    ``(point, normal, pressure)``.  Returns a list of :class:`Dab`.
    """
    pts = list(points)
    if not pts:
        return []
    sampler = StrokeSampler(params)

    def unpack(item):
        if (len(item) == 3 and not isinstance(item[0], (list, tuple))
                and not hasattr(item[0], "__len__")):
            return (_v3(item), None, 1.0)
        point = _v3(item[0])
        normal = item[1] if len(item) > 1 else None
        pressure = float(item[2]) if len(item) > 2 else 1.0
        return (point, normal, pressure)

    p, n, pr = unpack(pts[0])
    out = sampler.begin(p, n, pr)
    for item in pts[1:]:
        p, n, pr = unpack(item)
        out.extend(sampler.move(p, n, pr))
    sampler.end()
    return out


# --------------------------------------------------------------------------
# applying a dab
# --------------------------------------------------------------------------

def affected(mesh, dab, mask=None):
    """Sorted vertex indices the dab can move, honouring frozen mask areas."""
    idx = mesh.vertices_in_radius(dab.center, dab.radius)
    if mask is None:
        return idx
    return [i for i in idx if mask.factor(i) > 0.0]


def apply_dab(mesh, layer, params, dab, mask=None, stack=None, history=None,
              indices=None):
    """Lay one dab down: compute the deltas, accumulate them into ``layer``.

    ``mesh`` carries the *evaluated* positions (base plus the whole stack).
    The deltas are computed against those positions and stored in ``layer`` as
    raw offsets; the caller decides how the mesh is refreshed:

    * pass ``stack`` and the touched vertices are re-evaluated from the whole
      stack, which is what the session does;
    * omit it and the mesh is moved directly by ``delta * layer.weight``.

    ``history`` -- when an entry is open -- is snapshotted before the layer is
    modified.  Returns the sorted list of vertex indices that moved.
    """
    if layer.locked:
        return []
    idx = affected(mesh, dab, mask) if indices is None else list(indices)
    if not idx:
        return []
    deltas = _compute_deltas(mesh, layer, params, dab, idx, mask)
    if not deltas:
        return []
    touched = sorted(deltas)
    if history is not None and history.open_entry is not None:
        history.snapshot(layer, touched)
    for i in touched:
        layer.add(i, deltas[i])
    drift = 0.0
    for d in deltas.values():
        m = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
        if m > drift:
            drift = m
    if stack is not None:
        stack.evaluate(out=mesh.positions, indices=touched)
        mesh.touch(touched, drift=drift)
    else:
        w = layer.effective_weight
        p = mesh.positions
        for i in touched:
            d = deltas[i]
            o = i * 3
            p[o] += d[0] * w
            p[o + 1] += d[1] * w
            p[o + 2] += d[2] * w
        mesh.touch(touched, drift=drift * abs(w))
    return touched


def _compute_deltas(mesh, layer, params, dab, idx, mask):
    kind = params.kind
    if kind == "smooth":
        return _delta_smooth(mesh, params, dab, idx, mask)
    if kind == "erase":
        return _delta_erase(layer, params, dab, idx, mask, mesh)
    if kind in ("flatten", "scrape", "clay"):
        return _delta_plane(mesh, params, dab, idx, mask, kind)
    if kind in ("grab", "snake_hook"):
        return _delta_grab(mesh, params, dab, idx, mask, kind)
    if kind == "pinch":
        return _delta_pinch(mesh, params, dab, idx, mask)
    if kind == "crease":
        return _delta_crease(mesh, params, dab, idx, mask)
    return _delta_normal(mesh, params, dab, idx, mask, kind)


def _weights(mesh, params, dab, idx, mask):
    """``{index: falloff * mask}`` for the vertices inside the dab."""
    out = {}
    r = dab.radius
    if r <= 0.0:
        return out
    p = mesh.positions
    cx, cy, cz = dab.center
    curve = params.falloff
    for i in idx:
        o = i * 3
        dx = p[o] - cx
        dy = p[o + 1] - cy
        dz = p[o + 2] - cz
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d >= r:
            continue
        w = falloff(curve, d / r)
        if mask is not None:
            w *= mask.factor(i)
        if w > 0.0:
            out[i] = w
    return out


def _delta_normal(mesh, params, dab, idx, mask, kind):
    """draw (stroke normal) and inflate (per-vertex normal)."""
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    amount = dab.strength * dab.radius
    out = {}
    if kind == "inflate":
        nrm = mesh.normals()
        for i, f in w.items():
            o = i * 3
            k = amount * f
            out[i] = (nrm[o] * k, nrm[o + 1] * k, nrm[o + 2] * k)
    else:
        nx, ny, nz = dab.normal
        for i, f in w.items():
            k = amount * f
            out[i] = (nx * k, ny * k, nz * k)
    return out


def _delta_plane(mesh, params, dab, idx, mask, kind):
    """flatten / scrape / clay -- all defined against a local plane."""
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    p = mesh.positions
    nx, ny, nz = dab.normal
    # weighted centroid of the affected region gives the plane its position
    total = 0.0
    sx = sy = sz = 0.0
    for i, f in w.items():
        o = i * 3
        sx += p[o] * f
        sy += p[o + 1] * f
        sz += p[o + 2] * f
        total += f
    if total <= _EPS:
        return {}
    px = sx / total + nx * params.plane_offset * dab.radius
    py = sy / total + ny * params.plane_offset * dab.radius
    pz = sz / total + nz * params.plane_offset * dab.radius
    # ``flatten`` pulls both sides onto the plane and takes the strength
    # signed.  ``scrape`` and ``clay`` are one-sided: they always move
    # *towards* the plane, and the sign of the strength picks which side they
    # act on -- scrape shaves the material standing above the plane, inverted
    # scrape fills the pits below it, and clay is the same pair the other way
    # round.
    signed = dab.strength
    strength = abs(signed) if kind != "flatten" else signed
    above = (signed >= 0.0) if kind == "scrape" else (signed < 0.0)
    out = {}
    for i, f in w.items():
        o = i * 3
        d = ((p[o] - px) * nx + (p[o + 1] - py) * ny + (p[o + 2] - pz) * nz)
        if kind != "flatten":
            if above and d <= 0.0:
                continue
            if not above and d >= 0.0:
                continue
        k = -d * strength * f
        if k == 0.0:
            continue
        out[i] = (nx * k, ny * k, nz * k)
    return out


def _delta_pinch(mesh, params, dab, idx, mask):
    """Pull towards (or, inverted, away from) the stroke axis."""
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    p = mesh.positions
    cx, cy, cz = dab.center
    nx, ny, nz = dab.normal
    out = {}
    for i, f in w.items():
        o = i * 3
        vx = p[o] - cx
        vy = p[o + 1] - cy
        vz = p[o + 2] - cz
        axial = vx * nx + vy * ny + vz * nz
        rx = vx - nx * axial
        ry = vy - ny * axial
        rz = vz - nz * axial
        k = -dab.strength * f
        if k == 0.0:
            continue
        out[i] = (rx * k, ry * k, rz * k)
    return out


def _delta_crease(mesh, params, dab, idx, mask):
    """Pinch towards the axis and push in along the normal."""
    pinch = _delta_pinch(mesh, params, dab, idx, mask)
    push = _delta_normal(mesh, params, dab, idx, mask, "draw")
    k = _clamp(params.crease_pinch, 0.0, 4.0)
    out = {}
    for i, d in push.items():
        px, py, pz = pinch.get(i, (0.0, 0.0, 0.0))
        out[i] = (-d[0] + px * k, -d[1] + py * k, -d[2] + pz * k)
    for i, d in pinch.items():
        if i not in out:
            out[i] = (d[0] * k, d[1] * k, d[2] * k)
    return out


def _delta_grab(mesh, params, dab, idx, mask, kind):
    """Translate the region by the controller delta."""
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    dx, dy, dz = dab.direction
    scale = abs(dab.strength) if params.strength_pressure else 1.0
    out = {}
    if kind == "snake_hook":
        p = mesh.positions
        cx = dab.center[0] + dx
        cy = dab.center[1] + dy
        cz = dab.center[2] + dz
        pinch = _clamp(params.crease_pinch, 0.0, 1.0)
        for i, f in w.items():
            o = i * 3
            k = f * scale
            # the region is dragged to the new tip and squeezed towards it,
            # which is what makes a snake hook thin out as it is pulled
            out[i] = (dx * k + (cx - p[o]) * f * pinch * scale,
                      dy * k + (cy - p[o + 1]) * f * pinch * scale,
                      dz * k + (cz - p[o + 2]) * f * pinch * scale)
        return out
    for i, f in w.items():
        k = f * scale
        out[i] = (dx * k, dy * k, dz * k)
    return out


def _delta_smooth(mesh, params, dab, idx, mask):
    """Laplacian relaxation, optionally tangential only.

    The plain form moves each vertex a fraction ``strength * falloff`` of the
    way to its one-ring centroid.  With ``strength <= 1`` that is a convex
    combination, so the vertex can never leave the convex hull of its
    neighbours and repeated passes converge monotonically.

    ``volume_preserving`` removes the component along the vertex normal before
    moving, leaving only tangential relaxation: the surface is smoothed but no
    material is pushed inwards, so a smoothed sphere keeps its radius instead
    of shrinking towards its centre.
    """
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    p = mesh.positions
    nrm = mesh.normals() if params.volume_preserving else None
    strength = _clamp(abs(dab.strength), 0.0, 1.0)
    out = {}
    for i, f in w.items():
        cx, cy, cz = mesh.one_ring_centroid(i)
        o = i * 3
        lx = cx - p[o]
        ly = cy - p[o + 1]
        lz = cz - p[o + 2]
        if nrm is not None:
            axial = lx * nrm[o] + ly * nrm[o + 1] + lz * nrm[o + 2]
            lx -= nrm[o] * axial
            ly -= nrm[o + 1] * axial
            lz -= nrm[o + 2] * axial
        k = strength * f
        if k == 0.0:
            continue
        out[i] = (lx * k, ly * k, lz * k)
    return out


def _delta_erase(layer, params, dab, idx, mask, mesh):
    """Reduce what the *active layer* already holds, towards zero."""
    w = _weights(mesh, params, dab, idx, mask)
    if not w:
        return {}
    strength = _clamp(abs(dab.strength), 0.0, 1.0)
    out = {}
    for i, f in w.items():
        if i not in layer:
            continue
        x, y, z = layer.get(i)
        k = strength * f
        if k == 0.0:
            continue
        out[i] = (-x * k, -y * k, -z * k)
    return out


# --------------------------------------------------------------------------
# vector helpers
# --------------------------------------------------------------------------

def _v3(p):
    return (float(p[0]), float(p[1]), float(p[2]))


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dist(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _unit(v):
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    n = math.sqrt(x * x + y * y + z * z)
    if n < _EPS:
        return (0.0, 0.0, 1.0)
    return (x / n, y / n, z / n)


def _lerp_normal(a, b, t):
    if b is None:
        return a
    if a is None:
        return b
    return _unit((a[0] + (b[0] - a[0]) * t,
                  a[1] + (b[1] - a[1]) * t,
                  a[2] + (b[2] - a[2]) * t))
