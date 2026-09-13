# SPDX-License-Identifier: LGPL-2.1-or-later
"""Triangle-level collision: intersection, penetration, closest distance.

``collide(a, b, relative)`` answers "does mesh ``a``, placed by ``relative``
in ``b``'s frame, touch ``b``, and if so which way is out?" — which is what
a fit check needs every frame. ``closest_distance`` answers "how much room
is there?", which is the clearance figure once the part is seated.

Triangle/triangle intersection is Möller's 1997 interval test; distances are
built from segment/segment and point/triangle closest points (Ericson,
*Real-Time Collision Detection*). All coordinates are in ``b``'s frame.
"""

from xrsketch import vecmath as vm

from .bvh import BVH

EPS = 1e-12


class Contact(object):
    """One intersecting triangle pair with a push-out estimate."""

    __slots__ = ("tri_a", "tri_b", "normal", "depth", "point")

    def __init__(self, tri_a, tri_b, normal, depth, point):
        self.tri_a = tri_a
        self.tri_b = tri_b
        #: unit vector, in ``b``'s frame, that moves ``a`` out of ``b``
        self.normal = normal
        self.depth = float(depth)
        self.point = point

    def to_dict(self):
        return {"tri_a": self.tri_a, "tri_b": self.tri_b, "normal": list(self.normal),
                "depth": self.depth, "point": list(self.point)}

    def __repr__(self):
        return "Contact(a=%d, b=%d, depth=%.4g)" % (self.tri_a, self.tri_b, self.depth)


class CollisionResult(object):
    __slots__ = ("contacts", "pairs_tested", "push", "inside_vertices")

    def __init__(self, contacts, pairs_tested, push=None, inside_vertices=0):
        self.contacts = contacts
        self.pairs_tested = pairs_tested
        #: the translation, in ``b``'s frame, that moves ``a`` clear of ``b``
        self.push = push if push is not None else _aggregate_push(contacts)
        self.inside_vertices = inside_vertices

    @property
    def colliding(self):
        return bool(self.contacts)

    @property
    def depth(self):
        """Penetration depth: the length of the minimum push when one was
        computed from inside vertices, else the largest local estimate."""
        if self.inside_vertices:
            return vm.length(self.push)
        return max((c.depth for c in self.contacts), default=0.0)

    def __repr__(self):
        return "CollisionResult(%d contacts, depth=%.4g)" % (len(self.contacts), self.depth)


# ----------------------------------------------------------------------
# triangle / triangle intersection (Möller)
# ----------------------------------------------------------------------


def _plane(a, b, c):
    n = vm.cross(vm.sub(b, a), vm.sub(c, a))
    return n, -vm.dot(n, a)


def _interval(p0, p1, p2, d0, d1, d2):
    """Projection interval of a triangle onto the intersection line, given
    projected coords ``p`` and signed plane distances ``d`` (Möller)."""
    if d0 * d1 > 0.0:
        # d2 on the other side
        return (p2 + (p0 - p2) * d2 / (d2 - d0), p2 + (p1 - p2) * d2 / (d2 - d1))
    if d0 * d2 > 0.0:
        return (p1 + (p0 - p1) * d1 / (d1 - d0), p1 + (p2 - p1) * d1 / (d1 - d2))
    if d1 * d2 > 0.0 or d0 != 0.0:
        return (p0 + (p1 - p0) * d0 / (d0 - d1), p0 + (p2 - p0) * d0 / (d0 - d2))
    if d1 != 0.0:
        return (p1 + (p0 - p1) * d1 / (d1 - d0), p1 + (p2 - p1) * d1 / (d1 - d2))
    if d2 != 0.0:
        return (p2 + (p0 - p2) * d2 / (d2 - d0), p2 + (p1 - p2) * d2 / (d2 - d1))
    return None  # coplanar


