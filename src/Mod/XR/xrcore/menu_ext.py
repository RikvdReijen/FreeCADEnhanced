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
}

_STATUS_NAME = "xr_ext_status_label"


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
    return f"{state.get('environment', '?')}  |  1:{scale:.2f}  |  {who}"


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

    from xrcore import environment_bridge, paint_bridge, service

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
        elif widget_name == "xr_commit_vector_button":
            created = paint_bridge.commit_vector_document()
            FreeCAD.Console.PrintMessage(f"XR: committed {created} vector object(s)\n")
    except service.XRServiceError as exc:
        FreeCAD.Console.PrintWarning(f"XR: {exc}\n")
    except Exception as exc:
        FreeCAD.Console.PrintError(f"XR: menu action '{widget_name}' failed: {exc}\n")

    if menu is not None:
        refresh_status(menu)
    return True
