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
# Gravity-Sketch-style design tools
# --------------------------------------------------------------------------


@register
class XRSketchSelect(XRCommand):
    name = "XR_SketchSelect"
    icon = "XR_SketchSelect.svg"
    menu_text = "Select and move"
    tool_tip = "Pick objects and move them; grab with both hands to scale and rotate"
    accel = "X,G"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("SELECT")


@register
class XRSketchCurve(XRCommand):
    name = "XR_SketchCurve"
    icon = "XR_SketchCurve.svg"
    menu_text = "Freehand curve"
    tool_tip = "Draw a curve in the air; it is fitted to clean Béziers as you release"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("CURVE")


@register
class XRSketchPen(XRCommand):
    name = "XR_SketchPen"
    icon = "XR_SketchPen.svg"
    menu_text = "Control point pen"
    tool_tip = "Place and edit control points and tangent handles directly"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("PEN")


@register
class XRSketchPrimitive(XRCommand):
    name = "XR_SketchPrimitive"
    icon = "XR_SketchPrimitive.svg"
    menu_text = "Primitives"
    tool_tip = "Place a box, sphere, cylinder, cone, torus, plane or tube between your hands"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("PRIMITIVE")


@register
class XRSketchSubd(XRCommand):
    name = "XR_SketchSubd"
    icon = "XR_SketchSubd.svg"
    menu_text = "Subdivision cage"
    tool_tip = "Push a control cage around and watch the smooth surface follow"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("SUBD")


@register
class XRSketchMeasure(XRCommand):
    name = "XR_SketchMeasure"
    icon = "XR_SketchMeasure.svg"
    menu_text = "Measure"
    tool_tip = "Measure distances and angles — readouts stay true even when you are miniaturised"

    def run(self):
        from xrcore import sketch_bridge

        sketch_bridge.activate_tool("MEASURE")


@register
class XRSketchSurface(XRCommand):
    name = "XR_SketchSurface"
    icon = "XR_SketchSurface.svg"
    menu_text = "Surface from curves"
    tool_tip = "Loft, revolve, sweep or patch a surface through the selected curves"

    def run(self):
        from xrsketch import surfacing

        session = service.get_sketch_session()
        if session is None:
            raise service.XRServiceError("Draw some curves first.")
        curves = [
            obj.data
            for obj in session.scene.selected_objects()
            if getattr(obj, "kind", None) == "curve"
        ]
        if len(curves) < 2:
            raise service.XRServiceError(
                "Select at least two curves to loft a surface through."
            )
        # Two boundary curves loft; three or four can also close as a patch,
        # and a loft is the reading that always holds, so it is the default.
        session.history_begin("surface from curves")
        try:
            surface = surfacing.loft(curves)
            session.scene.add_surface(surface)
        finally:
            session.history_commit()
        FreeCAD.Console.PrintMessage(
            f"XR: lofted a surface through {len(curves)} curves\n"
        )


@register
class XRSketchReference(XRCommand):
    name = "XR_SketchReference"
    icon = "XR_SketchReference.svg"
    menu_text = "Reference image…"
    tool_tip = "Place a blueprint or backdrop image in space to model against"

    def run(self):
        from PySide.QtWidgets import QFileDialog

        from xrcore import sketch_bridge

        path, _ = QFileDialog.getOpenFileName(
            Gui.getMainWindow(), "Reference image", "", "Images (*.png *.jpg *.jpeg)"
        )
        if not path:
            return

        from xrsketch.reference import ImagePlane

        session = sketch_bridge.ensure_session()
        # A metre-wide plane a little in front of the user reads well at 1:1 and
        # can be grabbed and resized from there.
        plane = ImagePlane(source=path, size=(1.0, 1.0), origin=(0.0, 1.2, -0.8))
        session.history_begin("reference image")
        try:
            session.scene.add("image", plane, name=os.path.basename(path))
        finally:
            session.history_commit()
        FreeCAD.Console.PrintMessage(f"XR: placed {os.path.basename(path)} as a reference\n")


