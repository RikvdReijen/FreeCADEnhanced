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
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""RoboPrint workbench GUI initialisation."""

import FreeCAD
import FreeCADGui


class RoboPrintWorkbench(FreeCADGui.Workbench):
    """Large format robotic 3D printing: slicing, toolpaths and robot programs."""

    def __init__(self):
        # InitGui.py is executed with separate global and local scopes, so
        # everything a method needs has to be imported or defined inside it.
        import os

        def QT_TRANSLATE_NOOP(context, text):
            return text

        def find_icon():
            try:
                import RoboPrint_rc  # noqa: F401  (compiled resources: icons, ui)

                return ":/icons/RoboPrintWorkbench.svg"
            except ImportError:
                pass
            relative = os.path.join(
                "Mod", "RoboPrint", "Resources", "icons", "RoboPrintWorkbench.svg"
            )
            for base in (
                FreeCAD.getResourceDir(),
                FreeCAD.getUserAppDataDir(),
                FreeCAD.getHomePath(),
            ):
                candidate = os.path.join(base, relative)
                if os.path.exists(candidate):
                    return candidate
            return os.path.join(FreeCAD.getResourceDir(), relative)

        self.__class__.Icon = find_icon()
        self.__class__.MenuText = QT_TRANSLATE_NOOP("Workbench", "RoboPrint")
        self.__class__.ToolTip = QT_TRANSLATE_NOOP(
            "Workbench",
            "Large format robotic 3D printing: slice on planes, cones, cylinders or an "
            "existing surface, build the toolpath with the tool orientation, check it and "
            "write the robot program",
        )

    def Initialize(self):
        def QT_TRANSLATE_NOOP(context, text):
            return text

        from roboprint import commands

        commands.register()

        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "RoboPrint"), commands.TOOLBAR_COMMANDS)
        self.appendMenu(QT_TRANSLATE_NOOP("Workbench", "&RoboPrint"), commands.MENU_COMMANDS)

        FreeCADGui.addIconPath(":/icons")
        FreeCADGui.addLanguagePath(":/translations")
        FreeCADGui.addPreferencePage(
            ":/ui/preferences-roboprint.ui", QT_TRANSLATE_NOOP("QObject", "RoboPrint")
        )

    def Activated(self):
        FreeCAD.Console.PrintLog("RoboPrint workbench activated\n")

    def Deactivated(self):
        FreeCAD.Console.PrintLog("RoboPrint workbench deactivated\n")

    def ContextMenu(self, recipient):
        if recipient == "Tree":
            self.appendContextMenu(
                "RoboPrint", ["RoboPrint_Toolpath", "RoboPrint_Analyze", "RoboPrint_Export"]
            )

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(RoboPrintWorkbench())

FreeCAD.__unit_test__ += ["TestRoboPrintGui"]
