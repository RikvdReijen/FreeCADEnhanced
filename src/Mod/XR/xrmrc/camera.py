# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""The mixed-reality-capture camera: poses, smoothing and lens settings.

This module is the arithmetic floor of :mod:`xrmrc`.  It carries the vector and
quaternion helpers, the :class:`Pose` type, the handedness conversion between
OpenXR and the Unity convention that ``externalcamera.cfg`` is written in, the
pose sources a capture camera can be driven from, the damping that makes
tracked footage watchable, and the lens model shared with the existing
third-person (TPP) camera preferences.

Conventions
-----------

Everything in this package is in the **OpenXR** convention, which is also
OpenVR's: right handed, **Y up**, **-Z forward**, metres.  That is the same
frame ``xrcore.commonXR`` locates the HMD, the controllers and the Vive tracker
in, so a pose produced here can be handed straight to an ``SoPerspectiveCamera``
the way :meth:`~xrcore.commonXR.XRwidget.update_tpp_camera` already does.

``externalcamera.cfg`` is the one exception: its ``x/y/z/rx/ry/rz`` fields are
in **Unity's** left-handed, +Z-forward frame, because the file format is defined
by a Unity component.  :func:`unity_to_xr_position` and
:func:`unity_to_xr_orientation` are the bridge, and
:mod:`xrmrc.externalcamera` is the only place that needs them.

Quaternions are ``(x, y, z, w)`` tuples — the same order as
``XrQuaternionf`` and ``pivy.coin.SbRotation`` — and rotate a vector by
``q * v * q⁻¹``.  Rotation matrices are row-major 3-tuples of 3-tuples acting on
column vectors.

Pure stdlib: no ``FreeCAD``, no ``pivy.coin``, no ``numpy`` (ARCHITECTURE.md §6).
"""

import math

__all__ = [
    # types
    "Pose",
    "CameraContext",
    "LensSettings",
    "PoseSmoother",
    "MRCCamera",
    # pose sources
    "PoseSource",
    "FixedPose",
    "TrackedPose",
    "FollowHmd",
    "Orbit",
    "SOURCES",
    "make_source",
    # vectors
    "v_add",
    "v_sub",
    "v_scale",
    "v_dot",
    "v_cross",
    "v_length",
    "v_normalize",
    "v_lerp",
    "horizontal",
    # quaternions
    "q_identity",
    "q_mul",
    "q_conj",
    "q_normalize",
    "q_dot",
    "q_from_axis_angle",
    "q_rotate",
    "q_slerp",
    "q_from_matrix3",
    "q_to_matrix3",
    "q_look_rotation",
    # unity interop
    "unity_euler_to_quat",
    "quat_to_unity_euler",
    "unity_to_xr_position",
    "xr_to_unity_position",
    "unity_to_xr_orientation",
    "xr_to_unity_orientation",
    "matrix34_to_pose",
    "pose_to_matrix34",
    # helpers
    "clamp",
    "smoothing_alpha",
]

_EPS = 1e-12

# --------------------------------------------------------------------------
# scalars and vectors
# --------------------------------------------------------------------------


def clamp(value, low, high):
    """``value`` limited to ``[low, high]``, tolerating an inverted range."""
    if low > high:
        low, high = high, low
    if value < low:
        return low
    if value > high:
        return high
    return value


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_length(a):
    return math.sqrt(v_dot(a, a))


def v_normalize(a, fallback=(0.0, 0.0, -1.0)):
    """Unit vector, or ``fallback`` when ``a`` is degenerate."""
    length = v_length(a)
    if length < _EPS:
        return fallback
    return (a[0] / length, a[1] / length, a[2] / length)


def v_lerp(a, b, t):
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def horizontal(vector, fallback=(0.0, 0.0, -1.0)):
    """``vector`` flattened onto the ground plane and normalised.

    The foreground/background split of §"quadrant MRC" is computed against a
    *horizontal* forward vector, exactly as the reference implementation does,
    so that tilting the camera up or down does not move the person in and out
    of the foreground.  A camera pointing straight up or down has no horizontal
    forward at all; that degenerate case returns ``fallback``.
    """
    flat = (vector[0], 0.0, vector[2])
    if v_length(flat) < 1e-6:
        return fallback
    return v_normalize(flat, fallback)


# --------------------------------------------------------------------------
# quaternions
# --------------------------------------------------------------------------


def q_identity():
    return (0.0, 0.0, 0.0, 1.0)


def q_mul(a, b):
    """Hamilton product.  ``q_mul(a, b)`` applies ``b`` first, then ``a``."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def q_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def q_normalize(q):
    length = math.sqrt(q_dot(q, q))
    if length < _EPS:
        return q_identity()
    return (q[0] / length, q[1] / length, q[2] / length, q[3] / length)


