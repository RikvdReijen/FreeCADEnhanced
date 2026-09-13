# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the checks run on a toolpath before it leaves the desk."""

import math
import unittest

import Part
from FreeCAD import Vector

from roboprint import analysis, slicing, toolpath


class TestBeadGeometry(unittest.TestCase):
    """A bead is a stadium: a rectangle with a half circle on each side."""

    def test_bead_area_of_a_wide_bead(self):
        expected = (8.0 - 4.0) * 4.0 + math.pi * 2.0**2
        self.assertAlmostEqual(analysis.bead_area(8.0, 4.0), expected, places=6)

    def test_a_bead_no_wider_than_it_is_tall_is_a_disc(self):
        self.assertAlmostEqual(analysis.bead_area(4.0, 4.0), math.pi * 4.0, places=6)
        self.assertAlmostEqual(analysis.bead_area(2.0, 4.0), math.pi * 1.0, places=6)

    def test_extrusion_per_millimetre_is_the_area(self):
        self.assertAlmostEqual(
            analysis.extrusion_per_millimetre(8.0, 4.0), analysis.bead_area(8.0, 4.0)
        )

    def test_filament_length_matches_the_volume(self):
        length = analysis.filament_per_millimetre(8.0, 4.0, 1.75)
        section = math.pi * (1.75 / 2.0) ** 2
        self.assertAlmostEqual(length * section, analysis.bead_area(8.0, 4.0), places=6)


class TestOverhang(unittest.TestCase):
    """Overhang is how far a bead leans out over the one below it."""

    def toolpath_of(self, shape, layer_height=4.0, **options):
        layers, values = slicing.slice_mesh(shape, "Planar", layer_height, **options)
        paths = toolpath.generate_toolpath(
            layers, values, bead_width=6.0, layer_height=layer_height, perimeters=1
        )
        analysis.compute_overhangs(paths, layer_height)
        return paths

    def test_a_straight_wall_has_no_overhang(self):
        paths = self.toolpath_of(Part.makeBox(60, 40, 40))
        worst = max(p.overhang for path in paths for p in path.points)
        self.assertAlmostEqual(worst, 0.0, places=3)

    def test_overhang_is_not_sampling_dependent(self):
        coarse = self.toolpath_of(Part.makeBox(60, 40, 40), 4.0)
        fine = self.toolpath_of(Part.makeBox(60, 40, 40), 1.0)
        for paths in (coarse, fine):
            self.assertAlmostEqual(
                max(p.overhang for path in paths for p in path.points), 0.0, places=3
            )

    def test_an_inward_step_is_supported(self):
        # A cone narrowing as it rises always lands on the layer below.
        cone = Part.makeCone(30.0, 5.0, 40.0)
        paths = self.toolpath_of(cone)
        worst = max(p.overhang for path in paths for p in path.points)
        self.assertAlmostEqual(worst, 0.0, places=1)

    def test_an_outward_step_is_measured(self):
        # A cone widening as it rises leans out by atan(dr / dz): here the
        # radius grows 25 mm over 40 mm of height, so 32 degrees.
        cone = Part.makeCone(5.0, 30.0, 40.0)
        paths = self.toolpath_of(cone, 4.0)
        worst = max(p.overhang for path in paths for p in path.points)
        expected = math.degrees(math.atan2(25.0, 40.0))
        self.assertAlmostEqual(worst, expected, delta=3.0)

    def test_overhang_report_counts_the_points_over_the_limit(self):
        paths = self.toolpath_of(Part.makeCone(5.0, 30.0, 40.0))
        report = analysis.overhang_report(paths, 4.0, limit=10.0)
        self.assertGreater(report["over_limit"], 0)
        self.assertEqual(report["points"], sum(len(p.points) for p in paths))
        self.assertAlmostEqual(
            report["fraction"], report["over_limit"] / report["points"], places=6
        )


