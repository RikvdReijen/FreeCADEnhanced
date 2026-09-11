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

"""Parametric generators of the Freeform workbench (Grasshopper inspired).

Every object here is a live, editable feature: change a count, a seed or
an attractor and the result regenerates.

Curves
    ``Expression``   a stroke defined by x(t), y(t), z(t) expressions
    ``Offset``       a parallel copy of a planar curve
    ``Blend``        a tangent continuous bridge between two curve ends
    ``Divide``       points and frames evenly spaced along a curve
    ``Contours``     section curves of a shape at regular spacing
Arrays and panels
    ``CurveArray``   copies of an object oriented along a curve, with
                     attractor driven scale and twist
    ``SurfaceGrid``  points, panels or copies on the UV grid of a face,
                     with attractor driven panel size
    ``Voronoi``      Voronoi cells on a planar face, optionally inset
Meshes
    ``Deform``       twist, taper, bend, stretch, wave, noise or flow a
                     mesh along a curve
    ``Relax``        Laplacian relaxation towards a minimal surface
"""

import math
import random

import FreeCAD
import Part
from FreeCAD import Vector

from . import features, geometry, parametric
from .features import _FeatureBase, _ViewProviderBase, _document, _hide, _apply_current_color

translate = FreeCAD.Qt.translate

