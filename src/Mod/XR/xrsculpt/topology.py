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
"""Adaptive detail: subdivision, decimation and edge-length remeshing.

Every operation here returns a **new** mesh plus a :class:`TopologyMap`
describing how the old vertices became the new ones.  Nothing is done in
place, because a sculpt layer is indexed by vertex and a topology change that
quietly renumbered the vertices would scramble every layer on the stack.  The
map is what keeps the layers, the base positions and the mask in step -- see
:meth:`TopologyMap.remap_positions`, :meth:`TopologyMap.remap_layer` and
:meth:`TopologyMap.remap_mask`.

Subdivision is *conforming*: an edge is either split for both of the triangles
that share it or for neither, and each triangle is re-triangulated according to
how many of its three edges were split (1 -> 2, 2 -> 3, 3 -> 4 triangles).  No
T-junctions are created, so the mesh stays manifold, keeps its winding, and
gains no holes -- the boundary edge count is unchanged by a split and the
Euler characteristic is preserved.

Honest note about UVs and other attributes
------------------------------------------

**Nothing in this module preserves UVs.**

* :func:`subdivide_uniform` and :func:`subdivide_in_radius` *could* carry UVs
  through: every new vertex is the midpoint of exactly two old ones, so a
  linear interpolation of their UVs is correct as long as the two are not on
  opposite sides of a UV seam -- and at a seam it is wrong in the usual,
  visible way.  The mesh here has no UV channel at all, so the question is
  moot until one is added; when it is, midpoint interpolation plus explicit
  seam handling is the thing to write.
* :func:`collapse_short_edges` and :func:`remesh` **cannot** preserve UVs in
  any meaningful sense.  A collapse merges two vertices that may carry
  different UVs, and remeshing invents vertices with no correspondence in the
  original parameterisation at all.  A mesh that has been remeshed needs its
  texture re-projected or re-unwrapped; there is no fixing it up afterwards.

The same caveat applies to any per-vertex data the caller keeps outside the
layer stack.  Sculpt layers and masks *are* remapped, because the map carries
each new vertex's parents and both quantities interpolate linearly (which is
exactly why a subdivided sculpt evaluates to the same surface: the displacement
of a midpoint is the mean of its parents' displacements, so the new vertex
lands precisely on the segment the two old ones already spanned).

Complexity
----------

``split_edges`` is ``O(V + F)``; ``subdivide_uniform`` is ``O(V + F)`` and
multiplies the face count by four.  ``collapse_short_edges`` is
``O(E log E + V + F)`` -- the log comes from sorting the candidate edges by
length so the shortest go first.  ``remesh`` is ``iterations`` rounds of both.
"""

import array
import math

from .layers import SculptLayer
from .mesh import SculptMesh

__all__ = [
    "TopologyMap",
    "collapse_short_edges",
    "remesh",
    "split_edges",
    "subdivide_in_radius",
    "subdivide_uniform",
]

_EPS = 1e-12


# --------------------------------------------------------------------------
# the map from old vertices to new ones
# --------------------------------------------------------------------------

