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
"""Control-cage surface modelling: a quad-dominant cage with Catmull-Clark.

You push a coarse cage around with your hands and a smooth surface follows.
:class:`Cage` holds the topology, :meth:`Cage.subdivide` refines it,
:meth:`Cage.limit_points` jumps straight to where the surface actually ends up,
and the editing operations below keep the cage manifold at every step.

Topology
--------
Faces are lists of vertex indices wound counter-clockwise seen from outside.
The half-edge view (:meth:`Cage.topology`) is derived from that on demand: one
half-edge per face corner, ``next``/``prev`` inside the face and ``twin``
across it.  :meth:`Cage.check` verifies the invariants a cage must satisfy —
indices in range, no repeated corner, every directed edge used at most once
(which is winding consistency *and* edge manifoldness), every twin mutual (no
orphaned half-edge) and a single face fan around every vertex (no bowties).

UV coordinates
--------------
UVs are optional and stored per face corner (``face_uvs[f][i]``), so a seam is
just two faces disagreeing about the same vertex.

**Operations that preserve UVs:** :meth:`move_vertices`, :meth:`subdivide`
(bilinear, per face, so seams survive), :meth:`loop_cut` (linear along the cut
edges), :meth:`mirror` (mirrored copy of the source UVs — overlapping by
construction, which is what a mirrored model wants), :meth:`delete_faces` and
:meth:`limit_points`.

**Operations that drop the UVs of the faces they create or reshape**, because
there is no honest way to invent them: :meth:`extrude_face`,
:meth:`inset_face`,
:meth:`bevel_edge`, :meth:`bridge_faces` and :meth:`merge_vertices`.  Those
faces get ``None``; :meth:`uv_complete` reports whether anything is missing.
"""

import math

from . import vecmath as vm

__all__ = [
    "Cage",
    "HalfEdgeMesh",
    "Selection",
    "SubdError",
    "cube_cage",
    "grid_cage",
]


class SubdError(ValueError):
    """Raised when an edit would leave the cage invalid, or cannot be made."""


def _key(a, b):
    return (a, b) if a < b else (b, a)


# --------------------------------------------------------------------------
# half-edge view
# --------------------------------------------------------------------------

class HalfEdgeMesh(object):
    """Derived half-edge adjacency for a :class:`Cage`.

    Half-edge ``h`` runs from ``origin[h]`` to ``origin[next[h]]`` inside
    ``face[h]``.  ``twin[h]`` is ``-1`` on a boundary.
    """

    __slots__ = ("origin", "face", "next", "prev", "twin", "first",
                 "outgoing", "edges")

    def __init__(self, faces):
        self.origin = []
        self.face = []
        self.next = []
        self.prev = []
        self.first = []            # first half-edge of each face
        self.outgoing = {}         # vertex -> list of outgoing half-edges
        self.edges = {}            # (u, v) undirected key -> list of faces
        directed = {}
        for fi, face in enumerate(faces):
            base = len(self.origin)
            self.first.append(base)
            k = len(face)
            for j in range(k):
                self.origin.append(face[j])
                self.face.append(fi)
                self.next.append(base + (j + 1) % k)
                self.prev.append(base + (j - 1) % k)
                self.outgoing.setdefault(face[j], []).append(base + j)
                directed[(face[j], face[(j + 1) % k])] = base + j
                self.edges.setdefault(_key(face[j], face[(j + 1) % k]),
                                      []).append(fi)
        self.twin = []
        for h in range(len(self.origin)):
            a = self.origin[h]
            b = self.origin[self.next[h]]
            self.twin.append(directed.get((b, a), -1))

    def dest(self, h):
        return self.origin[self.next[h]]

    def rotate(self, h):
        """Next outgoing half-edge around ``origin[h]``; -1 on a boundary."""
        t = self.twin[self.prev[h]]
        return t

    def fan(self, vertex):
        """Outgoing half-edges around ``vertex`` in cyclic order.

        Returns ``None`` when the fan is not a single closed cycle (a boundary
        vertex or a non-manifold one).
        """
        starts = self.outgoing.get(vertex)
        if not starts:
            return None
        h0 = starts[0]
        out = [h0]
        h = self.rotate(h0)
        while h != h0:
            if h < 0 or len(out) > len(starts):
                return None
            out.append(h)
            h = self.rotate(h)
        if len(out) != len(starts):
            return None
        return out

    def __len__(self):
        return len(self.origin)


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

