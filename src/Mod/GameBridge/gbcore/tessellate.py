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
"""Turning FreeCAD's B-rep solids into triangles the engines can draw.

The engines cannot render a NURBS surface, so somewhere the exact geometry has
to become an approximation, and the only question is how good an approximation
and where the seams fall.

The bridge tessellates **face by face** rather than tessellating the whole
solid.  That costs a little memory - a box arrives as 24 vertices rather than 8
until it is welded - and buys two things that matter more.  First, a face can
have its own material, which is how a painted CAD model survives the trip at
all.  Second, and less obviously, it produces correct shading for free: within
one face the triangles share vertices, so averaging their normals smooths a
cylinder the way it should be smoothed, while between faces nothing is shared,
so the edge where a fillet meets a flat stays sharp.  A whole-solid tessellation
gives you a choice between a faceted cylinder and rounded-off edges.

Nothing here imports FreeCAD.  A shape is anything with ``Faces`` whose entries
tessellate, which is what the FreeCAD API provides and what the test stubs
imitate.
"""

from .scene import Mesh

__all__ = ["TessellationSettings", "tessellate_shape", "meshes_from_shape", "QUALITY"]


class TessellationSettings:
    """How finely to approximate, and what to do with per-face materials.

    ``deviation`` is the largest distance the triangles may stray from the real
    surface.  FreeCAD states it in millimetres, which is right for a bracket and
    wrong for a building, so ``relative`` scales it by the model's size instead.
    """

    __slots__ = ("deviation", "angular_deviation", "relative", "per_face_materials", "compute_normals")

    def __init__(
        self,
        deviation=0.1,
        angular_deviation=20.0,
        relative=False,
        per_face_materials=True,
        compute_normals=True,
    ):
        if deviation <= 0.0:
            raise ValueError("deviation has to be positive")
        self.deviation = float(deviation)
        #: Degrees between adjacent facet normals; controls how round a small
        #: hole looks, which pure distance deviation handles badly.
        self.angular_deviation = float(angular_deviation)
        self.relative = bool(relative)
        self.per_face_materials = bool(per_face_materials)
        self.compute_normals = bool(compute_normals)

    def deviation_for(self, shape):
        """The absolute deviation to use for one shape."""
        if not self.relative:
            return self.deviation
        size = _diagonal(shape)
        return max(1e-4, size * self.deviation)

    def to_dict(self):
        return {
            "deviation": self.deviation,
            "angularDeviation": self.angular_deviation,
            "relative": self.relative,
            "perFaceMaterials": self.per_face_materials,
        }

    def __repr__(self):
        return "TessellationSettings(deviation=%g%s)" % (
            self.deviation,
            " relative" if self.relative else " mm",
        )


#: Named presets, because "0.1" means nothing in a dialog.
QUALITY = {
    "draft": TessellationSettings(0.5, 30.0),
    "normal": TessellationSettings(0.1, 20.0),
    "fine": TessellationSettings(0.02, 12.0),
    "very fine": TessellationSettings(0.005, 8.0),
}


def _diagonal(shape):
    box = getattr(shape, "BoundBox", None)
    if box is None:
        return 100.0
    try:
        return float(box.DiagonalLength)
    except (AttributeError, TypeError, ValueError):
        return 100.0


def _call_tessellate(target, deviation, angular):
    """Call ``tessellate`` whichever signature this FreeCAD build provides."""
    try:
        return target.tessellate(deviation, angular)
    except TypeError:
        # Older builds, and Mesh objects, take the deviation alone.
        return target.tessellate(deviation)


def tessellate_shape(shape, settings=None):
    """Triangulate a shape, one group per face.

    Returns a list of ``(positions, indices, face_index)``, positions being a
    flat list of millimetre coordinates in the shape's own space.
    """
    settings = settings or TessellationSettings()
    deviation = settings.deviation_for(shape)
    groups = []

    faces = list(getattr(shape, "Faces", ()) or ())
    if not faces:
        # A shape with no faces - a compound of meshes, or a build that hands
        # the whole solid back at once - still tessellates as a whole.
        points, facets = _call_tessellate(shape, deviation, settings.angular_deviation)
        return [(_flatten_points(points), _flatten_facets(facets), 0)]

    for index, face in enumerate(faces):
        points, facets = _call_tessellate(face, deviation, settings.angular_deviation)
        if not points or not facets:
            continue
        indices = _flatten_facets(facets)
        if getattr(face, "Orientation", "Forward") == "Reversed":
            # OCC hands back the triangles in the surface's parametric winding.
            # For a reversed face that points into the solid, so every triangle
            # has to be turned around or the part renders inside out.
            for i in range(0, len(indices), 3):
                indices[i + 1], indices[i + 2] = indices[i + 2], indices[i + 1]
        groups.append((_flatten_points(points), indices, index))
    return groups


def _flatten_points(points):
    flat = []
    for point in points:
        if hasattr(point, "x"):
            flat.extend((float(point.x), float(point.y), float(point.z)))
        else:
            flat.extend(float(value) for value in point)
    return flat


def _flatten_facets(facets):
    flat = []
    for facet in facets:
        flat.extend(int(value) for value in facet)
    return flat


def meshes_from_shape(shape, name, settings=None, face_materials=None):
    """Build one :class:`~gbcore.scene.Mesh` per material used by the shape.

    ``face_materials`` maps a face index to a material index; a shape painted
    all one colour therefore produces a single mesh, and one painted per face
    produces as many meshes as it has distinct colours.  Engines want a mesh
    part per material, so splitting here saves every target from doing it.
    """
    settings = settings or TessellationSettings()
    groups = tessellate_shape(shape, settings)
    if not groups:
        return []

    def material_for(face_index):
        if not face_materials or not settings.per_face_materials:
            return face_materials[0] if face_materials else None
        if face_index < len(face_materials):
            return face_materials[face_index]
        # A shape with fewer colours than faces uses the first for the rest,
        # which is what FreeCAD's own view provider does.
        return face_materials[0]

    by_material = {}
    order = []
    for positions, indices, face_index in groups:
        material = material_for(face_index)
        if material not in by_material:
            by_material[material] = ([], [])
            order.append(material)
        target_positions, target_indices = by_material[material]
        offset = len(target_positions) // 3
        target_positions.extend(positions)
        target_indices.extend(index + offset for index in indices)

    meshes = []
    for material in order:
        positions, indices = by_material[material]
        mesh_name = name if len(order) == 1 else "%s_%s" % (name, len(meshes))
        mesh = Mesh(mesh_name, positions, indices, material=material)
        mesh.drop_degenerate_triangles()
        if settings.compute_normals:
            # Within a face the triangles share vertices, so this smooths a
            # curved surface; between faces nothing is shared, so edges stay
            # hard.  That is the whole reason for tessellating face by face.
            mesh.compute_normals()
        if not mesh.is_empty:
            meshes.append(mesh)
    return meshes
