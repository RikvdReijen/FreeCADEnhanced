# SPDX-License-Identifier: LGPL-2.1-or-later
"""Aligning a scanned mesh to the model — or the model to the scan.

Three estimators, all returning an :class:`xrsketch.vecmath.Transform` that
maps *source* points onto *target* points:

* :func:`kabsch` — rigid (or similarity, with ``scale=True``) alignment from
  point correspondences: the three (or more) pairs a user picks by touching
  the same feature on the scan and on the model.
* :func:`icp` — iterative closest point refinement of an initial pose
  against a target mesh, point-to-plane when the target has normals.
* :func:`fit_plane` — RANSAC plane through a point set, for sitting a scan
  on the build plate or the floor.

And two conveniences that come up constantly with scans: :func:`scale_from_known_length`
(two picked points and the tape-measure figure), and :func:`principal_axes`
for a first guess when nothing has been picked yet.

Pure Python. ICP on a 500k-point scan is not interactive here; the session
subsamples to a few thousand points, which is plenty for alignment.
"""

import math
import random

from xrsketch import vecmath as vm

from xrfit.bvh import BVH
from xrfit.collide import closest_point_on_triangle


class AlignmentError(ValueError):
    pass


class AlignResult(object):
    __slots__ = ("transform", "rms", "iterations", "inliers", "notes")

    def __init__(self, transform, rms, iterations=0, inliers=None, notes=()):
        self.transform = transform
        self.rms = float(rms)
        self.iterations = int(iterations)
        self.inliers = inliers
        self.notes = list(notes)

    def to_dict(self):
        return {"transform": self.transform.to_dict(), "rms": self.rms, "iterations": self.iterations,
                "inliers": self.inliers, "notes": list(self.notes)}

    def __repr__(self):
        return "AlignResult(rms=%.4g, %d iterations)" % (self.rms, self.iterations)


# ----------------------------------------------------------------------
# small linear algebra
# ----------------------------------------------------------------------


def _centroid(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)


