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
"""FCXR v1 container — reader and writer.

``.fcxr`` is the portable scene package moved between desktop FreeCAD, the
Quest headset, the LAN sync server and Google Drive.  It is a GLB-style
chunked binary so it can be parsed with zero third party dependencies on both
ends (see ``Resources/doc/ARCHITECTURE.md`` §1).

Layout::

    Header (12 bytes, little endian)
      uint8[4] magic   = 'F','C','X','R'
      uint32   version = 1
      uint32   total_length      (whole file, including this header)

    Chunk (repeated until total_length)
      uint32   payload_length    (not including this 8 byte chunk header)
      uint8[4] type              'JSON' | 'BIN\\0' | 'PNG\\0'
      uint8[payload_length] payload
      padding to a 4 byte boundary ( 0x20 for JSON, 0x00 for binary )

Note on ``payload_length``: unlike glTF/GLB, the architecture document defines
``payload_length`` as the length of the *unpadded* payload; the alignment
padding follows the payload and is **not** counted in ``payload_length`` (it is
of course counted in ``total_length``).  The reader accepts either reading
because a payload length that already includes the padding simply describes a
payload with trailing pad bytes, but the writer always emits the unpadded form
so that ``quest/app/src/main/cpp/fcxr.cpp`` and this module agree byte for byte.

This module is pure standard library (``struct``/``json``/``zlib``/``array``)
and must never import FreeCAD or pivy at module scope — it has to stay unit
testable without FreeCAD present.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import struct
import sys
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

__all__ = [
    "FCXR_MAGIC",
    "FCXR_VERSION",
    "CHUNK_JSON",
    "CHUNK_BIN",
    "CHUNK_PNG",
    "COMPONENT_SIZES",
    "TYPE_COMPONENT_COUNTS",
    "FcxrError",
    "FcxrDocument",
    "FcxrReader",
    "FcxrWriter",
    "content_hash",
    "read",
]

FCXR_MAGIC = b"FCXR"
FCXR_VERSION = 1

CHUNK_JSON = b"JSON"
CHUNK_BIN = b"BIN\x00"
CHUNK_PNG = b"PNG\x00"

_HEADER_STRUCT = struct.Struct("<4sII")
_CHUNK_STRUCT = struct.Struct("<I4s")
HEADER_SIZE = _HEADER_STRUCT.size          # 12
CHUNK_HEADER_SIZE = _CHUNK_STRUCT.size     # 8

#: bytes per component
COMPONENT_SIZES = {"F32": 4, "U32": 4, "U16": 2, "U8": 1}
#: components per element
TYPE_COMPONENT_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
#: :mod:`array` type codes used to (un)pack accessor payloads
_ARRAY_CODES = {"F32": "f", "U32": "I", "U16": "H", "U8": "B"}

GENERATOR = "FreeCAD-XR 1.0"
DEFAULT_UNIT_SCALE = 0.001  # document units (mm) -> metres

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FcxrError(Exception):
    """Raised for any malformed, inconsistent or unsupported FCXR data."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def content_hash(data: bytes) -> str:
    """Return a short stable content hash (sha256 hex, first 16 characters)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("content_hash() expects bytes")
    return hashlib.sha256(bytes(data)).hexdigest()[:16]


def _pad_to_4(n: int) -> int:
    """Number of padding bytes needed to reach the next 4 byte boundary."""
    return (-n) & 3


def _array_code(component: str) -> str:
    try:
        return _ARRAY_CODES[component]
    except KeyError:
        raise FcxrError("unknown component type %r" % (component,)) from None


def _check_array_itemsizes() -> None:
    for comp, code in _ARRAY_CODES.items():
        if array.array(code).itemsize != COMPONENT_SIZES[comp]:
            raise FcxrError(
                "platform array itemsize mismatch for %s (%d != %d)"
                % (comp, array.array(code).itemsize, COMPONENT_SIZES[comp])
            )


_check_array_itemsizes()


def _flatten(values: Iterable[Any], stride: int, what: str) -> List[Any]:
    """Accept either a flat sequence or a sequence of tuples/lists."""
    if values is None:
        raise FcxrError("%s: no data" % what)
    out: List[Any] = []
    for item in values:
        if isinstance(item, (list, tuple)):
            if len(item) != stride:
                raise FcxrError(
                    "%s: nested element has %d components, expected %d"
                    % (what, len(item), stride)
                )
            out.extend(item)
        elif hasattr(item, "x") and hasattr(item, "y"):
            # FreeCAD Vector / Base.Vector duck typing (never imported here)
            comps = [item.x, item.y]
            if stride >= 3:
                comps.append(getattr(item, "z", 0.0))
            if stride == 4:
                comps.append(getattr(item, "w", 0.0))
            if len(comps) != stride:
                raise FcxrError("%s: vector element does not match stride" % what)
            out.extend(comps)
        else:
            out.append(item)
    if stride and len(out) % stride:
        raise FcxrError(
            "%s: %d values are not a multiple of %d" % (what, len(out), stride)
        )
    return out


def _as_float_list(values: Iterable[Any], n: int, what: str) -> List[float]:
    out = [float(v) for v in values]
    if len(out) != n:
        raise FcxrError("%s: expected %d numbers, got %d" % (what, n, len(out)))
    return out


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


class FcxrWriter:
    """Builds an FCXR v1 package.

    The writer is deterministic: for identical inputs (including an identical
    or omitted ``created`` timestamp) :meth:`to_bytes` returns identical bytes,
    so :func:`content_hash` of the result is meaningful for change detection.
    """

    def __init__(
        self,
        source_document: Optional[str] = None,
        unit_scale: float = DEFAULT_UNIT_SCALE,
        generator: str = GENERATOR,
        created: Optional[str] = None,
        index_component: str = "auto",
    ) -> None:
        if index_component not in ("auto", "U32", "U16"):
            raise FcxrError("index_component must be 'auto', 'U32' or 'U16'")
        self.source_document = source_document
        self.unit_scale = float(unit_scale)
        self.generator = generator
        self.created = created
        self.index_component = index_component

        self._bin = bytearray()
        self._accessors: List[Dict[str, Any]] = []
        self._meshes: List[Dict[str, Any]] = []
        self._materials: List[Dict[str, Any]] = []
        self._nodes: List[Dict[str, Any]] = []
        self._images: List[Dict[str, Any]] = []
        self._image_chunks: List[bytes] = []
        self._scene: Dict[str, Any] = {"root": 0}
        self._paint: Optional[Dict[str, Any]] = None
        self._vector: Optional[Dict[str, Any]] = None
        self._extra_asset: Dict[str, Any] = {}

    # -- accessors ---------------------------------------------------------

    def add_accessor(self, values: Sequence[Any], type_: str, component: str) -> int:
        """Append raw ``values`` to the binary buffer, returning its index."""
        if type_ not in TYPE_COMPONENT_COUNTS:
            raise FcxrError("unknown accessor type %r" % (type_,))
        code = _array_code(component)
        ncomp = TYPE_COMPONENT_COUNTS[type_]
        if len(values) % ncomp:
            raise FcxrError(
                "accessor %s: %d values are not a multiple of %d"
                % (type_, len(values), ncomp)
            )
        if component == "F32":
            arr = array.array(code, [float(v) for v in values])
        else:
            limit = 1 << (8 * COMPONENT_SIZES[component])
            ints = []
            for v in values:
                iv = int(v)
                if iv < 0 or iv >= limit:
                    raise FcxrError(
                        "accessor %s/%s: value %r out of range" % (type_, component, v)
                    )
                ints.append(iv)
            arr = array.array(code, ints)
        if sys.byteorder != "little":  # pragma: no cover - big endian hosts
            arr.byteswap()
        payload = arr.tobytes()

        # accessor offsets must be 4 byte aligned (§1)
        self._bin.extend(b"\x00" * _pad_to_4(len(self._bin)))
        offset = len(self._bin)
        self._bin.extend(payload)
        self._accessors.append(
            {
                "offset": offset,
                "length": len(payload),
                "type": type_,
                "component": component,
                "count": len(values) // ncomp,
            }
        )
        return len(self._accessors) - 1

    # -- materials ---------------------------------------------------------

    def add_material(
        self,
        name: str,
        base_color: Sequence[float] = (0.8, 0.8, 0.8, 1.0),
        metallic: float = 0.0,
        roughness: float = 0.6,
        emissive: Sequence[float] = (0.0, 0.0, 0.0),
        base_color_texture: Optional[int] = None,
        double_sided: bool = False,
    ) -> int:
        """Append a PBR material, returning its index."""
        if base_color_texture is not None:
            base_color_texture = int(base_color_texture)
            if base_color_texture < 0:
                raise FcxrError("base_color_texture must be a non negative index")
        self._materials.append(
            {
                "name": str(name),
                "base_color": _as_float_list(base_color, 4, "material base_color"),
                "metallic": float(metallic),
                "roughness": float(roughness),
                "emissive": _as_float_list(emissive, 3, "material emissive"),
                "base_color_texture": base_color_texture,
                "double_sided": bool(double_sided),
            }
        )
        return len(self._materials) - 1

    # -- images ------------------------------------------------------------

    def add_image(self, name: str, png_bytes: bytes) -> int:
        """Append a PNG image chunk, returning the image index."""
        if not isinstance(png_bytes, (bytes, bytearray, memoryview)):
            raise FcxrError("add_image() expects bytes")
        png_bytes = bytes(png_bytes)
        if not png_bytes.startswith(_PNG_SIGNATURE):
            raise FcxrError("image %r is not a PNG (bad signature)" % (name,))
        index = len(self._images)
        self._images.append(
            {"name": str(name), "mime": "image/png", "chunk": index}
        )
        self._image_chunks.append(png_bytes)
        return index

    # -- meshes ------------------------------------------------------------

    def add_mesh(
        self,
        name: str,
        positions: Sequence[Any],
        normals: Optional[Sequence[Any]] = None,
        uvs: Optional[Sequence[Any]] = None,
        indices: Optional[Sequence[Any]] = None,
        material: Optional[int] = None,
    ) -> int:
        """Add a single primitive mesh, returning the mesh index."""
        mesh = {"name": str(name), "primitives": []}
        self._meshes.append(mesh)
        mesh_index = len(self._meshes) - 1
        self.add_primitive(mesh_index, positions, normals, uvs, indices, material)
        return mesh_index

    def add_primitive(
        self,
        mesh_index: int,
        positions: Sequence[Any],
        normals: Optional[Sequence[Any]] = None,
        uvs: Optional[Sequence[Any]] = None,
        indices: Optional[Sequence[Any]] = None,
        material: Optional[int] = None,
    ) -> int:
        """Add another primitive to an existing mesh, returning its index."""
        if not 0 <= mesh_index < len(self._meshes):
            raise FcxrError("mesh index %r out of range" % (mesh_index,))

        pos = _flatten(positions, 3, "positions")
        if not pos:
            raise FcxrError("positions: a primitive needs at least one vertex")
        vertex_count = len(pos) // 3

        prim: Dict[str, Any] = {"positions": self.add_accessor(pos, "VEC3", "F32")}

        if normals is not None:
            nrm = _flatten(normals, 3, "normals")
            if len(nrm) // 3 != vertex_count:
                raise FcxrError(
                    "normals: %d vertices, expected %d"
                    % (len(nrm) // 3, vertex_count)
                )
            prim["normals"] = self.add_accessor(nrm, "VEC3", "F32")
        else:
            prim["normals"] = None

        if uvs is not None:
            uv = _flatten(uvs, 2, "uvs")
            if len(uv) // 2 != vertex_count:
                raise FcxrError(
                    "uvs: %d vertices, expected %d" % (len(uv) // 2, vertex_count)
                )
            prim["uvs"] = self.add_accessor(uv, "VEC2", "F32")
        else:
            prim["uvs"] = None

        if indices is not None:
            idx = _flatten(indices, 3, "indices")
            for i in idx:
                if int(i) < 0 or int(i) >= vertex_count:
                    raise FcxrError(
                        "indices: value %r out of range (%d vertices)"
                        % (i, vertex_count)
                    )
            component = self._index_component_for(idx)
            prim["indices"] = self.add_accessor(idx, "SCALAR", component)
        else:
            prim["indices"] = None

        if material is not None:
            material = int(material)
            if not 0 <= material < len(self._materials):
                raise FcxrError("material index %r out of range" % (material,))
        prim["material"] = material

        self._meshes[mesh_index]["primitives"].append(prim)
        return len(self._meshes[mesh_index]["primitives"]) - 1

    def _index_component_for(self, idx: Sequence[Any]) -> str:
        if self.index_component != "auto":
            return self.index_component
        return "U16" if (not idx or max(int(i) for i in idx) < 0x10000) else "U32"

    # -- nodes / scene -----------------------------------------------------

    def add_node(
        self,
        name: str,
        mesh: Optional[int] = None,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        rotation: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
        scale: Sequence[float] = (1.0, 1.0, 1.0),
        children: Sequence[int] = (),
        fc_name: Optional[str] = None,
        visible: bool = True,
    ) -> int:
        """Append a scene node, returning its index."""
        if mesh is not None:
            mesh = int(mesh)
            if not 0 <= mesh < len(self._meshes):
                raise FcxrError("node %r: mesh index %r out of range" % (name, mesh))
        kids = [int(c) for c in children]
        self._nodes.append(
            {
                "name": str(name),
                "mesh": mesh,
                "translation": _as_float_list(translation, 3, "node translation"),
                "rotation": _as_float_list(rotation, 4, "node rotation"),
                "scale": _as_float_list(scale, 3, "node scale"),
                "children": kids,
                "fc_name": str(fc_name) if fc_name is not None else str(name),
                "visible": bool(visible),
            }
        )
        return len(self._nodes) - 1

    def set_node_children(self, node_index: int, children: Sequence[int]) -> None:
        """Replace a node's children (useful when building trees bottom up)."""
        if not 0 <= node_index < len(self._nodes):
            raise FcxrError("node index %r out of range" % (node_index,))
        self._nodes[node_index]["children"] = [int(c) for c in children]

    def set_scene(
        self,
        root: int = 0,
        environment: Optional[str] = None,
        user_scale: Optional[float] = None,
    ) -> None:
        """Set the scene block (root node, environment id and user scale)."""
        scene: Dict[str, Any] = {"root": int(root)}
        if environment is not None:
            scene["environment"] = str(environment)
        if user_scale is not None:
            scene["user_scale"] = float(user_scale)
        self._scene = scene

    def set_asset_field(self, key: str, value: Any) -> None:
        """Add an extra key to the ``asset`` block (forward compatible)."""
        self._extra_asset[str(key)] = value

    # -- paint / vector ----------------------------------------------------

    def set_paint(self, paint: Optional[Dict[str, Any]]) -> None:
        """Attach a paint document (see §4); ``None`` removes it."""
        if paint is None:
            self._paint = None
            return
        if not isinstance(paint, dict):
            raise FcxrError("paint document must be a dict")
        validate_paint(paint, image_count=len(self._images))
        self._paint = paint

    def set_vector(self, vector: Optional[Dict[str, Any]]) -> None:
        """Attach a vector document (see §4); ``None`` removes it."""
        if vector is None:
            self._vector = None
            return
        if not isinstance(vector, dict):
            raise FcxrError("vector document must be a dict")
        validate_vector(vector)
        self._vector = vector

    # -- output ------------------------------------------------------------

    def build_manifest(self) -> Dict[str, Any]:
        """Return the manifest that :meth:`to_bytes` would serialise."""
        asset: Dict[str, Any] = {
            "generator": self.generator,
            "version": FCXR_VERSION,
            "unit_scale": self.unit_scale,
        }
        if self.created is not None:
            asset["created"] = str(self.created)
        if self.source_document is not None:
            asset["source_document"] = str(self.source_document)
        asset.update(self._extra_asset)

        manifest: Dict[str, Any] = {
            "asset": asset,
            "scene": dict(self._scene),
            "nodes": [dict(n) for n in self._nodes],
            "meshes": [
                {"name": m["name"], "primitives": [dict(p) for p in m["primitives"]]}
                for m in self._meshes
            ],
            "accessors": [dict(a) for a in self._accessors],
            "materials": [dict(m) for m in self._materials],
            "images": [dict(i) for i in self._images],
        }
        if self._paint is not None:
            manifest["paint"] = self._paint
        if self._vector is not None:
            manifest["vector"] = self._vector
        return manifest

    def to_bytes(self) -> bytes:
        """Serialise the package to bytes."""
        manifest = self.build_manifest()
        validate_manifest(manifest, bin_length=len(self._bin),
                          image_chunk_count=len(self._image_chunks))
        json_payload = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

        chunks: List[Tuple[bytes, bytes, int]] = [(CHUNK_JSON, json_payload, 0x20)]
        if self._bin:
            chunks.append((CHUNK_BIN, bytes(self._bin), 0x00))
        for png in self._image_chunks:
            chunks.append((CHUNK_PNG, png, 0x00))

        body = bytearray()
        for ctype, payload, pad_byte in chunks:
            body.extend(_CHUNK_STRUCT.pack(len(payload), ctype))
            body.extend(payload)
            body.extend(bytes([pad_byte]) * _pad_to_4(len(payload)))

        total = HEADER_SIZE + len(body)
        out = bytearray(_HEADER_STRUCT.pack(FCXR_MAGIC, FCXR_VERSION, total))
        out.extend(body)
        return bytes(out)

    def write(self, path: Union[str, "os.PathLike[str]"]) -> str:
        """Write the package to ``path`` (atomically) and return the path."""
        data = self.to_bytes()
        path = os.fspath(path)
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return path

    def content_hash(self) -> str:
        """Short content hash of the serialised package."""
        return content_hash(self.to_bytes())


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FcxrError(message)


