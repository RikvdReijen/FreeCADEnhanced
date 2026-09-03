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
"""Export a FreeCAD document to an FCXR package (ARCHITECTURE.md §1).

Everything FreeCAD related is imported *inside* functions so this module can be
imported (and partly tested) without FreeCAD present, per §6.

Conventions used by the exporter
--------------------------------

* **Units.**  ``asset.unit_scale`` records the document-unit -> metre factor
  (0.001 for FreeCAD's millimetres).  The exporter applies that factor itself,
  so *both* accessor positions and node translations in the produced file are
  already in metres — §1 pins node translations to metres and leaves accessor
  units unstated, and a single consistent unit is what a renderer wants.
* **Up axis.**  FreeCAD is Z up, OpenXR (and therefore §2 environments and the
  Quest renderer) is Y up.  Rather than rewriting every vertex the exporter
  emits a synthetic root node rotated -90° about X, and records
  ``asset.up_axis = "Y"``.  Pass ``y_up=False`` to keep the FreeCAD axes.
* **Geometry is local.**  ``obj.Shape`` and ``obj.Mesh.Topology`` return points
  with the object placement already applied, so the exporter multiplies them by
  the inverse placement and carries the placement on the node instead.
* **Colours.**  FreeCAD view colours are sRGB; §1 asks for linear base colours,
  so they are converted.
"""

from __future__ import annotations

import hashlib
import math
import os
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .fcxr import DEFAULT_UNIT_SCALE, FcxrError, FcxrWriter

__all__ = [
    "LOD_DEVIATIONS",
    "DEFAULT_LOD",
    "deviation_for_lod",
    "ExportOptions",
    "export_document",
    "export_document_bytes",
    "export_objects",
    "export_selection",
    "scene_hash",
    "document_thumbnail",
    "document_info",
    "write_document",
    "compute_normals",
]

#: tessellation deviation in document units (mm) for LOD 0 (coarse) .. 3 (fine)
LOD_DEVIATIONS: Tuple[float, float, float, float] = (2.0, 0.75, 0.25, 0.05)
DEFAULT_LOD = 1

#: angle above which a shared vertex is split so an edge stays sharp
DEFAULT_CREASE_DEG = 35.0

#: quaternion rotating FreeCAD's Z-up frame onto OpenXR's Y-up frame (-90° X)
_Z_UP_TO_Y_UP = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))

_CONTAINER_TYPES = (
    "App::Part",
    "App::DocumentObjectGroup",
    "App::LinkGroup",
    "App::Origin",
)


def deviation_for_lod(lod: Any) -> float:
    """Tessellation deviation (document units) for an LOD in ``0..3``."""
    try:
        index = int(lod)
    except (TypeError, ValueError):
        index = DEFAULT_LOD
    index = max(0, min(len(LOD_DEVIATIONS) - 1, index))
    return LOD_DEVIATIONS[index]


class ExportOptions:
    """Tunables for :func:`export_document` and friends."""

    def __init__(
        self,
        lod: int = DEFAULT_LOD,
        unit_scale: float = DEFAULT_UNIT_SCALE,
        environment: Optional[str] = None,
        user_scale: Optional[float] = None,
        include_hidden: bool = False,
        crease_deg: float = DEFAULT_CREASE_DEG,
        y_up: bool = True,
        created: Optional[str] = None,
        max_triangles: int = 0,
    ) -> None:
        self.lod = lod
        self.unit_scale = float(unit_scale)
        self.environment = environment
        self.user_scale = user_scale
        self.include_hidden = bool(include_hidden)
        self.crease_deg = float(crease_deg)
        self.y_up = bool(y_up)
        self.created = created
        #: soft budget; 0 disables the check (only used for reporting)
        self.max_triangles = int(max_triangles)

    @property
    def deviation(self) -> float:
        return deviation_for_lod(self.lod)


# ---------------------------------------------------------------------------
# small maths helpers (kept numpy free on purpose, §6)
# ---------------------------------------------------------------------------


