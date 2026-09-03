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
"""What the GUI, the macro API and the command line all actually call.

Everything above this module is either FreeCAD talking (commands, the workbench)
or an engine talking (the clients); everything below it is the bridge proper.
Putting the operations here means a macro gets exactly the same behaviour as the
toolbar button, and the command-line exporter gets it without a GUI at all::

    from gbcore import service
    service.export_active_document("unreal", "/tmp/bracket")
    service.start_link()

The document observer deserves a note.  FreeCAD emits a great many signals
during a recompute - one per object, sometimes several - and publishing on each
would send the same scene twenty times.  The observer therefore publishes on the
*end* of a recompute and no more often than :data:`MIN_PUBLISH_INTERVAL`, so a
model being dragged updates smoothly rather than flooding the link.
"""

import os
import threading
import time

try:
    import queue
except ImportError:  # pragma: no cover - FreeCAD is Python 3 only
    import Queue as queue

from . import preferences as preferences_module
from .document import DocumentWalker
from .transform import get_convention

__all__ = [
    "MIN_PUBLISH_INTERVAL",
    "PENDING_SELECTIONS",
    "active_document",
    "build_scene",
    "export_document",
    "export_active_document",
    "link_server",
    "start_link",
    "stop_link",
    "publish",
    "link_status",
]

#: Seconds between published updates, however fast the document changes.
MIN_PUBLISH_INTERVAL = 0.2

#: How many selections from engines may be waiting to be applied.
PENDING_SELECTIONS = 32

_server = None
_observer = None
_last_publish = 0.0
_ticker = None
#: Set by the observer, cleared by the tick that publishes.  A flag rather than
#: an immediate publish so that a burst of signals costs one scene, and - the
#: part a leading-edge throttle gets wrong - so the *last* state of a burst is
#: still sent once the burst stops.
_dirty = threading.Event()
#: Selections arriving from an engine, to be applied on the main thread.
_selection_requests = queue.Queue(maxsize=PENDING_SELECTIONS)


def _freecad():
    """FreeCAD, imported late so this module loads without it."""
    import FreeCAD

    return FreeCAD


def active_document():
    """The document to work on, or ``None`` when there is not one."""
    try:
        return _freecad().ActiveDocument
    except (ImportError, AttributeError):
        return None


def _console(message, level="log"):
    try:
        console = _freecad().Console
    except (ImportError, AttributeError):
        print("GameBridge: %s" % message)
        return
    text = "GameBridge: %s\n" % message
    if level == "error":
        console.PrintError(text)
    elif level == "warning":
        console.PrintWarning(text)
    else:
        console.PrintMessage(text)


# ---------------------------------------------------------------------------
# Exporting
# ---------------------------------------------------------------------------


def build_scene(document=None, objects=None, settings=None, include_hidden=None):
    """Tessellate a document into a scene, reporting anything that went wrong."""
    prefs = preferences_module.preferences
    document = document if document is not None else active_document()
    if document is None and objects is None:
        raise RuntimeError("there is no active document to export")
    settings = settings or prefs.tessellation_settings()
    if include_hidden is None:
        include_hidden = prefs.get("IncludeHidden")

    walker = DocumentWalker(settings, include_hidden)
    if objects:
        name = getattr(document, "Label", None) or "Selection"
        scene = walker.walk_objects(objects, name, getattr(document, "Name", None))
    else:
        scene = walker.walk_document(document)
    scene.metadata["tessellation"] = settings.to_dict()
    scene.metadata["warnings"] = walker.warnings
    for warning in walker.warnings:
        _console(warning, "warning")
    return scene


def export_document(target, directory, document=None, objects=None, options=None):
    """Export a document for one engine.  Returns the target's result object."""
    from gbtargets import get_target

    prefs = preferences_module.preferences
    options = options or prefs.export_options()
    scene = build_scene(document, objects)
    if not scene.meshes:
        _console(
            "nothing was exported: the document has no visible geometry", "warning"
        )
    result = get_target(target, options).export(scene, directory)
    for warning in result.warnings:
        _console(warning, "warning")
    _console(result.summary())
    return result


def export_active_document(target=None, directory=None, objects=None):
    """Export using the preferences for anything the caller left out."""
    prefs = preferences_module.preferences
    target = target or prefs.get("Target")
    directory = directory or prefs.get("ExportDirectory") or os.getcwd()
    return export_document(target, directory, objects=objects)


# ---------------------------------------------------------------------------
# The live link
# ---------------------------------------------------------------------------


def link_server():
    """The running link server, or ``None``."""
    return _server


def start_link(port=None, token=None, convention=None, auto_publish=None, document=None):
    """Start the live link and publish the current document at it."""
    global _server
    from gblink import LinkServer

    if _server is not None and _server.running:
        _console("the link is already running on port %d" % _server.port, "warning")
        return _server

    settings = preferences_module.preferences.link_settings()
    port = settings["port"] if port is None else port
    token = settings["token"] if token is None else token
    convention = settings["convention"] if convention is None else convention
    if auto_publish is None:
        auto_publish = settings["auto_publish"]

    _server = LinkServer(
        port=port,
        token=token,
        convention=get_convention(convention),
        name="FreeCAD",
        logger=lambda message: _console(message),
    )
    _server.selection_callback = _select_in_freecad
    _server.start()
    document = document if document is not None else active_document()
    if document is not None:
        publish(document)
    if auto_publish:
        _install_observer()
        _start_ticker()
    return _server