class Selection(object):
    """Vertices, edges and faces picked in the headset."""

    __slots__ = ("vertices", "edges", "faces")

    def __init__(self, vertices=None, edges=None, faces=None):
        self.vertices = set(vertices or ())
        self.edges = set(_key(*e) for e in (edges or ()))
        self.faces = set(faces or ())

    def clear(self):
        self.vertices.clear()
        self.edges.clear()
        self.faces.clear()
        return self

    def add_vertex(self, v):
        self.vertices.add(int(v))
        return self

    def add_edge(self, a, b):
        self.edges.add(_key(int(a), int(b)))
        return self

    def add_face(self, f):
        self.faces.add(int(f))
        return self

    def toggle_vertex(self, v):
        v = int(v)
        self.vertices.symmetric_difference_update({v})
        return v in self.vertices

    @property
    def empty(self):
        return not (self.vertices or self.edges or self.faces)

    def vertex_set(self, cage):
        """Every vertex touched, expanding selected edges and faces."""
        out = set(self.vertices)
        for a, b in self.edges:
            out.add(a)
            out.add(b)
        for f in self.faces:
            out.update(cage.faces[f])
        return out

    def copy(self):
        return Selection(self.vertices, self.edges, self.faces)

    def __repr__(self):
        return "Selection(%d v, %d e, %d f)" % (
            len(self.vertices), len(self.edges), len(self.faces))


# --------------------------------------------------------------------------
# the cage
# --------------------------------------------------------------------------

