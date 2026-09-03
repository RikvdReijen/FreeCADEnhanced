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
"""Glue between the sketch toolset (:mod:`xrsketch`) and the XR viewer.

Same shape as :mod:`xrcore.paint_bridge` and :mod:`xrcore.sculpt_bridge`.

Two details are specific to this one:

* the controller list must be passed in a **stable hand order** (index 0 left,
  1 right), because the two-handed grab keys its anchors on that index — a list
  that reorders between frames makes a grab jump;
* the session shares the environment switcher's :class:`ScaleController`, so
  that grabbing the world with both hands and the shrink/grow commands move the
  same number rather than fighting over it.
"""

import FreeCAD

from xrcore import service

__all__ = [
    "TOOLS",
    "get_session",
    "ensure_session",
    "attach",
    "detach",
    "activate_tool",
    "deactivate",
    "handle_frame",
    "commit_sketch",
    "undo",
    "redo",
    "current_tool",
]

#: Mirrors ``xrsketch.session.TOOLS``; duplicated so the commands can be
#: registered without importing xrsketch at workbench start-up.
TOOLS = ("SELECT", "CURVE", "PEN", "PRIMITIVE", "SUBD", "MEASURE")


#: The Coin renderer that draws the scene; owned here so ``handle_frame`` can
#: refresh it without the session having to know Coin exists.
_renderer = None


def renderer():
    return _renderer


def get_session():
    return service.get_sketch_session()


def ensure_session():
    """Return the sketch session, creating it on first use."""
    session = service.get_sketch_session()
    if session is not None:
        return session

    from xrsketch.session import SketchSession

    session = SketchSession()
    service.set_sketch_session(session)
    return session


def attach(widget, sketch_root):
    """Called by the viewer once the scenegraph exists."""
    global _renderer

    session = ensure_session()
    session.attach_scenegraph(sketch_root)
    session.bind_viewer(widget)

    from xrcore.sketch_render import SketchRenderer

    _renderer = SketchRenderer()
    _renderer.attach(sketch_root, session)
    try:
        from xrcore import environment_bridge

        controller = environment_bridge.manager().controller
        if controller is not None:
            session.bind_scale(controller)
    except Exception as exc:
        # Without it the world grab still works, it just will not agree with
        # the shrink/grow commands about the current scale.
        FreeCAD.Console.PrintWarning(
            f"XR: sketch tools could not share the scale controller: {exc}\n"
        )
    return session


def detach():
    global _renderer

    if _renderer is not None:
        _renderer.detach()
        _renderer = None
    session = service.get_sketch_session()
    if session is not None:
        session.detach()


def activate_tool(name):
    if name not in TOOLS:
        raise service.XRServiceError(f"Unknown sketch tool '{name}'")
    session = ensure_session()
    session.set_tool(name)
    if service.get_widget() is None:
        FreeCAD.Console.PrintMessage(
            f"XR: sketch {name.lower()} tool armed — it becomes active when you "
            "open the XR viewer\n"
        )
    else:
        FreeCAD.Console.PrintMessage(f"XR: sketch {name.lower()} tool active\n")
    return session


def deactivate():
    session = service.get_sketch_session()
    if session is not None:
        session.cancel_all()


def current_tool():
    session = service.get_sketch_session()
    return session.tool if session is not None else None


def handle_frame(dt, controllers):
    """Per-frame hook driven by the XR render loop.

    ``controllers`` must keep a stable hand order; see the module docstring.
    """
    session = service.get_sketch_session()
    if session is None:
        return False
    consumed = bool(session.update(dt, controllers))
    if _renderer is not None:
        _renderer.update()
    _apply_world_grab(session)
    return consumed


def _apply_world_grab(session):
    """Apply the rigid half of the two-handed world grab.

    ``WorldGrab`` splits into a scale, which the shared ScaleController has
    already absorbed, and a rigid move. The rigid half goes on its own node
    rather than on the environment's scale transform, so the world grab and
    the shrink/grow commands cannot fight over one field.
    """
    grab = getattr(session, "world_grab", None)
    if grab is None:
        return False
    rigid = getattr(grab, "rigid", None)
    if rigid is None:
        return False
    widget = service.get_widget()
    node = getattr(widget, "world_grab_transform", None) if widget is not None else None
    if node is None:
        return False
    try:
        from pivy.coin import SbRotation, SbVec3f

        translation = rigid.translation
        rotation = rigid.rotation
        node.translation.setValue(
            SbVec3f(float(translation[0]), float(translation[1]), float(translation[2]))
        )
        node.rotation.setValue(
            SbRotation(
                float(rotation[0]), float(rotation[1]),
                float(rotation[2]), float(rotation[3]),
            )
        )
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------
# document round trip
# --------------------------------------------------------------------------


def commit_sketch(document=None):
    """Turn the VR sketch into Draft/Part geometry. Returns a count."""
    session = service.get_sketch_session()
    if session is None:
        raise service.XRServiceError("Nothing has been sketched yet.")

    document = document or FreeCAD.ActiveDocument
    if document is None:
        raise service.XRServiceError("Open a document first.")

    document.openTransaction("XR sketch commit")
    try:
        objects = session.commit_to_document(document)
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    document.recompute()
    return len(objects or ())


def undo():
    session = service.get_sketch_session()
    if session is None:
        raise service.XRServiceError("Nothing has been sketched yet.")
    return session.undo()


def redo():
    session = service.get_sketch_session()
    if session is None:
        raise service.XRServiceError("Nothing has been sketched yet.")
    return session.redo()
