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

"""Freeform workbench GUI initialisation."""

import FreeCAD
import FreeCADGui


class FreeformWorkbench(FreeCADGui.Workbench):
    """Free-form sketching in 3D: strokes, tubes, ribbons and surfaces."""

    def __init__(self):
        # InitGui.py is executed with separate global and local scopes, so
        # everything a method needs has to be imported or defined inside it.
        import os

        def QT_TRANSLATE_NOOP(context, text):
            return text

        def find_icon():
            try:
                import Freeform_rc  # noqa: F401  (compiled resources: icons, ui)

                return ":/icons/FreeformWorkbench.svg"
            except ImportError:
                pass
            relative = os.path.join(
                "Mod", "Freeform", "Resources", "icons", "FreeformWorkbench.svg"
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
        self.__class__.MenuText = QT_TRANSLATE_NOOP("Workbench", "Freeform")
        self.__class__.ToolTip = QT_TRANSLATE_NOOP(
            "Workbench",
            "Free-form sketching in 3D: draw strokes, give them thickness, "
            "span surfaces between them, mirror them live and smooth blocky shapes",
        )

    def Initialize(self):
        def QT_TRANSLATE_NOOP(context, text):
            return text

        from freeform import commands

        commands.register()

        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "Freeform"), commands.TOOLBAR_COMMANDS)
        self.appendToolbar(
            QT_TRANSLATE_NOOP("Workbench", "Freeform parametric"), commands.PARAMETRIC_COMMANDS
        )
        self.appendMenu(QT_TRANSLATE_NOOP("Workbench", "&Freeform"), commands.MENU_COMMANDS)
        self.appendMenu(
            [
                QT_TRANSLATE_NOOP("Workbench", "&Freeform"),
                QT_TRANSLATE_NOOP("Workbench", "Parametric"),
            ],
            commands.PARAMETRIC_MENU_COMMANDS,
        )

        FreeCADGui.addIconPath(":/icons")
        FreeCADGui.addLanguagePath(":/translations")
        FreeCADGui.addPreferencePage(
            ":/ui/preferences-freeform.ui", QT_TRANSLATE_NOOP("QObject", "Freeform")
        )

    def Activated(self):
        FreeCAD.Console.PrintLog("Freeform workbench activated\n")

    def Deactivated(self):
        FreeCAD.Console.PrintLog("Freeform workbench deactivated\n")

    def ContextMenu(self, recipient):
        if recipient == "View":
            self.appendContextMenu(
                "Freeform", ["Freeform_Stroke", "Freeform_Palette", "Freeform_Symmetry"]
            )

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(FreeformWorkbench())

FreeCAD.__unit_test__ += ["TestFreeformGui"]
