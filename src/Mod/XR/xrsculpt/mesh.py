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
"""The editable sculpt mesh.

A :class:`SculptMesh` is a plain indexed triangle mesh built out of
:mod:`array` buffers, plus the four derived structures a sculpting brush needs
every frame:

============================  ==============================================
derived structure             built by
============================  ==============================================
per vertex normals            :meth:`SculptMesh.normals`
one-ring adjacency (CSR)      :meth:`SculptMesh.adjacency`
vertex-to-face incidence      :meth:`SculptMesh.vertex_faces`
uniform spatial hash grid     :meth:`SculptMesh.grid`
dirty region                  :meth:`SculptMesh.touch`
============================  ==============================================

All of them are *lazy* and *cached*: they are built on first use and
invalidated only by the operation that actually breaks them.  Moving vertices
does not touch the adjacency at all, and it *refreshes* rather than discards
the normals -- only the moved vertices and their one-rings are recomputed,
which is what keeps an inflate or smooth brush independent of the mesh size.
The grid survives small movements and is rebuilt only once the accumulated
drift can change which cell a vertex lives in (see
:attr:`SculptMesh.grid_drift`).

Complexity
----------

``V`` is the vertex count, ``F`` the face count, ``k`` the number of vertices a
query returns and ``C`` the number of grid cells a query sphere overlaps.

=====================================  ====================  ==========
operation                              time                  space
=====================================  ====================  ==========
``__init__`` (buffer copy only)        ``O(V + F)``          ``O(V + F)``
``normals()`` (full rebuild)           ``O(V + F)``          ``O(V)``
``normals()`` (incremental, after dab) ``O(k · valence)``    --
``adjacency()`` (first call, cached)   ``O(F + V)``          ``O(V + F)``
``vertex_faces()`` (first, cached)     ``O(F + V)``          ``O(F)``
``neighbours(i)``                      ``O(deg(i))``         --
``grid()`` (first call / rebuild)      ``O(V)``              ``O(V)``
``vertices_in_radius(c, r)``           ``O(C + candidates)`` ``O(k)``
``vertices_in_radius_bruteforce()``    ``O(V)``              ``O(k)``
``set_vertex`` / ``move_vertex``       ``O(1)`` amortised    --
``bounds()``                           ``O(V)``              ``O(1)``
``average_edge_length()``              ``O(F)``              ``O(1)``
``touch()`` / ``dirty_indices()``      ``O(len(indices))``   ``O(V)``
``copy()``                             ``O(V + F)``          ``O(V + F)``
=====================================  ====================  ==========

The only structure that is ``O(V)`` per *stroke* rather than per *dab* is the
grid rebuild, and it is amortised: a dab moves vertices by a fraction of the
brush radius, so with the default cell size (twice the mean edge length) a
rebuild happens after tens of dabs, not every dab.  Everything the brush does
afterwards is proportional to the few thousand vertices under the cursor, never
to ``V``.

numpy is *optional* and is used in exactly one place -- evaluating the layer
stack in :mod:`xrsculpt.layers`, where the vectorised form performs the same
float64 multiply-add per vertex in the same order and therefore agrees with the
scalar loop bit for bit (``Tests/test_sculpt_layers.py`` asserts it).
:func:`set_use_numpy` ``(False)`` forces the pure Python path.  The normal
computation deliberately stays scalar: a vectorised accumulation sums the
per-face normals in a different order and lands one ULP away, and a normal that
depends on whether numpy happens to be installed would make every brush result
depend on it too.

Nothing here imports FreeCAD, ``Mesh``, ``Part`` or ``pivy.coin`` at module
scope (ARCHITECTURE.md §6); the conversions do it inside the function.
"""

import array
import math

__all__ = [
    "SculptMesh",
    "SpatialGrid",
    "have_numpy",
    "set_use_numpy",
    "use_numpy",
    "make_grid_mesh",
    "make_icosphere",
]

_EPS = 1e-12


# --------------------------------------------------------------------------
# optional numpy acceleration
# --------------------------------------------------------------------------

