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

"""The GUI commands of the RoboPrint workbench."""

import os

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from . import analysis, features, postprocessors, slicing, toolpath

translate = FreeCAD.Qt.translate
QT_TRANSLATE_NOOP = FreeCAD.Qt.QT_TRANSLATE_NOOP

PARAM_PATH = features.PARAM_PATH

TOOLBAR_COMMANDS = [
    "RoboPrint_Slice",
    "RoboPrint_Toolpath",
    "Separator",
    "RoboPrint_Analyze",
    "RoboPrint_Export",
]

ALL_COMMANDS = ["RoboPrint_Slice", "RoboPrint_Toolpath", "RoboPrint_Analyze", "RoboPrint_Export"]

MENU_COMMANDS = list(ALL_COMMANDS)


def _params():
    return FreeCAD.ParamGet(PARAM_PATH)


def _doc():
    return FreeCAD.ActiveDocument


def _selection():
    return FreeCADGui.Selection.getSelection()


def _msg(text):
    FreeCAD.Console.PrintMessage("RoboPrint: " + text + "\n")


def _err(text):
    FreeCAD.Console.PrintError("RoboPrint: " + text + "\n")


def _transaction(name):
    class _Ctx:
        def __enter__(self):
            doc = _doc()
            if doc is not None:
                doc.openTransaction(name)
            return doc

        def __exit__(self, exc_type, exc, tb):
            doc = _doc()
            if doc is None:
                return False
            if exc_type is None:
                doc.commitTransaction()
                doc.recompute()
            else:
                doc.abortTransaction()
                _err(str(exc))
            return exc_type is not None and issubclass(exc_type, ValueError)

    return _Ctx()


def _resources(pixmap, menu, tooltip, accel=None):
    resources = {"Pixmap": pixmap, "MenuText": menu, "ToolTip": tooltip}
    if accel:
        resources["Accel"] = accel
    return resources


class _Command:
    def IsActive(self):
        return _doc() is not None


def _printable(obj):
    return hasattr(obj, "Mesh") or (
        getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()
    )


# ---------------------------------------------------------------------------
# Slice
# ---------------------------------------------------------------------------


class SliceDialog(QtWidgets.QDialog):
    """Mode and layer height for a new set of slices."""

    HINTS = {
        "Planar": "Flat layers. Tilt the build axis afterwards to print the part at an angle.",
        "Cylindrical": "Layers wrapped around an axis, for printing onto a mandrel or a pipe.",
        "Conical": "Cone shaped layers. The nozzle leans by the cone angle, which is how an "
        "overhang is printed with nothing underneath it.",
        "Spherical": "Layers as nested shells around a point.",
        "Conformal": "Layers follow a substrate you select: an existing face or a scan mesh.",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("RoboPrint", "Slice for robotic printing"))
        layout = QtWidgets.QFormLayout(self)
        params = _params()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(list(slicing.SLICING_MODES))
        stored = params.GetString("Mode", "Planar")
        if stored in slicing.SLICING_MODES:
            self.mode.setCurrentIndex(list(slicing.SLICING_MODES).index(stored))
        self.mode.currentTextChanged.connect(self.mode_changed)
        layout.addRow(translate("RoboPrint", "Mode"), self.mode)

        self.layer_height = QtWidgets.QDoubleSpinBox()
        self.layer_height.setRange(0.01, 1000.0)
        self.layer_height.setDecimals(2)
        self.layer_height.setSuffix(" mm")
        self.layer_height.setValue(params.GetFloat("LayerHeight", 4.0))
        layout.addRow(translate("RoboPrint", "Layer height"), self.layer_height)

        self.cone_angle = QtWidgets.QDoubleSpinBox()
        self.cone_angle.setRange(-80.0, 80.0)
        self.cone_angle.setSuffix(" °")
        self.cone_angle.setValue(params.GetFloat("ConeAngle", 30.0))
        layout.addRow(translate("RoboPrint", "Cone angle"), self.cone_angle)

        self.seam = QtWidgets.QComboBox()
        self.seam.addItems(["Aligned", "Nearest", "Scattered"])
        layout.addRow(translate("RoboPrint", "Seam"), self.seam)

        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        layout.addRow(self.hint)
        self.mode_changed(self.mode.currentText())

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def mode_changed(self, mode):
        self.hint.setText(translate("RoboPrint", self.HINTS.get(mode, "")))
        self.cone_angle.setEnabled(mode == "Conical")

    def values(self):
        params = _params()
        params.SetString("Mode", self.mode.currentText())
        params.SetFloat("LayerHeight", self.layer_height.value())
        params.SetFloat("ConeAngle", self.cone_angle.value())
        return (
            self.mode.currentText(),
            self.layer_height.value(),
            self.cone_angle.value(),
            self.seam.currentText(),
        )