def _mat3_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mat3_t(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _mat3_det(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _jacobi(a, sweeps=50):
    """Eigen-decomposition of a symmetric 3x3: ``(values, vectors_as_columns)``."""
    a = [row[:] for row in a]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(sweeps):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pq: abs(a[pq[0]][pq[1]]))
        if abs(a[p][q]) < 1e-15:
            break
        theta = 0.5 * math.atan2(2.0 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(theta), math.sin(theta)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[i][i] for i in range(3)], v


def _polar_rotation(h):
    """The rotation R closest to H (R = H (HᵀH)^-1/2), via the eigen system of HᵀH."""
    hth = _mat3_mul(_mat3_t(h), h)
    values, vecs = _jacobi(hth)
    # (HᵀH)^-1/2 = V diag(1/sqrt(λ)) Vᵀ
    inv_sqrt = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            inv_sqrt[i][j] = sum(vecs[i][k] * (1.0 / math.sqrt(max(values[k], 1e-18))) * vecs[j][k] for k in range(3))
    r = _mat3_mul(h, inv_sqrt)
    if _mat3_det(r) < 0:
        # reflection: flip the axis of the smallest singular value
        k = min(range(3), key=lambda i: values[i])
        for i in range(3):
            for j in range(3):
                r[i][j] -= 2.0 * sum(h[i][m] * vecs[m][k] for m in range(3)) / math.sqrt(max(values[k], 1e-18)) * vecs[j][k]
        if _mat3_det(r) < 0:
            r = [[-x for x in row] for row in r]
    return r


def _quat_from_mat3(m):
    return vm.quat_from_mat3(m)


# ----------------------------------------------------------------------
# Kabsch / Umeyama
# ----------------------------------------------------------------------


def kabsch(source, target, scale=False, weights=None):
    """Least-squares transform mapping ``source`` points onto ``target``.

    Rigid by default; ``scale=True`` also solves for a uniform scale
    (Umeyama). Needs at least three non-collinear pairs.
    """
    if len(source) != len(target):
        raise AlignmentError("source and target need the same number of points")
    n = len(source)
    if n < 3:
        raise AlignmentError("need at least three correspondences, got %d" % n)
    w = [1.0] * n if weights is None else [float(x) for x in weights]
    total = sum(w)
    if total <= 0:
        raise AlignmentError("weights sum to zero")
    cs = tuple(sum(w[i] * source[i][k] for i in range(n)) / total for k in range(3))
    ct = tuple(sum(w[i] * target[i][k] for i in range(n)) / total for k in range(3))
    h = [[0.0] * 3 for _ in range(3)]
    var_s = 0.0
    for i in range(n):
        s = vm.sub(source[i], cs)
        t = vm.sub(target[i], ct)
        var_s += w[i] * vm.dot(s, s)
        for r in range(3):
            for c in range(3):
                h[r][c] += w[i] * s[r] * t[c]
    if var_s < 1e-18:
        raise AlignmentError("source points are coincident")
    # H = Σ s tᵀ ; rotation R = argmax tr(R H) -> polar factor of Hᵀ
    ht = _mat3_t(h)
    hth = _mat3_mul(h, ht)
    values, _ = _jacobi(hth)
    if sorted(values)[1] < 1e-12 * max(values):
        raise AlignmentError("correspondences are collinear; pick a third point off the line")
    rot = _polar_rotation(ht)
    q = _quat_from_mat3(rot)
    factor = 1.0
    if scale:
        # s = Σ σ_i / var_s with the reflection-corrected singular values = tr(R H)
        trace = sum(rot[r][c] * ht[r][c] for r in range(3) for c in range(3))
        factor = trace / var_s
        if factor <= 0:
            raise AlignmentError("degenerate scale")
    rotated = vm.Transform((0, 0, 0), q, factor).apply(cs)
    translation = vm.sub(ct, rotated)
    transform = vm.Transform(translation, q, factor)
    rms = math.sqrt(sum(w[i] * vm.dist(transform.apply(source[i]), target[i]) ** 2 for i in range(n)) / total)
    return AlignResult(transform, rms, 1)


# ----------------------------------------------------------------------
# ICP
# ----------------------------------------------------------------------


def _subsample(points, count, seed=0):
    if len(points) <= count:
        return list(points)
    rng = random.Random(seed)
    return rng.sample(list(points), count)


def closest_on_mesh(bvh, point, upper=float("inf")):
    """Closest point on the mesh to ``point`` and its triangle index."""
    best, best_pt, best_tri = upper, None, None
    stack = [(bvh.root, _box_point_distance(bvh.root.box, point))]
    while stack:
        stack.sort(key=lambda item: item[1], reverse=True)
        node, lower = stack.pop()
        if lower >= best:
            continue
        if node.leaf:
            for i in node.triangles:
                q = closest_point_on_triangle(point, bvh.mesh.triangle(i))
                d = vm.dist(point, q)
                if d < best:
                    best, best_pt, best_tri = d, q, i
            continue
        for child in (node.left, node.right):
            d = _box_point_distance(child.box, point)
            if d < best:
                stack.append((child, d))
    return best, best_pt, best_tri


def _box_point_distance(box, p):
    d2 = 0.0
    for i in range(3):
        if p[i] < box.lo[i]:
            d2 += (box.lo[i] - p[i]) ** 2
        elif p[i] > box.hi[i]:
            d2 += (p[i] - box.hi[i]) ** 2
    return math.sqrt(d2)


def icp(source_points, target, initial=None, iterations=30, tolerance=1e-6, max_pairs=2000,
        reject_distance=None, scale=False, seed=0):
    """Refine ``initial`` so ``source_points`` sit on the ``target`` mesh.

    Point-to-point ICP with closest points found through the target's BVH,
    a per-iteration outlier cut at ``reject_distance`` (default: 3× the
    median), and Kabsch for the update. Stops when the RMS improves by less
    than ``tolerance``.
    """
    bvh = target if isinstance(target, BVH) else BVH(target)
    transform = initial or vm.Transform.identity()
    sample = _subsample(source_points, max_pairs, seed)
    if len(sample) < 3:
        raise AlignmentError("need at least three source points")
    last_rms = None
    notes = []
    for it in range(1, iterations + 1):
        moved = [transform.apply(p) for p in sample]
        pairs = []
        for p in moved:
            d, q, _ = closest_on_mesh(bvh, p)
            if q is not None:
                pairs.append((d, p, q))
        if len(pairs) < 3:
            raise AlignmentError("no closest points found")
        dists = sorted(d for d, _, _ in pairs)
        cut = reject_distance if reject_distance is not None else 3.0 * dists[len(dists) // 2] + 1e-12
        kept = [(p, q) for d, p, q in pairs if d <= cut]
        if len(kept) < 3:
            kept = [(p, q) for _, p, q in pairs]
        rms = math.sqrt(sum(vm.dist(p, q) ** 2 for p, q in kept) / len(kept))
        if last_rms is not None and abs(last_rms - rms) < tolerance:
            return AlignResult(transform, rms, it, len(kept), notes)
        last_rms = rms
        step = kabsch([p for p, _ in kept], [q for _, q in kept], scale=scale).transform
        transform = vm.compose(step, transform)
    notes.append("did not converge in %d iterations" % iterations)
    return AlignResult(transform, last_rms if last_rms is not None else float("inf"), iterations, None, notes)


# ----------------------------------------------------------------------
# planes and axes
# ----------------------------------------------------------------------


def fit_plane(points, iterations=200, threshold=None, seed=0):
    """RANSAC plane: ``(origin, normal, inlier_indices)``. The normal points
    towards the side with fewer points (the outside of a scanned floor)."""
    pts = list(points)
    if len(pts) < 3:
        raise AlignmentError("need at least three points")
    if threshold is None:
        lo, hi = _bounds(pts)
        threshold = 0.005 * max(hi[i] - lo[i] for i in range(3))
    rng = random.Random(seed)
    best = None
    for _ in range(iterations):
        a, b, c = rng.sample(pts, 3)
        n = vm.normalize(vm.cross(vm.sub(b, a), vm.sub(c, a)))
        if vm.length(n) < 0.5:
            continue
        inliers = [i for i, p in enumerate(pts) if abs(vm.dot(n, vm.sub(p, a))) <= threshold]
        if best is None or len(inliers) > len(best[2]):
            best = (a, n, inliers)
    if best is None:
        raise AlignmentError("all sampled triples were degenerate")
    # refine with the inliers' covariance
    inl = [pts[i] for i in best[2]]
    origin = _centroid(inl)
    cov = [[sum((p[r] - origin[r]) * (p[c] - origin[c]) for p in inl) for c in range(3)] for r in range(3)]
    values, vecs = _jacobi(cov)
    k = min(range(3), key=lambda i: values[i])
    normal = vm.normalize((vecs[0][k], vecs[1][k], vecs[2][k]))
    above = sum(1 for p in pts if vm.dot(normal, vm.sub(p, origin)) > threshold)
    below = sum(1 for p in pts if vm.dot(normal, vm.sub(p, origin)) < -threshold)
    if above > below:
        normal = vm.neg(normal)
    return origin, normal, best[2]


def _bounds(points):
    return (tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3)))


