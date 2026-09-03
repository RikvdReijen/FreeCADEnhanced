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
"""Mirror and radial symmetry, applied at *stroke* level.

The symmetry is not a post-process over the mesh and not a second layer: one
dab in becomes ``2**n * radial`` dabs out, every one of them applied to the
same :class:`~xrsculpt.layers.SculptLayer` in the same
:func:`~xrsculpt.brushes.apply_dab` pass.  That is what makes a symmetric
sculpt still a single, dial-back-able pass, and it is why a mirrored stroke
cannot drift out of sync with its original the way a mirror modifier can.

Mirroring is exact, not approximate.  A mirror about ``x = origin.x`` negates
the x components of the dab centre, its normal and its direction and changes
nothing else, so a mesh whose vertex positions are themselves exact mirror
images receives exactly negated displacements -- no epsilon, no tolerance.
(``Tests/test_sculpt.py`` asserts equality, not closeness.)

Vertices *on* a mirror plane are the one case that needs a tolerance.  A vertex
at ``x = 0`` is inside both the original and the mirrored dab, so it is moved
twice; worse, the two moves have opposite x components which very nearly, but
not exactly, cancel, leaving a seam.  :meth:`Symmetry.constrain` fixes it
properly: after a dab it zeroes the across-plane component of the stored offset
for every vertex whose *base* position lies within :attr:`Symmetry.tolerance`
of the plane, so those vertices slide along the plane and never off it.

Radial symmetry repeats the dab ``count`` times about an axis through the
origin.  Rotation is a real rotation, so radial copies are accurate rather than
bit-exact; the mirror planes remain exact.
"""

import math

__all__ = [
    "AXES",
    "Symmetry",
    "mirror_point",
    "mirror_vector",
]

#: Mirror plane names, in the order the axis flags are stored.
AXES = ("X", "Y", "Z")

_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2, "x": 0, "y": 1, "z": 2,
               0: 0, 1: 1, 2: 2}


def _axis_index(axis):
    try:
        return _AXIS_INDEX[axis]
    except (KeyError, TypeError):
        raise ValueError("unknown symmetry axis: %r" % (axis,)) from None


def mirror_point(p, axis, origin=(0.0, 0.0, 0.0)):
    """Reflect a point in the plane normal to ``axis`` at ``origin``."""
    a = _axis_index(axis)
    out = [float(p[0]), float(p[1]), float(p[2])]
    out[a] = 2.0 * float(origin[a]) - out[a]
    return (out[0], out[1], out[2])


def mirror_vector(v, axis):
    """Reflect a direction (no origin term)."""
    a = _axis_index(axis)
    out = [float(v[0]), float(v[1]), float(v[2])]
    out[a] = -out[a]
    return (out[0], out[1], out[2])


