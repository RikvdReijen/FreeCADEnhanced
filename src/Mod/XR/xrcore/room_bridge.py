# SPDX-License-Identifier: LGPL-2.1-or-later
"""Multiplayer on the desktop: host the room, share the place, apply the edits.

The desktop that runs the sync server hosts the room. Each frame the bridge
publishes what the desktop is in — environment, user scale, the document —
so every headset adopts it, and applies what guests send:

* **edits** (``POST /api/v1/edit``): deviation-layer operations, replayed
  onto a snapshot of the document with ``collab`` and materialised into it,
  so a guest saying "fillet two millimetres" changes the model for everyone
  and the desktop re-exports the scene;
* **location**: the room's shared origin and anchor; when a headset
  reports where it sees the shared QR code, the server hands it its
  calibration and its poses arrive in the shared frame from then on;
* **product data**: if the project beside the document has a ``.fcvcs``
  repository, it is served over ``POST /api/v1/vcs`` so headsets and other
  desktops push and pull workspaces and versions.

"Go to peer" moves *this* user next to a peer by moving the world, which
is the only thing a seated desktop user can do.
"""

import os

import FreeCAD

from xrcore import docmesh, service
from xrsketch import vecmath as vm

__all__ = ["attach", "detach", "handle_frame", "host", "room", "apply_edit", "teleport_to_peer", "status_text",
           "serve_repository", "commit_project", "share_edit"]

_root = None
_last_seq = -1
_last_pushed = None


def room():
    server = service.sync_server()
    return getattr(server, "room", None) if server is not None else None


def attach(widget, root):
    global _root
    _root = root
    server = service.sync_server()
    if server is not None:
        host(server)


def detach():
    global _root
    _root = None


def host(server=None):
    """Make this desktop the room's host and install the sinks."""
    server = server or service.sync_server()
    if server is None:
        raise service.XRServiceError("Start the sync server first (Virtual Reality → Sync server).")
    from xrcore.presence_bridge import SELF_ID

    server.room.join(SELF_ID, service.preferences().GetString("PeerName", "") or "desktop", "FreeCAD desktop",
                     {"edits": True, "vcs": server.vcs_repo is not None})
    server.room.claim_host(SELF_ID)
    server.edit_sink = apply_edit
    server.room_sink = _on_room_change
    serve_repository(server)
    _push_state(server, force=True)
    FreeCAD.Console.PrintMessage("XR: hosting room %r\n" % server.room.name)
    return server.room


def serve_repository(server=None):
    """Serve the project's .fcvcs beside the active document, if there is one."""
    server = server or service.sync_server()
    doc = FreeCAD.ActiveDocument
    if server is None or doc is None or not getattr(doc, "FileName", ""):
        return None
    try:
        from collab.vcs import Repository
    except ImportError:
        return None
    repo = Repository(os.path.dirname(doc.FileName))
    if repo.exists():
        server.vcs_repo = repo
        return repo
    server.vcs_repo = None
    return None


def commit_project(message, author=None):
    """Save the document and commit the project directory to its repository."""
    doc = FreeCAD.ActiveDocument
    if doc is None or not getattr(doc, "FileName", ""):
        raise service.XRServiceError("Save the document first; the repository lives beside it.")
    try:
        from collab.vcs import Repository
    except ImportError:
        raise service.XRServiceError("The Collab module (src/Mod/Collab) is needed for versioning.")
    project = os.path.dirname(doc.FileName)
    repo = Repository(project)
    if not repo.exists():
        repo = Repository.init(project, author or _author())
    doc.save()
    snapshot = repo.commit(message, author or _author())
    server = service.sync_server()
    if server is not None:
        server.vcs_repo = repo
        server.room.set_state(_self_id(), revision=snapshot.id)
        server.events.publish("vcs", peer=_self_id(), op="commit", id=snapshot.id, message=message)
    FreeCAD.Console.PrintMessage("XR: committed %s: %s\n" % (snapshot.short, message))
    return snapshot


def _author():
    return service.preferences().GetString("PeerName", "") or os.environ.get("USER", "desktop")


def _self_id():
    from xrcore.presence_bridge import SELF_ID

    return SELF_ID


# -- edits -------------------------------------------------------------


