# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scan import and alignment in the headset.

``import_scan(path)`` reads a mesh (STL/OBJ/PLY/3MF through xrimport),
shows it in the world at the scale the file implies, and opens a
:class:`xrscan.ScanSession` against the selected object (or the largest
part-like object) as the model. The trigger picks correspondences —
alternately on the scan and on the model, nearest surface point to the
controller — and the menu buttons run the estimators: align (Kabsch),
refine (ICP), sit on the plate, known length. ``commit()`` writes the
aligned scan into the document as a ``Mesh::Feature`` with the placement
it ended up with.
"""

import os

import FreeCAD

from xrcore import docmesh, service
from xrsketch import vecmath as vm

__all__ = ["get_session", "attach", "detach", "activate", "deactivate", "active", "handle_frame", "import_scan",
           "align", "refine", "sit", "known_length", "commit", "status_text", "undo"]

_root = None
_scan_node = None
_pick_nodes = []
_active = False
_trigger_was = False
_next_pick_is_scan = True
_scan_mesh = None


def get_session():
    return service.get_feature("scan")


def attach(widget, root):
    global _root
    _root = root


def detach():
    global _root, _scan_node
    _clear_nodes()
    _root = None


def _clear_nodes():
    global _scan_node
    if _root is None:
        return
    if _scan_node is not None:
        try:
            _root.removeChild(_scan_node)
        except Exception:
            pass
        _scan_node = None
    for node in _pick_nodes:
        try:
            _root.removeChild(node)
        except Exception:
            pass
    del _pick_nodes[:]


def activate():
    global _active
    if get_session() is None:
        raise service.XRServiceError("Import a scan first (Virtual Reality → Import scan).")
    _active = True


def deactivate():
    global _active
    _active = False


def active():
    return _active


def _model_mesh():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    for obj, _ in docmesh.selected_objects(doc):
        if docmesh.is_part_like(obj):
            mesh = docmesh.shape_mesh(obj, 0.3)
            if mesh is not None:
                return mesh.transformed(docmesh.placement_to_transform(obj.Placement))
    parts = docmesh.document_parts(doc, 0.3)
    if not parts:
        return None
    name, mesh, pose = max(parts, key=lambda p: len(p[1]))
    return mesh.transformed(pose)


def import_scan(path, scale_mm=None):
    """Read a mesh file, show it, and open the alignment session."""
    global _scan_mesh
    from xrimport import convert
    from xrscan import ScanSession

    kind, payload = convert.plan(path, scale_mm)
    if kind != "mesh":
        raise service.XRServiceError("%s is not a mesh scan (STL, OBJ, PLY or 3MF)" % os.path.basename(path))
    meshes = payload.meshes
    if not meshes:
        raise service.XRServiceError("no mesh in %s" % path)
    from xrfit.mesh import TriMesh

    if len(meshes) == 1:
        mesh_mm = meshes[0]
    else:  # merge
        verts, tris = [], []
        for m in meshes:
            base = len(verts)
            verts.extend(m.vertices)
            tris.extend((a + base, b + base, c + base) for a, b, c in m.triangles)
        mesh_mm = TriMesh(verts, tris, os.path.basename(path))
    _scan_mesh = TriMesh([(x * docmesh.MM, y * docmesh.MM, z * docmesh.MM) for x, y, z in mesh_mm.vertices],
                         mesh_mm.triangles, mesh_mm.name)
    session = ScanSession(_scan_mesh, _model_mesh(), sample_points=int(service.preferences().GetInt("ScanSamplePoints", 2000)))
    service.set_feature("scan", session)
    FreeCAD.Console.PrintMessage("XR scan: %s — %d triangles%s\n" % (
        _scan_mesh.name, len(_scan_mesh), "" if session.model is not None else " (no model selected; pick one for refine)"))
    _draw(session)
    return session


def _draw(session):
    global _scan_node
    if _root is None:
        return
    try:
        from xrcore import coin_util
    except Exception:
        return
    if _scan_node is not None:
        _root.removeChild(_scan_node)
    _scan_node = coin_util.make_mesh(session.scan, (0.9, 0.6, 0.3), 0.3)
    coin_util.set_transform(_scan_node.transform, session.scan_pose)
    _root.addChild(_scan_node)


def _update_pose(session):
    if _scan_node is not None:
        from xrcore import coin_util

        coin_util.set_transform(_scan_node.transform, session.scan_pose)


def handle_frame(dt, controllers):
    global _trigger_was, _next_pick_is_scan
    if not _active:
        return False
    session = get_session()
    if session is None or not controllers:
        return False
    widget = service.get_widget()
    ctl = docmesh.primary_controller(widget, controllers)
    hand = docmesh.controller_transform(ctl)
    buttons = docmesh.controller_buttons(ctl)
    if hand is None or buttons is None:
        return False
    trigger = buttons[0] >= 0.7
    if trigger and not _trigger_was:
        point = _surface_point(session, hand.translation, on_scan=_next_pick_is_scan)
        if _next_pick_is_scan:
            session.pick_scan(point)
        else:
            session.pick_model(point)
        _mark(point, (0.2, 0.8, 1.0) if _next_pick_is_scan else (1.0, 0.3, 0.8))
        _next_pick_is_scan = not _next_pick_is_scan
        FreeCAD.Console.PrintMessage("XR scan: pick %d on the %s\n" % (len(session.pairs), "model" if _next_pick_is_scan else "scan"))
    _trigger_was = trigger
    try:
        from xrcore import haptics_bridge
        from xrhaptics import SCAN_EVENTS

        haptics_bridge.feed(session.drain_events(), SCAN_EVENTS, hand=widget.primary_con if widget is not None else 1)
    except Exception:
        session.drain_events()
    return trigger


def _surface_point(session, hand_point, on_scan):
    """The nearest surface point to the hand, on the scan or the model."""
    from xrfit.bvh import BVH
    from xrscan import closest_on_mesh

    if on_scan:
        bvh = getattr(session, "_scan_bvh", None)
        if bvh is None:
            bvh = BVH(session.scan)
            session._scan_bvh = bvh
        local = session.scan_pose.inverse().apply(hand_point)
        d, q, _ = closest_on_mesh(bvh, local)
        return session.scan_pose.apply(q) if q is not None else hand_point
    if session._model_bvh is None:
        return hand_point
    d, q, _ = closest_on_mesh(session._model_bvh, hand_point)
    return q if q is not None else hand_point


def _mark(point, colour):
    if _root is None:
        return
    from xrcore import coin_util

    node = coin_util.make_marker(point, colour, 0.008)
    _root.addChild(node)
    _pick_nodes.append(node)


def _require():
    session = get_session()
    if session is None:
        raise service.XRServiceError("Import a scan first.")
    return session


def align(scale=False):
    session = _require()
    result = session.align_from_pairs(scale=scale)
    _update_pose(session)
    FreeCAD.Console.PrintMessage("XR scan: aligned from %d pairs, RMS %.2f mm\n" % (len(session.complete_pairs()), result.rms * 1000.0))
    return result


def refine(iterations=30):
    session = _require()
    result = session.refine(iterations=iterations)
    _update_pose(session)
    FreeCAD.Console.PrintMessage("XR scan: refined in %d iterations, RMS %.3f mm\n" % (result.iterations, result.rms * 1000.0))
    return result


def sit(env_id=None):
    """Put the scan's largest plane on the environment's build plate."""
    session = _require()
    origin, normal = (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    try:
        from xrenv import registry

        anchor = (registry.get(env_id or service.get_environment_id()).spec.get("anchors") or {}).get("build_plate")
        if anchor:
            origin = tuple(anchor.get("position", origin))
            normal = vm.Transform((0, 0, 0), tuple(anchor.get("rotation", vm.IDENTITY_QUAT))).apply_vector((0, 0, 1))
    except Exception:
        pass
    session.sit_on_plane(origin, normal)
    _update_pose(session)
    return session.scan_pose


def known_length(length_mm):
    session = _require()
    factor = session.set_known_length(length_mm * docmesh.MM)
    _update_pose(session)
    FreeCAD.Console.PrintMessage("XR scan: scaled by %.4g\n" % factor)
    return factor


def undo():
    session = _require()
    if session.undo():
        _update_pose(session)
        return True
    return False


def commit(document=None):
    """The aligned scan as a Mesh::Feature in the document (mm)."""
    session = _require()
    doc = document or FreeCAD.ActiveDocument
    if doc is None:
        raise service.XRServiceError("no document")
    import Mesh

    fc_mesh = Mesh.Mesh()
    scale = 1.0 / docmesh.MM
    fc_mesh.addFacets([tuple((p[0] * scale, p[1] * scale, p[2] * scale) for p in session.scan.triangle(i))
                       for i in range(len(session.scan))])
    obj = doc.addObject("Mesh::Feature", session.scan.name or "Scan")
    obj.Mesh = fc_mesh
    obj.Placement = docmesh.transform_to_placement(vm.Transform(session.scan_pose.translation, session.scan_pose.rotation))
    if abs(session.scan_pose.scale - 1.0) > 1e-9:
        obj.Mesh.transformGeometry(FreeCAD.Matrix(session.scan_pose.scale, 0, 0, 0, 0, session.scan_pose.scale, 0, 0,
                                                  0, 0, session.scan_pose.scale, 0, 0, 0, 0, 1))
    doc.recompute()
    return obj


def status_text():
    session = get_session()
    if session is None or not _active:
        return ""
    residuals = session.residuals()
    if residuals:
        return "scan: %d pairs, worst %.1f mm" % (len(residuals), max(residuals) * 1000.0)
    return "scan: pick on the %s" % ("scan" if _next_pick_is_scan else "model")
