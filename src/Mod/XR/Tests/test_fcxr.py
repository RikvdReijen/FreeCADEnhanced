# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the FCXR v1 container (ARCHITECTURE.md §1).

Runs under plain ``python3 -m unittest`` from ``src/Mod/XR`` with no FreeCAD.
"""

import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsync import fcxr  # noqa: E402
from xrsync.fcxr import (  # noqa: E402
    CHUNK_BIN,
    CHUNK_JSON,
    CHUNK_PNG,
    FCXR_MAGIC,
    FCXR_VERSION,
    FcxrError,
    FcxrReader,
    FcxrWriter,
    content_hash,
    read,
)


def make_png(width=2, height=2):
    """A tiny but valid PNG (the reader checks the signature)."""

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


TRIANGLE_POSITIONS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
TRIANGLE_NORMALS = [(0.0, 0.0, 1.0)] * 3
TRIANGLE_UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
TRIANGLE_INDICES = [(0, 1, 2)]

PAINT_DOC = {
    "version": 1,
    "targets": [
        {
            "fc_name": "Body",
            "layers": [
                {
                    "name": "Base",
                    "image": 0,
                    "opacity": 1.0,
                    "blend": "normal",
                    "visible": True,
                    "resolution": [1024, 1024],
                }
            ],
        }
    ],
    "strokes3d": [
        {
            "brush": "ribbon",
            "color": [1.0, 0.0, 0.0, 1.0],
            "width": 0.01,
            "points": [{"p": [0, 0, 0], "n": [0, 0, 1], "r": 0.01, "t": 0.0}],
        }
    ],
    "palette": [[1.0, 0.0, 0.0, 1.0]],
}

VECTOR_DOC = {
    "version": 1,
    "plane": {"origin": [0, 0, 0], "rotation": [0, 0, 0, 1]},
    "unit_scale": 0.001,
    "paths": [
        {
            "id": "p1",
            "closed": True,
            "nodes": [
                {"point": [0, 0], "in": None, "out": [1, 0], "type": "corner"},
                {"point": [10, 0], "in": [-1, 0], "out": None, "type": "smooth"},
            ],
            "stroke": {"color": [0, 0, 0, 1], "width": 0.5},
            "fill": None,
            "target": "draft",
        }
    ],
}


def build_writer(**kwargs):
    writer = FcxrWriter(source_document="Part.FCStd", **kwargs)
    steel = writer.add_material(
        "Steel", base_color=[0.5, 0.5, 0.55, 1.0], metallic=0.9, roughness=0.3
    )
    image = writer.add_image("paint_0", make_png())
    painted = writer.add_material(
        "Painted", base_color=[1.0, 1.0, 1.0, 1.0], base_color_texture=image
    )
    mesh = writer.add_mesh(
        "Body",
        positions=TRIANGLE_POSITIONS,
        normals=TRIANGLE_NORMALS,
        uvs=TRIANGLE_UVS,
        indices=TRIANGLE_INDICES,
        material=steel,
    )
    child = writer.add_node("Pad", mesh=mesh, translation=(0.0, 0.0, 0.01))
    root = writer.add_node("Body", mesh=mesh, children=[child], fc_name="Body")
    writer.set_scene(root=root, environment="bambu_x1c", user_scale=12.0)
    writer.set_paint(PAINT_DOC)
    writer.set_vector(VECTOR_DOC)
    assert painted >= 0
    return writer


def split_chunks(data):
    """Yield ``(type, payload, padding)`` for every chunk in ``data``."""
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    assert magic == FCXR_MAGIC and version == FCXR_VERSION
    pos = 12
    while pos < total:
        length, ctype = struct.unpack_from("<I4s", data, pos)
        pos += 8
        payload = data[pos : pos + length]
        pad = (-length) & 3
        padding = data[pos + length : pos + length + pad]
        pos += length + pad
        yield ctype, payload, padding


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.writer = build_writer()
        self.data = self.writer.to_bytes()
        self.doc = read(self.data)

    def test_header(self):
        magic, version, total = struct.unpack_from("<4sII", self.data, 0)
        self.assertEqual(magic, b"FCXR")
        self.assertEqual(version, 1)
        self.assertEqual(total, len(self.data))

    def test_asset_and_scene(self):
        asset = self.doc.asset
        self.assertEqual(asset["version"], 1)
        self.assertEqual(asset["unit_scale"], 0.001)
        self.assertEqual(asset["source_document"], "Part.FCStd")
        self.assertEqual(asset["generator"], fcxr.GENERATOR)
        self.assertEqual(self.doc.scene["environment"], "bambu_x1c")
        self.assertEqual(self.doc.scene["user_scale"], 12.0)
        self.assertEqual(self.doc.scene["root"], 1)

    def test_mesh_arrays_round_trip(self):
        arrays = self.doc.primitive_arrays(0)
        self.assertEqual(
            list(arrays["positions"]),
            [c for point in TRIANGLE_POSITIONS for c in point],
        )
        self.assertEqual(
            list(arrays["normals"]), [c for n in TRIANGLE_NORMALS for c in n]
        )
        self.assertEqual(list(arrays["uvs"]), [c for uv in TRIANGLE_UVS for c in uv])
        self.assertEqual(list(arrays["indices"]), [0, 1, 2])
        self.assertEqual(arrays["material"], 0)

    def test_accessor_metadata(self):
        accessors = self.doc.accessors
        self.assertEqual(accessors[0]["type"], "VEC3")
        self.assertEqual(accessors[0]["component"], "F32")
        self.assertEqual(accessors[0]["count"], 3)
        self.assertEqual(accessors[0]["length"], 36)
        self.assertEqual(accessors[2]["type"], "VEC2")
        # small index buffers are stored as U16
        self.assertEqual(accessors[3]["component"], "U16")
        self.assertEqual(accessors[3]["type"], "SCALAR")

    def test_materials(self):
        materials = self.doc.materials
        self.assertEqual(materials[0]["name"], "Steel")
        self.assertAlmostEqual(materials[0]["metallic"], 0.9)
        self.assertIsNone(materials[0]["base_color_texture"])
        self.assertEqual(materials[1]["base_color_texture"], 0)
        self.assertFalse(materials[0]["double_sided"])

    def test_images(self):
        self.assertEqual(len(self.doc.images), 1)
        self.assertEqual(self.doc.manifest["images"][0]["mime"], "image/png")
        self.assertEqual(self.doc.manifest["images"][0]["chunk"], 0)
        self.assertTrue(self.doc.image_bytes(0).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.doc.image_bytes(0), make_png())

    def test_paint_and_vector_round_trip(self):
        self.assertEqual(self.doc.paint, PAINT_DOC)
        self.assertEqual(self.doc.vector, VECTOR_DOC)

    def test_nodes_and_traversal(self):
        nodes = self.doc.nodes
        self.assertEqual(nodes[1]["name"], "Body")
        self.assertEqual(nodes[1]["children"], [0])
        self.assertEqual(nodes[1]["fc_name"], "Body")
        self.assertTrue(nodes[1]["visible"])
        self.assertEqual(nodes[0]["translation"], [0.0, 0.0, 0.01])
        self.assertEqual(nodes[0]["rotation"], [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(nodes[0]["scale"], [1.0, 1.0, 1.0])
        self.assertEqual([index for index, _, _ in self.doc.iter_nodes()], [1, 0])

    def test_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scene.fcxr")
            self.assertEqual(self.writer.write(path), path)
            self.assertFalse(os.path.exists(path + ".tmp"))
            from_file = read(path)
            self.assertEqual(from_file.manifest, self.doc.manifest)
            self.assertEqual(from_file.to_bytes(), self.data)
            self.assertEqual(FcxrReader().from_file(path).bin, self.doc.bin)

    def test_no_geometry_package(self):
        writer = FcxrWriter()
        writer.set_scene(root=0)
        document = read(writer.to_bytes())
        self.assertEqual(document.nodes, [])
        self.assertEqual(document.bin, b"")
        self.assertEqual(list(document.iter_nodes()), [])


class ChunkLayoutTest(unittest.TestCase):
    def setUp(self):
        self.data = build_writer().to_bytes()
        self.chunks = list(split_chunks(self.data))

    def test_chunk_order_and_types(self):
        types = [ctype for ctype, _, _ in self.chunks]
        self.assertEqual(types, [CHUNK_JSON, CHUNK_BIN, CHUNK_PNG])

    def test_total_length_is_four_byte_aligned(self):
        self.assertEqual(len(self.data) % 4, 0)

    def test_json_chunk_is_padded_with_spaces(self):
        ctype, payload, padding = self.chunks[0]
        self.assertEqual(ctype, CHUNK_JSON)
        self.assertEqual(set(padding) - {0x20}, set())
        self.assertEqual(len(padding), (-len(payload)) & 3)
        # the declared length excludes the padding
        json.loads(payload.decode("utf-8"))

    def test_binary_chunks_are_padded_with_zeros(self):
        for ctype, payload, padding in self.chunks[1:]:
            self.assertIn(ctype, (CHUNK_BIN, CHUNK_PNG))
            self.assertEqual(set(padding) - {0x00}, set())
            self.assertEqual(len(padding), (-len(payload)) & 3)

    def test_accessor_offsets_are_aligned_and_inside_the_buffer(self):
        document = read(self.data)
        for accessor in document.accessors:
            self.assertEqual(accessor["offset"] % 4, 0)
            self.assertLessEqual(
                accessor["offset"] + accessor["length"], len(document.bin)
            )

    def test_mixed_component_sizes_stay_aligned(self):
        # a U8 accessor of 3 bytes must still leave the next accessor aligned
        writer = FcxrWriter()
        writer.add_accessor([1, 2, 3], "SCALAR", "U8")
        writer.add_accessor([1.0, 2.0, 3.0], "VEC3", "F32")
        writer.set_scene(root=0)
        document = read(writer.to_bytes())
        self.assertEqual(document.accessors[0]["offset"], 0)
        self.assertEqual(document.accessors[1]["offset"], 4)
        self.assertEqual(list(document.read_accessor(1)), [1.0, 2.0, 3.0])


class DeterminismTest(unittest.TestCase):
    def test_identical_input_gives_identical_bytes(self):
        first = build_writer().to_bytes()
        second = build_writer().to_bytes()
        self.assertEqual(first, second)
        self.assertEqual(content_hash(first), content_hash(second))

    def test_hash_is_sixteen_hex_characters(self):
        digest = content_hash(build_writer().to_bytes())
        self.assertEqual(len(digest), 16)
        int(digest, 16)

    def test_geometry_change_changes_the_hash(self):
        writer = build_writer()
        moved = FcxrWriter(source_document="Part.FCStd")
        moved.add_material(
            "Steel", base_color=[0.5, 0.5, 0.55, 1.0], metallic=0.9, roughness=0.3
        )
        image = moved.add_image("paint_0", make_png())
        moved.add_material(
            "Painted", base_color=[1.0, 1.0, 1.0, 1.0], base_color_texture=image
        )
        mesh = moved.add_mesh(
            "Body",
            positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
            normals=TRIANGLE_NORMALS,
            uvs=TRIANGLE_UVS,
            indices=TRIANGLE_INDICES,
            material=0,
        )
        child = moved.add_node("Pad", mesh=mesh, translation=(0.0, 0.0, 0.01))
        root = moved.add_node("Body", mesh=mesh, children=[child], fc_name="Body")
        moved.set_scene(root=root, environment="bambu_x1c", user_scale=12.0)
        moved.set_paint(PAINT_DOC)
        moved.set_vector(VECTOR_DOC)
        self.assertNotEqual(
            content_hash(writer.to_bytes()), content_hash(moved.to_bytes())
        )

    def test_created_timestamp_is_opt_in(self):
        self.assertNotIn("created", build_writer().build_manifest()["asset"])
        stamped = build_writer(created="2026-09-03T10:00:00Z")
        self.assertEqual(
            stamped.build_manifest()["asset"]["created"], "2026-09-03T10:00:00Z"
        )
        self.assertNotEqual(stamped.to_bytes(), build_writer().to_bytes())

    def test_manifest_keys_are_sorted(self):
        payload = next(split_chunks(build_writer().to_bytes()))[1].decode("utf-8")
        manifest = json.loads(payload)
        self.assertEqual(
            payload,
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )


class CorruptFileTest(unittest.TestCase):
    def setUp(self):
        self.data = build_writer().to_bytes()

    def assertRejects(self, data, needle=None):
        with self.assertRaises(FcxrError) as caught:
            read(data)
        if needle:
            self.assertIn(needle, str(caught.exception).lower())

    def test_empty_and_short(self):
        self.assertRejects(b"", "too short")
        self.assertRejects(b"FCXR", "too short")

    def test_bad_magic(self):
        self.assertRejects(b"GLTF" + self.data[4:], "magic")

    def test_bad_version(self):
        self.assertRejects(
            self.data[:4] + struct.pack("<I", 2) + self.data[8:], "version"
        )

    def test_truncated_body(self):
        self.assertRejects(self.data[: len(self.data) // 2])

    def test_trailing_garbage(self):
        self.assertRejects(self.data + b"garbage", "trailing")

    def test_total_length_larger_than_file(self):
        broken = self.data[:8] + struct.pack("<I", len(self.data) + 64) + self.data[12:]
        self.assertRejects(broken, "does not fit")

    def test_lenient_reader_still_rejects_a_bad_header(self):
        with self.assertRaises(FcxrError):
            FcxrReader(strict=False).from_bytes(b"NOPE" + self.data[4:])

    def test_json_chunk_must_come_first(self):
        header = struct.pack("<4sII", FCXR_MAGIC, FCXR_VERSION, 12 + 8 + 4)
        self.assertRejects(header + struct.pack("<I4s", 4, CHUNK_BIN) + b"\x00" * 4,
                           "no json chunk")

    def test_invalid_json(self):
        payload = b"{not json"
        body = struct.pack("<I4s", len(payload), CHUNK_JSON) + payload + b"   "
        header = struct.pack("<4sII", FCXR_MAGIC, FCXR_VERSION, 12 + len(body))
        self.assertRejects(header + body, "json")

    def _package(self, manifest, bin_payload=b"", images=()):
        payload = json.dumps(manifest).encode("utf-8")
        body = struct.pack("<I4s", len(payload), CHUNK_JSON) + payload
        body += b" " * ((-len(payload)) & 3)
        if bin_payload:
            body += struct.pack("<I4s", len(bin_payload), CHUNK_BIN) + bin_payload
            body += b"\x00" * ((-len(bin_payload)) & 3)
        for image in images:
            body += struct.pack("<I4s", len(image), CHUNK_PNG) + image
            body += b"\x00" * ((-len(image)) & 3)
        return struct.pack("<4sII", FCXR_MAGIC, FCXR_VERSION, 12 + len(body)) + body

    def _manifest(self, **overrides):
        manifest = {
            "asset": {"generator": "test", "version": 1, "unit_scale": 0.001},
            "scene": {"root": 0},
            "nodes": [],
            "meshes": [],
            "accessors": [],
            "materials": [],
            "images": [],
        }
        manifest.update(overrides)
        return manifest

    def test_unaligned_accessor_offset(self):
        manifest = self._manifest(
            accessors=[
                {"offset": 2, "length": 12, "type": "VEC3", "component": "F32",
                 "count": 1}
            ]
        )
        self.assertRejects(self._package(manifest, b"\x00" * 16), "aligned")

    def test_accessor_past_the_end_of_the_buffer(self):
        manifest = self._manifest(
            accessors=[
                {"offset": 0, "length": 48, "type": "VEC3", "component": "F32",
                 "count": 4}
            ]
        )
        self.assertRejects(self._package(manifest, b"\x00" * 12), "exceeds")

    def test_accessor_length_does_not_match_count(self):
        manifest = self._manifest(
            accessors=[
                {"offset": 0, "length": 12, "type": "VEC3", "component": "F32",
                 "count": 4}
            ]
        )
        self.assertRejects(self._package(manifest, b"\x00" * 12), "does not match")

    def test_dangling_mesh_reference(self):
        manifest = self._manifest(
            nodes=[
                {"name": "n", "mesh": 3, "translation": [0, 0, 0],
                 "rotation": [0, 0, 0, 1], "scale": [1, 1, 1], "children": []}
            ]
        )
        self.assertRejects(self._package(manifest), "out of range")

    def test_dangling_child_reference(self):
        manifest = self._manifest(
            nodes=[
                {"name": "n", "mesh": None, "translation": [0, 0, 0],
                 "rotation": [0, 0, 0, 1], "scale": [1, 1, 1], "children": [7]}
            ]
        )
        self.assertRejects(self._package(manifest), "out of range")

    def test_image_without_a_chunk(self):
        manifest = self._manifest(
            images=[{"name": "i", "mime": "image/png", "chunk": 0}]
        )
        self.assertRejects(self._package(manifest), "out of range")

    def test_png_chunk_signature_is_checked(self):
        manifest = self._manifest(
            images=[{"name": "i", "mime": "image/png", "chunk": 0}]
        )
        self.assertRejects(
            self._package(manifest, images=[b"not a png at all"]), "signature"
        )

    def test_unknown_chunk_type_is_rejected_in_strict_mode(self):
        manifest = self._manifest()
        payload = json.dumps(manifest).encode("utf-8")
        body = struct.pack("<I4s", len(payload), CHUNK_JSON) + payload
        body += b" " * ((-len(payload)) & 3)
        body += struct.pack("<I4s", 4, b"XXXX") + b"\x00" * 4
        data = struct.pack("<4sII", FCXR_MAGIC, FCXR_VERSION, 12 + len(body)) + body
        self.assertRejects(data, "unknown chunk")
        self.assertIsNotNone(FcxrReader(strict=False).from_bytes(data))

    def test_bad_scene_root(self):
        manifest = self._manifest(
            scene={"root": 5},
            nodes=[
                {"name": "n", "mesh": None, "translation": [0, 0, 0],
                 "rotation": [0, 0, 0, 1], "scale": [1, 1, 1], "children": []}
            ],
        )
        self.assertRejects(self._package(manifest), "root")


class WriterValidationTest(unittest.TestCase):
    def test_mesh_needs_vertices(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh("empty", positions=[])

    def test_normal_count_must_match(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh(
                "bad", positions=TRIANGLE_POSITIONS, normals=[(0, 0, 1)]
            )

    def test_uv_count_must_match(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh("bad", positions=TRIANGLE_POSITIONS, uvs=[(0, 0)])

    def test_index_out_of_range(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh(
                "bad", positions=TRIANGLE_POSITIONS, indices=[(0, 1, 9)]
            )

    def test_indices_must_be_triangles(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh(
                "bad", positions=TRIANGLE_POSITIONS, indices=[0, 1, 2, 0]
            )

    def test_unknown_material_index(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_mesh("bad", positions=TRIANGLE_POSITIONS, material=4)

    def test_node_mesh_index_checked(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_node("bad", mesh=2)

    def test_image_must_be_png(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_image("bad", b"JPEGish")

    def test_flat_and_nested_positions_are_equivalent(self):
        flat = FcxrWriter()
        flat.add_mesh("m", positions=[0, 0, 0, 1, 0, 0, 0, 1, 0])
        flat.set_scene(root=0)
        nested = FcxrWriter()
        nested.add_mesh("m", positions=TRIANGLE_POSITIONS)
        nested.set_scene(root=0)
        self.assertEqual(flat.to_bytes(), nested.to_bytes())

    def test_large_index_buffers_use_u32(self):
        writer = FcxrWriter()
        positions = [float(i) for i in range(3 * 70000)]
        indices = [0, 1, 69999]
        writer.add_mesh("big", positions=positions, indices=indices)
        writer.set_scene(root=0)
        document = read(writer.to_bytes())
        self.assertEqual(document.accessors[1]["component"], "U32")
        self.assertEqual(list(document.primitive_arrays(0)["indices"]), indices)

    def test_forced_index_component(self):
        writer = FcxrWriter(index_component="U32")
        writer.add_mesh("m", positions=TRIANGLE_POSITIONS, indices=TRIANGLE_INDICES)
        writer.set_scene(root=0)
        document = read(writer.to_bytes())
        self.assertEqual(document.accessors[1]["component"], "U32")

    def test_paint_validation(self):
        writer = FcxrWriter()
        with self.assertRaises(FcxrError):
            writer.set_paint({"targets": [{"fc_name": "B",
                                           "layers": [{"blend": "screen"}]}]})
        with self.assertRaises(FcxrError):
            writer.set_paint({"targets": [{"layers": []}]})
        with self.assertRaises(FcxrError):
            # layer image index without any image in the package
            writer.set_paint({"targets": [{"fc_name": "B",
                                           "layers": [{"image": 0}]}]})

    def test_vector_validation(self):
        writer = FcxrWriter()
        with self.assertRaises(FcxrError):
            writer.set_vector({"paths": [{"nodes": [{"point": [0]}]}]})
        with self.assertRaises(FcxrError):
            writer.set_vector({"paths": [{"nodes": [], "target": "spaceship"}]})
        with self.assertRaises(FcxrError):
            writer.set_vector({"paths": [{"nodes": [{"point": [0, 0],
                                                     "type": "wobbly"}]}]})

    def test_accessor_range_is_checked(self):
        with self.assertRaises(FcxrError):
            FcxrWriter().add_accessor([70000], "SCALAR", "U16")
        with self.assertRaises(FcxrError):
            FcxrWriter().add_accessor([-1], "SCALAR", "U32")

    def test_read_accessor_index_is_checked(self):
        document = read(build_writer().to_bytes())
        with self.assertRaises(FcxrError):
            document.read_accessor(99)
        with self.assertRaises(FcxrError):
            document.image_bytes(3)
        with self.assertRaises(FcxrError):
            document.primitive_arrays(9)


class HelperTest(unittest.TestCase):
    def test_content_hash_rejects_text(self):
        with self.assertRaises(TypeError):
            content_hash("not bytes")

    def test_compress_round_trip(self):
        data = build_writer().to_bytes()
        self.assertEqual(fcxr.decompress(fcxr.compress(data)), data)
        with self.assertRaises(FcxrError):
            fcxr.decompress(b"not zlib")

    def test_read_accepts_bytes_and_paths(self):
        data = build_writer().to_bytes()
        self.assertEqual(read(bytearray(data)).manifest, read(data).manifest)


if __name__ == "__main__":
    unittest.main()
