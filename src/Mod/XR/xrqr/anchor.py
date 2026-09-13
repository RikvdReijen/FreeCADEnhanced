# SPDX-License-Identifier: LGPL-2.1-or-later
"""Snapping the world to a code.

``snap_to_code(payload, code_pose, current)`` returns the transform that
puts the thing the payload names (the model origin, a part, the
environment) onto the code, respecting ``up``. The session keeps the codes
it has seen, filters repeated detections of the same code (a scanner sees
it thirty times a second), and produces one ``snap`` event per settled
detection, so the haptics tick once.
"""

from xrsketch import vecmath as vm

from .payload import AnchorPayload
from .pose import CodePose, pose_from_corners, up_correction


class Snap(object):
    __slots__ = ("payload", "code", "transform", "what", "scale_error")

    def __init__(self, payload, code, transform, what):
        self.payload = payload
        self.code = code
        #: world transform to apply to ``what``
        self.transform = transform
        #: "model" | "env" | "part:<name>" | "target:<anchor>"
        self.what = what
        self.scale_error = code.scale_error

    def to_dict(self):
        return {"payload": self.payload.to_dict(), "code": self.code.to_dict(), "transform": self.transform.to_dict(),
                "what": self.what, "scale_error": self.scale_error}

    def __repr__(self):
        return "Snap(%s -> %s)" % (self.payload.id, self.what)


def snap_to_code(payload, code, current=None):
    """The transform that places ``payload.origin`` at the code.

    The code frame is (centre, +X along the top edge, +Z out of the paper).
    The model's ``up`` axis is turned onto +Z first, so a code lying on the
    bench puts the model's Z up and a code on the wall (``up=y``) puts its
    Y along the wall's normal. ``current`` is the thing's current transform
    (scale is kept from it).
    """
    scale = current.scale if current is not None else 1.0
    fix = up_correction(payload.up)
    base = vm.Transform((0, 0, 0), fix, scale)
    transform = vm.compose(code.transform, base)
    what = "target:%s" % payload.target if payload.target else payload.origin
    return Snap(payload, code, transform, what)


class QrSession(object):
    """Detections in, settled snaps out."""

    def __init__(self, settle_count=3, max_residual=0.004, max_scale_error=0.05, rescan_after=2.0):
        self.settle_count = int(settle_count)
        self.max_residual = float(max_residual)
        self.max_scale_error = float(max_scale_error)
        self.rescan_after = float(rescan_after)
        self.seen = {}       # id -> [CodePose, ...] recent
        self.snapped = {}    # id -> (Snap, time)
        self.events = []
        self._time = 0.0

    def tick(self, dt):
        self._time += float(dt or 0.0)

    def detect(self, text, corners, time=None):
        """Feed one detection. Returns a Snap when the code has settled, else None."""
        if time is not None:
            self._time = float(time)
        try:
            payload = AnchorPayload.decode(text)
        except ValueError as exc:
            self.events.append(_QrEvent("ignored", {"reason": str(exc)}))
            return None
        try:
            code = pose_from_corners(corners, payload.size_mm)
        except ValueError as exc:
            self.events.append(_QrEvent("ignored", {"reason": str(exc), "id": payload.id}))
            return None
        if code.residual > self.max_residual:
            self.events.append(_QrEvent("rejected", {"id": payload.id, "residual": code.residual}))
            return None
        if abs(code.scale_error) > self.max_scale_error:
            self.events.append(_QrEvent("rejected", {"id": payload.id, "scale_error": code.scale_error}))
            return None
        recent = self.seen.setdefault(payload.id, [])
        recent.append(code)
        if len(recent) > self.settle_count:
            del recent[:-self.settle_count]
        previous = self.snapped.get(payload.id)
        if previous is not None and self._time - previous[1] < self.rescan_after:
            return None
        if len(recent) < self.settle_count:
            self.events.append(_QrEvent("seen", {"id": payload.id, "count": len(recent)}))
            return None
        averaged = _average_pose(recent, payload.size_mm)
        snap = snap_to_code(payload, averaged)
        self.snapped[payload.id] = (snap, self._time)
        self.seen[payload.id] = []
        self.events.append(_QrEvent("snap", {"id": payload.id, "what": snap.what, "scale_error": snap.scale_error,
                                             "magnitude": None}))
        return snap

    def forget(self, code_id=None):
        if code_id is None:
            self.snapped = {}
            self.seen = {}
        else:
            self.snapped.pop(code_id, None)
            self.seen.pop(code_id, None)

    def drain_events(self):
        events, self.events = self.events, []
        return events


def _average_pose(codes, size_mm):
    """Average the corners of several detections and refit."""
    n = float(len(codes))
    corners = []
    for k in range(4):
        total = (0.0, 0.0, 0.0)
        for c in codes:
            total = vm.add(total, c.corners[k])
        corners.append(vm.mul(total, 1.0 / n))
    return pose_from_corners(corners, size_mm)


class _QrEvent(object):
    __slots__ = ("kind", "detail")

    def __init__(self, kind, detail):
        self.kind = kind
        self.detail = detail

    def __repr__(self):
        return "QrEvent(%s %s)" % (self.kind, self.detail)
