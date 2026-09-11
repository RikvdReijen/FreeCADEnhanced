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


class TestPopulating(unittest.TestCase):
    def setUp(self):
        self.bounds = [(0, 0), (100, 0), (100, 100), (0, 100)]

    def _min_spacing(self, points):
        return min(
            math.dist(points[i], points[j])
            for i in range(len(points))
            for j in range(i + 1, len(points))
        )

    def test_populate_respects_bounds_and_seed(self):
        points = parametric.populate_2d(self.bounds, 40, seed=2)
        self.assertEqual(len(points), 40)
        self.assertTrue(all(0 <= p[0] <= 100 and 0 <= p[1] <= 100 for p in points))
        self.assertEqual(points, parametric.populate_2d(self.bounds, 40, seed=2))
        self.assertNotEqual(points, parametric.populate_2d(self.bounds, 40, seed=3))

    def test_populate_inside_and_weights(self):
        left = parametric.populate_2d(self.bounds, 30, seed=2, inside=lambda p: p[0] < 50)
        self.assertEqual(len(left), 30)
        self.assertLess(max(p[0] for p in left), 50)
        uniform = parametric.populate_2d(self.bounds, 60, seed=5)
        weighted = parametric.populate_2d(self.bounds, 60, seed=5, weights=lambda p: p[0] / 100.0)
        mean = lambda pts: sum(p[0] for p in pts) / len(pts)  # noqa: E731
        self.assertGreater(mean(weighted), mean(uniform) + 5)

    def test_lloyd_relaxation_evens_the_spacing(self):
        points = parametric.populate_2d(self.bounds, 25, seed=1)
        relaxed = parametric.lloyd_relax(points, self.bounds, 5)
        self.assertEqual(len(relaxed), len(points))
        self.assertGreater(self._min_spacing(relaxed), self._min_spacing(points) * 1.5)
        self.assertTrue(all(-1 <= p[0] <= 101 and -1 <= p[1] <= 101 for p in relaxed))

    def test_polygon_centroid(self):
        self.assertEqual(
            parametric.polygon_centroid([(0, 0), (10, 0), (10, 10), (0, 10)]), (5.0, 5.0)
        )
        triangle = parametric.polygon_centroid([(0, 0), (9, 0), (0, 9)])
        self.assertAlmostEqual(triangle[0], 3.0)
        self.assertAlmostEqual(triangle[1], 3.0)
        degenerate = parametric.polygon_centroid([(0, 0), (5, 0), (10, 0)])
        self.assertAlmostEqual(degenerate[0], 5.0)


class TestPanelPatterns(unittest.TestCase):
    def test_every_pattern_stays_in_the_unit_square(self):
        for pattern in parametric.PANEL_PATTERNS:
            cells = parametric.panel_cells(4, 3, pattern)
            self.assertTrue(cells, pattern)
            for cell in cells:
                self.assertGreaterEqual(len(cell), 3, pattern)
                for u, v in cell:
                    self.assertGreaterEqual(u, -1e-9, pattern)
                    self.assertLessEqual(u, 1 + 1e-9, pattern)
                    self.assertGreaterEqual(v, -1e-9, pattern)
                    self.assertLessEqual(v, 1 + 1e-9, pattern)

    def test_counts_and_shapes(self):
        self.assertEqual(len(parametric.panel_cells(4, 3, "Quad")), 12)
        self.assertEqual(len(parametric.panel_cells(4, 3, "Triangle")), 24)
        self.assertEqual(len(parametric.panel_cells(4, 3, "Hexagon")), 12)
        self.assertTrue(all(len(c) == 4 for c in parametric.panel_cells(4, 3, "Quad")))
        self.assertTrue(all(len(c) == 3 for c in parametric.panel_cells(4, 3, "Triangle")))
        self.assertTrue(all(len(c) == 6 for c in parametric.panel_cells(4, 3, "Hexagon")))
        quads = parametric.panel_cells(2, 2, "Quad")
        self.assertIn([(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)], quads)
        with self.assertRaises(ValueError):
            parametric.panel_cells(2, 2, "Escher")


