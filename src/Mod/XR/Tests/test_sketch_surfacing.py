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
"""Curves and the surfaces built from them.

The curve tests also pin down the reuse of :mod:`xrpaint.curve`: the 3D
evaluation must agree with the planar one coordinate by coordinate, because it
*is* the planar one applied twice.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrpaint import curve as _curve                            # noqa: E402
from xrsketch import curves as C                               # noqa: E402
from xrsketch import surfacing as S                            # noqa: E402
from xrsketch import vecmath as vm                             # noqa: E402


def circle(radius=1.0, z=0.0, count=32, centre=(0.0, 0.0)):
    return [(centre[0] + radius * math.cos(2.0 * math.pi * i / count),
             centre[1] + radius * math.sin(2.0 * math.pi * i / count), z)
            for i in range(count + 1)]


# ==========================================================================
# curves
# ==========================================================================

class TestBezier3D(unittest.TestCase):

    BEZ = ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (2.0, -1.0, 1.0),
           (3.0, 0.0, -2.0))

    def test_evaluation_matches_the_planar_implementation(self):
        for t in (0.0, 0.125, 0.5, 0.9, 1.0):
            got = C.bezier_point3(self.BEZ, t)
            xy = _curve.bezier_point([(p[0], p[1]) for p in self.BEZ], t)
            z = _curve.bezier_point([(p[2], 0.0) for p in self.BEZ], t)[0]
            self.assertEqual(got, (xy[0], xy[1], z))

    def test_endpoints_are_interpolated(self):
        self.assertEqual(C.bezier_point3(self.BEZ, 0.0), self.BEZ[0])
        self.assertEqual(C.bezier_point3(self.BEZ, 1.0), self.BEZ[3])

    def test_split_is_exact(self):
        left, right = C.bezier_split3(self.BEZ, 0.375)
        self.assertEqual(left[3], right[0])
        for t in (0.0, 0.4, 1.0):
            self.assertAlmostEqual(
                vm.dist(C.bezier_point3(left, t),
                        C.bezier_point3(self.BEZ, t * 0.375)), 0.0, places=12)
            self.assertAlmostEqual(
                vm.dist(C.bezier_point3(right, t),
                        C.bezier_point3(self.BEZ, 0.375 + t * 0.625)), 0.0,
                places=12)

    def test_subdivide_matches_split(self):
        sub = C.bezier_subdivide3(self.BEZ, 0.25, 0.75)
        for t in (0.0, 0.5, 1.0):
            self.assertAlmostEqual(
                vm.dist(C.bezier_point3(sub, t),
                        C.bezier_point3(self.BEZ, 0.25 + t * 0.5)), 0.0,
                places=12)

    def test_length_of_a_straight_cubic(self):
        line = C.line_to_bezier3((0.0, 0.0, 0.0), (1.0, 2.0, 2.0))
        self.assertAlmostEqual(C.bezier_length3(line), 3.0, places=6)

    def test_tangent_survives_repeated_control_points(self):
        degenerate = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                      (1.0, 0.0, 0.0))
        self.assertAlmostEqual(
            vm.dist(C.bezier_tangent3(degenerate, 0.0), (1.0, 0.0, 0.0)),
            0.0, places=9)


class TestCurve3D(unittest.TestCase):

    def test_from_points_interpolates_them(self):
        pts = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.5), (2.0, 0.0, 1.0),
               (3.0, -1.0, 0.0)]
        curve = C.Curve3D.from_points(pts)
        self.assertEqual(len(curve.points), 4)
        for i, p in enumerate(pts):
            self.assertAlmostEqual(vm.dist(curve.evaluate(min(i, 2),
                                                          1.0 if i == 3
                                                          else 0.0), p),
                                   0.0, places=12)

    def test_straight_from_points(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)],
                                      smooth=False)
        self.assertAlmostEqual(curve.length(), 2.0, places=9)

    def test_freehand_fit_stays_within_tolerance(self):
        samples = [(0.01 * i, 0.2 * math.sin(0.05 * i), 0.0)
                   for i in range(120)]
        curve = C.Curve3D.from_freehand(samples, error=0.002)
        self.assertLess(len(curve.points), len(samples))
        for p in samples:
            self.assertLess(curve.closest_point(p)[3], 0.004)

    def test_freehand_fit_of_a_spatial_stroke(self):
        helix = [(0.3 * math.cos(0.1 * t), 0.3 * math.sin(0.1 * t), 0.01 * t)
                 for t in range(120)]
        curve = C.Curve3D.from_freehand(helix, error=0.002)
        for p in helix:
            self.assertLess(curve.closest_point(p)[3], 0.004)

    def test_a_flat_stroke_is_fitted_in_one_plane(self):
        flat = [(0.01 * i, 0.1 * math.sin(0.06 * i), 0.5) for i in range(80)]
        curve = C.Curve3D.from_freehand(flat, error=0.002)
        for cp in curve.points:
            self.assertAlmostEqual(cp.position[2], 0.5, places=6)

    def test_control_point_types(self):
        cp = C.ControlPoint((0, 0, 0), (-1, 0, 0), (2, 0, 0), "symmetric")
        cp.set_handle_out((0, 3, 0))
        self.assertEqual(cp.handle_in, (0.0, -3.0, 0.0))
        cp.set_type("smooth")
        cp.set_handle_out((0, 0, 5))
        self.assertAlmostEqual(vm.length(cp.handle_in), 3.0, places=12)
        self.assertAlmostEqual(vm.dot(vm.normalize(cp.handle_in),
                                      vm.normalize(cp.handle_out)), -1.0,
                               places=12)
        self.assertRaises(ValueError, cp.set_type, "wobbly")

    def test_insert_point_does_not_change_the_shape(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 1, 0), (2, 0, 0)])
        before = [curve.point_at(s * 0.1) for s in range(21)]
        index = curve.insert_point(0, 0.5)
        self.assertEqual(index, 1)
        self.assertEqual(len(curve.points), 4)
        self.assertEqual(curve.segment_count(), 3)
        for p in before:
            # 1e-6 is the resolution of the closest-point search itself
            self.assertLess(curve.closest_point(p, samples=64)[3], 1e-6)

    def test_split_and_join_round_trip(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 1, 0), (2, 0, 0),
                                       (3, 1, 1)])
        a, b = C.split(curve, 1, 0.4)
        self.assertAlmostEqual(vm.dist(a.end_point(), b.start_point()), 0.0,
                               places=12)
        joined = C.join(a, b)
        self.assertIsNotNone(joined)
        self.assertAlmostEqual(joined.length(1e-6), curve.length(1e-6),
                               places=6)
        for s in range(11):
            p = curve.point_at(3.0 * s / 10.0)
            self.assertLess(joined.closest_point(p, samples=64)[3], 1e-6)

    def test_join_flips_when_that_is_what_meets(self):
        a = C.Curve3D.from_points([(0, 0, 0), (1, 0, 0)], smooth=False)
        b = C.Curve3D.from_points([(2, 0, 0), (1, 0, 0)], smooth=False)
        joined = C.join(a, b)
        self.assertIsNotNone(joined)
        self.assertAlmostEqual(joined.length(), 2.0, places=9)
        far = C.Curve3D.from_points([(9, 9, 9), (8, 8, 8)], smooth=False)
        self.assertIsNone(C.join(a, far))

    def test_joining_the_halves_of_a_loop_closes_it(self):
        top = C.Curve3D.from_points([(0, 0, 0), (1, 1, 0), (2, 0, 0)],
                                    smooth=False)
        bottom = C.Curve3D.from_points([(2, 0, 0), (1, -1, 0), (0, 0, 0)],
                                       smooth=False)
        loop = C.join(top, bottom)
        self.assertIsNotNone(loop)
        self.assertTrue(loop.closed)
        self.assertEqual(len(loop.points), 4)
        self.assertAlmostEqual(loop.length(), top.length() + bottom.length(),
                               places=6)

    def test_trim(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 0, 0), (2, 0, 0),
                                       (3, 0, 0)], smooth=False)
        cut = C.trim(curve, 0.5, 2.5)
        self.assertAlmostEqual(cut.length(), 2.0, places=6)
        self.assertRaises(ValueError, C.trim, curve, 1.0, 1.0)

    def test_mirror(self):
        curve = C.Curve3D.from_points([(1, 0, 0), (2, 1, 0), (3, 0, 0)])
        flipped = C.mirror(curve, (0, 0, 0), (1, 0, 0))
        self.assertAlmostEqual(flipped.points[0].position[0], -1.0, places=12)
        self.assertAlmostEqual(flipped.length(), curve.length(), places=9)

    def test_offset_of_a_planar_curve(self):
        square = C.Curve3D.from_points([(1, 0, 0), (0, 1, 0), (-1, 0, 0),
                                        (0, -1, 0)], closed=True)
        offset = C.offset(square, 0.1)
        self.assertGreater(len(offset.points), 2)
        plane = offset.is_planar(1e-6)
        self.assertIsNotNone(plane)

    def test_offset_refuses_a_spatial_curve(self):
        helix = C.Curve3D.from_points([(1, 0, 0), (0, 1, 0.5), (-1, 0, 1),
                                       (0, -1, 1.5)])
        self.assertRaises(ValueError, C.offset, helix, 0.1)

    def test_project_to_plane(self):
        curve = C.Curve3D.from_points([(0, 0, 1), (1, 1, 2), (2, 0, 3)])
        flat = C.project_to_plane(curve, (0, 0, 0), (0, 0, 1))
        for cp in flat.points:
            self.assertAlmostEqual(cp.position[2], 0.0, places=12)
        self.assertRaises(ValueError, C.project_to_plane, curve, (0, 0, 0),
                          (0, 0, 1), (1, 0, 0))

    def test_project_to_surface(self):
        dome = S.revolve([(0.0, 0.0, 1.0), (0.7, 0.0, 0.7), (1.0, 0.0, 0.0)],
                         (0, 0, 0), (0, 0, 1), segments=24, samples=12)
        curve = C.Curve3D.from_points([(0.2, 0.0, 3.0), (0.0, 0.2, 3.0),
                                       (-0.2, 0.0, 3.0)])
        on = C.project_to_surface(curve, dome, samples=16)
        for p in on.flatten(1e-3):
            self.assertLess(dome.closest_point(p)[2], 0.05)

    def test_planarity_detection(self):
        flat = C.Curve3D.from_points([(0, 0, 2), (1, 1, 2), (2, 0, 2)])
        self.assertIsNotNone(flat.is_planar(1e-9))
        spatial = C.Curve3D.from_points([(0, 0, 0), (1, 1, 1), (2, 0, -1),
                                         (3, 1, 2)])
        self.assertIsNone(spatial.is_planar(1e-9))

    def test_serialisation_round_trip(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 1, 0), (2, 0, 1)])
        clone = C.Curve3D.from_dict(curve.to_dict())
        self.assertEqual(len(clone.points), len(curve.points))
        self.assertAlmostEqual(clone.length(), curve.length(), places=9)

    def test_network_joins_and_junctions(self):
        a = C.Curve3D.from_points([(0, 0, 0), (1, 0, 0)], smooth=False)
        b = C.Curve3D.from_points([(1, 0, 0), (1, 1, 0)], smooth=False)
        c = C.Curve3D.from_points([(1, 0, 0), (1, -1, 0)], smooth=False)
        net = C.CurveNetwork([a, b, c])
        self.assertEqual(len(net.junctions(1e-9)), 1)
        self.assertGreaterEqual(net.join_all(1e-9), 1)


# ==========================================================================
# surfaces
# ==========================================================================

class TestLoft(unittest.TestCase):

    def test_loft_through_identical_circles_is_a_cylinder(self):
        sides = 64
        surface = S.loft([circle(0.5, 0.0, sides), circle(0.5, 1.0, sides)],
                         samples=sides)
        self.assertEqual(surface.kind, "loft")
        # every station has the same cross section at a different height:
        # that is what makes it a cylinder rather than a general loft
        for i in range(surface.nu + 1):
            bottom = surface.grid[0][i]
            top = surface.grid[1][i]
            self.assertAlmostEqual(bottom[0], top[0], places=12)
            self.assertAlmostEqual(bottom[1], top[1], places=12)
            self.assertAlmostEqual(bottom[2], 0.0, places=12)
            self.assertAlmostEqual(top[2], 1.0, places=12)
        # and the section is the circle, up to the sagitta of the polygon
        # the section was given as
        sagitta = 0.5 * (1.0 - math.cos(math.pi / sides))
        for p in surface.points():
            self.assertAlmostEqual(math.hypot(p[0], p[1]), 0.5,
                                   delta=sagitta * 1.001)
        for u in (0.1, 0.37, 0.8):
            for v in (0.25, 0.5, 0.75):
                p = surface.evaluate(u, v)
                self.assertAlmostEqual(math.hypot(p[0], p[1]), 0.5,
                                       delta=sagitta * 1.001)
                self.assertAlmostEqual(p[2], v, places=12)

    def test_loft_through_three_sections(self):
        surface = S.loft([circle(0.5, 0.0), circle(1.0, 1.0),
                          circle(0.5, 2.0)], samples=16)
        self.assertEqual(surface.nv, 2)
        self.assertAlmostEqual(math.hypot(*surface.grid[1][0][:2]), 1.0,
                               places=9)

    def test_loft_needs_two_sections(self):
        self.assertRaises(ValueError, S.loft, [circle()])
        self.assertRaises(ValueError, S.loft, [])
        self.assertRaises(ValueError, S.loft, None)

    def test_loft_refuses_a_degenerate_section(self):
        self.assertRaises(ValueError, S.loft, [[(0, 0, 0)], circle()])

    def test_a_zero_length_section_does_not_produce_nans(self):
        flat = [(0.0, 0.0, 0.0)] * 8
        surface = S.loft([flat, circle(1.0, 1.0)], samples=8)
        for p in surface.points():
            for c in p:
                self.assertTrue(math.isfinite(c))


class TestRevolve(unittest.TestCase):

    def test_revolving_a_parallel_line_gives_a_cylinder(self):
        surface = S.revolve([(1.0, 0.0, 0.0), (1.0, 0.0, 2.0)],
                            (0, 0, 0), (0, 0, 1), segments=36, samples=8)
        for p in surface.points():
            self.assertAlmostEqual(math.hypot(p[0], p[1]), 1.0, places=9)
            self.assertTrue(-1e-12 <= p[2] <= 2.0 + 1e-12)

    def test_revolving_a_slanted_line_gives_a_cone(self):
        surface = S.revolve([(1.0, 0.0, 0.0), (0.0, 0.0, 2.0)],
                            (0, 0, 0), (0, 0, 1), segments=36, samples=16)
        for p in surface.points():
            expected = 1.0 - p[2] / 2.0
            self.assertAlmostEqual(math.hypot(p[0], p[1]), expected,
                                   places=9)
        self.assertTrue(surface.closed_v)

    def test_partial_revolve(self):
        surface = S.revolve([(1.0, 0.0, 0.0), (1.0, 0.0, 1.0)],
                            (0, 0, 0), (0, 0, 1), angle=math.pi / 2.0,
                            segments=8, samples=4)
        self.assertFalse(surface.closed_v)
        last = surface.grid[-1][0]
        self.assertAlmostEqual(last[0], 0.0, places=9)
        self.assertAlmostEqual(last[1], 1.0, places=9)

    def test_degenerate_revolve_raises(self):
        line = [(1.0, 0.0, 0.0), (1.0, 0.0, 1.0)]
        self.assertRaises(ValueError, S.revolve, line, (0, 0, 0), (0, 0, 0))
        self.assertRaises(ValueError, S.revolve, line, (0, 0, 0), (0, 0, 1),
                          angle=0.0)
        self.assertRaises(ValueError, S.revolve, line, (0, 0, 0), (0, 0, 1),
                          segments=0)


class TestSweep(unittest.TestCase):

    def test_sweep_along_a_straight_rail_is_an_extrusion(self):
        profile = [(0.0, -0.1, 0.0), (0.0, 0.1, 0.0)]
        rail = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        surface = S.sweep(profile, rail, samples=4, stations=4)
        for row in surface.grid:
            self.assertAlmostEqual(vm.dist(row[0], row[-1]), 0.2, places=9)

    def test_sweep_frames_do_not_flip(self):
        profile = [(0.0, -0.1, 0.0), (0.0, 0.1, 0.0)]
        rail = [(math.cos(t * 0.2), math.sin(t * 0.2), t * 0.05)
                for t in range(20)]
        surface = S.sweep(profile, rail, samples=4, stations=19)
        widths = [vm.dist(row[0], row[-1]) for row in surface.grid]
        for w in widths:
            self.assertAlmostEqual(w, 0.2, places=9)

    def test_two_rail_sweep_lands_on_both_rails(self):
        profile = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        rail_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        rail_b = [(0.0, 2.0, 0.0), (1.0, 2.0, 0.5), (2.0, 2.0, 0.0)]
        surface = S.sweep_two_rails(profile, rail_a, rail_b, samples=4,
                                    stations=8)
        for row in surface.grid:
            self.assertLess(min(vm.dist(row[0], p) for p in
                                S._resample(rail_a, 64)), 1e-6)
            self.assertLess(min(vm.dist(row[-1], p) for p in
                                S._resample(rail_b, 64)), 1e-6)

    def test_two_rail_sweep_refuses_touching_rails(self):
        profile = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        rail = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        self.assertRaises(ValueError, S.sweep_two_rails, profile, rail, rail)
        self.assertRaises(ValueError, S.sweep_two_rails,
                          [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)], rail,
                          [(0.0, 1.0, 0.0), (1.0, 1.0, 0.0)])


class TestCoonsPatch(unittest.TestCase):

    def _boundaries(self):
        return [
            C.Curve3D.from_points([(0, 0, 0), (0.5, 0, 0.4), (1, 0, 0)]),
            C.Curve3D.from_points([(1, 0, 0), (1, 0.5, -0.3), (1, 1, 0)]),
            C.Curve3D.from_points([(1, 1, 0), (0.5, 1, 0.6), (0, 1, 0)]),
            C.Curve3D.from_points([(0, 1, 0), (0, 0.5, 0.2), (0, 0, 0)]),
        ]

    def test_the_patch_interpolates_its_boundaries_exactly(self):
        """The patch edges *are* the boundary polylines, to the last bit."""
        bottom = [(0.0, 0.0, 0.0), (0.5, 0.0, 0.4), (1.0, 0.0, 0.0)]
        right = [(1.0, 0.0, 0.0), (1.0, 0.5, -0.3), (1.0, 1.0, 0.0)]
        top = [(1.0, 1.0, 0.0), (0.5, 1.0, 0.6), (0.0, 1.0, 0.0)]
        left = [(0.0, 1.0, 0.0), (0.0, 0.5, 0.2), (0.0, 0.0, 0.0)]
        patch = S.coons_patch([bottom, right, top, left], 8, 8)
        expect_bottom = S._resample(bottom, 8)
        expect_top = list(reversed(S._resample(top, 8)))
        expect_right = S._resample(right, 8)
        expect_left = list(reversed(S._resample(left, 8)))
        for i in range(9):
            self.assertAlmostEqual(vm.dist(patch.grid[0][i],
                                           expect_bottom[i]), 0.0, places=15)
            self.assertAlmostEqual(vm.dist(patch.grid[8][i], expect_top[i]),
                                   0.0, places=15)
        for j in range(9):
            self.assertAlmostEqual(vm.dist(patch.grid[j][8], expect_right[j]),
                                   0.0, places=15)
            self.assertAlmostEqual(vm.dist(patch.grid[j][0], expect_left[j]),
                                   0.0, places=15)

    def test_the_patch_follows_curved_boundaries(self):
        boundaries = self._boundaries()
        patch = S.coons_patch(boundaries, 24, 24)
        for i in range(patch.nu + 1):
            for edge, v in ((0, 0.0), (2, 1.0)):
                p = patch.evaluate(i / float(patch.nu), v)
                self.assertLess(boundaries[edge].closest_point(p)[3], 1e-4)
        for j in range(patch.nv + 1):
            for edge, u in ((3, 0.0), (1, 1.0)):
                p = patch.evaluate(u, j / float(patch.nv))
                self.assertLess(boundaries[edge].closest_point(p)[3], 1e-4)

    def test_the_corners_are_the_shared_endpoints(self):
        patch = S.coons_patch(self._boundaries(), 8, 8)
        self.assertAlmostEqual(vm.dist(patch.evaluate(0.0, 0.0),
                                       (0, 0, 0)), 0.0, places=9)
        self.assertAlmostEqual(vm.dist(patch.evaluate(1.0, 0.0),
                                       (1, 0, 0)), 0.0, places=9)
        self.assertAlmostEqual(vm.dist(patch.evaluate(1.0, 1.0),
                                       (1, 1, 0)), 0.0, places=9)
        self.assertAlmostEqual(vm.dist(patch.evaluate(0.0, 1.0),
                                       (0, 1, 0)), 0.0, places=9)

    def test_a_flat_boundary_gives_a_flat_patch(self):
        square = [
            [(0, 0, 0), (1, 0, 0)], [(1, 0, 0), (1, 1, 0)],
            [(1, 1, 0), (0, 1, 0)], [(0, 1, 0), (0, 0, 0)],
        ]
        patch = S.coons_patch(square, 6, 6)
        for p in patch.points():
            self.assertAlmostEqual(p[2], 0.0, places=12)

    def test_boundaries_may_be_given_in_any_order_or_direction(self):
        boundaries = self._boundaries()
        shuffled = [boundaries[2], boundaries[0], boundaries[3],
                    boundaries[1]]
        patch = S.coons_patch(shuffled, 8, 8)
        corners = [patch.evaluate(u, v) for u in (0.0, 1.0)
                   for v in (0.0, 1.0)]
        for c in corners:
            self.assertLess(min(vm.dist(c, p) for p in
                                [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]),
                            1e-9)

    def test_three_boundaries_make_a_triangular_patch(self):
        triangle = [
            [(0, 0, 0), (1, 0, 0)], [(1, 0, 0), (0.5, 1, 0)],
            [(0.5, 1, 0), (0, 0, 0)],
        ]
        patch = S.coons_patch(triangle, 8, 8)
        for p in patch.points():
            self.assertTrue(all(math.isfinite(c) for c in p))

    def test_an_open_boundary_loop_raises(self):
        broken = [
            [(0, 0, 0), (1, 0, 0)], [(1, 0, 0), (1, 1, 0)],
            [(1, 1, 0), (0, 1, 0)], [(0, 1, 0), (0, 0.5, 0)],
        ]
        self.assertRaises(ValueError, S.coons_patch, broken)
        self.assertRaises(ValueError, S.coons_patch, broken[:2])


class TestExtrudeAndMesh(unittest.TestCase):

    def test_extrude(self):
        surface = S.extrude(circle(1.0), (0.0, 0.0, 2.0), segments=4,
                            samples=16)
        self.assertEqual(surface.nv, 4)
        for p in surface.points():
            self.assertAlmostEqual(math.hypot(p[0], p[1]), 1.0, places=9)
        self.assertAlmostEqual(surface.grid[-1][0][2], 2.0, places=12)

    def test_extrude_refuses_a_zero_vector(self):
        self.assertRaises(ValueError, S.extrude, circle(), (0, 0, 0))
        self.assertRaises(ValueError, S.extrude, circle(), (0, 0, 1),
                          segments=0)

    def test_surface_evaluation_and_normals(self):
        surface = S.extrude([(0, 0, 0), (1, 0, 0)], (0, 1, 0), segments=2,
                            samples=2)
        self.assertAlmostEqual(vm.dist(surface.evaluate(0.5, 0.5),
                                       (0.5, 0.5, 0.0)), 0.0, places=12)
        self.assertAlmostEqual(abs(vm.dot(surface.normal(0.5, 0.5),
                                          (0.0, 0.0, 1.0))), 1.0, places=6)

    def test_closest_point_and_raycast(self):
        surface = S.extrude([(0, 0, 0), (1, 0, 0)], (0, 1, 0), segments=4,
                            samples=4)
        point, uv, distance = surface.closest_point((0.5, 0.5, 1.0))
        self.assertAlmostEqual(distance, 1.0, places=6)
        self.assertAlmostEqual(point[2], 0.0, places=9)
        hit = surface.raycast((0.5, 0.5, 1.0), (0.0, 0.0, -1.0))
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[1], 1.0, places=9)
        self.assertIsNone(surface.raycast((5.0, 5.0, 1.0), (0.0, 0.0, -1.0)))

    def test_to_cage_welds_a_closed_seam(self):
        surface = S.extrude(circle(1.0, count=8), (0, 0, 1), segments=2,
                            samples=8)
        cage = surface.to_cage()
        self.assertEqual(cage.check(), [])
        self.assertEqual(cage.face_count, 16)

    def test_ragged_and_tiny_grids_are_refused(self):
        self.assertRaises(ValueError, S.SurfaceMesh, [[(0, 0, 0)]])
        self.assertRaises(ValueError, S.SurfaceMesh,
                          [[(0, 0, 0), (1, 0, 0)], [(0, 1, 0)]])

    def test_serialisation_round_trip(self):
        surface = S.extrude([(0, 0, 0), (1, 0, 0)], (0, 1, 0))
        clone = S.SurfaceMesh.from_dict(surface.to_dict())
        self.assertEqual(clone.grid, surface.grid)


class TestPartMapping(unittest.TestCase):
    """to_part() must be faithful or say so — never quietly approximate."""

    def test_unfaithful_kinds_are_refused_by_name(self):
        patch = S.coons_patch([
            [(0, 0, 0), (1, 0, 0)], [(1, 0, 0), (1, 1, 0)],
            [(1, 1, 0), (0, 1, 0)], [(0, 1, 0), (0, 0, 0)]], 4, 4)
        with self.assertRaises(S.UnsupportedMapping) as caught:
            S.to_part(patch)
        self.assertIn("coons", str(caught.exception))
        self.assertIn("to_mesh_shape", str(caught.exception))
        two_rails = S.sweep_two_rails([(0, 0, 0), (0, 1, 0)],
                                      [(0, 0, 0), (1, 0, 0)],
                                      [(0, 1, 0), (1, 1, 0)], samples=4,
                                      stations=4)
        self.assertRaises(S.UnsupportedMapping, S.to_part, two_rails)

    def test_faithful_kinds_are_listed(self):
        self.assertEqual(set(S.FAITHFUL_KINDS),
                         {"extrude", "revolve", "loft"})

    def test_faithful_kinds_report_a_missing_part_module(self):
        surface = S.extrude([(0, 0, 0), (1, 0, 0)], (0, 1, 0))
        try:
            import Part                                        # noqa: F401
        except Exception:
            self.assertRaises(S.UnsupportedMapping, S.to_part, surface)
        else:                                    # pragma: no cover - host
            self.skipTest("Part is importable, the failure path cannot run")


if __name__ == "__main__":
    unittest.main()
