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
"""Engine-independent core of the GameBridge module.

Nothing in :mod:`gbcore` imports FreeCAD at module level.  The pieces that do
need a running FreeCAD - the document walker and the tessellator - import it
inside the functions that use it, so that the Blender add-on, the Unreal editor
scripts and the unit tests can all import this package on a bare interpreter.
"""

from .transform import (  # noqa: F401
    AxisConvention,
    BLENDER,
    CONVENTIONS,
    FREECAD,
    GLTF,
    Matrix4,
    UNITY,
    UNREAL,
    get_convention,
)
from .scene import Material, Mesh, Node, Scene, SceneError  # noqa: F401
from .naming import NameAllocator, NamePolicy, get_policy  # noqa: F401
from .materials import (  # noqa: F401
    DEFAULT_MATERIAL,
    material_from_appearance,
    materials_from_object,
    phong_to_pbr,
)

#: Version of the bridge itself, reported in every export and every handshake.
#: Bumped when the on-the-wire or on-disk format changes in a way the engine
#: side has to know about.
BRIDGE_VERSION = "1.0.0"

#: The scene format revision written into ``.gbscene`` manifests.
SCENE_FORMAT_VERSION = 1

__all__ = [
    "AxisConvention",
    "BLENDER",
    "BRIDGE_VERSION",
    "CONVENTIONS",
    "DEFAULT_MATERIAL",
    "FREECAD",
    "GLTF",
    "Material",
    "Matrix4",
    "Mesh",
    "NameAllocator",
    "NamePolicy",
    "Node",
    "SCENE_FORMAT_VERSION",
    "Scene",
    "SceneError",
    "UNITY",
    "UNREAL",
    "get_convention",
    "get_policy",
    "material_from_appearance",
    "materials_from_object",
    "phong_to_pbr",
]