class Cage(object):
    """A quad-dominant control cage."""

    def __init__(self, vertices=None, faces=None, face_uvs=None):
        self.vertices = [vm.vec3(v) for v in (vertices or [])]
        self.faces = [tuple(int(i) for i in f) for f in (faces or [])]
        if face_uvs is None:
            self.face_uvs = [None] * len(self.faces)
        else:
            self.face_uvs = [None if uv is None
                             else [(float(u), float(v)) for u, v in uv]
                             for uv in face_uvs]
            while len(self.face_uvs) < len(self.faces):
                self.face_uvs.append(None)
        self._topo = None

    # -- basics ----------------------------------------------------------
    def copy(self):
        return Cage(self.vertices, self.faces, self.face_uvs)

    def invalidate(self):
        self._topo = None

    def topology(self):
        if self._topo is None:
            self._topo = HalfEdgeMesh(self.faces)
        return self._topo

    @property
    def vertex_count(self):
        return len(self.vertices)

    @property
    def face_count(self):
        return len(self.faces)

    def edge_keys(self):
        return sorted(self.topology().edges.keys())

    @property
    def edge_count(self):
        return len(self.topology().edges)

    def has_uvs(self):
        return any(uv is not None for uv in self.face_uvs)

    def uv_complete(self):
        """True when every face carries UVs for every corner."""
        if not self.face_uvs:
            return False
        for f, uv in zip(self.faces, self.face_uvs):
            if uv is None or len(uv) != len(f):
                return False
        return True

    def bounds(self):
        if not self.vertices:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = [min(v[i] for v in self.vertices) for i in range(3)]
        hi = [max(v[i] for v in self.vertices) for i in range(3)]
        return (tuple(lo), tuple(hi))

    def to_dict(self):
        return {
            "vertices": [list(v) for v in self.vertices],
            "faces": [list(f) for f in self.faces],
            "face_uvs": [None if uv is None else [list(c) for c in uv]
                         for uv in self.face_uvs],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("vertices"), d.get("faces"), d.get("face_uvs"))

    def __repr__(self):
        return "Cage(%d verts, %d faces)" % (len(self.vertices),
                                             len(self.faces))

    # -- geometry --------------------------------------------------------
    def face_points(self, fi):
        return [self.vertices[i] for i in self.faces[fi]]

    def face_centroid(self, fi):
        pts = self.face_points(fi)
        n = float(len(pts))
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n,
                sum(p[2] for p in pts) / n)

    def face_normal(self, fi):
        """Newell normal — correct for non-planar polygons too."""
        pts = self.face_points(fi)
        nx = ny = nz = 0.0
        k = len(pts)
        for i in range(k):
            a = pts[i]
            b = pts[(i + 1) % k]
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        return vm.normalize((nx, ny, nz), (0.0, 0.0, 1.0))

    def vertex_normals(self):
        """Area weighted vertex normals of the cage itself."""
        acc = [(0.0, 0.0, 0.0)] * len(self.vertices)
        for fi in range(len(self.faces)):
            n = self.face_normal(fi)
            area = self.face_area(fi)
            for vi in self.faces[fi]:
                acc[vi] = vm.add(acc[vi], vm.mul(n, area))
        return [vm.normalize(a, (0.0, 0.0, 1.0)) for a in acc]

    def face_area(self, fi):
        pts = self.face_points(fi)
        c = self.face_centroid(fi)
        total = 0.0
        k = len(pts)
        for i in range(k):
            total += 0.5 * vm.length(vm.cross(vm.sub(pts[i], c),
                                              vm.sub(pts[(i + 1) % k], c)))
        return total

    def triangles(self):
        """Fan triangulation, for a renderer or a Mesh export."""
        out = []
        for face in self.faces:
            for i in range(1, len(face) - 1):
                out.append((face[0], face[i], face[i + 1]))
        return out

    # -- validity --------------------------------------------------------
    def check(self, allow_unused=True):
        """Every topological problem found, as a list of strings."""
        problems = []
        nv = len(self.vertices)
        used = set()
        directed = {}
        for fi, face in enumerate(self.faces):
            if len(face) < 3:
                problems.append("face %d has only %d corners" % (fi,
                                                                 len(face)))
                continue
            if len(set(face)) != len(face):
                problems.append("face %d repeats a vertex: %r" % (fi, face))
            for i, v in enumerate(face):
                if not (0 <= v < nv):
                    problems.append("face %d references vertex %d of %d"
                                    % (fi, v, nv))
                    continue
                used.add(v)
                d = (v, face[(i + 1) % len(face)])
                if d in directed:
                    problems.append(
                        "directed edge %r used by faces %d and %d "
                        "(non-manifold or inconsistent winding)"
                        % (d, directed[d], fi))
                else:
                    directed[d] = fi
        if problems:
            return problems
        topo = self.topology()
        for k, faces in topo.edges.items():
            if len(faces) > 2:
                problems.append("edge %r is shared by %d faces"
                                % (k, len(faces)))
        for h in range(len(topo)):
            t = topo.twin[h]
            if t >= 0 and topo.twin[t] != h:
                problems.append("half-edge %d has an orphaned twin" % h)
        for v in used:
            outgoing = topo.outgoing.get(v, [])
            boundary = any(topo.twin[h] < 0 for h in outgoing)
            if boundary:
                continue
            if topo.fan(v) is None:
                problems.append("vertex %d has a non-manifold fan" % v)
        if not allow_unused:
            for v in range(nv):
                if v not in used:
                    problems.append("vertex %d is not used by any face" % v)
        return problems

    def is_valid(self, allow_unused=True):
        return not self.check(allow_unused)

    def validate(self, allow_unused=True):
        problems = self.check(allow_unused)
        if problems:
            raise SubdError("invalid cage: " + "; ".join(problems[:4]))
        return self

    def is_boundary_edge(self, a, b):
        return len(self.topology().edges.get(_key(a, b), ())) < 2

    def is_closed(self):
        return all(len(f) == 2 for f in self.topology().edges.values())

    def vertex_faces(self, v):
        topo = self.topology()
        return [topo.face[h] for h in topo.outgoing.get(v, [])]

    def valence(self, v):
        """Number of edges meeting at ``v``."""
        return len(self.vertex_neighbours(v))

    def vertex_neighbours(self, v):
        topo = self.topology()
        out = []
        seen = set()
        for h in topo.outgoing.get(v, []):
            for w in (topo.dest(h), topo.origin[topo.prev[h]]):
                if w != v and w not in seen:
                    seen.add(w)
                    out.append(w)
        return out

    def boundary_neighbours(self, v):
        """The two boundary neighbours of a boundary vertex, else ``None``."""
        topo = self.topology()
        nb = []
        for h in topo.outgoing.get(v, []):
            if topo.twin[h] < 0:
                nb.append(topo.dest(h))
            p = topo.prev[h]
            if topo.twin[p] < 0:
                nb.append(topo.origin[p])
        nb = list(dict.fromkeys(nb))
        if len(nb) != 2:
            return None
        return nb

    # ------------------------------------------------------------------
    # Catmull-Clark
    # ------------------------------------------------------------------
    def subdivide(self, levels=1):
        """Catmull-Clark refinement; returns a new all-quad :class:`Cage`.

        Interior rules are the classic ones (face point = centroid, edge point
        = average of the two endpoints and the two face points, vertex point =
        ``(F + 2R + (n-3)P) / n``).  Boundary edges use the midpoint and
        boundary vertices the cubic B-spline mask ``(P0 + 6P + P1) / 8``, so an
        open cage keeps a clean edge instead of shrinking away from it.
        """
        cage = self
        for _ in range(max(0, int(levels))):
            cage = cage._subdivide_once()
        return cage

    def _subdivide_once(self):
        topo = self.topology()
        nv = len(self.vertices)
        centroids = [self.face_centroid(fi) for fi in range(len(self.faces))]
        new_verts = []
        face_point = {}
        for fi in range(len(self.faces)):
            face_point[fi] = nv + len(new_verts)
            new_verts.append(centroids[fi])
        edge_point = {}
        for k, faces in topo.edges.items():
            a, b = k
            pa, pb = self.vertices[a], self.vertices[b]
            if len(faces) == 2:
                fa = centroids[faces[0]]
                fb = centroids[faces[1]]
                p = vm.mul(vm.add(vm.add(pa, pb), vm.add(fa, fb)), 0.25)
            else:
                p = vm.mul(vm.add(pa, pb), 0.5)
            edge_point[k] = nv + len(new_verts)
            new_verts.append(p)

        moved = []
        for v in range(nv):
            moved.append(self._moved_vertex(v, topo, centroids))

        vertices = moved + new_verts
        faces = []
        face_uvs = []
        for fi, face in enumerate(self.faces):
            k = len(face)
            uv = self.face_uvs[fi] if fi < len(self.face_uvs) else None
            has_uv = uv is not None and len(uv) == k
            if has_uv:
                uv_face = (sum(c[0] for c in uv) / k,
                           sum(c[1] for c in uv) / k)
            for i in range(k):
                v = face[i]
                nxt = face[(i + 1) % k]
                prv = face[(i - 1) % k]
                faces.append((v, edge_point[_key(v, nxt)], face_point[fi],
                              edge_point[_key(prv, v)]))
                if has_uv:
                    uv_next = _uv_mid(uv[i], uv[(i + 1) % k])
                    uv_prev = _uv_mid(uv[(i - 1) % k], uv[i])
                    face_uvs.append([uv[i], uv_next, uv_face, uv_prev])
                else:
                    face_uvs.append(None)
        return Cage(vertices, faces, face_uvs)

    def _moved_vertex(self, v, topo, centroids=None):
        p = self.vertices[v]
        outgoing = topo.outgoing.get(v, [])
        if not outgoing:
            return p
        boundary = self.boundary_neighbours(v)
        if boundary is not None:
            a = self.vertices[boundary[0]]
            b = self.vertices[boundary[1]]
            return vm.mul(vm.add(vm.add(a, b), vm.mul(p, 6.0)), 1.0 / 8.0)
        faces = set(topo.face[h] for h in outgoing)
        nb = self.vertex_neighbours(v)
        n = len(nb)
        if n < 3:
            return p
        F = (0.0, 0.0, 0.0)
        for fi in faces:
            F = vm.add(F, centroids[fi] if centroids is not None
                       else self.face_centroid(fi))
        F = vm.mul(F, 1.0 / max(1, len(faces)))
        R = (0.0, 0.0, 0.0)
        for w in nb:
            R = vm.add(R, vm.mul(vm.add(p, self.vertices[w]), 0.5))
        R = vm.mul(R, 1.0 / n)
        return vm.mul(vm.add(vm.add(F, vm.mul(R, 2.0)),
                             vm.mul(p, float(n - 3))), 1.0 / n)

    def limit_points(self):
        """Where every cage vertex ends up on the limit surface.

        For an interior vertex of valence ``n`` the mask is

            ``(n(n-1) P + 2 Σ neighbours + 4 Σ centroids) / (n (n + 5))``

        which is the left eigenvector of the local subdivision matrix for
        eigenvalue 1, so it is *exact*: applying it to any level of refinement
        gives the same point, and iterated subdivision converges to it.  The
        derivation only assumes that the faces around the vertex form a single
        fan, so n-gons in the cage are fine.

        Boundary vertices follow the cubic B-spline limit of the boundary
        polyline, ``(P0 + 4 P + P1) / 6``, matching the ``(P0 + 6P + P1) / 8``
        refinement rule used by :meth:`subdivide`.  A vertex with no faces, or
        with fewer than three neighbours, is returned unchanged.
        """
        topo = self.topology()
        out = []
        for v in range(len(self.vertices)):
            p = self.vertices[v]
            outgoing = topo.outgoing.get(v, [])
            if not outgoing:
                out.append(p)
                continue
            boundary = self.boundary_neighbours(v)
            if boundary is not None:
                a = self.vertices[boundary[0]]
                b = self.vertices[boundary[1]]
                out.append(vm.mul(vm.add(vm.add(a, b), vm.mul(p, 4.0)),
                                  1.0 / 6.0))
                continue
            nb = self.vertex_neighbours(v)
            n = len(nb)
            if n < 3:
                out.append(p)
                continue
            acc = vm.mul(p, float(n * (n - 1)))
            for w in nb:
                acc = vm.add(acc, vm.mul(self.vertices[w], 2.0))
            for fi in set(topo.face[h] for h in outgoing):
                acc = vm.add(acc, vm.mul(self.face_centroid(fi), 4.0))
            out.append(vm.mul(acc, 1.0 / float(n * (n + 5))))
        return out

    def limit_surface(self, levels=2):
        """A cage refined ``levels`` times and pushed onto the limit surface.

        The result is an evaluable approximation of the true limit surface:
        every vertex sits exactly on it, and refining further only adds
        detail between those points.  UVs survive the refinement.
        """
        cage = self.subdivide(levels)
        out = cage.copy()
        out.vertices = cage.limit_points()
        out.invalidate()
        return out

    def limit_normals(self, levels=2):
        surf = self.limit_surface(levels)
        return (surf, surf.vertex_normals())

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------
    def move_vertices(self, indices, delta):
        """Translate vertices; topology and UVs untouched."""
        d = vm.vec3(delta)
        for i in indices:
            i = int(i)
            if not (0 <= i < len(self.vertices)):
                raise SubdError("no such vertex: %d" % i)
            self.vertices[i] = vm.add(self.vertices[i], d)
        return self

    def set_vertex(self, index, position):
        index = int(index)
        if not (0 <= index < len(self.vertices)):
            raise SubdError("no such vertex: %d" % index)
        self.vertices[index] = vm.vec3(position)
        return self

    def _require_face(self, fi):
        if not (0 <= fi < len(self.faces)):
            raise SubdError("no such face: %r" % (fi,))
        return self.faces[fi]

    def extrude_face(self, fi, distance, direction=None):
        """Push a face out along its normal, walling in the gap.

        Adds ``k`` vertices and ``k`` side faces for a ``k``-gon; the extruded
        cap keeps the face index.  The new faces have no UVs.
        """
        face = self._require_face(fi)
        k = len(face)
        if direction is None:
            direction = self.face_normal(fi)
        offset = vm.mul(vm.normalize(direction, (0.0, 0.0, 1.0)),
                        float(distance))
        base = len(self.vertices)
        for vi in face:
            self.vertices.append(vm.add(self.vertices[vi], offset))
        cap = tuple(base + i for i in range(k))
        for i in range(k):
            side = (face[i], face[(i + 1) % k], cap[(i + 1) % k], cap[i])
            self.faces.append(side)
            self.face_uvs.append(None)
        self.faces[fi] = cap
        self.face_uvs[fi] = None
        self.invalidate()
        return fi

    def inset_face(self, fi, amount, relative=False):
        """Shrink a face inwards, ringing it with new quads.

        ``amount`` is a distance towards the face centroid, or a fraction of
        the distance when ``relative`` is true.  Adds ``k`` vertices and ``k``
        faces; the inner face keeps the index.  The new faces have no UVs.
        """
        face = self._require_face(fi)
        k = len(face)
        c = self.face_centroid(fi)
        base = len(self.vertices)
        for vi in face:
            p = self.vertices[vi]
            v = vm.sub(c, p)
            L = vm.length(v)
            if L < 1e-12:
                self.vertices.append(p)
                continue
            t = float(amount) if relative else float(amount) / L
            t = vm.clamp(t, 0.0, 0.999)
            self.vertices.append(vm.add(p, vm.mul(v, t)))
        inner = tuple(base + i for i in range(k))
        for i in range(k):
            self.faces.append((face[i], face[(i + 1) % k], inner[(i + 1) % k],
                               inner[i]))
            self.face_uvs.append(None)
        self.faces[fi] = inner
        self.face_uvs[fi] = None
        self.invalidate()
        return fi

    def bevel_edge(self, a, b, amount):
        """Chamfer one interior edge into a quad.

        Each endpoint is split in two, offset towards the centroids of the two
        faces sharing the edge.  An endpoint of valence 3 lets the remaining
        face absorb both halves (it gains one corner — the cube case, which
        gives the familiar chamfer with no extra triangles); a higher valence
        endpoint gets a small triangle closing the corner instead.  Boundary
        edges are refused.  UVs are dropped on every face involved.

        Returns the index of the new quad.
        """
        a, b = int(a), int(b)
        topo = self.topology()
        key = _key(a, b)
        faces = topo.edges.get(key)
        if not faces or len(faces) != 2:
            raise SubdError("bevel needs an interior edge, %r has %d faces"
                            % (key, 0 if not faces else len(faces)))
        h_ab = None
        for h in topo.outgoing.get(a, []):
            if topo.dest(h) == b:
                h_ab = h
                break
        if h_ab is None:
            raise SubdError("edge %r is not wound consistently" % (key,))
        f0 = topo.face[h_ab]
        f1 = topo.face[topo.twin[h_ab]]
        amount = float(amount)

        # the chamfer quad, in terms of the two halves of each endpoint
        corner_a = self._bevel_corner(a, b, f0, f1, amount, topo)
        corner_b = self._bevel_corner(b, a, f0, f1, amount, topo)
        quad = (corner_a["split"][f0], corner_a["split"][f1],
                corner_b["split"][f1], corner_b["split"][f0])
        # what the quad supplies at each endpoint; the corner fill must
        # supply the twin of it
        _check_fill(corner_a, quad[0], quad[1])
        _check_fill(corner_b, quad[2], quad[3])

        edits = {}
        for corner in (corner_a, corner_b):
            for fi, sub in corner["edits"].items():
                edits.setdefault(fi, {}).update(sub)
        new_faces = list(self.faces)
        for fi, sub in edits.items():
            face = []
            for v in new_faces[fi]:
                repl = sub.get(v)
                if repl is None:
                    face.append(v)
                else:
                    face.extend(repl)
            new_faces[fi] = tuple(face)
            self.face_uvs[fi] = None
        self.faces = new_faces
        self.faces.append(quad)
        self.face_uvs.append(None)
        quad_index = len(self.faces) - 1
        for corner in (corner_a, corner_b):
            tri = corner["triangle"]
            if tri is not None:
                self.faces.append(tri)
                self.face_uvs.append(None)
        self.invalidate()
        # the two original endpoints are no longer referenced
        self.compact()
        return quad_index

    def _bevel_corner(self, v, other, f0, f1, amount, topo):
        """Split ``v`` into a face-``f0`` half and a face-``f1`` half."""
        fan = topo.fan(v)
        if fan is None:
            raise SubdError("cannot bevel at boundary or non-manifold "
                            "vertex %d" % v)
        faces = [topo.face[h] for h in fan]
        if f0 not in faces or f1 not in faces:
            raise SubdError("edge faces are not both incident to vertex %d"
                            % v)
        i0 = faces.index(f0)
        order = faces[i0:] + faces[:i0]
        if order[-1] == f1:
            fa, fb = f0, f1
        elif len(order) > 1 and order[1] == f1:
            i1 = faces.index(f1)
            order = faces[i1:] + faces[:i1]
            fa, fb = f1, f0
        else:
            raise SubdError("faces %d and %d are not adjacent around vertex "
                            "%d" % (f0, f1, v))
        p = self.vertices[v]
        v_a = len(self.vertices)
        self.vertices.append(_towards(p, self.face_centroid(fa), amount))
        v_b = len(self.vertices)
        self.vertices.append(_towards(p, self.face_centroid(fb), amount))
        split = {fa: v_a, fb: v_b}
        edits = {fa: {v: (v_a,)}, fb: {v: (v_b,)}}
        arc = order[1:-1]
        triangle = None
        fill = None
        if not arc:
            raise SubdError("vertex %d has valence 2, nothing to bevel" % v)
        if len(arc) == 1:
            # valence 3: the remaining face absorbs both halves.  Which half
            # comes first is fixed by the face sharing the edge (v, prev).
            g = arc[0]
            face = self.faces[g]
            i = face.index(v)
            prev_v = face[(i - 1) % len(face)]
            neighbour = _other_face(topo, v, prev_v, g)
            first = split.get(neighbour)
            if first is None:
                raise SubdError("cannot resolve the bevel corner at vertex %d"
                                % v)
            second = v_b if first == v_a else v_a
            edits[g] = {v: (first, second)}
            fill = (first, second)
        else:
            for g in arc:
                edits[g] = {v: (v_a,)}
            face_b = self.faces[fb]
            i = face_b.index(v)
            d = face_b[(i + 1) % len(face_b)]
            forward = True
            if d == other:
                d = face_b[(i - 1) % len(face_b)]
                forward = False
            if d == other:
                raise SubdError("degenerate face at vertex %d" % v)
            if forward:
                # (v_a, d, v_b) carries v_a->d and d->v_b, so between the two
                # halves it supplies v_b->v_a
                triangle = (v_a, d, v_b)
                fill = (v_b, v_a)
            else:
                triangle = (v_b, d, v_a)
                fill = (v_a, v_b)
        return {"split": split, "edits": edits, "triangle": triangle,
                "fill": fill}

    def edge_ring(self, a, b):
        """The ring of quads an edge loop cut would pass through.

        Returns ``(edges, faces, closed)`` where ``edges`` are undirected keys
        in walking order.  Raises when the ring runs into a face that is not a
        quad.
        """
        topo = self.topology()
        start = _key(int(a), int(b))
        if start not in topo.edges:
            raise SubdError("no such edge: %r" % (start,))
        edges = [start]
        faces = []
        for direction in (0, 1):
            adj = topo.edges[start]
            if direction >= len(adj):
                break
            f = adj[direction]
            cur = start
            while True:
                face = self.faces[f]
                if len(face) != 4:
                    raise SubdError("loop cut needs quads, face %d has %d "
                                    "corners" % (f, len(face)))
                if f in faces:
                    break
                opp = _opposite_edge(face, cur)
                if direction == 0:
                    faces.append(f)
                    if opp == start:
                        return (edges, faces, True)
                    edges.append(opp)
                else:
                    faces.insert(0, f)
                    if opp == start:
                        return (edges, faces, True)
                    edges.insert(0, opp)
                nxt = [g for g in topo.edges.get(opp, ()) if g != f]
                if not nxt:
                    break
                cur = opp
                f = nxt[0]
        return (edges, faces, False)

    def loop_cut(self, a, b):
        """Insert an edge loop through the ring carrying edge ``(a, b)``.

        Each edge of the ring gains a midpoint vertex and each quad is split in
        two.  UVs are interpolated along the cut edges.
        """
        edges, faces, closed = self.edge_ring(a, b)
        mid = {}
        for key in edges:
            p = vm.mul(vm.add(self.vertices[key[0]], self.vertices[key[1]]),
                       0.5)
            mid[key] = len(self.vertices)
            self.vertices.append(p)
        new_faces = list(self.faces)
        new_uvs = list(self.face_uvs)
        extra = []
        extra_uvs = []
        for f in faces:
            face = new_faces[f]
            cut = [i for i in range(4)
                   if _key(face[i], face[(i + 1) % 4]) in mid]
            if len(cut) != 2:
                continue
            i, j = cut
            if (j - i) % 4 != 2:
                continue
            m1 = mid[_key(face[i], face[(i + 1) % 4])]
            m2 = mid[_key(face[j], face[(j + 1) % 4])]
            v0, v1 = face[i], face[(i + 1) % 4]
            v2, v3 = face[j], face[(j + 1) % 4]
            uv = new_uvs[f]
            has_uv = uv is not None and len(uv) == 4
            if has_uv:
                q = [uv[(i + k) % 4] for k in range(4)]
                u_m1 = _uv_mid(q[0], q[1])
                u_m2 = _uv_mid(q[2], q[3])
                new_uvs[f] = [q[0], u_m1, u_m2, q[3]]
                extra_uvs.append([u_m1, q[1], q[2], u_m2])
            else:
                new_uvs[f] = None
                extra_uvs.append(None)
            new_faces[f] = (v0, m1, m2, v3)
            extra.append((m1, v1, v2, m2))
        self.faces = new_faces + extra
        self.face_uvs = new_uvs + extra_uvs
        self.invalidate()
        return len(edges)

    def bridge_faces(self, fa, fb):
        """Delete two equally sized faces and tube between their borders.

        The second loop is reversed and rotated to the alignment with the
        smallest total corner distance, so the bridge does not come out
        twisted.  The new faces have no UVs.
        """
        face_a = list(self._require_face(fa))
        face_b = list(self._require_face(fb))
        if fa == fb:
            raise SubdError("cannot bridge a face to itself")
        if len(face_a) != len(face_b):
            raise SubdError("bridge needs equal corner counts, got %d and %d"
                            % (len(face_a), len(face_b)))
        k = len(face_a)
        rev = list(reversed(face_b))
        best = None
        for shift in range(k):
            cand = rev[shift:] + rev[:shift]
            cost = sum(vm.dist(self.vertices[face_a[i]],
                               self.vertices[cand[i]]) for i in range(k))
            if best is None or cost < best[0]:
                best = (cost, cand)
        loop_b = best[1]
        new = []
        for i in range(k):
            new.append((face_a[i], face_a[(i + 1) % k], loop_b[(i + 1) % k],
                        loop_b[i]))
        keep = [i for i in range(len(self.faces)) if i not in (fa, fb)]
        self.faces = [self.faces[i] for i in keep] + new
        self.face_uvs = [self.face_uvs[i] for i in keep] + [None] * k
        self.invalidate()
        return k

    def delete_faces(self, indices, remove_orphans=True):
        """Remove faces, optionally compacting away the vertices they held."""
        drop = set(int(i) for i in indices)
        for i in drop:
            self._require_face(i)
        keep = [i for i in range(len(self.faces)) if i not in drop]
        self.faces = [self.faces[i] for i in keep]
        self.face_uvs = [self.face_uvs[i] for i in keep]
        self.invalidate()
        if remove_orphans:
            self.compact()
        return len(drop)

    def delete_vertices(self, indices):
        """Remove vertices and every face that used them."""
        drop = set(int(i) for i in indices)
        faces = [i for i, f in enumerate(self.faces)
                 if any(v in drop for v in f)]
        self.delete_faces(faces, remove_orphans=False)
        self._remap(drop)
        return len(drop)

    def compact(self):
        """Drop vertices no face refers to; returns the number removed."""
        used = set()
        for f in self.faces:
            used.update(f)
        unused = set(range(len(self.vertices))) - used
        if not unused:
            return 0
        self._remap(unused)
        return len(unused)

    def _remap(self, dropped):
        mapping = {}
        verts = []
        for i, v in enumerate(self.vertices):
            if i in dropped:
                continue
            mapping[i] = len(verts)
            verts.append(v)
        self.vertices = verts
        self.faces = [tuple(mapping[v] for v in f) for f in self.faces
                      if all(v in mapping for v in f)]
        self.face_uvs = self.face_uvs[:len(self.faces)]
        while len(self.face_uvs) < len(self.faces):
            self.face_uvs.append(None)
        self.invalidate()

    def merge_vertices(self, indices, position=None):
        """Weld vertices together at their centroid (or ``position``).

        Faces that collapse to fewer than three distinct corners are removed,
        and duplicate faces are dropped.  UVs are dropped on every face that
        changed.
        """
        idx = sorted(set(int(i) for i in indices))
        if len(idx) < 2:
            return -1 if not idx else idx[0]
        for i in idx:
            if not (0 <= i < len(self.vertices)):
                raise SubdError("no such vertex: %d" % i)
        if position is None:
            pts = [self.vertices[i] for i in idx]
            position = (sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts),
                        sum(p[2] for p in pts) / len(pts))
        keep = idx[0]
        self.vertices[keep] = vm.vec3(position)
        remap = dict((i, keep) for i in idx[1:])
        self._apply_remap(remap)
        return keep

    def _apply_remap(self, remap):
        faces = []
        uvs = []
        seen = set()
        for fi, face in enumerate(self.faces):
            changed = any(v in remap for v in face)
            new = []
            for v in face:
                w = remap.get(v, v)
                if not new or new[-1] != w:
                    new.append(w)
            while len(new) > 1 and new[0] == new[-1]:
                new.pop()
            if len(set(new)) < 3:
                continue
            key = frozenset(new)
            if changed and key in seen:
                continue
            seen.add(key)
            faces.append(tuple(new))
            uvs.append(None if changed else self.face_uvs[fi])
        self.faces = faces
        self.face_uvs = uvs
        self.invalidate()
        self.compact()

    def weld(self, tolerance=1e-6):
        """Merge every pair of vertices closer than ``tolerance``."""
        n = len(self.vertices)
        remap = {}
        buckets = {}
        cell = max(tolerance, 1e-12) * 2.0
        for i in range(n):
            if i in remap:
                continue
            p = self.vertices[i]
            key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)),
                   int(math.floor(p[2] / cell)))
            hit = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for j in buckets.get((key[0] + dx, key[1] + dy,
                                              key[2] + dz), ()):
                            if vm.dist(self.vertices[j], p) <= tolerance:
                                hit = j
                                break
                        if hit is not None:
                            break
                    if hit is not None:
                        break
                if hit is not None:
                    break
            if hit is None:
                buckets.setdefault(key, []).append(i)
            else:
                remap[i] = hit
        if remap:
            self._apply_remap(remap)
        return len(remap)

    def mirror(self, origin=(0.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0),
               weld_tolerance=1e-6):
        """Mirror the cage across a plane and weld the seam.

        Vertices lying on the plane (within ``weld_tolerance``) are shared
        rather than duplicated, so the seam has no doubled vertices and the
        result stays manifold.  Mirrored faces are wound the other way round
        and carry a mirrored copy of the source UVs.
        """
        n = vm.normalize(normal)
        if vm.length(n) < 0.5:
            raise SubdError("mirror plane needs a non-zero normal")
        o = vm.vec3(origin)
        nv = len(self.vertices)
        mapping = {}
        # snapshot: the loop appends to self.vertices as it goes
        for i in range(nv):
            p = self.vertices[i]
            d = vm.dot(vm.sub(p, o), n)
            if abs(d) <= weld_tolerance:
                mapping[i] = i
            else:
                mapping[i] = len(self.vertices)
                self.vertices.append(vm.reflect_point(p, o, n))
        added = 0
        base_faces = list(self.faces[:])
        base_uvs = list(self.face_uvs[:len(base_faces)])
        for fi, face in enumerate(base_faces):
            new = tuple(mapping[v] for v in reversed(face))
            if len(set(new)) < 3:
                continue
            self.faces.append(new)
            uv = base_uvs[fi] if fi < len(base_uvs) else None
            self.face_uvs.append(None if uv is None else list(reversed(uv)))
            added += 1
        self.invalidate()
        return (len(self.vertices) - nv, added)


