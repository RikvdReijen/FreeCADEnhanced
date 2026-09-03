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
"""``externalcamera.cfg`` — the calibration file the MRC ecosystem agrees on.

Every mixed-reality tool in the SteamVR world (LIV's legacy quadrant mode, the
OBS workflows, the calibration utilities) exchanges the camera calibration
through one small ``key=value`` text file that the game reads from its working
directory.  There is no specification for it: the format *is*
``SteamVR_ExternalCamera.cs`` in Valve's Unity plugin, and this module is a
faithful, tolerant reimplementation of what that file does.  See
``Resources/doc/MIXED_REALITY_CAPTURE.md`` for the sources and for the fields
that the reference implementation declares but never reads.

Two things are worth knowing before reading the code:

* the pose fields (``x``/``y``/``z``/``rx``/``ry``/``rz``) are in **Unity's**
  left-handed, +Z-forward frame, because the reference implementation assigns
  them straight to a Unity ``Transform``.  The optional ``m`` field is an
  OpenVR ``HmdMatrix34_t`` and is therefore **right handed** already.  Both are
  converted to the OpenXR frame by :func:`pose`, and the two routes agree.
* ``fov`` is the **vertical** field of view in degrees, because it is assigned
  to Unity's ``Camera.fieldOfView``.

Pure stdlib.  No FreeCAD, Qt or Coin imports at all — this module is the piece
of :mod:`xrmrc` most worth unit testing (ARCHITECTURE.md §6).
"""

import math
import os

from .camera import (
    Pose,
    clamp,
    matrix34_to_pose,
    pose_to_matrix34,
    quat_to_unity_euler,
    unity_euler_to_quat,
    unity_to_xr_orientation,
    unity_to_xr_position,
    xr_to_unity_orientation,
    xr_to_unity_position,
)

__all__ = [
    "ExternalCameraConfig",
    "ExternalCameraError",
    "Issue",
    "FIELDS",
    "FIELD_ORDER",
    "CONFIG_FILENAME",
    "parse",
    "load",
    "dumps",
    "save",
    "pose",
    "projection",
    "validate",
    "default_paths",
    "find_config",
    "default_config",
]

CONFIG_FILENAME = "externalcamera.cfg"

#: ``externalcamera.cfg`` key -> (attribute name, kind).  The keys, their
#: spelling and their meaning come from the ``Config`` struct of Valve's
#: ``SteamVR_ExternalCamera.cs``; the attribute names are the snake_case
#: rendering of the same thing.
FIELDS = {
    "x": ("x", "float"),
    "y": ("y", "float"),
    "z": ("z", "float"),
    "rx": ("rx", "float"),
    "ry": ("ry", "float"),
    "rz": ("rz", "float"),
    "fov": ("fov", "float"),
    "near": ("near", "float"),
    "far": ("far", "float"),
    "sceneResolutionScale": ("scene_resolution_scale", "float"),
    "frameSkip": ("frame_skip", "float"),
    "nearOffset": ("near_offset", "float"),
    "farOffset": ("far_offset", "float"),
    "hmdOffset": ("hmd_offset", "float"),
    "r": ("r", "float"),
    "g": ("g", "float"),
    "b": ("b", "float"),
    "a": ("a", "float"),
    "disableStandardAssets": ("disable_standard_assets", "bool"),
}

#: The order fields are written back out in.  ``m`` is last because it
#: supersedes the euler fields and reads better underneath them.
FIELD_ORDER = (
    "x", "y", "z",
    "rx", "ry", "rz",
    "fov", "near", "far",
    "sceneResolutionScale", "frameSkip",
    "nearOffset", "farOffset", "hmdOffset",
    "r", "g", "b", "a",
    "disableStandardAssets",
)

_LOWER_KEYS = {key.lower(): key for key in FIELDS}

_COMMENT_PREFIXES = ("//", "#", ";")

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


class ExternalCameraError(ValueError):
    """A configuration file that could not be understood in strict mode."""


class Issue:
    """One complaint from :func:`validate`."""

    __slots__ = ("level", "field", "message")

    def __init__(self, level, field, message):
        self.level = level  # "error" or "warning"
        self.field = field
        self.message = message

    @property
    def is_error(self):
        return self.level == "error"

    def __eq__(self, other):
        if not isinstance(other, Issue):
            return NotImplemented
        return (self.level, self.field, self.message) == (
            other.level,
            other.field,
            other.message,
        )

    def __hash__(self):
        return hash((self.level, self.field, self.message))

    def __repr__(self):
        return f"Issue({self.level!r}, {self.field!r}, {self.message!r})"

    def __str__(self):
        return f"{self.level}: {self.field}: {self.message}"