@register
class XRSketchCommit(XRCommand):
    name = "XR_SketchCommit"
    icon = "XR_SketchCommit.svg"
    menu_text = "Commit sketch"
    tool_tip = "Turn the VR sketch into Draft curves, Part surfaces and primitives"
    needs_document = True

    def run(self):
        from xrcore import sketch_bridge

        objects = sketch_bridge.commit_sketch()
        FreeCAD.Console.PrintMessage(f"XR: committed {len(objects)} sketch object(s)\n")


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



# --------------------------------------------------------------------------
# xr-v0.2: assembly, fit check, voice, import, CAM preview, drawings, scans,
# haptics, QR anchors, peers
# --------------------------------------------------------------------------


def _ask_text(title, label, default=""):
    from PySide.QtWidgets import QInputDialog

    text, ok = QInputDialog.getText(Gui.getMainWindow(), title, label, text=default)
    return text.strip() if ok and text.strip() else None


def _ask_file(title, pattern):
    from PySide.QtWidgets import QFileDialog

    path, _ = QFileDialog.getOpenFileName(Gui.getMainWindow(), title, "", pattern)
    return path or None


def _exclusive_mode():
    """Release the painting, sculpting and sketching modes before an xr-v0.2 mode."""
    from xrcore import paint_bridge, sculpt_bridge, sketch_bridge

    paint_bridge.deactivate()
    sculpt_bridge.deactivate()
    sketch_bridge.deactivate()
    for name in ("assembly_bridge", "fit_bridge", "draw_bridge", "scan_bridge"):
        try:
            __import__("xrcore." + name, fromlist=["deactivate"]).deactivate()
        except Exception:
            pass


@register
class XRAssemblyMode(XRCommand):
    name = "XR_AssemblyMode"
    icon = "XR_Assembly.svg"
    menu_text = "Assembly mode"
    tool_tip = "Place mate constraints by hand: grip a part, bring it to another, trigger to confirm the mate"
    needs_viewer = True
    needs_document = True

    def run(self):
        from xrcore import assembly_bridge

        _exclusive_mode()
        assembly_bridge.ensure_session()
        count = assembly_bridge.reload()
        assembly_bridge.activate()
        FreeCAD.Console.PrintMessage(f"XR: assembly mode with {count} part(s)\n")


@register
class XRAssemblyCommit(XRCommand):
    name = "XR_AssemblyCommit"
    icon = "XR_AssemblyCommit.svg"
    menu_text = "Commit mates"
    tool_tip = "Write the VR mates into the document as Assembly joints (placements when the Assembly workbench is absent)"
    needs_document = True

    def run(self):
        from xrcore import assembly_bridge

        result = assembly_bridge.commit()
        FreeCAD.Console.PrintMessage(
            f"XR: {len(result.placements)} placement(s), {len(result.joints)} joint(s), {len(result.skipped)} skipped\n")


@register
class XRFitCheck(XRCommand):
    name = "XR_FitCheck"
    icon = "XR_FitCheck.svg"
    menu_text = "Fit check mode"
    tool_tip = "Grab a part and try to insert it; collision stops it in your hand and reports the clearance"
    needs_viewer = True
    needs_document = True

    def run(self):
        from xrcore import fit_bridge

        _exclusive_mode()
        fit_bridge.ensure_session()
        count = fit_bridge.reload()
        fit_bridge.activate()
        FreeCAD.Console.PrintMessage(f"XR: fit check with {count} part(s)\n")


@register
class XRVoiceToggle(XRCommand):
    name = "XR_VoiceToggle"
    icon = "XR_Voice.svg"
    menu_text = "Voice input"
    tool_tip = "Start or stop listening for spoken modelling commands ('fillet these edges, 2 mm')"

    def run(self):
        from xrcore import voice_bridge

        state = voice_bridge.toggle()
        FreeCAD.Console.PrintMessage("XR: voice %s\n" % ("listening" if state else "off"))


@register
class XRVoiceSay(XRCommand):
    name = "XR_VoiceSay"
    icon = "XR_Voice.svg"
    menu_text = "Type a voice command…"
    tool_tip = "Run a spoken command by typing it — the same grammar, without a microphone"

    def run(self):
        from xrcore import voice_bridge

        text = _ask_text("Voice command", "Say:", "fillet 2 mm")
        if text:
            voice_bridge.say(text)


