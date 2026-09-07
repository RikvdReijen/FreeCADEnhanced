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

"""GUI init of the experimental Grasshopper mode workbench."""

import FreeCAD
import FreeCADGui


class GrasshopperWorkbench(Workbench):  # noqa: F821 - provided by FreeCAD
    """Node-based parametric canvas with a mixed-reality front end."""

    def __init__(self):
        from grasshoppermode import resources

        self.__class__.Icon = resources.icon_path("GrasshopperWorkbench.svg")
        self.__class__.MenuText = "Grasshopper (experimental)"
        self.__class__.ToolTip = (
            "Experimental node-based (dataflow) parametric modelling canvas.\n"
            "Edit it on the desktop or on your table in mixed reality with a\n"
            "Logitech MX Ink stylus or your hands (Quest/WebXR)."
        )

    def Initialize(self):
        from grasshoppermode import commands

        commands.register()
        self.appendToolbar("Grasshopper", commands.TOOLBAR)
        self.appendToolbar("Grasshopper XR", commands.XR_TOOLBAR)
        self.appendMenu("&Grasshopper", commands.MENU)

    def Activated(self):
        FreeCAD.Console.PrintMessage(
            "Grasshopper mode is experimental: file layout and node ids may change.\n"
        )

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(GrasshopperWorkbench())
