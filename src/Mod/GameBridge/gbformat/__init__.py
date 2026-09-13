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
"""File formats the bridge writes.

glTF/GLB is the interchange the engines read, OBJ is the fallback that
everything reads, and the ``.gbscene`` manifest is what turns a pile of files
into a scene the engine-side importer can rebuild.
"""

from .gltf import GLTFWriter, write_glb, write_gltf  # noqa: F401
from .obj import OBJWriter, write_obj  # noqa: F401
from .manifest import (  # noqa: F401
    AssetRecord,
    MANIFEST_FORMAT,
    build_manifest,
    read_manifest,
    write_manifest,
)

__all__ = [
    "AssetRecord",
    "GLTFWriter",
    "MANIFEST_FORMAT",
    "OBJWriter",
    "build_manifest",
    "read_manifest",
    "write_glb",
    "write_gltf",
    "write_manifest",
    "write_obj",
]