def stop_link():
    """Stop the link and detach the observer."""
    global _server
    _remove_observer()
    _stop_ticker()
    _dirty.clear()
    if _server is not None:
        _server.stop()
        _server = None
    return None


def publish(document=None, force=False):
    """Push the document to every connected engine.

    Throttled: FreeCAD can emit a recompute signal far faster than an engine can
    apply one, and the newest scene is the only one anybody wants.  A throttled
    call marks the document dirty instead of dropping it, so the tick that
    follows publishes the state the user actually stopped at.
    """
    global _last_publish
    if _server is None or not _server.running:
        return None
    now = time.time()
    if not force and (now - _last_publish) < MIN_PUBLISH_INTERVAL:
        _dirty.set()
        return None
    _dirty.clear()
    document = document if document is not None else active_document()
    if document is None:
        return None
    try:
        scene = build_scene(document)
    except Exception as problem:
        _console("could not build the scene to publish: %s" % problem, "error")
        return None
    _last_publish = now
    name = getattr(document, "Label", None) or getattr(document, "Name", None)
    _server.publish(scene, name)
    return scene


def link_status():
    """A dictionary describing the link, for the GUI and for macros."""
    if _server is None:
        return {"running": False, "clients": []}
    return _server.describe()


def _select_in_freecad(connection, names):
    """Queue a selection that arrived from an engine.

    This runs on the link's reader thread, and FreeCAD's Gui API is not thread
    safe: touching Selection from here is a crash waiting for a busy moment.
    The names are queued and applied by :func:`_tick` on the main thread.
    """
    try:
        _selection_requests.put_nowait(list(names))
    except queue.Full:
        # An engine spamming selections must not grow a queue without limit;
        # the newest selection is the only one that matters anyway.
        try:
            _selection_requests.get_nowait()
            _selection_requests.put_nowait(list(names))
        except (queue.Empty, queue.Full):
            pass


def _apply_selection(names):
    """Apply a queued selection.  Main thread only."""
    try:
        import FreeCADGui
    except ImportError:
        return
    document = active_document()
    if document is None:
        return
    FreeCADGui.Selection.clearSelection()
    for name in names:
        obj = document.getObject(name)
        if obj is not None:
            FreeCADGui.Selection.addSelection(obj)


def _tick():
    """Periodic main-thread work: apply selections, publish pending changes.

    Everything that touches FreeCAD - building a scene, changing the selection -
    happens here rather than on a network thread or a plain Python timer, both
    of which would be calling into FreeCAD from the wrong thread.
    """
    names = None
    while True:
        try:
            names = _selection_requests.get_nowait()
        except queue.Empty:
            break
    if names is not None:
        _apply_selection(names)
    if _dirty.is_set():
        publish()


def _start_ticker():
    """Run :func:`_tick` on the GUI's event loop, if there is one.

    Without a GUI - freecadcmd driving a headless session - there is no event
    loop to hang a timer on, so publishing stays on the leading edge of each
    change and selection sync is inert.  Both are fine for a script; neither is
    worth a background thread poking at FreeCAD from outside the main one.
    """
    global _ticker
    if _ticker is not None:
        return _ticker
    try:
        from PySide import QtCore
    except ImportError:
        return None
    timer = QtCore.QTimer()
    timer.setInterval(int(MIN_PUBLISH_INTERVAL * 1000))
    timer.timeout.connect(_tick)
    timer.start()
    _ticker = timer
    return timer


def _stop_ticker():
    global _ticker
    if _ticker is not None:
        try:
            _ticker.stop()
        except Exception:
            pass
        _ticker = None


# ---------------------------------------------------------------------------
# The document observer
# ---------------------------------------------------------------------------


class _Observer:
    """Publishes when a document finishes changing.

    Only the signals that mean "the model is different now" are acted on.
    Property changes during a recompute are ignored, because the recompute's own
    completion signal follows and says the same thing once.
    """

    def slotRecomputedDocument(self, document):
        publish(document)

    def slotDeletedObject(self, obj):
        publish(getattr(obj, "Document", None))

    def slotDeletedDocument(self, document):
        publish(active_document())

    def slotActivateDocument(self, document):
        publish(document, force=True)

    def slotChangedObject(self, obj, prop):
        # Visibility lives on the view provider and never triggers a recompute,
        # so hiding a part would otherwise not reach the engine.
        if prop in ("Visibility", "Placement"):
            publish(getattr(obj, "Document", None))


def _install_observer():
    global _observer
    if _observer is not None:
        return _observer
    try:
        FreeCAD = _freecad()
    except ImportError:
        return None
    _observer = _Observer()
    FreeCAD.addDocumentObserver(_observer)
    return _observer


def _remove_observer():
    global _observer
    if _observer is None:
        return
    try:
        _freecad().removeDocumentObserver(_observer)
    except (ImportError, AttributeError, RuntimeError):
        pass
    _observer = None
