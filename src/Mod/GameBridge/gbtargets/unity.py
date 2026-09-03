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
"""The Unity target.

Unity is metres, Y up and left handed, and like Unreal it is asset-based, so the
export is again one mesh per file plus a manifest with the placements.  Unlike
Unreal it has no in-editor Python, so the import is driven by the C# editor
package in ``clients/unity``: the export writes a small job file next to the
manifest and the package's asset post-processor picks it up the next time Unity
regains focus and refreshes the asset database.

The export is written straight into ``Assets/`` when the user points it there.
That is deliberate - Unity only sees files inside the project - and it is why
the writer is careful never to touch a ``.meta`` file: those belong to Unity's
asset database, and rewriting one detaches every reference to the asset.
"""

import json
import os

from .base import Target

__all__ = ["UnityTarget"]


class UnityTarget(Target):
    name = "unity"
    title = "Unity"
    convention_name = "unity"
    policy_name = "unity"
    split_meshes = True
    mesh_prefix = ""
    material_prefix = "M_"
    mesh_directory = "Meshes"
    manifest_name = "scene.gbscene"

    #: Written next to the manifest; the editor package consumes and deletes it.
    job_name = "scene.gbimport"

    #: Whether the importer should build prefabs as well as scene objects.
    create_prefabs = True

    def describe(self):
        data = Target.describe(self)
        data["engine"] = "unity"
        data["createPrefabs"] = self.create_prefabs
        return data

    def write_bootstrap(self, scene, directory, result):
        """Write the job file the C# editor package watches for."""
        job = {
            "manifest": self.manifest_name,
            "createPrefabs": self.create_prefabs,
            "rootName": scene.name or scene.document or "FreeCAD",
            "generateColliders": False,
            "staticFlags": True,
        }
        path = os.path.join(directory, self.job_name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(job, handle, indent=2)
            handle.write("\n")
        result.add_file(path, "job")
        return path
