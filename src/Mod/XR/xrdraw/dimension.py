# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dimensioning by pointing.

A TechDraw view is a set of 2-D vertices, edges and circles on the page.
The picker snaps a page point (where the ray landed) to the nearest one
and collects picks; :func:`infer` turns the picks into a dimension —
two vertices give a distance (X or Y aligned when they nearly are), a
circle a diameter, an arc a radius, two lines an angle (or a distance when
parallel), a vertex and a line a perpendicular distance. Values are
measured in view space and divided by the view scale, so the number on the
sheet is the model's, not the paper's.
"""

import math

TYPES = ("Distance", "DistanceX", "DistanceY", "Diameter", "Radius", "Angle")


class Vertex(object):
    __slots__ = ("index", "x", "y")

    def __init__(self, index, x, y):
        self.index = int(index)
        self.x = float(x)
        self.y = float(y)

    @property
    def name(self):
        return "Vertex%d" % self.index

    @property
    def point(self):
        return (self.x, self.y)


class Edge(object):
    """A line (``start``/``end``) or an arc/circle (``center``, ``radius``, ``closed``)."""

    __slots__ = ("index", "kind", "start", "end", "center", "radius", "closed")

    def __init__(self, index, kind, start=None, end=None, center=None, radius=0.0, closed=False):
        self.index = int(index)
        self.kind = kind  # "line" | "circle" | "arc"
        self.start = start
        self.end = end
        self.center = center
        self.radius = float(radius)
        self.closed = bool(closed)

    @property
    def name(self):
        return "Edge%d" % self.index

    def distance_to(self, p):
        if self.kind == "line":
            return _point_segment_distance(p, self.start, self.end)
        d = math.dist(p, self.center)
        return abs(d - self.radius)

    def direction(self):
        if self.kind != "line":
            return None
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 0 else (1.0, 0.0)


class ViewGeometry(object):
    """A view's 2-D geometry in *page* mm (already placed and scaled), plus its scale."""

    def __init__(self, name, x, y, scale=1.0, vertices=(), edges=()):
        self.name = name
        self.x = float(x)
        self.y = float(y)
        self.scale = float(scale)
        self.vertices = list(vertices)
        self.edges = list(edges)

    def nearest(self, point, tolerance=3.0):
        """The closest vertex, then edge, within ``tolerance`` page mm — or None."""
        best, best_d = None, tolerance
        for v in self.vertices:
            d = math.dist(point, v.point)
            if d < best_d:
                best, best_d = v, d
        if best is not None:
            return best, best_d
        for e in self.edges:
            d = e.distance_to(point)
            if d < best_d:
                best, best_d = e, d
        return (best, best_d) if best is not None else None

    def model_length(self, page_length):
        return page_length / self.scale if self.scale else page_length


class Pick(object):
    __slots__ = ("view", "element", "point")

    def __init__(self, view, element, point):
        self.view = view
        self.element = element
        self.point = point

    @property
    def reference(self):
        return (self.view.name, self.element.name)

    def __repr__(self):
        return "Pick(%s.%s)" % self.reference


class DimensionSpec(object):
    __slots__ = ("type", "references", "value", "label", "text_position", "view")

    def __init__(self, type, references, value, view, text_position=None):
        if type not in TYPES:
            raise ValueError("unknown dimension type %r" % type)
        self.type = type
        self.references = list(references)
        self.value = float(value)
        self.view = view
        self.text_position = text_position
        self.label = self.format()

    def format(self, decimals=2):
        if self.type == "Angle":
            return "%.*f°" % (decimals if self.value % 1 else 0, self.value)
        prefix = {"Diameter": "⌀", "Radius": "R"}.get(self.type, "")
        return "%s%.*f" % (prefix, decimals, self.value)

    def to_dict(self):
        return {"type": self.type, "references": list(self.references), "value": self.value, "label": self.label,
                "view": self.view, "text_position": None if self.text_position is None else list(self.text_position)}

    def __repr__(self):
        return "DimensionSpec(%s %s %s)" % (self.type, self.label, self.references)


class InferenceError(ValueError):
    pass


