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

"""GUI commands of the Freeform workbench.

Every command is a thin wrapper: it collects the selection or captures
mouse input, then calls the scripting API in :mod:`freeform.features`
inside a document transaction so it is undoable.
"""

import math

import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Vector
from PySide import QtWidgets

from . import features, geometry, palette, tracker, workplane

translate = FreeCAD.Qt.translate
QT_TRANSLATE_NOOP = FreeCAD.Qt.QT_TRANSLATE_NOOP

PARAM_PATH = features.PARAM_PATH

TOOLBAR_COMMANDS = [
    "Freeform_Stroke",
    "Freeform_Primitives",
    "Freeform_Thicken",
    "Freeform_Ribbon",
    "Freeform_Surface",
    "Freeform_Patch",
    "Freeform_Revolve",
    "Freeform_SubD",
    "Separator",
    "Freeform_Smooth",
    "Freeform_Simplify",
    "Freeform_Recognize",
    "Freeform_Join",
    "Separator",
    "Freeform_Mirror",
    "Freeform_Symmetry",
    "Freeform_SymmetryPlanes",
    "Freeform_Planes",
    "Freeform_Snap",
    "Separator",
    "Freeform_Palette",
    "Freeform_Layer",
]

MENU_COMMANDS = [c for c in TOOLBAR_COMMANDS if c != "Separator"]

