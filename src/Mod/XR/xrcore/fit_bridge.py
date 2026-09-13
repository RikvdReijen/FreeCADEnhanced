# SPDX-License-Identifier: LGPL-2.1-or-later
"""Physics-based fit checking on the open document.

``activate()`` tessellates the part-like objects into an
:class:`xrfit.FitSession` — everything static except the part you grab.
Grip grabs the part under the hand; from then on the hand proposes poses
and the session stops the part where it touches something, sliding it
along what it touches. Contacts and blocks go to the haptics; the
clearance of a seated part is printed to the console and shown on the
wrist-menu status line. Releasing writes the final placement back.
"""

import FreeCAD

from xrcore import docmesh, service
from xrsketch import vecmath as vm

__all__ = ["get_session", "ensure_session", "attach", "detach", "activate", "deactivate", "active", "handle_frame",
           "reload", "probe", "status_text"]

_active = False
_grab_was = False
_last_clearance = None


def get_session():
    return service.get_feature("fit")


def ensure_session(document=None):
    session = get_session()
    if session is None:
        from xrfit import FitParams, FitSession

        prefs = service.preferences()
        session = FitSession(FitParams(contact_margin=prefs.GetFloat("FitContactMargin", 0.0),
                                       clearance_max=prefs.GetFloat("FitClearanceMax", 0.05)))
        service.set_feature("fit", session)
        reload(document)
    return session


def reload(document=None, deviation_mm=0.3):
    session = get_session()
    if session is None:
        return 0
    doc = document or FreeCAD.ActiveDocument
    if doc is None:
        return 0
    session.parts.clear()
    for name, mesh, pose in docmesh.document_parts(doc, deviation_mm):
        session.add_part(name, mesh, pose, static=True)
    return len(session.parts)


def attach(widget, root=None):
    return ensure_session()


def detach():
    deactivate()


def activate():
    global _active
    ensure_session()
    _active = True
    FreeCAD.Console.PrintMessage("XR: fit check — grip a part and try to insert it\n")


def deactivate():
    global _active
    _active = False
    session = get_session()
    if session is not None and session.grabbed:
        session.release()


def active():
    return _active


def _nearest_part(session, hand):
    best, best_d = None, 0.2
    for part in session.parts.values():
        lo, hi = part.mesh.bounds
        centre = part.pose.apply(tuple((lo[i] + hi[i]) * 0.5 for i in range(3)))
        d = vm.dist(centre, hand.translation)
        if d < best_d:
            best, best_d = part.name, d
    return best


def _write_back(session, name):
    doc = FreeCAD.ActiveDocument
    obj = doc.getObject(name) if doc is not None else None
    if obj is not None and hasattr(obj, "Placement"):
        obj.Placement = docmesh.transform_to_placement(session.parts[name].pose)


def handle_frame(dt, controllers):
    global _grab_was, _last_clearance
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
    trigger, grip, _, _ = buttons
    gripping = grip >= 0.7 if grip else trigger >= 0.7
    if gripping and not _grab_was and session.grabbed is None:
        name = _nearest_part(session, hand)
        if name is not None:
            session.parts[name].static = False
            session.grab(name, hand)
    _grab_was = gripping
    consumed = session.grabbed is not None
    if consumed:
        name = session.grabbed
        session.update(dt, hand, grip=1.0 if gripping else 0.0)
        _write_back(session, name)
        if session.grabbed is None:
            session.parts[name].static = True
        if session.clearance is not None:
            _last_clearance = session.clearance
    try:
        from xrcore import haptics_bridge
        from xrhaptics import FIT_EVENTS

        haptics_bridge.feed(session.drain_events(), FIT_EVENTS, hand=1 if widget is None else widget.primary_con)
    except Exception:
        session.drain_events()
    return consumed


def probe(name, direction=(0, 0, -1), distance=0.1):
    """Sweep a part along a direction and report whether it goes in."""
    from xrfit import InsertionProbe

    session = ensure_session()
    if name not in session.parts:
        raise service.XRServiceError(f"{name} is not a part of the fit session")
    session.parts[name].static = False
    try:
        return InsertionProbe().probe(session, name, direction, distance)
    finally:
        session.parts[name].static = True


def status_text():
    session = get_session()
    if session is None or not _active:
        return ""
    if session.blocked:
        return "fit: BLOCKED"
    if _last_clearance is not None:
        return "fit: clearance %.2f mm to %s" % (_last_clearance[0] * 1000.0, _last_clearance[1])
    return "fit: grab a part"
