# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry backend abstraction.

Node functions never import ``Part`` directly.  They talk to a backend object
which is either :class:`PartBackend` (FreeCAD's OpenCASCADE kernel) or
:class:`StubBackend`, a light-weight stand-in that tracks bounding boxes so
the graph engine, the XR bridge and the unit tests run without FreeCAD.
"""

import math


class Vec3:
    """Tiny immutable 3-vector used for points and vectors in graph values."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @classmethod
    def coerce(cls, value):
        if isinstance(value, Vec3):
            return value
        if value is None:
            return None
        if isinstance(value, dict):
            return cls(value.get("x", 0), value.get("y", 0), value.get("z", 0))
        if isinstance(value, (int, float)):
            return cls(value, value, value)
        if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
            return cls(value.x, value.y, value.z)
        seq = list(value)
        if len(seq) == 3 and all(isinstance(v, (int, float)) for v in seq):
            return cls(*seq)
        if len(seq) == 2 and all(isinstance(v, (int, float)) for v in seq):
            return cls(seq[0], seq[1], 0.0)
        return [cls.coerce(v) for v in seq]

    def __add__(self, other):
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, factor):
        return Vec3(self.x * factor, self.y * factor, self.z * factor)

    __rmul__ = __mul__

    def __neg__(self):
        return Vec3(-self.x, -self.y, -self.z)

    def __eq__(self, other):
        return isinstance(other, Vec3) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __hash__(self):
        return hash((self.x, self.y, self.z))

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other):
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self):
        return math.sqrt(self.dot(self))

    def normalized(self):
        n = self.length()
        return Vec3(self.x / n, self.y / n, self.z / n) if n > 0 else Vec3()

    def distance(self, other):
        return (self - other).length()

    def to_json(self):
        return [self.x, self.y, self.z]

    def to_tuple(self):
        return (self.x, self.y, self.z)

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.z

    def __repr__(self):
        return "Vec3(%.4g, %.4g, %.4g)" % (self.x, self.y, self.z)


def rotate_point(point, center, axis, angle_deg):
    """Rodrigues rotation of ``point`` around ``axis`` through ``center``."""
    axis = axis.normalized()
    theta = math.radians(angle_deg)
    v = point - center
    c, s = math.cos(theta), math.sin(theta)
    rotated = v * c + axis.cross(v) * s + axis * (axis.dot(v) * (1 - c))
    return center + rotated


class StubShape:
    """Bounding-box only stand-in for a ``Part.Shape``."""

    __slots__ = ("kind", "bbox", "meta")

    def __init__(self, kind, bbox, meta=None):
        self.kind = kind
        self.bbox = tuple(float(v) for v in bbox)
        self.meta = dict(meta or {})

    @property
    def is_null(self):
        return self.bbox[3] < self.bbox[0]

    def __repr__(self):
        return "StubShape(%s, %s)" % (self.kind, ", ".join("%.3g" % v for v in self.bbox))


def _bbox_of_points(points):
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    zs = [p.z for p in points]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _bbox_union(boxes):
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return (0, 0, 0, -1, -1, -1)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        min(b[2] for b in boxes),
        max(b[3] for b in boxes),
        max(b[4] for b in boxes),
        max(b[5] for b in boxes),
    )


def _bbox_corners(b):
    return [Vec3(x, y, z) for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]


