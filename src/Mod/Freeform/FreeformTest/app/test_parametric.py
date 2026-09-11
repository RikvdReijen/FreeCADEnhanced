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

"""Tests for the parametric (Grasshopper style) algorithms."""

import math
import random
import unittest

import Part
from FreeCAD import Vector

from freeform import features, geometry, parametric


def _polygon_area(poly):
    return 0.5 * abs(
        sum(
            poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly))
        )
    )


class TestNumbers(unittest.TestCase):
    def test_remap(self):
        self.assertAlmostEqual(parametric.remap(5, 0, 10, 0, 100), 50.0)
        self.assertAlmostEqual(parametric.remap(20, 0, 10, 0, 100), 100.0)
        self.assertAlmostEqual(parametric.remap(20, 0, 10, 0, 100, clamp=False), 200.0)
        self.assertAlmostEqual(parametric.remap(3, 5, 5, 0, 1), 0.0)

    def test_attractor_factor(self):
        origin = [Vector(0, 0, 0)]
        self.assertAlmostEqual(parametric.attractor_factor(Vector(0, 0, 0), origin, 10), 0.2)
        self.assertAlmostEqual(parametric.attractor_factor(Vector(5, 0, 0), origin, 10), 0.6)
        self.assertAlmostEqual(parametric.attractor_factor(Vector(50, 0, 0), origin, 10), 1.0)
        self.assertAlmostEqual(parametric.attractor_factor(Vector(5, 0, 0), [], 10), 1.0)
        smooth = parametric.attractor_factor(Vector(2, 0, 0), origin, 10, falloff="smooth")
        linear = parametric.attractor_factor(Vector(2, 0, 0), origin, 10)
        self.assertLess(smooth, linear)
        inverse = parametric.attractor_factor(Vector(10, 0, 0), origin, 10, falloff="inverse")
        self.assertAlmostEqual(inverse, 1.0)

    def test_expression(self):
        self.assertAlmostEqual(parametric.evaluate_expression("10*sin(t)+pi", t=0), math.pi)
        self.assertAlmostEqual(parametric.evaluate_expression("pow(a, 2) + abs(b)", a=3, b=-1), 10)
        with self.assertRaises(ValueError):
            parametric.evaluate_expression("__import__('os').getcwd()")
        with self.assertRaises(ValueError):
            parametric.evaluate_expression("open('x')")
        with self.assertRaises(ValueError):
            parametric.evaluate_expression("1 +", t=1)
        pts = parametric.expression_points("cos(t)", "sin(t)", "t", 0, 2 * math.pi, 9)
        self.assertEqual(len(pts), 9)
        self.assertAlmostEqual(pts[0].x, 1.0)
        self.assertAlmostEqual(pts[-1].z, 2 * math.pi)


class TestFrames(unittest.TestCase):
    def test_frames_along_polyline(self):
        wire = features.build_curve(
            [Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0)], degree=1
        )
        frames = parametric.frames_along_wire(wire, count=5)
        self.assertEqual(len(frames), 5)
        point, tangent, normal, binormal = frames[0]
        self.assertEqual(point, Vector(0, 0, 0))
        self.assertAlmostEqual(tangent.x, 1.0)
        self.assertAlmostEqual(normal.z, 1.0)
        self.assertAlmostEqual(abs(binormal.y), 1.0)
        self.assertAlmostEqual((frames[-1][0] - Vector(10, 10, 0)).Length, 0.0, places=6)
        # orthonormal
        for _, t, n, b in frames:
            self.assertAlmostEqual(t.dot(n), 0.0, places=9)
            self.assertAlmostEqual(t.cross(n).dot(b), 1.0, places=9)

    def test_frames_spacing_and_closed(self):
        wire = features.build_curve([Vector(0, 0, 0), Vector(20, 0, 0)])
        self.assertEqual(len(parametric.frames_along_wire(wire, spacing=5.0)), 5)
        ring = features.build_curve(
            [
                Vector(10 * math.cos(a), 10 * math.sin(a), 0)
                for a in [i * math.pi / 8 for i in range(16)]
            ],
            closed=True,
        )
        self.assertEqual(len(parametric.frames_along_wire(ring, count=8)), 7)
        with self.assertRaises(ValueError):
            parametric.frames_along_wire(wire)


