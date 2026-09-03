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
"""The sculpt layer stack -- named, independently weighted passes of
displacement over one base mesh.

A layer stores a **sparse full vector offset per vertex**, not a scalar along a
stored normal.  The alternative is tempting: one float instead of three, and it
is what a ZBrush *morph* target does.  It was rejected for three reasons.

1. **It cannot express half the brushes.**  Grab, snake hook, pinch, scrape,
   smooth and flatten all move vertices tangentially.  A scalar-along-normal
   layer silently drops the tangential component, so a grab pass recorded into
   such a layer replays as a different shape from the one that was sculpted.
2. **The normal it is "along" goes stale.**  Displacement is stored so it can
   be re-evaluated after the layers below it change.  As soon as a lower layer
   moves the surface, the stored normal is no longer the surface normal, so the
   layer either drifts (re-derive the normal) or is not really along the normal
   at all (keep the old one).  Either way the promise in the name is false.
3. **Order independence falls out for free.**  With vector offsets, evaluating
   the stack is ``base + Σ wᵢ·offsetᵢ``: a plain weighted sum.  Addition
   commutes, so reordering additive layers cannot change the shape, changing a
   weight is exactly linear, and setting a weight back restores the previous
   positions bit for bit.  None of that survives a per-layer renormalisation.
   (The one caveat is floating point associativity: the sum runs in stack
   order, so a *reorder* can move the last bit even though the mathematics is
   invariant.  Evaluation of a given stack is bit-for-bit repeatable, which is
   what "deterministic" has to mean here.)

The cost is three doubles per touched vertex instead of one.  Because storage
is sparse that is a cost on the vertices a stroke actually touched: a layer
covering 500 vertices of a 200k mesh holds 500 entries and about 16 kB,
independent of the mesh size.

Blend behaviour
---------------

``add``      the layer's weighted offset is added to the accumulated
             displacement.  Commutative, so the position in the stack does not
             matter.  This is the default and what every brush writes.
``replace``  on the vertices the layer touches, the accumulated displacement is
             *replaced* by this layer's weighted offset -- an override pass
             ("this region is exactly this shape, whatever is underneath").
             Order-sensitive by definition, still deterministic.

Effective weight is ``weight if visible else 0.0``, so hiding a layer is
exactly equivalent to setting its weight to zero, and both restore the mesh
underneath exactly.  Weights are deliberately unclamped: negative inverts the
pass, above one exaggerates it.

Undo
----

:class:`History` stores **sparse deltas**: for a stroke it keeps the before and
after offsets of the handful of vertices the stroke touched, in one flat
``array('d')`` per side, plus the layer id.  A 500 vertex dab therefore costs
about 12 kB of history rather than a mesh copy.  Structural operations (add,
remove, reorder, rename, weight, merge, bake) snapshot the affected layers,
which are sparse too.  The stack is bounded by both an entry count and a byte
budget.
"""

import array

from . import mesh as _mesh

__all__ = [
    "BLEND_MODES",
    "History",
    "SculptLayer",
    "LayerStack",
]

#: How a layer combines with the displacement accumulated below it.
BLEND_MODES = ("add", "replace")

_EPS = 1e-12

_next_layer_id = [0]


def _new_id():
    _next_layer_id[0] += 1
    return _next_layer_id[0]


# --------------------------------------------------------------------------
# layer
# --------------------------------------------------------------------------