class RoboPrint_Slice(_Command):
    def GetResources(self):
        return _resources(
            "RoboPrint_Slice",
            QT_TRANSLATE_NOOP("RoboPrint_Slice", "Slice"),
            QT_TRANSLATE_NOOP(
                "RoboPrint_Slice",
                "Slices the selected solid or mesh into layers, flat or on cones, cylinders "
                "or an existing surface",
            ),
            accel="R, S",
        )

    def IsActive(self):
        return _doc() is not None and any(_printable(o) for o in _selection())

    def Activated(self):
        targets = [o for o in _selection() if _printable(o)]
        if not targets:
            _err(translate("RoboPrint", "Select a solid or a mesh to slice"))
            return
        dialog = SliceDialog(FreeCADGui.getMainWindow())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        mode, layer_height, cone_angle, seam = dialog.values()
        surface = None
        if mode == "Conformal":
            # The substrate is whatever was picked last, so the part is everything before it.
            if len(targets) < 2:
                _err(
                    translate(
                        "RoboPrint",
                        "Conformal slicing needs the part and the substrate selected, the "
                        "substrate last",
                    )
                )
                return
            surface = targets[-1]
            targets = targets[:-1]
        created = []
        with _transaction(translate("RoboPrint", "Slice")):
            for target in targets:
                obj = features.make_slices(
                    target,
                    mode,
                    layer_height,
                    doc=_doc(),
                    ConeAngle=cone_angle,
                    Seam=seam,
                )
                if surface is not None:
                    obj.Surface = surface
                created.append(obj)
        for obj in created:
            _msg(
                translate("RoboPrint", "%s: %d layers, %d contours")
                % (obj.Label, obj.LayerCount, obj.ContourCount)
            )


# ---------------------------------------------------------------------------
# Toolpath
# ---------------------------------------------------------------------------


