# SPDX-License-Identifier: LGPL-2.1-or-later
"""QR-anchored canvas frames and table-contact detection.

The XR client (JavaScript) implements the same maths for real-time use;
this module is the documented reference implementation, used by the server
for marker generation, for tests and for the optional "raw pose" mode where
a client streams stylus/hand poses and lets the server run the detectors.

Conventions
-----------
* Canvas coordinates are the abstract units of :mod:`layout` (x right,
  y down like a screen).  ``scale_mm`` says how many millimetres one canvas
  unit is when the canvas is laid on the table.
* A marker (printed QR) lies on the table.  Its frame has the origin at the
  QR centre, ``x`` to the right of the printed code and ``y`` towards the top
  of the printed code (away from the reader) and ``z`` out of the paper.
* The canvas sheet is placed relative to the marker: by default the marker
  sits at the top-left corner of the sheet, so canvas ``(0, 0)`` is the
  marker centre, canvas ``+x`` is marker ``+x`` and canvas ``+y`` is marker
  ``-y`` (towards the reader).
"""

import math
import re

from .geometry import Vec3

DEFAULT_MARKER_MM = 80.0
DEFAULT_SCALE_MM = 0.5  # one canvas unit = half a millimetre -> 168 unit node = 84 mm


class MarkerSpec:
    """Everything needed to print and later recognise a canvas marker."""

    def __init__(
        self,
        canvas_id,
        size_mm=DEFAULT_MARKER_MM,
        base_url="",
        scale_mm=DEFAULT_SCALE_MM,
        offset=(0.0, 0.0),
        label=None,
        marker_index=0,
    ):
        self.canvas_id = canvas_id
        self.size_mm = float(size_mm)
        self.base_url = base_url
        self.scale_mm = float(scale_mm)
        # canvas coordinates of the marker centre
        self.offset = (float(offset[0]), float(offset[1]))
        self.label = label or "FreeCAD canvas %s" % canvas_id
        self.marker_index = int(marker_index)

    def payload(self):
        """Text encoded in the QR: a URL the XR page understands.

        A phone camera opens the XR page directly; the WebXR client reads the
        fragment to know which canvas and which physical size it sees.
        """
        frag = "c=%s&mm=%g&s=%g&m=%d" % (
            self.canvas_id,
            self.size_mm,
            self.scale_mm,
            self.marker_index,
        )
        if self.offset != (0.0, 0.0):
            frag += "&ox=%g&oy=%g" % self.offset
        base = self.base_url.rstrip("/")
        return "%s/xr#%s" % (base, frag) if base else "fcgh:%s" % frag

    def to_dict(self):
        return {
            "canvas": self.canvas_id,
            "size_mm": self.size_mm,
            "scale_mm": self.scale_mm,
            "offset": list(self.offset),
            "label": self.label,
            "index": self.marker_index,
            "payload": self.payload(),
        }

    @classmethod
    def parse(cls, text, base_url=""):
        """Inverse of :meth:`payload`."""
        m = re.search(r"(?:#|fcgh:)(.*)$", text)
        if not m:
            raise ValueError("not a canvas marker payload")
        params = {}
        for part in m.group(1).split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
        if "c" not in params:
            raise ValueError("marker payload has no canvas id")
        return cls(
            params["c"],
            float(params.get("mm", DEFAULT_MARKER_MM)),
            base_url,
            float(params.get("s", DEFAULT_SCALE_MM)),
            (float(params.get("ox", 0.0)), float(params.get("oy", 0.0))),
            marker_index=int(params.get("m", 0)),
        )


