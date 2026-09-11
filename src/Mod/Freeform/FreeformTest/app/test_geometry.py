# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Tests for the pure geometry algorithms."""

import math
import random
import unittest

import FreeCAD
import Part
from FreeCAD import Vector

from freeform import geometry


def _circle_points(radius, count, z=0.0, start=0.0, sweep=2 * math.pi, noise=0.0, seed=0):
    rng = random.Random(seed)
    pts = []
    for i in range(count):
        a = start + sweep * i / float(count - 1 if sweep < 2 * math.pi else count)
        r = radius + rng.uniform(-noise, noise)
        pts.append(Vector(r * math.cos(a), r * math.sin(a), z))
    return pts


class TestStrokeConditioning(unittest.TestCase):
    def setUp(self):
        self.zigzag = [Vector(i, (-1) ** i, 0) for i in range(11)]

    def test_remove_duplicates(self):
        pts = [
            Vector(0, 0, 0),
            Vector(0, 0, 0),
            Vector(1, 0, 0),
            Vector(1, 0, 1e-9),
            Vector(2, 0, 0),
        ]
        self.assertEqual(len(geometry.remove_duplicates(pts)), 3)

    def test_polyline_length(self):
        pts = [Vector(0, 0, 0), Vector(3, 0, 0), Vector(3, 4, 0)]
        self.assertAlmostEqual(geometry.polyline_length(pts), 7.0)
        self.assertAlmostEqual(geometry.polyline_length(pts, closed=True), 12.0)

    def test_smooth_keeps_ends_and_reduces_wobble(self):
        smoothed = geometry.smooth_points(self.zigzag, iterations=3)
        self.assertEqual(len(smoothed), len(self.zigzag))
        self.assertEqual(smoothed[0], self.zigzag[0])
        self.assertEqual(smoothed[-1], self.zigzag[-1])
        wobble_before = max(abs(p.y) for p in self.zigzag[1:-1])
        wobble_after = max(abs(p.y) for p in smoothed[1:-1])
        self.assertLess(wobble_after, wobble_before * 0.5)

    def test_smooth_closed_moves_every_point(self):
        pts = _circle_points(5, 12)
        smoothed = geometry.smooth_points(pts, iterations=1, closed=True)
        self.assertTrue(all((p.Length < 5.0) for p in smoothed))

    def test_chaikin(self):
        pts = [Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0)]
        out = geometry.chaikin(pts, iterations=1)
        self.assertEqual(len(out), 6)
        self.assertEqual(out[0], pts[0])
        self.assertEqual(out[-1], pts[-1])
        out2 = geometry.chaikin(pts, iterations=2, closed=True)
        self.assertEqual(len(out2), 12)

    def test_simplify(self):
        pts = [Vector(i, 0.01 * math.sin(i), 0) for i in range(50)]
        pts[25] = Vector(25, 5, 0)  # one real corner
        out = geometry.simplify_points(pts, tolerance=0.1)
        # flat run, spike, flat run: ends, the spike and its two neighbours survive
        self.assertEqual(len(out), 5)
        self.assertEqual(out[2], pts[25])
        self.assertEqual(len(geometry.simplify_points(pts, tolerance=0)), 50)

    def test_resample(self):
        pts = [Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0)]
        out = geometry.resample_points(pts, count=5)
        self.assertEqual(len(out), 5)
        self.assertAlmostEqual((out[1] - out[0]).Length, 5.0)
        self.assertEqual(out[-1], pts[-1])
        out = geometry.resample_points(pts, spacing=2.5)
        self.assertEqual(len(out), 9)
        closed = geometry.resample_points(_circle_points(1, 8), count=16, closed=True)
        self.assertEqual(len(closed), 15)
        with self.assertRaises(ValueError):
            geometry.resample_points(pts)

    def test_condition_stroke(self):
        pts = [Vector(i, (-1) ** i * 0.2, 0) for i in range(30)]
        out = geometry.condition_stroke(pts, smoothing=3, tolerance=0.3)
        self.assertLess(len(out), 10)
        self.assertEqual(out[0], pts[0])
        self.assertEqual(out[-1], pts[-1])