_NUMPY = [None]          # None = not probed yet, False = not available
_USE_NUMPY = [True]


def _numpy():
    """Return the numpy module or ``None``; probed lazily, once."""
    if _NUMPY[0] is None:
        try:
            import numpy as _np  # noqa: F401
            _NUMPY[0] = _np
        except Exception:
            _NUMPY[0] = False
    return _NUMPY[0] or None


def have_numpy():
    """True when numpy is importable in this interpreter."""
    return _numpy() is not None


def use_numpy():
    """True when the numpy paths are both available and enabled."""
    return _USE_NUMPY[0] and _numpy() is not None


def set_use_numpy(enabled):
    """Enable/disable the numpy path; returns the previous setting."""
    old = _USE_NUMPY[0]
    _USE_NUMPY[0] = bool(enabled)
    return old


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def flatten3(values, what="values"):
    """Accept a flat sequence or a sequence of triples; return a flat list."""
    out = []
    for item in values:
        if isinstance(item, (list, tuple)):
            if len(item) != 3:
                raise ValueError("%s: element with %d components, expected 3"
                                 % (what, len(item)))
            out.extend(item)
        elif hasattr(item, "x") and hasattr(item, "y"):
            # FreeCAD Vector duck typing, never imported here
            out.extend((item.x, item.y, getattr(item, "z", 0.0)))
        else:
            out.append(item)
    if len(out) % 3:
        raise ValueError("%s: %d values are not a multiple of 3"
                         % (what, len(out)))
    return out


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# --------------------------------------------------------------------------
# spatial index
# --------------------------------------------------------------------------

class SpatialGrid(object):
    """Uniform spatial hash over the vertices of a mesh.

    A dict keyed by integer cell coordinates; no bounding box is stored, so a
    vertex dragged far outside the original extent still lands in a valid cell
    instead of being clamped into the border one (which is what turns a naive
    grid into a linear scan after a few grab strokes).

    Build is ``O(V)``.  :meth:`query` visits the cells overlapping the query
    sphere's bounding box and tests their members, so it is ``O(C + n)`` for
    ``C`` cells holding ``n`` candidates.  Results are returned **sorted**, so
    they do not depend on the hash order.
    """

    __slots__ = ("cell", "_inv", "_cells", "_count")

    def __init__(self, verts, cell):
        self.cell = float(cell) if cell and cell > 0.0 else 1.0
        self._inv = 1.0 / self.cell
        self._cells = {}
        self._count = len(verts) // 3
        self.rebuild(verts)

    def rebuild(self, verts):
        cells = {}
        inv = self._inv
        floor = math.floor
        n = len(verts) // 3
        for i in range(n):
            o = i * 3
            key = (int(floor(verts[o] * inv)),
                   int(floor(verts[o + 1] * inv)),
                   int(floor(verts[o + 2] * inv)))
            bucket = cells.get(key)
            if bucket is None:
                cells[key] = [i]
            else:
                bucket.append(i)
        self._cells = cells
        self._count = n
        return self

    def __len__(self):
        return self._count

    @property
    def cell_count(self):
        return len(self._cells)

    def query(self, verts, center, radius):
        """Sorted list of vertex indices within ``radius`` of ``center``."""
        if radius <= 0.0:
            return []
        cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
        inv = self._inv
        floor = math.floor
        x0 = int(floor((cx - radius) * inv))
        x1 = int(floor((cx + radius) * inv))
        y0 = int(floor((cy - radius) * inv))
        y1 = int(floor((cy + radius) * inv))
        z0 = int(floor((cz - radius) * inv))
        z1 = int(floor((cz + radius) * inv))
        r2 = radius * radius
        cells = self._cells
        out = []
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                for gz in range(z0, z1 + 1):
                    bucket = cells.get((gx, gy, gz))
                    if not bucket:
                        continue
                    for i in bucket:
                        o = i * 3
                        dx = verts[o] - cx
                        dy = verts[o + 1] - cy
                        dz = verts[o + 2] - cz
                        if dx * dx + dy * dy + dz * dz <= r2:
                            out.append(i)
        out.sort()
        return out

    def __repr__(self):
        return "SpatialGrid(cell=%.4g, %d cells, %d vertices)" % (
            self.cell, len(self._cells), self._count)


