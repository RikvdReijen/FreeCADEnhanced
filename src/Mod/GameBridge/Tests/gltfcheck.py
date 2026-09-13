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
"""A small glTF/GLB validator, so the tests check the spec and not the writer.

The official validator is a Dart binary nobody is going to have installed on a
CI runner, and a test that only asserts "the writer wrote what the writer
writes" catches nothing.  This re-reads the container and the accessors from
scratch and complains about the mistakes that actually break loaders: bad chunk
padding, accessor ranges that fall outside their buffer view, offsets that are
not aligned to the component size, indices pointing past the vertex count, and
POSITION accessors missing the min/max the spec requires.
"""

import json
import struct

_COMPONENT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GLTFValidationError(AssertionError):
    pass


def parse_glb(data):
    """Split a GLB into ``(document, binary_chunk)``, checking the container."""
    if len(data) < 12:
        raise GLTFValidationError("GLB is too short to hold a header")
    magic, version, length = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:
        raise GLTFValidationError("bad GLB magic %#x" % magic)
    if version != 2:
        raise GLTFValidationError("expected GLB version 2, got %d" % version)
    if length != len(data):
        raise GLTFValidationError(
            "GLB header claims %d bytes, file holds %d" % (length, len(data))
        )
    offset = 12
    document = None
    blob = b""
    while offset < len(data):
        if offset + 8 > len(data):
            raise GLTFValidationError("truncated chunk header at %d" % offset)
        chunk_length, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        if chunk_length % 4:
            raise GLTFValidationError(
                "chunk at %d has length %d, which is not a multiple of 4"
                % (offset, chunk_length)
            )
        payload = data[offset + 8:offset + 8 + chunk_length]
        if len(payload) != chunk_length:
            raise GLTFValidationError("chunk at %d is truncated" % offset)
        if chunk_type == 0x4E4F534A:
            if document is not None:
                raise GLTFValidationError("more than one JSON chunk")
            document = json.loads(payload.decode("utf-8"))
        elif chunk_type == 0x004E4942:
            blob = payload
        offset += 8 + chunk_length
    if document is None:
        raise GLTFValidationError("GLB has no JSON chunk")
    return document, blob


def validate(document, blob=b""):
    """Check a parsed glTF document against the parts of the spec we rely on."""
    asset = document.get("asset")
    if not asset or asset.get("version") != "2.0":
        raise GLTFValidationError("asset.version must be '2.0'")

    buffers = document.get("buffers", [])
    if blob and not buffers:
        raise GLTFValidationError("a binary chunk is present but no buffer declares it")
    for index, buffer in enumerate(buffers):
        if index == 0 and blob and buffer["byteLength"] > len(blob):
            raise GLTFValidationError(
                "buffer 0 claims %d bytes, chunk holds %d" % (buffer["byteLength"], len(blob))
            )

    views = document.get("bufferViews", [])
    for index, view in enumerate(views):
        end = view.get("byteOffset", 0) + view["byteLength"]
        limit = buffers[view["buffer"]]["byteLength"]
        if end > limit:
            raise GLTFValidationError(
                "bufferView %d runs to %d, past the buffer's %d" % (index, end, limit)
            )

    accessors = document.get("accessors", [])
    for index, accessor in enumerate(accessors):
        size = _COMPONENT_SIZE[accessor["componentType"]]
        stride = size * _TYPE_COUNT[accessor["type"]]
        offset = accessor.get("byteOffset", 0)
        if offset % size:
            raise GLTFValidationError(
                "accessor %d starts at %d, which is not aligned to its %d byte components"
                % (index, offset, size)
            )
        view = views[accessor["bufferView"]]
        view_offset = view.get("byteOffset", 0)
        if view_offset % size:
            raise GLTFValidationError(
                "bufferView %d starts at %d, unaligned for accessor %d"
                % (accessor["bufferView"], view_offset, index)
            )
        needed = offset + accessor["count"] * stride
        if needed > view["byteLength"]:
            raise GLTFValidationError(
                "accessor %d needs %d bytes, its bufferView holds %d"
                % (index, needed, view["byteLength"])
            )

    for mesh_index, mesh in enumerate(document.get("meshes", [])):
        for prim_index, primitive in enumerate(mesh["primitives"]):
            where = "mesh %d primitive %d" % (mesh_index, prim_index)
            attributes = primitive.get("attributes", {})
            if "POSITION" not in attributes:
                raise GLTFValidationError("%s has no POSITION" % where)
            position = accessors[attributes["POSITION"]]
            if "min" not in position or "max" not in position:
                raise GLTFValidationError("%s: POSITION needs min and max" % where)
            if position["type"] != "VEC3" or position["componentType"] != 5126:
                raise GLTFValidationError("%s: POSITION must be float VEC3" % where)
            count = position["count"]
            for name, accessor_index in attributes.items():
                if accessors[accessor_index]["count"] != count:
                    raise GLTFValidationError(
                        "%s: %s has %d elements, POSITION has %d"
                        % (where, name, accessors[accessor_index]["count"], count)
                    )
            if "indices" in primitive:
                indices = accessors[primitive["indices"]]
                if indices["type"] != "SCALAR":
                    raise GLTFValidationError("%s: indices must be SCALAR" % where)
                if indices["componentType"] not in (5121, 5123, 5125):
                    raise GLTFValidationError("%s: bad index component type" % where)
                if indices["count"] % 3:
                    raise GLTFValidationError(
                        "%s: %d indices is not a whole number of triangles"
                        % (where, indices["count"])
                    )
            if primitive.get("material") is not None:
                if primitive["material"] >= len(document.get("materials", [])):
                    raise GLTFValidationError("%s: material index out of range" % where)

    nodes = document.get("nodes", [])
    seen = set()
    for index, node in enumerate(nodes):
        if "matrix" in node and len(node["matrix"]) != 16:
            raise GLTFValidationError("node %d: matrix must have 16 values" % index)
        if node.get("mesh") is not None and node["mesh"] >= len(document.get("meshes", [])):
            raise GLTFValidationError("node %d: mesh index out of range" % index)
        for child in node.get("children", ()):
            if child in seen:
                raise GLTFValidationError("node %d has more than one parent" % child)
            if child == index:
                raise GLTFValidationError("node %d is its own child" % index)
            seen.add(child)

    scenes = document.get("scenes", [])
    if scenes:
        default = document.get("scene", 0)
        if default >= len(scenes):
            raise GLTFValidationError("scene index out of range")
        for root in scenes[default].get("nodes", ()):
            if root >= len(nodes):
                raise GLTFValidationError("scene root %d does not exist" % root)
    return True


def read_accessor(document, blob, index):
    """Read an accessor's values back out of the binary chunk."""
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    size = _COMPONENT_SIZE[accessor["componentType"]]
    components = _TYPE_COUNT[accessor["type"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    total = accessor["count"] * components
    code = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}[accessor["componentType"]]
    values = struct.unpack_from("<%d%s" % (total, code), blob, start)
    if components == 1:
        return list(values)
    return [tuple(values[i:i + components]) for i in range(0, total, components)]
