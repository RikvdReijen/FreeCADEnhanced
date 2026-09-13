# SPDX-License-Identifier: LGPL-2.1-or-later
"""Locate module resources both in a build/install tree and in the source
tree (so the XR client can be served straight from a checkout)."""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SOURCE_RESOURCES = os.path.join(os.path.dirname(_HERE), "Resources")


def resources_dir():
    try:
        import FreeCAD

        installed = os.path.join(FreeCAD.getResourceDir(), "Mod", "Grasshopper", "Resources")
        if os.path.isdir(os.path.join(installed, "xr")):
            return installed
    except ImportError:
        pass
    return _SOURCE_RESOURCES


def xr_client_dir():
    return os.path.join(resources_dir(), "xr")


def icon_path(name):
    return os.path.join(resources_dir(), "icons", name)
