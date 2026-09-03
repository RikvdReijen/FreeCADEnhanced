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
"""Unit tests for the vector editor: curve maths, the §4 document, SVG and
the FreeCAD commit layer.

Runs under plain ``python3 -m unittest`` from ``src/Mod/XR`` without FreeCAD.
"""

import json
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrpaint import curve, svg, to_freecad, ui, vector  # noqa: E402
from xrpaint.vector import (Node, Path, Plane, SnapEngine, SnapSettings,  # noqa: E402
                            VectorDocument)

ARC = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
LINE = curve.line_to_bezier((0.0, 0.0), (3.0, 0.0))


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_segment_distance(p, a, b):
    ax = b[0] - a[0]
    ay = b[1] - a[1]
    L2 = ax * ax + ay * ay
    if L2 <= 0.0:
        return _dist(p, a)
    t = ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2
    t = max(0.0, min(1.0, t))
    return _dist(p, (a[0] + ax * t, a[1] + ay * t))


def _point_polyline_distance(p, pts):
    return min(_point_segment_distance(p, pts[i], pts[i + 1])
               for i in range(len(pts) - 1))


# ==========================================================================
# Bezier maths against analytic values
# ==========================================================================

class TestBezierMath(unittest.TestCase):

    def test_endpoints(self):
        self.assertEqual(curve.bezier_point(ARC, 0.0), (0.0, 0.0))
        self.assertEqual(curve.bezier_point(ARC, 1.0), (1.0, 0.0))

    def test_midpoint_is_analytic(self):
        # B(1/2) = (p0 + 3p1 + 3p2 + p3) / 8
        p = curve.bezier_point(ARC, 0.5)
        self.assertAlmostEqual(p[0], 0.5, places=12)
        self.assertAlmostEqual(p[1], 0.75, places=12)

    def test_first_derivative_is_analytic(self):
        self.assertEqual(curve.bezier_derivative(ARC, 0.0), (0.0, 3.0))
        self.assertEqual(curve.bezier_derivative(ARC, 1.0), (0.0, -3.0))
        d = curve.bezier_derivative(ARC, 0.5)
        self.assertAlmostEqual(d[0], 1.5, places=12)
        self.assertAlmostEqual(d[1], 0.0, places=12)

    def test_derivative_matches_finite_differences(self):
        h = 1e-6
        for t in (0.1, 0.37, 0.5, 0.83):
            a = curve.bezier_point(ARC, t - h)
            b = curve.bezier_point(ARC, t + h)
            fd = ((b[0] - a[0]) / (2 * h), (b[1] - a[1]) / (2 * h))
            d = curve.bezier_derivative(ARC, t)
            self.assertAlmostEqual(d[0], fd[0], places=5)
            self.assertAlmostEqual(d[1], fd[1], places=5)

    def test_second_derivative_is_analytic(self):
        # B''(0) = 6 (p2 - 2 p1 + p0) = 6 * (1, -1)
        d2 = curve.bezier_second_derivative(ARC, 0.0)
        self.assertAlmostEqual(d2[0], 6.0, places=12)
        self.assertAlmostEqual(d2[1], -6.0, places=12)

    def test_tangent_is_unit_and_survives_degenerate_handles(self):
        t = curve.bezier_tangent(ARC, 0.5)
        self.assertAlmostEqual(math.hypot(*t), 1.0, places=12)
        flat = ((0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (1.0, 0.0))
        t0 = curve.bezier_tangent(flat, 0.0)
        self.assertAlmostEqual(math.hypot(*t0), 1.0, places=12)
        degenerate = ((2.0, 2.0),) * 4
        self.assertEqual(curve.bezier_tangent(degenerate, 0.5), (0.0, 0.0))

    def test_split_reproduces_the_original(self):
        left, right = curve.bezier_split(ARC, 0.3)
        self.assertEqual(left[3], right[0])
        for i in range(21):
            u = i / 20.0
            a = curve.bezier_point(left, u)
            b = curve.bezier_point(ARC, u * 0.3)
            self.assertAlmostEqual(a[0], b[0], places=12)
            self.assertAlmostEqual(a[1], b[1], places=12)
            a = curve.bezier_point(right, u)
            b = curve.bezier_point(ARC, 0.3 + u * 0.7)
            self.assertAlmostEqual(a[0], b[0], places=12)
            self.assertAlmostEqual(a[1], b[1], places=12)

    def test_subdivide_range(self):
        sub = curve.bezier_subdivide(ARC, 0.25, 0.75)
        self.assertAlmostEqual(sub[0][0], curve.bezier_point(ARC, 0.25)[0],
                               places=12)
        self.assertAlmostEqual(sub[3][1], curve.bezier_point(ARC, 0.75)[1],
                               places=12)
        self.assertEqual(curve.bezier_subdivide(ARC, 0.0, 1.0), ARC)

    def test_length_of_a_straight_cubic(self):
        self.assertAlmostEqual(curve.bezier_length(LINE), 3.0, places=10)

    def test_length_matches_a_dense_polyline(self):
        gauss = curve.bezier_length(ARC)
        poly = curve.bezier_length(ARC, samples=200000)
        self.assertAlmostEqual(gauss, poly, places=6)

    def test_partial_lengths_add_up(self):
        total = curve.bezier_length(ARC)
        a = curve.bezier_length(ARC, 0.0, 0.4)
        b = curve.bezier_length(ARC, 0.4, 1.0)
        self.assertAlmostEqual(a + b, total, places=10)

    def test_bbox_is_tight(self):
        box = curve.bezier_bbox(ARC)
        self.assertAlmostEqual(box[0], 0.0, places=12)
        self.assertAlmostEqual(box[1], 0.0, places=12)
        self.assertAlmostEqual(box[2], 1.0, places=12)
        self.assertAlmostEqual(box[3], 0.75, places=12)
        # sampling must never leave the box
        for i in range(101):
            p = curve.bezier_point(ARC, i / 100.0)
            self.assertGreaterEqual(p[0], box[0] - 1e-12)
            self.assertLessEqual(p[0], box[2] + 1e-12)
            self.assertLessEqual(p[1], box[3] + 1e-12)

    def test_flatten_respects_the_tolerance(self):
        for tol in (1.0, 0.1, 0.01):
            pts = curve.flatten_bezier(ARC, tol)
            self.assertEqual(pts[0], ARC[0])
            self.assertEqual(pts[-1], ARC[3])
            for i in range(201):
                p = curve.bezier_point(ARC, i / 200.0)
                self.assertLessEqual(_point_polyline_distance(p, pts),
                                     tol * 1.5 + 1e-9)

    def test_flatten_gets_finer_with_a_smaller_tolerance(self):
        coarse = curve.flatten_bezier(ARC, 0.5)
        fine = curve.flatten_bezier(ARC, 0.001)
        self.assertLess(len(coarse), len(fine))

    def test_point_at_length(self):
        segs = [LINE]
        i, t, p = curve.point_at_length(segs, 1.5)
        self.assertEqual(i, 0)
        self.assertAlmostEqual(p[0], 1.5, places=4)
        self.assertAlmostEqual(curve.path_length(segs), 3.0, places=9)

    def test_resample_uniform(self):
        pts = curve.resample_uniform([(0, 0), (10, 0)], 2.0)
        self.assertEqual(len(pts), 6)
        for a, b in zip(pts, pts[1:]):
            self.assertAlmostEqual(_dist(a, b), 2.0, places=9)

    def test_remove_duplicates(self):
        pts = curve.remove_duplicates([(0, 0), (0, 0), (1, 1), (1, 1),
                                       (2, 2)])
        self.assertEqual(pts, [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])


# ==========================================================================
# Schneider fitting
# ==========================================================================

class TestCurveFitting(unittest.TestCase):

    KNOWN = ((0.0, 0.0), (30.0, 80.0), (90.0, 80.0), (120.0, 0.0))

    def _samples(self, n=200, noise=0.0, seed=1):
        rng = random.Random(seed)
        out = []
        for i in range(n + 1):
            p = curve.bezier_point(self.KNOWN, i / float(n))
            if noise:
                p = (p[0] + rng.uniform(-noise, noise),
                     p[1] + rng.uniform(-noise, noise))
            out.append(p)
        return out

    def test_fits_a_known_curve_within_tolerance(self):
        pts = self._samples()
        for tol in (0.5, 1.0, 2.0):
            segs = curve.fit_curve(pts, error=tol)
            self.assertGreaterEqual(len(segs), 1)
            worst = max(curve.closest_point_on_path(segs, p)[3] for p in pts)
            self.assertLessEqual(worst, tol,
                                 "tolerance %.2f exceeded (%.4f)"
                                 % (tol, worst))

    def test_a_clean_cubic_needs_a_single_segment(self):
        segs = curve.fit_curve(self._samples(), error=1.0)
        self.assertEqual(len(segs), 1)
        # the endpoints are interpolated exactly
        self.assertAlmostEqual(segs[0][0][0], 0.0, places=9)
        self.assertAlmostEqual(segs[0][3][0], 120.0, places=9)

    def test_noisy_input_still_fits(self):
        pts = self._samples(noise=0.4, seed=42)
        segs = curve.fit_curve(pts, error=2.0)
        worst = max(curve.closest_point_on_path(segs, p)[3] for p in pts)
        self.assertLessEqual(worst, 2.0)

    def test_a_straight_run_becomes_one_segment(self):
        pts = [(float(i), 0.0) for i in range(50)]
        segs = curve.fit_curve(pts, error=0.1)
        self.assertEqual(len(segs), 1)
        for t in (0.0, 0.25, 0.5, 1.0):
            self.assertAlmostEqual(curve.bezier_point(segs[0], t)[1], 0.0,
                                   places=6)

    def test_two_points_become_a_line(self):
        segs = curve.fit_curve([(0, 0), (10, 5)], error=0.1)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][0], (0.0, 0.0))
        self.assertEqual(segs[0][3], (10.0, 5.0))

    def test_degenerate_input(self):
        self.assertEqual(curve.fit_curve([], 1.0), [])
        self.assertEqual(curve.fit_curve([(1, 1)], 1.0), [])
        self.assertEqual(curve.fit_curve([(1, 1), (1, 1), (1, 1)], 1.0), [])

    def test_corner_detection_on_a_right_angle(self):
        pts = ([(float(x), 0.0) for x in range(51)]
               + [(50.0, float(y)) for y in range(1, 51)])
        corners = curve.detect_corners(pts, 60.0)
        self.assertEqual(corners, [50])

    def test_corner_detection_ignores_a_smooth_curve(self):
        self.assertEqual(curve.detect_corners(self._samples(), 60.0), [])

    def test_corner_detection_thresholds(self):
        # a 45 degree turn
        pts = [(0.0, 0.0), (10.0, 0.0), (20.0, 10.0)]
        self.assertEqual(curve.detect_corners(pts, 30.0), [1])
        self.assertEqual(curve.detect_corners(pts, 60.0), [])

    def test_fit_reports_corner_nodes(self):
        pts = ([(float(x), 0.0) for x in range(51)]
               + [(50.0, float(y)) for y in range(1, 51)])
        segs, corners = curve.fit_curve(pts, error=0.5, return_corners=True)
        self.assertEqual(len(corners), 1)
        idx = corners[0]
        corner_pt = segs[idx - 1][3]
        self.assertAlmostEqual(corner_pt[0], 50.0, places=6)
        self.assertAlmostEqual(corner_pt[1], 0.0, places=6)

    def test_corner_is_kept_sharp(self):
        pts = ([(float(x), 0.0) for x in range(31)]
               + [(30.0, float(y)) for y in range(1, 31)])
        segs = curve.fit_curve(pts, error=0.5)
        worst = max(curve.closest_point_on_path(segs, p)[3] for p in pts)
        self.assertLessEqual(worst, 0.5)

    def test_simplify_pre_pass(self):
        pts = self._samples(n=400)
        a = curve.fit_curve(pts, error=1.0, simplify_tol=0.0)
        b = curve.fit_curve(pts, error=1.0, simplify_tol=0.5)
        self.assertGreaterEqual(len(a), 1)
        self.assertGreaterEqual(len(b), 1)


