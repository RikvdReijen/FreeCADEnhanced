# SPDX-License-Identifier: LGPL-2.1-or-later
"""Grasshopper mode: an experimental node-based (dataflow) parametric canvas
for FreeCAD with a mixed-reality front end.

The package is split so that everything that does not need FreeCAD or Qt can
be imported and unit-tested with a plain Python interpreter:

``graph``        dataflow graph model, evaluation, undo, serialisation
``nodes``        the standard node library (parameters, math, curves, solids)
``geometry``     geometry backend abstraction (FreeCAD ``Part`` or a stub)
``layout``       shared node/port geometry used for hit-testing everywhere
``qrcode_gen``   pure-Python QR encoder used for printable canvas markers
``markers``      QR-anchored canvas frame maths and table-contact detection
``protocol``     JSON message protocol shared with the WebXR client
``session``      bridges graph <-> XR clients (intents in, snapshots out)
``xrserver``     stdlib HTTP + WebSocket server hosting the WebXR client

``canvas_qt``, ``document`` and ``commands`` need FreeCAD/Qt and are only
imported from ``InitGui.py``.
"""

__version__ = "0.1.0-experimental"