class TestShapeRecognition(unittest.TestCase):
    def test_fit_line(self):
        pts = [Vector(i, 2 * i, 3 * i) for i in range(10)]
        origin, direction, deviation = geometry.fit_line(pts)
        self.assertAlmostEqual(deviation, 0.0, places=9)
        expected = Vector(1, 2, 3).normalize()
        self.assertAlmostEqual(direction.dot(expected), 1.0, places=9)
        self.assertAlmostEqual((origin - Vector(4.5, 9, 13.5)).Length, 0.0, places=9)

    def test_fit_plane(self):
        pts = [Vector(x, y, 2 * x - y + 1) for x in range(4) for y in range(4)]
        _, normal, deviation = geometry.fit_plane(pts)
        self.assertAlmostEqual(deviation, 0.0, places=9)
        expected = Vector(-2, 1, 1).normalize()
        self.assertAlmostEqual(abs(normal.dot(expected)), 1.0, places=9)

    def test_fit_circle_tilted(self):
        placement = FreeCAD.Placement(Vector(1, 2, 3), FreeCAD.Rotation(Vector(1, 1, 0), 40))
        pts = [placement.multVec(p) for p in _circle_points(3, 30)]
        fit = geometry.fit_circle(pts)
        self.assertAlmostEqual(fit["radius"], 3.0, places=6)
        self.assertAlmostEqual((fit["center"] - Vector(1, 2, 3)).Length, 0.0, places=6)
        axis = placement.Rotation.multVec(Vector(0, 0, 1))
        self.assertAlmostEqual(abs(fit["normal"].dot(axis)), 1.0, places=6)
        self.assertGreater(fit["coverage"], 1.9 * math.pi)

    def test_recognize_line(self):
        pts = [Vector(i, 0.02 * math.sin(i), 0.01) for i in range(20)]
        kind, (start, end) = geometry.recognize_stroke(pts)
        self.assertEqual(kind, "line")
        self.assertAlmostEqual((end - start).Length, 19.0, places=1)

    def test_recognize_two_points(self):
        kind, data = geometry.recognize_stroke([Vector(0, 0, 0), Vector(1, 1, 1)])
        self.assertEqual(kind, "line")
        self.assertEqual(data[1], Vector(1, 1, 1))

    def test_recognize_circle(self):
        pts = _circle_points(5, 40, z=2, noise=0.05, seed=3)
        kind, (center, normal, radius) = geometry.recognize_stroke(pts)
        self.assertEqual(kind, "circle")
        self.assertAlmostEqual(radius, 5.0, places=1)
        self.assertAlmostEqual((center - Vector(0, 0, 2)).Length, 0.0, places=1)
        self.assertAlmostEqual(abs(normal.z), 1.0, places=6)

    def test_recognize_arc(self):
        pts = _circle_points(5, 30, sweep=math.pi / 2)
        kind, (start, mid, end) = geometry.recognize_stroke(pts)
        self.assertEqual(kind, "arc")
        for p in (start, mid, end):
            self.assertAlmostEqual(p.Length, 5.0, places=6)
        arc = Part.ArcOfCircle(start, mid, end)
        self.assertAlmostEqual(arc.Radius, 5.0, places=6)
        self.assertAlmostEqual(arc.LastParameter - arc.FirstParameter, math.pi / 2, places=3)

    def test_recognize_rejects_freeform(self):
        pts = [Vector(i, 3 * math.sin(i * 0.7), 0) for i in range(30)]
        kind, data = geometry.recognize_stroke(pts)
        self.assertIsNone(kind)
        self.assertIsNone(data)

    def test_recognize_tolerance(self):
        pts = [Vector(i, 0.5 * math.sin(i), 0) for i in range(20)]
        self.assertIsNone(geometry.recognize_stroke(pts, tolerance=0.1)[0])
        self.assertEqual(geometry.recognize_stroke(pts, tolerance=1.0)[0], "line")