class TestSimplification(unittest.TestCase):

    def test_endpoints_are_kept(self):
        pts = [(0, 0), (1, 0.05), (2, 0), (3, 0.02), (4, 0)]
        out = curve.douglas_peucker(pts, 0.5)
        self.assertEqual(out[0], (0.0, 0.0))
        self.assertEqual(out[-1], (4.0, 0.0))
        self.assertEqual(len(out), 2)

    def test_output_is_a_subsequence_of_the_input(self):
        rng = random.Random(5)
        pts = [(float(i), rng.uniform(-3, 3)) for i in range(80)]
        out = curve.douglas_peucker(pts, 1.0)
        it = iter(pts)
        for p in out:
            self.assertIn(p, it, "output is not in input order")

    def test_deviation_stays_within_the_tolerance(self):
        rng = random.Random(9)
        pts = [(float(i), math.sin(i * 0.2) * 10 + rng.uniform(-0.5, 0.5))
               for i in range(120)]
        for tol in (0.25, 1.0, 3.0):
            out = curve.douglas_peucker(pts, tol)
            self.assertGreaterEqual(len(out), 2)
            worst = max(_point_polyline_distance(p, out) for p in pts)
            self.assertLessEqual(worst, tol + 1e-9,
                                 "tol %.2f exceeded (%.4f)" % (tol, worst))

    def test_larger_tolerance_removes_more(self):
        rng = random.Random(3)
        pts = [(float(i), rng.uniform(-5, 5)) for i in range(200)]
        self.assertGreaterEqual(len(curve.douglas_peucker(pts, 0.5)),
                                len(curve.douglas_peucker(pts, 4.0)))

    def test_short_input_is_returned_unchanged(self):
        self.assertEqual(curve.douglas_peucker([(0, 0)], 1.0), [(0, 0)])
        self.assertEqual(curve.douglas_peucker([(0, 0), (1, 1)], 1.0),
                         [(0, 0), (1, 1)])