__all__ = [
    "Expression",
    "Offset",
    "Blend",
    "Divide",
    "Contours",
    "CurveArray",
    "SurfaceGrid",
    "Voronoi",
    "Deform",
    "Relax",
    "make_expression",
    "make_offset",
    "make_blend",
    "make_divide",
    "make_contours",
    "make_curve_array",
    "make_surface_grid",
    "make_voronoi",
    "make_deform",
    "make_relax",
    "attractor_points",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shape_of(link):
    shape = features._link_shape(link)  # pylint: disable=protected-access
    if shape is None:
        raise ValueError(
            translate("Freeform", "Object %s has no shape") % getattr(link, "Label", "?")
        )
    return shape


def _wire_of(link):
    return features._wire_of(_shape_of(link))  # pylint: disable=protected-access


def _face_of(link, subname=None):
    shape = _shape_of(link)
    if subname:
        shape = shape.getElement(subname)
    if shape.ShapeType == "Face":
        return shape
    if shape.Faces:
        return shape.Faces[0]
    raise ValueError(translate("Freeform", "Object %s has no face") % getattr(link, "Label", "?"))


def _mesh_polygons(link):
    """``(points, faces)`` of a mesh object or of a tessellated shape."""
    mesh = getattr(link, "Mesh", None) if link is not None else None
    if mesh is not None:
        points, facets = mesh.Topology
        return list(points), [list(f) for f in facets]
    shape = _shape_of(link)
    return geometry.polygons_from_shape(shape)


def _mesh_from_polygons(points, faces):
    import Mesh

    flat = []
    for tri in geometry.triangulate_polygons(faces):
        flat.extend(points[i] for i in tri)
    return Mesh.Mesh(flat)


def attractor_points(objects):
    """Anchor points of attractor objects: shape centre of mass or placement."""
    points = []
    for obj in objects or []:
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            if shape.Vertexes and len(shape.Vertexes) == 1:
                points.append(shape.Vertexes[0].Point)
            else:
                try:
                    points.append(shape.CenterOfMass)
                except Exception:  # pylint: disable=broad-except
                    points.append(shape.BoundBox.Center)
        elif hasattr(obj, "Placement"):
            points.append(Vector(obj.Placement.Base))
    return points


def _local_shape(obj):
    """The shape of ``obj`` with its placement removed (as modelled at the origin)."""
    shape = _shape_of(obj).copy()
    placement = getattr(obj, "Placement", None)
    if placement is not None:
        shape.transformShape(placement.inverse().toMatrix())
    return shape


def _place_copy(local, point, x_axis, y_axis, z_axis, scale=1.0, align=True):
    copy = local.copy()
    if align:
        rotation = FreeCAD.Rotation(x_axis, y_axis, z_axis, "ZXY")
    else:
        rotation = FreeCAD.Rotation()
    matrix = FreeCAD.Placement(Vector(point), rotation).toMatrix()
    if abs(scale - 1.0) > 1e-12:
        scale_matrix = FreeCAD.Matrix()
        scale_matrix.scale(scale, scale, scale)
        matrix = matrix.multiply(scale_matrix)
        copy = copy.transformGeometry(matrix)
    else:
        copy.transformShape(matrix)
    return copy


def _add_attractor_properties(add, obj, group):
    add(
        obj,
        "App::PropertyLinkList",
        "Attractors",
        group,
        "Objects whose position shrinks nearby elements (Grasshopper attractor)",
    )
    add(
        obj,
        "App::PropertyLength",
        "AttractorRadius",
        group,
        "Distance over which an attractor has influence",
        50.0,
    )
    add(
        obj,
        "App::PropertyFloatConstraint",
        "MinScale",
        group,
        "Scale of elements sitting on an attractor",
        (0.2, 0.0, 10.0, 0.05),
    )
    add(
        obj,
        "App::PropertyEnumeration",
        "Falloff",
        group,
        "How the attractor influence fades with distance",
    )
    if not obj.Falloff:
        obj.Falloff = ["linear", "smooth", "inverse"]
        obj.Falloff = "linear"


def _attractor_scale(obj, point, base=1.0):
    attractors = attractor_points(obj.Attractors)
    if not attractors:
        return base
    return base * parametric.attractor_factor(
        point,
        attractors,
        float(obj.AttractorRadius),
        minimum=float(obj.MinScale),
        maximum=1.0,
        falloff=obj.Falloff or "linear",
    )


# ---------------------------------------------------------------------------
# Expression curve
# ---------------------------------------------------------------------------


class Expression(features.Stroke):
    """A stroke whose points come from x(t), y(t), z(t) expressions."""

    Type = "Freeform::Expression"

    def migrate(self, obj):
        super().migrate(obj)
        add = self._add
        add(
            obj,
            "App::PropertyString",
            "XExpression",
            "Expression",
            "x as a function of t",
            "40*cos(t)",
        )
        add(
            obj,
            "App::PropertyString",
            "YExpression",
            "Expression",
            "y as a function of t",
            "40*sin(t)",
        )
        add(obj, "App::PropertyString", "ZExpression", "Expression", "z as a function of t", "4*t")
        add(obj, "App::PropertyFloat", "TMin", "Expression", "Start of the parameter range", 0.0)
        add(
            obj,
            "App::PropertyFloat",
            "TMax",
            "Expression",
            "End of the parameter range",
            4 * math.pi,
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Samples",
            "Expression",
            "Number of evaluated points",
            (100, 2, 10000, 1),
        )
        obj.setEditorMode("Points", 1)

    def execute(self, obj):
        obj.Points = parametric.expression_points(
            obj.XExpression,
            obj.YExpression,
            obj.ZExpression,
            float(obj.TMin),
            float(obj.TMax),
            int(obj.Samples),
        )
        super().execute(obj)


# ---------------------------------------------------------------------------
# Offset and blend curves
# ---------------------------------------------------------------------------


class Offset(_FeatureBase):
    """A parallel copy of a planar curve."""

    Type = "Freeform::Offset"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Offset", "The planar curve to offset")
        add(
            obj,
            "App::PropertyDistance",
            "Distance",
            "Offset",
            "Offset distance (sign flips the side)",
            5.0,
        )
        add(obj, "App::PropertyEnumeration", "Join", "Offset", "Corner treatment")
        if not obj.Join:
            obj.Join = ["Arc", "Tangent", "Intersection"]
            obj.Join = "Arc"
        add(
            obj,
            "App::PropertyBool",
            "Fill",
            "Offset",
            "Fill the band between curve and offset",
            False,
        )

    def execute(self, obj):
        wire = _wire_of(obj.Base)
        distance = float(obj.Distance)
        if abs(distance) < 1e-9:
            obj.Shape = wire
            return
        join = ["Arc", "Tangent", "Intersection"].index(obj.Join or "Arc")
        result = wire.makeOffset2D(distance, join, obj.Fill, not wire.isClosed())
        if result.isNull():
            raise ValueError(translate("Freeform", "Could not offset the curve"))
        obj.Shape = result


class Blend(_FeatureBase):
    """A tangent continuous curve bridging the nearest ends of two curves."""

    Type = "Freeform::Blend"

    def __init__(self, obj, first=None, second=None):
        super().__init__(obj)
        self.migrate(obj)
        if first is not None:
            obj.First = first
        if second is not None:
            obj.Second = second

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "First", "Blend", "First curve")
        add(obj, "App::PropertyLink", "Second", "Blend", "Second curve")
        add(
            obj,
            "App::PropertyFloatConstraint",
            "Bulge",
            "Blend",
            "Tangent strength relative to the gap between the curves",
            (1.0, 0.0, 10.0, 0.1),
        )

    @staticmethod
    def _ends(wire):
        edges = wire.OrderedEdges
        first, last = edges[0], edges[-1]
        start = (first.valueAt(first.FirstParameter), first.tangentAt(first.FirstParameter) * -1.0)
        end = (last.valueAt(last.LastParameter), last.tangentAt(last.LastParameter))
        if first.Orientation == "Reversed":
            start = (start[0], start[1] * -1.0)
        if last.Orientation == "Reversed":
            end = (end[0], end[1] * -1.0)
        return [start, end]  # (point, outward tangent)

    def execute(self, obj):
        wire_a = _wire_of(obj.First)
        wire_b = _wire_of(obj.Second)
        best = None
        for point_a, tangent_a in self._ends(wire_a):
            for point_b, tangent_b in self._ends(wire_b):
                gap = (point_b - point_a).Length
                if best is None or gap < best[0]:
                    best = (gap, point_a, tangent_a, point_b, tangent_b)
        gap, point_a, tangent_a, point_b, tangent_b = best
        if gap < 1e-9:
            raise ValueError(translate("Freeform", "The curves already touch"))
        strength = float(obj.Bulge) * gap
        if strength < 1e-9:
            obj.Shape = Part.Wire(Part.makeLine(point_a, point_b))
            return
        curve = Part.BSplineCurve()
        curve.interpolate(
            Points=[point_a, point_b],
            InitialTangent=geometry._safe_normalize(tangent_a) * strength,
            FinalTangent=geometry._safe_normalize(tangent_b) * -strength,
        )
        obj.Shape = Part.Wire(curve.toShape())


