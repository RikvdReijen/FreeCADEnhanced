# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Serialising the sculpt layer stack.

Two front doors, both lossless:

``dumps`` / ``loads``
    A self-contained **FCSL v1** blob -- a tiny chunked container built the
    same way as FCXR (§1): a fixed header, a JSON directory, then one binary
    payload every entry indexes into.  This is what a FreeCAD document stores
    (base64 in a string property) and what survives a save/reload.

``sculpt_section`` / ``read_sculpt_section``
    The ``sculpt`` section of an FCXR manifest, so a sculpt travels inside an
    ``.fcxr`` package alongside the meshes it belongs to.  The section is
    described in ``Resources/doc/SCULPTING.md``; it follows the conventions of
    :mod:`xrsync.fcxr` -- everything bulky lives in the single ``BIN`` chunk
    behind accessors, the JSON holds only names, weights and indices, and
    identical inputs produce identical bytes.

Why the sparse displacements are stored as **float64 inside a ``U8`` accessor**
rather than as a ``VEC3``/``F32`` one: a layer is an *edit*, and an edit that
does not come back exactly is a layer whose weight slider no longer returns the
mesh to where it was.  F32 would quantise every offset on every save, so the
round trip would be lossy in a way the user can see after a few passes.  The
FCXR accessor vocabulary has no 64-bit float, so the lossless payload rides in
a byte accessor and the manifest says ``"encoding": "fcsl1"``.  For readers
that want the plain form (the Quest app, a debugger) ``sculpt_section`` can
also emit ``"encoding": "f32"`` with ordinary ``U32``/``VEC3``-``F32``
accessors -- lossy, interoperable, and explicitly marked as such.

Determinism: layer entries are written in ascending vertex order and the JSON
is emitted with sorted keys and no whitespace, so two runs over the same stack
produce byte-identical output and the FCXR ``content_hash`` stays meaningful.

Pure standard library; :mod:`xrsync.fcxr` is imported lazily inside the
functions that need it (ARCHITECTURE.md §6).
"""

import array
import base64
import json
import struct
import sys
import zlib

from .layers import LayerStack, SculptLayer
from .masking import VertexMask
from .symmetry import Symmetry

__all__ = [
    "FCSL_MAGIC",
    "FCSL_VERSION",
    "SculptIoError",
    "SculptPayload",
    "dumps",
    "loads",
    "dumps_base64",
    "loads_base64",
    "read_sculpt_section",
    "sculpt_section",
]

FCSL_MAGIC = b"FCSL"
FCSL_VERSION = 1

_HEADER = struct.Struct("<4sII")     # magic, version, json length


class SculptIoError(Exception):
    """Raised for malformed or unsupported sculpt payloads."""


class SculptPayload(object):
    """What one target's sculpt state serialises to and from."""

    __slots__ = ("stack", "mask", "symmetry", "fc_name")

    def __init__(self, stack, mask=None, symmetry=None, fc_name=None):
        self.stack = stack
        self.mask = mask
        self.symmetry = symmetry
        self.fc_name = fc_name

    def __repr__(self):
        return "SculptPayload(%r, %r, %s)" % (
            self.fc_name, self.stack,
            "masked" if self.mask is not None else "unmasked")


# --------------------------------------------------------------------------
# byte helpers
# --------------------------------------------------------------------------

def _pack_doubles(values):
    arr = array.array("d", values)
    if sys.byteorder != "little":   # pragma: no cover - big endian hosts
        arr.byteswap()
    return arr.tobytes()


def _unpack_doubles(blob):
    arr = array.array("d")
    arr.frombytes(bytes(blob))
    if sys.byteorder != "little":   # pragma: no cover - big endian hosts
        arr.byteswap()
    return arr


def _pack_ints(values):
    arr = array.array("i", values)
    if sys.byteorder != "little":   # pragma: no cover - big endian hosts
        arr.byteswap()
    return arr.tobytes()


