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

"""Parametric document objects of the Freeform workbench.

All objects are ``Part::FeaturePython`` (or ``Mesh::FeaturePython`` for the
subdivision surface) with a Python proxy, so they can be created and
recomputed headlessly. The ``make_*`` functions are the scripting API; the
GUI commands in :mod:`freeform.commands` are thin wrappers around them.

Objects
-------
Stroke
    A free-form 3D curve through a list of points, optionally smoothed,
    simplified, closed, filled, and given a (tapered) tube thickness.
Ribbon
    A flat or upright band of a given width following a stroke.
Surface
    A loft spanning two or more strokes.
Patch
    A filled surface over a closed loop of stroke / edge boundaries.
SubD
    A Catmull-Clark subdivision surface over a blocky cage (Part shape or
    mesh).
"""

import FreeCAD
import Part
from FreeCAD import Vector

from . import geometry

translate = FreeCAD.Qt.translate
QT_TRANSLATE_NOOP = FreeCAD.Qt.QT_TRANSLATE_NOOP

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Freeform"

__all__ = [
    "Stroke",
    "Ribbon",
    "Surface",
    "Patch",
    "SubD",
    "make_stroke",
    "make_ribbon",
    "make_surface",
    "make_patch",
    "make_subd",
    "make_mirror",
    "make_revolve",
    "make_primitive",
    "build_curve",
    "build_tube",
    "fill_edges",
    "is_freeform_object",
]


# ---------------------------------------------------------------------------
# Shape building helpers (usable without document objects)
# ---------------------------------------------------------------------------


def build_curve(points, closed=False, degree=3, interpolate=True, tolerance=0.01):
    """Return a ``Part.Wire`` through ``points``.

    - two points or ``degree == 1`` give a polyline
    - ``interpolate`` True gives a B-spline passing through every point
    - ``interpolate`` False gives a least squares B-spline of ``degree``
      approximating the points within ``tolerance``
    """
    pts = geometry.remove_duplicates(points)
    if closed and len(pts) > 2 and (pts[0] - pts[-1]).Length < 1e-7:
        pts.pop()
    if len(pts) < 2:
        raise ValueError(translate("Freeform", "A stroke needs at least two distinct points"))
    if len(pts) == 2 or degree <= 1 or (len(pts) == 3 and closed):
        poly = list(pts)
        if closed:
            poly.append(pts[0])
        return Part.makePolygon(poly)
    curve = Part.BSplineCurve()
    if interpolate:
        curve.interpolate(Points=pts, PeriodicFlag=bool(closed))
    else:
        approx = list(pts)
        if closed:
            approx.append(pts[0])
        deg = max(2, min(int(degree), 8))
        curve.approximate(
            Points=approx, DegMin=min(2, deg), DegMax=deg, Tolerance=max(tolerance, 1e-6)
        )
    return Part.Wire(curve.toShape())


def _stations(wire, count):
    """Return ``count`` (point, tangent) pairs evenly spread along ``wire``."""
    count = max(2, int(count))
    total = wire.Length
    stations = []
    edges = wire.OrderedEdges if hasattr(wire, "OrderedEdges") else wire.Edges
    for k in range(count):
        target = total * k / float(count - 1)
        remaining = target
        edge = edges[-1]
        for e in edges:
            if remaining <= e.Length + 1e-9:
                edge = e
                break
            remaining -= e.Length
        remaining = max(0.0, min(remaining, edge.Length))
        param = edge.getParameterByLength(remaining)
        point = edge.valueAt(param)
        tangent = edge.tangentAt(param)
        if edge.Orientation == "Reversed":
            tangent = tangent * -1.0
        stations.append((point, tangent))
    return stations


