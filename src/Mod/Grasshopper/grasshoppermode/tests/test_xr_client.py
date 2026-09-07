# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end test of the WebXR client in desktop-simulator mode.

Needs the optional ``playwright`` package with a Chromium build; skipped
otherwise.  Drives the simulator's stylus/flat-hand pointers and checks that
the graph inside the server changes accordingly.
"""

import os
import time
import unittest

from grasshoppermode import layout
from grasshoppermode.geometry import StubBackend
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry, example_graph
from grasshoppermode.session import Session
from grasshoppermode.xrserver import XRServer

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Resources", "xr"
)


def _wait(fn, timeout=5.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return fn()


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class TestXRClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = Graph(default_registry(), StubBackend(), canvas_id="e2e")
        example_graph(cls.graph)
        cls.session = Session(cls.graph)
        cls.server = XRServer(cls.session, STATIC, host="127.0.0.1", port=0).start()
        cls.pw = sync_playwright().start()
        # software GL; compositing off keeps headless frame rates usable
        args = [
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
            "--disable-gpu-compositing",
        ]
        candidates = [os.environ.get("GH_CHROMIUM"), "/opt/pw-browsers/chromium", None]
        try:
            cls.browser = None
            last = None
            for exe in candidates:
                if exe is not None and not os.path.exists(exe):
                    continue
                try:
                    cls.browser = (
                        cls.pw.chromium.launch(args=args, executable_path=exe)
                        if exe
                        else cls.pw.chromium.launch(args=args)
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last = exc
            if cls.browser is None:
                raise last or RuntimeError("no chromium")
        except Exception as exc:  # pragma: no cover
            cls.pw.stop()
            cls.server.stop()
            raise unittest.SkipTest("chromium unavailable: %s" % exc)
        cls.page = cls.browser.new_page(viewport={"width": 720, "height": 480})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.on("console", lambda m: cls.errors.append(m.text) if m.type == "error" else None)
        cls.page.goto("http://127.0.0.1:%d/xr#auto=sim" % cls.server.bound_port)
        cls.page.wait_for_function(
            "window.app && window.app.mode === 'sim' && window.app.view.nodes.size > 0",
            timeout=15000,
        )
        cls.page.wait_for_timeout(300)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.page.screenshot(
                path=os.path.join(
                    os.environ.get("GH_TEST_ARTIFACTS", "/tmp"), "grasshopper_xr_sim.png"
                )
            )
        except Exception:  # pragma: no cover
            pass
        cls.browser.close()
        cls.pw.stop()
        cls.server.stop()

    def _drive(self, action, cx, cy, settle=0):
        # resolves once the simulator has processed the input (two frames)
        self.page.evaluate("([a, x, y]) => window.app.sim.drive(a, x, y)", [action, cx, cy])
        if settle:
            self.page.wait_for_timeout(settle)

    def _node(self, label):
        return [n for n in self.graph.nodes.values() if n.params.get("label") == label][0]

    def test_01_renders_and_connects(self):
        self.assertEqual(self.errors, [])
        state = self.page.evaluate(
            "() => ({nodes: app.view.nodes.size, wires: app.view.wires.size, previews: app.view.previews.length, connected: app.net.connected, draws: app.renderer.stats.draws})"
        )
        self.assertEqual(state["nodes"], len(self.graph.nodes))
        self.assertEqual(state["wires"], len(self.graph.wires))
        self.assertEqual(state["previews"], 1)
        self.assertTrue(state["connected"])
        self.assertGreater(state["draws"], 5)
        self.assertIn(
            "Number Slider", self.page.evaluate("() => app.view.nodeTypes.map(t => t.label)")
        )

    def test_02_tap_selects(self):
        node = self._node("count")
        self._drive("move", node.x + 20, node.y + 10)
        self._drive("down", node.x + 20, node.y + 10)
        self._drive("up", node.x + 20, node.y + 10)
        self.assertTrue(_wait(lambda: self.graph.selection == [node.id]))
        # tap on an empty part of the sheet clears (taps off the sheet are ignored)
        self._drive("down", 40, 620)
        self._drive("up", 40, 620)
        self.assertTrue(_wait(lambda: self.graph.selection == []))
        kinds = self.page.evaluate(
            "() => app.gestures.events.filter(e => e.kind === 'tap').map(e => e.hit)"
        )
        self.assertEqual(kinds[-2:], ["header", "empty"])

    def test_03_drag_moves_node(self):
        node = self._node("radius")
        x0, y0 = node.x, node.y
        self._drive("down", x0 + 30, y0 + 10)
        for i in range(1, 6):
            self._drive("move", x0 + 30 + i * 12, y0 + 10 + i * 8)
        self._drive("up", x0 + 90, y0 + 50)
        self.assertTrue(
            _wait(
                lambda: abs(self.graph.nodes[node.id].x - (x0 + 60)) < 1
                and abs(self.graph.nodes[node.id].y - (y0 + 40)) < 1
            ),
            "node did not move: %s" % ((self.graph.nodes[node.id].x, self.graph.nodes[node.id].y),),
        )

    def test_04_flat_hand_pans(self):
        before = self.page.evaluate("() => ({...app.view.view})")
        self._drive("move", 400, 400)
        self._drive("flat", 400, 400)
        for i in range(1, 6):
            self._drive("move", 400 + i * 20, 400)
        self._drive("unflat", 500, 400)
        after = self.page.evaluate("() => ({...app.view.view})")
        self.assertLess(after["ox"], before["ox"] - 40)
        self.assertAlmostEqual(after["oy"], before["oy"], delta=2)
        palms = self.page.evaluate(
            "() => app.gestures.events.filter(e => e.kind === 'palm').length"
        )
        self.assertGreaterEqual(palms, 1)

    def test_05_wire_drag(self):
        size = self._node("size")
        cyl = [n for n in self.graph.nodes.values() if n.type_id == "solid.cylinder"][0]
        out = [p for p in layout.output_ports(size, self.graph.node_type(size)) if p[0] == "value"][
            0
        ]
        inp = [p for p in layout.input_ports(cyl, self.graph.node_type(cyl)) if p[0] == "height"][0]
        self._drive("move", out[1], out[2])
        self._drive("down", out[1], out[2])
        for t in (0.25, 0.5, 0.75, 1.0):
            self._drive("move", out[1] + (inp[1] - out[1]) * t, out[2] + (inp[2] - out[2]) * t)
        self._drive("up", inp[1], inp[2])
        self.assertTrue(
            _wait(
                lambda: any(
                    w.src_node == size.id and w.dst_node == cyl.id and w.dst_port == "height"
                    for w in self.graph.wires.values()
                )
            )
        )
        self.assertEqual(self.graph.nodes[cyl.id].outputs["shape"].bbox[5], 8.0)

    def test_06_slider_and_palette(self):
        count = self._node("count")
        wr = layout.widget_rect(count, self.graph.node_type(count))
        self._drive("down", wr[0] + wr[2] * 0.5, wr[1] + wr[3] / 2)
        self._drive("move", wr[0] + wr[2] * 0.95, wr[1] + wr[3] / 2)
        self._drive("up", wr[0] + wr[2] * 0.95, wr[1] + wr[3] / 2)
        self.assertTrue(_wait(lambda: self.graph.nodes[count.id].values["value"] >= 11))
        # hold on empty sheet opens the palette, tapping the first item adds a node
        n_before = len(self.graph.nodes)
        self._drive("down", 40, 700)
        self.page.wait_for_timeout(800)
        self._drive("up", 40, 700)
        item = self.page.evaluate("() => app.view.palette ? app.view.palette.items[0] : null")
        self.assertIsNotNone(item, "palette did not open")
        cx, cy = item["rect"][0] + 10, item["rect"][1] + 10
        self._drive("down", cx, cy)
        self._drive("up", cx, cy)
        self.assertTrue(_wait(lambda: len(self.graph.nodes) == n_before + 1))
        newest = list(self.graph.nodes.values())[-1]
        self.assertEqual(newest.type_id, item["type"])

    def test_07_no_js_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
