# SPDX-License-Identifier: LGPL-2.1-or-later
"""The bridge between a real FreeCAD document and the document model.

Three jobs, all optional at import time — this module imports cleanly
without FreeCAD, and :func:`freecad_available` says whether the rest works:

* :func:`document_model` — take a :class:`collab.model.DocumentModel`
  snapshot of an ``App.Document``: the feature tree, its parameters, and the
  faces and edges of every shape-bearing feature with the attributes anchors
  need (surface type, normal, area, centroid, adjacency).
* :func:`materialise` — bring an ``App.Document`` into line with a document
  model after a replay: set parameters, add and remove features, reorder a
  body. Covers the operations of SPEC §3 for PartDesign bodies; what it
  cannot express it reports, rather than approximating.
* :class:`FreeCADEvaluator` — the real evaluator: recompute, mass
  properties, bounding box, shape validity.

Entity names take the form ``<Feature>.Face<N>``: the feature prefix is what
makes the names unique across a document, and it is *still* volatile —
``Pad3.Face6`` can become ``Pad3.Face9`` after an upstream edit exactly as
``Face6`` can. Nothing in this module relies on them.
"""

import hashlib
import os

from .evaluate import Evaluator, GeometryIssue, RecomputeResult, check_structure
from .model import DocumentModel, Entity, Feature

#: FreeCAD properties that are read into ``Feature.params``. Anything else
#: numeric is ignored; this is the set a layer may set with ``set_param``.
PARAM_PROPERTIES = (
    "Length",
    "Length2",
    "Radius",
    "Radius1",
    "Radius2",
    "Angle",
    "Angle2",
    "Offset",
    "Size",
    "Thickness",
    "Height",
    "Width",
    "Depth",
    "Occurrences",
    "Midplane",
    "Reversed",
    "Type",
)

#: Properties read into ``Feature.properties`` (never geometric).
META_PROPERTIES = ("Label", "Label2", "Visibility")

_SURFACES = {
    "Plane": "plane",
    "Cylinder": "cylinder",
    "Cone": "cone",
    "Sphere": "sphere",
    "Toroid": "torus",
    "Torus": "torus",
    "BSplineSurface": "bspline",
    "BezierSurface": "bezier",
    "SurfaceOfRevolution": "revolution",
    "SurfaceOfExtrusion": "extrusion",
    "OffsetSurface": "offset",
}
_CURVES = {
    "Line": "line",
    "LineSegment": "line",
    "Circle": "circle",
    "ArcOfCircle": "circle",
    "Ellipse": "ellipse",
    "ArcOfEllipse": "ellipse",
    "BSplineCurve": "bspline",
    "BezierCurve": "bezier",
}


def freecad_available():
    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        return False
    return True


def _app():
    import FreeCAD

    return FreeCAD


