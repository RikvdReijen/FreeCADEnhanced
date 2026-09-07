# SPDX-License-Identifier: LGPL-2.1-or-later
import base64
import os
import shutil
import struct
import tempfile
import unittest
import zlib

from grasshoppermode import layout, media
from grasshoppermode.geometry import StubBackend
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry


def tiny_png(w=4, h=3, color=(255, 0, 0)):
    """A valid PNG without any imaging library."""
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(tag, data):
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class TestMedia(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = media.MediaStore(self.root, "cv/1")
        self.graph = Graph(default_registry(), StubBackend(), canvas_id="cv1")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_store_and_size(self):
        png = tiny_png(6, 2)
        name = self.store.save_png(
            "data:image/png;base64," + base64.b64encode(png).decode(), "outline"
        )
        self.assertTrue(name.endswith(".png"))
        self.assertIn("outline", name)
        self.assertTrue(self.store.exists(name))
        self.assertEqual(media.png_size(self.store.read(name)), (6, 2))
        self.assertEqual(self.store.list(), [name])
        self.assertEqual(self.store.url(name), "/media/cv_1/" + name)
        with self.assertRaises(ValueError):
            self.store.save_png(b"not a png")
        with self.assertRaises(ValueError):
            self.store.path("../evil.png")
        with self.assertRaises(ValueError):
            media.decode_data_url("data:image/jpeg;base64,AAAA")
        self.assertFalse(self.store.exists("../x"))

    def test_image_node_strokes_and_export(self):
        name = self.store.save_png(tiny_png(8, 4), "rendered")
        node = media.add_image_node(
            self.graph, self.store, name, "rendered", 10, 20, pose={"p": [0, 0, 0]}
        )
        self.assertEqual(node.type_id, "media.image")
        self.assertEqual(node.params["px_w"], 8)
        self.assertAlmostEqual(node.params["aspect"], 0.5)
        self.graph.evaluate()
        self.assertEqual(node.outputs["path"], name)
        self.assertEqual(node.outputs["strokes"], 0)
        # layout keeps the aspect ratio of the picture area
        ntype = self.graph.node_type(node)
        wr = layout.widget_rect(node, ntype)
        self.assertAlmostEqual(wr[3], (260 - 16) * 0.5)
        self.assertEqual(layout.hit_test(self.graph, wr[0] + 5, wr[1] + 5)["widget"], "image")
        n = media.add_stroke(self.graph, node.id, [(1, 1), (5, 3)], "#00f", 2)
        self.assertEqual(n, 1)
        self.graph.evaluate()
        self.assertEqual(node.outputs["strokes"], 1)
        with self.assertRaises(ValueError):
            media.add_stroke(self.graph, node.id, [])
        other = self.graph.add_node("math.add")
        with self.assertRaises(ValueError):
            media.add_stroke(self.graph, other.id, [(0, 0)])
        svg = media.strokes_svg(node)
        self.assertIn("polyline", svg)
        out = media.export_bundle(self.store, node)
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("data:image/png;base64", text)
        self.assertIn('stroke="#00f"', text)
        media.clear_strokes(self.graph, node.id)
        self.assertEqual(node.params["strokes"], [])
        # survives JSON round trip
        g2 = Graph.from_json(self.graph.to_json(), default_registry(), StubBackend())
        self.assertEqual(g2.nodes[node.id].params["file"], name)


if __name__ == "__main__":
    unittest.main()