def _other_face(topo, u, v, this):
    """The face across edge ``(u, v)`` from ``this``, or ``None``."""
    for f in topo.edges.get(_key(u, v), ()):
        if f != this:
            return f
    return None


def _check_fill(corner, quad_from, quad_to):
    """The corner fill must carry the twin of the quad's edge."""
    fill = corner["fill"]
    if fill != (quad_to, quad_from):
        raise SubdError("bevel would leave a non-manifold corner "
                        "(%r vs %r)" % (fill, (quad_to, quad_from)))


def _towards(p, target, amount):
    v = vm.sub(target, p)
    L = vm.length(v)
    if L < 1e-12:
        return p
    t = vm.clamp(float(amount) / L, 0.0, 0.999)
    return vm.add(p, vm.mul(v, t))


def _opposite_edge(face, key):
    for i in range(4):
        if _key(face[i], face[(i + 1) % 4]) == key:
            j = (i + 2) % 4
            return _key(face[j], face[(j + 1) % 4])
    raise SubdError("edge %r is not on face %r" % (key, face))


def _uv_mid(a, b):
    return (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))


# --------------------------------------------------------------------------
# starting cages
# --------------------------------------------------------------------------

def cube_cage(size=1.0, center=(0.0, 0.0, 0.0), with_uvs=False):
    """The six-quad cube every subdivision demo starts from."""
    h = float(size) * 0.5
    cx, cy, cz = vm.vec3(center)
    verts = [
        (cx - h, cy - h, cz - h), (cx + h, cy - h, cz - h),
        (cx + h, cy + h, cz - h), (cx - h, cy + h, cz - h),
        (cx - h, cy - h, cz + h), (cx + h, cy - h, cz + h),
        (cx + h, cy + h, cz + h), (cx - h, cy + h, cz + h),
    ]
    faces = [
        (0, 3, 2, 1),   # -Z
        (4, 5, 6, 7),   # +Z
        (0, 1, 5, 4),   # -Y
        (2, 3, 7, 6),   # +Y
        (1, 2, 6, 5),   # +X
        (0, 4, 7, 3),   # -X
    ]
    uvs = None
    if with_uvs:
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        uvs = [list(square) for _ in faces]
    return Cage(verts, faces, uvs)


def grid_cage(nx=2, ny=2, size=(1.0, 1.0), origin=(0.0, 0.0, 0.0),
              with_uvs=False):
    """A flat, open quad grid in the XY plane — a cage with a boundary."""
    nx = max(1, int(nx))
    ny = max(1, int(ny))
    sx, sy = float(size[0]), float(size[1])
    ox, oy, oz = vm.vec3(origin)
    verts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            verts.append((ox + sx * i / nx, oy + sy * j / ny, oz))
    faces = []
    uvs = [] if with_uvs else None
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2, a + nx + 1))
            if with_uvs:
                uvs.append([(i / nx, j / ny), ((i + 1) / nx, j / ny),
                            ((i + 1) / nx, (j + 1) / ny),
                            (i / nx, (j + 1) / ny)])
    return Cage(verts, faces, uvs)
