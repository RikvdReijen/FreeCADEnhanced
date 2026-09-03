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
"""Commit a sketch scene into the active FreeCAD document.

What each kind becomes
----------------------

=========== ================================================================
curve       ``Draft.make_bspline`` through the control points when the curve
            is smooth, ``Draft.make_wire`` when every segment is straight,
            and ``Draft.make_bezcurve`` when it is available and the curve
            really is a Bezier chain — the same order of preference as
            :mod:`xrpaint.to_freecad`.
primitive   a *parametric* ``Part::Box`` / ``Sphere`` / ``Cylinder`` /
            ``Cone`` / ``Torus`` / ``Plane`` feature with its placement, so
            it stays editable in the tree.  ``tube`` has no parametric
            counterpart and becomes a ``Part::Feature`` sweep.
cage        the Catmull-Clark limit surface as a ``Mesh::Feature`` (default)
            or a ``Part::Feature`` shell of polygons.  A subdivision surface
            is not a B-spline surface, so nothing here claims it is.
surface     ``Part`` geometry through :func:`xrsketch.surfacing.to_part`
            where the mapping is faithful, and a mesh otherwise — with a
            message saying which happened.
image       skipped: a reference image is not document geometry.
measure     skipped by default; ``measurements=True`` writes them as
            ``App::MeasureDistance`` objects where that type exists.
=========== ================================================================

Layers become nested ``App::DocumentObjectGroup`` objects with the same names,
so the sketch's organisation survives the trip.  Every FreeCAD import happens
inside a function (ARCHITECTURE.md §6).

Units: the scene is in **metres** (the headset's units) and FreeCAD documents
are normally in millimetres, so everything is multiplied by ``scale``, 1000 by
default.
"""

import math

from . import vecmath as vm

__all__ = [
    "CommitResult",
    "commit",
    "is_available",
    "missing_reason",
]

DEFAULT_SCALE = 1000.0


def _try_import(name):
    try:
        return __import__(name), None
    except Exception as exc:                     # pragma: no cover - host
        return None, "%s: %s" % (name, exc)


def is_available():
    """True when FreeCAD (and Part) can be imported here."""
    return missing_reason() is None


def missing_reason():
    """``None`` when a commit is possible, else a human readable reason."""
    fc, err = _try_import("FreeCAD")
    if fc is None:
        return ("FreeCAD is not importable in this interpreter, so a sketch "
                "cannot be committed to a document (%s)" % err)
    part, err = _try_import("Part")
    if part is None:
        return ("the Part module is not available, so sketch geometry cannot "
                "be created (%s)" % err)
    return None


class CommitResult(object):
    """What :func:`commit` produced."""

    __slots__ = ("objects", "groups", "messages", "document", "skipped")

    def __init__(self, objects=None, groups=None, messages=None,
                 document=None, skipped=None):
        self.objects = list(objects or [])
        self.groups = dict(groups or {})
        self.messages = list(messages or [])
        self.document = document
        self.skipped = list(skipped or [])

    def __iter__(self):
        return iter(self.objects)

    def __len__(self):
        return len(self.objects)

    def __repr__(self):
        return "CommitResult(%d objects, %d skipped)" % (len(self.objects),
                                                         len(self.skipped))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _placement(FreeCAD, transform, scale):
    """A FreeCAD placement from a similarity transform."""
    t = transform.translation
    q = transform.rotation
    return FreeCAD.Placement(
        FreeCAD.Vector(t[0] * scale, t[1] * scale, t[2] * scale),
        FreeCAD.Rotation(q[0], q[1], q[2], q[3]))


def _points(FreeCAD, points, transform, scale):
    out = []
    for p in points:
        w = transform.apply(p)
        out.append(FreeCAD.Vector(w[0] * scale, w[1] * scale, w[2] * scale))
    return out


def _safe_name(name):
    out = "".join(c if c.isalnum() else "_" for c in str(name))
    if not out or out[0].isdigit():
        out = "X" + out
    return out


def _is_straight(curve, tol=1e-9):
    for bez in curve.to_beziers():
        p0, c1, c2, p3 = bez
        d = vm.sub(p3, p0)
        L = vm.length(d)
        if L < tol:
            if vm.dist(c1, p0) > tol or vm.dist(c2, p0) > tol:
                return False
            continue
        for c in (c1, c2):
            w = vm.sub(c, p0)
            if vm.length(vm.cross(w, d)) / L > tol * max(1.0, L):
                return False
            t = vm.dot(w, d) / (L * L)
            if t < -tol or t > 1.0 + tol:
                return False
    return True


# --------------------------------------------------------------------------
# commit
# --------------------------------------------------------------------------

