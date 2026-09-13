# SPDX-License-Identifier: LGPL-2.1-or-later
"""Physics-based fit checking.

Grab a part and try to actually insert it. Collision tells you about
clearance in a way a numeric check does not: a peg that binds at 0.05 mm of
interference *stops* in your hand, and a hole with 0.3 mm of clearance lets
the part rattle.

::

    mesh.py     triangle meshes (positions + indices) with bounds and normals
    bvh.py      axis-aligned bounding-volume hierarchy over triangles
    collide.py  triangle/triangle intersection, penetration estimate,
                closest distance between two meshes under a relative pose
    session.py  FitSession: a grabbed part following the hand, stopped and
                slid by contact with the static parts, reporting contacts
                (for haptics) and clearance

Everything is pure Python (numpy is used for vertex transforms when present)
and works on any mesh — tessellated FreeCAD shapes, imported scans, or the
environment's machine parts.
"""

from .mesh import TriMesh, box_mesh, cylinder_mesh
from .bvh import BVH
from .collide import (
    Contact,
    CollisionResult,
    intersecting_pairs,
    collide,
    closest_distance,
    triangles_intersect,
)
from .session import FitSession, FitParams, InsertionProbe

__all__ = [
    "TriMesh", "box_mesh", "cylinder_mesh", "BVH",
    "Contact", "CollisionResult", "intersecting_pairs", "collide",
    "closest_distance", "triangles_intersect",
    "FitSession", "FitParams", "InsertionProbe",
]
