# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Two-handed manipulation — the signature Gravity Sketch interaction.

Grab with one hand and the thing you hold follows that hand rigidly.  Grab with
the second hand as well and the pair of hands defines a *similarity*: the
midpoint carries the translation, the line between the hands plus their common
roll carries the rotation, and the ratio of their separation carries a uniform
scale.  The same controller drives the whole world (fly through your model by
pulling it towards you) and a selected object.

Formulation
-----------
A gesture is the map

    G(p) = c_now + s · R · (p - c_start)

where ``c`` is the grab centroid (the hand position with one hand, the midpoint
with two), ``R`` the rotation carried by the hands and ``s`` the separation
ratio.  The result written back to the target is ``G ∘ M_base`` where
``M_base`` is the target's transform when the gesture last (re)started.
Because both are similarities the composition is again a similarity, so the
whole thing stays exact and inverts cleanly
(:class:`xrsketch.vecmath.Transform`).

No popping
----------
Whenever the set of grabbing hands changes — the second hand joins or leaves
mid-gesture — the controller *re-baselines*: the current target transform
becomes the new ``M_base`` and the current hand poses become the new anchors,
so the gesture restarts at identity.  The output is therefore continuous
across the transition by construction, not by tuning.

Tremor
------
Each gesture channel gets a *soft* dead zone: the magnitude has the dead zone
subtracted rather than being snapped to zero, so a hand that crosses the
threshold does not jump, and a hand that wanders back inside it returns exactly
to the baseline.  A hard dead zone would either jump or, if the baseline were
re-anchored, let the model creep.  Damping is a first-order low pass with a
time constant in seconds, applied to the *output* transform.
"""

import math

from . import vecmath as vm
from .vecmath import Transform

__all__ = [
    "BimanualController",
    "GrabParams",
    "HandPose",
    "WorldGrab",
    "view_to_env",
]

_UP = (0.0, 1.0, 0.0)
#: hands closer than this cannot define a scale or an axis
MIN_SEPARATION = 1e-4


class HandPose(object):
    """A controller pose: position in metres plus an orientation quaternion."""

    __slots__ = ("position", "rotation")

    def __init__(self, position, rotation=vm.IDENTITY_QUAT):
        self.position = vm.vec3(position)
        self.rotation = vm.quat_normalize(rotation)

    def copy(self):
        return HandPose(self.position, self.rotation)

    def __repr__(self):
        return "HandPose(%s)" % (tuple(round(v, 4) for v in self.position),)


class GrabParams(object):
    """Tuning for :class:`BimanualController`.

    ``dead_zone_*`` are soft thresholds (metres, radians and ``|ln s|``);
    ``damping`` is a first-order time constant in seconds, 0 meaning none.
    ``min_scale``/``max_scale`` clamp the *accumulated* scale of the target,
    not the scale of one gesture.
    """

    __slots__ = ("allow_translate", "allow_rotate", "allow_scale",
                 "dead_zone_translation", "dead_zone_rotation",
                 "dead_zone_scale", "damping", "min_scale", "max_scale")

    def __init__(self, allow_translate=True, allow_rotate=True,
                 allow_scale=True, dead_zone_translation=0.002,
                 dead_zone_rotation=math.radians(0.5),
                 dead_zone_scale=0.005, damping=0.0,
                 min_scale=1.0e-3, max_scale=1.0e3):
        self.allow_translate = bool(allow_translate)
        self.allow_rotate = bool(allow_rotate)
        self.allow_scale = bool(allow_scale)
        self.dead_zone_translation = max(0.0, float(dead_zone_translation))
        self.dead_zone_rotation = max(0.0, float(dead_zone_rotation))
        self.dead_zone_scale = max(0.0, float(dead_zone_scale))
        self.damping = max(0.0, float(damping))
        self.min_scale = float(min_scale)
        self.max_scale = float(max_scale)
        if self.min_scale <= 0.0 or self.max_scale < self.min_scale:
            raise ValueError("invalid scale clamp range")

    def copy(self):
        p = GrabParams()
        for name in self.__slots__:
            setattr(p, name, getattr(self, name))
        return p

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)


def _soft(value, dead_zone):
    """Soft dead zone on a signed magnitude."""
    if dead_zone <= 0.0:
        return value
    if value > dead_zone:
        return value - dead_zone
    if value < -dead_zone:
        return value + dead_zone
    return 0.0


class BimanualController(object):
    """One- and two-handed grabbing, as pure maths on controller poses.

    ``grab``/``release``/``move`` feed poses in; :meth:`update` advances the
    damping and returns the resulting :class:`~xrsketch.vecmath.Transform`.
    Nothing here touches Coin, FreeCAD or a headset, so a whole gesture can be
    replayed in a unit test.
    """

    def __init__(self, params=None, transform=None, target=None):
        self.params = params or GrabParams()
        self._target_transform = (transform.copy() if transform is not None
                                  else Transform())
        self._transform = self._target_transform.copy()
        self._base = self._target_transform.copy()
        self._anchors = {}
        self._poses = {}
        self.target = None
        if target is not None:
            self.attach(target)

    # ------------------------------------------------------------------
    # binding
    # ------------------------------------------------------------------
    def attach(self, target):
        """Bind an object with a ``transform`` attribute; ``None`` unbinds."""
        self.target = target
        if target is not None:
            t = getattr(target, "transform", None)
            if isinstance(t, Transform):
                self.set_transform(t)
        return target

    def detach(self):
        self.cancel()
        self.target = None

    def _write_back(self):
        if self.target is not None:
            self.target.transform = self._transform.copy()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    @property
    def transform(self):
        """The current (damped) transform of the grabbed thing."""
        return self._transform

    @property
    def target_transform(self):
        """The undamped transform the gesture is asking for."""
        return self._target_transform

    @property
    def hands(self):
        """Sorted tuple of the hands currently grabbing."""
        return tuple(sorted(self._anchors))

    @property
    def active(self):
        return bool(self._anchors)

    @property
    def two_handed(self):
        return len(self._anchors) >= 2

    def set_transform(self, transform):
        """Force the transform (external edit); rebaselines any gesture."""
        self._transform = transform.copy()
        self._target_transform = transform.copy()
        self._rebaseline()
        self._write_back()
        return self._transform

    # ------------------------------------------------------------------
    # gesture lifecycle
    # ------------------------------------------------------------------
    def grab(self, hand, position, rotation=vm.IDENTITY_QUAT):
        """Start (or restart) a grab with ``hand``."""
        pose = HandPose(position, rotation)
        self._poses[hand] = pose
        self._anchors[hand] = pose.copy()
        self._rebaseline()
        return self._transform

    def release(self, hand):
        """Stop grabbing with ``hand``; the others carry on seamlessly."""
        if hand not in self._anchors:
            return False
        del self._anchors[hand]
        self._poses.pop(hand, None)
        self._rebaseline()
        return True

    def release_all(self):
        for hand in list(self._anchors):
            self.release(hand)
        return self._transform

    def cancel(self):
        """Drop the gesture, keeping the transform where it is."""
        self._anchors = {}
        self._poses = {}
        self._base = self._target_transform.copy()
        return self._transform

    def move(self, hand, position, rotation=None):
        """Update a hand pose (grabbing or not)."""
        old = self._poses.get(hand)
        rot = rotation
        if rot is None:
            rot = old.rotation if old is not None else vm.IDENTITY_QUAT
        self._poses[hand] = HandPose(position, rot)
        return self._poses[hand]

    def set_poses(self, poses):
        """Update several hands from ``{hand: HandPose|(pos, quat)}``."""
        for hand, pose in (poses or {}).items():
            if pose is None:
                continue
            if isinstance(pose, HandPose):
                self.move(hand, pose.position, pose.rotation)
            elif len(pose) == 2 and hasattr(pose[0], "__len__"):
                self.move(hand, pose[0], pose[1])
            else:
                self.move(hand, pose)
        return self._poses

    def _rebaseline(self):
        """Restart the gesture from the current state, continuously."""
        self._base = self._target_transform.copy()
        for hand in self._anchors:
            pose = self._poses.get(hand)
            if pose is not None:
                self._anchors[hand] = pose.copy()

    # ------------------------------------------------------------------
    # the maths
    # ------------------------------------------------------------------
    def _centroid_frame(self, poses):
        """``(centroid, rotation_matrix, separation)`` for one or two hands."""
        if len(poses) == 1:
            p = poses[0]
            return (p.position, vm.quat_to_mat3(p.rotation), None)
        a, b = poses[0], poses[1]
        centroid = vm.mul(vm.add(a.position, b.position), 0.5)
        axis = vm.sub(b.position, a.position)
        sep = vm.length(axis)
        if sep < MIN_SEPARATION:
            # coincident hands: no usable axis, fall back to hand 0's frame
            return (centroid, vm.quat_to_mat3(a.rotation), None)
        up = vm.add(vm.quat_rotate(a.rotation, _UP),
                    vm.quat_rotate(b.rotation, _UP))
        u, v, w = vm.orthonormal_basis(axis, up)
        return (centroid, vm.mat3_from_columns(u, v, w), sep)

    def gesture(self):
        """The raw gesture transform ``G`` (locks and dead zones applied)."""
        hands = self.hands
        if not hands:
            return Transform()
        anchors = [self._anchors[h] for h in hands[:2]]
        poses = []
        for h in hands[:2]:
            poses.append(self._poses.get(h) or self._anchors[h])
        c0, m0, sep0 = self._centroid_frame(anchors)
        c1, m1, sep1 = self._centroid_frame(poses)

        # rotation
        rot = vm.quat_from_mat3(vm.mat3_mul(m1, vm.mat3_transpose(m0)))
        if not self.params.allow_rotate:
            rot = vm.IDENTITY_QUAT
        else:
            axis, angle = vm.quat_to_axis_angle(rot)
            angle = _soft(angle, self.params.dead_zone_rotation)
            rot = vm.quat_from_axis_angle(axis, angle)

        # scale
        scale = 1.0
        if self.params.allow_scale and sep0 and sep1 and sep0 > MIN_SEPARATION:
            ratio = sep1 / sep0
            if ratio > 0.0:
                scale = math.exp(_soft(math.log(ratio),
                                       self.params.dead_zone_scale))

        # translation of the centroid
        delta = vm.sub(c1, c0)
        if not self.params.allow_translate:
            delta = (0.0, 0.0, 0.0)
        else:
            d = vm.length(delta)
            if d > vm.EPS:
                delta = vm.mul(
                    delta,
                    _soft(d, self.params.dead_zone_translation) / d)
            else:
                delta = (0.0, 0.0, 0.0)
        centre_now = vm.add(c0, delta)

        # clamp the *accumulated* scale
        total = scale * self._base.scale
        clamped = vm.clamp(total, self.params.min_scale, self.params.max_scale)
        if clamped != total and self._base.scale > vm.EPS:
            scale = clamped / self._base.scale

        rotated = vm.quat_rotate(rot, c0)
        translation = vm.sub(centre_now, vm.mul(rotated, scale))
        return Transform(translation, rot, scale)

    def _recompute_target(self):
        if not self._anchors:
            return self._target_transform
        self._target_transform = vm.compose(self.gesture(), self._base)
        return self._target_transform

    # ------------------------------------------------------------------
    # per frame
    # ------------------------------------------------------------------
    def update(self, dt=0.0, poses=None):
        """Advance one frame and return the resulting transform."""
        if poses:
            self.set_poses(poses)
        self._recompute_target()
        tau = self.params.damping
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        if tau <= 0.0:
            self._transform = self._target_transform.copy()
        elif dt > 0.0:
            alpha = 1.0 - math.exp(-dt / tau)
            self._transform = _blend(self._transform, self._target_transform,
                                     alpha)
        self._write_back()
        return self._transform

    def settle(self):
        """Skip the damping and jump to the requested transform."""
        self._recompute_target()
        self._transform = self._target_transform.copy()
        self._write_back()
        return self._transform

    def __repr__(self):
        return "BimanualController(hands=%s, %r)" % (self.hands,
                                                     self._transform)


def _blend(a, b, alpha):
    alpha = vm.clamp(float(alpha), 0.0, 1.0)
    translation = vm.lerp(a.translation, b.translation, alpha)
    rotation = vm.quat_slerp(a.rotation, b.rotation, alpha)
    # geometric interpolation of scale: doubling always feels the same,
    # matching xrenv.scale.ScaleController.step()
    la = math.log(max(vm.EPS, a.scale))
    lb = math.log(max(vm.EPS, b.scale))
    return Transform(translation, rotation, math.exp(la + (lb - la) * alpha))


# --------------------------------------------------------------------------
# grabbing the world
# --------------------------------------------------------------------------

def view_to_env(controller, point):
    """Map a viewer-space point to environment metres for a ScaleController."""
    s = controller.world_scale * controller.unit_scale
    if abs(s) < vm.EPS:
        return vm.vec3(point)
    o = controller.world_offset
    return ((point[0] - o[0]) / s, (point[1] - o[1]) / s,
            (point[2] - o[2]) / s)


class WorldGrab(object):
    """Grab the whole world with both hands.

    Scale is *not* reimplemented here: the uniform part of the gesture is fed
    into :class:`xrenv.scale.ScaleController`, which already owns the
    user-scale story (shrinking the user by growing the world, the held pivot,
    the clip planes).  Pulling the hands apart makes the world bigger, which
    is the same thing as making the user smaller, so the user scale is
    multiplied by the gesture's scale.

    What the ScaleController cannot express — the rigid rotation and the
    translation of the world about the grab midpoint — is returned as
    :attr:`rigid`, a transform the viewer applies to the world root node.
    """

    def __init__(self, scale_controller, params=None):
        self.scale = scale_controller
        if params is None:
            params = GrabParams(damping=0.05)
        self.grab_controller = BimanualController(params)
        self._base_user_scale = float(scale_controller.scale)
        self.rigid = Transform()

    @property
    def active(self):
        return self.grab_controller.active

    def grab(self, hand, position, rotation=vm.IDENTITY_QUAT):
        self._base_user_scale = float(self.scale.scale)
        self.grab_controller.set_transform(self.rigid)
        return self.grab_controller.grab(hand, position, rotation)

    def release(self, hand):
        ok = self.grab_controller.release(hand)
        self._base_user_scale = float(self.scale.scale)
        return ok

    def move(self, hand, position, rotation=None):
        return self.grab_controller.move(hand, position, rotation)

    def update(self, dt=0.0, poses=None):
        """Apply one frame of world grabbing.

        Returns ``(user_scale, rigid_transform)``.
        """
        if not self.grab_controller.active:
            return (self.scale.scale, self.rigid)
        t = self.grab_controller.update(dt, poses)
        gesture_scale = t.scale
        if abs(gesture_scale - 1.0) > 1e-9:
            pivot_view = self.grab_controller.gesture().translation
            hands = self.grab_controller.hands
            poses_now = [self.grab_controller._poses.get(h)
                         for h in hands[:2]]
            poses_now = [p for p in poses_now if p is not None]
            if poses_now:
                pivot_view = poses_now[0].position
                if len(poses_now) > 1:
                    pivot_view = vm.mul(vm.add(poses_now[0].position,
                                               poses_now[1].position), 0.5)
            pivot_env = view_to_env(self.scale, pivot_view)
            self.scale.scale_about_point(
                pivot_env, self._base_user_scale * gesture_scale,
                animate=False)
        self.rigid = Transform(t.translation, t.rotation, 1.0)
        return (self.scale.scale, self.rigid)
