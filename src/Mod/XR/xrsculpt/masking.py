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
"""Vertex masks -- the shared "do not touch this" state every brush consults.

A mask is one float per vertex in ``[0, 1]``: ``0`` unmasked (fully editable),
``1`` fully masked (protected).  The brushes call exactly one method,
:meth:`VertexMask.factor`, once per candidate vertex, so it is deliberately a
plain array lookup and two comparisons -- no allocation, no dict, no branchy
policy object.

``freeze`` is the hard version of the same idea: with it on, any vertex whose
mask exceeds :attr:`VertexMask.freeze_threshold` returns a factor of exactly
zero rather than ``1 - m``, so a half-painted mask still stops a brush dead
instead of letting it bleed through at half strength.

Complexity: :meth:`factor` is ``O(1)``; :meth:`paint_sphere` is ``O(k)`` in the
vertices the brush touches; :meth:`invert`, :meth:`fill` and :meth:`clear` are
``O(V)``; :meth:`blur` is ``O(V + E)`` per iteration; :meth:`mask_by_cavity` is
``O(V + E)``.

Storage is dense (one ``array('f')`` of ``V`` floats -- 800 kB for a 200k
vertex mesh) rather than sparse, because the brush hits it once per candidate
vertex per dab and a dict lookup there costs more than the memory saves.
"""

import array
import math

__all__ = [
    "VertexMask",
]

_EPS = 1e-12


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


