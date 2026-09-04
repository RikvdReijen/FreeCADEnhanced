# SPDX-License-Identifier: LGPL-2.1-or-later
"""The shared room: one model, one place, many people.

Presence (``presence.py``) says where everyone *is*; the room says what
everyone is *in*: the document and its revision, the environment, the
user scale, and the **shared origin** — where the model sits in a frame
all devices agree on. Three parts:

* :class:`Room` — host-authoritative state with a sequence number, the
  member list, and an **edit log** of deviation-layer operations so a late
  joiner replays what it missed and every peer applies the same edits in
  the same order.
* **Co-location** — each device sees a shared anchor (a QR code, or a peer
  chosen as the origin) in its own tracking frame; :func:`colocation_transform`
  turns that observation into the device's local→shared calibration, and
  every pose crossing the wire is expressed in the shared frame.
* **Follow and teleport** — :func:`pose_beside` puts a user at a peer's
  side, facing what they face, which is how "come here" works.

Pure Python; the server keeps one :class:`Room`, the bridges keep the
calibration, and ``test_room.py`` pins the maths.
"""

import math
import threading
import time

from xrsketch import vecmath as vm


class Member(object):
    __slots__ = ("peer_id", "name", "device", "joined", "calibrated", "role", "capabilities")

    def __init__(self, peer_id, name="", device="", role="guest", capabilities=None):
        self.peer_id = peer_id
        self.name = name or peer_id
        self.device = device
        self.joined = time.time()
        self.calibrated = False
        self.role = role
        self.capabilities = dict(capabilities or {})

    def to_dict(self):
        return {"peer_id": self.peer_id, "name": self.name, "device": self.device, "joined": self.joined,
                "calibrated": self.calibrated, "role": self.role, "capabilities": dict(self.capabilities)}


class Edit(object):
    """One shared edit: deviation-layer operations by a peer."""

    __slots__ = ("seq", "peer", "layer", "operations", "message", "time", "applied", "revision")

    def __init__(self, seq, peer, operations, layer=None, message="", applied=None, revision=None):
        self.seq = seq
        self.peer = peer
        self.layer = layer
        self.operations = list(operations)
        self.message = message
        self.time = time.time()
        self.applied = applied
        self.revision = revision

    def to_dict(self):
        return {"seq": self.seq, "peer": self.peer, "layer": self.layer, "operations": list(self.operations),
                "message": self.message, "time": self.time, "applied": self.applied, "revision": self.revision}


class Room(object):
    def __init__(self, name="room", host=None, doc=None, environment="studio", scale=1.0):
        self.name = name
        self.host = host
        self.doc = doc
        self.revision = None
        self.environment = environment
        self.scale = float(scale)
        #: the model's origin in the shared frame (metres, Y up), and its rotation
        self.origin = {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}
        #: the shared anchor everyone calibrates against
        self.anchor = None  # {"kind": "qr"|"peer"|"manual", "id": ..., "pose": {...}}
        self.members = {}
        self.observations = {}   # peer_id -> {anchor_id: pose}
        self.edits = []
        self.seq = 0
        self.edit_seq = 0
        self.updated = time.time()
        self._lock = threading.RLock()

    # -- membership ------------------------------------------------------

    def join(self, peer_id, name="", device="", capabilities=None):
        with self._lock:
            member = self.members.get(peer_id)
            joined = member is None
            if joined:
                role = "host" if self.host in (None, peer_id) else "guest"
                member = Member(peer_id, name, device, role, capabilities)
                self.members[peer_id] = member
                if self.host is None:
                    self.host = peer_id
                self._bump()
            else:
                if name:
                    member.name = name
                if capabilities:
                    member.capabilities.update(capabilities)
            return member, joined

    def leave(self, peer_id):
        with self._lock:
            member = self.members.pop(peer_id, None)
            self.observations.pop(peer_id, None)
            if member is not None:
                if self.host == peer_id:
                    # hand the room to the longest-standing member
                    remaining = sorted(self.members.values(), key=lambda m: m.joined)
                    self.host = remaining[0].peer_id if remaining else None
                    if remaining:
                        remaining[0].role = "host"
                self._bump()
            return member

    def is_host(self, peer_id):
        return self.host == peer_id or self.host is None

    def claim_host(self, peer_id):
        with self._lock:
            if peer_id not in self.members:
                raise KeyError(peer_id)
            for m in self.members.values():
                m.role = "guest"
            self.members[peer_id].role = "host"
            self.host = peer_id
            self._bump()

    # -- shared state ----------------------------------------------------

    def set_state(self, peer_id, doc=None, revision=None, environment=None, scale=None, origin=None, anchor=None):
        """Host-only changes; returns True when something changed."""
        with self._lock:
            if not self.is_host(peer_id):
                raise PermissionError("only the host (%s) may change the room" % self.host)
            changed = False
            for key, value in (("doc", doc), ("revision", revision), ("environment", environment)):
                if value is not None and getattr(self, key) != value:
                    setattr(self, key, value)
                    changed = True
            if scale is not None and float(scale) > 0 and abs(self.scale - float(scale)) > 1e-9:
                self.scale = float(scale)
                changed = True
            if origin is not None:
                self.origin = {"position": [float(c) for c in origin.get("position", (0, 0, 0))],
                               "rotation": [float(c) for c in origin.get("rotation", (0, 0, 0, 1))]}
                changed = True
            if anchor is not None:
                self.anchor = dict(anchor)
                changed = True
            if changed:
                self._bump()
            return changed

    def _bump(self):
        self.seq += 1
        self.updated = time.time()

    # -- co-location -----------------------------------------------------

    def observe_anchor(self, peer_id, anchor_id, local_pose):
        """A peer reports where it sees anchor ``anchor_id`` in its own frame.
        Returns the peer's local→shared calibration when the anchor is the
        room's, else None (kept for later)."""
        with self._lock:
            self.observations.setdefault(peer_id, {})[anchor_id] = dict(local_pose)
            if self.anchor is None:
                # the first observation of anything defines the shared frame: that anchor sits at the origin
                self.anchor = {"kind": "qr", "id": anchor_id, "pose": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}}
                self._bump()
            if self.anchor.get("id") != anchor_id:
                return None
            member = self.members.get(peer_id)
            if member is not None:
                member.calibrated = True
            return colocation_transform(_pose(local_pose), _pose(self.anchor["pose"]))

    # -- edits -----------------------------------------------------------

    def record_edit(self, peer_id, operations, layer=None, message="", applied=None, revision=None):
        with self._lock:
            self.edit_seq += 1
            edit = Edit(self.edit_seq, peer_id, operations, layer, message, applied, revision)
            self.edits.append(edit)
            if len(self.edits) > 2000:
                del self.edits[:-2000]
            self.updated = time.time()
            return edit

    def edits_since(self, seq):
        with self._lock:
            return [e for e in self.edits if e.seq > seq]

    # -- export ----------------------------------------------------------

    def to_dict(self):
        with self._lock:
            return {"name": self.name, "host": self.host, "doc": self.doc, "revision": self.revision,
                    "environment": self.environment, "scale": self.scale, "origin": dict(self.origin),
                    "anchor": dict(self.anchor) if self.anchor else None,
                    "members": [m.to_dict() for m in self.members.values()], "seq": self.seq,
                    "edit_seq": self.edit_seq, "updated": self.updated}