class TestTween(unittest.TestCase):
    def test_tween_resamples_and_blends(self):
        first = [Vector(0, 0, 0), Vector(10, 0, 0)]
        second = [Vector(0, 10, 0), Vector(5, 10, 0), Vector(10, 10, 0)]
        middle = parametric.tween_points(first, second, 0.5)
        self.assertEqual(len(middle), 3)
        self.assertAlmostEqual(middle[0].y, 5.0)
        self.assertAlmostEqual(middle[-1].x, 10.0)
        start = parametric.tween_points(first, second, 0.0, samples=5)
        self.assertEqual(len(start), 5)
        self.assertTrue(all(abs(p.y) < 1e-9 for p in start))
        end = parametric.tween_points(first, second, 1.0, samples=5)
        self.assertTrue(all(abs(p.y - 10.0) < 1e-9 for p in end))


class TestMeshTopology(unittest.TestCase):
    def test_mesh_edges(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(2, 2, 2))
        edges = parametric.mesh_edges(faces)
        self.assertEqual(len(edges), 12)
        self.assertTrue(all(a < b for a, b in edges))
        self.assertEqual(len(set(edges)), 12)

    def test_dual_mesh_of_a_closed_cage(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(2, 2, 2))
        points, faces = geometry.catmull_clark(points, faces, 1)
        centres, dual_faces = parametric.dual_mesh(points, faces)
        self.assertEqual(len(centres), len(faces))
        # a closed cage has one dual face per vertex
        self.assertEqual(len(dual_faces), len(points))
        for face in dual_faces:
            self.assertGreaterEqual(len(face), 3)

    def test_dual_mesh_skips_the_boundary(self):
        points = [
            Vector(0, 0, 0),
            Vector(1, 0, 0),
            Vector(1, 1, 0),
            Vector(0, 1, 0),
            Vector(0.5, 0.5, 1),
        ]
        faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
        _, dual_faces = parametric.dual_mesh(points, faces)
        self.assertEqual(len(dual_faces), 1)  # only the apex is interior
        self.assertEqual(len(dual_faces[0]), 4)


class TestLSystem(unittest.TestCase):
    def test_expansion(self):
        self.assertEqual(parametric.lsystem_string("F", {"F": "F+F"}, 1), "F+F")
        self.assertEqual(parametric.lsystem_string("F", {"F": "F+F"}, 2), "F+F+F+F")
        self.assertEqual(parametric.lsystem_string("A", {"F": "FF"}, 3), "A")
        with self.assertRaises(ValueError):
            parametric.lsystem_string("F", {"F": "FFFF"}, 20)

    def test_turtle(self):
        segments = parametric.lsystem_segments("FF", step=5.0, direction=Vector(0, 0, 1))
        self.assertEqual(len(segments), 2)
        (start, end), depth = segments[0]
        self.assertEqual(start, Vector(0, 0, 0))
        self.assertAlmostEqual((end - Vector(0, 0, 5)).Length, 0.0, places=9)
        self.assertEqual(depth, 0)
        # f moves without drawing
        self.assertEqual(len(parametric.lsystem_segments("fF", step=5.0)), 1)
        # brackets restore the state and raise the depth
        branched = parametric.lsystem_segments("F[+F]F", step=5.0, angle=90.0)
        self.assertEqual(len(branched), 3)
        self.assertEqual([d for _, d in branched], [0, 1, 0])
        self.assertAlmostEqual((branched[2][0][0] - branched[0][0][1]).Length, 0.0, places=9)
        # the branch turned, the trunk did not
        trunk = branched[2][0][1] - branched[2][0][0]
        branch = branched[1][0][1] - branched[1][0][0]
        self.assertAlmostEqual(trunk.dot(branch), 0.0, places=6)

    def test_turtle_scaling(self):
        segments = parametric.lsystem_segments("F[F]", step=10.0, step_scale=0.5)
        lengths = [(end - start).Length for (start, end), _ in segments]
        self.assertAlmostEqual(lengths[0], 10.0)
        self.assertAlmostEqual(lengths[1], 5.0)