class VertexMask(object):
    """A per-vertex protection weight, shared by every brush on a target."""

    __slots__ = ("values", "freeze", "freeze_threshold")

    def __init__(self, n_vertices=0, values=None, freeze=False,
                 freeze_threshold=0.5):
        if values is not None:
            self.values = array.array("f", [_clamp01(float(v))
                                            for v in values])
        else:
            self.values = array.array("f", bytes(4 * int(n_vertices)))
        self.freeze = bool(freeze)
        self.freeze_threshold = float(freeze_threshold)

    # -- the hot path ----------------------------------------------------
    def factor(self, index):
        """Brush multiplier for a vertex: ``1`` free, ``0`` fully protected."""
        m = self.values[index]
        if self.freeze and m > self.freeze_threshold:
            return 0.0
        return 1.0 - m

    def is_masked(self, index):
        return self.values[index] > 0.0

    def is_frozen(self, index):
        return self.freeze and self.values[index] > self.freeze_threshold

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]

    def __setitem__(self, index, value):
        self.values[index] = _clamp01(float(value))

    def any(self):
        """True when at least one vertex carries a mask."""
        for v in self.values:
            if v > 0.0:
                return True
        return False

    def count(self, threshold=0.0):
        return sum(1 for v in self.values if v > threshold)

    def resize(self, n_vertices, fill=0.0):
        """Grow or shrink to match a mesh whose topology changed."""
        n = int(n_vertices)
        cur = len(self.values)
        if n == cur:
            return self
        if n < cur:
            del self.values[n:]
        else:
            self.values.extend([_clamp01(float(fill))] * (n - cur))
        return self

    def copy(self):
        return VertexMask(values=self.values, freeze=self.freeze,
                          freeze_threshold=self.freeze_threshold)

    # -- editing ---------------------------------------------------------
    def clear(self):
        """Unmask everything.  ``O(V)``."""
        v = self.values
        for i in range(len(v)):
            v[i] = 0.0
        return self

    def fill(self, value=1.0):
        """Set every vertex to ``value``.  ``O(V)``."""
        x = _clamp01(float(value))
        v = self.values
        for i in range(len(v)):
            v[i] = x
        return self

    def invert(self):
        """``m -> 1 - m`` for every vertex.  ``O(V)``."""
        v = self.values
        for i in range(len(v)):
            v[i] = 1.0 - v[i]
        return self

    def paint(self, weights, strength=1.0, mode="add"):
        """Apply ``{index: weight}`` (or an iterable of pairs).

        ``mode`` is ``add`` (paint), ``subtract`` (unpaint) or ``set``.
        ``O(len(weights))``.
        """
        items = weights.items() if hasattr(weights, "items") else weights
        s = float(strength)
        v = self.values
        for i, w in items:
            x = float(w) * s
            if mode == "set":
                v[i] = _clamp01(x)
            elif mode == "subtract":
                v[i] = _clamp01(v[i] - x)
            else:
                v[i] = _clamp01(v[i] + x)
        return self

    def paint_sphere(self, mesh, center, radius, strength=1.0,
                     curve="smooth", mode="add"):
        """Paint the mask with a falloff sphere -- the mask brush.

        ``O(k)`` in the vertices inside the sphere (the spatial index finds
        them), plus the index query itself.
        """
        from .brushes import falloff as _falloff
        idx = mesh.vertices_in_radius(center, radius)
        if not idx:
            return []
        p = mesh.positions
        cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
        r = float(radius)
        weights = {}
        for i in idx:
            o = i * 3
            dx = p[o] - cx
            dy = p[o + 1] - cy
            dz = p[o + 2] - cz
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d >= r:
                continue
            weights[i] = _falloff(curve, d / r)
        self.paint(weights, strength, mode)
        return sorted(weights)

    def blur(self, mesh, iterations=1, amount=1.0):
        """Average each mask value with its one-ring.  ``O(V + E)`` per pass.

        Uses a double buffer, so the result does not depend on the vertex
        order the way an in-place relaxation would.
        """
        a = _clamp01(float(amount))
        if a <= 0.0:
            return self
        off, nbr = mesh.adjacency()
        n = min(len(self.values), len(off) - 1)
        for _ in range(max(0, int(iterations))):
            src = array.array("f", self.values)
            for i in range(n):
                lo = off[i]
                hi = off[i + 1]
                if hi <= lo:
                    continue
                total = 0.0
                for k in range(lo, hi):
                    total += src[nbr[k]]
                mean = total / float(hi - lo)
                self.values[i] = _clamp01(src[i] + (mean - src[i]) * a)
        return self

    def sharpen(self, threshold=0.5):
        """Push every value to 0 or 1.  ``O(V)``."""
        t = float(threshold)
        v = self.values
        for i in range(len(v)):
            v[i] = 1.0 if v[i] > t else 0.0
        return self

    def mask_by_cavity(self, mesh, strength=1.0, invert=False, blur=2,
                       mode="add"):
        """Mask the creases (or, inverted, the ridges).  ``O(V + E)``.

        Cavity is measured as the signed distance from the vertex to its
        one-ring centroid along the vertex normal: positive where the surface
        curves away (a pit), negative on a ridge.  It is normalised by the mean
        edge length so the result does not depend on the mesh scale.
        """
        off, nbr = mesh.adjacency()
        p = mesh.positions
        nrm = mesh.normals()
        scale = mesh.average_edge_length() or 1.0
        n = min(len(self.values), len(off) - 1)
        raw = array.array("f", bytes(4 * n))
        for i in range(n):
            lo = off[i]
            hi = off[i + 1]
            if hi <= lo:
                continue
            sx = sy = sz = 0.0
            for k in range(lo, hi):
                o = nbr[k] * 3
                sx += p[o]
                sy += p[o + 1]
                sz += p[o + 2]
            m = float(hi - lo)
            o = i * 3
            dx = sx / m - p[o]
            dy = sy / m - p[o + 1]
            dz = sz / m - p[o + 2]
            c = (dx * nrm[o] + dy * nrm[o + 1] + dz * nrm[o + 2]) / scale
            if invert:
                c = -c
            raw[i] = _clamp01(c * 2.0)
        tmp = VertexMask(values=raw)
        if blur:
            tmp.blur(mesh, int(blur))
        self.paint(((i, tmp.values[i]) for i in range(n)
                    if tmp.values[i] > 0.0), strength, mode)
        return self

    def mask_indices(self, indices, value=1.0, mode="set"):
        """Mask an explicit set of vertices.  ``O(len(indices))``."""
        return self.paint(((int(i), float(value)) for i in indices), 1.0, mode)

    # -- serialisation ---------------------------------------------------
    def to_bytes(self):
        """Quantised to one byte per vertex -- what the FCXR section holds."""
        return bytes(bytearray(int(_clamp01(v) * 255.0 + 0.5)
                               for v in self.values))

    @classmethod
    def from_bytes(cls, data, freeze=False, freeze_threshold=0.5):
        return cls(values=[b / 255.0 for b in bytearray(data)],
                   freeze=freeze, freeze_threshold=freeze_threshold)

    def to_dict(self):
        return {
            "freeze": self.freeze,
            "freeze_threshold": self.freeze_threshold,
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(values=d.get("values") or [],
                   freeze=bool(d.get("freeze", False)),
                   freeze_threshold=float(d.get("freeze_threshold", 0.5)))

    def __repr__(self):
        return "VertexMask(%d verts, %d masked%s)" % (
            len(self.values), self.count(),
            ", frozen" if self.freeze else "")
