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
"""The intermediate scene the bridge passes between FreeCAD and the engines.

Everything in here is stored in **FreeCAD space**: millimetres, Z up, right
handed, exactly as the document holds it.  Conversion to a target's space is
deferred to the moment something is written out, which is what lets one
tessellation feed a glTF file, an Unreal import and a live Blender session
without being rebuilt three times - and what keeps the conversion in one place
instead of smeared across three exporters.

The classes are plain containers with validation.  They are deliberately
picklable, JSON-serialisable and free of FreeCAD imports so the tests, the
Blender add-on and the Unreal editor scripts can all use them.
"""

import hashlib
import math
import struct

from .transform import Matrix4

__all__ = [
    "Material",
    "Mesh",
    "Node",
    "Scene",
    "SceneError",
]


class SceneError(ValueError):
    """Raised when a scene, mesh or material fails validation."""


def _clamp01(value):
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))


def _color4(value, default=(0.8, 0.8, 0.8, 1.0)):
    if value is None:
        return default
    values = [float(v) for v in value]
    if len(values) == 3:
        values.append(1.0)
    if len(values) != 4:
        raise SceneError("a colour needs 3 or 4 components, got %d" % len(values))
    return tuple(_clamp01(v) for v in values)


class Material:
    """A metallic-roughness material, the one model all three targets share.

    FreeCAD's ``ShapeAppearance`` is a Phong-ish description with ambient,
    diffuse, specular and shininess.  Unreal, Unity's URP/HDRP and Blender's
    Principled BSDF are all metallic-roughness.  Rather than have each target
    invent its own approximation, the conversion happens once in
    :mod:`gbcore.materials` and everything downstream sees this.
    """

    __slots__ = (
        "name",
        "base_color",
        "metallic",
        "roughness",
        "emissive",
        "double_sided",
        "alpha_mode",
        "source",
    )

    def __init__(
        self,
        name,
        base_color=(0.8, 0.8, 0.8, 1.0),
        metallic=0.0,
        roughness=0.5,
        emissive=(0.0, 0.0, 0.0),
        double_sided=False,
        alpha_mode="OPAQUE",
        source=None,
    ):
        self.name = str(name)
        self.base_color = _color4(base_color)
        self.metallic = _clamp01(metallic)
        self.roughness = _clamp01(roughness)
        emissive = _color4(emissive, (0.0, 0.0, 0.0, 1.0))
        self.emissive = emissive[:3]
        self.double_sided = bool(double_sided)
        if alpha_mode not in ("OPAQUE", "MASK", "BLEND"):
            raise SceneError("unknown alpha mode %r" % (alpha_mode,))
        self.alpha_mode = alpha_mode
        #: Where the material came from, e.g. the FreeCAD object's name.
        self.source = source

    @property
    def is_transparent(self):
        return self.alpha_mode != "OPAQUE" or self.base_color[3] < 1.0

    def key(self):
        """Identity for de-duplication: two materials that look the same are."""
        return (
            self.base_color,
            round(self.metallic, 6),
            round(self.roughness, 6),
            self.emissive,
            self.double_sided,
            self.alpha_mode,
        )

    def to_dict(self):
        return {
            "name": self.name,
            "baseColor": list(self.base_color),
            "metallic": self.metallic,
            "roughness": self.roughness,
            "emissive": list(self.emissive),
            "doubleSided": self.double_sided,
            "alphaMode": self.alpha_mode,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("name", "Material"),
            data.get("baseColor", (0.8, 0.8, 0.8, 1.0)),
            data.get("metallic", 0.0),
            data.get("roughness", 0.5),
            data.get("emissive", (0.0, 0.0, 0.0)),
            data.get("doubleSided", False),
            data.get("alphaMode", "OPAQUE"),
        )

    def __repr__(self):
        return "Material(%r)" % self.name


