# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly in VR: hand-placed mates on the objects of the open document.

``activate()`` builds an :class:`xrassembly.AssemblySession` from the
part-like objects of the active document (features from their shapes,
poses from their placements). With the mode on, the primary controller's
grip grabs the part under the hand, the session previews the best mate as
the hand moves (drawn as a highlight line), the trigger confirms it, and
letting go releases. Every pose change is written back to the object's
``Placement`` so the desktop view follows, and ``commit()`` turns the
mates into Assembly workbench joints.
"""

import FreeCAD

from xrcore import docmesh, service
from xrsketch import vecmath as vm

__all__ = ["get_session", "ensure_session", "attach", "detach", "activate", "deactivate", "active",
           "handle_frame", "commit", "reload", "grab_nearest", "release", "confirm"]

_root = None
_preview = None
_active = False
_grab_was = False
_trigger_was = False


def get_session():
    return service.get_feature("assembly")


def ensure_session(document=None):
    session = get_session()
    if session is None:
        from xrassembly import AssemblySession

        session = AssemblySession()
        service.set_feature("assembly", session)
        reload(document)
    return session


def reload(document=None):
    """Rebuild the parts from the document (call after the model changed)."""
    session = get_session()
    if session is None:
        return 0
    doc = document or FreeCAD.ActiveDocument
    if doc is None:
        return 0
    session.parts.clear()
    count = 0
    for obj in getattr(doc, "Objects", []) or []:
        if not docmesh.is_part_like(obj) or not getattr(obj, "Visibility", True):
            continue
        features = docmesh.features_of(obj)
        if len(features) == 0:
            continue
        session.add_part(obj.Name, features, docmesh.placement_to_transform(obj.Placement), fixed=False, label=obj.Label)
        count += 1
    # The first part is the ground unless the user says otherwise.
    if count and not any(p.fixed for p in session.parts.values()):
        session.fix(next(iter(session.parts)))
    return count


def attach(widget, root):
    global _root
    _root = root
    return ensure_session()


def detach():
    global _root, _preview
    if _root is not None and _preview is not None:
        try:
            _root.removeChild(_preview)
        except Exception:
            pass
    _root = None
    _preview = None
    deactivate()


def activate():
    global _active
    ensure_session()
    _active = True
    FreeCAD.Console.PrintMessage("XR: assembly mode — grip to grab a part, trigger to confirm a mate\n")


def deactivate():
    global _active
    _active = False
    session = get_session()
    if session is not None and session.grabbed:
        session.release()


def active():
    return _active


def _write_back(session, name):
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return
    obj = doc.getObject(name)
    if obj is not None and hasattr(obj, "Placement"):
        obj.Placement = docmesh.transform_to_placement(session.parts[name].pose)


def grab_nearest(session, hand):
    """Grab the movable part whose bounding features are closest to the hand."""
    best, best_d = None, 0.15
    for part in session.parts.values():
        if part.fixed:
            continue
        for f in part.features.world(part.pose):
            d = vm.dist(f.origin, hand.translation)
            if d < best_d:
                best, best_d = part.name, d
    if best is None:
        return None
    session.grab(best, hand)
    _lock(best, True)
    return best


def release():
    session = get_session()
    if session is None or session.grabbed is None:
        return None
    name = session.release()
    _write_back(session, name)
    _lock(name, False)
    return name


def confirm():
    session = get_session()
    if session is None:
        return None
    mate = session.confirm()
    if mate is not None and session.grabbed:
        _write_back(session, session.grabbed)
    return mate


def _lock(name, acquire):
    server = service.sync_server()
    if server is None:
        return
    try:
        if acquire:
            server.locks.acquire(name, "desktop")
        else:
            server.locks.release(name, "desktop")
    except Exception:
        pass


def handle_frame(dt, controllers):
    global _grab_was, _trigger_was
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
    trigger_now = trigger >= 0.7
    if gripping and not _grab_was and session.grabbed is None:
        grab_nearest(session, hand)
    _grab_was = gripping
    consumed = session.grabbed is not None
    if consumed:
        session.update(dt, hand, grip=1.0 if gripping else 0.0, trigger=trigger_now and not _trigger_was)
        if session.grabbed is not None:
            _write_back(session, session.grabbed)
            _broadcast(session, session.grabbed)
        else:
            released = [e.part for e in session.events if e.kind == "release"]
            for name in released:
                _write_back(session, name)
                _lock(name, False)
    _trigger_was = trigger_now
    _draw_preview(session)
    try:
        from xrcore import haptics_bridge
        from xrhaptics import ASSEMBLY_EVENTS

        haptics_bridge.feed(session.drain_events(), ASSEMBLY_EVENTS, hand=1 if widget is None else widget.primary_con)
    except Exception:
        session.drain_events()
    return consumed


def _broadcast(session, name):
    server = service.sync_server()
    if server is None:
        return
    try:
        pose = session.parts[name].pose
        server.events.publish("object_moved", object=name, peer="desktop", position=list(pose.translation),
                              rotation=list(pose.rotation), final=False, applied=True)
    except Exception:
        pass


def _draw_preview(session):
    """A line from the grabbed feature to its mate target while previewing."""
    global _preview
    if _root is None:
        return
    try:
        from xrcore import coin_util
    except Exception:
        return
    if _preview is not None:
        try:
            _root.removeChild(_preview)
        except Exception:
            pass
        _preview = None
    candidate = session.preview
    if candidate is None or session.grabbed is None:
        return
    mate = candidate.mate
    part = session.parts.get(mate.part)
    other = session.parts.get(mate.other_part)
    if part is None or other is None:
        return
    mine = part.features.world(part.pose).get(mate.feature)
    theirs = other.features.world(other.pose).get(mate.other_feature)
    if mine is None or theirs is None:
        return
    colour = (0.3, 1.0, 0.4) if candidate.score > 0.5 else (1.0, 0.8, 0.2)
    _preview = coin_util.make_lines([[mine.origin, theirs.origin]], colour, 3.0)
    _root.addChild(_preview)


def commit(document=None):
    session = get_session()
    if session is None:
        raise service.XRServiceError("No assembly session; activate assembly mode first.")
    from xrassembly import commit as _commit

    result = _commit(session, document or FreeCAD.ActiveDocument)
    for note in result.notes:
        FreeCAD.Console.PrintWarning(f"XR: {note}\n")
    return result