def _srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, float(c)))
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def compute_normals(
    positions: Sequence[float],
    indices: Sequence[int],
    crease_deg: float = DEFAULT_CREASE_DEG,
) -> Tuple[List[float], List[float], List[int]]:
    """Compute per-vertex normals, splitting vertices across sharp edges.

    ``positions`` is a flat ``x,y,z`` list and ``indices`` a flat triangle list.
    Returns ``(positions, normals, indices)`` where vertices whose incident
    faces differ by more than ``crease_deg`` have been duplicated so hard edges
    stay hard.  A ``crease_deg`` of 180 gives fully smooth normals.
    """
    vcount = len(positions) // 3
    tri_count = len(indices) // 3
    if vcount == 0 or tri_count == 0:
        return list(positions), [0.0, 0.0, 1.0] * vcount, list(indices)

    cos_limit = math.cos(math.radians(max(0.0, min(180.0, crease_deg))))

    # face normals, scaled by twice the triangle area (area weighting)
    face_normals: List[Tuple[float, float, float]] = []
    unit_normals: List[Tuple[float, float, float]] = []
    incident: List[List[int]] = [[] for _ in range(vcount)]
    for f in range(tri_count):
        i0, i1, i2 = indices[3 * f], indices[3 * f + 1], indices[3 * f + 2]
        ax, ay, az = positions[3 * i0 : 3 * i0 + 3]
        bx, by, bz = positions[3 * i1 : 3 * i1 + 3]
        cx, cy, cz = positions[3 * i2 : 3 * i2 + 3]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        face_normals.append((nx, ny, nz))
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-20:
            unit_normals.append((nx / length, ny / length, nz / length))
        else:  # degenerate triangle
            unit_normals.append((0.0, 0.0, 0.0))
        for v in (i0, i1, i2):
            incident[v].append(f)

    out_positions: List[float] = []
    out_normals: List[float] = []
    out_indices: List[int] = []
    remap: Dict[Tuple[int, int, int, int], int] = {}

    for f in range(tri_count):
        fn = unit_normals[f]
        for corner in range(3):
            v = indices[3 * f + corner]
            sx = sy = sz = 0.0
            for g in incident[v]:
                gn = unit_normals[g]
                if fn[0] * gn[0] + fn[1] * gn[1] + fn[2] * gn[2] >= cos_limit - 1e-6:
                    gw = face_normals[g]
                    sx += gw[0]
                    sy += gw[1]
                    sz += gw[2]
            length = math.sqrt(sx * sx + sy * sy + sz * sz)
            if length > 1e-20:
                nx, ny, nz = sx / length, sy / length, sz / length
            else:
                nx, ny, nz = fn if any(fn) else (0.0, 0.0, 1.0)
            key = (v, int(nx * 4096), int(ny * 4096), int(nz * 4096))
            index = remap.get(key)
            if index is None:
                index = len(out_positions) // 3
                remap[key] = index
                out_positions.extend(positions[3 * v : 3 * v + 3])
                out_normals.extend((nx, ny, nz))
            out_indices.append(index)

    return out_positions, out_normals, out_indices


# ---------------------------------------------------------------------------
# FreeCAD access (all lazily imported)
# ---------------------------------------------------------------------------


def _freecad():
    import FreeCAD  # noqa: F401  (lazy on purpose, §6)

    return FreeCAD


def _resolve_document(doc: Any = None):
    """Accept a document, a document name or ``None`` (the active document)."""
    App = _freecad()
    if doc is None:
        doc = App.ActiveDocument
        if doc is None:
            raise FcxrError("no active FreeCAD document")
        return doc
    if isinstance(doc, str):
        resolved = App.getDocument(doc) if doc in App.listDocuments() else None
        if resolved is None:
            raise FcxrError("no such document: %r" % (doc,))
        return resolved
    return doc


def _is_visible(obj: Any) -> bool:
    visible = getattr(obj, "Visibility", None)
    if visible is None:
        view = getattr(obj, "ViewObject", None)
        visible = getattr(view, "Visibility", True) if view is not None else True
    return bool(visible)


def _is_container(obj: Any) -> bool:
    return getattr(obj, "TypeId", "") in _CONTAINER_TYPES or (
        hasattr(obj, "Group") and not hasattr(obj, "Shape")
    )


def _children_of(obj: Any) -> List[Any]:
    group = getattr(obj, "Group", None)
    if isinstance(group, (list, tuple)):
        return [child for child in group if child is not None]
    return []


def _placement_of(obj: Any):
    placement = getattr(obj, "Placement", None)
    if placement is None:
        App = _freecad()
        return App.Placement()
    return placement


