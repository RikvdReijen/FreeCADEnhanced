# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mate constraints and the closed-form solver that snaps a part to them.

A :class:`Mate` joins a feature on the moving part to a feature on a fixed
part. The solver applies the mates of a part *in order*, each one removing
degrees of freedom while keeping the earlier mates satisfied, and leaves
whatever freedom is left exactly where the hand had it. That is what makes
snapping feel right in VR: a peg dropped into a bore keeps the rotation
about the bore and the depth the hand chose, until a face mate fixes the
depth too.

Mate kinds (``Mate.KINDS``):

``coincident``  plane on plane, normals opposed (or ``flush``: aligned), with ``offset``
``concentric``  axis on axis (either direction; ``align`` forces the same direction)
``parallel``    plane/plane or axis/axis directions parallel, position free
``distance``    a coincident mate at ``offset``
``angle``       plane/plane or axis/axis at ``angle_deg``
``point``       point on point
``fixed``       the moving part's pose is frozen where it is

The solver is exact for the combinations that come up when placing a part
by hand (one or two mates); a third mate that fights the first two is
reported through :class:`SolveResult.residual` rather than silently
satisfied by breaking an earlier one.
"""

import math

from xrsketch import vecmath as vm


class Mate(object):
    KINDS = ("coincident", "concentric", "parallel", "distance", "angle", "point", "fixed")
    __slots__ = ("kind", "part", "feature", "other_part", "other_feature", "offset", "flush", "align", "angle_deg", "name")

    def __init__(self, kind, part, feature, other_part, other_feature, offset=0.0, flush=False, align=False,
                 angle_deg=0.0, name=""):
        if kind not in self.KINDS:
            raise ValueError("unknown mate kind %r" % (kind,))
        self.kind = kind
        self.part = part
        self.feature = feature
        self.other_part = other_part
        self.other_feature = other_feature
        self.offset = float(offset)
        self.flush = bool(flush)
        self.align = bool(align)
        self.angle_deg = float(angle_deg)
        self.name = name or "%s_%s_%s" % (kind, feature, other_feature)

    def to_dict(self):
        return {"kind": self.kind, "part": self.part, "feature": self.feature, "other_part": self.other_part,
                "other_feature": self.other_feature, "offset": self.offset, "flush": self.flush,
                "align": self.align, "angle_deg": self.angle_deg, "name": self.name}

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], d["part"], d["feature"], d["other_part"], d["other_feature"], d.get("offset", 0.0),
                   d.get("flush", False), d.get("align", False), d.get("angle_deg", 0.0), d.get("name", ""))

    def __repr__(self):
        return "Mate(%s %s.%s -> %s.%s)" % (self.kind, self.part, self.feature, self.other_part, self.other_feature)


class SolveResult(object):
    __slots__ = ("pose", "satisfied", "residual", "notes")

    def __init__(self, pose, satisfied, residual=0.0, notes=()):
        self.pose = pose
        self.satisfied = list(satisfied)
        self.residual = float(residual)
        self.notes = list(notes)

    @property
    def ok(self):
        return self.residual < 1e-6

    def __repr__(self):
        return "SolveResult(%d mates, residual=%.3g)" % (len(self.satisfied), self.residual)


# ----------------------------------------------------------------------
# rotation helpers
# ----------------------------------------------------------------------


def rotation_between(a, b):
    """Quaternion (x, y, z, w) rotating unit vector ``a`` onto ``b``."""
    a, b = vm.normalize(a), vm.normalize(b)
    d = vm.dot(a, b)
    if d > 1.0 - 1e-12:
        return vm.IDENTITY_QUAT
    if d < -1.0 + 1e-12:
        axis = vm.normalize(vm.any_perp(a))
        return (axis[0], axis[1], axis[2], 0.0)
    c = vm.cross(a, b)
    q = (c[0], c[1], c[2], 1.0 + d)
    return vm.quat_normalize(q)


def rotation_about(axis, angle):
    axis = vm.normalize(axis)
    s = math.sin(angle * 0.5)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle * 0.5))


def _rotate_pose(pose, q, pivot):
    """Rotate a pose by ``q`` about world point ``pivot``."""
    rotation = vm.quat_normalize(vm.quat_mul(q, pose.rotation)) if hasattr(vm, "quat_mul") else _qmul(q, pose.rotation)
    offset = vm.sub(pose.translation, pivot)
    translation = vm.add(pivot, _qrot(q, offset))
    return vm.Transform(translation, rotation, pose.scale)


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return vm.quat_normalize((aw * bx + ax * bw + ay * bz - az * by,
                              aw * by - ax * bz + ay * bw + az * bx,
                              aw * bz + ax * by - ay * bx + az * bw,
                              aw * bw - ax * bx - ay * by - az * bz))


def _qrot(q, v):
    return vm.Transform((0, 0, 0), q).apply_vector(v)


def _translate_pose(pose, delta):
    return vm.Transform(vm.add(pose.translation, delta), pose.rotation, pose.scale)


# ----------------------------------------------------------------------
# the solver
# ----------------------------------------------------------------------


def solve(pose, mates, features, world_features, locked_dirs=None):
    """Snap ``pose`` (the moving part's world transform) to satisfy ``mates``.

    ``features`` are the moving part's local features; ``world_features``
    maps ``other_part`` names to their features already in world space.
    Applied in order; each mate only moves the part within the freedom the
    earlier mates left. Returns a :class:`SolveResult`.
    """
    notes = []
    satisfied = []
    fixed_dir = None      # a world direction the part may only rotate about (after a plane/axis mate)
    fixed_point = None    # a world point the part may only rotate about / translate along fixed_dir
    fixed_kind = None     # "plane" (translate in plane, rotate about normal) or "axis" (translate along, rotate about)
    for mate in mates:
        if mate.kind == "fixed":
            satisfied.append(mate)
            continue
        local = features.get(mate.feature)
        others = world_features.get(mate.other_part)
        target = others.get(mate.other_feature) if others is not None else None
        if local is None or target is None:
            notes.append("%s: feature missing" % mate)
            continue
        current = local.transformed(pose)
        current._local = local
        if mate.kind in ("coincident", "distance", "parallel", "angle") and local.kind == "plane" and target.kind == "plane":
            pose = _plane_to_plane(pose, current, target, mate, fixed_dir, fixed_point, fixed_kind, notes)
            if mate.kind in ("coincident", "distance"):
                if fixed_kind is None:
                    fixed_dir, fixed_point, fixed_kind = target.normal, target.origin, "plane"
                else:
                    fixed_kind = "full"
            elif fixed_kind is None:
                fixed_dir, fixed_kind = target.normal, "dir"
        elif mate.kind in ("concentric", "parallel", "angle") and local.kind == "axis" and target.kind == "axis":
            pose = _axis_to_axis(pose, current, target, mate, fixed_dir, fixed_point, fixed_kind, notes)
            if mate.kind == "concentric":
                if fixed_kind is None:
                    fixed_dir, fixed_point, fixed_kind = target.direction, target.origin, "axis"
                elif fixed_kind == "plane":
                    fixed_kind = "full" if abs(vm.dot(fixed_dir, target.direction)) > 0.999 else "full"
                else:
                    fixed_kind = "full"
            elif fixed_kind is None:
                fixed_dir, fixed_kind = target.direction, "dir"
        elif mate.kind == "point" and local.kind == "point" and target.kind == "point":
            pose = _point_to_point(pose, current, target, fixed_dir, fixed_point, fixed_kind, notes)
            fixed_kind = "full" if fixed_kind else "point"
            fixed_point = target.origin if fixed_point is None else fixed_point
        elif mate.kind in ("parallel", "angle") and local.kind != target.kind:
            notes.append("%s: parallel/angle needs two planes or two axes" % mate)
            continue
        else:
            notes.append("%s: cannot mate a %s to a %s" % (mate, local.kind, target.kind))
            continue
        satisfied.append(mate)
    residual = residual_of(pose, satisfied, features, world_features)
    return SolveResult(pose, satisfied, residual, notes)


def _plane_to_plane(pose, current, target, mate, fixed_dir, fixed_point, fixed_kind, notes):
    want = target.normal if mate.flush else vm.neg(target.normal)
    if mate.kind == "angle":
        # rotate the current normal to the requested angle from the target normal
        angle = math.radians(mate.angle_deg)
        axis = vm.cross(target.normal, current.normal)
        if vm.length(axis) < 1e-9:
            axis = vm.any_perp(target.normal)
        want = _qrot(rotation_about(axis, angle), target.normal)
    pose = _align_direction(pose, current.normal, want, current.origin, fixed_dir, fixed_kind)
    if mate.kind in ("parallel", "angle"):
        return pose
    # translate along the target normal so the planes are `offset` apart
    current = _refresh(current, pose)
    gap = vm.dot(target.normal, vm.sub(current.origin, target.origin)) - mate.offset
    move = vm.mul(target.normal, -gap)
    if fixed_kind == "axis" and fixed_dir is not None:
        # only allowed to slide along the fixed axis
        along = vm.dot(move, fixed_dir)
        if abs(vm.length(move) - abs(along)) > 1e-6 * max(1.0, vm.length(move)):
            notes.append("%s: plane offset not reachable along the mated axis; slid as far as possible" % mate)
        move = vm.mul(fixed_dir, along)
    elif fixed_kind == "plane" and fixed_dir is not None:
        # already on a plane: may only move within it
        normal_part = vm.dot(move, fixed_dir)
        move = vm.sub(move, vm.mul(fixed_dir, normal_part))
        if abs(normal_part) > 1e-6:
            notes.append("%s: conflicts with the earlier plane mate by %.3g" % (mate, normal_part))
    return _translate_pose(pose, move)


def _axis_to_axis(pose, current, target, mate, fixed_dir, fixed_point, fixed_kind, notes):
    want = target.direction
    if not mate.align and vm.dot(current.direction, want) < 0.0:
        want = vm.neg(want)
    if mate.kind == "angle":
        axis = vm.cross(target.direction, current.direction)
        if vm.length(axis) < 1e-9:
            axis = vm.any_perp(target.direction)
        want = _qrot(rotation_about(axis, math.radians(mate.angle_deg)), target.direction)
    pose = _align_direction(pose, current.direction, want, current.origin, fixed_dir, fixed_kind)
    if mate.kind != "concentric":
        return pose
    current = _refresh(current, pose)
    # move the current axis line onto the target line (perpendicular offset only)
    foot = target.closest_point_on_axis(current.origin)
    move = vm.sub(foot, current.origin)
    if fixed_kind == "plane" and fixed_dir is not None:
        normal_part = vm.dot(move, fixed_dir)
        move = vm.sub(move, vm.mul(fixed_dir, normal_part))
        if abs(normal_part) > 1e-6:
            notes.append("%s: axis not reachable within the mated plane by %.3g" % (mate, normal_part))
    elif fixed_kind == "axis" and fixed_dir is not None:
        along = vm.dot(move, fixed_dir)
        move = vm.mul(fixed_dir, along)
    return _translate_pose(pose, move)


def _point_to_point(pose, current, target, fixed_dir, fixed_point, fixed_kind, notes):
    move = vm.sub(target.origin, current.origin)
    if fixed_kind == "plane" and fixed_dir is not None:
        move = vm.sub(move, vm.mul(fixed_dir, vm.dot(move, fixed_dir)))
    elif fixed_kind == "axis" and fixed_dir is not None:
        move = vm.mul(fixed_dir, vm.dot(move, fixed_dir))
    elif fixed_kind == "full":
        notes.append("point mate ignored: no freedom left")
        return pose
    return _translate_pose(pose, move)


def _align_direction(pose, have, want, pivot, fixed_dir, fixed_kind):
    """Rotate the part so ``have`` points along ``want``, about ``pivot``.

    With an earlier mate fixing a direction, only rotation about that
    direction is allowed: the best such rotation is the one aligning the
    projections of ``have`` and ``want`` onto the plane perpendicular to it.
    """
    if fixed_kind in ("plane", "axis", "dir") and fixed_dir is not None:
        h = vm.sub(have, vm.mul(fixed_dir, vm.dot(have, fixed_dir)))
        w = vm.sub(want, vm.mul(fixed_dir, vm.dot(want, fixed_dir)))
        if vm.length(h) < 1e-9 or vm.length(w) < 1e-9:
            return pose
        h, w = vm.normalize(h), vm.normalize(w)
        angle = math.atan2(vm.dot(vm.cross(h, w), fixed_dir), vm.dot(h, w))
        return _rotate_pose(pose, rotation_about(fixed_dir, angle), pivot)
    if fixed_kind == "full":
        return pose
    return _rotate_pose(pose, rotation_between(have, want), pivot)


def _refresh(feature, pose):
    """Re-express a world feature after the pose changed."""
    local = getattr(feature, "_local", None)
    return local.transformed(pose) if local is not None else feature


def residual_of(pose, mates, features, world_features):
    """How far the mates are from satisfied, as a single distance-like number."""
    worst = 0.0
    for mate in mates:
        if mate.kind == "fixed":
            continue
        local = features.get(mate.feature)
        others = world_features.get(mate.other_part)
        target = others.get(mate.other_feature) if others else None
        if local is None or target is None:
            continue
        current = local.transformed(pose)
        if local.kind == "plane" and target.kind == "plane":
            want = target.normal if mate.flush else vm.neg(target.normal)
            if mate.kind == "angle":
                ang = math.degrees(vm.angle_between(current.normal, target.normal))
                worst = max(worst, abs(ang - mate.angle_deg) * math.pi / 180.0)
                continue
            worst = max(worst, vm.length(vm.sub(current.normal, want)))
            if mate.kind in ("coincident", "distance"):
                worst = max(worst, abs(vm.dot(target.normal, vm.sub(current.origin, target.origin)) - mate.offset))
        elif local.kind == "axis" and target.kind == "axis":
            c = abs(vm.dot(current.direction, target.direction))
            if mate.kind == "angle":
                ang = math.degrees(math.acos(max(-1.0, min(1.0, c))))
                worst = max(worst, abs(ang - mate.angle_deg) * math.pi / 180.0)
                continue
            worst = max(worst, 1.0 - c)
            if mate.kind == "concentric":
                worst = max(worst, current.distance_to_line(target))
        elif local.kind == "point" and target.kind == "point":
            worst = max(worst, vm.dist(current.origin, target.origin))
    return worst
