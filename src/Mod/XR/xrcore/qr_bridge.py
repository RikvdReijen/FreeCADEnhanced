# SPDX-License-Identifier: LGPL-2.1-or-later
"""Snap the world to a printed QR code.

Detections come from a device with a camera — the headset over
``POST /api/v1/qr`` — and are settled by a :class:`xrqr.QrSession`. A
settled snap moves what the code names: the document (through the world
grab transform, so the model's origin lands on the code), a part (its
``Placement``), or, for ``target=`` codes, reports the offset between the
environment's anchor and the real-world code for the user to apply.
``make_code(id, size)`` writes a printable SVG of a code when the
``qrcode`` package is installed; otherwise it prints the payload text so
any QR generator can be used.
"""

import os

import FreeCAD

from xrcore import docmesh, service
from xrsketch import vecmath as vm

__all__ = ["get_session", "ensure_session", "attach", "detach", "sink", "apply_snap", "make_code", "status_text"]

_last_snap = None


def get_session():
    return service.get_feature("qr")


def ensure_session():
    session = get_session()
    if session is None:
        from xrqr import QrSession

        prefs = service.preferences()
        session = QrSession(settle_count=prefs.GetInt("QrSettleCount", 3), max_residual=prefs.GetFloat("QrMaxResidual", 0.004))
        service.set_feature("qr", session)
    server = service.sync_server()
    if server is not None:
        server.qr_sink = sink
    return session


def attach(widget=None, root=None):
    return ensure_session()


def detach():
    pass


def sink(payload, peer_id):
    """The sync server's QR sink: settle and apply."""
    session = ensure_session()
    snap = session.detect(payload.get("text", ""), payload.get("corners", []), payload.get("time"))
    for event in session.drain_events():
        if event.kind in ("rejected", "ignored"):
            FreeCAD.Console.PrintLog("XR qr: %s %s\n" % (event.kind, event.detail))
    if snap is None:
        return "seen"
    return apply_snap(snap)


def apply_snap(snap):
    global _last_snap
    _last_snap = snap
    what = snap.what
    message = "XR qr: %s -> %s (scale error %.1f%%)" % (snap.payload.id, what, snap.scale_error * 100.0)
    try:
        from xrcore import haptics_bridge

        haptics_bridge.engine().trigger("snap", None)
    except Exception:
        pass
    if what == "model":
        widget = service.get_widget()
        node = getattr(widget, "world_grab_transform", None) if widget is not None else None
        if node is None:
            FreeCAD.Console.PrintWarning(message + " — viewer not running, nothing moved\n")
            return "no viewer"
        from xrcore import coin_util

        coin_util.set_transform(node, vm.Transform(snap.transform.translation, snap.transform.rotation))
        FreeCAD.Console.PrintMessage(message + " — model moved onto the code\n")
        return "model"
    if what.startswith("part:"):
        doc = FreeCAD.ActiveDocument
        obj = doc.getObject(what[5:]) if doc is not None else None
        if obj is None or not hasattr(obj, "Placement"):
            FreeCAD.Console.PrintWarning(message + " — no such part\n")
            return "no part"
        obj.Placement = docmesh.transform_to_placement(vm.Transform(snap.transform.translation, snap.transform.rotation))
        doc.recompute()
        FreeCAD.Console.PrintMessage(message + "\n")
        return "part"
    if what.startswith("target:"):
        FreeCAD.Console.PrintMessage(message + " — the real %s is at %s; use the environment's placement to match\n"
                                     % (what[7:], tuple(round(c, 3) for c in snap.transform.translation)))
        return "target"
    return "ignored"


def make_code(code_id, size_mm=80.0, path=None, **fields):
    """Write a printable anchor code (SVG). Returns the path, or the payload text when
    the ``qrcode`` package is missing."""
    from xrqr import AnchorPayload

    payload = AnchorPayload(code_id, size_mm, **fields)
    text = payload.encode()
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        FreeCAD.Console.PrintMessage("XR qr: install the 'qrcode' package to write an SVG; the payload is:\n  %s\n" % text)
        return text
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    path = path or service.user_dir("qr", "%s.svg" % code_id)
    with open(path, "wb") as handle:
        image.save(handle)
    FreeCAD.Console.PrintMessage("XR qr: wrote %s — print it at exactly %g mm wide\n" % (path, size_mm))
    return path


def status_text():
    if _last_snap is None:
        return ""
    return "qr: %s" % _last_snap.payload.id