class Mesh:
    """Triangles in FreeCAD millimetres.

    Positions and normals are flat lists of three floats per vertex, UVs two per
    vertex, and indices three per triangle.  Flat lists rather than tuples of
    vectors because that is the shape both the glTF buffer writer and the live
    link want, and converting between the two for a million-triangle assembly is
    not free.
    """

    __slots__ = ("name", "positions", "normals", "uvs", "indices", "material", "source")

    def __init__(
        self, name, positions=None, indices=None, normals=None, uvs=None,
        material=None, source=None,
    ):
        self.name = str(name)
        self.positions = list(positions or [])
        self.indices = list(indices or [])
        self.normals = list(normals or [])
        self.uvs = list(uvs or [])
        #: Index into :attr:`Scene.materials`, or ``None`` for the default.
        self.material = material
        self.source = source

    # -- geometry --------------------------------------------------------

    @property
    def vertex_count(self):
        return len(self.positions) // 3

    @property
    def triangle_count(self):
        return len(self.indices) // 3

    @property
    def is_empty(self):
        return not self.indices or not self.positions

    def vertex(self, index):
        base = index * 3
        return tuple(self.positions[base:base + 3])

    def triangle(self, index):
        base = index * 3
        return tuple(self.indices[base:base + 3])

    def bounds(self):
        """Axis aligned bounds as ``(min, max)``, or ``None`` when empty."""
        if not self.positions:
            return None
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for i in range(0, len(self.positions), 3):
            for axis in range(3):
                value = self.positions[i + axis]
                if value < lo[axis]:
                    lo[axis] = value
                if value > hi[axis]:
                    hi[axis] = value
        return (tuple(lo), tuple(hi))

    def validate(self):
        """Raise :class:`SceneError` if the mesh could not be written out."""
        if len(self.positions) % 3:
            raise SceneError(
                "mesh %r: position list is not a multiple of 3" % self.name
            )
        if len(self.indices) % 3:
            raise SceneError(
                "mesh %r: index list is not a multiple of 3" % self.name
            )
        count = self.vertex_count
        if self.normals and len(self.normals) != len(self.positions):
            raise SceneError(
                "mesh %r: %d normals for %d vertices"
                % (self.name, len(self.normals) // 3, count)
            )
        if self.uvs and len(self.uvs) // 2 != count:
            raise SceneError(
                "mesh %r: %d UVs for %d vertices" % (self.name, len(self.uvs) // 2, count)
            )
        for index in self.indices:
            if index < 0 or index >= count:
                raise SceneError(
                    "mesh %r: index %d out of range for %d vertices"
                    % (self.name, index, count)
                )
        for value in self.positions:
            if not _finite(value):
                raise SceneError("mesh %r: non-finite vertex coordinate" % self.name)
        return self

    def compute_normals(self, force=False):
        """Area-weighted vertex normals, computed only when there are none.

        FreeCAD's tessellation gives per-face normals for free, so this is the
        fallback for meshes that arrive from elsewhere - an engine round trip,
        or a Mesh object with the normals stripped.
        """
        if self.normals and not force:
            return self
        count = self.vertex_count
        accum = [0.0] * (count * 3)
        pos = self.positions
        for i in range(0, len(self.indices), 3):
            a, b, c = self.indices[i], self.indices[i + 1], self.indices[i + 2]
            ax, ay, az = pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2]
            bx, by, bz = pos[b * 3], pos[b * 3 + 1], pos[b * 3 + 2]
            cx, cy, cz = pos[c * 3], pos[c * 3 + 1], pos[c * 3 + 2]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            # The cross product's length is twice the triangle area, so leaving
            # it un-normalised is what makes this area weighted.
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            for vertex in (a, b, c):
                accum[vertex * 3] += nx
                accum[vertex * 3 + 1] += ny
                accum[vertex * 3 + 2] += nz
        for i in range(count):
            x, y, z = accum[i * 3], accum[i * 3 + 1], accum[i * 3 + 2]
            length = math.sqrt(x * x + y * y + z * z)
            if length > 1e-12:
                accum[i * 3] = x / length
                accum[i * 3 + 1] = y / length
                accum[i * 3 + 2] = z / length
            else:
                accum[i * 3], accum[i * 3 + 1], accum[i * 3 + 2] = 0.0, 0.0, 1.0
        self.normals = accum
        return self

    def weld(self, position_tolerance=1e-6):
        """Merge vertices that share a position, normal and UV.

        FreeCAD tessellates face by face, so a box arrives as 12 triangles over
        36 vertices.  Welding them costs one pass and saves the engine a third
        of the buffer; splits across a hard edge survive because the normal is
        part of the key.
        """
        if not self.positions:
            return self
        quantum = max(position_tolerance, 1e-12)
        has_normals = bool(self.normals)
        has_uvs = bool(self.uvs)
        remap = {}
        order = []
        new_index = []
        for old in range(self.vertex_count):
            key = tuple(
                _quantise(self.positions[old * 3 + a], quantum) for a in range(3)
            )
            if has_normals:
                key += tuple(
                    _quantise(self.normals[old * 3 + a], 1e-4) for a in range(3)
                )
            if has_uvs:
                key += tuple(
                    _quantise(self.uvs[old * 2 + a], 1e-6) for a in range(2)
                )
            target = remap.get(key)
            if target is None:
                target = len(order)
                remap[key] = target
                order.append(old)
            new_index.append(target)
        if len(order) == self.vertex_count:
            return self
        positions, normals, uvs = [], [], []
        for old in order:
            positions.extend(self.positions[old * 3:old * 3 + 3])
            if has_normals:
                normals.extend(self.normals[old * 3:old * 3 + 3])
            if has_uvs:
                uvs.extend(self.uvs[old * 2:old * 2 + 2])
        self.positions = positions
        self.normals = normals
        self.uvs = uvs
        self.indices = [new_index[i] for i in self.indices]
        return self

    def drop_degenerate_triangles(self, tolerance=1e-9):
        """Remove triangles with a repeated index or a vanishing area.

        Tessellating a cylinder's seam or a fillet that runs to zero width tends
        to leave slivers behind.  Unreal's static mesh build warns about them,
        Unity silently drops them, and Blender keeps them as zero-area faces
        that break its normal recalculation, so the bridge removes them itself.
        """
        pos = self.positions
        kept = []
        for i in range(0, len(self.indices), 3):
            a, b, c = self.indices[i], self.indices[i + 1], self.indices[i + 2]
            if a == b or b == c or a == c:
                continue
            ax, ay, az = pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2]
            bx, by, bz = pos[b * 3], pos[b * 3 + 1], pos[b * 3 + 2]
            cx, cy, cz = pos[c * 3], pos[c * 3 + 1], pos[c * 3 + 2]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            if (nx * nx + ny * ny + nz * nz) <= tolerance * tolerance:
                continue
            kept.extend((a, b, c))
        self.indices = kept
        return self

    def transformed(self, matrix, name=None):
        """A copy with ``matrix`` baked into the vertices.

        Used when a target cannot express a node's transform - Unreal's static
        mesh assets, for instance, want their pivot at the origin.
        """
        result = Mesh(
            name or self.name,
            material=self.material,
            source=self.source,
        )
        for i in range(0, len(self.positions), 3):
            result.positions.extend(
                matrix.transform_point(self.positions[i:i + 3])
            )
        if self.normals:
            for i in range(0, len(self.normals), 3):
                result.normals.extend(
                    _normalised(matrix.transform_vector(self.normals[i:i + 3]))
                )
        result.uvs = list(self.uvs)
        result.indices = list(self.indices)
        if matrix.determinant3() < 0.0:
            result.flip_winding()
        return result

    def flip_winding(self):
        """Reverse every triangle, for a conversion that mirrors the model."""
        for i in range(0, len(self.indices), 3):
            self.indices[i + 1], self.indices[i + 2] = (
                self.indices[i + 2],
                self.indices[i + 1],
            )
        return self

    def checksum(self):
        """A stable digest of the geometry, used by the live link to spot
        meshes that have not actually changed."""
        digest = hashlib.sha1()
        digest.update(self.name.encode("utf-8"))
        digest.update(struct.pack("<II", self.vertex_count, self.triangle_count))
        digest.update(struct.pack("<%df" % len(self.positions), *self.positions))
        if self.normals:
            digest.update(struct.pack("<%df" % len(self.normals), *self.normals))
        if self.uvs:
            digest.update(struct.pack("<%df" % len(self.uvs), *self.uvs))
        digest.update(struct.pack("<%dI" % len(self.indices), *self.indices))
        return digest.hexdigest()

    def to_dict(self, include_geometry=True):
        data = {
            "name": self.name,
            "vertexCount": self.vertex_count,
            "triangleCount": self.triangle_count,
            "material": self.material,
            "checksum": self.checksum(),
        }
        if include_geometry:
            data["positions"] = self.positions
            data["indices"] = self.indices
            if self.normals:
                data["normals"] = self.normals
            if self.uvs:
                data["uvs"] = self.uvs
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("name", "Mesh"),
            data.get("positions"),
            data.get("indices"),
            data.get("normals"),
            data.get("uvs"),
            data.get("material"),
        )

    def __repr__(self):
        return "Mesh(%r, %d tris)" % (self.name, self.triangle_count)


