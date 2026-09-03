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
"""Coordinate system and unit conversion between FreeCAD and the game engines.

FreeCAD models in millimetres on a right handed, Z-up basis.  None of the three
targets agree with that, and none of them agree with each other:

===========  ============  ===========  ==========  ==================
Target       Handedness    Up axis      Unit        Forward
===========  ============  ===========  ==========  ==================
FreeCAD      right         +Z           mm          +X
glTF 2.0     right         +Y           m           -Z
Blender      right         +Z           m           -Y
Unity        left          +Y           m           +Z
Unreal       left          +Z           cm          +X
===========  ============  ===========  ==========  ==================

A conversion is therefore a signed permutation of the axes plus a scale factor.
:class:`AxisConvention` carries both, and knows the one consequence that is easy
to forget: a conversion whose permutation has a negative determinant mirrors the
model, so every triangle it touches has to be wound the other way round or the
engine will light and cull the wrong side of it.

The module deliberately uses nothing but the standard library.  It runs inside
FreeCAD, inside Blender, inside the Unreal editor's Python and inside a bare
``python3`` running the unit tests, and only the first of those is guaranteed to
have anything more interesting installed.
"""

import math

__all__ = [
    "Matrix4",
    "AxisConvention",
    "FREECAD",
    "GLTF",
    "BLENDER",
    "UNITY",
    "UNREAL",
    "CONVENTIONS",
    "get_convention",
    "MM_PER_M",
    "MM_PER_CM",
]

MM_PER_M = 1000.0
MM_PER_CM = 10.0

_EPS = 1e-12


class Matrix4:
    """A 4x4 row-major transformation matrix.

    FreeCAD has a perfectly good ``Base.Matrix``, but it is not importable from
    Blender or from the test runner, so the bridge carries its own.  Only the
    handful of operations the exporter needs are implemented.
    """

    __slots__ = ("m",)

    def __init__(self, values=None):
        if values is None:
            self.m = (
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            )
            return
        values = tuple(float(v) for v in values)
        if len(values) != 16:
            raise ValueError("a 4x4 matrix needs 16 values, got %d" % len(values))
        self.m = values

    # -- construction ----------------------------------------------------

    @classmethod
    def identity(cls):
        return cls()

    @classmethod
    def from_rows(cls, rows):
        flat = []
        for row in rows:
            row = tuple(row)
            if len(row) != 4:
                raise ValueError("each row needs 4 values")
            flat.extend(row)
        return cls(flat)

    @classmethod
    def from_basis(cls, basis, translation=(0.0, 0.0, 0.0)):
        """Build a matrix from a 3x3 basis and a translation."""
        (a, b, c), (d, e, f), (g, h, i) = basis
        tx, ty, tz = translation
        return cls(
            (
                a, b, c, tx,
                d, e, f, ty,
                g, h, i, tz,
                0.0, 0.0, 0.0, 1.0,
            )
        )

    @classmethod
    def translation(cls, x, y, z):
        return cls.from_basis(((1, 0, 0), (0, 1, 0), (0, 0, 1)), (x, y, z))

    @classmethod
    def scaling(cls, x, y=None, z=None):
        if y is None:
            y = x
        if z is None:
            z = x
        return cls.from_basis(((x, 0, 0), (0, y, 0), (0, 0, z)))

    @classmethod
    def from_freecad(cls, matrix):
        """Convert a ``FreeCAD.Base.Matrix`` (or anything with ``.A``)."""
        values = getattr(matrix, "A", None)
        if values is None:
            values = matrix
        return cls(values)

    # -- accessors -------------------------------------------------------

    def __getitem__(self, index):
        return self.m[index]

    def row(self, index):
        return self.m[index * 4:index * 4 + 4]

    @property
    def basis(self):
        """The upper-left 3x3 block, as three rows."""
        m = self.m
        return (m[0:3], m[4:7], m[8:11])

    @property
    def translation_part(self):
        m = self.m
        return (m[3], m[7], m[11])

    def __eq__(self, other):
        if not isinstance(other, Matrix4):
            return NotImplemented
        return self.almost_equal(other, 0.0)

    def __hash__(self):
        return hash(self.m)

    def __repr__(self):
        rows = ", ".join("(%g, %g, %g, %g)" % self.row(i) for i in range(4))
        return "Matrix4[%s]" % rows

    def almost_equal(self, other, tolerance=1e-9):
        return all(abs(a - b) <= tolerance for a, b in zip(self.m, other.m))

    def is_identity(self, tolerance=1e-9):
        return self.almost_equal(Matrix4(), tolerance)

    # -- algebra ---------------------------------------------------------

    def __mul__(self, other):
        if not isinstance(other, Matrix4):
            return NotImplemented
        a, b = self.m, other.m
        out = []
        for r in range(4):
            for c in range(4):
                out.append(
                    a[r * 4 + 0] * b[0 * 4 + c]
                    + a[r * 4 + 1] * b[1 * 4 + c]
                    + a[r * 4 + 2] * b[2 * 4 + c]
                    + a[r * 4 + 3] * b[3 * 4 + c]
                )
        return Matrix4(out)

    def transposed(self):
        m = self.m
        return Matrix4(
            (
                m[0], m[4], m[8], m[12],
                m[1], m[5], m[9], m[13],
                m[2], m[6], m[10], m[14],
                m[3], m[7], m[11], m[15],
            )
        )

    def transform_point(self, point):
        x, y, z = point
        m = self.m
        return (
            m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11],
        )

    def transform_vector(self, vector):
        """Transform a direction: like a point, but ignoring the translation."""
        x, y, z = vector
        m = self.m
        return (
            m[0] * x + m[1] * y + m[2] * z,
            m[4] * x + m[5] * y + m[6] * z,
            m[8] * x + m[9] * y + m[10] * z,
        )

    def determinant3(self):
        """Determinant of the 3x3 basis, i.e. the sign of the handedness."""
        m = self.m
        return (
            m[0] * (m[5] * m[10] - m[6] * m[9])
            - m[1] * (m[4] * m[10] - m[6] * m[8])
            + m[2] * (m[4] * m[9] - m[5] * m[8])
        )

    def column_major(self):
        """The 16 values in the column-major order glTF and OpenGL expect."""
        return self.transposed().m

    def to_trs(self):
        """Decompose into (translation, rotation quaternion, scale).

        The rotation is returned in the ``(x, y, z, w)`` order glTF uses.  Shear
        is not representable and is silently dropped; the exporter only ever
        decomposes placements, which never shear.
        """
        tx, ty, tz = self.translation_part
        rows = self.basis
        cols = [
            (rows[0][0], rows[1][0], rows[2][0]),
            (rows[0][1], rows[1][1], rows[2][1]),
            (rows[0][2], rows[1][2], rows[2][2]),
        ]
        scale = [math.sqrt(sum(v * v for v in col)) for col in cols]
        if self.determinant3() < 0.0:
            # A mirrored basis has to put the flip somewhere; by convention it
            # goes on X so that the other two axes stay readable in the editor.
            scale[0] = -scale[0]
        basis = []
        for r in range(3):
            basis.append(
                tuple(
                    rows[r][c] / scale[c] if abs(scale[c]) > _EPS else 0.0
                    for c in range(3)
                )
            )
        return (tx, ty, tz), _basis_to_quaternion(basis), tuple(scale)


