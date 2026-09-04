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
"""Extra pages on the in-VR wrist menu.

The desktop dialogs are unreachable with a headset on, so the environment
switcher, the scale controls and the painting modes get their own columns on
the existing Coin3D menu.  The layout is deliberately kept to the left of the
upstream columns so the original menu is untouched.

``add_extension_widgets`` appends the widgets to a live menu; ``handle``
processes a button press and returns True when it recognised the name, which is
how :meth:`xrcore.commonXR.XRwidget.process_menu_selection` dispatches to us
before falling through to the upstream handling.
"""

import FreeCAD

__all__ = ["add_extension_widgets", "handle", "refresh_status", "BUTTONS"]

# name -> (label, column x, row y, radio group, width)
BUTTONS = {
    "xr_env_next_button": ("Environment >", -0.30, 0.25, 0, 0.22),
    "xr_env_prev_button": ("< Environment", -0.30, 0.20, 0, 0.22),
    "xr_shrink_button": ("Shrink Me", -0.30, 0.15, 0, 0.22),
    "xr_grow_button": ("Grow Me", -0.30, 0.10, 0, 0.22),
    "xr_scale_reset_button": ("Default Scale", -0.30, 0.05, 0, 0.22),
    "xr_paint_texture_button": ("Paint Texture", -0.55, 0.25, 3, 0.22),
    "xr_paint_stroke_button": ("3D Strokes", -0.55, 0.20, 3, 0.22),
    "xr_paint_vector_button": ("Vector Mode", -0.55, 0.15, 3, 0.22),
    "xr_paint_off_button": ("Modelling", -0.55, 0.10, 3, 0.22),
    "xr_commit_vector_button": ("Commit Vector", -0.55, 0.05, 0, 0.22),
    "xr_mrc_toggle_button": ("Capture", -0.80, 0.25, 0, 0.22),
    "xr_mrc_cycle_button": ("MRC Mode", -0.80, 0.20, 0, 0.22),
    "xr_sculpt_button": ("Sculpt", -0.80, 0.15, 3, 0.22),
    "xr_sculpt_mask_button": ("Mask", -0.80, 0.10, 3, 0.22),
    "xr_sculpt_undo_button": ("Sculpt Undo", -0.80, 0.05, 0, 0.22),
    "xr_sketch_select_button": ("Select/Grab", -1.05, 0.25, 3, 0.22),
    "xr_sketch_curve_button": ("Curve", -1.05, 0.20, 3, 0.22),
    "xr_sketch_pen_button": ("Pen", -1.05, 0.15, 3, 0.22),
    "xr_sketch_primitive_button": ("Primitive", -1.05, 0.10, 3, 0.22),
    "xr_sketch_subd_button": ("Subd Cage", -1.05, 0.05, 3, 0.22),
    "xr_sketch_measure_button": ("Measure", -1.05, 0.00, 3, 0.22),
    "xr_sketch_undo_button": ("Sketch Undo", -1.30, 0.25, 0, 0.22),
    "xr_sketch_redo_button": ("Sketch Redo", -1.30, 0.20, 0, 0.22),
    "xr_sketch_commit_button": ("Commit Sketch", -1.30, 0.15, 0, 0.22),
    # xr-v0.2: assembly, fit check, drafting table, scan, CAM, voice
    "xr_assembly_button": ("Assemble", -1.55, 0.25, 3, 0.22),
    "xr_fit_button": ("Fit Check", -1.55, 0.20, 3, 0.22),
    "xr_draw_button": ("Draft Table", -1.55, 0.15, 3, 0.22),
    "xr_scan_button": ("Align Scan", -1.55, 0.10, 3, 0.22),
    "xr_mode_confirm_button": ("Confirm", -1.55, 0.05, 0, 0.22),
    "xr_mode_undo_button": ("Undo Mate/Dim", -1.55, 0.00, 0, 0.22),
    "xr_cam_play_button": ("Play/Pause", -1.80, 0.25, 0, 0.22),
    "xr_cam_faster_button": ("Faster", -1.80, 0.20, 0, 0.22),
    "xr_cam_slower_button": ("Slower", -1.80, 0.15, 0, 0.22),
    "xr_voice_button": ("Voice", -1.80, 0.10, 0, 0.22),
    "xr_haptics_button": ("Haptics", -1.80, 0.05, 0, 0.22),
    "xr_scan_align_button": ("Scan: Align", -1.80, 0.00, 0, 0.22),
}

