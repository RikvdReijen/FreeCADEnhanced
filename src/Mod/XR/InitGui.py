# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *   Based on freecad-xr-workbench, (c) 2023-2026 Adrian Przekwas          *
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
"""Registration of the Virtual Reality (XR) workbench."""

import os

import FreeCAD
import FreeCADGui as Gui
from FreeCADGui import Workbench

__dir__ = os.path.dirname(__file__)


class XRWorkbench(Workbench):
    """Virtual Reality workbench.

    Provides an OpenXR viewer for the desktop, an environment switcher that
    places the user inside machine interiors at miniature scale, a VR painting
    and vector drawing module, and the sync services that feed the standalone
    Meta Quest 3 application.
    """

    MenuText = "Virtual Reality"
    ToolTip = "View, model and paint your documents in VR (OpenXR)"
    Icon = os.path.join(__dir__, "Resources", "icons", "Stepien_Glasses.svg")

    def Initialize(self):
        from PySide.QtCore import QT_TRANSLATE_NOOP

        # Importing the command module registers every XR_* command.
        from xrcore import commands  # noqa: F401

        self.viewer_commands = [
            "XR_Start",
            "XR_Stop",
            "Separator",
            "XR_EnableMirror",
            "XR_DisableMirror",
            "XR_ToggleTPPCamera",
            "XR_ReloadScenegraph",
        ]
        self.environment_commands = [
            "XR_EnvironmentSwitcher",
            "XR_EnvironmentNext",
            "XR_ScaleDown",
            "XR_ScaleUp",
            "XR_ScaleReset",
        ]
        self.paint_commands = [
            "XR_PaintTexture",
            "XR_PaintStroke3D",
            "XR_VectorMode",
            "XR_PaintLayers",
            "XR_PaintCommitVector",
            "XR_PaintExportSvg",
        ]
        self.sync_commands = [
            "XR_SyncServerToggle",
            "XR_PairDevice",
            "XR_ExportFcxr",
            "XR_DriveOpen",
            "XR_DriveSave",
            "XR_DriveSettings",
        ]

        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR viewer"), self.viewer_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR environments"), self.environment_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR painting"), self.paint_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR sync"), self.sync_commands)

        self.appendMenu(QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), self.viewer_commands)
        self.appendMenu(
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Environment")],
            self.environment_commands,
        )
        self.appendMenu(
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Painting")],
            self.paint_commands,
        )
        self.appendMenu(
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Sync")],
            self.sync_commands,
        )

        Gui.addIconPath(os.path.join(__dir__, "Resources", "icons"))
        Gui.addLanguagePath(os.path.join(__dir__, "Resources", "translations"))

        from xrcore import preferences

        Gui.addPreferencePage(
            preferences.VRPreferencesPage, QT_TRANSLATE_NOOP("QObject", "Virtual Reality")
        )
        from xrcore import preferences_xr

        Gui.addPreferencePage(
            preferences_xr.XRSyncPreferencesPage, QT_TRANSLATE_NOOP("QObject", "Virtual Reality")
        )

        FreeCAD.Console.PrintLog("XR workbench loaded\n")

    def Activated(self):
        # Autostart of the companion sync server is opt-in.
        try:
            from xrcore import service

            service.autostart_if_enabled()
        except Exception as exc:  # pragma: no cover - GUI only
            FreeCAD.Console.PrintWarning(f"XR: sync autostart failed: {exc}\n")

    def Deactivated(self):
        return

    def ContextMenu(self, recipient):
        self.appendContextMenu("XR", self.viewer_commands)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(XRWorkbench())
