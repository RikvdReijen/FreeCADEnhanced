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
"""Layer stack, compositing and a tile based undo history.

The history deliberately never stores a whole image: a stroke touches a small
dirty rectangle, which is split into fixed size tiles, each zlib compressed.
That keeps a long VR painting session inside a predictable memory budget while
still restoring pixels exactly.
"""

import zlib

from . import raster
from .raster import Image

__all__ = [
    "History",
    "Layer",
    "LayerStack",
    "TILE",
]

#: Undo tiles are this many pixels on a side.
TILE = 128

#: Blend modes usable on a layer.  ARCHITECTURE.md §4 enumerates
#: ``normal|multiply|add|erase``; ``screen`` is supported as an extension and
#: is written out under that name.
LAYER_BLEND_MODES = ("normal", "multiply", "add", "screen", "erase")


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _norm_rect(rect, w, h):
    if rect is None:
        return (0, 0, w, h)
    x0, y0, x1, y1 = rect
    x0 = _clamp(int(x0), 0, w)
    y0 = _clamp(int(y0), 0, h)
    x1 = _clamp(int(x1), 0, w)
    y1 = _clamp(int(y1), 0, h)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


# --------------------------------------------------------------------------
# layer
# --------------------------------------------------------------------------

_next_layer_id = [0]


def _new_id():
    _next_layer_id[0] += 1
    return _next_layer_id[0]


class Layer(object):
    """One paintable RGBA layer."""

    __slots__ = ("id", "name", "image", "opacity", "blend", "visible",
                 "locked")

    def __init__(self, name, image, opacity=1.0, blend="normal",
                 visible=True, locked=False, layer_id=None):
        if blend not in LAYER_BLEND_MODES:
            raise ValueError("unknown layer blend mode: %r" % (blend,))
        self.id = _new_id() if layer_id is None else int(layer_id)
        self.name = str(name)
        self.image = image
        self.opacity = _clamp(float(opacity), 0.0, 1.0)
        self.blend = blend
        self.visible = bool(visible)
        self.locked = bool(locked)

    @property
    def width(self):
        return self.image.width

    @property
    def height(self):
        return self.image.height

    def copy(self):
        return Layer(self.name, self.image.copy(), self.opacity, self.blend,
                     self.visible, self.locked)

    def to_dict(self, image_index=None):
        """§4 paint layer record.  ``image`` is the FCXR image index."""
        return {
            "name": self.name,
            "image": image_index,
            "opacity": self.opacity,
            "blend": self.blend,
            "visible": self.visible,
            "resolution": [self.image.width, self.image.height],
        }

    @classmethod
    def from_dict(cls, d, image=None):
        res = d.get("resolution") or [64, 64]
        img = image if image is not None else Image(int(res[0]), int(res[1]))
        return cls(d.get("name", "Layer"), img,
                   float(d.get("opacity", 1.0)),
                   d.get("blend", "normal"),
                   bool(d.get("visible", True)))

    def __repr__(self):
        return "Layer(%r, %dx%d, %s)" % (self.name, self.image.width,
                                         self.image.height, self.blend)


# --------------------------------------------------------------------------
# stack
# --------------------------------------------------------------------------

