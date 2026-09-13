# SPDX-License-Identifier: LGPL-2.1-or-later
"""The VR assembly session: grab a part, bring it close, feel it snap, confirm.

Per frame with a part in hand:

1. the hand pose sets the part's *free* pose (hand ∘ grab offset);
2. the confirmed mates are solved on top of it, so a peg already mated to a
   bore keeps following the hand along and about the bore only;
3. the best new mate :mod:`candidate <xrassembly.detect>` is found; when
   one appears the part is shown snapped to it (a *preview* pose) and a
   ``snap`` event is emitted for the haptics;
4. the trigger confirms the previewed mate (``constraint`` event); the grip
   letting go releases the part where it is.

Parts are named; each carries local :class:`~xrassembly.features.Features`,
a pose, and its mates. :func:`to_freecad.commit` turns the mates into
Assembly workbench joints.
"""

from xrsketch import vecmath as vm

from . import detect
from .mates import Mate, solve


class AssemblyEvent(object):
    __slots__ = ("kind", "part", "candidate", "mate", "time")

    def __init__(self, kind, part, candidate=None, mate=None, time=0.0):
        self.kind = kind  # grab, release, snap, unsnap, constraint, unconstrain
        self.part = part
        self.candidate = candidate
        self.mate = mate
        self.time = time

    def to_dict(self):
        return {"kind": self.kind, "part": self.part, "time": self.time,
                "candidate": self.candidate.to_dict() if self.candidate else None,
                "mate": self.mate.to_dict() if self.mate else None}

    def __repr__(self):
        return "AssemblyEvent(%s %s)" % (self.kind, self.part)


class Part(object):
    __slots__ = ("name", "features", "pose", "fixed", "mates", "label")

    def __init__(self, name, features, pose=None, fixed=False, label=None):
        self.name = name
        self.features = features
        self.pose = pose or vm.Transform.identity()
        self.fixed = fixed
        self.mates = []
        self.label = label or name

    def world_features(self):
        return self.features.world(self.pose)

    def to_dict(self):
        return {"name": self.name, "label": self.label, "pose": self.pose.to_dict(), "fixed": self.fixed,
                "features": self.features.to_dict(), "mates": [m.to_dict() for m in self.mates]}


class AssemblySession(object):
    def __init__(self, params=None, grab_threshold=0.7):
        self.params = params or detect.DetectParams()
        self.parts = {}
        self.grabbed = None
        self._grab_offset = None
        self._free_pose = None
        self.preview = None      # the previewed Candidate
        self.events = []
        self._time = 0.0
        self.grab_threshold = float(grab_threshold)
        self.snap_enabled = True

    # -- parts -----------------------------------------------------------

    def add_part(self, name, features, pose=None, fixed=False, label=None):
        part = Part(name, features, pose, fixed, label)
        self.parts[name] = part
        return part

    def remove_part(self, name):
        if self.grabbed == name:
            self.release()
        return self.parts.pop(name, None)

    def fixed_features(self, exclude=None):
        return {n: p.world_features() for n, p in self.parts.items() if n != exclude and (p.fixed or p.mates or n != self.grabbed)}

    # -- grabbing --------------------------------------------------------

    def grab(self, name, hand_pose):
        part = self.parts[name]
        if part.fixed:
            return None
        self.grabbed = name
        self._grab_offset = vm.compose(hand_pose.inverse(), part.pose)
        self._free_pose = part.pose
        self.preview = None
        self._emit("grab", name)
        return part

    def release(self):
        if self.grabbed is None:
            return None
        name = self.grabbed
        part = self.parts[name]
        if self.preview is not None:
            # A previewed snap that was not confirmed does not stick.
            part.pose = self._solved(part, self._free_pose)
            self._emit("unsnap", name, candidate=self.preview)
            self.preview = None
        self.grabbed = None
        self._grab_offset = None
        self._emit("release", name)
        return name

    # -- per frame -------------------------------------------------------

    def update(self, dt, hand_pose=None, grip=None, trigger=False):
        self._time += float(dt or 0.0)
        if self.grabbed is None or hand_pose is None:
            return False
        if grip is not None and grip < self.grab_threshold:
            self.release()
            return True
        part = self.parts[self.grabbed]
        self._free_pose = vm.compose(hand_pose, self._grab_offset)
        base = self._solved(part, self._free_pose)
        part.pose = base
        best = None
        if self.snap_enabled:
            world = part.features.world(base)
            found = detect.candidates(part.name, world, self.fixed_features(exclude=part.name), self.params, part.mates)
            found = [c for c in found if detect.compatible(c, part.mates)]
            best = found[0] if found else None
        if best is not None:
            part.pose = self._solved(part, self._free_pose, extra=best.mate)
            if self.preview is None or not _same_pair(self.preview.mate, best.mate):
                self.preview = best
                self._emit("snap", part.name, candidate=best)
            else:
                self.preview = best
        elif self.preview is not None:
            self._emit("unsnap", part.name, candidate=self.preview)
            self.preview = None
        if trigger and self.preview is not None:
            self.confirm()
        return True

    def confirm(self):
        """Lock the previewed mate onto the part."""
        if self.grabbed is None or self.preview is None:
            return None
        part = self.parts[self.grabbed]
        mate = self.preview.mate
        part.mates.append(mate)
        part.pose = self._solved(part, self._free_pose)
        self._emit("constraint", part.name, candidate=self.preview, mate=mate)
        self.preview = None
        return mate

    def unconstrain(self, name=None):
        """Remove the last mate of ``name`` (or of the grabbed part)."""
        name = name or self.grabbed
        if name is None or not self.parts[name].mates:
            return None
        mate = self.parts[name].mates.pop()
        self._emit("unconstrain", name, mate=mate)
        return mate

    def add_mate(self, mate):
        part = self.parts[mate.part]
        part.mates.append(mate)
        part.pose = self._solved(part, part.pose)
        return part.pose

    def fix(self, name):
        part = self.parts[name]
        part.fixed = True
        part.mates.append(Mate("fixed", name, "", "", ""))

    def _solved(self, part, free_pose, extra=None):
        mates = list(part.mates) + ([extra] if extra is not None else [])
        if not mates:
            return free_pose
        result = solve(free_pose, mates, part.features, self.fixed_features(exclude=part.name))
        return result.pose

    def residuals(self):
        out = {}
        for part in self.parts.values():
            if part.mates:
                from .mates import residual_of

                out[part.name] = residual_of(part.pose, part.mates, part.features, self.fixed_features(exclude=part.name))
        return out

    # -- events / export -------------------------------------------------

    def _emit(self, kind, part, candidate=None, mate=None):
        self.events.append(AssemblyEvent(kind, part, candidate, mate, self._time))

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def to_dict(self):
        return {"parts": [p.to_dict() for p in self.parts.values()]}

    def all_mates(self):
        return [m for p in self.parts.values() for m in p.mates if m.kind != "fixed"]


def _same_pair(a, b):
    return (a.kind, a.feature, a.other_part, a.other_feature) == (b.kind, b.feature, b.other_part, b.other_feature)