#: The in-VR tool buttons that must release the other modes when pressed.
_SKETCH_TOOLS = {
    "xr_sketch_select_button": "SELECT",
    "xr_sketch_curve_button": "CURVE",
    "xr_sketch_pen_button": "PEN",
    "xr_sketch_primitive_button": "PRIMITIVE",
    "xr_sketch_subd_button": "SUBD",
    "xr_sketch_measure_button": "MEASURE",
}

_STATUS_NAME = "xr_ext_status_label"

#: xr-v0.2 exclusive modes reachable from the wrist menu
_MODE_BUTTONS = {
    "xr_assembly_button": "assembly_bridge",
    "xr_fit_button": "fit_bridge",
    "xr_draw_button": "draw_bridge",
    "xr_scan_button": "scan_bridge",
}
_MODE_BRIDGES = ("assembly_bridge", "fit_bridge", "draw_bridge", "scan_bridge")


def _switch_mode(bridge_name):
    """Activate one xr-v0.2 mode, releasing every other mode first."""
    from xrcore import paint_bridge, sculpt_bridge, sketch_bridge

    paint_bridge.deactivate()
    sculpt_bridge.deactivate()
    sketch_bridge.deactivate()
    for other in _MODE_BRIDGES:
        if other != bridge_name:
            try:
                __import__("xrcore." + other, fromlist=["deactivate"]).deactivate()
            except Exception:
                pass
    bridge = __import__("xrcore." + bridge_name, fromlist=["activate"])
    if bridge_name in ("assembly_bridge", "fit_bridge"):
        bridge.ensure_session()
        bridge.reload()
    bridge.activate()


def _confirm_current():
    """The Confirm button: the mate preview, the drafting-table picks, or the sketch."""
    from xrcore import assembly_bridge, draw_bridge, sketch_bridge

    if assembly_bridge.active():
        assembly_bridge.confirm()
    elif draw_bridge.active():
        draw_bridge.place_dimension()
    else:
        created = sketch_bridge.commit_sketch()
        FreeCAD.Console.PrintMessage(f"XR: committed {len(created)} sketch object(s)\n")


def _undo_current():
    from xrcore import assembly_bridge, draw_bridge, scan_bridge, sketch_bridge

    if assembly_bridge.active():
        session = assembly_bridge.get_session()
        if session is not None:
            session.unconstrain()
    elif draw_bridge.active():
        draw_bridge.undo()
    elif scan_bridge.active():
        scan_bridge.undo()
    else:
        sketch_bridge.undo()


def add_extension_widgets(menu):
    """Append the environment and painting widgets to ``menu`` in place."""
    from pivy.coin import SbRotation, SbVec3f

    from xrcore import menuCoin

    for name, (label, x, y, group, width) in BUTTONS.items():
        button = menuCoin.buttonWidget(name, label, group, width)
        button.set_location(SbVec3f(x, y, -0.3), SbRotation(0, 0, 0, 0))
        menu.widget_list.append(button)
        menu.menu_node.addChild(button.get_scenegraph())

    status = menuCoin.labelWidget(_STATUS_NAME, _status_text(), 0.5)
    status.set_location(SbVec3f(-0.55, 0.30, -0.3), SbRotation(0, 0, 0, 0))
    menu.widget_list.append(status)
    menu.menu_node.addChild(status.get_scenegraph())

    # Reflect the mode the session is already in.
    menu.select_widget_by_name("xr_paint_off_button")
    return menu


def _status_text():
    from xrcore import environment_bridge

    try:
        state = environment_bridge.current_state()
    except Exception:
        return "XR"
    scale = state.get("scale", 1.0)
    if scale >= 1.5:
        height = 1700.0 / scale
        who = f"you: {height:.0f} mm"
    elif scale <= 0.75:
        who = f"you: {1.7 / scale:.1f} m"
    else:
        who = "you: life size"
    capture = ""
    try:
        from xrcore import service

        session = service.get_mrc_session()
        if session is not None and session.active:
            capture = f"  |  REC {session.mode}"
    except Exception:
        pass
    extra = ""
    for bridge_name in ("fit_bridge", "draw_bridge", "scan_bridge", "cam_bridge", "voice_bridge", "presence_bridge"):
        try:
            text = __import__("xrcore." + bridge_name, fromlist=["status_text"]).status_text()
        except Exception:
            text = ""
        if text:
            extra += "  |  " + text
    return f"{state.get('environment', '?')}  |  1:{scale:.2f}  |  {who}{capture}{extra}"


def refresh_status(menu):
    """Update the status line after a scale or environment change."""
    for widget in getattr(menu, "widget_list", []):
        if getattr(widget, "name", None) == _STATUS_NAME:
            try:
                widget.set_text(_status_text())
            except Exception:
                pass
            return True
    return False