ALL_COMMANDS = [
    "Freeform_Stroke",
    "Freeform_Sphere",
    "Freeform_Box",
    "Freeform_Cylinder",
    "Freeform_Cone",
    "Freeform_Torus",
    "Freeform_Primitives",
    "Freeform_Thicken",
    "Freeform_Ribbon",
    "Freeform_Surface",
    "Freeform_Patch",
    "Freeform_Revolve",
    "Freeform_SubD",
    "Freeform_Smooth",
    "Freeform_Simplify",
    "Freeform_Recognize",
    "Freeform_Join",
    "Freeform_Mirror",
    "Freeform_Symmetry",
    "Freeform_SymmetryYZ",
    "Freeform_SymmetryXZ",
    "Freeform_SymmetryXY",
    "Freeform_SymmetryFromFace",
    "Freeform_SymmetryPlanes",
    "Freeform_PlaneTop",
    "Freeform_PlaneFront",
    "Freeform_PlaneSide",
    "Freeform_PlaneView",
    "Freeform_PlaneSurface",
    "Freeform_PlaneFromFace",
    "Freeform_PlaneDraft",
    "Freeform_Planes",
    "Freeform_Snap",
    "Freeform_Palette",
    "Freeform_Layer",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _params():
    return FreeCAD.ParamGet(PARAM_PATH)


def _doc():
    return FreeCAD.ActiveDocument


def _view():
    doc = FreeCADGui.ActiveDocument
    if doc is None:
        return None
    view = doc.ActiveView
    if view is None or not hasattr(view, "getSceneGraph"):
        return None
    return view


def _msg(text):
    FreeCAD.Console.PrintMessage("Freeform: " + text + "\n")


def _err(text):
    FreeCAD.Console.PrintError("Freeform: " + text + "\n")


def _selection():
    return FreeCADGui.Selection.getSelection()


def _selection_ex():
    return FreeCADGui.Selection.getSelectionEx()


def _selected_strokes():
    return [o for o in _selection() if features.is_freeform_object(o, "Stroke")]


def _selected_curves():
    """Selected objects that carry at least one edge."""
    result = []
    for obj in _selection():
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull() and shape.Edges:
            result.append(obj)
    return result


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
            return exc_type is not None and issubclass(exc_type, (ValueError, Part.OCCError))

    return _Ctx()


def _ask_double(title, label, value, minimum=0.0, maximum=1e6, decimals=2):
    result, ok = QtWidgets.QInputDialog.getDouble(
        FreeCADGui.getMainWindow(), title, label, value, minimum, maximum, decimals
    )
    return result if ok else None


def _resources(pixmap, menu, tooltip, accel=None, checkable=None):
    resources = {
        "Pixmap": pixmap,
        "MenuText": menu,
        "ToolTip": tooltip,
    }
    if accel:
        resources["Accel"] = accel
    if checkable is not None:
        resources["Checkable"] = checkable
    return resources


class _Command:
    """Base class: active whenever a document is open."""

    def IsActive(self):
        return _doc() is not None


class _SelectionCommand(_Command):
    """Active when something is selected."""

    def IsActive(self):
        return _doc() is not None and bool(_selection())


# ---------------------------------------------------------------------------
# Stroke tool with task panel
# ---------------------------------------------------------------------------


class StrokeTaskPanel:
    """Settings for the stroke tool, shown while drawing."""

    def __init__(self, command):
        self.command = command
        params = _params()
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("Freeform", "Draw strokes"))
        layout = QtWidgets.QVBoxLayout(self.form)

        hint = QtWidgets.QLabel(
            translate(
                "Freeform",
                "Press and drag in the 3D view to draw. Each release creates a stroke. "
                "Press Escape or Close to finish.",
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.plane_combo = QtWidgets.QComboBox()
        self.plane_names = [
            ("Top", translate("Freeform", "Top (XY)")),
            ("Front", translate("Freeform", "Front (XZ)")),
            ("Side", translate("Freeform", "Side (YZ)")),
            ("View", translate("Freeform", "Facing the camera")),
            ("Surface", translate("Freeform", "On surfaces")),
            ("Custom", translate("Freeform", "Custom plane")),
            ("Draft", translate("Freeform", "Draft working plane")),
        ]
        for _, label in self.plane_names:
            self.plane_combo.addItem(label)
        plane = workplane.get_work_plane()
        self.plane_combo.setCurrentIndex([m for m, _ in self.plane_names].index(plane.mode))
        self.plane_combo.currentIndexChanged.connect(self.plane_changed)
        form.addRow(translate("Freeform", "Draw on"), self.plane_combo)

        self.snap_check = QtWidgets.QCheckBox(translate("Freeform", "Snap to grid"))
        self.snap_check.setChecked(plane.snap)
        self.snap_check.toggled.connect(self.snap_changed)
        self.grid_spin = QtWidgets.QDoubleSpinBox()
        self.grid_spin.setRange(0.01, 1e6)
        self.grid_spin.setValue(plane.grid)
        self.grid_spin.setSuffix(" mm")
        self.grid_spin.valueChanged.connect(self.snap_changed)
        snap_row = QtWidgets.QHBoxLayout()
        snap_row.addWidget(self.snap_check)
        snap_row.addWidget(self.grid_spin)
        form.addRow("", snap_row)

        self.thickness_spin = QtWidgets.QDoubleSpinBox()
        self.thickness_spin.setRange(0.0, 1e6)
        self.thickness_spin.setDecimals(2)
        self.thickness_spin.setSuffix(" mm")
        self.thickness_spin.setValue(params.GetFloat("DefaultThickness", 0.0))
        self.thickness_spin.setToolTip(
            translate("Freeform", "Tube diameter; 0 draws a plain curve")
        )
        form.addRow(translate("Freeform", "Thickness"), self.thickness_spin)

        self.taper_spin = QtWidgets.QDoubleSpinBox()
        self.taper_spin.setRange(-1.0, 1e6)
        self.taper_spin.setDecimals(2)
        self.taper_spin.setSuffix(" mm")
        self.taper_spin.setSpecialValueText(translate("Freeform", "same"))
        self.taper_spin.setValue(params.GetFloat("DefaultEndThickness", -1.0))
        self.taper_spin.setToolTip(
            translate("Freeform", "Tube diameter at the end of the stroke (taper)")
        )
        form.addRow(translate("Freeform", "End thickness"), self.taper_spin)

        self.smoothing_spin = QtWidgets.QSpinBox()
        self.smoothing_spin.setRange(0, 50)
        self.smoothing_spin.setValue(params.GetInt("DefaultSmoothing", 2))
        form.addRow(translate("Freeform", "Smoothing"), self.smoothing_spin)

        self.tolerance_spin = QtWidgets.QDoubleSpinBox()
        self.tolerance_spin.setRange(0.0, 1e4)
        self.tolerance_spin.setDecimals(3)
        self.tolerance_spin.setSuffix(" mm")
        self.tolerance_spin.setValue(params.GetFloat("DefaultTolerance", 0.5))
        self.tolerance_spin.setToolTip(
            translate("Freeform", "Simplification tolerance; 0 keeps every point")
        )
        form.addRow(translate("Freeform", "Simplify"), self.tolerance_spin)

        checks = QtWidgets.QGridLayout()
        self.closed_check = QtWidgets.QCheckBox(translate("Freeform", "Closed"))
        self.fill_check = QtWidgets.QCheckBox(translate("Freeform", "Fill closed"))
        self.recognize_check = QtWidgets.QCheckBox(
            translate("Freeform", "Recognise lines, circles and arcs")
        )
        self.recognize_check.setChecked(params.GetBool("RecognizeShapes", True))
        self.symmetry_check = QtWidgets.QCheckBox(translate("Freeform", "Symmetry"))
        self.symmetry_check.setChecked(workplane.get_symmetry_plane().enabled)
        self.symmetry_check.toggled.connect(self.symmetry_changed)
        self.continuous_check = QtWidgets.QCheckBox(translate("Freeform", "Keep drawing"))
        self.continuous_check.setChecked(params.GetBool("ContinuousDrawing", True))
        checks.addWidget(self.closed_check, 0, 0)
        checks.addWidget(self.fill_check, 0, 1)
        checks.addWidget(self.symmetry_check, 1, 0)
        checks.addWidget(self.continuous_check, 1, 1)
        checks.addWidget(self.recognize_check, 2, 0, 1, 2)
        layout.addLayout(checks)

        layout.addWidget(QtWidgets.QLabel(translate("Freeform", "Colour")))
        self.palette = palette.PaletteWidget(self.form, apply_to_selection=False)
        self.palette.colorChanged.connect(self.color_changed)
        layout.addWidget(self.palette)
        layout.addStretch()

    # -- settings ----------------------------------------------------------

    def settings(self):
        params = _params()
        params.SetFloat("DefaultThickness", self.thickness_spin.value())
        params.SetFloat("DefaultEndThickness", self.taper_spin.value())
        params.SetInt("DefaultSmoothing", self.smoothing_spin.value())
        params.SetFloat("DefaultTolerance", self.tolerance_spin.value())
        params.SetBool("RecognizeShapes", self.recognize_check.isChecked())
        params.SetBool("ContinuousDrawing", self.continuous_check.isChecked())
        return {
            "thickness": self.thickness_spin.value(),
            "end_thickness": self.taper_spin.value(),
            "smoothing": self.smoothing_spin.value(),
            "tolerance": self.tolerance_spin.value(),
            "closed": self.closed_check.isChecked(),
            "fill": self.fill_check.isChecked(),
            "recognize": self.recognize_check.isChecked(),
            "continuous": self.continuous_check.isChecked(),
        }

    def plane_changed(self, index):
        mode = self.plane_names[index][0]
        plane = workplane.get_work_plane()
        if mode == "View":
            plane.align_to_view()
        elif mode == "Draft" and not plane.align_to_draft():
            _err(translate("Freeform", "The Draft workbench is not available"))
            return
        plane.set_mode(mode)
        self.command.plane_updated()

    def snap_changed(self, *_):
        plane = workplane.get_work_plane()
        plane.snap = self.snap_check.isChecked()
        plane.grid = self.grid_spin.value()
        plane.save()

    def symmetry_changed(self, checked):
        workplane.get_symmetry_plane().set_enabled(checked)

    def color_changed(self, rgb):
        self.command.color_updated(rgb)

    # -- task dialog protocol ---------------------------------------------

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        self.command.finish()
        return True

    def isAllowedAlterSelection(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterDocument(self):
        return True


def create_stroke_from_points(points, settings, plane=None, symmetry=None):
    """Create the document objects for one captured stroke.

    Returns the list of created objects. Uses shape recognition when
    requested, colours from the palette and mirrors when symmetry is on.
    """
    doc = _doc()
    created = []
    thickness = float(settings.get("thickness", 0.0))
    end_thickness = float(settings.get("end_thickness", -1.0))
    closed = bool(settings.get("closed", False))
    obj = None
    if settings.get("recognize", True):
        conditioned = geometry.condition_stroke(points, smoothing=int(settings.get("smoothing", 2)))
        kind, data = geometry.recognize_stroke(conditioned, closed=closed)
        if kind == "line":
            obj = features.make_stroke(list(data), doc=doc, smoothing=0)
            obj.Degree = 1
            obj.Label = translate("Freeform", "Line")
        elif kind in ("circle", "arc"):
            obj = _make_circle_object(kind, data, doc)
        if obj is not None and thickness > 0 and features.is_freeform_object(obj, "Stroke"):
            obj.Thickness = thickness
            obj.EndThickness = end_thickness
    if obj is None:
        obj = features.make_stroke(
            points,
            doc=doc,
            closed=closed,
            thickness=thickness,
            smoothing=int(settings.get("smoothing", 2)),
            tolerance=float(settings.get("tolerance", 0.0)),
        )
        obj.EndThickness = end_thickness
        if closed and settings.get("fill", False):
            obj.MakeFace = True
    created.append(obj)
    symmetry = symmetry or workplane.get_symmetry_plane()
    if symmetry.enabled:
        created.append(features.make_mirror(obj, symmetry.origin, symmetry.normal, doc=doc))
    return created


def _make_circle_object(kind, data, doc):
    """Create a parametric ``Part::Circle`` for a recognised circle or arc."""
    if kind == "circle":
        center, normal, radius = data
        circle = Part.Circle(center, normal, radius)
        angle1, angle2 = 0.0, 360.0
        label = translate("Freeform", "Circle")
    else:
        start, mid, end = data
        arc = Part.ArcOfCircle(start, mid, end)
        circle = Part.Circle(arc.Center, arc.Axis, arc.Radius)
        circle.XAxis = arc.XAxis
        angle1, angle2 = math.degrees(arc.FirstParameter), math.degrees(arc.LastParameter)
        label = translate("Freeform", "Arc")
    obj = doc.addObject("Part::Circle", "Circle")
    obj.Label = label
    obj.Radius = circle.Radius
    obj.Angle1 = angle1
    obj.Angle2 = angle2
    rotation = FreeCAD.Rotation(circle.XAxis, circle.YAxis, circle.Axis, "ZXY")
    obj.Placement = FreeCAD.Placement(circle.Center, rotation)
    if FreeCAD.GuiUp and obj.ViewObject is not None:
        obj.ViewObject.LineWidth = 3.0
        rgb = palette.get_current_color()
        if rgb is not None:
            palette.apply_color([obj], rgb)
    return obj


class Freeform_Stroke(_Command):
    """Draw free-form strokes with the mouse."""

    def __init__(self):
        self.panel = None
        self.capture = None
        self.plane_tracker = None

    def GetResources(self):
        return _resources(
            "Freeform_Stroke",
            QT_TRANSLATE_NOOP("Freeform_Stroke", "Stroke"),
            QT_TRANSLATE_NOOP(
                "Freeform_Stroke",
                "Draws free-form strokes in 3D by dragging the mouse on the drawing plane, "
                "on surfaces or facing the camera",
            ),
            accel="F, S",
        )

    def IsActive(self):
        return _doc() is not None and _view() is not None

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        view = _view()
        if view is None:
            return
        self.panel = StrokeTaskPanel(self)
        FreeCADGui.Control.showDialog(self.panel)
        rgb = palette.get_current_color() or (1.0, 0.5, 0.0)
        self.capture = tracker.StrokeCapture(
            self.on_stroke,
            self.finish,
            view=view,
            min_pixel_step=_params().GetInt("PixelStep", 3),
            color=rgb,
        )
        self.capture.mirror = workplane.get_symmetry_plane()
        self.plane_tracker = tracker.PlaneTracker(view=view)
        self.plane_updated()
        self.capture.start()
        _msg(translate("Freeform", "Stroke tool: drag to draw, Escape to finish"))

    def plane_updated(self):
        if self.plane_tracker is not None:
            plane = workplane.get_work_plane()
            if plane.mode in ("Surface", "View"):
                self.plane_tracker.off()
            else:
                self.plane_tracker.update(plane)

    def color_updated(self, rgb):
        if self.capture is not None:
            self.capture.set_color(rgb or (1.0, 0.5, 0.0))

    def on_stroke(self, points):
        settings = self.panel.settings()
        with _transaction(translate("Freeform", "Stroke")):
            created = create_stroke_from_points(points, settings)
            _msg(translate("Freeform", "Created %s") % ", ".join(o.Label for o in created))
        if not settings["continuous"]:
            self.finish()

    def finish(self):
        if self.capture is not None:
            self.capture.finalize()
            self.capture = None
        if self.plane_tracker is not None:
            self.plane_tracker.finalize()
            self.plane_tracker = None
        if self.panel is not None:
            self.panel = None
            if FreeCADGui.Control.activeDialog():
                FreeCADGui.Control.closeDialog()


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class _PrimitiveCommand(_Command):
    kind = "Sphere"
    menu = QT_TRANSLATE_NOOP("Freeform_Sphere", "Sphere")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_Sphere", "Places a sphere: click for the default size or drag to size it"
    )

    def __init__(self):
        self.capture = None

    def GetResources(self):
        return _resources("Freeform_" + self.kind, self.menu, self.tooltip)

    def IsActive(self):
        return _doc() is not None and _view() is not None

    def Activated(self):
        view = _view()
        if view is None:
            return
        self.capture = tracker.ClickCapture(self.on_place, self.finish, view=view)
        self.capture.start()
        _msg(
            translate("Freeform", "%s: click or drag in the 3D view to place, Escape to finish")
            % self.kind
        )

    def on_place(self, point, size):
        if size < 1e-6:
            size = _params().GetFloat("PrimitiveSize", 10.0)
        else:
            size *= 2.0  # the drag radius is half the extent
        plane = workplane.get_work_plane()
        with _transaction(translate("Freeform", "Primitive")):
            obj = features.make_primitive(self.kind, point, size, plane.normal, doc=_doc())
            symmetry = workplane.get_symmetry_plane()
            if symmetry.enabled and not symmetry.is_on_plane(point, size * 0.01):
                features.make_mirror(obj, symmetry.origin, symmetry.normal, doc=_doc())
        if not _params().GetBool("ContinuousDrawing", True):
            self.finish()

    def finish(self):
        if self.capture is not None:
            self.capture.finalize()
            self.capture = None


class Freeform_Sphere(_PrimitiveCommand):
    kind = "Sphere"


class Freeform_Box(_PrimitiveCommand):
    kind = "Box"
    menu = QT_TRANSLATE_NOOP("Freeform_Box", "Box")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_Box", "Places a box: click for the default size or drag to size it"
    )


class Freeform_Cylinder(_PrimitiveCommand):
    kind = "Cylinder"
    menu = QT_TRANSLATE_NOOP("Freeform_Cylinder", "Cylinder")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_Cylinder", "Places a cylinder: click for the default size or drag to size it"
    )


