# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Coin renderer must handle every kind of object the sketch scene holds.

This exists because of a bug it now catches: the renderer originally dispatched
on the ``SketchObject`` wrapper rather than on the geometry in its ``.data``,
so it silently drew nothing at all. Nothing else in the suite would have
noticed — the scene was correct, the commit was correct, and only a headset
would have shown the empty world.
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from Tests import stubs  # noqa: E402


def _sample_scene():
    """A scene holding one object of every kind the renderer must draw."""
    from xrsketch.curves import Curve3D
    from xrsketch.primitives import Primitive
    from xrsketch.reference import ImagePlane, Measurement
    from xrsketch.scene import Scene
    from xrsketch.subd import cube_cage
    from xrsketch.surfacing import loft

    scene = Scene()
    kinds = {}

    curve = Curve3D.from_points([(0, 0, 0), (0.2, 0.3, 0), (0.5, 0.1, 0.2)])
    kinds["curve"] = scene.add_curve(curve)
    kinds["cage"] = scene.add_cage(cube_cage(0.4))
    kinds["primitive"] = scene.add_primitive(Primitive("box"))

    rail_a = Curve3D.from_points([(0, 0, 0), (0.5, 0, 0), (1.0, 0, 0)])
    rail_b = Curve3D.from_points([(0, 0.4, 0), (0.5, 0.4, 0), (1.0, 0.4, 0)])
    kinds["surface"] = scene.add_surface(loft([rail_a, rail_b]))

    kinds["image"] = scene.add(
        "image", ImagePlane(source="ref.png", size=(1.0, 1.0)), name="ref.png"
    )
    measurement = Measurement(
        kind="distance", points=[(0.0, 0.0, 0.0), (0.3, 0.0, 0.4)]
    )
    kinds["measure"] = scene.add("measure", measurement, name="M1")
    return scene, kinds


class TestSketchRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs.install()

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith("xrcore"):
                del sys.modules[name]
        stubs.uninstall()

    def setUp(self):
        from pivy.coin import SoSeparator

        from xrcore.sketch_render import SketchRenderer
        from xrsketch.session import SketchSession

        self.scene, self.kinds = _sample_scene()
        self.session = SketchSession(scene=self.scene)
        self.root = SoSeparator()
        self.renderer = SketchRenderer()
        self.renderer.attach(self.root, self.session)

    def test_every_object_kind_produces_a_node(self):
        built = self.renderer.rebuild()
        self.assertEqual(
            built,
            len(self.scene.objects),
            "the renderer skipped an object kind it is supposed to draw",
        )

    def test_each_kind_individually_produces_a_node(self):
        """Fail per kind, so a regression names the one that broke."""
        for kind, obj in self.kinds.items():
            with self.subTest(kind=kind):
                node = self.renderer._node_for(obj.data, obj)
                self.assertIsNotNone(node, f"{kind} produced no Coin node")

    def test_hidden_objects_are_skipped(self):
        self.kinds["curve"].visible = False
        built = self.renderer.rebuild()
        self.assertEqual(built, len(self.scene.objects) - 1)

    def test_unknown_geometry_is_skipped_rather_than_raising(self):
        class Mystery:
            pass

        self.assertIsNone(self.renderer._node_for(Mystery(), None))

    def test_update_is_gated_on_the_dirty_flag(self):
        self.session.changed = False
        self.assertFalse(self.renderer.update(), "rebuilt an unchanged scene")
        self.session.changed = True
        self.assertTrue(self.renderer.update(), "did not rebuild a changed scene")
        self.assertFalse(
            self.session.changed, "the dirty flag was not cleared after rebuilding"
        )

    def test_update_survives_a_broken_scene(self):
        """A drawing fault must not propagate into the XR render loop."""

        class Exploding:
            @property
            def objects(self):
                raise RuntimeError("boom")

        self.session.scene = Exploding()
        self.session.changed = True
        self.assertFalse(self.renderer.update())

    def test_detach_clears_the_subgraph(self):
        self.renderer.rebuild()
        self.renderer.detach()
        self.assertIsNone(self.renderer.root)
        self.assertIsNone(self.renderer.session)
        self.assertFalse(self.renderer.update())

    def test_identity_transforms_add_no_node(self):
        from xrsketch.scene import Transform

        self.assertIsNone(self.renderer._transform_node(None))
        self.assertIsNone(self.renderer._transform_node(Transform()))

    def test_a_placed_object_gets_a_transform_node(self):
        from xrsketch.scene import Transform

        moved = Transform()
        moved.translation = (0.1, 0.2, 0.3)
        self.assertIsNotNone(self.renderer._transform_node(moved))


class TestCurveTolerance(unittest.TestCase):
    """Curve flattening has to tighten as the user shrinks.

    At 1:12 a two-millimetre chord error is two centimetres of apparent
    error, which is visible as faceting on every curve in the scene.
    """

    @classmethod
    def setUpClass(cls):
        stubs.install()

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith("xrcore"):
                del sys.modules[name]
        stubs.uninstall()

    def test_tolerance_tightens_with_user_scale(self):
        from xrcore import environment_bridge, service
        from xrcore.sketch_render import SketchRenderer

        renderer = SketchRenderer()
        service.preferences().SetFloat("UserScale", 1.0)
        at_life_size = renderer._curve_tolerance()

        service.preferences().SetFloat("UserScale", 12.0)
        # current_state() reads the stored preference when no viewer is live.
        self.assertEqual(environment_bridge.current_state()["scale"], 12.0)
        miniaturised = renderer._curve_tolerance()

        self.assertLess(miniaturised, at_life_size)
        self.assertAlmostEqual(miniaturised, at_life_size / 12.0, places=9)


if __name__ == "__main__":
    unittest.main()
