# SPDX-License-Identifier: LGPL-2.1-or-later
import json
import unittest

from grasshoppermode.geometry import StubBackend, Vec3
from grasshoppermode.graph import Graph, GraphError, NodeType, PortSpec, Registry, coerce, flatten
from grasshoppermode.nodes import default_registry, example_graph


def _graph():
    return Graph(default_registry(), StubBackend())


class TestGraphBasics(unittest.TestCase):
    def test_add_connect_evaluate(self):
        g = _graph()
        a = g.add_node("param.slider", 0, 0, {"value": 3})
        b = g.add_node("param.slider", 0, 100, {"value": 4})
        add = g.add_node("math.add", 200, 0)
        g.connect(a.id, "value", add.id, "a")
        g.connect(b.id, "value", add.id, "b")
        evaluated = g.evaluate()
        self.assertEqual(set(evaluated), {a.id, b.id, add.id})
        self.assertEqual(add.outputs["result"], 7.0)
        self.assertEqual(g.errors(), {})

    def test_incremental_evaluation(self):
        g = _graph()
        a = g.add_node("param.slider", 0, 0, {"value": 3})
        neg = g.add_node("math.negate", 200, 0)
        other = g.add_node("param.slider", 0, 200, {"value": 1})
        g.connect(a.id, "value", neg.id, "a")
        g.evaluate()
        g.set_value(a.id, "value", 5)
        evaluated = g.evaluate()
        self.assertIn(a.id, evaluated)
        self.assertIn(neg.id, evaluated)
        self.assertNotIn(other.id, evaluated)
        self.assertEqual(neg.outputs["result"], -5.0)
        self.assertEqual(g.evaluate(), [])

    def test_cycle_rejected(self):
        g = _graph()
        a = g.add_node("math.add")
        b = g.add_node("math.add")
        g.connect(a.id, "result", b.id, "a")
        ok, reason = g.can_connect(b.id, "result", a.id, "a")
        self.assertFalse(ok)
        self.assertIn("cycle", reason)
        with self.assertRaises(GraphError):
            g.connect(b.id, "result", a.id, "a")

    def test_kind_compatibility(self):
        g = _graph()
        box = g.add_node("solid.box")
        add = g.add_node("math.add")
        ok, reason = g.can_connect(box.id, "shape", add.id, "a")
        self.assertFalse(ok)
        self.assertIn("shape", reason)
        panel = g.add_node("param.panel")
        self.assertTrue(g.can_connect(box.id, "shape", panel.id, "value")[0])

    def test_single_input_replaced_multi_input_accumulates(self):
        g = _graph()
        s1 = g.add_node("param.slider", 0, 0, {"value": 1})
        s2 = g.add_node("param.slider", 0, 0, {"value": 2})
        add = g.add_node("math.add")
        g.connect(s1.id, "value", add.id, "a")
        g.connect(s2.id, "value", add.id, "a")
        self.assertEqual(len(g.wires_into(add.id, "a")), 1)
        merge = g.add_node("list.merge")
        g.connect(s1.id, "value", merge.id, "items")
        g.connect(s2.id, "value", merge.id, "items")
        self.assertEqual(len(g.wires_into(merge.id, "items")), 2)
        g.evaluate()
        self.assertEqual(merge.outputs["list"], [1.0, 2.0])

    def test_remove_node_removes_wires(self):
        g = _graph()
        a = g.add_node("param.slider")
        b = g.add_node("math.negate")
        g.connect(a.id, "value", b.id, "a")
        g.remove_node(a.id)
        self.assertEqual(len(g.wires), 0)
        self.assertNotIn(a.id, g.nodes)

    def test_errors_are_captured_not_raised(self):
        g = _graph()
        div = g.add_node("math.divide", 0, 0, {"a": 1, "b": 0})
        g.evaluate()
        self.assertIn("ZeroDivisionError", div.error)
        self.assertIn(div.id, g.errors())
        g.set_value(div.id, "b", 2)
        g.evaluate()
        self.assertIsNone(div.error)
        self.assertEqual(div.outputs["result"], 0.5)

    def test_elementwise_longest_list(self):
        g = _graph()
        series = g.add_node("math.series", 0, 0, {"start": 0, "step": 1, "count": 4})
        mul = g.add_node("math.multiply", 0, 0, {"b": 10})
        g.connect(series.id, "series", mul.id, "a")
        g.evaluate()
        self.assertEqual(mul.outputs["result"], [0.0, 10.0, 20.0, 30.0])
        # second list shorter: last item repeats
        short = g.add_node("math.series", 0, 0, {"start": 1, "step": 1, "count": 2})
        g.connect(short.id, "series", mul.id, "b")
        g.evaluate()
        self.assertEqual(mul.outputs["result"], [0.0, 2.0, 4.0, 6.0])

    def test_selection_modes(self):
        g = _graph()
        a = g.add_node("param.slider")
        b = g.add_node("param.slider")
        g.select([a.id])
        g.select([b.id], "add")
        self.assertEqual(g.selection, [a.id, b.id])
        g.select([a.id], "toggle")
        self.assertEqual(g.selection, [b.id])
        g.select(["missing"], "add")
        self.assertEqual(g.selection, [b.id])
        g.select([], "replace")
        self.assertEqual(g.selection, [])

    def test_events(self):
        g = _graph()
        seen = []
        g.on(lambda ev, payload: seen.append(ev))
        a = g.add_node("param.slider")
        g.move_node(a.id, 5, 6)
        g.set_value(a.id, "value", 2)
        g.evaluate()
        self.assertEqual(seen, ["node_added", "node_moved", "value_changed", "evaluated"])
        seen[:] = []
        with g.batch():
            g.add_node("param.slider")
            g.add_node("param.slider")
            self.assertEqual(seen, [])
        self.assertEqual(seen, ["node_added", "node_added"])

    def test_undo_redo(self):
        g = _graph()
        g.push_undo()
        a = g.add_node("param.slider", 1, 2)
        self.assertTrue(g.undo())
        self.assertEqual(len(g.nodes), 0)
        self.assertTrue(g.redo())
        self.assertIn(a.id, g.nodes)
        self.assertFalse(g.redo())

    def test_duplicate_nodes(self):
        g = _graph()
        a = g.add_node("param.slider", 0, 0, {"value": 2})
        b = g.add_node("math.negate", 100, 0)
        g.connect(a.id, "value", b.id, "a")
        new_ids = g.duplicate_nodes([a.id, b.id])
        self.assertEqual(len(new_ids), 2)
        self.assertEqual(len(g.wires), 2)
        self.assertEqual(len(g.nodes), 4)