class Freeform_Cone(_PrimitiveCommand):
    kind = "Cone"
    menu = QT_TRANSLATE_NOOP("Freeform_Cone", "Cone")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_Cone", "Places a cone: click for the default size or drag to size it"
    )


class Freeform_Torus(_PrimitiveCommand):
    kind = "Torus"
    menu = QT_TRANSLATE_NOOP("Freeform_Torus", "Torus")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_Torus", "Places a torus: click for the default size or drag to size it"
    )


class Freeform_Primitives(_Command):
    """Group command with the primitives."""

    def GetResources(self):
        return {
            "Pixmap": "Freeform_Primitives",
            "MenuText": QT_TRANSLATE_NOOP("Freeform_Primitives", "Primitives"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Freeform_Primitives", "Places primitive solids on the drawing plane"
            ),
        }

    def GetCommands(self):
        return (
            "Freeform_Sphere",
            "Freeform_Box",
            "Freeform_Cylinder",
            "Freeform_Cone",
            "Freeform_Torus",
        )

    def GetDefaultCommand(self):
        return 0


# ---------------------------------------------------------------------------
# Stroke modifiers
# ---------------------------------------------------------------------------


class Freeform_Thicken(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Thicken",
            QT_TRANSLATE_NOOP("Freeform_Thicken", "Thicken"),
            QT_TRANSLATE_NOOP(
                "Freeform_Thicken",
                "Gives the selected strokes a tube thickness (optionally tapered)",
            ),
        )

    def IsActive(self):
        return bool(_selected_strokes())

    def Activated(self):
        strokes = _selected_strokes()
        current = (
            max(float(s.Thickness) for s in strokes)
            or _params().GetFloat("DefaultThickness", 2.0)
            or 2.0
        )
        thickness = _ask_double(
            translate("Freeform", "Thicken"),
            translate("Freeform", "Tube diameter (0 removes the tube):"),
            current,
        )
        if thickness is None:
            return
        end = _ask_double(
            translate("Freeform", "Thicken"),
            translate("Freeform", "End diameter (same as start when equal):"),
            thickness,
        )
        with _transaction(translate("Freeform", "Thicken")):
            for stroke in strokes:
                stroke.Thickness = thickness
                stroke.EndThickness = -1.0 if end is None or abs(end - thickness) < 1e-9 else end