# ---------------------------------------------------------------------------
# Divide and contours
# ---------------------------------------------------------------------------


class Divide(_FeatureBase):
    """Evenly spaced points (and frames) along a curve."""

    Type = "Freeform::Divide"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Divide", "The curve to divide")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Count",
            "Divide",
            "Number of points",
            (10, 2, 10000, 1),
        )
        add(
            obj,
            "App::PropertyLength",
            "Spacing",
            "Divide",
            "Distance between points; overrides Count when positive",
            0.0,
        )
        add(
            obj,
            "App::PropertyVector",
            "Up",
            "Divide",
            "Reference direction for the frames",
            Vector(0, 0, 1),
        )
        add(
            obj,
            "App::PropertyLength",
            "FrameSize",
            "Divide",
            "Length of the drawn frame axes; 0 draws points only",
            0.0,
        )
        add(obj, "App::PropertyPlacementList", "Placements", "Divide", "The frames (read only)")
        obj.setEditorMode("Placements", 1)

    def execute(self, obj):
        wire = _wire_of(obj.Base)
        spacing = float(obj.Spacing)
        frames = parametric.frames_along_wire(
            wire,
            count=None if spacing > 0 else int(obj.Count),
            spacing=spacing if spacing > 0 else None,
            up=Vector(obj.Up),
        )
        placements = []
        shapes = []
        size = float(obj.FrameSize)
        for point, tangent, normal, binormal in frames:
            placements.append(
                FreeCAD.Placement(point, FreeCAD.Rotation(normal, binormal, tangent, "ZXY"))
            )
            shapes.append(Part.Vertex(point))
            if size > 0:
                shapes.append(Part.makeLine(point, point + tangent * size))
                shapes.append(Part.makeLine(point, point + normal * (size * 0.5)))
        obj.Placements = placements
        obj.Shape = Part.makeCompound(shapes)


