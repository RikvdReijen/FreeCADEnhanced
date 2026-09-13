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
"""The workbench's commands.

Each one is a thin shell: ask the user for whatever the operation needs, call
:mod:`gbcore.service`, and report what happened.  Nothing here decides anything
about exporting, which is why the same behaviour is available from a macro.
"""

import os

import FreeCAD
import FreeCADGui
from PySide import QtGui

from . import service
from .preferences import preferences

__all__ = ["COMMANDS", "register"]

TRANSLATE = FreeCAD.Qt.translate


def _icon(name):
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "Resources",
        "icons",
        name,
    )


def _selected_objects():
    return list(FreeCADGui.Selection.getSelection())


def _report(title, message, level="information"):
    getattr(QtGui.QMessageBox, level)(FreeCADGui.getMainWindow(), title, message)


class _ExportCommand:
    """Shared behaviour: pick a folder, export, say what happened."""

    target = "unreal"
    engine_title = "the engine"
    icon = "GameBridge_Export.svg"

    def GetResources(self):
        return {
            "Pixmap": _icon(self.icon),
            "MenuText": TRANSLATE("GameBridge", "Export to %s") % self.engine_title,
            "ToolTip": TRANSLATE(
                "GameBridge",
                "Export the visible geometry of the active document for %s. "
                "Select objects first to export only those.",
            )
            % self.engine_title,
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        document = FreeCAD.ActiveDocument
        if document is None:
            return
        start = preferences.get("ExportDirectory") or os.path.expanduser("~")
        directory = QtGui.QFileDialog.getExistingDirectory(
            FreeCADGui.getMainWindow(),
            TRANSLATE("GameBridge", "Export folder for %s") % self.engine_title,
            start,
        )
        if not directory:
            return
        preferences.set("ExportDirectory", directory)
        objects = _selected_objects()
        try:
            result = service.export_document(
                self.target, directory, document, objects or None
            )
        except Exception as problem:
            FreeCAD.Console.PrintError("GameBridge: %s\n" % problem)
            _report(
                TRANSLATE("GameBridge", "Export failed"), str(problem), "critical"
            )
            return
        message = result.summary()
        if result.warnings:
            message += "\n\n" + "\n".join(result.warnings)
        _report(TRANSLATE("GameBridge", "Export finished"), message)


class ExportUnreal(_ExportCommand):
    target = "unreal"
    engine_title = "Unreal Engine"
    icon = "GameBridge_Unreal.svg"


class ExportUnity(_ExportCommand):
    target = "unity"
    engine_title = "Unity"
    icon = "GameBridge_Unity.svg"


class ExportBlender(_ExportCommand):
    target = "blender"
    engine_title = "Blender"
    icon = "GameBridge_Blender.svg"


class ToggleLink:
    """Start or stop the live link."""

    def GetResources(self):
        return {
            "Pixmap": _icon("GameBridge_Link.svg"),
            "MenuText": TRANSLATE("GameBridge", "Live link"),
            "ToolTip": TRANSLATE(
                "GameBridge",
                "Start or stop the live link, which pushes the document to a "
                "connected engine as you edit it.",
            ),
            "Checkable": True,
        }

    def IsActive(self):
        return True

    def Activated(self, checked=None):
        server = service.link_server()
        if server is not None and server.running:
            service.stop_link()
            _report(
                TRANSLATE("GameBridge", "Live link"),
                TRANSLATE("GameBridge", "The live link has been stopped."),
            )
            return
        try:
            server = service.start_link()
        except Exception as problem:
            FreeCAD.Console.PrintError("GameBridge: %s\n" % problem)
            _report(TRANSLATE("GameBridge", "Live link"), str(problem), "critical")
            return
        _report(
            TRANSLATE("GameBridge", "Live link"),
            TRANSLATE(
                "GameBridge",
                "Listening on 127.0.0.1:%d.\n\nConnect the engine-side client "
                "and the document will follow your edits.",
            )
            % server.port,
        )


class PublishNow:
    """Push the document at the link immediately, ignoring the throttle."""

    def GetResources(self):
        return {
            "Pixmap": _icon("GameBridge_Publish.svg"),
            "MenuText": TRANSLATE("GameBridge", "Publish now"),
            "ToolTip": TRANSLATE(
                "GameBridge", "Send the current state of the document to every connected engine."
            ),
        }

    def IsActive(self):
        server = service.link_server()
        return bool(server and server.running and FreeCAD.ActiveDocument)

    def Activated(self):
        scene = service.publish(force=True)
        if scene is None:
            FreeCAD.Console.PrintWarning("GameBridge: nothing to publish\n")
            return
        FreeCAD.Console.PrintMessage(
            "GameBridge: published %s\n" % (scene.stats(),)
        )


class LinkStatus:
    """Show who is connected and what they have."""

    def GetResources(self):
        return {
            "Pixmap": _icon("GameBridge_Status.svg"),
            "MenuText": TRANSLATE("GameBridge", "Link status"),
            "ToolTip": TRANSLATE("GameBridge", "Show the state of the live link."),
        }

    def IsActive(self):
        return True

    def Activated(self):
        status = service.link_status()
        if not status.get("running"):
            _report(
                TRANSLATE("GameBridge", "Link status"),
                TRANSLATE("GameBridge", "The live link is not running."),
            )
            return
        lines = [
            TRANSLATE("GameBridge", "Listening on %s:%d")
            % (status["host"], status["port"]),
            TRANSLATE("GameBridge", "Sending in %s space") % status["target"]["name"],
            "",
        ]
        clients = status.get("clients") or []
        if not clients:
            lines.append(TRANSLATE("GameBridge", "No engine has connected yet."))
        for client in clients:
            lines.append(
                "%s - %d node(s), %d mesh(es), connected %.0f s"
                % (
                    client["name"],
                    client["session"]["nodes"],
                    client["session"]["meshes"],
                    client["connectedFor"],
                )
            )
        _report(TRANSLATE("GameBridge", "Link status"), "\n".join(lines))


#: Command name to implementation.  Names are prefixed so they cannot collide
#: with another workbench's.
COMMANDS = {
    "GameBridge_ExportUnreal": ExportUnreal,
    "GameBridge_ExportUnity": ExportUnity,
    "GameBridge_ExportBlender": ExportBlender,
    "GameBridge_ToggleLink": ToggleLink,
    "GameBridge_PublishNow": PublishNow,
    "GameBridge_LinkStatus": LinkStatus,
}


def register():
    """Add every command to FreeCAD.  Returns the names, in toolbar order."""
    for name, factory in COMMANDS.items():
        FreeCADGui.addCommand(name, factory())
    return [
        "GameBridge_ExportUnreal",
        "GameBridge_ExportUnity",
        "GameBridge_ExportBlender",
        "Separator",
        "GameBridge_ToggleLink",
        "GameBridge_PublishNow",
        "GameBridge_LinkStatus",
    ]