class Freeform_Smooth(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Smooth",
            QT_TRANSLATE_NOOP("Freeform_Smooth", "Smooth"),
            QT_TRANSLATE_NOOP("Freeform_Smooth", "Smooths the selected strokes a little more"),
        )

    def IsActive(self):
        return bool(_selected_strokes())

    def Activated(self):
        with _transaction(translate("Freeform", "Smooth")):
            for stroke in _selected_strokes():
                stroke.Smoothing = min(100, int(stroke.Smoothing) + 2)


class Freeform_Simplify(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Simplify",
            QT_TRANSLATE_NOOP("Freeform_Simplify", "Simplify"),
            QT_TRANSLATE_NOOP(
                "Freeform_Simplify", "Reduces the number of points of the selected strokes"
            ),
        )

    def IsActive(self):
        return bool(_selected_strokes())

    def Activated(self):
        strokes = _selected_strokes()
        current = max(float(s.Tolerance) for s in strokes)
        if current <= 0:
            current = max(0.01, max(float(s.Length) for s in strokes) * 0.01)
        tolerance = _ask_double(
            translate("Freeform", "Simplify"),
            translate("Freeform", "Tolerance (0 keeps every point):"),
            current,
            decimals=3,
        )
        if tolerance is None:
            return
        with _transaction(translate("Freeform", "Simplify")):
            for stroke in strokes:
                stroke.Tolerance = tolerance