class Symmetry(object):
    """Mirror symmetry across X/Y/Z plus radial symmetry about one axis."""

    __slots__ = ("axes", "origin", "tolerance", "radial", "radial_axis")

    def __init__(self, axes=(False, False, False), origin=(0.0, 0.0, 0.0),
                 tolerance=1e-6, radial=0, radial_axis="Y"):
        self.axes = [bool(axes[0]), bool(axes[1]), bool(axes[2])]
        self.origin = (float(origin[0]), float(origin[1]), float(origin[2]))
        self.tolerance = float(tolerance)
        self.radial = max(0, int(radial))
        self.radial_axis = "XYZ"[_axis_index(radial_axis)]

    # -- configuration ---------------------------------------------------
    @property
    def enabled(self):
        return any(self.axes) or self.radial > 1

    def set_axis(self, axis, on=True):
        self.axes[_axis_index(axis)] = bool(on)
        return self

    def toggle_axis(self, axis):
        a = _axis_index(axis)
        self.axes[a] = not self.axes[a]
        return self.axes[a]

    def set_radial(self, count, axis=None):
        self.radial = max(0, int(count))
        if axis is not None:
            self.radial_axis = "XYZ"[_axis_index(axis)]
        return self.radial

    def clear(self):
        self.axes = [False, False, False]
        self.radial = 0
        return self

    def copy(self):
        return Symmetry(self.axes, self.origin, self.tolerance, self.radial,
                        self.radial_axis)

    # -- expansion -------------------------------------------------------
    def expand(self, dab):
        """Every dab a single input dab stands for, the original first.

        Returns ``2**n`` mirrored copies for ``n`` enabled mirror planes,
        each repeated ``radial`` times about the radial axis.  The list has no
        duplicates for a dab sitting on a mirror plane -- an exact duplicate
        would double the stroke strength there.
        """
        out = [dab]
        for a in range(3):
            if not self.axes[a]:
                continue
            grown = list(out)
            for d in out:
                grown.append(self._mirror_dab(d, a))
            out = grown
        if self.radial > 1:
            grown = []
            for d in out:
                grown.extend(self._radial_dabs(d))
            out = grown
        return _dedupe(out, self.tolerance)

    def _mirror_dab(self, dab, axis):
        return dab.copy(center=mirror_point(dab.center, axis, self.origin),
                        normal=mirror_vector(dab.normal, axis),
                        direction=mirror_vector(dab.direction, axis))

    def _radial_dabs(self, dab):
        n = self.radial
        axis = _axis_index(self.radial_axis)
        out = [dab]
        for k in range(1, n):
            angle = 2.0 * math.pi * k / n
            out.append(dab.copy(
                center=self._rotate_point(dab.center, axis, angle),
                normal=self._rotate_vector(dab.normal, axis, angle),
                direction=self._rotate_vector(dab.direction, axis, angle)))
        return out

    def _rotate_point(self, p, axis, angle):
        o = self.origin
        v = self._rotate_vector((p[0] - o[0], p[1] - o[1], p[2] - o[2]),
                                axis, angle)
        return (v[0] + o[0], v[1] + o[1], v[2] + o[2])

    @staticmethod
    def _rotate_vector(v, axis, angle):
        c = math.cos(angle)
        s = math.sin(angle)
        x, y, z = float(v[0]), float(v[1]), float(v[2])
        if axis == 0:
            return (x, y * c - z * s, y * s + z * c)
        if axis == 1:
            return (x * c + z * s, y, -x * s + z * c)
        return (x * c - y * s, x * s + y * c, z)

    # -- plane vertices --------------------------------------------------
    def plane_vertices(self, positions, axis=None):
        """Indices whose position lies within ``tolerance`` of a mirror plane.

        ``positions`` is a flat buffer -- pass the *base* positions, not the
        sculpted ones, so a vertex cannot wander off the seam and then be
        treated as free.  ``O(V)``; the session caches the result per stroke.
        """
        axes = [_axis_index(axis)] if axis is not None else \
            [a for a in range(3) if self.axes[a]]
        if not axes:
            return []
        tol = self.tolerance
        out = []
        n = len(positions) // 3
        for i in range(n):
            o = i * 3
            for a in axes:
                if abs(positions[o + a] - self.origin[a]) <= tol:
                    out.append(i)
                    break
        return out

    def constrain(self, layer, base_positions, indices=None):
        """Keep seam vertices on their mirror plane.

        Zeroes the across-plane component of the stored offset for every
        vertex on a mirror plane, so the two halves of a symmetric stroke meet
        exactly instead of leaving a hairline ridge.  Returns the number of
        offsets it touched.
        """
        axes = [a for a in range(3) if self.axes[a]]
        if not axes:
            return 0
        tol = self.tolerance
        origin = self.origin
        pool = layer.indices() if indices is None else indices
        n = 0
        for i in pool:
            if i not in layer:
                continue
            o = i * 3
            if o + 2 >= len(base_positions):
                continue
            off = list(layer.get(i))
            hit = False
            for a in axes:
                if abs(base_positions[o + a] - origin[a]) <= tol \
                        and off[a] != 0.0:
                    off[a] = 0.0
                    hit = True
            if hit:
                layer.set(i, off)
                n += 1
        return n

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "axes": list(self.axes),
            "origin": list(self.origin),
            "tolerance": self.tolerance,
            "radial": self.radial,
            "radial_axis": self.radial_axis,
        }

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        return cls(d.get("axes", (False, False, False)),
                   d.get("origin", (0.0, 0.0, 0.0)),
                   float(d.get("tolerance", 1e-6)),
                   int(d.get("radial", 0)),
                   d.get("radial_axis", "Y"))

    def __repr__(self):
        on = "".join(AXES[a] for a in range(3) if self.axes[a]) or "-"
        return "Symmetry(%s, radial=%d about %s)" % (on, self.radial,
                                                     self.radial_axis)


def _dedupe(dabs, tolerance):
    """Drop dabs that coincide with an earlier one (a dab on the plane)."""
    tol = max(tolerance, 1e-12)
    out = []
    for d in dabs:
        dup = False
        for k in out:
            if (abs(k.center[0] - d.center[0]) <= tol
                    and abs(k.center[1] - d.center[1]) <= tol
                    and abs(k.center[2] - d.center[2]) <= tol
                    and abs(k.normal[0] - d.normal[0]) <= tol
                    and abs(k.normal[1] - d.normal[1]) <= tol
                    and abs(k.normal[2] - d.normal[2]) <= tol
                    and abs(k.direction[0] - d.direction[0]) <= tol
                    and abs(k.direction[1] - d.direction[1]) <= tol
                    and abs(k.direction[2] - d.direction[2]) <= tol):
                dup = True
                break
        if not dup:
            out.append(d)
    return out