class TestCatmullRomAndOffset(unittest.TestCase):

    def test_catmull_rom_interpolates_the_points(self):
        pts = [(0, 0), (10, 10), (20, 0), (30, 10)]
        segs = curve.catmull_rom_to_bezier(pts)
        self.assertEqual(len(segs), 3)
        for i, seg in enumerate(segs):
            self.assertAlmostEqual(seg[0][0], pts[i][0], places=12)
            self.assertAlmostEqual(seg[3][1], pts[i + 1][1], places=12)

    def test_catmull_rom_closed(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        segs = curve.catmull_rom_to_bezier(pts, closed=True)
        self.assertEqual(len(segs), 4)
        self.assertAlmostEqual(segs[-1][3][0], pts[0][0], places=12)

    def test_catmull_rom_two_points(self):
        segs = curve.catmull_rom_to_bezier([(0, 0), (1, 1)])
        self.assertEqual(len(segs), 1)
        self.assertEqual(curve.catmull_rom_to_bezier([(0, 0)]), [])

    def test_offset_of_a_straight_line(self):
        segs = curve.offset_path([LINE], 2.0)
        self.assertGreaterEqual(len(segs), 1)
        for i in range(21):
            _, _, p = curve.point_at_length(
                segs, curve.path_length(segs) * i / 20.0)
            self.assertAlmostEqual(p[1], 2.0, places=4)

    def test_offset_keeps_the_distance_on_a_curve(self):
        segs = curve.offset_path([ARC], 0.2, tol=1e-3)
        self.assertGreaterEqual(len(segs), 1)
        for i in range(41):
            t = i / 40.0
            p = curve.bezier_point(ARC, t)
            d = curve.closest_point_on_path(segs, p)[3]
            self.assertAlmostEqual(d, 0.2, delta=0.02)

    def test_offset_of_nothing(self):
        self.assertEqual(curve.offset_path([], 1.0), [])


class TestClosestPoint(unittest.TestCase):

    def test_closest_point_on_a_straight_cubic(self):
        t, p, d = curve.closest_point_on_bezier(LINE, (1.5, 2.0))
        self.assertAlmostEqual(p[0], 1.5, places=4)
        self.assertAlmostEqual(p[1], 0.0, places=6)
        self.assertAlmostEqual(d, 2.0, places=4)
        self.assertAlmostEqual(t, 0.5, places=4)

    def test_closest_point_clamps_to_the_endpoints(self):
        t, p, d = curve.closest_point_on_bezier(LINE, (-5.0, 0.0))
        self.assertAlmostEqual(t, 0.0, places=6)
        self.assertAlmostEqual(d, 5.0, places=5)

    def test_closest_point_on_a_path_picks_the_segment(self):
        a = curve.line_to_bezier((0, 0), (10, 0))
        b = curve.line_to_bezier((10, 0), (10, 10))
        i, t, p, d = curve.closest_point_on_path([a, b], (11.0, 5.0))
        self.assertEqual(i, 1)
        self.assertAlmostEqual(d, 1.0, places=4)


# ==========================================================================
# nodes and paths
# ==========================================================================

class TestNodeInvariants(unittest.TestCase):

    def test_symmetric_mirrors_the_other_handle(self):
        n = Node((0, 0), (-1, 0), (1, 0), "symmetric")
        n.set_handle_out((0.0, 2.0))
        self.assertAlmostEqual(n.handle_in[0], 0.0)
        self.assertAlmostEqual(n.handle_in[1], -2.0)
        self.assertTrue(n.is_valid())
        n.set_handle_in((3.0, 4.0))
        self.assertAlmostEqual(n.handle_out[0], -3.0)
        self.assertAlmostEqual(n.handle_out[1], -4.0)
        self.assertTrue(n.is_valid())

    def test_smooth_keeps_lengths_but_shares_the_direction(self):
        n = Node((0, 0), (-3, 0), (1, 0), "smooth")
        n.set_handle_out((0.0, 5.0))
        self.assertAlmostEqual(math.hypot(*n.handle_in), 3.0, places=12)
        self.assertAlmostEqual(n.handle_in[0], 0.0, places=12)
        self.assertAlmostEqual(n.handle_in[1], -3.0, places=12)
        self.assertTrue(n.is_valid())

    def test_corner_handles_are_independent(self):
        n = Node((0, 0), (-1, 0), (1, 0), "corner")
        n.set_handle_out((0.0, 5.0))
        self.assertEqual(n.handle_in, (-1.0, 0.0))
        self.assertTrue(n.is_valid())

    def test_set_type_re_establishes_the_constraint(self):
        n = Node((0, 0), (-1.0, 1.0), (4.0, 0.0), "corner")
        self.assertEqual(n.classify(), "corner")
        n.set_type("smooth")
        self.assertTrue(n.is_valid())
        self.assertAlmostEqual(math.hypot(*n.handle_out), 4.0, places=12)
        self.assertAlmostEqual(math.hypot(*n.handle_in), math.sqrt(2.0),
                               places=12)
        n.set_type("symmetric")
        self.assertTrue(n.is_valid())
        self.assertAlmostEqual(n.handle_in[0], -n.handle_out[0], places=12)
        self.assertAlmostEqual(n.handle_in[1], -n.handle_out[1], places=12)

    def test_classify(self):
        self.assertEqual(Node((0, 0), (-1, 0), (1, 0)).classify(),
                         "symmetric")
        self.assertEqual(Node((0, 0), (-2, 0), (1, 0)).classify(), "smooth")
        self.assertEqual(Node((0, 0), (-1, 1), (1, 0)).classify(), "corner")
        self.assertEqual(Node((0, 0), None, (1, 0)).classify(), "corner")

    def test_moving_a_node_keeps_relative_handles(self):
        n = Node((5, 5), (-1, 0), (1, 0), "symmetric")
        n.move(10.0, -3.0)
        self.assertEqual(n.point, (15.0, 2.0))
        self.assertEqual(n.handle_out, (1.0, 0.0))
        self.assertEqual(n.out_point, (16.0, 2.0))

    def test_absolute_handle_setters(self):
        n = Node((10, 10), (-1, 0), (1, 0), "symmetric")
        n.set_out_point((14.0, 10.0))
        self.assertEqual(n.handle_out, (4.0, 0.0))
        self.assertEqual(n.in_point, (6.0, 10.0))

    def test_invariants_survive_a_transform(self):
        for kind in ("smooth", "symmetric"):
            n = Node((1, 2), (-2, 0), (1, 0), kind)
            n.set_type(kind)
            # rotate 37 degrees, then scale non-uniformly
            a = math.radians(37.0)
            rot = ((math.cos(a), -math.sin(a), 0.0),
                   (math.sin(a), math.cos(a), 0.0))
            n.transform(rot)
            self.assertTrue(n.is_valid(1e-9), kind)
            n.transform(((2.0, 0.0, 0.0), (0.0, 0.5, 0.0)))
            self.assertTrue(n.is_valid(1e-9), kind)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            Node((0, 0), type="rounded")
        with self.assertRaises(ValueError):
            Node((0, 0)).set_type("rounded")

    def test_node_json_roundtrip(self):
        n = Node((1.5, -2.0), (-1, 0), None, "corner")
        d = n.to_dict()
        self.assertEqual(set(d), {"point", "in", "out", "type"})
        self.assertIsNone(d["out"])
        self.assertEqual(Node.from_dict(d), n)


class TestPathEditing(unittest.TestCase):

    def _path(self):
        return Path.from_beziers([
            ((0, 0), (0, 10), (20, 10), (20, 0)),
            ((20, 0), (20, -10), (40, -10), (40, 0)),
        ])

    def test_from_beziers_reproduces_the_segments(self):
        segs = [((0, 0), (0, 10), (20, 10), (20, 0)),
                ((20, 0), (20, -10), (40, -10), (40, 0))]
        p = Path.from_beziers(segs)
        self.assertEqual(len(p.nodes), 3)
        self.assertEqual(p.to_beziers(), [tuple(s) for s in segs])

    def test_segment_count(self):
        p = self._path()
        self.assertEqual(p.segment_count(), 2)
        p.closed = True
        self.assertEqual(p.segment_count(), 3)

    def test_insert_node_preserves_the_shape(self):
        p = self._path()
        before = [curve.bezier_point(p.to_beziers()[0], i / 20.0)
                  for i in range(21)]
        idx = p.split_segment(0, 0.5)
        self.assertEqual(idx, 1)
        self.assertEqual(len(p.nodes), 4)
        segs = p.to_beziers()
        for i, q in enumerate(before):
            t = i / 20.0
            src = segs[0] if t <= 0.5 else segs[1]
            u = t * 2.0 if t <= 0.5 else (t - 0.5) * 2.0
            r = curve.bezier_point(src, u)
            self.assertAlmostEqual(r[0], q[0], places=9)
            self.assertAlmostEqual(r[1], q[1], places=9)

    def test_split_segment_out_of_range(self):
        with self.assertRaises(IndexError):
            self._path().split_segment(9, 0.5)

    def test_delete_node(self):
        p = self._path()
        p.delete_node(1)
        self.assertEqual(len(p.nodes), 2)
        self.assertEqual(p.segment_count(), 1)
        with self.assertRaises(IndexError):
            p.delete_node(9)

    def test_close_welds_a_duplicated_endpoint(self):
        p = Path.from_beziers([
            ((0, 0), (0, 5), (5, 5), (5, 0)),
            ((5, 0), (5, -5), (0, -5), (0, 0)),
        ])
        n0 = len(p.nodes)
        p.close()
        self.assertTrue(p.closed)
        self.assertEqual(len(p.nodes), n0 - 1)
        self.assertEqual(p.segment_count(), 2)
        segs = p.to_beziers()
        self.assertEqual(segs[-1][3], segs[0][0])

    def test_close_from_beziers(self):
        p = Path.from_beziers([
            ((0, 0), (0, 5), (5, 5), (5, 0)),
            ((5, 0), (5, -5), (0, -5), (0, 0)),
        ], closed=True)
        self.assertTrue(p.closed)
        self.assertEqual(len(p.nodes), 2)
        self.assertEqual(p.segment_count(), 2)

    def test_open_path(self):
        p = self._path()
        p.close()
        p.open_path()
        self.assertFalse(p.closed)

    def test_split_at_node_makes_two_paths(self):
        p = self._path()
        parts = p.split_at_node(1)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0].nodes), 2)
        self.assertEqual(len(parts[1].nodes), 2)
        self.assertEqual(parts[0].nodes[-1].point, parts[1].nodes[0].point)
        self.assertIsNone(parts[0].nodes[-1].handle_out)

    def test_split_at_an_endpoint_is_a_no_op(self):
        p = self._path()
        self.assertEqual(len(p.split_at_node(0)), 1)
        self.assertEqual(len(p.split_at_node(len(p.nodes) - 1)), 1)

    def test_split_a_closed_path_opens_it(self):
        p = self._path()
        p.closed = True
        n0 = len(p.nodes)
        parts = p.split_at_node(1)
        self.assertEqual(len(parts), 1)
        self.assertFalse(p.closed)
        self.assertEqual(len(p.nodes), n0 + 1)
        self.assertEqual(p.nodes[0].point, p.nodes[-1].point)

    def test_join_welds_matching_endpoints(self):
        a = Path.from_beziers([((0, 0), (0, 5), (5, 5), (5, 0))])
        b = Path.from_beziers([((5, 0), (5, -5), (10, -5), (10, 0))])
        a.join(b)
        self.assertEqual(len(a.nodes), 3)
        self.assertEqual(a.segment_count(), 2)
        self.assertEqual(a.to_beziers()[1][3], (10.0, 0.0))

    def test_join_reverses_when_that_matches_better(self):
        a = Path.from_beziers([((0, 0), (0, 5), (5, 5), (5, 0))])
        b = Path.from_beziers([((10, 0), (10, -5), (5, -5), (5, 0))])
        a.join(b)
        self.assertEqual(len(a.nodes), 3)
        self.assertEqual(a.nodes[-1].point, (10.0, 0.0))

    def test_join_with_an_empty_path(self):
        a = self._path()
        n = len(a.nodes)
        a.join(Path())
        self.assertEqual(len(a.nodes), n)
        empty = Path()
        empty.join(a)
        self.assertEqual(len(empty.nodes), n)

    def test_reverse_swaps_the_handles(self):
        p = self._path()
        pts = [n.point for n in p.nodes]
        segs = p.to_beziers()
        p.reverse()
        self.assertEqual([n.point for n in p.nodes], list(reversed(pts)))
        rsegs = p.to_beziers()
        self.assertEqual(rsegs[0][0], segs[-1][3])
        self.assertEqual(rsegs[0][1], segs[-1][2])
        self.assertEqual(rsegs[-1][3], segs[0][0])

    def test_reverse_keeps_node_invariants(self):
        p = self._path()
        for n in p.nodes:
            if n.handle_in is not None and n.handle_out is not None:
                n.set_type("symmetric")
        p.reverse()
        for n in p.nodes:
            self.assertTrue(n.is_valid())

    def test_transform_translate_scale_rotate(self):
        p = self._path()
        p.translate(5.0, -2.0)
        self.assertEqual(p.nodes[0].point, (5.0, -2.0))
        p.translate(-5.0, 2.0)
        p.scale(2.0)
        self.assertEqual(p.nodes[-1].point, (80.0, 0.0))
        p.scale(0.5)
        p.rotate(math.pi / 2.0)
        self.assertAlmostEqual(p.nodes[-1].point[0], 0.0, places=9)
        self.assertAlmostEqual(p.nodes[-1].point[1], 40.0, places=9)

    def test_bbox_and_length(self):
        p = self._path()
        box = p.bbox()
        self.assertAlmostEqual(box[0], 0.0, places=9)
        self.assertAlmostEqual(box[2], 40.0, places=9)
        self.assertGreater(p.length(), 40.0)
        self.assertIsNone(Path().bbox())

    def test_closest_node_and_handle(self):
        p = self._path()
        i, d = p.closest_node((21.0, 0.0))
        self.assertEqual(i, 1)
        self.assertAlmostEqual(d, 1.0, places=9)
        i, which, d = p.closest_handle((0.0, 10.0))
        self.assertEqual((i, which), (0, "out"))


