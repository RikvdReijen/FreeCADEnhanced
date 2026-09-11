# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the slicer: the fields, the marching triangles and the contours."""

import math
import unittest

import FreeCAD
import Part
from FreeCAD import Vector

from roboprint import slicing


def box(length=60.0, width=40.0, height=20.0):
    return Part.makeBox(length, width, height)


class TestMeshAndRefinement(unittest.TestCase):
    """A shape becomes a triangle mesh fine enough for a curved field."""

    def test_mesh_of_shape(self):
        points, triangles = slicing.mesh_of(box())
        self.assertGreaterEqual(len(points), 8)
        self.assertGreaterEqual(len(triangles), 12)

    def test_refinement_reaches_the_target_edge(self):
        points, triangles = slicing.mesh_of(box())
        self.assertGreater(slicing.max_edge_length(points, triangles), 5.0)
        points, triangles = slicing.refine_mesh(points, triangles, 5.0)
        self.assertLessEqual(slicing.max_edge_length(points, triangles), 5.0 + 1e-6)

    def test_refinement_stays_watertight(self):
        points, triangles = slicing.mesh_of(box(10, 10, 10))
        points, triangles = slicing.refine_mesh(points, triangles, 3.0)
        edges = {}
        for a, b, c in triangles:
            for start, end in ((a, b), (b, c), (c, a)):
                key = (min(start, end), max(start, end))
                edges[key] = edges.get(key, 0) + 1
        self.assertTrue(all(count == 2 for count in edges.values()))

    def test_refinement_leaves_a_fine_mesh_alone(self):
        points, triangles = slicing.mesh_of(box(10, 10, 10))
        points, triangles = slicing.refine_mesh(points, triangles, 3.0)
        count = len(triangles)
        again, triangles = slicing.refine_mesh(points, triangles, 3.0)
        self.assertEqual(len(triangles), count)

    def test_refinement_only_splits_what_is_too_coarse(self):
        # A long thin box has a few long edges and many short ones; splitting
        # everything four ways would cost far more than bringing those down.
        points, triangles = slicing.mesh_of(box(400, 2, 2))
        before = len(triangles)
        points, triangles = slicing.refine_mesh(points, triangles, 4.0)
        self.assertLessEqual(slicing.max_edge_length(points, triangles), 4.0 + 1e-6)
        # Uniform four-way subdivision would need 7 passes, so 4**7 times more.
        self.assertLess(len(triangles), before * 4**7)

    def test_refinement_respects_its_budget(self):
        points, triangles = slicing.mesh_of(box(1000, 1000, 1000))
        points, triangles = slicing.refine_mesh(points, triangles, 0.001, budget=2000)
        self.assertLessEqual(len(triangles), 2000 * 4)


class TestFields(unittest.TestCase):
    """Every slicing mode is one scalar field over space."""

    def test_planar_field_is_the_height(self):
        field = slicing.planar_field()
        self.assertAlmostEqual(field(Vector(3, 4, 7)), 7.0)

    def test_cylindrical_field_is_the_radius(self):
        field = slicing.cylindrical_field()
        self.assertAlmostEqual(field(Vector(3, 4, 99)), 5.0)

    def test_spherical_field_is_the_distance(self):
        field = slicing.spherical_field(Vector(1, 0, 0))
        self.assertAlmostEqual(field(Vector(4, 4, 0)), 5.0)

    def test_conical_field_leans_by_the_angle(self):
        field = slicing.conical_field(30.0)
        # A point one cone-slope up from the axis sits on the same layer.
        self.assertAlmostEqual(field(Vector(0, 0, 0)), 0.0)
        self.assertAlmostEqual(field(Vector(10, 0, 10 * math.tan(math.radians(30.0)))), 0.0)

    def test_field_for_mode_covers_every_mode(self):
        for mode in slicing.SLICING_MODES:
            surface = box() if mode == "Conformal" else None
            field = slicing.field_for_mode(mode, surface=surface)
            self.assertTrue(callable(field), mode)


