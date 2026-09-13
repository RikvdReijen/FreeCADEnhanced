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
"""A Wavefront OBJ writer, for the cases where glTF is the wrong answer.

OBJ has no hierarchy, no units, no PBR and no axis convention, which is exactly
why it is still useful: every tool in the pipeline reads it, including the ones
that predate glTF and the ones whose glTF importer is a paid plugin.  The bridge
writes it as a fallback and as a debugging aid - an OBJ can be diffed, which a
GLB cannot.

Because the format has no hierarchy, the writer flattens the scene: each node's
world transform is baked into its vertices and the node becomes one ``o`` group.
The material file carries the metallic-roughness values in the ``Pr``/``Pm``
extensions that Blender and most modern importers understand, alongside the
classic ``Kd``/``Ks``/``Ns`` fields for everything else.
"""

import os

from gbcore.transform import get_convention

__all__ = ["OBJWriter", "write_obj"]


class OBJWriter:
    """Flattens a scene into OBJ text plus its MTL companion."""

    def __init__(self, scene, convention=None, material_library=None):
        self.scene = scene
        self.convention = get_convention(convention or "blender")
        self.material_library = material_library

    def material_names(self):
        """Unique, MTL-safe names in material index order."""
        names = []
        seen = set()
        for index, material in enumerate(self.scene.materials):
            name = "".join(
                c if (c.isalnum() or c in "_-") else "_" for c in material.name
            ) or "material"
            candidate = name
            counter = 0
            while candidate.lower() in seen:
                counter += 1
                candidate = "%s_%03d" % (name, counter)
            seen.add(candidate.lower())
            names.append(candidate)
        return names

    def to_obj(self):
        scene = self.scene
        scene.validate()
        convention = self.convention
        names = self.material_names()
        lines = [
            "# Exported from FreeCAD by GameBridge",
            "# axis convention: %s" % convention.describe(),
        ]
        if self.material_library:
            lines.append("mtllib %s" % self.material_library)

        # OBJ indices are one-based and run over the whole file, so the offsets
        # have to be tracked across every object written so far.
        vertex_offset = 1
        normal_offset = 1
        uv_offset = 1
        for node, world in scene.world_transforms():
            if node.mesh is None or not node.visible:
                continue
            mesh = scene.meshes[node.mesh]
            if mesh.is_empty:
                continue
            baked = mesh.transformed(world)
            lines.append("o %s" % node.name.replace(" ", "_"))
            for i in range(0, len(baked.positions), 3):
                x, y, z = convention.convert_point(baked.positions[i:i + 3])
                lines.append("v %.6f %.6f %.6f" % (x, y, z))
            for i in range(0, len(baked.normals), 3):
                x, y, z = convention.convert_direction(baked.normals[i:i + 3])
                lines.append("vn %.6f %.6f %.6f" % (x, y, z))
            for i in range(0, len(baked.uvs), 2):
                lines.append("vt %.6f %.6f" % (baked.uvs[i], baked.uvs[i + 1]))
            if mesh.material is not None and mesh.material < len(names):
                lines.append("usemtl %s" % names[mesh.material])
            has_normals = bool(baked.normals)
            has_uvs = bool(baked.uvs)
            for i in range(0, len(baked.indices), 3):
                triangle = convention.convert_triangle(baked.indices[i:i + 3])
                face = []
                for index in triangle:
                    vertex = index + vertex_offset
                    if has_uvs and has_normals:
                        face.append("%d/%d/%d" % (vertex, index + uv_offset, index + normal_offset))
                    elif has_normals:
                        face.append("%d//%d" % (vertex, index + normal_offset))
                    elif has_uvs:
                        face.append("%d/%d" % (vertex, index + uv_offset))
                    else:
                        face.append("%d" % vertex)
                lines.append("f %s" % " ".join(face))
            vertex_offset += baked.vertex_count
            normal_offset += len(baked.normals) // 3
            uv_offset += len(baked.uvs) // 2
        return "\n".join(lines) + "\n"

    def to_mtl(self):
        names = self.material_names()
        lines = ["# Exported from FreeCAD by GameBridge"]
        for name, material in zip(names, self.scene.materials):
            r, g, b, a = material.base_color
            lines.append("")
            lines.append("newmtl %s" % name)
            lines.append("Kd %.6f %.6f %.6f" % (r, g, b))
            # Phong's specular is reconstructed so that classic viewers show
            # something sensible; the Pr/Pm lines below carry the real values.
            specular = 0.04 + 0.96 * material.metallic
            lines.append("Ks %.6f %.6f %.6f" % (specular * r, specular * g, specular * b))
            lines.append("Ns %.6f" % (1000.0 * (1.0 - material.roughness) ** 2))
            if any(material.emissive):
                lines.append("Ke %.6f %.6f %.6f" % material.emissive)
            lines.append("Pr %.6f" % material.roughness)
            lines.append("Pm %.6f" % material.metallic)
            lines.append("d %.6f" % a)
            lines.append("illum %d" % (4 if a < 1.0 else 2))
        return "\n".join(lines) + "\n"


def write_obj(scene, path, convention=None, write_materials=True):
    """Write ``scene`` to ``path``, plus a sidecar ``.mtl``.  Returns the path."""
    stem = os.path.splitext(os.path.basename(path))[0]
    library = stem + ".mtl" if (write_materials and scene.materials) else None
    writer = OBJWriter(scene, convention, library)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(writer.to_obj())
    if library:
        with open(os.path.join(os.path.dirname(path), library), "w", encoding="utf-8") as handle:
            handle.write(writer.to_mtl())
    return path
