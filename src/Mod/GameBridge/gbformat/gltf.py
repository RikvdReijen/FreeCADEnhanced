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
"""A glTF 2.0 writer, in the standard library only.

glTF is the one interchange format all three targets read without a paid plugin:
Blender imports it natively, Unreal 5 has an Interchange path for it and Unity
takes it through glTFast or UnityGLTF.  FBX would be the other candidate, but
its binary format is undocumented and its ASCII form is enormous, so the bridge
writes glTF and lets the engine side convert if it wants an engine-native asset.

The writer is deliberately narrow.  It emits exactly what a CAD export needs -
static triangle meshes, a node hierarchy, metallic-roughness materials - and
nothing else: no skinning, no animation, no textures, no cameras.  What it does
emit, it emits to spec, including the two alignment rules that quietly break
loaders when you get them wrong:

* every ``bufferView`` for vertex data starts on a multiple of its component
  size, and every accessor offset likewise;
* every GLB chunk is padded to a multiple of four - the JSON chunk with spaces,
  the binary chunk with zeros.
"""

import base64
import json
import os
import struct

from gbcore import BRIDGE_VERSION
from gbcore.transform import FREECAD, get_convention

__all__ = ["GLTFWriter", "write_gltf", "write_glb"]

# glTF component types.
_UNSIGNED_SHORT = 5123
_UNSIGNED_INT = 5125
_FLOAT = 5126

# glTF bufferView targets.
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963

_GLB_MAGIC = 0x46546C67          # 'glTF'
_GLB_JSON_CHUNK = 0x4E4F534A     # 'JSON'
_GLB_BIN_CHUNK = 0x004E4942      # 'BIN\0'


