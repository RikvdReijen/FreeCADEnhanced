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
"""Glue between the sculpting module (:mod:`xrsculpt`) and the XR viewer.

Shaped deliberately like :mod:`xrcore.paint_bridge`: the session is a plain
Python state machine, this module owns its lifetime, feeds it controller events
from the render loop, and exposes the few operations the desktop commands need.

Sculpt layers are stored on the FreeCAD object itself, in a hidden string
property, so a sculpt survives saving and reopening the document without an
extra sidecar file to lose.
"""

import FreeCAD

from xrcore import service

__all__ = [
    "MODES",
    "LAYER_PROPERTY",
    "get_session",
    "ensure_session",
    "attach",
    "detach",
    "activate_mode",
    "deactivate",
    "handle_frame",
    "add_target",
    "commit_to_document",
    "store_on_object",
    "load_from_object",
    "sculpt_manifest",
    "apply_remote_sculpt",
]

MODES = ("SCULPT", "MASK")

#: Hidden string property carrying the base64 sculpt layer stack.
LAYER_PROPERTY = "XRSculptLayers"


def get_session():
    return service.get_sculpt_session()


def ensure_session():
    """Return the sculpt session, creating it on first use."""
    session = service.get_sculpt_session()
    if session is not None:
        return session

    from xrsculpt.session import SculptSession

    session = SculptSession()
    service.set_sculpt_session(session)
    return session


def attach(widget, sculpt_root):
    """Called by the viewer once the scenegraph exists."""
    session = ensure_session()
    session.attach_scenegraph(sculpt_root)
    session.bind_viewer(widget)
    return session


def detach():
    session = service.get_sculpt_session()
    if session is not None:
        session.detach()


def activate_mode(mode):
    if mode not in MODES:
        raise service.XRServiceError(f"Unknown sculpting mode '{mode}'")
    session = ensure_session()
    session.set_mode(mode)
    if service.get_widget() is None:
        FreeCAD.Console.PrintMessage(
            f"XR: {mode.lower()} mode armed — it becomes active when you open the XR viewer\n"
        )
    else:
        FreeCAD.Console.PrintMessage(f"XR: {mode.lower()} mode active\n")
    return session


def deactivate():
    session = service.get_sculpt_session()
    if session is not None:
        session.set_mode(None)


def handle_frame(dt, controllers):
    """Per-frame hook driven by the XR render loop."""
    session = service.get_sculpt_session()
    if session is None or session.mode is None:
        return False
    return session.update(dt, controllers)


# --------------------------------------------------------------------------
# document round trip
# --------------------------------------------------------------------------


def add_target(obj=None):
    """Make a document object sculptable, tessellating it if need be."""
    session = ensure_session()
    if obj is None:
        import FreeCADGui as Gui

        selection = Gui.Selection.getSelection()
        if not selection:
            raise service.XRServiceError("Select the object you want to sculpt first.")
        obj = selection[0]
    target = session.add_target_object(obj)
    load_from_object(obj)
    FreeCAD.Console.PrintMessage(f"XR: sculpting {obj.Label}\n")
    return target


def commit_to_document(document=None):
    """Write the evaluated sculpt back onto the document object."""
    session = service.get_sculpt_session()
    if session is None:
        raise service.XRServiceError("Nothing has been sculpted yet.")
    target = session.active_target()
    if target is None:
        raise service.XRServiceError("No object is being sculpted.")

    document = document or FreeCAD.ActiveDocument
    if document is None:
        raise service.XRServiceError("Open a document first.")
    obj = document.getObject(target.fc_name)
    if obj is None:
        raise service.XRServiceError(
            f"'{target.fc_name}' is no longer in the document."
        )

    document.openTransaction("XR sculpt")
    try:
        target.mesh.write_back(obj)
        store_on_object(obj)
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    document.recompute()
    return obj


def store_on_object(obj, session=None):
    """Persist the layer stack in a hidden property on the object."""
    session = session or service.get_sculpt_session()
    if session is None:
        return False
    try:
        payload = session.export_bytes()
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"XR: sculpt layers not stored: {exc}\n")
        return False
    if not payload:
        return False

    import base64

    if not hasattr(obj, LAYER_PROPERTY):
        obj.addProperty(
            "App::PropertyString",
            LAYER_PROPERTY,
            "XR",
            "Sculpt layers created in the VR workbench",
        )
        obj.setEditorMode(LAYER_PROPERTY, 2)  # hidden and read-only in the editor
    setattr(obj, LAYER_PROPERTY, base64.b64encode(payload).decode("ascii"))
    return True


def load_from_object(obj, session=None):
    """Restore a layer stack previously stored on the object."""
    session = session or ensure_session()
    text = getattr(obj, LAYER_PROPERTY, "")
    if not text:
        return False

    import base64

    try:
        session.import_bytes(base64.b64decode(text))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"XR: could not restore the sculpt layers on {obj.Label}: {exc}\n"
        )
        return False
    FreeCAD.Console.PrintMessage(f"XR: restored sculpt layers on {obj.Label}\n")
    return True


# --------------------------------------------------------------------------
# headset round trip
# --------------------------------------------------------------------------


def sculpt_manifest(writer):
    """Add the ``sculpt`` section to an FCXR package being written."""
    session = service.get_sculpt_session()
    if session is None:
        return None
    return session.export_sculpt_manifest(writer)


def apply_remote_sculpt(section, document=None):
    """Apply a sculpt received from the Quest application."""
    session = ensure_session()
    document = document or FreeCAD.ActiveDocument
    session.import_sculpt_manifest(document, section)
    FreeCAD.Console.PrintMessage("XR: applied sculpt received from headset\n")
    return True