# ----------------------------------------------------------------------
# co-location maths
# ----------------------------------------------------------------------


def _pose(d):
    return vm.Transform(tuple(d.get("position", (0, 0, 0))), tuple(d.get("rotation", (0, 0, 0, 1))))


def colocation_transform(local_anchor_pose, shared_anchor_pose):
    """The transform taking this device's tracking frame into the shared frame.

    If the device sees the anchor at ``L`` and the room says the anchor is
    at ``S``, then ``C = S ∘ L⁻¹`` maps any local pose ``P`` to ``C ∘ P``.
    """
    local = local_anchor_pose if isinstance(local_anchor_pose, vm.Transform) else _pose(local_anchor_pose)
    shared = shared_anchor_pose if isinstance(shared_anchor_pose, vm.Transform) else _pose(shared_anchor_pose)
    return vm.compose(shared, local.inverse())


def to_shared(pose, calibration):
    return vm.compose(calibration, pose)


def to_local(pose, calibration):
    return vm.compose(calibration.inverse(), pose)


def pose_dict(transform):
    return {"position": [float(c) for c in transform.translation], "rotation": [float(c) for c in transform.rotation]}


def pose_beside(peer_head, distance=0.8, side=1.0):
    """A pose standing next to a peer, at the same height, facing their way.

    ``side`` +1 puts you on their right, −1 on their left."""
    head = peer_head if isinstance(peer_head, vm.Transform) else _pose(peer_head)
    forward = head.apply_vector((0.0, 0.0, -1.0))
    forward = vm.normalize((forward[0], 0.0, forward[2]), (0.0, 0.0, -1.0))
    right = vm.normalize(vm.cross(forward, (0.0, 1.0, 0.0)), (1.0, 0.0, 0.0))
    position = vm.add(head.translation, vm.mul(right, distance * side))
    yaw = math.atan2(-forward[0], -forward[2])
    rotation = (0.0, math.sin(yaw / 2.0), 0.0, math.cos(yaw / 2.0))
    return vm.Transform(position, rotation)


def pose_facing(peer_head, distance=1.2):
    """A pose in front of a peer, facing them."""
    head = peer_head if isinstance(peer_head, vm.Transform) else _pose(peer_head)
    forward = head.apply_vector((0.0, 0.0, -1.0))
    forward = vm.normalize((forward[0], 0.0, forward[2]), (0.0, 0.0, -1.0))
    position = vm.add(head.translation, vm.mul(forward, distance))
    back = vm.neg(forward)
    yaw = math.atan2(-back[0], -back[2])
    return vm.Transform(position, (0.0, math.sin(yaw / 2.0), 0.0, math.cos(yaw / 2.0)))


def world_offset_for_scale(origin, old_scale, new_scale, pivot):
    """When the shared scale changes, keep ``pivot`` (a point everyone is looking
    at, shared frame) where it is: the origin moves by the scale change about it."""
    o = _pose(origin)
    factor = new_scale / old_scale if old_scale else 1.0
    p = vm.vec3(pivot)
    new_translation = vm.add(p, vm.mul(vm.sub(o.translation, p), factor))
    return {"position": [float(c) for c in new_translation], "rotation": list(o.rotation)}