def principal_axes(points):
    """Centroid and the three principal directions (largest variance first)."""
    pts = list(points)
    c = _centroid(pts)
    cov = [[sum((p[r] - c[r]) * (p[k] - c[k]) for p in pts) for k in range(3)] for r in range(3)]
    values, vecs = _jacobi(cov)
    order = sorted(range(3), key=lambda i: -values[i])
    axes = [vm.normalize((vecs[0][i], vecs[1][i], vecs[2][i])) for i in order]
    if vm.dot(vm.cross(axes[0], axes[1]), axes[2]) < 0:
        axes[2] = vm.neg(axes[2])
    return c, axes


def plane_to_plane(origin, normal, target_origin, target_normal):
    """Transform putting plane (origin, normal) onto the target plane."""
    from xrassembly.mates import rotation_between

    q = rotation_between(normal, target_normal)
    rotated = vm.Transform((0, 0, 0), q).apply(origin)
    return vm.Transform(vm.sub(target_origin, rotated), q)


def scale_from_known_length(p1, p2, known_length):
    """The uniform scale that makes the distance between two picked points
    equal a measured length — the fix for a scan exported in the wrong unit."""
    d = vm.dist(p1, p2)
    if d <= 0:
        raise AlignmentError("the two points coincide")
    if known_length <= 0:
        raise AlignmentError("the known length must be positive")
    return float(known_length) / d
