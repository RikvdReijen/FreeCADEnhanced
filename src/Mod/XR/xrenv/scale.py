# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""Miniaturisation: put the user inside a machine at model railway scale.

Sign convention
---------------
There is no way to shrink a head mounted display, so **shrinking the user is
implemented by scaling the world up**:

* :attr:`ScaleController.scale` is the *user's* scale — ``1.0`` is life size,
  ``12.0`` means the user is twelve times smaller than reality.
* :attr:`ScaleController.world_scale` is what the environment (and the
  document dropped into it) must be multiplied by.  It **grows** as ``scale``
  grows: ``world_scale == scale``.
* :attr:`ScaleController.world_offset` is the translation applied *after* the
  scale, ``p_view = world_scale * p_env + world_offset``.  It is chosen so
  that the held pivot — normally the environment's spawn point, i.e. the
  ground under the user's feet — stays put instead of the world sliding away
  as it grows.

So a 1.65 m tall person standing on a Bambu build plate at ``scale = 11``
reads as a ``1.65 / 11 = 0.15 m`` tall figure, and the 256 mm plate is drawn
``0.256 * 11 = 2.8 m`` across, about the size of a small room.

The module is pure math: no ``FreeCAD``, ``FreeCADGui`` or ``pivy`` import at
module level.  The one FreeCAD lookup (the ``ScaleTransition`` preference) is
done lazily inside :func:`transition_duration`, and falls back to a plain
default when FreeCAD is not importable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_EYE_HEIGHT",
    "DEFAULT_TRANSITION",
    "FitTransform",
    "ScaleController",
    "transition_duration",
    "fit_document_to_anchor",
    "quat_mul",
    "quat_rotate",
    "quat_from_axis_angle",
    "quat_conjugate",
]

DEFAULT_EYE_HEIGHT = 1.65      # metres, floor to eye, standing adult
DEFAULT_TRANSITION = 0.6       # seconds, matches the ScaleTransition preference
_EPS = 1e-12

Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# quaternion helpers (xyzw)
# ---------------------------------------------------------------------------


def quat_mul(a: Sequence[float], b: Sequence[float]) -> Quat:
    ax, ay, az, aw = (float(v) for v in a)
    bx, by, bz, bw = (float(v) for v in b)
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_conjugate(q: Sequence[float]) -> Quat:
    return (-float(q[0]), -float(q[1]), -float(q[2]), float(q[3]))


def quat_rotate(q: Sequence[float], v: Sequence[float]) -> Vec3:
    x, y, z, w = (float(c) for c in q)
    vx, vy, vz = (float(c) for c in v)
    # t = 2 * (q_vec x v);  v' = v + w*t + q_vec x t
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def quat_from_axis_angle(axis: Sequence[float], angle_rad: float) -> Quat:
    ax, ay, az = (float(v) for v in axis)
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < _EPS:
        return (0.0, 0.0, 0.0, 1.0)
    s = math.sin(angle_rad * 0.5) / n
    return (ax * s, ay * s, az * s, math.cos(angle_rad * 0.5))


def _smoothstep(t: float) -> float:
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------


def transition_duration(default: float = DEFAULT_TRANSITION) -> float:
    """Scale transition length in seconds.

    Reads the FreeCAD preference
    ``BaseApp/Preferences/Mod/XR:ScaleTransition`` when FreeCAD is importable,
    otherwise returns ``default``.  The import is deliberately done here and
    not at module level.
    """
    try:  # pragma: no cover - needs FreeCAD
        import FreeCAD  # noqa: WPS433

        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/XR")
        value = float(params.GetFloat("ScaleTransition", float(default)))
        if math.isfinite(value) and 0.0 <= value <= 10.0:
            return value
    except Exception:
        pass
    return float(default)


# ---------------------------------------------------------------------------
# document placement
# ---------------------------------------------------------------------------


