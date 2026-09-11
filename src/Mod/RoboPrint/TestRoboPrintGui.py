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

"""GUI tests of the RoboPrint workbench (require a running FreeCAD GUI)."""

import os
import tempfile
import unittest

import FreeCAD
import FreeCADGui


class TestRoboPrintCommands(unittest.TestCase):
    """The workbench registers its commands and they report resources."""

    def setUp(self):
        FreeCADGui.activateWorkbench("RoboPrintWorkbench")
        self.doc = FreeCAD.newDocument("RoboPrintGuiTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def box(self, length=100.0, width=80.0, height=20.0):
        obj = self.doc.addObject("Part::Box", "Box")
        obj.Length = length
        obj.Width = width
        obj.Height = height
        self.doc.recompute()
        return obj

    def test_commands_registered(self):
        from roboprint import commands

        registered = set(FreeCADGui.listCommands())
        for name in commands.ALL_COMMANDS:
            self.assertIn(name, registered, name)

    def test_toolbar_and_menu_only_name_real_commands(self):
        from roboprint import commands

        for name in commands.TOOLBAR_COMMANDS + commands.MENU_COMMANDS:
            if name != "Separator":
                self.assertIn(name, FreeCADGui.listCommands(), name)

    def test_every_command_reports_its_resources(self):
        from roboprint import commands

        for name in commands.ALL_COMMANDS:
            resources = getattr(commands, name)().GetResources()
            self.assertTrue(resources["MenuText"], name)
            self.assertTrue(resources["ToolTip"], name)
            self.assertTrue(resources["Pixmap"], name)

    def test_commands_are_inactive_without_a_selection(self):
        from roboprint import commands

        FreeCADGui.Selection.clearSelection()
        for name in ("RoboPrint_Slice", "RoboPrint_Toolpath", "RoboPrint_Analyze"):
            self.assertFalse(getattr(commands, name)().IsActive(), name)

    def test_slice_is_active_with_a_solid_selected(self):
        from roboprint import commands

        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.box())
        self.assertTrue(commands.RoboPrint_Slice().IsActive())

    def test_view_providers_attach_and_give_an_icon(self):
        from roboprint import features

        slices = features.make_slices(self.box(), "Planar", 5.0, doc=self.doc)
        toolpath = features.make_toolpath(slices, bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        for obj in (slices, toolpath):
            self.assertIsNotNone(obj.ViewObject)
            self.assertTrue(obj.ViewObject.Proxy.getIcon().endswith(".svg"))
            self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [obj.Base])

    def test_the_base_is_hidden_once_it_is_sliced(self):
        from roboprint import features

        base = self.box()
        base.ViewObject.Visibility = True
        features.make_slices(base, "Planar", 5.0, doc=self.doc)
        self.doc.recompute()
        self.assertFalse(base.ViewObject.Visibility)


