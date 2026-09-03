# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *   Viewer commands based on freecad-xr-workbench,                        *
# *   (c) 2023-2026 Adrian Przekwas                                         *
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
"""Every GUI command of the XR workbench.

Importing this module registers the commands with FreeCADGui; it is imported
from ``InitGui.XRWorkbench.Initialize``.
"""

import os
import traceback

import FreeCAD
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

from xrcore import service

translate = FreeCAD.Qt.translate


def _report(exc, context):
    """Show a failure to the user without tearing down the GUI."""
    FreeCAD.Console.PrintError(f"XR: {context}: {exc}\n")
    FreeCAD.Console.PrintLog(traceback.format_exc())
    try:
        from PySide.QtWidgets import QMessageBox

        QMessageBox.warning(Gui.getMainWindow(), "Virtual Reality", f"{context}:\n{exc}")
    except Exception:
        pass


class XRCommand:
    """Small base class taking care of the boilerplate FreeCAD expects."""

    name = ""
    icon = ""
    menu_text = ""
    tool_tip = ""
    accel = ""
    needs_viewer = False
    needs_document = False

    def GetResources(self):
        resources = {
            "Pixmap": self.icon,
            "MenuText": QT_TRANSLATE_NOOP(self.name, self.menu_text),
            "ToolTip": QT_TRANSLATE_NOOP(self.name, self.tool_tip),
        }
        if self.accel:
            resources["Accel"] = self.accel
        return resources

    def IsActive(self):
        if self.needs_document and FreeCAD.ActiveDocument is None:
            return False
        if self.needs_viewer and service.get_widget() is None:
            return False
        return True

    def Activated(self):
        try:
            self.run()
        except service.XRServiceError as exc:
            _report(exc, self.menu_text)
        except ImportError as exc:
            _report(exc, f"{self.menu_text} — a Python dependency is missing")
        except Exception as exc:  # pragma: no cover - GUI only
            _report(exc, self.menu_text)

    def run(self):
        raise NotImplementedError


def register(command_class):
    """Class decorator instantiating and registering a command."""
    instance = command_class()
    Gui.addCommand(command_class.name, instance)
    return command_class


# --------------------------------------------------------------------------
# viewer
# --------------------------------------------------------------------------


@register
class XRStart(XRCommand):
    name = "XR_Start"
    icon = "Stepien_Glasses.svg"
    menu_text = "Open XR viewer"
    tool_tip = "Start rendering the active document to the connected VR headset"
    accel = "X,R"

    def run(self):
        from xrcore import commonXR

        commonXR.open_xr_viewer()


@register
class XRStop(XRCommand):
    name = "XR_Stop"
    icon = "Glasses_disabled.svg"
    menu_text = "Close XR viewer"
    tool_tip = "Stop the OpenXR session and release the headset"
    needs_viewer = True

    def run(self):
        from xrcore import commonXR

        commonXR.close_xr_viewer()


@register
class XREnableMirror(XRCommand):
    name = "XR_EnableMirror"
    icon = "Display_enabled.svg"
    menu_text = "Enable mirror window"
    tool_tip = "Show what the headset sees in a docked window on the desktop"
    needs_viewer = True

    def run(self):
        from xrcore import commonXR

        commonXR.open_xr_mirror()


@register
class XRDisableMirror(XRCommand):
    name = "XR_DisableMirror"
    icon = "Display_disabled.svg"
    menu_text = "Disable mirror window"
    tool_tip = "Hide the desktop mirror window to save GPU time"
    needs_viewer = True

    def run(self):
        from xrcore import commonXR

        commonXR.close_xr_mirror()


@register
class XRToggleTPPCamera(XRCommand):
    name = "XR_ToggleTPPCamera"
    icon = "TPPCam_toggle.svg"
    menu_text = "Toggle third person camera"
    tool_tip = "Switch the mirror window between the headset view and a tracked third person camera"
    needs_viewer = True

    def run(self):
        from xrcore import commonXR

        commonXR.toggle_tpp_camera()


@register
class XRReloadScenegraph(XRCommand):
    name = "XR_ReloadScenegraph"
    icon = "Reload_scenegraph.svg"
    menu_text = "Reload scenegraph"
    tool_tip = "Rebuild the VR scene from the active document"
    needs_viewer = True

    def run(self):
        from xrcore import commonXR

        commonXR.reload_scenegraph()


