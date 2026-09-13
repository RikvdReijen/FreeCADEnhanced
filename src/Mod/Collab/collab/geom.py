# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small vector and tolerance helpers.

Deliberately dependency-free: this module has to import in a plain Python
interpreter with no FreeCAD, no numpy and no OCC, because anchor resolution is
the part of the design that most needs to be testable in isolation.
"""

import math

Vec3 = tuple

#: Default absolute tolerance for length-like comparisons, in millimetres.
LENGTH_TOL = 1e-6
#: Default angular tolerance for direction comparisons, in degrees.
ANGLE_TOL_DEG = 5.0


def as_vec(value, field="vector"):
    """Coerce ``value`` to a 3-tuple of floats."""
    if value is None:
        return None
    try:
        x, y, z = value
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be three numbers, got {value!r}") from None
    return (float(x), float(y), float(z))


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    return math.sqrt(dot(a, a))


def distance(a, b):
    return norm(sub(a, b))


def normalized(a):
    n = norm(a)
    if n == 0.0:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def angle_between_deg(a, b):
    """Angle between two directions in degrees, or ``None`` if either is null."""
    na, nb = normalized(a), normalized(b)
    if na is None or nb is None:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(na, nb)))))


def directions_match(a, b, tol_deg=ANGLE_TOL_DEG):
    """True when ``a`` points the same way as ``b`` within ``tol_deg``.

    Direction is *signed*: a face normal of ``-Z`` does not match a query for
    ``+Z``. That is intentional — the outside and the inside of a wall are not
    interchangeable, and a resolver that treats them as such would silently
    anchor a pocket to the wrong side.
    """
    angle = angle_between_deg(a, b)
    return angle is not None and angle <= tol_deg


def close(a, b, tol=LENGTH_TOL):
    """Absolute comparison of two scalars, tolerating ``None`` on either side."""
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def relative_error(actual, expected):
    """``|actual - expected|`` scaled by ``expected``, or absolute if it is ~0."""
    if actual is None or expected is None:
        return None
    scale = abs(float(expected))
    diff = abs(float(actual) - float(expected))
    return diff / scale if scale > LENGTH_TOL else diff
