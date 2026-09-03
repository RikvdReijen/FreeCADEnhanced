# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Two-handed manipulation: the gesture maths and its transitions.

Runs under plain ``python3 -m unittest`` with neither FreeCAD nor numpy.
``xrsketch`` never imports numpy at all, which is what makes the "with numpy"
and "without numpy" results identical rather than merely close; the last test
case here pins that down.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrenv.scale import ScaleController                        # noqa: E402
from xrsketch import vecmath as vm                             # noqa: E402
from xrsketch.bimanual import (BimanualController, GrabParams,  # noqa: E402
                               HandPose, WorldGrab, view_to_env)
from xrsketch.vecmath import Transform                         # noqa: E402

LEFT = 0
RIGHT = 1


def exact_params(**kw):
    """Parameters with the tremor filtering switched off."""
    base = dict(dead_zone_translation=0.0, dead_zone_rotation=0.0,
                dead_zone_scale=0.0, damping=0.0)
    base.update(kw)
    return GrabParams(**base)


def rot(axis, angle):
    return vm.quat_from_axis_angle(axis, angle)


class TestSingleHand(unittest.TestCase):

    def test_one_hand_translates_rigidly(self):
        c = BimanualController(exact_params())
        c.grab(LEFT, (0.0, 1.0, 0.0))
        c.move(LEFT, (0.3, 1.2, -0.1))
        t = c.update()
        self.assertAlmostEqual(t.scale, 1.0, places=12)
        self.assertAlmostEqual(vm.dist(t.translation, (0.3, 0.2, -0.1)), 0.0,
                               places=12)

    def test_one_hand_rotation_pivots_on_the_hand(self):
        c = BimanualController(exact_params())
        q = rot((0.0, 1.0, 0.0), math.pi / 2.0)
        c.grab(LEFT, (0.5, 0.0, 0.0))
        c.move(LEFT, (0.5, 0.0, 0.0), q)
        t = c.update()
        # the grab point does not move
        self.assertAlmostEqual(vm.dist(t.apply((0.5, 0.0, 0.0)),
                                       (0.5, 0.0, 0.0)), 0.0, places=12)
        axis, angle = vm.quat_to_axis_angle(t.rotation)
        self.assertAlmostEqual(angle, math.pi / 2.0, places=12)

    def test_no_hands_is_the_identity(self):
        c = BimanualController(exact_params())
        c.move(LEFT, (1.0, 2.0, 3.0))
        t = c.update(0.1)
        self.assertTrue(t.almost_equal(Transform()))


