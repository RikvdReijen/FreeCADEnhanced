# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drive the bridges against the real subsystems.

``test_contracts.py`` checks that the names the GUI calls exist.  This file
goes a step further and actually calls them, with FreeCAD, Qt and Coin stubbed
out, so a signature that matches but behaves differently is caught here rather
than in a headset.
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from Tests import stubs  # noqa: E402


def _available(module_name):
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


HAVE_ENV = _available("xrenv.registry")
HAVE_PAINT = _available("xrpaint.session")


class BridgeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs.install()

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith("xrcore"):
                del sys.modules[name]
        stubs.uninstall()


@unittest.skipUnless(HAVE_ENV, "xrenv is not available")
class TestEnvironmentBridge(BridgeCase):
    def setUp(self):
        from xrcore import environment_bridge, service

        self.bridge = environment_bridge
        self.service = service
        self.manager = environment_bridge.manager()
        # Start from a known state; the manager is a process-wide singleton.
        self.manager.detach()
        self.manager.environment = None
        service.set_environment_id("studio")

    def test_lists_the_environments_the_dialog_shows(self):
        infos = self.bridge.available_environments()
        self.assertTrue(infos)
        for info in infos:
            self.assertTrue(info.id and info.name)
            self.assertGreater(info.user_scale, 0.0)

    def test_selecting_an_environment_without_a_viewer_only_stores_it(self):
        environment = self.bridge.set_environment("laser_cutter")
        self.assertEqual(environment.info.id, "laser_cutter")
        self.assertEqual(self.service.get_environment_id(), "laser_cutter")
        # No viewer, so nothing was built.
        self.assertFalse(self.manager.is_live)

    def test_cycling_visits_every_environment_and_returns(self):
        ids = [info.id for info in self.bridge.available_environments()]
        self.bridge.set_environment(ids[0])
        seen = [ids[0]]
        for _ in range(len(ids) - 1):
            seen.append(self.bridge.cycle_environment(1).info.id)
        self.assertCountEqual(seen, ids)
        self.assertEqual(self.bridge.cycle_environment(1).info.id, ids[0])

    def test_cycling_backwards_is_the_inverse(self):
        ids = [info.id for info in self.bridge.available_environments()]
        self.bridge.set_environment(ids[0])
        self.bridge.cycle_environment(1)
        self.assertEqual(self.bridge.cycle_environment(-1).info.id, ids[0])

    def test_scale_is_clamped_to_the_documented_range(self):
        self.assertAlmostEqual(self.bridge.manager().set_scale(10_000.0), self.bridge.MAX_SCALE)
        self.assertAlmostEqual(self.bridge.manager().set_scale(0.0001), self.bridge.MIN_SCALE)

    def test_machine_environments_actually_miniaturise_the_user(self):
        """The whole point: you must end up small enough to stand inside."""
        for env_id in ("bambu_x1c", "laser_cutter"):
            environment = self.bridge.set_environment(env_id)
            self.assertGreater(
                environment.user_scale,
                4.0,
                f"{env_id} does not shrink the user meaningfully",
            )
            height_mm = 1700.0 / environment.user_scale
            self.assertLess(height_mm, 400.0, f"you would not fit inside {env_id}")
            anchor = environment.primary_anchor()
            self.assertIsNotNone(anchor, f"{env_id} has nowhere to put the model")

    def test_open_environments_leave_you_life_sized(self):
        for env_id in ("studio", "void"):
            environment = self.bridge.set_environment(env_id)
            self.assertAlmostEqual(environment.user_scale, 1.0)

    def test_current_state_reports_what_the_menu_shows(self):
        self.bridge.set_environment("workshop")
        state = self.bridge.current_state()
        self.assertEqual(state["environment"], "workshop")
        self.assertGreater(state["scale"], 0.0)
        self.assertFalse(state["live"])