def q_from_axis_angle(axis, angle_rad):
    axis = v_normalize(axis, (0.0, 1.0, 0.0))
    half = angle_rad * 0.5
    s = math.sin(half)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(half))


def q_rotate(q, v):
    """Rotate the vector ``v`` by the quaternion ``q``."""
    x, y, z, w = q
    # t = 2 * (q_vec x v);  v' = v + w*t + q_vec x t
    tx = 2.0 * (y * v[2] - z * v[1])
    ty = 2.0 * (z * v[0] - x * v[2])
    tz = 2.0 * (x * v[1] - y * v[0])
    return (
        v[0] + w * tx + (y * tz - z * ty),
        v[1] + w * ty + (z * tx - x * tz),
        v[2] + w * tz + (x * ty - y * tx),
    )


def q_slerp(a, b, t):
    """Shortest-arc spherical interpolation, clamped to ``t in [0, 1]``.

    The result always lies *on* the arc between the two inputs, so a smoother
    built on it cannot overshoot its target.
    """
    t = clamp(t, 0.0, 1.0)
    a = q_normalize(a)
    b = q_normalize(b)
    cos_half = q_dot(a, b)
    if cos_half < 0.0:  # take the short way round
        b = (-b[0], -b[1], -b[2], -b[3])
        cos_half = -cos_half
    if cos_half > 0.9995:  # nearly parallel, lerp and renormalise
        out = (
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t,
            a[3] + (b[3] - a[3]) * t,
        )
        return q_normalize(out)
    half = math.acos(clamp(cos_half, -1.0, 1.0))
    sin_half = math.sin(half)
    wa = math.sin((1.0 - t) * half) / sin_half
    wb = math.sin(t * half) / sin_half
    return q_normalize(
        (
            a[0] * wa + b[0] * wb,
            a[1] * wa + b[1] * wb,
            a[2] * wa + b[2] * wb,
            a[3] * wa + b[3] * wb,
        )
    )


def q_to_matrix3(q):
    """Row-major 3x3 rotation matrix for ``q`` (column-vector convention)."""
    x, y, z, w = q_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)),
        (2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)),
        (2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)),
    )


