# SPDX-License-Identifier: LGPL-2.1-or-later
"""Physics-based fit checking: collision, clearance, and the stopping hand."""

import math
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrfit import (BVH, FitParams, FitSession, InsertionProbe, TriMesh, box_mesh,  # noqa: E402
                   closest_distance, collide, cylinder_mesh, triangles_intersect)
from xrfit.bvh import AABB, ray_triangle  # noqa: E402
from xrfit.mesh import tube_mesh  # noqa: E402
from xrsketch import vecmath as vm  # noqa: E402


def T(x=0.0, y=0.0, z=0.0, rotation=vm.IDENTITY_QUAT):
    return vm.Transform((x, y, z), rotation)


class MeshTest(unittest.TestCase):
    def test_box_is_closed_and_outward(self):
        box = box_mesh((2.0, 3.0, 4.0))
        self.assertAlmostEqual(box.volume(), 24.0)
        self.assertAlmostEqual(box.area(), 2 * (6 + 8 + 12))
        self.assertEqual(box.bounds, ((-1.0, -1.5, -2.0), (1.0, 1.5, 2.0)))

    def test_cylinder_and_tube(self):
        cyl = cylinder_mesh(0.5, 2.0, sides=64)
        self.assertAlmostEqual(cyl.volume(), math.pi * 0.25 * 2.0, delta=0.01)
        tube = tube_mesh(0.4, 0.6, 1.0, sides=64)
        self.assertAlmostEqual(tube.volume(), math.pi * (0.36 - 0.16), delta=0.01)
        self.assertTrue(BVH(tube).contains_point((0.5, 0.0, 0.0)))
        self.assertFalse(BVH(tube).contains_point((0.0, 0.0, 0.0)), "the hole is not inside")

    def test_from_flat_and_dict(self):
        m = TriMesh.from_flat([0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2], "t")
        self.assertEqual(len(m), 1)
        self.assertEqual(TriMesh.from_dict(m.to_dict()).triangles, [(0, 1, 2)])
        with self.assertRaises(ValueError):
            TriMesh([(0, 0, 0)], [(0, 1, 2)])


class BVHTest(unittest.TestCase):
    def test_tree_covers_mesh(self):
        box = box_mesh((1, 1, 1))
        tree = BVH(box, leaf_size=2)
        self.assertEqual(tree.bounds.lo, box.bounds[0])
        self.assertEqual(sorted(i for leaf in tree.leaves() for i in leaf.triangles), list(range(12)))
        self.assertEqual(len(tree.triangles_in(AABB((0.4, -1, -1), (2, 1, 1)))), 2 + 4 * 2)

    def test_ray(self):
        self.assertAlmostEqual(ray_triangle((0.25, 0.25, 1.0), (0, 0, -1), ((0, 0, 0), (1, 0, 0), (0, 1, 0))), 1.0)
        self.assertIsNone(ray_triangle((2, 2, 1), (0, 0, -1), ((0, 0, 0), (1, 0, 0), (0, 1, 0))))
        hits = BVH(box_mesh((1, 1, 1))).ray_hits((0, 0, 0), (1, 0, 0))
        self.assertEqual(len(hits), 2, "a ray from the centre exits through one face (two triangles share the edge... or one)")

    def test_aabb(self):
        a, b = AABB((0, 0, 0), (1, 1, 1)), AABB((2, 0, 0), (3, 1, 1))
        self.assertAlmostEqual(a.distance(b), 1.0)
        self.assertFalse(a.overlaps(b))
        self.assertTrue(a.expanded(1.1).overlaps(b))
        self.assertEqual(a.transformed(T(1, 0, 0)).lo, (1.0, 0.0, 0.0))


