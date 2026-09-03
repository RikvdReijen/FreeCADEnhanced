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
"""Brush engine for VR texture painting.

Pure math plus :mod:`xrpaint.raster`; no Coin, no FreeCAD, no numpy required.

The engine is built around three pieces:

``BrushParams``      the tweakable description of a brush,
``make_mask()``      turns those parameters into an 8 bit alpha stamp,
``StrokeSampler``    resamples a controller path into evenly spaced stamps.

The sampler is what keeps a fast controller sweep continuous: the VR runtime
delivers a handful of poses per frame, and the sampler walks the polyline
between them emitting a stamp every ``spacing * diameter`` pixels, carrying the
leftover distance across frames so spacing never drifts.
"""

import math
import random

from . import raster
from .raster import Mask

__all__ = [
    "BRUSH_KINDS",
    "PRESETS",
    "PRESSURE_CURVES",
    "BrushParams",
    "Stamp",
    "StrokeSampler",
    "apply_pressure_curve",
    "clear_mask_cache",
    "make_mask",
    "paint_stamps",
    "preset",
    "stamp_along_path",
]


BRUSH_KINDS = (
    "round",      # hard-ish round tip, hardness controls the falloff
    "soft",       # airbrush, gaussian falloff
    "square",     # axis aligned / rotatable box
    "chisel",     # narrow rotated rectangle, calligraphic
    "marker",     # flat alpha rounded rectangle
    "spray",      # scattered dots, density controlled
    "smudge",     # pushes existing pixels around
    "clone",      # copies from a source offset
)

PRESSURE_CURVES = ("linear", "soft", "hard", "square", "constant")


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _smoothstep(t):
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------

class BrushParams(object):
    """Everything that describes a brush tip and how it is laid down."""

    __slots__ = (
        "kind", "radius", "hardness", "flow", "opacity", "spacing",
        "jitter", "scatter", "rotation", "angle_jitter", "aspect",
        "pressure_curve", "size_pressure", "opacity_pressure",
        "flow_pressure", "blend", "density", "seed", "smudge_strength",
        "clone_offset", "name",
    )

    def __init__(self, kind="round", radius=16.0, hardness=0.75, flow=1.0,
                 opacity=1.0, spacing=0.15, jitter=0.0, scatter=0.0,
                 rotation=0.0, angle_jitter=0.0, aspect=1.0,
                 pressure_curve="linear", size_pressure=True,
                 opacity_pressure=False, flow_pressure=True, blend="normal",
                 density=0.35, seed=12345, smudge_strength=0.6,
                 clone_offset=(0.0, 0.0), name=None):
        if kind not in BRUSH_KINDS:
            raise ValueError("unknown brush kind: %r" % (kind,))
        if blend not in raster.BLEND_MODES:
            raise ValueError("unknown blend mode: %r" % (blend,))
        self.kind = kind
        self.radius = float(radius)
        self.hardness = _clamp(float(hardness), 0.0, 1.0)
        self.flow = _clamp(float(flow), 0.0, 1.0)
        self.opacity = _clamp(float(opacity), 0.0, 1.0)
        self.spacing = max(0.01, float(spacing))
        self.jitter = max(0.0, float(jitter))
        self.scatter = max(0.0, float(scatter))
        self.rotation = float(rotation)
        self.angle_jitter = max(0.0, float(angle_jitter))
        self.aspect = max(0.01, float(aspect))
        self.pressure_curve = pressure_curve
        self.size_pressure = bool(size_pressure)
        self.opacity_pressure = bool(opacity_pressure)
        self.flow_pressure = bool(flow_pressure)
        self.blend = blend
        self.density = _clamp(float(density), 0.0, 1.0)
        self.seed = int(seed)
        self.smudge_strength = _clamp(float(smudge_strength), 0.0, 1.0)
        self.clone_offset = (float(clone_offset[0]), float(clone_offset[1]))
        self.name = name or kind

    def copy(self, **overrides):
        kw = dict((k, getattr(self, k)) for k in self.__slots__)
        kw.pop("name", None)
        kw["name"] = overrides.pop("name", self.name)
        kw.update(overrides)
        return BrushParams(**kw)

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d.pop("name", None)
        name = d.pop("name", None) if "name" in d else None
        obj = cls(**d)
        if name:
            obj.name = name
        return obj

    def __repr__(self):
        return "BrushParams(%s, r=%.2f, hardness=%.2f)" % (
            self.kind, self.radius, self.hardness)

    def __eq__(self, other):
        if not isinstance(other, BrushParams):
            return NotImplemented
        return self.to_dict() == other.to_dict()


