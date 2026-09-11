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

"""GUI tests of the Freeform workbench (require a running FreeCAD GUI)."""

import unittest

import FreeCAD
import FreeCADGui


class TestFreeformCommands(unittest.TestCase):
    """The workbench registers its commands and they report resources."""

    def setUp(self):
        FreeCADGui.activateWorkbench("FreeformWorkbench")
        self.doc = FreeCAD.newDocument("FreeformGuiTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_commands_registered(self):
        from freeform import commands

        registered = set(FreeCADGui.listCommands())
        for name in commands.ALL_COMMANDS:
            self.assertIn(name, registered, name)

    def test_toolbar_commands_exist(self):
        from freeform import commands

        for name in commands.TOOLBAR_COMMANDS:
            if name.startswith("Freeform_"):
                self.assertIn(name, commands.ALL_COMMANDS, name)
            elif name != "Separator":
                self.assertIn(name, FreeCADGui.listCommands(), name)

    def test_stroke_creation_with_view_providers(self):
        from freeform import features, palette

        palette.set_current_color((0.2, 0.4, 0.8))
        stroke = features.make_stroke(
            [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(5, 3, 0), FreeCAD.Vector(10, 0, 0)],
            doc=self.doc,
        )
        ribbon = features.make_ribbon(stroke, width=2.0, doc=self.doc)
        self.doc.recompute()
        self.assertEqual(stroke.ViewObject.Proxy.getIcon(), ":/icons/Freeform_Stroke.svg")
        self.assertFalse(stroke.ViewObject.Visibility)
        self.assertIn(stroke, ribbon.ViewObject.Proxy.claimChildren())
        self.assertAlmostEqual(stroke.ViewObject.LineColor[2], 0.8, places=2)
        palette.set_current_color(None)

    def _drag(self, capture, pixels):
        """Feed a synthetic press-move-release gesture to a capture object."""
        capture._event(
            {
                "Type": "SoMouseButtonEvent",
                "Button": "BUTTON1",
                "State": "DOWN",
                "Position": pixels[0],
            }
        )
        for pos in pixels[1:]:
            capture._event({"Type": "SoLocation2Event", "Position": pos})
        capture._event(
            {
                "Type": "SoMouseButtonEvent",
                "Button": "BUTTON1",
                "State": "UP",
                "Position": pixels[-1],
            }
        )

    def test_stroke_tool_with_synthetic_events(self):
        import math

        from freeform import commands, features, workplane

        view = FreeCADGui.ActiveDocument.ActiveView
        view.viewTop()
        workplane.get_work_plane().set_mode("Top")
        symmetry = workplane.get_symmetry_plane()
        symmetry.set(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), enabled=True)
        command = commands.Freeform_Stroke()
        command.Activated()
        try:
            self.assertTrue(command.capture.active)
            command.panel.recognize_check.setChecked(True)
            command.panel.thickness_spin.setValue(0.0)
            # a wavy stroke: creates a Stroke and its mirror twin
            wave = [(200 + i * 12, int(300 + 40 * math.sin(i * 0.5))) for i in range(30)]
            self._drag(command.capture, wave)
            strokes = [o for o in self.doc.Objects if features.is_freeform_object(o, "Stroke")]
            mirrors = [o for o in self.doc.Objects if o.TypeId == "Part::Mirroring"]
            self.assertEqual(len(strokes), 1)
            self.assertEqual(len(mirrors), 1)
            self.assertEqual(mirrors[0].Source, strokes[0])
            self.assertGreater(len(strokes[0].Points), 10)
            # a straight drag is recognised as a line (two points, degree 1)
            self._drag(command.capture, [(100 + i * 20, 500 + (i % 2)) for i in range(15)])
            strokes = [o for o in self.doc.Objects if features.is_freeform_object(o, "Stroke")]
            self.assertEqual(len(strokes), 2)
            line = [s for s in strokes if int(s.Degree) == 1][0]
            self.assertEqual(len(line.Points), 2)
            # a round drag is recognised as a circle
            circle_pixels = [
                (int(400 + 80 * math.cos(a)), int(400 + 80 * math.sin(a)))
                for a in [i * 2 * math.pi / 40 for i in range(41)]
            ]
            self._drag(command.capture, circle_pixels)
            circles = [o for o in self.doc.Objects if o.TypeId == "Part::Circle"]
            self.assertEqual(len(circles), 1)
            self.assertAlmostEqual(float(circles[0].Angle2), 360.0, places=3)
            # escape ends the tool
            command.capture._event({"Type": "SoKeyboardEvent", "Key": "ESCAPE", "State": "DOWN"})
            self.assertIsNone(command.capture)
        finally:
            command.finish()
            symmetry.set(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), enabled=False)
        self.doc.recompute()
        self.assertTrue(all(o.Shape.isValid() for o in self.doc.Objects if hasattr(o, "Shape")))

    def test_stroke_end_snapping_and_auto_close(self):
        from freeform import commands, features, workplane

        view = FreeCADGui.ActiveDocument.ActiveView
        view.viewTop()
        workplane.get_work_plane().set_mode("Top")
        workplane.get_symmetry_plane().set_enabled(False)
        command = commands.Freeform_Stroke()
        command.Activated()
        try:
            command.panel.recognize_check.setChecked(False)
            command.panel.snap_ends_check.setChecked(True)
            command.panel.thickness_spin.setValue(0.0)
            self._drag(command.capture, [(100 + i * 10, 300 + (i * 7) % 23) for i in range(20)])
            first = [o for o in self.doc.Objects if features.is_freeform_object(o, "Stroke")][0]
            self.doc.recompute()
            end = first.Shape.Vertexes[-1].Point
            # the first stroke ends at pixel (290, 318); a second stroke starting a few
            # pixels away snaps onto that end point
            self._drag(command.capture, [(293 + i * 3, 316 + (i * 9) % 17) for i in range(20)])
            strokes = [o for o in self.doc.Objects if features.is_freeform_object(o, "Stroke")]
            self.assertEqual(len(strokes), 2)
            self.assertAlmostEqual((strokes[1].Points[0] - end).Length, 0.0, places=6)
            # a loop that returns to its start is closed automatically
            import math

            loop = [
                (int(500 + 60 * math.cos(a)), int(500 + 40 * math.sin(a) + 10 * math.sin(3 * a)))
                for a in [i * 2 * math.pi / 30 for i in range(31)]
            ]
            self._drag(command.capture, loop)
            strokes = [o for o in self.doc.Objects if features.is_freeform_object(o, "Stroke")]
            self.assertEqual(len(strokes), 3)
            self.assertTrue(strokes[2].Closed)
            self.assertTrue(strokes[2].Shape.isClosed())
        finally:
            command.finish()

    def test_solidify_sweep_extrude_commands_registered(self):
        registered = set(FreeCADGui.listCommands())
        for name in (
            "Freeform_Solidify",
            "Freeform_Sweep",
            "Freeform_Extrude",
            "Freeform_Shell",
            "Freeform_ToSketch",
            "Std_TransformManip",
        ):
            self.assertIn(name, registered)

    def test_primitive_tool_drag_sizes_the_solid(self):
        from freeform import commands, workplane

        view = FreeCADGui.ActiveDocument.ActiveView
        view.viewTop()
        workplane.get_work_plane().set_mode("Top")
        workplane.get_symmetry_plane().set_enabled(False)
        command = commands.Freeform_Sphere()
        command.Activated()
        try:
            self._drag(command.capture, [(300, 300), (340, 300), (380, 300)])
            spheres = [o for o in self.doc.Objects if o.TypeId == "Part::Sphere"]
            self.assertEqual(len(spheres), 1)
            expected = (view.getPoint(380, 300) - view.getPoint(300, 300)).Length
            self.assertAlmostEqual(float(spheres[0].Radius), expected, delta=expected * 0.05)
            # a plain click uses the default size
            self._drag(command.capture, [(500, 500)])
            spheres = [o for o in self.doc.Objects if o.TypeId == "Part::Sphere"]
            self.assertEqual(len(spheres), 2)
            self.assertAlmostEqual(float(spheres[1].Radius), 5.0, places=6)
        finally:
            command.finish()

    def test_symmetry_toggle_syncs_panel_and_action(self):
        from freeform import commands, workplane

        command = commands.Freeform_Stroke()
        command.Activated()
        try:
            toggle = commands.Freeform_Symmetry()
            toggle.Activated(1)
            self.assertTrue(workplane.get_symmetry_plane().enabled)
            self.assertTrue(command.panel.symmetry_check.isChecked())
            command.panel.symmetry_check.setChecked(False)
            self.assertFalse(workplane.get_symmetry_plane().enabled)
            toggle.Activated(0)
            self.assertFalse(command.panel.symmetry_check.isChecked())
            commands._sync_checkable("Freeform_Symmetry", False)  # must not raise
        finally:
            command.finish()
            workplane.get_symmetry_plane().set_enabled(False)
        self.assertIsNone(commands.ACTIVE_STROKE_COMMAND)

    def test_parametric_commands_registered(self):
        from freeform import commands

        registered = set(FreeCADGui.listCommands())
        for name in commands.PARAMETRIC_MENU_COMMANDS:
            self.assertIn(name, registered, name)
            self.assertIn(name, commands.ALL_COMMANDS, name)

    def test_generator_view_providers(self):
        from freeform import features, generators

        curve = features.make_stroke(
            [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(20, 10, 0), FreeCAD.Vector(40, 0, 0)],
            doc=self.doc,
        )
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        array = generators.make_curve_array(box, curve, count=4, doc=self.doc)
        divide = generators.make_divide(curve, count=5, doc=self.doc)
        self.doc.recompute()
        self.assertTrue(array.Shape.Solids)
        self.assertFalse(box.ViewObject.Visibility)
        self.assertIn(box, array.ViewObject.Proxy.claimChildren())
        self.assertIn(curve, array.ViewObject.Proxy.claimChildren())
        self.assertEqual(divide.ViewObject.Proxy.getIcon(), ":/icons/Freeform_Divide.svg")

    def test_tracker_lifecycle(self):
        from freeform import tracker

        view = FreeCADGui.ActiveDocument.ActiveView
        line = tracker.LineTracker(view=view)
        line.set_points([FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 1, 1)])
        self.assertEqual(line.count, 2)
        line.finalize()
        capture = tracker.StrokeCapture(lambda pts: None, view=view)
        capture.start()
        capture.finalize()
        self.assertFalse(capture.active)
