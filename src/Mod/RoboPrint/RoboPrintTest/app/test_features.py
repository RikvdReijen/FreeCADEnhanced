# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the document objects: recompute, storage and round-trip."""

import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector

from roboprint import features, slicing, toolpath


class _DocumentTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("RoboPrintTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def box(self, length=100.0, width=80.0, height=20.0):
        obj = self.doc.addObject("Part::Box", "Box")
        obj.Length = length
        obj.Width = width
        obj.Height = height
        self.doc.recompute()
        return obj


class TestSlices(_DocumentTest):
    """The Slices object slices its base and stores the contours."""

    def test_planar_slices_recompute(self):
        obj = features.make_slices(self.box(), "Planar", 5.0, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(obj.isValid())
        self.assertGreater(obj.LayerCount, 1)
        self.assertGreaterEqual(obj.ContourCount, obj.LayerCount)

    def test_contours_come_back_out(self):
        obj = features.make_slices(self.box(), "Planar", 5.0, doc=self.doc)
        self.doc.recompute()
        layers, values = features.contours_of(obj)
        self.assertEqual(len(layers), obj.LayerCount)
        self.assertEqual(len(values), obj.LayerCount)
        middle = layers[len(layers) // 2]
        self.assertAlmostEqual(middle[0].length(), 2 * (100.0 + 80.0), places=2)

    def test_layer_height_drives_the_layer_count(self):
        coarse = features.make_slices(self.box(), "Planar", 10.0, doc=self.doc)
        fine = features.make_slices(self.box(), "Planar", 2.0, doc=self.doc)
        self.doc.recompute()
        self.assertGreater(fine.LayerCount, coarse.LayerCount)

    def test_every_mode_recomputes(self):
        base = self.box(40, 40, 20)
        for mode in slicing.SLICING_MODES:
            obj = features.make_slices(base, mode, 5.0, doc=self.doc)
            if mode == "Conformal":
                obj.Surface = base
            self.doc.recompute()
            self.assertTrue(obj.isValid(), mode)
            self.assertGreater(obj.LayerCount, 0, mode)

    def test_the_shape_holds_a_wire_per_contour(self):
        obj = features.make_slices(self.box(), "Planar", 5.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(len(obj.Shape.Wires), obj.ContourCount)

    def test_a_missing_base_is_reported(self):
        obj = features.make_slices(None, "Planar", 5.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(obj.LayerCount, 0)


class TestToolpathObject(_DocumentTest):
    """The Toolpath object builds on a Slices object and reports the numbers."""

    def slices(self, mode="Planar", **options):
        obj = features.make_slices(self.box(), mode, 4.0, doc=self.doc, **options)
        self.doc.recompute()
        return obj

    def test_toolpath_recomputes(self):
        obj = features.make_toolpath(self.slices(), bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(obj.isValid())
        self.assertGreater(obj.PathCount, 0)
        self.assertGreater(obj.PointCount, obj.PathCount)
        self.assertGreater(obj.PathLength, 0.0)

    def test_bead_height_defaults_to_the_layer_height(self):
        source = self.slices()
        obj = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        self.assertAlmostEqual(float(obj.BeadHeight), float(source.LayerHeight))

    def test_paths_come_back_out_with_their_process_values(self):
        obj = features.make_toolpath(self.slices(), bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        paths = features.paths_of(obj)
        self.assertEqual(len(paths), obj.PathCount)
        self.assertEqual(sum(len(p.points) for p in paths), obj.PointCount)
        for path in paths:
            for point in path.points:
                self.assertAlmostEqual(point.width, 8.0)
                self.assertGreater(point.speed, 0.0)

    def test_mass_follows_the_density(self):
        obj = features.make_toolpath(self.slices(), bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        light = obj.Mass
        obj.Density = 2.0 * obj.Density
        self.doc.recompute()
        self.assertAlmostEqual(obj.Mass, 2.0 * light, delta=light * 0.01)

    def test_infill_adds_paths(self):
        source = self.slices()
        plain = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        filled = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        filled.InfillPattern = "Lines"
        filled.InfillSpacing = 20.0
        self.doc.recompute()
        self.assertGreater(filled.PathCount, plain.PathCount)

    def test_spiral_gives_one_continuous_path(self):
        obj = features.make_toolpath(self.slices(), bead_width=8.0, doc=self.doc)
        obj.Spiral = True
        obj.Perimeters = 1
        self.doc.recompute()
        self.assertEqual(obj.PathCount, 1)

    def test_max_tilt_clamps_a_conical_toolpath(self):
        source = self.slices("Conical", ConeAngle=30.0)
        obj = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        obj.Orientation = "LayerNormal"
        obj.MaxTilt = 10.0
        self.doc.recompute()
        self.assertLessEqual(obj.MaxTiltUsed, 10.0 + 1e-3)

    def test_travel_clearance_adds_travel_paths(self):
        source = self.slices()
        plain = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        lifted = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        lifted.TravelClearance = 15.0
        self.doc.recompute()
        self.assertEqual(lifted.PathCount, 2 * plain.PathCount - 1)
        # Lifting the nozzle must not change how much material comes out.
        self.assertAlmostEqual(lifted.PathLength, plain.PathLength, places=3)
        self.assertAlmostEqual(lifted.Volume, plain.Volume, places=3)

    def test_a_rebuilt_travel_is_still_a_travel(self):
        source = self.slices()
        obj = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        obj.TravelClearance = 15.0
        self.doc.recompute()
        paths = features.paths_of(obj)
        travels = [p for p in paths if p.travel]
        self.assertTrue(travels)
        for path in travels:
            self.assertEqual(path.role, "travel")
            self.assertTrue(all(p.travel for p in path.points))

    def test_a_base_that_is_not_slices_is_reported(self):
        obj = features.make_toolpath(self.box(), bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(obj.PathCount, 0)


class TestExportAndRoundTrip(_DocumentTest):
    """A saved document reloads and still exports."""

    def build(self):
        source = features.make_slices(self.box(), "Planar", 4.0, doc=self.doc)
        obj = features.make_toolpath(source, bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        return obj

    def test_export_writes_a_program(self):
        obj = self.build()
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "program.gcode")
            text = features.export_toolpath(obj, "G-code (3 axis)", target)
            self.assertIn("G1", text)
            self.assertTrue(os.path.exists(target))

    def test_export_rejects_the_wrong_object(self):
        with self.assertRaises(ValueError):
            features.export_toolpath(self.box(), "CSV")

    def test_save_and_reload_keeps_the_toolpath(self):
        obj = self.build()
        name, paths, points = obj.Name, obj.PathCount, obj.PointCount
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "roboprint.FCStd")
            self.doc.saveAs(target)
            FreeCAD.closeDocument(self.doc.Name)
            self.doc = FreeCAD.openDocument(target)
            reloaded = self.doc.getObject(name)
            self.assertEqual(reloaded.PathCount, paths)
            self.assertEqual(reloaded.PointCount, points)
            self.assertEqual(len(features.paths_of(reloaded)), paths)
            self.assertTrue(features.export_toolpath(reloaded, "CSV").strip())

    def test_is_roboprint_object_tells_the_two_apart(self):
        obj = self.build()
        self.assertTrue(features.is_roboprint_object(obj))
        self.assertTrue(features.is_roboprint_object(obj, "Toolpath"))
        self.assertFalse(features.is_roboprint_object(obj, "Slices"))
        self.assertTrue(features.is_roboprint_object(obj.Base, "Slices"))
        self.assertFalse(features.is_roboprint_object(self.box()))


class TestPreferences(_DocumentTest):
    """The property defaults come from the preferences page."""

    def test_layer_height_default_follows_the_preference(self):
        params = FreeCAD.ParamGet(features.PARAM_PATH)
        try:
            params.SetFloat("LayerHeight", 7.5)
            obj = features.make_slices(self.box(), doc=self.doc)
            self.assertAlmostEqual(float(obj.LayerHeight), 7.5)
        finally:
            params.RemFloat("LayerHeight")

    def test_preference_falls_back_to_the_given_default(self):
        self.assertAlmostEqual(features.preference("NoSuchSetting", 3.25), 3.25)
        self.assertEqual(features.preference("NoSuchSetting", 7), 7)
        self.assertEqual(features.preference("NoSuchSetting", "x"), "x")
        self.assertIs(features.preference("NoSuchSetting", True), True)


if __name__ == "__main__":
    unittest.main()
