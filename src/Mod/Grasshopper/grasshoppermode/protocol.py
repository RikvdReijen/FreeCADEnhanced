# SPDX-License-Identifier: LGPL-2.1-or-later
"""JSON message protocol shared by the server and the WebXR client.

Every message is a JSON object with a ``t`` (type) field.

Server -> client
----------------
``hello``      server info, canvas id, marker spec, interaction profiles
``graph``      full snapshot (nodes with rectangles/ports, wires, selection)
``patch``      incremental change (node moved, value changed, ...)
``preview``    tessellated geometry for preview nodes
``selection``  new selection list
``ack``        reply to a request carrying ``rid``; has ``ok`` and ``error``
``clients``    number of connected clients

Client -> server
----------------
``hello``      capabilities (hands, stylus, image tracking, ...)
``tap``        table tap at canvas (x, y) with a selection mode
``select``     explicit selection change
``move``       drag a node: phase begin/update/end
``wire``       connect an output port to an input port
``disconnect`` remove a wire
``set_value``  set a literal/widget value
``set_param``  set a node parameter (slider range, expression, ...)
``add_node``   create a node at canvas (x, y)
``delete``     delete nodes/wires
``duplicate``  duplicate nodes
``undo``/``redo``
``anchor``     client reports/stores where the canvas is anchored
``pose``       optional raw stylus/hand poses (server-side detection)
``log``        diagnostics from the headset
"""

import json

PROTOCOL_VERSION = 1

# Interaction concepts the client can switch between.  They are the "A/B/C"
# answers to the open design questions; the JSON is delivered to the client
# in ``hello`` so both sides agree on gesture meaning.
INTERACTION_PROFILES = {
    "table-stylus": {
        "label": "A: Stylus on the table (MX Ink first)",
        "summary": "The canvas is a sheet on the table. Tap with the stylus tip to select, press and drag to move nodes or draw wires, flat hand slides the sheet.",
        "select": "stylus-tap",
        "drag_node": "stylus-press-drag",
        "wire": "stylus-drag-from-port",
        "pan": "flat-hand-slide",
        "zoom": "two-flat-hands",
        "add_node": "stylus-hold-empty",
        "delete": "stylus-rear-button",
        "slider": "stylus-press-drag-track",
        "context": "stylus-front-button",
    },
    "table-hands": {
        "label": "B: Bare hands on the table",
        "summary": "Index fingertip taps the table to select, pinch to grab nodes and wires, flat hand slides the sheet, two flat hands zoom.",
        "select": "index-tap",
        "drag_node": "pinch-drag",
        "wire": "pinch-drag-from-port",
        "pan": "flat-hand-slide",
        "zoom": "two-flat-hands",
        "add_node": "index-hold-empty",
        "delete": "pinch-throw-off-sheet",
        "slider": "pinch-drag-knob",
        "context": "double-tap",
    },
    "floating-panel": {
        "label": "C: Floating panel (Embodreal style)",
        "summary": "The canvas hovers vertically in front of you. Point-and-pinch or stylus ray to select and drag, grab the frame to reposition, flat hand pushes the panel.",
        "select": "ray-pinch",
        "drag_node": "ray-pinch-drag",
        "wire": "ray-pinch-from-port",
        "pan": "grab-frame",
        "zoom": "two-hand-frame-stretch",
        "add_node": "ray-pinch-hold-empty",
        "delete": "stylus-rear-button",
        "slider": "ray-pinch-drag-knob",
        "context": "stylus-front-button",
    },
}

DEFAULT_PROFILE = "table-stylus"


def message(t, **fields):
    fields["t"] = t
    return fields


def encode(msg):
    return json.dumps(msg, separators=(",", ":"), default=_default)


def decode(text):
    msg = json.loads(text)
    if not isinstance(msg, dict) or "t" not in msg:
        raise ValueError("message must be an object with a 't' field")
    return msg


def _default(obj):
    if hasattr(obj, "to_json"):
        return obj.to_json()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError("cannot serialise %r" % (obj,))


def ack(rid, ok=True, error=None, **extra):
    msg = message("ack", rid=rid, ok=bool(ok))
    if error:
        msg["error"] = str(error)
    msg.update(extra)
    return msg