class TestTessellation(unittest.TestCase):
    def setUp(self):
        rng = random.Random(1)
        self.points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        self.bounds = [(0, 0), (100, 0), (100, 100), (0, 100)]

    def test_delaunay_properties(self):
        tris = parametric.delaunay_2d(self.points)
        self.assertGreater(len(tris), 80)
        pts = self.points
        for a, b, c in tris:
            area = (pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[b][1] - pts[a][1]) * (
                pts[c][0] - pts[a][0]
            )
            self.assertGreater(area, 0)  # counter clockwise
            circ = parametric._circumcircle(pts[a], pts[b], pts[c])
            for i, p in enumerate(pts):
                if i in (a, b, c):
                    continue
                # empty circumcircle property
                self.assertGreater(
                    (p[0] - circ[0]) ** 2 + (p[1] - circ[1]) ** 2, circ[2] * (1 - 1e-9)
                )
        self.assertEqual(parametric.delaunay_2d(self.points[:2]), [])

    def test_delaunay_square(self):
        tris = parametric.delaunay_2d([(0, 0), (1, 0), (1, 1), (0, 1)])
        self.assertEqual(len(tris), 2)

    def test_voronoi_tiles_the_bounds(self):
        cells = parametric.voronoi_2d(self.points, self.bounds)
        self.assertEqual(len(cells), len(self.points))
        self.assertAlmostEqual(sum(_polygon_area(c) for c in cells), 10000.0, places=6)
        for seed, cell in zip(self.points, cells):
            self.assertGreaterEqual(len(cell), 3)
            # the seed lies inside its own cell
            for i in range(len(cell)):
                a, b = cell[i], cell[(i + 1) % len(cell)]
                self.assertGreaterEqual(
                    (b[0] - a[0]) * (seed[1] - a[1]) - (b[1] - a[1]) * (seed[0] - a[0]), -1e-9
                )

    def test_clip_polygon(self):
        clipped = parametric.clip_polygon([(-10, -10), (50, -10), (50, 50), (-10, 50)], self.bounds)
        self.assertAlmostEqual(_polygon_area(clipped), 2500.0)
        outside = parametric.clip_polygon([(200, 200), (210, 200), (210, 210)], self.bounds)
        self.assertEqual(outside, [])