def commit(scene, document=None, scale=DEFAULT_SCALE, group_name="VR Sketch",
           cage_mode="mesh", subdivision=2, measurements=False,
           recompute=True):
    """Write a :class:`xrsketch.scene.Scene` into a FreeCAD document."""
    reason = missing_reason()
    if reason:
        raise RuntimeError(reason)
    import FreeCAD

    doc = document
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument("VRSketch")
    if doc is None or not hasattr(doc, "addObject"):
        raise RuntimeError("there is no FreeCAD document to commit into")
    scale = float(scale)

    result = CommitResult(document=doc)
    groups = {}

    def group_for(layer_id):
        if layer_id is None:
            return root_group
        if layer_id in groups:
            return groups[layer_id]
        layer = scene.layer(layer_id)
        if layer is None:
            return root_group
        grp = doc.addObject("App::DocumentObjectGroup",
                            _safe_name(layer.name))
        try:
            grp.Label = layer.name
        except Exception:
            pass
        groups[layer_id] = grp
        parent = group_for(layer.parent) if layer.parent else root_group
        _add_to_group(parent, grp)
        return grp

    root_group = doc.addObject("App::DocumentObjectGroup",
                               _safe_name(group_name))
    try:
        root_group.Label = group_name
    except Exception:
        pass
    result.groups["__root__"] = root_group

    for obj in scene.objects:
        try:
            made = _commit_object(FreeCAD, doc, obj, scale, cage_mode,
                                  subdivision, measurements, result)
        except Exception as exc:                 # pragma: no cover - host
            result.messages.append("%s not committed: %s" % (obj.name, exc))
            result.skipped.append(obj.name)
            continue
        if made is None:
            result.skipped.append(obj.name)
            continue
        for feature in (made if isinstance(made, list) else [made]):
            try:
                feature.Label = obj.name
            except Exception:
                pass
            _add_to_group(group_for(obj.layer), feature)
            result.objects.append(feature)

    result.groups.update(groups)
    if recompute:
        try:
            doc.recompute()
        except Exception as exc:                 # pragma: no cover - host
            result.messages.append("recompute failed: %s" % exc)
    return result


def _add_to_group(group, obj):
    try:
        group.addObject(obj)
    except Exception:
        try:
            group.Group = list(group.Group) + [obj]
        except Exception:
            pass


def _commit_object(FreeCAD, doc, obj, scale, cage_mode, subdivision,
                   measurements, result):
    kind = obj.kind
    if kind == "curve":
        return _commit_curve(FreeCAD, doc, obj, scale, result)
    if kind == "primitive":
        return _commit_primitive(FreeCAD, doc, obj, scale, result)
    if kind == "cage":
        return _commit_cage(FreeCAD, doc, obj, scale, cage_mode, subdivision,
                            result)
    if kind == "surface":
        return _commit_surface(FreeCAD, doc, obj, scale, result)
    if kind == "image":
        result.messages.append(
            "%s is a reference image, not geometry — not committed"
            % obj.name)
        return None
    if kind == "measure":
        if not measurements:
            return None
        return _commit_measurement(FreeCAD, doc, obj, scale, result)
    return None


def _commit_curve(FreeCAD, doc, obj, scale, result):
    curve = obj.data
    if len(curve.points) < 2:
        result.messages.append("%s has too few points" % obj.name)
        return None
    Draft, err = _try_import("Draft")
    pts = _points(FreeCAD, [cp.position for cp in curve.points],
                  obj.transform, scale)
    if Draft is not None:
        if _is_straight(curve):
            return Draft.make_wire(pts, closed=curve.closed)
        maker = getattr(Draft, "make_bezcurve", None)
        if maker is not None:
            try:
                return maker(pts, closed=curve.closed, degree=3)
            except Exception as exc:
                result.messages.append("%s: make_bezcurve failed (%s), "
                                       "falling back to a B-spline"
                                       % (obj.name, exc))
        return Draft.make_bspline(pts, closed=curve.closed)
    import Part
    result.messages.append("Draft is unavailable (%s); %s became a plain "
                           "Part B-spline" % (err, obj.name))
    spline = Part.BSplineCurve()
    spline.interpolate(pts, PeriodicFlag=bool(curve.closed))
    feature = doc.addObject("Part::Feature", _safe_name(obj.name))
    feature.Shape = spline.toShape()
    return feature


_PART_TYPES = {
    "box": "Part::Box",
    "sphere": "Part::Sphere",
    "cylinder": "Part::Cylinder",
    "cone": "Part::Cone",
    "torus": "Part::Torus",
    "plane": "Part::Plane",
}

#: primitives are +Y aligned (ARCHITECTURE.md §2) while ``Part`` builds them
#: along +Z, so the placement gets this correction
_Y_TO_Z = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))


