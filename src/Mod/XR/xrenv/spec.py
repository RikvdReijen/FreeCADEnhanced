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
"""Declarative environment spec, validation and the reference tessellator.

This module implements §2 of ``Resources/doc/ARCHITECTURE.md``.

It is deliberately dependency free (stdlib + :mod:`math` only) and must never
import ``FreeCAD``, ``FreeCADGui`` or ``pivy`` at module level: the Quest C++
renderer mirrors :func:`tessellate_shape` triangle for triangle, and the unit
tests run without FreeCAD installed.

Conventions
-----------
* **Y up, metres, right handed** (OpenXR).  The Coin/FreeCAD Z-up conversion
  lives in exactly one place, :func:`xrenv.builder.spec_to_coin_matrix`.
* Every primitive is **centred on the node origin** unless the table in the
  architecture document states otherwise.
* Primitives with a natural axis (``cylinder``, ``cone``, ``sphere``,
  ``torus``) are aligned with **+Y**.  Flat primitives (``plane``, ``grid``,
  ``honeycomb``, ``extrusion``, ``text``) live in the **XY plane** and grow
  along **+Z**, matching the ``plane`` definition in the spec table.
* Triangles are **counter-clockwise when seen from outside** (CCW front
  faces) and vertex normals point **outward**.
* The environment interior box spans ``x in [-w/2, w/2]``, ``z in [-d/2, d/2]``
  and ``y in [0, h]`` for ``bounds = [w, d, h]``.  ``y = 0`` is the floor the
  user is spawned on.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "SPEC_VERSION",
    "SHAPE_TYPES",
    "LIGHT_TYPES",
    "Material",
    "Light",
    "Anchor",
    "Node",
    "EnvironmentSpec",
    "validate_spec",
    "load_spec",
    "save_spec",
    "spec_to_json",
    "spec_from_json",
    "tessellate_shape",
    "tessellate_spec",
    "iter_nodes",
    "count_parts",
    "spec_bounds",
    "compose_trs",
    "mat_mul",
    "mat_apply_point",
    "mat_apply_dir",
    "quat_to_mat",
    "TessellationError",
]

SPEC_VERSION = 1

SHAPE_TYPES = frozenset(
    {
        "box",
        "cylinder",
        "cone",
        "sphere",
        "torus",
        "tube",
        "plane",
        "extrusion",
        "grid",
        "honeycomb",
        "text",
        "mesh",
    }
)

LIGHT_TYPES = frozenset({"directional", "point", "spot"})

_EPS = 1e-9


class TessellationError(ValueError):
    """Raised when a shape cannot be tessellated (degenerate parameters)."""


# ---------------------------------------------------------------------------
# tiny linear algebra (row-major 4x4, column vectors: p' = M * p)
# ---------------------------------------------------------------------------


def quat_to_mat(q: Sequence[float]) -> List[List[float]]:
    """Convert an ``[x, y, z, w]`` quaternion to a 3x3 row-major matrix."""
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < _EPS:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    x, y, z, w = x / n, y / n, z / n, w / n
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
        [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
        [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
    ]


def compose_trs(
    translation: Sequence[float],
    rotation: Sequence[float],
    scale: Sequence[float],
) -> List[List[float]]:
    """Build the local matrix ``T * R * S`` as a row-major 4x4."""
    r = quat_to_mat(rotation)
    sx, sy, sz = (float(v) for v in scale)
    tx, ty, tz = (float(v) for v in translation)
    return [
        [r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
        [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
        [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


IDENTITY4: List[List[float]] = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def mat_mul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> List[List[float]]:
    """Multiply two row-major 4x4 matrices."""
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def mat_apply_point(m: Sequence[Sequence[float]], p: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = p[0], p[1], p[2]
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def mat_apply_dir(m: Sequence[Sequence[float]], v: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = v[0], v[1], v[2]
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z,
        m[1][0] * x + m[1][1] * y + m[1][2] * z,
        m[2][0] * x + m[2][1] * y + m[2][2] * z,
    )


def _norm3(v: Sequence[float]) -> Tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < _EPS:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# ---------------------------------------------------------------------------
# dataclass mirrors of the JSON spec
# ---------------------------------------------------------------------------


@dataclass
class Material:
    name: str = "default"
    base_color: List[float] = field(default_factory=lambda: [0.8, 0.8, 0.8, 1.0])
    metallic: float = 0.0
    roughness: float = 0.6
    emissive: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    texture: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Material":
        return Material(
            name=d.get("name", "default"),
            base_color=list(d.get("base_color", [0.8, 0.8, 0.8, 1.0])),
            metallic=float(d.get("metallic", 0.0)),
            roughness=float(d.get("roughness", 0.6)),
            emissive=list(d.get("emissive", [0.0, 0.0, 0.0])),
            texture=d.get("texture"),
        )


@dataclass
class Light:
    type: str = "directional"
    direction: List[float] = field(default_factory=lambda: [0.0, -1.0, 0.0])
    position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    color: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    intensity: float = 1.0
    cutoff_deg: float = 45.0
    range: float = 4.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Light":
        return Light(
            type=d.get("type", "directional"),
            direction=list(d.get("direction", [0.0, -1.0, 0.0])),
            position=list(d.get("position", [0.0, 0.0, 0.0])),
            color=list(d.get("color", [1.0, 1.0, 1.0])),
            intensity=float(d.get("intensity", 1.0)),
            cutoff_deg=float(d.get("cutoff_deg", 45.0)),
            range=float(d.get("range", 4.0)),
        )


@dataclass
class Anchor:
    """A named place in the environment a document can be dropped onto.

    ``name`` is the key the anchor is stored under in ``spec["anchors"]``; it
    is *not* serialised into the anchor object itself.
    """

    name: str = ""
    position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    size: List[float] = field(default_factory=lambda: [1.0, 1.0])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position": list(self.position),
            "rotation": list(self.rotation),
            "size": list(self.size),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any], name: str = "") -> "Anchor":
        return Anchor(
            name=name or d.get("name", ""),
            position=list(d.get("position", [0.0, 0.0, 0.0])),
            rotation=list(d.get("rotation", [0.0, 0.0, 0.0, 1.0])),
            size=list(d.get("size", [1.0, 1.0])),
        )


@dataclass
class Node:
    name: str = ""
    shape: Optional[Dict[str, Any]] = None
    material: Optional[int] = None
    translation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    children: List["Node"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name}
        if self.shape is not None:
            d["shape"] = self.shape
        if self.material is not None:
            d["material"] = self.material
        d["translation"] = list(self.translation)
        d["rotation"] = list(self.rotation)
        d["scale"] = list(self.scale)
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Node":
        return Node(
            name=d.get("name", ""),
            shape=d.get("shape"),
            material=d.get("material"),
            translation=list(d.get("translation", [0.0, 0.0, 0.0])),
            rotation=list(d.get("rotation", [0.0, 0.0, 0.0, 1.0])),
            scale=list(d.get("scale", [1.0, 1.0, 1.0])),
            children=[Node.from_dict(c) for c in d.get("children", [])],
        )


@dataclass
class EnvironmentSpec:
    id: str = ""
    name: str = ""
    description: str = ""
    version: int = SPEC_VERSION
    user_scale: float = 1.0
    bounds: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    spawn: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    ambient: List[float] = field(default_factory=lambda: [0.1, 0.1, 0.1])
    lights: List[Light] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    anchors: Dict[str, Anchor] = field(default_factory=dict)
    nodes: List[Node] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "user_scale": self.user_scale,
            "bounds": list(self.bounds),
            "spawn": list(self.spawn),
            "ambient": list(self.ambient),
            "lights": [l.to_dict() for l in self.lights],
            "materials": [m.to_dict() for m in self.materials],
            "anchors": {k: v.to_dict() for k, v in self.anchors.items()},
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EnvironmentSpec":
        return EnvironmentSpec(
            id=d.get("id", ""),
            name=d.get("name", ""),
            description=d.get("description", ""),
            version=int(d.get("version", SPEC_VERSION)),
            user_scale=float(d.get("user_scale", 1.0)),
            bounds=list(d.get("bounds", [1.0, 1.0, 1.0])),
            spawn=list(d.get("spawn", [0.0, 0.0, 0.0])),
            ambient=list(d.get("ambient", [0.1, 0.1, 0.1])),
            lights=[Light.from_dict(x) for x in d.get("lights", [])],
            materials=[Material.from_dict(x) for x in d.get("materials", [])],
            anchors={k: Anchor.from_dict(v, k) for k, v in (d.get("anchors") or {}).items()},
            nodes=[Node.from_dict(x) for x in d.get("nodes", [])],
        )


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _check_vec(problems: List[str], where: str, value: Any, n: int, required: bool = True) -> bool:
    if value is None:
        if required:
            problems.append("%s: missing" % where)
        return False
    if not isinstance(value, (list, tuple)) or len(value) != n:
        problems.append("%s: expected %d numbers, got %r" % (where, n, value))
        return False
    for i, v in enumerate(value):
        if not _is_num(v):
            problems.append("%s[%d]: not a finite number (%r)" % (where, i, v))
            return False
    return True


def _check_positive(problems: List[str], where: str, value: Any, allow_zero: bool = False) -> bool:
    if not _is_num(value):
        problems.append("%s: not a finite number (%r)" % (where, value))
        return False
    if float(value) < 0.0 or (not allow_zero and float(value) <= 0.0):
        problems.append("%s: must be > 0 (got %r)" % (where, value))
        return False
    return True


def _check_quat(problems: List[str], where: str, value: Any) -> None:
    if not _check_vec(problems, where, value, 4):
        return
    n = math.sqrt(sum(float(v) * float(v) for v in value))
    if abs(n - 1.0) > 1e-3:
        problems.append("%s: quaternion is not normalised (|q| = %.6f)" % (where, n))


def _validate_shape(problems: List[str], where: str, shape: Any) -> None:
    if not isinstance(shape, dict):
        problems.append("%s: shape must be an object" % where)
        return
    stype = shape.get("type")
    if stype not in SHAPE_TYPES:
        problems.append("%s: unknown shape type %r" % (where, stype))
        return
    w = "%s.%s" % (where, stype)

    if stype == "box":
        if _check_vec(problems, w + ".size", shape.get("size"), 3):
            for i, v in enumerate(shape["size"]):
                _check_positive(problems, "%s.size[%d]" % (w, i), v)

    elif stype == "cylinder":
        _check_positive(problems, w + ".radius", shape.get("radius"))
        _check_positive(problems, w + ".height", shape.get("height"))
        sides = int(shape.get("sides", 24))
        if sides < 3:
            problems.append("%s.sides: need at least 3 (got %d)" % (w, sides))

    elif stype == "cone":
        _check_positive(problems, w + ".radius", shape.get("radius"), allow_zero=True)
        _check_positive(problems, w + ".top_radius", shape.get("top_radius", 0.0), allow_zero=True)
        _check_positive(problems, w + ".height", shape.get("height"))
        if float(shape.get("radius", 0.0)) <= 0.0 and float(shape.get("top_radius", 0.0)) <= 0.0:
            problems.append("%s: both radii are zero" % w)
        if int(shape.get("sides", 24)) < 3:
            problems.append("%s.sides: need at least 3" % w)

    elif stype == "sphere":
        _check_positive(problems, w + ".radius", shape.get("radius"))
        if int(shape.get("rings", 12)) < 2:
            problems.append("%s.rings: need at least 2" % w)
        if int(shape.get("sectors", 24)) < 3:
            problems.append("%s.sectors: need at least 3" % w)

    elif stype == "torus":
        _check_positive(problems, w + ".radius", shape.get("radius"))
        _check_positive(problems, w + ".tube_radius", shape.get("tube_radius"))
        if int(shape.get("sides", 12)) < 3:
            problems.append("%s.sides: need at least 3" % w)
        if int(shape.get("rings", 24)) < 3:
            problems.append("%s.rings: need at least 3" % w)

    elif stype == "tube":
        path = shape.get("path")
        if not isinstance(path, (list, tuple)) or len(path) < 2:
            problems.append("%s.path: need at least 2 points" % w)
        else:
            for i, p in enumerate(path):
                _check_vec(problems, "%s.path[%d]" % (w, i), p, 3)
        _check_positive(problems, w + ".radius", shape.get("radius"))
        if int(shape.get("sides", 12)) < 3:
            problems.append("%s.sides: need at least 3" % w)

    elif stype == "plane":
        if _check_vec(problems, w + ".size", shape.get("size"), 2):
            for i, v in enumerate(shape["size"]):
                _check_positive(problems, "%s.size[%d]" % (w, i), v)
        sub = shape.get("subdiv", [1, 1])
        if _check_vec(problems, w + ".subdiv", sub, 2):
            if int(sub[0]) < 1 or int(sub[1]) < 1:
                problems.append("%s.subdiv: must be >= 1" % w)

    elif stype == "extrusion":
        prof = shape.get("profile")
        if not isinstance(prof, (list, tuple)) or len(prof) < 2:
            problems.append("%s.profile: need at least 2 points" % w)
        else:
            for i, p in enumerate(prof):
                _check_vec(problems, "%s.profile[%d]" % (w, i), p, 2)
            if shape.get("closed", True) and len(prof) < 3:
                problems.append("%s.profile: a closed profile needs at least 3 points" % w)
        _check_positive(problems, w + ".height", shape.get("height"))

    elif stype == "grid":
        if _check_vec(problems, w + ".size", shape.get("size"), 2):
            for i, v in enumerate(shape["size"]):
                _check_positive(problems, "%s.size[%d]" % (w, i), v)
        _check_positive(problems, w + ".pitch", shape.get("pitch"))
        _check_positive(problems, w + ".bar", shape.get("bar"))
        if _is_num(shape.get("pitch")) and _is_num(shape.get("bar")):
            if float(shape["bar"]) >= float(shape["pitch"]):
                problems.append("%s: bar (%r) must be smaller than pitch (%r)"
                                % (w, shape["bar"], shape["pitch"]))

    elif stype == "honeycomb":
        if _check_vec(problems, w + ".size", shape.get("size"), 2):
            for i, v in enumerate(shape["size"]):
                _check_positive(problems, "%s.size[%d]" % (w, i), v)
        _check_positive(problems, w + ".cell", shape.get("cell"))
        _check_positive(problems, w + ".wall", shape.get("wall"))
        _check_positive(problems, w + ".height", shape.get("height"))
        if _is_num(shape.get("cell")) and _is_num(shape.get("wall")):
            if float(shape["wall"]) >= float(shape["cell"]) * 0.5:
                problems.append("%s: wall must be well below half the cell size" % w)

    elif stype == "text":
        s = shape.get("string")
        if not isinstance(s, str) or not s:
            problems.append("%s.string: must be a non-empty string" % w)
        _check_positive(problems, w + ".height", shape.get("height"))
        _check_positive(problems, w + ".depth", shape.get("depth"))

    elif stype == "mesh":
        pos = shape.get("positions")
        idx = shape.get("indices")
        nrm = shape.get("normals")
        if not isinstance(pos, (list, tuple)) or len(pos) < 9 or len(pos) % 3 != 0:
            problems.append("%s.positions: need a flat list of 3*N >= 9 floats" % w)
            return
        if not all(_is_num(v) for v in pos):
            problems.append("%s.positions: contains non-finite values" % w)
        if not isinstance(idx, (list, tuple)) or len(idx) < 3 or len(idx) % 3 != 0:
            problems.append("%s.indices: need a flat list of 3*T indices" % w)
            return
        nverts = len(pos) // 3
        for i in idx:
            if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= nverts:
                problems.append("%s.indices: index %r out of range 0..%d" % (w, i, nverts - 1))
                break
        if nrm is not None and len(nrm) != len(pos):
            problems.append("%s.normals: length must match positions" % w)
        uvs = shape.get("uvs")
        if uvs is not None and len(uvs) != nverts * 2:
            problems.append("%s.uvs: length must be 2*N" % w)


def _validate_node(problems: List[str], where: str, node: Any, nmaterials: int, depth: int = 0) -> None:
    if not isinstance(node, dict):
        problems.append("%s: node must be an object" % where)
        return
    if depth > 32:
        problems.append("%s: node tree nested too deeply (>32)" % where)
        return
    name = node.get("name", "")
    if not isinstance(name, str):
        problems.append("%s.name: must be a string" % where)
    _check_vec(problems, where + ".translation", node.get("translation", [0, 0, 0]), 3)
    _check_quat(problems, where + ".rotation", node.get("rotation", [0, 0, 0, 1]))
    if _check_vec(problems, where + ".scale", node.get("scale", [1, 1, 1]), 3):
        for i, v in enumerate(node.get("scale", [1, 1, 1])):
            if abs(float(v)) < 1e-12:
                problems.append("%s.scale[%d]: zero scale collapses the node" % (where, i))
    mat = node.get("material")
    if mat is not None:
        if not isinstance(mat, int) or isinstance(mat, bool):
            problems.append("%s.material: must be an integer index or null" % where)
        elif mat < 0 or mat >= nmaterials:
            problems.append("%s.material: index %d out of range (0..%d)"
                            % (where, mat, nmaterials - 1))
    shape = node.get("shape")
    children = node.get("children") or []
    if shape is None and not children:
        problems.append("%s: node has neither a shape nor children" % where)
    if shape is not None:
        _validate_shape(problems, where, shape)
    if not isinstance(children, list):
        problems.append("%s.children: must be a list" % where)
        return
    for i, c in enumerate(children):
        _validate_node(problems, "%s.children[%d]" % (where, i), c, nmaterials, depth + 1)


def validate_spec(spec: Any) -> List[str]:
    """Validate an environment spec.

    Returns a list of human readable problem descriptions.  An empty list
    means the spec conforms to §2 of the architecture document.
    """
    problems: List[str] = []
    if not isinstance(spec, dict):
        return ["spec: must be a JSON object"]

    ident = spec.get("id")
    if not isinstance(ident, str) or not ident:
        problems.append("id: must be a non-empty string")
    elif not all(c.isalnum() or c in "_-" for c in ident):
        problems.append("id: only alphanumerics, '_' and '-' are allowed (got %r)" % ident)

    if not isinstance(spec.get("name"), str) or not spec.get("name"):
        problems.append("name: must be a non-empty string")
    if not isinstance(spec.get("description", ""), str):
        problems.append("description: must be a string")

    ver = spec.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        problems.append("version: must be an integer")
    elif ver != SPEC_VERSION:
        problems.append("version: unsupported spec version %r (expected %d)" % (ver, SPEC_VERSION))

    us = spec.get("user_scale")
    if not _is_num(us):
        problems.append("user_scale: must be a finite number")
    elif float(us) <= 0.0:
        problems.append("user_scale: must be > 0 (got %r)" % us)
    elif float(us) > 1000.0:
        problems.append("user_scale: implausibly large (%r)" % us)

    bounds_ok = _check_vec(problems, "bounds", spec.get("bounds"), 3)
    if bounds_ok:
        for i, v in enumerate(spec["bounds"]):
            _check_positive(problems, "bounds[%d]" % i, v)

    spawn_ok = _check_vec(problems, "spawn", spec.get("spawn"), 3)
    if bounds_ok and spawn_ok:
        w, d, h = (float(v) for v in spec["bounds"])
        x, y, z = (float(v) for v in spec["spawn"])
        if not (-w / 2.0 <= x <= w / 2.0):
            problems.append("spawn: x=%.4f outside the interior (+/-%.4f)" % (x, w / 2.0))
        if not (0.0 <= y <= h):
            problems.append("spawn: y=%.4f outside the interior (0..%.4f)" % (y, h))
        if not (-d / 2.0 <= z <= d / 2.0):
            problems.append("spawn: z=%.4f outside the interior (+/-%.4f)" % (z, d / 2.0))

    _check_vec(problems, "ambient", spec.get("ambient", [0, 0, 0]), 3)

    lights = spec.get("lights")
    if not isinstance(lights, list):
        problems.append("lights: must be a list")
    else:
        if not lights:
            problems.append("lights: at least one light is required")
        for i, l in enumerate(lights):
            wl = "lights[%d]" % i
            if not isinstance(l, dict):
                problems.append("%s: must be an object" % wl)
                continue
            lt = l.get("type")
            if lt not in LIGHT_TYPES:
                problems.append("%s.type: unknown light type %r" % (wl, lt))
                continue
            _check_vec(problems, wl + ".color", l.get("color", [1, 1, 1]), 3)
            if not _is_num(l.get("intensity", 1.0)):
                problems.append("%s.intensity: must be a finite number" % wl)
            if lt == "directional":
                if _check_vec(problems, wl + ".direction", l.get("direction"), 3):
                    if math.sqrt(sum(float(v) ** 2 for v in l["direction"])) < 1e-6:
                        problems.append("%s.direction: zero length" % wl)
            else:
                _check_vec(problems, wl + ".position", l.get("position"), 3)
                if lt == "spot":
                    if _check_vec(problems, wl + ".direction", l.get("direction"), 3):
                        if math.sqrt(sum(float(v) ** 2 for v in l["direction"])) < 1e-6:
                            problems.append("%s.direction: zero length" % wl)
                    co = l.get("cutoff_deg", 45.0)
                    if not _is_num(co) or not (0.0 < float(co) < 90.0):
                        problems.append("%s.cutoff_deg: must be in (0, 90)" % wl)

    materials = spec.get("materials")
    nmaterials = 0
    if not isinstance(materials, list):
        problems.append("materials: must be a list")
    else:
        nmaterials = len(materials)
        if not materials:
            problems.append("materials: at least one material is required")
        seen = set()
        for i, m in enumerate(materials):
            wm = "materials[%d]" % i
            if not isinstance(m, dict):
                problems.append("%s: must be an object" % wm)
                continue
            mn = m.get("name")
            if not isinstance(mn, str) or not mn:
                problems.append("%s.name: must be a non-empty string" % wm)
            elif mn in seen:
                problems.append("%s.name: duplicate material name %r" % (wm, mn))
            else:
                seen.add(mn)
            if _check_vec(problems, wm + ".base_color", m.get("base_color"), 4):
                for j, v in enumerate(m["base_color"]):
                    if not (0.0 <= float(v) <= 1.0):
                        problems.append("%s.base_color[%d]: must be in 0..1" % (wm, j))
            for key in ("metallic", "roughness"):
                v = m.get(key, 0.0)
                if not _is_num(v) or not (0.0 <= float(v) <= 1.0):
                    problems.append("%s.%s: must be in 0..1 (got %r)" % (wm, key, v))
            _check_vec(problems, wm + ".emissive", m.get("emissive", [0, 0, 0]), 3)
            tex = m.get("texture")
            if tex is not None and not isinstance(tex, str):
                problems.append("%s.texture: must be a string or null" % wm)

    anchors = spec.get("anchors")
    if not isinstance(anchors, dict):
        problems.append("anchors: must be an object")
    else:
        if not anchors:
            problems.append("anchors: at least one anchor is required")
        for key, a in anchors.items():
            wa = "anchors[%r]" % key
            if not isinstance(key, str) or not key:
                problems.append("%s: anchor key must be a non-empty string" % wa)
            if not isinstance(a, dict):
                problems.append("%s: must be an object" % wa)
                continue
            _check_vec(problems, wa + ".position", a.get("position"), 3)
            _check_quat(problems, wa + ".rotation", a.get("rotation", [0, 0, 0, 1]))
            if _check_vec(problems, wa + ".size", a.get("size"), 2):
                for i, v in enumerate(a["size"]):
                    _check_positive(problems, "%s.size[%d]" % (wa, i), v)

    nodes = spec.get("nodes")
    if not isinstance(nodes, list):
        problems.append("nodes: must be a list")
    else:
        if not nodes:
            problems.append("nodes: the environment has no geometry")
        for i, n in enumerate(nodes):
            _validate_node(problems, "nodes[%d]" % i, n, nmaterials)

    return problems


# ---------------------------------------------------------------------------
# JSON round trip
# ---------------------------------------------------------------------------


def _round_floats(obj: Any, ndigits: int = 6) -> Any:
    """Recursively round floats so serialisation is byte-for-byte stable."""
    if isinstance(obj, float):
        r = round(obj, ndigits)
        # normalise -0.0 -> 0.0 so regeneration is deterministic
        return 0.0 if r == 0.0 else r
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, ndigits) for v in obj]
    return obj


def spec_to_json(spec: Dict[str, Any], indent: int = 2, ndigits: int = 6) -> str:
    """Serialise a spec deterministically (sorted keys, rounded floats)."""
    return json.dumps(_round_floats(spec, ndigits), indent=indent, sort_keys=True) + "\n"


def spec_from_json(text: str) -> Dict[str, Any]:
    """Parse a spec from JSON text."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("environment spec must be a JSON object")
    return data