def apply_edit(edit, peer_id):
    """The server's edit sink: replay the operations on the document.

    Runs on the server thread; FreeCAD document changes are marshalled to the
    GUI thread when Qt is available, else applied directly."""
    try:
        from collab.freecad_adapter import document_model, materialise
        from collab.replay import replay
        from collab.schema import Layer, operation_from_json
    except ImportError:
        return {"applied": False, "message": "the Collab module is not installed; edit broadcast only"}
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return {"applied": False, "message": "no document open"}

    def work():
        layer = Layer(edit.get("layer") or ("peer-" + peer_id), operations=[operation_from_json(op) for op in edit["operations"]])
        model = document_model(doc, revision="live")
        result = replay(layer, model)
        if not result.ok:
            raise ValueError("; ".join(f.message for f in result.failures) or "replay failed")
        report = materialise(result.doc, doc)
        doc.recompute()
        return report

    report = _on_gui_thread(work)
    message = "applied" if report.ok else "applied with notes: " + "; ".join(report.unsupported + report.errors)
    FreeCAD.Console.PrintMessage("XR: edit from %s %s\n" % (peer_id, message))
    return {"applied": True, "revision": _scene_revision(), "message": message}


def _on_gui_thread(fn):
    try:
        from PySide import QtCore

        app = QtCore.QCoreApplication.instance()
        if app is None or QtCore.QThread.currentThread() is app.thread():
            return fn()
        box = {}

        def run():
            try:
                box["result"] = fn()
            except Exception as exc:
                box["error"] = exc

        QtCore.QMetaObject.invokeMethod(app, run, QtCore.Qt.BlockingQueuedConnection) if hasattr(QtCore.QMetaObject, "invokeMethod") else run()
        if "error" in box:
            raise box["error"]
        return box.get("result")
    except ImportError:
        return fn()


def share_edit(operations, layer=None, message=""):
    """Record an edit this desktop made so the headsets see it (applied already)."""
    server = service.sync_server()
    if server is None:
        return None
    edit = server.room.record_edit(_self_id(), operations, layer, message, applied=True, revision=_scene_revision())
    server.events.publish("edit", peer=_self_id(), seq=edit.seq, layer=layer, operations=list(operations), applied=True)
    return edit


def _scene_revision():
    try:
        from xrsync.scene_export import scene_hash

        return scene_hash()
    except Exception:
        return None


# -- shared place ------------------------------------------------------


def _push_state(server, force=False):
    global _last_pushed
    try:
        from xrcore import environment_bridge

        state = environment_bridge.current_state()
    except Exception:
        state = {}
    doc = FreeCAD.ActiveDocument
    current = (state.get("environment"), float(state.get("scale", 1.0) or 1.0), doc.Name if doc is not None else None)
    if not force and current == _last_pushed:
        return False
    _last_pushed = current
    try:
        server.room.set_state(_self_id(), doc=current[2], environment=current[0], scale=current[1])
        return True
    except PermissionError:
        return False


def _on_room_change(state, peer_id):
    """A guest that claimed the room changed the place: follow it on the desktop."""
    if peer_id == _self_id():
        return
    try:
        from xrcore import environment_bridge

        if state.get("environment") and state["environment"] != service.get_environment_id():
            environment_bridge.set_environment(state["environment"])
    except Exception as exc:
        FreeCAD.Console.PrintLog("XR: room change not applied: %s\n" % exc)


def handle_frame(dt, controllers):
    server = service.sync_server()
    if server is None:
        return False
    if server.room.host is None:
        host(server)
    elif server.room.is_host(_self_id()):
        _push_state(server)
    return False


# -- teleport ----------------------------------------------------------


def teleport_to_peer(peer_id=None, side=1.0):
    """Move the world so this user stands beside a peer (the first one when unnamed)."""
    from xrcore.presence_bridge import SELF_ID
    from xrsync.room import pose_beside

    server = service.sync_server()
    if server is None:
        raise service.XRServiceError("No sync server running.")
    peers = [p for p in server.presence.peers(exclude=SELF_ID) if p.head and p.head.get("position")]
    if peer_id is not None:
        peers = [p for p in peers if p.peer_id == peer_id or p.name == peer_id]
    if not peers:
        raise service.XRServiceError("No peer with a known position.")
    peer = peers[0]
    target = pose_beside(vm.Transform(tuple(peer.head["position"]), tuple(peer.head.get("rotation", vm.IDENTITY_QUAT))),
                         distance=0.8, side=side)
    widget = service.get_widget()
    node = getattr(widget, "world_grab_transform", None) if widget is not None else None
    if node is None:
        raise service.XRServiceError("The XR viewer is not running.")
    try:
        p = widget.hmdpos.getValue()
        head = vm.vec3((p[0], p[1], p[2]))
    except Exception:
        head = (0.0, 1.6, 0.0)
    # Moving the world by (head - target) puts the user at target.
    from xrcore import coin_util

    coin_util.set_transform(node, vm.Transform(vm.sub(head, target.translation)))
    FreeCAD.Console.PrintMessage("XR: moved next to %s\n" % peer.name)
    return target


def status_text():
    r = room()
    if r is None or not r.members:
        return ""
    return "room: %d in, host %s, %d edit(s)" % (len(r.members), r.host or "?", r.edit_seq)