#: Named factory presets.  ``preset(name)`` returns a fresh copy.
PRESETS = {
    "round":    dict(kind="round", radius=16.0, hardness=0.85, spacing=0.1),
    "airbrush": dict(kind="soft", radius=32.0, hardness=0.15, flow=0.25,
                     spacing=0.05, opacity_pressure=True),
    "square":   dict(kind="square", radius=14.0, hardness=1.0, spacing=0.2),
    "chisel":   dict(kind="chisel", radius=20.0, hardness=0.9, aspect=0.28,
                     rotation=math.pi / 4.0, spacing=0.06),
    "marker":   dict(kind="marker", radius=18.0, hardness=1.0, aspect=0.7,
                     flow=0.85, spacing=0.08, size_pressure=False),
    "spray":    dict(kind="spray", radius=26.0, hardness=0.0, flow=0.4,
                     density=0.25, spacing=0.25, scatter=0.9),
    "smudge":   dict(kind="smudge", radius=20.0, hardness=0.4, spacing=0.04,
                     smudge_strength=0.65),
    "clone":    dict(kind="clone", radius=22.0, hardness=0.7, spacing=0.05,
                     clone_offset=(40.0, 0.0)),
}


def preset(name):
    """Return a new :class:`BrushParams` for a named factory preset."""
    if name not in PRESETS:
        raise KeyError("no such brush preset: %r" % (name,))
    kw = dict(PRESETS[name])
    kw["name"] = name
    return BrushParams(**kw)


# --------------------------------------------------------------------------
# pressure
# --------------------------------------------------------------------------

def apply_pressure_curve(p, curve="linear"):
    """Map the raw trigger value 0..1 through a response curve."""
    p = _clamp(float(p), 0.0, 1.0)
    if isinstance(curve, (int, float)):
        gamma = float(curve)
        if gamma <= 0.0:
            return p
        return p ** gamma
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
# alpha mask generation
# --------------------------------------------------------------------------

_MASK_CACHE = {}
_MASK_CACHE_LIMIT = 256


def clear_mask_cache():
    _MASK_CACHE.clear()


def _mask_key(kind, radius, hardness, rotation, aspect, density, seed):
    return (kind, round(radius, 2), round(hardness, 3),
            round(rotation % (2.0 * math.pi), 4), round(aspect, 3),
            round(density, 3), seed)


def make_mask(params, radius=None, rotation=None, cache=True):
    """Build the 8 bit alpha stamp for ``params``.

    ``radius``/``rotation`` override the values in ``params`` (the stroke
    sampler passes pressure-scaled values here).
    """
    r = float(params.radius if radius is None else radius)
    r = max(0.5, r)
    rot = float(params.rotation if rotation is None else rotation)
    kind = params.kind
    key = _mask_key(kind, r, params.hardness, rot, params.aspect,
                    params.density, params.seed)
    if cache:
        hit = _MASK_CACHE.get(key)
        if hit is not None:
            return hit
    mask = _build_mask(kind, r, params.hardness, rot, params.aspect,
                       params.density, params.seed)
    if cache:
        if len(_MASK_CACHE) >= _MASK_CACHE_LIMIT:
            _MASK_CACHE.clear()
        _MASK_CACHE[key] = mask
    return mask


