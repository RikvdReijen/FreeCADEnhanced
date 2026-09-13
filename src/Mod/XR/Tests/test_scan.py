# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scan alignment: Kabsch, ICP, planes, and the pick-driven session."""

import math
import os
import random
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrassembly.mates import rotation_about  # noqa: E402
from xrfit import box_mesh, cylinder_mesh  # noqa: E402
from xrfit.bvh import BVH  # noqa: E402
from xrscan import (AlignmentError, ScanSession, closest_on_mesh, fit_plane, icp, kabsch, plane_to_plane,  # noqa: E402
                    principal_axes, scale_from_known_length)
from xrsketch import vecmath as vm  # noqa: E402


def truth():
    return vm.Transform((0.3, -0.2, 0.5), rotation_about((0.3, 1.0, 0.2), 0.7))


class KabschTest(unittest.TestCase):
    def test_recovers_rigid_transform(self):
        t = truth()
        src = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)]
        dst = [t.apply(p) for p in src]
        r = kabsch(src, dst)
        self.assertLess(r.rms, 1e-9)
        self.assertTrue(r.transform.almost_equal(t, 1e-8))

    def test_recovers_scale(self):
        t = vm.Transform((1, 2, 3), rotation_about((0, 0, 1), 0.4), 2.5)
        src = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        r = kabsch(src, [t.apply(p) for p in src], scale=True)
        self.assertAlmostEqual(r.transform.scale, 2.5, places=8)
        self.assertLess(r.rms, 1e-9)

    def test_reflection_is_not_a_rotation(self):
        src = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        dst = [(x, y, -z) for x, y, z in src]  # mirrored
        r = kabsch(src, dst)
        m = vm.quat_to_mat3(r.transform.rotation)
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        self.assertAlmostEqual(det, 1.0, places=6)
        self.assertGreater(r.rms, 0.1, "a mirror cannot be matched by a rotation")

    def test_errors(self):
        with self.assertRaises(AlignmentError):
            kabsch([(0, 0, 0), (1, 0, 0)], [(0, 0, 0), (1, 0, 0)])
        with self.assertRaises(AlignmentError):
            kabsch([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 0, 0), (1, 0, 0), (2, 0, 0)])
        with self.assertRaises(AlignmentError):
            kabsch([(0, 0, 0)] * 3, [(0, 0, 0), (1, 0, 0), (0, 1, 0)])


class ICPTest(unittest.TestCase):
    def test_refines_a_rough_pose(self):
        model = box_mesh((0.1, 0.06, 0.04))
        rng = random.Random(1)
        # sample points on the box surface
        pts = []
        for _ in range(600):
            face = rng.randrange(6)
            u, v = rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5)
            s = (0.1, 0.06, 0.04)
            p = [u * s[0], v * s[1], 0.0]
            axis = face // 2
            sign = 1 if face % 2 else -1
            coords = [u * s[(axis + 1) % 3], v * s[(axis + 2) % 3]]
            p = [0.0, 0.0, 0.0]
            p[axis] = sign * s[axis] / 2
            p[(axis + 1) % 3] = coords[0]
            p[(axis + 2) % 3] = coords[1]
            pts.append(tuple(p))
        rough = vm.Transform((0.004, -0.003, 0.002), rotation_about((0, 0, 1), 0.08))
        r = icp(pts, BVH(model), initial=rough, iterations=40, max_pairs=600)
        self.assertLess(r.rms, 5e-4, r)
        self.assertLess(vm.length(r.transform.translation), 1.5e-3)

    def test_closest_on_mesh(self):
        d, q, tri = closest_on_mesh(BVH(box_mesh((2, 2, 2))), (0, 0, 5))
        self.assertAlmostEqual(d, 4.0)
        self.assertAlmostEqual(q[2], 1.0)