class Contours(_FeatureBase):
    """Section curves of a shape at a regular spacing."""

    Type = "Freeform::Contours"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Contours", "The shape to slice")
        add(
            obj,
            "App::PropertyVector",
            "Direction",
            "Contours",
            "Normal of the section planes",
            Vector(0, 0, 1),
        )
        add(obj, "App::PropertyLength", "Spacing", "Contours", "Distance between sections", 5.0)
        add(obj, "App::PropertyDistance", "Start", "Contours", "Offset of the first section", 0.0)
        add(obj, "App::PropertyBool", "Faces", "Contours", "Fill closed sections with faces", False)

    def execute(self, obj):
        shape = _shape_of(obj.Base)
        direction = geometry._safe_normalize(Vector(obj.Direction))
        spacing = float(obj.Spacing)
        if spacing <= 0:
            raise ValueError(translate("Freeform", "Contour spacing must be positive"))
        # project the bounding box corners: a sphere's vertices are just its
        # poles, which would give an empty range for most directions
        box = shape.BoundBox
        corners = [
            Vector(x, y, z)
            for x in (box.XMin, box.XMax)
            for y in (box.YMin, box.YMax)
            for z in (box.ZMin, box.ZMax)
        ]
        distances = [c.dot(direction) for c in corners]
        low, high = min(distances), max(distances)
        first = low + float(obj.Start)
        values = []
        d = first + spacing * 0.5 if abs(float(obj.Start)) < 1e-9 else first
        while d <= high - 1e-9:
            values.append(d)
            d += spacing
        if not values:
            raise ValueError(translate("Freeform", "No section fits inside the shape"))
        sections = shape.slices(direction, values)
        if obj.Faces:
            faces = []
            for wire in sections.Wires:
                if wire.isClosed():
                    try:
                        faces.append(Part.Face(wire))
                    except Part.OCCError:
                        faces.append(wire)
                else:
                    faces.append(wire)
            sections = Part.makeCompound(faces)
        obj.Shape = sections


# ---------------------------------------------------------------------------
# Arrays and panels
# ---------------------------------------------------------------------------


class CurveArray(_FeatureBase):
    """Copies of an object distributed and oriented along a curve."""

    Type = "Freeform::CurveArray"

    def __init__(self, obj, base=None, path=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base
        if path is not None:
            obj.Path = path

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Array", "The object to copy")
        add(obj, "App::PropertyLink", "Path", "Array", "The curve to follow")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Count",
            "Array",
            "Number of copies",
            (10, 1, 10000, 1),
        )
        add(obj, "App::PropertyBool", "Align", "Array", "Orient the copies along the curve", True)
        add(
            obj,
            "App::PropertyVector",
            "Up",
            "Array",
            "Reference direction for the orientation",
            Vector(0, 0, 1),
        )
        add(
            obj,
            "App::PropertyAngle",
            "Twist",
            "Array",
            "Total rotation about the curve over its length",
            0.0,
        )
        add(obj, "App::PropertyFloat", "StartScale", "Array", "Scale of the first copy", 1.0)
        add(obj, "App::PropertyFloat", "EndScale", "Array", "Scale of the last copy", 1.0)
        _add_attractor_properties(add, obj, "Attractor")

    def execute(self, obj):
        local = _local_shape(obj.Base)
        wire = _wire_of(obj.Path)
        count = int(obj.Count)
        frames = parametric.frames_along_wire(wire, count=max(2, count), up=Vector(obj.Up))
        frames = frames[:count]
        copies = []
        n = max(1, len(frames) - 1)
        for i, (point, tangent, normal, binormal) in enumerate(frames):
            t = i / float(n)
            scale = float(obj.StartScale) + (float(obj.EndScale) - float(obj.StartScale)) * t
            scale = _attractor_scale(obj, point, scale)
            if scale <= 1e-9:
                continue
            angle = math.radians(float(obj.Twist)) * t
            if abs(angle) > 1e-12:
                c, s = math.cos(angle), math.sin(angle)
                normal, binormal = normal * c + binormal * s, binormal * c - normal * s
            copies.append(_place_copy(local, point, normal, binormal, tangent, scale, obj.Align))
        if not copies:
            raise ValueError(translate("Freeform", "No copies were created"))
        obj.Shape = Part.makeCompound(copies)


