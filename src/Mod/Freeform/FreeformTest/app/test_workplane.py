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

"""Tests for the drawing plane and the symmetry plane."""

import unittest

import Part
from FreeCAD import Vector

from freeform import workplane


class _FakeView:
    """Mimics the parts of View3DInventor used by point_from_screen."""

    def __init__(self, direction=Vector(0, 0, -1), hit=None):
        self.direction = direction
        self.hit = hit

    def getPoint(self, x, y):
        # the focal plane point: simple mapping of pixels to world units at z = 50
        return (float(x), float(y), 50.0)

    def getViewDirection(self):
        return (self.direction.x, self.direction.y, self.direction.z)

    def getUpDirection(self):
        return (0.0, 1.0, 0.0)

    def getObjectInfo(self, position):
        return self.hit


class TestWorkPlane(unittest.TestCase):
    def setUp(self):
        self.plane = workplane.WorkPlane()
        self.plane.set_mode("Top")
        self.plane.origin = Vector(0, 0, 0)
        self.plane.snap = False

    def tearDown(self):
        self.plane.set_mode("Top")
        self.plane.snap = False
        self.plane.save()

    def test_modes(self):
        self.plane.set_mode("Front")
        self.assertEqual(self.plane.normal, Vector(0, -1, 0))
        self.plane.set_mode("Side")
        self.assertEqual(self.plane.normal, Vector(1, 0, 0))
        with self.assertRaises(ValueError):
            self.plane.set_mode("Diagonal")

    def test_local_global_roundtrip(self):
        self.plane.set_axes(Vector(1, 2, 3), Vector(1, 1, 0), Vector(0, 0, 1))
        self.assertEqual(self.plane.mode, "Custom")
        p = Vector(4, -2, 7)
        local = self.plane.to_local(p)
        self.assertAlmostEqual((self.plane.to_global(local) - p).Length, 0.0, places=9)
        self.assertAlmostEqual(self.plane.u.dot(self.plane.normal), 0.0, places=9)
        self.assertAlmostEqual(self.plane.v.Length, 1.0, places=9)

    def test_intersect_ray_and_project(self):
        self.plane.set_mode("Front")
        hit = self.plane.intersect_ray(Vector(1, 10, 2), Vector(0, -1, 0))
        self.assertEqual(hit, Vector(1, 0, 2))
        parallel = self.plane.intersect_ray(Vector(1, 10, 2), Vector(1, 0, 0))
        self.assertEqual(parallel, Vector(1, 0, 2))
        self.assertEqual(self.plane.project(Vector(3, 7, 1)), Vector(3, 0, 1))

    def test_snap(self):
        self.plane.snap = True
        self.plane.grid = 5.0
        self.assertEqual(self.plane.snap_point(Vector(12, 3, 0)), Vector(10, 5, 0))
        self.plane.snap = False
        self.assertEqual(self.plane.snap_point(Vector(12, 3, 0)), Vector(12, 3, 0))

    def test_point_from_screen_top(self):
        view = _FakeView()
        point, hit = self.plane.point_from_screen(view, (10, 20))
        self.assertFalse(hit)
        self.assertEqual(point, Vector(10, 20, 0))

    def test_point_from_screen_surface(self):
        view = _FakeView(hit={"x": 1.0, "y": 2.0, "z": 3.0})
        self.plane.set_mode("Surface")
        point, hit = self.plane.point_from_screen(view, (10, 20))
        self.assertTrue(hit)
        self.assertEqual(point, Vector(1, 2, 3))
        # no geometry under the cursor: fall back to the plane
        view.hit = None
        point, hit = self.plane.point_from_screen(view, (10, 20))
        self.assertFalse(hit)
        self.assertEqual(point, Vector(10, 20, 0))

    def test_point_from_screen_view_mode(self):
        view = _FakeView()
        self.plane.set_mode("View")  # no GUI: axes stay, mode is View
        point, _ = self.plane.point_from_screen(view, (4, 6))
        self.assertEqual(point, Vector(4, 6, 50))
        self.assertEqual(self.plane.origin, Vector(4, 6, 50))

    def test_align_to_face(self):
        box = Part.makeBox(10, 10, 10)
        top = [f for f in box.Faces if abs(f.CenterOfMass.z - 10) < 1e-9][0]
        self.assertTrue(self.plane.align_to_face(top))
        self.assertEqual(self.plane.mode, "Custom")
        self.assertAlmostEqual(self.plane.normal.z, 1.0, places=9)
        self.assertAlmostEqual(self.plane.origin.z, 10.0, places=9)
        self.assertFalse(self.plane.align_to_face(box.Edges[0]))

    def test_placement_matches_axes(self):
        self.plane.set_mode("Front")
        placement = self.plane.placement()
        self.assertAlmostEqual(
            (placement.Rotation.multVec(Vector(0, 0, 1)) - self.plane.normal).Length, 0, places=9
        )

    def test_persistence(self):
        self.plane.set_mode("Side")
        self.plane.snap = True
        self.plane.grid = 2.5
        self.plane.save()
        other = workplane.WorkPlane()
        self.assertEqual(other.mode, "Side")
        self.assertTrue(other.snap)
        self.assertAlmostEqual(other.grid, 2.5)

    def test_local_polyline(self):
        corners = self.plane.local_polyline(10)
        self.assertEqual(len(corners), 4)
        self.assertEqual(corners[0], Vector(-5, -5, 0))


class TestSymmetryPlane(unittest.TestCase):
    def setUp(self):
        self.symmetry = workplane.SymmetryPlane()
        self.symmetry.set(Vector(0, 0, 0), Vector(1, 0, 0), enabled=False)

    def tearDown(self):
        self.symmetry.set(Vector(0, 0, 0), Vector(1, 0, 0), enabled=False)

    def test_mirror(self):
        self.assertEqual(self.symmetry.mirror_point(Vector(2, 1, 1)), Vector(-2, 1, 1))
        self.symmetry.set(Vector(0, 5, 0), Vector(0, 2, 0))
        self.assertEqual(self.symmetry.axis_name(), "XZ")
        self.assertEqual(self.symmetry.mirror([Vector(1, 7, 1)])[0], Vector(1, 3, 1))
        self.assertTrue(self.symmetry.is_on_plane(Vector(9, 5, 9)))
        self.assertFalse(self.symmetry.is_on_plane(Vector(9, 6, 9)))

    def test_axis_name(self):
        self.symmetry.set(Vector(0, 0, 0), Vector(0, 0, 3))
        self.assertEqual(self.symmetry.axis_name(), "XY")
        self.symmetry.set(Vector(0, 0, 0), Vector(1, 1, 0))
        self.assertEqual(self.symmetry.axis_name(), "Custom")

    def test_persistence(self):
        self.symmetry.set(Vector(1, 2, 3), Vector(0, 1, 0), enabled=True)
        other = workplane.SymmetryPlane()
        self.assertTrue(other.enabled)
        self.assertEqual(other.origin, Vector(1, 2, 3))
        self.assertEqual(other.normal, Vector(0, 1, 0))
        other.set_enabled(False)
        self.assertFalse(workplane.SymmetryPlane().enabled)

    def test_shared_instances(self):
        self.assertIs(workplane.get_symmetry_plane(), workplane.get_symmetry_plane())
        self.assertIs(workplane.get_work_plane(), workplane.get_work_plane())