class Freeform_Recognize(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Recognize",
            QT_TRANSLATE_NOOP("Freeform_Recognize", "Recognise shape"),
            QT_TRANSLATE_NOOP(
                "Freeform_Recognize",
                "Replaces the selected strokes by the line, circle or arc they resemble",
            ),
        )

    def IsActive(self):
        return bool(_selected_strokes())

    def Activated(self):
        strokes = _selected_strokes()
        with _transaction(translate("Freeform", "Recognise shape")):
            for stroke in strokes:
                points = stroke.Proxy.conditioned_points(stroke)
                tolerance = (
                    geometry.polyline_length(points)
                    * _params().GetFloat("RecognizeTolerance", 5.0)
                    / 100.0
                )
                kind, data = geometry.recognize_stroke(
                    points, tolerance=tolerance, closed=stroke.Closed
                )
                if kind == "line":
                    stroke.Points = list(data)
                    stroke.Degree = 1
                    stroke.Smoothing = 0
                    stroke.Label = translate("Freeform", "Line")
                elif kind in ("circle", "arc"):
                    obj = _make_circle_object(kind, data, _doc())
                    if float(stroke.Thickness) > 0:
                        _msg(
                            translate("Freeform", "Tube thickness of %s was dropped") % stroke.Label
                        )
                    obj.Label = stroke.Label + " " + obj.Label
                    _doc().removeObject(stroke.Name)
                else:
                    _msg(
                        translate("Freeform", "%s does not look like a line, circle or arc")
                        % stroke.Label
                    )