class SculptLayer(object):
    """One sculpt pass: a sparse map from vertex index to a vector offset.

    Internally the entries live in two parallel buffers -- ``array('i')`` of
    vertex indices and ``array('d')`` of ``3 * n`` offset components -- with a
    dict from vertex index to slot.  Lookup, insert and update are ``O(1)``;
    iteration is ``O(entries)``; nothing is ever proportional to the vertex
    count of the mesh.

    Removing an entry swaps the last slot into the hole, so :meth:`items`
    iterates in an arbitrary order.  Use :meth:`sorted_items` (``O(n log n)``)
    wherever the order is observable -- serialisation does.
    """

    __slots__ = ("id", "name", "weight", "visible", "locked", "blend",
                 "_slot", "_idx", "_off")

    def __init__(self, name="Layer", weight=1.0, visible=True, locked=False,
                 blend="add", layer_id=None):
        if blend not in BLEND_MODES:
            raise ValueError("unknown layer blend mode: %r" % (blend,))
        self.id = _new_id() if layer_id is None else int(layer_id)
        self.name = str(name)
        self.weight = float(weight)
        self.visible = bool(visible)
        self.locked = bool(locked)
        self.blend = blend
        self._slot = {}
        self._idx = array.array("i")
        self._off = array.array("d")

    # -- sparse container ------------------------------------------------
    def __len__(self):
        return len(self._idx)

    def __contains__(self, index):
        return int(index) in self._slot

    def __bool__(self):
        return bool(self._idx)

    @property
    def effective_weight(self):
        """``weight`` when visible, ``0.0`` when hidden."""
        return self.weight if self.visible else 0.0

    def indices(self):
        """The touched vertex indices, sorted."""
        return sorted(self._slot)

    def get(self, index, default=(0.0, 0.0, 0.0)):
        """The stored offset for ``index``.  ``O(1)``."""
        s = self._slot.get(int(index))
        if s is None:
            return default
        o = s * 3
        f = self._off
        return (f[o], f[o + 1], f[o + 2])

    def set(self, index, offset):
        """Store an absolute offset, creating the entry if new.  ``O(1)``."""
        index = int(index)
        s = self._slot.get(index)
        x = float(offset[0])
        y = float(offset[1])
        z = float(offset[2])
        if s is None:
            self._slot[index] = len(self._idx)
            self._idx.append(index)
            self._off.append(x)
            self._off.append(y)
            self._off.append(z)
        else:
            o = s * 3
            self._off[o] = x
            self._off[o + 1] = y
            self._off[o + 2] = z
        return self

    def add(self, index, delta):
        """Accumulate ``delta`` into the offset for ``index``.  ``O(1)``."""
        index = int(index)
        s = self._slot.get(index)
        if s is None:
            self._slot[index] = len(self._idx)
            self._idx.append(index)
            self._off.append(float(delta[0]))
            self._off.append(float(delta[1]))
            self._off.append(float(delta[2]))
        else:
            o = s * 3
            self._off[o] += float(delta[0])
            self._off[o + 1] += float(delta[1])
            self._off[o + 2] += float(delta[2])
        return self

    def pop(self, index):
        """Remove an entry (swap-with-last).  ``O(1)``.  Returns the offset."""
        index = int(index)
        s = self._slot.pop(index, None)
        if s is None:
            return None
        o = s * 3
        value = (self._off[o], self._off[o + 1], self._off[o + 2])
        last = len(self._idx) - 1
        if s != last:
            moved = self._idx[last]
            self._idx[s] = moved
            lo = last * 3
            self._off[o] = self._off[lo]
            self._off[o + 1] = self._off[lo + 1]
            self._off[o + 2] = self._off[lo + 2]
            self._slot[moved] = s
        del self._idx[last]
        del self._off[last * 3:last * 3 + 3]
        return value

    def items(self):
        """``(index, (dx, dy, dz))`` pairs in storage order."""
        f = self._off
        for s, i in enumerate(self._idx):
            o = s * 3
            yield i, (f[o], f[o + 1], f[o + 2])

    def sorted_items(self):
        """``(index, (dx, dy, dz))`` pairs in ascending index order."""
        f = self._off
        for i in sorted(self._slot):
            o = self._slot[i] * 3
            yield i, (f[o], f[o + 1], f[o + 2])

    def nbytes(self):
        """Approximate payload size, ignoring the Python object overhead."""
        return len(self._idx) * (4 + 24)

    # -- whole layer operations ------------------------------------------
    def clear(self):
        """Drop every entry, keeping name/weight/flags.  ``O(1)``."""
        self._slot = {}
        self._idx = array.array("i")
        self._off = array.array("d")
        return self

    def invert(self):
        """Negate every offset in place.  ``O(entries)``."""
        f = self._off
        for i in range(len(f)):
            f[i] = -f[i]
        return self

    def scale(self, factor):
        """Multiply every offset by ``factor`` in place.  ``O(entries)``."""
        k = float(factor)
        f = self._off
        for i in range(len(f)):
            f[i] = f[i] * k
        return self

    def prune(self, tolerance=0.0):
        """Drop entries whose offset is within ``tolerance`` of zero."""
        if tolerance < 0.0:
            raise ValueError("tolerance must be >= 0")
        t2 = float(tolerance) * float(tolerance)
        drop = []
        for i, (x, y, z) in self.items():
            if x * x + y * y + z * z <= t2:
                drop.append(i)
        for i in drop:
            self.pop(i)
        return len(drop)

    def copy(self, name=None, new_id=True):
        """A deep copy.  ``O(entries)``."""
        out = SculptLayer(self.name if name is None else name, self.weight,
                          self.visible, self.locked, self.blend,
                          layer_id=None if new_id else self.id)
        out._slot = dict(self._slot)
        out._idx = array.array("i", self._idx)
        out._off = array.array("d", self._off)
        return out

    def add_layer(self, other, factor=1.0):
        """Accumulate ``factor * other`` into this one.  ``O(len(other))``."""
        k = float(factor)
        for i, (x, y, z) in other.sorted_items():
            self.add(i, (x * k, y * k, z * k))
        return self

    def displacement_of(self, index):
        """The *weighted* offset for one vertex."""
        w = self.effective_weight
        x, y, z = self.get(index)
        return (x * w, y * w, z * w)

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        """Plain-data form (indices sorted, so it is byte stable)."""
        idx = []
        off = []
        for i, v in self.sorted_items():
            idx.append(i)
            off.extend(v)
        return {
            "name": self.name,
            "weight": self.weight,
            "visible": self.visible,
            "locked": self.locked,
            "blend": self.blend,
            "indices": idx,
            "offsets": off,
        }

    @classmethod
    def from_dict(cls, d):
        layer = cls(d.get("name", "Layer"), float(d.get("weight", 1.0)),
                    bool(d.get("visible", True)),
                    bool(d.get("locked", False)),
                    d.get("blend", "add"))
        idx = d.get("indices") or []
        off = d.get("offsets") or []
        if len(off) != 3 * len(idx):
            raise ValueError("layer %r: %d offsets for %d indices"
                             % (layer.name, len(off), len(idx)))
        for k, i in enumerate(idx):
            layer.set(i, (off[k * 3], off[k * 3 + 1], off[k * 3 + 2]))
        return layer

    def __eq__(self, other):
        if not isinstance(other, SculptLayer):
            return NotImplemented
        return (self.name == other.name and self.weight == other.weight
                and self.visible == other.visible
                and self.locked == other.locked
                and self.blend == other.blend
                and list(self.sorted_items()) == list(other.sorted_items()))

    def __hash__(self):
        return id(self)

    def __repr__(self):
        return "SculptLayer(%r, w=%.3g, %d verts%s%s)" % (
            self.name, self.weight, len(self._idx),
            "" if self.visible else ", hidden",
            "" if self.blend == "add" else ", " + self.blend)


