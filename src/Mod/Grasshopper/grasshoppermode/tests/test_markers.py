# SPDX-License-Identifier: LGPL-2.1-or-later
import math
import unittest

from grasshoppermode import markers
from grasshoppermode.geometry import Vec3
from grasshoppermode.markers import CanvasFrame, ContactDetector, MarkerSpec


class TestMarkerSpec(unittest.TestCase):
    def test_payload_roundtrip(self):
        spec = MarkerSpec("abc123", 90, "http://192.168.1.5:8765", 0.4, (10, 20), marker_index=2)
        text = spec.payload()
        self.assertTrue(text.startswith("http://192.168.1.5:8765/xr#c=abc123"))
        back = MarkerSpec.parse(text)
        self.assertEqual(back.canvas_id, "abc123")
        self.assertEqual(back.size_mm, 90)
        self.assertEqual(back.scale_mm, 0.4)
        self.assertEqual(back.offset, (10.0, 20.0))
        self.assertEqual(back.marker_index, 2)
        self.assertEqual(MarkerSpec.parse(MarkerSpec("x").payload()).canvas_id, "x")
        with self.assertRaises(ValueError):
            MarkerSpec.parse("hello")
        self.assertIn("payload", spec.to_dict())


class TestCanvasFrame(unittest.TestCase):
    def test_identity_marker(self):
        spec = MarkerSpec("c", 80, scale_mm=1.0)
        frame = CanvasFrame.from_marker(Vec3(1, 0, 0.7), (0, 0, 0, 1), spec, marker_up="z")
        self.assertEqual(frame.origin, Vec3(1, 0, 0.7))
        self.assertEqual(frame.x_axis, Vec3(1, 0, 0))
        self.assertEqual(frame.y_axis, Vec3(0, -1, 0))  # canvas y goes towards the reader
        self.assertEqual(frame.normal, Vec3(0, 0, 1))
        w = frame.to_world(100, 50)
        self.assertAlmostEqual(w.x, 1.1)
        self.assertAlmostEqual(w.y, -0.05)
        cx, cy, h = frame.to_canvas(Vec3(1.1, -0.05, 0.72))
        self.assertAlmostEqual(cx, 100)
        self.assertAlmostEqual(cy, 50)
        self.assertAlmostEqual(h, 0.02)

    def test_webxr_y_up_marker(self):
        # A marker lying flat in WebXR space: y is up, image top points -z.
        spec = MarkerSpec("c", 80, scale_mm=1.0)
        frame = CanvasFrame.from_marker(Vec3(0, 0.75, -0.5), (0, 0, 0, 1), spec, marker_up="y")
        self.assertEqual(frame.normal, Vec3(0, 1, 0))
        self.assertEqual(frame.x_axis, Vec3(1, 0, 0))
        # canvas +y goes towards the reader, i.e. +z in WebXR
        self.assertEqual(frame.y_axis, Vec3(0, 0, 1))

    def test_offset_marker(self):
        spec = MarkerSpec("c", 80, scale_mm=1.0, offset=(100, 0))
        frame = CanvasFrame.from_marker(Vec3(0, 0, 0), (0, 0, 0, 1), spec)
        # marker centre is at canvas x=100 so origin sits 0.1 m to the left
        self.assertAlmostEqual(frame.origin.x, -0.1)
        self.assertAlmostEqual(frame.to_canvas(Vec3(0, 0, 0))[0], 100)

    def test_rotated_marker(self):
        spec = MarkerSpec("c", 80, scale_mm=1.0)
        q = markers.axis_angle_quat(Vec3(0, 0, 1), 90)
        frame = CanvasFrame.from_marker(Vec3(), q, spec)
        self.assertAlmostEqual(frame.x_axis.y, 1.0)
        self.assertAlmostEqual(frame.y_axis.x, 1.0)
        self.assertAlmostEqual(frame.normal.z, 1.0)

    def test_from_points_and_dict(self):
        frame = CanvasFrame.from_points(
            Vec3(0, 0, 0), Vec3(0.2, 0, 0), Vec3(0.1, 0.3, 0.01), scale_mm=2.0
        )
        self.assertEqual(frame.x_axis, Vec3(1, 0, 0))
        self.assertAlmostEqual(frame.y_axis.y, 1.0, places=2)
        self.assertAlmostEqual(abs(frame.normal.z), 1.0, places=2)
        again = CanvasFrame.from_dict(frame.to_dict())
        self.assertEqual(again.origin, frame.origin)
        self.assertEqual(again.scale_mm, 2.0)
        with self.assertRaises(ValueError):
            CanvasFrame.from_points(Vec3(), Vec3(), Vec3(0, 1, 0))
        with self.assertRaises(ValueError):
            CanvasFrame.from_marker(Vec3(), (0, 0, 0, 1), MarkerSpec("c"), marker_up="x")