def save_spec(spec: Dict[str, Any], path: str, indent: int = 2) -> str:
    """Write ``spec`` to ``path`` (creating parent directories).  Returns path."""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(spec_to_json(spec, indent=indent))
    return path


def load_spec(path: str) -> Dict[str, Any]:
    """Read a spec from a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        return spec_from_json(fh.read())


# ---------------------------------------------------------------------------
# mesh accumulator
# ---------------------------------------------------------------------------


class _Mesh:
    """Accumulates positions / normals / uvs / triangle indices."""

    __slots__ = ("pos", "nrm", "uv", "idx")

    def __init__(self) -> None:
        self.pos: List[float] = []
        self.nrm: List[float] = []
        self.uv: List[float] = []
        self.idx: List[int] = []

    def vertex(self, p: Sequence[float], n: Sequence[float], uv: Sequence[float]) -> int:
        i = len(self.pos) // 3
        self.pos.extend((float(p[0]), float(p[1]), float(p[2])))
        self.nrm.extend((float(n[0]), float(n[1]), float(n[2])))
        self.uv.extend((float(uv[0]), float(uv[1])))
        return i

    def tri(self, a: int, b: int, c: int) -> None:
        self.idx.extend((a, b, c))

    def quad(self, a: int, b: int, c: int, d: int) -> None:
        self.idx.extend((a, b, c, a, c, d))

    def add_quad(
        self,
        p0: Sequence[float],
        p1: Sequence[float],
        p2: Sequence[float],
        p3: Sequence[float],
        n0: Sequence[float],
        n1: Optional[Sequence[float]] = None,
        n2: Optional[Sequence[float]] = None,
        n3: Optional[Sequence[float]] = None,
        uvs: Optional[Sequence[Sequence[float]]] = None,
    ) -> None:
        """Add a CCW quad ``p0,p1,p2,p3`` seen from the ``n`` side."""
        if n1 is None:
            n1 = n2 = n3 = n0
        if uvs is None:
            uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        a = self.vertex(p0, n0, uvs[0])
        b = self.vertex(p1, n1, uvs[1])
        c = self.vertex(p2, n2, uvs[2])
        d = self.vertex(p3, n3, uvs[3])
        self.quad(a, b, c, d)

    def add_tri(
        self,
        p0: Sequence[float],
        p1: Sequence[float],
        p2: Sequence[float],
        n0: Sequence[float],
        n1: Optional[Sequence[float]] = None,
        n2: Optional[Sequence[float]] = None,
        uvs: Optional[Sequence[Sequence[float]]] = None,
    ) -> None:
        if n1 is None:
            n1 = n2 = n0
        if uvs is None:
            uvs = ((0.0, 0.0), (1.0, 0.0), (0.5, 1.0))
        a = self.vertex(p0, n0, uvs[0])
        b = self.vertex(p1, n1, uvs[1])
        c = self.vertex(p2, n2, uvs[2])
        self.tri(a, b, c)

    def extend_mesh(self, other: "_Mesh") -> None:
        base = len(self.pos) // 3
        self.pos.extend(other.pos)
        self.nrm.extend(other.nrm)
        self.uv.extend(other.uv)
        self.idx.extend(i + base for i in other.idx)

    def transformed_copy_into(
        self,
        target: "_Mesh",
        origin: Sequence[float],
        ex: Sequence[float],
        ey: Sequence[float],
        ez: Sequence[float],
    ) -> None:
        """Append ``self`` into ``target`` rotated by the orthonormal frame."""
        base = len(target.pos) // 3
        for i in range(0, len(self.pos), 3):
            x, y, z = self.pos[i], self.pos[i + 1], self.pos[i + 2]
            target.pos.extend(
                (
                    origin[0] + ex[0] * x + ey[0] * y + ez[0] * z,
                    origin[1] + ex[1] * x + ey[1] * y + ez[1] * z,
                    origin[2] + ex[2] * x + ey[2] * y + ez[2] * z,
                )
            )
            nx, ny, nz = self.nrm[i], self.nrm[i + 1], self.nrm[i + 2]
            target.nrm.extend(
                (
                    ex[0] * nx + ey[0] * ny + ez[0] * nz,
                    ex[1] * nx + ey[1] * ny + ez[1] * nz,
                    ex[2] * nx + ey[2] * ny + ez[2] * nz,
                )
            )
        target.uv.extend(self.uv)
        target.idx.extend(i + base for i in self.idx)

    def result(self) -> Tuple[List[float], List[float], List[float], List[int]]:
        return self.pos, self.nrm, self.uv, self.idx


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

_BOX_FACES = (
    # normal, tangent, bitangent  (tangent x bitangent == normal)
    ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
    ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ((0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
)


def _box_into(m: _Mesh, sx: float, sy: float, sz: float,
              centre: Sequence[float] = (0.0, 0.0, 0.0)) -> None:
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    cx, cy, cz = centre
    half = (hx, hy, hz)
    for n, t, b in _BOX_FACES:
        # face centre = normal component * half extent
        fc = (cx + n[0] * half[0], cy + n[1] * half[1], cz + n[2] * half[2])
        # tangent / bitangent scaled by the half extent along their axis
        ts = abs(t[0]) * hx + abs(t[1]) * hy + abs(t[2]) * hz
        bs = abs(b[0]) * hx + abs(b[1]) * hy + abs(b[2]) * hz
        tv = (t[0] * ts, t[1] * ts, t[2] * ts)
        bv = (b[0] * bs, b[1] * bs, b[2] * bs)
        p0 = (fc[0] - tv[0] - bv[0], fc[1] - tv[1] - bv[1], fc[2] - tv[2] - bv[2])
        p1 = (fc[0] + tv[0] - bv[0], fc[1] + tv[1] - bv[1], fc[2] + tv[2] - bv[2])
        p2 = (fc[0] + tv[0] + bv[0], fc[1] + tv[1] + bv[1], fc[2] + tv[2] + bv[2])
        p3 = (fc[0] - tv[0] + bv[0], fc[1] - tv[1] + bv[1], fc[2] - tv[2] + bv[2])
        m.add_quad(p0, p1, p2, p3, n)


def _tess_box(shape: Dict[str, Any]) -> _Mesh:
    sx, sy, sz = (float(v) for v in shape["size"])
    if min(sx, sy, sz) <= 0.0:
        raise TessellationError("box: all size components must be > 0")
    m = _Mesh()
    _box_into(m, sx, sy, sz)
    return m


def _cone_into(m: _Mesh, r0: float, r1: float, height: float, sides: int, caps: bool) -> None:
    """Truncated cone along +Y, from ``-h/2`` (radius r0) to ``+h/2`` (radius r1)."""
    h2 = height * 0.5
    ang = [2.0 * math.pi * i / sides for i in range(sides + 1)]
    cs = [(math.cos(a), math.sin(a)) for a in ang]
    dr = r0 - r1
    slope_len = math.sqrt(height * height + dr * dr)
    if slope_len < _EPS:
        raise TessellationError("cone: degenerate profile")
    ny = dr / slope_len
    nr = height / slope_len

    bottom_deg = r0 <= _EPS
    top_deg = r1 <= _EPS

    for i in range(sides):
        c0, s0 = cs[i]
        c1, s1 = cs[i + 1]
        u0, u1 = i / sides, (i + 1) / sides
        n0 = (nr * c0, ny, nr * s0)
        n1 = (nr * c1, ny, nr * s1)
        b0 = (r0 * c0, -h2, r0 * s0)
        b1 = (r0 * c1, -h2, r0 * s1)
        t0 = (r1 * c0, h2, r1 * s0)
        t1 = (r1 * c1, h2, r1 * s1)
        if top_deg or bottom_deg:
            # the apex normal is the *normalised* mean of the two side normals
            am = math.atan2(s0 + s1, c0 + c1)
            napex = (nr * math.cos(am), ny, nr * math.sin(am))
        if top_deg:
            m.add_tri(b0, (0.0, h2, 0.0), b1, n0, napex, n1,
                      ((u0, 0.0), (0.5 * (u0 + u1), 1.0), (u1, 0.0)))
        elif bottom_deg:
            m.add_tri((0.0, -h2, 0.0), t0, t1, napex, n0, n1,
                      ((0.5 * (u0 + u1), 0.0), (u0, 1.0), (u1, 1.0)))
        else:
            m.add_quad(b0, t0, t1, b1, n0, n0, n1, n1,
                       ((u0, 0.0), (u0, 1.0), (u1, 1.0), (u1, 0.0)))

    if caps:
        if not bottom_deg:
            nb = (0.0, -1.0, 0.0)
            for i in range(sides):
                c0, s0 = cs[i]
                c1, s1 = cs[i + 1]
                m.add_tri((0.0, -h2, 0.0), (r0 * c0, -h2, r0 * s0), (r0 * c1, -h2, r0 * s1),
                          nb, nb, nb,
                          ((0.5, 0.5), (0.5 + 0.5 * c0, 0.5 + 0.5 * s0), (0.5 + 0.5 * c1, 0.5 + 0.5 * s1)))
        if not top_deg:
            nt = (0.0, 1.0, 0.0)
            for i in range(sides):
                c0, s0 = cs[i]
                c1, s1 = cs[i + 1]
                m.add_tri((0.0, h2, 0.0), (r1 * c1, h2, r1 * s1), (r1 * c0, h2, r1 * s0),
                          nt, nt, nt,
                          ((0.5, 0.5), (0.5 + 0.5 * c1, 0.5 + 0.5 * s1), (0.5 + 0.5 * c0, 0.5 + 0.5 * s0)))


def _tess_cylinder(shape: Dict[str, Any]) -> _Mesh:
    r = float(shape["radius"])
    h = float(shape["height"])
    sides = int(shape.get("sides", 24))
    caps = bool(shape.get("caps", True))
    if r <= 0.0 or h <= 0.0:
        raise TessellationError("cylinder: radius and height must be > 0")
    if sides < 3:
        raise TessellationError("cylinder: sides must be >= 3")
    m = _Mesh()
    _cone_into(m, r, r, h, sides, caps)
    return m


def _tess_cone(shape: Dict[str, Any]) -> _Mesh:
    r0 = float(shape.get("radius", 0.0))
    r1 = float(shape.get("top_radius", 0.0))
    h = float(shape["height"])
    sides = int(shape.get("sides", 24))
    caps = bool(shape.get("caps", True))
    if h <= 0.0:
        raise TessellationError("cone: height must be > 0")
    if r0 <= 0.0 and r1 <= 0.0:
        raise TessellationError("cone: at least one radius must be > 0")
    if sides < 3:
        raise TessellationError("cone: sides must be >= 3")
    m = _Mesh()
    _cone_into(m, r0, r1, h, sides, caps)
    return m


def _tess_sphere(shape: Dict[str, Any]) -> _Mesh:
    r = float(shape["radius"])
    rings = int(shape.get("rings", 12))
    sectors = int(shape.get("sectors", 24))
    if r <= 0.0:
        raise TessellationError("sphere: radius must be > 0")
    if rings < 2 or sectors < 3:
        raise TessellationError("sphere: need rings >= 2 and sectors >= 3")
    m = _Mesh()
    # vertex grid, theta 0..pi (north pole first), phi 0..2pi
    grid: List[List[int]] = []
    for i in range(rings + 1):
        theta = math.pi * i / rings
        st, ct = math.sin(theta), math.cos(theta)
        row: List[int] = []
        for j in range(sectors + 1):
            phi = 2.0 * math.pi * j / sectors
            n = (st * math.cos(phi), ct, st * math.sin(phi))
            p = (r * n[0], r * n[1], r * n[2])
            row.append(m.vertex(p, n, (j / sectors, 1.0 - i / rings)))
        grid.append(row)
    for i in range(rings):
        for j in range(sectors):
            a = grid[i][j]
            b = grid[i][j + 1]
            c = grid[i + 1][j + 1]
            d = grid[i + 1][j]
            if i == 0:
                m.tri(a, c, d)
            elif i == rings - 1:
                m.tri(a, b, c)
            else:
                m.quad(a, b, c, d)
    return m


def _tess_torus(shape: Dict[str, Any]) -> _Mesh:
    R = float(shape["radius"])
    r = float(shape["tube_radius"])
    sides = int(shape.get("sides", 12))
    rings = int(shape.get("rings", 24))
    if R <= 0.0 or r <= 0.0:
        raise TessellationError("torus: radius and tube_radius must be > 0")
    if sides < 3 or rings < 3:
        raise TessellationError("torus: need sides >= 3 and rings >= 3")
    m = _Mesh()
    grid: List[List[int]] = []
    for i in range(rings + 1):
        phi = 2.0 * math.pi * i / rings
        cp, sp = math.cos(phi), math.sin(phi)
        row: List[int] = []
        for j in range(sides + 1):
            psi = 2.0 * math.pi * j / sides
            cq, sq = math.cos(psi), math.sin(psi)
            n = (cq * cp, sq, cq * sp)
            p = ((R + r * cq) * cp, r * sq, (R + r * cq) * sp)
            row.append(m.vertex(p, n, (i / rings, j / sides)))
        grid.append(row)
    for i in range(rings):
        for j in range(sides):
            m.quad(grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j])
    return m


def _tess_plane(shape: Dict[str, Any]) -> _Mesh:
    sx, sy = (float(v) for v in shape["size"])
    su, sv = (int(v) for v in shape.get("subdiv", [1, 1]))
    if sx <= 0.0 or sy <= 0.0:
        raise TessellationError("plane: size must be > 0")
    if su < 1 or sv < 1:
        raise TessellationError("plane: subdiv must be >= 1")
    m = _Mesh()
    n = (0.0, 0.0, 1.0)
    grid: List[List[int]] = []
    for j in range(sv + 1):
        y = -sy * 0.5 + sy * j / sv
        row: List[int] = []
        for i in range(su + 1):
            x = -sx * 0.5 + sx * i / su
            row.append(m.vertex((x, y, 0.0), n, (i / su, j / sv)))
        grid.append(row)
    for j in range(sv):
        for i in range(su):
            m.quad(grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i])
    return m


def _polygon_area2(profile: Sequence[Sequence[float]]) -> float:
    a = 0.0
    n = len(profile)
    for i in range(n):
        x0, y0 = profile[i]
        x1, y1 = profile[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy) -> bool:
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def triangulate_polygon(profile: Sequence[Sequence[float]]) -> List[Tuple[int, int, int]]:
    """Ear clipping triangulation of a simple CCW polygon.

    Returns index triples into ``profile``, wound CCW.
    """
    n = len(profile)
    if n < 3:
        return []
    idx = list(range(n))
    if _polygon_area2(profile) < 0.0:
        idx.reverse()
    tris: List[Tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n * n + 64:
        guard += 1
        ear_found = False
        cnt = len(idx)
        for k in range(cnt):
            i0 = idx[(k - 1) % cnt]
            i1 = idx[k]
            i2 = idx[(k + 1) % cnt]
            ax, ay = profile[i0]
            bx, by = profile[i1]
            cx, cy = profile[i2]
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if cross <= 1e-14:
                continue  # reflex or collinear
            bad = False
            for other in idx:
                if other in (i0, i1, i2):
                    continue
                px, py = profile[other]
                if _point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
                    bad = True
                    break
            if bad:
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            ear_found = True
            break
        if not ear_found:
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _tess_extrusion(shape: Dict[str, Any]) -> _Mesh:
    profile = [(float(p[0]), float(p[1])) for p in shape["profile"]]
    height = float(shape["height"])
    closed = bool(shape.get("closed", True))
    if height <= 0.0:
        raise TessellationError("extrusion: height must be > 0")
    # drop consecutive duplicates
    clean: List[Tuple[float, float]] = []
    for p in profile:
        if not clean or abs(p[0] - clean[-1][0]) > 1e-12 or abs(p[1] - clean[-1][1]) > 1e-12:
            clean.append(p)
    if closed and len(clean) > 1 and abs(clean[0][0] - clean[-1][0]) < 1e-12 \
            and abs(clean[0][1] - clean[-1][1]) < 1e-12:
        clean.pop()
    profile = clean
    if closed and len(profile) < 3:
        raise TessellationError("extrusion: closed profile needs >= 3 distinct points")
    if not closed and len(profile) < 2:
        raise TessellationError("extrusion: open profile needs >= 2 distinct points")
    if closed:
        area2 = _polygon_area2(profile)
        if abs(area2) < 1e-14:
            raise TessellationError("extrusion: closed profile encloses no area")
        if area2 < 0.0:
            profile.reverse()

    z0, z1 = -height * 0.5, height * 0.5
    m = _Mesh()
    n = len(profile)
    segs = n if closed else n - 1
    total = 0.0
    lens = []
    for i in range(segs):
        x0, y0 = profile[i]
        x1, y1 = profile[(i + 1) % n]
        d = math.hypot(x1 - x0, y1 - y0)
        lens.append(d)
        total += d
    if total < _EPS:
        raise TessellationError("extrusion: degenerate profile")
    acc = 0.0
    for i in range(segs):
        x0, y0 = profile[i]
        x1, y1 = profile[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ln = lens[i]
        if ln < 1e-12:
            continue
        nx, ny = dy / ln, -dx / ln
        nv = (nx, ny, 0.0)
        u0 = acc / total
        acc += ln
        u1 = acc / total
        m.add_quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1), nv,
                   uvs=((u0, 0.0), (u1, 0.0), (u1, 1.0), (u0, 1.0)))
    if closed:
        tris = triangulate_polygon(profile)
        if not tris:
            raise TessellationError("extrusion: profile could not be triangulated")
        for a, b, c in tris:
            pa, pb, pc = profile[a], profile[b], profile[c]
            m.add_tri((pa[0], pa[1], z1), (pb[0], pb[1], z1), (pc[0], pc[1], z1), (0.0, 0.0, 1.0),
                      uvs=(pa, pb, pc))
            m.add_tri((pa[0], pa[1], z0), (pc[0], pc[1], z0), (pb[0], pb[1], z0), (0.0, 0.0, -1.0),
                      uvs=(pa, pc, pb))
    return m


def _tess_tube(shape: Dict[str, Any]) -> _Mesh:
    raw = [(float(p[0]), float(p[1]), float(p[2])) for p in shape["path"]]
    radius = float(shape["radius"])
    sides = int(shape.get("sides", 12))
    caps = bool(shape.get("caps", True))
    if radius <= 0.0:
        raise TessellationError("tube: radius must be > 0")
    if sides < 3:
        raise TessellationError("tube: sides must be >= 3")
    # drop duplicate points
    path: List[Tuple[float, float, float]] = []
    for p in raw:
        if not path or max(abs(p[k] - path[-1][k]) for k in range(3)) > 1e-9:
            path.append(p)
    if len(path) < 2:
        raise TessellationError("tube: path needs >= 2 distinct points")
    npts = len(path)

    # tangents
    tangents: List[Tuple[float, float, float]] = []
    for i in range(npts):
        if i == 0:
            t = tuple(path[1][k] - path[0][k] for k in range(3))
        elif i == npts - 1:
            t = tuple(path[-1][k] - path[-2][k] for k in range(3))
        else:
            a = _norm3(tuple(path[i][k] - path[i - 1][k] for k in range(3)))
            b = _norm3(tuple(path[i + 1][k] - path[i][k] for k in range(3)))
            t = tuple(a[k] + b[k] for k in range(3))
            if math.sqrt(sum(c * c for c in t)) < 1e-9:  # 180 degree reversal
                t = b
        tangents.append(_norm3(t))

    # initial normal: any vector perpendicular to t0
    t0 = tangents[0]
    ref = (0.0, 1.0, 0.0) if abs(t0[1]) < 0.9 else (1.0, 0.0, 0.0)
    n0 = _norm3(_cross(ref, t0))
    normals = [n0]
    # rotation minimising frames (double reflection)
    for i in range(1, npts):
        prev_t = tangents[i - 1]
        cur_t = tangents[i]
        prev_n = normals[-1]
        v = tuple(cur_t[k] - prev_t[k] for k in range(3))
        c1 = _dot(v, v)
        if c1 < 1e-16:
            normals.append(prev_n)
            continue
        # reflect prev_n across the bisecting plane
        d = _dot(v, prev_n)
        nl = tuple(prev_n[k] - (2.0 / c1) * d * v[k] for k in range(3))
        tl = tuple(prev_t[k] - (2.0 / c1) * _dot(v, prev_t) * v[k] for k in range(3))
        v2 = tuple(cur_t[k] - tl[k] for k in range(3))
        c2 = _dot(v2, v2)
        if c2 < 1e-16:
            normals.append(_norm3(nl))
            continue
        d2 = _dot(v2, nl)
        nn = tuple(nl[k] - (2.0 / c2) * d2 * v2[k] for k in range(3))
        # re-orthogonalise against the tangent to stop drift
        dp = _dot(nn, cur_t)
        nn = tuple(nn[k] - dp * cur_t[k] for k in range(3))
        normals.append(_norm3(nn))

    m = _Mesh()
    # arc length for v coordinate
    arc = [0.0]
    for i in range(1, npts):
        arc.append(arc[-1] + math.sqrt(sum((path[i][k] - path[i - 1][k]) ** 2 for k in range(3))))
    total = arc[-1] or 1.0

    rings: List[List[int]] = []
    frames: List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = []
    for i in range(npts):
        t = tangents[i]
        nvec = normals[i]
        bvec = _cross(t, nvec)
        frames.append((nvec, bvec))
        row: List[int] = []
        for j in range(sides + 1):
            a = 2.0 * math.pi * j / sides
            ca, sa = math.cos(a), math.sin(a)
            nrm = (nvec[0] * ca + bvec[0] * sa,
                   nvec[1] * ca + bvec[1] * sa,
                   nvec[2] * ca + bvec[2] * sa)
            p = (path[i][0] + radius * nrm[0],
                 path[i][1] + radius * nrm[1],
                 path[i][2] + radius * nrm[2])
            row.append(m.vertex(p, nrm, (j / sides, arc[i] / total)))
        rings.append(row)

    for i in range(npts - 1):
        for j in range(sides):
            m.quad(rings[i][j], rings[i][j + 1], rings[i + 1][j + 1], rings[i + 1][j])

    if caps:
        # start cap, normal -t0
        t = tangents[0]
        nvec, bvec = frames[0]
        cn = (-t[0], -t[1], -t[2])
        for j in range(sides):
            a0 = 2.0 * math.pi * j / sides
            a1 = 2.0 * math.pi * (j + 1) / sides
            p0 = tuple(path[0][k] + radius * (nvec[k] * math.cos(a0) + bvec[k] * math.sin(a0))
                       for k in range(3))
            p1 = tuple(path[0][k] + radius * (nvec[k] * math.cos(a1) + bvec[k] * math.sin(a1))
                       for k in range(3))
            m.add_tri(path[0], p1, p0, cn)
        t = tangents[-1]
        nvec, bvec = frames[-1]
        for j in range(sides):
            a0 = 2.0 * math.pi * j / sides
            a1 = 2.0 * math.pi * (j + 1) / sides
            p0 = tuple(path[-1][k] + radius * (nvec[k] * math.cos(a0) + bvec[k] * math.sin(a0))
                       for k in range(3))
            p1 = tuple(path[-1][k] + radius * (nvec[k] * math.cos(a1) + bvec[k] * math.sin(a1))
                       for k in range(3))
            m.add_tri(path[-1], p0, p1, t)
    return m


def _tess_grid(shape: Dict[str, Any]) -> _Mesh:
    sx, sy = (float(v) for v in shape["size"])
    pitch = float(shape["pitch"])
    bar = float(shape["bar"])
    if sx <= 0.0 or sy <= 0.0:
        raise TessellationError("grid: size must be > 0")
    if pitch <= 0.0 or bar <= 0.0:
        raise TessellationError("grid: pitch and bar must be > 0")
    if bar >= pitch:
        raise TessellationError("grid: bar must be smaller than pitch")
    m = _Mesh()
    nx = int(math.floor(sx / (2.0 * pitch))) if pitch > 0 else 0
    ny = int(math.floor(sy / (2.0 * pitch))) if pitch > 0 else 0
    # bars running along X, spaced in Y
    for j in range(-ny, ny + 1):
        y = j * pitch
        if abs(y) > sy * 0.5 + 1e-12:
            continue
        _box_into(m, sx, bar, bar, (0.0, y, 0.0))
    # bars running along Y, spaced in X
    for i in range(-nx, nx + 1):
        x = i * pitch
        if abs(x) > sx * 0.5 + 1e-12:
            continue
        _box_into(m, bar, sy, bar, (x, 0.0, 0.0))
    if not m.idx:
        raise TessellationError("grid: pitch too large for the requested size")
    return m


def _tess_honeycomb(shape: Dict[str, Any]) -> _Mesh:
    sx, sy = (float(v) for v in shape["size"])
    cell = float(shape["cell"])
    wall = float(shape["wall"])
    height = float(shape["height"])
    if min(sx, sy, cell, wall, height) <= 0.0:
        raise TessellationError("honeycomb: all parameters must be > 0")
    if wall >= cell * 0.5:
        raise TessellationError("honeycomb: wall must be well below half the cell size")

    # pointy top hexagons, `cell` is the across-flats width
    R = cell / math.sqrt(3.0)           # circumradius == side length
    row_pitch = 1.5 * R
    col_pitch = cell
    ncols = int(math.ceil(sx / col_pitch)) + 2
    nrows = int(math.ceil(sy / row_pitch)) + 2

    corners = [(R * math.sin(math.pi / 3.0 * k), R * math.cos(math.pi / 3.0 * k)) for k in range(6)]

    m = _Mesh()
    seen: set = set()
    hx, hy = sx * 0.5, sy * 0.5
    for r in range(-nrows // 2 - 1, nrows // 2 + 2):
        cy = r * row_pitch
        xoff = (col_pitch * 0.5) if (r & 1) else 0.0
        for c in range(-ncols // 2 - 1, ncols // 2 + 2):
            cx = c * col_pitch + xoff
            if abs(cx) > hx + col_pitch or abs(cy) > hy + row_pitch:
                continue
            for k in range(6):
                ax, ay = corners[k]
                bx, by = corners[(k + 1) % 6]
                p0 = (cx + ax, cy + ay)
                p1 = (cx + bx, cy + by)
                mx, my = (p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5
                if abs(mx) > hx or abs(my) > hy:
                    continue
                key = (round(mx / (R * 0.01)), round(my / (R * 0.01)))
                if key in seen:
                    continue
                seen.add(key)
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                ln = math.hypot(dx, dy)
                if ln < 1e-12:
                    continue
                ux, uy = dx / ln, dy / ln
                # wall segment: length ln, thickness `wall`, height along Z
                bar = _Mesh()
                _box_into(bar, ln + wall, wall, height)
                bar.transformed_copy_into(
                    m, (mx, my, 0.0), (ux, uy, 0.0), (-uy, ux, 0.0), (0.0, 0.0, 1.0)
                )
    if not m.idx:
        raise TessellationError("honeycomb: cell size too large for the requested area")
    return m


# ---------------------------------------------------------------------------
# stroked block font for `text`
# ---------------------------------------------------------------------------
#
# Glyphs are polylines in a 0..1 (width) x 0..1 (height) box.  Rendering
# extrudes every segment into a small box, so no font dependency is needed.

_GLYPH_W = 0.56
_GLYPHS: Dict[str, Tuple[Tuple[Tuple[float, float], ...], ...]] = {
    " ": (),
    "A": (((0, 0), (0.28, 1), (0.56, 0)), ((0.12, 0.36), (0.44, 0.36))),
    "B": (((0, 0), (0, 1), (0.4, 1), (0.52, 0.86), (0.4, 0.55), (0, 0.55)),
          ((0.4, 0.55), (0.56, 0.36), (0.42, 0), (0, 0))),
    "C": (((0.56, 0.86), (0.36, 1), (0.14, 1), (0, 0.78), (0, 0.22), (0.14, 0), (0.36, 0), (0.56, 0.14)),),
    "D": (((0, 0), (0, 1), (0.34, 1), (0.56, 0.74), (0.56, 0.26), (0.34, 0), (0, 0)),),
    "E": (((0.54, 1), (0, 1), (0, 0), (0.54, 0)), ((0, 0.52), (0.42, 0.52))),
    "F": (((0.54, 1), (0, 1), (0, 0)), ((0, 0.54), (0.42, 0.54))),
    "G": (((0.56, 0.86), (0.36, 1), (0.14, 1), (0, 0.78), (0, 0.22), (0.14, 0), (0.4, 0),
           (0.56, 0.18), (0.56, 0.44), (0.3, 0.44)),),
    "H": (((0, 0), (0, 1)), ((0.56, 0), (0.56, 1)), ((0, 0.52), (0.56, 0.52))),
    "I": (((0.06, 0), (0.5, 0)), ((0.28, 0), (0.28, 1)), ((0.06, 1), (0.5, 1))),
    "J": (((0.44, 1), (0.44, 0.2), (0.3, 0), (0.12, 0), (0, 0.18)),),
    "K": (((0, 0), (0, 1)), ((0.54, 1), (0.04, 0.48)), ((0.16, 0.62), (0.56, 0))),
    "L": (((0, 1), (0, 0), (0.52, 0)),),
    "M": (((0, 0), (0, 1), (0.28, 0.5), (0.56, 1), (0.56, 0)),),
    "N": (((0, 0), (0, 1), (0.56, 0), (0.56, 1)),),
    "O": (((0.14, 0), (0, 0.22), (0, 0.78), (0.14, 1), (0.42, 1), (0.56, 0.78),
           (0.56, 0.22), (0.42, 0), (0.14, 0)),),
    "P": (((0, 0), (0, 1), (0.4, 1), (0.56, 0.82), (0.42, 0.56), (0, 0.56)),),
    "Q": (((0.14, 0), (0, 0.22), (0, 0.78), (0.14, 1), (0.42, 1), (0.56, 0.78),
           (0.56, 0.22), (0.42, 0), (0.14, 0)), ((0.34, 0.24), (0.6, -0.04))),
    "R": (((0, 0), (0, 1), (0.4, 1), (0.56, 0.82), (0.42, 0.56), (0, 0.56)),
          ((0.26, 0.56), (0.56, 0))),
    "S": (((0.56, 0.86), (0.36, 1), (0.14, 1), (0, 0.82), (0.06, 0.62), (0.46, 0.44),
           (0.56, 0.24), (0.42, 0), (0.16, 0), (0, 0.12)),),
    "T": (((0, 1), (0.56, 1)), ((0.28, 1), (0.28, 0))),
    "U": (((0, 1), (0, 0.2), (0.16, 0), (0.4, 0), (0.56, 0.2), (0.56, 1)),),
    "V": (((0, 1), (0.28, 0), (0.56, 1)),),
    "W": (((0, 1), (0.12, 0), (0.28, 0.6), (0.44, 0), (0.56, 1)),),
    "X": (((0, 0), (0.56, 1)), ((0, 1), (0.56, 0))),
    "Y": (((0, 1), (0.28, 0.5), (0.56, 1)), ((0.28, 0.5), (0.28, 0))),
    "Z": (((0, 1), (0.56, 1), (0, 0), (0.56, 0)),),
    "0": (((0.14, 0), (0, 0.22), (0, 0.78), (0.14, 1), (0.42, 1), (0.56, 0.78),
           (0.56, 0.22), (0.42, 0), (0.14, 0)), ((0.06, 0.2), (0.5, 0.8))),
    "1": (((0.08, 0.8), (0.28, 1), (0.28, 0)), ((0.06, 0), (0.5, 0))),
    "2": (((0, 0.82), (0.16, 1), (0.42, 1), (0.56, 0.8), (0, 0), (0.56, 0)),),
    "3": (((0, 1), (0.56, 1), (0.24, 0.58), (0.5, 0.5), (0.56, 0.26), (0.4, 0), (0.14, 0), (0, 0.14)),),
    "4": (((0.42, 0), (0.42, 1), (0, 0.32), (0.56, 0.32)),),
    "5": (((0.54, 1), (0.06, 1), (0.02, 0.56), (0.34, 0.62), (0.56, 0.44), (0.5, 0.14),
           (0.28, 0), (0.06, 0.06)),),
    "6": (((0.5, 0.94), (0.24, 1), (0.04, 0.72), (0, 0.24), (0.18, 0), (0.42, 0),
           (0.56, 0.22), (0.44, 0.48), (0.14, 0.52), (0.02, 0.34)),),
    "7": (((0, 1), (0.56, 1), (0.2, 0)),),
    "8": (((0.2, 0.54), (0.02, 0.74), (0.14, 1), (0.42, 1), (0.54, 0.74), (0.36, 0.54),
           (0.2, 0.54), (0, 0.28), (0.16, 0), (0.4, 0), (0.56, 0.28), (0.36, 0.54)),),
    "9": (((0.06, 0.06), (0.32, 0), (0.52, 0.28), (0.56, 0.76), (0.38, 1), (0.14, 1),
           (0, 0.78), (0.12, 0.52), (0.42, 0.48), (0.54, 0.66)),),
    ".": (((0.22, 0), (0.34, 0), (0.34, 0.12), (0.22, 0.12), (0.22, 0)),),
    ",": (((0.3, 0.12), (0.18, -0.12)),),
    "-": (((0.08, 0.5), (0.48, 0.5)),),
    "_": (((0.0, 0.0), (0.56, 0.0)),),
    "+": (((0.06, 0.5), (0.5, 0.5)), ((0.28, 0.28), (0.28, 0.72))),
    "/": (((0.02, 0), (0.54, 1)),),
    "\\": (((0.02, 1), (0.54, 0)),),
    ":": (((0.24, 0.18), (0.32, 0.18)), ((0.24, 0.62), (0.32, 0.62))),
    "!": (((0.28, 1), (0.28, 0.26)), ((0.28, 0.1), (0.28, 0.02))),
    "?": (((0, 0.8), (0.16, 1), (0.42, 1), (0.56, 0.78), (0.28, 0.48), (0.28, 0.3)),
          ((0.28, 0.1), (0.28, 0.02))),
    "%": (((0.02, 0), (0.54, 1)), ((0.02, 0.76), (0.16, 0.76), (0.16, 1), (0.02, 1), (0.02, 0.76)),
          ((0.4, 0), (0.54, 0), (0.54, 0.24), (0.4, 0.24), (0.4, 0))),
    "(": (((0.4, 1), (0.16, 0.7), (0.16, 0.3), (0.4, 0)),),
    ")": (((0.16, 1), (0.4, 0.7), (0.4, 0.3), (0.16, 0)),),
    "*": (((0.1, 0.3), (0.46, 0.86)), ((0.46, 0.3), (0.1, 0.86)), ((0.28, 0.24), (0.28, 0.92))),
    "#": (((0.12, 0), (0.2, 1)), ((0.36, 0), (0.44, 1)), ((0.02, 0.32), (0.54, 0.32)),
          ((0.02, 0.68), (0.54, 0.68))),
    "=": (((0.06, 0.34), (0.5, 0.34)), ((0.06, 0.66), (0.5, 0.66))),
    "'": (((0.28, 1), (0.28, 0.76)),),
    "\"": (((0.18, 1), (0.18, 0.76)), ((0.38, 1), (0.38, 0.76))),
    "<": (((0.46, 0.9), (0.08, 0.5), (0.46, 0.1)),),
    ">": (((0.1, 0.9), (0.48, 0.5), (0.1, 0.1)),),
    "°": (((0.18, 0.74), (0.38, 0.74), (0.38, 0.94), (0.18, 0.94), (0.18, 0.74)),),
}

_TEXT_ADVANCE = 0.78          # glyph pitch, relative to the cap height
_TEXT_STROKE = 0.115          # stroke thickness, relative to the cap height


def text_metrics(string: str, height: float = 1.0) -> Tuple[float, float]:
    """Return ``(width, height)`` of the block text at the given cap height."""
    lines = string.split("\n")
    longest = max((len(l) for l in lines), default=0)
    w = max(0.0, (longest * _TEXT_ADVANCE - (_TEXT_ADVANCE - _GLYPH_W)) * height)
    h = (len(lines) * 1.0 + (len(lines) - 1) * 0.4) * height
    return w, h


def _tess_text(shape: Dict[str, Any]) -> _Mesh:
    string = str(shape["string"])
    height = float(shape["height"])
    depth = float(shape["depth"])
    if height <= 0.0 or depth <= 0.0:
        raise TessellationError("text: height and depth must be > 0")
    if not string:
        raise TessellationError("text: string must not be empty")
    stroke = height * _TEXT_STROKE
    lines = string.split("\n")
    line_pitch = height * 1.4
    total_w, total_h = text_metrics(string, height)

    m = _Mesh()
    seg_count = 0
    for li, line in enumerate(lines):
        # each line is centred horizontally on the block
        line_w = max(0.0, (len(line) * _TEXT_ADVANCE - (_TEXT_ADVANCE - _GLYPH_W)) * height)
        x0 = -line_w * 0.5
        y0 = total_h * 0.5 - height - li * line_pitch
        for ci, ch in enumerate(line):
            glyph = _GLYPHS.get(ch.upper())
            if not glyph:
                continue
            gx = x0 + ci * _TEXT_ADVANCE * height
            for poly in glyph:
                for k in range(len(poly) - 1):
                    ax = gx + poly[k][0] * height
                    ay = y0 + poly[k][1] * height
                    bx = gx + poly[k + 1][0] * height
                    by = y0 + poly[k + 1][1] * height
                    dx, dy = bx - ax, by - ay
                    ln = math.hypot(dx, dy)
                    if ln < 1e-9:
                        continue
                    ux, uy = dx / ln, dy / ln
                    seg = _Mesh()
                    _box_into(seg, ln + stroke, stroke, depth)
                    seg.transformed_copy_into(
                        m,
                        ((ax + bx) * 0.5, (ay + by) * 0.5, 0.0),
                        (ux, uy, 0.0), (-uy, ux, 0.0), (0.0, 0.0, 1.0),
                    )
                    seg_count += 1
    if seg_count == 0:
        raise TessellationError("text: %r contains no renderable glyphs" % string)
    return m


def _tess_mesh(shape: Dict[str, Any]) -> _Mesh:
    pos = [float(v) for v in shape["positions"]]
    idx = [int(v) for v in shape["indices"]]
    if len(pos) % 3 or len(pos) < 9:
        raise TessellationError("mesh: positions must be 3*N floats with N >= 3")
    if len(idx) % 3 or not idx:
        raise TessellationError("mesh: indices must be 3*T ints with T >= 1")
    nverts = len(pos) // 3
    if any(i < 0 or i >= nverts for i in idx):
        raise TessellationError("mesh: index out of range")
    nrm = shape.get("normals")
    if nrm is not None:
        nrm = [float(v) for v in nrm]
        if len(nrm) != len(pos):
            raise TessellationError("mesh: normals must match positions")
    else:
        nrm = [0.0] * len(pos)
        for t in range(0, len(idx), 3):
            a, b, c = idx[t], idx[t + 1], idx[t + 2]
            pa = pos[3 * a:3 * a + 3]
            pb = pos[3 * b:3 * b + 3]
            pc = pos[3 * c:3 * c + 3]
            e1 = [pb[k] - pa[k] for k in range(3)]
            e2 = [pc[k] - pa[k] for k in range(3)]
            fn = _cross(e1, e2)
            for v in (a, b, c):
                for k in range(3):
                    nrm[3 * v + k] += fn[k]
        for v in range(nverts):
            n = _norm3(nrm[3 * v:3 * v + 3])
            nrm[3 * v], nrm[3 * v + 1], nrm[3 * v + 2] = n
    uv = shape.get("uvs")
    if uv is not None:
        uv = [float(v) for v in uv]
        if len(uv) != nverts * 2:
            raise TessellationError("mesh: uvs must be 2*N floats")
    else:
        uv = [0.0] * (nverts * 2)
    m = _Mesh()
    m.pos = pos
    m.nrm = nrm
    m.uv = uv
    m.idx = idx
    return m


_TESSELLATORS = {
    "box": _tess_box,
    "cylinder": _tess_cylinder,
    "cone": _tess_cone,
    "sphere": _tess_sphere,
    "torus": _tess_torus,
    "tube": _tess_tube,
    "plane": _tess_plane,
    "extrusion": _tess_extrusion,
    "grid": _tess_grid,
    "honeycomb": _tess_honeycomb,
    "text": _tess_text,
    "mesh": _tess_mesh,
}


def tessellate_shape(
    shape: Dict[str, Any]
) -> Tuple[List[float], List[float], List[float], List[int]]:
    """Tessellate one shape primitive.

    Returns ``(positions, normals, uvs, indices)`` as flat lists:
    ``positions`` and ``normals`` hold ``3*N`` floats, ``uvs`` ``2*N`` floats
    and ``indices`` ``3*T`` integers forming CCW (front facing) triangles with
    outward pointing normals.

    Raises :class:`TessellationError` for degenerate or unknown shapes.
    """
    if not isinstance(shape, dict):
        raise TessellationError("shape must be an object, got %r" % type(shape).__name__)
    stype = shape.get("type")
    fn = _TESSELLATORS.get(stype)
    if fn is None:
        raise TessellationError("unknown shape type %r" % (stype,))
    try:
        mesh = fn(shape)
    except TessellationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TessellationError("%s: %s" % (stype, exc)) from exc
    if not mesh.idx:
        raise TessellationError("%s: produced no triangles" % stype)
    return mesh.result()


# ---------------------------------------------------------------------------
# spec level helpers
# ---------------------------------------------------------------------------


def iter_nodes(spec_or_nodes: Any, _matrix: Optional[Sequence[Sequence[float]]] = None):
    """Yield ``(node, world_matrix)`` for every node in a spec, depth first."""
    if isinstance(spec_or_nodes, dict) and "nodes" in spec_or_nodes:
        nodes = spec_or_nodes.get("nodes") or []
    else:
        nodes = spec_or_nodes or []
    stack = [(n, _matrix or IDENTITY4) for n in reversed(nodes)]
    while stack:
        node, parent = stack.pop()
        local = compose_trs(
            node.get("translation", (0.0, 0.0, 0.0)),
            node.get("rotation", (0.0, 0.0, 0.0, 1.0)),
            node.get("scale", (1.0, 1.0, 1.0)),
        )
        world = mat_mul(parent, local)
        yield node, world
        for c in reversed(node.get("children") or []):
            stack.append((c, world))


def count_parts(spec: Dict[str, Any]) -> int:
    """Number of nodes carrying geometry."""
    return sum(1 for n, _ in iter_nodes(spec) if n.get("shape") is not None)


def tessellate_spec(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tessellate every shape node of a spec into world space.

    Returns a list of ``{"name", "material", "positions", "normals", "uvs",
    "indices"}`` dictionaries.  Mostly useful for tests and exporters.
    """
    out: List[Dict[str, Any]] = []
    for node, world in iter_nodes(spec):
        shape = node.get("shape")
        if shape is None:
            continue
        pos, nrm, uv, idx = tessellate_shape(shape)
        wp: List[float] = []
        wn: List[float] = []
        for i in range(0, len(pos), 3):
            p = mat_apply_point(world, pos[i:i + 3])
            wp.extend(p)
            n = _norm3(mat_apply_dir(world, nrm[i:i + 3]))
            wn.extend(n)
        out.append(
            {
                "name": node.get("name", ""),
                "material": node.get("material"),
                "positions": wp,
                "normals": wn,
                "uvs": uv,
                "indices": idx,
            }
        )
    return out


def spec_bounds(spec: Dict[str, Any]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """World space axis aligned bounding box of all geometry in a spec."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for node, world in iter_nodes(spec):
        shape = node.get("shape")
        if shape is None:
            continue
        pos, _n, _u, _i = tessellate_shape(shape)
        for i in range(0, len(pos), 3):
            p = mat_apply_point(world, pos[i:i + 3])
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    if lo[0] == math.inf:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return (tuple(lo), tuple(hi))  # type: ignore[return-value]