class ToolpathDialog(QtWidgets.QDialog):
    """Bead, shells and infill for a new toolpath."""

    def __init__(self, layer_height=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("RoboPrint", "Toolpath"))
        layout = QtWidgets.QFormLayout(self)
        params = _params()

        self.bead_width = QtWidgets.QDoubleSpinBox()
        self.bead_width.setRange(0.01, 1000.0)
        self.bead_width.setDecimals(2)
        self.bead_width.setSuffix(" mm")
        self.bead_width.setValue(params.GetFloat("BeadWidth", 8.0))
        layout.addRow(translate("RoboPrint", "Bead width"), self.bead_width)

        self.bead_height = QtWidgets.QDoubleSpinBox()
        self.bead_height.setRange(0.01, 1000.0)
        self.bead_height.setDecimals(2)
        self.bead_height.setSuffix(" mm")
        # The bead is as tall as the layer it fills, so the slices decide this by default.
        self.bead_height.setValue(
            layer_height if layer_height else params.GetFloat("BeadHeight", 4.0)
        )
        layout.addRow(translate("RoboPrint", "Bead height"), self.bead_height)

        self.perimeters = QtWidgets.QSpinBox()
        self.perimeters.setRange(0, 50)
        self.perimeters.setValue(params.GetInt("Perimeters", 2))
        layout.addRow(translate("RoboPrint", "Perimeters"), self.perimeters)

        self.infill = QtWidgets.QComboBox()
        self.infill.addItems(list(toolpath.INFILL_PATTERNS))
        stored = params.GetString("InfillPattern", "None")
        if stored in toolpath.INFILL_PATTERNS:
            self.infill.setCurrentIndex(list(toolpath.INFILL_PATTERNS).index(stored))
        self.infill.currentTextChanged.connect(self.infill_changed)
        layout.addRow(translate("RoboPrint", "Infill"), self.infill)

        self.spacing = QtWidgets.QDoubleSpinBox()
        self.spacing.setRange(0.0, 1000.0)
        self.spacing.setDecimals(2)
        self.spacing.setSuffix(" mm")
        self.spacing.setSpecialValueText(translate("RoboPrint", "one bead width"))
        self.spacing.setValue(params.GetFloat("InfillSpacing", 0.0))
        layout.addRow(translate("RoboPrint", "Infill spacing"), self.spacing)

        self.orientation = QtWidgets.QComboBox()
        self.orientation.addItems(list(toolpath.ORIENTATION_MODES))
        stored = params.GetString("Orientation", "LayerNormal")
        if stored in toolpath.ORIENTATION_MODES:
            self.orientation.setCurrentIndex(list(toolpath.ORIENTATION_MODES).index(stored))
        layout.addRow(translate("RoboPrint", "Tool orientation"), self.orientation)

        self.clearance = QtWidgets.QDoubleSpinBox()
        self.clearance.setRange(0.0, 10000.0)
        self.clearance.setDecimals(1)
        self.clearance.setSuffix(" mm")
        self.clearance.setSpecialValueText(translate("RoboPrint", "move straight across"))
        self.clearance.setValue(params.GetFloat("TravelClearance", 0.0))
        self.clearance.setToolTip(
            translate(
                "RoboPrint",
                "Lift the nozzle this far along the tool axis when crossing between paths",
            )
        )
        layout.addRow(translate("RoboPrint", "Travel clearance"), self.clearance)

        self.spiral = QtWidgets.QCheckBox(
            translate("RoboPrint", "Spiral the outer wall into one continuous path")
        )
        self.spiral.setChecked(params.GetBool("Spiral", False))
        layout.addRow(self.spiral)

        self.infill_changed(self.infill.currentText())

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def infill_changed(self, pattern):
        self.spacing.setEnabled(pattern != "None")

    def values(self):
        params = _params()
        params.SetFloat("BeadWidth", self.bead_width.value())
        params.SetFloat("BeadHeight", self.bead_height.value())
        params.SetInt("Perimeters", self.perimeters.value())
        params.SetString("InfillPattern", self.infill.currentText())
        params.SetFloat("InfillSpacing", self.spacing.value())
        params.SetString("Orientation", self.orientation.currentText())
        params.SetFloat("TravelClearance", self.clearance.value())
        params.SetBool("Spiral", self.spiral.isChecked())
        return {
            "bead_width": self.bead_width.value(),
            "bead_height": self.bead_height.value(),
            "Perimeters": self.perimeters.value(),
            "InfillPattern": self.infill.currentText(),
            "InfillSpacing": self.spacing.value(),
            "Orientation": self.orientation.currentText(),
            "TravelClearance": self.clearance.value(),
            "Spiral": self.spiral.isChecked(),
        }


class RoboPrint_Toolpath(_Command):
    def GetResources(self):
        return _resources(
            "RoboPrint_Toolpath",
            QT_TRANSLATE_NOOP("RoboPrint_Toolpath", "Toolpath"),
            QT_TRANSLATE_NOOP(
                "RoboPrint_Toolpath",
                "Builds the robot toolpath from the selected slices: perimeters, infill, tool "
                "orientation and the process values",
            ),
            accel="R, T",
        )

    def IsActive(self):
        return any(features.is_roboprint_object(o, "Slices") for o in _selection())

    def Activated(self):
        sources = [o for o in _selection() if features.is_roboprint_object(o, "Slices")]
        if not sources:
            return
        dialog = ToolpathDialog(float(sources[0].LayerHeight), FreeCADGui.getMainWindow())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        options = dialog.values()
        width = options.pop("bead_width")
        height = options.pop("bead_height")
        created = []
        with _transaction(translate("RoboPrint", "Toolpath")):
            for source in sources:
                created.append(
                    features.make_toolpath(
                        source, bead_width=width, bead_height=height, doc=_doc(), **options
                    )
                )
        for obj in created:
            _msg(
                translate("RoboPrint", "%s: %d paths, %d points, %.0f mm")
                % (obj.Label, obj.PathCount, obj.PointCount, obj.PathLength)
            )


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------