def build_tube(wire, radius, end_radius=None, sections=6):
    """Sweep a circle (optionally tapering to ``end_radius``) along ``wire``.

    A constant radius uses a single profile. A taper places ``sections``
    circular profiles along the path and builds a multi-section pipe.
    """
    radius = float(radius)
    if end_radius is None:
        end_radius = radius
    end_radius = float(end_radius)
    if radius <= 0 and end_radius <= 0:
        raise ValueError(translate("Freeform", "Tube radius must be positive"))
    tapered = abs(end_radius - radius) > 1e-9
    count = sections if tapered else 1
    stations = _stations(wire, max(2, count))
    if not tapered:
        stations = stations[:1]
    profiles = []
    n = len(stations)
    for i, (point, tangent) in enumerate(stations):
        t = 0.0 if n == 1 else i / float(n - 1)
        r = radius + (end_radius - radius) * t
        r = max(r, 1e-4)
        profiles.append(Part.Wire(Part.makeCircle(r, point, tangent)))
    # corrected Frenet trihedron (isFrenet=False) keeps the tube from twisting
    solid = wire.makePipeShell(profiles, True, False)
    if solid.isNull():
        raise ValueError(translate("Freeform", "Could not sweep the stroke"))
    return solid


def _wire_of(shape):
    """Best effort: return a wire from ``shape`` (wire, edge or face)."""
    if shape.ShapeType == "Wire":
        return shape
    if shape.ShapeType == "Edge":
        return Part.Wire(shape)
    if shape.Wires:
        return shape.Wires[0]
    if shape.Edges:
        return Part.Wire(Part.__sortEdges__(shape.Edges))
    raise ValueError(translate("Freeform", "Object has no curve to use"))


def _link_shape(link):
    if link is None:
        return None
    shape = getattr(link, "Shape", None)
    if shape is None or shape.isNull():
        return None
    return shape


def is_freeform_object(obj, kind=None):
    """True when ``obj`` is a Freeform feature (optionally of type ``kind``)."""
    proxy = getattr(obj, "Proxy", None)
    if proxy is None:
        return False
    type_name = getattr(proxy, "Type", "")
    if not type_name.startswith("Freeform::"):
        return False
    return kind is None or type_name == "Freeform::" + kind


# ---------------------------------------------------------------------------
# Base proxy
# ---------------------------------------------------------------------------


class _FeatureBase:
    """Common proxy behaviour: serialization and a ``Type`` marker."""

    Type = "Freeform::Base"

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
        """Add properties that were introduced after the file was saved."""

    def _add(self, obj, ptype, name, group, doc, default=None):
        if not hasattr(obj, name):
            obj.addProperty(
                ptype, name, group, QT_TRANSLATE_NOOP("App::Property", doc), locked=True
            )
            if default is not None:
                setattr(obj, name, default)


# ---------------------------------------------------------------------------
# Stroke
# ---------------------------------------------------------------------------