def _tessellate(obj: Any, deviation: float) -> Optional[Tuple[List[float], List[int]]]:
    """Return ``(flat local positions, flat indices)`` in document units."""
    placement = _placement_of(obj)
    try:
        inverse = placement.inverse()
    except Exception:  # pragma: no cover - exotic placement types
        inverse = None

    points = facets = None
    shape = getattr(obj, "Shape", None)
    if shape is not None and getattr(shape, "isNull", lambda: False)() is False:
        try:
            points, facets = shape.tessellate(float(deviation))
        except Exception:
            points = facets = None
    if points is None:
        mesh = getattr(obj, "Mesh", None)
        topology = getattr(mesh, "Topology", None) if mesh is not None else None
        if topology is not None:
            points, facets = topology[0], topology[1]
    if not points or not facets:
        return None

    positions: List[float] = []
    for point in points:
        if inverse is not None:
            point = inverse.multVec(point)
        positions.extend((point.x, point.y, point.z))
    indices: List[int] = []
    for facet in facets:
        indices.extend((int(facet[0]), int(facet[1]), int(facet[2])))
    return positions, indices


def _view_material(obj: Any) -> Dict[str, Any]:
    """Extract base colour / transparency / shininess from the ViewObject."""
    view = getattr(obj, "ViewObject", None)
    rgb = (0.8, 0.8, 0.8)
    alpha = 1.0
    shininess = 0.2
    if view is not None:
        appearance = getattr(view, "ShapeAppearance", None)
        material = None
        if isinstance(appearance, (list, tuple)) and appearance:
            material = appearance[0]
        elif appearance is not None and hasattr(appearance, "DiffuseColor"):
            material = appearance
        if material is not None:
            diffuse = getattr(material, "DiffuseColor", None)
            if diffuse is not None:
                rgb = (diffuse[0], diffuse[1], diffuse[2])
                if len(diffuse) > 3:
                    alpha = 1.0 - float(diffuse[3])
            shininess = float(getattr(material, "Shininess", shininess) or shininess)
            transparency = getattr(material, "Transparency", None)
            if transparency is not None:
                alpha = 1.0 - float(transparency)
        color = getattr(view, "ShapeColor", None)
        if color is not None:
            rgb = (color[0], color[1], color[2])
        transparency = getattr(view, "Transparency", None)
        if transparency is not None:
            try:
                alpha = 1.0 - max(0, min(100, int(transparency))) / 100.0
            except (TypeError, ValueError):
                pass
    return {
        "base_color": [
            _srgb_to_linear(rgb[0]),
            _srgb_to_linear(rgb[1]),
            _srgb_to_linear(rgb[2]),
            max(0.0, min(1.0, float(alpha))),
        ],
        "metallic": 0.0,
        "roughness": max(0.05, min(1.0, 1.0 - float(shininess))),
        "double_sided": bool(getattr(view, "Lighting", "") == "Two side"),
    }


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


