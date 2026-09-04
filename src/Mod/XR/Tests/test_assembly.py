# SPDX-License-Identifier: LGPL-2.1-or-later
"""Hand-placed mates: features, the solver, candidates, the session."""

import math
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrassembly import (AssemblySession, AxisFeature, DetectParams, Features, Mate, PlaneFeature,  # noqa: E402
                        PointFeature, candidates, compatible, from_mesh, joint_type_for, residual_of,
                        rotation_about, rotation_between, solve)
from xrfit import box_mesh, cylinder_mesh  # noqa: E402
from xrsketch import vecmath as vm  # noqa: E402


def peg_features():
    return Features([AxisFeature("shaft", (0, 0, 0.005), (0, 0, 1), 0.002, 0.010),
                     PlaneFeature("bottom", (0, 0, 0), (0, 0, -1), extent=0.002),
                     PointFeature("tip", (0, 0, 0))])


def block_features():
    return Features([AxisFeature("bore", (0.01, 0.01, 0.0025), (0, 0, 1), 0.00205, 0.005),
                     PlaneFeature("top", (0.01, 0.01, 0.005), (0, 0, 1), extent=0.02),
                     PlaneFeature("side", (0.03, 0.01, 0.0025), (1, 0, 0), extent=0.005),
                     PointFeature("corner", (0.01, 0.01, 0.005))])


class RotationTest(unittest.TestCase):
    def test_rotation_between(self):
        q = rotation_between((1, 0, 0), (0, 1, 0))
        r = vm.Transform((0, 0, 0), q).apply_vector((1, 0, 0))
        self.assertAlmostEqual(r[1], 1.0)
        q = rotation_between((0, 0, 1), (0, 0, -1))
        r = vm.Transform((0, 0, 0), q).apply_vector((0, 0, 1))
        self.assertAlmostEqual(r[2], -1.0)
        self.assertEqual(rotation_between((0, 0, 1), (0, 0, 1)), vm.IDENTITY_QUAT)

    def test_rotation_about(self):
        r = vm.Transform((0, 0, 0), rotation_about((0, 0, 1), math.pi / 2)).apply_vector((1, 0, 0))
        self.assertAlmostEqual(r[1], 1.0)


class SolverTest(unittest.TestCase):
    def world(self):
        return {"block": block_features()}

    def test_concentric_keeps_depth_and_spin(self):
        pose = vm.Transform((0.011, 0.009, 0.008), rotation_about((1, 0, 0), 0.3))
        r = solve(pose, [Mate("concentric", "peg", "shaft", "block", "bore")], peg_features(), self.world())
        self.assertTrue(r.ok, r.notes)
        t = r.pose.translation
        self.assertAlmostEqual(t[0], 0.01)
        self.assertAlmostEqual(t[1], 0.01)
        before = pose.apply((0, 0, 0.005))[2]
        after = r.pose.apply((0, 0, 0.005))[2]
        self.assertAlmostEqual(after, before, msg="the shaft keeps the depth the hand gave it")

    def test_concentric_then_coincident(self):
        pose = vm.Transform((0.011, 0.009, 0.008), rotation_about((1, 0, 0), 0.3))
        r = solve(pose, [Mate("concentric", "peg", "shaft", "block", "bore"),
                         Mate("coincident", "peg", "bottom", "block", "top")], peg_features(), self.world())
        self.assertTrue(r.ok)
        self.assertEqual([round(x, 6) for x in r.pose.translation], [0.01, 0.01, 0.005])

    def test_coincident_with_offset_then_concentric(self):
        pose = vm.Transform((0.011, 0.009, 0.008), rotation_about((0, 1, 0), 0.2))
        r = solve(pose, [Mate("distance", "peg", "bottom", "block", "top", offset=0.001),
                         Mate("concentric", "peg", "shaft", "block", "bore")], peg_features(), self.world())
        self.assertTrue(r.ok, r.notes)
        self.assertEqual([round(x, 6) for x in r.pose.translation], [0.01, 0.01, 0.006])

    def test_flush_and_parallel(self):
        pose = vm.Transform((0, 0, 0.02), rotation_about((1, 0, 0), 0.5))
        r = solve(pose, [Mate("coincident", "peg", "bottom", "block", "top", flush=True)], peg_features(), self.world())
        n = peg_features().get("bottom").transformed(r.pose).normal
        self.assertAlmostEqual(n[2], 1.0)
        r = solve(pose, [Mate("parallel", "peg", "bottom", "block", "top")], peg_features(), self.world())
        self.assertAlmostEqual(r.pose.translation[2], 0.02, msg="parallel leaves position alone")

    def test_angle(self):
        pose = vm.Transform((0, 0, 0.02))
        r = solve(pose, [Mate("angle", "peg", "bottom", "block", "top", angle_deg=90)], peg_features(), self.world())
        n = peg_features().get("bottom").transformed(r.pose).normal
        self.assertAlmostEqual(vm.dot(n, (0, 0, 1)), 0.0, places=6)
        self.assertTrue(r.ok)

    def test_point_mate_within_plane(self):
        pose = vm.Transform((0.03, 0.03, 0.02))
        r = solve(pose, [Mate("coincident", "peg", "bottom", "block", "top"),
                         Mate("point", "peg", "tip", "block", "corner")], peg_features(), self.world())
        self.assertEqual([round(x, 6) for x in r.pose.translation], [0.01, 0.01, 0.005])

    def test_conflicting_planes_are_reported(self):
        # Two coincident mates to perpendicular faces both wanting the same point: second can only slide.
        world = {"block": block_features()}
        pose = vm.Transform((0.05, 0.01, 0.05))
        r = solve(pose, [Mate("coincident", "peg", "bottom", "block", "top"),
                         Mate("coincident", "peg", "bottom", "block", "side")], peg_features(), world)
        self.assertFalse(r.ok)
        self.assertTrue(any("conflicts" in n or "not reachable" in n for n in r.notes) or r.residual > 0)

    def test_missing_feature_and_kind_mismatch(self):
        r = solve(vm.Transform(), [Mate("coincident", "peg", "nope", "block", "top"),
                                   Mate("concentric", "peg", "bottom", "block", "bore")], peg_features(), self.world())
        self.assertEqual(len(r.satisfied), 0)
        self.assertEqual(len(r.notes), 2)
        with self.assertRaises(ValueError):
            Mate("weld", "a", "b", "c", "d")

    def test_residual_of(self):
        pose = vm.Transform((0.02, 0.01, 0.005))
        res = residual_of(pose, [Mate("concentric", "peg", "shaft", "block", "bore")], peg_features(), self.world())
        self.assertAlmostEqual(res, 0.01)