class SurfaceGrid(_FeatureBase):
    """Points, panels or copies on the UV grid of a face."""

    Type = "Freeform::SurfaceGrid"

    def __init__(self, obj, base=None, subname=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = (base, [subname] if subname else [""])

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLinkSub", "Base", "Grid", "The face to panel")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "CountU",
            "Grid",
            "Divisions in U",
            (8, 1, 500, 1),
        )
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "CountV",
            "Grid",
            "Divisions in V",
            (8, 1, 500, 1),
        )
        add(obj, "App::PropertyEnumeration", "Output", "Grid", "What to generate on the grid")
        if not obj.Output:
            obj.Output = ["Panels", "Points", "Copies", "Frames"]
            obj.Output = "Panels"
        add(
            obj, "App::PropertyLink", "Item", "Grid", "Object to copy onto the grid (Copies output)"
        )
        add(
            obj,
            "App::PropertyFloatConstraint",
            "PanelScale",
            "Grid",
            "Size of the panels relative to their grid cell",
            (0.9, 0.0, 1.0, 0.05),
        )
        add(
            obj,
            "App::PropertyLength",
            "ItemScale",
            "Grid",
            "Size reference for copies and frames",
            1.0,
        )
        _add_attractor_properties(add, obj, "Attractor")

    def execute(self, obj):
        link, subs = obj.Base
        face = _face_of(link, subs[0] if subs and subs[0] else None)
        count_u, count_v = int(obj.CountU), int(obj.CountV)
        u0, u1, v0, v1 = face.ParameterRange
        grid = []
        for i in range(count_u + 1):
            row = []
            for j in range(count_v + 1):
                u = u0 + (u1 - u0) * i / float(count_u)
                v = v0 + (v1 - v0) * j / float(count_v)
                row.append((face.valueAt(u, v), face.normalAt(u, v), u, v))
            grid.append(row)
        output = obj.Output or "Panels"
        shapes = []
        if output in ("Points", "Frames", "Copies"):
            local = _local_shape(obj.Item) if output == "Copies" and obj.Item is not None else None
            size = float(obj.ItemScale)
            for i in range(count_u + 1):
                for j in range(count_v + 1):
                    point, normal, u, v = grid[i][j]
                    scale = _attractor_scale(obj, point, 1.0)
                    if output == "Points":
                        shapes.append(Part.Vertex(point))
                        continue
                    # tangent along u for the frame's x axis
                    du = face.tangentAt(u, v)[0]
                    x_axis = geometry._safe_normalize(du - normal * du.dot(normal))
                    y_axis = normal.cross(x_axis)
                    if output == "Frames":
                        length = size * scale
                        shapes.append(Part.makeLine(point, point + normal * length))
                        shapes.append(Part.makeLine(point, point + x_axis * (length * 0.5)))
                    elif local is not None:
                        shapes.append(
                            _place_copy(local, point, x_axis, y_axis, normal, scale * size, True)
                        )
        else:
            for i in range(count_u):
                for j in range(count_v):
                    corners = [
                        grid[i][j][0],
                        grid[i + 1][j][0],
                        grid[i + 1][j + 1][0],
                        grid[i][j + 1][0],
                    ]
                    centre = Vector()
                    for c in corners:
                        centre += c
                    centre *= 0.25
                    scale = _attractor_scale(obj, centre, float(obj.PanelScale))
                    if scale <= 1e-6:
                        continue
                    poly = [centre + (c - centre) * scale for c in corners]
                    poly = geometry.remove_duplicates(poly)
                    if len(poly) < 3:
                        continue
                    wire = Part.makePolygon(poly + [poly[0]])
                    try:
                        shapes.append(Part.Face(wire))
                    except Part.OCCError:
                        shapes.append(features.fill_edges(wire.Edges))
        if not shapes:
            raise ValueError(translate("Freeform", "The grid produced nothing"))
        obj.Shape = Part.makeCompound(shapes)


