# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                          *
# *   This file is part of FreeCAD.                                          *
# *                                                                          *
# *   FreeCAD is free software: you can redistribute it and/or modify it     *
# *   under the terms of the GNU Lesser General Public License as            *
# *   published by the Free Software Foundation, either version 2.1 of the   *
# *   License, or (at your option) any later version.                        *
# *                                                                          *
# *   FreeCAD is distributed in the hope that it will be useful, but         *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU      *
# *   Lesser General Public License for more details.                        *
# ***************************************************************************
"""Import an FCXR package back into a FreeCAD document (reverse of scene_export).

Used by File -> Import for ``.fcxr`` and by the paint/vector round trip coming
back from the headset.

The scene graph flattening, unit conversion and colour conversion all live in
pure helpers (:func:`extract_meshes`) that never touch FreeCAD, so they can be
unit tested without it; only :func:`import_package` creates document objects,
and it imports ``Mesh``/``FreeCAD`` lazily (§6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .fcxr import DEFAULT_UNIT_SCALE, FcxrDocument, FcxrError, read

__all__ = [
    "MeshSpec",
    "Transform",
    "IDENTITY",
    "extract_meshes",
    "linear_to_srgb",
    "import_package",
    "apply_paint_section",
    "apply_vector_section",
]


# ---------------------------------------------------------------------------
# tiny transform maths (pure python, numpy free per §6)
# ---------------------------------------------------------------------------

Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]  # x, y, z, w


def _quat_normalise(q: Sequence[float]) -> Quat:
    x, y, z, w = (float(v) for v in q)
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def _quat_mul(a: Sequence[float], b: Sequence[float]) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_rotate(q: Sequence[float], v: Sequence[float]) -> Vec3:
    x, y, z, w = q
    vx, vy, vz = v
    # t = 2 * (q_vec x v);  v' = v + w*t + q_vec x t
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


@dataclass(frozen=True)
class Transform:
    """A TRS transform, composable like a glTF/FCXR node transform."""

    translation: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    scale: Vec3 = (1.0, 1.0, 1.0)

    def compose(self, child: "Transform") -> "Transform":
        """``self`` applied on top of ``child`` (parent.compose(child))."""
        sx, sy, sz = self.scale
        cx, cy, cz = child.translation
        rotated = _quat_rotate(self.rotation, (cx * sx, cy * sy, cz * sz))
        translation = (
            self.translation[0] + rotated[0],
            self.translation[1] + rotated[1],
            self.translation[2] + rotated[2],
        )
        # Exact for uniform parent scale; the usual TRS approximation otherwise.
        return Transform(
            translation=translation,
            rotation=_quat_normalise(_quat_mul(self.rotation, child.rotation)),
            scale=(sx * child.scale[0], sy * child.scale[1], sz * child.scale[2]),
        )

    def apply(self, point: Sequence[float]) -> Vec3:
        scaled = (
            point[0] * self.scale[0],
            point[1] * self.scale[1],
            point[2] * self.scale[2],
        )
        rotated = _quat_rotate(self.rotation, scaled)
        return (
            rotated[0] + self.translation[0],
            rotated[1] + self.translation[1],
            rotated[2] + self.translation[2],
        )


IDENTITY = Transform()

#: +90° about X — undoes the exporter's Z-up -> Y-up root rotation
_Y_UP_TO_Z_UP = Transform(rotation=(math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)))


def linear_to_srgb(c: float) -> float:
    """Inverse of the exporter's sRGB -> linear conversion."""
    c = max(0.0, min(1.0, float(c)))
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


# ---------------------------------------------------------------------------
# pure extraction
# ---------------------------------------------------------------------------


@dataclass
class MeshSpec:
    """One importable mesh, already converted to FreeCAD conventions.

    ``points`` and ``placement`` are in document units (mm), Z up; ``color`` is
    sRGB 0..1 and ``transparency`` is FreeCAD's 0..100 integer.
    """

    fc_name: str = ""
    label: str = ""
    points: List[Vec3] = field(default_factory=list)
    facets: List[Tuple[int, int, int]] = field(default_factory=list)
    translation: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    color: Tuple[float, float, float] = (0.8, 0.8, 0.8)
    transparency: int = 0
    visible: bool = True

    @property
    def triangle_count(self) -> int:
        return len(self.facets)


def _node_transform(node: Dict[str, Any]) -> Transform:
    translation = node.get("translation") or (0.0, 0.0, 0.0)
    rotation = node.get("rotation") or (0.0, 0.0, 0.0, 1.0)
    scale = node.get("scale") or (1.0, 1.0, 1.0)
    return Transform(
        translation=(float(translation[0]), float(translation[1]), float(translation[2])),
        rotation=_quat_normalise(rotation),
        scale=(float(scale[0]), float(scale[1]), float(scale[2])),
    )