def validate_paint(paint: Dict[str, Any], image_count: Optional[int] = None) -> None:
    """Validate a paint document against §4 (lenient about extra keys)."""
    _require(isinstance(paint, dict), "paint: not an object")
    version = paint.get("version", 1)
    _require(isinstance(version, int) and version >= 1, "paint: bad version")
    targets = paint.get("targets", [])
    _require(isinstance(targets, list), "paint: targets must be a list")
    for target in targets:
        _require(isinstance(target, dict), "paint: target must be an object")
        _require("fc_name" in target, "paint: target without fc_name")
        layers = target.get("layers", [])
        _require(isinstance(layers, list), "paint: layers must be a list")
        for layer in layers:
            _require(isinstance(layer, dict), "paint: layer must be an object")
            image = layer.get("image")
            if image is not None and image_count is not None:
                _require(
                    isinstance(image, int) and 0 <= image < image_count,
                    "paint: layer image index %r out of range" % (image,),
                )
            blend = layer.get("blend", "normal")
            _require(
                blend in ("normal", "multiply", "add", "erase"),
                "paint: unknown blend mode %r" % (blend,),
            )
    strokes = paint.get("strokes3d", [])
    _require(isinstance(strokes, list), "paint: strokes3d must be a list")
    for stroke in strokes:
        _require(isinstance(stroke, dict), "paint: stroke must be an object")
        points = stroke.get("points", [])
        _require(isinstance(points, list), "paint: stroke points must be a list")