class Voronoi(_FeatureBase):
    """Voronoi cells over a planar face."""

    Type = "Freeform::Voronoi"

    def __init__(self, obj, base=None, subname=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = (base, [subname] if subname else [""])

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLinkSub", "Base", "Voronoi", "The planar face to tessellate")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Count",
            "Voronoi",
            "Number of random seed points",
            (20, 1, 5000, 1),
        )
        add(obj, "App::PropertyInteger", "Seed", "Voronoi", "Random seed", 0)
        add(
            obj,
            "App::PropertyLinkList",
            "Points",
            "Voronoi",
            "Objects used as seed points instead of random ones (their vertices)",
        )
        add(
            obj,
            "App::PropertyLength",
            "Inset",
            "Voronoi",
            "Shrink every cell inward by this distance",
            0.0,
        )
        add(obj, "App::PropertyEnumeration", "Output", "Voronoi", "Cells as faces or their edges")
        if not obj.Output:
            obj.Output = ["Cells", "Edges", "Delaunay"]
            obj.Output = "Cells"
        _add_attractor_properties(add, obj, "Attractor")

    def execute(self, obj):
        link, subs = obj.Base
        face = _face_of(link, subs[0] if subs and subs[0] else None)
        if face.Surface.__class__.__name__ != "Plane":
            raise ValueError(translate("Freeform", "Voronoi needs a planar face"))
        normal = face.normalAt(0, 0)
        origin = face.CenterOfMass
        u_axis = geometry._perpendicular(normal)
        v_axis = normal.cross(u_axis)

        def to_2d(p):
            d = Vector(p) - origin
            return (d.dot(u_axis), d.dot(v_axis))

        def to_3d(p):
            return origin + u_axis * p[0] + v_axis * p[1]

        seeds = []
        if obj.Points:
            for source in obj.Points:
                shape = getattr(source, "Shape", None)
                if shape is not None and not shape.isNull():
                    seeds.extend(to_2d(v.Point) for v in shape.Vertexes)
        else:
            rng = random.Random(int(obj.Seed))
            corners = [to_2d(v.Point) for v in face.Vertexes]
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            attempts = 0
            while len(seeds) < int(obj.Count) and attempts < 50 * int(obj.Count):
                attempts += 1
                candidate = (rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys)))
                if face.isInside(to_3d(candidate), 1e-6, True):
                    seeds.append(candidate)
        if len(seeds) < 2:
            raise ValueError(translate("Freeform", "Voronoi needs at least two seed points"))
        xs = [c[0] for c in (to_2d(v.Point) for v in face.Vertexes)]
        ys = [c[1] for c in (to_2d(v.Point) for v in face.Vertexes)]
        margin = 0.01 * max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        bounds = [
            (min(xs) - margin, min(ys) - margin),
            (max(xs) + margin, min(ys) - margin),
            (max(xs) + margin, max(ys) + margin),
            (min(xs) - margin, max(ys) + margin),
        ]
        shapes = []
        if (obj.Output or "Cells") == "Delaunay":
            for a, b, c in parametric.delaunay_2d(seeds):
                poly = [to_3d(seeds[a]), to_3d(seeds[b]), to_3d(seeds[c])]
                shapes.append(Part.makePolygon(poly + [poly[0]]))
        else:
            inset = float(obj.Inset)
            for seed, cell in zip(seeds, parametric.voronoi_2d(seeds, bounds)):
                if len(cell) < 3:
                    continue
                poly = geometry.remove_duplicates([to_3d(p) for p in cell], 1e-7)
                if len(poly) < 3:
                    continue
                cell_face = Part.Face(Part.makePolygon(poly + [poly[0]]))
                try:
                    cell_face = cell_face.common(face)
                except Part.OCCError:
                    continue
                if cell_face.isNull() or not cell_face.Faces:
                    continue
                cell_face = cell_face.Faces[0] if len(cell_face.Faces) == 1 else cell_face
                distance = inset
                if obj.Attractors:
                    distance = inset * _attractor_scale(obj, to_3d(seed), 1.0)
                if distance > 1e-9:
                    try:
                        cell_face = cell_face.makeOffset2D(-distance, 0, False, False, True)
                    except Part.OCCError:
                        continue
                    if cell_face.isNull() or not cell_face.Faces:
                        continue
                if (obj.Output or "Cells") == "Edges":
                    shapes.extend(cell_face.Wires)
                else:
                    shapes.extend(cell_face.Faces)
        if not shapes:
            raise ValueError(translate("Freeform", "No Voronoi cells were created"))
        obj.Shape = Part.makeCompound(shapes)


# ---------------------------------------------------------------------------
# Mesh deform and relax
# ---------------------------------------------------------------------------