class TestImages(unittest.TestCase):
    @staticmethod
    def _write_png(path, width, height, colour_type, rows_bytes, filters=None):
        import struct
        import zlib

        raw = b""
        for index, row in enumerate(rows_bytes):
            raw += bytes([filters[index] if filters else 0]) + bytes(row)

        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
        with open(path, "wb") as handle:
            handle.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )

    def setUp(self):
        import tempfile

        self.directory = tempfile.mkdtemp(prefix="freeform_images_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.directory, ignore_errors=True)

    def path(self, name):
        import os

        return os.path.join(self.directory, name)

    def test_grayscale_png(self):
        rows = [[0, 85, 170, 255] for _ in range(3)]
        path = self.path("gray.png")
        self._write_png(path, 4, 3, 0, rows)
        width, height, values = parametric.read_image(path)
        self.assertEqual((width, height), (4, 3))
        self.assertAlmostEqual(values[0][0], 0.0)
        self.assertAlmostEqual(values[0][3], 1.0)
        self.assertAlmostEqual(values[1][1], 85 / 255.0)

    def test_rgb_png_with_filters(self):
        rows = []
        for y in range(4):
            row = []
            for x in range(4):
                row += [x * 85, y * 85, 0]
            rows.append(row)
        # Sub filter on the second row, Up on the third, Paeth on the fourth
        filtered = [list(rows[0])]
        sub = list(rows[1])
        for k in range(len(sub) - 1, 2, -1):
            sub[k] = (rows[1][k] - rows[1][k - 3]) & 0xFF
        filtered.append(sub)
        filtered.append([(rows[2][k] - rows[1][k]) & 0xFF for k in range(len(rows[2]))])
        filtered.append(list(rows[3]))
        path = self.path("rgb.png")
        self._write_png(path, 4, 4, 2, filtered, filters=[0, 1, 2, 0])
        width, height, values = parametric.read_image(path)
        self.assertEqual((width, height), (4, 4))
        # brightness is the mean of the three colour channels
        self.assertAlmostEqual(values[0][0], 0.0, places=6)
        self.assertAlmostEqual(values[1][3], (255 + 85 + 0) / (3 * 255.0), places=6)
        self.assertAlmostEqual(values[2][0], (0 + 170 + 0) / (3 * 255.0), places=6)

    def test_pgm_and_field(self):
        path = self.path("ramp.pgm")
        with open(path, "wb") as handle:
            handle.write(b"P5\n# a comment\n4 2\n255\n" + bytes([0, 85, 170, 255, 255, 170, 85, 0]))
        width, height, values = parametric.read_image(path)
        self.assertEqual((width, height), (4, 2))
        self.assertAlmostEqual(values[0][0], 0.0)
        self.assertAlmostEqual(values[1][0], 1.0)
        field = parametric.ImageField(path)
        self.assertAlmostEqual(field.sample_uv(0.0, 0.0), 1.0)  # v = 0 is the bottom row
        self.assertAlmostEqual(field.sample_uv(1.0, 1.0), 1.0)
        self.assertAlmostEqual(field.sample_uv(0.0, 1.0), 0.0)
        inverted = parametric.ImageField(path, invert=True)
        self.assertAlmostEqual(inverted.sample_uv(0.0, 1.0), 1.0)
        # out of range coordinates clamp instead of raising
        self.assertAlmostEqual(field.sample_uv(-5.0, 9.0), 0.0)

    def test_unsupported_format(self):
        path = self.path("broken.bmp")
        with open(path, "wb") as handle:
            handle.write(b"BM not an image")
        with self.assertRaises(ValueError):
            parametric.read_image(path)


class TestMergeCoplanar(unittest.TestCase):
    def test_triangulated_box_becomes_quads(self):
        points, faces = geometry.polygons_from_shape(Part.makeBox(10, 10, 10))
        triangles = [list(t) for t in geometry.triangulate_polygons(faces)]
        self.assertEqual(len(triangles), 12)
        merged = parametric.merge_coplanar(points, triangles)
        self.assertEqual(len(merged), 6)
        self.assertTrue(all(len(f) == 4 for f in merged))
        self.assertEqual(len(parametric.mesh_edges(merged)), 12)

    def test_tolerance_controls_merging(self):
        # a shallow pyramid: the four sides are not coplanar with each other
        points = [
            Vector(0, 0, 0),
            Vector(10, 0, 0),
            Vector(10, 10, 0),
            Vector(0, 10, 0),
            Vector(5, 5, 0.05),
        ]
        faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
        self.assertEqual(len(parametric.merge_coplanar(points, faces, 0.1)), 4)
        self.assertEqual(len(parametric.merge_coplanar(points, faces, 5.0)), 1)

    def test_single_faces_pass_through(self):
        points = [Vector(0, 0, 0), Vector(1, 0, 0), Vector(1, 1, 0)]
        faces = [[0, 1, 2]]
        self.assertEqual(parametric.merge_coplanar(points, faces), [[0, 1, 2]])