def validate_vector(vector: Dict[str, Any]) -> None:
    """Validate a vector document against §4 (lenient about extra keys)."""
    _require(isinstance(vector, dict), "vector: not an object")
    version = vector.get("version", 1)
    _require(isinstance(version, int) and version >= 1, "vector: bad version")
    paths = vector.get("paths", [])
    _require(isinstance(paths, list), "vector: paths must be a list")
    for path in paths:
        _require(isinstance(path, dict), "vector: path must be an object")
        nodes = path.get("nodes", [])
        _require(isinstance(nodes, list), "vector: path nodes must be a list")
        for node in nodes:
            _require(isinstance(node, dict), "vector: path node must be an object")
            point = node.get("point")
            _require(
                isinstance(point, (list, tuple)) and len(point) == 2,
                "vector: path node needs a 2D point",
            )
            ntype = node.get("type", "corner")
            _require(
                ntype in ("corner", "smooth", "symmetric"),
                "vector: unknown node type %r" % (ntype,),
            )
        target = path.get("target", "draft")
        _require(
            target in ("draft", "sketch", "annotation"),
            "vector: unknown target %r" % (target,),
        )


def validate_manifest(
    manifest: Dict[str, Any],
    bin_length: int = 0,
    image_chunk_count: int = 0,
) -> None:
    """Strictly validate a manifest and its cross references."""
    _require(isinstance(manifest, dict), "manifest: not a JSON object")

    asset = manifest.get("asset")
    _require(isinstance(asset, dict), "manifest: missing 'asset' object")
    _require(
        asset.get("version") == FCXR_VERSION,
        "manifest: unsupported asset version %r" % (asset.get("version"),),
    )
    unit_scale = asset.get("unit_scale", DEFAULT_UNIT_SCALE)
    _require(
        isinstance(unit_scale, (int, float)) and unit_scale > 0.0,
        "manifest: bad unit_scale %r" % (unit_scale,),
    )

    accessors = manifest.get("accessors", [])
    _require(isinstance(accessors, list), "manifest: 'accessors' must be a list")
    for i, acc in enumerate(accessors):
        _require(isinstance(acc, dict), "accessor %d: not an object" % i)
        type_ = acc.get("type")
        component = acc.get("component")
        _require(type_ in TYPE_COMPONENT_COUNTS, "accessor %d: bad type %r" % (i, type_))
        _require(
            component in COMPONENT_SIZES,
            "accessor %d: bad component %r" % (i, component),
        )
        offset = acc.get("offset")
        length = acc.get("length")
        count = acc.get("count")
        for key, value in (("offset", offset), ("length", length), ("count", count)):
            _require(
                isinstance(value, int) and value >= 0,
                "accessor %d: bad %s %r" % (i, key, value),
            )
        _require(offset % 4 == 0, "accessor %d: offset %d is not 4 byte aligned" % (i, offset))
        expect = count * TYPE_COMPONENT_COUNTS[type_] * COMPONENT_SIZES[component]
        _require(
            length == expect,
            "accessor %d: length %d does not match count %d (expected %d)"
            % (i, length, count, expect),
        )
        _require(
            offset + length <= bin_length,
            "accessor %d: [%d,%d) exceeds the %d byte BIN chunk"
            % (i, offset, offset + length, bin_length),
        )

    images = manifest.get("images", [])
    _require(isinstance(images, list), "manifest: 'images' must be a list")
    for i, img in enumerate(images):
        _require(isinstance(img, dict), "image %d: not an object" % i)
        chunk = img.get("chunk")
        _require(
            isinstance(chunk, int) and 0 <= chunk < image_chunk_count,
            "image %d: chunk %r out of range (%d PNG chunks)"
            % (i, chunk, image_chunk_count),
        )

    materials = manifest.get("materials", [])
    _require(isinstance(materials, list), "manifest: 'materials' must be a list")
    for i, mat in enumerate(materials):
        _require(isinstance(mat, dict), "material %d: not an object" % i)
        tex = mat.get("base_color_texture")
        if tex is not None:
            _require(
                isinstance(tex, int) and 0 <= tex < len(images),
                "material %d: base_color_texture %r out of range" % (i, tex),
            )
        base_color = mat.get("base_color", [1.0, 1.0, 1.0, 1.0])
        _require(
            isinstance(base_color, (list, tuple)) and len(base_color) == 4,
            "material %d: base_color must have 4 components" % i,
        )

    meshes = manifest.get("meshes", [])
    _require(isinstance(meshes, list), "manifest: 'meshes' must be a list")
    for i, mesh in enumerate(meshes):
        _require(isinstance(mesh, dict), "mesh %d: not an object" % i)
        prims = mesh.get("primitives", [])
        _require(isinstance(prims, list) and prims, "mesh %d: no primitives" % i)
        for j, prim in enumerate(prims):
            _require(isinstance(prim, dict), "mesh %d primitive %d: not an object" % (i, j))
            pos = prim.get("positions")
            _require(
                isinstance(pos, int) and 0 <= pos < len(accessors),
                "mesh %d primitive %d: bad positions accessor %r" % (i, j, pos),
            )
            _require(
                accessors[pos]["type"] == "VEC3" and accessors[pos]["component"] == "F32",
                "mesh %d primitive %d: positions must be VEC3/F32" % (i, j),
            )
            vcount = accessors[pos]["count"]
            for key, want_type in (("normals", "VEC3"), ("uvs", "VEC2")):
                ref = prim.get(key)
                if ref is None:
                    continue
                _require(
                    isinstance(ref, int) and 0 <= ref < len(accessors),
                    "mesh %d primitive %d: bad %s accessor %r" % (i, j, key, ref),
                )
                _require(
                    accessors[ref]["type"] == want_type,
                    "mesh %d primitive %d: %s must be %s" % (i, j, key, want_type),
                )
                _require(
                    accessors[ref]["count"] == vcount,
                    "mesh %d primitive %d: %s count %d != vertex count %d"
                    % (i, j, key, accessors[ref]["count"], vcount),
                )
            ind = prim.get("indices")
            if ind is not None:
                _require(
                    isinstance(ind, int) and 0 <= ind < len(accessors),
                    "mesh %d primitive %d: bad indices accessor %r" % (i, j, ind),
                )
                _require(
                    accessors[ind]["type"] == "SCALAR",
                    "mesh %d primitive %d: indices must be SCALAR" % (i, j),
                )
                _require(
                    accessors[ind]["count"] % 3 == 0,
                    "mesh %d primitive %d: index count %d is not a multiple of 3"
                    % (i, j, accessors[ind]["count"]),
                )
            mat = prim.get("material")
            if mat is not None:
                _require(
                    isinstance(mat, int) and 0 <= mat < len(materials),
                    "mesh %d primitive %d: material %r out of range" % (i, j, mat),
                )

    nodes = manifest.get("nodes", [])
    _require(isinstance(nodes, list), "manifest: 'nodes' must be a list")
    for i, node in enumerate(nodes):
        _require(isinstance(node, dict), "node %d: not an object" % i)
        mesh = node.get("mesh")
        if mesh is not None:
            _require(
                isinstance(mesh, int) and 0 <= mesh < len(meshes),
                "node %d: mesh %r out of range" % (i, mesh),
            )
        for key, size in (("translation", 3), ("rotation", 4), ("scale", 3)):
            value = node.get(key)
            if value is None:
                continue
            _require(
                isinstance(value, (list, tuple)) and len(value) == size,
                "node %d: %s must have %d components" % (i, key, size),
            )
        children = node.get("children", [])
        _require(isinstance(children, list), "node %d: children must be a list" % i)
        for child in children:
            _require(
                isinstance(child, int) and 0 <= child < len(nodes),
                "node %d: child %r out of range" % (i, child),
            )
            _require(child != i, "node %d: is its own child" % i)

    scene = manifest.get("scene")
    _require(isinstance(scene, dict), "manifest: missing 'scene' object")
    root = scene.get("root", 0)
    if nodes:
        _require(
            isinstance(root, int) and 0 <= root < len(nodes),
            "scene: root %r out of range" % (root,),
        )
    if "paint" in manifest:
        validate_paint(manifest["paint"], image_count=len(images))
    if "vector" in manifest:
        validate_vector(manifest["vector"])


