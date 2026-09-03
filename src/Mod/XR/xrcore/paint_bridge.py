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
"""Glue between the painting/vector module (:mod:`xrpaint`) and the XR viewer.

The paint session is a plain Python state machine; this module owns its
lifetime, feeds it controller events from the render loop, and exposes the few
operations the desktop commands need (commit to the document, export SVG).
"""

import FreeCAD

from xrcore import service

__all__ = [
    "get_session",
    "ensure_session",
    "activate_mode",
    "deactivate",
    "attach",
    "detach",
    "handle_frame",
    "commit_vector_document",
    "export_svg",
    "paint_manifest",
    "apply_remote_paint",
    "apply_remote_vector",
]

MODES = ("TEXTURE", "STROKE3D", "VECTOR")


def get_session():
    return service.get_paint_session()


def ensure_session():
    """Return the paint session, creating it on first use."""
    session = service.get_paint_session()
    if session is not None:
        return session

    from xrpaint.session import PaintSession

    session = PaintSession()
    service.set_paint_session(session)
    return session


def attach(widget, paint_root):
    """Called by the viewer once the scenegraph exists."""
    session = ensure_session()
    session.attach_scenegraph(paint_root)
    session.bind_viewer(widget)
    return session


def detach():
    session = service.get_paint_session()
    if session is not None:
        session.detach()


def activate_mode(mode):
    if mode not in MODES:
        raise service.XRServiceError(f"Unknown painting mode '{mode}'")
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
    session = service.get_paint_session()
    if session is not None:
        session.set_mode(None)


def handle_frame(dt, controllers):
    """Per-frame hook driven by the XR render loop."""
    session = service.get_paint_session()
    if session is None or session.mode is None:
        return False
    return session.update(dt, controllers)


# --------------------------------------------------------------------------
# document round trip
# --------------------------------------------------------------------------


def commit_vector_document(document=None):
    """Turn the VR vector drawing into Draft/Part geometry. Returns a count."""
    session = service.get_paint_session()
    if session is None:
        raise service.XRServiceError("Nothing has been drawn in vector mode yet.")
    vector_doc = session.vector_document
    if vector_doc is None or not vector_doc.paths:
        raise service.XRServiceError("The vector drawing is empty.")

    document = document or FreeCAD.ActiveDocument
    if document is None:
        raise service.XRServiceError("Open a document first.")

    from xrpaint import to_freecad

    document.openTransaction("XR vector commit")
    try:
        objects = to_freecad.commit(vector_doc, document)
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    document.recompute()
    return len(objects)


def export_svg(path):
    session = service.get_paint_session()
    if session is None or session.vector_document is None:
        raise service.XRServiceError("The vector drawing is empty.")

    from xrpaint import svg

    text = svg.export_document(session.vector_document)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def paint_manifest():
    """The ``paint`` section of an FCXR manifest for the current session."""
    session = service.get_paint_session()
    if session is None:
        return None
    return session.export_paint_manifest()


# --------------------------------------------------------------------------
# incoming edits from the headset
# --------------------------------------------------------------------------


def apply_remote_paint(manifest, images):
    """Apply a paint document received from the Quest application."""
    session = ensure_session()
    session.import_paint_manifest(manifest, images)
    FreeCAD.Console.PrintMessage("XR: applied painting received from headset\n")
    return True


def apply_remote_vector(vector_doc_json, document=None):
    """Apply a vector document received from the Quest application."""
    from xrpaint import vector

    session = ensure_session()
    session.vector_document = vector.VectorDocument.from_json(vector_doc_json)
    if service.preferences().GetBool("VectorAutoCommit", True):
        return commit_vector_document(document)
    return 0
