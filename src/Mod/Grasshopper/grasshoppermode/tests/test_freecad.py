# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests that need a real FreeCAD (document objects, Part backend).
Skipped outside FreeCAD."""

import json
import unittest

try:
    import FreeCAD
    import Part  # noqa: F401
except ImportError:  # pragma: no cover
    FreeCAD = None


@unittest.skipIf(FreeCAD is None, "needs FreeCAD with Part")
class TestDefinitionObject(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("GrasshopperTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_example_definition_recomputes_and_bakes(self):
        from grasshoppermode import document as ghdoc

        obj = ghdoc.make_definition(self.doc, "Example", example=True)
        self.doc.recompute()
        self.assertTrue(ghdoc.is_definition(obj))
        self.assertGreater(obj.NodeCount, 5)
        self.assertEqual(list(obj.Errors), [])
        self.assertGreater(obj.Shape.Volume, 0)
        graph = ghdoc.graph_for(obj)
        count = [n for n in graph.nodes.values() if n.params.get("label") == "count"][0]
        graph.set_value(count.id, "value", 3)
        graph.evaluate()
        self.doc.recompute()
        self.assertEqual(json.loads(obj.Definition)["nodes"][0]["values"]["value"], 3.0)
        # wire the result into a bake node
        diff = [n for n in graph.nodes.values() if n.type_id == "bool.difference"][0]
        bake = graph.add_node("out.bake", 1000, 400)
        graph.connect(diff.id, "shape", bake.id, "geometry")
        graph.evaluate()
        created = ghdoc.bake(obj)
        self.assertEqual(len(created), 1)
        self.assertGreater(created[0].Shape.Volume, 0)

    def test_definition_survives_save_load(self):
        import os
        import tempfile

        from grasshoppermode import document as ghdoc

        obj = ghdoc.make_definition(self.doc, "Example", example=True)
        self.doc.recompute()
        path = os.path.join(tempfile.mkdtemp(), "gh.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        obj = self.doc.getObject("Example")
        self.assertTrue(ghdoc.is_definition(obj))
        self.doc.recompute()
        self.assertGreater(obj.Shape.Volume, 0)

    def test_part_backend_operations(self):
        from grasshoppermode.geometry import PartBackend, Vec3

        b = PartBackend()
        box = b.box(10, 10, 10)
        cyl = b.cylinder(3, 20, Vec3(5, 5, -5))
        cut = b.cut(box, [cyl])
        self.assertLess(b.volume(cut), 1000)
        verts, faces = b.tessellate(cut, 0.5)
        self.assertGreater(len(faces), 10)
        self.assertGreater(len(b.edges(cut)), 3)
        circle = b.circle(Vec3(), 5)
        solid = b.extrude(circle, Vec3(0, 0, 4))
        self.assertAlmostEqual(b.volume(solid), 3.14159 * 25 * 4, delta=1)
        rev = b.revolve(b.rectangle(2, 3, Vec3(5, 0, 0)), Vec3(), Vec3(0, 1, 0), 360)
        self.assertGreater(b.volume(rev), 0)
        self.assertIn("Solid", b.describe(solid))


if __name__ == "__main__":
    unittest.main()
