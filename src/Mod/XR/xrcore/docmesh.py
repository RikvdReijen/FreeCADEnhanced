# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared helpers for the feature bridges: documents, controllers, units.

Everything here converts between the document (millimetres, ``Placement``)
and the subsystems (metres, :class:`xrsketch.vecmath.Transform`), or reads
a controller into the ``(position, rotation)`` / ``(trigger, grip, x, y)``
tuples the sessions expect. Imports of FreeCAD and Coin happen inside the
functions so the module loads under the test stubs.
"""

from xrsketch import vecmath as vm

MM = 0.001


def placement_to_transform(placement):
    """A FreeCAD ``Placement`` (mm) as a Transform in metres."""
    base = placement.Base
    q = placement.Rotation.Q
    return vm.Transform((base.x * MM, base.y * MM, base.z * MM), (q[0], q[1], q[2], q[3]))


def transform_to_placement(transform):
    import FreeCAD

    t, q = transform.translation, transform.rotation
    return FreeCAD.Placement(FreeCAD.Vector(t[0] / MM, t[1] / MM, t[2] / MM), FreeCAD.Rotation(q[0], q[1], q[2], q[3]))


def local_shape(obj):
    """The object's shape with its placement removed (local frame, mm)."""
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    import FreeCAD

    local = shape.copy()
    local.Placement = FreeCAD.Placement()
    return local


def shape_mesh(obj, deviation_mm=0.5, name=None):
    """Tessellate an object's local shape into a TriMesh in metres."""
    from xrfit.mesh import TriMesh

    shape = local_shape(obj)
    if shape is None:
        return None
    points, facets = shape.tessellate(deviation_mm)
    verts = [(p.x * MM, p.y * MM, p.z * MM) for p in points]
    return TriMesh(verts, [tuple(f) for f in facets], name or obj.Name)


def features_of(obj):
    """Mating features of an object's local shape, in metres."""
    from xrassembly.features import Features, feature_from_dict, from_shape

    shape = local_shape(obj)
    if shape is None:
        return Features()
    scaled = []
    for f in from_shape(shape):
        d = f.to_dict()
        d["origin"] = [c * MM for c in d["origin"]]
        d["radius"] = d["radius"] * MM
        d["extent"] = d["extent"] * MM
        scaled.append(feature_from_dict(d))
    return Features(scaled)


def is_part_like(obj):
    tid = getattr(obj, "TypeId", "")
    if not hasattr(obj, "Shape") or not hasattr(obj, "Placement"):
        return False
    if tid.startswith(("Sketcher::", "PartDesign::Datum", "App::Origin")):
        return False
    return not getattr(obj, "InList", None) or not any(getattr(p, "TypeId", "") == "PartDesign::Body" for p in obj.InList)


def document_parts(doc, deviation_mm=0.5, visible_only=True):
    """``[(name, TriMesh in metres, Transform)]`` for the part-like objects of a document."""
    out = []
    for obj in getattr(doc, "Objects", []) or []:
        if not is_part_like(obj):
            continue
        if visible_only and not getattr(obj, "Visibility", True):
            continue
        try:
            mesh = shape_mesh(obj, deviation_mm)
        except Exception:
            mesh = None
        if mesh is None or len(mesh) == 0:
            continue
        out.append((obj.Name, mesh, placement_to_transform(obj.Placement)))
    return out


def selected_objects(doc=None):
    """``[(object, [subnames])]`` from the GUI selection, or [] without a GUI."""
    try:
        import FreeCADGui
    except ImportError:
        return []
    out = []
    try:
        for sel in FreeCADGui.Selection.getSelectionEx():
            out.append((sel.Object, list(sel.SubElementNames)))
    except Exception:
        pass
    return out


# -- controllers ---------------------------------------------------------


def controller_pose(controller):
    """``(position, rotation)`` in world metres, or ``(None, None)``."""
    try:
        tr = controller.get_global_transf()
        pos = tr.translation.getValue()
        position = (float(pos[0]), float(pos[1]), float(pos[2]))
    except Exception:
        return None, None
    rotation = vm.IDENTITY_QUAT
    try:
        rot = tr.rotation.getValue()
        value = rot.getValue() if hasattr(rot, "getValue") else rot
        if value is not None and len(value) >= 4:
            rotation = vm.quat_normalize((float(value[0]), float(value[1]), float(value[2]), float(value[3])))
    except Exception:
        pass
    return position, rotation


def controller_transform(controller):
    position, rotation = controller_pose(controller)
    if position is None:
        return None
    return vm.Transform(position, rotation)


def controller_buttons(controller):
    """``(trigger, grip, lever_x, lever_y)`` with the upstream ButtonsState semantics."""
    try:
        st = controller.get_buttons_states()
    except Exception:
        return None
    trigger = getattr(st, "trigger", None)
    grab = float(getattr(st, "grab", 0.0))
    value = grab if trigger is None else float(trigger)
    grip = grab if trigger is not None else 0.0
    return value, grip, float(getattr(st, "lever_x", 0.0)), float(getattr(st, "lever_y", 0.0))


def controller_ray(controller):
    """``(origin, direction)`` of the controller's pointing ray (local -Z)."""
    t = controller_transform(controller)
    if t is None:
        return None, None
    return t.translation, t.apply_vector((0.0, 0.0, -1.0))


def primary_controller(widget, controllers=None):
    controllers = controllers if controllers is not None else getattr(widget, "xr_con", [])
    if not controllers:
        return None
    index = getattr(widget, "primary_con", 0)
    return controllers[index] if index < len(controllers) else controllers[0]
