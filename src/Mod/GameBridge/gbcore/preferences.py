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
"""The module's settings, and their translation into working objects.

FreeCAD keeps preferences in its parameter tree.  Reading them through this
module rather than directly does two things: it puts the defaults in one place
where they can be seen next to each other, and it lets everything downstream be
used - and tested - with no FreeCAD present, because a missing parameter tree
simply yields the defaults.
"""

from .tessellate import QUALITY, TessellationSettings

__all__ = ["PARAMETER_PATH", "DEFAULTS", "Preferences", "preferences"]

PARAMETER_PATH = "User parameter:BaseApp/Preferences/Mod/GameBridge"

#: Every setting the module has, with its default and its type.
DEFAULTS = {
    "Target": "unreal",
    "MeshFormat": "glb",
    "Quality": "normal",
    "Deviation": 0.1,
    "AngularDeviation": 20.0,
    "RelativeDeviation": False,
    "Weld": True,
    "DropDegenerate": True,
    "IncludeHidden": False,
    "PerFaceMaterials": True,
    "ExportDirectory": "",
    "LinkPort": 54321,
    "LinkToken": "",
    "LinkAutoPublish": True,
    "LinkTarget": "unity",
}

_TYPES = {
    str: ("GetString", "SetString"),
    bool: ("GetBool", "SetBool"),
    int: ("GetInt", "SetInt"),
    float: ("GetFloat", "SetFloat"),
}


class Preferences:
    """A thin, typed wrapper over one parameter group."""

    def __init__(self, path=PARAMETER_PATH, defaults=None):
        self.path = path
        self.defaults = dict(defaults or DEFAULTS)

    def _group(self):
        try:
            import FreeCAD
        except ImportError:
            return None
        try:
            return FreeCAD.ParamGet(self.path)
        except Exception:
            return None

    def get(self, key, default=None):
        if default is None:
            default = self.defaults.get(key)
        group = self._group()
        if group is None:
            return default
        getter = _TYPES.get(type(default))
        if getter is None:
            return default
        try:
            return getattr(group, getter[0])(key, default)
        except Exception:
            return default

    def set(self, key, value):
        group = self._group()
        if group is None:
            return False
        setter = _TYPES.get(type(value))
        if setter is None:
            return False
        try:
            getattr(group, setter[1])(key, value)
            return True
        except Exception:
            return False

    def all(self):
        return {key: self.get(key) for key in self.defaults}

    # -- derived objects -------------------------------------------------

    def tessellation_settings(self):
        """The settings the tessellator should use.

        A named quality is a starting point, not a straitjacket: a user who has
        set an explicit deviation gets that, and the preset only fills in what
        they have not overridden.
        """
        preset = QUALITY.get(str(self.get("Quality")).lower(), QUALITY["normal"])
        return TessellationSettings(
            deviation=self.get("Deviation") or preset.deviation,
            angular_deviation=self.get("AngularDeviation") or preset.angular_deviation,
            relative=self.get("RelativeDeviation"),
            per_face_materials=self.get("PerFaceMaterials"),
        )

    def export_options(self, target=None):
        """The options an export should run with."""
        from gbtargets import ExportOptions

        return ExportOptions(
            mesh_format=self.get("MeshFormat"),
            weld=self.get("Weld"),
            drop_degenerate=self.get("DropDegenerate"),
            include_hidden=self.get("IncludeHidden"),
        )

    def link_settings(self):
        return {
            "port": int(self.get("LinkPort")),
            "token": self.get("LinkToken") or None,
            "convention": self.get("LinkTarget"),
            "auto_publish": bool(self.get("LinkAutoPublish")),
        }


#: The instance everything uses; a test can build its own with other defaults.
preferences = Preferences()