def handle(widget_name, menu=None):
    """Act on a menu press. Returns True when the name belongs to us."""
    if widget_name not in BUTTONS:
        return False

    from xrcore import (
        environment_bridge,
        paint_bridge,
        sculpt_bridge,
        service,
        sketch_bridge,
    )

    try:
        if widget_name == "xr_env_next_button":
            environment_bridge.cycle_environment(1)
        elif widget_name == "xr_env_prev_button":
            environment_bridge.cycle_environment(-1)
        elif widget_name == "xr_shrink_button":
            environment_bridge.nudge_scale(1.25)
        elif widget_name == "xr_grow_button":
            environment_bridge.nudge_scale(1.0 / 1.25)
        elif widget_name == "xr_scale_reset_button":
            environment_bridge.reset_scale()
        elif widget_name == "xr_paint_texture_button":
            paint_bridge.activate_mode("TEXTURE")
        elif widget_name == "xr_paint_stroke_button":
            paint_bridge.activate_mode("STROKE3D")
        elif widget_name == "xr_paint_vector_button":
            paint_bridge.activate_mode("VECTOR")
        elif widget_name == "xr_paint_off_button":
            paint_bridge.deactivate()
            sculpt_bridge.deactivate()
            sketch_bridge.deactivate()
        elif widget_name == "xr_commit_vector_button":
            created = paint_bridge.commit_vector_document()
            FreeCAD.Console.PrintMessage(f"XR: committed {created} vector object(s)\n")
        elif widget_name == "xr_mrc_toggle_button":
            service.require_mrc_session().toggle()
        elif widget_name == "xr_mrc_cycle_button":
            service.require_mrc_session().cycle()
        elif widget_name == "xr_sculpt_button":
            paint_bridge.deactivate()
            sketch_bridge.deactivate()
            sculpt_bridge.activate_mode("SCULPT")
        elif widget_name == "xr_sculpt_mask_button":
            paint_bridge.deactivate()
            sketch_bridge.deactivate()
            sculpt_bridge.activate_mode("MASK")
        elif widget_name in _SKETCH_TOOLS:
            paint_bridge.deactivate()
            sculpt_bridge.deactivate()
            sketch_bridge.activate_tool(_SKETCH_TOOLS[widget_name])
        elif widget_name == "xr_sketch_undo_button":
            sketch_bridge.undo()
        elif widget_name == "xr_sketch_redo_button":
            sketch_bridge.redo()
        elif widget_name == "xr_sketch_commit_button":
            created = sketch_bridge.commit_sketch()
            FreeCAD.Console.PrintMessage(
                f"XR: committed {len(created)} sketch object(s)\n"
            )
        elif widget_name == "xr_sculpt_undo_button":
            session = service.get_sculpt_session()
            if session is None:
                raise service.XRServiceError("Nothing has been sculpted yet.")
            session.undo()
        elif widget_name in _MODE_BUTTONS:
            _switch_mode(_MODE_BUTTONS[widget_name])
        elif widget_name == "xr_mode_confirm_button":
            _confirm_current()
        elif widget_name == "xr_mode_undo_button":
            _undo_current()
        elif widget_name == "xr_cam_play_button":
            from xrcore import cam_bridge

            cam_bridge.toggle()
        elif widget_name == "xr_cam_faster_button":
            from xrcore import cam_bridge

            cam_bridge.set_speed(cam_bridge.get_session().player.speed * 2.0 if cam_bridge.get_session() else 1.0)
        elif widget_name == "xr_cam_slower_button":
            from xrcore import cam_bridge

            cam_bridge.set_speed(cam_bridge.get_session().player.speed * 0.5 if cam_bridge.get_session() else 1.0)
        elif widget_name == "xr_voice_button":
            from xrcore import voice_bridge

            voice_bridge.toggle()
        elif widget_name == "xr_haptics_button":
            from xrcore import haptics_bridge

            haptics_bridge.set_enabled(not haptics_bridge.engine().enabled)
        elif widget_name == "xr_scan_align_button":
            from xrcore import scan_bridge

            session = scan_bridge.get_session()
            if session is None:
                raise service.XRServiceError("Import a scan first.")
            if len(session.complete_pairs()) >= 3:
                scan_bridge.align()
            elif session.model is not None:
                scan_bridge.refine()
    except service.XRServiceError as exc:
        FreeCAD.Console.PrintWarning(f"XR: {exc}\n")
    except Exception as exc:
        FreeCAD.Console.PrintError(f"XR: menu action '{widget_name}' failed: {exc}\n")

    if menu is not None:
        refresh_status(menu)
    return True
