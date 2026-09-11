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

"""Tests for the parametric generator objects."""

import math
import os
import tempfile
import unittest

import FreeCAD
import Mesh
from FreeCAD import Vector

from freeform import features, generators


def _wave(count=12):
    return [Vector(i * 5, 10 * math.sin(i * 0.5), 0) for i in range(count)]


def _ring(radius=20.0, count=16):
    return [
        Vector(radius * math.cos(a), radius * math.sin(a), 0)
        for a in [i * 2 * math.pi / count for i in range(count)]
    ]


class _DocTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("FreeformGenerators")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def vertex(self, x, y, z=0.0, name="Point"):
        obj = self.doc.addObject("Part::Vertex", name)
        obj.X, obj.Y, obj.Z = x, y, z
        return obj


class TestCurveGenerators(_DocTest):
    def test_expression_is_a_stroke(self):
        helix = generators.make_expression(doc=self.doc)
        self.doc.recompute()
        self.assertTrue(features.is_freeform_object(helix, "Stroke"))
        self.assertTrue(features.is_freeform_object(helix, "Expression"))
        self.assertEqual(helix.Shape.ShapeType, "Wire")
        self.assertAlmostEqual(helix.Shape.BoundBox.ZLength, 16 * math.pi, delta=0.5)
        helix.Thickness = 2.0
        helix.Samples = 40
        self.doc.recompute()
        self.assertEqual(helix.Shape.ShapeType, "Solid")
        helix.XExpression = "nonsense("
        self.doc.recompute()
        self.assertFalse(helix.isValid())

    def test_offset(self):
        ring = features.make_stroke(_ring(), doc=self.doc, closed=True)
        wave = features.make_stroke(_wave(), doc=self.doc)
        self.doc.recompute()
        outer = generators.make_offset(ring, 3.0, doc=self.doc)
        inner = generators.make_offset(ring, -3.0, doc=self.doc)
        open_offset = generators.make_offset(wave, 2.0, doc=self.doc)
        self.doc.recompute()
        self.assertAlmostEqual(outer.Shape.Length, 2 * math.pi * 23, delta=1.0)
        self.assertAlmostEqual(inner.Shape.Length, 2 * math.pi * 17, delta=1.0)
        self.assertTrue(open_offset.Shape.isValid())
        self.assertGreater(open_offset.Shape.distToShape(wave.Shape)[0], 1.9)
        outer.Fill = True
        self.doc.recompute()
        self.assertGreater(outer.Shape.Area, 0)
        outer.Distance = 0
        self.doc.recompute()
        self.assertAlmostEqual(outer.Shape.Length, ring.Shape.Length, places=6)

    def test_blend(self):
        first = features.make_stroke(
            [Vector(0, 0, 0), Vector(10, 2, 0), Vector(20, 0, 0)], doc=self.doc
        )
        second = features.make_stroke(
            [Vector(40, 0, 0), Vector(50, 5, 0), Vector(60, 0, 0)], doc=self.doc
        )
        self.doc.recompute()
        blend = generators.make_blend(first, second, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(blend.Shape.isValid())
        ends = [v.Point for v in blend.Shape.Vertexes]
        self.assertAlmostEqual((ends[0] - Vector(20, 0, 0)).Length, 0.0, places=6)
        self.assertAlmostEqual((ends[-1] - Vector(40, 0, 0)).Length, 0.0, places=6)
        # tangent continuity with the first curve
        edge = blend.Shape.Edges[0]
        tangent = edge.tangentAt(edge.FirstParameter)
        first_edge = first.Shape.Edges[0]
        first_tangent = first_edge.tangentAt(first_edge.LastParameter)
        self.assertAlmostEqual(tangent.dot(first_tangent), 1.0, places=5)
        blend.Bulge = 0.0
        self.doc.recompute()
        self.assertAlmostEqual(blend.Shape.Length, 20.0, places=4)

    def test_divide(self):
        wave = features.make_stroke(_wave(), doc=self.doc)
        self.doc.recompute()
        divide = generators.make_divide(wave, count=7, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(divide.Placements), 7)
        self.assertEqual(len(divide.Shape.Vertexes), 7)
        self.assertAlmostEqual(
            (divide.Placements[0].Base - wave.Shape.Vertexes[0].Point).Length, 0.0, places=6
        )
        divide.FrameSize = 2.0
        self.doc.recompute()
        self.assertEqual(len(divide.Shape.Edges), 14)
        divide.Spacing = 10.0
        self.doc.recompute()
        self.assertEqual(len(divide.Placements), int(round(wave.Shape.Length / 10.0)) + 1)

    def test_contours(self):
        sphere = self.doc.addObject("Part::Sphere", "Sphere")
        sphere.Radius = 15
        contours = generators.make_contours(sphere, spacing=5.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(contours.Shape.Wires), 6)
        self.assertTrue(all(w.isClosed() for w in contours.Shape.Wires))
        contours.Faces = True
        self.doc.recompute()
        self.assertEqual(len(contours.Shape.Faces), 6)
        contours.Direction = Vector(1, 0, 0)
        contours.Spacing = 10.0
        self.doc.recompute()
        self.assertEqual(len(contours.Shape.Wires), 3)
        contours.Spacing = 0
        self.doc.recompute()
        self.assertFalse(contours.isValid())


class TestArrays(_DocTest):
    def setUp(self):
        super().setUp()
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length = self.box.Width = self.box.Height = 3
        self.wave = features.make_stroke(_wave(), doc=self.doc)
        self.doc.recompute()

    def test_curve_array(self):
        array = generators.make_curve_array(self.box, self.wave, count=8, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(array.Shape.Solids), 8)
        for solid in array.Shape.Solids:
            self.assertAlmostEqual(solid.Volume, 27.0, places=6)
        # the first copy sits at the start of the curve
        self.assertLess(array.Shape.Solids[0].distToShape(self.wave.Shape)[0], 1e-6)
        array.Align = False
        self.doc.recompute()
        self.assertEqual(len(array.Shape.Solids), 8)

    def test_curve_array_attractor_and_twist(self):
        attractor = self.vertex(30, 0, 0)
        array = generators.make_curve_array(
            self.box, self.wave, count=8, attractors=[attractor], doc=self.doc
        )
        array.AttractorRadius = 20
        array.MinScale = 0.2
        array.Twist = 90
        self.doc.recompute()
        volumes = sorted(s.Volume for s in array.Shape.Solids)
        self.assertLess(volumes[0], 27.0 * 0.5)
        self.assertAlmostEqual(volumes[-1], 27.0, places=6)
        # an attractor sitting exactly on a copy shrinks that copy away entirely
        array.Attractors = [self.vertex(0, 0, 0, "Origin")]
        array.MinScale = 0.0
        self.doc.recompute()
        self.assertEqual(len(array.Shape.Solids), 7)

    def test_surface_grid_panels_points_copies(self):
        plane = self.doc.addObject("Part::Plane", "Plane")
        plane.Length, plane.Width = 100, 60
        grid = generators.make_surface_grid(plane, count_u=5, count_v=3, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(grid.Shape.Faces), 15)
        self.assertAlmostEqual(grid.Shape.Faces[0].Area, 20 * 20 * 0.81, places=6)
        attractor = self.vertex(0, 0, 0)
        grid.Attractors = [attractor]
        grid.AttractorRadius = 40
        self.doc.recompute()
        areas = sorted(f.Area for f in grid.Shape.Faces)
        self.assertLess(areas[0], 100)
        self.assertAlmostEqual(areas[-1], 324.0, places=6)
        grid.Output = "Points"
        self.doc.recompute()
        self.assertEqual(len(grid.Shape.Vertexes), 24)
        grid.Output = "Frames"
        grid.ItemScale = 5
        self.doc.recompute()
        self.assertEqual(len(grid.Shape.Edges), 48)
        grid.Output = "Copies"
        grid.Item = self.box
        self.doc.recompute()
        self.assertEqual(len(grid.Shape.Solids), 24)

    def test_surface_grid_on_curved_face(self):
        cylinder = self.doc.addObject("Part::Cylinder", "Cylinder")
        cylinder.Radius, cylinder.Height = 20, 40
        self.doc.recompute()
        grid = generators.make_surface_grid(cylinder, "Face1", 12, 4, doc=self.doc)
        grid.PanelScale = 1.0  # full size panels have their corners on the surface
        self.doc.recompute()
        self.assertEqual(len(grid.Shape.Faces), 48)
        for face in grid.Shape.Faces:
            self.assertLess(face.distToShape(cylinder.Shape.Face1)[0], 1e-3)

    def test_voronoi(self):
        plane = self.doc.addObject("Part::Plane", "Plane")
        plane.Length, plane.Width = 100, 60
        voronoi = generators.make_voronoi(plane, count=15, seed=3, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(voronoi.Shape.Faces), 15)
        self.assertAlmostEqual(sum(f.Area for f in voronoi.Shape.Faces), 6000.0, places=3)
        voronoi.Inset = 1.5
        self.doc.recompute()
        self.assertLess(sum(f.Area for f in voronoi.Shape.Faces), 6000.0 * 0.9)
        voronoi.Output = "Edges"
        self.doc.recompute()
        self.assertEqual(len(voronoi.Shape.Faces), 0)
        self.assertGreater(len(voronoi.Shape.Edges), 15)
        voronoi.Output = "Delaunay"
        self.doc.recompute()
        self.assertGreater(len(voronoi.Shape.Wires), 10)
        voronoi.Output = "Cells"
        voronoi.Inset = 0
        voronoi.Seed = 7
        self.doc.recompute()
        self.assertEqual(len(voronoi.Shape.Faces), 15)

    def test_voronoi_from_points_and_errors(self):
        plane = self.doc.addObject("Part::Plane", "Plane")
        plane.Length, plane.Width = 50, 50
        seeds = [self.vertex(10, 10), self.vertex(40, 10), self.vertex(25, 40)]
        voronoi = generators.make_voronoi(plane, doc=self.doc)
        voronoi.Points = seeds
        self.doc.recompute()
        self.assertEqual(len(voronoi.Shape.Faces), 3)
        self.assertAlmostEqual(sum(f.Area for f in voronoi.Shape.Faces), 2500.0, places=3)
        curved = generators.make_voronoi(self.doc.addObject("Part::Sphere", "S"), doc=self.doc)
        self.doc.recompute()
        self.assertFalse(curved.isValid())


class TestMeshGenerators(_DocTest):
    def setUp(self):
        super().setUp()
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length = self.box.Width = 10
        self.box.Height = 40
        self.subd = features.make_subd(self.box, iterations=2, doc=self.doc)
        self.doc.recompute()

    def test_deform_modes(self):
        for mode, amount in (
            ("Twist", 9.0),
            ("Taper", 0.5),
            ("Bend", 90.0),
            ("Stretch", 2.0),
            ("Wave", 2.0),
            ("Noise", 0.5),
        ):
            deform = generators.make_deform(self.subd, mode, amount, doc=self.doc)
            self.doc.recompute()
            self.assertTrue(deform.isValid(), mode)
            self.assertEqual(deform.Mesh.CountFacets, self.subd.Mesh.CountFacets, mode)
        self.assertAlmostEqual(
            deform.Mesh.BoundBox.ZLength, self.subd.Mesh.BoundBox.ZLength, delta=1.5
        )
        stretch = generators.make_deform(self.subd, "Stretch", 2.0, doc=self.doc)
        self.doc.recompute()
        # mesh coordinates are single precision, hence the tolerance
        self.assertAlmostEqual(
            stretch.Mesh.BoundBox.ZLength, 2 * self.subd.Mesh.BoundBox.ZLength, delta=1e-3
        )

    def test_deform_flow_and_shape_input(self):
        path = features.make_stroke(
            [Vector(0, 0, 0), Vector(30, 0, 20), Vector(60, 0, 40)], doc=self.doc
        )
        self.doc.recompute()
        flow = generators.make_deform(self.box, "Flow", path=path, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(flow.isValid())
        self.assertGreater(flow.Mesh.BoundBox.XLength, 50)
        flow.Path = None
        self.doc.recompute()
        self.assertFalse(flow.isValid())

    def test_relax(self):
        pyramid = [
            Vector(0, 0, 0),
            Vector(10, 0, 0),
            Vector(10, 10, 0),
            Vector(0, 10, 0),
            Vector(5, 5, 8),
        ]
        tris = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
        mesh_obj = self.doc.addObject("Mesh::Feature", "Pyramid")
        mesh_obj.Mesh = Mesh.Mesh([pyramid[i] for t in tris for i in t])
        cage = features.make_subd(mesh_obj, iterations=2, doc=self.doc)
        relax = generators.make_relax(cage, iterations=60, doc=self.doc)
        self.doc.recompute()
        self.assertLess(relax.Mesh.BoundBox.ZMax, 1.0)
        self.assertAlmostEqual(relax.Mesh.BoundBox.XLength, 10.0, places=6)
        pole = self.vertex(5, 5, 8, "Pole")
        relax.Anchors = [pole]
        self.doc.recompute()
        self.assertGreater(relax.Mesh.BoundBox.ZMax, 3.5)

    def test_persistence(self):
        plane = self.doc.addObject("Part::Plane", "Plane")
        plane.Length, plane.Width = 50, 50
        generators.make_voronoi(plane, count=6, seed=1, doc=self.doc)
        generators.make_curve_array(
            self.box, generators.make_expression(doc=self.doc), count=4, doc=self.doc
        )
        generators.make_deform(self.subd, "Twist", 3.0, doc=self.doc)
        self.doc.recompute()
        facets = self.subd.Mesh.CountFacets
        path = os.path.join(tempfile.gettempdir(), "freeform_generators_test.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        for name, kind in (
            ("Voronoi", "Voronoi"),
            ("CurveArray", "CurveArray"),
            ("Deform", "Deform"),
            ("Expression", "Expression"),
        ):
            obj = self.doc.getObject(name)
            self.assertTrue(features.is_freeform_object(obj, kind), name)
        self.doc.getObject("Voronoi").Seed = 2
        self.doc.getObject("Expression").TMax = math.pi
        self.doc.recompute()
        self.assertEqual(len(self.doc.getObject("Voronoi").Shape.Faces), 6)
        self.assertEqual(len(self.doc.getObject("CurveArray").Shape.Solids), 4)
        self.assertEqual(self.doc.getObject("Deform").Mesh.CountFacets, facets)
        os.remove(path)


class TestPopulateAndLattice(_DocTest):
    def setUp(self):
        super().setUp()
        self.plane = self.doc.addObject("Part::Plane", "Plane")
        self.plane.Length, self.plane.Width = 100, 80
        self.doc.recompute()

    def test_populate(self):
        populate = generators.make_populate(self.plane, count=40, seed=3, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(populate.Placements), 40)
        self.assertEqual(len(populate.Shape.Vertexes), 40)
        for placement in populate.Placements:
            self.assertTrue(self.plane.Shape.Faces[0].isInside(placement.Base, 1e-6, True))

    def test_populate_relaxation_evens_the_spacing(self):
        populate = generators.make_populate(self.plane, count=30, seed=3, doc=self.doc)
        self.doc.recompute()
        spacing = lambda o: min(  # noqa: E731
            (a.Base - b.Base).Length
            for i, a in enumerate(o.Placements)
            for b in o.Placements[i + 1 :]
        )
        rough = spacing(populate)
        populate.Relax = 5
        self.doc.recompute()
        self.assertGreater(spacing(populate), rough * 1.5)

    def test_populate_feeds_voronoi(self):
        populate = generators.make_populate(self.plane, count=12, seed=1, relax=3, doc=self.doc)
        voronoi = generators.make_voronoi(self.plane, doc=self.doc)
        voronoi.Points = [populate]
        self.doc.recompute()
        self.assertEqual(len(voronoi.Shape.Faces), 12)
        self.assertAlmostEqual(sum(f.Area for f in voronoi.Shape.Faces), 8000.0, places=2)

    def test_lattice_from_mesh_and_shape(self):
        box = self.doc.addObject("Part::Box", "Box")
        box.Length = box.Width = box.Height = 30
        subd = features.make_subd(box, iterations=1, doc=self.doc)
        self.doc.recompute()
        lattice = generators.make_lattice(subd, radius=1.0, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(lattice.Shape.Solids)
        self.assertTrue(lattice.Shape.isValid())
        shape_lattice = generators.make_lattice(box, radius=1.5, doc=self.doc)
        self.doc.recompute()
        # a box has twelve edges and eight corner nodes
        self.assertEqual(len(shape_lattice.Shape.Solids), 20)
        shape_lattice.Nodes = False
        self.doc.recompute()
        self.assertEqual(len(shape_lattice.Shape.Solids), 12)
        shape_lattice.Radius = 0
        self.doc.recompute()
        self.assertEqual(len(shape_lattice.Shape.Solids), 0)
        self.assertEqual(len(shape_lattice.Shape.Edges), 12)


class TestTweenAndGrowth(_DocTest):
    def test_tween(self):
        first = features.make_stroke(
            [Vector(0, 0, 0), Vector(20, 10, 0), Vector(40, 0, 0)], doc=self.doc
        )
        second = features.make_stroke(
            [Vector(0, 40, 20), Vector(20, 30, 20), Vector(40, 40, 20)], doc=self.doc
        )
        self.doc.recompute()
        tween = generators.make_tween(first, second, count=6, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(tween.Shape.Wires), 6)
        heights = sorted(w.BoundBox.ZMax for w in tween.Shape.Wires)
        self.assertGreater(heights[0], 0)
        self.assertLess(heights[-1], 20)
        self.assertEqual(len(set(round(h, 3) for h in heights)), 6)
        tween.IncludeEnds = True
        self.doc.recompute()
        self.assertEqual(len(tween.Shape.Wires), 8)

    def test_lsystem(self):
        tree = generators.make_lsystem(doc=self.doc)
        self.doc.recompute()
        self.assertTrue(tree.Shape.Edges)
        self.assertGreater(tree.Shape.BoundBox.ZLength, 0)
        wire_count = len(tree.Shape.Edges)
        tree.Thickness = 2.0
        self.doc.recompute()
        self.assertEqual(len(tree.Shape.Solids), wire_count)
        tree.Rules = ["F=F[+F][-F]F"]
        tree.Generations = 3
        self.doc.recompute()
        self.assertTrue(tree.isValid())

    def test_lsystem_branch_cap(self):
        tree = generators.make_lsystem(doc=self.doc)
        tree.MaxBranches = 50
        tree.Generations = 4
        self.doc.recompute()
        self.assertFalse(tree.isValid())
        tree.Generations = 1
        self.doc.recompute()
        self.assertTrue(tree.isValid())

    def test_box_morph(self):
        cylinder = self.doc.addObject("Part::Cylinder", "Cylinder")
        cylinder.Radius, cylinder.Height = 15, 40
        unit = self.doc.addObject("Part::Box", "Unit")
        unit.Length = unit.Width = unit.Height = 5
        self.doc.recompute()
        morph = generators.make_box_morph(unit, cylinder, "Face1", 6, 3, 6.0, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(morph.isValid())
        self.assertEqual(morph.Mesh.CountFacets, 12 * 6 * 3)
        box = morph.Mesh.BoundBox
        self.assertAlmostEqual(box.ZLength, 40.0, delta=2.0)
        self.assertGreater(box.XLength, 30.0)  # wrapped around the cylinder
        morph.Height = 12.0
        self.doc.recompute()
        self.assertGreater(morph.Mesh.BoundBox.XLength, box.XLength)
        # a cell scale below one leaves gaps between the copies
        morph.Height = 6.0
        morph.CellScale = 0.5
        self.doc.recompute()
        self.assertEqual(morph.Mesh.CountFacets, 12 * 6 * 3)
        self.assertLess(morph.Mesh.BoundBox.XLength, box.XLength)


class TestImageDrivenAttractors(_DocTest):
    def setUp(self):
        super().setUp()
        import tempfile

        self.directory = tempfile.mkdtemp(prefix="freeform_field_")
        self.image = os.path.join(self.directory, "ramp.pgm")
        # a horizontal ramp: dark on the left, bright on the right
        with open(self.image, "wb") as handle:
            handle.write(b"P5\n8 2\n255\n" + bytes([0, 36, 73, 109, 146, 182, 219, 255] * 2))
        self.plane = self.doc.addObject("Part::Plane", "Plane")
        self.plane.Length, self.plane.Width = 100, 80
        self.doc.recompute()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.directory, ignore_errors=True)
        super().tearDown()

    def test_panels_follow_the_image(self):
        grid = generators.make_surface_grid(self.plane, count_u=8, count_v=4, doc=self.doc)
        self.doc.recompute()
        even = sorted(f.Area for f in grid.Shape.Faces)
        self.assertAlmostEqual(even[0], even[-1], places=6)
        grid.Image = self.image
        grid.MinScale = 0.1
        self.doc.recompute()
        varied = sorted(f.Area for f in grid.Shape.Faces)
        self.assertLess(varied[0], varied[-1] * 0.2)

    def test_populate_density_follows_the_image(self):
        plain = generators.make_populate(self.plane, count=60, seed=4, doc=self.doc)
        shaded = generators.make_populate(self.plane, count=60, seed=4, doc=self.doc)
        shaded.Image = self.image
        shaded.MinScale = 0.0
        self.doc.recompute()
        plane = generators._FacePlane(self.plane.Shape.Faces[0])
        mean = lambda o: sum(  # noqa: E731
            plane.to_unit(plane.to_2d(p.Base))[0] for p in o.Placements
        ) / len(o.Placements)
        self.assertGreater(mean(shaded), mean(plain) + 0.05)

    def test_missing_image_is_reported(self):
        grid = generators.make_surface_grid(self.plane, count_u=3, count_v=3, doc=self.doc)
        grid.Image = os.path.join(self.directory, "absent.png")
        self.doc.recompute()
        self.assertFalse(grid.isValid())


class TestPanelPatternObjects(_DocTest):
    def test_patterns_on_a_face(self):
        plane = self.doc.addObject("Part::Plane", "Plane")
        plane.Length, plane.Width = 60, 40
        self.doc.recompute()
        grid = generators.make_surface_grid(plane, count_u=6, count_v=4, doc=self.doc)
        expected = {"Quad": 24, "Triangle": 48, "Hexagon": 24}
        for pattern, count in expected.items():
            grid.Pattern = pattern
            self.doc.recompute()
            self.assertTrue(grid.isValid(), pattern)
            self.assertEqual(len(grid.Shape.Faces), count, pattern)
        for pattern in ("Diamond", "Brick"):
            grid.Pattern = pattern
            self.doc.recompute()
            self.assertTrue(grid.isValid(), pattern)
            self.assertGreater(len(grid.Shape.Faces), 5, pattern)
        grid.Pattern = "Hexagon"
        grid.PanelScale = 1.0
        self.doc.recompute()
        for face in grid.Shape.Faces:
            self.assertEqual(len(face.Vertexes), 6)
