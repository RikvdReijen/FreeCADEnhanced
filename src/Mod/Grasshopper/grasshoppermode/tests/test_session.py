# SPDX-License-Identifier: LGPL-2.1-or-later
import base64
import os
import shutil
import tempfile
import unittest

from grasshoppermode import layout, media, protocol
from grasshoppermode.geometry import StubBackend
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry, example_graph
from grasshoppermode.session import Session, _fmt_value


class _Client:
    def __init__(self):
        self.messages = []

    def send(self, msg):
        # make sure everything is JSON serialisable
        protocol.decode(protocol.encode(msg))
        self.messages.append(msg)

    def types(self):
        return [m["t"] for m in self.messages]

    def last(self, t):
        for m in reversed(self.messages):
            if m["t"] == t:
                return m
        return None


class TestSession(unittest.TestCase):
    def setUp(self):
        self.graph = Graph(default_registry(), StubBackend(), canvas_id="cv1")
        example_graph(self.graph)
        self.saved = []
        self.media_root = tempfile.mkdtemp()
        self.hooked = []
        self.session = Session(
            self.graph,
            base_url="http://10.0.0.2:8765",
            autosave=lambda g: self.saved.append(g.revision),
            media_dir=self.media_root,
            export_hook=lambda path, node: self.hooked.append(path) or "ok",
        )
        self.client = _Client()
        self.session.add_client("a", self.client.send)

    def test_join_sends_hello_graph_preview(self):
        self.assertEqual(self.client.types()[:3], ["hello", "graph", "preview"])
        hello = self.client.last("hello")
        self.assertEqual(hello["canvas"], "cv1")
        self.assertIn("table-stylus", hello["profiles"])
        self.assertTrue(hello["marker"]["payload"].startswith("http://10.0.0.2:8765/xr#c=cv1"))
        self.assertEqual(len(hello["node_types"]), len(default_registry()))
        graph = self.client.last("graph")
        self.assertEqual(len(graph["nodes"]), len(self.graph.nodes))
        node = graph["nodes"][0]
        for key in ("x", "y", "w", "h", "inputs", "outputs", "widget_rect"):
            self.assertIn(key, node)
        preview = self.client.last("preview")
        self.assertEqual(len(preview["items"]), 1)
        self.assertEqual(len(preview["items"][0]["vertices"]), 24)

    def _slider(self, label):
        return [n for n in self.graph.nodes.values() if n.params.get("label") == label][0]

    def test_tap_selects_and_tap_empty_clears(self):
        node = self._slider("count")
        ack = self.session.handle("a", {"t": "tap", "x": node.x + 5, "y": node.y + 5, "rid": 1})
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["hit"]["kind"], "header")
        self.assertEqual(self.graph.selection, [node.id])
        self.assertEqual(self.client.last("selection")["selection"], [node.id])
        other = self._slider("radius")
        self.session.handle("a", {"t": "tap", "x": other.x + 5, "y": other.y + 5, "mode": "add"})
        self.assertEqual(self.graph.selection, [node.id, other.id])
        self.session.handle("a", {"t": "tap", "x": -1000, "y": -1000})
        self.assertEqual(self.graph.selection, [])

    def test_tap_on_slider_sets_value(self):
        node = self._slider("count")
        wr = layout.widget_rect(node, self.graph.node_type(node))
        self.session.handle("a", {"t": "tap", "x": wr[0] + wr[2], "y": wr[1] + wr[3] / 2})
        self.assertEqual(node.values["value"], 12.0)
        polar = [n for n in self.graph.nodes.values() if n.type_id == "xform.polar_array"][0]
        self.assertEqual(len(polar.outputs["shapes"]), 12)
        patch = self.client.last("patch")
        self.assertEqual(patch["op"], "values")

    def test_move_drag_phases_and_group(self):
        a, b = self._slider("count"), self._slider("radius")
        self.graph.select([a.id, b.id])
        ax, ay, bx, by = a.x, a.y, b.x, b.y
        self.session.handle("a", {"t": "move", "node": a.id, "phase": "begin", "x": ax, "y": ay})
        self.session.handle(
            "a", {"t": "move", "node": a.id, "phase": "update", "x": ax + 10, "y": ay + 20}
        )
        self.assertEqual((a.x, a.y), (ax + 10, ay + 20))
        self.assertEqual((b.x, b.y), (bx + 10, by + 20))
        self.session.handle("a", {"t": "move", "node": a.id, "phase": "end", "x": ax + 30, "y": ay})
        self.assertEqual((a.x, a.y), (ax + 30, ay))
        self.assertEqual(self.client.last("patch")["op"], "node")
        self.assertTrue(self.session.handle("a", {"t": "undo"})["undone"])
        # undo rebuilds the node objects, so look them up again
        a, b = self.graph.nodes[a.id], self.graph.nodes[b.id]
        self.assertEqual((a.x, a.y), (ax, ay))
        self.assertIn("graph", self.client.types()[-4:])
        # bare update without begin still works
        self.session.handle("b", {"t": "move", "node": b.id, "x": 1, "y": 2})
        self.assertEqual((b.x, b.y), (1.0, 2.0))
        bad = self.session.handle("a", {"t": "move", "node": "nope", "x": 0, "y": 0, "rid": 9})
        self.assertFalse(bad["ok"])

    def test_wire_and_disconnect(self):
        size = self._slider("size")
        cyl = [n for n in self.graph.nodes.values() if n.type_id == "solid.cylinder"][0]
        ack = self.session.handle(
            "a", {"t": "wire", "src": [size.id, "value"], "dst": [cyl.id, "height"]}
        )
        self.assertTrue(ack["ok"])
        self.assertEqual(cyl.outputs["shape"].bbox[5], 8.0)
        bad = self.session.handle(
            "a", {"t": "wire", "src": [cyl.id, "shape"], "dst": [size.id, "value"]}
        )
        self.assertFalse(bad["ok"])
        self.assertIn("shape", bad["error"])
        self.session.handle("a", {"t": "disconnect", "dst": [cyl.id, "height"]})
        self.assertEqual(cyl.outputs["shape"].bbox[5], 6.0)
        self.assertEqual(self.client.types()[-1], "preview")

    def test_add_delete_duplicate(self):
        ack = self.session.handle(
            "a", {"t": "add_node", "type": "solid.sphere", "x": 5, "y": 6, "values": {"radius": 3}}
        )
        nid = ack["node"]
        self.assertIn(nid, self.graph.nodes)
        self.assertEqual(self.graph.selection, [nid])
        size = self._slider("size")
        ack = self.session.handle(
            "a",
            {"t": "add_node", "type": "math.negate", "x": 0, "y": 0, "from": [size.id, "value"]},
        )
        neg = self.graph.nodes[ack["node"]]
        self.assertEqual(neg.outputs["result"], -8.0)
        ack = self.session.handle("a", {"t": "duplicate", "nodes": [nid]})
        self.assertEqual(len(ack["nodes"]), 1)
        self.session.handle("a", {"t": "delete", "nodes": [nid], "selection": True})
        self.assertNotIn(nid, self.graph.nodes)
        self.assertEqual(self.session.handle("a", {"t": "delete"}), protocol.ack(None, True))

    def test_values_params_profile_anchor_log(self):
        size = self._slider("size")
        self.session.handle(
            "a", {"t": "set_value", "node": size.id, "value": "4", "phase": "begin"}
        )
        self.assertEqual(size.values["value"], 4.0)
        self.session.handle("a", {"t": "set_param", "node": size.id, "key": "max", "value": 3})
        self.assertEqual(size.outputs["value"], 3.0)
        self.assertEqual(self.client.types()[-2:], ["patch", "preview"])
        self.assertTrue(
            self.session.handle("a", {"t": "set_profile", "profile": "table-hands"})["ok"]
        )
        self.assertEqual(self.client.last("profile")["profile"], "table-hands")
        self.assertFalse(self.session.handle("a", {"t": "set_profile", "profile": "x"})["ok"])
        self.session.handle(
            "a", {"t": "anchor", "method": "image", "frame": {"origin": [0, 0, 0]}, "uuid": "u1"}
        )
        self.assertEqual(self.session.anchors["a"]["method"], "image")
        self.session.handle("a", {"t": "log", "text": "hello from headset"})
        self.assertIn("hello from headset", self.session.log[-1])
        self.assertFalse(self.session.handle("a", {"t": "bogus"})["ok"])
        self.assertTrue(
            self.session.handle(
                "a", {"t": "hello", "caps": {"hands": True}, "profile": "table-hands"}
            )["ok"]
        )
        self.assertTrue(self.session.clients["a"]["caps"]["hands"])
        self.assertTrue(len(self.saved) > 0)

    def test_select_rect_and_get_graph(self):
        bounds = self.graph.bounds()
        ack = self.session.handle(
            "a", {"t": "select", "rect": [bounds[0] - 1, bounds[1] - 1, 10000, 10000]}
        )
        self.assertEqual(set(ack["selection"]), set(self.graph.nodes))
        n = len(self.client.messages)
        self.session.handle("a", {"t": "get_graph"})
        self.assertEqual(self.client.types()[n:], ["graph", "preview"])

    def test_default_profile_and_view_relay(self):
        self.assertEqual(self.client.last("hello")["profile"], "floating-panel")
        phone = _Client()
        self.session.add_client("phone", phone.send)
        n = len(phone.messages)
        self.assertTrue(self.session.handle("phone", {"t": "pan", "dx": 10, "dy": -5})["ok"])
        self.assertTrue(self.session.handle("phone", {"t": "zoom", "factor": 1.5})["ok"])
        self.assertTrue(self.session.handle("phone", {"t": "fit"})["ok"])
        views = [m for m in self.client.messages if m["t"] == "view"]
        self.assertEqual([v["op"] for v in views], ["pan", "zoom", "fit"])
        self.assertEqual((views[0]["dx"], views[0]["dy"]), (10.0, -5.0))
        self.assertEqual(views[1]["factor"], 1.5)
        # the sender does not get its own view messages, and nothing is autosaved
        self.assertEqual([m["t"] for m in phone.messages[n:]], [])
        self.assertEqual(self.saved, [])

    def tearDown(self):
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_snapshot_stroke_export(self):
        from grasshoppermode.tests.test_media import tiny_png

        png = "data:image/png;base64," + base64.b64encode(tiny_png(10, 5)).decode()
        ack = self.session.handle(
            "a",
            {"t": "snapshot", "png": png, "mode": "outline", "pose": {"p": [0, 1, 2]}, "rid": 1},
        )
        self.assertTrue(ack["ok"], ack)
        node = self.graph.nodes[ack["node"]]
        self.assertEqual(node.type_id, "media.image")
        self.assertEqual(node.params["mode"], "outline")
        self.assertEqual(node.params["pose"], {"p": [0, 1, 2]})
        self.assertTrue(self.session.media.exists(ack["file"]))
        self.assertEqual(self.graph.selection, [node.id])
        snap = [n for n in self.client.last("graph")["nodes"] if n["id"] == node.id][0]
        self.assertEqual(snap["widget"], "image")
        self.assertEqual(snap["params"]["url"], ack["url"])
        self.assertEqual(snap["params"]["strokes"], [])
        ack2 = self.session.handle(
            "a",
            {
                "t": "stroke",
                "node": node.id,
                "points": [[1, 1], [8, 4]],
                "color": "#0f0",
                "width": 2,
            },
        )
        self.assertEqual(ack2["strokes"], 1)
        self.assertEqual(
            self.client.last("graph")["nodes"][-1]["params"]["strokes"][0]["color"], "#0f0"
        )
        # export without a client-rendered PNG falls back to the SVG bundle and calls the hook
        ack3 = self.session.handle("a", {"t": "export_picture", "node": node.id})
        self.assertTrue(ack3["path"].endswith("-export.svg"))
        self.assertEqual(ack3["hook"], "ok")
        self.assertEqual(self.hooked, [ack3["path"]])
        ack4 = self.session.handle("a", {"t": "export_picture", "node": node.id, "png": png})
        self.assertTrue(ack4["path"].endswith("-export.png"))
        self.assertTrue(os.path.isfile(ack4["path"]))
        self.assertTrue(self.session.handle("a", {"t": "clear_strokes", "node": node.id})["ok"])
        self.assertEqual(node.params["strokes"], [])
        bad = self.session.handle(
            "a", {"t": "snapshot", "png": "data:image/png;base64,AAAA", "rid": 2}
        )
        self.assertFalse(bad["ok"])
        other = [n for n in self.graph.nodes.values() if n.type_id == "math.add"] or [
            self.graph.add_node("math.add")
        ]
        self.assertFalse(
            self.session.handle("a", {"t": "export_picture", "node": other[0].id})["ok"]
        )

    def test_remove_client(self):
        second = _Client()
        self.session.add_client("b", second.send)
        self.assertEqual(self.client.last("clients")["count"], 2)
        self.session.remove_client("b")
        self.assertEqual(self.client.last("clients")["count"], 1)

    def test_fmt_value(self):
        b = StubBackend()
        self.assertEqual(_fmt_value(b, None), "-")
        self.assertEqual(_fmt_value(b, True), "true")
        self.assertEqual(_fmt_value(b, 3.14159), "3.142")
        self.assertEqual(_fmt_value(b, [1, 2, 3]), "[3] 1")
        self.assertEqual(_fmt_value(b, []), "[]")
        self.assertIn("box", _fmt_value(b, b.box(1, 1, 1)))
        self.assertEqual(_fmt_value(b, {"a": 1}), "{a: 1}")
        self.assertTrue(_fmt_value(b, "x" * 50).endswith("..."))


if __name__ == "__main__":
    unittest.main()