class GLTFWriter:
    """Turns a :class:`~gbcore.scene.Scene` into glTF or GLB.

    The scene is held in FreeCAD millimetres; ``convention`` says which space to
    write it in.  The default is the glTF standard one (metres, Y up), which is
    what every conforming importer expects; passing ``BLENDER`` or ``UNITY``
    instead produces a file that is *pre*-converted, which is occasionally what
    an engine-side script wants when it intends to bypass the importer's own
    axis handling.
    """

    def __init__(self, scene, convention=None, generator=None):
        self.scene = scene
        self.convention = get_convention(convention or "gltf")
        self.generator = generator or "FreeCAD GameBridge %s" % BRIDGE_VERSION
        self._buffer = bytearray()
        self._buffer_views = []
        self._accessors = []

    # -- buffer plumbing -------------------------------------------------

    def _align(self, alignment=4):
        padding = (-len(self._buffer)) % alignment
        if padding:
            self._buffer.extend(b"\x00" * padding)

    def _add_buffer_view(self, data, target=None, stride=None, alignment=4):
        self._align(alignment)
        offset = len(self._buffer)
        self._buffer.extend(data)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        if stride is not None:
            view["byteStride"] = stride
        self._buffer_views.append(view)
        return len(self._buffer_views) - 1

    def _add_accessor(self, view, component_type, count, type_name, minimum=None, maximum=None):
        accessor = {
            "bufferView": view,
            "componentType": component_type,
            "count": count,
            "type": type_name,
        }
        if minimum is not None:
            accessor["min"] = list(minimum)
            accessor["max"] = list(maximum)
        self._accessors.append(accessor)
        return len(self._accessors) - 1

    def _add_vec3(self, values, with_bounds=False):
        data = struct.pack("<%df" % len(values), *values)
        view = self._add_buffer_view(data, _ARRAY_BUFFER, stride=12)
        minimum = maximum = None
        if with_bounds:
            minimum = [min(values[i::3]) for i in range(3)]
            maximum = [max(values[i::3]) for i in range(3)]
        return self._add_accessor(view, _FLOAT, len(values) // 3, "VEC3", minimum, maximum)

    def _add_vec2(self, values):
        data = struct.pack("<%df" % len(values), *values)
        view = self._add_buffer_view(data, _ARRAY_BUFFER, stride=8)
        return self._add_accessor(view, _FLOAT, len(values) // 2, "VEC2")

    def _add_indices(self, indices, vertex_count):
        # Staying on 16-bit indices where possible halves the index buffer, and
        # some mobile targets still prefer them.
        if vertex_count <= 0xFFFF:
            data = struct.pack("<%dH" % len(indices), *indices)
            component, alignment = _UNSIGNED_SHORT, 2
        else:
            data = struct.pack("<%dI" % len(indices), *indices)
            component, alignment = _UNSIGNED_INT, 4
        view = self._add_buffer_view(data, _ELEMENT_ARRAY_BUFFER, alignment=alignment)
        return self._add_accessor(view, component, len(indices), "SCALAR")

    # -- scene conversion ------------------------------------------------

    def _convert_mesh(self, mesh):
        """Convert one mesh into the target space and register its accessors."""
        convention = self.convention
        positions = []
        for i in range(0, len(mesh.positions), 3):
            positions.extend(convention.convert_point(mesh.positions[i:i + 3]))
        normals = []
        for i in range(0, len(mesh.normals), 3):
            normals.extend(convention.convert_direction(mesh.normals[i:i + 3]))
        indices = list(mesh.indices)
        if convention.flips_winding:
            for i in range(0, len(indices), 3):
                indices[i + 1], indices[i + 2] = indices[i + 2], indices[i + 1]

        attributes = {"POSITION": self._add_vec3(positions, with_bounds=True)}
        if normals:
            attributes["NORMAL"] = self._add_vec3(normals)
        if mesh.uvs:
            attributes["TEXCOORD_0"] = self._add_vec2(list(mesh.uvs))
        primitive = {
            "attributes": attributes,
            "indices": self._add_indices(indices, len(positions) // 3),
            "mode": 4,  # triangles
        }
        if mesh.material is not None:
            primitive["material"] = mesh.material
        return {"name": mesh.name, "primitives": [primitive]}

    def _convert_material(self, material):
        data = {
            "name": material.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": list(material.base_color),
                "metallicFactor": material.metallic,
                "roughnessFactor": material.roughness,
            },
            "doubleSided": material.double_sided,
        }
        if any(material.emissive):
            data["emissiveFactor"] = list(material.emissive)
        if material.alpha_mode != "OPAQUE":
            data["alphaMode"] = material.alpha_mode
        elif material.base_color[3] < 1.0:
            data["alphaMode"] = "BLEND"
        return data

    def _convert_node(self, node, nodes):
        """Append ``node`` and its children to ``nodes``, returning its index."""
        index = len(nodes)
        entry = {"name": node.name}
        nodes.append(entry)
        transform = self.convention.convert_matrix(node.transform)
        if not transform.is_identity():
            # glTF matrices are column-major, which is the one place a
            # row-major convention silently produces a transposed scene.
            entry["matrix"] = list(transform.column_major())
        if node.mesh is not None:
            entry["mesh"] = node.mesh
        extras = dict(node.metadata)
        if node.source:
            extras["freecadName"] = node.source
        if not node.visible:
            # glTF has no visibility flag; importers that understand extras can
            # honour it, and the rest get the node with its geometry intact.
            extras["visible"] = False
        if extras:
            entry["extras"] = extras
        children = [self._convert_node(child, nodes) for child in node.children]
        if children:
            entry["children"] = children
        return index

    def build(self):
        """Produce ``(json_document, binary_blob)``."""
        self._buffer = bytearray()
        self._buffer_views = []
        self._accessors = []

        scene = self.scene
        scene.validate()
        meshes = [self._convert_mesh(mesh) for mesh in scene.meshes]
        materials = [self._convert_material(m) for m in scene.materials]

        nodes = []
        roots = [self._convert_node(root, nodes) for root in scene.roots]

        document = {
            "asset": {
                "version": "2.0",
                "generator": self.generator,
                "extras": {
                    "freecadDocument": scene.document,
                    "sourceUnit": "mm",
                    "axisConvention": self.convention.to_dict(),
                },
            },
            "scene": 0,
            "scenes": [{"name": scene.name, "nodes": roots}],
            "nodes": nodes,
        }
        if meshes:
            document["meshes"] = meshes
        if materials:
            document["materials"] = materials
        if self._accessors:
            document["accessors"] = self._accessors
            document["bufferViews"] = self._buffer_views
        if scene.metadata:
            document["asset"]["extras"].update(scene.metadata)
        return document, bytes(self._buffer)

    # -- output ----------------------------------------------------------

    def to_glb(self):
        """The single-file binary form, which is what the engines prefer."""
        document, blob = self.build()
        if blob:
            document["buffers"] = [{"byteLength": len(blob)}]
        json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * ((-len(json_bytes)) % 4)
        blob += b"\x00" * ((-len(blob)) % 4)

        total = 12 + 8 + len(json_bytes) + (8 + len(blob) if blob else 0)
        out = bytearray()
        out.extend(struct.pack("<III", _GLB_MAGIC, 2, total))
        out.extend(struct.pack("<II", len(json_bytes), _GLB_JSON_CHUNK))
        out.extend(json_bytes)
        if blob:
            out.extend(struct.pack("<II", len(blob), _GLB_BIN_CHUNK))
            out.extend(blob)
        return bytes(out)

    def to_gltf(self, buffer_uri=None, embed=False):
        """The JSON form.

        With ``embed`` the buffer becomes a base64 data URI, which keeps the
        export to one file at the cost of a third more bytes.  Otherwise
        ``buffer_uri`` names a sidecar ``.bin`` the caller has to write; the
        blob is returned alongside so it can.
        """
        document, blob = self.build()
        if blob:
            if embed:
                uri = "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")
            else:
                uri = buffer_uri or "buffer.bin"
            document["buffers"] = [{"byteLength": len(blob), "uri": uri}]
        return document, blob


def write_glb(scene, path, convention=None):
    """Write ``scene`` as a self-contained ``.glb``.  Returns the path."""
    data = GLTFWriter(scene, convention).to_glb()
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def write_gltf(scene, path, convention=None, embed=False):
    """Write ``scene`` as ``.gltf``, with or without a sidecar ``.bin``."""
    writer = GLTFWriter(scene, convention)
    stem = os.path.splitext(os.path.basename(path))[0]
    document, blob = writer.to_gltf(stem + ".bin", embed=embed)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
    if blob and not embed:
        sidecar = os.path.join(os.path.dirname(path), stem + ".bin")
        with open(sidecar, "wb") as handle:
            handle.write(blob)
    return path