def _build_mask(kind, r, hardness, rot, aspect, density, seed):
    # the stamp is always odd sized so it has a well defined centre pixel
    ext = r
    if kind in ("square", "chisel", "marker"):
        ext = r * math.sqrt(1.0 + aspect * aspect)
    n = int(math.ceil(ext - 0.5)) * 2 + 1
    n = max(1, n)
    c = (n - 1) * 0.5
    mask = Mask(n, n)
    data = mask.data
    ca = math.cos(-rot)
    sa = math.sin(-rot)

    if kind == "spray":
        _fill_spray(mask, r, density, seed)
        return mask

    for iy in range(n):
        dy = iy - c
        row = iy * n
        for ix in range(n):
            dx = ix - c
            if kind in ("round", "soft", "smudge", "clone"):
                d = math.hypot(dx, dy)
                cov = _round_profile(d, r, hardness,
                                     soft=(kind == "soft"))
            else:
                # rotate into brush space
                bx = dx * ca - dy * sa
                by = dx * sa + dy * ca
                cov = _box_profile(bx, by, r, r * aspect, hardness,
                                   rounded=(kind == "marker"))
            if cov > 0.0:
                v = int(cov * 255.0 + 0.5)
                data[row + ix] = 255 if v > 255 else v
    return mask


def _round_profile(d, r, hardness, soft=False):
    if d > r + 0.5:
        return 0.0
    if soft:
        # gaussian airbrush; hardness pulls the falloff towards the rim
        k = 1.0 + 8.0 * (1.0 - hardness)
        t = d / r if r > 0 else 0.0
        if t >= 1.0:
            return 0.0
        return math.exp(-k * t * t) - math.exp(-k)
    core = r * hardness
    if d <= core:
        return 1.0
    if r - core < 1e-9:
        # perfectly hard tip: 1px analytic-ish antialiased rim
        return _clamp(r + 0.5 - d, 0.0, 1.0)
    t = (d - core) / (r - core)
    return 1.0 - _smoothstep(t)


def _box_profile(bx, by, hw, hh, hardness, rounded=False):
    ax = abs(bx)
    ay = abs(by)
    if rounded:
        # rounded rectangle: distance to the inset rectangle's corner radius
        rad = min(hw, hh) * 0.35
        if rad <= 0.0:
            return 1.0 if (ax <= hw and ay <= hh) else 0.0
        rx = max(0.0, ax - (hw - rad))
        ry = max(0.0, ay - (hh - rad))
        d = math.hypot(rx, ry)
        soft = max(0.5, rad * (1.0 - hardness))
        return _clamp((rad + 0.5 - d) / soft, 0.0, 1.0)
    cx = _clamp(hw + 0.5 - ax, 0.0, 1.0)
    cy = _clamp(hh + 0.5 - ay, 0.0, 1.0)
    cov = cx * cy
    if hardness >= 1.0:
        return cov
    # soften the edge inwards
    fx = _clamp((hw - ax) / max(1e-6, hw * (1.0 - hardness)), 0.0, 1.0)
    fy = _clamp((hh - ay) / max(1e-6, hh * (1.0 - hardness)), 0.0, 1.0)
    return cov * _smoothstep(fx) * _smoothstep(fy)


def _fill_spray(mask, r, density, seed):
    n = mask.width
    c = (n - 1) * 0.5
    rng = random.Random(seed)
    area = math.pi * r * r
    dots = max(1, int(area * _clamp(density, 0.01, 1.0) * 0.25))
    for _ in range(dots):
        # uniform in the disc
        a = rng.random() * 2.0 * math.pi
        rr = r * math.sqrt(rng.random())
        x = c + rr * math.cos(a)
        y = c + rr * math.sin(a)
        ix = int(x + 0.5)
        iy = int(y + 0.5)
        if 0 <= ix < n and 0 <= iy < n:
            o = iy * n + ix
            v = mask.data[o] + rng.randint(60, 200)
            mask.data[o] = 255 if v > 255 else v


# --------------------------------------------------------------------------
# stroke resampling
# --------------------------------------------------------------------------