class TestSerialisation(unittest.TestCase):
    def test_roundtrip(self):
        g = _graph()
        example_graph(g)
        text = g.to_json()
        g2 = Graph.from_json(text, default_registry(), StubBackend())
        self.assertEqual(g2.canvas_id, g.canvas_id)
        self.assertEqual(set(g2.nodes), set(g.nodes))
        self.assertEqual(set(g2.wires), set(g.wires))
        g2.evaluate()
        self.assertEqual(g2.errors(), {})
        preview = [n for n in g2.nodes.values() if n.type_id == "out.preview"][0]
        self.assertEqual(len(preview.outputs["geometry"]), 1)
        self.assertTrue(json.loads(text)["format"].startswith("freecad-grasshopper"))

    def test_unknown_nodes_skipped(self):
        data = {
            "nodes": [{"id": "x", "type": "does.not.exist"}, {"id": "y", "type": "param.slider"}],
            "wires": [{"src": ["x", "value"], "dst": ["y", "value"]}],
        }
        g = Graph.from_dict(data, default_registry(), StubBackend())
        self.assertEqual(list(g.nodes), ["y"])
        self.assertEqual(len(g.wires), 0)

    def test_vec3_serialises(self):
        g = _graph()
        p = g.add_node("vec.point", 0, 0, {"x": 1, "y": 2, "z": 3})
        n = g.add_node("xform.move", 0, 0, {"vector": Vec3(1, 1, 1)})
        data = g.to_dict()
        self.assertEqual(data["nodes"][1]["values"]["vector"], [1.0, 1.0, 1.0])
        g2 = Graph.from_dict(data, default_registry(), StubBackend())
        self.assertEqual(g2.nodes[n.id].values["vector"], Vec3(1, 1, 1))
        del p


class TestHelpers(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(coerce("number", "2.5"), 2.5)
        self.assertEqual(coerce("int", 2.7), 3)
        self.assertTrue(coerce("bool", "yes"))
        self.assertFalse(coerce("bool", "off"))
        self.assertEqual(coerce("point", [1, 2, 3]), Vec3(1, 2, 3))
        self.assertEqual(coerce("point", {"x": 1, "y": 2}), Vec3(1, 2, 0))
        self.assertEqual(coerce("number", [1, "2"]), [1.0, 2.0])
        self.assertIsNone(coerce("number", None))

    def test_flatten(self):
        self.assertEqual(flatten([1, [2, [3, 4]], 5]), [1, 2, 3, 4, 5])
        self.assertEqual(flatten(7), [7])

    def test_registry_search(self):
        reg = default_registry()
        self.assertTrue(any(t.type_id == "solid.box" for t in reg.search("box")))
        self.assertIn("Params", reg.categories())
        custom = Registry()
        custom.register(NodeType("t", "T", "C", [PortSpec("a")], [PortSpec("b")]))
        g = Graph(custom)
        n = g.add_node("t", values={"a": 5})
        g.evaluate()
        self.assertEqual(n.outputs["b"], 5)


if __name__ == "__main__":
    unittest.main()