class TestZOrder(unittest.TestCase):

    def _doc(self):
        d = VectorDocument()
        for i in range(4):
            d.add_path(Path([Node((i, 0)), Node((i + 1, 1))], id="p%d" % i))
        return d

    def test_raise_and_lower(self):
        d = self._doc()
        self.assertEqual(d.raise_path("p0"), 1)
        self.assertEqual([p.id for p in d.paths],
                         ["p1", "p0", "p2", "p3"])
        self.assertEqual(d.lower_path("p0"), 0)
        self.assertEqual([p.id for p in d.paths],
                         ["p0", "p1", "p2", "p3"])

    def test_front_and_back(self):
        d = self._doc()
        d.bring_to_front("p0")
        self.assertEqual(d.paths[-1].id, "p0")
        d.send_to_back("p0")
        self.assertEqual(d.paths[0].id, "p0")

    def test_edges_are_clamped(self):
        d = self._doc()
        self.assertEqual(d.lower_path("p0"), 0)
        self.assertEqual(d.raise_path("p3"), 3)

    def test_remove_and_lookup(self):
        d = self._doc()
        self.assertIsNotNone(d.path_by_id("p2"))
        d.remove_path("p2")
        self.assertIsNone(d.path_by_id("p2"))
        with self.assertRaises(IndexError):
            d.remove_path("nope")


# ==========================================================================
# snapping
# ==========================================================================

class TestSnapping(unittest.TestCase):

    def _doc(self):
        d = VectorDocument()
        d.add_path(Path.from_beziers(
            [((0, 0), (0, 10), (20, 10), (20, 0))], id="p1"))
        return d

    def test_grid_snap(self):
        eng = SnapEngine(SnapSettings(grid_size=5.0, node=False,
                                      midpoint=False, tangent=False,
                                      angle=False, radius=3.0))
        r = eng.snap((11.0, 4.0))
        self.assertEqual(r.kind, "grid")
        self.assertEqual(r.point, (10.0, 5.0))
        self.assertIsNone(eng.snap((12.5, 12.5)).kind)

    def test_node_snap_wins_over_grid(self):
        eng = SnapEngine(SnapSettings(grid_size=5.0, radius=3.0,
                                      midpoint=False, tangent=False,
                                      angle=False))
        r = eng.snap((20.5, 0.4), self._doc())
        self.assertEqual(r.kind, "node")
        self.assertEqual(r.point, (20.0, 0.0))
        self.assertEqual(r.path_id, "p1")
        self.assertEqual(r.node_index, 1)

    def test_midpoint_snap(self):
        d = self._doc()
        mid = curve.bezier_point(d.paths[0].to_beziers()[0], 0.5)
        eng = SnapEngine(SnapSettings(grid=False, node=False, tangent=False,
                                      angle=False, radius=2.0))
        r = eng.snap((mid[0] + 0.3, mid[1] + 0.3), d)
        self.assertEqual(r.kind, "midpoint")
        self.assertAlmostEqual(r.point[0], mid[0], places=9)

    def test_tangent_snap(self):
        d = self._doc()
        eng = SnapEngine(SnapSettings(grid=False, node=False, midpoint=False,
                                      angle=False, radius=2.0))
        # the first node's out handle points straight up from (0, 0)
        r = eng.snap((0.5, 30.0), d)
        self.assertEqual(r.kind, "tangent")
        self.assertAlmostEqual(r.point[0], 0.0, places=9)
        self.assertAlmostEqual(r.point[1], 30.0, places=9)

    def test_angle_snap(self):
        eng = SnapEngine(SnapSettings(grid=False, node=False, midpoint=False,
                                      tangent=False,
                                      angle_step=math.pi / 4.0, radius=2.0))
        r = eng.snap((10.0, 9.0), origin=(0.0, 0.0))
        self.assertEqual(r.kind, "angle")
        self.assertAlmostEqual(r.point[0], r.point[1], places=9)

    def test_angle_snap_needs_an_origin(self):
        eng = SnapEngine(SnapSettings(grid=False, node=False, midpoint=False,
                                      tangent=False))
        self.assertIsNone(eng.snap((10.0, 9.0)).kind)

    def test_snapping_can_be_disabled(self):
        eng = SnapEngine(SnapSettings(enabled=False, grid_size=5.0))
        r = eng.snap((11.0, 4.0))
        self.assertIsNone(r.kind)
        self.assertEqual(r.point, (11.0, 4.0))
        self.assertFalse(r.snapped)

    def test_exclude_a_path(self):
        d = self._doc()
        eng = SnapEngine(SnapSettings(grid=False, radius=3.0))
        self.assertIsNone(eng.snap((20.2, 0.1), d, exclude="p1").kind)

    def test_result_unpacks_as_a_point(self):
        eng = SnapEngine(SnapSettings(grid_size=5.0, radius=3.0))
        x, y = eng.snap((11.0, 4.0))
        self.assertEqual((x, y), (10.0, 5.0))


