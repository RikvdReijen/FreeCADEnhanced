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
"""FreeCAD's File > Export hook.

The export dialog gives one file name where the bridge would rather have a
folder and a target, so the extension decides.  ``.gbscene`` runs a full export
for the target in the preferences - manifest, per-mesh assets, importer script -
into the folder the user picked, while ``.glb``, ``.gltf`` and ``.obj`` write
exactly the one file they name, which is what somebody typing a file name into
an export dialog is asking for.
"""

import os

import FreeCAD

from gbcore import service
from gbcore.document import DocumentWalker
from gbcore.preferences import preferences
from gbformat import write_glb, write_gltf, write_obj
from gbtargets import get_target

__all__ = ["export"]

_WRITERS = {
    ".glb": write_glb,
    ".gltf": write_gltf,
    ".obj": write_obj,
}


def export(objects, filename):
    """Called by FreeCAD with the selected objects and the chosen file name."""
    extension = os.path.splitext(filename)[1].lower()
    document = objects[0].Document if objects else FreeCAD.ActiveDocument

    if extension == ".gbscene":
        # A manifest names a whole export, so the folder it was pointed at is
        # what actually gets written.
        target = preferences.get("Target")
        result = service.export_document(
            target, os.path.dirname(filename) or os.getcwd(), document, objects
        )
        FreeCAD.Console.PrintMessage("GameBridge: %s\n" % result.summary())
        return result

    writer = _WRITERS.get(extension)
    if writer is None:
        raise ValueError("GameBridge cannot write %s files" % (extension or filename))

    walker = DocumentWalker(
        preferences.tessellation_settings(), preferences.get("IncludeHidden")
    )
    name = os.path.splitext(os.path.basename(filename))[0]
    scene = walker.walk_objects(
        list(objects or []), name, getattr(document, "Name", None)
    )
    target = get_target(preferences.get("Target"))
    target.prepare(scene, _Warnings())
    writer(scene, filename, target.convention)
    FreeCAD.Console.PrintMessage(
        "GameBridge: wrote %s in %s space (%d triangle(s))\n"
        % (os.path.basename(filename), target.convention.name, scene.stats()["triangles"])
    )
    return filename


class _Warnings:
    """The minimum a target's prepare() needs; warnings go to the console."""

    def warn(self, message):
        FreeCAD.Console.PrintWarning("GameBridge: %s\n" % message)
        return message
