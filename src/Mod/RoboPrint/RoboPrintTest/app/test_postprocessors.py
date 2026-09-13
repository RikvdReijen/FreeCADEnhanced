# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the machine program writers."""

import os
import tempfile
import unittest

import Part
from FreeCAD import Vector

from roboprint import postprocessors, slicing, toolpath


def sample_paths(bead_width=8.0, layer_height=4.0):
    layers, values = slicing.slice_mesh(Part.makeBox(60, 40, 12), "Planar", layer_height)
    return toolpath.generate_toolpath(
        layers, values, bead_width=bead_width, layer_height=layer_height, perimeters=1
    )


def frame_count(paths):
    """Points written out: a closed path repeats its first point to close."""
    return sum(len(p.points) + (1 if p.closed and len(p.points) > 2 else 0) for p in paths)


class TestToolFrames(unittest.TestCase):
    """Every print point becomes a placement the robot can be told to reach."""

    def setUp(self):
        self.paths = sample_paths()

    def test_a_frame_per_point(self):
        frames = postprocessors.tool_frames(self.paths)
        self.assertEqual(len(frames), frame_count(self.paths))

    def test_a_closed_path_returns_to_its_start(self):
        closed = [p for p in self.paths if p.closed][0]
        frames = postprocessors.tool_frames([closed])
        self.assertEqual(len(frames), len(closed.points) + 1)
        self.assertAlmostEqual((frames[0][1].Base - frames[-1][1].Base).Length, 0.0, places=6)

    def test_tool_z_points_against_the_print_axis(self):
        for point, placement in postprocessors.tool_frames(self.paths):
            tool_z = placement.Rotation.multVec(Vector(0, 0, 1))
            self.assertAlmostEqual(tool_z.dot(point.axis), -1.0, places=5)

    def test_the_frame_sits_on_the_point(self):
        for point, placement in postprocessors.tool_frames(self.paths):
            self.assertAlmostEqual((placement.Base - point.position).Length, 0.0, places=6)

    def test_tool_x_follows_the_travel_direction(self):
        path = self.paths[0]
        frames = postprocessors.tool_frames([path])
        direction = path.points[1].position - path.points[0].position
        tool_x = frames[0][1].Rotation.multVec(Vector(1, 0, 0))
        self.assertGreater(tool_x.dot(direction.normalize()), 0.9)


class TestWriters(unittest.TestCase):
    """Each flavour writes its own language, with a row per point."""

    def setUp(self):
        self.paths = sample_paths()
        self.points = frame_count(self.paths)

    def test_csv_has_a_header_and_a_row_per_point(self):
        text = postprocessors.write_csv(self.paths)
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), self.points + 1)
        self.assertIn("x", lines[0].split(","))
        self.assertEqual(len(lines[0].split(",")), len(lines[1].split(",")))

    def test_gcode_moves_and_extrudes(self):
        text = postprocessors.write_gcode(self.paths, axes=3)
        self.assertIn("G1", text)
        self.assertIn("E", text)
        self.assertNotIn("A", text.split("\n")[-3])

    def test_five_axis_gcode_carries_the_rotary_words(self):
        for path in self.paths:
            for point in path.points:
                point.axis = Vector(1, 0, 1)
        text = postprocessors.write_gcode(self.paths, axes=5)
        move = [line for line in text.splitlines() if line.startswith("G1")][0]
        self.assertIn(" A", move)
        self.assertIn(" B", move)

    def test_volumetric_extrusion_differs_from_filament(self):
        volumetric = postprocessors.write_gcode(self.paths, volumetric=True)
        filament = postprocessors.write_gcode(self.paths, volumetric=False)
        self.assertNotEqual(volumetric, filament)

    def test_krl_is_a_kuka_module(self):
        text = postprocessors.write_krl(self.paths, name="Demo")
        self.assertIn("DEF Demo", text)
        self.assertIn("END", text)
        self.assertEqual(
            len([l for l in text.splitlines() if l.strip().startswith("LIN")]), self.points
        )

    def test_rapid_is_an_abb_module(self):
        text = postprocessors.write_rapid(self.paths, name="Demo", module="DemoModule")
        self.assertIn("MODULE DemoModule", text)
        self.assertIn("PROC Demo()", text)
        self.assertIn("MoveL", text)

    def test_urscript_is_in_metres(self):
        text = postprocessors.write_urscript(self.paths)
        self.assertIn("movel(p[", text)
        move = [line for line in text.splitlines() if "movel(p[" in line][0]
        first = float(move.split("p[")[1].split(",")[0])
        self.assertLess(abs(first), 10.0)

    def test_every_flavour_writes_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            for flavour, (_, suffix) in postprocessors.POST_PROCESSORS.items():
                target = os.path.join(folder, "program")
                text = postprocessors.post_process(self.paths, flavour, target)
                self.assertTrue(text.strip(), flavour)
                self.assertTrue(os.path.exists(target + suffix), flavour)

    def test_options_are_filtered_per_writer(self):
        # The GUI passes everything it knows; a writer that has no use for an
        # option must not choke on it.
        text = postprocessors.post_process(
            self.paths, "CSV", None, volumetric=True, filament_diameter=2.85
        )
        self.assertTrue(text.strip())

    def test_travel_moves_are_written_as_rapids(self):
        paths = toolpath.add_travel_moves(self.paths, 15.0)
        text = postprocessors.write_gcode(paths, axes=3)
        rapids = [line for line in text.splitlines() if line.startswith("G0")]
        self.assertGreaterEqual(len(rapids), 2 * (len(self.paths) - 1))
        for line in rapids:
            self.assertNotIn(" E", line)

    def test_an_unknown_flavour_is_rejected(self):
        with self.assertRaises(ValueError):
            postprocessors.post_process(self.paths, "Klingon")


if __name__ == "__main__":
    unittest.main()
