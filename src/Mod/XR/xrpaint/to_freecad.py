# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Commit a VR vector document into the active FreeCAD document.

Bezier paths become Draft objects (``make_wire`` for straight runs,
``make_bezcurve``/``make_bspline`` for curved ones), closed filled paths become
``Part.Face`` features, and everything is placed on the document's working
plane and collected into a group.  ``target: "sketch"`` paths instead land in a
``Sketcher::SketchObject`` as ``Part.BSplineCurve`` segments.

Every FreeCAD import happens inside a function (ARCHITECTURE.md §6); when the
Draft workbench is missing the module degrades with a clear message instead of
failing at import time.
"""

import math

__all__ = [
    "CommitResult",
    "commit",
    "commit_strokes3d",
    "is_available",
    "missing_reason",
    "path_to_points",
]

_LINE_TOL = 1e-7


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------

def _try_import(name):
    try:
        return __import__(name), None
    except Exception as exc:      # pragma: no cover - depends on the host
        return None, "%s: %s" % (name, exc)


def is_available():
    """True when FreeCAD *and* Draft can be imported here."""
    return missing_reason() is None


def missing_reason():
    """``None`` when everything is available, else a human readable reason."""
    fc, err = _try_import("FreeCAD")
    if fc is None:
        return ("FreeCAD is not importable in this interpreter, so a vector "
                "document cannot be committed to a document (%s)" % err)
    draft, err = _try_import("Draft")
    if draft is None:
        return ("the Draft workbench is not available, so vector paths cannot "
                "be converted into document geometry (%s)" % err)
    return None


class CommitResult(object):
    """What :func:`commit` produced."""

    __slots__ = ("objects", "group", "messages", "document")

    def __init__(self, objects=None, group=None, messages=None,
                 document=None):
        self.objects = list(objects or [])
        self.group = group
        self.messages = list(messages or [])
        self.document = document

    def __iter__(self):
        return iter(self.objects)

    def __len__(self):
        return len(self.objects)

    def __repr__(self):
        return "CommitResult(%d objects)" % (len(self.objects),)


# --------------------------------------------------------------------------
# geometry helpers (pure, testable)
# --------------------------------------------------------------------------

def _is_straight_segment(bez, tol=_LINE_TOL):
    """True when a cubic is the straight line between its endpoints."""
    p0, c1, c2, p3 = bez
    dx = p3[0] - p0[0]
    dy = p3[1] - p0[1]
    L = math.hypot(dx, dy)
    if L < tol:
        return (abs(c1[0] - p0[0]) <= tol and abs(c1[1] - p0[1]) <= tol
                and abs(c2[0] - p0[0]) <= tol and abs(c2[1] - p0[1]) <= tol)
    for c in (c1, c2):
        wx = c[0] - p0[0]
        wy = c[1] - p0[1]
        if abs(wx * dy - wy * dx) / L > tol * max(1.0, L):
            return False
        t = (wx * dx + wy * dy) / (L * L)
        if t < -tol or t > 1.0 + tol:
            return False
    return True


def is_polyline(path, tol=_LINE_TOL):
    """True when every segment of ``path`` is a straight line."""
    segs = path.to_beziers()
    if not segs:
        return True
    return all(_is_straight_segment(b, tol) for b in segs)


def path_to_points(path, plane, scale=1.0, flatten_tol=0.05):
    """3D points (in FreeCAD mm) approximating a path on ``plane``."""
    if is_polyline(path):
        pts2 = [n.point for n in path.nodes]
        if path.closed and pts2:
            pts2 = list(pts2)
    else:
        pts2 = path.flatten(flatten_tol)
        if path.closed and len(pts2) > 1:
            pts2 = pts2[:-1]
    return [_to3(plane, p, scale) for p in pts2]


def _to3(plane, p2, scale):
    x, y, z = plane.to_world(p2)
    return (x * scale, y * scale, z * scale)


def _placement(plane, scale):
    """FreeCAD placement of the working plane (identity here: the points are
    already emitted in world coordinates)."""
    import FreeCAD
    o = plane.origin
    return FreeCAD.Placement(
        FreeCAD.Vector(o[0] * scale, o[1] * scale, o[2] * scale),
        FreeCAD.Rotation(plane.rotation[0], plane.rotation[1],
                         plane.rotation[2], plane.rotation[3]))


# --------------------------------------------------------------------------
# commit
# --------------------------------------------------------------------------

def commit(vector_document, document=None, group_name="XR Vector",
           flatten_tol=0.05, prefer_bezcurve=True, recompute=True,
           sketch_name="XRSketch"):
    """Create FreeCAD geometry for every path of ``vector_document``.

    Returns the list of created objects (also reachable, together with the
    group and any messages, on the returned object's ``objects`` attribute --
    the result is a :class:`CommitResult`, which iterates and lens like a
    list).

    Raises ``RuntimeError`` with an explanatory message when FreeCAD or Draft
    is not available.
    """
    reason = missing_reason()
    if reason is not None:
        raise RuntimeError("xrpaint.to_freecad.commit(): " + reason)

    import FreeCAD
    import Draft

    doc = document
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument("XR")

    plane = vector_document.plane
    scale = float(vector_document.unit_scale) * 1000.0   # doc units -> mm
    if scale <= 0.0:
        scale = 1.0

    result = CommitResult(document=doc)
    sketch_paths = []
    for path in vector_document.paths:
        if len(path.nodes) < 2:
            result.messages.append("path %r skipped: fewer than two nodes"
                                   % (path.id,))
            continue
        if path.target == "sketch":
            sketch_paths.append(path)
            continue
        obj = _commit_draft_path(doc, Draft, FreeCAD, path, plane, scale,
                                 flatten_tol, prefer_bezcurve, result)
        if obj is not None:
            result.objects.append(obj)

    if sketch_paths:
        sk = _commit_sketch(doc, FreeCAD, sketch_paths, plane, scale, result,
                            sketch_name)
        if sk is not None:
            result.objects.append(sk)

    if result.objects:
        try:
            group = doc.addObject("App::DocumentObjectGroup", group_name)
            group.Label = group_name
            for o in result.objects:
                group.addObject(o)
            result.group = group
        except Exception as exc:
            result.messages.append("could not group the objects: %s" % exc)
    if recompute:
        try:
            doc.recompute()
        except Exception as exc:
            result.messages.append("recompute failed: %s" % exc)
    return result


def _commit_draft_path(doc, Draft, FreeCAD, path, plane, scale, flatten_tol,
                       prefer_bezcurve, result):
    V = FreeCAD.Vector
    obj = None
    if is_polyline(path):
        pts = [V(*p) for p in path_to_points(path, plane, scale, flatten_tol)]
        if path.closed and len(pts) > 1 and (pts[0] - pts[-1]).Length < 1e-9:
            pts = pts[:-1]
        obj = Draft.make_wire(pts, closed=bool(path.closed))
    else:
        if prefer_bezcurve and hasattr(Draft, "make_bezcurve"):
            ctrl = _bezier_control_points(path)
            pts = [V(*_to3(plane, p, scale)) for p in ctrl]
            try:
                obj = Draft.make_bezcurve(pts, closed=bool(path.closed),
                                          degree=3)
            except Exception as exc:
                result.messages.append(
                    "make_bezcurve failed for %r (%s), falling back to a "
                    "B-spline" % (path.id, exc))
                obj = None
        if obj is None:
            pts = [V(*p) for p in path_to_points(path, plane, scale,
                                                 flatten_tol)]
            obj = Draft.make_bspline(pts, closed=bool(path.closed))
    if obj is None:
        result.messages.append("path %r produced no object" % (path.id,))
        return None
    try:
        obj.Label = str(path.id)
    except Exception:
        pass
    _apply_style(obj, path, result)
    if path.closed and path.fill is not None:
        face = _make_face(doc, FreeCAD, obj, path, result)
        if face is not None:
            return face
    return obj


def _bezier_control_points(path):
    """The control point sequence a Draft BezCurve of degree 3 expects."""
    segs = path.to_beziers()
    pts = [segs[0][0]]
    for b in segs:
        pts.extend([b[1], b[2], b[3]])
    if path.closed and len(pts) > 1:
        pts = pts[:-1]
    return pts


def _make_face(doc, FreeCAD, wire_obj, path, result):
    try:
        import Part
    except Exception as exc:
        result.messages.append("Part unavailable, %r stays a wire (%s)"
                               % (path.id, exc))
        return None
    try:
        shape = wire_obj.Shape
        wires = shape.Wires if shape.Wires else [shape]
        face = Part.Face(wires[0])
        feat = doc.addObject("Part::Feature", "%s_fill" % path.id)
        feat.Shape = face
        feat.Label = "%s (filled)" % path.id
        _apply_fill_colour(feat, path)
        try:
            doc.removeObject(wire_obj.Name)
        except Exception:
            pass
        return feat
    except Exception as exc:
        result.messages.append("could not build a face for %r: %s"
                               % (path.id, exc))
        return None


def _apply_style(obj, path, result):
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return
    try:
        if path.stroke:
            col = path.stroke.get("color")
            if col:
                vo.LineColor = (float(col[0]), float(col[1]), float(col[2]))
            w = path.stroke.get("width")
            if w:
                vo.LineWidth = max(1.0, float(w))
        if path.fill:
            col = path.fill.get("color")
            if col:
                vo.ShapeColor = (float(col[0]), float(col[1]), float(col[2]))
    except Exception as exc:
        result.messages.append("style not applied to %r: %s" % (path.id, exc))


def _apply_fill_colour(obj, path):
    vo = getattr(obj, "ViewObject", None)
    if vo is None or not path.fill:
        return
    col = path.fill.get("color")
    try:
        if col:
            vo.ShapeColor = (float(col[0]), float(col[1]), float(col[2]))
            if len(col) > 3:
                vo.Transparency = int((1.0 - float(col[3])) * 100)
    except Exception:
        pass


def _commit_sketch(doc, FreeCAD, paths, plane, scale, result, sketch_name):
    """Put every ``target: "sketch"`` path into one Sketcher object."""
    try:
        import Part
        import Sketcher  # noqa: F401  (registers the object type)
    except Exception as exc:
        result.messages.append(
            "Sketcher/Part unavailable, %d sketch path(s) skipped (%s)"
            % (len(paths), exc))
        return None
    try:
        sketch = doc.addObject("Sketcher::SketchObject", sketch_name)
    except Exception as exc:
        result.messages.append("could not create a sketch: %s" % exc)
        return None
    try:
        sketch.Placement = _placement(plane, scale)
    except Exception:
        pass
    V = FreeCAD.Vector
    added = 0
    for path in paths:
        for bez in path.to_beziers():
            try:
                if _is_straight_segment(bez):
                    a = V(bez[0][0] * scale, bez[0][1] * scale, 0.0)
                    b = V(bez[3][0] * scale, bez[3][1] * scale, 0.0)
                    if (b - a).Length < 1e-9:
                        continue
                    sketch.addGeometry(Part.LineSegment(a, b), False)
                else:
                    poles = [V(p[0] * scale, p[1] * scale, 0.0) for p in bez]
                    spline = Part.BSplineCurve()
                    spline.buildFromPolesMultsKnots(
                        poles, [4, 4], [0.0, 1.0], False, 3)
                    sketch.addGeometry(spline, False)
                added += 1
            except Exception as exc:
                result.messages.append(
                    "sketch segment of %r skipped: %s" % (path.id, exc))
    if added == 0:
        try:
            doc.removeObject(sketch.Name)
        except Exception:
            pass
        return None
    return sketch


# --------------------------------------------------------------------------
# 3d strokes
# --------------------------------------------------------------------------

def commit_strokes3d(strokes, document=None, name="XR Stroke", as_shape=False,
                     unit_scale=0.001, recompute=True):
    """Turn air-painted strokes into Mesh (or Part) objects in a document."""
    try:
        import FreeCAD
    except Exception as exc:
        raise RuntimeError(
            "FreeCAD is not importable, 3D strokes cannot be committed "
            "(%s)" % exc)
    doc = document or FreeCAD.ActiveDocument or FreeCAD.newDocument("XR")
    scale = float(unit_scale) * 1000.0
    out = []
    for i, stroke in enumerate(strokes):
        geo = stroke.build_geometry()
        if not geo.faces:
            continue
        if scale != 1.0:
            geo.vertices = [(v[0] * scale, v[1] * scale, v[2] * scale)
                            for v in geo.vertices]
        if as_shape:
            shape = stroke.to_part_shape(geo)
            if shape is None:
                continue
            obj = doc.addObject("Part::Feature", "%s_%d" % (name, i))
            obj.Shape = shape
        else:
            mesh = stroke.to_freecad_mesh(geo)
            obj = doc.addObject("Mesh::Feature", "%s_%d" % (name, i))
            obj.Mesh = mesh
        try:
            obj.ViewObject.ShapeColor = tuple(stroke.color[:3])
        except Exception:
            pass
        out.append(obj)
    if recompute:
        try:
            doc.recompute()
        except Exception:
            pass
    return out