def _basis_to_quaternion(basis):
    """Shepperd's method: pick the largest diagonal term to stay stable."""
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = basis
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length < _EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


class AxisConvention:
    """Maps FreeCAD's millimetre, Z-up, right handed space onto a target's.

    ``basis`` is the 3x3 signed permutation taking a FreeCAD direction to a
    target direction, and ``mm_per_unit`` is how many millimetres one target
    unit spans.  Everything else - the winding flip, the placement conjugation,
    the inverse - follows from those two.
    """

    __slots__ = ("name", "basis", "mm_per_unit", "up", "forward", "handedness")

    def __init__(self, name, basis, mm_per_unit, up="+Z", forward="+X"):
        rows = tuple(tuple(float(v) for v in row) for row in basis)
        if len(rows) != 3 or any(len(row) != 3 for row in rows):
            raise ValueError("basis must be 3x3")
        self.name = name
        self.basis = rows
        self.mm_per_unit = float(mm_per_unit)
        if self.mm_per_unit <= 0.0:
            raise ValueError("mm_per_unit must be positive")
        self.up = up
        self.forward = forward
        self.handedness = "left" if self._determinant() < 0.0 else "right"

    def _determinant(self):
        (a, b, c), (d, e, f), (g, h, i) = self.basis
        return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)

    # -- properties ------------------------------------------------------

    @property
    def scale(self):
        """The factor converting a FreeCAD millimetre length to target units."""
        return 1.0 / self.mm_per_unit

    @property
    def flips_winding(self):
        """True when triangles have to be reversed to stay outward facing."""
        return self._determinant() < 0.0

    @property
    def is_identity(self):
        return self.mm_per_unit == 1.0 and self.basis == (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )

    # -- conversion ------------------------------------------------------

    def convert_point(self, point):
        """Convert a FreeCAD point in mm to a target-space point."""
        x, y, z = point
        s = self.scale
        (a, b, c), (d, e, f), (g, h, i) = self.basis
        return (
            (a * x + b * y + c * z) * s,
            (d * x + e * y + f * z) * s,
            (g * x + h * y + i * z) * s,
        )

    def convert_direction(self, direction):
        """Convert a FreeCAD direction; unlike a point it is never scaled."""
        x, y, z = direction
        (a, b, c), (d, e, f), (g, h, i) = self.basis
        return (
            a * x + b * y + c * z,
            d * x + e * y + f * z,
            g * x + h * y + i * z,
        )

    def convert_triangle(self, triangle):
        """Reorder a triangle's indices so it still faces outward."""
        if not self.flips_winding:
            return tuple(triangle)
        a, b, c = triangle
        return (a, c, b)

    def basis_matrix(self):
        """The conversion itself, as a matrix (scale included)."""
        s = self.scale
        rows = tuple(tuple(v * s for v in row) for row in self.basis)
        return Matrix4.from_basis(rows)

    def inverse_basis_matrix(self):
        """The inverse conversion, for reading engine data back into FreeCAD."""
        s = self.mm_per_unit
        transposed = tuple(
            tuple(self.basis[c][r] * s for c in range(3)) for r in range(3)
        )
        return Matrix4.from_basis(transposed)

    def convert_matrix(self, matrix):
        """Re-express a FreeCAD placement in the target space.

        A placement is a change of basis, so converting it is a conjugation:
        ``C M C-1``.  Doing it any other way - converting the translation but
        leaving the rotation alone, say - produces the classic bridge bug where
        objects land in the right spot facing the wrong way.
        """
        c = self.basis_matrix()
        return c * matrix * self.inverse_basis_matrix()

    def invert_point(self, point):
        """Convert a target-space point back to FreeCAD millimetres."""
        x, y, z = point
        s = self.mm_per_unit
        b = self.basis
        return (
            (b[0][0] * x + b[1][0] * y + b[2][0] * z) * s,
            (b[0][1] * x + b[1][1] * y + b[2][1] * z) * s,
            (b[0][2] * x + b[1][2] * y + b[2][2] * z) * s,
        )

    def describe(self):
        return "%s (%s handed, %s up, 1 unit = %g mm)" % (
            self.name,
            self.handedness,
            self.up,
            self.mm_per_unit,
        )

    def to_dict(self):
        return {
            "name": self.name,
            "basis": [list(row) for row in self.basis],
            "mmPerUnit": self.mm_per_unit,
            "up": self.up,
            "forward": self.forward,
            "handedness": self.handedness,
            "flipsWinding": self.flips_winding,
        }

    def __repr__(self):
        return "AxisConvention(%s)" % self.describe()