def triangles_intersect(t1, t2, eps=1e-9):
    """True when triangles ``t1`` and ``t2`` (3 points each) intersect.

    Coplanar triangles are tested by edge/edge overlap and containment so a
    face resting flat on another counts as touching.
    """
    v0, v1, v2 = t1
    u0, u1, u2 = t2
    n2, d2 = _plane(u0, u1, u2)
    dv = [vm.dot(n2, v) + d2 for v in (v0, v1, v2)]
    dv = [0.0 if abs(x) < eps else x for x in dv]
    if (dv[0] > 0 and dv[1] > 0 and dv[2] > 0) or (dv[0] < 0 and dv[1] < 0 and dv[2] < 0):
        return False
    n1, d1 = _plane(v0, v1, v2)
    du = [vm.dot(n1, u) + d1 for u in (u0, u1, u2)]
    du = [0.0 if abs(x) < eps else x for x in du]
    if (du[0] > 0 and du[1] > 0 and du[2] > 0) or (du[0] < 0 and du[1] < 0 and du[2] < 0):
        return False
    direction = vm.cross(n1, n2)
    axis = max(range(3), key=lambda k: abs(direction[k]))
    if abs(direction[axis]) < eps:
        return _coplanar(t1, t2, n1)
    vp = [v[axis] for v in (v0, v1, v2)]
    up = [u[axis] for u in (u0, u1, u2)]
    i1 = _interval(vp[0], vp[1], vp[2], dv[0], dv[1], dv[2])
    i2 = _interval(up[0], up[1], up[2], du[0], du[1], du[2])
    if i1 is None or i2 is None:
        return _coplanar(t1, t2, n1)
    a0, a1 = sorted(i1)
    b0, b1 = sorted(i2)
    return not (a1 < b0 - eps or b1 < a0 - eps)


def _coplanar(t1, t2, n):
    axis = max(range(3), key=lambda k: abs(n[k]))
    i, j = [k for k in range(3) if k != axis]
    p1 = [(v[i], v[j]) for v in t1]
    p2 = [(u[i], u[j]) for u in t2]
    for a in range(3):
        for b in range(3):
            if _segments_intersect_2d(p1[a], p1[(a + 1) % 3], p2[b], p2[(b + 1) % 3]):
                return True
    return _point_in_tri_2d(p1[0], p2) or _point_in_tri_2d(p2[0], p1)


def _segments_intersect_2d(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0:
        return True

    def on(p, q, r):
        return (min(p[0], q[0]) - EPS <= r[0] <= max(p[0], q[0]) + EPS and
                min(p[1], q[1]) - EPS <= r[1] <= max(p[1], q[1]) + EPS)

    return ((abs(o1) < EPS and on(a, b, c)) or (abs(o2) < EPS and on(a, b, d)) or
            (abs(o3) < EPS and on(c, d, a)) or (abs(o4) < EPS and on(c, d, b)))


def _point_in_tri_2d(p, tri):
    (ax, ay), (bx, by), (cx, cy) = tri
    d1 = (p[0] - bx) * (ay - by) - (ax - bx) * (p[1] - by)
    d2 = (p[0] - cx) * (by - cy) - (bx - cx) * (p[1] - cy)
    d3 = (p[0] - ax) * (cy - ay) - (cx - ax) * (p[1] - ay)
    neg = d1 < -EPS or d2 < -EPS or d3 < -EPS
    pos = d1 > EPS or d2 > EPS or d3 > EPS
    return not (neg and pos)


# ----------------------------------------------------------------------
# distances
# ----------------------------------------------------------------------


def closest_point_on_triangle(p, tri):
    """Closest point to ``p`` on triangle ``tri`` (Ericson 5.1.5)."""
    a, b, c = tri
    ab, ac, ap = vm.sub(b, a), vm.sub(c, a), vm.sub(p, a)
    d1, d2 = vm.dot(ab, ap), vm.dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = vm.sub(p, b)
    d3, d4 = vm.dot(ab, bp), vm.dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3) if (d1 - d3) != 0 else 0.0
        return vm.add(a, vm.mul(ab, v))
    cp = vm.sub(p, c)
    d5, d6 = vm.dot(ab, cp), vm.dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6) if (d2 - d6) != 0 else 0.0
        return vm.add(a, vm.mul(ac, w))
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        denom = (d4 - d3) + (d5 - d6)
        w = (d4 - d3) / denom if denom != 0 else 0.0
        return vm.add(b, vm.mul(vm.sub(c, b), w))
    denom = va + vb + vc
    if abs(denom) < EPS:
        return a
    v, w = vb / denom, vc / denom
    return vm.add(a, vm.add(vm.mul(ab, v), vm.mul(ac, w)))


def closest_points_segments(p1, q1, p2, q2):
    """Closest points between segments p1q1 and p2q2 (Ericson 5.1.9)."""
    d1, d2, r = vm.sub(q1, p1), vm.sub(q2, p2), vm.sub(p1, p2)
    a, e, f = vm.dot(d1, d1), vm.dot(d2, d2), vm.dot(d2, r)
    if a <= EPS and e <= EPS:
        return p1, p2
    if a <= EPS:
        s, t = 0.0, min(max(f / e, 0.0), 1.0)
    else:
        c = vm.dot(d1, r)
        if e <= EPS:
            t, s = 0.0, min(max(-c / a, 0.0), 1.0)
        else:
            b = vm.dot(d1, d2)
            denom = a * e - b * b
            s = min(max((b * f - c * e) / denom, 0.0), 1.0) if denom != 0 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, min(max(-c / a, 0.0), 1.0)
            elif t > 1.0:
                t, s = 1.0, min(max((b - c) / a, 0.0), 1.0)
    return vm.add(p1, vm.mul(d1, s)), vm.add(p2, vm.mul(d2, t))