class _Exporter:
    def __init__(self, doc: Any, options: ExportOptions) -> None:
        self.doc = doc
        self.options = options
        self.writer = FcxrWriter(
            source_document=getattr(doc, "FileName", None)
            or getattr(doc, "Name", None),
            unit_scale=options.unit_scale,
            created=options.created,
        )
        if options.y_up:
            self.writer.set_asset_field("up_axis", "Y")
        self.writer.set_asset_field("lod", int(options.lod))
        self._material_cache: Dict[Tuple, int] = {}
        self.triangles = 0

    # -- materials ---------------------------------------------------------

    def _material_for(self, obj: Any) -> int:
        spec = _view_material(obj)
        key = (
            tuple(round(c, 6) for c in spec["base_color"]),
            round(spec["metallic"], 4),
            round(spec["roughness"], 4),
            spec["double_sided"],
        )
        cached = self._material_cache.get(key)
        if cached is not None:
            return cached
        index = self.writer.add_material(
            name=getattr(obj, "Label", None) or getattr(obj, "Name", "material"),
            base_color=spec["base_color"],
            metallic=spec["metallic"],
            roughness=spec["roughness"],
            double_sided=spec["double_sided"],
        )
        self._material_cache[key] = index
        return index

    # -- nodes -------------------------------------------------------------

    def _node_transform(self, obj: Any) -> Dict[str, Any]:
        placement = _placement_of(obj)
        scale_factor = self.options.unit_scale
        base = getattr(placement, "Base", None)
        translation = (
            [base.x * scale_factor, base.y * scale_factor, base.z * scale_factor]
            if base is not None
            else [0.0, 0.0, 0.0]
        )
        rotation = [0.0, 0.0, 0.0, 1.0]
        quaternion = getattr(getattr(placement, "Rotation", None), "Q", None)
        if quaternion is not None and len(quaternion) == 4:
            rotation = [float(q) for q in quaternion]
        scale = [1.0, 1.0, 1.0]
        obj_scale = getattr(obj, "Scale", None)
        if obj_scale is not None and hasattr(obj_scale, "x"):
            scale = [float(obj_scale.x), float(obj_scale.y), float(obj_scale.z)]
        return {"translation": translation, "rotation": rotation, "scale": scale}

    def _add_object(self, obj: Any) -> Optional[int]:
        """Recursively add ``obj`` and return its node index (or ``None``)."""
        if obj is None:
            return None
        if not self.options.include_hidden and not _is_visible(obj):
            return None

        children: List[int] = []
        for child in _children_of(obj):
            index = self._add_object(child)
            if index is not None:
                children.append(index)

        mesh_index = None
        geometry = None
        if hasattr(obj, "Shape") or hasattr(obj, "Mesh"):
            try:
                geometry = _tessellate(obj, self.options.deviation)
            except Exception:
                geometry = None
        if geometry is not None:
            positions, indices = geometry
            positions, normals, indices = compute_normals(
                positions, indices, self.options.crease_deg
            )
            scale_factor = self.options.unit_scale
            positions = [p * scale_factor for p in positions]
            self.triangles += len(indices) // 3
            mesh_index = self.writer.add_mesh(
                name=getattr(obj, "Label", None) or getattr(obj, "Name", "mesh"),
                positions=positions,
                normals=normals,
                indices=indices,
                material=self._material_for(obj),
            )

        if mesh_index is None and not children:
            return None

        transform = self._node_transform(obj)
        return self.writer.add_node(
            name=getattr(obj, "Label", None) or getattr(obj, "Name", "node"),
            mesh=mesh_index,
            children=children,
            fc_name=getattr(obj, "Name", None),
            visible=_is_visible(obj),
            **transform,
        )

    def run(self, objects: Sequence[Any]) -> bytes:
        roots: List[int] = []
        for obj in objects:
            index = self._add_object(obj)
            if index is not None:
                roots.append(index)
        root = self.writer.add_node(
            name=getattr(self.doc, "Label", None) or getattr(self.doc, "Name", "Scene"),
            children=roots,
            rotation=_Z_UP_TO_Y_UP if self.options.y_up else (0.0, 0.0, 0.0, 1.0),
            fc_name=getattr(self.doc, "Name", "Scene"),
        )
        self.writer.set_scene(
            root=root,
            environment=self.options.environment,
            user_scale=self.options.user_scale,
        )
        return self.writer.to_bytes()


def _top_level_objects(doc: Any) -> List[Any]:
    """Document objects that no container claims as a child."""
    objects = list(getattr(doc, "Objects", []) or [])
    claimed = set()
    for obj in objects:
        for child in _children_of(obj):
            claimed.add(getattr(child, "Name", id(child)))
    return [obj for obj in objects if getattr(obj, "Name", id(obj)) not in claimed]


def _write_or_return(data: bytes, path: Optional[str]):
    """Return the bytes, or write them to ``path`` and return the path."""
    if path is None:
        return data
    path = os.fspath(path)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)
    return path


def export_document(
    document: Any = None,
    path: Optional[str] = None,
    lod: int = DEFAULT_LOD,
    environment: Optional[str] = None,
    user_scale: Optional[float] = None,
    options: Optional[ExportOptions] = None,
    **kwargs: Any,
):
    """Export a whole FreeCAD document.

    With ``path`` the package is written there and the path is returned (the
    signature the GUI layer uses); without it the FCXR bytes are returned.
    """
    doc = _resolve_document(document)
    if options is None:
        options = ExportOptions(
            lod=lod, environment=environment, user_scale=user_scale, **kwargs
        )
    data = _Exporter(doc, options).run(_top_level_objects(doc))
    return _write_or_return(data, path)


def export_document_bytes(document: Any = None, **kwargs: Any) -> bytes:
    """Always return FCXR bytes (never writes a file)."""
    kwargs.pop("path", None)
    return export_document(document, None, **kwargs)


def export_objects(
    objects: Iterable[Any],
    path: Optional[str] = None,
    document: Any = None,
    lod: int = DEFAULT_LOD,
    environment: Optional[str] = None,
    user_scale: Optional[float] = None,
    options: Optional[ExportOptions] = None,
    **kwargs: Any,
):
    """Export an explicit list of objects (see :func:`export_document`)."""
    objects = list(objects)
    if document is None and objects:
        document = getattr(objects[0], "Document", None)
    doc = _resolve_document(document)
    if options is None:
        options = ExportOptions(
            lod=lod, environment=environment, user_scale=user_scale, **kwargs
        )
    data = _Exporter(doc, options).run(objects)
    return _write_or_return(data, path)