# --------------------------------------------------------------------------
# environment switcher
# --------------------------------------------------------------------------


@register
class XREnvironmentSwitcher(XRCommand):
    name = "XR_EnvironmentSwitcher"
    icon = "XR_Environment.svg"
    menu_text = "Environment…"
    tool_tip = (
        "Choose the world around you in VR — a neutral studio, or the inside of a "
        "3D printer or laser cutter, where you are miniaturised while you model"
    )
    accel = "X,E"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_environment_dialog()


@register
class XREnvironmentNext(XRCommand):
    name = "XR_EnvironmentNext"
    icon = "XR_EnvironmentNext.svg"
    menu_text = "Next environment"
    tool_tip = "Cycle to the next environment without opening the dialog"

    def run(self):
        from xrcore import environment_bridge

        environment_bridge.cycle_environment(1)


@register
class XRScaleDown(XRCommand):
    name = "XR_ScaleDown"
    icon = "XR_ScaleDown.svg"
    menu_text = "Shrink me"
    tool_tip = "Make yourself smaller relative to the scene (walk inside your model)"

    def run(self):
        from xrcore import environment_bridge

        environment_bridge.nudge_scale(1.25)


@register
class XRScaleUp(XRCommand):
    name = "XR_ScaleUp"
    icon = "XR_ScaleUp.svg"
    menu_text = "Grow me"
    tool_tip = "Make yourself larger relative to the scene"

    def run(self):
        from xrcore import environment_bridge

        environment_bridge.nudge_scale(1.0 / 1.25)


@register
class XRScaleReset(XRCommand):
    name = "XR_ScaleReset"
    icon = "XR_ScaleReset.svg"
    menu_text = "Reset scale"
    tool_tip = "Return to the environment's default scale"

    def run(self):
        from xrcore import environment_bridge

        environment_bridge.reset_scale()


# --------------------------------------------------------------------------
# painting and vector editing
# --------------------------------------------------------------------------


@register
class XRPaintTexture(XRCommand):
    name = "XR_PaintTexture"
    icon = "XR_PaintTexture.svg"
    menu_text = "Texture painting"
    tool_tip = "Paint directly onto the surface of your model with the motion controllers"
    accel = "X,P"

    def run(self):
        from xrcore import paint_bridge

        paint_bridge.activate_mode("TEXTURE")


@register
class XRPaintStroke3D(XRCommand):
    name = "XR_PaintStroke3D"
    icon = "XR_PaintStroke.svg"
    menu_text = "3D strokes"
    tool_tip = "Draw ribbons and tubes in mid air, and turn them into document geometry"

    def run(self):
        from xrcore import paint_bridge

        paint_bridge.activate_mode("STROKE3D")


@register
class XRVectorMode(XRCommand):
    name = "XR_VectorMode"
    icon = "XR_Vector.svg"
    menu_text = "Vector editor"
    tool_tip = "Draw and edit Bézier paths on a working plane, then commit them as Draft geometry"
    accel = "X,V"

    def run(self):
        from xrcore import paint_bridge

        paint_bridge.activate_mode("VECTOR")


@register
class XRPaintLayers(XRCommand):
    name = "XR_PaintLayers"
    icon = "XR_Layers.svg"
    menu_text = "Paint layers…"
    tool_tip = "Manage the layer stack of the current painting"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_layers_dialog()


@register
class XRPaintCommitVector(XRCommand):
    name = "XR_PaintCommitVector"
    icon = "XR_VectorCommit.svg"
    menu_text = "Commit vector paths"
    tool_tip = "Convert the vector paths drawn in VR into Draft wires, B-splines and faces"
    needs_document = True

    def run(self):
        from xrcore import paint_bridge

        created = paint_bridge.commit_vector_document()
        FreeCAD.Console.PrintMessage(f"XR: committed {created} vector object(s)\n")


@register
class XRPaintExportSvg(XRCommand):
    name = "XR_PaintExportSvg"
    icon = "XR_ExportSvg.svg"
    menu_text = "Export vector paths as SVG…"
    tool_tip = "Save the VR vector drawing as an SVG file"

    def run(self):
        from PySide.QtWidgets import QFileDialog

        from xrcore import paint_bridge

        path, _ = QFileDialog.getSaveFileName(
            Gui.getMainWindow(), "Export SVG", "", "Scalable Vector Graphics (*.svg)"
        )
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        paint_bridge.export_svg(path)
        FreeCAD.Console.PrintMessage(f"XR: wrote {path}\n")