def _unpack_ints(blob):
    arr = array.array("i")
    arr.frombytes(bytes(blob))
    if sys.byteorder != "little":   # pragma: no cover - big endian hosts
        arr.byteswap()
    return arr


# --------------------------------------------------------------------------
# FCSL container
# --------------------------------------------------------------------------

def dumps(stack, mask=None, symmetry=None, fc_name=None, include_base=True,
          compress=True):
    """Serialise a layer stack (and optionally its mask) to FCSL v1 bytes.

    Size is proportional to the *touched* vertices, not the mesh: a layer over
    500 vertices costs 500 * 28 bytes before compression whatever the mesh
    underneath it looks like.  ``include_base=False`` drops the base positions,
    which is right when the caller already stores the mesh elsewhere and only
    wants the edits.
    """
    payload = bytearray()
    header = {
        "version": FCSL_VERSION,
        "vertex_count": stack.n_vertices,
        "active": stack.active_index,
        "compression": "zlib" if compress else "none",
        "layers": [],
    }
    if fc_name:
        header["fc_name"] = str(fc_name)

    def _append(blob):
        offset = len(payload)
        payload.extend(blob)
        return {"offset": offset, "length": len(blob)}

    if include_base and len(stack.base):
        header["base"] = _append(_pack_doubles(stack.base))
    else:
        header["base"] = None

    for layer in stack.layers:
        idx = []
        off = []
        for i, v in layer.sorted_items():
            idx.append(i)
            off.extend(v)
        header["layers"].append({
            "name": layer.name,
            "weight": layer.weight,
            "visible": layer.visible,
            "locked": layer.locked,
            "blend": layer.blend,
            "count": len(idx),
            "indices": _append(_pack_ints(idx)),
            "offsets": _append(_pack_doubles(off)),
        })

    if mask is not None and len(mask):
        header["mask"] = {
            "freeze": bool(mask.freeze),
            "freeze_threshold": float(mask.freeze_threshold),
            "data": _append(bytes(mask.to_bytes())),
        }
    else:
        header["mask"] = None

    header["symmetry"] = symmetry.to_dict() if symmetry is not None else None

    raw = bytes(payload)
    header["uncompressed_length"] = len(raw)
    body = zlib.compress(raw, 6) if compress else raw
    blob = json.dumps(header, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return _HEADER.pack(FCSL_MAGIC, FCSL_VERSION, len(blob)) + blob + body


def loads(data):
    """Inverse of :func:`dumps`; returns a :class:`SculptPayload`."""
    data = bytes(data)
    if len(data) < _HEADER.size:
        raise SculptIoError("sculpt payload is too short")
    magic, version, json_len = _HEADER.unpack_from(data, 0)
    if magic != FCSL_MAGIC:
        raise SculptIoError("not an FCSL payload (bad magic %r)" % (magic,))
    if version != FCSL_VERSION:
        raise SculptIoError("unsupported FCSL version %d" % (version,))
    start = _HEADER.size
    end = start + json_len
    if end > len(data):
        raise SculptIoError("truncated FCSL header")
    try:
        header = json.loads(data[start:end].decode("utf-8"))
    except Exception as exc:
        raise SculptIoError("bad FCSL header: %s" % (exc,)) from None
    body = data[end:]
    if header.get("compression", "none") == "zlib":
        try:
            body = zlib.decompress(body)
        except zlib.error as exc:
            raise SculptIoError("FCSL payload will not inflate: %s"
                                % (exc,)) from None
    want = header.get("uncompressed_length")
    if want is not None and len(body) != want:
        raise SculptIoError("FCSL payload is %d bytes, header says %d"
                            % (len(body), want))

    def _slice(ref, what):
        if ref is None:
            return b""
        o = int(ref["offset"])
        n = int(ref["length"])
        if o < 0 or n < 0 or o + n > len(body):
            raise SculptIoError("%s: [%d, %d) is outside the %d byte payload"
                                % (what, o, o + n, len(body)))
        return body[o:o + n]

    base_ref = header.get("base")
    if base_ref is not None:
        base = _unpack_doubles(_slice(base_ref, "base"))
        stack = LayerStack(base=list(base))
    else:
        stack = LayerStack(n_vertices=int(header.get("vertex_count", 0)))

    for rec in header.get("layers", []):
        layer = SculptLayer(rec.get("name", "Layer"),
                            float(rec.get("weight", 1.0)),
                            bool(rec.get("visible", True)),
                            bool(rec.get("locked", False)),
                            rec.get("blend", "add"))
        idx = _unpack_ints(_slice(rec.get("indices"), "layer indices"))
        off = _unpack_doubles(_slice(rec.get("offsets"), "layer offsets"))
        if len(off) != 3 * len(idx):
            raise SculptIoError("layer %r: %d offsets for %d indices"
                                % (layer.name, len(off), len(idx)))
        for k, i in enumerate(idx):
            o = k * 3
            layer.set(i, (off[o], off[o + 1], off[o + 2]))
        stack.layers.append(layer)
    stack.active_index = int(header.get("active", len(stack.layers) - 1))
    if stack.active_index >= len(stack.layers):
        stack.active_index = len(stack.layers) - 1

    mask = None
    mrec = header.get("mask")
    if mrec:
        mask = VertexMask.from_bytes(_slice(mrec.get("data"), "mask"),
                                     freeze=bool(mrec.get("freeze", False)),
                                     freeze_threshold=float(
                                         mrec.get("freeze_threshold", 0.5)))
    sym = header.get("symmetry")
    symmetry = Symmetry.from_dict(sym) if sym else None
    return SculptPayload(stack, mask, symmetry, header.get("fc_name"))


def dumps_base64(stack, **kw):
    """FCSL bytes as ASCII, for a FreeCAD string property."""
    return base64.b64encode(dumps(stack, **kw)).decode("ascii")


def loads_base64(text):
    """Inverse of :func:`dumps_base64`."""
    return loads(base64.b64decode(text.encode("ascii")))


# --------------------------------------------------------------------------
# FCXR manifest section
# --------------------------------------------------------------------------

def sculpt_section(writer, payloads, encoding="fcsl1"):
    """Build the ``sculpt`` section of an FCXR manifest.

    ``writer`` is an :class:`xrsync.fcxr.FcxrWriter` (anything with
    ``add_accessor``); ``payloads`` is an iterable of :class:`SculptPayload`
    or ``(fc_name, stack, mask, symmetry)`` tuples.

    ``encoding``:

    ``"fcsl1"``  (default) one ``U8`` accessor per target holding the whole
                 lossless FCSL blob.  Round trips bit for bit.
    ``"f32"``    plain accessors -- ``U32`` indices and ``VEC3``/``F32``
                 offsets per layer, ``U8`` per-vertex mask.  Readable by any
                 FCXR reader, and lossy: offsets are rounded to float32.

    Returns the dict to store under ``manifest["sculpt"]``.
    """
    if encoding not in ("fcsl1", "f32"):
        raise SculptIoError("unknown sculpt encoding: %r" % (encoding,))
    targets = []
    for item in payloads:
        p = item if isinstance(item, SculptPayload) else \
            SculptPayload(item[1], item[2] if len(item) > 2 else None,
                          item[3] if len(item) > 3 else None, item[0])
        rec = {
            "fc_name": p.fc_name or "",
            "vertex_count": p.stack.n_vertices,
            "active": p.stack.active_index,
            "encoding": encoding,
        }
        if p.symmetry is not None:
            rec["symmetry"] = p.symmetry.to_dict()
        if encoding == "fcsl1":
            blob = dumps(p.stack, p.mask, p.symmetry, p.fc_name,
                         include_base=True, compress=True)
            rec["blob"] = writer.add_accessor(list(blob), "SCALAR", "U8")
            rec["layers"] = [{"name": l.name, "weight": l.weight,
                              "visible": l.visible, "locked": l.locked,
                              "blend": l.blend, "count": len(l)}
                             for l in p.stack.layers]
        else:
            layers = []
            for layer in p.stack.layers:
                idx = []
                off = []
                for i, v in layer.sorted_items():
                    idx.append(i)
                    off.extend(v)
                layers.append({
                    "name": layer.name, "weight": layer.weight,
                    "visible": layer.visible, "locked": layer.locked,
                    "blend": layer.blend, "count": len(idx),
                    "indices": (writer.add_accessor(idx, "SCALAR", "U32")
                                if idx else None),
                    "offsets": (writer.add_accessor(off, "VEC3", "F32")
                                if off else None),
                })
            rec["layers"] = layers
            rec["base"] = writer.add_accessor(list(p.stack.base), "VEC3",
                                              "F32") if len(p.stack.base) \
                else None
            if p.mask is not None and len(p.mask):
                rec["mask"] = writer.add_accessor(list(bytearray(
                    p.mask.to_bytes())), "SCALAR", "U8")
                rec["mask_freeze"] = bool(p.mask.freeze)
            else:
                rec["mask"] = None
        targets.append(rec)
    return {"version": 1, "targets": targets}


def read_sculpt_section(document, section=None):
    """Inverse of :func:`sculpt_section`.

    ``document`` is an :class:`xrsync.fcxr.FcxrDocument` (anything with
    ``read_accessor``); ``section`` defaults to ``document.manifest["sculpt"]``
    where that is reachable.  Returns a list of :class:`SculptPayload`.
    """
    if section is None:
        manifest = getattr(document, "manifest", None)
        if manifest is None:
            raise SculptIoError("no sculpt section and no manifest to take "
                                "one from")
        section = manifest.get("sculpt")
    if not section:
        return []
    if int(section.get("version", 1)) != 1:
        raise SculptIoError("unsupported sculpt section version %r"
                            % (section.get("version"),))
    out = []
    for rec in section.get("targets", []):
        encoding = rec.get("encoding", "fcsl1")
        if encoding == "fcsl1":
            blob = document.read_accessor(int(rec["blob"]))
            payload = loads(bytes(bytearray(blob)))
            payload.fc_name = rec.get("fc_name") or payload.fc_name
            out.append(payload)
            continue
        if encoding != "f32":
            raise SculptIoError("unknown sculpt encoding: %r" % (encoding,))
        base_ref = rec.get("base")
        if base_ref is None:
            stack = LayerStack(n_vertices=int(rec.get("vertex_count", 0)))
        else:
            stack = LayerStack(
                base=list(document.read_accessor(int(base_ref))))
        for lrec in rec.get("layers", []):
            layer = SculptLayer(lrec.get("name", "Layer"),
                                float(lrec.get("weight", 1.0)),
                                bool(lrec.get("visible", True)),
                                bool(lrec.get("locked", False)),
                                lrec.get("blend", "add"))
            iref = lrec.get("indices")
            oref = lrec.get("offsets")
            if iref is not None and oref is not None:
                idx = document.read_accessor(int(iref))
                off = document.read_accessor(int(oref))
                for k, i in enumerate(idx):
                    o = k * 3
                    layer.set(int(i), (off[o], off[o + 1], off[o + 2]))
            stack.layers.append(layer)
        stack.active_index = int(rec.get("active", len(stack.layers) - 1))
        if stack.active_index >= len(stack.layers):
            stack.active_index = len(stack.layers) - 1
        mask = None
        mref = rec.get("mask")
        if mref is not None:
            mask = VertexMask.from_bytes(
                bytes(bytearray(document.read_accessor(int(mref)))),
                freeze=bool(rec.get("mask_freeze", False)))
        sym = rec.get("symmetry")
        out.append(SculptPayload(stack, mask,
                                 Symmetry.from_dict(sym) if sym else None,
                                 rec.get("fc_name")))
    return out
