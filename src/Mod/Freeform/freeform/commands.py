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

from . import features, generators, geometry, palette, tracker, workplane

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
    "Freeform_Sweep",
    "Freeform_Extrude",
    "Freeform_SubD",
    "Freeform_Solidify",
    "Freeform_Shell",
    "Separator",
    "Freeform_Smooth",
    "Freeform_Simplify",
    "Freeform_Recognize",
    "Freeform_Join",
    "Freeform_ToSketch",
    "Separator",
    "Freeform_Mirror",
    "Freeform_Symmetry",
    "Freeform_SymmetryPlanes",
    "Freeform_Planes",
    "Freeform_Snap",
    "Separator",
    "Freeform_Palette",
    "Freeform_Layer",
    "Separator",
    "Std_TransformManip",
]

PARAMETRIC_COMMANDS = [
    "Freeform_Expression",
    "Freeform_Offset",
    "Freeform_Blend",
    "Freeform_Divide",
    "Freeform_Contours",
    "Separator",
    "Freeform_Tween",
    "Freeform_Project",
    "Freeform_Sweep2",
    "Separator",
    "Freeform_CurveArray",
    "Freeform_SurfaceGrid",
    "Freeform_Voronoi",
    "Freeform_Populate",
    "Separator",
    "Freeform_Lattice",
    "Freeform_Frame",
    "Freeform_LSystem",
    "Separator",
    "Freeform_Deform",
    "Freeform_BoxMorph",
    "Freeform_Relax",
]

MENU_COMMANDS = [c for c in TOOLBAR_COMMANDS if c != "Separator" and c.startswith("Freeform_")]
PARAMETRIC_MENU_COMMANDS = [c for c in PARAMETRIC_COMMANDS if c != "Separator"]

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
    "Freeform_Sweep",
    "Freeform_Extrude",
    "Freeform_SubD",
    "Freeform_Solidify",
    "Freeform_Shell",
    "Freeform_Smooth",
    "Freeform_Simplify",
    "Freeform_Recognize",
    "Freeform_Join",
    "Freeform_ToSketch",
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
    "Freeform_Expression",
    "Freeform_Offset",
    "Freeform_Blend",
    "Freeform_Divide",
    "Freeform_Contours",
    "Freeform_CurveArray",
    "Freeform_SurfaceGrid",
    "Freeform_Voronoi",
    "Freeform_Deform",
    "Freeform_Relax",
    "Freeform_Populate",
    "Freeform_Lattice",
    "Freeform_Tween",
    "Freeform_LSystem",
    "Freeform_BoxMorph",
    "Freeform_Project",
    "Freeform_Sweep2",
    "Freeform_Frame",
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


def _sync_checkable(name, checked):
    """Update the toolbar/menu action of a checkable command without firing it."""
    from PySide import QtGui

    action_type = getattr(QtGui, "QAction", None) or QtWidgets.QAction
    window = FreeCADGui.getMainWindow()
    if window is None:
        return
    for action in window.findChildren(action_type):
        if action.objectName() == name and action.isCheckable() and action.isChecked() != checked:
            action.blockSignals(True)
            action.setChecked(bool(checked))
            action.blockSignals(False)