@dataclass
class FitTransform:
    """Where and how big a FreeCAD document should sit on an anchor.

    ``translation`` is in environment metres (Y up), ``rotation`` is an
    ``xyzw`` quaternion and ``scale`` is a single uniform factor applied to
    the document *after* its own unit conversion.
    """

    translation: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    scale: float = 1.0
    #: True when the document had to be shrunk to fit the anchor.
    clipped: bool = False

    def apply(self, point: Sequence[float]) -> Vec3:
        """Map a point from document-local space into environment space."""
        p = (float(point[0]) * self.scale, float(point[1]) * self.scale, float(point[2]) * self.scale)
        r = quat_rotate(self.rotation, p)
        return (r[0] + self.translation[0], r[1] + self.translation[1], r[2] + self.translation[2])

    def as_dict(self) -> Dict[str, Any]:
        return {
            "translation": list(self.translation),
            "rotation": list(self.rotation),
            "scale": self.scale,
            "clipped": self.clipped,
        }


def _anchor_fields(anchor: Any) -> Optional[Tuple[Vec3, Quat, Tuple[float, float]]]:
    if anchor is None:
        return None
    if isinstance(anchor, dict):
        pos = anchor.get("position", (0.0, 0.0, 0.0))
        rot = anchor.get("rotation", (0.0, 0.0, 0.0, 1.0))
        size = anchor.get("size", (1.0, 1.0))
    else:
        pos = getattr(anchor, "position", (0.0, 0.0, 0.0))
        rot = getattr(anchor, "rotation", (0.0, 0.0, 0.0, 1.0))
        size = getattr(anchor, "size", (1.0, 1.0))
    try:
        return (
            (float(pos[0]), float(pos[1]), float(pos[2])),
            (float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3])),
            (float(size[0]), float(size[1])),
        )
    except (TypeError, IndexError, ValueError):
        return None


def fit_document_to_anchor(
    bbox: Optional[Sequence[Sequence[float]]],
    anchor: Any,
    unit_scale: float = 0.001,
    fill: float = 0.8,
    allow_upscale: bool = False,
    max_height: Optional[float] = None,
) -> Optional[FitTransform]:
    """Drop a document bounding box onto an anchor, e.g. the build plate.

    ``bbox`` is ``((minx, miny, minz), (maxx, maxy, maxz))`` in *document
    units* (millimetres by default), using FreeCAD's Z-up convention.  Pass
    ``None`` when the extent is unknown — the function then returns ``None``.

    The anchor's local frame is the FreeCAD convention too: its ``size``
    spans local X and Y and its surface normal is local +Z, so a document
    resting on its own XY plane needs no extra basis change — the anchor's
    ``rotation`` alone carries it into environment (Y up) space.

    The document is centred on the anchor with its lowest point touching the
    anchor surface, and shrunk only as far as needed to fit ``fill`` of the
    anchor footprint (and ``max_height`` of headroom, when given).  It is
    never enlarged unless ``allow_upscale`` is true, so a 20 mm bracket stays
    a 20 mm bracket on a 256 mm build plate.
    """
    if bbox is None:
        return None
    try:
        lo = (float(bbox[0][0]), float(bbox[0][1]), float(bbox[0][2]))
        hi = (float(bbox[1][0]), float(bbox[1][1]), float(bbox[1][2]))
    except (TypeError, IndexError, ValueError):
        return None
    if not all(math.isfinite(v) for v in lo + hi):
        return None

    fields = _anchor_fields(anchor)
    if fields is None:
        return None
    apos, arot, asize = fields
    if asize[0] <= 0.0 or asize[1] <= 0.0:
        return None

    u = float(unit_scale)
    if u <= 0.0:
        return None
    sx = max(0.0, hi[0] - lo[0]) * u
    sy = max(0.0, hi[1] - lo[1]) * u
    sz = max(0.0, hi[2] - lo[2]) * u
    if sx <= 0.0 and sy <= 0.0 and sz <= 0.0:
        return None

    fill = max(1e-3, min(1.0, float(fill)))
    limits: List[float] = []
    if sx > _EPS:
        limits.append(asize[0] * fill / sx)
    if sy > _EPS:
        limits.append(asize[1] * fill / sy)
    if max_height is not None and sz > _EPS and max_height > 0.0:
        limits.append(float(max_height) * fill / sz)
    fit = min(limits) if limits else 1.0
    clipped = fit < 1.0
    scale = fit if (clipped or allow_upscale) else 1.0
    if not math.isfinite(scale) or scale <= 0.0:
        return None

    # anchor-local offset of the document origin: centre in XY, resting on Z=0
    cx = 0.5 * (lo[0] + hi[0]) * u * scale
    cy = 0.5 * (lo[1] + hi[1]) * u * scale
    cz = lo[2] * u * scale
    off = quat_rotate(arot, (cx, cy, cz))
    return FitTransform(
        translation=(apos[0] - off[0], apos[1] - off[1], apos[2] - off[2]),
        rotation=arot,
        scale=scale * u,
        clipped=clipped,
    )


