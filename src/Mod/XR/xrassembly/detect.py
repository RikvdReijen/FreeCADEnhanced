# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mate candidates from where the hand is holding the part.

While a part is grabbed, every one of its features is compared with every
feature of the fixed parts. A pair that is nearly aligned and nearly
touching is a candidate, scored by how close it is; the best candidate is
what the session snaps to and the haptics announce. Thresholds are loose on
purpose — the hand is not a coordinate-measuring machine — and the snap
itself makes the mate exact.
"""

import math

from xrsketch import vecmath as vm

from .mates import Mate


class DetectParams(object):
    __slots__ = ("distance", "angle_deg", "axis_distance", "point_distance", "radius_tolerance", "max_candidates")

    def __init__(self, distance=0.01, angle_deg=15.0, axis_distance=0.008, point_distance=0.006,
                 radius_tolerance=0.25, max_candidates=5):
        #: plane-to-plane gap within which a coincident mate is offered, metres
        self.distance = float(distance)
        #: angular slack for normals / axes, degrees
        self.angle_deg = float(angle_deg)
        #: axis-to-axis offset within which a concentric mate is offered, metres
        self.axis_distance = float(axis_distance)
        self.point_distance = float(point_distance)
        #: relative radius mismatch still offered as concentric (a 10 mm peg in a 12 mm bore, not a 30 mm one)
        self.radius_tolerance = float(radius_tolerance)
        self.max_candidates = int(max_candidates)


class Candidate(object):
    __slots__ = ("mate", "score", "distance", "angle_deg", "note")

    def __init__(self, mate, score, distance, angle_deg, note=""):
        self.mate = mate
        #: 0 (far) .. 1 (already satisfied)
        self.score = float(score)
        self.distance = float(distance)
        self.angle_deg = float(angle_deg)
        self.note = note

    def to_dict(self):
        return {"mate": self.mate.to_dict(), "score": self.score, "distance": self.distance,
                "angle_deg": self.angle_deg, "note": self.note}

    def __repr__(self):
        return "Candidate(%r, score=%.2f)" % (self.mate, self.score)


def _angle_deg(a, b):
    return math.degrees(vm.angle_between(a, b))


def _score(distance, max_distance, angle, max_angle):
    d = max(0.0, 1.0 - distance / max_distance) if max_distance > 0 else 1.0
    a = max(0.0, 1.0 - angle / max_angle) if max_angle > 0 else 1.0
    return d * a


def candidates(part, world_features, fixed, params=None, existing=()):
    """Mate candidates for ``part`` whose features are ``world_features`` (in
    world space) against ``fixed``: ``{part_name: Features (world)}``.

    ``existing`` mates are skipped (same pair) so a confirmed mate is not
    offered again, and a second candidate is only offered when it is
    compatible with the first (a plane after an axis, an axis after a plane,
    never two of the same pair).
    """
    params = params or DetectParams()
    taken = {(m.feature, m.other_part, m.other_feature) for m in existing}
    found = []
    for other_name, others in fixed.items():
        if other_name == part:
            continue
        for mine in world_features:
            for theirs in others:
                if (mine.name, other_name, theirs.name) in taken:
                    continue
                c = _pair(part, mine, other_name, theirs, params)
                if c is not None:
                    found.append(c)
    found.sort(key=lambda c: -c.score)
    return found[: params.max_candidates]


def _pair(part, mine, other_name, theirs, p):
    if mine.kind == "plane" and theirs.kind == "plane":
        angle = _angle_deg(mine.normal, vm.neg(theirs.normal))
        flush = False
        if angle > p.angle_deg:
            angle_flush = _angle_deg(mine.normal, theirs.normal)
            if angle_flush > p.angle_deg:
                return None
            angle, flush = angle_flush, True
        gap = abs(theirs.distance_to_point(mine.origin))
        if gap > p.distance:
            return None
        # the patches must overlap in the plane, roughly
        lateral = vm.sub(mine.origin, theirs.origin)
        lateral = vm.sub(lateral, vm.mul(theirs.normal, vm.dot(lateral, theirs.normal)))
        if vm.length(lateral) > mine.extent + theirs.extent + p.distance:
            return None
        mate = Mate("coincident", part, mine.name, other_name, theirs.name, flush=flush)
        return Candidate(mate, _score(gap, p.distance, angle, p.angle_deg), gap, angle,
                         "flush" if flush else "face to face")
    if mine.kind == "axis" and theirs.kind == "axis":
        angle = min(_angle_deg(mine.direction, theirs.direction), _angle_deg(mine.direction, vm.neg(theirs.direction)))
        if angle > p.angle_deg:
            return None
        if mine.radius > 0 and theirs.radius > 0:
            rel = abs(mine.radius - theirs.radius) / max(mine.radius, theirs.radius)
            if rel > p.radius_tolerance:
                return None
        offset = mine.distance_to_line(theirs)
        if offset > p.axis_distance:
            return None
        mate = Mate("concentric", part, mine.name, other_name, theirs.name)
        note = "r %.3g in r %.3g" % (mine.radius, theirs.radius) if mine.radius and theirs.radius else "coaxial"
        return Candidate(mate, _score(offset, p.axis_distance, angle, p.angle_deg), offset, angle, note)
    if mine.kind == "point" and theirs.kind == "point":
        d = vm.dist(mine.origin, theirs.origin)
        if d > p.point_distance:
            return None
        return Candidate(Mate("point", part, mine.name, other_name, theirs.name), _score(d, p.point_distance, 0.0, 1.0), d, 0.0)
    return None


def compatible(candidate, existing):
    """Can this candidate be added on top of the confirmed mates?

    Two concentric mates on non-parallel axes, or three planes, over-constrain
    a rigid part; those are refused here so the solver never has to fight.
    """
    kinds = [m.kind for m in existing]
    if "fixed" in kinds:
        return False
    k = candidate.mate.kind
    if k == "concentric" and kinds.count("concentric") >= 1:
        return False
    if k in ("coincident", "distance") and kinds.count("coincident") + kinds.count("distance") >= 2:
        return False
    if k == "point" and len(kinds) >= 2:
        return False
    return True
