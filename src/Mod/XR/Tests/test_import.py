# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mesh formats, platform sources (offline), and the import planner."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrfit import box_mesh  # noqa: E402
from xrimport import (FormatError, GrabCAD, MakerWorld, ModelFile, Printables, SourceError,  # noqa: E402
                      Thingiverse, convert, formats, resolve, source_for)


class FormatsTest(unittest.TestCase):
    def setUp(self):
        self.box = box_mesh((2, 2, 2), name="cube")

    def test_stl_binary_round_trip(self):
        data = formats.write_stl(self.box)
        self.assertEqual(len(data), 84 + 12 * 50)
        result = formats.read(data, "cube.stl")
        self.assertEqual(result.format, "stl")
        self.assertAlmostEqual(result.meshes[0].volume(), 8.0)
        self.assertEqual(len(result.meshes[0].vertices), 8, "welded")

    def test_stl_ascii_round_trip(self):
        data = formats.write_stl(self.box, binary=False)
        self.assertTrue(data.startswith(b"solid cube"))
        result = formats.read(data, "anything")
        self.assertEqual(result.meshes[0].name, "cube")
        self.assertAlmostEqual(result.meshes[0].volume(), 8.0)

    def test_stl_truncated_binary_is_noted(self):
        data = formats.write_stl(self.box)[:-50]
        result = formats.read(data, "cube.stl")
        self.assertEqual(len(result.meshes[0]), 11)
        self.assertTrue(any("does not match" in n for n in result.notes))

    def test_obj_round_trip_with_quads_and_groups(self):
        text = b"# c\no first\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\no second\nv 0 0 1\nv 1 0 1\nv 0 1 1\nf -3 -2 -1\n"
        result = formats.read(text, "x.obj")
        self.assertEqual([m.name for m in result.meshes], ["first", "second"])
        self.assertEqual(len(result.meshes[0]), 2, "quad fan-triangulated")
        self.assertEqual(len(result.meshes[1]), 1)
        again = formats.read(formats.write_obj(self.box), "b.obj")
        self.assertAlmostEqual(again.meshes[0].volume(), 8.0)

    def test_ply_ascii_and_binary(self):
        ascii_ply = (b"ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
                     b"element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n")
        result = formats.read(ascii_ply, "t.ply")
        self.assertEqual(result.meshes[0].triangles, [(0, 1, 2)])
        import struct
        binary = (b"ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
                  b"element face 1\nproperty list uchar int vertex_indices\nend_header\n"
                  + struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<B3i", 3, 0, 1, 2))
        result = formats.read(binary, "t.ply")
        self.assertEqual(result.meshes[0].triangles, [(0, 1, 2)])
        cloud = b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n1 2 3\n"
        result = formats.read(cloud, "c.ply")
        self.assertTrue(any("point cloud" in n for n in result.notes))

    def test_3mf_round_trip_units_and_transform(self):
        data = formats.write_3mf([self.box], unit="inch")
        result = formats.read(data, "cube.3mf")
        self.assertEqual(result.unit_mm, 25.4)
        self.assertAlmostEqual(result.meshes[0].volume(), 8.0)
        # A build transform moves the mesh.
        text = data  # rebuild with a transform on the item
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            model = z.read("3D/3dmodel.model").decode()
        model = model.replace('<item objectid="1"/>', '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 0"/>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("3D/3dmodel.model", model)
        result = formats.read(buf.getvalue(), "moved.3mf")
        self.assertAlmostEqual(result.meshes[0].centroid[0], 10.0)

    def test_sniff_and_errors(self):
        self.assertEqual(formats.sniff(b"PK\x03\x04", "x"), "3mf")
        self.assertEqual(formats.sniff(b"ply\n", ""), "ply")
        self.assertEqual(formats.sniff(b"solid x", ""), "stl")
        with self.assertRaises(FormatError):
            formats.sniff(b"", "")
        with self.assertRaises(FormatError):
            formats.read(b"solid empty\nendsolid empty\n", "e.stl")
        with self.assertRaises(FormatError):
            formats.read_3mf(b"not a zip")


class FakeFetch(object):
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, headers=None, data=None, timeout=None):
        self.calls.append((url, dict(headers or {}), data))
        for key, value in self.responses.items():
            if key in url:
                return value if isinstance(value, bytes) else json.dumps(value).encode()
        raise SourceError("no fake response for %s" % url, url, 404)


class SourcesTest(unittest.TestCase):
    def test_source_for(self):
        self.assertIsInstance(source_for("https://www.thingiverse.com/thing:12345"), Thingiverse)
        self.assertIsInstance(source_for("https://www.printables.com/model/1234-a-b"), Printables)
        self.assertIsInstance(source_for("https://makerworld.com/en/models/98765#profileId-1"), MakerWorld)
        self.assertIsInstance(source_for("https://grabcad.com/library/bracket-1"), GrabCAD)
        with self.assertRaises(SourceError):
            source_for("https://example.com/model")

    def test_thingiverse(self):
        fetch = FakeFetch({
            "/things/12345/files": [{"id": 1, "name": "part.stl", "download_url": "https://cdn/part.stl", "size": 10},
                                    {"id": 2, "name": "readme.txt", "download_url": "https://cdn/readme.txt"}],
            "/things/12345": {"name": "Bracket", "creator": {"name": "maker"}, "license": "CC-BY", "public_url": "u", "thumbnail": "t"},
        })
        with self.assertRaises(SourceError):
            Thingiverse(fetch=fetch).resolve("https://www.thingiverse.com/thing:12345")  # no token
        ref = Thingiverse(fetch=fetch, token="tok").resolve("https://www.thingiverse.com/thing:12345")
        self.assertEqual(ref.title, "Bracket")
        self.assertEqual([f.name for f in ref.printable_files], ["part.stl"])
        self.assertEqual(fetch.calls[0][1]["Authorization"], "Bearer tok")
        with self.assertRaises(SourceError):
            Thingiverse(fetch=fetch, token="tok").resolve("https://www.thingiverse.com/groups")

    def test_printables(self):
        fetch = FakeFetch({"graphql": {"data": {"print": {
            "id": "77", "name": "Hook", "license": {"name": "CC0"}, "user": {"publicUsername": "p"},
            "stls": [{"id": "s1", "name": "hook.stl", "fileSize": 5}], "gcodes": [], "otherFiles": [],
            "images": [{"filePath": "media/x.png"}]}}}})
        ref = resolve("https://www.printables.com/model/77-hook", fetch=fetch)
        self.assertEqual(ref.source, "printables")
        self.assertEqual(ref.thumbnail, "https://files.printables.com/media/x.png")
        self.assertEqual(ref.files[0].extra["group"], "stls")
        body = json.loads(fetch.calls[0][2].decode())
        self.assertEqual(body["variables"]["id"], "77")
        self.assertTrue(any("unofficial" in n for n in ref.notes))

    def test_printables_download_gets_signed_link(self):
        fetch = FakeFetch({"graphql": {"data": {"getDownloadLink": {"ok": True, "output": {"link": "https://files/hook.stl?sig"}}}},
                           "files/hook.stl": b"solid a\nendsolid a\n"})
        tmp = tempfile.mkdtemp()
        try:
            path = Printables(fetch=fetch).download(ModelFile("hook.stl", "https://www.printables.com/model/77/files/s1", extra={"id": "s1", "group": "stls"}), tmp)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(os.path.basename(path), "hook.stl")
        finally:
            shutil.rmtree(tmp)

    def test_makerworld(self):
        fetch = FakeFetch({"design-service/design/98765": {
            "title": "Vase", "designCreator": {"name": "b"}, "license": "BY-NC",
            "instances": [{"id": 1, "files": [{"name": "vase.3mf", "url": "https://cdn/vase.3mf", "size": 3}]}],
            "modelFiles": [{"name": "vase.stl", "url": "https://cdn/vase.stl"}], "cover": "c"}})
        ref = resolve("https://makerworld.com/en/models/98765-vase", fetch=fetch)
        self.assertEqual([f.name for f in ref.files], ["vase.3mf", "vase.stl"])

    def test_grabcad_is_honest(self):
        fetch = FakeFetch({"grabcad.com/library/bracket-1": b"<html><title>Bracket v2 | 3D CAD Model Library | GrabCAD</title></html>"})
        ref = resolve("https://grabcad.com/library/bracket-1", fetch=fetch)
        self.assertEqual(ref.title, "Bracket v2")
        self.assertEqual(ref.files, [])
        self.assertTrue(any("no public download API" in n for n in ref.notes))
        ref = resolve("https://grabcad.com/library/other-2", fetch=FakeFetch({}))
        self.assertEqual(ref.title, "other 2")


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_plan(self):
        stl = os.path.join(self.tmp, "a.stl")
        with open(stl, "wb") as h:
            h.write(formats.write_stl(box_mesh((1, 1, 1))))
        kind, result = convert.plan(stl, scale_mm=25.4)
        self.assertEqual(kind, "mesh")
        self.assertAlmostEqual(result.meshes[0].volume(), 25.4 ** 3)
        self.assertEqual(convert.plan(os.path.join(self.tmp, "x.step")), ("kernel", ".step"))
        self.assertEqual(convert.plan(os.path.join(self.tmp, "x.doc")), ("unsupported", ".doc"))

    def test_import_path_without_freecad(self):
        stl = os.path.join(self.tmp, "a.stl")
        with open(stl, "wb") as h:
            h.write(formats.write_stl(box_mesh((1, 1, 1))))
        result = convert.import_path(stl)
        self.assertEqual(len(result.meshes), 1)
        self.assertEqual(result.objects, [], "no FreeCAD: parsed but no object made")
        result = convert.import_path(os.path.join(self.tmp, "x.doc"))
        self.assertTrue(result.skipped)

    def test_import_archive(self):
        path = os.path.join(self.tmp, "grabcad.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("parts/a.stl", formats.write_stl(box_mesh((1, 1, 1))))
            z.writestr("parts/b.obj", formats.write_obj(box_mesh((2, 2, 2))))
            z.writestr("readme.txt", "hi")
        result = convert.import_archive(path)
        self.assertEqual(len(result.meshes), 2)
        self.assertEqual(result.skipped, ["readme.txt: not a model file"])
        bad = convert.import_archive(stl_path := os.path.join(self.tmp, "no.zip"))
        self.assertTrue(bad.skipped)

    def test_import_model_downloads(self):
        from xrimport import ModelRef, Source

        class Local(Source):
            name = "local"

            def download(self, model_file, dest_dir):
                path = os.path.join(dest_dir, model_file.name)
                with open(path, "wb") as h:
                    h.write(formats.write_stl(box_mesh((1, 1, 1))))
                return path

        ref = ModelRef("local", "1", "t", files=[ModelFile("a.stl", "x"), ModelFile("notes.md", "y")])
        result = convert.import_model(ref, Local(), dest_dir=self.tmp)
        self.assertEqual(len(result.meshes), 1)
        empty = convert.import_model(ModelRef("local", "2", "n"), Local(), dest_dir=self.tmp)
        self.assertTrue(empty.skipped)


if __name__ == "__main__":
    unittest.main()