class TopologyMap(object):
    """How the vertices of a mesh survived a topology change.

    ``old_to_new[i]`` is where old vertex ``i`` went, or ``-1`` if it was
    collapsed away entirely.  ``created`` lists ``(new_index, pa, pb)`` for
    every vertex the operation invented, in creation order, with the parents
    given in the *new* index space so a single forward pass resolves them.
    ``merged`` lists ``(target_new_index, [old_indices...])`` for collapses.
    """

    __slots__ = ("old_to_new", "created", "merged", "old_count", "new_count")

    def __init__(self, old_count, new_count=0):
        self.old_to_new = [-1] * int(old_count)
        self.created = []
        self.merged = {}
        self.old_count = int(old_count)
        self.new_count = int(new_count)

    # -- generic remapping ----------------------------------------------
    def remap_positions(self, old_positions):
        """Carry a flat ``3 * V`` buffer through the change.

        Surviving vertices keep their value, created vertices get the mean of
        their parents and merged vertices get the mean of the vertices that
        merged into them -- exactly the rule the geometry itself follows, which
        is what makes a subdivided sculpt evaluate to the same surface.
        """
        out = array.array("d", bytes(24 * self.new_count))
        counts = [0] * self.new_count
        for old, new in enumerate(self.old_to_new):
            if new < 0:
                continue
            o = old * 3
            n = new * 3
            out[n] += old_positions[o]
            out[n + 1] += old_positions[o + 1]
            out[n + 2] += old_positions[o + 2]
            counts[new] += 1
        for new in range(self.new_count):
            c = counts[new]
            if c > 1:
                n = new * 3
                out[n] /= c
                out[n + 1] /= c
                out[n + 2] /= c
        for new, pa, pb in self.created:
            n = new * 3
            a = pa * 3
            b = pb * 3
            out[n] = (out[a] + out[b]) * 0.5
            out[n + 1] = (out[a + 1] + out[b + 1]) * 0.5
            out[n + 2] = (out[a + 2] + out[b + 2]) * 0.5
        return out

    def remap_layer(self, layer):
        """A copy of ``layer`` indexed by the new vertex numbering.

        Sparse in, sparse out: a created vertex only gains an entry when at
        least one of its parents had one.
        """
        out = SculptLayer(layer.name, layer.weight, layer.visible,
                          layer.locked, layer.blend, layer_id=layer.id)
        acc = {}
        for old, value in layer.sorted_items():
            if old >= self.old_count:
                continue
            new = self.old_to_new[old]
            if new < 0:
                continue
            hit = acc.get(new)
            if hit is None:
                acc[new] = [value[0], value[1], value[2], 1]
            else:
                hit[0] += value[0]
                hit[1] += value[1]
                hit[2] += value[2]
                hit[3] += 1
        for new, v in acc.items():
            c = float(v[3])
            out.set(new, (v[0] / c, v[1] / c, v[2] / c))
        for new, pa, pb in self.created:
            a = out.get(pa) if pa in out else None
            b = out.get(pb) if pb in out else None
            if a is None and b is None:
                continue
            a = a or (0.0, 0.0, 0.0)
            b = b or (0.0, 0.0, 0.0)
            out.set(new, ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5,
                          (a[2] + b[2]) * 0.5))
        return out

    def remap_stack(self, stack):
        """Rebuild a whole :class:`~xrsculpt.layers.LayerStack` in place."""
        stack.base = self.remap_positions(stack.base)
        stack.layers = [self.remap_layer(l) for l in stack.layers]
        if stack.active_index >= len(stack.layers):
            stack.active_index = len(stack.layers) - 1
        return stack

    def remap_mask(self, mask):
        """Carry a :class:`~xrsculpt.masking.VertexMask` across the change."""
        values = [0.0] * self.new_count
        counts = [0] * self.new_count
        for old, new in enumerate(self.old_to_new):
            if new < 0 or old >= len(mask.values):
                continue
            values[new] += mask.values[old]
            counts[new] += 1
        for i in range(self.new_count):
            if counts[i] > 1:
                values[i] /= counts[i]
        for new, pa, pb in self.created:
            values[new] = (values[pa] + values[pb]) * 0.5
        mask.values = array.array("f", values)
        return mask

    def __repr__(self):
        return "TopologyMap(%d -> %d verts, %d created)" % (
            self.old_count, self.new_count, len(self.created))


# --------------------------------------------------------------------------
# subdivision
# --------------------------------------------------------------------------

def _edge_key(a, b):
    return (a, b) if a < b else (b, a)


def split_edges(mesh, edges):
    """Split the given edges, re-triangulating the faces around them.

    ``edges`` is an iterable of ``(a, b)`` vertex pairs in any order.  Returns
    ``(new_mesh, TopologyMap)``.  Conforming, so the result is still manifold,
    still wound the same way, and has the same boundary.  ``O(V + F)``.
    """
    wanted = set(_edge_key(int(a), int(b)) for a, b in edges)
    nv = mesh.n_vertices
    if not wanted:
        return (mesh.copy(), _identity_map(nv))

    positions = list(mesh.positions)
    topo = TopologyMap(nv)
    topo.old_to_new = list(range(nv))
    mid = {}
    for key in sorted(wanted):
        a, b = key
        if not (0 <= a < nv and 0 <= b < nv):
            continue
        new = len(positions) // 3
        ao = a * 3
        bo = b * 3
        positions.extend(((positions[ao] + positions[bo]) * 0.5,
                          (positions[ao + 1] + positions[bo + 1]) * 0.5,
                          (positions[ao + 2] + positions[bo + 2]) * 0.5))
        mid[key] = new
        topo.created.append((new, a, b))
    topo.new_count = len(positions) // 3

    faces = []
    t = mesh.faces
    for f in range(0, len(t), 3):
        a = t[f]
        b = t[f + 1]
        c = t[f + 2]
        m_ab = mid.get(_edge_key(a, b))
        m_bc = mid.get(_edge_key(b, c))
        m_ca = mid.get(_edge_key(c, a))
        # rotate the triangle so the split pattern lands in a canonical slot
        rot = 0
        while rot < 3:
            pattern = ((m_ab is not None) << 2) | ((m_bc is not None) << 1) \
                | (m_ca is not None)
            if pattern in (0b000, 0b100, 0b110, 0b111):
                break
            a, b, c = b, c, a
            m_ab, m_bc, m_ca = m_bc, m_ca, m_ab
            rot += 1
        if m_ab is None and m_bc is None and m_ca is None:
            faces.extend((a, b, c))
        elif m_bc is None and m_ca is None:
            faces.extend((a, m_ab, c, m_ab, b, c))
        elif m_ca is None:
            faces.extend((b, m_bc, m_ab, a, m_ab, m_bc, a, m_bc, c))
        else:
            faces.extend((a, m_ab, m_ca, b, m_bc, m_ab, c, m_ca, m_bc,
                          m_ab, m_bc, m_ca))
    out = SculptMesh(positions, faces, mesh.name)
    return (out, topo)