# --------------------------------------------------------------- rotations
def quat_to_axes(q):
    """Return (x_axis, y_axis, z_axis) unit vectors of quaternion (x, y, z, w)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz, wx, wy, wz = x * y, x * z, y * z, w * x, w * y, w * z
    ax = Vec3(1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy))
    ay = Vec3(2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx))
    az = Vec3(2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy))
    return ax, ay, az


def axis_angle_quat(axis, angle_deg):
    a = axis.normalized()
    half = math.radians(angle_deg) / 2.0
    s = math.sin(half)
    return (a.x * s, a.y * s, a.z * s, math.cos(half))


class CanvasFrame:
    """Placement of the canvas sheet in world space (metres)."""

    def __init__(self, origin, x_axis, y_axis, scale_mm=DEFAULT_SCALE_MM):
        self.origin = origin
        self.x_axis = x_axis.normalized()
        # re-orthogonalise y against x
        y = y_axis - self.x_axis * y_axis.dot(self.x_axis)
        self.y_axis = y.normalized()
        # canvas y points "down" the sheet (screen convention), so the
        # up-out-of-the-table normal is y cross x, not x cross y
        self.normal = self.y_axis.cross(self.x_axis).normalized()
        self.scale_mm = float(scale_mm)

    @property
    def unit_m(self):
        """Metres per canvas unit."""
        return self.scale_mm / 1000.0

    @classmethod
    def from_marker(cls, position, quaternion, spec, marker_up="z"):
        """Frame from a tracked marker pose.

        ``marker_up`` names which axis of the tracked pose points out of the
        printed code: WebXR image tracking reports the image normal along
        ``+y`` with ``-z`` towards the top of the image; a plane/anchor style
        pose or our own three-point calibration uses ``+z``.
        """
        ax, ay, az = quat_to_axes(quaternion)
        if marker_up == "y":
            right, top = ax, -az
        elif marker_up == "z":
            right, top = ax, ay
        else:
            raise ValueError("marker_up must be 'y' or 'z'")
        # canvas y runs towards the reader, i.e. opposite to the marker top
        unit = spec.scale_mm / 1000.0
        origin = position - right * (spec.offset[0] * unit) - (-top) * (spec.offset[1] * unit)
        return cls(origin, right, -top, spec.scale_mm)

    @classmethod
    def from_points(cls, p_origin, p_x, p_y, scale_mm=DEFAULT_SCALE_MM):
        """Frame from three tapped points: sheet origin, a point along +x and
        any point on the +y side.  Used for stylus calibration when no marker
        tracking is available."""
        x_axis = p_x - p_origin
        if x_axis.length() < 1e-6:
            raise ValueError("x point coincides with origin")
        y_hint = p_y - p_origin
        return cls(p_origin, x_axis, y_hint, scale_mm)

    def to_world(self, cx, cy, height=0.0):
        u = self.unit_m
        return self.origin + self.x_axis * (cx * u) + self.y_axis * (cy * u) + self.normal * height

    def to_canvas(self, point):
        """World point -> (cx, cy, height_m above the sheet)."""
        d = point - self.origin
        u = self.unit_m
        return (d.dot(self.x_axis) / u, d.dot(self.y_axis) / u, d.dot(self.normal))

    def to_dict(self):
        return {
            "origin": self.origin.to_json(),
            "x": self.x_axis.to_json(),
            "y": self.y_axis.to_json(),
            "normal": self.normal.to_json(),
            "scale_mm": self.scale_mm,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            Vec3.coerce(data["origin"]),
            Vec3.coerce(data["x"]),
            Vec3.coerce(data["y"]),
            data.get("scale_mm", DEFAULT_SCALE_MM),
        )


# ------------------------------------------------------------ table contact
class ContactDetector:
    """Turns a stream of tip heights above the sheet into touch events.

    Works for the MX Ink tip, a fingertip or a controller ray hit.  Uses
    hysteresis (``down_mm`` < ``up_mm``) so tracking jitter near the surface
    does not toggle the state, and classifies a short press without movement
    as a ``tap``.
    """

    def __init__(self, down_mm=6.0, up_mm=12.0, tap_max_s=0.35, tap_max_move=12.0, hold_s=0.6):
        self.down_m = down_mm / 1000.0
        self.up_m = up_mm / 1000.0
        self.tap_max_s = tap_max_s
        self.tap_max_move = tap_max_move  # canvas units
        self.hold_s = hold_s
        self.touching = False
        self.pressed_external = False
        self._down_time = None
        self._down_pos = None
        self._moved = 0.0
        self._hold_sent = False

    def update(self, height_m, cx, cy, t, pressed=None):
        """Feed one sample.  ``pressed`` optionally overrides the height test
        (e.g. the MX Ink tip pressure button).  Returns a list of events:
        ``("down", cx, cy)``, ``("move", cx, cy)``, ``("hold", cx, cy)``,
        ``("up", cx, cy)`` and ``("tap", cx, cy)``."""
        events = []
        if pressed is not None:
            want_touch = bool(pressed)
        elif self.touching:
            want_touch = height_m < self.up_m
        else:
            want_touch = height_m < self.down_m
        if want_touch and not self.touching:
            self.touching = True
            self._down_time = t
            self._down_pos = (cx, cy)
            self._moved = 0.0
            self._hold_sent = False
            events.append(("down", cx, cy))
        elif want_touch and self.touching:
            self._moved = max(
                self._moved, math.hypot(cx - self._down_pos[0], cy - self._down_pos[1])
            )
            events.append(("move", cx, cy))
            if (
                not self._hold_sent
                and self._moved <= self.tap_max_move
                and t - self._down_time >= self.hold_s
            ):
                self._hold_sent = True
                events.append(("hold", cx, cy))
        elif not want_touch and self.touching:
            self.touching = False
            events.append(("up", cx, cy))
            if self._moved <= self.tap_max_move and (t - self._down_time) <= self.tap_max_s:
                events.append(("tap", self._down_pos[0], self._down_pos[1]))
        return events


# ------------------------------------------------------------- flat hand
# WebXR joint names we rely on (subset of the 25 XRHand joints)
FINGER_CHAINS = {
    "index": (
        "index-finger-metacarpal",
        "index-finger-phalanx-proximal",
        "index-finger-phalanx-intermediate",
        "index-finger-tip",
    ),
    "middle": (
        "middle-finger-metacarpal",
        "middle-finger-phalanx-proximal",
        "middle-finger-phalanx-intermediate",
        "middle-finger-tip",
    ),
    "ring": (
        "ring-finger-metacarpal",
        "ring-finger-phalanx-proximal",
        "ring-finger-phalanx-intermediate",
        "ring-finger-tip",
    ),
    "pinky": (
        "pinky-finger-metacarpal",
        "pinky-finger-phalanx-proximal",
        "pinky-finger-phalanx-intermediate",
        "pinky-finger-tip",
    ),
}


def finger_extension(joints, chain):
    """1.0 when the finger is straight, 0.0 when fully curled."""
    pts = [joints.get(name) for name in chain]
    if any(p is None for p in pts):
        return 0.0
    straight = pts[0].distance(pts[-1])
    along = sum(pts[i].distance(pts[i + 1]) for i in range(len(pts) - 1))
    if along <= 0:
        return 0.0
    return max(0.0, min(1.0, (straight / along - 0.6) / 0.4))


def palm_normal(joints):
    """Normal of the palm plane (wrist, index metacarpal, pinky metacarpal)."""
    w = joints.get("wrist")
    i = joints.get("index-finger-metacarpal")
    p = joints.get("pinky-finger-metacarpal")
    if w is None or i is None or p is None:
        return None
    return (i - w).cross(p - w).normalized()


def flat_hand_score(joints, frame, max_height_m=0.04):
    """Score in [0, 1] of how much the hand is a flat palm resting on the
    sheet: fingers extended, palm parallel to the sheet, palm near it.

    ``joints`` maps WebXR joint names to world positions (Vec3).
    """
    ext = [finger_extension(joints, chain) for chain in FINGER_CHAINS.values()]
    extension = sum(ext) / len(ext)
    n = palm_normal(joints)
    if n is None:
        return 0.0
    parallel = abs(n.dot(frame.normal))
    palm = joints.get("middle-finger-metacarpal") or joints.get("wrist")
    _cx, _cy, height = frame.to_canvas(palm)
    closeness = max(0.0, 1.0 - abs(height) / max_height_m)
    return extension * parallel * closeness


def pinch_strength(joints, pinch_mm=15.0, open_mm=45.0):
    """1.0 when index tip and thumb tip touch, 0.0 when far apart."""
    a = joints.get("index-finger-tip")
    b = joints.get("thumb-tip")
    if a is None or b is None:
        return 0.0
    d = a.distance(b) * 1000.0
    return max(0.0, min(1.0, (open_mm - d) / (open_mm - pinch_mm)))


def palm_center(joints):
    pts = [
        joints.get(k)
        for k in (
            "wrist",
            "index-finger-metacarpal",
            "pinky-finger-metacarpal",
            "middle-finger-phalanx-proximal",
        )
    ]
    pts = [p for p in pts if p is not None]
    if not pts:
        return None
    acc = Vec3()
    for p in pts:
        acc = acc + p
    return acc * (1.0 / len(pts))