class TestSlicing(unittest.TestCase):
    """Slicing a known shape gives contours of a known size."""

    def test_planar_layer_perimeter(self):
        layers, values = slicing.slice_mesh(box(), "Planar", 5.0)
        self.assertGreater(len(layers), 1)
        self.assertEqual(len(layers), len(values))
        middle = layers[len(layers) // 2]
        self.assertEqual(len(middle), 1)
        self.assertAlmostEqual(middle[0].length(), 2 * (60.0 + 40.0), places=3)
        self.assertTrue(middle[0].closed)

    def test_layer_spacing_matches_the_layer_height(self):
        _, values = slicing.slice_mesh(box(), "Planar", 5.0)
        gaps = [b - a for a, b in zip(values, values[1:])]
        for gap in gaps:
            self.assertAlmostEqual(gap, 5.0, places=6)

    def test_first_layer_offsets_the_start(self):
        # Without it the first layer sits half a layer up; first_layer puts
        # it exactly that far above the bottom of the part instead.
        _, plain = slicing.slice_mesh(box(), "Planar", 5.0)
        _, offset = slicing.slice_mesh(box(), "Planar", 5.0, first_layer=2.0)
        self.assertAlmostEqual(offset[0] - plain[0], 2.0 - 5.0 * 0.5, places=6)

    def test_cylindrical_layer_is_a_circle(self):
        cylinder = Part.makeCylinder(25.0, 30.0)
        layers, values = slicing.slice_mesh(cylinder, "Cylindrical", 5.0)
        outermost = layers[-1]
        # The layers step by the layer height, so the last one lands within
        # one layer of the wall. A cylinder cut at a radius smaller than its
        # own gives one circle on the bottom face and one on the top.
        radius = max(values)
        self.assertAlmostEqual(radius, 25.0, delta=5.0)
        self.assertEqual(len(outermost), 2)
        for contour in outermost:
            self.assertAlmostEqual(contour.length(), 2 * math.pi * radius, delta=radius * 0.1)
        heights = sorted(contour.points[0].z for contour in outermost)
        self.assertAlmostEqual(heights[0], 0.0, delta=0.5)
        self.assertAlmostEqual(heights[1], 30.0, delta=0.5)

    def test_conical_layers_are_not_flat(self):
        layers, values = slicing.slice_mesh(box(60, 60, 20), "Conical", 4.0, angle=30.0)
        self.assertTrue(layers)
        spread = []
        for layer in layers:
            for contour in layer:
                heights = [p.z for p in contour.points]
                spread.append(max(heights) - min(heights))
        self.assertGreater(max(spread), 5.0)

    def test_conical_layers_sit_on_their_cone(self):
        layers, values = slicing.slice_mesh(box(60, 60, 20), "Conical", 4.0, angle=30.0)
        field = slicing.conical_field(30.0)
        worst = 0.0
        for layer, value in zip(layers, values):
            for contour in layer:
                for point in contour.points:
                    worst = max(worst, abs(field(point) - value))
        self.assertLess(worst, 0.1)

    def test_a_hole_gives_a_second_contour(self):
        tube = Part.makeCylinder(25.0, 20.0).cut(Part.makeCylinder(15.0, 20.0))
        layers, _ = slicing.slice_mesh(tube, "Planar", 5.0)
        middle = layers[len(layers) // 2]
        self.assertEqual(len(middle), 2)

    def test_contours_are_simplified_to_the_tessellation_tolerance(self):
        # Marching triangles emits a point per crossed edge; a box layer is
        # four corners however fine the mesh underneath it is.
        layers, _ = slicing.slice_mesh(box(), "Planar", 5.0, tolerance=0.1)
        middle = layers[len(layers) // 2][0]
        self.assertEqual(len(middle.points), 4)
        self.assertAlmostEqual(middle.length(), 2 * (60.0 + 40.0), places=3)

    def test_simplification_stays_within_its_deviation(self):
        cylinder = Part.makeCylinder(50.0, 20.0)
        layers, _ = slicing.slice_mesh(cylinder, "Planar", 5.0, tolerance=0.05)
        contour = layers[len(layers) // 2][0]
        for point in contour.points:
            self.assertAlmostEqual(Vector(point.x, point.y, 0).Length, 50.0, delta=0.2)

    def test_a_curved_slice_does_not_explode_in_points(self):
        cone = Part.makeCone(25.0, 90.0, 60.0)
        layers, _ = slicing.slice_mesh(cone, "Conical", 5.0, angle=35.0)
        total = sum(len(c.points) for layer in layers for c in layer)
        self.assertGreater(total, 0)
        self.assertLess(total, 20000)

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            slicing.slice_mesh(([], []), "Planar", 1.0)


class TestContours(unittest.TestCase):
    """Contours can be oriented, nested, resampled and smoothed."""

    def square(self, size=10.0, clockwise=False):
        points = [
            Vector(0, 0, 0),
            Vector(size, 0, 0),
            Vector(size, size, 0),
            Vector(0, size, 0),
            Vector(0, 0, 0),
        ]
        if clockwise:
            points.reverse()
        return slicing.Contour(points, closed=True)

    def test_area_and_normal(self):
        contour = self.square()
        self.assertAlmostEqual(abs(slicing.contour_area(contour)), 100.0, places=6)
        self.assertAlmostEqual(slicing.contour_normal(contour).z, 1.0, places=6)

    def test_orient_contour_flips_a_clockwise_loop(self):
        contour = slicing.orient_contour(self.square(clockwise=True), Vector(0, 0, 1))
        self.assertGreater(slicing.contour_area(contour, Vector(0, 0, 1)), 0.0)

    def test_nesting_marks_the_hole(self):
        outer = self.square(20.0)
        inner = slicing.Contour(
            [
                Vector(5, 5, 0),
                Vector(15, 5, 0),
                Vector(15, 15, 0),
                Vector(5, 15, 0),
                Vector(5, 5, 0),
            ],
            closed=True,
        )
        nested = slicing.nest_contours([inner, outer], Vector(0, 0, 1))
        # One island, holding one hole.
        self.assertEqual(len(nested), 1)
        island, holes = nested[0]
        self.assertAlmostEqual(abs(slicing.contour_area(island)), 400.0, places=6)
        self.assertEqual(len(holes), 1)
        self.assertAlmostEqual(abs(slicing.contour_area(holes[0])), 100.0, places=6)

    def test_resampling_keeps_the_length(self):
        contour = self.square(20.0)
        resampled = slicing.resample_contour(contour, 1.0)
        self.assertGreater(len(resampled.points), len(contour.points))
        self.assertAlmostEqual(resampled.length(), contour.length(), delta=0.2)

    def test_simplification_drops_collinear_points(self):
        points = [Vector(x, 0, 0) for x in range(0, 51, 5)] + [Vector(50, 20, 0)]
        contour = slicing.Contour(points, closed=False)
        simplified = slicing.simplify_contour(contour, 0.01)
        self.assertEqual(len(simplified.points), 3)
        self.assertAlmostEqual(simplified.length(), contour.length(), places=6)

    def test_simplification_keeps_what_it_cannot_drop(self):
        contour = self.square(20.0)
        simplified = slicing.simplify_contour(contour, 0.01)
        self.assertEqual(len(simplified.points), len(contour.points))

    def test_smoothing_shrinks_a_sharp_loop(self):
        contour = self.square(20.0)
        smoothed = slicing.smooth_contour(contour, iterations=3)
        self.assertLess(smoothed.length(), contour.length())

    def test_seam_alignment_moves_the_start(self):
        layers, _ = slicing.slice_mesh(box(), "Planar", 5.0)
        aligned = slicing.align_seams([list(layer) for layer in layers], "Aligned")
        starts = [layer[0].points[0] for layer in aligned if layer]
        reference = starts[0]
        for start in starts[1:]:
            self.assertLess(Vector(start.x - reference.x, start.y - reference.y, 0).Length, 6.0)


if __name__ == "__main__":
    unittest.main()