def _commit_primitive(FreeCAD, doc, obj, scale, result):
    prim = obj.data
    ptype = _PART_TYPES.get(prim.kind)
    placement = obj.transform
    if ptype is None:
        return _commit_mesh_shape(FreeCAD, doc, obj, scale, result,
                                  "%s has no parametric Part feature; it "
                                  "became a Part::Feature shell" % prim.kind)
    feature = doc.addObject(ptype, _safe_name(obj.name))
    p = prim.params
    axis_fix = False
    if prim.kind == "box":
        sx, sy, sz = p["size"]
        feature.Length = sx * scale
        feature.Width = sy * scale
        feature.Height = sz * scale
        placement = vm.compose(placement,
                               vm.Transform(vm.mul((-sx, -sy, -sz), 0.5)))
    elif prim.kind == "sphere":
        feature.Radius = p["radius"] * scale
    elif prim.kind == "cylinder":
        feature.Radius = p["radius"] * scale
        feature.Height = p["height"] * scale
        axis_fix = True
    elif prim.kind == "cone":
        feature.Radius1 = p["radius"] * scale
        feature.Radius2 = p["top_radius"] * scale
        feature.Height = p["height"] * scale
        axis_fix = True
    elif prim.kind == "torus":
        feature.Radius1 = p["radius"] * scale
        feature.Radius2 = p["tube_radius"] * scale
        axis_fix = True
    elif prim.kind == "plane":
        sx, sy = p["size"]
        feature.Length = sx * scale
        feature.Width = sy * scale
        placement = vm.compose(placement,
                               vm.Transform(vm.mul((-sx, -sy, 0.0), 0.5)))
    if axis_fix:
        centre = vm.Transform((0.0, 0.0, 0.0), _Y_TO_Z, 1.0)
        placement = vm.compose(placement, centre)
        if prim.kind in ("cylinder", "cone"):
            placement = vm.compose(
                placement, vm.Transform((0.0, 0.0, -0.5 * p["height"])))
    feature.Placement = _placement(FreeCAD, placement, scale)
    return feature


def _commit_cage(FreeCAD, doc, obj, scale, cage_mode, subdivision, result):
    cage = obj.data
    surface = cage.limit_surface(max(0, int(subdivision)))
    verts = [obj.transform.apply(v) for v in surface.vertices]
    verts = [(v[0] * scale, v[1] * scale, v[2] * scale) for v in verts]
    if cage_mode == "shape":
        import Part
        faces = []
        for face in surface.faces:
            poly = [FreeCAD.Vector(*verts[i]) for i in face]
            poly.append(poly[0])
            faces.append(Part.Face(Part.makePolygon(poly)))
        feature = doc.addObject("Part::Feature", _safe_name(obj.name))
        feature.Shape = Part.makeShell(faces)
        result.messages.append(
            "%s was committed as a polygon shell: a Catmull-Clark limit "
            "surface is not a B-spline surface, so there is no exact Part "
            "equivalent" % obj.name)
        return feature
    import Mesh
    flat = []
    for tri in surface.triangles():
        for i in tri:
            flat.append(FreeCAD.Vector(*verts[i]))
    feature = doc.addObject("Mesh::Feature", _safe_name(obj.name))
    feature.Mesh = Mesh.Mesh(flat)
    return feature


def _commit_surface(FreeCAD, doc, obj, scale, result):
    from . import surfacing as _surfacing
    surface = obj.data
    try:
        shape = _surfacing.to_part(surface)
    except _surfacing.UnsupportedMapping as exc:
        return _commit_mesh_shape(FreeCAD, doc, obj, scale, result, str(exc))
    feature = doc.addObject("Part::Feature", _safe_name(obj.name))
    try:
        shape.scale(scale)
    except Exception:
        pass
    feature.Shape = shape
    feature.Placement = _placement(FreeCAD, obj.transform, scale)
    return feature


def _commit_mesh_shape(FreeCAD, doc, obj, scale, result, message):
    import Mesh
    data = obj.data
    if hasattr(data, "triangles") and hasattr(data, "points"):
        pts = data.points()
        tris = data.triangles()
    else:
        positions, _n, _uv, indices = data.mesh()
        pts = [tuple(positions[i:i + 3]) for i in range(0, len(positions), 3)]
        tris = [tuple(indices[i:i + 3]) for i in range(0, len(indices), 3)]
    flat = []
    for tri in tris:
        for i in tri:
            w = obj.transform.apply(pts[i])
            flat.append(FreeCAD.Vector(w[0] * scale, w[1] * scale,
                                       w[2] * scale))
    feature = doc.addObject("Mesh::Feature", _safe_name(obj.name))
    feature.Mesh = Mesh.Mesh(flat)
    result.messages.append("%s: %s" % (obj.name, message))
    return feature


def _commit_measurement(FreeCAD, doc, obj, scale, result):
    m = obj.data
    if m.kind != "distance" or len(m.points) < 2:
        result.messages.append("%s: only distance measurements can be "
                               "committed" % obj.name)
        return None
    try:
        feature = doc.addObject("App::MeasureDistance", _safe_name(obj.name))
    except Exception as exc:
        result.messages.append("%s: no measurement object in this FreeCAD "
                               "(%s)" % (obj.name, exc))
        return None
    a, b = m.points[0], m.points[1]
    feature.P1 = FreeCAD.Vector(*a)
    feature.P2 = FreeCAD.Vector(*b)
    return feature