class Stroke(_FeatureBase):
    """A free-form curve, optionally with tube thickness."""

    Type = "Freeform::Stroke"

    def __init__(self, obj, points=None):
        super().__init__(obj)
        self.migrate(obj)
        if points:
            obj.Points = [Vector(p) for p in points]

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyVectorList", "Points", "Stroke", "The raw points of the stroke")
        add(obj, "App::PropertyBool", "Closed", "Stroke", "Close the stroke into a loop", False)
        add(
            obj,
            "App::PropertyBool",
            "MakeFace",
            "Stroke",
            "Fill a closed stroke with a face (planar or free-form)",
            False,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Smoothing",
            "Stroke",
            "Number of smoothing passes applied to the points",
            (2, 0, 100, 1),
        )
        add(
            obj,
            "App::PropertyLength",
            "Tolerance",
            "Stroke",
            "Simplification tolerance; 0 keeps every point",
            0.0,
        )
        add(
            obj,
            "App::PropertyBool",
            "Interpolate",
            "Stroke",
            "Pass exactly through the points (true) or approximate them (false)",
            True,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Degree",
            "Stroke",
            "Curve degree; 1 gives a polyline",
            (3, 1, 8, 1),
        )
        add(
            obj,
            "App::PropertyLength",
            "Thickness",
            "Tube",
            "Tube diameter at the start of the stroke; 0 keeps a plain curve",
            0.0,
        )
        # PropertyDistance (not PropertyLength) so the -1 "same as start"
        # sentinel is not clamped to zero
        add(
            obj,
            "App::PropertyDistance",
            "EndThickness",
            "Tube",
            "Tube diameter at the end of the stroke; negative uses Thickness",
            -1.0,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "TubeSections",
            "Tube",
            "Number of profiles used for a tapered tube",
            (6, 2, 64, 1),
        )
        add(
            obj,
            "App::PropertyLength",
            "Length",
            "Stroke",
            "Length of the resulting curve (read only)",
            0.0,
        )
        obj.setEditorMode("Length", 1)

    def conditioned_points(self, obj):
        return geometry.condition_stroke(
            obj.Points,
            smoothing=int(obj.Smoothing),
            tolerance=float(obj.Tolerance),
            closed=obj.Closed,
        )

    def execute(self, obj):
        pts = self.conditioned_points(obj)
        wire = build_curve(
            pts,
            closed=obj.Closed,
            degree=int(obj.Degree),
            interpolate=obj.Interpolate,
            tolerance=max(float(obj.Tolerance), 0.01),
        )
        obj.Length = wire.Length
        shape = wire
        thickness = float(obj.Thickness)
        end_thickness = float(obj.EndThickness)
        if end_thickness < 0:
            end_thickness = thickness
        if thickness > 0 or end_thickness > 0:
            shape = build_tube(wire, thickness / 2.0, end_thickness / 2.0, int(obj.TubeSections))
        elif obj.Closed and obj.MakeFace:
            shape = _fill_wire(wire)
        obj.Shape = shape

    def onChanged(self, obj, prop):
        if prop == "Closed" and not obj.Closed and hasattr(obj, "MakeFace"):
            obj.MakeFace = False


def _fill_wire(wire):
    """Make a face from a closed wire, planar or free-form."""
    try:
        face = Part.Face(wire)
        if face.isValid() and face.Area > 1e-9:
            return face
    except Part.OCCError:
        pass
    return fill_edges(wire.Edges)


def fill_edges(edges):
    """Fill a loop of edges with a smooth (non planar) face.

    The edges are chained into a wire first: OCC's filling algorithm needs
    them connected head to tail to produce a valid face.
    """
    ordered = list(edges)
    try:
        wire = Part.Wire(Part.__sortEdges__(edges))
        ordered = wire.OrderedEdges
    except Part.OCCError:
        pass
    face = Part.makeFilledFace(ordered)
    if face.isNull():
        raise ValueError(translate("Freeform", "Could not fill the boundary"))
    return face


# ---------------------------------------------------------------------------
# Ribbon
# ---------------------------------------------------------------------------