@register
class XRImportUrl(XRCommand):
    name = "XR_ImportUrl"
    icon = "XR_ImportUrl.svg"
    menu_text = "Import from URL…"
    tool_tip = "Import a model from Thingiverse, Printables, MakerWorld or GrabCAD by its page URL"
    needs_document = True

    def run(self):
        from xrcore import import_bridge

        url = _ask_text("Import model", "Model page URL:")
        if url:
            import_bridge.import_url(url)


@register
class XRImportArchive(XRCommand):
    name = "XR_ImportArchive"
    icon = "XR_ImportArchive.svg"
    menu_text = "Import archive or mesh…"
    tool_tip = "Import every model file from a downloaded ZIP (GrabCAD, Thingiverse), or a single STL/OBJ/PLY/3MF/STEP"
    needs_document = True

    def run(self):
        from xrcore import import_bridge

        path = _ask_file("Import", "Models (*.zip *.stl *.obj *.ply *.3mf *.step *.stp *.iges *.igs);;All files (*)")
        if not path:
            return
        if path.lower().endswith(".zip"):
            import_bridge.import_archive(path)
        else:
            import_bridge.import_file(path)


@register
class XRCamLoad(XRCommand):
    name = "XR_CamLoad"
    icon = "XR_CamLoad.svg"
    menu_text = "Load toolpath…"
    tool_tip = "Preview a G-code file (or the selected CAM job) inside the machine environment"

    def run(self):
        from xrcore import cam_bridge, docmesh

        for obj, _ in docmesh.selected_objects():
            if getattr(obj, "TypeId", "").startswith("Path::") and hasattr(obj, "Operations"):
                cam_bridge.load_job(obj)
                return
        path = _ask_file("Load G-code", "G-code (*.gcode *.gco *.g *.nc *.ngc *.tap);;All files (*)")
        if path:
            cam_bridge.load_gcode(path)


@register
class XRCamPlay(XRCommand):
    name = "XR_CamPlay"
    icon = "XR_CamPlay.svg"
    menu_text = "Play / pause toolpath"
    tool_tip = "Run the loaded toolpath at scale; collisions and out-of-travel moves are reported as they happen"

    def run(self):
        from xrcore import cam_bridge

        playing = cam_bridge.toggle()
        FreeCAD.Console.PrintMessage("XR CAM: %s\n" % ("playing" if playing else "paused"))


@register
class XRDrawTable(XRCommand):
    name = "XR_DrawTable"
    icon = "XR_DrawTable.svg"
    menu_text = "Drafting table"
    tool_tip = "Put the document's TechDraw page on a drafting table in VR and dimension it by pointing"
    needs_viewer = True
    needs_document = True

    def run(self):
        from xrcore import draw_bridge

        _exclusive_mode()
        draw_bridge.activate()


@register
class XRDrawDimension(XRCommand):
    name = "XR_DrawDimension"
    icon = "XR_DrawTable.svg"
    menu_text = "Place dimension"
    tool_tip = "Turn the picks on the drafting table into a TechDraw dimension"
    needs_document = True

    def run(self):
        from xrcore import draw_bridge

        draw_bridge.place_dimension()


@register
class XRScanImport(XRCommand):
    name = "XR_ScanImport"
    icon = "XR_ScanImport.svg"
    menu_text = "Import scan…"
    tool_tip = "Bring in a scanned mesh at 1:1 and align it to the selected model by touching matching points"
    needs_document = True

    def run(self):
        from xrcore import scan_bridge

        path = _ask_file("Import scan", "Meshes (*.stl *.obj *.ply *.3mf);;All files (*)")
        if not path:
            return
        scan_bridge.import_scan(path)
        if service.get_widget() is not None:
            _exclusive_mode()
            scan_bridge.activate()


