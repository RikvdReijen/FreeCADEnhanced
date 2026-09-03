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
"""FreeCAD import/export hooks for the ``.fcxr`` headset scene package.

Registered from :mod:`Init`, so ``File → Export`` offers "FreeCAD XR scene" and
a ``.fcxr`` brought back from the headset — carrying painted textures and VR
strokes — can be opened or merged into a document.
"""

import os

import FreeCAD


def export(objects, filename, options=None):
    """Export the given objects as an FCXR package."""
    from xrsync import scene_export

    document = objects[0].Document if objects else FreeCAD.ActiveDocument
    if document is None:
        raise ValueError("Nothing to export")
    lod = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/XR").GetInt("ExportLod", 1)
    scene_export.export_objects(objects, filename, document=document, lod=lod)
    FreeCAD.Console.PrintMessage(f"XR: exported {filename}\n")
    return filename


def open(filename):
    """Open an FCXR package in a new document."""
    name = os.path.splitext(os.path.basename(filename))[0]
    document = FreeCAD.newDocument(name)
    insert(filename, document.Name)
    return document


def insert(filename, docname=None):
    """Merge an FCXR package into an existing document.

    Meshes become ``Mesh::Feature`` objects, painted textures are re-attached to
    the matching objects when they are still present, and 3D strokes become
    their own mesh features so a VR painting survives the round trip.
    """
    from xrsync import scene_import

    if docname is None:
        document = FreeCAD.ActiveDocument or FreeCAD.newDocument(
            os.path.splitext(os.path.basename(filename))[0]
        )
    else:
        document = FreeCAD.getDocument(docname)

    created = scene_import.import_package(filename, document)
    document.recompute()
    FreeCAD.Console.PrintMessage(f"XR: imported {len(created)} object(s) from {filename}\n")
    return document