def _material_appearance(
    doc: FcxrDocument, material_index: Optional[int]
) -> Tuple[Tuple[float, float, float], int]:
    if material_index is None:
        return (0.8, 0.8, 0.8), 0
    materials = doc.materials
    if not 0 <= material_index < len(materials):
        return (0.8, 0.8, 0.8), 0
    base = materials[material_index].get("base_color") or [0.8, 0.8, 0.8, 1.0]
    rgb = (
        linear_to_srgb(base[0]),
        linear_to_srgb(base[1]),
        linear_to_srgb(base[2]),
    )
    alpha = float(base[3]) if len(base) > 3 else 1.0
    transparency = int(round((1.0 - max(0.0, min(1.0, alpha))) * 100.0))
    return rgb, transparency


def extract_meshes(doc: FcxrDocument, include_hidden: bool = True) -> List[MeshSpec]:
    """Flatten an FCXR scene into a list of :class:`MeshSpec`.

    Node transforms are composed down the tree, any Y-up root convention is
    undone, and metres are converted back to document units using the manifest
    ``unit_scale``.  Non-uniform node scale (which a FreeCAD Placement cannot
    express) is baked into the points.
    """
    if not isinstance(doc, FcxrDocument):
        raise FcxrError("extract_meshes() expects an FcxrDocument")

    unit_scale = doc.unit_scale or DEFAULT_UNIT_SCALE
    to_document_units = 1.0 / unit_scale
    nodes = doc.nodes
    if not nodes:
        return []

    root = doc.scene.get("root", 0)
    if not isinstance(root, int) or not 0 <= root < len(nodes):
        raise FcxrError("scene root %r out of range" % (root,))

    base = _Y_UP_TO_Z_UP if str(doc.asset.get("up_axis", "")).upper() == "Y" else IDENTITY

    out: List[MeshSpec] = []
    stack: List[Tuple[int, Transform, bool]] = [(root, base, True)]
    seen: set = set()
    while stack:
        index, parent, parent_visible = stack.pop()
        if index in seen:
            raise FcxrError("node graph contains a cycle at node %d" % index)
        seen.add(index)
        node = nodes[index]
        world = parent.compose(_node_transform(node))
        visible = parent_visible and bool(node.get("visible", True))

        mesh_index = node.get("mesh")
        if mesh_index is not None and (visible or include_hidden):
            out.extend(
                _mesh_specs(doc, node, mesh_index, world, to_document_units, visible)
            )
        for child in reversed(node.get("children", []) or []):
            stack.append((int(child), world, visible))
    return out


