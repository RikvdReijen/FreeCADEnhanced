# SPDX-License-Identifier: LGPL-2.1-or-later
"""MX Ink stylus state and the QR anchors."""

import math
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrink import ACTIONS, EXTENSION, PROFILE, PressureMap, StylusState, is_supported, route, suggested_bindings  # noqa: E402
from xrqr import AnchorPayload, QrSession, is_anchor, pose_from_corners, snap_to_code  # noqa: E402
from xrsketch import vecmath as vm  # noqa: E402


class ProfileTest(unittest.TestCase):
    def test_bindings(self):
        b = suggested_bindings()
        paths = [p for _, p in b]
        self.assertIn("/user/hand/right/input/tip_logitech/force", paths)
        self.assertIn("/user/hand/left/input/cluster_back_logitech/double_tap_logitech", paths)
        self.assertIn(("pose", "/user/hand/right/input/aim/pose"), b)
        self.assertEqual(len(b), 2 * (len(ACTIONS) + 2))
        self.assertTrue(PROFILE.startswith("/interaction_profiles/logitech/"))
        self.assertTrue(is_supported(["XR_KHR_opengl_enable", EXTENSION]))
        self.assertFalse(is_supported(["XR_KHR_opengl_enable"]))
        self.assertEqual(len(suggested_bindings(("right",), include_upstream=False)), len(ACTIONS))


class StylusTest(unittest.TestCase):
    def test_pressure_curves(self):
        lin = PressureMap(0.0, 1.0, "linear", deadzone=0.0)
        self.assertAlmostEqual(lin.apply(0.5), 0.5)
        self.assertGreater(PressureMap(0, 1, "soft", 0).apply(0.25), 0.25)
        self.assertLess(PressureMap(0, 1, "hard", 0).apply(0.5), 0.5)
        self.assertAlmostEqual(PressureMap(0.2, 1.0, "linear").apply(0.0), 0.2)
        self.assertAlmostEqual(PressureMap(0.2, 1.0, "linear").apply(1.0), 1.0)

    def test_tip_hysteresis_and_events(self):
        s = StylusState()
        s.update({"tip_force": 0.03})
        self.assertFalse(s.tip_down)
        s.update({"tip_force": 0.3})
        self.assertTrue(s.tip_down)
        s.update({"tip_force": 0.04})
        self.assertTrue(s.tip_down, "between release and press thresholds: still down")
        s.update({"tip_force": 0.01})
        self.assertFalse(s.tip_down)
        kinds = [e.kind for e in s.drain_events()]
        self.assertEqual(kinds, ["tip_down", "tip_pressure", "tip_up"])
        self.assertEqual(s.pressure, 0.0)

    def test_buttons_debounce_and_routes(self):
        s = StylusState(debounce=0.05)
        s.update({"front_click": True}, dt=0.1)
        s.update({"front_click": False}, dt=0.01)  # bounce
        self.assertTrue(s.buttons["front"], "bounce ignored")
        s.update({"front_click": False}, dt=0.1)
        s.update({"back_click": True, "double_tap": True}, dt=0.1)
        events = s.drain_events()
        routed = [route(e, s) for e in events]
        self.assertEqual(routed[0], ("confirm", None))
        self.assertEqual(routed[1], ("confirm_release", None))
        self.assertIn(("undo", None), routed)
        self.assertIn(("menu", None), routed)
        s.update({"tip_force": 0.5})
        e = s.drain_events()[0]
        self.assertEqual(route(e, s)[0], "trigger")
        self.assertGreater(route(e, s)[1], 0.0)
        buttons = s.as_controller_buttons()
        self.assertAlmostEqual(buttons["trigger"], 0.5)

    def test_dock_and_dict(self):
        s = StylusState(roles={"back": "redo"})
        s.update({"docked": True})
        s.update({"docked": False, "present": True})
        self.assertEqual([e.kind for e in s.drain_events()], ["docked", "undocked"])
        self.assertEqual(s.to_dict()["roles"]["back"], "redo")


class PayloadTest(unittest.TestCase):
    def test_round_trip(self):
        p = AnchorPayload("bench-1", 80, doc="housing.FCStd", origin="part:Bracket", up="y", extras={"note": "left wall"})
        text = p.encode()
        self.assertTrue(text.startswith("fcxr://anchor?id=bench-1&size=80"))
        q = AnchorPayload.decode(text)
        self.assertEqual(q.to_dict(), p.to_dict())
        self.assertEqual(q.part, "Bracket")
        self.assertTrue(is_anchor(text))
        self.assertFalse(is_anchor("https://example.com"))
        self.assertEqual(AnchorPayload.decode("fcxr://anchor?id=plate&size=60&target=build_plate").target, "build_plate")

    def test_validation(self):
        with self.assertRaises(ValueError):
            AnchorPayload("", 10)
        with self.assertRaises(ValueError):
            AnchorPayload("x", 0)
        with self.assertRaises(ValueError):
            AnchorPayload("x", 10, up="w")
        with self.assertRaises(ValueError):
            AnchorPayload.decode("fcxr://anchor?id=x&size=big")