# ==========================================================================
# §4 JSON
# ==========================================================================

class TestVectorJson(unittest.TestCase):

    def _doc(self):
        d = VectorDocument(Plane((1.0, 2.0, 3.0), (0.0, 0.0, 0.3826834, 0.9238795)),
                           unit_scale=0.001)
        p = Path.from_beziers([((0, 0), (0, 10), (20, 10), (20, 0))],
                              id="p1")
        p.stroke = {"color": [1.0, 0.0, 0.0, 1.0], "width": 0.5}
        p.fill = {"color": [0.0, 0.0, 1.0, 0.25]}
        p.target = "sketch"
        d.add_path(p)
        q = Path([Node((0, 0), None, (1, 0), "corner"),
                  Node((10, 0), (-1, 0), None, "corner")], id="p2")
        q.fill = None
        d.add_path(q)
        return d

    def test_schema_keys(self):
        data = self._doc().to_json()
        self.assertEqual(set(data), {"version", "plane", "unit_scale",
                                     "paths"})
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(data["plane"]), {"origin", "rotation"})
        path = data["paths"][0]
        self.assertEqual(set(path), {"id", "closed", "nodes", "stroke",
                                     "fill", "target"})
        node = path["nodes"][0]
        self.assertEqual(set(node), {"point", "in", "out", "type"})
        self.assertEqual(node["type"], "corner")
        self.assertIn(path["target"], ("draft", "sketch", "annotation"))

    def test_roundtrip_is_exact(self):
        d = self._doc()
        data = d.to_json()
        blob = json.dumps(data)
        back = VectorDocument.from_json(json.loads(blob))
        self.assertEqual(back.to_json(), data)

    def test_roundtrip_from_a_json_string(self):
        d = self._doc()
        back = VectorDocument.from_json(d.dumps())
        self.assertEqual(back.to_json(), d.to_json())

    def test_roundtrip_from_bytes(self):
        d = self._doc()
        back = VectorDocument.from_json(d.dumps().encode("utf-8"))
        self.assertEqual(back.to_json(), d.to_json())

    def test_handles_are_relative_in_the_json(self):
        d = self._doc()
        node = d.to_json()["paths"][1]["nodes"][1]
        self.assertEqual(node["point"], [10.0, 0.0])
        self.assertEqual(node["in"], [-1.0, 0.0])
        self.assertIsNone(node["out"])

    def test_null_fill_survives(self):
        self.assertIsNone(self._doc().to_json()["paths"][1]["fill"])

    def test_geometry_survives_the_roundtrip(self):
        d = self._doc()
        back = VectorDocument.from_json(json.loads(d.dumps()))
        self.assertEqual(back.paths[0].to_beziers(),
                         d.paths[0].to_beziers())

    def test_copy_is_independent(self):
        d = self._doc()
        c = d.copy()
        c.paths[0].nodes[0].set_point((99.0, 99.0))
        self.assertNotEqual(d.paths[0].nodes[0].point,
                            c.paths[0].nodes[0].point)

    def test_plane_projection_roundtrip(self):
        pl = Plane((1.0, 2.0, 3.0), (0.0, 0.3826834, 0.0, 0.9238795))
        for p2 in ((0.0, 0.0), (5.0, -3.0), (-2.5, 7.25)):
            back = pl.to_plane(pl.to_world(p2))
            self.assertAlmostEqual(back[0], p2[0], places=6)
            self.assertAlmostEqual(back[1], p2[1], places=6)

    def test_plane_normal_is_unit(self):
        n = Plane((0, 0, 0), (0.0, 0.3826834, 0.0, 0.9238795)).normal()
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in n)), 1.0,
                               places=6)


class TestFreehandToPath(unittest.TestCase):

    def test_freehand_stroke_becomes_a_clean_path(self):
        pts = [(i * 2.0, math.sin(i * 0.12) * 20.0) for i in range(80)]
        p = vector.path_from_stroke(pts, error=1.0)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(len(p.nodes), 2)
        segs = p.to_beziers()
        worst = max(curve.closest_point_on_path(segs, q)[3] for q in pts)
        self.assertLessEqual(worst, 3.0)

    def test_interior_nodes_are_smooth_and_valid(self):
        pts = [(i * 2.0, math.sin(i * 0.12) * 20.0) for i in range(80)]
        p = vector.path_from_stroke(pts, error=0.2)
        for n in p.nodes:
            self.assertTrue(n.is_valid(1e-6))
        for n in p.nodes[1:-1]:
            if n.handle_in is not None and n.handle_out is not None:
                self.assertIn(n.type, ("smooth", "symmetric"))

    def test_corner_nodes_stay_corners(self):
        pts = ([(float(x), 0.0) for x in range(41)]
               + [(40.0, float(y)) for y in range(1, 41)])
        p = vector.path_from_stroke(pts, error=0.5)
        corner = [n for n in p.nodes
                  if abs(n.point[0] - 40.0) < 1e-6
                  and abs(n.point[1]) < 1e-6]
        self.assertEqual(len(corner), 1)
        self.assertEqual(corner[0].type, "corner")

    def test_document_add_stroke(self):
        d = VectorDocument()
        pts = [(math.cos(t * 0.1) * 30.0, math.sin(t * 0.1) * 30.0)
               for t in range(63)]
        p = d.add_stroke(pts, error=0.5, closed=True)
        self.assertIsNotNone(p)
        self.assertTrue(p.closed)
        self.assertEqual(len(d.paths), 1)
        for n in p.nodes:
            self.assertTrue(n.is_valid(1e-6))

    def test_too_short_a_stroke_is_ignored(self):
        self.assertIsNone(vector.path_from_stroke([(1, 1)]))
        self.assertIsNone(vector.path_from_stroke([(1, 1), (1, 1)]))


# ==========================================================================
# SVG
# ==========================================================================

