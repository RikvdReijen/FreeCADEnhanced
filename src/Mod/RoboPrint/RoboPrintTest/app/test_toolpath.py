# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the toolpath: offsets, infill, ordering, spiral and tool axes."""

import math
import unittest

import Part
from FreeCAD import Vector

from roboprint import slicing, toolpath


def square(size=100.0, z=0.0):
    half = size / 2.0
    points = [
        Vector(-half, -half, z),
        Vector(half, -half, z),
        Vector(half, half, z),
        Vector(-half, half, z),
        Vector(-half, -half, z),
    ]
    return slicing.Contour(points, closed=True)


class TestOffsets(unittest.TestCase):
    """A perimeter is the contour walked inwards by half a bead."""

    def test_offset_shrinks_the_outline(self):
        offset = toolpath.offset_contour(square(100.0), -6.0, Vector(0, 0, 1))
        self.assertEqual(len(offset), 1)
        self.assertAlmostEqual(offset[0].length(), 4 * (100.0 - 12.0), delta=1.0)

    def test_offset_of_a_hole_goes_the_other_way(self):
        tube = Part.makeCylinder(25.0, 20.0).cut(Part.makeCylinder(15.0, 20.0))
        layers, _ = slicing.slice_mesh(tube, "Planar", 5.0)
        middle = layers[len(layers) // 2]
        inset = toolpath.offset_layer(middle, Vector(0, 0, 1), 5.0)
        lengths = sorted(round(c.length()) for c in inset)
        # Outer 25 -> 20, inner 15 -> 20: both circles end up at radius 20.
        self.assertEqual(len(lengths), 2)
        for length in lengths:
            self.assertAlmostEqual(length, 2 * math.pi * 20.0, delta=8.0)

    def test_a_collapsed_offset_is_dropped(self):
        self.assertEqual(toolpath.offset_contour(square(4.0), -10.0, Vector(0, 0, 1)), [])


class TestInfill(unittest.TestCase):
    """The inside of a layer is filled with one of the patterns."""

    def test_lines_stay_inside_the_outline(self):
        paths = toolpath.infill_paths([square(100.0)], 10.0, "Lines", 0.0)
        self.assertTrue(paths)
        for path in paths:
            for point in path.points:
                self.assertLessEqual(abs(point.position.x), 50.0 + 1e-6)
                self.assertLessEqual(abs(point.position.y), 50.0 + 1e-6)

    def test_every_pattern_produces_something(self):
        for pattern in toolpath.INFILL_PATTERNS:
            paths = toolpath.infill_paths([square(100.0)], 10.0, pattern, 0.0)
            if pattern == "None":
                self.assertEqual(paths, [], pattern)
            else:
                self.assertTrue(paths, pattern)

    def test_grid_has_both_directions(self):
        paths = toolpath.infill_paths([square(100.0)], 20.0, "Grid", 0.0)
        angles = set()
        for path in paths:
            start, end = path.points[0].position, path.points[-1].position
            angles.add(round(math.degrees(math.atan2(end.y - start.y, end.x - start.x))) % 180)
        self.assertGreaterEqual(len(angles), 2)

    def test_infill_is_lifted_onto_a_curved_layer(self):
        field = slicing.conical_field(30.0)
        paths = toolpath.infill_paths([square(60.0)], 10.0, "Lines", 0.0, field=field, value=0.0)
        self.assertTrue(paths)
        for path in paths:
            for point in path.points:
                self.assertAlmostEqual(field(point.position), 0.0, places=3)

    def test_spacing_of_zero_gives_nothing(self):
        self.assertEqual(toolpath.infill_paths([square()], 0.0, "Lines", 0.0), [])


class TestOrdering(unittest.TestCase):
    """Paths are printed in an order that keeps the travel short."""

    def path_at(self, x):
        points = [toolpath.PrintPoint(Vector(x, 0, 0)), toolpath.PrintPoint(Vector(x + 1, 0, 0))]
        return toolpath.Path(points)

    def test_nearest_path_comes_first(self):
        paths = [self.path_at(100.0), self.path_at(5.0), self.path_at(50.0)]
        ordered = toolpath.order_paths(paths, Vector(0, 0, 0))
        self.assertAlmostEqual(ordered[0].points[0].position.x, 5.0)

    def test_ordering_keeps_every_path(self):
        paths = [self.path_at(x) for x in (0.0, 30.0, 60.0, 90.0)]
        self.assertEqual(len(toolpath.order_paths(paths)), 4)


class TestSpiral(unittest.TestCase):
    """A stack of loops becomes one helix so the extruder never stops."""

    def test_spiral_joins_the_loops_and_ramps_the_height(self):
        layers = [square(50.0, z) for z in (0.0, 4.0, 8.0, 12.0)]
        paths = [
            toolpath.Path([toolpath.PrintPoint(p) for p in layer.points], closed=True)
            for layer in layers
        ]
        spiralled = toolpath.spiralize(paths, [0.0, 4.0, 8.0, 12.0])
        self.assertEqual(len(spiralled), 1)
        heights = [p.position.z for p in spiralled[0].points]
        self.assertAlmostEqual(min(heights), 0.0, delta=0.5)
        self.assertGreater(max(heights) - min(heights), 8.0)
        self.assertFalse(spiralled[0].closed)


class TestToolAxes(unittest.TestCase):
    """The nozzle is aimed by the mode, and never past the tilt limit."""

    def layer_paths(self):
        return [toolpath.Path([toolpath.PrintPoint(p) for p in square(50.0).points], closed=True)]

    def test_fixed_mode_keeps_the_build_axis(self):
        paths = self.layer_paths()
        toolpath.apply_tool_axes(paths, "Fixed", fixed_axis=Vector(0, 0, 1))
        for point in paths[0].points:
            self.assertAlmostEqual(point.axis.getAngle(Vector(0, 0, 1)), 0.0, places=6)

    def test_layer_normal_follows_a_cone(self):
        field = slicing.conical_field(30.0)
        paths = self.layer_paths()
        toolpath.apply_tool_axes(paths, "LayerNormal", field=field, max_tilt=90.0)
        for point in paths[0].points:
            tilt = math.degrees(point.axis.getAngle(Vector(0, 0, 1)))
            self.assertAlmostEqual(tilt, 30.0, delta=0.5)

    def test_tilt_is_clamped(self):
        field = slicing.conical_field(30.0)
        paths = self.layer_paths()
        toolpath.apply_tool_axes(paths, "LayerNormal", field=field, max_tilt=15.0)
        for point in paths[0].points:
            tilt = math.degrees(point.axis.getAngle(Vector(0, 0, 1)))
            self.assertLessEqual(tilt, 15.0 + 1e-3)

    def test_orientation_smoothing_limits_the_step(self):
        paths = self.layer_paths()
        axes = [Vector(0, 0, 1), Vector(1, 0, 1), Vector(0, 0, 1), Vector(-1, 0, 1)]
        for index, point in enumerate(paths[0].points):
            point.axis = axes[index % len(axes)]
        toolpath.smooth_orientations(paths, max_change=10.0, passes=6)
        points = paths[0].points
        for a, b in zip(points, points[1:]):
            self.assertLessEqual(math.degrees(a.axis.getAngle(b.axis)), 45.0)


class TestGenerateToolpath(unittest.TestCase):
    """The whole pipeline, from layers to a printable toolpath."""

    def layers(self, mode="Planar", **options):
        return slicing.slice_mesh(Part.makeBox(100, 80, 20), mode, 4.0, **options)

    def test_perimeters_shrink_inwards(self):
        layers, values = self.layers()
        paths = toolpath.generate_toolpath(
            layers, values, bead_width=12.0, layer_height=4.0, perimeters=2
        )
        roles = {p.role for p in paths}
        self.assertIn("perimeter", roles)
        self.assertIn("inset", roles)
        first = [p for p in paths if p.layer == 1]
        perimeter = [p for p in first if p.role == "perimeter"][0]
        inset = [p for p in first if p.role == "inset"][0]
        self.assertLess(self.perimeter_length(inset), self.perimeter_length(perimeter))

    def perimeter_length(self, path):
        contour = slicing.Contour([p.position for p in path.points], closed=path.closed)
        return contour.length()

    def test_first_perimeter_is_half_a_bead_in(self):
        # The bead centre runs half a bead inside the wall, so a 100 x 80 box
        # printed with a 12 mm bead has a 88 x 68 first perimeter.
        layers, values = self.layers()
        paths = toolpath.generate_toolpath(
            layers, values, bead_width=12.0, layer_height=4.0, perimeters=1
        )
        middle = [p for p in paths if p.layer == len(layers) // 2 and p.role == "perimeter"][0]
        self.assertAlmostEqual(
            self.perimeter_length(middle), 2 * (100.0 - 12.0) + 2 * (80.0 - 12.0), places=6
        )

    def test_the_second_perimeter_is_a_whole_bead_further_in(self):
        layers, values = self.layers()
        paths = toolpath.generate_toolpath(
            layers, values, bead_width=12.0, layer_height=4.0, perimeters=2
        )
        middle = [p for p in paths if p.layer == len(layers) // 2]
        perimeter = [p for p in middle if p.role == "perimeter"][0]
        inset = [p for p in middle if p.role == "inset"][0]
        self.assertAlmostEqual(
            self.perimeter_length(perimeter) - self.perimeter_length(inset), 8 * 12.0, places=6
        )

    def test_every_point_carries_the_process_values(self):
        layers, values = self.layers()
        paths = toolpath.generate_toolpath(layers, values, bead_width=8.0, layer_height=4.0)
        for path in paths:
            for point in path.points:
                self.assertAlmostEqual(point.width, 8.0)
                self.assertAlmostEqual(point.height, 4.0)
                self.assertGreater(point.speed, 0.0)

    def test_point_spacing_densifies(self):
        layers, values = self.layers()
        coarse = toolpath.generate_toolpath(layers, values, bead_width=8.0, layer_height=4.0)
        fine = toolpath.generate_toolpath(
            layers, values, bead_width=8.0, layer_height=4.0, point_spacing=5.0
        )
        self.assertGreater(sum(len(p.points) for p in fine), sum(len(p.points) for p in coarse))

    def test_conical_toolpath_tilts_the_nozzle(self):
        layers, values = self.layers("Conical", angle=25.0)
        paths = toolpath.generate_toolpath(
            layers,
            values,
            bead_width=8.0,
            layer_height=4.0,
            orientation="LayerNormal",
            field=slicing.conical_field(25.0),
            max_tilt=90.0,
        )
        tilts = [
            math.degrees(p.axis.getAngle(Vector(0, 0, 1))) for path in paths for p in path.points
        ]
        self.assertAlmostEqual(max(tilts), 25.0, delta=1.0)


class TestTravelMoves(unittest.TestCase):
    """Between paths the nozzle lifts, crosses and comes back down."""

    def printing_paths(self):
        layers, values = slicing.slice_mesh(Part.makeBox(100, 80, 20), "Planar", 4.0)
        return toolpath.generate_toolpath(
            layers, values, bead_width=8.0, layer_height=4.0, perimeters=1
        )

    def test_zero_clearance_leaves_the_direct_move(self):
        paths = self.printing_paths()
        self.assertEqual(len(toolpath.add_travel_moves(paths, 0.0)), len(paths))

    def test_one_travel_between_every_pair_of_paths(self):
        paths = self.printing_paths()
        with_travel = toolpath.add_travel_moves(paths, 10.0)
        travels = [p for p in with_travel if p.travel]
        self.assertEqual(len(travels), len(paths) - 1)
        self.assertEqual(len(with_travel), 2 * len(paths) - 1)

    def test_a_travel_lifts_by_the_clearance(self):
        paths = self.printing_paths()
        with_travel = toolpath.add_travel_moves(paths, 10.0)
        for index, path in enumerate(with_travel):
            if not path.travel:
                continue
            before = with_travel[index - 1]
            last = before.points[0] if before.closed else before.points[-1]
            self.assertAlmostEqual(path.points[0].position.z - last.position.z, 10.0, places=6)

    def test_a_travel_lifts_along_a_leaning_tool_axis(self):
        paths = self.printing_paths()
        for path in paths:
            for point in path.points:
                point.axis = Vector(1, 0, 0)
        with_travel = toolpath.add_travel_moves(paths, 10.0)
        travel = [p for p in with_travel if p.travel][0]
        index = with_travel.index(travel)
        before = with_travel[index - 1]
        last = before.points[0] if before.closed else before.points[-1]
        self.assertAlmostEqual(travel.points[0].position.x - last.position.x, 10.0, places=6)
        self.assertAlmostEqual(travel.points[0].position.z, last.position.z, places=6)

    def test_travel_points_are_marked_as_travel(self):
        with_travel = toolpath.add_travel_moves(self.printing_paths(), 10.0)
        for path in with_travel:
            for point in path.points:
                self.assertEqual(point.travel, path.travel)
        self.assertTrue(all(p.role == "travel" for p in with_travel if p.travel))

    def test_travel_does_not_count_towards_the_printed_length(self):
        paths = self.printing_paths()
        plain = toolpath.toolpath_length(paths)
        with_travel = toolpath.toolpath_length(toolpath.add_travel_moves(paths, 10.0))
        self.assertAlmostEqual(plain, with_travel, places=6)


class TestModifiers(unittest.TestCase):
    """Corner and overhang compensation change the process values, not the path."""

    def corner_path(self):
        points = [
            toolpath.PrintPoint(Vector(0, 0, 0), width=8.0, height=4.0, speed=3000.0),
            toolpath.PrintPoint(Vector(50, 0, 0), width=8.0, height=4.0, speed=3000.0),
            toolpath.PrintPoint(Vector(50, 50, 0), width=8.0, height=4.0, speed=3000.0),
        ]
        return [toolpath.Path(points)]

    def test_corner_detection(self):
        corners = toolpath.path_corners(self.corner_path()[0], threshold=60.0)
        self.assertEqual(corners, [1])

    def test_corner_compensation_slows_down_and_thickens(self):
        paths = self.corner_path()
        toolpath.corner_compensation(paths, threshold=60.0)
        corner = paths[0].points[1]
        self.assertLess(corner.speed, 3000.0)
        self.assertGreater(corner.extrusion, 1.0)

    def test_corner_compensation_keeps_the_positions(self):
        paths = self.corner_path()
        before = [p.position for p in paths[0].points]
        toolpath.corner_compensation(paths, threshold=60.0)
        for a, b in zip(before, (p.position for p in paths[0].points)):
            self.assertAlmostEqual((a - b).Length, 0.0)

    def test_overhang_reinforcement_only_touches_hanging_points(self):
        paths = self.corner_path()
        paths[0].points[1].overhang = 70.0
        toolpath.reinforce_overhangs(paths, limit=45.0)
        self.assertGreater(paths[0].points[1].extrusion, 1.0)
        self.assertAlmostEqual(paths[0].points[0].extrusion, 1.0)


if __name__ == "__main__":
    unittest.main()