# ---------------------------------------------------------------------------
# reader
# ---------------------------------------------------------------------------


class FcxrDocument:
    """A parsed FCXR package."""

    __slots__ = ("manifest", "bin", "images", "_raw")

    def __init__(
        self,
        manifest: Dict[str, Any],
        bin_data: bytes = b"",
        images: Optional[List[bytes]] = None,
        raw: bytes = b"",
    ) -> None:
        self.manifest = manifest
        self.bin = bin_data
        self.images = list(images or [])
        self._raw = raw

    # -- convenience -------------------------------------------------------

    @property
    def asset(self) -> Dict[str, Any]:
        return self.manifest.get("asset", {})

    @property
    def scene(self) -> Dict[str, Any]:
        return self.manifest.get("scene", {})

    @property
    def nodes(self) -> List[Dict[str, Any]]:
        return self.manifest.get("nodes", [])

    @property
    def meshes(self) -> List[Dict[str, Any]]:
        return self.manifest.get("meshes", [])

    @property
    def materials(self) -> List[Dict[str, Any]]:
        return self.manifest.get("materials", [])

    @property
    def accessors(self) -> List[Dict[str, Any]]:
        return self.manifest.get("accessors", [])

    @property
    def paint(self) -> Optional[Dict[str, Any]]:
        return self.manifest.get("paint")

    @property
    def vector(self) -> Optional[Dict[str, Any]]:
        return self.manifest.get("vector")

    @property
    def unit_scale(self) -> float:
        return float(self.asset.get("unit_scale", DEFAULT_UNIT_SCALE))

    def content_hash(self) -> str:
        """Content hash of the bytes this document was parsed from."""
        return content_hash(self._raw)

    def to_bytes(self) -> bytes:
        """The original bytes this document was parsed from."""
        return self._raw

    # -- data access -------------------------------------------------------

    def read_accessor(self, index: int) -> "array.array":
        """Return accessor ``index`` as an :class:`array.array`.

        ``F32`` accessors come back as ``'f'`` arrays, integer accessors as
        unsigned arrays.  The values are flat (``VEC3`` accessors yield
        ``3 * count`` numbers).
        """
        accessors = self.accessors
        if not isinstance(index, int) or not 0 <= index < len(accessors):
            raise FcxrError("accessor index %r out of range" % (index,))
        acc = accessors[index]
        offset = acc["offset"]
        length = acc["length"]
        if offset + length > len(self.bin):
            raise FcxrError("accessor %d exceeds the binary buffer" % index)
        arr = array.array(_array_code(acc["component"]))
        arr.frombytes(self.bin[offset : offset + length])
        if sys.byteorder != "little":  # pragma: no cover - big endian hosts
            arr.byteswap()
        return arr

    def image_bytes(self, index: int) -> bytes:
        """Return the PNG bytes of image ``index``."""
        images = self.manifest.get("images", [])
        if not isinstance(index, int) or not 0 <= index < len(images):
            raise FcxrError("image index %r out of range" % (index,))
        chunk = images[index]["chunk"]
        if not 0 <= chunk < len(self.images):
            raise FcxrError("image %d references missing chunk %r" % (index, chunk))
        return self.images[chunk]

    def primitive_arrays(self, mesh_index: int, primitive: int = 0) -> Dict[str, Any]:
        """Return ``{positions, normals, uvs, indices, material}`` arrays."""
        meshes = self.meshes
        if not 0 <= mesh_index < len(meshes):
            raise FcxrError("mesh index %r out of range" % (mesh_index,))
        prims = meshes[mesh_index].get("primitives", [])
        if not 0 <= primitive < len(prims):
            raise FcxrError("primitive index %r out of range" % (primitive,))
        prim = prims[primitive]
        out: Dict[str, Any] = {"material": prim.get("material")}
        for key in ("positions", "normals", "uvs", "indices"):
            ref = prim.get(key)
            out[key] = None if ref is None else self.read_accessor(ref)
        return out

    def iter_nodes(self, root: Optional[int] = None):
        """Depth first iteration over ``(index, node, parent_index)``."""
        nodes = self.nodes
        if not nodes:
            return
        start = self.scene.get("root", 0) if root is None else root
        stack = [(int(start), None)]
        seen = set()
        while stack:
            index, parent = stack.pop()
            if index in seen:
                raise FcxrError("node graph contains a cycle at node %d" % index)
            seen.add(index)
            node = nodes[index]
            yield index, node, parent
            for child in reversed(node.get("children", [])):
                stack.append((int(child), index))