class PlaneTest(unittest.TestCase):
    def test_fit_plane_finds_the_floor(self):
        rng = random.Random(3)
        pts = [(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.gauss(0.0, 0.001)) for _ in range(300)]
        pts += [(rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), rng.uniform(0.05, 0.5)) for _ in range(80)]  # the object on it
        origin, normal, inliers = fit_plane(pts, threshold=0.005)
        self.assertGreater(len(inliers), 280)
        self.assertAlmostEqual(abs(normal[2]), 1.0, places=3)
        self.assertLess(normal[2], 0.0, "normal points away from the object side")
        self.assertAlmostEqual(origin[2], 0.0, places=2)

    def test_plane_to_plane_and_principal_axes(self):
        t = plane_to_plane((0, 0, 1), (0, 0, 1), (5, 5, 0), (0, 1, 0))
        moved = t.apply((0, 0, 1))
        self.assertEqual([round(c, 9) for c in moved], [5.0, 5.0, 0.0])
        n = t.apply_vector((0, 0, 1))
        self.assertAlmostEqual(n[1], 1.0)
        pts = [(x, 0.1 * math.sin(x), 0.0) for x in [i * 0.1 for i in range(50)]]
        c, axes = principal_axes(pts)
        self.assertAlmostEqual(abs(axes[0][0]), 1.0, places=1)

    def test_scale_from_known_length(self):
        self.assertAlmostEqual(scale_from_known_length((0, 0, 0), (0, 0, 2), 50.0), 25.0)
        with self.assertRaises(AlignmentError):
            scale_from_known_length((0, 0, 0), (0, 0, 0), 1.0)


class SessionTest(unittest.TestCase):
    def test_pick_align_refine(self):
        model = box_mesh((0.1, 0.06, 0.04))
        t = truth()
        scan = model.transformed(t.inverse())  # the scan is the model seen in another frame
        s = ScanSession(scan, model)
        corners = [(-0.05, -0.03, -0.02), (0.05, -0.03, -0.02), (-0.05, 0.03, -0.02), (0.05, 0.03, 0.02)]
        for c in corners:
            s.pick_scan(t.inverse().apply(c))  # world == scan-local while the pose is identity
            s.pick_model(c)
        self.assertEqual(len(s.complete_pairs()), 4)
        r = s.align_from_pairs()
        self.assertLess(r.rms, 1e-9)
        self.assertTrue(s.scan_pose.almost_equal(t, 1e-8))
        self.assertLess(max(s.residuals()), 1e-9)
        r = s.refine(iterations=5, max_pairs=200)
        self.assertLess(r.rms, 1e-6)
        kinds = [e.kind for e in s.drain_events()]
        self.assertIn("aligned", kinds)
        self.assertIn("refined", kinds)
        self.assertTrue(s.undo())
        self.assertTrue(s.undo())
        self.assertTrue(s.scan_pose.almost_equal(vm.Transform.identity()))

    def test_pairs_need_three(self):
        s = ScanSession(box_mesh((1, 1, 1)))
        s.pick_scan((0, 0, 0)); s.pick_model((1, 1, 1))
        with self.assertRaises(AlignmentError):
            s.align_from_pairs()
        s.drop_last_pair()
        self.assertEqual(s.pairs, [])
        with self.assertRaises(AlignmentError):
            s.refine()

    def test_known_length_scales_about_the_picks(self):
        s = ScanSession(cylinder_mesh(0.5, 2.0))
        s.pick_length_point((0, 0, -1))
        s.pick_length_point((0, 0, 1))
        factor = s.set_known_length(0.02)  # the 2 m tall scan is really 20 mm
        self.assertAlmostEqual(factor, 0.01)
        self.assertAlmostEqual(s.scan_pose.scale, 0.01)
        mid = s.scan_pose.apply((0, 0, 0))
        self.assertAlmostEqual(vm.length(mid), 0.0, places=9, msg="midpoint of the picks stays put")

    def test_sit_on_plane(self):
        rng = random.Random(5)
        from xrfit import TriMesh
        # a slab of triangles lying in z=0.3 tilted a little, plus noise blob above
        pts = [(rng.uniform(-1, 1), rng.uniform(-1, 1), 0.3) for _ in range(200)]
        tris = [(i, i + 1, i + 2) for i in range(0, 198, 3)]
        mesh = TriMesh(pts, tris)
        s = ScanSession(mesh)
        s.sit_on_plane((0, 0, 0), (0, 0, 1))
        z = [s.scan_pose.apply(p)[2] for p in pts]
        self.assertLess(max(abs(v) for v in z), 1e-6)
        self.assertEqual(s.drain_events()[-1].kind, "seated")


if __name__ == "__main__":
    unittest.main()