def revision_of(path):
    """A short content hash of the document file: the ``base`` a layer records."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def _kind(obj):
    type_id = getattr(obj, "TypeId", "") or ""
    return type_id.split("::")[-1] if "::" in type_id else type_id or type(obj).__name__


def _value(obj, name):
    value = getattr(obj, name, None)
    if value is None:
        return None
    # Quantity-like: has .Value
    inner = getattr(value, "Value", None)
    if isinstance(inner, (int, float)):
        return inner
    if isinstance(value, (bool, int, float, str)):
        return value
    return None


def _feature_of(obj):
    params, properties = {}, {}
    for name in PARAM_PROPERTIES:
        if name in getattr(obj, "PropertiesList", ()) or hasattr(obj, name):
            value = _value(obj, name)
            if value is not None:
                params[name] = value
    for name in META_PROPERTIES:
        value = _value(obj, name)
        if value is not None:
            properties[name] = value
    depends = [o.Name for o in getattr(obj, "OutList", ()) or () if getattr(o, "Name", None)]
    return Feature(obj.Name, _kind(obj), params=params, properties=properties, depends_on=depends)


def _vec(v):
    return (float(v.x), float(v.y), float(v.z))


def _entities_of(obj):
    """Faces and edges of ``obj.Shape``; empty for objects without one."""
    shape = getattr(obj, "Shape", None)
    if shape is None or getattr(shape, "isNull", lambda: True)():
        return []
    entities = []
    faces = list(getattr(shape, "Faces", ()) or ())
    edges = list(getattr(shape, "Edges", ()) or ())

    # Adjacency: faces sharing an edge. Edge identity via hashCode().
    edge_faces = {}
    for fi, face in enumerate(faces):
        for edge in getattr(face, "Edges", ()) or ():
            key = edge.hashCode() if hasattr(edge, "hashCode") else id(edge)
            edge_faces.setdefault(key, set()).add(fi)
    for fi, face in enumerate(faces):
        neighbours = set()
        for edge in getattr(face, "Edges", ()) or ():
            key = edge.hashCode() if hasattr(edge, "hashCode") else id(edge)
            neighbours |= edge_faces.get(key, set())
        neighbours.discard(fi)
        surface = _SURFACES.get(type(getattr(face, "Surface", None)).__name__, "unknown")
        normal = None
        if surface == "plane":
            try:
                u0, u1, v0, v1 = face.ParameterRange
                normal = _vec(face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0))
            except Exception:  # pragma: no cover - depends on OCC state
                normal = None
        entities.append(
            Entity(
                f"{obj.Name}.Face{fi + 1}",
                "face",
                obj.Name,
                surface,
                normal=normal,
                area=float(getattr(face, "Area", 0.0)),
                centroid_local=_vec(face.CenterOfMass),
                adjacency=len(neighbours),
            )
        )
    for ei, edge in enumerate(edges):
        curve = _CURVES.get(type(getattr(edge, "Curve", None)).__name__, "unknown")
        entities.append(
            Entity(
                f"{obj.Name}.Edge{ei + 1}",
                "edge",
                obj.Name,
                curve,
                length=float(getattr(edge, "Length", 0.0)),
                centroid_local=_vec(edge.CenterOfMass),
                adjacency=2,
                between=(obj.Name,),
            )
        )
    return entities


def _parameters_of(doc):
    """Spreadsheet aliases and VarSet properties, as ``name -> value``."""
    parameters = {}
    for obj in getattr(doc, "Objects", ()) or ():
        kind = _kind(obj)
        if kind == "Sheet":
            cells = getattr(obj, "cells", None)
            used = getattr(cells, "getUsedCells", lambda: [])()
            for cell in used or []:
                alias = getattr(obj, "getAlias", lambda c: None)(cell)
                if alias:
                    try:
                        parameters[alias] = obj.get(cell)
                    except Exception:  # pragma: no cover
                        continue
        elif kind == "VarSet":
            for name in getattr(obj, "PropertiesList", ()) or ():
                if name in ("Label", "Label2", "ExpressionEngine", "Visibility", "Proxy"):
                    continue
                value = _value(obj, name)
                if value is not None:
                    parameters[name] = value
    return parameters


def feature_order(doc):
    """Objects in tree order: for each PartDesign body, its Group order."""
    ordered, seen = [], set()
    for obj in getattr(doc, "Objects", ()) or ():
        if _kind(obj) == "Body":
            for child in getattr(obj, "Group", ()) or ():
                if child.Name not in seen:
                    ordered.append(child)
                    seen.add(child.Name)
    for obj in getattr(doc, "Objects", ()) or ():
        if obj.Name not in seen and _kind(obj) not in ("Body", "Origin", "App::Origin"):
            ordered.append(obj)
            seen.add(obj.Name)
    return ordered


def document_model(doc, revision=None, with_entities=True):
    """Snapshot ``doc`` (an ``App.Document``) as a :class:`DocumentModel`."""
    filename = getattr(doc, "FileName", "") or ""
    if revision is None:
        revision = revision_of(filename) if filename and os.path.isfile(filename) else ""
    features, entities = [], []
    for obj in feature_order(doc):
        if _kind(obj) in ("Origin", "Line", "Plane", "Point") and getattr(obj, "Role", None):
            continue  # origin planes/axes: implicit datums, not tree features
        features.append(_feature_of(obj))
        if with_entities:
            entities.extend(_entities_of(obj))
    return DocumentModel(
        revision=revision,
        document=os.path.basename(filename),
        features=features,
        entities=entities,
        parameters=_parameters_of(doc),
    )


# ---------------------------------------------------------------------------
# materialise
# ---------------------------------------------------------------------------


class MaterialiseReport:
    def __init__(self):
        self.applied = []
        self.unsupported = []
        self.errors = []

    @property
    def ok(self):
        return not self.errors and not self.unsupported

    def to_json(self):
        return {"applied": self.applied, "unsupported": self.unsupported, "errors": self.errors}


def _body_of(doc, obj):
    for parent in getattr(obj, "InList", ()) or ():
        if _kind(parent) == "Body":
            return parent
    return None


def _first_body(doc):
    for obj in getattr(doc, "Objects", ()) or ():
        if _kind(obj) == "Body":
            return obj
    return None


def _set_param(obj, name, value):
    current = getattr(obj, name)
    inner = getattr(current, "Value", None)
    if inner is not None and not isinstance(current, (int, float, str, bool)):
        setattr(obj, name, type(current)(value) if callable(type(current)) else value)
    else:
        setattr(obj, name, value)


def _add_sketch_geometry(sketch, geometry, report):
    Part = __import__("Part")
    App = _app()
    for item in geometry or []:
        kind = item.get("type") if isinstance(item, dict) else None
        try:
            if kind == "circle":
                cx, cy = item["center"]
                sketch.addGeometry(Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), float(item["radius"])), False)
            elif kind == "line":
                (x0, y0), (x1, y1) = item["start"], item["end"]
                sketch.addGeometry(Part.LineSegment(App.Vector(x0, y0, 0), App.Vector(x1, y1, 0)), False)
            elif kind == "rectangle":
                (x0, y0), (x1, y1) = item["corner"], item["opposite"]
                pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                for i in range(4):
                    a, b = pts[i], pts[(i + 1) % 4]
                    sketch.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), False)
            else:
                report.unsupported.append(f"sketch geometry {kind!r}")
        except Exception as exc:
            report.errors.append(f"sketch geometry {kind!r}: {exc}")


def _resolve_face(doc, model, ref):
    """``Pad3.Face6`` -> (object, "Face6"); a datum name -> (object, "")."""
    if "." in ref:
        owner, sub = ref.rsplit(".", 1)
    else:
        owner, sub = ref, ""
    obj = doc.getObject(owner)
    if obj is None:
        return None, None
    return obj, sub


def materialise(model, doc, report=None):
    """Make ``doc`` match ``model``.

    Sets parameters and metadata on existing objects, removes objects the
    model no longer has, creates datums and PartDesign features the model
    added, and reorders bodies to the model's order. Anything the FreeCAD
    API cannot express from the recorded data is listed in
    ``report.unsupported`` — sketch *constraints* in particular are not
    replayed, which is deliberate (README §2, level 4).
    """
    App = _app()
    report = report or MaterialiseReport()
    model_names = {f.name for f in model.features}

    # Remove.
    for obj in list(feature_order(doc)):
        if obj.Name not in model_names and _kind(obj) not in ("Sheet", "VarSet"):
            try:
                doc.removeObject(obj.Name)
                report.applied.append(f"removed {obj.Name}")
            except Exception as exc:
                report.errors.append(f"remove {obj.Name}: {exc}")

    # Add and update, in model order.
    body = _first_body(doc)
    previous = None
    for feature in model.features:
        obj = doc.getObject(feature.name)
        if obj is None:
            obj = _create(doc, body, feature, model, previous, report)
            if obj is None:
                previous = None
                continue
        for name, value in feature.params.items():
            if name == "sketch" or not hasattr(obj, name):
                continue
            try:
                if _value(obj, name) != value:
                    _set_param(obj, name, value)
                    report.applied.append(f"{feature.name}.{name} = {value!r}")
            except Exception as exc:
                report.errors.append(f"{feature.name}.{name}: {exc}")
        for name, value in feature.properties.items():
            if hasattr(obj, name):
                try:
                    if _value(obj, name) != value:
                        setattr(obj, name, value)
                        report.applied.append(f"{feature.name}.{name} = {value!r}")
                except Exception as exc:
                    report.errors.append(f"{feature.name}.{name}: {exc}")
        previous = obj

    # Reorder within the body.
    if body is not None and hasattr(body, "Group"):
        wanted = [doc.getObject(f.name) for f in model.features]
        wanted = [o for o in wanted if o is not None and _body_of(doc, o) is body]
        current = list(body.Group)
        if [o.Name for o in current] != [o.Name for o in wanted]:
            try:
                body.Group = wanted
                report.applied.append("reordered " + ", ".join(o.Name for o in wanted))
            except Exception as exc:
                report.errors.append(f"reorder: {exc}")

    # Parameters.
    for obj in getattr(doc, "Objects", ()) or ():
        if _kind(obj) == "VarSet":
            for name, value in model.parameters.items():
                if hasattr(obj, name) and _value(obj, name) != value:
                    try:
                        _set_param(obj, name, value)
                        report.applied.append(f"param {name} = {value!r}")
                    except Exception as exc:
                        report.errors.append(f"param {name}: {exc}")
        elif _kind(obj) == "Sheet":
            for name, value in model.parameters.items():
                cell = getattr(obj, "getCellFromAlias", lambda a: None)(name)
                if cell:
                    try:
                        obj.set(cell, str(value))
                        report.applied.append(f"param {name} = {value!r}")
                    except Exception as exc:
                        report.errors.append(f"param {name}: {exc}")
    return report


def _create(doc, body, feature, model, previous, report):
    App = _app()
    kind = feature.kind
    try:
        if kind.startswith("Datum"):
            type_id = "PartDesign::" + ("Plane" if "Plane" in kind else "Line" if "Line" in kind else "Point")
            obj = body.newObject(type_id, feature.name) if body is not None else doc.addObject(type_id, feature.name)
            normal = feature.params.get("normal")
            origin = feature.params.get("origin", (0, 0, 0))
            if normal is not None and hasattr(obj, "Placement"):
                rotation = App.Rotation(App.Vector(0, 0, 1), App.Vector(*normal))
                obj.Placement = App.Placement(App.Vector(*origin), rotation)
            report.applied.append(f"added datum {feature.name}")
            return obj
        if kind in ("Pad", "Pocket", "Revolution", "Groove", "Hole"):
            if body is None:
                report.unsupported.append(f"{feature.name}: a {kind} needs a PartDesign body")
                return None
            sketch_spec = feature.params.get("sketch")
            sketch = None
            if sketch_spec:
                sketch = body.newObject("Sketcher::SketchObject", feature.name + "Sketch")
                plane = sketch_spec.get("plane")
                if plane:
                    support, sub = _resolve_face(doc, model, plane)
                    if support is None:
                        report.errors.append(f"{feature.name}: sketch plane {plane!r} not found")
                    else:
                        attr = "AttachmentSupport" if hasattr(sketch, "AttachmentSupport") else "Support"
                        setattr(sketch, attr, [(support, sub)] if sub else [(support, "")])
                        sketch.MapMode = "FlatFace"
                _add_sketch_geometry(sketch, sketch_spec.get("geometry"), report)
                if sketch_spec.get("constraints"):
                    report.unsupported.append(f"{feature.name}: sketch constraints are not replayed (level 4)")
            obj = body.newObject("PartDesign::" + kind, feature.name)
            if sketch is not None:
                obj.Profile = sketch
                if hasattr(sketch, "Visibility"):
                    sketch.Visibility = False
            report.applied.append(f"added {kind} {feature.name}")
            return obj
        if kind == "Fillet" or kind == "Chamfer":
            report.unsupported.append(f"{feature.name}: {kind} needs edge references the layer does not carry")
            return None
        report.unsupported.append(f"{feature.name}: cannot create a {kind}")
        return None
    except Exception as exc:
        report.errors.append(f"create {feature.name}: {exc}")
        return None


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------


class FreeCADEvaluator(Evaluator):
    """Evaluate document models by materialising them into a FreeCAD document.

    ``doc`` is the ``App.Document`` to evaluate in — normally a scratch copy
    of the project document, never the one the user has open: replaying a
    candidate merge is exactly the kind of thing that belongs in a worktree.

    ``density_g_cm3`` turns volume into mass for the ``mass_g`` metric; a
    ``density_g_cm3`` document parameter overrides it.
    """

    capabilities = frozenset({"recompute", "metrics", "self_intersection", "bounding_box"})
    name = "freecad"

    def __init__(self, doc, density_g_cm3=2.70):
        self.doc = doc
        self.density = density_g_cm3
        self.last_report = None

    def _tip_shape(self):
        body = _first_body(self.doc)
        tip = getattr(body, "Tip", None) if body is not None else None
        shape = getattr(tip, "Shape", None) if tip is not None else None
        if shape is None or shape.isNull():
            for obj in reversed(list(getattr(self.doc, "Objects", ()) or ())):
                candidate = getattr(obj, "Shape", None)
                if candidate is not None and not candidate.isNull():
                    return candidate
        return shape

    def recompute(self, model):
        errors, warnings = check_structure(model)
        if errors:
            return RecomputeResult(False, errors, warnings, evaluated=False)
        report = materialise(model, self.doc)
        self.last_report = report
        errors.extend(report.errors)
        warnings.extend("not materialised: " + u for u in report.unsupported)
        try:
            self.doc.recompute()
        except Exception as exc:
            return RecomputeResult(False, errors + [f"recompute raised: {exc}"], warnings)
        for obj in getattr(self.doc, "Objects", ()) or ():
            state = getattr(obj, "State", ()) or ()
            if "Invalid" in state or "Error" in state:
                errors.append(f"{obj.Name} failed to recompute")
        return RecomputeResult(not errors, errors, warnings, evaluated=not report.unsupported)

    def metrics(self, model):
        shape = self._tip_shape()
        if shape is None:
            return {}
        density = model.parameters.get("density_g_cm3", self.density)
        volume_mm3 = float(shape.Volume)
        out = {"volume_mm3": volume_mm3, "mass_g": volume_mm3 / 1000.0 * float(density), "area_mm2": float(shape.Area)}
        try:
            com = shape.CenterOfMass
            out["cog_x"], out["cog_y"], out["cog_z"] = float(com.x), float(com.y), float(com.z)
        except Exception:  # pragma: no cover
            pass
        return out

    def geometry_issues(self, model):
        shape = self._tip_shape()
        if shape is None:
            return None
        issues = []
        try:
            if not shape.isValid():
                issues.append(GeometryIssue("invalid_shape", "result shape is not valid (BRepCheck)", []))
        except Exception as exc:  # pragma: no cover
            issues.append(GeometryIssue("check_failed", f"validity check raised: {exc}", []))
        solids = list(getattr(shape, "Solids", ()) or ())
        if len(solids) > 1:
            issues.append(GeometryIssue("multiple_solids", f"result has {len(solids)} disjoint solids", [], len(solids)))
        return issues

    def bounding_box(self, model):
        shape = self._tip_shape()
        if shape is None:
            return None
        bb = shape.BoundBox
        return ((bb.XMin, bb.YMin, bb.ZMin), (bb.XMax, bb.YMax, bb.ZMax))