# ---------------------------------------------------------------------------
# the controller
# ---------------------------------------------------------------------------


class ScaleController:
    """Computes the world transform that miniaturises the user.

    ``p_view = world_scale * p_env + world_offset`` where ``p_env`` is a point
    in environment metres (Y up) and ``p_view`` is in viewer units.

    Typical use from the render loop::

        ctl = ScaleController()
        ctl.set_environment(env)          # picks up user_scale, spawn, bounds
        ...
        if ctl.step(dt):
            update_transform(ctl.world_scale, ctl.world_offset)
            near, far = ctl.clip_planes()
    """

    #: reference spaces: "stage" puts the tracking origin on the floor,
    #: "local" puts it at the user's eyes.
    REFERENCE_SPACES = ("stage", "local")

    def __init__(
        self,
        scale: float = 1.0,
        eye_height: float = DEFAULT_EYE_HEIGHT,
        unit_scale: float = 1.0,
        reference_space: str = "stage",
        duration: Optional[float] = None,
    ) -> None:
        #: viewer units per metre (1.0 for the OpenXR view, 1000.0 for a
        #: millimetre based FreeCAD camera).
        self.unit_scale = float(unit_scale)
        self.eye_height = float(eye_height)
        self.reference_space = reference_space if reference_space in self.REFERENCE_SPACES else "stage"
        self.duration = float(duration) if duration is not None else transition_duration()

        self.environment: Any = None
        self._bounds: Vec3 = (2.0, 2.0, 2.0)
        self._spawn: Vec3 = (0.0, 0.0, 0.0)

        self._scale = max(_EPS, float(scale))
        self._from_scale = self._scale
        self._target_scale = self._scale
        self._t = 1.0

        # the pivot: `_hold_point` (environment metres) is kept at
        # `_hold_view` (viewer space, metres) whatever the scale does.
        self._hold_point: Vec3 = (0.0, 0.0, 0.0)
        self._hold_view: Vec3 = self._rest_view()

        # clip plane tuning
        self.near_min = 0.02
        self.near_max = 0.12
        self.far_min = 8.0
        self.far_margin = 3.0
        self.max_depth_ratio = 4000.0

        self._dirty = True

    # -- environment ------------------------------------------------------

    def _rest_view(self) -> Vec3:
        """Where the pivot sits in viewer space when nothing is held."""
        if self.reference_space == "local":
            return (0.0, -self.eye_height, 0.0)
        return (0.0, 0.0, 0.0)

    def set_environment(self, environment: Any) -> None:
        """Adopt an environment's ``user_scale``, ``spawn`` and ``bounds``.

        Accepts an :class:`xrenv.registry.Environment`, a raw spec dict, or
        ``None`` to fall back to life size in a 2 m room.
        """
        self.environment = environment
        if environment is None:
            self._bounds = (2.0, 2.0, 2.0)
            self._spawn = (0.0, 0.0, 0.0)
            self.set_scale(1.0, animate=False)
            return

        spec = getattr(environment, "spec", None)
        if spec is None and isinstance(environment, dict):
            spec = environment
        spec = spec or {}

        user_scale = getattr(environment, "user_scale", None)
        if user_scale is None:
            user_scale = spec.get("user_scale", 1.0)
        spawn = getattr(environment, "spawn", None)
        if spawn is None:
            spawn = spec.get("spawn", (0.0, 0.0, 0.0))
        bounds = spec.get("bounds", (2.0, 2.0, 2.0))

        try:
            self._bounds = (float(bounds[0]), float(bounds[1]), float(bounds[2]))
        except (TypeError, IndexError, ValueError):
            self._bounds = (2.0, 2.0, 2.0)
        try:
            self._spawn = (float(spawn[0]), float(spawn[1]), float(spawn[2]))
        except (TypeError, IndexError, ValueError):
            self._spawn = (0.0, 0.0, 0.0)

        self._hold_point = self._spawn
        self._hold_view = self._rest_view()
        try:
            self.set_scale(float(user_scale), animate=False)
        except (TypeError, ValueError):
            self.set_scale(1.0, animate=False)

    # -- scale ------------------------------------------------------------

    @property
    def scale(self) -> float:
        """The *user's* scale: 1.0 is life size, 12.0 is twelve times smaller."""
        return self._scale

    @property
    def target_scale(self) -> float:
        return self._target_scale

    @property
    def animating(self) -> bool:
        return self._t < 1.0

    @property
    def world_scale(self) -> float:
        """What the environment and document transform is scaled by.

        Grows with :attr:`scale` — shrinking the user means growing the world.
        """
        return self._scale

    @property
    def world_offset(self) -> Vec3:
        """Translation applied after :attr:`world_scale`, in viewer units."""
        s = self._scale
        u = self.unit_scale
        return (
            (self._hold_view[0] - s * self._hold_point[0]) * u,
            (self._hold_view[1] - s * self._hold_point[1]) * u,
            (self._hold_view[2] - s * self._hold_point[2]) * u,
        )

    def set_scale(self, scale: float, animate: bool = True) -> None:
        """Set the target user scale, optionally easing into it."""
        try:
            target = float(scale)
        except (TypeError, ValueError):
            return
        if not math.isfinite(target) or target <= 0.0:
            return
        target = max(1e-3, min(1000.0, target))
        self._target_scale = target
        if not animate or self.duration <= 0.0 or abs(target - self._scale) < 1e-9:
            self._from_scale = target
            self._scale = target
            self._t = 1.0
            self._dirty = True
            return
        self._from_scale = self._scale
        self._t = 0.0
        self._dirty = True

    def scale_about_point(self, point: Sequence[float], scale: float, animate: bool = True) -> None:
        """Change scale while keeping ``point`` (environment metres) fixed.

        Pass the user's feet to shrink in place, or a picked point on the
        model to zoom towards it.
        """
        try:
            p = (float(point[0]), float(point[1]), float(point[2]))
        except (TypeError, IndexError, ValueError):
            return
        s = self._scale
        # where that point currently appears in viewer space (metres)
        view = (
            self._hold_view[0] + s * (p[0] - self._hold_point[0]),
            self._hold_view[1] + s * (p[1] - self._hold_point[1]),
            self._hold_view[2] + s * (p[2] - self._hold_point[2]),
        )
        self._hold_point = p
        self._hold_view = view
        self.set_scale(scale, animate=animate)

    def reset_pivot(self) -> None:
        """Return the pivot to the environment spawn point."""
        self._hold_point = self._spawn
        self._hold_view = self._rest_view()
        self._dirty = True

    def teleport(self, point: Sequence[float]) -> None:
        """Place the user's feet at ``point`` (environment metres)."""
        try:
            p = (float(point[0]), float(point[1]), float(point[2]))
        except (TypeError, IndexError, ValueError):
            return
        self._hold_point = p
        self._hold_view = self._rest_view()
        self._dirty = True

    def step(self, dt: float) -> bool:
        """Advance the animation by ``dt`` seconds.

        Returns True when the transform changed and the caller should push a
        new world matrix.
        """
        changed = self._dirty
        self._dirty = False
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return changed
        if not math.isfinite(dt) or dt < 0.0:
            dt = 0.0
        if self._t < 1.0 and self.duration > 0.0:
            self._t = min(1.0, self._t + dt / self.duration)
            k = _smoothstep(self._t)
            # geometric interpolation: doubling the scale always feels the same
            a = math.log(max(_EPS, self._from_scale))
            b = math.log(max(_EPS, self._target_scale))
            self._scale = math.exp(a + (b - a) * k)
            if self._t >= 1.0:
                self._scale = self._target_scale
            changed = True
        return changed

    def finish(self) -> None:
        """Jump straight to the target scale."""
        if self._t < 1.0:
            self._t = 1.0
            self._scale = self._target_scale
            self._dirty = True

    # -- derived quantities ------------------------------------------------

    @property
    def apparent_height(self) -> float:
        """The user's height in environment metres — 0.15 m inside the X1C."""
        return self.eye_height / max(_EPS, self._scale)

    def eye_position(self) -> Vec3:
        """Where the user's eyes are, in environment metres."""
        return (
            self._hold_point[0],
            self._hold_point[1] + self.eye_height / max(_EPS, self._scale),
            self._hold_point[2],
        )

    def world_matrix(self) -> List[List[float]]:
        """The full world transform as a row-major 4x4 matrix."""
        s = self._scale * self.unit_scale
        o = self.world_offset
        return [
            [s, 0.0, 0.0, o[0]],
            [0.0, s, 0.0, o[1]],
            [0.0, 0.0, s, o[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def to_view(self, point: Sequence[float]) -> Vec3:
        """Map an environment point into viewer space."""
        m = self.world_matrix()
        return (
            m[0][0] * float(point[0]) + m[0][3],
            m[1][1] * float(point[1]) + m[1][3],
            m[2][2] * float(point[2]) + m[2][3],
        )

    def clip_planes(self) -> Tuple[float, float]:
        """Near and far clip distances, in the viewer's own units.

        The world grows by :attr:`world_scale`, so the far plane has to grow
        with it or the far wall of the chamber gets cut away.  The near plane
        stays at a fixed, small real-world distance so the shrunk user's hands
        are never clipped — but it is also never allowed to fall below
        ``far / max_depth_ratio``, which is what keeps the depth buffer from
        z-fighting once the world is twelve times larger.
        """
        diag = math.sqrt(sum(v * v for v in self._bounds))
        far = max(self.far_min, diag * self._scale * self.far_margin)
        near = far / self.max_depth_ratio
        near = max(self.near_min, min(self.near_max, near))
        if far <= near * 2.0:
            far = near * 2.0
        return (near * self.unit_scale, far * self.unit_scale)

    # -- document placement ------------------------------------------------

    def primary_anchor(self) -> Any:
        env = self.environment
        if env is None:
            return None
        getter = getattr(env, "primary_anchor", None)
        if callable(getter):
            return getter()
        return None

    def fit_document_to_anchor(
        self,
        bbox: Optional[Sequence[Sequence[float]]],
        anchor: Any = None,
        unit_scale: float = 0.001,
        fill: float = 0.8,
        allow_upscale: bool = False,
    ) -> Optional[FitTransform]:
        """Drop the current document onto ``anchor`` (default: the primary one).

        Returns ``None`` when the document extent is unknown (``bbox is
        None``) or no usable anchor exists.
        """
        if anchor is None:
            anchor = self.primary_anchor()
        if anchor is None:
            return None
        headroom = None
        if self._bounds and self._spawn:
            headroom = max(0.0, self._bounds[2] - self._spawn[1])
            if headroom <= 0.0:
                headroom = None
        return fit_document_to_anchor(
            bbox,
            anchor,
            unit_scale=unit_scale,
            fill=fill,
            allow_upscale=allow_upscale,
            max_height=headroom,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<ScaleController scale=%.3f -> %.3f offset=%s>" % (
            self._scale,
            self._target_scale,
            tuple(round(v, 4) for v in self.world_offset),
        )