class TestTwoHands(unittest.TestCase):

    def _grab_pair(self, params=None, left=(-0.2, 0.0, 0.0),
                   right=(0.2, 0.0, 0.0)):
        c = BimanualController(params or exact_params())
        c.grab(LEFT, left)
        c.grab(RIGHT, right)
        return c

    def test_pure_translation_gives_no_scale_and_no_rotation(self):
        c = self._grab_pair()
        delta = (0.4, 0.5, -0.3)
        c.move(LEFT, vm.add((-0.2, 0.0, 0.0), delta))
        c.move(RIGHT, vm.add((0.2, 0.0, 0.0), delta))
        t = c.update()
        self.assertAlmostEqual(t.scale, 1.0, places=12)
        self.assertAlmostEqual(vm.dist(t.translation, delta), 0.0, places=12)
        axis, angle = vm.quat_to_axis_angle(t.rotation)
        self.assertAlmostEqual(angle, 0.0, places=12)

    def test_separation_scales_by_exactly_the_distance_ratio(self):
        for ratio in (0.25, 0.5, 2.0, 3.7):
            c = self._grab_pair()
            c.move(LEFT, (-0.2 * ratio, 0.0, 0.0))
            c.move(RIGHT, (0.2 * ratio, 0.0, 0.0))
            t = c.update()
            self.assertAlmostEqual(t.scale, ratio, places=12)
            # scaling about the midpoint leaves the midpoint alone
            self.assertAlmostEqual(vm.dist(t.apply((0.0, 0.0, 0.0)),
                                           (0.0, 0.0, 0.0)), 0.0, places=12)

    def test_the_grab_points_follow_the_hands(self):
        """Whatever was under a hand when it grabbed stays under it."""
        c = self._grab_pair(left=(0.0, 0.0, 0.0), right=(1.0, 0.0, 0.0))
        c.move(LEFT, (0.0, 0.0, 0.0))
        c.move(RIGHT, (2.0, 0.0, 0.0))
        t = c.update()
        self.assertAlmostEqual(t.scale, 2.0, places=12)
        self.assertAlmostEqual(vm.dist(t.apply((0.0, 0.0, 0.0)),
                                       (0.0, 0.0, 0.0)), 0.0, places=12)
        self.assertAlmostEqual(vm.dist(t.apply((1.0, 0.0, 0.0)),
                                       (2.0, 0.0, 0.0)), 0.0, places=12)
        # the midpoint of the hands is carried with them, not pinned
        self.assertAlmostEqual(t.apply((0.5, 0.0, 0.0))[0], 1.0, places=12)

    def test_rotation_about_the_midpoint(self):
        for angle in (math.pi / 6.0, math.pi / 2.0, -math.pi / 3.0):
            for axis in ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)):
                c = self._grab_pair()
                q = rot(axis, angle)
                c.move(LEFT, vm.quat_rotate(q, (-0.2, 0.0, 0.0)), q)
                c.move(RIGHT, vm.quat_rotate(q, (0.2, 0.0, 0.0)), q)
                t = c.update()
                self.assertAlmostEqual(t.scale, 1.0, places=12)
                got_axis, got_angle = vm.quat_to_axis_angle(t.rotation)
                if angle < 0.0:
                    got_axis = vm.neg(got_axis)
                self.assertAlmostEqual(got_angle, abs(angle), places=10)
                self.assertAlmostEqual(vm.dist(got_axis, vm.normalize(axis)),
                                       0.0, places=9)
                # a probe point is carried by exactly that rotation
                probe = (0.1, 0.2, 0.3)
                self.assertAlmostEqual(
                    vm.dist(t.apply(probe), vm.quat_rotate(q, probe)), 0.0,
                    places=10)

    def test_roll_about_the_hand_axis_is_captured(self):
        c = self._grab_pair()
        q = rot((1.0, 0.0, 0.0), math.pi / 3.0)
        c.move(LEFT, (-0.2, 0.0, 0.0), q)
        c.move(RIGHT, (0.2, 0.0, 0.0), q)
        t = c.update()
        axis, angle = vm.quat_to_axis_angle(t.rotation)
        self.assertAlmostEqual(angle, math.pi / 3.0, places=10)
        self.assertAlmostEqual(abs(vm.dot(axis, (1.0, 0.0, 0.0))), 1.0,
                               places=9)

    def test_combined_move_scale_and_rotate(self):
        c = self._grab_pair()
        q = rot((0.0, 1.0, 0.0), math.pi / 4.0)
        shift = (1.0, 0.5, -2.0)
        for hand, p in ((LEFT, (-0.2, 0.0, 0.0)), (RIGHT, (0.2, 0.0, 0.0))):
            c.move(hand, vm.add(vm.mul(vm.quat_rotate(q, p), 3.0), shift), q)
        t = c.update()
        self.assertAlmostEqual(t.scale, 3.0, places=12)
        probe = (0.4, -0.1, 0.2)
        expect = vm.add(vm.mul(vm.quat_rotate(q, probe), 3.0), shift)
        self.assertAlmostEqual(vm.dist(t.apply(probe), expect), 0.0,
                               places=10)