class Ribbon(_FeatureBase):
    """A band of constant width following a base curve."""

    Type = "Freeform::Ribbon"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Ribbon", "The stroke or curve the ribbon follows")
        add(obj, "App::PropertyLength", "Width", "Ribbon", "Width of the ribbon", 10.0)
        add(
            obj,
            "App::PropertyVector",
            "Normal",
            "Ribbon",
            "Reference direction: the ribbon lies flat perpendicular to it (Flat) or along it (Upright)",
            Vector(0, 0, 1),
        )
        add(
            obj,
            "App::PropertyEnumeration",
            "Mode",
            "Ribbon",
            "How the ribbon is oriented around the curve",
        )
        if not obj.Mode:
            obj.Mode = ["Flat", "Upright"]
            obj.Mode = "Flat"
        add(
            obj,
            "App::PropertyLength",
            "Thickness",
            "Ribbon",
            "Thickness of the ribbon; 0 gives a surface",
            0.0,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Samples",
            "Ribbon",
            "Number of samples along the curve used to build the ribbon",
            (40, 4, 1000, 1),
        )
        add(
            obj,
            "App::PropertyBool",
            "Centered",
            "Ribbon",
            "Center the ribbon on the curve (true) or start from it (false)",
            True,
        )

    def execute(self, obj):
        shape = _link_shape(obj.Base)
        if shape is None:
            raise ValueError(translate("Freeform", "Ribbon has no base curve"))
        wire = _wire_of(shape)
        width = float(obj.Width)
        if width <= 0:
            raise ValueError(translate("Freeform", "Ribbon width must be positive"))
        normal = Vector(obj.Normal)
        if normal.Length < 1e-9:
            normal = Vector(0, 0, 1)
        normal.normalize()
        closed = wire.isClosed()
        stations = _stations(wire, int(obj.Samples) + (1 if closed else 0))
        if closed:
            stations = stations[:-1]
        left, right = [], []
        half = width / 2.0
        for point, tangent in stations:
            if obj.Mode == "Upright":
                direction = normal
            else:
                direction = normal.cross(tangent)
                if direction.Length < 1e-9:
                    direction = geometry._perpendicular(tangent)
                direction.normalize()
            if obj.Centered:
                left.append(point + direction * half)
                right.append(point - direction * half)
            else:
                left.append(point + direction * width)
                right.append(Vector(point))
        wire_a = build_curve(left, closed=closed)
        wire_b = build_curve(right, closed=closed)
        if len(wire_a.Edges) == 1 and len(wire_b.Edges) == 1:
            face = Part.makeRuledSurface(wire_a.Edges[0], wire_b.Edges[0])
        else:
            face = Part.makeRuledSurface(wire_a, wire_b)
        thickness = float(obj.Thickness)
        if thickness > 0:
            # offset one half down, then thicken by the full amount so the
            # solid is centred on the ribbon surface
            lower = face.makeOffsetShape(-thickness / 2.0, 1e-4)
            solid = lower.makeOffsetShape(thickness, 1e-4, fill=True)
            if solid.isNull():
                raise ValueError(translate("Freeform", "Could not thicken the ribbon"))
            face = solid
        obj.Shape = face


# ---------------------------------------------------------------------------
# Surface (loft)
# ---------------------------------------------------------------------------


class Surface(_FeatureBase):
    """A lofted surface through a series of strokes."""

    Type = "Freeform::Surface"

    def __init__(self, obj, sections=None):
        super().__init__(obj)
        self.migrate(obj)
        if sections:
            obj.Sections = list(sections)

    def migrate(self, obj):
        add = self._add
        add(
            obj,
            "App::PropertyLinkList",
            "Sections",
            "Surface",
            "The strokes to span the surface through",
        )
        add(
            obj,
            "App::PropertyBool",
            "Ruled",
            "Surface",
            "Straight (ruled) or smooth transitions",
            False,
        )
        add(
            obj,
            "App::PropertyBool",
            "Closed",
            "Surface",
            "Loop the surface back to the first section",
            False,
        )
        add(
            obj,
            "App::PropertyBool",
            "Solid",
            "Surface",
            "Make a solid when all sections are closed",
            False,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "MaxDegree",
            "Surface",
            "Maximum degree of the lofted surface",
            (5, 1, 8, 1),
        )

    def execute(self, obj):
        profiles = []
        for link in obj.Sections:
            shape = _link_shape(link)
            if shape is None:
                continue
            if shape.ShapeType == "Vertex":
                profiles.append(shape)
            else:
                profiles.append(_wire_of(shape))
        if len(profiles) < 2:
            raise ValueError(translate("Freeform", "A surface needs at least two sections"))
        solid = obj.Solid and all(p.ShapeType == "Vertex" or p.isClosed() for p in profiles)
        obj.Shape = Part.makeLoft(profiles, solid, obj.Ruled, obj.Closed, int(obj.MaxDegree))


# ---------------------------------------------------------------------------
# Patch (filled boundary)
# ---------------------------------------------------------------------------