def export_selection(
    objects: Optional[Iterable[Any]] = None,
    path: Optional[str] = None,
    lod: int = DEFAULT_LOD,
    environment: Optional[str] = None,
    user_scale: Optional[float] = None,
    options: Optional[ExportOptions] = None,
    **kwargs: Any,
):
    """Export the current GUI selection (or an explicit object list)."""
    if objects is None:
        try:
            import FreeCADGui  # noqa: F401 (lazy on purpose, §6)

            objects = FreeCADGui.Selection.getSelection()
        except Exception as exc:
            raise FcxrError("no selection available: %s" % (exc,)) from None
    objects = list(objects)
    if not objects:
        raise FcxrError("nothing selected")
    return export_objects(
        objects,
        path=path,
        lod=lod,
        environment=environment,
        user_scale=user_scale,
        options=options,
        **kwargs,
    )


def write_document(path: str, document: Any = None, **kwargs: Any) -> str:
    """Export a document and write it to ``path`` (legacy argument order)."""
    return export_document(document, path, **kwargs)


# ---------------------------------------------------------------------------
# cheap change detection
# ---------------------------------------------------------------------------


def _object_fingerprint(obj: Any) -> str:
    parts: List[str] = [
        str(getattr(obj, "Name", "")),
        str(getattr(obj, "TypeId", "")),
        str(getattr(obj, "Label", "")),
        "1" if _is_visible(obj) else "0",
    ]
    placement = getattr(obj, "Placement", None)
    if placement is not None:
        try:
            base = placement.Base
            quaternion = placement.Rotation.Q
            parts.append(
                "%.6f,%.6f,%.6f|%.6f,%.6f,%.6f,%.6f"
                % (base.x, base.y, base.z, *[float(q) for q in quaternion])
            )
        except Exception:
            parts.append("?")
    # A shape hash is O(1) in OCC (it hashes the TShape identity) and changes
    # whenever the object is recomputed, which is exactly what we want here.
    shape = getattr(obj, "Shape", None)
    if shape is not None:
        for attr in ("hashCode", "TShape"):
            try:
                value = getattr(shape, attr)
                parts.append(str(value() if callable(value) else hash(value)))
                break
            except Exception:
                continue
        else:
            parts.append("shape")
    mesh = getattr(obj, "Mesh", None)
    if mesh is not None:
        try:
            parts.append("%d/%d" % (mesh.CountPoints, mesh.CountFacets))
        except Exception:
            parts.append("mesh")
    try:
        parts.append("T" if obj.isTouched() else "-")
    except Exception:
        pass
    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            color = getattr(view, "ShapeColor", None)
            parts.append(
                "%.4f,%.4f,%.4f/%s"
                % (color[0], color[1], color[2], getattr(view, "Transparency", 0))
                if color is not None
                else "-"
            )
        except Exception:
            parts.append("-")
    return "\x1f".join(parts)


def scene_hash(doc: Any = None) -> str:
    """A cheap hash of everything the exporter would look at.

    Deliberately avoids tessellation so it can be polled every second: object
    names, types, labels, visibility, placements, OCC shape identities, touched
    state and view colours only.
    """
    document = _resolve_document(doc)
    digest = hashlib.sha256()
    digest.update((getattr(document, "Name", "") or "").encode("utf-8"))
    for obj in getattr(document, "Objects", []) or []:
        digest.update(_object_fingerprint(obj).encode("utf-8", "replace"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:16]


def document_thumbnail(doc: Any = None) -> Optional[bytes]:
    """Return the document's saved thumbnail PNG, if the FCStd has one."""
    document = _resolve_document(doc)
    filename = getattr(document, "FileName", "") or ""
    if not filename or not os.path.isfile(filename):
        return None
    try:
        with zipfile.ZipFile(filename) as archive:
            for name in ("thumbnails/Thumbnail.png", "Thumbnail.png"):
                if name in archive.namelist():
                    return archive.read(name)
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def document_info(doc: Any = None) -> Dict[str, Any]:
    """Small dict describing a document, matching ``protocol.DocumentInfo``."""
    document = _resolve_document(doc)
    objects = list(getattr(document, "Objects", []) or [])
    touched = False
    try:
        touched = bool(document.isTouched())
    except Exception:
        pass
    return {
        "name": getattr(document, "Name", ""),
        "label": getattr(document, "Label", "") or getattr(document, "Name", ""),
        "hash": scene_hash(document),
        "path": getattr(document, "FileName", None) or None,
        "touched": touched,
        "object_count": len(objects),
    }