class ExternalCameraConfig:
    """The parsed contents of an ``externalcamera.cfg``.

    The defaults are *ours*, not the format's.  Valve's prefab ships every
    field at zero and relies on the file to supply them, which makes a missing
    ``near``/``far``/``fov`` a silently broken camera; the defaults here are the
    values the community sample files have used for years (60° vertical, 0.1 m
    near, 100 m far) so that a partial file still renders something sane.
    :attr:`present` records which keys the file actually carried, so a writer
    can tell "defaulted" from "explicitly zero".
    """

    __slots__ = (
        "x", "y", "z",
        "rx", "ry", "rz",
        "fov", "near", "far",
        "scene_resolution_scale", "frame_skip",
        "near_offset", "far_offset", "hmd_offset",
        "r", "g", "b", "a",
        "disable_standard_assets",
        "matrix", "unknown", "present", "errors", "source_path",
    )

    def __init__(self, **kwargs):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.rx = 0.0
        self.ry = 0.0
        self.rz = 0.0
        self.fov = 60.0
        self.near = 0.1
        self.far = 100.0
        self.scene_resolution_scale = 0.0
        self.frame_skip = 0.0
        self.near_offset = 0.0
        self.far_offset = 0.0
        self.hmd_offset = 0.0
        self.r = 0.0
        self.g = 0.0
        self.b = 0.0
        self.a = 0.0
        self.disable_standard_assets = False
        #: The 12 floats of an ``HmdMatrix34_t`` when the file carried ``m=``.
        self.matrix = None
        #: Keys we do not know, in file order, so a round trip keeps them.
        self.unknown = {}
        #: Keys the file actually contained.
        self.present = set()
        #: Non-fatal parse complaints, as :class:`Issue` objects.
        self.errors = []
        self.source_path = None
        for key, value in kwargs.items():
            if key not in self.__slots__:
                raise TypeError(f"unexpected configuration field '{key}'")
            setattr(self, key, value)

    # ------------------------------------------------------------------
    # derived geometry
    # ------------------------------------------------------------------

    def pose(self):
        """The camera pose in the OpenXR frame (right handed, Y up, -Z fwd)."""
        return pose(self)

    def projection(self, aspect):
        """Perspective matrix for this calibration at ``aspect`` (w / h)."""
        return projection(self, aspect)

    def unity_pose(self):
        """The pose as the file states it: Unity's frame, degrees."""
        if self.matrix is not None:
            xr = matrix34_to_pose(self.matrix)
            return Pose(
                xr_to_unity_position(xr.position),
                xr_to_unity_orientation(xr.orientation),
            )
        return Pose(
            (self.x, self.y, self.z),
            unity_euler_to_quat(self.rx, self.ry, self.rz),
        )

    def apply_matrix(self):
        """Fold ``m`` into ``x/y/z`` and ``rx/ry/rz``, the way SteamVR does.

        ``SteamVR_ExternalCamera.ReadConfig`` overwrites the euler fields from
        the matrix as soon as it has read one, so that the rest of the code only
        ever looks at the euler form.  Doing the same here makes the two
        representations impossible to disagree.
        """
        if self.matrix is None:
            return self
        unity = self.unity_pose()
        self.x, self.y, self.z = unity.position
        self.rx, self.ry, self.rz = quat_to_unity_euler(unity.orientation)
        self.present.update({"x", "y", "z", "rx", "ry", "rz"})
        return self

    def set_pose(self, camera_pose, use_matrix=False):
        """Write an OpenXR-frame pose into the calibration fields."""
        if use_matrix:
            self.matrix = pose_to_matrix34(camera_pose)
            self.present.add("m")
            return self.apply_matrix()
        unity_position = xr_to_unity_position(camera_pose.position)
        unity_orientation = xr_to_unity_orientation(camera_pose.orientation)
        self.x, self.y, self.z = unity_position
        self.rx, self.ry, self.rz = quat_to_unity_euler(unity_orientation)
        self.matrix = None
        self.present.discard("m")
        self.present.update({"x", "y", "z", "rx", "ry", "rz"})
        return self

    @property
    def chroma_key(self):
        """The ``r``/``g``/``b``/``a`` clip colour, as a 4-tuple."""
        return (self.r, self.g, self.b, self.a)

    @property
    def frame_divisor(self):
        """Render the capture camera on one frame in ``frame_divisor``.

        ``SteamVR_Render.RenderExternalCamera`` skips a frame when
        ``frameCount % (frameSkip + 1) != 0``, so a ``frameSkip`` of 1 halves
        the capture rate.  Negative values are treated as zero, as there.
        """
        return int(max(self.frame_skip, 0.0)) + 1

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------

    def copy(self):
        clone = ExternalCameraConfig()
        for slot in self.__slots__:
            value = getattr(self, slot)
            if isinstance(value, dict):
                value = dict(value)
            elif isinstance(value, set):
                value = set(value)
            elif isinstance(value, list):
                value = list(value)
            setattr(clone, slot, value)
        return clone

    def sanitised(self):
        """A copy with every value forced into a range that can be rendered."""
        clone = self.copy()
        clone.near = max(clone.near, 1e-4)
        clone.far = max(clone.far, clone.near * 1.001)
        clone.fov = clamp(clone.fov, 1.0, 179.0)
        clone.scene_resolution_scale = clamp(clone.scene_resolution_scale, 0.0, 4.0)
        clone.frame_skip = max(clone.frame_skip, 0.0)
        clone.near_offset = clamp(clone.near_offset, -clone.far, clone.far)
        clone.far_offset = clamp(clone.far_offset, -clone.far, clone.far)
        clone.r = clamp(clone.r, 0.0, 1.0)
        clone.g = clamp(clone.g, 0.0, 1.0)
        clone.b = clamp(clone.b, 0.0, 1.0)
        clone.a = clamp(clone.a, 0.0, 1.0)
        return clone

    def validate(self):
        return validate(self)

    def as_dict(self):
        data = {FIELDS[key][0]: getattr(self, FIELDS[key][0]) for key in FIELD_ORDER}
        data["matrix"] = list(self.matrix) if self.matrix is not None else None
        data["unknown"] = dict(self.unknown)
        return data

    def __eq__(self, other):
        if not isinstance(other, ExternalCameraConfig):
            return NotImplemented
        for key in FIELD_ORDER:
            attr = FIELDS[key][0]
            if getattr(self, attr) != getattr(other, attr):
                return False
        if (self.matrix is None) != (other.matrix is None):
            return False
        if self.matrix is not None and tuple(self.matrix) != tuple(other.matrix):
            return False
        return self.unknown == other.unknown

    def __repr__(self):
        return (
            f"ExternalCameraConfig(pos=({self.x:g}, {self.y:g}, {self.z:g}), "
            f"rot=({self.rx:g}, {self.ry:g}, {self.rz:g}), fov={self.fov:g}, "
            f"near={self.near:g}, far={self.far:g})"
        )