class Stamp(object):
    """One brush imprint produced by the sampler."""

    __slots__ = ("x", "y", "pressure", "radius", "opacity", "flow",
                 "rotation", "distance")

    def __init__(self, x, y, pressure, radius, opacity, flow, rotation,
                 distance):
        self.x = x
        self.y = y
        self.pressure = pressure
        self.radius = radius
        self.opacity = opacity
        self.flow = flow
        self.rotation = rotation
        self.distance = distance

    def as_tuple(self):
        return (self.x, self.y, self.pressure, self.radius, self.opacity,
                self.flow, self.rotation, self.distance)

    def __repr__(self):
        return "Stamp(%.2f, %.2f, p=%.2f, r=%.2f)" % (
            self.x, self.y, self.pressure, self.radius)


class StrokeSampler(object):
    """Turns an irregular controller path into evenly spaced brush stamps.

    Usage::

        s = StrokeSampler(params)
        stamps = s.begin(x, y, pressure)
        stamps += s.move(x2, y2, pressure2)     # one call per VR frame
        s.end()

    The leftover distance is carried across calls, so the spacing along the
    whole stroke is uniform even when the controller jumps hundreds of pixels
    between two frames.
    """

    def __init__(self, params, seed=None):
        self.params = params
        self.rng = random.Random(params.seed if seed is None else seed)
        self._last = None          # (x, y, pressure)
        self._acc = 0.0            # distance travelled since the last stamp
        self._total = 0.0
        self.active = False

    # -- helpers ---------------------------------------------------------
    def spacing_px(self, radius):
        return max(0.5, self.params.spacing * 2.0 * radius)

    def _radius_for(self, pressure):
        p = self.params
        if not p.size_pressure:
            return p.radius
        return max(0.5, p.radius * (0.15 + 0.85 * pressure))

    def _make_stamp(self, x, y, pressure, distance):
        p = self.params
        radius = self._radius_for(pressure)
        opacity = p.opacity * (pressure if p.opacity_pressure else 1.0)
        flow = p.flow * (pressure if p.flow_pressure else 1.0)
        rot = p.rotation
        if p.angle_jitter:
            rot += (self.rng.random() * 2.0 - 1.0) * p.angle_jitter
        jx = jy = 0.0
        amt = max(p.jitter, p.scatter)
        if amt:
            a = self.rng.random() * 2.0 * math.pi
            rr = amt * p.radius * math.sqrt(self.rng.random())
            jx = rr * math.cos(a)
            jy = rr * math.sin(a)
        return Stamp(x + jx, y + jy, pressure, radius, opacity, flow, rot,
                     distance)

    # -- event API -------------------------------------------------------
    def begin(self, x, y, pressure=1.0):
        p = apply_pressure_curve(pressure, self.params.pressure_curve)
        self._last = (float(x), float(y), p)
        self._acc = 0.0
        self._total = 0.0
        self.active = True
        return [self._make_stamp(float(x), float(y), p, 0.0)]

    def move(self, x, y, pressure=1.0):
        if not self.active:
            return self.begin(x, y, pressure)
        x = float(x)
        y = float(y)
        p1 = apply_pressure_curve(pressure, self.params.pressure_curve)
        x0, y0, p0 = self._last
        seg = math.hypot(x - x0, y - y0)
        out = []
        if seg <= 1e-9:
            self._last = (x, y, p1)
            return out
        pos = 0.0
        while True:
            pr_mid = p0 + (p1 - p0) * ((pos + 1e-12) / seg)
            step = self.spacing_px(self._radius_for(pr_mid))
            need = step - self._acc
            if pos + need > seg:
                self._acc += seg - pos
                break
            pos += need
            t = pos / seg
            px = x0 + (x - x0) * t
            py = y0 + (y - y0) * t
            pr = p0 + (p1 - p0) * t
            self._total += step
            out.append(self._make_stamp(px, py, pr, self._total))
            self._acc = 0.0
        self._last = (x, y, p1)
        return out

    def end(self):
        self.active = False
        self._last = None
        self._acc = 0.0
        return []