class Freeform_Join(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Join",
            QT_TRANSLATE_NOOP("Freeform_Join", "Join"),
            QT_TRANSLATE_NOOP(
                "Freeform_Join", "Joins the selected strokes end to end into one stroke"
            ),
        )

    def IsActive(self):
        return len(_selected_strokes()) >= 2

    def Activated(self):
        strokes = _selected_strokes()
        points = list(strokes[0].Proxy.conditioned_points(strokes[0]))
        remaining = strokes[1:]
        # greedy chaining: always append the stroke whose end is closest
        while remaining:
            tail = points[-1]
            best, best_dist, reverse = None, None, False
            for stroke in remaining:
                pts = stroke.Proxy.conditioned_points(stroke)
                d_start = (pts[0] - tail).Length
                d_end = (pts[-1] - tail).Length
                if best_dist is None or min(d_start, d_end) < best_dist:
                    best, best_dist, reverse = stroke, min(d_start, d_end), d_end < d_start
            pts = best.Proxy.conditioned_points(best)
            if reverse:
                pts = list(reversed(pts))
            points.extend(pts)
            remaining.remove(best)
        first = strokes[0]
        with _transaction(translate("Freeform", "Join strokes")):
            obj = features.make_stroke(
                points, doc=_doc(), thickness=float(first.Thickness), smoothing=1
            )
            obj.Label = first.Label
            if FreeCAD.GuiUp and first.ViewObject is not None:
                for prop in ("LineColor", "ShapeAppearance", "LineWidth"):
                    try:
                        setattr(obj.ViewObject, prop, getattr(first.ViewObject, prop))
                    except Exception:  # pylint: disable=broad-except
                        pass
            for stroke in strokes:
                if not stroke.InList:
                    _doc().removeObject(stroke.Name)
                else:
                    stroke.ViewObject.hide()


# ---------------------------------------------------------------------------
# Surface tools
# ---------------------------------------------------------------------------