ACTIVE_STROKE_COMMAND = None


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

        self.profile_combo = QtWidgets.QComboBox()
        self.profile_labels = [
            ("Round", translate("Freeform", "Round")),
            ("Square", translate("Freeform", "Square")),
            ("Triangle", translate("Freeform", "Triangle")),
            ("Flat", translate("Freeform", "Flat")),
        ]
        for _, label in self.profile_labels:
            self.profile_combo.addItem(label)
        default_profile = params.GetString("DefaultProfile", "Round")
        profile_keys = [k for k, _ in self.profile_labels]
        if default_profile in profile_keys:
            self.profile_combo.setCurrentIndex(profile_keys.index(default_profile))
        self.profile_combo.setToolTip(translate("Freeform", "Cross section used for tubes"))
        form.addRow(translate("Freeform", "Profile"), self.profile_combo)

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
        self.snap_ends_check = QtWidgets.QCheckBox(translate("Freeform", "Snap to stroke ends"))
        self.snap_ends_check.setToolTip(
            translate(
                "Freeform",
                "Start and end points close to the end of an existing stroke snap to it; "
                "a stroke ending where it started is closed automatically",
            )
        )
        self.snap_ends_check.setChecked(params.GetBool("SnapEndpoints", True))
        checks.addWidget(self.closed_check, 0, 0)
        checks.addWidget(self.fill_check, 0, 1)
        checks.addWidget(self.symmetry_check, 1, 0)
        checks.addWidget(self.continuous_check, 1, 1)
        checks.addWidget(self.recognize_check, 2, 0, 1, 2)
        checks.addWidget(self.snap_ends_check, 3, 0, 1, 2)
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
        params.SetBool("SnapEndpoints", self.snap_ends_check.isChecked())
        profile = self.profile_labels[self.profile_combo.currentIndex()][0]
        params.SetString("DefaultProfile", profile)
        return {
            "profile": profile,
            "snap_ends": self.snap_ends_check.isChecked(),
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
        _sync_checkable("Freeform_Snap", plane.snap)

    def symmetry_changed(self, checked):
        workplane.get_symmetry_plane().set_enabled(checked)
        _sync_checkable("Freeform_Symmetry", checked)

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
            obj.Profile = settings.get("profile", "Round")
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
        obj.Profile = settings.get("profile", "Round")
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


def stroke_end_targets(doc):
    """End points of every stroke-like object in ``doc`` (for snapping)."""
    targets = []
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Vertexes:
            continue
        if features.is_freeform_object(obj, "Stroke") or obj.TypeId == "Part::Circle":
            if shape.ShapeType not in ("Wire", "Edge"):
                continue  # tubes, faces and solids have no free ends
            targets.append(shape.Vertexes[0].Point)
            targets.append(shape.Vertexes[-1].Point)
    return targets


def snap_stroke_ends(view, points, targets, radius_px=12):
    """Snap the first and last point of ``points`` to nearby ``targets``.

    Distances are measured on screen. Returns ``(points, closed)``; ``closed``
    is True when the stroke ends within the radius of its own start, in which
    case the last point is dropped so the closed curve does not double up.
    """
    if len(points) < 2:
        return list(points), False
    pts = list(points)

    def screen(p):
        x, y = view.getPointOnScreen(p)
        return float(x), float(y)

    def nearest(p):
        sx, sy = screen(p)
        best, best_d2 = None, radius_px * radius_px
        for target in targets:
            tx, ty = screen(target)
            d2 = (tx - sx) ** 2 + (ty - sy) ** 2
            if d2 <= best_d2:
                best, best_d2 = target, d2
        return best

    start = nearest(pts[0])
    if start is not None:
        pts[0] = Vector(start)
    closed = False
    if len(pts) >= 8:
        sx, sy = screen(pts[0])
        ex, ey = screen(pts[-1])
        if (sx - ex) ** 2 + (sy - ey) ** 2 <= radius_px * radius_px:
            closed = True
            pts.pop()
    if not closed:
        end = nearest(pts[-1])
        if end is not None:
            pts[-1] = Vector(end)
    return pts, closed


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
        global ACTIVE_STROKE_COMMAND  # pylint: disable=global-statement
        ACTIVE_STROKE_COMMAND = self
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
        if settings.get("snap_ends", True) and self.capture is not None:
            targets = stroke_end_targets(_doc())
            points, auto_closed = snap_stroke_ends(self.capture.view, points, targets)
            if auto_closed:
                settings["closed"] = True
        with _transaction(translate("Freeform", "Stroke")):
            created = create_stroke_from_points(points, settings)
            _msg(translate("Freeform", "Created %s") % ", ".join(o.Label for o in created))
        if not settings["continuous"]:
            self.finish()

    def finish(self):
        global ACTIVE_STROKE_COMMAND  # pylint: disable=global-statement
        if ACTIVE_STROKE_COMMAND is self:
            ACTIVE_STROKE_COMMAND = None
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
            accel="F, T",
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


class Freeform_Solidify(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Solidify",
            QT_TRANSLATE_NOOP("Freeform_Solidify", "Solidify"),
            QT_TRANSLATE_NOOP(
                "Freeform_Solidify",
                "Converts the selected mesh (for example a subdivision surface) into a solid",
            ),
        )

    def IsActive(self):
        return any(hasattr(o, "Mesh") for o in _selection())

    def Activated(self):
        with _transaction(translate("Freeform", "Solidify")):
            for obj in _selection():
                if hasattr(obj, "Mesh"):
                    features.make_mesh_solid(obj, doc=_doc())


class Freeform_Sweep(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Sweep",
            QT_TRANSLATE_NOOP("Freeform_Sweep", "Sweep"),
            QT_TRANSLATE_NOOP(
                "Freeform_Sweep",
                "Sweeps a closed profile stroke along a path stroke: select the path first, "
                "then the profile",
            ),
        )

    def IsActive(self):
        return len(_selected_curves()) == 2

    def Activated(self):
        path, profile = _selected_curves()
        closed = bool(profile.Shape.Wires) and all(w.isClosed() for w in profile.Shape.Wires)
        with _transaction(translate("Freeform", "Sweep")):
            features.make_sweep(path, profile, solid=closed, doc=_doc())


class Freeform_Extrude(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Extrude",
            QT_TRANSLATE_NOOP("Freeform_Extrude", "Extrude"),
            QT_TRANSLATE_NOOP(
                "Freeform_Extrude",
                "Extrudes the selected strokes along the normal of the drawing plane; "
                "closed strokes give solids",
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        curves = _selected_curves()
        length = _ask_double(
            translate("Freeform", "Extrude"),
            translate("Freeform", "Extrusion length:"),
            _params().GetFloat("ExtrudeLength", 10.0),
            minimum=-1e6,
        )
        if length is None or abs(length) < 1e-9:
            return
        _params().SetFloat("ExtrudeLength", length)
        plane = workplane.get_work_plane()
        with _transaction(translate("Freeform", "Extrude")):
            for curve in curves:
                wires = curve.Shape.Wires
                closed = bool(wires) and all(w.isClosed() for w in wires)
                features.make_extrude(curve, plane.normal, length, solid=closed, doc=_doc())


class Freeform_Shell(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Shell",
            QT_TRANSLATE_NOOP("Freeform_Shell", "Thicken surface"),
            QT_TRANSLATE_NOOP(
                "Freeform_Shell",
                "Gives the selected ribbons, surfaces and patches a thickness (0 keeps them thin)",
            ),
        )

    @staticmethod
    def _targets():
        return [
            o
            for o in _selection()
            if features.is_freeform_object(o, "Ribbon")
            or features.is_freeform_object(o, "Surface")
            or features.is_freeform_object(o, "Patch")
        ]

    def IsActive(self):
        return bool(self._targets())

    def Activated(self):
        targets = self._targets()
        current = max(float(o.Thickness) for o in targets) or _params().GetFloat(
            "ShellThickness", 2.0
        )
        thickness = _ask_double(
            translate("Freeform", "Thicken surface"),
            translate("Freeform", "Thickness (0 keeps a thin surface):"),
            current,
        )
        if thickness is None:
            return
        _params().SetFloat("ShellThickness", thickness)
        with _transaction(translate("Freeform", "Thicken surface")):
            for obj in targets:
                obj.Thickness = thickness


class Freeform_ToSketch(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_ToSketch",
            QT_TRANSLATE_NOOP("Freeform_ToSketch", "To sketch"),
            QT_TRANSLATE_NOOP(
                "Freeform_ToSketch",
                "Converts the selected planar strokes into Sketcher sketches to constrain them "
                "or use them in Part Design",
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        with _transaction(translate("Freeform", "To sketch")):
            for curve in _selected_curves():
                try:
                    features.make_sketch(curve, doc=_doc())
                except ValueError as exc:
                    _err(str(exc))


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
            accel="F, R",
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
            accel="F, M",
            checkable=workplane.get_symmetry_plane().enabled,
        )

    def IsActive(self):
        return True

    def Activated(self, index=0):
        workplane.get_symmetry_plane().set_enabled(bool(index))
        command = ACTIVE_STROKE_COMMAND
        if command is not None and command.panel is not None:
            command.panel.symmetry_check.setChecked(bool(index))
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
        workplane.get_symmetry_plane().set(face.CenterOfMass, face.normalAt(0, 0))
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
        command = ACTIVE_STROKE_COMMAND
        if command is not None and command.panel is not None:
            command.panel.snap_check.setChecked(bool(index))


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
            accel="F, C",
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
# Parametric generators (Grasshopper inspired)
# ---------------------------------------------------------------------------


def _selected_face_target():
    """First selected face as ``(object, subname)``.

    Unlike :func:`_selected_face` this also accepts a whole object that
    carries faces, returning ``(object, None)`` for it.
    """
    obj, sub = _selected_face()
    if obj is not None:
        return obj, sub
    for obj in _selection():
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull() and shape.Faces:
            return obj, None
    return None, None


def _selected_meshable():
    return [o for o in _selection() if hasattr(o, "Mesh") or hasattr(o, "Shape")]


class ExpressionDialog(QtWidgets.QDialog):
    """Asks for x(t), y(t), z(t) and the t range."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("Freeform", "Expression curve"))
        layout = QtWidgets.QFormLayout(self)
        params = _params()
        self.x_edit = QtWidgets.QLineEdit(params.GetString("ExpressionX", "40*cos(t)"))
        self.y_edit = QtWidgets.QLineEdit(params.GetString("ExpressionY", "40*sin(t)"))
        self.z_edit = QtWidgets.QLineEdit(params.GetString("ExpressionZ", "4*t"))
        self.tmin = QtWidgets.QDoubleSpinBox()
        self.tmin.setRange(-1e6, 1e6)
        self.tmin.setDecimals(4)
        self.tmin.setValue(params.GetFloat("ExpressionTMin", 0.0))
        self.tmax = QtWidgets.QDoubleSpinBox()
        self.tmax.setRange(-1e6, 1e6)
        self.tmax.setDecimals(4)
        self.tmax.setValue(params.GetFloat("ExpressionTMax", 4 * math.pi))
        self.samples = QtWidgets.QSpinBox()
        self.samples.setRange(2, 10000)
        self.samples.setValue(params.GetInt("ExpressionSamples", 100))
        layout.addRow("x(t)", self.x_edit)
        layout.addRow("y(t)", self.y_edit)
        layout.addRow("z(t)", self.z_edit)
        layout.addRow(translate("Freeform", "t from"), self.tmin)
        layout.addRow(translate("Freeform", "t to"), self.tmax)
        layout.addRow(translate("Freeform", "Samples"), self.samples)
        hint = QtWidgets.QLabel(
            translate("Freeform", "Functions: sin cos tan exp log sqrt abs pow atan2 … and pi, e")
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        params = _params()
        params.SetString("ExpressionX", self.x_edit.text())
        params.SetString("ExpressionY", self.y_edit.text())
        params.SetString("ExpressionZ", self.z_edit.text())
        params.SetFloat("ExpressionTMin", self.tmin.value())
        params.SetFloat("ExpressionTMax", self.tmax.value())
        params.SetInt("ExpressionSamples", self.samples.value())
        return (
            self.x_edit.text(),
            self.y_edit.text(),
            self.z_edit.text(),
            self.tmin.value(),
            self.tmax.value(),
            self.samples.value(),
        )


class Freeform_Expression(_Command):
    def GetResources(self):
        return _resources(
            "Freeform_Expression",
            QT_TRANSLATE_NOOP("Freeform_Expression", "Expression curve"),
            QT_TRANSLATE_NOOP(
                "Freeform_Expression",
                "Creates a stroke from x(t), y(t), z(t) expressions; edit them later in the "
                "property editor",
            ),
        )

    def Activated(self):
        dialog = ExpressionDialog(FreeCADGui.getMainWindow())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        x, y, z, t_min, t_max, samples = dialog.values()
        with _transaction(translate("Freeform", "Expression curve")):
            generators.make_expression(x, y, z, t_min, t_max, samples, doc=_doc())


class Freeform_Offset(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Offset",
            QT_TRANSLATE_NOOP("Freeform_Offset", "Offset curve"),
            QT_TRANSLATE_NOOP(
                "Freeform_Offset", "Creates parallel copies of the selected planar curves"
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        distance = _ask_double(
            translate("Freeform", "Offset curve"),
            translate("Freeform", "Distance (negative flips the side):"),
            _params().GetFloat("OffsetDistance", 5.0),
            minimum=-1e6,
        )
        if distance is None:
            return
        _params().SetFloat("OffsetDistance", distance)
        with _transaction(translate("Freeform", "Offset curve")):
            for curve in _selected_curves():
                generators.make_offset(curve, distance, doc=_doc())


class Freeform_Blend(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Blend",
            QT_TRANSLATE_NOOP("Freeform_Blend", "Blend curves"),
            QT_TRANSLATE_NOOP(
                "Freeform_Blend",
                "Bridges the nearest ends of two selected curves with a smooth curve",
            ),
        )

    def IsActive(self):
        return len(_selected_curves()) == 2

    def Activated(self):
        first, second = _selected_curves()
        with _transaction(translate("Freeform", "Blend curves")):
            generators.make_blend(first, second, doc=_doc())


class Freeform_Divide(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Divide",
            QT_TRANSLATE_NOOP("Freeform_Divide", "Divide curve"),
            QT_TRANSLATE_NOOP(
                "Freeform_Divide",
                "Places evenly spaced points and frames along the selected curves",
            ),
        )

    def IsActive(self):
        return bool(_selected_curves())

    def Activated(self):
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Divide curve"),
            translate("Freeform", "Number of points:"),
            _params().GetInt("DivideCount", 10),
            2,
            10000,
        )
        if not ok:
            return
        _params().SetInt("DivideCount", count)
        with _transaction(translate("Freeform", "Divide curve")):
            for curve in _selected_curves():
                size = max(0.5, float(curve.Shape.Length) / count * 0.3)
                generators.make_divide(curve, count=count, frame_size=size, doc=_doc())


class Freeform_Contours(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Contours",
            QT_TRANSLATE_NOOP("Freeform_Contours", "Contours"),
            QT_TRANSLATE_NOOP(
                "Freeform_Contours",
                "Slices the selected shapes into section curves along the normal of the drawing plane",
            ),
        )

    def Activated(self):
        shapes = [o for o in _selection() if hasattr(o, "Shape") and not o.Shape.isNull()]
        if not shapes:
            return
        spacing = _ask_double(
            translate("Freeform", "Contours"),
            translate("Freeform", "Spacing between sections:"),
            _params().GetFloat("ContourSpacing", 5.0),
            minimum=0.001,
        )
        if spacing is None:
            return
        _params().SetFloat("ContourSpacing", spacing)
        normal = workplane.get_work_plane().normal
        with _transaction(translate("Freeform", "Contours")):
            for obj in shapes:
                generators.make_contours(obj, normal, spacing, doc=_doc())


class Freeform_CurveArray(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_CurveArray",
            QT_TRANSLATE_NOOP("Freeform_CurveArray", "Array along curve"),
            QT_TRANSLATE_NOOP(
                "Freeform_CurveArray",
                "Copies the first selected object along the second selected curve; add "
                "attractors in the property editor to vary the size",
            ),
        )

    def IsActive(self):
        return len(_selection()) == 2 and bool(_selected_curves())

    def Activated(self):
        objects = _selection()
        path = _selected_curves()[-1]
        base = objects[0] if objects[0] is not path else objects[1]
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Array along curve"),
            translate("Freeform", "Number of copies:"),
            _params().GetInt("ArrayCount", 10),
            1,
            10000,
        )
        if not ok:
            return
        _params().SetInt("ArrayCount", count)
        with _transaction(translate("Freeform", "Array along curve")):
            generators.make_curve_array(base, path, count=count, doc=_doc())


class Freeform_SurfaceGrid(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_SurfaceGrid",
            QT_TRANSLATE_NOOP("Freeform_SurfaceGrid", "Surface panels"),
            QT_TRANSLATE_NOOP(
                "Freeform_SurfaceGrid",
                "Panels the selected face with a UV grid: panels, points, frames or copies of "
                "a second selected object; attractors vary the panel size",
            ),
        )

    def IsActive(self):
        return _selected_face_target()[0] is not None

    def Activated(self):
        obj, sub = _selected_face_target()
        item = None
        for other in _selection():
            if other is not obj and hasattr(other, "Shape"):
                item = other
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Surface panels"),
            translate("Freeform", "Divisions in each direction:"),
            _params().GetInt("GridCount", 8),
            1,
            500,
        )
        if not ok:
            return
        _params().SetInt("GridCount", count)
        with _transaction(translate("Freeform", "Surface panels")):
            generators.make_surface_grid(
                obj,
                sub,
                count,
                count,
                output="Copies" if item is not None else "Panels",
                item=item,
                doc=_doc(),
            )


class Freeform_Voronoi(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Voronoi",
            QT_TRANSLATE_NOOP("Freeform_Voronoi", "Voronoi"),
            QT_TRANSLATE_NOOP(
                "Freeform_Voronoi",
                "Covers the selected planar face with Voronoi cells; edit count, seed and inset "
                "in the property editor",
            ),
        )

    def IsActive(self):
        return _selected_face_target()[0] is not None

    def Activated(self):
        obj, sub = _selected_face_target()
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Voronoi"),
            translate("Freeform", "Number of cells:"),
            _params().GetInt("VoronoiCount", 20),
            1,
            5000,
        )
        if not ok:
            return
        _params().SetInt("VoronoiCount", count)
        with _transaction(translate("Freeform", "Voronoi")):
            generators.make_voronoi(obj, sub, count=count, doc=_doc())


class Freeform_Deform(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Deform",
            QT_TRANSLATE_NOOP("Freeform_Deform", "Deform"),
            QT_TRANSLATE_NOOP(
                "Freeform_Deform",
                "Twists, tapers, bends, stretches, waves, noises or flows the selected mesh "
                "(or shape) along a curve",
            ),
        )

    def IsActive(self):
        return bool(_selected_meshable())

    def Activated(self):
        modes = list(generators.parametric.DEFORM_MODES)
        mode, ok = QtWidgets.QInputDialog.getItem(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Deform"),
            translate("Freeform", "Deformation:"),
            modes,
            (
                modes.index(_params().GetString("DeformMode", "Twist"))
                if _params().GetString("DeformMode", "Twist") in modes
                else 0
            ),
            False,
        )
        if not ok:
            return
        _params().SetString("DeformMode", mode)
        targets = _selected_meshable()
        path = None
        if mode == "Flow":
            curves = _selected_curves()
            if not curves:
                _err(translate("Freeform", "Flow needs a curve in the selection"))
                return
            path = curves[-1]
            targets = [t for t in targets if t is not path]
        defaults = {
            "Twist": 2.0,
            "Taper": 0.5,
            "Bend": 45.0,
            "Stretch": 1.5,
            "Wave": 2.0,
            "Noise": 1.0,
        }
        amount = defaults.get(mode, 1.0)
        if mode != "Flow":
            amount = _ask_double(
                translate("Freeform", "Deform"),
                translate("Freeform", "Amount:"),
                amount,
                minimum=-1e6,
            )
            if amount is None:
                return
        with _transaction(translate("Freeform", "Deform")):
            for target in targets:
                generators.make_deform(target, mode, amount, path=path, doc=_doc())


class Freeform_Relax(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Relax",
            QT_TRANSLATE_NOOP("Freeform_Relax", "Relax"),
            QT_TRANSLATE_NOOP(
                "Freeform_Relax",
                "Relaxes the selected mesh towards a minimal surface, keeping its boundary; "
                "add anchors in the property editor for tent poles",
            ),
        )

    def IsActive(self):
        return bool(_selected_meshable())

    def Activated(self):
        with _transaction(translate("Freeform", "Relax")):
            for target in _selected_meshable():
                generators.make_relax(target, doc=_doc())


class Freeform_Populate(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Populate",
            QT_TRANSLATE_NOOP("Freeform_Populate", "Populate"),
            QT_TRANSLATE_NOOP(
                "Freeform_Populate",
                "Scatters points over the selected planar face; set Relax for even spacing, "
                "or attractors and an image to vary the density",
            ),
        )

    def IsActive(self):
        return _selected_face_target()[0] is not None

    def Activated(self):
        obj, sub = _selected_face_target()
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Populate"),
            translate("Freeform", "Number of points:"),
            _params().GetInt("PopulateCount", 50),
            1,
            100000,
        )
        if not ok:
            return
        _params().SetInt("PopulateCount", count)
        with _transaction(translate("Freeform", "Populate")):
            generators.make_populate(obj, sub, count=count, doc=_doc())


class Freeform_Lattice(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Lattice",
            QT_TRANSLATE_NOOP("Freeform_Lattice", "Lattice"),
            QT_TRANSLATE_NOOP(
                "Freeform_Lattice",
                "Turns the edges of the selected mesh or shape into struts with nodes",
            ),
        )

    def IsActive(self):
        return bool(_selected_meshable())

    def Activated(self):
        radius = _ask_double(
            translate("Freeform", "Lattice"),
            translate("Freeform", "Strut radius (0 gives a wireframe):"),
            _params().GetFloat("LatticeRadius", 0.6),
        )
        if radius is None:
            return
        _params().SetFloat("LatticeRadius", radius)
        with _transaction(translate("Freeform", "Lattice")):
            for target in _selected_meshable():
                generators.make_lattice(target, radius, doc=_doc())


class Freeform_Tween(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Tween",
            QT_TRANSLATE_NOOP("Freeform_Tween", "Tween curves"),
            QT_TRANSLATE_NOOP(
                "Freeform_Tween",
                "Creates intermediate curves morphing the first selected curve into the second",
            ),
        )

    def IsActive(self):
        return len(_selected_curves()) == 2

    def Activated(self):
        first, second = _selected_curves()
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Tween curves"),
            translate("Freeform", "Number of intermediate curves:"),
            _params().GetInt("TweenCount", 5),
            1,
            1000,
        )
        if not ok:
            return
        _params().SetInt("TweenCount", count)
        with _transaction(translate("Freeform", "Tween curves")):
            generators.make_tween(first, second, count, doc=_doc())


class LSystemDialog(QtWidgets.QDialog):
    """Asks for the axiom, the rewriting rules and the turtle settings."""

    PRESETS = {
        "Bush": ("F", ["F=FF-[-F+F+F]+[+F-F-F]"], 25.0, 4),
        "Tree": ("F", ["F=F[+F]F[-F][F]"], 22.0, 4),
        "Seaweed": ("F", ["F=F[+F]F[-F]F"], 25.0, 4),
        "Spiral": ("F", ["F=F[+F]F"], 30.0, 5),
        "Koch": ("F", ["F=F+F-F-F+F"], 90.0, 3),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("Freeform", "L-system"))
        layout = QtWidgets.QFormLayout(self)
        self.preset = QtWidgets.QComboBox()
        self.preset.addItems(list(self.PRESETS))
        self.preset.currentTextChanged.connect(self.load_preset)
        layout.addRow(translate("Freeform", "Preset"), self.preset)
        self.axiom = QtWidgets.QLineEdit("F")
        self.rules = QtWidgets.QPlainTextEdit("F=FF-[-F+F+F]+[+F-F-F]")
        self.rules.setMaximumHeight(70)
        self.generations = QtWidgets.QSpinBox()
        self.generations.setRange(0, 12)
        self.generations.setValue(4)
        self.step = QtWidgets.QDoubleSpinBox()
        self.step.setRange(0.001, 1e6)
        self.step.setValue(_params().GetFloat("LSystemStep", 10.0))
        self.angle = QtWidgets.QDoubleSpinBox()
        self.angle.setRange(-360, 360)
        self.angle.setValue(25.0)
        layout.addRow(translate("Freeform", "Axiom"), self.axiom)
        layout.addRow(translate("Freeform", "Rules"), self.rules)
        layout.addRow(translate("Freeform", "Generations"), self.generations)
        layout.addRow(translate("Freeform", "Step"), self.step)
        layout.addRow(translate("Freeform", "Angle"), self.angle)
        hint = QtWidgets.QLabel(
            translate(
                "Freeform",
                "F draws, f moves, + - turn, & ^ pitch, \\ / roll, | turns back, "
                "[ ] branch. One rule per line.",
            )
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_preset(self, name):
        axiom, rules, angle, generations = self.PRESETS[name]
        self.axiom.setText(axiom)
        self.rules.setPlainText("\n".join(rules))
        self.angle.setValue(angle)
        self.generations.setValue(generations)

    def values(self):
        _params().SetFloat("LSystemStep", self.step.value())
        rules = [line.strip() for line in self.rules.toPlainText().splitlines() if line.strip()]
        return (
            self.axiom.text(),
            rules,
            self.generations.value(),
            self.step.value(),
            self.angle.value(),
        )


class Freeform_LSystem(_Command):
    def GetResources(self):
        return _resources(
            "Freeform_LSystem",
            QT_TRANSLATE_NOOP("Freeform_LSystem", "L-system"),
            QT_TRANSLATE_NOOP(
                "Freeform_LSystem",
                "Grows a branching structure from rewriting rules; every setting stays editable",
            ),
        )

    def Activated(self):
        dialog = LSystemDialog(FreeCADGui.getMainWindow())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        axiom, rules, generations, step, angle = dialog.values()
        with _transaction(translate("Freeform", "L-system")):
            generators.make_lsystem(axiom, rules, generations, step, angle, doc=_doc())


class Freeform_BoxMorph(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_BoxMorph",
            QT_TRANSLATE_NOOP("Freeform_BoxMorph", "Morph onto surface"),
            QT_TRANSLATE_NOOP(
                "Freeform_BoxMorph",
                "Morphs copies of the first selected object into the grid cells of the "
                "selected face",
            ),
        )

    def IsActive(self):
        obj, _ = _selected_face_target()
        return obj is not None and len(_selection()) >= 2

    def Activated(self):
        target, sub = _selected_face_target()
        base = None
        for other in _selection():
            if other is not target:
                base = other
                break
        if base is None:
            _err(translate("Freeform", "Select the object to morph and the target face"))
            return
        count, ok = QtWidgets.QInputDialog.getInt(
            FreeCADGui.getMainWindow(),
            translate("Freeform", "Morph onto surface"),
            translate("Freeform", "Copies in each direction:"),
            _params().GetInt("MorphCount", 4),
            1,
            200,
        )
        if not ok:
            return
        _params().SetInt("MorphCount", count)
        with _transaction(translate("Freeform", "Morph onto surface")):
            generators.make_box_morph(base, target, sub, count, count, doc=_doc())


class Freeform_Project(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Project",
            QT_TRANSLATE_NOOP("Freeform_Project", "Project onto shape"),
            QT_TRANSLATE_NOOP(
                "Freeform_Project",
                "Projects the selected curves onto the last selected shape, along the drawing "
                "plane normal",
            ),
        )

    def IsActive(self):
        return len(_selection()) >= 2 and bool(_selected_curves())

    def Activated(self):
        objects = [o for o in _selection() if hasattr(o, "Shape")]
        target = objects[-1]
        curves = [o for o in _selected_curves() if o is not target]
        if not curves:
            _err(translate("Freeform", "Select the curves first and the target shape last"))
            return
        normal = workplane.get_work_plane().normal * -1.0
        with _transaction(translate("Freeform", "Project onto shape")):
            for curve in curves:
                generators.make_project(curve, target, "Along direction", normal, doc=_doc())


class Freeform_Sweep2(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Sweep2",
            QT_TRANSLATE_NOOP("Freeform_Sweep2", "Two rail sweep"),
            QT_TRANSLATE_NOOP(
                "Freeform_Sweep2",
                "Sweeps the first selected profile along the second curve, guided by the third",
            ),
        )

    def IsActive(self):
        return len(_selected_curves()) == 3

    def Activated(self):
        profile, path, rail = _selected_curves()
        with _transaction(translate("Freeform", "Two rail sweep")):
            generators.make_sweep2(profile, path, rail, doc=_doc())


class Freeform_Frame(_SelectionCommand):
    def GetResources(self):
        return _resources(
            "Freeform_Frame",
            QT_TRANSLATE_NOOP("Freeform_Frame", "Frame panels"),
            QT_TRANSLATE_NOOP(
                "Freeform_Frame",
                "Turns every face of the selected mesh or shape into a panel with a border "
                "and an opening",
            ),
        )

    def IsActive(self):
        return bool(_selected_meshable())

    def Activated(self):
        width = _ask_double(
            translate("Freeform", "Frame panels"),
            translate("Freeform", "Border width, as a fraction of each face:"),
            _params().GetFloat("FrameWidth", 0.2),
            minimum=0.01,
            maximum=0.99,
        )
        if width is None:
            return
        _params().SetFloat("FrameWidth", width)
        with _transaction(translate("Freeform", "Frame panels")):
            for target in _selected_meshable():
                generators.make_frame(target, width, doc=_doc())


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register():
    """Register every command with FreeCADGui (idempotent)."""
    registered = set(FreeCADGui.listCommands())
    for name in ALL_COMMANDS:
        if not name.startswith("Freeform_"):
            continue
        if name in registered:
            continue
        cls = globals()[name]
        FreeCADGui.addCommand(name, cls())
