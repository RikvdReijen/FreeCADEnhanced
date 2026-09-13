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
        self.sketch_commands = [
            "XR_SketchSelect",
            "XR_SketchCurve",
            "XR_SketchPen",
            "XR_SketchPrimitive",
            "XR_SketchSubd",
            "XR_SketchMeasure",
            "XR_SketchSurface",
            "XR_SketchReference",
            "XR_SketchCommit",
        ]
        self.sculpt_commands = [
            "XR_SculptTarget",
            "XR_SculptMode",
            "XR_SculptMaskMode",
            "XR_SculptLayers",
            "XR_SculptSubdivide",
            "XR_SculptBake",
            "XR_SculptCommit",
        ]
        self.capture_commands = [
            "XR_MrcToggle",
            "XR_MrcMode",
            "XR_MrcCalibration",
        ]
        self.sync_commands = [
            "XR_SyncServerToggle",
            "XR_PairDevice",
            "XR_ExportFcxr",
            "XR_DriveOpen",
            "XR_DriveSave",
            "XR_DriveSettings",
        ]

        self.assembly_commands = ["XR_AssemblyMode", "XR_AssemblyCommit", "XR_FitCheck"]
        self.input_commands = ["XR_VoiceToggle", "XR_VoiceSay", "XR_HapticsToggle"]
        self.import_commands = ["XR_ImportUrl", "XR_ImportArchive", "XR_ScanImport", "XR_ScanAlign", "XR_ScanCommit"]
        self.machine_commands = ["XR_CamLoad", "XR_CamPlay", "XR_DrawTable", "XR_DrawDimension"]
        self.session_commands = ["XR_RoomHost", "XR_RoomGoto", "XR_Peers", "XR_QrMakeCode", "XR_VcsCommit", "XR_VcsVersion"]

        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR viewer"), self.viewer_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR environments"), self.environment_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR painting"), self.paint_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR sketching"), self.sketch_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR sculpting"), self.sculpt_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR capture"), self.capture_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR sync"), self.sync_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR assembly"), self.assembly_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR input"), self.input_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR import"), self.import_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR machine"), self.machine_commands)
        self.appendToolbar(QT_TRANSLATE_NOOP("Workbench", "XR session"), self.session_commands)

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
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Sketching")],
            self.sketch_commands,
        )
        self.appendMenu(
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Sculpting")],
            self.sculpt_commands,
        )
        self.appendMenu(
            [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", "Capture")],
            self.capture_commands,
        )
        for title, commands in (
            ("Assembly", self.assembly_commands),
            ("Input", self.input_commands),
            ("Import", self.import_commands),
            ("Machine", self.machine_commands),
            ("Session", self.session_commands),
        ):
            self.appendMenu(
                [QT_TRANSLATE_NOOP("Workbench", "Virtual Reality"), QT_TRANSLATE_NOOP("Workbench", title)],
                commands,
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
