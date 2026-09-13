# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mating features: the planes, axes and points a mate is placed between.

A :class:`PlaneFeature` is a planar face (origin, outward normal, an extent
radius for overlap tests); an :class:`AxisFeature` is a cylindrical face or a
circular edge (a point on the axis, its direction, radius, length); a
:class:`PointFeature` is a vertex or a circle centre. All are in the part's
local frame; :meth:`Features.world` maps them through a pose.

Two extractors: :func:`from_shape` reads them off a FreeCAD ``Part.Shape``
(exact — the surface types are known), and :func:`from_mesh` recovers them
from a triangle mesh by clustering coplanar triangles and fitting cylinders
to the curved patches (approximate, for imported STLs and scans).
"""

import math

from xrsketch import vecmath as vm


class Feature(object):
    kind = ""
    __slots__ = ("name", "origin", "direction", "radius", "extent", "source", "_local")

    def __init__(self, name, origin, direction=(0.0, 0.0, 1.0), radius=0.0, extent=0.0, source=None):
        self._local = None
        self.name = name
        self.origin = vm.vec3(origin)
        self.direction = vm.normalize(direction, (0.0, 0.0, 1.0))
        self.radius = float(radius)
        self.extent = float(extent)
        #: what this came from: ("Face", 3) for FreeCAD, ("patch", k) for meshes
        self.source = source

    def transformed(self, pose):
        cls = type(self)
        return cls(self.name, pose.apply(self.origin), pose.apply_vector(self.direction),
                   self.radius * pose.scale, self.extent * pose.scale, self.source)

    def to_dict(self):
        return {"kind": self.kind, "name": self.name, "origin": list(self.origin),
                "direction": list(self.direction), "radius": self.radius, "extent": self.extent,
                "source": list(self.source) if self.source else None}

    def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.name)


class PlaneFeature(Feature):
    kind = "plane"

    @property
    def normal(self):
        return self.direction

    def offset(self):
        """Signed distance of the plane from the origin along its normal."""
        return vm.dot(self.normal, self.origin)

    def distance_to_point(self, p):
        return vm.dot(self.normal, vm.sub(p, self.origin))


class AxisFeature(Feature):
    kind = "axis"

    def closest_point_on_axis(self, p):
        t = vm.dot(vm.sub(p, self.origin), self.direction)
        return vm.add(self.origin, vm.mul(self.direction, t))

    def distance_to_line(self, other):
        """Distance between this axis line and another (0 when coaxial)."""
        cross = vm.cross(self.direction, other.direction)
        w = vm.sub(other.origin, self.origin)
        if vm.length(cross) < 1e-9:
            return vm.length(vm.sub(w, vm.mul(self.direction, vm.dot(w, self.direction))))
        return abs(vm.dot(w, vm.normalize(cross)))


class PointFeature(Feature):
    kind = "point"

    @property
    def position(self):
        return self.origin


FEATURE_KINDS = {"plane": PlaneFeature, "axis": AxisFeature, "point": PointFeature}


def feature_from_dict(d):
    cls = FEATURE_KINDS[d["kind"]]
    return cls(d["name"], d["origin"], d.get("direction", (0, 0, 1)), d.get("radius", 0.0), d.get("extent", 0.0),
               tuple(d["source"]) if d.get("source") else None)


class Features(object):
    """The mating features of one part, in its local frame."""

    def __init__(self, features=()):
        self.items = list(features)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def get(self, name):
        for f in self.items:
            if f.name == name:
                return f
        return None

    def of_kind(self, kind):
        return [f for f in self.items if f.kind == kind]

    def add(self, feature):
        self.items.append(feature)
        return feature

    def world(self, pose):
        return Features(f.transformed(pose) for f in self.items)

    def to_dict(self):
        return {"features": [f.to_dict() for f in self.items]}

    @classmethod
    def from_dict(cls, d):
        return cls(feature_from_dict(f) for f in d.get("features", []))


# ----------------------------------------------------------------------
# from a FreeCAD shape
# ----------------------------------------------------------------------


def from_shape(shape, min_area=1e-6):
    """Mating features of a ``Part.Shape``: one per planar face, cylindrical
    face and circular edge, named ``Face<n>`` / ``Edge<n>`` like FreeCAD does."""
    features = Features()
    for i, face in enumerate(getattr(shape, "Faces", []) or []):
        surface = getattr(face, "Surface", None)
        kind = type(surface).__name__
        area = float(getattr(face, "Area", 0.0))
        if area < min_area:
            continue
        name = "Face%d" % (i + 1)
        if kind == "Plane":
            centre = face.CenterOfMass
            normal = _normal_at_centre(face)
            features.add(PlaneFeature(name, (centre.x, centre.y, centre.z), normal,
                                      extent=math.sqrt(area / math.pi), source=("Face", i + 1)))
        elif kind == "Cylinder":
            centre, axis = surface.Center, surface.Axis
            bb = face.BoundBox
            length = max(bb.XLength, bb.YLength, bb.ZLength)
            features.add(AxisFeature(name, (centre.x, centre.y, centre.z), (axis.x, axis.y, axis.z),
                                     radius=float(surface.Radius), extent=length, source=("Face", i + 1)))
    for i, edge in enumerate(getattr(shape, "Edges", []) or []):
        curve = getattr(edge, "Curve", None)
        if type(curve).__name__ == "Circle":
            c, a = curve.Center, curve.Axis
            name = "Edge%d" % (i + 1)
            features.add(AxisFeature(name, (c.x, c.y, c.z), (a.x, a.y, a.z), radius=float(curve.Radius),
                                     extent=0.0, source=("Edge", i + 1)))
            features.add(PointFeature(name + ".center", (c.x, c.y, c.z), source=("Edge", i + 1)))
    for i, vertex in enumerate(getattr(shape, "Vertexes", []) or []):
        p = vertex.Point
        features.add(PointFeature("Vertex%d" % (i + 1), (p.x, p.y, p.z), source=("Vertex", i + 1)))
    return features


def _normal_at_centre(face):
    try:
        u0, u1, v0, v1 = face.ParameterRange
        n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        return (n.x, n.y, n.z)
    except Exception:
        return (0.0, 0.0, 1.0)


# ----------------------------------------------------------------------
# from a mesh
# ----------------------------------------------------------------------


def from_mesh(mesh, angle_tol_deg=2.0, plane_tol=1e-4, min_area=0.0, cylinder=True, smooth_deg=35.0):
    """Recover planes (and cylinders, when ``cylinder``) from a triangle mesh.

    Pass one clusters edge-connected coplanar triangles into patches. Pass
    two merges patches that meet at a shallow dihedral angle (under
    ``smooth_deg``) — the facets of a tessellated curved surface — into
    curved groups, which are then fitted as cylinders. A patch that meets
    all its neighbours at sharp angles is a planar face. Two genuinely planar
    faces meeting at less than ``smooth_deg`` (a shallow wedge) are merged
    too; that is the price of not knowing the surface types.
    """
    n_tris = len(mesh)
    if n_tris == 0:
        return Features()
    normals = [mesh.normal(i) for i in range(n_tris)]
    cos_tol = math.cos(math.radians(angle_tol_deg))
    edge_map = {}
    for i, (a, b, c) in enumerate(mesh.triangles):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_map.setdefault((min(u, v), max(u, v)), []).append(i)

    def neighbours(i):
        a, b, c = mesh.triangles[i]
        for u, v in ((a, b), (b, c), (c, a)):
            for j in edge_map[(min(u, v), max(u, v))]:
                if j != i:
                    yield j

    # pass one: coplanar patches
    patch_of = [-1] * n_tris
    patches = []
    for seed in range(n_tris):
        if patch_of[seed] >= 0 or vm.length(normals[seed]) < 0.5:
            continue
        pid = len(patches)
        members = [seed]
        patch_of[seed] = pid
        stack = [seed]
        n0, p0 = normals[seed], mesh.triangle(seed)[0]
        while stack:
            i = stack.pop()
            for j in neighbours(i):
                if patch_of[j] < 0 and _coplanar_with(mesh, normals, j, n0, p0, cos_tol, plane_tol):
                    patch_of[j] = pid
                    members.append(j)
                    stack.append(j)
        patches.append(members)

    # pass two: union patches across smooth edges
    parent = list(range(len(patches)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cos_smooth = math.cos(math.radians(smooth_deg))
    for i in range(n_tris):
        for j in neighbours(i):
            pi, pj = patch_of[i], patch_of[j]
            if pi < 0 or pj < 0 or pi == pj:
                continue
            if vm.dot(normals[i], normals[j]) >= cos_smooth:
                parent[find(pi)] = find(pj)
    groups = {}
    for pid in range(len(patches)):
        groups.setdefault(find(pid), []).append(pid)

    features = Features()
    for k, (root, pids) in enumerate(sorted(groups.items())):
        members = [i for pid in pids for i in patches[pid]]
        area = sum(_tri_area(mesh, i) for i in members)
        if area < min_area:
            continue
        if len(pids) == 1:
            centroid = _area_centroid(mesh, members, area)
            n = vm.normalize(_sum(normals[i] for i in members))
            features.add(PlaneFeature("Patch%d" % k, centroid, n, extent=math.sqrt(area / math.pi), source=("patch", k)))
        elif cylinder and len(members) >= 4:
            fit = _fit_cylinder(mesh, members, normals)
            if fit is not None:
                origin, axis, radius, length = fit
                features.add(AxisFeature("Cyl%d" % k, origin, axis, radius=radius, extent=length, source=("patch", k)))
    return features


def _coplanar_with(mesh, normals, j, n0, p0, cos_tol, plane_tol):
    return (vm.dot(normals[j], n0) >= cos_tol and
            abs(vm.dot(n0, vm.sub(mesh.triangle(j)[0], p0))) <= plane_tol * (1.0 + vm.length(p0)))


def _tri_area(mesh, i):
    a, b, c = mesh.triangle(i)
    return 0.5 * vm.length(vm.cross(vm.sub(b, a), vm.sub(c, a)))


def _area_centroid(mesh, members, area):
    total = (0.0, 0.0, 0.0)
    for i in members:
        a, b, c = mesh.triangle(i)
        w = _tri_area(mesh, i)
        centre = vm.mul(vm.add(vm.add(a, b), c), 1.0 / 3.0)
        total = vm.add(total, vm.mul(centre, w))
    return vm.mul(total, 1.0 / area) if area > 0 else total


def _sum(vectors):
    total = (0.0, 0.0, 0.0)
    for v in vectors:
        total = vm.add(total, v)
    return total


def _fit_cylinder(mesh, members, normals, max_rel_error=0.05):
    """Axis = direction most perpendicular to all patch normals; radius from
    the spread of the vertices around it. ``None`` when it is not a cylinder."""
    ns = [normals[i] for i in members]
    cov = [[sum(n[r] * n[c] for n in ns) for c in range(3)] for r in range(3)]
    axis = _smallest_eigenvector(cov)
    if axis is None:
        return None
    # normals must all be ~perpendicular to the axis
    if max(abs(vm.dot(n, axis)) for n in ns) > 0.2:
        return None
    verts = list({v for i in members for v in mesh.triangle(i)})
    centroid = vm.mul(_sum(verts), 1.0 / len(verts))
    radial = []
    for v in verts:
        d = vm.sub(v, centroid)
        radial.append(vm.sub(d, vm.mul(axis, vm.dot(d, axis))))
    # Circle fit in the plane perpendicular to the axis: centre = centroid + c, solve least squares
    # via the normals: each triangle centre lies at distance r along -normal from the axis.
    tri_centres = [vm.mul(vm.add(vm.add(*mesh.triangle(i)[:2]), mesh.triangle(i)[2]), 1.0 / 3.0) for i in members]
    # Axis point estimate: average of (centre - r*normal) needs r; estimate r from the chord geometry first.
    # Use the fact that for points on a cylinder, |p - axis_point|_perp = r: solve linear LS for centre offset.
    u, w = vm.orthonormal_basis(axis)[1:]
    pts2 = [(vm.dot(r, u), vm.dot(r, w)) for r in radial]
    fit = _fit_circle_2d(pts2)
    if fit is None:
        return None
    cx, cy, r = fit
    if r <= 0:
        return None
    errors = [abs(math.hypot(x - cx, y - cy) - r) for x, y in pts2]
    if max(errors) > max_rel_error * r:
        return None
    origin = vm.add(centroid, vm.add(vm.mul(u, cx), vm.mul(w, cy)))
    along = [vm.dot(vm.sub(v, origin), axis) for v in verts]
    length = max(along) - min(along)
    origin = vm.add(origin, vm.mul(axis, 0.5 * (max(along) + min(along))))
    # orient the axis so that the normals point outward (convex cylinder) -> sign is arbitrary; keep as is
    return origin, axis, r, length


def _fit_circle_2d(points):
    """Algebraic (Kåsa) circle fit."""
    n = len(points)
    if n < 3:
        return None
    sx = sum(p[0] for p in points); sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points); syy = sum(p[1] ** 2 for p in points); sxy = sum(p[0] * p[1] for p in points)
    sxxx = sum(p[0] ** 3 for p in points); syyy = sum(p[1] ** 3 for p in points)
    sxyy = sum(p[0] * p[1] ** 2 for p in points); sxxy = sum(p[0] ** 2 * p[1] for p in points)
    a = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]]
    b = [-(sxxx + sxyy), -(sxxy + syyy), -(sxx + syy)]
    sol = _solve3(a, b)
    if sol is None:
        return None
    d, e, f = sol
    cx, cy = -d / 2.0, -e / 2.0
    r2 = cx * cx + cy * cy - f
    return (cx, cy, math.sqrt(r2)) if r2 > 0 else None


def _solve3(a, b):
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, 4):
                    m[r][c] -= f * m[col][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def _smallest_eigenvector(m, sweeps=32):
    """Eigenvector of the smallest eigenvalue of a symmetric 3x3 (Jacobi)."""
    a = [row[:] for row in m]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(sweeps):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pq: abs(a[pq[0]][pq[1]]))
        if abs(a[p][q]) < 1e-14:
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
    idx = min(range(3), key=lambda i: a[i][i])
    return vm.normalize((v[0][idx], v[1][idx], v[2][idx]))