class FeaturesTest(unittest.TestCase):
    def test_from_mesh_box(self):
        f = from_mesh(box_mesh((0.02, 0.02, 0.02)))
        planes = f.of_kind("plane")
        self.assertEqual(len(planes), 6)
        normals = sorted(tuple(round(c) for c in p.normal) for p in planes)
        self.assertIn((0, 0, 1), normals)
        top = next(p for p in planes if round(p.normal[2]) == 1)
        self.assertAlmostEqual(top.origin[2], 0.01)
        self.assertAlmostEqual(top.extent, math.sqrt(0.0004 / math.pi))

    def test_from_mesh_cylinder(self):
        f = from_mesh(cylinder_mesh(0.005, 0.02, sides=48))
        axes = f.of_kind("axis")
        self.assertEqual(len(axes), 1, [x.name for x in f])
        self.assertAlmostEqual(abs(axes[0].direction[2]), 1.0, places=3)
        self.assertAlmostEqual(axes[0].radius, 0.005, delta=0.0002)
        self.assertAlmostEqual(axes[0].extent, 0.02, delta=0.001)
        self.assertEqual(len(f.of_kind("plane")), 2)

    def test_transform_and_dict(self):
        f = peg_features().world(vm.Transform((1, 0, 0)))
        self.assertEqual(f.get("shaft").origin, (1.0, 0.0, 0.005))
        again = Features.from_dict(f.to_dict())
        self.assertEqual(len(again), 3)
        self.assertEqual(again.get("tip").kind, "point")