class TestTransitions(unittest.TestCase):
    """The second hand joining or leaving must not move anything."""

    def test_second_hand_joining_is_continuous(self):
        c = BimanualController(exact_params())
        probe = (0.3, 0.4, -0.2)
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (0.1, 0.0, 0.0))
        before = c.update().apply(probe)
        c.grab(RIGHT, (0.5, 0.0, 0.0))
        after = c.update().apply(probe)
        self.assertAlmostEqual(vm.dist(before, after), 0.0, places=12)
        # and the two-handed gesture then works from that new baseline
        c.move(LEFT, (0.1, 0.0, 0.0))
        c.move(RIGHT, (0.9, 0.0, 0.0))
        t = c.update()
        self.assertAlmostEqual(t.scale, 2.0, places=12)

    def test_second_hand_leaving_is_continuous(self):
        c = BimanualController(exact_params())
        probe = (-0.2, 0.7, 0.1)
        c.grab(LEFT, (-0.2, 0.0, 0.0))
        c.grab(RIGHT, (0.2, 0.0, 0.0))
        c.move(LEFT, (-0.4, 0.1, 0.0))
        c.move(RIGHT, (0.4, 0.1, 0.0))
        before = c.update().apply(probe)
        c.release(RIGHT)
        after = c.update().apply(probe)
        self.assertAlmostEqual(vm.dist(before, after), 0.0, places=12)
        # the remaining hand keeps the accumulated scale and translates
        c.move(LEFT, (-0.3, 0.1, 0.0))
        t = c.update()
        self.assertAlmostEqual(t.scale, 2.0, places=12)
        self.assertAlmostEqual(vm.dist(t.apply(probe),
                                       vm.add(after, (0.1, 0.0, 0.0))), 0.0,
                               places=12)

    def test_no_jump_across_a_whole_replayed_gesture(self):
        """Frame to frame the probe never moves more than the hands do."""
        c = BimanualController(exact_params())
        probe = (0.05, 0.05, 0.05)
        c.grab(LEFT, (-0.1, 0.0, 0.0))
        previous = c.update().apply(probe)
        step = 0.01
        for i in range(1, 60):
            left = (-0.1 - i * step * 0.5, i * step * 0.2, 0.0)
            right = (0.1 + i * step * 0.5, i * step * 0.2, 0.0)
            c.move(LEFT, left)
            if i == 10:
                c.grab(RIGHT, right)
            if i > 10:
                c.move(RIGHT, right)
            if i == 40:
                c.release(RIGHT)
            current = c.update().apply(probe)
            self.assertLess(vm.dist(previous, current), 0.05,
                            "discontinuity at frame %d" % i)
            previous = current

    def test_release_all_keeps_the_transform(self):
        c = BimanualController(exact_params())
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (1.0, 0.0, 0.0))
        t = c.update()
        c.release_all()
        self.assertTrue(c.update().almost_equal(t))
        self.assertFalse(c.active)


class TestDeadZoneAndDamping(unittest.TestCase):

    def test_dead_zone_suppresses_tremor(self):
        params = GrabParams(dead_zone_translation=0.003,
                            dead_zone_rotation=math.radians(1.0),
                            dead_zone_scale=0.01, damping=0.0)
        c = BimanualController(params)
        c.grab(LEFT, (-0.2, 0.0, 0.0))
        c.grab(RIGHT, (0.2, 0.0, 0.0))
        tremor = [(0.0005, -0.0004, 0.0002), (-0.0006, 0.0009, -0.0003),
                  (0.0011, 0.0002, 0.0007)]
        for shake in tremor:
            c.move(LEFT, vm.add((-0.2, 0.0, 0.0), shake))
            c.move(RIGHT, vm.add((0.2, 0.0, 0.0), vm.neg(shake)))
            t = c.update(1.0 / 72.0)
            self.assertAlmostEqual(vm.length(t.translation), 0.0, places=12)
            self.assertAlmostEqual(t.scale, 1.0, places=12)

    def test_dead_zone_is_soft_and_does_not_creep(self):
        params = GrabParams(dead_zone_translation=0.01, damping=0.0)
        c = BimanualController(params)
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (0.1, 0.0, 0.0))
        moved = c.update().translation
        # soft: the dead zone is subtracted, not snapped away
        self.assertAlmostEqual(moved[0], 0.09, places=12)
        # and going back to the start returns exactly to the start
        c.move(LEFT, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(vm.length(c.update().translation), 0.0,
                               places=12)

    def test_damping_converges_without_overshoot(self):
        params = GrabParams(dead_zone_translation=0.0, damping=0.08)
        c = BimanualController(params)
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (1.0, 0.0, 0.0))
        previous = 0.0
        for _ in range(200):
            x = c.update(1.0 / 90.0).translation[0]
            self.assertGreaterEqual(x + 1e-12, previous)
            self.assertLessEqual(x, 1.0 + 1e-12)
            previous = x
        self.assertAlmostEqual(previous, 1.0, places=6)

    def test_settle_skips_the_damping(self):
        c = BimanualController(GrabParams(dead_zone_translation=0.0,
                                          damping=0.5))
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (2.0, 0.0, 0.0))
        c.update(1.0 / 90.0)
        self.assertLess(c.transform.translation[0], 2.0)
        self.assertAlmostEqual(c.settle().translation[0], 2.0, places=12)