def subdivide_uniform(mesh, levels=1):
    """Split every edge, four triangles for one.  ``O(V + F)`` per level."""
    current = mesh
    combined = None
    for _ in range(max(1, int(levels))):
        edges = list(current.edge_face_count())
        current, topo = split_edges(current, edges)
        combined = topo if combined is None else _compose(combined, topo)
    return (current, combined)


def subdivide_in_radius(mesh, center, radius, min_edge=0.0, max_new=None):
    """Adaptive subdivision under the brush.

    Splits the edges that have at least one endpoint inside the sphere and are
    longer than ``min_edge`` -- the "add detail where I am sculpting" case.
    Because the split is conforming, triangles straddling the rim are
    re-triangulated rather than left with a T-junction.

    ``min_edge`` of ``0`` means "split them all"; passing the target edge
    length is what stops a stroke from subdividing without bound.
    """
    inside = set(mesh.vertices_in_radius(center, radius))
    if not inside:
        return (mesh.copy(), _identity_map(mesh.n_vertices))
    p = mesh.positions
    m2 = float(min_edge) * float(min_edge)
    candidates = []
    for (a, b) in mesh.edge_face_count():
        if a not in inside and b not in inside:
            continue
        ao = a * 3
        bo = b * 3
        dx = p[ao] - p[bo]
        dy = p[ao + 1] - p[bo + 1]
        dz = p[ao + 2] - p[bo + 2]
        d2 = dx * dx + dy * dy + dz * dz
        if d2 <= m2:
            continue
        candidates.append((d2, a, b))
    if max_new is not None and len(candidates) > int(max_new):
        candidates.sort(reverse=True)
        candidates = candidates[:int(max_new)]
    return split_edges(mesh, [(a, b) for _, a, b in candidates])


# --------------------------------------------------------------------------
# decimation
# --------------------------------------------------------------------------

def collapse_short_edges(mesh, min_length, max_collapses=None):
    """Collapse edges shorter than ``min_length``; returns ``(mesh, map)``.

    Each collapse merges the two endpoints at their midpoint and drops the one
    or two triangles that shared the edge.  Two guards keep the result
    manifold:

    * the **link condition** -- the two endpoints may share exactly as many
      neighbours as they have common faces (two for an interior edge, one on a
      boundary).  Collapsing an edge that fails it pinches the surface into a
      non-manifold vertex, which is the classic way a naive decimator produces
      a mesh that no longer bounds a solid.
    * **boundary vertices are never collapsed**, so a mesh with holes keeps
      exactly the holes it had.

    Candidates are processed shortest first and each vertex takes part in at
    most one collapse per call, so a single pass cannot cascade.
    ``O(E log E + V + F)``.
    """
    nv = mesh.n_vertices
    off, nbr = mesh.adjacency()
    p = mesh.positions
    limit = float(min_length) * float(min_length)

    counts = mesh.edge_face_count()
    boundary = set()
    for (a, b), n in counts.items():
        if n != 2:
            boundary.add(a)
            boundary.add(b)

    neighbours = [set(nbr[off[i]:off[i + 1]]) for i in range(nv)]

    candidates = []
    for (a, b), n in counts.items():
        if a in boundary or b in boundary:
            continue
        ao = a * 3
        bo = b * 3
        dx = p[ao] - p[bo]
        dy = p[ao + 1] - p[bo + 1]
        dz = p[ao + 2] - p[bo + 2]
        d2 = dx * dx + dy * dy + dz * dz
        if d2 < limit:
            candidates.append((d2, a, b))
    candidates.sort()

    parent = list(range(nv))
    used = set()
    done = 0
    for _, a, b in candidates:
        if a in used or b in used:
            continue
        if max_collapses is not None and done >= int(max_collapses):
            break
        shared = neighbours[a] & neighbours[b]
        if len(shared) != counts.get(_edge_key(a, b), 0):
            continue                      # link condition
        parent[b] = a
        used.add(a)
        used.add(b)
        used.update(shared)
        done += 1
    if not done:
        return (mesh.copy(), _identity_map(nv))

    def _root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    # compact the surviving vertices
    topo = TopologyMap(nv)
    order = {}
    for i in range(nv):
        r = _root(i)
        if r not in order:
            order[r] = len(order)
    faces = []
    t = mesh.faces
    for f in range(0, len(t), 3):
        a = order[_root(t[f])]
        b = order[_root(t[f + 1])]
        c = order[_root(t[f + 2])]
        if a == b or b == c or c == a:
            continue                       # the collapsed triangles
        faces.extend((a, b, c))
    used_new = set(faces)
    renumber = {}
    for old_new in sorted(used_new):
        renumber[old_new] = len(renumber)
    faces = [renumber[i] for i in faces]
    topo.new_count = len(renumber)
    for i in range(nv):
        slot = order[_root(i)]
        topo.old_to_new[i] = renumber.get(slot, -1)
    positions = topo.remap_positions(mesh.positions)
    out = SculptMesh(positions, faces, mesh.name)
    return (out, topo)