# --------------------------------------------------------------------------
# sculpting
# --------------------------------------------------------------------------


@register
class XRSculptTarget(XRCommand):
    name = "XR_SculptTarget"
    icon = "XR_SculptTarget.svg"
    menu_text = "Sculpt the selected object"
    tool_tip = "Tessellate the selected object and make it sculptable in VR"
    needs_document = True

    def run(self):
        from xrcore import sculpt_bridge

        sculpt_bridge.add_target()


@register
class XRSculptMode(XRCommand):
    name = "XR_SculptMode"
    icon = "XR_Sculpt.svg"
    menu_text = "Sculpting"
    tool_tip = "Push, pull and smooth the surface with the motion controllers"
    accel = "X,S"

    def run(self):
        from xrcore import sculpt_bridge

        sculpt_bridge.activate_mode("SCULPT")


@register
class XRSculptMaskMode(XRCommand):
    name = "XR_SculptMaskMode"
    icon = "XR_SculptMask.svg"
    menu_text = "Mask painting"
    tool_tip = "Paint a mask to protect part of the surface from the brushes"

    def run(self):
        from xrcore import sculpt_bridge

        sculpt_bridge.activate_mode("MASK")


@register
class XRSculptLayers(XRCommand):
    name = "XR_SculptLayers"
    icon = "XR_SculptLayers.svg"
    menu_text = "Sculpt layers…"
    tool_tip = (
        "Manage the sculpt layer stack — weights, order and visibility, so a pass "
        "can be dialled back without losing the strokes underneath"
    )

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_sculpt_layers_dialog()


@register
class XRSculptSubdivide(XRCommand):
    name = "XR_SculptSubdivide"
    icon = "XR_SculptSubdivide.svg"
    menu_text = "Subdivide"
    tool_tip = "Add detail by subdividing the sculpt mesh once"

    def run(self):
        session = service.get_sculpt_session()
        if session is None or session.active_target() is None:
            raise service.XRServiceError("No object is being sculpted.")
        session.subdivide(1)
        target = session.active_target()
        FreeCAD.Console.PrintMessage(
            f"XR: subdivided to {len(target.mesh.positions) // 3} vertices\n"
        )


@register
class XRSculptBake(XRCommand):
    name = "XR_SculptBake"
    icon = "XR_SculptBake.svg"
    menu_text = "Bake layers"
    tool_tip = "Flatten every sculpt layer into the base mesh"

    def run(self):
        session = service.get_sculpt_session()
        if session is None or session.active_target() is None:
            raise service.XRServiceError("No object is being sculpted.")
        session.bake_layers()
        FreeCAD.Console.PrintMessage("XR: sculpt layers baked into the base mesh\n")


@register
class XRSculptCommit(XRCommand):
    name = "XR_SculptCommit"
    icon = "XR_SculptCommit.svg"
    menu_text = "Commit sculpt"
    tool_tip = "Write the sculpted mesh back onto the document object"
    needs_document = True

    def run(self):
        from xrcore import sculpt_bridge

        obj = sculpt_bridge.commit_to_document()
        FreeCAD.Console.PrintMessage(f"XR: committed the sculpt to {obj.Label}\n")


# --------------------------------------------------------------------------
# mixed reality capture
# --------------------------------------------------------------------------


@register
class XRMrcToggle(XRCommand):
    name = "XR_MrcToggle"
    icon = "XR_Mrc.svg"
    menu_text = "Mixed reality capture"
    tool_tip = (
        "Start or stop capture for LIV, OBS or a spectator view, using the "
        "tracked third person camera"
    )
    needs_viewer = True

    def run(self):
        session = service.require_mrc_session()
        session.toggle()
        FreeCAD.Console.PrintMessage(f"XR: {session.summary()}\n")


@register
class XRMrcMode(XRCommand):
    name = "XR_MrcMode"
    icon = "XR_MrcMode.svg"
    menu_text = "Capture mode"
    tool_tip = "Cycle between off, third person, four-quadrant MRC and LIV"
    needs_viewer = True

    def run(self):
        session = service.require_mrc_session()
        session.cycle()
        FreeCAD.Console.PrintMessage(f"XR: {session.summary()}\n")