class Patch(_FeatureBase):
    """A smooth surface filling a closed loop of curves."""

    Type = "Freeform::Patch"

    def __init__(self, obj, boundary=None):
        super().__init__(obj)
        self.migrate(obj)
        if boundary:
            obj.Boundary = boundary

    def migrate(self, obj):
        add = self._add
        add(
            obj,
            "App::PropertyLinkSubList",
            "Boundary",
            "Patch",
            "Curves (or edges of objects) forming a closed boundary",
        )

    def execute(self, obj):
        edges = []
        for link, subs in obj.Boundary:
            shape = _link_shape(link)
            if shape is None:
                continue
            if subs and any(subs):
                for sub in subs:
                    if sub:
                        edges.extend(shape.getElement(sub).Edges)
            else:
                edges.extend(shape.Edges)
        if len(edges) < 1:
            raise ValueError(translate("Freeform", "A patch needs at least one boundary edge"))
        obj.Shape = fill_edges(edges)


# ---------------------------------------------------------------------------
# Subdivision surface
# ---------------------------------------------------------------------------


class SubD(_FeatureBase):
    """Catmull-Clark subdivision of a cage shape or mesh."""

    Type = "Freeform::SubD"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "SubD", "The cage: a Part shape or a mesh")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Iterations",
            "SubD",
            "Number of subdivision passes",
            (2, 0, 6, 1),
        )
        add(
            obj,
            "App::PropertyBool",
            "KeepBoundary",
            "SubD",
            "Keep the open boundary of the cage fixed",
            True,
        )

    @staticmethod
    def cage_polygons(base):
        """Return ``(points, faces)`` of the cage object."""
        if base is None:
            raise ValueError(translate("Freeform", "SubD has no cage"))
        mesh = getattr(base, "Mesh", None)
        if mesh is not None:
            points, facets = mesh.Topology
            return geometry.weld_points(points, [list(f) for f in facets])
        shape = _link_shape(base)
        if shape is None:
            raise ValueError(translate("Freeform", "SubD cage has no shape"))
        return geometry.polygons_from_shape(shape)

    def execute(self, obj):
        import Mesh

        points, faces = self.cage_polygons(obj.Base)
        points, faces = geometry.catmull_clark(points, faces, int(obj.Iterations), obj.KeepBoundary)
        triangles = geometry.triangulate_polygons(faces)
        flat = []
        for tri in triangles:
            flat.extend(points[i] for i in tri)
        obj.Mesh = Mesh.Mesh(flat)


# ---------------------------------------------------------------------------
# View providers (GUI only)
# ---------------------------------------------------------------------------


class _ViewProviderBase:
    icon = "Freeform_Stroke"

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/" + self.icon + ".svg"

    def claimChildren(self):
        return []

    def onDelete(self, vobj, subelements):
        for child in self.claimChildren():
            try:
                child.ViewObject.show()
            except Exception:  # pylint: disable=broad-except
                pass
        return True

    def updateData(self, obj, prop):
        return None

    def onChanged(self, vobj, prop):
        return None

    def dumps(self):
        return None

    def loads(self, state):
        return None


class ViewProviderStroke(_ViewProviderBase):
    icon = "Freeform_Stroke"

    def attach(self, vobj):
        super().attach(vobj)
        vobj.LineWidth = 3.0
        vobj.PointSize = 4.0

    def getIcon(self):
        obj = getattr(self, "Object", None)
        if obj is not None and float(getattr(obj, "Thickness", 0.0)) > 0:
            return ":/icons/Freeform_Thicken.svg"
        return super().getIcon()


class ViewProviderRibbon(_ViewProviderBase):
    icon = "Freeform_Ribbon"

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        base = getattr(obj, "Base", None)
        return [base] if base is not None else []


class ViewProviderSurface(_ViewProviderBase):
    icon = "Freeform_Surface"

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        return list(getattr(obj, "Sections", []) or [])


class ViewProviderPatch(_ViewProviderBase):
    icon = "Freeform_Patch"

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        return [link for link, _ in (getattr(obj, "Boundary", []) or [])]