def q_from_matrix3(m):
    """Quaternion for a row-major 3x3 rotation matrix (Shepperd's method)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return q_normalize((x, y, z, w))


def q_look_rotation(forward, up=(0.0, 1.0, 0.0)):
    """Orientation whose **-Z** axis points along ``forward``.

    -Z is "where the camera looks" in OpenXR, so this is the rotation to give a
    camera that should aim at a target.
    """
    z_axis = v_scale(v_normalize(forward, (0.0, 0.0, -1.0)), -1.0)
    x_axis = v_cross(up, z_axis)
    if v_length(x_axis) < 1e-6:
        # Looking straight up or down: pick any perpendicular axis.
        x_axis = v_cross((0.0, 0.0, 1.0), z_axis)
        if v_length(x_axis) < 1e-6:
            x_axis = (1.0, 0.0, 0.0)
    x_axis = v_normalize(x_axis, (1.0, 0.0, 0.0))
    y_axis = v_cross(z_axis, x_axis)
    matrix = (
        (x_axis[0], y_axis[0], z_axis[0]),
        (x_axis[1], y_axis[1], z_axis[1]),
        (x_axis[2], y_axis[2], z_axis[2]),
    )
    return q_from_matrix3(matrix)


# --------------------------------------------------------------------------
# Unity interop
#
# Unity is left handed with +Z forward; OpenXR/OpenVR are right handed with -Z
# forward.  The two frames are related by the reflection S = diag(1, 1, -1),
# which is exactly the conversion Valve's ``SteamVR_Utils.RigidTransform``
# performs when it reads an ``HmdMatrix34_t``:
#
#     p_unity = S . p_xr                 (only z flips)
#     R_unity = S . R_xr . S             (rows/columns 2 flip)
#
# and in quaternion terms  q_unity = (-x, -y, z, w).  The map is an involution,
# so the same expression converts in both directions.
# --------------------------------------------------------------------------


def unity_to_xr_position(position):
    return (position[0], position[1], -position[2])


def xr_to_unity_position(position):
    return (position[0], position[1], -position[2])


def unity_to_xr_orientation(q):
    return q_normalize((-q[0], -q[1], q[2], q[3]))


def xr_to_unity_orientation(q):
    return q_normalize((-q[0], -q[1], q[2], q[3]))


def unity_euler_to_quat(rx_deg, ry_deg, rz_deg):
    """Unity's ``Quaternion.Euler(rx, ry, rz)``.

    Unity applies the rotations Z, then X, then Y, so the composite quaternion
    is ``qy * qx * qz``.  The result is still in Unity's frame; feed it through
    :func:`unity_to_xr_orientation` to use it here.
    """
    qx = q_from_axis_angle((1.0, 0.0, 0.0), math.radians(rx_deg))
    qy = q_from_axis_angle((0.0, 1.0, 0.0), math.radians(ry_deg))
    qz = q_from_axis_angle((0.0, 0.0, 1.0), math.radians(rz_deg))
    return q_normalize(q_mul(q_mul(qy, qx), qz))


def quat_to_unity_euler(q):
    """Inverse of :func:`unity_euler_to_quat`, in degrees on ``[0, 360)``.

    Matches ``Quaternion.eulerAngles``: the ZXY decomposition of the rotation
    matrix, wrapped into the positive range the way Unity reports it.
    """
    m = q_to_matrix3(q)
    sin_x = clamp(-m[1][2], -1.0, 1.0)
    x = math.asin(sin_x)
    if abs(sin_x) > 0.9999995:  # gimbal lock: fold z into y
        y = math.atan2(-m[2][0], m[0][0])
        z = 0.0
    else:
        y = math.atan2(m[0][2], m[2][2])
        z = math.atan2(m[1][0], m[1][1])
    return tuple(math.degrees(a) % 360.0 for a in (x, y, z))


# --------------------------------------------------------------------------
# poses
# --------------------------------------------------------------------------


class Pose:
    """A rigid transform: a position in metres and an orientation.

    Immutable by convention.  ``forward`` is -Z, ``up`` is +Y and ``right`` is
    +X, all in the OpenXR frame.
    """

    __slots__ = ("position", "orientation")

    def __init__(self, position=(0.0, 0.0, 0.0), orientation=(0.0, 0.0, 0.0, 1.0)):
        self.position = (float(position[0]), float(position[1]), float(position[2]))
        self.orientation = q_normalize(
            (
                float(orientation[0]),
                float(orientation[1]),
                float(orientation[2]),
                float(orientation[3]),
            )
        )

    # -- construction ---------------------------------------------------

    @classmethod
    def identity(cls):
        return cls((0.0, 0.0, 0.0), q_identity())

    @classmethod
    def looking_at(cls, position, target, up=(0.0, 1.0, 0.0)):
        return cls(position, q_look_rotation(v_sub(target, position), up))

    # -- axes -----------------------------------------------------------

    def forward(self):
        return q_rotate(self.orientation, (0.0, 0.0, -1.0))

    def up(self):
        return q_rotate(self.orientation, (0.0, 1.0, 0.0))

    def right(self):
        return q_rotate(self.orientation, (1.0, 0.0, 0.0))

    # -- algebra --------------------------------------------------------

    def transform_point(self, point):
        return v_add(self.position, q_rotate(self.orientation, point))

    def compose(self, other):
        """``self`` applied to ``other`` — ``other`` expressed in our frame."""
        return Pose(
            self.transform_point(other.position),
            q_mul(self.orientation, other.orientation),
        )

    def inverse(self):
        inv = q_conj(self.orientation)
        return Pose(v_scale(q_rotate(inv, self.position), -1.0), inv)

    def translated(self, offset):
        return Pose(v_add(self.position, offset), self.orientation)

    # -- plumbing -------------------------------------------------------

    def as_dict(self):
        return {"position": list(self.position), "orientation": list(self.orientation)}

    def approx_equal(self, other, tolerance=1e-6):
        if v_length(v_sub(self.position, other.position)) > tolerance:
            return False
        # q and -q are the same rotation.
        return abs(abs(q_dot(self.orientation, other.orientation)) - 1.0) <= tolerance

    def __eq__(self, other):
        if not isinstance(other, Pose):
            return NotImplemented
        return self.position == other.position and self.orientation == other.orientation

    def __hash__(self):
        return hash((self.position, self.orientation))

    def __repr__(self):
        p = ", ".join(f"{c:.4f}" for c in self.position)
        q = ", ".join(f"{c:.4f}" for c in self.orientation)
        return f"Pose(position=({p}), orientation=({q}))"


def matrix34_to_pose(values):
    """Pose from the 12 numbers of an OpenVR ``HmdMatrix34_t``.

    The layout is row-major ``[m0..m11]`` where ``m3``, ``m7`` and ``m11`` are
    the translation, and the frame is right handed — the same as OpenXR — so no
    handedness conversion is applied here.  This is the ``m=`` field of
    ``externalcamera.cfg``.
    """
    if len(values) != 12:
        raise ValueError("an HmdMatrix34_t has exactly 12 values")
    m = tuple(float(v) for v in values)
    rotation = (
        (m[0], m[1], m[2]),
        (m[4], m[5], m[6]),
        (m[8], m[9], m[10]),
    )
    return Pose((m[3], m[7], m[11]), q_from_matrix3(rotation))


def pose_to_matrix34(pose):
    """The 12 numbers of an ``HmdMatrix34_t`` for ``pose``."""
    r = q_to_matrix3(pose.orientation)
    p = pose.position
    return (
        r[0][0], r[0][1], r[0][2], p[0],
        r[1][0], r[1][1], r[1][2], p[1],
        r[2][0], r[2][1], r[2][2], p[2],
    )


# --------------------------------------------------------------------------
# lens
# --------------------------------------------------------------------------


class LensSettings:
    """Field of view and the sensor/focal pair that produces it.

    The workbench already stores a camera lens in the ``TPPCam*`` preferences:
    ``TPPCamVFov`` (degrees), and ``TPPCamAspectW``/``TPPCamAspectH``, whose
    defaults 6.29 and 4.71 are the **millimetre dimensions of a Raspberry Pi HQ
    camera sensor** rather than a plain 4:3-style ratio.  They double as the
    aspect ratio, because ``6.29 / 4.71`` is the shape of the image, and as the
    sensor size, because ``2 * atan(4.71 / (2 * 6)) = 42.88°`` reproduces the
    default vertical FOV for a 6 mm lens exactly.  Both readings are kept here:
    :attr:`aspect` for the projection, :meth:`focal_length` for the lens.
    """

    __slots__ = ("vfov_deg", "sensor_width", "sensor_height")

    def __init__(self, vfov_deg=42.88, sensor_width=6.29, sensor_height=4.71):
        self.vfov_deg = float(vfov_deg)
        self.sensor_width = float(sensor_width)
        self.sensor_height = float(sensor_height)

    # -- derived --------------------------------------------------------

    @property
    def aspect(self):
        if self.sensor_height <= 0.0:
            return 1.0
        return self.sensor_width / self.sensor_height

    @property
    def vfov_rad(self):
        return math.radians(self.vfov_deg)

    @property
    def hfov_deg(self):
        """Horizontal FOV implied by the vertical FOV and the aspect ratio."""
        half = math.atan(math.tan(self.vfov_rad * 0.5) * self.aspect)
        return math.degrees(half * 2.0)

    def focal_length(self):
        """Focal length in millimetres for this FOV and sensor height."""
        half = math.tan(self.vfov_rad * 0.5)
        if half <= 0.0:
            return float("inf")
        return self.sensor_height / (2.0 * half)

    # -- construction ---------------------------------------------------

    @classmethod
    def from_focal_length(cls, focal_mm, sensor_width=6.29, sensor_height=4.71):
        if focal_mm <= 0.0:
            raise ValueError("focal length must be positive")
        vfov = 2.0 * math.atan(sensor_height / (2.0 * focal_mm))
        return cls(math.degrees(vfov), sensor_width, sensor_height)

    @classmethod
    def from_preferences(cls, get_float):
        """Build from the workbench's ``TPPCam*`` keys.

        ``get_float`` is any ``(key, default) -> float`` callable, which is what
        FreeCAD's parameter group offers, so this works with the real
        preferences and with a plain dictionary in a test.
        """
        return cls(
            get_float("TPPCamVFov", 42.88),
            get_float("TPPCamAspectW", 6.29),
            get_float("TPPCamAspectH", 4.71),
        )

    def as_dict(self):
        return {
            "vfov_deg": self.vfov_deg,
            "hfov_deg": self.hfov_deg,
            "aspect": self.aspect,
            "sensor_width": self.sensor_width,
            "sensor_height": self.sensor_height,
            "focal_length_mm": self.focal_length(),
        }

    def __repr__(self):
        return (
            f"LensSettings(vfov_deg={self.vfov_deg:.3f}, "
            f"sensor_width={self.sensor_width:.3f}, "
            f"sensor_height={self.sensor_height:.3f})"
        )


# --------------------------------------------------------------------------
# smoothing
# --------------------------------------------------------------------------


def smoothing_alpha(dt, time_constant):
    """Frame-rate independent blend factor for exponential damping.

    ``1 - exp(-dt / tau)`` lies strictly inside ``[0, 1]`` for any positive
    ``dt`` and ``tau``, so the smoothed value is always a convex combination of
    where it is and where it is going: it converges and never overshoots, no
    matter how large a step the tracker jumps by.  ``tau <= 0`` disables the
    damping (alpha becomes 1, i.e. follow exactly).
    """
    if time_constant <= 0.0 or dt <= 0.0:
        return 1.0 if time_constant <= 0.0 else 0.0
    return 1.0 - math.exp(-dt / time_constant)


class PoseSmoother:
    """Critically damped follower for a jittery pose.

    Position and orientation get separate time constants because a handheld rig
    usually needs the rotation damped harder than the translation.
    """

    def __init__(self, position_tau=0.08, rotation_tau=0.12):
        self.position_tau = float(position_tau)
        self.rotation_tau = float(rotation_tau)
        self._current = None

    @property
    def current(self):
        return self._current

    def reset(self, pose=None):
        """Snap to ``pose`` (or forget the state entirely when ``None``)."""
        self._current = pose
        return pose

    def update(self, target, dt):
        """Step towards ``target`` by ``dt`` seconds and return the new pose."""
        if target is None:
            return self._current
        if self._current is None:
            self._current = target
            return target
        pos_a = smoothing_alpha(dt, self.position_tau)
        rot_a = smoothing_alpha(dt, self.rotation_tau)
        self._current = Pose(
            v_lerp(self._current.position, target.position, pos_a),
            q_slerp(self._current.orientation, target.orientation, rot_a),
        )
        return self._current


# --------------------------------------------------------------------------
# pose sources
# --------------------------------------------------------------------------


class CameraContext:
    """What a pose source is allowed to look at when it produces a pose."""

    __slots__ = ("hmd_pose", "tracker_pose", "target_pose", "time")

    def __init__(self, hmd_pose=None, tracker_pose=None, target_pose=None, time=0.0):
        self.hmd_pose = hmd_pose
        self.tracker_pose = tracker_pose
        self.target_pose = target_pose
        self.time = float(time)

    def anchor(self):
        """The pose an orbit or follow camera should aim at."""
        return self.target_pose or self.hmd_pose


class PoseSource:
    """Base class: turn a :class:`CameraContext` into a camera pose."""

    name = "base"

    def update(self, dt, context):  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self):
        return {"source": self.name}


class FixedPose(PoseSource):
    """A camera bolted to one spot — a tripod that never moves."""

    name = "fixed"

    def __init__(self, pose=None):
        self.pose = pose or Pose.identity()

    def set_pose(self, pose):
        self.pose = pose

    def update(self, dt, context):
        return self.pose

    def describe(self):
        return {"source": self.name, "pose": self.pose.as_dict()}


class TrackedPose(PoseSource):
    """Driven by a tracked device — a Vive tracker with the ``camera`` role.

    ``xrcore.commonXR`` already locates that device every frame in
    :meth:`~xrcore.commonXR.XRwidget.update_tpp_camera`; the hook described in
    MIXED_REALITY_CAPTURE.md forwards the located pose here through
    :meth:`submit`.  The offset is the ``TPPCamXTransl``/``TPPCamXRot`` family
    of preferences: where the lens sits relative to the puck.
    """

    name = "tracked"

    def __init__(self, offset=None):
        self.offset = offset or Pose.identity()
        self._pose = None
        self._valid = False

    @property
    def valid(self):
        return self._valid

    def submit(self, pose, valid=True):
        """Feed a freshly located tracker pose in."""
        self._pose = pose
        self._valid = bool(valid and pose is not None)
        return self._valid

    def invalidate(self):
        self._valid = False

    def update(self, dt, context):
        pose = self._pose if self._valid else None
        if pose is None:
            pose = context.tracker_pose
        if pose is None:
            return None
        return pose.compose(self.offset)

    def describe(self):
        return {"source": self.name, "valid": self._valid, "offset": self.offset.as_dict()}


class FollowHmd(PoseSource):
    """A camera that hangs behind and above the player and looks at them.

    The offset is applied in the HMD's *yaw* frame only: pitching or rolling
    your head must not roll the shot.  ``look_at`` aims the camera back at the
    HMD, which is what makes this usable as a hands-free third-person view.
    """

    name = "follow_hmd"

    def __init__(self, distance=1.8, height=0.4, side=0.0, look_at=True, pitch_deg=0.0):
        self.distance = float(distance)
        self.height = float(height)
        self.side = float(side)
        self.look_at = bool(look_at)
        self.pitch_deg = float(pitch_deg)

    def update(self, dt, context):
        hmd = context.hmd_pose
        if hmd is None:
            return None
        forward = horizontal(hmd.forward())
        right = v_normalize(v_cross((0.0, 1.0, 0.0), v_scale(forward, -1.0)), (1.0, 0.0, 0.0))
        position = v_add(
            hmd.position,
            v_add(
                v_add(v_scale(forward, -self.distance), (0.0, self.height, 0.0)),
                v_scale(right, self.side),
            ),
        )
        if self.look_at:
            orientation = q_look_rotation(v_sub(hmd.position, position))
        else:
            orientation = q_look_rotation(forward)
        if self.pitch_deg:
            pitch = q_from_axis_angle((1.0, 0.0, 0.0), math.radians(self.pitch_deg))
            orientation = q_mul(orientation, pitch)
        return Pose(position, orientation)

    def describe(self):
        return {
            "source": self.name,
            "distance": self.distance,
            "height": self.height,
            "side": self.side,
            "look_at": self.look_at,
        }


class Orbit(PoseSource):
    """A camera circling the player (or a fixed point) at a steady rate."""

    name = "orbit"

    def __init__(self, radius=2.5, height=1.6, degrees_per_second=12.0, phase_deg=0.0,
                 centre=None):
        self.radius = float(radius)
        self.height = float(height)
        self.degrees_per_second = float(degrees_per_second)
        self.phase_deg = float(phase_deg)
        self.centre = centre  # None -> orbit the context anchor
        self._angle = float(phase_deg)

    @property
    def angle_deg(self):
        return self._angle % 360.0

    def reset(self):
        self._angle = self.phase_deg

    def update(self, dt, context):
        if self.centre is not None:
            centre = self.centre
        else:
            anchor = context.anchor()
            if anchor is None:
                return None
            centre = anchor.position
        self._angle += self.degrees_per_second * max(dt, 0.0)
        angle = math.radians(self._angle)
        position = (
            centre[0] + math.sin(angle) * self.radius,
            centre[1] + self.height,
            centre[2] + math.cos(angle) * self.radius,
        )
        return Pose.looking_at(position, centre)

    def describe(self):
        return {
            "source": self.name,
            "radius": self.radius,
            "height": self.height,
            "degrees_per_second": self.degrees_per_second,
            "angle_deg": self.angle_deg,
        }


SOURCES = {
    FixedPose.name: FixedPose,
    TrackedPose.name: TrackedPose,
    FollowHmd.name: FollowHmd,
    Orbit.name: Orbit,
}


def make_source(name, **kwargs):
    """Instantiate a pose source by name; raises ``KeyError`` for unknowns."""
    try:
        factory = SOURCES[name]
    except KeyError:
        raise KeyError(
            f"unknown MRC pose source '{name}'; known: {', '.join(sorted(SOURCES))}"
        ) from None
    return factory(**kwargs)


# --------------------------------------------------------------------------
# the camera itself
# --------------------------------------------------------------------------


class MRCCamera:
    """A pose source, a smoother and a lens — everything a capture shot needs."""

    def __init__(self, source=None, lens=None, smoother=None):
        self.source = source or FollowHmd()
        self.lens = lens or LensSettings()
        self.smoother = smoother or PoseSmoother()
        self._pose = None
        self._raw_pose = None

    # -- state ----------------------------------------------------------

    @property
    def pose(self):
        """The smoothed pose, or ``None`` before the first valid update."""
        return self._pose

    @property
    def raw_pose(self):
        """The pose the source produced, before damping."""
        return self._raw_pose

    @property
    def available(self):
        return self._pose is not None

    def set_source(self, source):
        self.source = source
        self.smoother.reset(None)
        self._pose = None
        self._raw_pose = None
        return source

    def set_smoothing(self, position_tau=None, rotation_tau=None):
        if position_tau is not None:
            self.smoother.position_tau = float(position_tau)
        if rotation_tau is not None:
            self.smoother.rotation_tau = float(rotation_tau)

    def reset(self):
        self.smoother.reset(None)
        self._pose = None
        self._raw_pose = None

    # -- per frame ------------------------------------------------------

    def update(self, dt, context):
        """Advance by ``dt`` seconds and return the smoothed pose (or ``None``).

        A source that cannot produce a pose this frame — a tracker that dropped
        out, an HMD that is not located yet — leaves the previous smoothed pose
        standing rather than snapping the shot to the origin.
        """
        raw = self.source.update(dt, context)
        self._raw_pose = raw
        if raw is None:
            return self._pose
        self._pose = self.smoother.update(raw, dt)
        return self._pose

    # -- preferences ----------------------------------------------------

    @classmethod
    def from_preferences(cls, get_float, get_string=None, get_bool=None):
        """Build a camera from the workbench preferences.

        Reads the existing ``TPPCam*`` lens and tracker-offset keys so a rig
        that is already calibrated for the third-person camera needs no second
        calibration, plus the ``MRCCam*`` keys this package adds.
        """
        lens = LensSettings.from_preferences(get_float)
        offset = _tracker_offset_from_preferences(get_float)
        source_name = (get_string("MRCCamSource", "tracked") if get_string else "tracked")
        if source_name == TrackedPose.name:
            source = TrackedPose(offset)
        elif source_name == FollowHmd.name:
            source = FollowHmd(
                get_float("MRCCamDistance", 1.8),
                get_float("MRCCamHeight", 0.4),
                get_float("MRCCamSide", 0.0),
            )
        elif source_name == Orbit.name:
            source = Orbit(
                get_float("MRCCamRadius", 2.5),
                get_float("MRCCamHeight", 1.6),
                get_float("MRCCamOrbitSpeed", 12.0),
            )
        else:
            source = FixedPose(Pose(offset.position, offset.orientation))
        smoother = PoseSmoother(
            get_float("MRCCamPositionSmoothing", 0.08),
            get_float("MRCCamRotationSmoothing", 0.12),
        )
        return cls(source, lens, smoother)

    def describe(self):
        return {
            "source": self.source.describe(),
            "lens": self.lens.as_dict(),
            "smoothing": {
                "position_tau": self.smoother.position_tau,
                "rotation_tau": self.smoother.rotation_tau,
            },
            "pose": self._pose.as_dict() if self._pose else None,
        }


def _tracker_offset_from_preferences(get_float):
    """The ``TPPCam*Transl``/``*Rot`` offset, in this package's conventions.

    ``xrcore.commonXR.read_preferences`` reads the same keys but swaps Y and Z
    on the way in, because the preference dialog is labelled in FreeCAD's Z-up
    convention while the tracker pose is Y-up.  The millimetre-to-metre
    conversion is the other thing it does, and both are reproduced here so a
    calibration made for the TPP camera lands in the same place.
    """
    translation = (
        get_float("TPPCamXTransl", 0.0) / 1000.0,
        get_float("TPPCamZTransl", 0.0) / 1000.0,
        get_float("TPPCamYTransl", 0.0) / 1000.0,
    )
    axis = (
        get_float("TPPCamXRot", 0.0),
        get_float("TPPCamZRot", 0.0),
        get_float("TPPCamYRot", 1.0),
    )
    angle = math.radians(get_float("TPPCamAngleRot", 0.0))
    return Pose(translation, q_from_axis_angle(axis, angle))