def triangle_distance(t1, t2):
    """Distance between two triangles, and the closest point pair."""
    if triangles_intersect(t1, t2):
        return 0.0, (t1[0], t1[0])
    best, pair = float("inf"), None
    for p in t1:
        q = closest_point_on_triangle(p, t2)
        d = vm.dist(p, q)
        if d < best:
            best, pair = d, (p, q)
    for q in t2:
        p = closest_point_on_triangle(q, t1)
        d = vm.dist(p, q)
        if d < best:
            best, pair = d, (p, q)
    for i in range(3):
        for j in range(3):
            p, q = closest_points_segments(t1[i], t1[(i + 1) % 3], t2[j], t2[(j + 1) % 3])
            d = vm.dist(p, q)
            if d < best:
                best, pair = d, (p, q)
    return best, pair


# ----------------------------------------------------------------------
# mesh level
# ----------------------------------------------------------------------


def _as_bvh(thing):
    return thing if isinstance(thing, BVH) else BVH(thing)


def intersecting_pairs(a, b, relative=None):
    """Triangle index pairs of ``a`` (mapped by ``relative``) intersecting ``b``."""
    a, b = _as_bvh(a), _as_bvh(b)
    relative = relative or vm.Transform.identity()
    hits = []
    tested = 0
    cache = {}
    for i, j in a.candidate_pairs(b, relative):
        ta = cache.get(i)
        if ta is None:
            ta = tuple(relative.apply(p) for p in a.mesh.triangle(i))
            cache[i] = ta
        tested += 1
        if triangles_intersect(ta, b.mesh.triangle(j)):
            hits.append((i, j))
    return hits, tested


def collide(a, b, relative=None, hint=None):
    """Full collision query: contacts with push-out normals and depths.

    ``hint`` is a direction (in ``b``'s frame) the push should point along —
    the reverse of the approach — so a part that tunnelled through a thin
    wall in one frame is pushed back the way it came, not out the far side.

    The push-out for a contact is along ``b``'s triangle normal (``b`` is the
    static side), and its depth is how far the deepest vertex of ``a``'s
    triangle sits behind ``b``'s plane — a local estimate that is exact for a
    vertex poking through a large flat face and conservative elsewhere, which
    is what a push-out solver wants.
    """
    a, b = _as_bvh(a), _as_bvh(b)
    relative = relative or vm.Transform.identity()
    hits, tested = intersecting_pairs(a, b, relative)
    contacts = []
    for i, j in hits:
        ta = tuple(relative.apply(p) for p in a.mesh.triangle(i))
        tb = b.mesh.triangle(j)
        nb = b.mesh.normal(j)
        if vm.length(nb) < 0.5:
            continue
        depths = [-vm.dot(nb, vm.sub(p, tb[0])) for p in ta]
        depth = max(depths)
        if depth <= 0.0:
            # a's vertices all in front: the triangles cross edge-on; use a's normal instead
            na = vm.normalize(vm.cross(vm.sub(ta[1], ta[0]), vm.sub(ta[2], ta[0])))
            d2 = [vm.dot(na, vm.sub(q, ta[0])) for q in tb]
            depth = max(max(d2), 0.0)
            if vm.length(na) < 0.5:
                continue
            nb = vm.neg(na)
        deepest = ta[depths.index(max(depths))]
        contacts.append(Contact(i, j, nb, depth, deepest))
    push, inside = _minimum_translation(a, b, relative, contacts, hint) if contacts else ((0.0, 0.0, 0.0), 0)
    return CollisionResult(contacts, tested, push, inside)


