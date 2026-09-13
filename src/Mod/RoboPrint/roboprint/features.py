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

"""The parametric objects of the RoboPrint workbench.

Two objects carry the whole pipeline, and both keep their result in
properties so a later step does not have to recompute the earlier one:

``Slices``
    a part plus a slicing mode gives the layer contours
``Toolpath``
    slices plus the process values give the oriented print points

Change the layer height and only the objects downstream of it rebuild,
which is FreeCAD's dependency graph doing the same job a node editor does
in the commercial tools.
"""

import FreeCAD
import Part
from FreeCAD import Vector

from . import analysis, postprocessors, slicing, toolpath

translate = FreeCAD.Qt.translate
QT_TRANSLATE_NOOP = FreeCAD.Qt.QT_TRANSLATE_NOOP

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/RoboPrint"


def preference(name, default):
    """The stored preference for *name*, falling back to *default*.

    The type of *default* picks the accessor, so the preferences page and the
    property defaults stay in step without a separate table.
    """
    params = FreeCAD.ParamGet(PARAM_PATH)
    if isinstance(default, bool):
        return params.GetBool(name, default)
    if isinstance(default, int):
        return params.GetInt(name, default)
    if isinstance(default, float):
        return params.GetFloat(name, default)
    return params.GetString(name, default)