# --------------------------------------------------------------------------
# the mesh
# --------------------------------------------------------------------------

class SculptMesh(object):
    """An editable indexed triangle mesh.

    ``positions`` is a flat ``array('d')`` of ``3 * n_vertices`` doubles and
    ``faces`` a flat ``array('i')`` of ``3 * n_faces`` indices; both are public
    and may be written in place as long as :meth:`touch` (or
    :meth:`invalidate`) is called afterwards.
    """

    __slots__ = ("positions", "faces", "name", "_normals", "_adj_off",
                 "_adj_nbr", "_vf_off", "_vf_idx", "_grid", "_grid_drift",
                 "_dirty", "_dirty_all", "_ndirty", "_ndirty_all",
                 "_avg_edge")

    def __init__(self, vertices=None, faces=None, name="Sculpt"):
        pos = flatten3(vertices or (), "vertices")
        self.positions = array.array("d", [float(v) for v in pos])
        idx = []
        for f in (faces or ()):
            if isinstance(f, (list, tuple)):
                if len(f) != 3:
                    raise ValueError("faces: only triangles are supported")
                idx.extend(f)
            else:
                idx.append(f)
        if len(idx) % 3:
            raise ValueError("faces: %d indices are not a multiple of 3"
                             % len(idx))
        nv = len(self.positions) // 3
        for i in idx:
            if not (0 <= int(i) < nv):
                raise ValueError("face index %r out of range (%d vertices)"
                                 % (i, nv))
        self.faces = array.array("i", [int(i) for i in idx])
        self.name = str(name)
        self._normals = None
        self._adj_off = None
        self._adj_nbr = None
        self._vf_off = None
        self._vf_idx = None
        self._grid = None
        self._grid_drift = 0.0
        self._dirty = set()
        self._dirty_all = False
        self._ndirty = set()
        self._ndirty_all = True
        self._avg_edge = None

    # -- basics ----------------------------------------------------------
    @property
    def n_vertices(self):
        return len(self.positions) // 3

    @property
    def n_faces(self):
        return len(self.faces) // 3

    def __len__(self):
        return len(self.positions) // 3

    def vertex(self, i):
        o = i * 3
        p = self.positions
        return (p[o], p[o + 1], p[o + 2])

    def face(self, f):
        o = f * 3
        t = self.faces
        return (t[o], t[o + 1], t[o + 2])

    def set_vertex(self, i, p):
        o = i * 3
        self.positions[o] = float(p[0])
        self.positions[o + 1] = float(p[1])
        self.positions[o + 2] = float(p[2])
        self.touch((i,))

    def move_vertex(self, i, d):
        o = i * 3
        self.positions[o] += float(d[0])
        self.positions[o + 1] += float(d[1])
        self.positions[o + 2] += float(d[2])
        self.touch((i,))

    def copy(self):
        m = SculptMesh.__new__(SculptMesh)
        m.positions = array.array("d", self.positions)
        m.faces = array.array("i", self.faces)
        m.name = self.name
        m._normals = None
        m._adj_off = self._adj_off
        m._adj_nbr = self._adj_nbr
        m._vf_off = self._vf_off
        m._vf_idx = self._vf_idx
        m._grid = None
        m._grid_drift = 0.0
        m._dirty = set()
        m._dirty_all = True
        m._ndirty = set()
        m._ndirty_all = True
        m._avg_edge = self._avg_edge
        return m

    def __repr__(self):
        return "SculptMesh(%r, %d verts, %d faces)" % (
            self.name, self.n_vertices, self.n_faces)

    # -- invalidation / dirty tracking -----------------------------------
    def touch(self, indices=None, drift=0.0):
        """Mark vertices as moved.

        ``indices`` of ``None`` means "everything".  ``drift`` is the largest
        distance any of them moved, used to decide when the spatial grid has
        to be rebuilt.

        Moved vertices are remembered rather than the normal cache simply
        being thrown away, so the next :meth:`normals` call only recomputes
        the vertices whose normal can actually have changed.
        """
        if drift:
            self._grid_drift += abs(float(drift))
        if indices is None:
            self._normals = None
            self._dirty_all = True
            self._dirty.clear()
            self._ndirty_all = True
            self._ndirty.clear()
            self._grid_drift = float("inf")
            return self
        idx = list(indices)
        if not self._dirty_all:
            self._dirty.update(idx)
        if not self._ndirty_all:
            self._ndirty.update(idx)
        return self

    def invalidate(self, topology=False):
        """Drop cached derived data.  ``topology`` also drops the adjacency."""
        self._normals = None
        self._grid = None
        self._grid_drift = 0.0
        self._avg_edge = None
        if topology:
            self._adj_off = None
            self._adj_nbr = None
            self._vf_off = None
            self._vf_idx = None
        self._dirty_all = True
        self._dirty.clear()
        self._ndirty_all = True
        self._ndirty.clear()
        return self

    def dirty_indices(self):
        """Sorted vertex indices touched since the last :meth:`clear_dirty`."""
        if self._dirty_all:
            return list(range(self.n_vertices))
        return sorted(self._dirty)

    @property
    def dirty_all(self):
        return self._dirty_all

    def dirty_bounds(self):
        """Axis aligned bounds of the dirty region, or ``None``."""
        idx = self.dirty_indices()
        if not idx:
            return None
        p = self.positions
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for i in idx:
            o = i * 3
            for c in range(3):
                v = p[o + c]
                if v < lo[c]:
                    lo[c] = v
                if v > hi[c]:
                    hi[c] = v
        return (tuple(lo), tuple(hi))

    def clear_dirty(self):
        self._dirty.clear()
        self._dirty_all = False
        return self

    @property
    def grid_drift(self):
        """Accumulated movement since the spatial grid was last rebuilt."""
        return self._grid_drift

    # -- normals ---------------------------------------------------------
    def normals(self):
        """Per-vertex normals, area weighted, as a flat ``array('d')``.

        Cached, and refreshed **incrementally**.  A full rebuild is
        ``O(V + F)``; after a brush dab only the moved vertices and their
        one-rings can have changed, so the refresh is ``O(k · valence)`` in
        the ``k`` vertices the dab touched.  That is the difference between
        an inflate brush costing a fraction of a millisecond and costing most
        of a second on a 160k vertex mesh, because a dab invalidates the
        cache every single time.

        The incremental result is *identical* to the full rebuild, not merely
        close: the vertex-face table is built by scanning the face list in
        order, so a vertex accumulates its incident face normals in the same
        order either way, and float addition is only non-associative when the
        order differs.

        The computation stays pure Python on purpose.  A vectorised
        accumulation (``np.add.at`` over the face table) sums the per-face
        normals in a different order and lands one ULP away from the scalar
        loop, and a normal that depended on whether numpy happens to be
        installed would make every brush result depend on it too.
        """
        if self._normals is None or self._ndirty_all:
            self._normals = self._normals_scalar()
            self._ndirty.clear()
            self._ndirty_all = False
            return self._normals
        if self._ndirty:
            dirty = self._ndirty
            self._ndirty = set()
            if len(dirty) * 8 >= self.n_vertices:
                self._normals = self._normals_scalar()
            else:
                self._refresh_normals(dirty)
        return self._normals

    def vertex_faces(self):
        """Vertex-to-face incidence in CSR form: ``(offsets, faces)``.

        Built once per topology, ``O(F + V)``, with each vertex's face list in
        ascending face order -- which is what makes the incremental normal
        refresh bit-identical to a full rebuild.
        """
        if self._vf_off is not None:
            return (self._vf_off, self._vf_idx)
        nv = self.n_vertices
        t = self.faces
        counts = array.array("i", bytes(4 * (nv + 1)))
        for i in t:
            counts[i] += 1
        off = array.array("i", bytes(4 * (nv + 1)))
        total = 0
        for i in range(nv):
            off[i] = total
            total += counts[i]
        off[nv] = total
        cursor = array.array("i", off[:nv])
        idx = array.array("i", bytes(4 * total))
        for f in range(len(t) // 3):
            for k in range(3):
                v = t[f * 3 + k]
                idx[cursor[v]] = f
                cursor[v] += 1
        self._vf_off = off
        self._vf_idx = idx
        return (off, idx)

    def _refresh_normals(self, dirty):
        """Recompute the normals of ``dirty`` and their one-ring neighbours."""
        adj_off, adj_nbr = self.adjacency()
        affected = set()
        nv = self.n_vertices
        for i in dirty:
            if not (0 <= i < nv):
                continue
            affected.add(i)
            affected.update(adj_nbr[adj_off[i]:adj_off[i + 1]])
        vf_off, vf_idx = self.vertex_faces()
        p = self.positions
        t = self.faces
        out = self._normals
        for i in affected:
            sx = sy = sz = 0.0
            for k in range(vf_off[i], vf_off[i + 1]):
                f = vf_idx[k] * 3
                ia = t[f] * 3
                ib = t[f + 1] * 3
                ic = t[f + 2] * 3
                ax = p[ia]
                ay = p[ia + 1]
                az = p[ia + 2]
                ux = p[ib] - ax
                uy = p[ib + 1] - ay
                uz = p[ib + 2] - az
                vx = p[ic] - ax
                vy = p[ic + 1] - ay
                vz = p[ic + 2] - az
                sx += uy * vz - uz * vy
                sy += uz * vx - ux * vz
                sz += ux * vy - uy * vx
            o = i * 3
            ln = math.sqrt(sx * sx + sy * sy + sz * sz)
            if ln > _EPS:
                out[o] = sx / ln
                out[o + 1] = sy / ln
                out[o + 2] = sz / ln
            else:
                out[o] = 0.0
                out[o + 1] = 0.0
                out[o + 2] = 1.0
        return out

    def vertex_normal(self, i):
        n = self.normals()
        o = i * 3
        return (n[o], n[o + 1], n[o + 2])

    def _normals_scalar(self):
        p = self.positions
        t = self.faces
        acc = array.array("d", bytes(len(p) * 8))
        for f in range(0, len(t), 3):
            ia = t[f] * 3
            ib = t[f + 1] * 3
            ic = t[f + 2] * 3
            ax = p[ia]
            ay = p[ia + 1]
            az = p[ia + 2]
            ux = p[ib] - ax
            uy = p[ib + 1] - ay
            uz = p[ib + 2] - az
            vx = p[ic] - ax
            vy = p[ic + 1] - ay
            vz = p[ic + 2] - az
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            acc[ia] += nx
            acc[ia + 1] += ny
            acc[ia + 2] += nz
            acc[ib] += nx
            acc[ib + 1] += ny
            acc[ib + 2] += nz
            acc[ic] += nx
            acc[ic + 1] += ny
            acc[ic + 2] += nz
        for i in range(0, len(acc), 3):
            x = acc[i]
            y = acc[i + 1]
            z = acc[i + 2]
            ln = math.sqrt(x * x + y * y + z * z)
            if ln > _EPS:
                acc[i] = x / ln
                acc[i + 1] = y / ln
                acc[i + 2] = z / ln
            else:
                acc[i] = 0.0
                acc[i + 1] = 0.0
                acc[i + 2] = 1.0
        return acc

    # -- adjacency -------------------------------------------------------
    def adjacency(self):
        """One-ring neighbours in CSR form: ``(offsets, neighbours)``.

        ``neighbours[offsets[i]:offsets[i + 1]]`` are the vertices sharing an
        edge with ``i``, sorted and de-duplicated.  Built once per topology,
        ``O(F + V)``.
        """
        if self._adj_off is not None:
            return (self._adj_off, self._adj_nbr)
        nv = self.n_vertices
        t = self.faces
        sets = [set() for _ in range(nv)]
        for f in range(0, len(t), 3):
            a = t[f]
            b = t[f + 1]
            c = t[f + 2]
            sets[a].add(b)
            sets[a].add(c)
            sets[b].add(a)
            sets[b].add(c)
            sets[c].add(a)
            sets[c].add(b)
        off = array.array("i", bytes(4 * (nv + 1)))
        total = 0
        for i in range(nv):
            off[i] = total
            total += len(sets[i])
        off[nv] = total
        nbr = array.array("i", bytes(4 * total))
        k = 0
        for i in range(nv):
            for j in sorted(sets[i]):
                nbr[k] = j
                k += 1
        self._adj_off = off
        self._adj_nbr = nbr
        return (off, nbr)

    def neighbours(self, i):
        """The one-ring of vertex ``i`` as a tuple.  ``O(deg(i))``."""
        off, nbr = self.adjacency()
        return tuple(nbr[off[i]:off[i + 1]])

    def degree(self, i):
        off, _ = self.adjacency()
        return off[i + 1] - off[i]

    def one_ring_centroid(self, i):
        """Mean of the one-ring, or the vertex itself when it has none."""
        off, nbr = self.adjacency()
        a = off[i]
        b = off[i + 1]
        if b <= a:
            return self.vertex(i)
        p = self.positions
        sx = sy = sz = 0.0
        for k in range(a, b):
            o = nbr[k] * 3
            sx += p[o]
            sy += p[o + 1]
            sz += p[o + 2]
        n = float(b - a)
        return (sx / n, sy / n, sz / n)

    # -- spatial index ---------------------------------------------------
    def default_cell_size(self):
        e = self.average_edge_length()
        if e <= 0.0:
            lo, hi = self.bounds()
            span = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            e = span / 32.0 if span > 0.0 else 1.0
        return max(e * 2.0, 1e-9)

    def grid(self, cell=None):
        """The spatial hash, rebuilt when it has drifted out of date."""
        g = self._grid
        if g is None:
            g = SpatialGrid(self.positions,
                            self.default_cell_size() if cell is None
                            else cell)
            self._grid = g
            self._grid_drift = 0.0
        elif self._grid_drift > g.cell * 0.5:
            g.rebuild(self.positions)
            self._grid_drift = 0.0
        elif len(g) != self.n_vertices:
            g.rebuild(self.positions)
            self._grid_drift = 0.0
        return g

    def refresh_index(self):
        """Force an immediate grid rebuild."""
        self._grid = None
        self._grid_drift = 0.0
        return self.grid()

    def vertices_in_radius(self, center, radius):
        """Sorted vertex indices inside the sphere.  ``O(C + candidates)``."""
        return self.grid().query(self.positions, center, radius)

    def vertices_in_radius_bruteforce(self, center, radius):
        """Reference version of :meth:`vertices_in_radius`.  ``O(V)``."""
        cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
        r2 = float(radius) * float(radius)
        p = self.positions
        out = []
        for i in range(len(p) // 3):
            o = i * 3
            dx = p[o] - cx
            dy = p[o + 1] - cy
            dz = p[o + 2] - cz
            if dx * dx + dy * dy + dz * dz <= r2:
                out.append(i)
        return out

    # -- measurements ----------------------------------------------------
    def bounds(self):
        """``((minx, miny, minz), (maxx, maxy, maxz))``.  ``O(V)``."""
        p = self.positions
        if not len(p):
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = [p[0], p[1], p[2]]
        hi = [p[0], p[1], p[2]]
        for i in range(3, len(p), 3):
            for c in range(3):
                v = p[i + c]
                if v < lo[c]:
                    lo[c] = v
                if v > hi[c]:
                    hi[c] = v
        return (tuple(lo), tuple(hi))

    def centroid(self):
        p = self.positions
        n = len(p) // 3
        if not n:
            return (0.0, 0.0, 0.0)
        sx = sy = sz = 0.0
        for i in range(0, len(p), 3):
            sx += p[i]
            sy += p[i + 1]
            sz += p[i + 2]
        return (sx / n, sy / n, sz / n)

    def average_edge_length(self):
        """Mean length of the three edges of every face.  ``O(F)``, cached."""
        if self._avg_edge is not None:
            return self._avg_edge
        p = self.positions
        t = self.faces
        total = 0.0
        count = 0
        for f in range(0, len(t), 3):
            a = t[f] * 3
            b = t[f + 1] * 3
            c = t[f + 2] * 3
            for (i, j) in ((a, b), (b, c), (c, a)):
                dx = p[i] - p[j]
                dy = p[i + 1] - p[j + 1]
                dz = p[i + 2] - p[j + 2]
                total += math.sqrt(dx * dx + dy * dy + dz * dz)
                count += 1
        self._avg_edge = (total / count) if count else 0.0
        return self._avg_edge

    def volume(self):
        """Signed volume of the closed mesh (divergence theorem).  ``O(F)``."""
        p = self.positions
        t = self.faces
        acc = 0.0
        for f in range(0, len(t), 3):
            a = t[f] * 3
            b = t[f + 1] * 3
            c = t[f + 2] * 3
            ax, ay, az = p[a], p[a + 1], p[a + 2]
            bx, by, bz = p[b], p[b + 1], p[b + 2]
            cx, cy, cz = p[c], p[c + 1], p[c + 2]
            acc += (ax * (by * cz - bz * cy)
                    - ay * (bx * cz - bz * cx)
                    + az * (bx * cy - by * cx))
        return acc / 6.0

    # -- topology helpers ------------------------------------------------
    def edge_face_count(self):
        """``{(lo, hi): number_of_incident_faces}``.  ``O(F)``."""
        t = self.faces
        counts = {}
        for f in range(0, len(t), 3):
            a = t[f]
            b = t[f + 1]
            c = t[f + 2]
            for (i, j) in ((a, b), (b, c), (c, a)):
                key = (i, j) if i < j else (j, i)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def boundary_edges(self):
        """Edges with exactly one incident face."""
        return [e for e, n in self.edge_face_count().items() if n == 1]

    def is_manifold(self):
        """True when no edge is shared by more than two faces."""
        for n in self.edge_face_count().values():
            if n > 2:
                return False
        return True

    def is_closed(self):
        """True when every edge has exactly two incident faces."""
        for n in self.edge_face_count().values():
            if n != 2:
                return False
        return bool(self.faces)

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "name": self.name,
            "positions": list(self.positions),
            "indices": list(self.faces),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("positions") or [], d.get("indices") or [],
                   d.get("name", "Sculpt"))

    # -- FreeCAD conversion (lazy imports, ARCHITECTURE.md §6) -----------
    @classmethod
    def from_mesh_object(cls, mesh_object, name=None):
        """Build from a FreeCAD ``Mesh.Mesh`` (or a document object with one).

        Accepts the ``Mesh`` object itself, a document object exposing
        ``.Mesh``, or anything with a ``Topology`` pair.  ``O(V + F)``.
        """
        obj = mesh_object
        label = name
        if hasattr(obj, "Mesh") and not hasattr(obj, "Topology"):
            if label is None:
                label = getattr(obj, "Name", None) \
                    or getattr(obj, "Label", None)
            obj = obj.Mesh
        topo = getattr(obj, "Topology", None)
        if topo is None:
            raise TypeError("not a FreeCAD Mesh: %r" % (mesh_object,))
        points, facets = topo[0], topo[1]
        verts = []
        for p in points:
            verts.extend((getattr(p, "x", None) if hasattr(p, "x") else p[0],
                          getattr(p, "y", None) if hasattr(p, "y") else p[1],
                          getattr(p, "z", None) if hasattr(p, "z") else p[2]))
        faces = []
        for f in facets:
            faces.extend((f[0], f[1], f[2]))
        return cls(verts, faces, label or "Sculpt")

    def to_mesh_object(self):
        """Return a FreeCAD ``Mesh.Mesh`` with the same triangles."""
        import Mesh  # noqa: F401  (lazy on purpose)
        facets = []
        p = self.positions
        t = self.faces
        for f in range(0, len(t), 3):
            tri = []
            for k in range(3):
                o = t[f + k] * 3
                tri.append((p[o], p[o + 1], p[o + 2]))
            facets.append(tri)
        return Mesh.Mesh(facets)

    @classmethod
    def from_shape(cls, shape, deflection=0.1, name=None):
        """Tessellate a ``Part`` shape (or an object with a ``.Shape``)."""
        obj = shape
        label = name
        if hasattr(obj, "Shape") and not hasattr(obj, "tessellate"):
            if label is None:
                label = getattr(obj, "Name", None) \
                    or getattr(obj, "Label", None)
            obj = obj.Shape
        if not hasattr(obj, "tessellate"):
            raise TypeError("not a Part shape: %r" % (shape,))
        points, facets = obj.tessellate(float(deflection))
        verts = []
        for p in points:
            verts.extend((p.x, p.y, p.z) if hasattr(p, "x")
                         else (p[0], p[1], p[2]))
        faces = []
        for f in facets:
            faces.extend((f[0], f[1], f[2]))
        return cls(verts, faces, label or "Sculpt")

    def to_shape(self):
        """Build a ``Part`` shell out of the triangles (expensive)."""
        import Part  # noqa: F401  (lazy on purpose)
        p = self.positions
        t = self.faces
        faces = []
        for f in range(0, len(t), 3):
            pts = []
            for k in range(3):
                o = t[f + k] * 3
                pts.append(Part.Vertex(p[o], p[o + 1], p[o + 2]).Point)
            pts.append(pts[0])
            wire = Part.makePolygon(pts)
            faces.append(Part.Face(wire))
        return Part.makeShell(faces)

    def write_back(self, mesh_object):
        """Push the current positions into an existing FreeCAD Mesh feature."""
        mesh_object.Mesh = self.to_mesh_object()
        return mesh_object


# --------------------------------------------------------------------------
# primitive builders (tests, previews, and "new sculpt" in the session)
# --------------------------------------------------------------------------

def make_grid_mesh(nx=8, ny=8, size=1.0, z=0.0, center=True):
    """A flat triangulated ``nx`` x ``ny`` quad grid in the XY plane.

    The vertex list is exactly mirror symmetric about ``x = 0`` and ``y = 0``
    when ``center`` is true and the subdivision count is even, which is what
    makes the symmetry tests exact rather than approximate.
    """
    nx = max(1, int(nx))
    ny = max(1, int(ny))
    step = float(size)
    ox = -0.5 * nx * step if center else 0.0
    oy = -0.5 * ny * step if center else 0.0
    verts = []
    for iy in range(ny + 1):
        for ix in range(nx + 1):
            verts.extend((ox + ix * step, oy + iy * step, float(z)))
    faces = []
    row = nx + 1
    for iy in range(ny):
        for ix in range(nx):
            a = iy * row + ix
            b = a + 1
            c = a + row
            d = c + 1
            faces.extend((a, b, d, a, d, c))
    return SculptMesh(verts, faces, "Grid")


def make_icosphere(subdivisions=2, radius=1.0):
    """A closed, manifold icosphere -- the standard sculpting test body."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    base = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
            (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
            (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
             (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
             (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    verts = [list(v) for v in base]

    def _mid(cache, a, b):
        key = (a, b) if a < b else (b, a)
        hit = cache.get(key)
        if hit is not None:
            return hit
        pa = verts[a]
        pb = verts[b]
        verts.append([(pa[0] + pb[0]) * 0.5, (pa[1] + pb[1]) * 0.5,
                      (pa[2] + pb[2]) * 0.5])
        cache[key] = len(verts) - 1
        return cache[key]

    for _ in range(max(0, int(subdivisions))):
        cache = {}
        out = []
        for (a, b, c) in faces:
            ab = _mid(cache, a, b)
            bc = _mid(cache, b, c)
            ca = _mid(cache, c, a)
            out.extend([(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)])
        faces = out

    flat = []
    r = float(radius)
    for v in verts:
        ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if ln < _EPS:
            ln = 1.0
        flat.extend((v[0] / ln * r, v[1] / ln * r, v[2] / ln * r))
    idx = []
    for f in faces:
        idx.extend(f)
    return SculptMesh(flat, idx, "Icosphere")
