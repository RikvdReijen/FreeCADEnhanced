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
"""Snapping: priority, the scale-aware radius, angles and "no snap"."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsketch import snapping as sn                            # noqa: E402
from xrsketch import vecmath as vm                             # noqa: E402
from xrsketch.curves import Curve3D                            # noqa: E402
from xrsketch.subd import cube_cage                            # noqa: E402


def settings(**kw):
    base = dict(grid=False, vertex=False, midpoint=False, face_center=False,
                curve_end=False, tangent=False, angle=False, symmetry=False,
                radius=0.02, grid_size=0.01)
    base.update(kw)
    return sn.SnapSettings(**base)


class TestPureFunctions(unittest.TestCase):

    def test_snap_to_grid(self):
        self.assertEqual(sn.snap_to_grid((0.013, -0.006, 0.0), 0.01),
                         (0.01, -0.01, 0.0))
        self.assertEqual(sn.snap_to_grid((1.0, 2.0, 3.0), 0.0),
                         (1.0, 2.0, 3.0))
        self.assertEqual(sn.snap_to_grid((1.0, 2.0, 3.0), None),
                         (1.0, 2.0, 3.0))

    def test_snap_to_plane(self):
        p = sn.snap_to_plane((1.0, 2.0, 3.0), (0.0, 0.0, 0.0),
                             (0.0, 0.0, 1.0))
        self.assertEqual(p, (1.0, 2.0, 0.0))
        self.assertEqual(sn.snap_to_plane((1.0, 2.0, 3.0), (0, 0, 0),
                                          (0, 0, 0)), (1.0, 2.0, 3.0))

    def test_angle_snapping_lands_on_exact_increments(self):
        step = math.pi / 4.0
        for degrees in (3.0, 20.0, 44.0, 46.0, 89.0, 130.0, -20.0):
            p = (math.cos(math.radians(degrees)),
                 math.sin(math.radians(degrees)), 0.0)
            snapped = sn.snap_angle(p, (0.0, 0.0, 0.0), step, (0, 0, 1))
            angle = math.atan2(snapped[1], snapped[0])
            k = angle / step
            self.assertAlmostEqual(k, round(k), places=12,
                                   msg="%g degrees" % degrees)
            # the radius is preserved
            self.assertAlmostEqual(vm.length(snapped), 1.0, places=12)

    def test_angle_snapping_keeps_the_out_of_plane_part(self):
        p = (1.0, 0.1, 0.7)
        snapped = sn.snap_angle(p, (0, 0, 0), math.pi / 2.0, (0, 0, 1))
        self.assertAlmostEqual(snapped[2], 0.7, places=12)
        self.assertAlmostEqual(snapped[1], 0.0, places=12)

    def test_angle_snapping_picks_a_plane_when_none_is_given(self):
        snapped = sn.snap_angle((1.0, 0.1, 0.0), (0, 0, 0), math.pi / 2.0)
        self.assertAlmostEqual(snapped[1], 0.0, places=12)
        self.assertAlmostEqual(snapped[2], 0.0, places=12)

    def test_degenerate_angle_snaps_return_none(self):
        self.assertIsNone(sn.snap_angle((0, 0, 0), (0, 0, 0), math.pi / 4.0))
        self.assertIsNone(sn.snap_angle((1, 0, 0), (0, 0, 0), 0.0))
        self.assertIsNone(sn.snap_angle((0, 0, 1), (0, 0, 0), math.pi / 4.0,
                                        (0, 0, 1)))


class TestNoSnap(unittest.TestCase):

    def test_nothing_in_range_returns_the_point_unchanged(self):
        engine = sn.SnapEngine(settings(vertex=True))
        targets = sn.SnapTargets().add_vertex((5.0, 5.0, 5.0))
        result = engine.snap((0.1, 0.2, 0.3), targets)
        self.assertFalse(result.snapped)
        self.assertIsNone(result.kind)
        self.assertIsNone(result.target)
        self.assertEqual(result.point, (0.1, 0.2, 0.3))
        self.assertEqual(result.distance, 0.0)
        self.assertEqual(tuple(result), (0.1, 0.2, 0.3))

    def test_disabled_snapping_never_moves_the_point(self):
        engine = sn.SnapEngine(settings(vertex=True, grid=True))
        engine.settings.enabled = False
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0))
        result = engine.snap((0.001, 0.0, 0.0), targets)
        self.assertFalse(result.snapped)
        self.assertEqual(result.point, (0.001, 0.0, 0.0))

    def test_a_zero_radius_disables_snapping(self):
        engine = sn.SnapEngine(settings(vertex=True, radius=0.0))
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0))
        self.assertFalse(engine.snap((0.001, 0.0, 0.0), targets).snapped)

    def test_no_targets_at_all(self):
        engine = sn.SnapEngine(settings(vertex=True, midpoint=True))
        self.assertFalse(engine.snap((0.5, 0.5, 0.5)).snapped)


class TestPriority(unittest.TestCase):

    def _engine(self):
        return sn.SnapEngine(sn.SnapSettings(radius=0.05, grid_size=0.01,
                                             angle_step=math.pi / 4.0))

    def test_the_documented_order(self):
        self.assertEqual(sn.SnapEngine.ORDER,
                         ("vertex", "curve_end", "midpoint", "face_center",
                          "tangent", "symmetry", "angle", "grid"))

    def test_a_vertex_beats_the_grid_even_when_further_away(self):
        engine = self._engine()
        targets = sn.SnapTargets().add_vertex((0.017, 0.0, 0.0))
        result = engine.snap((0.0102, 0.0, 0.0), targets)
        self.assertEqual(result.kind, "vertex")
        self.assertEqual(result.point, (0.017, 0.0, 0.0))

    def test_a_curve_end_beats_a_midpoint_and_a_face_centre(self):
        engine = self._engine()
        targets = sn.SnapTargets()
        targets.add_curve_end((0.02, 0.0, 0.0))
        targets.add_edge((0.0, 0.0, 0.0), (0.02, 0.0, 0.0))
        targets.add_face([(0.0, 0.0, 0.0), (0.02, 0.0, 0.0),
                          (0.02, 0.02, 0.0), (0.0, 0.02, 0.0)])
        self.assertEqual(engine.snap((0.011, 0.001, 0.0), targets).kind,
                         "curve_end")

    def test_a_midpoint_beats_a_face_centre(self):
        engine = self._engine()
        targets = sn.SnapTargets()
        targets.add_edge((0.0, 0.0, 0.0), (0.02, 0.0, 0.0))
        targets.add_face([(0.0, 0.0, 0.0), (0.02, 0.0, 0.0),
                          (0.02, 0.02, 0.0), (0.0, 0.02, 0.0)])
        result = engine.snap((0.011, 0.002, 0.0), targets)
        self.assertEqual(result.kind, "midpoint")
        self.assertEqual(result.point, (0.01, 0.0, 0.0))

    def test_symmetry_beats_angle_and_grid(self):
        engine = self._engine()
        targets = sn.SnapTargets().add_symmetry_plane((0.0, 0.0, 0.0),
                                                      (1.0, 0.0, 0.0))
        result = engine.snap((0.004, 0.5, 0.5), targets,
                             origin=(0.0, 0.0, 0.0))
        self.assertEqual(result.kind, "symmetry")
        self.assertAlmostEqual(result.point[0], 0.0, places=12)

    def test_ties_within_a_kind_are_broken_by_distance(self):
        engine = self._engine()
        targets = sn.SnapTargets()
        targets.add_vertex((0.03, 0.0, 0.0))
        targets.add_vertex((0.012, 0.0, 0.0))
        self.assertEqual(engine.snap((0.01, 0.0, 0.0), targets).point,
                         (0.012, 0.0, 0.0))

    def test_switching_a_kind_off_falls_through_to_the_next(self):
        engine = self._engine()
        engine.settings.vertex = False
        targets = sn.SnapTargets().add_vertex((0.017, 0.0, 0.0))
        result = engine.snap((0.0102, 0.0, 0.0), targets)
        self.assertEqual(result.kind, "grid")
        self.assertEqual(result.point, (0.01, 0.0, 0.0))


class TestUserScale(unittest.TestCase):
    """The radius is hand travel, so it shrinks with the user."""

    def test_effective_radius_divides_by_the_user_scale(self):
        s = sn.SnapSettings(radius=0.024)
        self.assertAlmostEqual(s.effective_radius(1.0), 0.024, places=12)
        self.assertAlmostEqual(s.effective_radius(12.0), 0.002, places=12)
        self.assertAlmostEqual(s.effective_radius(0.5), 0.048, places=12)

    def test_bad_user_scales_fall_back_to_life_size(self):
        s = sn.SnapSettings(radius=0.02)
        for bad in (0.0, -3.0, None, "nonsense", float("inf")):
            self.assertAlmostEqual(s.effective_radius(bad), 0.02, places=12)

    def test_a_shrunk_user_snaps_more_finely(self):
        engine = sn.SnapEngine(settings(vertex=True, radius=0.02))
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0))
        probe = (0.015, 0.0, 0.0)
        self.assertTrue(engine.snap(probe, targets, user_scale=1.0).snapped)
        self.assertFalse(engine.snap(probe, targets, user_scale=12.0).snapped)
        near = (0.001, 0.0, 0.0)
        self.assertTrue(engine.snap(near, targets, user_scale=12.0).snapped)

    def test_a_grown_user_snaps_more_coarsely(self):
        engine = sn.SnapEngine(settings(vertex=True, radius=0.02))
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0))
        probe = (0.03, 0.0, 0.0)
        self.assertFalse(engine.snap(probe, targets, user_scale=1.0).snapped)
        self.assertTrue(engine.snap(probe, targets, user_scale=0.5).snapped)


class TestTargets(unittest.TestCase):

    def test_a_cage_contributes_vertices_edges_and_faces(self):
        targets = sn.SnapTargets().add_cage(cube_cage(2.0))
        self.assertEqual(len(targets.vertices), 8)
        self.assertEqual(len(targets.faces), 6)
        self.assertEqual(len(targets.edges), 24)     # each edge twice
        engine = sn.SnapEngine(settings(face_center=True, radius=0.2))
        result = engine.snap((0.1, 0.0, -1.0), targets)
        self.assertEqual(result.kind, "face_center")
        self.assertAlmostEqual(vm.dist(result.point, (0.0, 0.0, -1.0)), 0.0,
                               places=12)

    def test_a_curve_contributes_its_ends_and_tangents(self):
        curve = Curve3D.from_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)],
                                    smooth=False)
        targets = sn.SnapTargets().add_curve(curve)
        self.assertEqual(len(targets.vertices), 3)
        self.assertEqual(len(targets.curve_ends), 2)
        self.assertEqual(len(targets.tangents), 2)

    def test_tangent_snapping_only_looks_forwards(self):
        engine = sn.SnapEngine(settings(tangent=True, radius=0.05))
        targets = sn.SnapTargets()
        targets.tangents.append(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), None))
        ahead = engine.snap((0.5, 0.01, 0.0), targets)
        self.assertEqual(ahead.kind, "tangent")
        self.assertAlmostEqual(ahead.point[1], 0.0, places=12)
        behind = engine.snap((-0.5, 0.01, 0.0), targets)
        self.assertFalse(behind.snapped)

    def test_exclude_drops_an_object_from_the_candidates(self):
        owner = object()
        engine = sn.SnapEngine(settings(vertex=True, radius=0.05))
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0), owner)
        self.assertTrue(engine.snap((0.01, 0.0, 0.0), targets).snapped)
        self.assertFalse(engine.snap((0.01, 0.0, 0.0), targets,
                                     exclude=owner).snapped)

    def test_owners_are_reported_back(self):
        owner = object()
        engine = sn.SnapEngine(settings(vertex=True, radius=0.05))
        targets = sn.SnapTargets().add_vertex((0.0, 0.0, 0.0), owner)
        result = engine.snap((0.01, 0.0, 0.0), targets)
        self.assertIs(result.target, owner)
        self.assertEqual(result.index, 0)
        self.assertAlmostEqual(result.distance, 0.01, places=12)

    def test_from_objects_duck_types(self):
        class _Obj(object):
            def __init__(self, data):
                self.data = data
        targets = sn.SnapTargets.from_objects(
            [_Obj(cube_cage(1.0)),
             _Obj(Curve3D.from_points([(0, 0, 0), (1, 0, 0)], smooth=False))])
        self.assertEqual(len(targets.vertices), 10)
        self.assertEqual(len(targets.faces), 6)

    def test_settings_copy_and_dict(self):
        s = sn.SnapSettings(radius=0.03)
        d = s.to_dict()
        self.assertEqual(d["radius"], 0.03)
        clone = s.copy()
        clone.radius = 0.1
        self.assertEqual(s.radius, 0.03)


class TestAngleAndGridTogether(unittest.TestCase):

    def test_angle_snapping_needs_an_origin(self):
        engine = sn.SnapEngine(settings(angle=True, radius=0.5,
                                        angle_step=math.pi / 4.0))
        self.assertFalse(engine.snap((1.0, 0.1, 0.0)).snapped)
        result = engine.snap((1.0, 0.1, 0.0), origin=(0.0, 0.0, 0.0),
                             plane_normal=(0.0, 0.0, 1.0))
        self.assertEqual(result.kind, "angle")
        self.assertAlmostEqual(result.point[1], 0.0, places=12)

    def test_angle_beats_grid(self):
        engine = sn.SnapEngine(sn.SnapSettings(
            vertex=False, midpoint=False, face_center=False, curve_end=False,
            tangent=False, symmetry=False, radius=0.5, grid_size=0.1,
            angle_step=math.pi / 4.0))
        result = engine.snap((1.0, 0.02, 0.0), origin=(0.0, 0.0, 0.0),
                             plane_normal=(0.0, 0.0, 1.0))
        self.assertEqual(result.kind, "angle")


if __name__ == "__main__":
    unittest.main()