class TestLocksAndClamps(unittest.TestCase):

    def test_translation_lock(self):
        c = BimanualController(exact_params(allow_translate=False))
        c.grab(LEFT, (-0.2, 0.0, 0.0))
        c.grab(RIGHT, (0.2, 0.0, 0.0))
        c.move(LEFT, (-0.4, 1.0, 0.0))
        c.move(RIGHT, (0.4, 1.0, 0.0))
        t = c.update()
        self.assertAlmostEqual(t.scale, 2.0, places=12)
        # the original midpoint is still fixed
        self.assertAlmostEqual(vm.length(t.apply((0.0, 0.0, 0.0))), 0.0,
                               places=12)

    def test_rotation_lock(self):
        c = BimanualController(exact_params(allow_rotate=False))
        q = rot((0.0, 1.0, 0.0), 1.0)
        c.grab(LEFT, (-0.2, 0.0, 0.0))
        c.grab(RIGHT, (0.2, 0.0, 0.0))
        c.move(LEFT, vm.quat_rotate(q, (-0.2, 0.0, 0.0)), q)
        c.move(RIGHT, vm.quat_rotate(q, (0.2, 0.0, 0.0)), q)
        t = c.update()
        self.assertTrue(t.almost_equal(Transform()))

    def test_scale_lock(self):
        c = BimanualController(exact_params(allow_scale=False))
        c.grab(LEFT, (-0.2, 0.0, 0.0))
        c.grab(RIGHT, (0.2, 0.0, 0.0))
        c.move(LEFT, (-1.0, 0.0, 0.0))
        c.move(RIGHT, (1.0, 0.0, 0.0))
        self.assertAlmostEqual(c.update().scale, 1.0, places=12)

    def test_scale_clamps_hold(self):
        c = BimanualController(exact_params(min_scale=0.5, max_scale=2.0))
        c.grab(LEFT, (-0.1, 0.0, 0.0))
        c.grab(RIGHT, (0.1, 0.0, 0.0))
        c.move(LEFT, (-1.0, 0.0, 0.0))
        c.move(RIGHT, (1.0, 0.0, 0.0))
        self.assertAlmostEqual(c.update().scale, 2.0, places=12)
        c.move(LEFT, (-0.001, 0.0, 0.0))
        c.move(RIGHT, (0.001, 0.0, 0.0))
        self.assertAlmostEqual(c.update().scale, 0.5, places=12)

    def test_clamp_accumulates_across_gestures(self):
        c = BimanualController(exact_params(max_scale=4.0))
        for _ in range(4):
            c.grab(LEFT, (-0.1, 0.0, 0.0))
            c.grab(RIGHT, (0.1, 0.0, 0.0))
            c.move(LEFT, (-0.2, 0.0, 0.0))
            c.move(RIGHT, (0.2, 0.0, 0.0))
            c.update()
            c.release_all()
        self.assertAlmostEqual(c.transform.scale, 4.0, places=12)

    def test_bad_clamp_range_is_refused(self):
        self.assertRaises(ValueError, GrabParams, min_scale=2.0, max_scale=1.0)
        self.assertRaises(ValueError, GrabParams, min_scale=0.0)


class TestTargetBinding(unittest.TestCase):

    class _Thing(object):
        def __init__(self):
            self.transform = Transform()

    def test_transform_is_written_back_to_the_target(self):
        thing = self._Thing()
        thing.transform = Transform((1.0, 0.0, 0.0))
        c = BimanualController(exact_params(), target=thing)
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.move(LEFT, (0.0, 0.5, 0.0))
        c.update()
        self.assertAlmostEqual(vm.dist(thing.transform.translation,
                                       (1.0, 0.5, 0.0)), 0.0, places=12)

    def test_coincident_hands_do_not_explode(self):
        c = BimanualController(exact_params())
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.grab(RIGHT, (0.0, 0.0, 0.0))
        c.move(RIGHT, (0.5, 0.0, 0.0))
        t = c.update()
        self.assertTrue(all(math.isfinite(v) for v in t.translation))
        self.assertTrue(math.isfinite(t.scale))
        self.assertGreater(t.scale, 0.0)


