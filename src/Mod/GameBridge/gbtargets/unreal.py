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
"""The Unreal Engine target.

Unreal is centimetres, Z up and left handed, and its content browser holds
assets that get placed into a level.  So the export is one static mesh asset per
FreeCAD solid, each with its pivot at its own origin, plus a manifest holding
the placements - which is what lets an artist drag a bracket into a different
level without dragging the whole assembly's coordinate system along with it.

Asset names follow the conventions the engine's own style guide uses, ``SM_``
for a static mesh and ``M_`` for a material, because an import that ignores them
is an import somebody has to rename by hand.
"""

import os

from .base import Target

__all__ = ["UnrealTarget"]


class UnrealTarget(Target):
    name = "unreal"
    title = "Unreal Engine"
    convention_name = "unreal"
    policy_name = "unreal"
    split_meshes = True
    mesh_prefix = "SM_"
    material_prefix = "M_"
    mesh_directory = "Meshes"
    manifest_name = "scene.gbscene"

    #: Where the assets land inside the project, if the user does not say.
    content_path = "/Game/FreeCAD"

    #: Copied next to the export so the import needs no installation step.
    client_script = os.path.join("unreal", "gamebridge_unreal_import.py")

    def describe(self):
        data = Target.describe(self)
        data["contentPath"] = self.content_path
        data["engine"] = "unreal"
        return data

    def write_bootstrap(self, scene, directory, result):
        """Copy in the editor script that performs the import.

        Unreal runs Python inside the editor, so the export ships the importer
        with it: the artist opens their project and runs the file, rather than
        installing a plugin first.
        """
        from . import copy_client_script

        path = copy_client_script(self.client_script, directory)
        if path is None:
            result.warn(
                "the Unreal importer script could not be found; import "
                "%s manually with the Interchange importer" % self.manifest_name
            )
            return None
        result.add_file(path, "importer")
        return path