class FcxrReader:
    """Parses FCXR packages from bytes or files."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    # -- API ---------------------------------------------------------------

    def read(self, path_or_bytes: Union[str, bytes, bytearray, "os.PathLike[str]"]) -> FcxrDocument:
        if isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
            return self.from_bytes(bytes(path_or_bytes))
        return self.from_file(path_or_bytes)

    def from_file(self, path: Union[str, "os.PathLike[str]"]) -> FcxrDocument:
        with open(os.fspath(path), "rb") as handle:
            return self.from_bytes(handle.read())

    def from_bytes(self, data: bytes) -> FcxrDocument:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise FcxrError("from_bytes() expects bytes")
        data = bytes(data)
        if len(data) < HEADER_SIZE:
            raise FcxrError(
                "file too short: %d bytes, need at least %d" % (len(data), HEADER_SIZE)
            )
        magic, version, total = _HEADER_STRUCT.unpack_from(data, 0)
        if magic != FCXR_MAGIC:
            raise FcxrError("bad magic %r, expected %r" % (magic, FCXR_MAGIC))
        if version != FCXR_VERSION:
            raise FcxrError("unsupported FCXR version %d (expected %d)" % (version, FCXR_VERSION))
        if total < HEADER_SIZE or total > len(data):
            raise FcxrError(
                "declared total_length %d does not fit in %d bytes" % (total, len(data))
            )
        if self.strict and total != len(data):
            raise FcxrError(
                "trailing data: total_length is %d but the file is %d bytes"
                % (total, len(data))
            )

        manifest: Optional[Dict[str, Any]] = None
        bin_data: Optional[bytes] = None
        images: List[bytes] = []

        pos = HEADER_SIZE
        chunk_no = 0
        while pos < total:
            if pos + CHUNK_HEADER_SIZE > total:
                raise FcxrError("truncated chunk header at offset %d" % pos)
            length, ctype = _CHUNK_STRUCT.unpack_from(data, pos)
            pos += CHUNK_HEADER_SIZE
            end = pos + length
            if end > total:
                raise FcxrError(
                    "chunk %d (%r) claims %d bytes but only %d remain"
                    % (chunk_no, ctype, length, total - pos)
                )
            payload = data[pos:end]
            padding = _pad_to_4(length)
            if end + padding > total:
                raise FcxrError("chunk %d (%r): missing alignment padding" % (chunk_no, ctype))
            pos = end + padding

            if ctype == CHUNK_JSON:
                if chunk_no != 0:
                    raise FcxrError("the JSON chunk must come first (found at #%d)" % chunk_no)
                if manifest is not None:
                    raise FcxrError("more than one JSON chunk")
                try:
                    manifest = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise FcxrError("manifest is not valid UTF-8 JSON: %s" % (exc,)) from None
                if not isinstance(manifest, dict):
                    raise FcxrError("manifest is not a JSON object")
            elif ctype == CHUNK_BIN:
                if bin_data is not None:
                    raise FcxrError("more than one BIN chunk")
                bin_data = payload
            elif ctype == CHUNK_PNG:
                if self.strict and not payload.startswith(_PNG_SIGNATURE):
                    raise FcxrError("PNG chunk %d has a bad signature" % len(images))
                images.append(payload)
            else:
                if self.strict:
                    raise FcxrError("unknown chunk type %r" % (ctype,))
            chunk_no += 1

        if manifest is None:
            raise FcxrError("no JSON chunk: not an FCXR package")
        bin_data = bin_data or b""
        if self.strict:
            validate_manifest(
                manifest, bin_length=len(bin_data), image_chunk_count=len(images)
            )
        return FcxrDocument(manifest, bin_data, images, raw=data)


def read(path_or_bytes: Union[str, bytes, bytearray, "os.PathLike[str]"], strict: bool = True) -> FcxrDocument:
    """Read an FCXR package from a path or bytes."""
    return FcxrReader(strict=strict).read(path_or_bytes)


def compress(data: bytes, level: int = 6) -> bytes:
    """zlib-compress a package for transports that do not do it themselves."""
    return zlib.compress(bytes(data), level)


def decompress(data: bytes) -> bytes:
    """Inverse of :func:`compress`."""
    try:
        return zlib.decompress(bytes(data))
    except zlib.error as exc:
        raise FcxrError("zlib decompression failed: %s" % (exc,)) from None