def stamp_along_path(points, params, seed=None):
    """Resample a whole path in one go.

    ``points`` is a sequence of ``(x, y)`` or ``(x, y, pressure)``.  Returns a
    list of :class:`Stamp`.
    """
    pts = list(points)
    if not pts:
        return []
    sampler = StrokeSampler(params, seed=seed)

    def unpack(p):
        if len(p) >= 3:
            return float(p[0]), float(p[1]), float(p[2])
        return float(p[0]), float(p[1]), 1.0

    x, y, pr = unpack(pts[0])
    out = sampler.begin(x, y, pr)
    for p in pts[1:]:
        x, y, pr = unpack(p)
        out.extend(sampler.move(x, y, pr))
    sampler.end()
    return out


# --------------------------------------------------------------------------
# painting stamps into an image
# --------------------------------------------------------------------------

def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]),
            max(a[3], b[3]))


def paint_stamps(image, stamps, color, params, source=None):
    """Blit a list of stamps into ``image``; returns the union dirty rect.

    ``source`` is the sample image used by the ``clone`` and ``smudge``
    brushes; it defaults to ``image`` itself.
    """
    dirty = None
    for st in stamps:
        mask = make_mask(params, radius=st.radius, rotation=st.rotation)
        if params.kind == "smudge":
            r = _apply_smudge(image, mask, st, params)
        elif params.kind == "clone":
            r = _apply_clone(image, mask, st, params,
                             source if source is not None else image)
        else:
            r = raster.blit_brush(image, mask, st.x, st.y, color,
                                  params.blend, st.opacity, st.flow)
        dirty = _union(dirty, r)
    return dirty


def _apply_clone(image, mask, st, params, source):
    ox, oy = params.clone_offset
    dst_x = int(math.floor(st.x - (mask.width - 1) * 0.5 + 0.5))
    dst_y = int(math.floor(st.y - (mask.height - 1) * 0.5 + 0.5))
    alpha = st.opacity * st.flow
    x0 = max(0, dst_x)
    y0 = max(0, dst_y)
    x1 = min(image.width, dst_x + mask.width)
    y1 = min(image.height, dst_y + mask.height)
    if x0 >= x1 or y0 >= y1 or alpha <= 0.0:
        return None
    for y in range(y0, y1):
        for x in range(x0, x1):
            m = mask.data[(y - dst_y) * mask.width + (x - dst_x)]
            if not m:
                continue
            sx = int(round(x - ox))
            sy = int(round(y - oy))
            src = source.get_pixel(sx, sy) if source.in_bounds(sx, sy) \
                else (0, 0, 0, 0)
            image.blend_pixel(x, y, src, (m / 255.0) * alpha, "normal")
    return (x0, y0, x1, y1)


def _apply_smudge(image, mask, st, params):
    """Drag colour along the stroke direction, like a wet finger."""
    dst_x = int(math.floor(st.x - (mask.width - 1) * 0.5 + 0.5))
    dst_y = int(math.floor(st.y - (mask.height - 1) * 0.5 + 0.5))
    x0 = max(0, dst_x)
    y0 = max(0, dst_y)
    x1 = min(image.width, dst_x + mask.width)
    y1 = min(image.height, dst_y + mask.height)
    if x0 >= x1 or y0 >= y1:
        return None
    # average colour under the stamp is smeared back over it
    acc = [0.0, 0.0, 0.0, 0.0]
    wsum = 0.0
    for y in range(y0, y1):
        for x in range(x0, x1):
            m = mask.data[(y - dst_y) * mask.width + (x - dst_x)]
            if not m:
                continue
            w = m / 255.0
            px = image.get_pixel(x, y)
            for i in range(4):
                acc[i] += px[i] * w
            wsum += w
    if wsum <= 0.0:
        return None
    avg = tuple(int(a / wsum + 0.5) for a in acc)
    strength = params.smudge_strength * st.flow * st.opacity
    for y in range(y0, y1):
        for x in range(x0, x1):
            m = mask.data[(y - dst_y) * mask.width + (x - dst_x)]
            if not m:
                continue
            image.blend_pixel(x, y, avg, (m / 255.0) * strength, "normal")
    return (x0, y0, x1, y1)