# --------------------------------------------------------------------------
# remeshing
# --------------------------------------------------------------------------

def remesh(mesh, target_edge, iterations=3, region=None):
    """Drive the mesh towards a uniform ``target_edge`` length.

    One iteration splits every edge longer than ``4/3 * target`` and then
    collapses every edge shorter than ``4/5 * target`` -- the standard
    incremental remeshing pair, whose fixed point is a mesh whose edges all sit
    inside ``[4/5, 4/3] * target``.  ``region`` restricts the work to
    ``(center, radius)``, which is how the session adds detail under the brush
    without touching the rest of the model.

    Returns ``(mesh, map)``; the map composes every intermediate step, so one
    call to :meth:`TopologyMap.remap_stack` is enough.
    """
    target = float(target_edge)
    if target <= 0.0:
        raise ValueError("target_edge must be positive")
    hi = target * 4.0 / 3.0
    lo = target * 4.0 / 5.0
    current = mesh
    combined = _identity_map(mesh.n_vertices)
    for _ in range(max(1, int(iterations))):
        if region is not None:
            current, topo = subdivide_in_radius(current, region[0], region[1],
                                                min_edge=hi)
        else:
            p = current.positions
            long_edges = []
            for (a, b) in current.edge_face_count():
                ao = a * 3
                bo = b * 3
                dx = p[ao] - p[bo]
                dy = p[ao + 1] - p[bo + 1]
                dz = p[ao + 2] - p[bo + 2]
                if math.sqrt(dx * dx + dy * dy + dz * dz) > hi:
                    long_edges.append((a, b))
            current, topo = split_edges(current, long_edges)
        combined = _compose(combined, topo)
        current, topo = collapse_short_edges(current, lo)
        combined = _compose(combined, topo)
    return (current, combined)


# --------------------------------------------------------------------------
# map algebra
# --------------------------------------------------------------------------

def _identity_map(n):
    topo = TopologyMap(n, n)
    topo.old_to_new = list(range(n))
    return topo


def _compose(first, second):
    """``first`` then ``second`` as a single map."""
    out = TopologyMap(first.old_count, second.new_count)
    for old, mid in enumerate(first.old_to_new):
        out.old_to_new[old] = -1 if mid < 0 else second.old_to_new[mid]
    # vertices created by the first step become "old" for the second, so they
    # are already covered by second.old_to_new; only the second step's own
    # creations need carrying, with their parents translated where they refer
    # to vertices the first step created.
    remap = {}
    for new, pa, pb in first.created:
        remap[new] = second.old_to_new[new] if new < second.old_count else -1
    for new, pa, pb in first.created:
        target = remap.get(new, -1)
        if target >= 0:
            pa2 = second.old_to_new[pa] if pa < second.old_count else -1
            pb2 = second.old_to_new[pb] if pb < second.old_count else -1
            if pa2 >= 0 and pb2 >= 0:
                out.created.append((target, pa2, pb2))
    for new, pa, pb in second.created:
        out.created.append((new, pa, pb))
    return out