@register
class XRScanAlign(XRCommand):
    name = "XR_ScanAlign"
    icon = "XR_ScanAlign.svg"
    menu_text = "Align scan"
    tool_tip = "Align the scan from the picked point pairs, then refine it with ICP against the model"

    def run(self):
        from xrcore import scan_bridge

        session = scan_bridge.get_session()
        if session is None:
            raise service.XRServiceError("Import a scan first.")
        if len(session.complete_pairs()) >= 3:
            scan_bridge.align()
        if session.model is not None:
            scan_bridge.refine()


@register
class XRScanCommit(XRCommand):
    name = "XR_ScanCommit"
    icon = "XR_ScanImport.svg"
    menu_text = "Commit scan"
    tool_tip = "Add the aligned scan to the document as a mesh object"
    needs_document = True

    def run(self):
        from xrcore import scan_bridge

        obj = scan_bridge.commit()
        FreeCAD.Console.PrintMessage(f"XR: scan committed as {obj.Name}\n")


@register
class XRHapticsToggle(XRCommand):
    name = "XR_HapticsToggle"
    icon = "XR_Haptics.svg"
    menu_text = "Haptics"
    tool_tip = "Enable or disable controller vibration on snaps, contacts and confirmed constraints"

    def run(self):
        from xrcore import haptics_bridge

        eng = haptics_bridge.engine()
        haptics_bridge.set_enabled(not eng.enabled)
        if eng.enabled:
            haptics_bridge.test_pulse()
        FreeCAD.Console.PrintMessage("XR: %s\n" % eng.describe())


@register
class XRQrMakeCode(XRCommand):
    name = "XR_QrMakeCode"
    icon = "XR_QrAnchor.svg"
    menu_text = "Make anchor code…"
    tool_tip = "Create a printable QR anchor code that snaps the model (or a part) to where the code is placed"

    def run(self):
        from xrcore import qr_bridge

        code_id = _ask_text("Anchor code", "Anchor id:", "bench-1")
        if not code_id:
            return
        size = _ask_text("Anchor code", "Printed size in mm:", "80")
        try:
            size_mm = float(size or 80)
        except ValueError:
            raise service.XRServiceError("the size must be a number of millimetres")
        target = _ask_text("Anchor code", "Snap what? 'model', 'part:<Name>' or 'env':", "model") or "model"
        qr_bridge.make_code(code_id, size_mm, origin=target)


@register
class XRPeers(XRCommand):
    name = "XR_Peers"
    icon = "XR_Peers.svg"
    menu_text = "Who is here"
    tool_tip = "List the headsets sharing this model through the sync server"

    def run(self):
        from xrcore import presence_bridge

        server = service.sync_server()
        if server is None:
            raise service.XRServiceError("The sync server is not running (Virtual Reality → Sync server).")
        peers = presence_bridge.peers()
        if not peers:
            FreeCAD.Console.PrintMessage("XR: nobody else is connected\n")
        for peer in peers:
            FreeCAD.Console.PrintMessage(f"XR: {peer.name} ({peer.device}) in {peer.environment or '?'}, "
                                         f"selection {peer.selection}\n")
        for lock in server.locks.locks():
            FreeCAD.Console.PrintMessage(f"XR: {lock.object} held by {lock.holder}\n")



NEW_COMMANDS = [
    "XR_AssemblyMode",
    "XR_AssemblyCommit",
    "XR_FitCheck",
    "XR_VoiceToggle",
    "XR_VoiceSay",
    "XR_ImportUrl",
    "XR_ImportArchive",
    "XR_CamLoad",
    "XR_CamPlay",
    "XR_DrawTable",
    "XR_DrawDimension",
    "XR_ScanImport",
    "XR_ScanAlign",
    "XR_ScanCommit",
    "XR_HapticsToggle",
    "XR_QrMakeCode",
    "XR_Peers",
]

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
    "XR_SketchSelect",
    "XR_SketchCurve",
    "XR_SketchPen",
    "XR_SketchPrimitive",
    "XR_SketchSubd",
    "XR_SketchMeasure",
    "XR_SketchSurface",
    "XR_SketchReference",
    "XR_SketchCommit",
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
] + NEW_COMMANDS