class Deform(_FeatureBase):
    """Twist, taper, bend, stretch, wave, noise or flow a mesh."""

    Type = "Freeform::Deform"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(
            obj, "App::PropertyLink", "Base", "Deform", "The mesh (or shape, tessellated) to deform"
        )
        add(obj, "App::PropertyEnumeration", "Mode", "Deform", "The deformation")
        if not obj.Mode:
            obj.Mode = list(parametric.DEFORM_MODES)
            obj.Mode = "Twist"
        add(
            obj,
            "App::PropertyFloat",
            "Amount",
            "Deform",
            "Twist: degrees per unit height; Taper: end scale; Bend: total degrees; "
            "Stretch: factor; Wave and Noise: amplitude",
            2.0,
        )
        add(
            obj, "App::PropertyVector", "Axis", "Deform", "Axis of the deformation", Vector(0, 0, 1)
        )
        add(
            obj,
            "App::PropertyVector",
            "Direction",
            "Deform",
            "Bend and wave mode: the direction the shape moves towards; "
            "a null vector picks one automatically",
            Vector(0, 0, 0),
        )
        add(
            obj,
            "App::PropertyVector",
            "Origin",
            "Deform",
            "Origin of the deformation",
            Vector(0, 0, 0),
        )
        add(
            obj,
            "App::PropertyBool",
            "AutoOrigin",
            "Deform",
            "Use the bottom centre of the mesh as origin",
            True,
        )
        add(
            obj,
            "App::PropertyLength",
            "Wavelength",
            "Deform",
            "Wave mode: length of one wave; 0 = half height",
            0.0,
        )
        add(obj, "App::PropertyInteger", "Seed", "Deform", "Noise mode: random seed", 0)
        add(obj, "App::PropertyLink", "Path", "Deform", "Flow mode: the curve to flow along")
        add(
            obj,
            "App::PropertyBool",
            "AlongNormals",
            "Deform",
            "Noise mode: displace along the vertex normals",
            True,
        )

    def execute(self, obj):
        points, faces = _mesh_polygons(obj.Base)
        if not points:
            raise ValueError(translate("Freeform", "Nothing to deform"))
        axis = geometry._safe_normalize(Vector(obj.Axis))
        origin = Vector(obj.Origin)
        if obj.AutoOrigin:
            heights = [p.dot(axis) for p in points]
            centre = Vector()
            for p in points:
                centre += p
            centre *= 1.0 / len(points)
            origin = centre - axis * (centre.dot(axis) - min(heights))
        options = {"seed": int(obj.Seed)}
        direction = Vector(obj.Direction)
        if direction.Length > 1e-9:
            options["direction"] = direction
        wavelength = float(obj.Wavelength)
        if wavelength > 0:
            options["wavelength"] = wavelength
        if obj.Mode == "Noise" and obj.AlongNormals:
            options["normals"] = parametric.mesh_normals(points, faces)
        if obj.Mode == "Flow":
            if obj.Path is None:
                raise ValueError(translate("Freeform", "Flow needs a path curve"))
            wire = _wire_of(obj.Path)
            options["frames"] = parametric.frames_along_wire(
                wire, count=max(8, int(wire.Length / 2.0))
            )
        moved = parametric.deform_points(
            points, obj.Mode, float(obj.Amount), origin, axis, **options
        )
        obj.Mesh = _mesh_from_polygons(moved, faces)


class Relax(_FeatureBase):
    """Laplacian relaxation of a mesh (fixed boundary gives a minimal surface)."""

    Type = "Freeform::Relax"

    def __init__(self, obj, base=None):
        super().__init__(obj)
        self.migrate(obj)
        if base is not None:
            obj.Base = base

    def migrate(self, obj):
        add = self._add
        add(obj, "App::PropertyLink", "Base", "Relax", "The mesh (or shape, tessellated) to relax")
        add(
            obj,
            "App::PropertyIntegerConstraint",
            "Iterations",
            "Relax",
            "Relaxation passes",
            (30, 0, 10000, 1),
        )
        add(
            obj,
            "App::PropertyFloatConstraint",
            "Strength",
            "Relax",
            "How far a vertex moves towards its neighbours per pass",
            (0.5, 0.0, 1.0, 0.05),
        )
        add(obj, "App::PropertyBool", "KeepBoundary", "Relax", "Keep the open boundary fixed", True)
        add(
            obj,
            "App::PropertyLinkList",
            "Anchors",
            "Relax",
            "Objects whose nearest mesh vertex is pinned (tent poles)",
        )

    def execute(self, obj):
        points, faces = _mesh_polygons(obj.Base)
        if not points:
            raise ValueError(translate("Freeform", "Nothing to relax"))
        fixed = set()
        for anchor in attractor_points(obj.Anchors):
            fixed.add(min(range(len(points)), key=lambda i: (points[i] - anchor).Length))
        moved = parametric.relax_mesh(
            points,
            faces,
            iterations=int(obj.Iterations),
            strength=float(obj.Strength),
            fixed=fixed,
            keep_boundary=obj.KeepBoundary,
        )
        obj.Mesh = _mesh_from_polygons(moved, faces)


# ---------------------------------------------------------------------------
# View providers
# ---------------------------------------------------------------------------


class _ViewProviderGenerator(_ViewProviderBase):
    icon = "Freeform_Expression"
    child_properties = ("Base", "Path", "First", "Second", "Item")

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        children = []
        for name in self.child_properties:
            value = getattr(obj, name, None)
            if isinstance(value, tuple):
                value = value[0]
            if value is not None and value not in children:
                children.append(value)
        return children