class Node:
    """A named placement in the scene graph, optionally carrying a mesh."""

    __slots__ = ("name", "transform", "mesh", "children", "visible", "source", "metadata")

    def __init__(
        self, name, transform=None, mesh=None, children=None, visible=True,
        source=None, metadata=None,
    ):
        self.name = str(name)
        self.transform = transform if transform is not None else Matrix4()
        #: Index into :attr:`Scene.meshes`, or ``None`` for a pure group.
        self.mesh = mesh
        self.children = list(children or [])
        self.visible = bool(visible)
        #: The FreeCAD object this came from, so a round trip can find it again.
        self.source = source
        self.metadata = dict(metadata or {})

    def add(self, child):
        self.children.append(child)
        return child

    def walk(self):
        """Depth-first iteration over this node and its descendants."""
        yield self
        for child in self.children:
            for node in child.walk():
                yield node

    def to_dict(self):
        data = {
            "name": self.name,
            "transform": list(self.transform.m),
            "visible": self.visible,
        }
        if self.mesh is not None:
            data["mesh"] = self.mesh
        if self.source:
            data["source"] = self.source
        if self.metadata:
            data["metadata"] = self.metadata
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("name", "Node"),
            Matrix4(data["transform"]) if "transform" in data else None,
            data.get("mesh"),
            [cls.from_dict(c) for c in data.get("children", ())],
            data.get("visible", True),
            data.get("source"),
            data.get("metadata"),
        )

    def __repr__(self):
        return "Node(%r, %d children)" % (self.name, len(self.children))