class DetectTest(unittest.TestCase):
    def test_candidates_near_the_bore(self):
        pose = vm.Transform((0.0105, 0.0095, 0.008), rotation_about((1, 0, 0), 0.1))
        world = peg_features().world(pose)
        found = candidates("peg", world, {"block": block_features()})
        self.assertTrue(found)
        self.assertEqual(found[0].mate.kind, "concentric")
        self.assertIn("r 0.002 in r 0.00205", found[0].note)

    def test_nothing_far_away(self):
        world = peg_features().world(vm.Transform((0.2, 0.2, 0.2)))
        self.assertEqual(candidates("peg", world, {"block": block_features()}), [])

    def test_radius_mismatch_and_angle(self):
        big = Features([AxisFeature("shaft", (0, 0, 0), (0, 0, 1), 0.02, 0.01)])
        self.assertEqual(candidates("peg", big.world(vm.Transform((0.01, 0.01, 0.0))), {"block": block_features()}), [])
        tilted = peg_features().world(vm.Transform((0.01, 0.01, 0.008), rotation_about((1, 0, 0), 0.6)))
        self.assertFalse(any(c.mate.kind == "concentric" for c in candidates("peg", tilted, {"block": block_features()})))

    def test_existing_and_compatible(self):
        pose = vm.Transform((0.01, 0.01, 0.0055))
        world = peg_features().world(pose)
        existing = [Mate("concentric", "peg", "shaft", "block", "bore")]
        found = candidates("peg", world, {"block": block_features()}, existing=existing)
        kinds = [c.mate.kind for c in found]
        self.assertNotIn("concentric", kinds)
        self.assertIn("coincident", kinds)
        self.assertTrue(compatible(found[0], existing))
        self.assertFalse(compatible(found[0], existing + [Mate("fixed", "peg", "", "", "")]))


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.s = AssemblySession(DetectParams())
        self.s.add_part("block", block_features(), fixed=True)
        self.s.add_part("peg", peg_features(), pose=vm.Transform((0.05, 0.05, 0.05)))

    def test_grab_snap_confirm_release(self):
        s = self.s
        hand = vm.Transform((0.05, 0.05, 0.05))
        s.grab("peg", hand)
        s.update(0.016, vm.Transform((0.0105, 0.0095, 0.008)), grip=1.0)
        kinds = [e.kind for e in s.events]
        self.assertIn("snap", kinds)
        self.assertEqual(s.preview.mate.kind, "concentric")
        t = s.parts["peg"].pose.translation
        self.assertAlmostEqual(t[0], 0.01, msg="previewed snapped")
        s.update(0.016, vm.Transform((0.0105, 0.0095, 0.008)), grip=1.0, trigger=True)
        self.assertEqual([m.kind for m in s.parts["peg"].mates], ["concentric"])
        self.assertIn("constraint", [e.kind for e in s.events])
        # Now pull along the bore: it follows in z only.
        s.snap_enabled = False
        s.update(0.016, vm.Transform((0.02, 0.0, 0.0055)), grip=1.0)
        t = s.parts["peg"].pose.translation
        self.assertAlmostEqual(t[0], 0.01)
        self.assertAlmostEqual(t[1], 0.01)
        self.assertAlmostEqual(t[2], 0.0055)
        s.snap_enabled = True
        s.update(0.016, vm.Transform((0.02, 0.0, 0.0055)), grip=1.0)
        self.assertEqual(s.preview.mate.kind, "coincident", "the shoulder is offered next")
        self.assertAlmostEqual(s.parts["peg"].pose.translation[2], 0.005, msg="previewed on the shoulder")
        s.confirm()
        self.assertAlmostEqual(s.parts["peg"].pose.translation[2], 0.005)
        s.update(0.016, vm.Transform((0.02, 0.0, 0.03)), grip=0.1)
        self.assertIsNone(s.grabbed)
        self.assertIn("release", [e.kind for e in s.events])
        self.assertAlmostEqual(s.parts["peg"].pose.translation[2], 0.005, msg="mated part stays put")
        self.assertEqual(max(s.residuals().values()), 0.0)

    def test_unconfirmed_preview_does_not_stick(self):
        s = self.s
        s.grab("peg", vm.Transform((0.05, 0.05, 0.05)))
        s.update(0.016, vm.Transform((0.0105, 0.0095, 0.008)), grip=1.0)
        self.assertIsNotNone(s.preview)
        s.release()
        t = s.parts["peg"].pose.translation
        self.assertAlmostEqual(t[0], 0.0105, msg="back to the free pose")
        self.assertIn("unsnap", [e.kind for e in s.events])

    def test_unsnap_when_moving_away(self):
        s = self.s
        s.grab("peg", vm.Transform((0.05, 0.05, 0.05)))
        s.update(0.016, vm.Transform((0.0105, 0.0095, 0.008)), grip=1.0)
        s.update(0.016, vm.Transform((0.2, 0.2, 0.2)), grip=1.0)
        self.assertIsNone(s.preview)
        self.assertEqual([e.kind for e in s.events][-1], "unsnap")

    def test_unconstrain_and_fixed(self):
        s = self.s
        s.add_mate(Mate("concentric", "peg", "shaft", "block", "bore"))
        self.assertAlmostEqual(s.parts["peg"].pose.translation[0], 0.01)
        self.assertEqual(s.unconstrain("peg").kind, "concentric")
        self.assertIsNone(s.grab("block", vm.Transform()), "fixed parts cannot be grabbed")
        self.assertEqual(len(s.to_dict()["parts"]), 2)


class JointMappingTest(unittest.TestCase):
    def test_mapping(self):
        c = Mate("concentric", "peg", "shaft", "block", "bore")
        p = Mate("coincident", "peg", "bottom", "block", "top")
        self.assertEqual([j[0] for j in joint_type_for([c, p])], ["Revolute"])
        self.assertEqual([j[0] for j in joint_type_for([c])], ["Cylindrical"])
        self.assertEqual([j[0] for j in joint_type_for([p])], ["Distance"])
        self.assertEqual([j[0] for j in joint_type_for([Mate("parallel", "a", "b", "c", "d"), Mate("point", "a", "b", "c", "d")])],
                         ["Parallel", "Ball"])
        self.assertEqual(joint_type_for([Mate("fixed", "a", "", "", "")]), [])


if __name__ == "__main__":
    unittest.main()