class Freeform_Ribbon(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Ribbon",
            QT_TRANSLATE_NOOP("Freeform_Ribbon", "Ribbon"),
            QT_TRANSLATE_NOOP(
                "Freeform_Ribbon", "Turns the selected strokes into flat ribbons of a given width"
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        curves = _selected_curves()
        width = _ask_double(
            translate("Freeform", "Ribbon"),
            translate("Freeform", "Ribbon width:"),
            _params().GetFloat("RibbonWidth", 10.0),
            minimum=0.001,
        )
        if width is None:
            return
        _params().SetFloat("RibbonWidth", width)
        plane = workplane.get_work_plane()
        with _transaction(translate("Freeform", "Ribbon")):
            for curve in curves:
                features.make_ribbon(curve, width=width, normal=plane.normal, doc=_doc())


class Freeform_Surface(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Surface",
            QT_TRANSLATE_NOOP("Freeform_Surface", "Surface"),
            QT_TRANSLATE_NOOP(
                "Freeform_Surface", "Spans a smooth surface through two or more selected strokes"
            ),
        )

    def IsActive(self):
        return len(_selected_curves()) >= 2

    def Activated(self):
        curves = _selected_curves()
        with _transaction(translate("Freeform", "Surface")):
            features.make_surface(curves, doc=_doc())


class Freeform_Patch(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Patch",
            QT_TRANSLATE_NOOP("Freeform_Patch", "Patch"),
            QT_TRANSLATE_NOOP(
                "Freeform_Patch", "Fills a closed loop of selected strokes or edges with a surface"
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        boundary = []
        for sel in _selection_ex():
            subs = [s for s in sel.SubElementNames if s.startswith("Edge")]
            boundary.append((sel.Object, subs))
        with _transaction(translate("Freeform", "Patch")):
            features.make_patch(boundary, doc=_doc())


class Freeform_Revolve(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Revolve",
            QT_TRANSLATE_NOOP("Freeform_Revolve", "Revolve"),
            QT_TRANSLATE_NOOP(
                "Freeform_Revolve",
                "Revolves the selected stroke around the vertical axis of the drawing plane, "
                "or around a second selected straight stroke",
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        curves = _selected_curves()
        plane = workplane.get_work_plane()
        origin, axis = plane.origin, plane.v
        profiles = curves
        if len(curves) >= 2:
            candidate = curves[-1].Shape
            if len(candidate.Edges) == 1 and candidate.Edges[0].Curve.__class__.__name__ in (
                "Line",
                "LineSegment",
            ):
                edge = candidate.Edges[0]
                origin = edge.Vertexes[0].Point
                axis = edge.Vertexes[-1].Point - origin
                profiles = curves[:-1]
        with _transaction(translate("Freeform", "Revolve")):
            for profile in profiles:
                closed = all(w.isClosed() for w in profile.Shape.Wires) and bool(
                    profile.Shape.Wires
                )
                features.make_revolve(profile, origin, axis, 360.0, solid=closed, doc=_doc())


class Freeform_SubD(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_SubD",
            QT_TRANSLATE_NOOP("Freeform_SubD", "Subdivide"),
            QT_TRANSLATE_NOOP(
                "Freeform_SubD",
                "Smooths the selected blocky shape or mesh into an organic subdivision surface",
            ),
        )

    def IsActive(self):
        return any(hasattr(o, "Shape") or hasattr(o, "Mesh") for o in _selection())

    def Activated(self):
        with _transaction(translate("Freeform", "Subdivide")):
            for obj in _selection():
                if hasattr(obj, "Shape") or hasattr(obj, "Mesh"):
                    features.make_subd(
                        obj, iterations=_params().GetInt("SubDIterations", 2), doc=_doc()
                    )


# ---------------------------------------------------------------------------
# Symmetry
# ---------------------------------------------------------------------------


class Freeform_Mirror(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Mirror",
            QT_TRANSLATE_NOOP("Freeform_Mirror", "Mirror"),
            QT_TRANSLATE_NOOP(
                "Freeform_Mirror",
                "Creates live mirrored copies of the selection across the symmetry plane",
            ),
        )

    def Activated(self):
        symmetry = workplane.get_symmetry_plane()
        with _transaction(translate("Freeform", "Mirror")):
            for obj in _selection():
                if hasattr(obj, "Shape"):
                    features.make_mirror(obj, symmetry.origin, symmetry.normal, doc=_doc())


class Freeform_Symmetry(_Command):
    """Toggle: new strokes and primitives get a live mirror copy."""

    def GetResources(self):
        return _resources(
            "Freeform_Symmetry",
            QT_TRANSLATE_NOOP("Freeform_Symmetry", "Symmetry mode"),
            QT_TRANSLATE_NOOP(
                "Freeform_Symmetry",
                "When enabled, every new stroke and primitive is mirrored across the symmetry plane",
            ),
            checkable=workplane.get_symmetry_plane().enabled,
        )

    def IsActive(self):
        return True

    def Activated(self, index=0):
        workplane.get_symmetry_plane().set_enabled(bool(index))
        state = translate("Freeform", "on") if index else translate("Freeform", "off")
        _msg(
            translate("Freeform", "Symmetry mode %s (plane %s)")
            % (state, workplane.get_symmetry_plane().axis_name())
        )


class _SymmetryPlaneCommand(_Command):
    normal = Vector(1, 0, 0)
    name = "YZ"
    menu = QT_TRANSLATE_NOOP("Freeform_SymmetryYZ", "Symmetry plane YZ")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_SymmetryYZ", "Mirrors left/right across the YZ plane through the origin"
    )

    def GetResources(self):
        return _resources("Freeform_Symmetry" + self.name, self.menu, self.tooltip)

    def IsActive(self):
        return True

    def Activated(self):
        workplane.get_symmetry_plane().set(Vector(0, 0, 0), self.normal)
        _msg(translate("Freeform", "Symmetry plane set to %s") % self.name)


class Freeform_SymmetryYZ(_SymmetryPlaneCommand):
    pass


class Freeform_SymmetryXZ(_SymmetryPlaneCommand):
    normal = Vector(0, 1, 0)
    name = "XZ"
    menu = QT_TRANSLATE_NOOP("Freeform_SymmetryXZ", "Symmetry plane XZ")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_SymmetryXZ", "Mirrors front/back across the XZ plane through the origin"
    )


class Freeform_SymmetryXY(_SymmetryPlaneCommand):
    normal = Vector(0, 0, 1)
    name = "XY"
    menu = QT_TRANSLATE_NOOP("Freeform_SymmetryXY", "Symmetry plane XY")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_SymmetryXY", "Mirrors top/bottom across the XY plane through the origin"
    )


def _selected_face():
    for sel in _selection_ex():
        for sub in sel.SubElementNames:
            if sub.startswith("Face"):
                return sel.Object, sub
    return None, None


class Freeform_SymmetryFromFace(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_SymmetryFromFace",
            QT_TRANSLATE_NOOP("Freeform_SymmetryFromFace", "Symmetry plane from face"),
            QT_TRANSLATE_NOOP(
                "Freeform_SymmetryFromFace", "Uses the selected planar face as the symmetry plane"
            ),
        )

    def IsActive(self):
        return _selected_face()[0] is not None

    def Activated(self):
        obj, sub = _selected_face()
        face = obj.Shape.getElement(sub)
        normal = face.normalAt(0, 0)
        if face.Orientation == "Reversed":
            normal = normal * -1.0
        workplane.get_symmetry_plane().set(face.CenterOfMass, normal)
        _msg(translate("Freeform", "Symmetry plane set from %s.%s") % (obj.Label, sub))


class Freeform_SymmetryPlanes(_Command):
    def GetResources(self):
        return {
            "Pixmap": "Freeform_SymmetryPlanes",
            "MenuText": QT_TRANSLATE_NOOP("Freeform_SymmetryPlanes", "Symmetry plane"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Freeform_SymmetryPlanes", "Chooses the plane used by symmetry mode and mirror"
            ),
        }

    def GetCommands(self):
        return (
            "Freeform_SymmetryYZ",
            "Freeform_SymmetryXZ",
            "Freeform_SymmetryXY",
            "Freeform_SymmetryFromFace",
        )

    def GetDefaultCommand(self):
        return 0

    def IsActive(self):
        return True


# ---------------------------------------------------------------------------
# Drawing plane
# ---------------------------------------------------------------------------


class _PlaneCommand(_Command):
    mode = "Top"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneTop", "Draw on top plane")
    tooltip = QT_TRANSLATE_NOOP("Freeform_PlaneTop", "Strokes are drawn on the XY plane")

    def GetResources(self):
        return _resources("Freeform_Plane" + self.mode, self.menu, self.tooltip)

    def IsActive(self):
        return True

    def Activated(self):
        plane = workplane.get_work_plane()
        if self.mode == "View":
            plane.align_to_view()
        if self.mode == "Draft" and not plane.align_to_draft():
            _err(translate("Freeform", "The Draft workbench is not available"))
            return
        plane.set_mode(self.mode)
        _msg(translate("Freeform", "Drawing plane: %s") % self.mode)


class Freeform_PlaneTop(_PlaneCommand):
    pass


class Freeform_PlaneFront(_PlaneCommand):
    mode = "Front"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneFront", "Draw on front plane")
    tooltip = QT_TRANSLATE_NOOP("Freeform_PlaneFront", "Strokes are drawn on the XZ plane")


class Freeform_PlaneSide(_PlaneCommand):
    mode = "Side"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneSide", "Draw on side plane")
    tooltip = QT_TRANSLATE_NOOP("Freeform_PlaneSide", "Strokes are drawn on the YZ plane")


class Freeform_PlaneView(_PlaneCommand):
    mode = "View"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneView", "Draw facing the camera")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_PlaneView",
        "Strokes are drawn in the air on a plane facing the camera; orbit to draw in 3D",
    )


class Freeform_PlaneSurface(_PlaneCommand):
    mode = "Surface"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneSurface", "Draw on surfaces")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_PlaneSurface", "Strokes follow the geometry under the cursor"
    )


class Freeform_PlaneDraft(_PlaneCommand):
    mode = "Draft"
    menu = QT_TRANSLATE_NOOP("Freeform_PlaneDraft", "Draw on Draft working plane")
    tooltip = QT_TRANSLATE_NOOP(
        "Freeform_PlaneDraft", "Strokes are drawn on the Draft workbench working plane"
    )


class Freeform_PlaneFromFace(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_PlaneFromFace",
            QT_TRANSLATE_NOOP("Freeform_PlaneFromFace", "Draw on selected face"),
            QT_TRANSLATE_NOOP(
                "Freeform_PlaneFromFace", "Uses the selected face as the drawing plane"
            ),
        )

    def IsActive(self):
        return _selected_face()[0] is not None

    def Activated(self):
        obj, sub = _selected_face()
        if workplane.get_work_plane().align_to_face(obj.Shape, sub):
            _msg(translate("Freeform", "Drawing plane set from %s.%s") % (obj.Label, sub))


class Freeform_Planes(_Command):
    def GetResources(self):
        return {
            "Pixmap": "Freeform_Planes",
            "MenuText": QT_TRANSLATE_NOOP("Freeform_Planes", "Drawing plane"),
            "ToolTip": QT_TRANSLATE_NOOP("Freeform_Planes", "Chooses where strokes are drawn"),
        }

    def GetCommands(self):
        return (
            "Freeform_PlaneTop",
            "Freeform_PlaneFront",
            "Freeform_PlaneSide",
            "Freeform_PlaneView",
            "Freeform_PlaneSurface",
            "Freeform_PlaneFromFace",
            "Freeform_PlaneDraft",
        )

    def GetDefaultCommand(self):
        return 0

    def IsActive(self):
        return True


class Freeform_Snap(_Command):
    def GetResources(self):
        return _resources(
            "Freeform_Snap",
            QT_TRANSLATE_NOOP("Freeform_Snap", "Snap to grid"),
            QT_TRANSLATE_NOOP(
                "Freeform_Snap", "Snaps stroke points to the grid of the drawing plane"
            ),
            checkable=workplane.get_work_plane().snap,
        )

    def IsActive(self):
        return True

    def Activated(self, index=0):
        plane = workplane.get_work_plane()
        plane.snap = bool(index)
        plane.save()


# ---------------------------------------------------------------------------
# Palette and layers
# ---------------------------------------------------------------------------


class Freeform_Palette(_Command):
    def GetResources(self):
        return _resources(
            "Freeform_Palette",
            QT_TRANSLATE_NOOP("Freeform_Palette", "Colour palette"),
            QT_TRANSLATE_NOOP(
                "Freeform_Palette", "Picks the colour for new strokes and recolours the selection"
            ),
        )

    def IsActive(self):
        return True

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        FreeCADGui.Control.showDialog(palette.PaletteTaskPanel())


class Freeform_Layer(_Command):
    def GetResources(self):
        return _resources(
            "Freeform_Layer",
            QT_TRANSLATE_NOOP("Freeform_Layer", "New layer"),
            QT_TRANSLATE_NOOP(
                "Freeform_Layer",
                "Creates a layer in the current colour and moves the selection into it",
            ),
        )

    def Activated(self):
        name, ok = QtWidgets.QInputDialog.getText(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "New layer"),
            translate("Freeform", "Layer name:"),
            QtWidgets.QLineEdit.Normal,
            translate("Freeform", "Layer"),
        )
        if not ok or not name:
            return
        rgb = palette.get_current_color()
        with _transaction(translate("Freeform", "New layer")):
            layer = palette.make_layer(name, rgb, doc=_doc())
            selection = [o for o in _selection() if o is not layer]
            if selection:
                palette.add_to_layer(layer, selection)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register():
    """Register every command with FreeCADGui (idempotent)."""
    registered = set(FreeCADGui.listCommands())
    for name in ALL_COMMANDS:
        if name in registered:
            continue
        cls = globals()[name]
        FreeCADGui.addCommand(name, cls())