class StubBackend:
    """Geometry backend that only tracks bounding boxes (no kernel needed)."""

    name = "stub"

    def is_shape(self, obj):
        return isinstance(obj, StubShape)

    # primitives -----------------------------------------------------------
    def box(self, length, width, height, origin=None):
        o = origin or Vec3()
        return StubShape(
            "box",
            (o.x, o.y, o.z, o.x + length, o.y + width, o.z + height),
            {"volume": length * width * height},
        )

    def cylinder(self, radius, height, origin=None, direction=None):
        o = origin or Vec3()
        return StubShape(
            "cylinder",
            (o.x - radius, o.y - radius, o.z, o.x + radius, o.y + radius, o.z + height),
            {"volume": math.pi * radius**2 * height},
        )

    def sphere(self, radius, center=None):
        c = center or Vec3()
        return StubShape(
            "sphere",
            (c.x - radius, c.y - radius, c.z - radius, c.x + radius, c.y + radius, c.z + radius),
            {"volume": 4.0 / 3.0 * math.pi * radius**3},
        )

    def cone(self, radius1, radius2, height, origin=None):
        o = origin or Vec3()
        r = max(radius1, radius2)
        return StubShape(
            "cone",
            (o.x - r, o.y - r, o.z, o.x + r, o.y + r, o.z + height),
            {"volume": math.pi * height / 3.0 * (radius1**2 + radius1 * radius2 + radius2**2)},
        )

    def vertex(self, point):
        return StubShape("vertex", (point.x, point.y, point.z, point.x, point.y, point.z))

    def line(self, p1, p2):
        return StubShape("line", _bbox_of_points([p1, p2]), {"length": p1.distance(p2)})

    def polyline(self, points, closed=False):
        if len(points) < 2:
            raise ValueError("polyline needs at least two points")
        length = sum(points[i].distance(points[i + 1]) for i in range(len(points) - 1))
        if closed:
            length += points[-1].distance(points[0])
        return StubShape("polyline", _bbox_of_points(points), {"length": length, "closed": closed})

    def circle(self, center, radius, normal=None):
        return StubShape(
            "circle",
            (
                center.x - radius,
                center.y - radius,
                center.z,
                center.x + radius,
                center.y + radius,
                center.z,
            ),
            {"length": 2 * math.pi * radius},
        )

    def rectangle(self, width, height, origin=None):
        o = origin or Vec3()
        return StubShape(
            "rectangle",
            (o.x, o.y, o.z, o.x + width, o.y + height, o.z),
            {"length": 2 * (width + height), "closed": True},
        )

    def polygon(self, center, radius, sides):
        return StubShape(
            "polygon",
            (
                center.x - radius,
                center.y - radius,
                center.z,
                center.x + radius,
                center.y + radius,
                center.z,
            ),
            {"closed": True, "sides": sides},
        )

    # operations ------------------------------------------------------------
    def extrude(self, shape, vector):
        b = shape.bbox
        moved = (
            b[0] + vector.x,
            b[1] + vector.y,
            b[2] + vector.z,
            b[3] + vector.x,
            b[4] + vector.y,
            b[5] + vector.z,
        )
        return StubShape("extrusion", _bbox_union([b, moved]))

    def revolve(self, shape, center, axis, angle):
        b = shape.bbox
        pts = []
        for corner in _bbox_corners(b):
            for a in (0, angle * 0.25, angle * 0.5, angle * 0.75, angle):
                pts.append(rotate_point(corner, center, axis, a))
        return StubShape("revolution", _bbox_of_points(pts))

    def translate(self, shape, vector):
        b = shape.bbox
        return StubShape(
            shape.kind,
            (
                b[0] + vector.x,
                b[1] + vector.y,
                b[2] + vector.z,
                b[3] + vector.x,
                b[4] + vector.y,
                b[5] + vector.z,
            ),
            shape.meta,
        )

    def rotate(self, shape, center, axis, angle):
        pts = [rotate_point(c, center, axis, angle) for c in _bbox_corners(shape.bbox)]
        return StubShape(shape.kind, _bbox_of_points(pts), shape.meta)

    def scale(self, shape, center, factor):
        pts = [center + (c - center) * factor for c in _bbox_corners(shape.bbox)]
        return StubShape(shape.kind, _bbox_of_points(pts), shape.meta)

    def mirror(self, shape, base, normal):
        n = normal.normalized()
        pts = []
        for c in _bbox_corners(shape.bbox):
            d = (c - base).dot(n)
            pts.append(c - n * (2 * d))
        return StubShape(shape.kind, _bbox_of_points(pts), shape.meta)

    def fuse(self, shapes):
        shapes = [s for s in shapes if s is not None]
        if not shapes:
            raise ValueError("nothing to fuse")
        return StubShape("fusion", _bbox_union([s.bbox for s in shapes]))

    def cut(self, base, tools):
        return StubShape("cut", base.bbox)

    def common(self, a, b):
        ba, bb = a.bbox, b.bbox
        box = (
            max(ba[0], bb[0]),
            max(ba[1], bb[1]),
            max(ba[2], bb[2]),
            min(ba[3], bb[3]),
            min(ba[4], bb[4]),
            min(ba[5], bb[5]),
        )
        return StubShape("common", box)

    def compound(self, shapes):
        return StubShape("compound", _bbox_union([s.bbox for s in shapes if s is not None]))

    def offset2d(self, shape, distance):
        b = shape.bbox
        return StubShape(
            "offset",
            (b[0] - distance, b[1] - distance, b[2], b[3] + distance, b[4] + distance, b[5]),
        )

    # queries ---------------------------------------------------------------
    def bbox(self, shape):
        return shape.bbox

    def center(self, shape):
        b = shape.bbox
        return Vec3((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0, (b[2] + b[5]) / 2.0)

    def volume(self, shape):
        return float(shape.meta.get("volume", 0.0))

    def length(self, shape):
        return float(shape.meta.get("length", 0.0))

    def describe(self, shape):
        b = shape.bbox
        return "%s %.3g x %.3g x %.3g" % (shape.kind, b[3] - b[0], b[4] - b[1], b[5] - b[2])

    def tessellate(self, shape, tolerance=0.5):
        """Return (vertices, triangles) of the bounding box."""
        b = shape.bbox
        if shape.is_null:
            return [], []
        corners = [
            (b[0], b[1], b[2]),
            (b[3], b[1], b[2]),
            (b[3], b[4], b[2]),
            (b[0], b[4], b[2]),
            (b[0], b[1], b[5]),
            (b[3], b[1], b[5]),
            (b[3], b[4], b[5]),
            (b[0], b[4], b[5]),
        ]
        faces = [
            (0, 2, 1),
            (0, 3, 2),
            (4, 5, 6),
            (4, 6, 7),
            (0, 1, 5),
            (0, 5, 4),
            (1, 2, 6),
            (1, 6, 5),
            (2, 3, 7),
            (2, 7, 6),
            (3, 0, 4),
            (3, 4, 7),
        ]
        return corners, faces

    def edges(self, shape, tolerance=0.5):
        """Return polylines (lists of xyz tuples) outlining the shape."""
        b = shape.bbox
        if shape.is_null:
            return []
        z0, z1 = b[2], b[5]
        rect = lambda z: [
            (b[0], b[1], z),
            (b[3], b[1], z),
            (b[3], b[4], z),
            (b[0], b[4], z),
            (b[0], b[1], z),
        ]  # noqa: E731
        lines = [rect(z0)]
        if z1 != z0:
            lines.append(rect(z1))
            for x, y in ((b[0], b[1]), (b[3], b[1]), (b[3], b[4]), (b[0], b[4])):
                lines.append([(x, y, z0), (x, y, z1)])
        return lines


class PartBackend:
    """Backend implemented with FreeCAD's ``Part`` module."""

    name = "part"

    def __init__(self):
        import FreeCAD
        import Part

        self.App = FreeCAD
        self.Part = Part

    def _v(self, vec):
        return self.App.Vector(vec.x, vec.y, vec.z)

    def is_shape(self, obj):
        return isinstance(obj, self.Part.Shape)

    def box(self, length, width, height, origin=None):
        return self.Part.makeBox(length, width, height, self._v(origin or Vec3()))

    def cylinder(self, radius, height, origin=None, direction=None):
        return self.Part.makeCylinder(
            radius, height, self._v(origin or Vec3()), self._v(direction or Vec3(0, 0, 1))
        )

    def sphere(self, radius, center=None):
        return self.Part.makeSphere(radius, self._v(center or Vec3()))

    def cone(self, radius1, radius2, height, origin=None):
        return self.Part.makeCone(radius1, radius2, height, self._v(origin or Vec3()))

    def vertex(self, point):
        return self.Part.Vertex(self._v(point))

    def line(self, p1, p2):
        return self.Part.makeLine(self._v(p1), self._v(p2))

    def polyline(self, points, closed=False):
        pts = [self._v(p) for p in points]
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        return self.Part.makePolygon(pts)

    def circle(self, center, radius, normal=None):
        return self.Part.makeCircle(radius, self._v(center), self._v(normal or Vec3(0, 0, 1)))

    def rectangle(self, width, height, origin=None):
        o = origin or Vec3()
        pts = [o, o + Vec3(width, 0, 0), o + Vec3(width, height, 0), o + Vec3(0, height, 0)]
        return self.polyline(pts, closed=True)

    def polygon(self, center, radius, sides):
        pts = [
            center
            + Vec3(math.cos(2 * math.pi * i / sides), math.sin(2 * math.pi * i / sides), 0) * radius
            for i in range(int(sides))
        ]
        return self.polyline(pts, closed=True)

    def _as_face_if_closed(self, shape):
        if shape.ShapeType == "Wire" and shape.isClosed():
            return self.Part.Face(shape)
        if shape.ShapeType == "Edge" and shape.isClosed():
            return self.Part.Face(self.Part.Wire(shape))
        return shape

    def extrude(self, shape, vector):
        return self._as_face_if_closed(shape).extrude(self._v(vector))

    def revolve(self, shape, center, axis, angle):
        return self._as_face_if_closed(shape).revolve(self._v(center), self._v(axis), angle)

    def translate(self, shape, vector):
        s = shape.copy()
        s.translate(self._v(vector))
        return s

    def rotate(self, shape, center, axis, angle):
        s = shape.copy()
        s.rotate(self._v(center), self._v(axis), angle)
        return s

    def scale(self, shape, center, factor):
        m = self.App.Matrix()
        m.move(-self._v(center))
        m.scale(factor, factor, factor)
        m.move(self._v(center))
        return shape.transformGeometry(m)

    def mirror(self, shape, base, normal):
        return shape.mirror(self._v(base), self._v(normal))

    def fuse(self, shapes):
        shapes = [s for s in shapes if s is not None]
        if not shapes:
            raise ValueError("nothing to fuse")
        if len(shapes) == 1:
            return shapes[0]
        return shapes[0].fuse(shapes[1:]).removeSplitter()

    def cut(self, base, tools):
        return base.cut(tools).removeSplitter()

    def common(self, a, b):
        return a.common(b)

    def compound(self, shapes):
        return self.Part.makeCompound([s for s in shapes if s is not None])

    def offset2d(self, shape, distance):
        return shape.makeOffset2D(distance)

    def bbox(self, shape):
        b = shape.BoundBox
        return (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)

    def center(self, shape):
        c = shape.BoundBox.Center
        return Vec3(c.x, c.y, c.z)

    def volume(self, shape):
        return float(shape.Volume)

    def length(self, shape):
        return float(shape.Length)

    def describe(self, shape):
        b = shape.BoundBox
        return "%s %.3g x %.3g x %.3g" % (shape.ShapeType, b.XLength, b.YLength, b.ZLength)

    def tessellate(self, shape, tolerance=0.5):
        verts, faces = shape.tessellate(tolerance)
        return [(v.x, v.y, v.z) for v in verts], [tuple(f) for f in faces]

    def edges(self, shape, tolerance=0.5):
        lines = []
        for edge in shape.Edges:
            try:
                pts = edge.discretize(Deflection=tolerance)
            except Exception:  # noqa: BLE001
                pts = [edge.firstVertex().Point, edge.lastVertex().Point]
            lines.append([(p.x, p.y, p.z) for p in pts])
        return lines


def default_backend():
    """Return :class:`PartBackend` if FreeCAD is importable, else the stub."""
    try:
        return PartBackend()
    except ImportError:
        return StubBackend()