def _mesh_specs(
    doc: FcxrDocument,
    node: Dict[str, Any],
    mesh_index: int,
    world: Transform,
    to_document_units: float,
    visible: bool,
) -> List[MeshSpec]:
    meshes = doc.meshes
    if not 0 <= mesh_index < len(meshes):
        raise FcxrError("node mesh index %r out of range" % (mesh_index,))
    mesh = meshes[mesh_index]
    specs: List[MeshSpec] = []
    primitives = mesh.get("primitives") or []
    for prim_index in range(len(primitives)):
        arrays = doc.primitive_arrays(mesh_index, prim_index)
        positions = arrays["positions"]
        indices = arrays["indices"]
        if positions is None or len(positions) < 3:
            continue

        # The rotation/translation stay on the Placement; only scale is baked.
        scale = world.scale
        points: List[Vec3] = []
        for i in range(0, len(positions), 3):
            points.append(
                (
                    positions[i] * scale[0] * to_document_units,
                    positions[i + 1] * scale[1] * to_document_units,
                    positions[i + 2] * scale[2] * to_document_units,
                )
            )

        if indices is None:
            facets = [(i, i + 1, i + 2) for i in range(0, len(points) - 2, 3)]
        else:
            facets = [
                (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
                for i in range(0, len(indices) - 2, 3)
            ]

        color, transparency = _material_appearance(doc, arrays.get("material"))
        name = node.get("fc_name") or node.get("name") or mesh.get("name") or "Mesh"
        label = node.get("name") or name
        if len(primitives) > 1:
            name = "%s_%d" % (name, prim_index)
            label = "%s (%d)" % (label, prim_index)
        specs.append(
            MeshSpec(
                fc_name=str(name),
                label=str(label),
                points=points,
                facets=facets,
                translation=(
                    world.translation[0] * to_document_units,
                    world.translation[1] * to_document_units,
                    world.translation[2] * to_document_units,
                ),
                rotation=world.rotation,
                color=color,
                transparency=transparency,
                visible=visible,
            )
        )
    return specs


# ---------------------------------------------------------------------------
# paint / vector hand-off to the GUI layer
# ---------------------------------------------------------------------------


def _paint_bridge():
    """Return ``xrcore.paint_bridge`` or ``None`` (console mode)."""
    try:
        from xrcore import paint_bridge  # type: ignore
    except Exception:  # ImportError, and anything the GUI layer raises
        return None
    return paint_bridge


def apply_paint_section(doc: FcxrDocument, document: Any = None) -> bool:
    """Hand ``manifest["paint"]`` to the GUI paint bridge if it is available."""
    paint = doc.paint
    if not paint:
        return False
    bridge = _paint_bridge()
    apply_remote_paint = getattr(bridge, "apply_remote_paint", None)
    if apply_remote_paint is None:
        return False
    apply_remote_paint(paint, list(doc.images))
    return True


def apply_vector_section(doc: FcxrDocument, document: Any = None) -> bool:
    """Hand ``manifest["vector"]`` to the GUI paint bridge if it is available."""
    vector = doc.vector
    if not vector:
        return False
    bridge = _paint_bridge()
    apply_remote_vector = getattr(bridge, "apply_remote_vector", None)
    if apply_remote_vector is None:
        return False
    apply_remote_vector(vector, document)
    return True


# ---------------------------------------------------------------------------
# document import
# ---------------------------------------------------------------------------


def import_package(
    path_or_bytes: Union[str, bytes, bytearray],
    document: Any = None,
    include_hidden: bool = True,
) -> List[Any]:
    """Import an FCXR package into ``document``, returning the touched objects.

    Meshes become ``Mesh::Feature`` objects.  A node whose ``fc_name`` already
    exists in the document is updated in place rather than duplicated.
    """
    doc = (
        path_or_bytes
        if isinstance(path_or_bytes, FcxrDocument)
        else read(path_or_bytes)
    )

    import FreeCAD  # noqa: F401  (lazy on purpose, §6)

    if document is None:
        document = FreeCAD.ActiveDocument
    if document is None:
        document = FreeCAD.newDocument(
            str(doc.asset.get("source_document", "XR Import")).rsplit("/", 1)[-1]
            or "XR Import"
        )

    try:
        import Mesh  # noqa: F401  (lazy on purpose)
    except ImportError as exc:  # pragma: no cover - Mesh is a standard module
        raise FcxrError("the Mesh workbench is required to import FCXR: %s" % (exc,))

    created: List[Any] = []
    for spec in extract_meshes(doc, include_hidden=include_hidden):
        mesh_data = Mesh.Mesh()
        for a, b, c in spec.facets:
            try:
                mesh_data.addFacet(
                    FreeCAD.Vector(*spec.points[a]),
                    FreeCAD.Vector(*spec.points[b]),
                    FreeCAD.Vector(*spec.points[c]),
                )
            except IndexError:
                continue

        obj = document.getObject(spec.fc_name)
        if obj is None or getattr(obj, "TypeId", "") != "Mesh::Feature":
            obj = document.addObject("Mesh::Feature", spec.fc_name)
            obj.Label = spec.label
        obj.Mesh = mesh_data
        obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(*spec.translation),
            FreeCAD.Rotation(*spec.rotation),
        )
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            try:
                view.ShapeColor = tuple(spec.color)
                view.Transparency = int(spec.transparency)
                view.Visibility = bool(spec.visible)
            except Exception:
                pass
        created.append(obj)

    try:
        apply_paint_section(doc, document)
        apply_vector_section(doc, document)
    except Exception as exc:  # never let a GUI bridge break the geometry import
        FreeCAD.Console.PrintWarning("XR: paint/vector import failed: %s\n" % (exc,))

    document.recompute()
    return created


def open(filename: str) -> Any:  # noqa: A001 - FreeCAD import hook name
    """FreeCAD ``File -> Open`` hook for ``.fcxr``."""
    import FreeCAD  # noqa: F401

    name = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "XR Import"
    document = FreeCAD.newDocument(name)
    import_package(filename, document)
    return document


def insert(filename: str, docname: Optional[str] = None) -> Any:
    """FreeCAD ``File -> Import`` hook for ``.fcxr``."""
    import FreeCAD  # noqa: F401

    document = None
    if docname:
        try:
            document = FreeCAD.getDocument(docname)
        except Exception:
            document = None
    if document is None:
        document = FreeCAD.ActiveDocument or FreeCAD.newDocument("XR Import")
    import_package(filename, document)
    return document
