# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drafting table mapping, dimension inference, the pick session."""

import math
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrdraw import DraftingTable, DrawSession, Edge, InferenceError, Pick, Vertex, ViewGeometry, infer  # noqa: E402
from xrsketch import vecmath as vm  # noqa: E402


def bracket_view():
    """A 40x20 rectangle with a 6 mm hole, drawn at 2:1 at page (100, 100)."""
    s = 2.0
    ox, oy = 100.0, 100.0
    verts = [Vertex(1, ox, oy), Vertex(2, ox + 40 * s, oy), Vertex(3, ox + 40 * s, oy + 20 * s), Vertex(4, ox, oy + 20 * s)]
    edges = [Edge(1, "line", verts[0].point, verts[1].point), Edge(2, "line", verts[1].point, verts[2].point),
             Edge(3, "line", verts[2].point, verts[3].point), Edge(4, "line", verts[3].point, verts[0].point),
             Edge(5, "circle", center=(ox + 20 * s, oy + 10 * s), radius=3 * s, closed=True),
             Edge(6, "arc", center=(ox + 40 * s, oy + 20 * s), radius=5 * s),
             Edge(7, "line", (ox, oy), (ox + 40 * s, oy + 20 * s))]
    return ViewGeometry("View", ox, oy, s, verts, edges)


class TableTest(unittest.TestCase):
    def test_page_fits_and_round_trips(self):
        t = DraftingTable(position=(0, 0.9, -0.6), tilt_deg=20, size=(0.9, 0.65), page_size="A3")
        self.assertAlmostEqual(t.page_scale, min((0.9 - 0.06) / 420.0, (0.65 - 0.06) / 297.0))
        for x, y in ((0, 0), (420, 297), (210, 148.5), (17, 250)):
            w = t.page_to_world(x, y)
            px, py, pz = t.world_to_page(w)
            self.assertAlmostEqual(px, x, places=9)
            self.assertAlmostEqual(py, y, places=9)
            self.assertAlmostEqual(pz, 0.0, places=9)
        centre = t.page_to_world(210, 148.5)
        self.assertEqual([round(c, 9) for c in centre], [0.0, 0.9, -0.6])
        n = t.normal
        self.assertGreater(n[1], 0.9, "a board tilted 20° mostly faces up")
        self.assertGreater(n[2], 0.3, "and leans towards the user")
        up_slope = t.transform.apply_vector((0, 1, 0))
        self.assertLess(up_slope[2], 0.0, "the top of the sheet is further from the user")

    def test_ray_to_page(self):
        t = DraftingTable(position=(0, 0.9, -0.6), tilt_deg=20, page_size="A3")
        target = t.page_to_world(300, 200)
        origin = (0.2, 1.5, 0.3)
        hit = t.ray_to_page(origin, vm.sub(target, origin))
        self.assertIsNotNone(hit)
        x, y, on_page, dist = hit
        self.assertAlmostEqual(x, 300, places=6)
        self.assertAlmostEqual(y, 200, places=6)
        self.assertTrue(on_page)
        self.assertAlmostEqual(dist, vm.dist(origin, target), places=9)
        off = t.page_to_world(-50, 100)
        self.assertFalse(t.ray_to_page(origin, vm.sub(off, origin))[2])
        self.assertIsNone(t.ray_to_page(origin, (0, 1, 0)), "pointing away")
        self.assertIsNone(t.ray_to_page(origin, vm.cross(t.normal, (1, 0, 0))), "parallel")
        self.assertEqual(len(t.corners_world()), 4)
        self.assertEqual(t.to_dict()["page"], "A3")