class TestSvgExport(unittest.TestCase):

    def _doc(self):
        d = VectorDocument()
        p = Path.from_beziers([((0, 0), (10, 30), (40, 30), (50, 0)),
                               ((50, 0), (60, -30), (90, -30), (100, 0))],
                              id="curvy")
        p.stroke = {"color": [1.0, 0.0, 0.0, 1.0], "width": 2.0}
        p.fill = {"color": [0.0, 0.0, 1.0, 0.5]}
        d.add_path(p)
        q = Path([Node((0, 0)), Node((20, 0)), Node((20, 20))],
                 closed=True, id="tri")
        q.stroke = {"color": [0.0, 0.0, 0.0, 1.0], "width": 0.5}
        d.add_path(q)
        return d

    def test_export_is_well_formed_xml(self):
        import xml.etree.ElementTree as ET
        text = svg.export_document(self._doc())
        root = ET.fromstring(text.split("?>", 1)[1])
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("viewBox", root.attrib)
        self.assertTrue(root.get("width").endswith("mm"))

    def test_export_uses_millimetres_from_unit_scale(self):
        d = self._doc()
        d.unit_scale = 0.001
        text = svg.export_document(d, margin=0.0)
        box = d.bbox()
        width = float(text.split('width="', 1)[1].split("mm", 1)[0])
        self.assertAlmostEqual(width, box[2] - box[0], places=6)

    def test_export_then_import_reproduces_the_geometry(self):
        d = self._doc()
        back = svg.import_document(svg.export_document(d))
        self.assertEqual(len(back.paths), len(d.paths))
        for a, b in zip(d.paths, back.paths):
            self.assertEqual(a.closed, b.closed)
            sa = a.to_beziers()
            sb = b.to_beziers()
            self.assertEqual(len(sa), len(sb))
            for seg_a, seg_b in zip(sa, sb):
                for pa, pb in zip(seg_a, seg_b):
                    self.assertAlmostEqual(pa[0], pb[0], places=9)
                    self.assertAlmostEqual(pa[1], pb[1], places=9)

    def test_export_then_import_keeps_the_style(self):
        d = self._doc()
        back = svg.import_document(svg.export_document(d))
        stroke = back.paths[0].stroke
        self.assertAlmostEqual(stroke["color"][0], 1.0, places=2)
        self.assertAlmostEqual(stroke["width"], 2.0, places=9)
        fill = back.paths[0].fill
        self.assertAlmostEqual(fill["color"][2], 1.0, places=2)
        self.assertAlmostEqual(fill["color"][3], 0.5, places=6)
        self.assertIsNone(back.paths[1].fill)

    def test_export_keeps_the_path_ids_and_targets(self):
        d = self._doc()
        d.paths[0].target = "sketch"
        text = svg.export_document(d)
        self.assertIn('id="curvy"', text)
        self.assertIn('data-target="sketch"', text)
        back = svg.import_document(text)
        self.assertEqual(back.paths[0].id, "curvy")
        self.assertEqual(back.paths[0].target, "sketch")

    def test_export_of_an_empty_document(self):
        text = svg.export_document(VectorDocument())
        self.assertIn("<svg", text)

    def test_straight_segments_use_lineto(self):
        d = VectorDocument()
        d.add_path(Path([Node((0, 0)), Node((10, 0))]))
        self.assertIn("L ", svg.export_path_data(d.paths[0]))

    def test_unit_scale_survives_the_roundtrip(self):
        d = self._doc()
        d.unit_scale = 0.01
        back = svg.import_document(svg.export_document(d))
        self.assertAlmostEqual(back.unit_scale, 0.01, places=12)


class TestSvgColors(unittest.TestCase):

    def test_parse_hex_and_names(self):
        self.assertEqual(svg.parse_color("#ff0000"), (1.0, 0.0, 0.0))
        self.assertEqual(svg.parse_color("#f00"), (1.0, 0.0, 0.0))
        self.assertEqual(svg.parse_color("red"), (1.0, 0.0, 0.0))
        self.assertIsNone(svg.parse_color("none"))
        self.assertIsNone(svg.parse_color(None))
        self.assertIsNone(svg.parse_color("wibble"))

    def test_parse_rgb_function(self):
        c = svg.parse_color("rgb(255, 128, 0)")
        self.assertAlmostEqual(c[0], 1.0)
        self.assertAlmostEqual(c[1], 128 / 255.0)
        c = svg.parse_color("rgb(100%, 0%, 50%)")
        self.assertAlmostEqual(c[0], 1.0)
        self.assertAlmostEqual(c[2], 0.5)

    def test_format_color(self):
        self.assertEqual(svg.format_color((1.0, 0.0, 0.5, 1.0)), "#ff0080")