def _view_provider(icon_name):
    return type(
        "ViewProvider" + icon_name.replace("Freeform_", ""),
        (_ViewProviderGenerator,),
        {"icon": icon_name},
    )


ViewProviderExpression = _view_provider("Freeform_Expression")
ViewProviderOffset = _view_provider("Freeform_Offset")
ViewProviderBlend = _view_provider("Freeform_Blend")
ViewProviderDivide = _view_provider("Freeform_Divide")
ViewProviderContours = _view_provider("Freeform_Contours")
ViewProviderCurveArray = _view_provider("Freeform_CurveArray")
ViewProviderSurfaceGrid = _view_provider("Freeform_SurfaceGrid")
ViewProviderVoronoi = _view_provider("Freeform_Voronoi")
ViewProviderDeform = _view_provider("Freeform_Deform")
ViewProviderRelax = _view_provider("Freeform_Relax")


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def _finish(obj, provider, hide=()):
    if FreeCAD.GuiUp:
        provider(obj.ViewObject)
        _apply_current_color(obj)
        _hide([o for o in hide if o is not None])
    return obj


def make_expression(
    x="40*cos(t)",
    y="40*sin(t)",
    z="4*t",
    t_min=0.0,
    t_max=4 * math.pi,
    samples=100,
    name="Expression",
    doc=None,
):
    """A stroke defined by expressions of ``t`` (see :func:`parametric.evaluate_expression`)."""
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Expression(obj)
    obj.XExpression, obj.YExpression, obj.ZExpression = x, y, z
    obj.TMin, obj.TMax, obj.Samples = t_min, t_max, samples
    if FreeCAD.GuiUp:
        features.ViewProviderStroke(obj.ViewObject)
        _apply_current_color(obj)
    return obj


def make_offset(base, distance=5.0, name="Offset", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Offset(obj, base)
    obj.Distance = distance
    return _finish(obj, ViewProviderOffset)


def make_blend(first, second, bulge=1.0, name="Blend", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Blend(obj, first, second)
    obj.Bulge = bulge
    return _finish(obj, ViewProviderBlend)


def make_divide(base, count=10, spacing=0.0, frame_size=0.0, name="Divide", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Divide(obj, base)
    obj.Count, obj.Spacing, obj.FrameSize = count, spacing, frame_size
    return _finish(obj, ViewProviderDivide)


def make_contours(base, direction=Vector(0, 0, 1), spacing=5.0, name="Contours", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Contours(obj, base)
    obj.Direction, obj.Spacing = Vector(direction), spacing
    return _finish(obj, ViewProviderContours)


def make_curve_array(
    base, path, count=10, align=True, attractors=None, name="CurveArray", doc=None
):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    CurveArray(obj, base, path)
    obj.Count, obj.Align = count, align
    if attractors:
        obj.Attractors = list(attractors)
    return _finish(obj, ViewProviderCurveArray, hide=[base])


def make_surface_grid(
    base,
    subname=None,
    count_u=8,
    count_v=8,
    output="Panels",
    item=None,
    attractors=None,
    name="SurfaceGrid",
    doc=None,
):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    SurfaceGrid(obj, base, subname)
    obj.CountU, obj.CountV, obj.Output = count_u, count_v, output
    if item is not None:
        obj.Item = item
    if attractors:
        obj.Attractors = list(attractors)
    return _finish(obj, ViewProviderSurfaceGrid, hide=[item])


def make_voronoi(
    base, subname=None, count=20, seed=0, inset=0.0, output="Cells", name="Voronoi", doc=None
):
    doc = _document(doc)
    obj = doc.addObject("Part::FeaturePython", name)
    Voronoi(obj, base, subname)
    obj.Count, obj.Seed, obj.Inset, obj.Output = count, seed, inset, output
    return _finish(obj, ViewProviderVoronoi)


def make_deform(base, mode="Twist", amount=2.0, path=None, name="Deform", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Mesh::FeaturePython", name)
    Deform(obj, base)
    obj.Mode, obj.Amount = mode, amount
    if path is not None:
        obj.Path = path
    return _finish(obj, ViewProviderDeform, hide=[base])


def make_relax(base, iterations=30, strength=0.5, anchors=None, name="Relax", doc=None):
    doc = _document(doc)
    obj = doc.addObject("Mesh::FeaturePython", name)
    Relax(obj, base)
    obj.Iterations, obj.Strength = iterations, strength
    if anchors:
        obj.Anchors = list(anchors)
    return _finish(obj, ViewProviderRelax, hide=[base])