class TestSymmetry(unittest.TestCase):
    def test_mirror_point(self):
        p = geometry.mirror_point(Vector(3, 1, 2))
        self.assertEqual(p, Vector(-3, 1, 2))
        p = geometry.mirror_point(Vector(3, 1, 2), Vector(1, 0, 0), Vector(2, 0, 0))
        self.assertEqual(p, Vector(-1, 1, 2))

    def test_mirror_points_is_involution(self):
        pts = [Vector(1, 2, 3), Vector(-4, 5, 6)]
        origin, normal = Vector(1, 1, 1), Vector(1, 1, 0)
        back = geometry.mirror_points(geometry.mirror_points(pts, origin, normal), origin, normal)
        for a, b in zip(pts, back):
            self.assertAlmostEqual((a - b).Length, 0.0, places=9)


class TestSubdivision(unittest.TestCase):
    def test_weld_points(self):
        pts = [
            Vector(0, 0, 0),
            Vector(1, 0, 0),
            Vector(1, 1, 0),
            Vector(0, 0, 0),
            Vector(1, 1, 0),
            Vector(0, 1, 0),
        ]
        faces = [[0, 1, 2], [3, 4, 5]]
        welded, new_faces = geometry.weld_points(pts, faces)
        self.assertEqual(len(welded), 4)
        self.assertEqual(new_faces, [[0, 1, 2], [0, 2, 3]])

    def test_catmull_clark_cube(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(2, 2, 2))
        self.assertEqual(len(points), 8)
        self.assertEqual([len(f) for f in faces], [4] * 6)
        out_points, out_faces = geometry.catmull_clark(points, faces, 1)
        # 8 moved vertices + 6 face points + 12 edge points
        self.assertEqual(len(out_points), 26)
        self.assertEqual(len(out_faces), 24)
        self.assertTrue(all(len(f) == 4 for f in out_faces))
        # the surface shrinks towards the inside of the cube and stays symmetric
        for p in out_points:
            for c in (p.x, p.y, p.z):
                self.assertGreaterEqual(c, -1e-9)
                self.assertLessEqual(c, 2 + 1e-9)
        centroid = Vector()
        for p in out_points:
            centroid += p
        centroid *= 1.0 / len(out_points)
        self.assertAlmostEqual((centroid - Vector(1, 1, 1)).Length, 0.0, places=9)

    def test_catmull_clark_closed_mesh_stays_closed(self):
        import Mesh

        points, faces = geometry.polygons_from_shape(Part.makeBox(1, 2, 3))
        points, faces = geometry.catmull_clark(points, faces, 2)
        flat = [points[i] for tri in geometry.triangulate_polygons(faces) for i in tri]
        mesh = Mesh.Mesh(flat)
        self.assertTrue(mesh.isSolid())
        self.assertEqual(mesh.CountFacets, 6 * 16 * 2)
        self.assertLess(mesh.Volume, 6.0)
        self.assertGreater(mesh.Volume, 2.0)

    def test_catmull_clark_open_surface_boundary(self):
        points = [
            Vector(0, 0, 0),
            Vector(2, 0, 0),
            Vector(2, 2, 0),
            Vector(0, 2, 0),
            Vector(1, 1, 1),
        ]
        faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
        fixed, _ = geometry.catmull_clark(points, faces, 1, keep_boundary=True)
        for corner in points[:4]:
            self.assertTrue(any((p - corner).Length < 1e-9 for p in fixed))
        creased, _ = geometry.catmull_clark(points, faces, 1, keep_boundary=False)
        self.assertFalse(any((p - points[0]).Length < 1e-9 for p in creased))
        # boundary edge points stay on the z = 0 rim in both cases
        self.assertTrue(
            any(abs(p.z) < 1e-9 and (p - Vector(1, 0, 0)).Length < 1e-9 for p in creased)
        )

    def test_polygons_from_curved_shape(self):
        points, faces = geometry.polygons_from_shape(Part.makeCylinder(1, 2))
        self.assertGreater(len(faces), 6)
        self.assertTrue(all(len(f) in (3, 4) for f in faces))