def default_config():
    """A calibration that renders something sensible with no file present."""
    return ExternalCameraConfig()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _parse_float(text):
    # The format is written by tools using the invariant culture, so a plain
    # float() is right.  A stray thousands separator or a trailing 'f' from a
    # hand-edited file is worth surviving, though.
    text = text.strip().rstrip("fF")
    return float(text)


def _parse_bool(text):
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"not a boolean: {text!r}")


def parse(text, strict=False, source_path=None, base=None):
    """Parse the contents of an ``externalcamera.cfg``.

    Tolerates comments (``//``, ``#``, ``;``), blank lines, CRLF and lone-CR
    line endings, a UTF-8 BOM, whitespace around keys and values, and unknown
    keys — which are preserved verbatim so a round trip does not throw away a
    setting some other tool cares about.  Values that cannot be parsed are
    recorded on :attr:`ExternalCameraConfig.errors` and the field keeps its
    default, mirroring the reference implementation's blanket ``catch``.

    ``base`` is the configuration to start from, and defaults to a fresh one.
    ``ReadConfig`` in the reference implementation boxes the *current* config
    and overwrites only the keys the file mentions, so a file that has lost a
    key — or has been truncated to nothing by a calibration tool mid-rewrite —
    leaves the previous value standing rather than snapping back to a default.
    Passing the previously loaded configuration reproduces that, which is what
    :class:`xrmrc.session.ConfigWatcher` does on every reload.

    With ``strict=True`` the first such problem raises
    :class:`ExternalCameraError` instead.
    """
    config = base.copy() if base is not None else ExternalCameraConfig()
    config.errors = []
    config.present = set()
    config.source_path = source_path
    if text is None:
        return config
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    elif text.startswith("﻿"):
        text = text[1:]

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in _COMMENT_PREFIXES):
            continue
        if "=" not in line:
            config.errors.append(Issue("warning", line, "line has no '=' separator"))
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Trailing inline comment: "fov=60 // vertical".
        for prefix in _COMMENT_PREFIXES:
            cut = value.find(" " + prefix)
            if cut >= 0:
                value = value[:cut].strip()

        if key == "m":
            _read_matrix(config, value, strict)
            continue

        canonical = key if key in FIELDS else _LOWER_KEYS.get(key.lower())
        if canonical is None:
            config.unknown[key] = value
            continue

        attribute, kind = FIELDS[canonical]
        try:
            parsed = _parse_bool(value) if kind == "bool" else _parse_float(value)
        except ValueError:
            issue = Issue("error", canonical, f"could not parse value {value!r}")
            if strict:
                raise ExternalCameraError(str(issue))
            config.errors.append(issue)
            continue
        if kind == "float" and not math.isfinite(parsed):
            issue = Issue("error", canonical, f"value {value!r} is not finite")
            if strict:
                raise ExternalCameraError(str(issue))
            config.errors.append(issue)
            continue
        setattr(config, attribute, parsed)
        config.present.add(canonical)

    if config.matrix is not None:
        config.apply_matrix()
    return config