class TestDeformers(unittest.TestCase):
    def setUp(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(10, 10, 40))
        self.points, self.faces = geometry.catmull_clark(points, faces, 1)

    def _bbox(self, pts):
        return Part.makeCompound([Part.Vertex(p) for p in pts]).BoundBox

    def test_twist_keeps_height_and_radius(self):
        # 9 degrees per unit over 40 units of height is a full turn
        out = parametric.deform_points(self.points, "Twist", 9.0, origin=Vector(5, 5, 0))
        box = self._bbox(out)
        self.assertAlmostEqual(box.ZLength, self._bbox(self.points).ZLength, places=6)
        for p, q in zip(self.points, out):
            self.assertAlmostEqual(
                math.hypot(p.x - 5, p.y - 5), math.hypot(q.x - 5, q.y - 5), places=6
            )
        # bottom ring unchanged, top ring rotated by a full turn -> unchanged too
        for p, q in zip(self.points, out):
            if abs(p.z) < 1e-9 or abs(p.z - 40) < 1e-9:
                self.assertAlmostEqual((p - q).Length, 0.0, places=6)

    def test_taper(self):
        out = parametric.deform_points(self.points, "Taper", 0.5, origin=Vector(5, 5, 0))
        top = [q for p, q in zip(self.points, out) if abs(p.z - 40) < 1e-9]
        self.assertTrue(
            all(
                abs(math.hypot(q.x - 5, q.y - 5) - 0.5 * math.hypot(p.x - 5, p.y - 5)) < 1e-6
                for p, q in zip([p for p in self.points if abs(p.z - 40) < 1e-9], top)
            )
        )

    def test_bend_and_stretch(self):
        bent = parametric.deform_points(self.points, "Bend", 90.0, direction=Vector(1, 0, 0))
        box = self._bbox(bent)
        self.assertLess(box.ZLength, 40.0)
        self.assertGreater(box.XLength, 10.0)
        self.assertAlmostEqual(self._bbox(bent).YLength, 10.0, places=6)
        # the default direction bends in some perpendicular plane, not along the axis
        default = self._bbox(parametric.deform_points(self.points, "Bend", 90.0))
        self.assertLess(default.ZLength, 40.0)
        self.assertGreater(default.XLength * default.YLength, 100.0)
        stretched = parametric.deform_points(self.points, "Stretch", 2.0)
        self.assertAlmostEqual(self._bbox(stretched).ZLength, 80.0, places=6)

    def test_wave_noise_flow(self):
        wave = parametric.deform_points(
            self.points, "Wave", 3.0, wavelength=20.0, direction=Vector(1, 0, 0)
        )
        self.assertGreater(self._bbox(wave).XLength, 10.0)
        self.assertAlmostEqual(self._bbox(wave).YLength, 10.0, places=6)
        noisy = parametric.deform_points(self.points, "Noise", 0.5, seed=4)
        same = parametric.deform_points(self.points, "Noise", 0.5, seed=4)
        self.assertTrue(all((a - b).Length < 1e-12 for a, b in zip(noisy, same)))
        self.assertTrue(any((a - b).Length > 1e-6 for a, b in zip(noisy, self.points)))
        wire = features.build_curve([Vector(0, 0, 0), Vector(30, 0, 20), Vector(60, 0, 40)])
        frames = parametric.frames_along_wire(wire, count=12)
        flowed = parametric.deform_points(self.points, "Flow", frames=frames)
        self.assertGreater(self._bbox(flowed).XLength, 50.0)
        with self.assertRaises(ValueError):
            parametric.deform_points(self.points, "Flow")
        with self.assertRaises(ValueError):
            parametric.deform_points(self.points, "Melt")


class TestRelax(unittest.TestCase):
    def test_relax_flattens_with_fixed_boundary(self):
        pyramid = [
            Vector(0, 0, 0),
            Vector(10, 0, 0),
            Vector(10, 10, 0),
            Vector(0, 10, 0),
            Vector(5, 5, 8),
        ]
        faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
        points, faces = geometry.catmull_clark(pyramid, faces, 2)
        boundary = parametric.boundary_vertices(faces)
        self.assertTrue(boundary)
        relaxed = parametric.relax_mesh(points, faces, iterations=100)
        self.assertLess(max(p.z for p in relaxed), 0.5)
        for i in boundary:
            self.assertAlmostEqual((relaxed[i] - points[i]).Length, 0.0, places=9)
        apex = max(range(len(points)), key=lambda i: points[i].z)
        pinned = parametric.relax_mesh(points, faces, iterations=100, fixed={apex})
        self.assertAlmostEqual(pinned[apex].z, points[apex].z, places=9)
        self.assertGreater(max(p.z for p in pinned), 3.0)
        untouched = parametric.relax_mesh(points, faces, iterations=0)
        self.assertTrue(all((a - b).Length < 1e-12 for a, b in zip(points, untouched)))

    def test_mesh_normals(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(2, 2, 2))
        normals = parametric.mesh_normals(points, faces)
        self.assertEqual(len(normals), 8)
        # the corner at the origin points away from the box centre
        corner = min(range(8), key=lambda i: (points[i] - Vector(0, 0, 0)).Length)
        self.assertAlmostEqual(normals[corner].dot(Vector(-1, -1, -1).normalize()), 1.0, places=6)
        far = max(range(8), key=lambda i: (points[i] - Vector(0, 0, 0)).Length)
        self.assertAlmostEqual(normals[far].dot(Vector(1, 1, 1).normalize()), 1.0, places=6)
