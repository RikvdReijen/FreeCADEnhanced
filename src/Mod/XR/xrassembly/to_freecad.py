# SPDX-License-Identifier: LGPL-2.1-or-later
"""Committing VR mates into the document.

Two levels. :func:`apply_placements` sets every part's ``Placement`` from
its solved pose — always possible, never wrong, and enough for a static
assembly. :func:`commit` additionally creates joints in a FreeCAD 1.0
Assembly (``Assembly::AssemblyObject`` + ``JointObject``) so the mates stay
parametric: a coincident face pair becomes a ``Distance`` joint at 0, a
concentric pair a ``Cylindrical`` joint, and a concentric+coincident pair on
one part collapses into one ``Revolute`` joint — the mapping is
:func:`joint_type_for` and is pure.

The Assembly workbench's Python API is reached through ``JointObject`` and
``UtilsAssembly``; both are optional and every call is guarded, so on a
build without the Assembly module the placements are still applied and the
result says which joints were not created.
"""

from xrsketch import vecmath as vm

JOINT_TYPES = ("Fixed", "Revolute", "Cylindrical", "Slider", "Ball", "Distance", "Parallel", "Perpendicular", "Angle")


class CommitResult(object):
    __slots__ = ("placements", "joints", "skipped", "notes")

    def __init__(self):
        self.placements = []
        self.joints = []
        self.skipped = []
        self.notes = []

    def to_dict(self):
        return {"placements": list(self.placements), "joints": list(self.joints),
                "skipped": list(self.skipped), "notes": list(self.notes)}


def joint_type_for(mates):
    """Collapse the mates of one part into Assembly joints.

    Returns ``[(joint_type, primary_mate, secondary_mate_or_None, params)]``.
    """
    mates = [m for m in mates if m.kind != "fixed"]
    joints = []
    used = set()
    concentric = [m for m in mates if m.kind == "concentric"]
    planar = [m for m in mates if m.kind in ("coincident", "distance")]
    # A bore plus a shoulder is a revolute joint about that bore.
    if concentric and planar:
        c, p = concentric[0], planar[0]
        joints.append(("Revolute", c, p, {"offset": p.offset}))
        used.update((id(c), id(p)))
    for m in mates:
        if id(m) in used:
            continue
        if m.kind == "concentric":
            joints.append(("Cylindrical", m, None, {}))
        elif m.kind in ("coincident", "distance"):
            joints.append(("Distance", m, None, {"distance": m.offset}))
        elif m.kind == "parallel":
            joints.append(("Parallel", m, None, {}))
        elif m.kind == "angle":
            joints.append(("Angle", m, None, {"angle": m.angle_deg}))
        elif m.kind == "point":
            joints.append(("Ball", m, None, {}))
        used.add(id(m))
    if any(m.kind == "fixed" for m in mates):
        joints.append(("Fixed", None, None, {}))
    return joints


def placement_of(pose, scale=1000.0):
    """A FreeCAD ``Placement`` for a pose in metres (document units mm)."""
    import FreeCAD

    t = pose.translation
    q = pose.rotation
    return FreeCAD.Placement(FreeCAD.Vector(t[0] * scale, t[1] * scale, t[2] * scale),
                             FreeCAD.Rotation(q[0], q[1], q[2], q[3]))


def apply_placements(session, document=None, scale=1000.0, result=None):
    result = result or CommitResult()
    try:
        import FreeCAD
    except ImportError:
        result.notes.append("FreeCAD not available; placements not applied")
        return result
    doc = document or FreeCAD.ActiveDocument
    if doc is None:
        result.notes.append("no document")
        return result
    for part in session.parts.values():
        obj = doc.getObject(part.name)
        if obj is None or not hasattr(obj, "Placement"):
            result.skipped.append("%s: no such object" % part.name)
            continue
        obj.Placement = placement_of(part.pose, scale)
        result.placements.append(part.name)
    return result


def _subname(feature):
    src = getattr(feature, "source", None)
    if src and src[0] in ("Face", "Edge", "Vertex"):
        return "%s%d" % (src[0], src[1])
    return ""


def commit(session, document=None, scale=1000.0, assembly=None):
    """Apply placements and create joints. Returns a :class:`CommitResult`."""
    result = apply_placements(session, document, scale)
    try:
        import FreeCAD
    except ImportError:
        return result
    doc = document or FreeCAD.ActiveDocument
    if doc is None:
        return result
    try:
        import JointObject  # Assembly workbench
    except ImportError:
        result.notes.append("Assembly workbench not available; mates applied as placements only")
        return result
    assembly = assembly or _find_or_make_assembly(doc, result)
    if assembly is None:
        return result
    for part in session.parts.values():
        obj = doc.getObject(part.name)
        if obj is None:
            continue
        for joint_type, primary, secondary, params in joint_type_for(part.mates):
            try:
                joint = doc.addObject("App::FeaturePython", "Joint")
                JointObject.Joint(joint, joint_type)
                if primary is not None:
                    other = doc.getObject(primary.other_part)
                    f1 = part.features.get(primary.feature)
                    f2 = session.parts[primary.other_part].features.get(primary.other_feature)
                    joint.Reference1 = [(obj, (_subname(f1),))]
                    joint.Reference2 = [(other, (_subname(f2),))]
                if "distance" in params and hasattr(joint, "Distance"):
                    joint.Distance = params["distance"] * scale
                if "angle" in params and hasattr(joint, "Angle"):
                    joint.Angle = params["angle"]
                if hasattr(assembly, "addObject"):
                    assembly.addObject(joint)
                result.joints.append((joint.Name, joint_type))
            except Exception as exc:
                result.skipped.append("%s %s: %s" % (part.name, joint_type, exc))
    return result


def _find_or_make_assembly(doc, result):
    for obj in getattr(doc, "Objects", []):
        if getattr(obj, "TypeId", "") == "Assembly::AssemblyObject":
            return obj
    try:
        return doc.addObject("Assembly::AssemblyObject", "Assembly")
    except Exception as exc:
        result.notes.append("cannot create an Assembly object: %s" % exc)
        return None
