# SPDX-License-Identifier: LGPL-2.1-or-later
"""Core of the FreeCAD Virtual Reality workbench.

The OpenXR session, the Coin3D scenegraph plumbing, the motion controllers and
the in-VR menus come from Adrian Przekwas' ``freecad-xr-workbench`` (see
``../LICENSE-upstream.txt`` and ``../NOTICE.md``); the service broker, the
commands and the bridges to :mod:`xrenv`, :mod:`xrpaint` and :mod:`xrsync` are
part of FreeCAD.

Nothing here is imported eagerly: ``commonXR`` pulls in ``pyopenxr``, ``PyOpenGL``
and ``pivy``, so importing this package must stay cheap enough for console mode.
"""

__all__ = [
    "commands",
    "commonXR",
    "controllerXR",
    "documentInteraction",
    "environment_bridge",
    "menuCoin",
    "movementXR",
    "paint_bridge",
    "preferences",
    "preferences_xr",
    "previewCoin",
    "qtWidgetRender",
    "service",
    "ui_dialogs",
]