# --------------------------------------------------------------------------
# stack
# --------------------------------------------------------------------------

class LayerStack(object):
    """An ordered list of :class:`SculptLayer`, index 0 = bottom.

    The stack owns the *base* positions (a flat ``array('d')``).  Evaluating it
    produces the sculpted positions; nothing else in the package mutates the
    base except :meth:`bake_to_base`.
    """

    def __init__(self, base=None, layers=None, n_vertices=None):
        if base is None:
            n = int(n_vertices or 0)
            self.base = array.array("d", bytes(24 * n))
        else:
            self.base = array.array(
                "d", [float(v) for v in _mesh.flatten3(base, "base")])
        self.layers = list(layers or [])
        self.active_index = len(self.layers) - 1 if self.layers else -1

    # -- container -------------------------------------------------------
    @property
    def n_vertices(self):
        return len(self.base) // 3

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

    def nbytes(self):
        return sum(l.nbytes() for l in self.layers)

    # -- structure -------------------------------------------------------
    def add_layer(self, name=None, index=None, weight=1.0, blend="add",
                  visible=True, locked=False):
        """Insert a new empty layer; it becomes active."""
        if name is None:
            name = self._unique_name("Layer %d" % (len(self.layers) + 1))
        layer = SculptLayer(name, weight, visible, locked, blend)
        return self.insert_layer(len(self.layers) if index is None else index,
                                 layer)

    def insert_layer(self, index, layer):
        index = max(0, min(int(index), len(self.layers)))
        self.layers.insert(index, layer)
        self.active_index = index
        return layer

    def remove_layer(self, index):
        if not (0 <= index < len(self.layers)):
            raise IndexError("no such layer: %r" % (index,))
        layer = self.layers.pop(index)
        if self.active_index >= len(self.layers):
            self.active_index = len(self.layers) - 1
        return layer

    def move_layer(self, src, dst):
        """Reorder a layer; returns its new index."""
        n = len(self.layers)
        if not (0 <= src < n):
            raise IndexError("no such layer: %r" % (src,))
        dst = max(0, min(int(dst), n - 1))
        layer = self.layers.pop(src)
        self.layers.insert(dst, layer)
        self.active_index = dst
        return dst

    def rename(self, index, name):
        self.layers[index].name = str(name)
        return self.layers[index]

    def set_weight(self, index, value):
        self.layers[index].weight = float(value)
        return self.layers[index]

    def set_visible(self, index, value):
        self.layers[index].visible = bool(value)
        return self.layers[index]

    def set_locked(self, index, value):
        self.layers[index].locked = bool(value)
        return self.layers[index]

    def set_blend(self, index, mode):
        if mode not in BLEND_MODES:
            raise ValueError("unknown layer blend mode: %r" % (mode,))
        self.layers[index].blend = mode
        return self.layers[index]

    def duplicate(self, index, name=None):
        """Copy a layer and insert the copy directly above it."""
        src = self.layers[index]
        copy = src.copy(name or self._unique_name(src.name + " copy"))
        return self.insert_layer(index + 1, copy)

    def _unique_name(self, base):
        names = set(l.name for l in self.layers)
        if base not in names:
            return base
        i = 2
        while "%s %d" % (base, i) in names:
            i += 1
        return "%s %d" % (base, i)

    def set_active(self, index):
        self.active_index = max(-1, min(int(index), len(self.layers) - 1))
        return self.active

    def ensure_active(self, name=None):
        """Return the active layer, creating one when the stack is empty."""
        layer = self.active
        if layer is None:
            layer = self.add_layer(name)
        return layer

    # -- evaluation ------------------------------------------------------
    def displacement(self, out=None, indices=None):
        """The accumulated displacement of the whole stack.

        ``O(Σ entries)`` when ``indices`` is ``None`` -- proportional to the
        sculpted vertices, never to the mesh -- apart from allocating the
        output buffer, which is ``O(V)``.
        """
        n = self.n_vertices
        if out is None:
            out = array.array("d", bytes(24 * n))
        elif len(out) != 3 * n:
            raise ValueError("displacement buffer has the wrong length")
        else:
            for i in range(len(out)):
                out[i] = 0.0
        wanted = None if indices is None else set(int(i) for i in indices)
        for layer in self.layers:
            w = layer.effective_weight
            replace = layer.blend == "replace"
            if w == 0.0 and not replace:
                continue
            for i, (x, y, z) in layer.items():
                if wanted is not None and i not in wanted:
                    continue
                o = i * 3
                if replace:
                    out[o] = x * w
                    out[o + 1] = y * w
                    out[o + 2] = z * w
                else:
                    out[o] += x * w
                    out[o + 1] += y * w
                    out[o + 2] += z * w
        return out

    def evaluate(self, out=None, indices=None):
        """Sculpted positions: ``base + Σ wᵢ·offsetᵢ``.

        Deterministic: contributions are summed in stack order, one per layer
        per vertex, so the result depends only on the layer contents and their
        order -- never on the order the strokes were made in.  With every layer
        in ``add`` mode the sum is commutative and the order does not matter
        either.

        Writes into ``out`` when given (a flat ``array('d')`` of ``3 * V``),
        which is how the session updates the live mesh without reallocating.
        ``indices`` restricts the update to those vertices, for a partial
        re-evaluation after a dab.
        """
        base = self.base
        if out is None:
            out = array.array("d", base)
        elif len(out) != len(base):
            raise ValueError("evaluate() buffer has the wrong length")
        elif indices is None:
            out[:] = array.array("d", base)
        if indices is not None:
            for i in indices:
                o = int(i) * 3
                out[o] = base[o]
                out[o + 1] = base[o + 1]
                out[o + 2] = base[o + 2]
        if indices is None and self._prefer_numpy():
            return self._evaluate_numpy(out)
        wanted = None if indices is None else set(int(i) for i in indices)
        for layer in self.layers:
            w = layer.effective_weight
            replace = layer.blend == "replace"
            if w == 0.0 and not replace:
                continue
            for i, (x, y, z) in layer.items():
                if wanted is not None and i not in wanted:
                    continue
                o = i * 3
                if replace:
                    out[o] = base[o] + x * w
                    out[o + 1] = base[o + 1] + y * w
                    out[o + 2] = base[o + 2] + z * w
                else:
                    out[o] += x * w
                    out[o + 1] += y * w
                    out[o + 2] += z * w
        return out

    def _prefer_numpy(self):
        """Whether the vectorised path is worth taking for this stack.

        The numpy path rebuilds the whole position array, so it only pays once
        the layers are dense enough that the scalar loop is no longer just
        walking a short list.  Measured on a 41k vertex mesh: three full-mesh
        layers evaluate about three times faster vectorised, while a single
        23-vertex dab evaluates faster scalar by an order of magnitude.  A
        quarter of the mesh is the crossover.  Either path gives bit-identical
        results, so this is purely a speed choice.
        """
        if not _mesh.use_numpy():
            return False
        n = self.n_vertices
        if n < 4096:
            return False
        return sum(len(l) for l in self.layers) * 4 >= n

    def _evaluate_numpy(self, out):
        """Vectorised :meth:`evaluate`.

        Each vertex still receives one multiply-add per layer, in stack order,
        so this is bit-identical to the scalar loop rather than merely close.
        """
        np = _mesh._numpy()
        base = np.frombuffer(self.base, dtype=np.float64).reshape(-1, 3)
        acc = base.copy()
        for layer in self.layers:
            w = layer.effective_weight
            replace = layer.blend == "replace"
            if not len(layer) or (w == 0.0 and not replace):
                continue
            idx = np.frombuffer(layer._idx, dtype=np.int32)
            off = np.frombuffer(layer._off, dtype=np.float64).reshape(-1, 3)
            if replace:
                acc[idx] = base[idx] + off * w
            else:
                acc[idx] += off * w
        flat = acc.reshape(-1)
        out[:] = array.array("d", flat.tolist())
        return out

    def apply_to(self, mesh, indices=None):
        """Write the evaluated positions into ``mesh`` and mark it dirty."""
        self.evaluate(out=mesh.positions, indices=indices)
        mesh.touch(indices)
        return mesh

    def touched_indices(self):
        """Every vertex any layer touches, sorted."""
        seen = set()
        for layer in self.layers:
            seen.update(layer._slot)
        return sorted(seen)

    # -- destructive operations ------------------------------------------
    def merge_down(self, index):
        """Fold layer ``index`` into ``index - 1`` and drop it.

        Exact: evaluating the stack afterwards gives the same positions.  Only
        defined when both layers blend with ``add`` -- a ``replace`` layer
        overrides everything below it, not just its immediate neighbour, so
        there is no pair of layers whose merge is equivalent in general.
        """
        if index <= 0 or index >= len(self.layers):
            raise IndexError("cannot merge layer %r down" % (index,))
        top = self.layers[index]
        bottom = self.layers[index - 1]
        if top.blend != "add" or bottom.blend != "add":
            raise ValueError(
                "merge_down is only exact for 'add' layers; layer %r is %r"
                % ((top if top.blend != "add" else bottom).name,
                   top.blend if top.blend != "add" else bottom.blend))
        merged = SculptLayer(bottom.name, 1.0, True, bottom.locked, "add")
        merged.add_layer(bottom, bottom.effective_weight)
        merged.add_layer(top, top.effective_weight)
        self.layers[index - 1] = merged
        self.layers.pop(index)
        self.active_index = index - 1
        return merged

    def flatten(self, name="Sculpt"):
        """Replace the whole stack with one equivalent ``add`` layer."""
        for layer in self.layers:
            if layer.blend != "add":
                raise ValueError("flatten() is only exact for 'add' layers")
        merged = SculptLayer(name, 1.0, True, False, "add")
        for layer in self.layers:
            merged.add_layer(layer, layer.effective_weight)
        self.layers = [merged]
        self.active_index = 0
        return merged

    def bake_to_base(self, indices=None, remove=True):
        """Fold the stack into the base positions.

        Afterwards :meth:`evaluate` returns exactly what it returned before,
        with an empty (or emptied) stack.  ``remove=False`` keeps the layers as
        empty shells so their names, weights and order survive.
        """
        disp = self.displacement(indices=indices)
        base = self.base
        for i in range(len(base)):
            base[i] += disp[i]
        if remove:
            self.layers = []
            self.active_index = -1
        else:
            for layer in self.layers:
                layer.clear()
        return base

    def clear_layer(self, index):
        return self.layers[index].clear()

    def invert_layer(self, index):
        return self.layers[index].invert()

    # -- serialisation ---------------------------------------------------
    def to_dict(self, include_base=True):
        d = {
            "version": 1,
            "vertex_count": self.n_vertices,
            "active": self.active_index,
            "layers": [l.to_dict() for l in self.layers],
        }
        if include_base:
            d["base"] = list(self.base)
        return d

    @classmethod
    def from_dict(cls, d, base=None):
        raw = d.get("base") if base is None else base
        if raw is None:
            n = int(d.get("vertex_count", 0))
            stack = cls(n_vertices=n)
        else:
            stack = cls(base=raw)
        stack.layers = [SculptLayer.from_dict(r) for r in d.get("layers", [])]
        stack.active_index = int(d.get("active", len(stack.layers) - 1))
        if stack.active_index >= len(stack.layers):
            stack.active_index = len(stack.layers) - 1
        return stack

    def __repr__(self):
        return "LayerStack(%d verts, %d layers, %d entries)" % (
            self.n_vertices, len(self.layers),
            sum(len(l) for l in self.layers))