def square(centre, size_m, rotation=vm.IDENTITY_QUAT, noise=0.0):
    t = vm.Transform(centre, rotation)
    h = size_m / 2.0
    corners = [(-h, h, 0), (h, h, 0), (h, -h, 0), (-h, -h, 0)]
    out = []
    for i, c in enumerate(corners):
        p = t.apply(c)
        if noise:
            p = (p[0] + noise * (1 if i % 2 else -1), p[1] + noise * (1 if i < 2 else -1), p[2] + noise * 0.5)
        out.append(p)
    return out


class PoseTest(unittest.TestCase):
    def test_pose_from_corners(self):
        from xrassembly.mates import rotation_about

        q = rotation_about((0, 1, 0), 0.4)
        centre = (0.5, 0.8, -0.3)
        code = pose_from_corners(square(centre, 0.08, q), 80)
        self.assertEqual([round(c, 9) for c in code.transform.translation], [round(c, 9) for c in centre])
        self.assertAlmostEqual(code.edge_mm, 80.0)
        self.assertAlmostEqual(code.scale_error, 0.0)
        self.assertLess(code.residual, 1e-9)
        n = code.normal
        expected = vm.Transform((0, 0, 0), q).apply_vector((0, 0, 1))
        self.assertAlmostEqual(vm.dot(n, expected), 1.0)
        with self.assertRaises(ValueError):
            pose_from_corners([(0, 0, 0)] * 4, 80)
        with self.assertRaises(ValueError):
            pose_from_corners([(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)], 80)

    def test_scale_error_reported(self):
        code = pose_from_corners(square((0, 0, 0), 0.079), 80)
        self.assertAlmostEqual(code.scale_error, -0.0125)

    def test_snap_respects_up(self):
        code = pose_from_corners(square((1, 1, 1), 0.08), 80)
        snap = snap_to_code(AnchorPayload("a", 80), code)
        self.assertEqual(snap.what, "model")
        self.assertEqual([round(c, 9) for c in snap.transform.translation], [1.0, 1.0, 1.0])
        z_up = snap.transform.apply_vector((0, 0, 1))
        self.assertAlmostEqual(z_up[2], 1.0, msg="model Z along the paper normal")
        snap = snap_to_code(AnchorPayload("a", 80, up="y"), code)
        y_up = snap.transform.apply_vector((0, 1, 0))
        self.assertAlmostEqual(y_up[2], 1.0, msg="with up=y the model's Y is the paper normal")
        snap = snap_to_code(AnchorPayload("a", 80, target="build_plate"), code, current=vm.Transform(scale=12.0))
        self.assertEqual(snap.what, "target:build_plate")
        self.assertEqual(snap.transform.scale, 12.0)


class QrSessionTest(unittest.TestCase):
    def test_settles_then_snaps_once(self):
        s = QrSession(settle_count=3, rescan_after=1.0)
        text = AnchorPayload("bench", 80).encode()
        corners = square((0.2, 0.9, -0.5), 0.08)
        self.assertIsNone(s.detect(text, corners, time=0.0))
        self.assertIsNone(s.detect(text, corners, time=0.03))
        snap = s.detect(text, corners, time=0.06)
        self.assertIsNotNone(snap)
        self.assertEqual([round(c, 6) for c in snap.transform.translation], [0.2, 0.9, -0.5])
        kinds = [e.kind for e in s.drain_events()]
        self.assertEqual(kinds, ["seen", "seen", "snap"])
        for t in (0.1, 0.13, 0.16, 0.19):
            self.assertIsNone(s.detect(text, corners, time=t), "no re-snap within the rescan window")
        later = [s.detect(text, corners, time=t) for t in (1.2, 1.23, 1.26)]
        self.assertEqual(sum(1 for x in later if x is not None), 1, "exactly one re-snap after the window")

    def test_rejections(self):
        s = QrSession(settle_count=1, max_residual=0.001)
        self.assertIsNone(s.detect("https://nope", square((0, 0, 0), 0.08)))
        self.assertIsNone(s.detect(AnchorPayload("a", 80).encode(), square((0, 0, 0), 0.08, noise=0.01)))
        self.assertIsNone(s.detect(AnchorPayload("a", 80).encode(), square((0, 0, 0), 0.10)))  # 25 % off
        kinds = [e.kind for e in s.drain_events()]
        self.assertEqual(kinds, ["ignored", "rejected", "rejected"])
        self.assertIsNotNone(s.detect(AnchorPayload("a", 80).encode(), square((0, 0, 0), 0.08)))
        s.forget("a")
        self.assertEqual(s.snapped, {})


if __name__ == "__main__":
    unittest.main()