class TestSvgPathData(unittest.TestCase):

    def test_moveto_lineto_absolute_and_relative(self):
        sp = svg.parse_path_data("M 0 0 L 10 0 L 10 10")
        self.assertEqual(len(sp), 1)
        self.assertEqual(len(sp[0]["beziers"]), 2)
        self.assertEqual(sp[0]["beziers"][-1][3], (10.0, 10.0))
        rel = svg.parse_path_data("m 0 0 l 10 0 l 0 10")
        self.assertEqual(rel[0]["beziers"][-1][3], (10.0, 10.0))

    def test_implicit_lineto_after_moveto(self):
        sp = svg.parse_path_data("M 0 0 10 0 20 0")
        self.assertEqual(len(sp[0]["beziers"]), 2)
        self.assertEqual(sp[0]["beziers"][-1][3], (20.0, 0.0))

    def test_horizontal_and_vertical(self):
        sp = svg.parse_path_data("M 0 0 H 10 V 5 h -4 v -2")
        pts = [b[3] for b in sp[0]["beziers"]]
        self.assertEqual(pts, [(10.0, 0.0), (10.0, 5.0), (6.0, 5.0),
                               (6.0, 3.0)])

    def test_closepath(self):
        sp = svg.parse_path_data("M 0 0 L 10 0 L 10 10 Z")
        self.assertTrue(sp[0]["closed"])
        self.assertEqual(len(sp[0]["beziers"]), 3)
        self.assertEqual(sp[0]["beziers"][-1][3], (0.0, 0.0))

    def test_multiple_subpaths(self):
        sp = svg.parse_path_data("M 0 0 L 1 0 Z M 5 5 L 6 5")
        self.assertEqual(len(sp), 2)
        self.assertTrue(sp[0]["closed"])
        self.assertFalse(sp[1]["closed"])

    def test_cubic_absolute_and_relative(self):
        a = svg.parse_path_data("M 0 0 C 0 10 10 10 10 0")[0]["beziers"][0]
        b = svg.parse_path_data("m 0 0 c 0 10 10 10 10 0")[0]["beziers"][0]
        self.assertEqual(a, b)
        self.assertEqual(a[1], (0.0, 10.0))
        self.assertEqual(a[3], (10.0, 0.0))

    def test_smooth_cubic_reflects_the_control_point(self):
        sp = svg.parse_path_data("M 0 0 C 1 1 2 1 3 0 S 5 -1 6 0")
        b = sp[0]["beziers"][1]
        # reflected control point of (2, 1) about (3, 0)
        self.assertEqual(b[1], (4.0, -1.0))
        self.assertEqual(b[2], (5.0, -1.0))

    def test_smooth_cubic_without_a_predecessor(self):
        sp = svg.parse_path_data("M 0 0 S 5 -1 6 0")
        self.assertEqual(sp[0]["beziers"][0][1], (0.0, 0.0))

    def test_quadratic_becomes_a_cubic(self):
        sp = svg.parse_path_data("M 0 0 Q 10 20 20 0")
        b = sp[0]["beziers"][0]
        # c1 = p0 + 2/3 (q - p0)
        self.assertAlmostEqual(b[1][0], 20.0 / 3.0, places=12)
        self.assertAlmostEqual(b[1][1], 40.0 / 3.0, places=12)
        self.assertEqual(b[3], (20.0, 0.0))
        # the midpoint matches the quadratic's own midpoint
        mid = curve.bezier_point(b, 0.5)
        self.assertAlmostEqual(mid[0], 10.0, places=12)
        self.assertAlmostEqual(mid[1], 10.0, places=12)

    def test_smooth_quadratic_reflects(self):
        sp = svg.parse_path_data("M 0 0 Q 10 20 20 0 T 40 0")
        self.assertEqual(len(sp[0]["beziers"]), 2)
        mid = curve.bezier_point(sp[0]["beziers"][1], 0.5)
        self.assertAlmostEqual(mid[1], -10.0, places=9)

    def test_arc_makes_a_circle(self):
        sp = svg.parse_path_data(
            "M -10 0 A 10 10 0 1 0 10 0 A 10 10 0 1 0 -10 0 Z")
        self.assertTrue(sp[0]["closed"])
        pts = curve.flatten_path(sp[0]["beziers"], 0.01)
        for p in pts:
            self.assertAlmostEqual(math.hypot(p[0], p[1]), 10.0, delta=0.02)

    def test_arc_endpoints_are_exact(self):
        sp = svg.parse_path_data("M 0 0 A 5 3 30 0 1 10 4")
        beziers = sp[0]["beziers"]
        self.assertEqual(beziers[0][0], (0.0, 0.0))
        self.assertAlmostEqual(beziers[-1][3][0], 10.0, places=9)
        self.assertAlmostEqual(beziers[-1][3][1], 4.0, places=9)

    def test_arc_flags_change_the_side(self):
        small = svg.parse_path_data("M 0 0 A 10 10 0 0 1 10 10")[0]["beziers"]
        large = svg.parse_path_data("M 0 0 A 10 10 0 1 1 10 10")[0]["beziers"]
        self.assertLess(curve.path_length(small), curve.path_length(large))
        sweep0 = svg.parse_path_data("M 0 0 A 10 10 0 0 0 10 10")[0]["beziers"]
        m1 = curve.bezier_point(small[len(small) // 2], 0.5)
        m0 = curve.bezier_point(sweep0[len(sweep0) // 2], 0.5)
        self.assertNotAlmostEqual(m0[0], m1[0], places=3)

    def test_arc_relative(self):
        a = svg.parse_path_data("M 5 5 A 4 4 0 0 1 13 5")[0]["beziers"]
        b = svg.parse_path_data("m 5 5 a 4 4 0 0 1 8 0")[0]["beziers"]
        self.assertEqual(len(a), len(b))
        self.assertAlmostEqual(a[-1][3][0], b[-1][3][0], places=9)

    def test_degenerate_arc_becomes_a_line(self):
        sp = svg.parse_path_data("M 0 0 A 0 0 0 0 1 10 0")
        self.assertEqual(len(sp[0]["beziers"]), 1)
        self.assertEqual(sp[0]["beziers"][0][3], (10.0, 0.0))

    def test_arc_radii_are_scaled_up_when_too_small(self):
        sp = svg.parse_path_data("M 0 0 A 1 1 0 0 1 10 0")
        pts = curve.flatten_path(sp[0]["beziers"], 0.01)
        self.assertAlmostEqual(pts[-1][0], 10.0, places=6)

    def test_scientific_notation_and_compact_numbers(self):
        sp = svg.parse_path_data("M0,0L1e1,.5")
        self.assertEqual(sp[0]["beziers"][0][3], (10.0, 0.5))

    def test_negative_numbers_without_separators(self):
        sp = svg.parse_path_data("M0 0L-10-5")
        self.assertEqual(sp[0]["beziers"][0][3], (-10.0, -5.0))

    def test_flip_y(self):
        sp = svg.parse_path_data("M 0 0 L 10 5", flip_y=True)
        self.assertEqual(sp[0]["beziers"][0][3], (10.0, -5.0))

    def test_empty_and_garbage_input(self):
        self.assertEqual(svg.parse_path_data(""), [])
        self.assertEqual(svg.parse_path_data("   "), [])
        self.assertEqual(svg.parse_path_data("Q"), [])


class TestSvgImport(unittest.TestCase):

    def _svg(self, body, extra=""):
        return ('<svg xmlns="http://www.w3.org/2000/svg" %s>%s</svg>'
                % (extra, body))

    def test_import_basic_shapes(self):
        doc = svg.import_document(self._svg(
            '<rect x="0" y="0" width="10" height="5"/>'
            '<line x1="0" y1="0" x2="4" y2="4" stroke="black"/>'
            '<polyline points="0,0 1,1 2,0" stroke="black"/>'
            '<polygon points="0,0 5,0 5,5" stroke="black"/>'
            '<circle cx="0" cy="0" r="4" stroke="black"/>'
            '<ellipse cx="0" cy="0" rx="4" ry="2" stroke="black"/>'))
        self.assertEqual(len(doc.paths), 6)

    def test_import_applies_group_transforms(self):
        doc = svg.import_document(self._svg(
            '<g transform="translate(10 20)">'
            '<path d="M 0 0 L 5 0" stroke="black"/></g>'), flip_y=False)
        self.assertEqual(doc.paths[0].nodes[0].point, (10.0, 20.0))

    def test_import_applies_scale_and_matrix(self):
        doc = svg.import_document(self._svg(
            '<path transform="scale(2 3)" d="M 1 1 L 2 2" stroke="black"/>'),
            flip_y=False)
        self.assertEqual(doc.paths[0].nodes[0].point, (2.0, 3.0))
        doc = svg.import_document(self._svg(
            '<path transform="matrix(1 0 0 1 5 6)" d="M 0 0 L 1 0"'
            ' stroke="black"/>'), flip_y=False)
        self.assertEqual(doc.paths[0].nodes[0].point, (5.0, 6.0))

    def test_import_reads_style_attributes(self):
        doc = svg.import_document(self._svg(
            '<path d="M 0 0 L 5 0" style="stroke:#00ff00;stroke-width:3"/>'))
        self.assertAlmostEqual(doc.paths[0].stroke["color"][1], 1.0, places=2)
        self.assertAlmostEqual(doc.paths[0].stroke["width"], 3.0)

    def test_import_splits_subpaths(self):
        doc = svg.import_document(self._svg(
            '<path id="two" d="M 0 0 L 1 0 Z M 5 5 L 6 5" stroke="black"/>'))
        self.assertEqual(len(doc.paths), 2)
        self.assertEqual(doc.paths[0].id, "two_0")

    def test_import_from_bytes(self):
        doc = svg.import_document(
            self._svg('<path d="M 0 0 L 1 0"/>').encode("utf-8"))
        self.assertEqual(len(doc.paths), 1)

    def test_import_of_an_empty_svg(self):
        self.assertEqual(len(svg.import_document(self._svg("")).paths), 0)


# ==========================================================================
# FreeCAD commit layer
# ==========================================================================

class TestToFreeCad(unittest.TestCase):

    def test_degrades_with_a_clear_message(self):
        if to_freecad.is_available():
            self.skipTest("FreeCAD is available in this interpreter")
        reason = to_freecad.missing_reason()
        self.assertIsInstance(reason, str)
        self.assertIn("FreeCAD", reason)
        d = VectorDocument()
        d.add_path(Path([Node((0, 0)), Node((1, 0))]))
        with self.assertRaises(RuntimeError) as ctx:
            to_freecad.commit(d)
        self.assertIn("commit()", str(ctx.exception))

    def test_polyline_detection(self):
        straight = Path.from_beziers(
            [curve.line_to_bezier((0, 0), (10, 0)),
             curve.line_to_bezier((10, 0), (10, 10))])
        self.assertTrue(to_freecad.is_polyline(straight))
        curved = Path.from_beziers([((0, 0), (0, 10), (10, 10), (10, 0))])
        self.assertFalse(to_freecad.is_polyline(curved))
        self.assertTrue(to_freecad.is_polyline(Path()))

    def test_path_to_points_on_the_default_plane(self):
        p = Path([Node((0, 0)), Node((10, 0)), Node((10, 5))])
        pts = to_freecad.path_to_points(p, Plane(), 1.0)
        self.assertEqual(pts, [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                               (10.0, 5.0, 0.0)])

    def test_path_to_points_flattens_curves(self):
        p = Path.from_beziers([((0, 0), (0, 10), (20, 10), (20, 0))])
        pts = to_freecad.path_to_points(p, Plane(), 1.0, flatten_tol=0.05)
        self.assertGreater(len(pts), 4)
        for x, y, z in pts:
            self.assertEqual(z, 0.0)

    def test_path_to_points_honours_the_scale(self):
        p = Path([Node((0, 0)), Node((10, 0))])
        pts = to_freecad.path_to_points(p, Plane(), 2.0)
        self.assertEqual(pts[-1], (20.0, 0.0, 0.0))

    def test_path_to_points_on_a_rotated_plane(self):
        # 90 degrees about X: the document's +y becomes the world's +z
        pl = Plane((0, 0, 0), (math.sin(math.pi / 4), 0.0, 0.0,
                               math.cos(math.pi / 4)))
        p = Path([Node((0, 0)), Node((0, 10))])
        pts = to_freecad.path_to_points(p, pl, 1.0)
        self.assertAlmostEqual(pts[1][2], 10.0, places=6)
        self.assertAlmostEqual(pts[1][1], 0.0, places=6)

    def test_commit_result_container(self):
        r = to_freecad.CommitResult(["a", "b"])
        self.assertEqual(len(r), 2)
        self.assertEqual(list(r), ["a", "b"])
        self.assertIn("2 objects", repr(r))

    def test_commit_strokes_needs_freecad(self):
        if to_freecad.is_available():
            self.skipTest("FreeCAD is available in this interpreter")
        with self.assertRaises(RuntimeError):
            to_freecad.commit_strokes3d([])


# ==========================================================================
# vector-side UI pieces
# ==========================================================================

class TestNodeGizmo(unittest.TestCase):

    def _path(self):
        return Path.from_beziers([((0, 0), (0, 10), (20, 10), (20, 0))])

    def test_geometry_lists_nodes_and_handles(self):
        g = ui.NodeGizmoModel().geometry(self._path())
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(len(g["handles"]), 2)
        self.assertEqual(len(g["lines"]), 2)
        self.assertEqual(g["nodes"][0]["point"], (0.0, 0.0))

    def test_geometry_of_nothing(self):
        g = ui.NodeGizmoModel().geometry(None)
        self.assertEqual(g["nodes"], [])

    def test_pick_prefers_handles(self):
        p = self._path()
        gz = ui.NodeGizmoModel(1.0)
        self.assertEqual(gz.pick(p, (0.0, 10.0)), ("handle", 0, "out"))
        self.assertEqual(gz.pick(p, (0.0, 0.0)), ("node", 0))
        hit = gz.pick(p, curve.bezier_point(p.to_beziers()[0], 0.5))
        self.assertEqual(hit[0], "segment")
        self.assertIsNone(gz.pick(p, (100.0, 100.0)))
        self.assertIsNone(gz.pick(None, (0.0, 0.0)))

    def test_drag_moves_a_node(self):
        p = self._path()
        gz = ui.NodeGizmoModel(1.0)
        self.assertTrue(gz.drag(p, ("node", 0), (5.0, 5.0)))
        self.assertEqual(p.nodes[0].point, (5.0, 5.0))

    def test_drag_a_handle_respects_the_node_type(self):
        p = self._path()
        p.nodes[0].handle_in = (0.0, -10.0)
        p.nodes[0].set_type("symmetric")
        gz = ui.NodeGizmoModel(1.0)
        self.assertTrue(gz.drag(p, ("handle", 0, "out"), (4.0, 0.0)))
        self.assertEqual(p.nodes[0].handle_out, (4.0, 0.0))
        self.assertEqual(p.nodes[0].handle_in, (-4.0, 0.0))
        self.assertTrue(p.nodes[0].is_valid())

    def test_drag_of_a_segment_is_ignored(self):
        p = self._path()
        gz = ui.NodeGizmoModel(1.0)
        self.assertFalse(gz.drag(p, ("segment", 0, 0.5), (1.0, 1.0)))
        self.assertFalse(gz.drag(p, None, (1.0, 1.0)))


class TestVectorSessionInteraction(unittest.TestCase):

    def _session(self):
        from xrpaint.session import PaintSession
        s = PaintSession(mode="VECTOR")
        s.ensure_vector_document()
        return s

    def test_pen_tool_builds_a_path(self):
        s = self._session()
        s.ui.vector_tool = "pen"
        s.ui.snap_enabled = False
        for x, y in ((0, 0), (10, 0), (10, 10)):
            s.on_trigger(0, 1.0, position=(x, y, 0))
            s.on_trigger(0, 0.0, position=(x, y, 0))
        path = s.vector_document.paths[0]
        self.assertEqual(len(path.nodes), 3)
        self.assertEqual(path.nodes[1].point, (10.0, 0.0))

    def test_pen_drag_sets_a_symmetric_handle(self):
        s = self._session()
        s.ui.vector_tool = "pen"
        s.ui.snap_enabled = False
        s.on_trigger(0, 1.0, position=(0, 0, 0))
        s.on_move(0, position=(3, 0, 0))
        s.on_trigger(0, 0.0, position=(3, 0, 0))
        node = s.vector_document.paths[0].nodes[0]
        self.assertEqual(node.type, "symmetric")
        self.assertEqual(node.handle_out, (3.0, 0.0))
        self.assertEqual(node.handle_in, (-3.0, 0.0))
        self.assertTrue(node.is_valid())

    def test_pen_closes_on_the_first_node(self):
        s = self._session()
        s.ui.vector_tool = "pen"
        s.ui.snap_enabled = False
        for x, y in ((0, 0), (10, 0), (10, 10), (0, 10)):
            s.on_trigger(0, 1.0, position=(x, y, 0))
            s.on_trigger(0, 0.0, position=(x, y, 0))
        s.on_trigger(0, 1.0, position=(0, 0, 0))
        s.on_trigger(0, 0.0, position=(0, 0, 0))
        self.assertTrue(s.vector_document.paths[0].closed)

    def test_select_then_node_edit(self):
        s = self._session()
        doc = s.vector_document
        doc.add_path(Path.from_beziers([((0, 0), (0, 10), (20, 10),
                                         (20, 0))], id="edit"))
        s.ui.vector_tool = "select"
        s.on_trigger(0, 1.0, position=(10, 7.5, 0))
        s.on_trigger(0, 0.0, position=(10, 7.5, 0))
        self.assertEqual(s.ui.selected_path, "edit")
        s.ui.vector_tool = "node"
        s.ui.snap_enabled = False
        s.on_trigger(0, 1.0, position=(0, 0, 0))
        s.on_move(0, position=(3, -3, 0))
        s.on_trigger(0, 0.0, position=(3, -3, 0))
        self.assertEqual(doc.paths[0].nodes[0].point, (3.0, -3.0))

    def test_freehand_closes_a_loop(self):
        s = self._session()
        s.ui.vector_tool = "draw"
        pts = [(math.cos(t * 0.2) * 20.0, math.sin(t * 0.2) * 20.0)
               for t in range(32)]
        pts.append(pts[0])
        s.on_trigger(0, 1.0, position=(pts[0][0], pts[0][1], 0))
        for p in pts[1:]:
            s.on_move(0, position=(p[0], p[1], 0))
        s.on_trigger(0, 0.0, position=(pts[-1][0], pts[-1][1], 0))
        self.assertTrue(s.vector_document.paths[0].closed)

    def test_svg_export_import_through_the_session(self):
        s = self._session()
        s.vector_document.add_path(
            Path.from_beziers([((0, 0), (0, 10), (20, 10), (20, 0))]))
        text = s.export_svg()
        self.assertIn("<path", text)
        s.import_svg(text)
        self.assertEqual(len(s.vector_document.paths), 1)

    def test_commit_vector_reports_the_missing_workbench(self):
        if to_freecad.is_available():
            self.skipTest("FreeCAD is available in this interpreter")
        s = self._session()
        s.vector_document.add_path(Path([Node((0, 0)), Node((1, 0))]))
        self.assertIsNone(s.commit_vector())
        self.assertTrue(any("Draft" in m or "FreeCAD" in m
                            for m in s.messages))

    def test_vector_document_is_settable(self):
        from xrpaint.session import PaintSession
        s = PaintSession()
        doc = VectorDocument()
        s.vector_document = doc
        self.assertIs(s.vector_document, doc)
        self.assertIs(s.ensure_vector_document(), doc)


if __name__ == "__main__":
    unittest.main()