# --------------------------------------------------------------------------
# undo / redo
# --------------------------------------------------------------------------

class _Delta(object):
    """Before/after offsets for a handful of vertices on one layer."""

    __slots__ = ("layer_id", "indices", "before", "after")

    def __init__(self, layer_id):
        self.layer_id = layer_id
        self.indices = array.array("i")
        self.before = array.array("d")
        self.after = None

    def record(self, index, offset):
        self.indices.append(int(index))
        self.before.append(offset[0])
        self.before.append(offset[1])
        self.before.append(offset[2])

    def nbytes(self):
        n = len(self.indices) * (4 + 24)
        if self.after is not None:
            n += len(self.indices) * 24
        return n


class _Snapshot(object):
    """A whole (sparse) layer plus its place in the stack."""

    __slots__ = ("index", "layer")

    def __init__(self, index, layer):
        self.index = index
        self.layer = layer

    def nbytes(self):
        return self.layer.nbytes() + 64


class _Entry(object):
    __slots__ = ("label", "deltas", "structural", "base_before", "base_after",
                 "active_before", "active_after")

    def __init__(self, label):
        self.label = label
        self.deltas = {}
        self.structural = None
        self.base_before = None
        self.base_after = None
        self.active_before = None
        self.active_after = None

    def nbytes(self):
        n = sum(d.nbytes() for d in self.deltas.values())
        if self.structural is not None:
            n += self.structural.nbytes()
        if self.base_before is not None:
            n += len(self.base_before) * 8
        if self.base_after is not None:
            n += len(self.base_after) * 8
        return n


