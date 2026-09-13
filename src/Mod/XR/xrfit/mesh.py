# SPDX-License-Identifier: LGPL-2.1-or-later
"""Triangle meshes for collision work.

A :class:`TriMesh` is a list of vertex tuples and a list of index triples —
the least a collision test needs. Builders for the shapes the tests and the
insertion probes use (boxes, cylinders, a cylinder with a hole) live here too
so that a fit check can be set up without FreeCAD.
"""

import math

from xrsketch import vecmath as vm


class TriMesh(object):
    __slots__ = ("vertices", "triangles", "name", "_bounds", "_normals")

    def __init__(self, vertices, triangles, name=""):
        self.vertices = [vm.vec3(v) for v in vertices]
        self.triangles = [(int(a), int(b), int(c)) for a, b, c in triangles]
        self.name = name
        self._bounds = None
        self._normals = None
        for a, b, c in self.triangles:
            if max(a, b, c) >= len(self.vertices) or min(a, b, c) < 0:
                raise ValueError("triangle index out of range")

    def __len__(self):
        return len(self.triangles)

    @property
    def bounds(self):
        """``(min, max)`` corners, or ``None`` for an empty mesh."""
        if self._bounds is None and self.vertices:
            xs = [v[0] for v in self.vertices]
            ys = [v[1] for v in self.vertices]
            zs = [v[2] for v in self.vertices]
            self._bounds = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
        return self._bounds

    @property
    def centroid(self):
        if not self.vertices:
            return (0.0, 0.0, 0.0)
        n = float(len(self.vertices))
        return (sum(v[0] for v in self.vertices) / n,
                sum(v[1] for v in self.vertices) / n,
                sum(v[2] for v in self.vertices) / n)

    def triangle(self, index):
        a, b, c = self.triangles[index]
        return (self.vertices[a], self.vertices[b], self.vertices[c])

    def normal(self, index):
        """Unit normal of triangle ``index`` (zero vector for a degenerate one)."""
        if self._normals is None:
            self._normals = [None] * len(self.triangles)
        n = self._normals[index]
        if n is None:
            a, b, c = self.triangle(index)
            n = vm.normalize(vm.cross(vm.sub(b, a), vm.sub(c, a)))
            self._normals[index] = n
        return n

    def transformed(self, transform):
        """A copy with every vertex mapped through ``transform``."""
        return TriMesh([transform.apply(v) for v in self.vertices], self.triangles, self.name)

    def area(self):
        total = 0.0
        for i in range(len(self.triangles)):
            a, b, c = self.triangle(i)
            total += 0.5 * vm.length(vm.cross(vm.sub(b, a), vm.sub(c, a)))
        return total

    def volume(self):
        """Signed volume (positive for outward-wound closed meshes)."""
        total = 0.0
        for i in range(len(self.triangles)):
            a, b, c = self.triangle(i)
            total += vm.dot(a, vm.cross(b, c)) / 6.0
        return total

    def to_dict(self):
        return {"name": self.name,
                "vertices": [list(v) for v in self.vertices],
                "triangles": [list(t) for t in self.triangles]}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("vertices", []), d.get("triangles", []), d.get("name", ""))

    @classmethod
    def from_flat(cls, positions, indices, name=""):
        """From flat ``[x,y,z,x,y,z,...]`` and ``[i,j,k,...]`` lists (FCXR style)."""
        verts = [(positions[i], positions[i + 1], positions[i + 2]) for i in range(0, len(positions), 3)]
        tris = [(indices[i], indices[i + 1], indices[i + 2]) for i in range(0, len(indices), 3)]
        return cls(verts, tris, name)

    def __repr__(self):
        return "TriMesh(%r, %d vertices, %d triangles)" % (self.name, len(self.vertices), len(self.triangles))


# ----------------------------------------------------------------------
# builders
# ----------------------------------------------------------------------


def box_mesh(size=(1.0, 1.0, 1.0), center=(0.0, 0.0, 0.0), name="box"):
    sx, sy, sz = (float(s) * 0.5 for s in size)
    cx, cy, cz = vm.vec3(center)
    corners = [(cx + x, cy + y, cz + z)
               for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)]
    # index: bit2 = x, bit1 = y, bit0 = z
    faces = [
        (0, 1, 3, 2),  # -x
        (4, 6, 7, 5),  # +x
        (0, 4, 5, 1),  # -y
        (2, 3, 7, 6),  # +y
        (0, 2, 6, 4),  # -z
        (1, 5, 7, 3),  # +z
    ]
    tris = []
    for a, b, c, d in faces:
        tris.append((a, b, c))
        tris.append((a, c, d))
    return TriMesh(corners, tris, name)


def cylinder_mesh(radius=0.5, height=1.0, sides=24, center=(0.0, 0.0, 0.0), axis="z", name="cylinder"):
    """A closed cylinder along ``axis`` centred on ``center``."""
    verts, tris = [], []
    h = float(height) * 0.5
    for k, z in enumerate((-h, h)):
        for i in range(sides):
            t = 2.0 * math.pi * i / sides
            verts.append(_axis_point(radius * math.cos(t), radius * math.sin(t), z, axis, center))
    bottom = len(verts)
    verts.append(_axis_point(0.0, 0.0, -h, axis, center))
    top = len(verts)
    verts.append(_axis_point(0.0, 0.0, h, axis, center))
    for i in range(sides):
        j = (i + 1) % sides
        tris.append((i, j, sides + j))
        tris.append((i, sides + j, sides + i))
        tris.append((bottom, j, i))
        tris.append((top, sides + i, sides + j))
    return TriMesh(verts, tris, name)


def tube_mesh(inner=0.4, outer=0.6, height=1.0, sides=24, center=(0.0, 0.0, 0.0), axis="z", name="tube"):
    """A closed tube (a cylinder with a coaxial hole) — the classic 'hole' test piece."""
    verts, tris = [], []
    h = float(height) * 0.5
    rings = []
    for z in (-h, h):
        for r in (outer, inner):
            start = len(verts)
            for i in range(sides):
                t = 2.0 * math.pi * i / sides
                verts.append(_axis_point(r * math.cos(t), r * math.sin(t), z, axis, center))
            rings.append(start)
    bo, bi, to, ti = rings  # bottom outer/inner, top outer/inner
    for i in range(sides):
        j = (i + 1) % sides
        tris.append((bo + i, bo + j, to + j)); tris.append((bo + i, to + j, to + i))   # outer wall
        tris.append((bi + j, bi + i, ti + i)); tris.append((bi + j, ti + i, ti + j))   # inner wall (inward)
        tris.append((bo + j, bo + i, bi + i)); tris.append((bo + j, bi + i, bi + j))   # bottom annulus
        tris.append((to + i, to + j, ti + j)); tris.append((to + i, ti + j, ti + i))   # top annulus
    return TriMesh(verts, tris, name)


def _axis_point(x, y, z, axis, center):
    if axis == "z":
        p = (x, y, z)
    elif axis == "y":
        p = (x, z, y)
    else:
        p = (z, x, y)
    return (p[0] + center[0], p[1] + center[1], p[2] + center[2])
