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
"""The engine profiles, and the registry the GUI and the exporter look them up in."""

import os
import shutil

from .base import ExportOptions, ExportResult, Target  # noqa: F401
from .blender import BlenderTarget  # noqa: F401
from .unity import UnityTarget  # noqa: F401
from .unreal import UnrealTarget  # noqa: F401

__all__ = [
    "BlenderTarget",
    "ExportOptions",
    "ExportResult",
    "TARGETS",
    "Target",
    "UnityTarget",
    "UnrealTarget",
    "clients_directory",
    "copy_client_script",
    "export",
    "get_target",
    "target_names",
]

TARGETS = {
    UnrealTarget.name: UnrealTarget,
    UnityTarget.name: UnityTarget,
    BlenderTarget.name: BlenderTarget,
}


def target_names():
    """The identifiers, in the order the GUI should offer them."""
    return ["unreal", "unity", "blender"]


def get_target(name, options=None):
    """Instantiate a target by name."""
    if isinstance(name, Target):
        return name
    if isinstance(name, type) and issubclass(name, Target):
        return name(options)
    try:
        factory = TARGETS[str(name).strip().lower()]
    except KeyError:
        raise KeyError(
            "unknown target %r, expected one of %s"
            % (name, ", ".join(target_names()))
        )
    return factory(options or ExportOptions(mesh_format=factory.default_format))


def export(scene, directory, target="unreal", options=None):
    """Convenience wrapper: pick a target, export, hand back the result."""
    return get_target(target, options).export(scene, directory)


def clients_directory():
    """Where the engine-side client code lives inside the module."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clients")


def copy_client_script(relative_path, directory):
    """Copy one client script next to an export, so it is self-contained.

    Returns the destination path, or ``None`` when the script is missing - which
    happens in a source checkout that has not been installed, and is a warning
    rather than a failure because the export itself is still perfectly usable.
    """
    source = os.path.join(clients_directory(), relative_path)
    if not os.path.isfile(source):
        return None
    destination = os.path.join(directory, os.path.basename(source))
    shutil.copyfile(source, destination)
    return destination