class TestContactDetector(unittest.TestCase):
    def test_tap(self):
        d = ContactDetector()
        self.assertEqual(d.update(0.05, 10, 10, 0.0), [])
        self.assertEqual(d.update(0.003, 10, 10, 0.1), [("down", 10, 10)])
        self.assertEqual(d.update(0.002, 11, 10, 0.15), [("move", 11, 10)])
        # hysteresis: 8 mm is above down threshold but below up threshold
        self.assertEqual(d.update(0.008, 11, 10, 0.2), [("move", 11, 10)])
        events = d.update(0.03, 11, 10, 0.25)
        self.assertEqual(events, [("up", 11, 10), ("tap", 10, 10)])

    def test_drag_is_not_a_tap_and_hold(self):
        d = ContactDetector()
        d.update(0.0, 0, 0, 0.0)
        d.update(0.0, 40, 0, 0.1)
        events = d.update(0.05, 40, 0, 0.2)
        self.assertEqual(events, [("up", 40, 0)])
        d.update(0.0, 0, 0, 1.0)
        events = d.update(0.0, 1, 1, 1.7)
        self.assertIn(("hold", 1, 1), events)
        self.assertNotIn(("hold", 1, 1), d.update(0.0, 1, 1, 1.8))
        # a long press is not a tap
        self.assertEqual(d.update(0.05, 1, 1, 1.9), [("up", 1, 1)])

    def test_external_pressed_overrides_height(self):
        d = ContactDetector()
        self.assertEqual(d.update(0.2, 0, 0, 0.0, pressed=True), [("down", 0, 0)])
        self.assertEqual(d.update(0.0, 0, 0, 0.1, pressed=False), [("up", 0, 0), ("tap", 0, 0)])


def _hand(flat=True, height=0.0, frame=None, pinch=False):
    """Synthetic right hand lying on the z=0 sheet (world units metres)."""
    frame = frame or CanvasFrame(Vec3(), Vec3(1, 0, 0), Vec3(0, 1, 0))
    j = {}
    j["wrist"] = Vec3(0, 0, height)
    curl = 0.0 if flat else 0.03
    for i, name in enumerate(["index", "middle", "ring", "pinky"]):
        x = 0.02 * (i - 1.5)
        chain = markers.FINGER_CHAINS[name]
        j[chain[0]] = Vec3(x, 0.06, height)
        j[chain[1]] = Vec3(x, 0.10, height)
        j[chain[2]] = Vec3(x, 0.13, height + curl)
        j[chain[3]] = Vec3(x, 0.15 - curl, height + 2 * curl)
    j["thumb-tip"] = (
        Vec3(-0.05, 0.08, height) if not pinch else j["index-finger-tip"] + Vec3(0.005, 0, 0)
    )
    return j


class TestHandScores(unittest.TestCase):
    def test_flat_hand_on_sheet(self):
        frame = CanvasFrame(Vec3(), Vec3(1, 0, 0), Vec3(0, 1, 0))
        self.assertGreater(markers.flat_hand_score(_hand(True, 0.0), frame), 0.9)
        self.assertLess(markers.flat_hand_score(_hand(False, 0.0), frame), 0.5)
        self.assertLess(markers.flat_hand_score(_hand(True, 0.2), frame), 0.05)
        self.assertEqual(markers.flat_hand_score({}, frame), 0.0)

    def test_tilted_hand_scores_low(self):
        frame = CanvasFrame(Vec3(), Vec3(1, 0, 0), Vec3(0, 1, 0))
        joints = _hand(True, 0.0)
        # rotate the hand 90 degrees about x: palm now vertical
        tilted = {k: Vec3(v.x, -v.z, v.y) for k, v in joints.items()}
        self.assertLess(markers.flat_hand_score(tilted, frame), 0.2)

    def test_pinch(self):
        self.assertGreater(markers.pinch_strength(_hand(pinch=True)), 0.9)
        self.assertEqual(markers.pinch_strength(_hand(pinch=False)), 0.0)
        self.assertEqual(markers.pinch_strength({}), 0.0)
        self.assertIsNotNone(markers.palm_center(_hand()))
        self.assertIsNone(markers.palm_center({}))

    def test_finger_extension_range(self):
        j = _hand(True)
        self.assertGreater(markers.finger_extension(j, markers.FINGER_CHAINS["index"]), 0.95)
        self.assertEqual(markers.finger_extension({}, markers.FINGER_CHAINS["index"]), 0.0)
        self.assertTrue(math.isclose(markers.quat_to_axes((0, 0, 0, 2))[0].x, 1.0))


if __name__ == "__main__":
    unittest.main()
