# SPDX-License-Identifier: LGPL-2.1-or-later
"""Peers in the room: publish where this desktop user is, draw everyone else.

The sync server (``xrsync.server``) keeps the presence registry; this
bridge writes the desktop's own head and controller poses into it as the
peer ``desktop`` and draws the other peers as small avatars — a head
block, two hand blocks, a name — coloured with each peer's colour. It also
applies ``object_moved`` events from peers to the document, so a part a
headset user is holding moves on the desktop too.
"""

import FreeCAD

from xrcore import service
from xrsketch import vecmath as vm

__all__ = ["attach", "detach", "handle_frame", "peers", "status_text", "SELF_ID"]

SELF_ID = "desktop"

_root = None
_avatars = {}
_last_event_seq = 0


def _registry():
    server = service.sync_server()
    return getattr(server, "presence", None) if server is not None else None


def attach(widget, root):
    global _root
    _root = root


def detach():
    global _root
    for avatar in list(_avatars.values()):
        try:
            _root.removeChild(avatar["node"])
        except Exception:
            pass
    _avatars.clear()
    _root = None


def _pose_dict(position, rotation):
    return {"position": [float(c) for c in position], "rotation": [float(c) for c in rotation]}


def _publish_self(widget, controllers):
    registry = _registry()
    if registry is None or widget is None:
        return
    try:
        p = widget.hmdpos.getValue()
        r = widget.hmdrot.getValue()
        head = _pose_dict((p[0], p[1], p[2]), (r[0], r[1], r[2], r[3]))
    except Exception:
        head = None
    hands = []
    from xrcore import docmesh

    for ctl in controllers or []:
        pos, rot = docmesh.controller_pose(ctl)
        buttons = docmesh.controller_buttons(ctl) or (0.0, 0.0, 0.0, 0.0)
        if pos is not None:
            entry = _pose_dict(pos, rot)
            entry["trigger"] = buttons[0]
            entry["grip"] = buttons[1]
            hands.append(entry)
    selection = []
    try:
        import FreeCADGui

        selection = [s.Object.Name for s in FreeCADGui.Selection.getSelectionEx()]
    except Exception:
        pass
    doc = FreeCAD.ActiveDocument
    registry.update(SELF_ID, {"name": service.preferences().GetString("PeerName", "") or "desktop", "head": head,
                              "hands": hands, "selection": selection, "environment": service.get_environment_id(),
                              "doc": doc.Name if doc is not None else None}, device="FreeCAD desktop")


def _apply_peer_moves(server):
    """Placements broadcast by peers -> the document (skipping our own)."""
    global _last_event_seq
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return
    try:
        events = server.events.since(_last_event_seq)
    except Exception:
        return
    for event in events:
        _last_event_seq = max(_last_event_seq, event.seq)
        if event.type != "object_moved" or event.data.get("peer") == SELF_ID or event.data.get("applied"):
            continue
        obj = doc.getObject(event.data.get("object", ""))
        if obj is None or not hasattr(obj, "Placement"):
            continue
        p = event.data.get("position") or [0, 0, 0]
        q = event.data.get("rotation") or [0, 0, 0, 1]
        from xrcore import docmesh

        obj.Placement = docmesh.transform_to_placement(vm.Transform(p, q))


def _draw(registry):
    if _root is None:
        return
    try:
        from xrcore import coin_util
    except Exception:
        return
    live = {}
    for peer in registry.peers(exclude=SELF_ID):
        live[peer.peer_id] = peer
        avatar = _avatars.get(peer.peer_id)
        if avatar is None:
            from pivy import coin

            node = coin.SoSeparator()
            head = coin_util.make_marker((0, 0, 0), peer.colour, 0.18, "cube")
            hands = [coin_util.make_marker((0, 0, 0), peer.colour, 0.05, "cube") for _ in range(2)]
            label = coin_util.make_label(peer.name, (0, 0.15, 0), peer.colour)
            for child in [head] + hands + [label]:
                node.addChild(child)
            _root.addChild(node)
            avatar = {"node": node, "head": head, "hands": hands, "label": label, "seq": -1}
            _avatars[peer.peer_id] = avatar
            try:
                from xrcore import haptics_bridge

                haptics_bridge.engine().trigger("peer", None)
            except Exception:
                pass
        if avatar["seq"] == peer.seq:
            continue
        avatar["seq"] = peer.seq
        if peer.head and peer.head.get("position"):
            pos = peer.head["position"]
            avatar["head"].transform.translation.setValue(*pos)
            avatar["label"].transform.translation.setValue(pos[0], pos[1] + 0.15, pos[2])
            rot = peer.head.get("rotation")
            if rot and len(rot) == 4:
                from pivy import coin

                avatar["head"].transform.rotation.setValue(coin.SbRotation(*[float(c) for c in rot]))
        for k, hand in enumerate(peer.hands[:2]):
            pos = hand.get("position")
            if pos:
                avatar["hands"][k].transform.translation.setValue(*pos)
    for peer_id in list(_avatars):
        if peer_id not in live:
            try:
                _root.removeChild(_avatars[peer_id]["node"])
            except Exception:
                pass
            del _avatars[peer_id]


def handle_frame(dt, controllers):
    server = service.sync_server()
    if server is None:
        return False
    widget = service.get_widget()
    _publish_self(widget, controllers)
    try:
        gone = server.presence.expire()
        for peer_id in gone:
            server.locks.release_all(peer_id)
    except Exception:
        pass
    _apply_peer_moves(server)
    _draw(server.presence)
    return False


def peers():
    registry = _registry()
    return registry.peers(exclude=SELF_ID) if registry is not None else []


def status_text():
    others = peers()
    if not others:
        return ""
    return "peers: " + ", ".join(p.name for p in others)
