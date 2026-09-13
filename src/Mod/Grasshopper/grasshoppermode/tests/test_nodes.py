# SPDX-License-Identifier: LGPL-2.1-or-later
import math
import unittest

from grasshoppermode.geometry import StubBackend, StubShape, Vec3, rotate_point
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry, example_graph


class _Ctx:
    def __init__(self, backend, params=None):
        self.backend = backend

        class N:
            pass

        self.node = N()
        self.node.params = dict(params or {})


def run(type_id, params=None, **inputs):
    reg = default_registry()
    ntype = reg.get(type_id)
    ctx = _Ctx(StubBackend(), params)
    return ntype.func(ctx, **inputs)


class TestMathNodes(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(run("math.add", a=2, b=3), 5.0)
        self.assertEqual(run("math.subtract", a=2, b=3), -1.0)
        self.assertEqual(run("math.multiply", a=2, b=3), 6.0)
        self.assertEqual(run("math.divide", a=6, b=3), 2.0)
        self.assertEqual(run("math.power", a=2, b=3), 8.0)
        self.assertAlmostEqual(run("math.sin", angle=90), 1.0)
        self.assertAlmostEqual(run("math.cos", angle=180), -1.0)
        self.assertEqual(
            run("math.remap", value=5, source_min=0, source_max=10, target_min=0, target_max=100),
            50.0,
        )
        self.assertEqual(run("math.series", start=1, step=2, count=3), [1.0, 3.0, 5.0])
        self.assertEqual(run("math.range", start=0, end=1, steps=2), [0.0, 0.5, 1.0])
        self.assertEqual(len(run("math.random", count=5)), 5)
        self.assertEqual(run("math.random", count=5, seed=3), run("math.random", count=5, seed=3))
        self.assertEqual(
            run("math.compare", a=1, b=2), {"less": True, "equal": False, "greater": False}
        )

    def test_expression(self):
        self.assertAlmostEqual(
            run("math.expression", {"expression": "sin(x) + y * 2"}, x=0, y=1.5), 3.0
        )
        with self.assertRaises(Exception):
            run("math.expression", {"expression": "__import__('os')"}, x=0)

    def test_slider_clamps(self):
        self.assertEqual(run("param.slider", {"min": 0, "max": 5}, value=99), 5.0)
        self.assertEqual(run("param.slider", {"min": 0, "max": 5}, value=None), 1.0)


class TestListNodes(unittest.TestCase):
    def test_lists(self):
        self.assertEqual(run("list.length", items=[1, [2, 3]]), 3)
        self.assertEqual(run("list.item", items=[1, 2, 3], index=4), 2)
        self.assertEqual(run("list.reverse", items=[1, 2]), [2, 1])
        self.assertEqual(run("list.sum", items=[1, 2, 3]), 6.0)
        self.assertEqual(run("list.average", items=[1, 3]), 2.0)
        self.assertEqual(run("list.cull", items=[1, 2, 3, 4], pattern=[True, False]), [1, 3])
        self.assertEqual(run("list.shift", items=[1, 2, 3], offset=1), [2, 3, 1])


class TestVectorNodes(unittest.TestCase):
    def test_points(self):
        self.assertEqual(run("vec.point", x=1, y=2, z=3), Vec3(1, 2, 3))
        self.assertEqual(run("vec.deconstruct", point=Vec3(1, 2, 3)), {"x": 1, "y": 2, "z": 3})
        self.assertEqual(run("vec.distance", a=Vec3(0, 0, 0), b=Vec3(3, 4, 0)), 5.0)
        self.assertEqual(run("vec.cross", a=Vec3(1, 0, 0), b=Vec3(0, 1, 0)), Vec3(0, 0, 1))
        p = run("vec.polar", radius=2, angle=90)
        self.assertAlmostEqual(p.x, 0.0)
        self.assertAlmostEqual(p.y, 2.0)
        self.assertEqual(len(run("vec.grid", count_x=2, count_y=3, spacing=1)), 6)

    def test_rotate_point(self):
        r = rotate_point(Vec3(1, 0, 0), Vec3(), Vec3(0, 0, 1), 90)
        self.assertAlmostEqual(r.x, 0.0)
        self.assertAlmostEqual(r.y, 1.0)


class TestGeometryNodes(unittest.TestCase):
    def test_primitives_and_ops(self):
        box = run("solid.box", length=2, width=3, height=4)
        self.assertIsInstance(box, StubShape)
        self.assertEqual(box.bbox, (0, 0, 0, 2, 3, 4))
        moved = run("xform.move", shape=box, vector=Vec3(1, 1, 1))
        self.assertEqual(moved.bbox, (1, 1, 1, 3, 4, 5))
        arr = run("xform.linear_array", shape=box, vector=Vec3(10, 0, 0), count=3)
        self.assertEqual(len(arr), 3)
        self.assertEqual(arr[2].bbox[0], 20.0)
        polar = run("xform.polar_array", shape=box, count=4)
        self.assertEqual(len(polar), 4)
        union = run("bool.union", shapes=[box, moved])
        self.assertEqual(union.bbox, (0, 0, 0, 3, 4, 5))
        cut = run("bool.difference", base=box, tools=[moved])
        self.assertEqual(cut.bbox, box.bbox)
        circle = run("curve.circle", center=Vec3(), radius=5)
        ext = run("solid.extrude", profile=circle, direction=Vec3(0, 0, 7))
        self.assertEqual(ext.bbox[5], 7.0)
        self.assertAlmostEqual(run("query.volume", shape=box), 24.0)
        bb = run("query.bbox", shape=box)
        self.assertEqual(bb["center"], Vec3(1, 1.5, 2))
        self.assertEqual(run("out.preview", geometry=[box, 5, None]), [box])
        self.assertEqual(run("out.preview", geometry=[box], enabled=False), [])

    def test_missing_inputs_raise(self):
        with self.assertRaises(ValueError):
            run("solid.extrude", profile=None)

    def test_example_graph_has_no_errors(self):
        g = Graph(default_registry(), StubBackend())
        example_graph(g)
        self.assertEqual(g.errors(), {})
        polar = [n for n in g.nodes.values() if n.type_id == "xform.polar_array"][0]
        self.assertEqual(len(polar.outputs["shapes"]), 6)

    def test_tessellate_and_edges(self):
        b = StubBackend()
        verts, faces = b.tessellate(b.box(1, 1, 1))
        self.assertEqual(len(verts), 8)
        self.assertEqual(len(faces), 12)
        self.assertEqual(len(b.edges(b.box(1, 1, 1))), 6)
        self.assertEqual(b.tessellate(StubShape("null", (0, 0, 0, -1, -1, -1))), ([], []))


class TestVec3(unittest.TestCase):
    def test_ops(self):
        v = Vec3(1, 2, 2)
        self.assertEqual(v.length(), 3.0)
        self.assertEqual((v * 2).x, 2.0)
        self.assertEqual(-v, Vec3(-1, -2, -2))
        self.assertEqual(v.normalized().length(), 1.0)
        self.assertEqual(Vec3.coerce([[1, 2, 3], [4, 5, 6]]), [Vec3(1, 2, 3), Vec3(4, 5, 6)])
        self.assertEqual(Vec3.coerce(2), Vec3(2, 2, 2))
        self.assertEqual(list(v), [1.0, 2.0, 2.0])
        self.assertEqual(Vec3().normalized(), Vec3())
        self.assertTrue(math.isclose(Vec3(3, 4, 0).distance(Vec3()), 5.0))


if __name__ == "__main__":
    unittest.main()
