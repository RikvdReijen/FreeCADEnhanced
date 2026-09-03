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
"""Authoring helpers shared by the built-in environment generators.

Everything here produces plain spec dictionaries (§2 of the architecture
document): metres, **Y up**, right handed.  Nothing in this module imports
FreeCAD or pivy.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "SpecBuilder",
    "IDENT",
    "rot_x",
    "rot_y",
    "rot_z",
    "rot_axis",
    "rot_mul",
    "look_rotation",
    "PLATE_ROT",
    "slot_profile",
    "angle_profile",
    "channel_profile",
    "srgb",
]

IDENT: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)


def rot_axis(axis: Sequence[float], degrees: float) -> Tuple[float, float, float, float]:
    """Quaternion (xyzw) for a rotation about ``axis``."""
    ax, ay, az = (float(v) for v in axis)
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-12:
        return IDENT
    a = math.radians(float(degrees)) * 0.5
    s = math.sin(a) / n
    q = (ax * s, ay * s, az * s, math.cos(a))
    return tuple(0.0 if abs(v) < 1e-15 else v for v in q)  # type: ignore[return-value]


def rot_x(degrees: float) -> Tuple[float, float, float, float]:
    return rot_axis((1.0, 0.0, 0.0), degrees)


def rot_y(degrees: float) -> Tuple[float, float, float, float]:
    return rot_axis((0.0, 1.0, 0.0), degrees)


def rot_z(degrees: float) -> Tuple[float, float, float, float]:
    return rot_axis((0.0, 0.0, 1.0), degrees)


def rot_mul(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float, float]:
    """``a`` applied after ``b``."""
    ax, ay, az, aw = (float(v) for v in a)
    bx, by, bz, bw = (float(v) for v in b)
    q = (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return tuple(0.0 if abs(v / n) < 1e-15 else v / n for v in q)  # type: ignore[return-value]


#: Rotation putting a flat XY primitive (``plane``, ``grid``, ``honeycomb``,
#: ``extrusion``, ``text``) into a horizontal, face-up position: local +Z
#: becomes world +Y.  Also the canonical rotation for a build-plate anchor.
PLATE_ROT = rot_x(-90.0)


def look_rotation(direction: Sequence[float]) -> Tuple[float, float, float, float]:
    """Rotation taking local +Y onto ``direction``.

    Handy for aiming cylinders (whose axis is +Y) along an arbitrary vector.
    """
    dx, dy, dz = (float(v) for v in direction)
    n = math.sqrt(dx * dx + dy * dy + dz * dz)
    if n < 1e-12:
        return IDENT
    dx, dy, dz = dx / n, dy / n, dz / n
    # rotation from (0,1,0) to d
    dot = dy
    if dot > 1.0 - 1e-12:
        return IDENT
    if dot < -1.0 + 1e-12:
        return rot_x(180.0)
    ax, ay, az = (dz, 0.0, -dx)  # cross((0,1,0), d)
    an = math.sqrt(ax * ax + ay * ay + az * az)
    return rot_axis((ax / an, ay / an, az / an), math.degrees(math.acos(max(-1.0, min(1.0, dot)))))


def srgb(r: float, g: float, b: float, a: float = 1.0) -> List[float]:
    """Convert an sRGB 0..1 colour to the linear values the spec stores."""

    def _lin(c: float) -> float:
        c = max(0.0, min(1.0, float(c)))
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return [round(_lin(r), 6), round(_lin(g), 6), round(_lin(b), 6), max(0.0, min(1.0, float(a)))]


# ---------------------------------------------------------------------------
# stock profiles for the `extrusion` primitive
# ---------------------------------------------------------------------------


def slot_profile(size: float = 0.020, slot: float = 0.0062, depth: float = 0.0055,
                 chamfer: float = 0.0018) -> List[List[float]]:
    """A 2020-style aluminium extrusion cross section with four T-slots.

    The profile is a chamfered square with a slot cut into the middle of each
    face, wound counter-clockwise in the XY plane.
    """
    h = size * 0.5
    s = slot * 0.5
    d = depth
    c = chamfer
    pts: List[List[float]] = []
    # four sides, counter-clockwise, starting at the bottom right chamfer
    corners = ((1, -1), (1, 1), (-1, 1), (-1, -1))
    # per side: (start corner, end corner, slot axis)
    sides = (
        ((h, -h + c), (h, h - c), (h - d, 0.0), 0),   # +X face
        ((h - c, h), (-h + c, h), (0.0, h - d), 1),   # +Y face
        ((-h, h - c), (-h, -h + c), (-h + d, 0.0), 0),  # -X face
        ((-h + c, -h), (h - c, -h), (0.0, -h + d), 1),  # -Y face
    )
    for (sx, sy), (ex, ey), (bx, by), axis in sides:
        pts.append([round(sx, 6), round(sy, 6)])
        if axis == 0:  # slot runs along Y on a vertical face
            sgn = 1.0 if sy < ey else -1.0
            pts.append([round(sx, 6), round(-s * sgn, 6)])
            pts.append([round(bx, 6), round(-s * sgn, 6)])
            pts.append([round(bx, 6), round(s * sgn, 6)])
            pts.append([round(sx, 6), round(s * sgn, 6)])
        else:          # slot runs along X on a horizontal face
            sgn = 1.0 if sx < ex else -1.0
            pts.append([round(-s * sgn, 6), round(sy, 6)])
            pts.append([round(-s * sgn, 6), round(by, 6)])
            pts.append([round(s * sgn, 6), round(by, 6)])
            pts.append([round(s * sgn, 6), round(sy, 6)])
        pts.append([round(ex, 6), round(ey, 6)])
    return pts


def angle_profile(leg_a: float, leg_b: float, thickness: float) -> List[List[float]]:
    """An L angle bracket cross section, corner at the origin, CCW."""
    t = thickness
    return [
        [0.0, 0.0],
        [round(leg_a, 6), 0.0],
        [round(leg_a, 6), round(t, 6)],
        [round(t, 6), round(t, 6)],
        [round(t, 6), round(leg_b, 6)],
        [0.0, round(leg_b, 6)],
    ]


def channel_profile(width: float, height: float, thickness: float) -> List[List[float]]:
    """A U channel cross section, centred on X, opening towards +Y, CCW."""
    hw = width * 0.5
    t = thickness
    return [
        [round(-hw, 6), 0.0],
        [round(hw, 6), 0.0],
        [round(hw, 6), round(height, 6)],
        [round(hw - t, 6), round(height, 6)],
        [round(hw - t, 6), round(t, 6)],
        [round(-hw + t, 6), round(t, 6)],
        [round(-hw + t, 6), round(height, 6)],
        [round(-hw, 6), round(height, 6)],
    ]


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------


def _v3(v: Optional[Sequence[float]]) -> List[float]:
    if v is None:
        return [0.0, 0.0, 0.0]
    return [float(v[0]), float(v[1]), float(v[2])]


def _v4(v: Optional[Sequence[float]]) -> List[float]:
    if v is None:
        return [0.0, 0.0, 0.0, 1.0]
    return [float(v[0]), float(v[1]), float(v[2]), float(v[3])]


class SpecBuilder:
    """Accumulates materials, lights, anchors and nodes into a spec dict."""

    def __init__(
        self,
        env_id: str,
        name: str,
        description: str = "",
        user_scale: float = 1.0,
        bounds: Sequence[float] = (2.0, 2.0, 2.0),
        spawn: Sequence[float] = (0.0, 0.0, 0.0),
        ambient: Sequence[float] = (0.06, 0.06, 0.07),
    ) -> None:
        self.id = env_id
        self.name = name
        self.description = description
        self.user_scale = float(user_scale)
        self.bounds = _v3(bounds)
        self.spawn = _v3(spawn)
        self.ambient = _v3(ambient)
        self._materials: List[Dict[str, Any]] = []
        self._material_index: Dict[str, int] = {}
        self._lights: List[Dict[str, Any]] = []
        self._anchors: Dict[str, Dict[str, Any]] = {}
        self._nodes: List[Dict[str, Any]] = []
        self._names: Dict[str, int] = {}

    # -- materials ---------------------------------------------------------

    def material(
        self,
        name: str,
        base_color: Sequence[float],
        metallic: float = 0.0,
        roughness: float = 0.6,
        emissive: Sequence[float] = (0.0, 0.0, 0.0),
        texture: Optional[str] = None,
    ) -> int:
        """Add (or fetch) a material and return its index."""
        if name in self._material_index:
            return self._material_index[name]
        color = [float(c) for c in base_color]
        while len(color) < 4:
            color.append(1.0)
        self._materials.append(
            {
                "name": name,
                "base_color": [max(0.0, min(1.0, c)) for c in color[:4]],
                "metallic": max(0.0, min(1.0, float(metallic))),
                "roughness": max(0.0, min(1.0, float(roughness))),
                "emissive": _v3(emissive),
                "texture": texture,
            }
        )
        idx = len(self._materials) - 1
        self._material_index[name] = idx
        return idx

    def mat(self, name: str) -> int:
        """Index of a previously declared material."""
        return self._material_index[name]

    # -- lights ------------------------------------------------------------

    def directional(self, direction: Sequence[float], color: Sequence[float] = (1, 1, 1),
                    intensity: float = 1.0) -> None:
        self._lights.append(
            {
                "type": "directional",
                "direction": _v3(direction),
                "position": [0.0, 0.0, 0.0],
                "color": _v3(color),
                "intensity": float(intensity),
                "cutoff_deg": 45.0,
                "range": 0.0,
            }
        )

    def point(self, position: Sequence[float], color: Sequence[float] = (1, 1, 1),
              intensity: float = 1.0, rng: float = 4.0) -> None:
        self._lights.append(
            {
                "type": "point",
                "direction": [0.0, -1.0, 0.0],
                "position": _v3(position),
                "color": _v3(color),
                "intensity": float(intensity),
                "cutoff_deg": 45.0,
                "range": float(rng),
            }
        )

    def spot(self, position: Sequence[float], direction: Sequence[float],
             color: Sequence[float] = (1, 1, 1), intensity: float = 1.0,
             cutoff_deg: float = 45.0, rng: float = 4.0) -> None:
        self._lights.append(
            {
                "type": "spot",
                "direction": _v3(direction),
                "position": _v3(position),
                "color": _v3(color),
                "intensity": float(intensity),
                "cutoff_deg": float(cutoff_deg),
                "range": float(rng),
            }
        )

    # -- anchors -----------------------------------------------------------

    def anchor(self, name: str, position: Sequence[float], size: Sequence[float],
               rotation: Sequence[float] = PLATE_ROT) -> None:
        self._anchors[name] = {
            "position": _v3(position),
            "rotation": _v4(rotation),
            "size": [float(size[0]), float(size[1])],
        }

    # -- nodes -------------------------------------------------------------

    def _unique(self, name: str) -> str:
        n = self._names.get(name, 0)
        self._names[name] = n + 1
        return name if n == 0 else "%s_%d" % (name, n)

    def group(self, name: str, at: Sequence[float] = (0, 0, 0),
              rot: Sequence[float] = IDENT, scale: Sequence[float] = (1, 1, 1),
              parent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add an empty transform node and return it, to be used as a parent."""
        node: Dict[str, Any] = {
            "name": self._unique(name),
            "translation": _v3(at),
            "rotation": _v4(rot),
            "scale": _v3(scale),
            "children": [],
        }
        self._attach(node, parent)
        return node

    def _attach(self, node: Dict[str, Any], parent: Optional[Dict[str, Any]]) -> None:
        if parent is None:
            self._nodes.append(node)
        else:
            parent.setdefault("children", []).append(node)

    def add(
        self,
        name: str,
        shape: Dict[str, Any],
        material: int,
        at: Sequence[float] = (0, 0, 0),
        rot: Sequence[float] = IDENT,
        scale: Sequence[float] = (1, 1, 1),
        parent: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "name": self._unique(name),
            "shape": shape,
            "material": int(material),
            "translation": _v3(at),
            "rotation": _v4(rot),
            "scale": _v3(scale),
        }
        self._attach(node, parent)
        return node

    # -- primitive shorthands ---------------------------------------------

    def box(self, name: str, size: Sequence[float], at: Sequence[float], material: int,
            rot: Sequence[float] = IDENT, parent: Optional[Dict[str, Any]] = None):
        return self.add(name, {"type": "box", "size": [float(s) for s in size]},
                        material, at, rot, parent=parent)

    def cylinder(self, name: str, radius: float, height: float, at: Sequence[float],
                 material: int, rot: Sequence[float] = IDENT, sides: int = 20,
                 caps: bool = True, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "cylinder", "radius": float(radius), "height": float(height),
             "sides": int(sides), "caps": bool(caps)},
            material, at, rot, parent=parent,
        )

    def cone(self, name: str, radius: float, top_radius: float, height: float,
             at: Sequence[float], material: int, rot: Sequence[float] = IDENT,
             sides: int = 20, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "cone", "radius": float(radius), "top_radius": float(top_radius),
             "height": float(height), "sides": int(sides)},
            material, at, rot, parent=parent,
        )

    def sphere(self, name: str, radius: float, at: Sequence[float], material: int,
               rings: int = 10, sectors: int = 16, rot: Sequence[float] = IDENT,
               parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "sphere", "radius": float(radius), "rings": int(rings),
             "sectors": int(sectors)},
            material, at, rot, parent=parent,
        )

    def torus(self, name: str, radius: float, tube_radius: float, at: Sequence[float],
              material: int, rot: Sequence[float] = IDENT, sides: int = 10,
              rings: int = 24, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "torus", "radius": float(radius), "tube_radius": float(tube_radius),
             "sides": int(sides), "rings": int(rings)},
            material, at, rot, parent=parent,
        )

    def tube(self, name: str, path: Sequence[Sequence[float]], radius: float, material: int,
             at: Sequence[float] = (0, 0, 0), sides: int = 8,
             rot: Sequence[float] = IDENT, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "tube", "path": [[round(float(c), 6) for c in p] for p in path],
             "radius": float(radius), "sides": int(sides)},
            material, at, rot, parent=parent,
        )

    def plane(self, name: str, size: Sequence[float], at: Sequence[float], material: int,
              rot: Sequence[float] = PLATE_ROT, subdiv: Sequence[int] = (1, 1),
              parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "plane", "size": [float(size[0]), float(size[1])],
             "subdiv": [int(subdiv[0]), int(subdiv[1])]},
            material, at, rot, parent=parent,
        )

    def extrusion(self, name: str, profile: Sequence[Sequence[float]], height: float,
                  at: Sequence[float], material: int, rot: Sequence[float] = IDENT,
                  closed: bool = True, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "extrusion", "profile": [[float(p[0]), float(p[1])] for p in profile],
             "height": float(height), "closed": bool(closed)},
            material, at, rot, parent=parent,
        )

    def grid(self, name: str, size: Sequence[float], pitch: float, bar: float,
             at: Sequence[float], material: int, rot: Sequence[float] = PLATE_ROT,
             parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "grid", "size": [float(size[0]), float(size[1])],
             "pitch": float(pitch), "bar": float(bar)},
            material, at, rot, parent=parent,
        )

    def honeycomb(self, name: str, size: Sequence[float], cell: float, wall: float,
                  height: float, at: Sequence[float], material: int,
                  rot: Sequence[float] = PLATE_ROT, parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "honeycomb", "size": [float(size[0]), float(size[1])],
             "cell": float(cell), "wall": float(wall), "height": float(height)},
            material, at, rot, parent=parent,
        )

    def text(self, name: str, string: str, height: float, depth: float,
             at: Sequence[float], material: int, rot: Sequence[float] = IDENT,
             parent: Optional[Dict[str, Any]] = None):
        return self.add(
            name,
            {"type": "text", "string": str(string), "height": float(height),
             "depth": float(depth)},
            material, at, rot, parent=parent,
        )

    # -- repetition helpers ------------------------------------------------

    def repeat(self, fn, positions: Iterable[Sequence[float]], *args, **kwargs) -> List[Dict[str, Any]]:
        """Call ``fn(at=p, ...)`` once per position; returns the nodes."""
        return [fn(at=p, *args, **kwargs) for p in positions]

    def screws(self, name: str, positions: Iterable[Sequence[float]], radius: float,
               height: float, material: int, rot: Sequence[float] = IDENT,
               head_ratio: float = 1.8, parent: Optional[Dict[str, Any]] = None) -> int:
        """Scatter cap-head screws; returns how many nodes were added."""
        n = 0
        for p in positions:
            self.cylinder(name, radius * head_ratio, height * 0.45, p, material,
                          rot=rot, sides=8, parent=parent)
            n += 1
        return n

    # -- output ------------------------------------------------------------

    def part_count(self) -> int:
        def _count(nodes: Sequence[Dict[str, Any]]) -> int:
            total = 0
            for n in nodes:
                if n.get("shape") is not None:
                    total += 1
                total += _count(n.get("children") or [])
            return total

        return _count(self._nodes)

    def build(self) -> Dict[str, Any]:
        from ..spec import SPEC_VERSION

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": SPEC_VERSION,
            "user_scale": self.user_scale,
            "bounds": list(self.bounds),
            "spawn": list(self.spawn),
            "ambient": list(self.ambient),
            "lights": list(self._lights),
            "materials": list(self._materials),
            "anchors": dict(self._anchors),
            "nodes": list(self._nodes),
        }