#: FreeCAD's own space, used when an export should not be converted at all.
FREECAD = AxisConvention(
    "freecad", ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 1.0, up="+Z", forward="+X"
)

#: glTF 2.0: metres, Y up, right handed.  A -90 degree turn about X.
GLTF = AxisConvention(
    "gltf", ((1, 0, 0), (0, 0, 1), (0, -1, 0)), MM_PER_M, up="+Y", forward="-Z"
)

#: Blender shares FreeCAD's axes and only disagrees about the unit.
BLENDER = AxisConvention(
    "blender", ((1, 0, 0), (0, 1, 0), (0, 0, 1)), MM_PER_M, up="+Z", forward="-Y"
)

#: Unity: metres, Y up, left handed.  Swapping Y and Z does both at once.
UNITY = AxisConvention(
    "unity", ((1, 0, 0), (0, 0, 1), (0, 1, 0)), MM_PER_M, up="+Y", forward="+Z"
)

#: Unreal: centimetres, Z up, left handed, reached by mirroring Y.
UNREAL = AxisConvention(
    "unreal", ((1, 0, 0), (0, -1, 0), (0, 0, 1)), MM_PER_CM, up="+Z", forward="+X"
)

CONVENTIONS = {
    c.name: c for c in (FREECAD, GLTF, BLENDER, UNITY, UNREAL)
}


def get_convention(name):
    """Look a convention up by name, case insensitively."""
    if isinstance(name, AxisConvention):
        return name
    try:
        return CONVENTIONS[str(name).strip().lower()]
    except KeyError:
        raise KeyError(
            "unknown axis convention %r, expected one of %s"
            % (name, ", ".join(sorted(CONVENTIONS)))
        )