def _read_matrix(config, value, strict):
    parts = [piece for piece in value.split(",") if piece.strip() != ""]
    if len(parts) != 12:
        issue = Issue(
            "error", "m", f"expected 12 comma separated values, got {len(parts)}"
        )
        if strict:
            raise ExternalCameraError(str(issue))
        config.errors.append(issue)
        return
    try:
        numbers = tuple(_parse_float(piece) for piece in parts)
    except ValueError:
        issue = Issue("error", "m", "matrix contains a value that is not a number")
        if strict:
            raise ExternalCameraError(str(issue))
        config.errors.append(issue)
        return
    if not all(math.isfinite(number) for number in numbers):
        issue = Issue("error", "m", "matrix contains a non-finite value")
        if strict:
            raise ExternalCameraError(str(issue))
        config.errors.append(issue)
        return
    config.matrix = numbers
    config.present.add("m")


def load(path, strict=False, base=None):
    """Read and parse ``path``."""
    with open(path, "rb") as handle:
        return parse(
            handle.read(), strict=strict, source_path=os.fspath(path), base=base
        )


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def _format_number(value):
    """Shortest text that reads back as exactly the same float.

    ``repr`` of a Python float is round-trip exact, which is what makes
    ``parse(dumps(config)) == config`` hold for a calibration matrix; rounding
    for prettiness here would quietly lose the last bits of a calibration
    someone spent twenty minutes measuring.
    """
    value = float(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def dumps(config, header=True, include_matrix=True):
    """Serialise ``config`` back to the ``key=value`` form.

    Unknown keys are written after the known ones so a file that came from
    another tool survives a load/save cycle intact.
    """
    lines = []
    if header:
        lines.append("// externalcamera.cfg - written by the FreeCAD XR workbench")
        lines.append("// vertical fov in degrees; near/far in metres;"
                     " x/y/z and rx/ry/rz in Unity's left-handed frame")
    for key in FIELD_ORDER:
        attribute, kind = FIELDS[key]
        value = getattr(config, attribute)
        if kind == "bool":
            lines.append(f"{key}={'true' if value else 'false'}")
        else:
            lines.append(f"{key}={_format_number(value)}")
    if include_matrix and config.matrix is not None:
        lines.append("m=" + ",".join(_format_number(v) for v in config.matrix))
    for key, value in config.unknown.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def save(config, path, header=True, include_matrix=True):
    """Write ``config`` to ``path`` atomically enough for a file watcher.

    The file is written to a sibling temporary and renamed, so a reader that is
    polling it — LIV, OBS, or our own hot reload — never sees a half-written
    calibration.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(config, header=header, include_matrix=include_matrix))
    os.replace(temporary, path)
    config.source_path = path
    return path


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def pose(config):
    """The camera pose in the OpenXR frame.

    When the file carried an ``m`` matrix that is used directly, because an
    ``HmdMatrix34_t`` is already right handed.  Otherwise the Unity-frame euler
    fields are converted.  Both routes agree: the reference implementation
    derives the euler fields *from* the matrix through exactly the reflection
    :mod:`xrmrc.camera` undoes.
    """
    if config.matrix is not None:
        return matrix34_to_pose(config.matrix)
    unity_orientation = unity_euler_to_quat(config.rx, config.ry, config.rz)
    return Pose(
        unity_to_xr_position((config.x, config.y, config.z)),
        unity_to_xr_orientation(unity_orientation),
    )


def projection(config, aspect):
    """Row-major 4x4 perspective matrix for the calibration.

    ``fov`` is vertical, in degrees.  ``aspect`` is width divided by height of
    the *rendered quadrant*, which — because the quadrant is half the width and
    half the height of the frame — equals the aspect of the whole MRC output.
    """
    if aspect <= 0.0:
        raise ValueError("aspect ratio must be positive")
    near = config.near
    far = config.far
    if near <= 0.0 or far <= near:
        raise ValueError(f"unusable clip range near={near} far={far}")
    half = math.radians(clamp(config.fov, 1e-3, 179.999)) * 0.5
    focal = 1.0 / math.tan(half)
    return (
        (focal / aspect, 0.0, 0.0, 0.0),
        (0.0, focal, 0.0, 0.0),
        (0.0, 0.0, (far + near) / (near - far), 2.0 * far * near / (near - far)),
        (0.0, 0.0, -1.0, 0.0),
    )


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate(config):
    """Everything wrong with ``config``, worst first.

    Errors mean "this cannot be rendered"; warnings mean "this will render but
    is probably not what you meant".
    """
    issues = []

    if not math.isfinite(config.near) or config.near <= 0.0:
        issues.append(Issue("error", "near", "near clip must be greater than zero"))
    if not math.isfinite(config.far) or config.far <= config.near:
        issues.append(Issue("error", "far", "far clip must be beyond the near clip"))
    if not math.isfinite(config.fov) or not 0.0 < config.fov < 180.0:
        issues.append(Issue("error", "fov", "vertical fov must be within (0, 180) degrees"))

    if math.isfinite(config.near) and 0.0 < config.near < 0.01:
        issues.append(
            Issue("warning", "near", "a near clip under 1 cm wastes depth precision")
        )
    if math.isfinite(config.far) and config.far > 10000.0:
        issues.append(
            Issue("warning", "far", "a far clip beyond 10 km wastes depth precision")
        )
    if math.isfinite(config.fov) and config.fov > 150.0:
        issues.append(Issue("warning", "fov", "a vertical fov above 150 degrees is extreme"))

    if config.frame_skip < 0.0:
        issues.append(
            Issue("warning", "frameSkip", "negative frame skip is treated as zero")
        )
    if config.scene_resolution_scale < 0.0:
        issues.append(
            Issue("warning", "sceneResolutionScale", "negative scale is ignored")
        )
    elif config.scene_resolution_scale > 2.0:
        issues.append(
            Issue(
                "warning",
                "sceneResolutionScale",
                "a scale above 2 will not keep up with the headset",
            )
        )

    for name, value in (("r", config.r), ("g", config.g), ("b", config.b), ("a", config.a)):
        if not 0.0 <= value <= 1.0:
            issues.append(
                Issue("warning", name, "chroma key components are expected in [0, 1]")
            )

    if math.isfinite(config.near) and math.isfinite(config.far) and config.far > config.near:
        span = config.far - config.near
        if abs(config.near_offset) > span:
            issues.append(
                Issue(
                    "warning",
                    "nearOffset",
                    "the near offset is larger than the whole clip range",
                )
            )
        if abs(config.far_offset) > span:
            issues.append(
                Issue(
                    "warning",
                    "farOffset",
                    "the far offset is larger than the whole clip range",
                )
            )
    if abs(config.hmd_offset) > 5.0:
        issues.append(
            Issue("warning", "hmdOffset", "an HMD offset beyond 5 m is almost certainly wrong")
        )

    if config.matrix is not None and len(config.matrix) != 12:
        issues.append(Issue("error", "m", "an HmdMatrix34_t has exactly 12 values"))

    for issue in config.errors:
        issues.append(issue)

    issues.sort(key=lambda item: 0 if item.is_error else 1)
    return issues


# --------------------------------------------------------------------------
# where the file lives
# --------------------------------------------------------------------------


def default_paths(extra=()):
    """Candidate locations for ``externalcamera.cfg``, most specific first.

    The ecosystem convention is "next to the game executable", which for a
    Python-hosted viewer means the process working directory.  A per-user copy
    under the FreeCAD data directory is offered as well so a calibration is not
    lost when the working directory changes; the caller passes that in through
    ``extra`` because this module does not import FreeCAD.
    """
    candidates = [os.fspath(path) for path in extra]
    env = os.environ.get("FREECAD_XR_EXTERNALCAMERA")
    if env:
        candidates.insert(0, env)
    candidates.append(os.path.join(os.getcwd(), CONFIG_FILENAME))
    seen = set()
    ordered = []
    for candidate in candidates:
        resolved = os.path.abspath(candidate)
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def find_config(extra=()):
    """The first existing candidate from :func:`default_paths`, or ``None``."""
    for candidate in default_paths(extra):
        if os.path.isfile(candidate):
            return candidate
    return None