class TestRoboPrintDialogs(unittest.TestCase):
    """The dialogs build, remember their settings and hand them back."""

    def setUp(self):
        FreeCADGui.activateWorkbench("RoboPrintWorkbench")
        self.doc = FreeCAD.newDocument("RoboPrintDialogTest")
        self.params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/RoboPrint")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_slice_dialog_has_a_hint_for_every_mode(self):
        from roboprint import commands, slicing

        dialog = commands.SliceDialog()
        for mode in slicing.SLICING_MODES:
            self.assertIn(mode, commands.SliceDialog.HINTS, mode)
            dialog.mode.setCurrentText(mode)
            self.assertTrue(dialog.hint.text(), mode)
        dialog.deleteLater()

    def test_slice_dialog_only_offers_the_cone_angle_in_conical_mode(self):
        from roboprint import commands

        dialog = commands.SliceDialog()
        dialog.mode.setCurrentText("Conical")
        self.assertTrue(dialog.cone_angle.isEnabled())
        dialog.mode.setCurrentText("Planar")
        self.assertFalse(dialog.cone_angle.isEnabled())
        dialog.deleteLater()

    def test_slice_dialog_remembers_its_values(self):
        from roboprint import commands

        dialog = commands.SliceDialog()
        dialog.mode.setCurrentText("Spherical")
        dialog.layer_height.setValue(6.5)
        mode, layer_height, _, _ = dialog.values()
        self.assertEqual(mode, "Spherical")
        self.assertAlmostEqual(layer_height, 6.5)
        dialog.deleteLater()
        self.assertEqual(self.params.GetString("Mode"), "Spherical")
        self.assertAlmostEqual(self.params.GetFloat("LayerHeight"), 6.5)
        self.params.RemString("Mode")
        self.params.RemFloat("LayerHeight")

    def test_toolpath_dialog_takes_the_bead_height_from_the_slices(self):
        from roboprint import commands

        dialog = commands.ToolpathDialog(layer_height=3.5)
        self.assertAlmostEqual(dialog.bead_height.value(), 3.5)
        dialog.deleteLater()

    def test_toolpath_dialog_hands_back_the_property_names(self):
        from roboprint import commands, features

        dialog = commands.ToolpathDialog(layer_height=4.0)
        dialog.infill.setCurrentText("Grid")
        dialog.perimeters.setValue(3)
        values = dialog.values()
        dialog.deleteLater()
        self.assertEqual(values["InfillPattern"], "Grid")
        self.assertEqual(values["Perimeters"], 3)
        obj = features.make_toolpath(None, doc=self.doc)
        for key in values:
            if key not in ("bead_width", "bead_height"):
                self.assertTrue(hasattr(obj, key), key)
        for key in ("Perimeters", "InfillPattern"):
            self.params.RemInt(key) if key == "Perimeters" else self.params.RemString(key)

    def test_toolpath_dialog_only_offers_the_spacing_with_a_pattern(self):
        from roboprint import commands

        dialog = commands.ToolpathDialog()
        dialog.infill.setCurrentText("None")
        self.assertFalse(dialog.spacing.isEnabled())
        dialog.infill.setCurrentText("Lines")
        self.assertTrue(dialog.spacing.isEnabled())
        dialog.deleteLater()

    def test_export_dialog_lists_every_post_processor(self):
        from roboprint import commands, postprocessors

        dialog = commands.ExportDialog()
        listed = {dialog.flavour.itemText(i) for i in range(dialog.flavour.count())}
        self.assertEqual(listed, set(postprocessors.POST_PROCESSORS))
        dialog.deleteLater()


class TestRoboPrintAnalysisPanel(unittest.TestCase):
    """The report panel fills a row per check without touching the document."""

    def setUp(self):
        FreeCADGui.activateWorkbench("RoboPrintWorkbench")
        self.doc = FreeCAD.newDocument("RoboPrintPanelTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def toolpath(self):
        from roboprint import features

        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 100.0, 80.0, 20.0
        self.doc.recompute()
        slices = features.make_slices(box, "Planar", 4.0, doc=self.doc)
        obj = features.make_toolpath(slices, bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        return obj

    def test_panel_reports_every_check(self):
        from roboprint import commands

        panel = commands.AnalysisPanel(self.toolpath())
        self.assertGreaterEqual(panel.table.rowCount(), 8)
        for row in range(panel.table.rowCount()):
            self.assertTrue(panel.table.item(row, 0).text(), row)
            self.assertTrue(panel.table.item(row, 1).text(), row)
        self.assertFalse(panel.isAllowedAlterDocument())
        panel.form.deleteLater()


class TestRoboPrintExportCommand(unittest.TestCase):
    """Exporting through the feature layer writes a program file."""

    def setUp(self):
        FreeCADGui.activateWorkbench("RoboPrintWorkbench")
        self.doc = FreeCAD.newDocument("RoboPrintExportTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_every_flavour_writes_from_a_document_object(self):
        from roboprint import features, postprocessors

        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 60.0, 40.0, 12.0
        self.doc.recompute()
        slices = features.make_slices(box, "Planar", 4.0, doc=self.doc)
        obj = features.make_toolpath(slices, bead_width=8.0, doc=self.doc)
        self.doc.recompute()
        with tempfile.TemporaryDirectory() as folder:
            for flavour, (_, suffix) in postprocessors.POST_PROCESSORS.items():
                target = os.path.join(folder, "program")
                features.export_toolpath(obj, flavour, target, volumetric=True)
                self.assertTrue(os.path.exists(target + suffix), flavour)


if __name__ == "__main__":
    unittest.main()
