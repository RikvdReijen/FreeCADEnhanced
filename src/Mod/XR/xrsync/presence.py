# SPDX-License-Identifier: LGPL-2.1-or-later
"""Who else is in the model: peers, their poses, and who holds what.

Two headsets in one model is a protocol extension, not a rebuild: the sync
server already knows every paired device by its token. This module adds
the three things a shared session needs, all pure and all small:

* :class:`PresenceRegistry` — each peer's head and hands, selection,
  environment and scale, refreshed every frame and expired when silent.
* :class:`LockTable` — who is holding which object; a grab takes the lock,
  a release drops it, and a lock held by a peer that went quiet expires.
* :func:`peer_colour` — a stable colour per peer so avatars and selection
  highlights match across every device.

The server wires these to ``/api/v1/presence``, ``/api/v1/lock`` and
``/api/v1/move``; :mod:`xrcore.presence_bridge` draws the peers.
"""

import colorsys
import hashlib
import threading
import time

#: seconds without an update before a peer is dropped
PEER_TIMEOUT = 5.0
#: seconds a lock lives without renewal
LOCK_TTL = 10.0


def peer_id_for(token, salt="fcxr-peer"):
    """A short, stable, non-reversible id for a device token."""
    return hashlib.sha1(("%s:%s" % (salt, token or "")).encode("utf-8")).hexdigest()[:8]


def peer_colour(peer_id):
    """A saturated colour (r, g, b in 0..1) derived from the id."""
    h = int(hashlib.md5(peer_id.encode("utf-8")).hexdigest()[:6], 16) / float(0xFFFFFF)
    return colorsys.hsv_to_rgb(h, 0.65, 0.95)


class PeerState(object):
    __slots__ = ("peer_id", "device", "name", "colour", "head", "hands", "selection", "environment", "scale",
                 "doc", "last_seen", "seq", "joined_at", "tool")

    def __init__(self, peer_id, device="", name=""):
        self.peer_id = peer_id
        self.device = device
        self.name = name or device or peer_id
        self.colour = peer_colour(peer_id)
        #: ``{"position": [x,y,z], "rotation": [x,y,z,w]}`` or None — world metres, Y up
        self.head = None
        #: ``[{"position", "rotation", "grip", "trigger"}, ...]`` left, right
        self.hands = []
        self.selection = []
        self.environment = None
        self.scale = 1.0
        self.doc = None
        self.tool = None
        self.last_seen = 0.0
        self.joined_at = 0.0
        self.seq = 0

    def apply(self, update, now):
        """Merge an update dict (missing keys unchanged)."""
        for key in ("head", "hands", "selection", "environment", "scale", "doc", "name", "tool"):
            if key in update and update[key] is not None:
                setattr(self, key, update[key])
        self.last_seen = now
        self.seq += 1
        return self

    def to_dict(self):
        return {"peer_id": self.peer_id, "device": self.device, "name": self.name, "colour": list(self.colour),
                "head": self.head, "hands": list(self.hands), "selection": list(self.selection),
                "environment": self.environment, "scale": self.scale, "doc": self.doc, "tool": self.tool,
                "last_seen": self.last_seen, "seq": self.seq}

    def __repr__(self):
        return "PeerState(%s %r)" % (self.peer_id, self.name)


class PresenceRegistry(object):
    def __init__(self, timeout=PEER_TIMEOUT, clock=None):
        self.timeout = float(timeout)
        self._clock = clock or time.time
        self._peers = {}
        self._lock = threading.RLock()

    def update(self, peer_id, update, device=""):
        """Apply an update; returns ``(state, joined)`` where joined is True the first time."""
        now = self._clock()
        with self._lock:
            state = self._peers.get(peer_id)
            joined = state is None
            if joined:
                state = PeerState(peer_id, device, update.get("name") if isinstance(update, dict) else "")
                state.joined_at = now
                self._peers[peer_id] = state
            state.apply(update or {}, now)
            return state, joined

    def get(self, peer_id):
        with self._lock:
            return self._peers.get(peer_id)

    def peers(self, exclude=None):
        with self._lock:
            return [p for pid, p in self._peers.items() if pid != exclude]

    def expire(self):
        """Drop silent peers; returns the ids removed."""
        now = self._clock()
        with self._lock:
            gone = [pid for pid, p in self._peers.items() if now - p.last_seen > self.timeout]
            for pid in gone:
                del self._peers[pid]
            return gone

    def remove(self, peer_id):
        with self._lock:
            return self._peers.pop(peer_id, None)

    def __len__(self):
        with self._lock:
            return len(self._peers)

    def to_dict(self, exclude=None):
        return {"peers": [p.to_dict() for p in self.peers(exclude)]}


class Lock(object):
    __slots__ = ("object", "holder", "acquired", "expires")

    def __init__(self, object_name, holder, acquired, expires):
        self.object = object_name
        self.holder = holder
        self.acquired = acquired
        self.expires = expires

    def to_dict(self):
        return {"object": self.object, "holder": self.holder, "acquired": self.acquired, "expires": self.expires}


class LockTable(object):
    def __init__(self, ttl=LOCK_TTL, clock=None):
        self.ttl = float(ttl)
        self._clock = clock or time.time
        self._locks = {}
        self._lock = threading.RLock()

    def acquire(self, object_name, holder, ttl=None):
        """Take or renew a lock. Returns ``(granted, current_lock)``."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            current = self._locks.get(object_name)
            if current is not None and current.holder != holder:
                return False, current
            lock = Lock(object_name, holder, current.acquired if current else now, now + float(ttl or self.ttl))
            self._locks[object_name] = lock
            return True, lock

    def release(self, object_name, holder):
        with self._lock:
            current = self._locks.get(object_name)
            if current is None:
                return True
            if current.holder != holder:
                return False
            del self._locks[object_name]
            return True

    def release_all(self, holder):
        with self._lock:
            names = [n for n, l in self._locks.items() if l.holder == holder]
            for n in names:
                del self._locks[n]
            return names

    def holder(self, object_name):
        with self._lock:
            self._expire(self._clock())
            lock = self._locks.get(object_name)
            return lock.holder if lock else None

    def locks(self):
        with self._lock:
            self._expire(self._clock())
            return list(self._locks.values())

    def _expire(self, now):
        for name in [n for n, l in self._locks.items() if l.expires <= now]:
            del self._locks[name]

    def to_dict(self):
        return {"locks": [l.to_dict() for l in self.locks()]}