class Scene:
    """A whole document: a forest of nodes plus shared mesh and material pools."""

    def __init__(self, name="Scene", document=None):
        self.name = str(name)
        self.document = document
        self.roots = []
        self.meshes = []
        self.materials = []
        #: Free-form provenance: FreeCAD version, tessellation settings, ...
        self.metadata = {}

    # -- building --------------------------------------------------------

    def add_root(self, node):
        self.roots.append(node)
        return node

    def add_mesh(self, mesh):
        self.meshes.append(mesh)
        return len(self.meshes) - 1

    def add_material(self, material, deduplicate=True):
        if deduplicate:
            key = material.key()
            for index, existing in enumerate(self.materials):
                if existing.key() == key:
                    return index
        self.materials.append(material)
        return len(self.materials) - 1

    # -- inspection ------------------------------------------------------

    def walk(self):
        for root in self.roots:
            for node in root.walk():
                yield node

    def nodes_with_meshes(self):
        return [n for n in self.walk() if n.mesh is not None]

    def world_transforms(self):
        """Every node's accumulated transform, as ``(node, matrix)`` pairs."""
        result = []
        stack = [(root, Matrix4()) for root in reversed(self.roots)]
        while stack:
            node, parent = stack.pop()
            world = parent * node.transform
            result.append((node, world))
            for child in reversed(node.children):
                stack.append((child, world))
        return result

    def bounds(self):
        """World-space bounds in millimetres, or ``None`` for an empty scene."""
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        found = False
        for node, world in self.world_transforms():
            if node.mesh is None or not node.visible:
                continue
            mesh = self.meshes[node.mesh]
            for i in range(0, len(mesh.positions), 3):
                x, y, z = world.transform_point(mesh.positions[i:i + 3])
                found = True
                for axis, value in enumerate((x, y, z)):
                    if value < lo[axis]:
                        lo[axis] = value
                    if value > hi[axis]:
                        hi[axis] = value
        return (tuple(lo), tuple(hi)) if found else None

    def stats(self):
        visible = [n for n in self.walk() if n.mesh is not None and n.visible]
        return {
            "nodes": sum(1 for _ in self.walk()),
            "meshes": len(self.meshes),
            "materials": len(self.materials),
            "visibleMeshNodes": len(visible),
            "triangles": sum(m.triangle_count for m in self.meshes),
            "vertices": sum(m.vertex_count for m in self.meshes),
        }

    def validate(self):
        for mesh in self.meshes:
            mesh.validate()
            if mesh.material is not None and not (
                0 <= mesh.material < len(self.materials)
            ):
                raise SceneError(
                    "mesh %r references material %r, which does not exist"
                    % (mesh.name, mesh.material)
                )
        seen = set()
        for node in self.walk():
            if id(node) in seen:
                raise SceneError("node %r appears twice in the graph" % node.name)
            seen.add(id(node))
            if node.mesh is not None and not (0 <= node.mesh < len(self.meshes)):
                raise SceneError(
                    "node %r references mesh %r, which does not exist"
                    % (node.name, node.mesh)
                )
        return self

    def prune_empty(self):
        """Drop nodes that carry nothing: no mesh, no visible descendant."""

        def keep(node):
            node.children = [c for c in node.children if keep(c)]
            return node.mesh is not None or bool(node.children)

        self.roots = [r for r in self.roots if keep(r)]
        return self

    def checksum(self):
        digest = hashlib.sha1()
        digest.update(self.name.encode("utf-8"))
        for mesh in self.meshes:
            digest.update(mesh.checksum().encode("ascii"))
        for material in self.materials:
            digest.update(repr(sorted(material.to_dict().items())).encode("utf-8"))
        for node, world in self.world_transforms():
            digest.update(node.name.encode("utf-8"))
            digest.update(struct.pack("<16d", *world.m))
            digest.update(b"\x01" if node.visible else b"\x00")
        return digest.hexdigest()

    # -- serialisation ---------------------------------------------------

    def to_dict(self, include_geometry=True):
        return {
            "name": self.name,
            "document": self.document,
            "unit": "mm",
            "upAxis": "+Z",
            "handedness": "right",
            "metadata": self.metadata,
            "materials": [m.to_dict() for m in self.materials],
            "meshes": [m.to_dict(include_geometry) for m in self.meshes],
            "roots": [n.to_dict() for n in self.roots],
        }

    @classmethod
    def from_dict(cls, data):
        scene = cls(data.get("name", "Scene"), data.get("document"))
        scene.metadata = dict(data.get("metadata") or {})
        scene.materials = [Material.from_dict(m) for m in data.get("materials", ())]
        scene.meshes = [Mesh.from_dict(m) for m in data.get("meshes", ())]
        scene.roots = [Node.from_dict(n) for n in data.get("roots", ())]
        return scene

    def __repr__(self):
        stats = self.stats()
        return "Scene(%r, %d nodes, %d meshes, %d tris)" % (
            self.name,
            stats["nodes"],
            stats["meshes"],
            stats["triangles"],
        )


def _finite(value):
    return value == value and value not in (float("inf"), float("-inf"))


def _quantise(value, quantum):
    return int(round(value / quantum))


def _normalised(vector):
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)