class InferTest(unittest.TestCase):
    def setUp(self):
        self.v = bracket_view()

    def pick(self, element):
        return Pick(self.v, element, (0, 0))

    def test_two_vertices(self):
        v1, v2, v3 = self.v.vertices[0], self.v.vertices[1], self.v.vertices[2]
        d = infer([self.pick(v1), self.pick(v2)])
        self.assertEqual((d.type, d.value), ("DistanceX", 40.0))
        self.assertEqual(d.references, [("View", "Vertex1"), ("View", "Vertex2")])
        self.assertEqual(d.label, "40.00")
        d = infer([self.pick(v2), self.pick(v3)])
        self.assertEqual((d.type, d.value), ("DistanceY", 20.0))
        d = infer([self.pick(v1), self.pick(v3)])
        self.assertEqual(d.type, "Distance")
        self.assertAlmostEqual(d.value, math.hypot(40, 20))
        with self.assertRaises(InferenceError):
            infer([self.pick(v1), self.pick(v1)])

    def test_circle_and_arc(self):
        d = infer([self.pick(self.v.edges[4])])
        self.assertEqual((d.type, d.value, d.label), ("Diameter", 6.0, "⌀6.00"))
        d = infer([self.pick(self.v.edges[5])])
        self.assertEqual((d.type, d.value, d.label), ("Radius", 5.0, "R5.00"))

    def test_lines(self):
        e1, e2, e3, diag = self.v.edges[0], self.v.edges[1], self.v.edges[2], self.v.edges[6]
        d = infer([self.pick(e1), self.pick(e2)])
        self.assertEqual((d.type, round(d.value, 6)), ("Angle", 90.0))
        self.assertEqual(d.label, "90°")
        d = infer([self.pick(e1), self.pick(e3)])
        self.assertEqual((d.type, d.value), ("Distance", 20.0), "parallel lines: the gap")
        d = infer([self.pick(e1), self.pick(diag)])
        self.assertAlmostEqual(d.value, math.degrees(math.atan2(20, 40)))
        d = infer([self.pick(e1)])
        self.assertEqual((d.type, d.value), ("DistanceX", 40.0), "a single line is its own length")

    def test_vertex_to_line_and_errors(self):
        d = infer([self.pick(self.v.vertices[3]), self.pick(self.v.edges[0])])
        self.assertEqual((d.type, d.value), ("Distance", 20.0))
        with self.assertRaises(InferenceError):
            infer([])
        with self.assertRaises(InferenceError):
            infer([self.pick(self.v.vertices[0])])
        other = ViewGeometry("Other", 0, 0, 1, [Vertex(1, 0, 0)])
        with self.assertRaises(InferenceError):
            infer([self.pick(self.v.vertices[0]), Pick(other, other.vertices[0], (0, 0))])
        with self.assertRaises(InferenceError):
            infer([self.pick(self.v.vertices[0]), self.pick(self.v.edges[4])])

    def test_nearest(self):
        found = self.v.nearest((101.0, 101.0), 3.0)
        self.assertEqual(found[0].name, "Vertex1")
        found = self.v.nearest((140.0, 101.0), 3.0)
        self.assertEqual(found[0].name, "Edge1")
        self.assertIsNone(self.v.nearest((300.0, 300.0), 3.0))


class SessionTest(unittest.TestCase):
    def test_point_pick_place(self):
        t = DraftingTable(position=(0, 0.9, -0.6), tilt_deg=20, page_size="A3")
        s = DrawSession(t, [bracket_view()])
        origin = (0.0, 1.4, 0.2)

        def aim(x, y):
            target = t.page_to_world(x, y)
            return s.point(origin, vm.sub(target, origin))

        hover = aim(100.5, 100.5)
        self.assertEqual(hover[4].name, "Vertex1")
        self.assertIsNotNone(s.pick())
        aim(180.0, 100.2)
        self.assertEqual(s.hover[4].name, "Vertex2")
        s.pick()
        preview = s.preview()
        self.assertEqual((preview.type, preview.value), ("DistanceX", 40.0))
        spec = s.place_dimension()
        self.assertEqual(spec.label, "40.00")
        self.assertEqual(s.picks, [])
        self.assertEqual(len(s.placed), 1)
        kinds = [e.kind for e in s.drain_events()]
        self.assertEqual(kinds.count("pick"), 2)
        self.assertIn("dimension", kinds)
        self.assertFalse(s.drain_events())
        # a miss
        aim(300.0, 250.0)
        self.assertIsNone(s.pick())
        self.assertIsNone(s.place_dimension())
        self.assertTrue(s.notes)
        self.assertEqual(s.undo().label, "40.00")
        self.assertIsNone(s.undo())
        self.assertIn("table", s.to_dict())

    def test_no_freecad_is_preview_only(self):
        from xrdraw import make_dimension
        from xrdraw.dimension import DimensionSpec

        notes = []
        self.assertIsNone(make_dimension(DimensionSpec("Distance", [("V", "Vertex1"), ("V", "Vertex2")], 1.0, "V"), None, notes=notes))
        self.assertTrue(any("FreeCAD" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