@register
class XRMrcCalibration(XRCommand):
    name = "XR_MrcCalibration"
    icon = "XR_MrcSettings.svg"
    menu_text = "Camera calibration…"
    tool_tip = "Show the externalcamera.cfg the capture camera is using, and reload it"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_mrc_dialog()


# --------------------------------------------------------------------------
# sync and cloud
# --------------------------------------------------------------------------


@register
class XRSyncServerToggle(XRCommand):
    name = "XR_SyncServerToggle"
    icon = "XR_Sync.svg"
    menu_text = "Sync server"
    tool_tip = "Start or stop the companion server the headset connects to over your local network"

    def run(self):
        if service.sync_server() is not None:
            service.stop_sync_server()
        else:
            instance = service.start_sync_server()
            from xrcore import ui_dialogs

            ui_dialogs.show_server_info(instance)


@register
class XRPairDevice(XRCommand):
    name = "XR_PairDevice"
    icon = "XR_Pair.svg"
    menu_text = "Pair headset…"
    tool_tip = "Show a pairing code to type into the Quest application"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_pairing_dialog()


@register
class XRExportFcxr(XRCommand):
    name = "XR_ExportFcxr"
    icon = "XR_ExportFcxr.svg"
    menu_text = "Export scene for headset…"
    tool_tip = "Write the active document as an .fcxr package for the standalone Quest application"
    needs_document = True

    def run(self):
        from PySide.QtWidgets import QFileDialog

        from xrsync import scene_export

        default = f"{FreeCAD.ActiveDocument.Label}.fcxr"
        path, _ = QFileDialog.getSaveFileName(
            Gui.getMainWindow(), "Export FCXR", default, "FreeCAD XR scene (*.fcxr)"
        )
        if not path:
            return
        if not path.lower().endswith(".fcxr"):
            path += ".fcxr"
        scene_export.export_document(FreeCAD.ActiveDocument, path)
        FreeCAD.Console.PrintMessage(f"XR: wrote {path}\n")


@register
class XRDriveOpen(XRCommand):
    name = "XR_DriveOpen"
    icon = "XR_DriveOpen.svg"
    menu_text = "Open from Google Drive…"
    tool_tip = "Browse your Google Drive and open a FreeCAD document or XR scene"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_drive_browser(mode="open")


@register
class XRDriveSave(XRCommand):
    name = "XR_DriveSave"
    icon = "XR_DriveSave.svg"
    menu_text = "Save to Google Drive…"
    tool_tip = "Upload the active document to Google Drive so the headset can pick it up"
    needs_document = True

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_drive_browser(mode="save")


@register
class XRDriveSettings(XRCommand):
    name = "XR_DriveSettings"
    icon = "XR_DriveSettings.svg"
    menu_text = "Google Drive account…"
    tool_tip = "Sign in to, or sign out of, the Google account used for XR sync"

    def run(self):
        from xrcore import ui_dialogs

        ui_dialogs.show_drive_account_dialog()


ALL_COMMANDS = [
    "XR_Start",
    "XR_Stop",
    "XR_EnableMirror",
    "XR_DisableMirror",
    "XR_ToggleTPPCamera",
    "XR_ReloadScenegraph",
    "XR_EnvironmentSwitcher",
    "XR_EnvironmentNext",
    "XR_ScaleDown",
    "XR_ScaleUp",
    "XR_ScaleReset",
    "XR_PaintTexture",
    "XR_PaintStroke3D",
    "XR_VectorMode",
    "XR_PaintLayers",
    "XR_PaintCommitVector",
    "XR_PaintExportSvg",
    "XR_SculptTarget",
    "XR_SculptMode",
    "XR_SculptMaskMode",
    "XR_SculptLayers",
    "XR_SculptSubdivide",
    "XR_SculptBake",
    "XR_SculptCommit",
    "XR_MrcToggle",
    "XR_MrcMode",
    "XR_MrcCalibration",
    "XR_SyncServerToggle",
    "XR_PairDevice",
    "XR_ExportFcxr",
    "XR_DriveOpen",
    "XR_DriveSave",
    "XR_DriveSettings",
]