class AnalysisPanel:
    """Shows the quality report of a toolpath."""

    def __init__(self, obj):
        self.obj = obj
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("RoboPrint", "Toolpath report"))
        layout = QtWidgets.QVBoxLayout(self.form)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            [translate("RoboPrint", "Check"), translate("RoboPrint", "Value")]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        self.fill()

    def row(self, name, value):
        index = self.table.rowCount()
        self.table.insertRow(index)
        self.table.setItem(index, 0, QtWidgets.QTableWidgetItem(name))
        self.table.setItem(index, 1, QtWidgets.QTableWidgetItem(value))

    def fill(self):
        obj = self.obj
        paths = features.paths_of(obj)
        source = obj.Base
        shape = None
        if source is not None and getattr(source, "Base", None) is not None:
            shape = getattr(source.Base, "Shape", None)
        report = analysis.quality_report(
            paths,
            float(obj.BeadHeight),
            shape=shape,
            overhang_limit=float(obj.OverhangLimit),
            tilt_limit=float(obj.MaxTilt),
            corner_threshold=float(obj.CornerThreshold),
            reference=(
                FreeCAD.Vector(source.Axis) if source is not None else FreeCAD.Vector(0, 0, 1)
            ),
        )
        estimate = analysis.estimate(
            paths, float(obj.Speed), float(obj.TravelSpeed), float(obj.Density)
        )
        clearance = analysis.clearance_report(
            paths,
            _params().GetFloat("ToolRadius", 25.0),
            _params().GetFloat("ToolLength", 120.0),
        )
        overhang = report["overhang"]
        self.row(
            translate("RoboPrint", "Overhang, worst"),
            "%.1f °  (limit %.0f °)" % (overhang["max"], overhang["limit"]),
        )
        self.row(
            translate("RoboPrint", "Overhang, points over the limit"),
            "%d of %d  (%.1f %%)"
            % (overhang["over_limit"], overhang["points"], 100.0 * overhang["fraction"]),
        )
        tilt = report["tilt"]
        self.row(
            translate("RoboPrint", "Tool tilt, worst"),
            "%.1f °  (limit %.0f °)" % (tilt["max"], tilt["limit"]),
        )
        cornering = report["cornering"]
        self.row(
            translate("RoboPrint", "Corners sharper than the threshold"),
            "%d  (sharpest %.0f °)" % (cornering["corners"], cornering["sharpest"]),
        )
        continuity = report["continuity"]
        self.row(
            translate("RoboPrint", "Extrusion interruptions"),
            "%d  (travel %.0f mm)" % (continuity["interruptions"], continuity["travel_length"]),
        )
        if "surface_tolerance" in report:
            tolerance = report["surface_tolerance"]
            self.row(
                translate("RoboPrint", "Distance from the model surface"),
                "worst %.2f mm, mean %.2f mm" % (tolerance["max"], tolerance["mean"]),
            )
        self.row(
            translate("RoboPrint", "Nozzle clearance"),
            "%d of %d points too close" % (clearance["hits"], clearance["points"]),
        )
        self.row(translate("RoboPrint", "Path length"), "%.0f mm" % estimate["printing_length"])
        self.row(
            translate("RoboPrint", "Material"),
            "%.0f cm³, %.0f g" % (estimate["volume"] / 1000.0, estimate["mass"]),
        )
        self.row(translate("RoboPrint", "Estimated time"), "%.2f h" % estimate["hours"])
        self.table.resizeColumnsToContents()

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def isAllowedAlterSelection(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterDocument(self):
        return False


class RoboPrint_Analyze(_Command):
    def GetResources(self):
        return _resources(
            "RoboPrint_Analyze",
            QT_TRANSLATE_NOOP("RoboPrint_Analyze", "Check toolpath"),
            QT_TRANSLATE_NOOP(
                "RoboPrint_Analyze",
                "Reports overhang, tool tilt, cornering, continuity, distance from the model, "
                "nozzle clearance, material and time",
            ),
        )

    def IsActive(self):
        return any(features.is_roboprint_object(o, "Toolpath") for o in _selection())

    def Activated(self):
        for obj in _selection():
            if features.is_roboprint_object(obj, "Toolpath"):
                if FreeCADGui.Control.activeDialog():
                    FreeCADGui.Control.closeDialog()
                FreeCADGui.Control.showDialog(AnalysisPanel(obj))
                return


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportDialog(QtWidgets.QDialog):
    """Post-processor and destination for a program."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("RoboPrint", "Export program"))
        layout = QtWidgets.QFormLayout(self)
        params = _params()
        self.flavour = QtWidgets.QComboBox()
        flavours = sorted(postprocessors.POST_PROCESSORS)
        self.flavour.addItems(flavours)
        stored = params.GetString("Flavour", "CSV")
        if stored in flavours:
            self.flavour.setCurrentIndex(flavours.index(stored))
        layout.addRow(translate("RoboPrint", "Format"), self.flavour)

        self.volumetric = QtWidgets.QCheckBox(
            translate("RoboPrint", "Volumetric extrusion (pellet extruder)")
        )
        self.volumetric.setChecked(params.GetBool("Volumetric", True))
        layout.addRow(self.volumetric)

        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit(params.GetString("LastExport", ""))
        browse = QtWidgets.QPushButton(translate("RoboPrint", "Browse…"))
        browse.clicked.connect(self.browse)
        row.addWidget(self.path)
        row.addWidget(browse)
        layout.addRow(translate("RoboPrint", "File"), row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def browse(self):
        suffix = postprocessors.POST_PROCESSORS[self.flavour.currentText()][1]
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            translate("RoboPrint", "Export program"),
            self.path.text() or os.path.expanduser("~"),
            "*%s" % suffix,
        )
        if chosen:
            self.path.setText(chosen)

    def values(self):
        params = _params()
        params.SetString("Flavour", self.flavour.currentText())
        params.SetBool("Volumetric", self.volumetric.isChecked())
        params.SetString("LastExport", self.path.text())
        return self.flavour.currentText(), self.path.text(), self.volumetric.isChecked()


class RoboPrint_Export(_Command):
    def GetResources(self):
        return _resources(
            "RoboPrint_Export",
            QT_TRANSLATE_NOOP("RoboPrint_Export", "Export program"),
            QT_TRANSLATE_NOOP(
                "RoboPrint_Export",
                "Writes the selected toolpath as CSV, G-code, KUKA KRL, ABB RAPID or a "
                "Universal Robots script",
            ),
            accel="R, E",
        )

    def IsActive(self):
        return any(features.is_roboprint_object(o, "Toolpath") for o in _selection())

    def Activated(self):
        targets = [o for o in _selection() if features.is_roboprint_object(o, "Toolpath")]
        if not targets:
            return
        dialog = ExportDialog(FreeCADGui.getMainWindow())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        flavour, path, volumetric = dialog.values()
        if not path:
            _err(translate("RoboPrint", "Choose a file to write the program to"))
            return
        for obj in targets:
            try:
                text = features.export_toolpath(
                    obj,
                    flavour,
                    path,
                    volumetric=volumetric,
                    travel_speed=float(obj.TravelSpeed),
                    filament_diameter=_params().GetFloat("FilamentDiameter", 1.75),
                )
            except Exception as exc:  # pylint: disable=broad-except
                _err(str(exc))
                return
            _msg(
                translate("RoboPrint", "%s written as %s, %d lines")
                % (obj.Label, flavour, len(text.splitlines()))
            )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register():
    """Register every command with FreeCADGui (idempotent)."""
    registered = set(FreeCADGui.listCommands())
    for name in ALL_COMMANDS:
        if name not in registered:
            FreeCADGui.addCommand(name, globals()[name]())
