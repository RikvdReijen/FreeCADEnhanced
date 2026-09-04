# SPDX-License-Identifier: LGPL-2.1-or-later
"""A bounding-volume hierarchy over the triangles of a :class:`TriMesh`.

Binary tree, median split on the longest axis of the node's centroid bounds,
leaves of at most ``leaf_size`` triangles. Built once per mesh; queried
under a *relative pose* so a moving part never has its tree rebuilt — the
other mesh's frame is what moves.
"""

from xrsketch import vecmath as vm

INF = float("inf")


class AABB(object):
    __slots__ = ("lo", "hi")

    def __init__(self, lo=(INF, INF, INF), hi=(-INF, -INF, -INF)):
        self.lo = tuple(lo)
        self.hi = tuple(hi)

    @classmethod
    def of_points(cls, points):
        xs = [p[0] for p in points]; ys = [p[1] for p in points]; zs = [p[2] for p in points]
        return cls((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

    def union(self, other):
        return AABB((min(self.lo[0], other.lo[0]), min(self.lo[1], other.lo[1]), min(self.lo[2], other.lo[2])),
                    (max(self.hi[0], other.hi[0]), max(self.hi[1], other.hi[1]), max(self.hi[2], other.hi[2])))

    def expanded(self, margin):
        return AABB((self.lo[0] - margin, self.lo[1] - margin, self.lo[2] - margin),
                    (self.hi[0] + margin, self.hi[1] + margin, self.hi[2] + margin))

    def overlaps(self, other):
        return (self.lo[0] <= other.hi[0] and self.hi[0] >= other.lo[0] and
                self.lo[1] <= other.hi[1] and self.hi[1] >= other.lo[1] and
                self.lo[2] <= other.hi[2] and self.hi[2] >= other.lo[2])

    def distance(self, other):
        """Lower bound on the distance between anything inside each box."""
        d2 = 0.0
        for i in range(3):
            if self.hi[i] < other.lo[i]:
                d = other.lo[i] - self.hi[i]; d2 += d * d
            elif other.hi[i] < self.lo[i]:
                d = self.lo[i] - other.hi[i]; d2 += d * d
        return d2 ** 0.5

    def contains_point(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    @property
    def center(self):
        return tuple((self.lo[i] + self.hi[i]) * 0.5 for i in range(3))

    @property
    def size(self):
        return tuple(self.hi[i] - self.lo[i] for i in range(3))

    def corners(self):
        return [(x, y, z) for x in (self.lo[0], self.hi[0]) for y in (self.lo[1], self.hi[1]) for z in (self.lo[2], self.hi[2])]

    def transformed(self, transform):
        """The axis-aligned box of this box's corners after ``transform``."""
        return AABB.of_points([transform.apply(c) for c in self.corners()])

    def __repr__(self):
        return "AABB(%s, %s)" % (tuple(round(c, 4) for c in self.lo), tuple(round(c, 4) for c in self.hi))


class _Node(object):
    __slots__ = ("box", "left", "right", "triangles")

    def __init__(self, box, triangles=None, left=None, right=None):
        self.box = box
        self.triangles = triangles
        self.left = left
        self.right = right

    @property
    def leaf(self):
        return self.triangles is not None


class BVH(object):
    """``BVH(mesh)`` builds the tree; the mesh is kept for triangle access."""

    def __init__(self, mesh, leaf_size=4):
        self.mesh = mesh
        self.leaf_size = max(1, int(leaf_size))
        self._tri_boxes = [AABB.of_points(mesh.triangle(i)) for i in range(len(mesh))]
        self._centroids = [b.center for b in self._tri_boxes]
        self.root = self._build(list(range(len(mesh)))) if len(mesh) else _Node(AABB(), [])
        self.node_count = self._count(self.root)

    def _build(self, indices):
        box = self._tri_boxes[indices[0]]
        for i in indices[1:]:
            box = box.union(self._tri_boxes[i])
        if len(indices) <= self.leaf_size:
            return _Node(box, indices)
        cbox = AABB.of_points([self._centroids[i] for i in indices])
        axis = max(range(3), key=lambda k: cbox.size[k])
        indices.sort(key=lambda i: self._centroids[i][axis])
        mid = len(indices) // 2
        return _Node(box, None, self._build(indices[:mid]), self._build(indices[mid:]))

    def _count(self, node):
        return 1 if node.leaf else 1 + self._count(node.left) + self._count(node.right)

    @property
    def bounds(self):
        return self.root.box

    def leaves(self):
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.leaf:
                yield node
            else:
                stack.append(node.left); stack.append(node.right)

    def triangles_in(self, box):
        """Indices of triangles whose bounding box overlaps ``box``."""
        found = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            if not node.box.overlaps(box):
                continue
            if node.leaf:
                found.extend(i for i in node.triangles if self._tri_boxes[i].overlaps(box))
            else:
                stack.append(node.left); stack.append(node.right)
        return found

    def ray_hits(self, origin, direction, max_t=float("inf")):
        """``[(t, triangle_index)]`` of ray/triangle hits with ``t > 0``, unsorted."""
        hits = []
        inv = tuple((1.0 / d) if abs(d) > 1e-15 else (1e15 if d >= 0 else -1e15) for d in direction)
        stack = [self.root]
        while stack:
            node = stack.pop()
            if not _ray_box(origin, inv, node.box, max_t):
                continue
            if node.leaf:
                for i in node.triangles:
                    t = ray_triangle(origin, direction, self.mesh.triangle(i))
                    if t is not None and 0.0 < t <= max_t:
                        hits.append((t, i))
            else:
                stack.append(node.left); stack.append(node.right)
        return hits

    def contains_point(self, point, direction=(0.577350, 0.577351, 0.577349)):
        """Parity test: is ``point`` inside this (closed) mesh?"""
        return len(self.ray_hits(point, direction)) % 2 == 1

    def candidate_pairs(self, other, relative=None, margin=0.0):
        """``(i, j)`` triangle pairs whose boxes overlap, ``i`` from this tree
        mapped through ``relative`` into ``other``'s frame."""
        relative = relative or vm.Transform.identity()
        pairs = []
        cache = {}

        def box_of(node):
            key = id(node)
            b = cache.get(key)
            if b is None:
                b = node.box.transformed(relative)
                if margin:
                    b = b.expanded(margin)
                cache[key] = b
            return b

        stack = [(self.root, other.root)]
        while stack:
            a, b = stack.pop()
            if not box_of(a).overlaps(b.box):
                continue
            if a.leaf and b.leaf:
                for i in a.triangles:
                    ai = self._tri_boxes[i].transformed(relative)
                    if margin:
                        ai = ai.expanded(margin)
                    for j in b.triangles:
                        if ai.overlaps(other._tri_boxes[j]):
                            pairs.append((i, j))
            elif a.leaf or (not b.leaf and _volume(b.box) > _volume(a.box)):
                stack.append((a, b.left)); stack.append((a, b.right))
            else:
                stack.append((a.left, b)); stack.append((a.right, b))
        return pairs

    def __repr__(self):
        return "BVH(%d triangles, %d nodes)" % (len(self.mesh), self.node_count)


def _ray_box(origin, inv, box, max_t):
    tmin, tmax = 0.0, max_t
    for i in range(3):
        t1 = (box.lo[i] - origin[i]) * inv[i]
        t2 = (box.hi[i] - origin[i]) * inv[i]
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmax < tmin:
            return False
    return True


def ray_triangle(origin, direction, tri, eps=1e-12):
    """Möller–Trumbore; the ray parameter ``t`` or ``None``."""
    a, b, c = tri
    e1, e2 = vm.sub(b, a), vm.sub(c, a)
    p = vm.cross(direction, e2)
    det = vm.dot(e1, p)
    if abs(det) < eps:
        return None
    inv = 1.0 / det
    s = vm.sub(origin, a)
    u = vm.dot(s, p) * inv
    if u < -eps or u > 1.0 + eps:
        return None
    q = vm.cross(s, e1)
    v = vm.dot(direction, q) * inv
    if v < -eps or u + v > 1.0 + eps:
        return None
    return vm.dot(e2, q) * inv


def _volume(box):
    s = box.size
    return max(s[0], 0.0) * max(s[1], 0.0) * max(s[2], 0.0)