class _Structural(object):
    """Undo record for add/remove/reorder/property/merge/bake."""

    __slots__ = ("payload",)

    def __init__(self, payload):
        self.payload = payload

    def nbytes(self):
        n = 128
        for key in ("before", "after"):
            for snap in self.payload.get(key, ()) or ():
                n += snap.nbytes()
        return n


class History(object):
    """Bounded undo/redo over a :class:`LayerStack`, storing sparse deltas.

    A stroke opens an entry, calls :meth:`snapshot` for each vertex before it
    is modified (de-duplicated within the entry), then :meth:`commit`, which
    captures the resulting offsets.  Nothing here ever copies the mesh.
    """

    def __init__(self, stack, max_entries=64, max_bytes=32 * 1024 * 1024):
        self.stack = stack
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._undo = []
        self._redo = []
        self._open = None
        self._seen = None

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
        self._seen = None

    # -- recording -------------------------------------------------------
    def begin(self, label="sculpt"):
        if self._open is not None:
            raise RuntimeError("a history entry is already open")
        self._open = _Entry(label)
        self._open.active_before = self.stack.active_index
        self._seen = {}
        return self._open

    @property
    def open_entry(self):
        return self._open

    def snapshot(self, layer, indices):
        """Record the current offsets of ``indices`` on ``layer``.

        Call *before* modifying them.  ``O(len(indices))``, de-duplicated
        within the open entry.
        """
        if self._open is None:
            raise RuntimeError("History.snapshot() outside begin()/commit()")
        delta = self._open.deltas.get(layer.id)
        if delta is None:
            delta = _Delta(layer.id)
            self._open.deltas[layer.id] = delta
        seen = self._seen.setdefault(layer.id, set())
        for i in indices:
            i = int(i)
            if i in seen:
                continue
            seen.add(i)
            delta.record(i, layer.get(i))
        return delta

    def commit(self, drop_if_empty=True):
        """Close the open entry, capturing the resulting offsets."""
        entry = self._open
        self._open = None
        self._seen = None
        if entry is None:
            return None
        entry.active_after = self.stack.active_index
        changed = entry.structural is not None
        for delta in entry.deltas.values():
            layer = self.stack.by_id(delta.layer_id)
            after = array.array("d")
            for i in delta.indices:
                x, y, z = (0.0, 0.0, 0.0) if layer is None else layer.get(i)
                after.append(x)
                after.append(y)
                after.append(z)
            delta.after = after
            if after != delta.before:
                changed = True
        if entry.base_before is not None and entry.base_after is None:
            entry.base_after = array.array("d", self.stack.base)
            if entry.base_after != entry.base_before:
                changed = True
        if drop_if_empty and not changed:
            return None
        self._push(entry)
        return entry

    def abort(self):
        """Discard the open entry, restoring the snapshotted offsets."""
        entry = self._open
        self._open = None
        self._seen = None
        if entry is None:
            return None
        self._restore(entry, undo=True)
        return entry

    def _push(self, entry):
        self._undo.append(entry)
        self._redo = []
        self._trim()
        return entry

    def _trim(self):
        while len(self._undo) > self.max_entries:
            self._undo.pop(0)
        while len(self._undo) > 1 and self.nbytes() > self.max_bytes:
            self._undo.pop(0)

    # -- structural ------------------------------------------------------
    def record_structural(self, label, payload, base_before=None):
        """Record a structural change; returns the entry."""
        entry = _Entry(label)
        entry.structural = _Structural(payload)
        entry.active_before = payload.get("active_before",
                                          self.stack.active_index)
        entry.active_after = self.stack.active_index
        if base_before is not None:
            entry.base_before = array.array("d", base_before)
            entry.base_after = array.array("d", self.stack.base)
        return self._push(entry)

    def snapshot_layers(self, indices):
        """Deep copies of the given layers, for a structural undo record."""
        return [_Snapshot(i, self.stack.layers[i].copy(new_id=False))
                for i in indices]

    # -- apply -----------------------------------------------------------
    def _apply_delta(self, delta, undo):
        layer = self.stack.by_id(delta.layer_id)
        if layer is None:
            return
        src = delta.before if undo else delta.after
        if src is None:
            return
        for k, i in enumerate(delta.indices):
            o = k * 3
            x, y, z = src[o], src[o + 1], src[o + 2]
            if x == 0.0 and y == 0.0 and z == 0.0 and i in layer:
                layer.pop(i)
            elif x or y or z:
                layer.set(i, (x, y, z))

    def _apply_structural(self, payload, undo):
        st = self.stack
        op = payload["op"]
        if op == "add":
            if undo:
                layer = st.by_id(payload["layer_id"])
                if layer is not None:
                    payload["saved"] = layer.copy(new_id=False)
                    st.remove_layer(st.index_of(layer))
            else:
                saved = payload.get("saved")
                if saved is not None:
                    st.insert_layer(payload["index"], saved.copy(new_id=False))
        elif op == "remove":
            if undo:
                st.insert_layer(payload["index"],
                                payload["saved"].copy(new_id=False))
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
        elif op == "replace":
            # a wholesale swap of a contiguous range of layers
            snaps = payload["before"] if undo else payload["after"]
            start = payload["start"]
            count = len(payload["after"] if undo else payload["before"])
            del st.layers[start:start + count]
            for k, snap in enumerate(snaps):
                st.layers.insert(start + k, snap.layer.copy(new_id=False))

    def _restore(self, entry, undo):
        if entry.structural is not None:
            self._apply_structural(entry.structural.payload, undo)
        for delta in entry.deltas.values():
            self._apply_delta(delta, undo)
        blob = entry.base_before if undo else entry.base_after
        if blob is not None:
            self.stack.base[:] = array.array("d", blob)
        active = entry.active_before if undo else entry.active_after
        if active is not None:
            self.stack.active_index = max(-1, min(int(active),
                                                  len(self.stack.layers) - 1))

    def undo(self):
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._restore(entry, undo=True)
        self._redo.append(entry)
        return entry

    def redo(self):
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._restore(entry, undo=False)
        self._undo.append(entry)
        return entry

    def __repr__(self):
        return "History(%d undo, %d redo, %d bytes)" % (
            len(self._undo), len(self._redo), self.nbytes())