def infer(picks, align_tolerance_deg=5.0, text_offset=8.0):
    """A :class:`DimensionSpec` from one or two picks (see the module docstring)."""
    if not picks:
        raise InferenceError("pick something first")
    view = picks[0].view
    if any(p.view is not view for p in picks):
        raise InferenceError("both picks must be on the same view")
    elements = [p.element for p in picks]
    if len(picks) == 1:
        e = elements[0]
        if isinstance(e, Edge) and e.kind == "circle":
            return DimensionSpec("Diameter", [picks[0].reference], view.model_length(2 * e.radius), view.name,
                                 (e.center[0] + e.radius + text_offset, e.center[1] + text_offset))
        if isinstance(e, Edge) and e.kind == "arc":
            return DimensionSpec("Radius", [picks[0].reference], view.model_length(e.radius), view.name,
                                 (e.center[0] + e.radius * 0.7, e.center[1] + e.radius * 0.7))
        if isinstance(e, Edge) and e.kind == "line":
            return _distance(picks[0].reference, e.start, e.end, view, align_tolerance_deg, text_offset, single=True)
        raise InferenceError("one vertex is not enough; pick a second one")
    a, b = elements[0], elements[1]
    if isinstance(a, Vertex) and isinstance(b, Vertex):
        if a.index == b.index:
            raise InferenceError("pick two different vertices")
        return _distance([picks[0].reference, picks[1].reference], a.point, b.point, view, align_tolerance_deg, text_offset)
    if isinstance(a, Edge) and isinstance(b, Edge) and a.kind == "line" and b.kind == "line":
        da, db = a.direction(), b.direction()
        cross = da[0] * db[1] - da[1] * db[0]
        dot = da[0] * db[0] + da[1] * db[1]
        angle = math.degrees(math.atan2(abs(cross), dot))
        if angle < align_tolerance_deg or abs(180.0 - angle) < align_tolerance_deg:
            # parallel lines: the distance between them
            d = _point_line_distance(b.start, a.start, a.end)
            mid = ((a.start[0] + b.start[0]) / 2.0, (a.start[1] + b.start[1]) / 2.0)
            return DimensionSpec("Distance", [picks[0].reference, picks[1].reference], view.model_length(d), view.name, mid)
        if angle > 90.0:
            angle = 180.0 - angle
        p = _intersection(a, b)
        return DimensionSpec("Angle", [picks[0].reference, picks[1].reference], angle, view.name,
                             None if p is None else (p[0] + text_offset, p[1] + text_offset))
    if isinstance(a, Vertex) and isinstance(b, Edge) and b.kind == "line" or isinstance(b, Vertex) and isinstance(a, Edge) and a.kind == "line":
        v, e = (a, b) if isinstance(a, Vertex) else (b, a)
        d = _point_line_distance(v.point, e.start, e.end)
        return DimensionSpec("Distance", [picks[0].reference, picks[1].reference], view.model_length(d), view.name,
                             (v.x + text_offset, v.y + text_offset))
    if isinstance(a, Edge) and isinstance(b, Edge) and a.kind != "line" and b.kind != "line":
        d = math.dist(a.center, b.center)
        return DimensionSpec("Distance", [picks[0].reference, picks[1].reference], view.model_length(d), view.name,
                             ((a.center[0] + b.center[0]) / 2.0, (a.center[1] + b.center[1]) / 2.0 + text_offset))
    raise InferenceError("cannot dimension a %s against a %s" % (_kind(a), _kind(b)))


def _kind(e):
    return "vertex" if isinstance(e, Vertex) else e.kind


def _distance(refs, p, q, view, tol_deg, text_offset, single=False):
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise InferenceError("the two points coincide")
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    kind = "Distance"
    if angle < tol_deg:
        kind, length = "DistanceX", abs(dx)
    elif angle > 90.0 - tol_deg:
        kind, length = "DistanceY", abs(dy)
    refs = refs if isinstance(refs, list) else [refs]
    mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
    nx, ny = (-dy / math.hypot(dx, dy), dx / math.hypot(dx, dy))
    text = (mid[0] + nx * text_offset, mid[1] + ny * text_offset)
    return DimensionSpec(kind, refs, view.model_length(length), view.name, text)


def _point_segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _point_line_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return math.dist(p, a)
    return abs(dy * (p[0] - a[0]) - dx * (p[1] - a[1])) / n


def _intersection(a, b):
    x1, y1 = a.start
    x2, y2 = a.end
    x3, y3 = b.start
    x4, y4 = b.end
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