@unittest.skipUnless(HAVE_PAINT, "xrpaint is not available")
class TestPaintBridge(BridgeCase):
    def setUp(self):
        from xrcore import paint_bridge, service

        self.bridge = paint_bridge
        self.service = service
        service.set_paint_session(None)

    def test_session_is_created_on_demand_and_reused(self):
        self.assertIsNone(self.bridge.get_session())
        session = self.bridge.ensure_session()
        self.assertIsNotNone(session)
        self.assertIs(self.bridge.ensure_session(), session)

    def test_every_mode_the_commands_offer_is_accepted(self):
        for mode in self.bridge.MODES:
            session = self.bridge.activate_mode(mode)
            self.assertEqual(session.mode, mode)
        self.bridge.deactivate()
        self.assertIsNone(self.bridge.get_session().mode)

    def test_unknown_modes_are_refused(self):
        with self.assertRaises(self.service.XRServiceError):
            self.bridge.activate_mode("SCULPT")

    def test_committing_an_empty_drawing_explains_itself(self):
        self.bridge.ensure_session()
        with self.assertRaises(self.service.XRServiceError) as caught:
            self.bridge.commit_vector_document()
        self.assertIn("empty", str(caught.exception).lower())

    def test_exporting_an_empty_drawing_explains_itself(self):
        self.bridge.ensure_session()
        with self.assertRaises(self.service.XRServiceError):
            self.bridge.export_svg("/tmp/xr-should-not-be-written.svg")
        self.assertFalse(os.path.exists("/tmp/xr-should-not-be-written.svg"))

    def test_a_drawn_path_survives_the_round_trip_to_svg(self):
        from xrpaint import vector

        session = self.bridge.ensure_session()
        document = vector.VectorDocument()
        path = document.add_path()
        for point in ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)):
            path.append_node(vector.Node(point))
        session.vector_document = document

        target = os.path.join(
            os.environ.get("TMPDIR", "/tmp"), "xr-bridge-export.svg"
        )
        try:
            self.bridge.export_svg(target)
            with open(target, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("<svg", text)
            self.assertIn("path", text)
        finally:
            if os.path.exists(target):
                os.remove(target)

    def test_paint_manifest_matches_the_documented_shape(self):
        session = self.bridge.ensure_session()
        manifest = self.bridge.paint_manifest()
        if manifest is None:
            self.skipTest("session exports no manifest until something is painted")
        self.assertIn("version", manifest)
        self.assertIn("targets", manifest)
        self.assertIsInstance(manifest["targets"], list)
        del session

    def test_frame_updates_are_a_no_op_while_idle(self):
        self.bridge.ensure_session()
        self.bridge.deactivate()
        self.assertFalse(self.bridge.handle_frame(0.016, []))


@unittest.skipUnless(HAVE_ENV and HAVE_PAINT, "subsystems not available")
class TestVrMenuActions(BridgeCase):
    """The wrist menu is the only UI reachable with a headset on."""

    def setUp(self):
        from xrcore import menu_ext, service

        self.menu_ext = menu_ext
        self.service = service
        service.set_paint_session(None)
        service.set_environment_id("studio")

    def test_every_button_runs_without_raising(self):
        for name in self.menu_ext.BUTTONS:
            self.assertTrue(self.menu_ext.handle(name), name)

    def test_the_paint_buttons_switch_mode(self):
        from xrcore import paint_bridge

        self.menu_ext.handle("xr_paint_texture_button")
        self.assertEqual(paint_bridge.get_session().mode, "TEXTURE")
        self.menu_ext.handle("xr_paint_vector_button")
        self.assertEqual(paint_bridge.get_session().mode, "VECTOR")
        self.menu_ext.handle("xr_paint_off_button")
        self.assertIsNone(paint_bridge.get_session().mode)

    def test_the_environment_buttons_change_the_environment(self):
        before = self.service.get_environment_id()
        self.menu_ext.handle("xr_env_next_button")
        self.assertNotEqual(self.service.get_environment_id(), before)
        self.menu_ext.handle("xr_env_prev_button")
        self.assertEqual(self.service.get_environment_id(), before)

    def test_the_status_line_describes_the_user(self):
        text = self.menu_ext._status_text()
        self.assertIn("1:", text)
        self.assertIn("you:", text)


if __name__ == "__main__":
    unittest.main()
