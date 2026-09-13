# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""The ``.gbscene`` manifest: the contract between FreeCAD and the engine side.

A glTF file says what the geometry is.  It does not say which FreeCAD object a
node came from, what the asset should be called in the target's content browser,
which file holds it, or whether the exporter already converted the axes.  The
manifest says all of that, so the importer on the other side is a data-driven
script rather than a pile of guesses.

The format is versioned by :data:`gbcore.SCENE_FORMAT_VERSION`.  Importers are
expected to refuse a *major* mismatch and to ignore fields they do not know,
which is what lets the bridge add fields without breaking an installed importer.

Transforms appear twice on purpose.  ``transform`` is the row-major 4x4 in the
target's space, which is what Blender wants; ``trs`` is the same thing
decomposed into translation, quaternion and scale, which is what Unity's
``Transform`` and Unreal's ``FTransform`` want.  Decomposing once here beats
three importers each getting quaternion extraction subtly wrong.
"""

import datetime
import json

from gbcore import BRIDGE_VERSION, SCENE_FORMAT_VERSION
from gbcore.transform import get_convention

__all__ = [
    "AssetRecord",
    "MANIFEST_FORMAT",
    "build_manifest",
    "convert_bounds",
    "read_manifest",
    "write_manifest",
]

MANIFEST_FORMAT = "freecad-gamebridge-scene"


class AssetRecord:
    """One file the export produced, and what is inside it."""

    __slots__ = ("identifier", "name", "path", "kind", "mesh", "triangles",
                 "vertices", "checksum", "material", "bounds")

    def __init__(self, identifier, name, path, kind="mesh", mesh=None,
                 triangles=0, vertices=0, checksum=None, material=None, bounds=None):
        self.identifier = identifier
        self.name = name
        #: Path relative to the manifest, always with forward slashes so a
        #: manifest written on Windows imports on Linux.
        self.path = path.replace("\\", "/") if path else path
        self.kind = kind
        self.mesh = mesh
        self.triangles = triangles
        self.vertices = vertices
        self.checksum = checksum
        self.material = material
        self.bounds = bounds

    @classmethod
    def for_mesh(cls, identifier, name, path, mesh, mesh_index=None):
        bounds = mesh.bounds()
        return cls(
            identifier,
            name,
            path,
            "mesh",
            mesh_index,
            mesh.triangle_count,
            mesh.vertex_count,
            mesh.checksum(),
            mesh.material,
            {"min": list(bounds[0]), "max": list(bounds[1])} if bounds else None,
        )

    def to_dict(self):
        data = {
            "id": self.identifier,
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "triangles": self.triangles,
            "vertices": self.vertices,
        }
        if self.mesh is not None:
            data["mesh"] = self.mesh
        if self.checksum:
            data["checksum"] = self.checksum
        if self.material is not None:
            data["material"] = self.material
        if self.bounds:
            data["bounds"] = self.bounds
        return data


def _node_entry(node, convention, names, asset_by_mesh):
    transform = convention.convert_matrix(node.transform)
    translation, rotation, scale = transform.to_trs()
    entry = {
        "name": names.get(id(node), node.name),
        "label": node.name,
        "visible": node.visible,
        "transform": list(transform.m),
        "trs": {
            "translation": list(translation),
            "rotation": list(rotation),
            "scale": list(scale),
        },
    }
    if node.source:
        entry["source"] = node.source
    if node.metadata:
        entry["metadata"] = dict(node.metadata)
    if node.mesh is not None:
        entry["mesh"] = node.mesh
        asset = asset_by_mesh.get(node.mesh)
        if asset is not None:
            entry["asset"] = asset
    children = [
        _node_entry(child, convention, names, asset_by_mesh)
        for child in node.children
    ]
    if children:
        entry["children"] = children
    return entry


def _flatten(entries, out, parent):
    """Depth-first flattening of the node tree, recording each node's parent.

    The hierarchy in ``nodes`` is the canonical form, and ``flatNodes`` is an
    index over it rather than a second source of truth.  It exists because not
    every importer can conveniently walk a recursive JSON structure: Unity's
    JsonUtility cannot deserialise a self-referencing type at all, so without
    this the C# side would need its own JSON parser.  Deriving it here, once,
    beats three importers each flattening the tree slightly differently.
    """
    for entry in entries:
        index = len(out)
        flat = {k: v for k, v in entry.items() if k != "children"}
        flat["index"] = index
        flat["parent"] = parent
        out.append(flat)
        _flatten(entry.get("children", ()), out, index)
    return out


def convert_bounds(convention, bounds):
    """Convert an axis-aligned box into the target's space.

    A mirroring conversion turns a minimum into a maximum, so the corners have
    to be re-sorted afterwards rather than converted in place - otherwise every
    Unreal export ends up with a box whose min is above its max on Y.
    """
    if not bounds:
        return None
    low = convention.convert_point(bounds[0])
    high = convention.convert_point(bounds[1])
    return {
        "min": [min(a, b) for a, b in zip(low, high)],
        "max": [max(a, b) for a, b in zip(low, high)],
    }


def _asset_entry(asset, convention):
    """An asset's record, with its bounds moved into the target's space."""
    data = asset.to_dict()
    if asset.bounds:
        converted = convert_bounds(convention, (asset.bounds["min"], asset.bounds["max"]))
        if converted:
            data["bounds"] = converted
    return data


def build_manifest(scene, convention, assets=(), node_names=None, extra=None):
    """Assemble the manifest dictionary for an export.

    ``assets`` are the :class:`AssetRecord` objects already written to disk, and
    ``node_names`` maps ``id(node)`` to the engine-side name the target's
    allocator handed out.  Both are optional: a live-link session sends a
    manifest with no assets at all, because the geometry travels on the wire.
    """
    convention = get_convention(convention)
    names = node_names or {}
    assets = list(assets)
    asset_by_mesh = {}
    for asset in assets:
        if asset.mesh is not None:
            asset_by_mesh.setdefault(asset.mesh, asset.identifier)

    converted_bounds = convert_bounds(convention, scene.bounds())

    manifest = {
        "format": MANIFEST_FORMAT,
        "version": SCENE_FORMAT_VERSION,
        "bridgeVersion": BRIDGE_VERSION,
        "generated": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "document": scene.document,
        "scene": scene.name,
        "target": convention.to_dict(),
        "source": {"unit": "mm", "upAxis": "+Z", "handedness": "right"},
        "stats": scene.stats(),
        "checksum": scene.checksum(),
        "materials": [m.to_dict() for m in scene.materials],
        "assets": [_asset_entry(a, convention) for a in assets],
        "nodes": [
            _node_entry(root, convention, names, asset_by_mesh)
            for root in scene.roots
        ],
    }
    manifest["flatNodes"] = _flatten(manifest["nodes"], [], -1)
    if converted_bounds:
        manifest["bounds"] = converted_bounds
    if scene.metadata:
        manifest["metadata"] = dict(scene.metadata)
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(manifest, path):
    """Write a manifest as indented JSON, which is meant to be readable."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def read_manifest(path):
    """Read a manifest back, checking that it is one and that we can read it."""
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError("%s is not a GameBridge scene manifest" % path)
    version = manifest.get("version", 0)
    if version > SCENE_FORMAT_VERSION:
        raise ValueError(
            "%s was written by a newer bridge (format %s, this build reads %s)"
            % (path, version, SCENE_FORMAT_VERSION)
        )
    return manifest