class ViewProviderSubD(_ViewProviderBase):
    icon = "Freeform_SubD"

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        base = getattr(obj, "Base", None)
        return [base] if base is not None else []


# ---------------------------------------------------------------------------
# Factory functions (scripting API)
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


def _apply_current_color(obj):
    """Colour a new object with the palette's current colour (GUI only)."""
    if not FreeCAD.GuiUp or obj.ViewObject is None:
        return
    params = FreeCAD.ParamGet(PARAM_PATH)
    packed = params.GetUnsigned("CurrentColor", 0)
    if packed == 0:
        return
    color = (
        ((packed >> 24) & 0xFF) / 255.0,
        ((packed >> 16) & 0xFF) / 255.0,
        ((packed >> 8) & 0xFF) / 255.0,
    )
    vobj = obj.ViewObject
    for prop in ("LineColor", "PointColor", "ShapeColor"):
        if hasattr(vobj, prop):
            try:
                setattr(vobj, prop, color)
            except Exception:  # pylint: disable=broad-except
                pass
    if hasattr(vobj, "ShapeAppearance"):
        try:
            material = vobj.ShapeAppearance[0]
            material.DiffuseColor = color
            vobj.ShapeAppearance = (material,)
        except Exception:  # pylint: disable=broad-except
            pass


def make_stroke(
    points, name="Stroke", doc=None, closed=False, thickness=0.0, smoothing=0, tolerance=0.0
):
    """Create a Freeform stroke through ``points``.

    ``smoothing`` Laplacian passes are applied before the curve is built;
    the interactive tool uses its preference value, scripting defaults to
    none so the curve passes exactly through the given points.
    """
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Stroke(obj, points)
    obj.Closed = closed
    obj.Thickness = thickness
    obj.Smoothing = smoothing
    obj.Tolerance = tolerance
    if FreeCAD.GuiUp:
        ViewProviderStroke(obj.ViewObject)
        _apply_current_color(obj)
    return obj


def make_ribbon(
    base, width=10.0, normal=Vector(0, 0, 1), mode="Flat", thickness=0.0, name="Ribbon", doc=None
):
    """Create a ribbon following ``base`` (a stroke or any curve object)."""
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Ribbon(obj, base)
    obj.Width = width
    obj.Normal = Vector(normal)
    obj.Mode = mode
    obj.Thickness = thickness
    if FreeCAD.GuiUp:
        ViewProviderRibbon(obj.ViewObject)
        _apply_current_color(obj)
        _hide([base])
    return obj


def make_surface(sections, ruled=False, closed=False, solid=False, name="Surface", doc=None):
    """Create a lofted surface through ``sections`` (stroke objects)."""
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Surface(obj, sections)
    obj.Ruled = ruled
    obj.Closed = closed
    obj.Solid = solid
    if FreeCAD.GuiUp:
        ViewProviderSurface(obj.ViewObject)
        _apply_current_color(obj)
        _hide(sections)
    return obj


def make_patch(boundary, name="Patch", doc=None):
    """Create a patch over ``boundary``: objects or ``(object, [subnames])`` tuples."""
    doc = _document(doc)
    links = []
    for item in boundary:
        if isinstance(item, (tuple, list)):
            subs = [sub for sub in item[1] if sub] if not isinstance(item[1], str) else [item[1]]
            links.append((item[0], subs or [""]))
        else:
            # an empty sub-element list would drop the entry, so use ""
            links.append((item, [""]))
    obj = doc.addObject("Part::FeaturePython", name)
    Patch(obj, links)
    if FreeCAD.GuiUp:
        ViewProviderPatch(obj.ViewObject)
        _apply_current_color(obj)
        _hide([link for link, _ in links if is_freeform_object(link, "Stroke")])
    return obj


