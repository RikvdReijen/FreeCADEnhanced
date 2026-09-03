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
"""Just enough of FreeCAD to exercise the parts of the bridge that touch it.

These are not a FreeCAD emulator.  They implement the handful of attributes the
document walker and the tessellator actually read - ``Label``, ``Shape``,
``Placement``, ``ViewObject``, ``Group`` - so the walker's traversal, naming and
material logic can be tested without a 400 MB dependency.  Anything the walker
asks for that is not modelled here should raise, not quietly return a mock: a
silent mock is how a bridge grows a dependency nobody noticed.
"""

import math

from gbcore.transform import Matrix4

__all__ = [
    "StubVector",
    "StubMatrix",
    "StubPlacement",
    "StubColor",
    "StubAppearance",
    "StubViewObject",
    "StubShape",
    "StubObject",
    "StubDocument",
    "make_box_shape",
    "make_two_part_document",
]


class StubVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __repr__(self):
        return "Vector(%g, %g, %g)" % (self.x, self.y, self.z)


class StubMatrix:
    """Mimics ``FreeCAD.Base.Matrix``: 16 row-major values in ``.A``."""

    def __init__(self, values=None):
        self.A = tuple(values) if values else tuple(Matrix4().m)


class StubPlacement:
    def __init__(self, matrix=None):
        self._matrix = matrix if matrix is not None else Matrix4()

    @classmethod
    def from_translation(cls, x, y, z):
        return cls(Matrix4.translation(x, y, z))

    @classmethod
    def rotation_z(cls, degrees, translation=(0.0, 0.0, 0.0)):
        angle = math.radians(degrees)
        cos, sin = math.cos(angle), math.sin(angle)
        basis = ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))
        return cls(Matrix4.from_basis(basis, translation))

    def toMatrix(self):
        return StubMatrix(self._matrix.m)


class StubColor:
    def __init__(self, r, g, b, a=1.0):
        self.r, self.g, self.b, self.a = r, g, b, a

    def __iter__(self):
        return iter((self.r, self.g, self.b, self.a))


class StubAppearance:
    """One entry of ``ViewObject.ShapeAppearance``."""

    def __init__(self, diffuse=(0.8, 0.8, 0.8), specular=(0.1, 0.1, 0.1),
                 shininess=0.2, emissive=(0.0, 0.0, 0.0), transparency=0.0):
        self.DiffuseColor = StubColor(*diffuse)
        self.SpecularColor = StubColor(*specular)
        self.EmissiveColor = StubColor(*emissive)
        self.Shininess = shininess
        self.Transparency = transparency


class StubViewObject:
    def __init__(self, appearances=None, visibility=True, transparency=0):
        self.ShapeAppearance = list(appearances or [StubAppearance()])
        self.Visibility = visibility
        self.Transparency = transparency
        self.ShapeColor = (0.8, 0.8, 0.8)


class StubShape:
    """A pre-tessellated shape: ``tessellate()`` just hands back the mesh."""

    def __init__(self, points, facets, faces=None, volume=1.0):
        self.points = [StubVector(*p) for p in points]
        self.facets = [tuple(f) for f in facets]
        #: One entry per face, each a ``StubShape`` of its own, for per-face
        #: material assignment.  ``None`` means "the shape is one face".
        self.Faces = faces if faces is not None else []
        self.Volume = volume
        self.tessellate_calls = []
        self.isNull_result = False

    def tessellate(self, deviation, angular=None):
        self.tessellate_calls.append((deviation, angular))
        return ([tuple(p) for p in self.points], list(self.facets))

    def isNull(self):
        return self.isNull_result

    @property
    def ShapeType(self):
        return "Solid"


class StubObject:
    def __init__(self, name, label=None, shape=None, placement=None,
                 view=None, group=None, type_id="Part::Feature", visible=True):
        self.Name = name
        self.Label = label if label is not None else name
        self.TypeId = type_id
        self.Placement = placement if placement is not None else StubPlacement()
        self.ViewObject = view if view is not None else StubViewObject()
        if not visible:
            self.ViewObject.Visibility = False
        if shape is not None:
            self.Shape = shape
        if group is not None:
            self.Group = list(group)
        self.OutList = list(group or [])

    def getParentGroup(self):
        return None

    def isDerivedFrom(self, type_id):
        return self.TypeId == type_id


class StubDocument:
    def __init__(self, name="StubDoc", objects=()):
        self.Name = name
        self.Label = name
        self.FileName = ""
        self.Objects = list(objects)

    def getObject(self, name):
        for obj in self.Objects:
            if obj.Name == name:
                return obj
        return None


def make_box_shape(size=10.0):
    """A unit box as 8 corners and 12 triangles, wound outward."""
    s = float(size)
    points = [
        (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
        (0, 0, s), (s, 0, s), (s, s, s), (0, s, s),
    ]
    facets = [
        (0, 2, 1), (0, 3, 2),  # bottom, normal -Z
        (4, 5, 6), (4, 6, 7),  # top, normal +Z
        (0, 1, 5), (0, 5, 4),  # front, normal -Y
        (1, 2, 6), (1, 6, 5),  # right, normal +X
        (2, 3, 7), (2, 7, 6),  # back, normal +Y
        (3, 0, 4), (3, 4, 7),  # left, normal -X
    ]
    return StubShape(points, facets, volume=s ** 3)


def make_two_part_document():
    """A document with a red box at the origin and a blue box moved along X."""
    red = StubObject(
        "Box",
        "Red box",
        make_box_shape(10.0),
        view=StubViewObject([StubAppearance(diffuse=(0.9, 0.1, 0.1))]),
    )
    blue = StubObject(
        "Box001",
        "Blue box",
        make_box_shape(20.0),
        placement=StubPlacement.from_translation(50.0, 0.0, 0.0),
        view=StubViewObject([StubAppearance(diffuse=(0.1, 0.2, 0.9))]),
    )
    return StubDocument("TwoParts", [red, blue])