__all__ = [
    "Slices",
    "Toolpath",
    "make_slices",
    "make_toolpath",
    "contours_of",
    "paths_of",
    "is_roboprint_object",
    "export_toolpath",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _document(doc):
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument()
    return doc


def _hide(objects):
    if not FreeCAD.GuiUp:
        return
    for obj in objects:
        if obj is not None and getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.hide()


def is_roboprint_object(obj, kind=None):
    """True when ``obj`` is a RoboPrint feature, optionally of type ``kind``."""
    proxy = getattr(obj, "Proxy", None)
    type_name = getattr(proxy, "Type", "")
    if not type_name.startswith("RoboPrint::"):
        return False
    return kind is None or type_name == "RoboPrint::" + kind


class _FeatureBase:
    """Serialization and a type marker, shared by both objects."""

    Type = "RoboPrint::Base"

    def __init__(self, obj):
        obj.Proxy = self
        self.Object = obj

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        self.Object = obj
        self.migrate(obj)

    def migrate(self, obj):
        """Add properties introduced after the file was saved."""

    def _add(self, obj, ptype, name, group, doc, default=None, read_only=False):
        if not hasattr(obj, name):
            obj.addProperty(
                ptype, name, group, QT_TRANSLATE_NOOP("App::Property", doc), locked=True
            )
            if default is not None:
                setattr(obj, name, default)
        if read_only:
            obj.setEditorMode(name, 1)


# ---------------------------------------------------------------------------
# Slices
# ---------------------------------------------------------------------------


class Slices(_FeatureBase):
    """The layer contours of a part, in one of the slicing modes."""

    Type = "RoboPrint::Slices"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Slices", "The solid or mesh to slice")
        add(obj, "App::PropertyEnumeration", "Mode", "Slices", "How the layers are laid out")
        if not obj.Mode:
            obj.Mode = list(slicing.SLICING_MODES)
            obj.Mode = "Planar"
        add(
            obj,
            "App::PropertyLength",
            "LayerHeight",
            "Slices",
            "Distance between layers",
            preference("LayerHeight", 4.0),
        )
        add(
            obj,
            "App::PropertyVector",
            "Axis",
            "Slices",
            "Build direction. Tilting it away from Z is how a part is printed at an angle",
            Vector(0, 0, 1),
        )
        add(
            obj,
            "App::PropertyVector",
            "Origin",
            "Slices",
            "Origin of the slicing field",
            Vector(0, 0, 0),
        )
        add(
            obj,
            "App::PropertyAngle",
            "ConeAngle",
            "Slices",
            "Conical mode: half angle of the cones, which is the overhang the "
            "nozzle can reach without support",
            preference("ConeAngle", 30.0),
        )
        add(
            obj,
            "App::PropertyLink",
            "Surface",
            "Slices",
            "Conformal mode: the substrate the layers follow, a face or a scan mesh",
        )
        add(
            obj,
            "App::PropertyLength",
            "FirstLayer",
            "Slices",
            "Height of the first layer above the bottom of the part; 0 uses half a layer",
            0.0,
        )
        add(obj, "App::PropertyLength", "Tolerance", "Slices", "Tessellation deviation", 0.1)
        add(obj, "App::PropertyEnumeration", "Seam", "Slices", "Where each closed contour starts")
        if not obj.Seam:
            obj.Seam = ["Aligned", "Nearest", "Scattered"]
            obj.Seam = "Aligned"
        add(
            obj,
            "App::PropertyInteger",
            "LayerCount",
            "Output",
            "Number of layers (read only)",
            0,
            True,
        )
        add(
            obj,
            "App::PropertyInteger",
            "ContourCount",
            "Output",
            "Number of contours (read only)",
            0,
            True,
        )
        add(
            obj,
            "App::PropertyVectorList",
            "Points",
            "Data",
            "Contour points (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyIntegerList",
            "Starts",
            "Data",
            "Index of each contour (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyBoolList",
            "Closed",
            "Data",
            "Closed flag per contour (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyIntegerList",
            "Layers",
            "Data",
            "Layer index per contour (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Values",
            "Data",
            "Field value per layer (read only)",
            None,
            True,
        )
        for name in ("Points", "Starts", "Closed", "Layers", "Values"):
            obj.setEditorMode(name, 2)

    def execute(self, obj):
        if obj.Base is None:
            raise ValueError(translate("RoboPrint", "Nothing to slice"))
        first = float(obj.FirstLayer)
        layers, values = slicing.slice_mesh(
            obj.Base,
            mode=obj.Mode or "Planar",
            layer_height=float(obj.LayerHeight),
            axis=Vector(obj.Axis),
            origin=Vector(obj.Origin),
            angle=float(obj.ConeAngle),
            surface=obj.Surface,
            tolerance=float(obj.Tolerance),
            first_layer=first if first > 0 else None,
        )
        slicing.align_seams(layers, obj.Seam or "Aligned", Vector(obj.Axis))
        points = []
        starts = []
        closed = []
        layer_index = []
        wires = []
        for index, layer in enumerate(layers):
            for contour in layer:
                if len(contour.points) < 2:
                    continue
                starts.append(len(points))
                closed.append(contour.closed)
                layer_index.append(index)
                points.extend(contour.points)
                loop = contour.points + [contour.points[0]] if contour.closed else contour.points
                try:
                    wires.append(Part.makePolygon(loop))
                except Part.OCCError:
                    continue
        obj.Points = points
        obj.Starts = starts
        obj.Closed = closed
        obj.Layers = layer_index
        obj.Values = list(values)
        obj.LayerCount = len(layers)
        obj.ContourCount = len(starts)
        if not wires:
            raise ValueError(translate("RoboPrint", "Slicing produced no contours"))
        obj.Shape = Part.makeCompound(wires)


def contours_of(obj):
    """Rebuild ``(layers, values)`` from a stored :class:`Slices` object."""
    points = list(obj.Points)
    starts = list(obj.Starts)
    closed = list(obj.Closed)
    layer_index = list(obj.Layers)
    values = list(obj.Values)
    layers = [[] for _ in range(max(layer_index) + 1)] if layer_index else []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(points)
        contour = slicing.Contour(points[start:end], closed[position])
        layers[layer_index[position]].append(contour)
    return layers, values


# ---------------------------------------------------------------------------
# Toolpath
# ---------------------------------------------------------------------------


class Toolpath(_FeatureBase):
    """The oriented print points generated from a set of slices."""

    Type = "RoboPrint::Toolpath"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Toolpath", "The slices to build the path from")
        add(
            obj,
            "App::PropertyLength",
            "BeadWidth",
            "Process",
            "Width of one deposited bead",
            preference("BeadWidth", 8.0),
        )
        add(
            obj,
            "App::PropertyLength",
            "BeadHeight",
            "Process",
            "Height of one deposited bead; normally the layer height",
            4.0,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Perimeters",
            "Toolpath",
            "Beads around each contour",
            (preference("Perimeters", 2), 1, 100, 1),
        )
        add(
            obj,
            "App::PropertyEnumeration",
            "InfillPattern",
            "Toolpath",
            "Pattern filling the inside",
        )
        if not obj.InfillPattern:
            obj.InfillPattern = list(toolpath.INFILL_PATTERNS)
            obj.InfillPattern = "None"
        add(
            obj,
            "App::PropertyLength",
            "InfillSpacing",
            "Toolpath",
            "Distance between infill beads",
            preference("InfillSpacing", 0.0),
        )
        add(
            obj,
            "App::PropertyAngle",
            "InfillAngle",
            "Toolpath",
            "Direction of the infill on the first layer",
            45.0,
        )
        add(
            obj,
            "App::PropertyLength",
            "PointSpacing",
            "Toolpath",
            "Resample the path to this spacing; 0 keeps it as sliced",
            0.0,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Smoothing",
            "Toolpath",
            "Smoothing passes over each contour",
            (0, 0, 50, 1),
        )
        add(
            obj,
            "App::PropertyBool",
            "Spiral",
            "Toolpath",
            "Join the perimeters into one continuous helix",
            False,
        )
        add(obj, "App::PropertyEnumeration", "Orientation", "Orientation", "How the tool is held")
        if not obj.Orientation:
            obj.Orientation = list(toolpath.ORIENTATION_MODES)
            obj.Orientation = "LayerNormal"
        add(
            obj,
            "App::PropertyAngle",
            "LeadAngle",
            "Orientation",
            "Tilted mode: lean along the direction of travel",
            0.0,
        )
        add(
            obj,
            "App::PropertyAngle",
            "MaxTilt",
            "Orientation",
            "Never lean further than this from the build axis",
            preference("MaxTilt", 45.0),
        )
        add(
            obj,
            "App::PropertyAngle",
            "MaxOrientationChange",
            "Orientation",
            "Limit on how far the tool axis may turn between two points; 0 disables the smoothing",
            10.0,
        )
        add(
            obj,
            "App::PropertySpeed",
            "Speed",
            "Process",
            "Printing speed",
            preference("Speed", 3000.0),
        )
        add(
            obj,
            "App::PropertySpeed",
            "TravelSpeed",
            "Process",
            "Speed of the moves between paths",
            preference("TravelSpeed", 9000.0),
        )
        add(
            obj,
            "App::PropertyFloat",
            "Density",
            "Process",
            "Material density in grams per cubic centimetre",
            preference("Density", 1.24),
        )
        add(
            obj,
            "App::PropertyLength",
            "TravelClearance",
            "Process",
            "Lift the nozzle this far along the tool axis when crossing "
            "between paths; 0 moves straight across",
            preference("TravelClearance", 0.0),
        )
        add(
            obj,
            "App::PropertyBool",
            "CornerCompensation",
            "Modifiers",
            "Slow down and thicken the bead through sharp corners",
            False,
        )
        add(
            obj,
            "App::PropertyAngle",
            "CornerThreshold",
            "Modifiers",
            "A turn sharper than this counts as a corner",
            60.0,
        )
        add(
            obj,
            "App::PropertyBool",
            "ReinforceOverhangs",
            "Modifiers",
            "Add material where the path hangs over itself",
            False,
        )
        add(
            obj,
            "App::PropertyAngle",
            "OverhangLimit",
            "Modifiers",
            "Overhang beyond which material is added",
            preference("OverhangLimit", 45.0),
        )
        for name, group, doc in (
            ("PathCount", "Output", "Number of separate paths (read only)"),
            ("PointCount", "Output", "Number of print points (read only)"),
        ):
            add(obj, "App::PropertyInteger", name, group, doc, 0, True)
        for name, doc in (
            ("PathLength", "Total extruding length (read only)"),
            ("Volume", "Deposited volume in cubic millimetres (read only)"),
            ("Mass", "Deposited mass in grams (read only)"),
            ("Hours", "Estimated print time in hours (read only)"),
            ("MaxOverhang", "Largest overhang in degrees (read only)"),
            ("MaxTiltUsed", "Largest tool tilt in degrees (read only)"),
        ):
            add(obj, "App::PropertyFloat", name, "Output", doc, 0.0, True)
        add(
            obj,
            "App::PropertyVectorList",
            "Points",
            "Data",
            "Print point positions (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyVectorList",
            "Axes",
            "Data",
            "Tool axis per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyIntegerList",
            "Starts",
            "Data",
            "Index of each path (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyBoolList",
            "Closed",
            "Data",
            "Closed flag per path (read only)",
            None,
            True,
        )
        add(
            obj, "App::PropertyStringList", "Roles", "Data", "Role per path (read only)", None, True
        )
        add(
            obj,
            "App::PropertyIntegerList",
            "Layers",
            "Data",
            "Layer index per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Widths",
            "Data",
            "Bead width per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Heights",
            "Data",
            "Bead height per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Speeds",
            "Data",
            "Speed per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Extrusions",
            "Data",
            "Flow multiplier per point (read only)",
            None,
            True,
        )
        add(
            obj,
            "App::PropertyFloatList",
            "Overhangs",
            "Data",
            "Overhang per point (read only)",
            None,
            True,
        )
        for name in (
            "Points",
            "Axes",
            "Starts",
            "Closed",
            "Roles",
            "Layers",
            "Widths",
            "Heights",
            "Speeds",
            "Extrusions",
            "Overhangs",
        ):
            obj.setEditorMode(name, 2)

    def field_of(self, source):
        """The slicing field of the source object, for the tool orientation."""
        try:
            return slicing.field_for_mode(
                source.Mode or "Planar",
                Vector(source.Axis),
                Vector(source.Origin),
                float(source.ConeAngle),
                source.Surface,
            )
        except Exception:  # pylint: disable=broad-except
            return None

    def execute(self, obj):
        source = obj.Base
        if source is None or not is_roboprint_object(source, "Slices"):
            raise ValueError(translate("RoboPrint", "The toolpath needs a Slices object"))
        layers, values = contours_of(source)
        if not layers:
            raise ValueError(translate("RoboPrint", "The slices are empty"))
        axis = Vector(source.Axis)
        field = self.field_of(source)
        paths = toolpath.generate_toolpath(
            layers,
            values,
            bead_width=float(obj.BeadWidth),
            layer_height=float(obj.BeadHeight),
            perimeters=int(obj.Perimeters),
            infill_pattern=obj.InfillPattern or "None",
            infill_spacing=float(obj.InfillSpacing),
            infill_angle=float(obj.InfillAngle),
            point_spacing=float(obj.PointSpacing),
            smoothing=int(obj.Smoothing),
            orientation=obj.Orientation or "LayerNormal",
            field=field,
            axis=axis,
            lead_angle=float(obj.LeadAngle),
            max_tilt=float(obj.MaxTilt),
            speed=float(obj.Speed),
            spiral=bool(obj.Spiral),
        )
        if not paths:
            raise ValueError(translate("RoboPrint", "The toolpath is empty"))
        analysis.compute_overhangs(paths, float(obj.BeadHeight), axis)
        if obj.CornerCompensation:
            toolpath.corner_compensation(paths, float(obj.CornerThreshold))
        if obj.ReinforceOverhangs:
            toolpath.reinforce_overhangs(paths, float(obj.OverhangLimit))
        if float(obj.MaxOrientationChange) > 0:
            toolpath.smooth_orientations(paths, float(obj.MaxOrientationChange))
        # Last, so the modifiers above only ever see printing points.
        paths = toolpath.add_travel_moves(paths, float(obj.TravelClearance), float(obj.TravelSpeed))
        self._store(obj, paths)
        estimate = analysis.estimate(
            paths, float(obj.Speed), float(obj.TravelSpeed), float(obj.Density)
        )
        tilt = analysis.tilt_report(paths, axis, float(obj.MaxTilt))
        obj.PathCount = len(paths)
        obj.PointCount = sum(len(p.points) for p in paths)
        obj.PathLength = estimate["printing_length"]
        obj.Volume = estimate["volume"]
        obj.Mass = estimate["mass"]
        obj.Hours = estimate["hours"]
        obj.MaxOverhang = max((p.overhang for path in paths for p in path.points), default=0.0)
        obj.MaxTiltUsed = tilt["max"]
        wires = []
        for path in paths:
            positions = path.positions()
            if len(positions) < 2:
                continue
            loop = positions + [positions[0]] if path.closed else positions
            try:
                wires.append(Part.makePolygon(loop))
            except Part.OCCError:
                continue
        if not wires:
            raise ValueError(translate("RoboPrint", "The toolpath has no drawable paths"))
        obj.Shape = Part.makeCompound(wires)

    @staticmethod
    def _store(obj, paths):
        points, axes, starts, closed, roles = [], [], [], [], []
        layers, widths, heights, speeds, extrusions, overhangs = [], [], [], [], [], []
        for path in paths:
            starts.append(len(points))
            closed.append(path.closed)
            roles.append(path.role)
            for point in path.points:
                points.append(point.position)
                axes.append(point.axis)
                layers.append(point.layer)
                widths.append(point.width)
                heights.append(point.height)
                speeds.append(point.speed)
                extrusions.append(point.extrusion)
                overhangs.append(point.overhang)
        obj.Points = points
        obj.Axes = axes
        obj.Starts = starts
        obj.Closed = closed
        obj.Roles = roles
        obj.Layers = layers
        obj.Widths = widths
        obj.Heights = heights
        obj.Speeds = speeds
        obj.Extrusions = extrusions
        obj.Overhangs = overhangs


def paths_of(obj):
    """Rebuild the list of :class:`toolpath.Path` stored on a Toolpath object."""
    points = list(obj.Points)
    axes = list(obj.Axes)
    starts = list(obj.Starts)
    closed = list(obj.Closed)
    roles = list(obj.Roles)
    layers = list(obj.Layers)
    widths = list(obj.Widths)
    heights = list(obj.Heights)
    speeds = list(obj.Speeds)
    extrusions = list(obj.Extrusions)
    overhangs = list(obj.Overhangs)
    paths = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(points)
        role = roles[position] if position < len(roles) else "perimeter"
        path = toolpath.Path(
            closed=closed[position] if position < len(closed) else False,
            layer=layers[start] if start < len(layers) else 0,
            role=role,
            # The role is what is stored, so it is what says whether the
            # path extrudes; a rebuilt travel must not come back as a bead.
            travel=role == "travel",
        )
        for index in range(start, end):
            point = toolpath.PrintPoint(
                points[index],
                axes[index] if index < len(axes) else Vector(0, 0, 1),
                layers[index] if index < len(layers) else 0,
                widths[index] if index < len(widths) else 0.0,
                heights[index] if index < len(heights) else 0.0,
                speeds[index] if index < len(speeds) else 0.0,
            )
            point.extrusion = extrusions[index] if index < len(extrusions) else 1.0
            point.overhang = overhangs[index] if index < len(overhangs) else 0.0
            point.travel = path.travel
            path.points.append(point)
        paths.append(path)
    return paths


def export_toolpath(obj, flavour="CSV", path=None, **options):
    """Post-process a stored toolpath into a machine program."""
    if not is_roboprint_object(obj, "Toolpath"):
        raise ValueError(translate("RoboPrint", "Select a toolpath to export"))
    return postprocessors.post_process(paths_of(obj), flavour, path, **options)


# ---------------------------------------------------------------------------
# View providers
# ---------------------------------------------------------------------------


class _ViewProviderBase:
    icon = "RoboPrint_Slice"

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/" + self.icon + ".svg"

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        base = getattr(obj, "Base", None)
        return [base] if base is not None else []

    def onDelete(self, vobj, subelements):
        return True

    def updateData(self, obj, prop):
        return None

    def onChanged(self, vobj, prop):
        return None

    def dumps(self):
        return None

    def loads(self, state):
        return None


class ViewProviderSlices(_ViewProviderBase):
    icon = "RoboPrint_Slice"

    def attach(self, vobj):
        super().attach(vobj)
        vobj.LineWidth = 1.0


class ViewProviderToolpath(_ViewProviderBase):
    icon = "RoboPrint_Toolpath"

    def attach(self, vobj):
        super().attach(vobj)
        vobj.LineWidth = 2.0


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_slices(base, mode=None, layer_height=None, name="Slices", doc=None, **options):
    """Slice ``base`` into layers.

    ``mode`` and ``layer_height`` fall back to the preferences page.
    """
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Slices(obj, base)
    obj.Mode = mode if mode else preference("Mode", "Planar")
    if layer_height:
        obj.LayerHeight = layer_height
    for key, value in options.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    if FreeCAD.GuiUp:
        ViewProviderSlices(obj.ViewObject)
        _hide([base])
    return obj


def make_toolpath(base, bead_width=None, bead_height=None, name="Toolpath", doc=None, **options):
    """Build a toolpath from a :class:`Slices` object.

    ``bead_width`` falls back to the preferences page; ``bead_height``
    falls back to the layer height of the slices, which is the height the
    bead has to reach to meet the next layer.
    """
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Toolpath(obj, base)
    if bead_width:
        obj.BeadWidth = bead_width
    if bead_height is None and is_roboprint_object(base, "Slices"):
        bead_height = float(base.LayerHeight)
    obj.BeadHeight = bead_height if bead_height else 4.0
    for key, value in options.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    if FreeCAD.GuiUp:
        ViewProviderToolpath(obj.ViewObject)
        _hide([base])
    return obj