def make_subd(base, iterations=2, keep_boundary=True, name="SubD", doc=None):
    """Create a subdivision surface mesh from the cage ``base``."""
    doc = _document(doc)
    obj = doc.addObject("Mesh::FeaturePython", name)
    SubD(obj, base)
    obj.Iterations = iterations
    obj.KeepBoundary = keep_boundary
    if FreeCAD.GuiUp:
        ViewProviderSubD(obj.ViewObject)
        _apply_current_color(obj)
        _hide([base])
    return obj


def make_mirror(source, origin=Vector(0, 0, 0), normal=Vector(1, 0, 0), name=None, doc=None):
    """Create a live mirror copy of ``source`` using ``Part::Mirroring``."""
    doc = _document(doc)
    obj = doc.addObject("Part::Mirroring", name or (source.Name + "_Mirror"))
    obj.Source = source
    obj.Base = Vector(origin)
    obj.Normal = Vector(normal)
    obj.Label = source.Label + " (mirror)"
    if FreeCAD.GuiUp and obj.ViewObject is not None and source.ViewObject is not None:
        for prop in ("LineColor", "PointColor", "LineWidth", "PointSize", "ShapeAppearance"):
            if hasattr(obj.ViewObject, prop) and hasattr(source.ViewObject, prop):
                try:
                    setattr(obj.ViewObject, prop, getattr(source.ViewObject, prop))
                except Exception:  # pylint: disable=broad-except
                    pass
    return obj


def make_revolve(
    source,
    origin=Vector(0, 0, 0),
    axis=Vector(0, 0, 1),
    angle=360.0,
    solid=True,
    name=None,
    doc=None,
):
    """Revolve ``source`` around an axis using ``Part::Revolution``."""
    doc = _document(doc)
    obj = doc.addObject("Part::Revolution", name or (source.Name + "_Revolve"))
    obj.Source = source
    obj.Base = Vector(origin)
    obj.Axis = Vector(axis)
    obj.Angle = angle
    obj.Solid = solid
    obj.Label = source.Label + " (revolved)"
    if FreeCAD.GuiUp:
        _apply_current_color(obj)
        _hide([source])
    return obj


_PRIMITIVES = {
    "Sphere": ("Part::Sphere", {"Radius": 1.0}),
    "Box": ("Part::Box", {"Length": 2.0, "Width": 2.0, "Height": 2.0}),
    "Cylinder": ("Part::Cylinder", {"Radius": 1.0, "Height": 2.0}),
    "Cone": ("Part::Cone", {"Radius1": 1.0, "Radius2": 0.0, "Height": 2.0}),
    "Torus": ("Part::Torus", {"Radius1": 0.7, "Radius2": 0.3}),
}


def make_primitive(kind, position=Vector(0, 0, 0), size=10.0, normal=Vector(0, 0, 1), doc=None):
    """Create a Part primitive of ``kind`` scaled to ``size`` at ``position``.

    ``size`` is the overall extent of the primitive; the box is centered on
    ``position``, the others are placed so that they sit on the drawing
    plane whose normal is ``normal``.
    """
    if kind not in _PRIMITIVES:
        raise ValueError(translate("Freeform", "Unknown primitive: %s") % kind)
    doc = _document(doc)
    type_name, props = _PRIMITIVES[kind]
    obj = doc.addObject(type_name, kind)
    for prop, factor in props.items():
        setattr(obj, prop, factor * size / 2.0)
    normal = Vector(normal)
    if normal.Length < 1e-9:
        normal = Vector(0, 0, 1)
    normal.normalize()
    rotation = FreeCAD.Rotation(Vector(0, 0, 1), normal)
    offset = Vector(0, 0, 0)
    if kind == "Box":
        offset = Vector(-size / 2.0, -size / 2.0, 0)
    elif kind in ("Sphere", "Torus"):
        offset = Vector(0, 0, size / 2.0 if kind == "Sphere" else size * 0.15)
    obj.Placement = FreeCAD.Placement(Vector(position) + rotation.multVec(offset), rotation)
    if FreeCAD.GuiUp:
        _apply_current_color(obj)
    return obj