class TestReports(unittest.TestCase):
    """Tilt, clearance, continuity, cornering and surface tolerance."""

    def setUp(self):
        self.shape = Part.makeBox(100, 80, 20)
        layers, values = slicing.slice_mesh(self.shape, "Planar", 4.0)
        self.paths = toolpath.generate_toolpath(
            layers, values, bead_width=8.0, layer_height=4.0, perimeters=1
        )

    def test_tilt_report_of_an_upright_nozzle(self):
        report = analysis.tilt_report(self.paths, Vector(0, 0, 1), limit=45.0)
        self.assertAlmostEqual(report["max"], 0.0, places=6)
        self.assertEqual(report["over_limit"], 0)

    def test_tilt_report_notices_a_leaning_nozzle(self):
        for path in self.paths:
            for point in path.points:
                point.axis = Vector(1, 0, 1)
        report = analysis.tilt_report(self.paths, Vector(0, 0, 1), limit=20.0)
        self.assertAlmostEqual(report["max"], 45.0, places=3)
        self.assertGreater(report["over_limit"], 0)

    def test_continuity_counts_the_travels(self):
        report = analysis.continuity_report(self.paths)
        self.assertEqual(report["interruptions"], max(0, len(self.paths) - 1))
        self.assertGreaterEqual(report["travel_length"], 0.0)

    def test_cornering_finds_the_box_corners(self):
        report = analysis.cornering_report(self.paths, threshold=60.0)
        self.assertGreaterEqual(report["corners"], 4)
        self.assertGreater(report["sharpest"], 60.0)

    def test_surface_tolerance_of_a_perimeter(self):
        # The perimeter sits half a bead in, so it is 4 mm off the wall; the
        # first and last layers sit closer than that to the end faces.
        report = analysis.surface_tolerance_report(self.paths, self.shape)
        self.assertAlmostEqual(report["max"], 4.0, delta=0.01)
        self.assertGreater(report["mean"], 0.0)
        self.assertLessEqual(report["mean"], report["max"] + 1e-9)

    def test_surface_tolerance_measures_against_the_skin(self):
        # Measuring against the solid instead would report zero for every
        # point, since they all lie inside it.
        report = analysis.surface_tolerance_report(self.paths, self.shape)
        self.assertGreater(report["points"], 0)
        self.assertGreater(report["max"], 0.0)

    def test_an_upright_planar_print_never_collides(self):
        # The head stands up from the nozzle and everything printed so far
        # is below it, so no size of nozzle can be in the way.
        report = analysis.clearance_report(self.paths, tool_radius=40.0, tool_length=120.0)
        self.assertEqual(report["hits"], 0)

    def overhanging_paths(self):
        # A shelf printed first, then a run directly underneath it: the head
        # standing up from the lower run passes through the shelf.
        high = toolpath.Path([toolpath.PrintPoint(Vector(x, 0, 60.0)) for x in range(0, 200, 2)])
        low = toolpath.Path([toolpath.PrintPoint(Vector(x, 0, 0.0)) for x in range(0, 200, 2)])
        return [high, low]

    def test_clearance_finds_the_head_running_into_a_shelf(self):
        report = analysis.clearance_report(
            self.overhanging_paths(), tool_radius=10.0, tool_length=120.0
        )
        self.assertGreater(report["hits"], 0)
        self.assertIsNotNone(report["first"])
        self.assertAlmostEqual(report["first"].z, 0.0, places=6)

    def test_a_short_head_clears_the_shelf(self):
        report = analysis.clearance_report(
            self.overhanging_paths(), tool_radius=10.0, tool_length=40.0
        )
        self.assertEqual(report["hits"], 0)

    def test_quality_report_has_every_check(self):
        report = analysis.quality_report(self.paths, 4.0, shape=self.shape)
        for key in ("overhang", "cornering", "continuity", "tilt", "surface_tolerance"):
            self.assertIn(key, report)

    def test_quality_report_without_a_shape_skips_the_tolerance(self):
        report = analysis.quality_report(self.paths, 4.0)
        self.assertNotIn("surface_tolerance", report)


class TestEstimate(unittest.TestCase):
    """Length, volume, mass and time of a toolpath."""

    def setUp(self):
        layers, values = slicing.slice_mesh(Part.makeBox(100, 80, 20), "Planar", 4.0)
        self.paths = toolpath.generate_toolpath(
            layers, values, bead_width=8.0, layer_height=4.0, perimeters=1
        )

    def test_volume_follows_the_bead_area(self):
        estimate = analysis.estimate(self.paths)
        expected = estimate["printing_length"] * analysis.bead_area(8.0, 4.0)
        self.assertAlmostEqual(estimate["volume"], expected, delta=expected * 0.01)

    def test_mass_follows_the_density(self):
        light = analysis.estimate(self.paths, density=1.0)
        heavy = analysis.estimate(self.paths, density=2.0)
        self.assertAlmostEqual(heavy["mass"], 2 * light["mass"], places=6)

    def test_time_follows_the_speed(self):
        # Each point carries its own speed, which is what the robot is told,
        # so that is what the estimate has to follow.
        for path in self.paths:
            for point in path.points:
                point.speed = 1000.0
        slow = analysis.estimate(self.paths, travel_speed=1000.0)
        for path in self.paths:
            for point in path.points:
                point.speed = 2000.0
        fast = analysis.estimate(self.paths, travel_speed=2000.0)
        self.assertAlmostEqual(slow["hours"], 2 * fast["hours"], places=6)

    def test_an_empty_toolpath_estimates_to_nothing(self):
        estimate = analysis.estimate([])
        self.assertAlmostEqual(estimate["printing_length"], 0.0)
        self.assertAlmostEqual(estimate["hours"], 0.0)


if __name__ == "__main__":
    unittest.main()
