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
"""The Blender target.

Blender is the odd one out, and in the easy direction: it shares FreeCAD's axes
and handedness, so the only conversion is millimetres to metres.  It is also not
asset-based - a .blend file is a scene, not a content browser - so the whole
hierarchy goes into one glTF file, which is exactly what Blender's importer
rebuilds in a single step.

The export ships the same importer script the add-on uses, so a headless
pipeline can run ``blender --background --python gamebridge_blender_import.py --
scene.gbscene`` without anything being installed first.
"""

import os

from .base import Target

__all__ = ["BlenderTarget"]


class BlenderTarget(Target):
    name = "blender"
    title = "Blender"
    convention_name = "blender"
    policy_name = "blender"
    #: One file, whole hierarchy: Blender's importer wants it that way.
    split_meshes = False
    mesh_prefix = ""
    material_prefix = ""
    manifest_name = "scene.gbscene"

    client_script = os.path.join("blender", "gamebridge_blender_import.py")

    def describe(self):
        data = Target.describe(self)
        data["engine"] = "blender"
        return data

    def write_bootstrap(self, scene, directory, result):
        from . import copy_client_script

        path = copy_client_script(self.client_script, directory)
        if path is None:
            result.warn(
                "the Blender importer script could not be found; import the "
                "glTF file directly and set the scene unit scale to 1.0"
            )
            return None
        result.add_file(path, "importer")
        return path