def _minimum_translation(a, b, relative, contacts, hint=None):
    """The smallest push, among the contact normals, that clears every
    vertex of ``a`` inside ``b`` and every vertex of ``b`` inside ``a``.

    For each candidate direction the required distance is the longest exit
    ray from an inside vertex to the other mesh's surface. Vertices are
    tested for containment by ray parity, so both meshes should be closed;
    an open mesh falls back to the per-pair local estimate.
    """
    candidates = {}
    for c in contacts:
        key = tuple(round(x, 3) for x in c.normal)
        candidates.setdefault(key, c.normal)
    if hint is not None and vm.length(hint) > 0.0:
        h = vm.normalize(hint)
        along = {k: n for k, n in candidates.items() if vm.dot(n, h) > 0.1}
        if along:
            candidates = along
    if not candidates:
        return _aggregate_push(contacts), 0
    overlap_lo = tuple(max(a.bounds.transformed(relative).lo[i], b.bounds.lo[i]) for i in range(3))
    overlap_hi = tuple(min(a.bounds.transformed(relative).hi[i], b.bounds.hi[i]) for i in range(3))
    eps = 1e-9

    def in_overlap(p):
        return all(overlap_lo[i] - eps <= p[i] <= overlap_hi[i] + eps for i in range(3))

    inside_a = [relative.apply(v) for v in a.mesh.vertices if in_overlap(relative.apply(v))]
    inside_a = [v for v in inside_a if b.contains_point(v)]
    inverse = relative.inverse()
    inside_b = [v for v in b.mesh.vertices if in_overlap(v)]
    inside_b = [v for v in inside_b if a.contains_point(inverse.apply(v))]
    if not inside_a and not inside_b:
        return _aggregate_push(contacts), 0

    best_n, best_d = None, float("inf")
    for n in candidates.values():
        d = 0.0
        n_a = inverse.apply_vector(vm.neg(n))  # the same direction seen from a's frame
        for v in inside_a:
            hits = [t for t, _ in b.ray_hits(v, n)]
            if hits:
                d = max(d, max(hits))
        for v in inside_b:
            hits = [t for t, _ in a.ray_hits(inverse.apply(v), n_a)]
            if hits:
                d = max(d, max(hits) * relative.scale)
        if d < best_d:
            best_n, best_d = n, d
    if best_n is None or best_d <= 0.0:
        return _aggregate_push(contacts), len(inside_a) + len(inside_b)
    return vm.mul(best_n, best_d), len(inside_a) + len(inside_b)


def _aggregate_push(contacts):
    """A single push-out vector resolving all contacts at once.

    Normals are averaged, weighted by depth; the magnitude is the largest
    depth so that one deep contact is not diluted by many shallow ones.
    """
    if not contacts:
        return (0.0, 0.0, 0.0)
    total = (0.0, 0.0, 0.0)
    for c in contacts:
        total = vm.add(total, vm.mul(c.normal, max(c.depth, 1e-9)))
    direction = vm.normalize(total)
    if vm.length(direction) < 0.5:
        direction = contacts[0].normal
    return vm.mul(direction, max(c.depth for c in contacts))


def closest_distance(a, b, relative=None, upper=float("inf")):
    """Minimum distance between ``a`` (mapped by ``relative``) and ``b``.

    Branch and bound over both trees with box distance as the lower bound.
    Returns ``(distance, point_on_a, point_on_b)`` in ``b``'s frame; a
    distance of 0 means they touch. ``upper`` lets a caller stop early when
    it only cares whether the clearance is under a threshold.
    """
    a, b = _as_bvh(a), _as_bvh(b)
    relative = relative or vm.Transform.identity()
    best = upper
    pair = (None, None)
    cache = {}

    def box_of(node):
        key = id(node)
        v = cache.get(key)
        if v is None:
            v = node.box.transformed(relative)
            cache[key] = v
        return v

    stack = [(a.root, b.root, box_of(a.root).distance(b.root.box))]
    tri_cache = {}
    while stack:
        # process the most promising pair first
        stack.sort(key=lambda item: item[2], reverse=True)
        na, nb, lower = stack.pop()
        if lower >= best:
            continue
        if na.leaf and nb.leaf:
            for i in na.triangles:
                ta = tri_cache.get(i)
                if ta is None:
                    ta = tuple(relative.apply(p) for p in a.mesh.triangle(i))
                    tri_cache[i] = ta
                for j in nb.triangles:
                    d, pts = triangle_distance(ta, b.mesh.triangle(j))
                    if d < best:
                        best, pair = d, pts
                        if best <= 0.0:
                            return 0.0, pair[0], pair[1]
            continue
        if na.leaf or (not nb.leaf and _vol(nb.box) > _vol(box_of(na))):
            children = [(na, nb.left), (na, nb.right)]
        else:
            children = [(na.left, nb), (na.right, nb)]
        for ca, cb in children:
            d = box_of(ca).distance(cb.box)
            if d < best:
                stack.append((ca, cb, d))
    return best, pair[0], pair[1]


def _vol(box):
    s = box.size
    return max(s[0], 0.0) * max(s[1], 0.0) * max(s[2], 0.0)