class LayerStack(object):
    """An ordered list of equally sized layers, index 0 = bottom."""

    def __init__(self, width, height, layers=None):
        self.width = int(width)
        self.height = int(height)
        self.layers = list(layers) if layers else []
        self.active_index = len(self.layers) - 1 if self.layers else -1
        self._cache = None
        self._cache_valid = False

    # -- container -------------------------------------------------------
    def __len__(self):
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    def __getitem__(self, i):
        return self.layers[i]

    @property
    def active(self):
        if 0 <= self.active_index < len(self.layers):
            return self.layers[self.active_index]
        return None

    def index_of(self, layer):
        for i, l in enumerate(self.layers):
            if l is layer:
                return i
        return -1

    def by_id(self, layer_id):
        for l in self.layers:
            if l.id == layer_id:
                return l
        return None

    def find(self, name):
        for l in self.layers:
            if l.name == name:
                return l
        return None

    def invalidate(self, rect=None):
        self._cache_valid = False

    # -- structure -------------------------------------------------------
    def add_layer(self, name=None, index=None, color=None, image=None,
                  blend="normal", opacity=1.0):
        if image is None:
            image = Image(self.width, self.height, color)
        elif image.width != self.width or image.height != self.height:
            raise ValueError("layer size does not match the stack")
        if name is None:
            name = "Layer %d" % (len(self.layers) + 1)
        layer = Layer(name, image, opacity=opacity, blend=blend)
        if index is None or index >= len(self.layers):
            self.layers.append(layer)
            self.active_index = len(self.layers) - 1
        else:
            index = max(0, int(index))
            self.layers.insert(index, layer)
            self.active_index = index
        self.invalidate()
        return layer

    def insert_layer(self, index, layer):
        self.layers.insert(max(0, min(int(index), len(self.layers))), layer)
        self.active_index = min(max(0, int(index)), len(self.layers) - 1)
        self.invalidate()
        return layer

    def remove_layer(self, index):
        if not (0 <= index < len(self.layers)):
            raise IndexError("no such layer: %r" % (index,))
        layer = self.layers.pop(index)
        if self.active_index >= len(self.layers):
            self.active_index = len(self.layers) - 1
        self.invalidate()
        return layer

    def move_layer(self, src, dst):
        """Reorder; returns the new index."""
        n = len(self.layers)
        if not (0 <= src < n):
            raise IndexError("no such layer: %r" % (src,))
        dst = _clamp(int(dst), 0, n - 1)
        layer = self.layers.pop(src)
        self.layers.insert(dst, layer)
        self.active_index = dst
        self.invalidate()
        return dst

    def rename(self, index, name):
        self.layers[index].name = str(name)

    def set_opacity(self, index, value):
        self.layers[index].opacity = _clamp(float(value), 0.0, 1.0)
        self.invalidate()

    def set_blend(self, index, mode):
        if mode not in LAYER_BLEND_MODES:
            raise ValueError("unknown layer blend mode: %r" % (mode,))
        self.layers[index].blend = mode
        self.invalidate()

    def set_visible(self, index, value):
        self.layers[index].visible = bool(value)
        self.invalidate()

    def set_locked(self, index, value):
        self.layers[index].locked = bool(value)

    # -- merging ---------------------------------------------------------
    def merge_down(self, index):
        """Composite layer ``index`` onto ``index - 1`` and drop it."""
        if index <= 0 or index >= len(self.layers):
            raise IndexError("cannot merge layer %r down" % (index,))
        top = self.layers[index]
        bottom = self.layers[index - 1]
        raster.composite(bottom.image, top.image, top.opacity, top.blend,
                         out=bottom.image)
        self.layers.pop(index)
        self.active_index = index - 1
        self.invalidate()
        return bottom

    def flatten(self, name="Flattened"):
        """Replace the stack with a single composited layer."""
        flat = self.composite()
        layer = Layer(name, flat)
        self.layers = [layer]
        self.active_index = 0
        self.invalidate()
        return layer

    # -- compositing -----------------------------------------------------
    def composite(self, rect=None, into=None, background=None):
        """Composite all visible layers bottom-up into one image."""
        out = into
        if out is None:
            out = Image(self.width, self.height, background)
        elif background is not None:
            out.fill(background)
        for layer in self.layers:
            if not layer.visible or layer.opacity <= 0.0:
                continue
            raster.composite(out, layer.image, layer.opacity, layer.blend,
                             out=out, rect=rect)
        return out

    def composite_cached(self, rect=None):
        """Composite reusing an internal buffer (frame-rate friendly)."""
        if self._cache is None or self._cache.width != self.width \
                or self._cache.height != self.height:
            self._cache = Image(self.width, self.height)
            self._cache_valid = False
        if not self._cache_valid or rect is None:
            self._cache.clear()
            self.composite(into=self._cache)
            self._cache_valid = True
        else:
            self._cache.fill((0, 0, 0, 0), rect)
            self.composite(rect=rect, into=self._cache)
        return self._cache

    # -- serialisation ---------------------------------------------------
    def to_dict(self, first_image_index=0):
        return [l.to_dict(first_image_index + i)
                for i, l in enumerate(self.layers)]

    @classmethod
    def from_dict(cls, records, images=None):
        layers = []
        w = h = None
        for i, rec in enumerate(records):
            img = None
            if images is not None:
                idx = rec.get("image")
                if idx is not None and 0 <= idx < len(images):
                    img = images[idx]
            layer = Layer.from_dict(rec, img)
            if w is None:
                w, h = layer.image.width, layer.image.height
            layers.append(layer)
        if w is None:
            w = h = 64
        return cls(w, h, layers)

    def __repr__(self):
        return "LayerStack(%dx%d, %d layers)" % (self.width, self.height,
                                                 len(self.layers))