class TestWorldGrab(unittest.TestCase):
    """Grabbing the world hands the scale to xrenv.scale, not to a copy."""

    def _controller(self):
        ctl = ScaleController(scale=1.0, unit_scale=1.0, duration=0.0)
        return ctl

    def test_pulling_the_hands_apart_shrinks_the_user(self):
        ctl = self._controller()
        grab = WorldGrab(ctl, exact_params())
        grab.grab(0, (-0.2, 1.0, 0.0))
        grab.grab(1, (0.2, 1.0, 0.0))
        grab.move(0, (-0.4, 1.0, 0.0))
        grab.move(1, (0.4, 1.0, 0.0))
        scale, rigid = grab.update(1.0 / 90.0)
        # the world got twice as big, so the user is twice as small
        self.assertAlmostEqual(scale, 2.0, places=9)
        self.assertAlmostEqual(rigid.scale, 1.0, places=12)

    def test_the_grab_midpoint_stays_put_in_view_space(self):
        ctl = self._controller()
        grab = WorldGrab(ctl, exact_params())
        grab.grab(0, (-0.2, 1.0, 0.0))
        grab.grab(1, (0.2, 1.0, 0.0))
        pivot_env = view_to_env(ctl, (0.0, 1.0, 0.0))
        grab.move(0, (-0.6, 1.0, 0.0))
        grab.move(1, (0.6, 1.0, 0.0))
        grab.update(1.0 / 90.0)
        s = ctl.world_scale * ctl.unit_scale
        o = ctl.world_offset
        view = tuple(pivot_env[i] * s + o[i] for i in range(3))
        self.assertAlmostEqual(vm.dist(view, (0.0, 1.0, 0.0)), 0.0, places=9)

    def test_translation_is_left_to_the_viewer_as_a_rigid_transform(self):
        ctl = self._controller()
        grab = WorldGrab(ctl, exact_params())
        grab.grab(0, (0.0, 1.0, 0.0))
        grab.move(0, (0.3, 1.0, 0.0))
        scale, rigid = grab.update(1.0 / 90.0)
        self.assertAlmostEqual(scale, 1.0, places=12)
        self.assertAlmostEqual(vm.dist(rigid.translation, (0.3, 0.0, 0.0)),
                               0.0, places=12)


class TestNumpyIndependence(unittest.TestCase):
    """The package must behave the same with and without numpy."""

    def test_xrsketch_never_imports_numpy(self):
        import xrsketch
        root = os.path.dirname(os.path.abspath(xrsketch.__file__))
        offenders = []
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name)) as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith(("import numpy", "from numpy")):
                        offenders.append(name)
                        break
        self.assertEqual(offenders, [])

    def test_results_are_identical_with_numpy_hidden(self):
        class _Block(object):
            def find_module(self, name, path=None):
                return self if name.split(".")[0] == "numpy" else None

            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "numpy":
                    raise ImportError("numpy is hidden for this test")
                return None

            def load_module(self, name):
                raise ImportError("numpy is hidden for this test")

        def sample():
            c = BimanualController(exact_params())
            q = rot((0.3, 1.0, -0.2), 0.77)
            c.grab(LEFT, (-0.2, 0.1, 0.0))
            c.grab(RIGHT, (0.2, -0.1, 0.05))
            c.move(LEFT, vm.add(vm.quat_rotate(q, (-0.3, 0.15, 0.0)),
                                (0.1, 0.2, 0.3)), q)
            c.move(RIGHT, vm.add(vm.quat_rotate(q, (0.3, -0.15, 0.075)),
                                 (0.1, 0.2, 0.3)), q)
            t = c.update()
            return (t.translation, t.rotation, t.scale,
                    t.apply((0.11, -0.22, 0.33)))

        reference = sample()
        blocker = _Block()
        sys.meta_path.insert(0, blocker)
        try:
            self.assertEqual(sample(), reference)
        finally:
            sys.meta_path.remove(blocker)


class TestHandPose(unittest.TestCase):

    def test_pose_normalises_its_quaternion(self):
        pose = HandPose((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 4.0))
        self.assertAlmostEqual(pose.rotation[3], 1.0, places=12)
        self.assertEqual(pose.position, (1.0, 2.0, 3.0))

    def test_set_poses_accepts_several_shapes(self):
        c = BimanualController(exact_params())
        c.grab(LEFT, (0.0, 0.0, 0.0))
        c.set_poses({LEFT: (0.5, 0.0, 0.0)})
        self.assertAlmostEqual(c.update().translation[0], 0.5, places=12)
        c.set_poses({LEFT: HandPose((1.0, 0.0, 0.0))})
        self.assertAlmostEqual(c.update().translation[0], 1.0, places=12)
        c.set_poses({LEFT: ((1.5, 0.0, 0.0), vm.IDENTITY_QUAT)})
        self.assertAlmostEqual(c.update().translation[0], 1.5, places=12)


if __name__ == "__main__":
    unittest.main()
