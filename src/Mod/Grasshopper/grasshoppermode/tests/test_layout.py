# SPDX-License-Identifier: LGPL-2.1-or-later
import unittest

from grasshoppermode import layout
from grasshoppermode.geometry import StubBackend
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.g = Graph(default_registry(), StubBackend())
        self.a = self.g.add_node("param.slider", 0, 0, {"value": 2})
        self.b = self.g.add_node("math.add", 300, 0)
        self.g.connect(self.a.id, "value", self.b.id, "a")

    def test_sizes(self):
        w, h = layout.node_size(self.a, self.g.node_type(self.a))
        self.assertEqual(w, layout.NODE_WIDTH)
        self.assertGreater(h, layout.HEADER_HEIGHT + layout.PORT_ROW)
        self.a.params["width"] = 250
        self.assertEqual(layout.node_size(self.a, self.g.node_type(self.a))[0], 250)

    def test_ports(self):
        outs = layout.output_ports(self.a, self.g.node_type(self.a))
        self.assertEqual(outs[0][0], "value")
        self.assertEqual(outs[0][1], layout.NODE_WIDTH)
        ins = layout.input_ports(self.b, self.g.node_type(self.b))
        self.assertEqual([p[0] for p in ins], ["a", "b"])
        self.assertEqual(ins[1][2] - ins[0][2], layout.PORT_ROW)

    def test_hit_test(self):
        ntype = self.g.node_type(self.b)
        ins = layout.input_ports(self.b, ntype)
        hit = layout.hit_test(self.g, ins[1][1], ins[1][2])
        self.assertEqual(hit["kind"], "port")
        self.assertEqual((hit["node"], hit["port"], hit["direction"]), (self.b.id, "b", "in"))
        outs = layout.output_ports(self.a, self.g.node_type(self.a))
        hit = layout.hit_test(self.g, outs[0][1] + 3, outs[0][2])
        self.assertEqual(hit["direction"], "out")
        self.assertEqual(layout.hit_test(self.g, 310, 5)["kind"], "header")
        self.assertEqual(layout.hit_test(self.g, 380, 50)["kind"], "node")
        wr = layout.widget_rect(self.a, self.g.node_type(self.a))
        hit = layout.hit_test(self.g, wr[0] + wr[2] / 2, wr[1] + wr[3] / 2)
        self.assertEqual(hit["kind"], "widget")
        self.assertEqual(hit["widget"], "slider")
        self.assertEqual(layout.hit_test(self.g, -500, -500)["kind"], "empty")
        # midpoint of the wire
        path = layout.wire_path(self.g, list(self.g.wires.values())[0])
        pts = layout.bezier_points(*path)
        mx, my = pts[len(pts) // 2]
        self.assertEqual(layout.hit_test(self.g, mx, my)["kind"], "wire")

    def test_nodes_in_rect_and_slider(self):
        self.assertEqual(layout.nodes_in_rect(self.g, -10, -10, 50, 50), [self.a.id])
        self.assertEqual(
            set(layout.nodes_in_rect(self.g, -10, -10, 600, 200)), {self.a.id, self.b.id}
        )
        ntype = self.g.node_type(self.a)
        wr = layout.widget_rect(self.a, ntype)
        self.assertEqual(layout.slider_value_at(self.a, ntype, wr[0]), 0.0)
        self.assertEqual(layout.slider_value_at(self.a, ntype, wr[0] + wr[2]), 10.0)
        self.assertAlmostEqual(layout.slider_value_at(self.a, ntype, wr[0] + wr[2] / 2), 5.0)
        self.assertIsNone(layout.slider_value_at(self.b, self.g.node_type(self.b), 0))


if __name__ == "__main__":
    unittest.main()