# --------------------------------------------------------------------------
# undo / redo
# --------------------------------------------------------------------------

class _Tile(object):
    __slots__ = ("layer_id", "x", "y", "w", "h", "before", "after")

    def __init__(self, layer_id, x, y, w, h, before):
        self.layer_id = layer_id
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.before = before
        self.after = None

    def nbytes(self):
        n = len(self.before or b"")
        if self.after:
            n += len(self.after)
        return n


class _Entry(object):
    __slots__ = ("label", "tiles", "structural")

    def __init__(self, label):
        self.label = label
        self.tiles = []
        self.structural = None

    def nbytes(self):
        n = sum(t.nbytes() for t in self.tiles)
        if self.structural:
            n += self.structural.nbytes()
        return n


class _Structural(object):
    """Undo record for add/remove/reorder/property changes."""

    __slots__ = ("undo_fn_name", "payload")

    def __init__(self, payload):
        self.payload = payload

    def nbytes(self):
        blob = self.payload.get("blob")
        return len(blob) if blob else 0


class History(object):
    """Bounded undo/redo stack storing compressed dirty-rect tiles."""

    def __init__(self, stack, max_entries=64, max_bytes=64 * 1024 * 1024,
                 tile=TILE):
        self.stack = stack
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self.tile = int(tile)
        self._undo = []
        self._redo = []
        self._open = None
        self._pending = {}

    # -- introspection ---------------------------------------------------
    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo_labels(self):
        return [e.label for e in self._undo]

    def redo_labels(self):
        return [e.label for e in self._redo]

    def nbytes(self):
        return sum(e.nbytes() for e in self._undo) \
            + sum(e.nbytes() for e in self._redo)

    def clear(self):
        self._undo = []
        self._redo = []
        self._open = None
        self._pending = {}

    # -- recording -------------------------------------------------------
    def begin(self, label="edit"):
        if self._open is not None:
            raise RuntimeError("a history entry is already open")
        self._open = _Entry(label)
        self._pending = {}
        return self._open

    def snapshot(self, layer, rect):
        """Record the *current* pixels of ``rect`` on ``layer``.

        Call this immediately *before* modifying the layer.  Overlapping calls
        within one entry are de-duplicated tile by tile.
        """
        if self._open is None:
            raise RuntimeError("History.snapshot() outside begin()/commit()")
        if rect is None:
            return
        img = layer.image
        x0, y0, x1, y1 = _norm_rect(rect, img.width, img.height)
        if x0 >= x1 or y0 >= y1:
            return
        t = self.tile
        for ty in range(y0 // t, (y1 - 1) // t + 1):
            for tx in range(x0 // t, (x1 - 1) // t + 1):
                key = (layer.id, tx, ty)
                if key in self._pending:
                    continue
                px = tx * t
                py = ty * t
                pw = min(t, img.width - px)
                ph = min(t, img.height - py)
                if pw <= 0 or ph <= 0:
                    continue
                sub = img.crop((px, py, px + pw, py + ph))
                tile = _Tile(layer.id, px, py, pw, ph,
                             zlib.compress(bytes(sub.data), 1))
                self._pending[key] = tile
                self._open.tiles.append(tile)

    def commit(self, drop_if_empty=True):
        """Close the open entry, capturing the resulting pixels."""
        entry = self._open
        self._open = None
        self._pending = {}
        if entry is None:
            return None
        changed = False
        for tile in entry.tiles:
            layer = self.stack.by_id(tile.layer_id)
            if layer is None:
                continue
            sub = layer.image.crop((tile.x, tile.y, tile.x + tile.w,
                                    tile.y + tile.h))
            blob = bytes(sub.data)
            tile.after = zlib.compress(blob, 1)
            if zlib.decompress(tile.before) != blob:
                changed = True
        if drop_if_empty and not changed and entry.structural is None:
            return None
        self._push(entry)
        return entry

    def abort(self):
        """Discard the open entry and restore the snapshotted pixels."""
        entry = self._open
        self._open = None
        self._pending = {}
        if entry is None:
            return
        for tile in reversed(entry.tiles):
            self._restore(tile, tile.before)

    def _push(self, entry):
        self._undo.append(entry)
        self._redo = []
        self._trim()

    def _trim(self):
        while len(self._undo) > self.max_entries:
            self._undo.pop(0)
        while self._undo and self.nbytes() > self.max_bytes \
                and len(self._undo) > 1:
            self._undo.pop(0)

    # -- structural ops --------------------------------------------------
    def record_structural(self, label, payload):
        """Record a non-pixel change (add/remove/move/property)."""
        entry = _Entry(label)
        entry.structural = _Structural(payload)
        self._push(entry)
        return entry

    def push_add_layer(self, layer, index):
        return self.record_structural("add layer", {
            "op": "add", "index": index, "layer_id": layer.id,
        })

    def push_remove_layer(self, layer, index):
        blob = zlib.compress(bytes(layer.image.data), 1)
        return self.record_structural("remove layer", {
            "op": "remove", "index": index, "layer_id": layer.id,
            "name": layer.name, "opacity": layer.opacity,
            "blend": layer.blend, "visible": layer.visible,
            "locked": layer.locked, "blob": blob,
            "size": (layer.image.width, layer.image.height),
        })

    def push_move_layer(self, src, dst):
        return self.record_structural("move layer", {
            "op": "move", "src": src, "dst": dst,
        })

    def push_property(self, layer, field, old, new):
        return self.record_structural("layer %s" % field, {
            "op": "property", "layer_id": layer.id, "field": field,
            "old": old, "new": new,
        })

    # -- apply -----------------------------------------------------------
    def _restore(self, tile, blob):
        layer = self.stack.by_id(tile.layer_id)
        if layer is None:
            return
        sub = Image(tile.w, tile.h, data=zlib.decompress(blob))
        layer.image.paste(sub, tile.x, tile.y)

    def _apply_structural(self, payload, undo):
        op = payload["op"]
        st = self.stack
        if op == "add":
            if undo:
                layer = st.by_id(payload["layer_id"])
                if layer is not None:
                    idx = st.index_of(layer)
                    payload["blob"] = zlib.compress(bytes(layer.image.data), 1)
                    payload["saved"] = (layer.name, layer.opacity,
                                        layer.blend, layer.visible,
                                        layer.locked,
                                        layer.image.width, layer.image.height)
                    st.remove_layer(idx)
            else:
                saved = payload.get("saved")
                blob = payload.get("blob")
                if saved is not None and blob is not None:
                    name, op_, bl, vis, lock, w, h = saved
                    img = Image(w, h, data=zlib.decompress(blob))
                    layer = Layer(name, img, op_, bl, vis, lock,
                                  layer_id=payload["layer_id"])
                    st.insert_layer(payload["index"], layer)
        elif op == "remove":
            if undo:
                w, h = payload["size"]
                img = Image(w, h, data=zlib.decompress(payload["blob"]))
                layer = Layer(payload["name"], img, payload["opacity"],
                              payload["blend"], payload["visible"],
                              payload["locked"], layer_id=payload["layer_id"])
                st.insert_layer(payload["index"], layer)
            else:
                layer = st.by_id(payload["layer_id"])
                if layer is not None:
                    st.remove_layer(st.index_of(layer))
        elif op == "move":
            if undo:
                st.move_layer(payload["dst"], payload["src"])
            else:
                st.move_layer(payload["src"], payload["dst"])
        elif op == "property":
            layer = st.by_id(payload["layer_id"])
            if layer is not None:
                setattr(layer, payload["field"],
                        payload["old"] if undo else payload["new"])
        st.invalidate()

    def undo(self):
        if not self._undo:
            return None
        entry = self._undo.pop()
        if entry.structural is not None:
            self._apply_structural(entry.structural.payload, undo=True)
        for tile in reversed(entry.tiles):
            self._restore(tile, tile.before)
        self._redo.append(entry)
        self.stack.invalidate()
        return entry

    def redo(self):
        if not self._redo:
            return None
        entry = self._redo.pop()
        if entry.structural is not None:
            self._apply_structural(entry.structural.payload, undo=False)
        for tile in entry.tiles:
            if tile.after is not None:
                self._restore(tile, tile.after)
        self._undo.append(entry)
        self.stack.invalidate()
        return entry
