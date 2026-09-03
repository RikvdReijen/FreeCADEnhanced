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
"""The GameBridge workbench.

Kept as small as a workbench can be: the commands live in
:mod:`gbcore.commands` and the work lives below that, so the class here is only
menus, toolbars and lifecycle.
"""

import FreeCAD
import FreeCADGui


class GameBridgeWorkbench(FreeCADGui.Workbench):
    """Send FreeCAD models to Unreal Engine, Unity and Blender."""

    MenuText = "GameBridge"
    ToolTip = (
        "Export models to Unreal Engine, Unity and Blender, or push them to a "
        "running engine over the live link"
    )

    def __init__(self):
        import os

        self.__class__.Icon = os.path.join(
            FreeCAD.getResourceDir(), "Mod", "GameBridge", "Resources", "icons",
            "GameBridgeWorkbench.svg",
        )
        self.commands = []

    def Initialize(self):
        """Called once, the first time the user selects the workbench."""
        from gbcore import commands

        self.commands = commands.register()
        self.appendToolbar("GameBridge", self.commands)
        self.appendMenu("&GameBridge", self.commands)
        FreeCADGui.addPreferencePage(
            FreeCAD.getResourceDir()
            + "Mod/GameBridge/Resources/GameBridgePreferences.ui",
            "GameBridge",
        )
        FreeCAD.Console.PrintLog("GameBridge workbench loaded\n")

    def Activated(self):
        return

    def Deactivated(self):
        return

    def ContextMenu(self, recipient):
        if FreeCADGui.Selection.getSelection():
            self.appendContextMenu(
                "GameBridge",
                [
                    "GameBridge_ExportUnreal",
                    "GameBridge_ExportUnity",
                    "GameBridge_ExportBlender",
                ],
            )

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(GameBridgeWorkbench())