class TriangleTest(unittest.TestCase):
    def test_crossing(self):
        t1 = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        t2 = ((0.2, 0.2, -1), (0.2, 0.2, 1), (0.3, 0.3, 1))
        self.assertTrue(triangles_intersect(t1, t2))
        far = ((5, 5, -1), (5, 5, 1), (6, 6, 1))
        self.assertFalse(triangles_intersect(t1, far))
        parallel = ((0, 0, 1), (1, 0, 1), (0, 1, 1))
        self.assertFalse(triangles_intersect(t1, parallel))

    def test_coplanar(self):
        t1 = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        overlapping = ((0.1, 0.1, 0), (1.1, 0.1, 0), (0.1, 1.1, 0))
        self.assertTrue(triangles_intersect(t1, overlapping))
        apart = ((2, 2, 0), (3, 2, 0), (2, 3, 0))
        self.assertFalse(triangles_intersect(t1, apart))


class CollideTest(unittest.TestCase):
    def test_separated_boxes(self):
        a, b = box_mesh((1, 1, 1)), box_mesh((1, 1, 1), center=(2, 0, 0))
        self.assertFalse(collide(a, b).colliding)
        d, pa, pb = closest_distance(a, b)
        self.assertAlmostEqual(d, 1.0)
        self.assertAlmostEqual(pa[0], 0.5)
        self.assertAlmostEqual(pb[0], 1.5)

    def test_overlap_push_is_minimal(self):
        a, b = box_mesh((1, 1, 1)), box_mesh((1, 1, 1), center=(1.5, 0, 0))
        r = collide(a, b, T(0.6))  # a spans x 0.1..1.1, b spans 1..2: 0.1 overlap
        self.assertTrue(r.colliding)
        self.assertAlmostEqual(r.depth, 0.1, places=6)
        self.assertAlmostEqual(r.push[0], -0.1, places=6)
        self.assertAlmostEqual(r.push[1], 0.0)
        # Pushing by the result separates them.
        self.assertFalse(collide(a, b, T(0.6 + r.push[0] - 1e-6)).colliding)

    def test_corner_overlap_picks_shortest_axis(self):
        a, b = box_mesh((1, 1, 1)), box_mesh((1, 1, 1), center=(1.5, 0, 0))
        r = collide(a, b, T(0.7, 0.95))  # x overlap 0.2, y overlap 0.05
        self.assertAlmostEqual(abs(r.push[1]), 0.05, places=6)
        self.assertAlmostEqual(r.push[0], 0.0)

    def test_rotated_part(self):
        a, b = box_mesh((1, 1, 1)), box_mesh((4, 0.2, 4), center=(0, -0.1, 0))  # a floor slab
        q = vm.quat_normalize((0.0, 0.0, math.sin(math.pi / 8), math.cos(math.pi / 8)))  # 45° about z
        r = collide(a, b, T(0, 0.6, 0, q))  # rotated cube, corner dips 0.707-0.6 = 0.107 below y=0
        self.assertTrue(r.colliding)
        self.assertGreater(r.push[1], 0.09)
        self.assertLess(r.push[1], 0.13)

    def test_peg_in_hole_clearance(self):
        hole = tube_mesh(0.5, 0.8, 1.0, sides=48)
        peg = cylinder_mesh(0.45, 1.0, sides=48)
        self.assertFalse(collide(peg, hole).colliding)
        d, _, _ = closest_distance(peg, hole)
        self.assertAlmostEqual(d, 0.05, delta=0.003)
        fat = cylinder_mesh(0.55, 1.0, sides=48)
        self.assertTrue(collide(fat, hole).colliding)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.session = FitSession(FitParams(clearance_max=1.0))
        self.session.add_part("floor", box_mesh((4, 0.2, 4), center=(0, -0.1, 0)))
        self.session.add_part("cube", box_mesh((1, 1, 1)), pose=T(0, 2, 0), static=False)

    def test_hand_pushes_cube_into_floor_and_it_stops(self):
        s = self.session
        hand = T(0, 2, 0)
        s.grab("cube", hand)
        moved = s.update(0.016, T(0, 0.3, 0), grip=1.0)  # ask for the cube to sink 0.2 into the floor
        self.assertTrue(moved)
        y = s.pose_of("cube").translation[1]
        self.assertAlmostEqual(y, 0.5, places=4, msg="stopped at the surface")
        kinds = [e.kind for e in s.drain_events()]
        self.assertIn("grab", kinds)
        self.assertIn("contact", kinds)
        self.assertNotIn("blocked", kinds, "resolved by push-out, not blocked")

    def test_sliding_along_the_floor(self):
        s = self.session
        s.grab("cube", T(0, 2, 0))
        s.update(0.016, T(0, 0.3, 0), grip=1.0)
        s.update(0.016, T(0.5, 0.3, 0), grip=1.0)
        p = s.pose_of("cube").translation
        self.assertAlmostEqual(p[0], 0.5, places=3, msg="tangential motion kept")
        self.assertAlmostEqual(p[1], 0.5, places=3)

    def test_release_on_grip(self):
        s = self.session
        s.grab("cube", T(0, 2, 0))
        s.update(0.016, T(0, 3, 0), grip=0.2)
        self.assertIsNone(s.grabbed)
        self.assertEqual(s.pose_of("cube").translation[1], 2.0)
        self.assertIn("release", [e.kind for e in s.events])

    def test_clearance_reported_when_free(self):
        s = self.session
        s.grab("cube", T(0, 2, 0))
        s.update(0.016, T(0, 1.0, 0), grip=1.0)
        self.assertIsNotNone(s.clearance)
        self.assertAlmostEqual(s.clearance[0], 0.5, places=4)
        self.assertEqual(s.clearance[1], "floor")

    def test_seated_event_with_margin(self):
        s = FitSession(FitParams(contact_margin=0.02, clearance_max=1.0))
        s.add_part("floor", box_mesh((4, 0.2, 4), center=(0, -0.1, 0)))
        s.add_part("cube", box_mesh((1, 1, 1)), pose=T(0, 2, 0), static=False)
        s.grab("cube", T(0, 2, 0))
        s.update(0.016, T(0, 0.3, 0), grip=1.0)
        self.assertAlmostEqual(s.pose_of("cube").translation[1], 0.52, places=3, msg="held off by the margin")
        self.assertIn("seated", [e.kind for e in s.events])

    def test_blocked_when_wedged(self):
        s = FitSession(FitParams(push_iterations=1, clearance_max=1.0))
        s.add_part("left", box_mesh((1, 3, 3), center=(-1.0, 0, 0)))
        s.add_part("right", box_mesh((1, 3, 3), center=(1.0, 0, 0)))
        s.add_part("wide", box_mesh((1.2, 0.5, 0.5)), pose=T(0, 3, 0), static=False)  # 1.2 wide into a 1.0 gap
        s.grab("wide", T(0, 3, 0))
        s.update(0.016, T(0, 0.0, 0), grip=1.0)
        self.assertTrue(s.blocked)
        self.assertGreater(s.pose_of("wide").translation[1], 1.5, "never entered the gap")
        self.assertIn("blocked", [e.kind for e in s.events])

    def test_insertion_probe(self):
        s = FitSession(FitParams(clearance_max=1.0))
        s.add_part("block", tube_mesh(0.5, 1.0, 1.0, sides=48))
        s.add_part("peg", cylinder_mesh(0.45, 1.0, sides=48), pose=T(0, 0, 2.0), static=False)
        result = InsertionProbe().probe(s, "peg", (0, 0, -1), 2.0)
        self.assertTrue(result["inserted"], result)
        self.assertAlmostEqual(result["travel"], 2.0)
        self.assertAlmostEqual(result["clearance"], 0.05, delta=0.005)
        s.add_part("fat", cylinder_mesh(0.6, 1.0, sides=48), pose=T(0, 0, 2.0), static=False)
        result = InsertionProbe().probe(s, "fat", (0, 0, -1), 2.0)
        self.assertFalse(result["inserted"])
        self.assertEqual(result["blocked_by"], "block")
        self.assertAlmostEqual(result["travel"], 1.0, delta=0.05, msg="stops at the face")


if __name__ == "__main__":
    unittest.main()
