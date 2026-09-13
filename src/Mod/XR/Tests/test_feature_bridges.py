# SPDX-License-Identifier: LGPL-2.1-or-later
"""The xr-v0.2 bridges against the stubs: they import, wire up, and run their
pure paths without a viewer.

Everything geometric is covered by the subsystem tests; what this file pins
is the glue — session creation through ``service``, the per-frame handlers
tolerating no viewer, the voice context, the QR sink applying a snap, and
the wrist menu knowing every new button.
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from Tests import stubs  # noqa: E402


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

    def setUp(self):
        from xrcore import service

        self.service = service
        for name in list(service.features()):
            service.set_feature(name, None)


class TestService(BridgeCase):
    def test_feature_map(self):
        s = self.service
        self.assertIsNone(s.get_feature("assembly"))
        s.set_feature("assembly", "x")
        self.assertEqual(s.require_feature("assembly"), "x")
        self.assertEqual(s.features(), {"assembly": "x"})
        s.set_feature("assembly", None)
        with self.assertRaises(s.XRServiceError):
            s.require_feature("assembly")


class TestHaptics(BridgeCase):
    def test_engine_and_toggle(self):
        from xrcore import haptics_bridge

        eng = haptics_bridge.engine()
        self.assertIs(self.service.get_feature("haptics"), eng)
        self.assertTrue(haptics_bridge.set_enabled(True))
        self.assertEqual(haptics_bridge.set_intensity(2.0), 1.0)
        haptics_bridge.test_pulse()  # NullBackend: no error
        self.assertEqual(haptics_bridge.pump(), 0)
        self.assertEqual(haptics_bridge.haptic_bindings(type("W", (), {"haptic_action": None})(), None), [])


class TestAssemblyAndFit(BridgeCase):
    def test_sessions_without_a_document(self):
        from xrcore import assembly_bridge, fit_bridge

        session = assembly_bridge.ensure_session()
        self.assertEqual(len(session.parts), 0)
        self.assertFalse(assembly_bridge.handle_frame(0.016, []))
        assembly_bridge.activate()
        self.assertTrue(assembly_bridge.active())
        self.assertFalse(assembly_bridge.handle_frame(0.016, []))
        assembly_bridge.deactivate()
        self.assertIsNone(assembly_bridge.release())
        fit = fit_bridge.ensure_session()
        self.assertEqual(len(fit.parts), 0)
        fit_bridge.activate()
        self.assertEqual(fit_bridge.status_text(), "fit: grab a part")
        with self.assertRaises(self.service.XRServiceError):
            fit_bridge.probe("nothing")
        fit_bridge.deactivate()

    def test_grab_nearest_and_lock(self):
        from xrassembly import AxisFeature, Features
        from xrcore import assembly_bridge
        from xrsketch import vecmath as vm

        session = assembly_bridge.ensure_session()
        session.add_part("Base", Features([AxisFeature("a", (0, 0, 0), (0, 0, 1), 0.01, 0.1)]), fixed=True)
        session.add_part("Peg", Features([AxisFeature("s", (0, 0, 0), (0, 0, 1), 0.009, 0.05)]), pose=vm.Transform((0.05, 0, 0)))
        self.assertEqual(assembly_bridge.grab_nearest(session, vm.Transform((0.06, 0, 0))), "Peg")
        self.assertEqual(assembly_bridge.release(), "Peg")


class TestVoice(BridgeCase):
    def test_say_and_context(self):
        from xrcore import voice_bridge

        session = voice_bridge.ensure_session()
        self.assertIn(session.backend.name, ("text", "vosk"))
        results = voice_bridge.say("select nothing")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].intent.name, "deselect")
        self.assertEqual(voice_bridge.remote_sink({"text": "undo", "confidence": 0.9}, "abcd1234"), "queued")
        polled = voice_bridge.poll()
        self.assertEqual(polled[0].intent.name, "undo")
        self.assertIn("voice", voice_bridge.status_text())
        self.assertFalse(voice_bridge.listening())


class TestPresence(BridgeCase):
    def test_without_server(self):
        from xrcore import presence_bridge

        self.assertFalse(presence_bridge.handle_frame(0.016, []))
        self.assertEqual(presence_bridge.peers(), [])
        self.assertEqual(presence_bridge.status_text(), "")


class TestCam(BridgeCase):
    def test_machine_and_status(self):
        from xrcam import CamSession, parse_gcode
        from xrcore import cam_bridge

        machine = cam_bridge.machine_for_environment("bambu_x1c")
        self.assertGreater(machine.bed[0], 100)
        session = CamSession(parse_gcode("G1 X10 Y10 F600\nG1 X20\n", name="t"), machine)
        self.service.set_feature("cam", session)
        cam_bridge.play()
        self.assertTrue(cam_bridge.toggle() is False)
        self.assertEqual(cam_bridge.set_speed(4.0), 4.0)
        self.assertIn("cam:", cam_bridge.status_text())
        self.assertFalse(cam_bridge.handle_frame(0.1, []))
        cam_bridge.clear()
        with self.assertRaises(self.service.XRServiceError):
            cam_bridge.play()


class TestDrawScanQr(BridgeCase):
    def test_draw_session(self):
        from xrcore import draw_bridge

        session = draw_bridge.ensure_session()
        self.assertEqual(session.table.page_mm[0], 420.0)
        self.assertIsNone(draw_bridge.place_dimension())
        self.assertFalse(draw_bridge.handle_frame(0.016, []))
        self.assertIsNone(draw_bridge.undo())

    def test_scan_requires_import(self):
        from xrcore import scan_bridge

        with self.assertRaises(self.service.XRServiceError):
            scan_bridge.activate()
        with self.assertRaises(self.service.XRServiceError):
            scan_bridge.align()

    def test_qr_sink_applies_part_snap(self):
        from xrcore import qr_bridge
        from xrqr import AnchorPayload

        qr_bridge.ensure_session()
        text = AnchorPayload("bench", 80, origin="part:Nope").encode()
        corners = [[0, 0.1, 0], [0.08, 0.1, 0], [0.08, 0.02, 0], [0, 0.02, 0]]
        outcomes = [qr_bridge.sink({"text": text, "corners": corners, "time": t}, "peer") for t in (0.0, 0.03, 0.06)]
        self.assertEqual(outcomes[:2], ["seen", "seen"])
        self.assertIn(outcomes[2], ("no part", "no viewer"))
        self.assertIn("qr: bench", qr_bridge.status_text())
        self.assertTrue(qr_bridge.make_code("t1", 60).startswith("fcxr://") or qr_bridge.make_code("t1", 60).endswith(".svg"))


class TestInk(BridgeCase):
    def test_stylus_without_extension(self):
        from xrcore import ink_bridge

        widget = type("W", (), {"ink_extension_enabled": False, "action_set": None, "hand_paths": [0, 1]})()
        self.assertEqual(ink_bridge.create_actions(widget, None), {})
        self.assertFalse(ink_bridge.suggest_bindings(widget, None))
        self.assertIsNone(ink_bridge.poll(widget, None))
        self.assertEqual(ink_bridge.wanted_extensions(["XR_LOGITECH_mx_ink_interaction"]), ["XR_LOGITECH_mx_ink_interaction"])
        self.assertEqual(ink_bridge.pressure(), 0.0)


class TestMenu(BridgeCase):
    def test_every_new_button_is_handled(self):
        from xrcore import menu_ext

        for name in ("xr_assembly_button", "xr_fit_button", "xr_draw_button", "xr_scan_button", "xr_mode_confirm_button",
                     "xr_mode_undo_button", "xr_cam_play_button", "xr_voice_button", "xr_haptics_button", "xr_scan_align_button"):
            self.assertIn(name, menu_ext.BUTTONS)
            self.assertTrue(menu_ext.handle(name), name)  # errors are reported, never raised
        self.assertFalse(menu_ext.handle("no_such_button"))


class TestCommands(BridgeCase):
    def test_new_commands_registered(self):
        from xrcore import commands

        for name in commands.NEW_COMMANDS:
            self.assertIn(name, stubs.recorded_commands, name)
            self.assertIn(name, commands.ALL_COMMANDS)


if __name__ == "__main__":
    unittest.main()


class TestRoom(BridgeCase):
    def test_without_server_and_status(self):
        from xrcore import room_bridge

        self.assertIsNone(room_bridge.room())
        self.assertFalse(room_bridge.handle_frame(0.016, []))
        self.assertEqual(room_bridge.status_text(), "")
        with self.assertRaises(self.service.XRServiceError):
            room_bridge.host()
        with self.assertRaises(self.service.XRServiceError):
            room_bridge.teleport_to_peer()
        self.assertIsNone(room_bridge.share_edit([{"op": "set_param", "target": "x", "from": 1, "to": 2}]))
        for name in ("xr_room_goto_button", "xr_room_commit_button"):
            from xrcore import menu_ext

            self.assertIn(name, menu_ext.BUTTONS)
            self.assertTrue(menu_ext.handle(name))

    def test_hosting_a_live_server(self):
        from xrcore import room_bridge
        from xrsync.server import SyncServer

        from Tests.test_presence import FakeBridge

        server = SyncServer(port=0, bridge=FakeBridge(), auth_required=False, discovery=False, devices_path=os.devnull)
        self.service._state["sync_server"] = server
        try:
            room = room_bridge.host(server)
            self.assertEqual(room.host, "desktop")
            self.assertIs(server.edit_sink, room_bridge.apply_edit)
            self.assertIn("room:", room_bridge.status_text())
            outcome = room_bridge.apply_edit({"operations": [{"op": "set_param", "target": "x", "from": 1, "to": 2}]}, "peer")
            self.assertIn("applied", outcome)
            self.assertFalse(room_bridge.handle_frame(0.016, []))
        finally:
            self.service._state["sync_server"] = None
