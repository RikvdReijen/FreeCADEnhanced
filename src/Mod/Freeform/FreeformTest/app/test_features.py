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

"""Tests for the parametric Freeform document objects."""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector

from freeform import features


def _wave(count=12, offset=Vector(0, 0, 0)):
    return [Vector(i * 5, 10 * math.sin(i * 0.5), i) + offset for i in range(count)]


def _ring(radius=10.0, count=16, z=0.0, wobble=0.0):
    pts = []
    for i in range(count):
        a = 2 * math.pi * i / count
        pts.append(Vector(radius * math.cos(a), radius * math.sin(a), z + wobble * math.sin(2 * a)))
    return pts


class _DocTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("FreeformTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)


class TestStroke(_DocTest):
    def test_open_stroke_is_wire(self):
        stroke = features.make_stroke(_wave(), doc=self.doc)
        self.doc.recompute()
        self.assertTrue(features.is_freeform_object(stroke, "Stroke"))
        self.assertEqual(stroke.Shape.ShapeType, "Wire")
        self.assertEqual(len(stroke.Shape.Edges), 1)
        self.assertGreater(stroke.Shape.Length, 60)
        self.assertAlmostEqual(float(stroke.Length), stroke.Shape.Length, places=6)

    def test_two_points_make_a_line(self):
        stroke = features.make_stroke([Vector(0, 0, 0), Vector(10, 0, 0)], doc=self.doc)
        self.doc.recompute()
        self.assertAlmostEqual(stroke.Shape.Length, 10.0)
        self.assertEqual(stroke.Shape.Edges[0].Curve.__class__.__name__, "Line")

    def test_degree_one_is_polyline(self):
        stroke = features.make_stroke(_wave(), doc=self.doc)
        stroke.Degree = 1
        self.doc.recompute()
        self.assertEqual(len(stroke.Shape.Edges), 11)

    def test_approximate_mode(self):
        stroke = features.make_stroke(_wave(30), doc=self.doc)
        stroke.Interpolate = False
        stroke.Degree = 3
        self.doc.recompute()
        self.assertEqual(stroke.Shape.Edges[0].Curve.Degree, 3)
        self.assertLess(stroke.Shape.Edges[0].Curve.NbPoles, 30)

    def test_smoothing_and_tolerance_change_shape(self):
        pts = [Vector(i, (-1) ** i, 0) for i in range(20)]
        stroke = features.make_stroke(pts, doc=self.doc)
        self.doc.recompute()
        rough = stroke.Shape.Length
        stroke.Smoothing = 5
        self.doc.recompute()
        self.assertLess(stroke.Shape.Length, rough)
        stroke.Tolerance = 2.0
        self.doc.recompute()
        self.assertAlmostEqual(stroke.Shape.Length, 19.0, delta=0.5)

    def test_invalid_stroke_reports_error(self):
        stroke = features.make_stroke([Vector(0, 0, 0), Vector(0, 0, 0)], doc=self.doc)
        self.doc.recompute()
        self.assertTrue(stroke.Shape.isNull() or not stroke.isValid())

    def test_closed_stroke(self):
        stroke = features.make_stroke(_ring(), doc=self.doc, closed=True)
        self.doc.recompute()
        self.assertTrue(stroke.Shape.isClosed())
        self.assertAlmostEqual(stroke.Shape.Length, 2 * math.pi * 10, delta=0.5)

    def test_closed_stroke_face(self):
        stroke = features.make_stroke(_ring(), doc=self.doc, closed=True)
        stroke.MakeFace = True
        self.doc.recompute()
        self.assertEqual(stroke.Shape.ShapeType, "Face")
        self.assertAlmostEqual(stroke.Shape.Area, math.pi * 100, delta=5)
        # non planar closed strokes get a free-form face
        stroke.Points = _ring(wobble=3.0)
        self.doc.recompute()
        self.assertEqual(stroke.Shape.ShapeType, "Face")
        self.assertTrue(stroke.Shape.isValid())
        self.assertGreater(stroke.Shape.Area, math.pi * 100)
        # opening the stroke drops the face
        stroke.Closed = False
        self.doc.recompute()
        self.assertFalse(stroke.MakeFace)
        self.assertEqual(stroke.Shape.ShapeType, "Wire")

    def test_tube(self):
        stroke = features.make_stroke(_wave(), doc=self.doc, thickness=3.0)
        self.doc.recompute()
        self.assertEqual(stroke.Shape.ShapeType, "Solid")
        self.assertTrue(stroke.Shape.isValid())
        expected = math.pi * 1.5**2 * float(stroke.Length)
        self.assertAlmostEqual(stroke.Shape.Volume, expected, delta=expected * 0.1)

    def test_tapered_tube(self):
        stroke = features.make_stroke(_wave(), doc=self.doc, thickness=3.0)
        stroke.EndThickness = 0.5
        self.doc.recompute()
        self.assertEqual(stroke.Shape.ShapeType, "Solid")
        self.assertTrue(stroke.Shape.isValid())
        fat = math.pi * 1.5**2 * float(stroke.Length)
        thin = math.pi * 0.25**2 * float(stroke.Length)
        self.assertLess(stroke.Shape.Volume, fat)
        self.assertGreater(stroke.Shape.Volume, thin)
        # start and end cross sections have the requested radii
        start = stroke.Shape.BoundBox
        self.assertGreater(start.DiagonalLength, 0)

    def test_build_helpers(self):
        wire = features.build_curve(_wave())
        self.assertEqual(wire.ShapeType, "Wire")
        tube = features.build_tube(wire, 1.0)
        self.assertEqual(tube.ShapeType, "Solid")
        with self.assertRaises(ValueError):
            features.build_tube(wire, 0.0)
        with self.assertRaises(ValueError):
            features.build_curve([Vector(0, 0, 0)])


class TestRibbon(_DocTest):
    def setUp(self):
        super().setUp()
        self.stroke = features.make_stroke(_wave(), doc=self.doc)
        self.doc.recompute()

    def test_flat_ribbon(self):
        ribbon = features.make_ribbon(self.stroke, width=4.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(ribbon.Shape.ShapeType, "Face")
        self.assertTrue(ribbon.Shape.isValid())
        expected = 4.0 * self.stroke.Shape.Length
        self.assertAlmostEqual(ribbon.Shape.Area, expected, delta=expected * 0.15)

    def test_upright_ribbon_differs(self):
        flat = features.make_ribbon(self.stroke, width=4.0, doc=self.doc)
        upright = features.make_ribbon(self.stroke, width=4.0, mode="Upright", doc=self.doc)
        self.doc.recompute()
        self.assertNotAlmostEqual(
            flat.Shape.BoundBox.ZLength, upright.Shape.BoundBox.ZLength, places=2
        )
        self.assertAlmostEqual(
            upright.Shape.BoundBox.ZLength, self.stroke.Shape.BoundBox.ZLength + 4.0, delta=0.2
        )

    def test_thick_ribbon_is_solid(self):
        ribbon = features.make_ribbon(self.stroke, width=4.0, thickness=1.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(ribbon.Shape.ShapeType, "Solid")
        self.assertTrue(ribbon.Shape.isValid())
        expected = 1.0 * 4.0 * self.stroke.Shape.Length
        self.assertAlmostEqual(ribbon.Shape.Volume, expected, delta=expected * 0.15)
        # centred on the ribbon surface: the flat ribbon lies at z of the stroke
        flat = features.make_ribbon(self.stroke, width=4.0, doc=self.doc)
        self.doc.recompute()
        self.assertAlmostEqual(
            ribbon.Shape.BoundBox.ZMax - flat.Shape.BoundBox.ZMax, 0.5, delta=0.1
        )
        self.assertAlmostEqual(
            flat.Shape.BoundBox.ZMin - ribbon.Shape.BoundBox.ZMin, 0.5, delta=0.1
        )

    def test_uncentered_ribbon_starts_on_curve(self):
        ribbon = features.make_ribbon(self.stroke, width=4.0, doc=self.doc)
        ribbon.Centered = False
        self.doc.recompute()
        start = self.stroke.Shape.Vertexes[0].Point
        self.assertLess(ribbon.Shape.distToShape(Part.Vertex(start))[0], 1e-6)

    def test_closed_ribbon(self):
        ring = features.make_stroke(_ring(), doc=self.doc, closed=True)
        ribbon = features.make_ribbon(ring, width=2.0, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(ribbon.Shape.isValid())
        self.assertAlmostEqual(ribbon.Shape.Area, 2 * math.pi * 10 * 2.0, delta=8)

    def test_ribbon_without_base_fails_gracefully(self):
        ribbon = features.make_ribbon(None, width=4.0, doc=self.doc)
        self.doc.recompute()
        self.assertFalse(ribbon.isValid())


class TestSurface(_DocTest):
    def setUp(self):
        super().setUp()
        self.a = features.make_stroke(_wave(), doc=self.doc)
        self.b = features.make_stroke(_wave(offset=Vector(0, 20, 5)), doc=self.doc)
        self.c = features.make_stroke(_wave(offset=Vector(0, 40, 0)), doc=self.doc)
        self.doc.recompute()

    def test_loft(self):
        surface = features.make_surface([self.a, self.b, self.c], doc=self.doc)
        self.doc.recompute()
        self.assertIn(surface.Shape.ShapeType, ("Shell", "Face"))
        self.assertTrue(surface.Shape.isValid())
        self.assertGreater(surface.Shape.Area, 40 * self.a.Shape.Length * 0.8)

    def test_ruled_loft(self):
        surface = features.make_surface([self.a, self.b], ruled=True, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(surface.Shape.isValid())

    def test_solid_loft(self):
        bottom = features.make_stroke(_ring(10), doc=self.doc, closed=True)
        top = features.make_stroke(_ring(6, z=15), doc=self.doc, closed=True)
        surface = features.make_surface([bottom, top], solid=True, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(surface.Shape.ShapeType, "Solid")
        frustum = math.pi * 15 / 3.0 * (100 + 60 + 36)
        self.assertAlmostEqual(surface.Shape.Volume, frustum, delta=frustum * 0.05)

    def test_needs_two_sections(self):
        surface = features.make_surface([self.a], doc=self.doc)
        self.doc.recompute()
        self.assertFalse(surface.isValid())


class TestPatch(_DocTest):
    def setUp(self):
        super().setUp()
        self.edges = [
            features.make_stroke(
                [Vector(0, 0, 0), Vector(5, 0, 2), Vector(10, 0, 0)], doc=self.doc
            ),
            features.make_stroke(
                [Vector(10, 0, 0), Vector(10, 5, 3), Vector(10, 10, 0)], doc=self.doc
            ),
            features.make_stroke(
                [Vector(10, 10, 0), Vector(5, 10, -2), Vector(0, 10, 0)], doc=self.doc
            ),
            features.make_stroke(
                [Vector(0, 10, 0), Vector(0, 5, 1), Vector(0, 0, 0)], doc=self.doc
            ),
        ]
        self.doc.recompute()

    def test_patch_from_strokes(self):
        patch = features.make_patch(self.edges, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(patch.Shape.ShapeType, "Face")
        self.assertTrue(patch.Shape.isValid())
        # the bulging boundary makes the patch a little larger than the 10x10 footprint
        self.assertGreater(patch.Shape.Area, 100)
        self.assertLess(patch.Shape.Area, 140)

    def test_patch_from_unordered_sub_elements(self):
        boundary = [
            self.edges[2],
            (self.edges[0], ["Edge1"]),
            self.edges[3],
            (self.edges[1], "Edge1"),
        ]
        patch = features.make_patch(boundary, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(patch.Shape.isValid())
        self.assertGreater(patch.Shape.Area, 100)
        self.assertLess(patch.Shape.Area, 140)
        self.assertEqual(len(patch.Boundary), 4)

    def test_empty_patch_fails(self):
        patch = features.make_patch([], doc=self.doc)
        self.doc.recompute()
        self.assertFalse(patch.isValid())


class TestSubD(_DocTest):
    def test_subd_from_box(self):
        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 20, 10, 5
        subd = features.make_subd(box, iterations=2, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(features.is_freeform_object(subd, "SubD"))
        self.assertEqual(subd.Mesh.CountFacets, 6 * 16 * 2)
        self.assertTrue(subd.Mesh.isSolid())
        self.assertLess(subd.Mesh.Volume, 1000)
        self.assertGreater(subd.Mesh.Volume, 300)
        subd.Iterations = 0
        self.doc.recompute()
        self.assertEqual(subd.Mesh.CountFacets, 12)
        self.assertAlmostEqual(subd.Mesh.Volume, 1000, places=6)

    def test_subd_from_mesh(self):
        import Mesh

        points, tris = Part.makeBox(4, 4, 4).tessellate(1)
        mesh_obj = self.doc.addObject("Mesh::Feature", "Cage")
        mesh_obj.Mesh = Mesh.Mesh([points[i] for tri in tris for i in tri])
        subd = features.make_subd(mesh_obj, iterations=1, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(subd.Mesh.isSolid())
        self.assertEqual(subd.Mesh.CountFacets, 12 * 3 * 2)

    def test_subd_without_cage_fails(self):
        subd = features.make_subd(None, doc=self.doc)
        self.doc.recompute()
        self.assertFalse(subd.isValid())


class TestDerived(_DocTest):
    def test_mirror(self):
        stroke = features.make_stroke(_wave(), doc=self.doc)
        mirror = features.make_mirror(stroke, Vector(0, 0, 0), Vector(0, 1, 0), doc=self.doc)
        self.doc.recompute()
        self.assertEqual(mirror.TypeId, "Part::Mirroring")
        self.assertAlmostEqual(mirror.Shape.Length, stroke.Shape.Length, places=6)
        self.assertAlmostEqual(mirror.Shape.BoundBox.YMax, -stroke.Shape.BoundBox.YMin, places=6)

    def test_revolve(self):
        profile = features.make_stroke(
            [Vector(5, 0, 0), Vector(8, 0, 5), Vector(5, 0, 10)], doc=self.doc
        )
        revolve = features.make_revolve(
            profile, Vector(0, 0, 0), Vector(0, 0, 1), 360, solid=False, doc=self.doc
        )
        self.doc.recompute()
        self.assertEqual(revolve.TypeId, "Part::Revolution")
        self.assertTrue(revolve.Shape.isValid())
        self.assertAlmostEqual(revolve.Shape.BoundBox.XLength, 16.0, delta=0.5)

    def test_primitives(self):
        normal = Vector(0, 0, 1)
        for kind in ("Sphere", "Box", "Cylinder", "Cone", "Torus"):
            obj = features.make_primitive(kind, Vector(1, 2, 3), 10.0, normal, doc=self.doc)
            self.doc.recompute()
            self.assertTrue(obj.Shape.isValid(), kind)
            box = obj.Shape.optimalBoundingBox()
            self.assertGreaterEqual(box.ZMin, 3.0 - 1e-6, kind)
            self.assertAlmostEqual(box.XLength, 10.0, places=6, msg=kind)
            self.assertAlmostEqual(box.Center.x, 1.0, places=6, msg=kind)
            self.assertAlmostEqual(box.Center.y, 2.0, places=6, msg=kind)

    def test_primitive_on_tilted_plane(self):
        obj = features.make_primitive(
            "Cylinder", Vector(0, 0, 0), 10.0, Vector(1, 0, 0), doc=self.doc
        )
        self.doc.recompute()
        box = obj.Shape.optimalBoundingBox()
        self.assertAlmostEqual(box.XMin, 0.0, places=6)
        self.assertAlmostEqual(box.XLength, 10.0, places=6)
        with self.assertRaises(ValueError):
            features.make_primitive("Teapot", doc=self.doc)


class TestPersistence(_DocTest):
    def test_save_and_restore(self):
        stroke = features.make_stroke(_wave(), doc=self.doc, thickness=2.0)
        ribbon = features.make_ribbon(stroke, width=3.0, doc=self.doc)
        box = self.doc.addObject("Part::Box", "Box")
        subd = features.make_subd(box, doc=self.doc)
        self.doc.recompute()
        volume = stroke.Shape.Volume
        facets = subd.Mesh.CountFacets
        path = os.path.join(tempfile.gettempdir(), "freeform_persistence_test.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        stroke = self.doc.getObject("Stroke")
        ribbon = self.doc.getObject("Ribbon")
        subd = self.doc.getObject("SubD")
        self.assertEqual(stroke.Proxy.Type, "Freeform::Stroke")
        self.assertEqual(ribbon.Proxy.Type, "Freeform::Ribbon")
        self.assertEqual(subd.Proxy.Type, "Freeform::SubD")
        stroke.Smoothing = 3
        self.doc.recompute()
        self.assertTrue(stroke.Shape.isValid())
        self.assertNotAlmostEqual(stroke.Shape.Volume, volume, places=3)
        self.assertTrue(ribbon.Shape.isValid())
        self.assertEqual(subd.Mesh.CountFacets, facets)
        os.remove(path)

    def test_migration_adds_missing_properties(self):
        stroke = features.make_stroke(_wave(), doc=self.doc)
        stroke.removeProperty("TubeSections")
        stroke.Proxy.onDocumentRestored(stroke)
        self.assertTrue(hasattr(stroke, "TubeSections"))
        self.assertEqual(int(stroke.TubeSections), 6)
