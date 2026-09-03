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
"""The VR vector editor document model (ARCHITECTURE.md §4 ``vector``).

Everything here is pure Python: nodes with in/out handles under
corner/smooth/symmetric constraints, path editing operations, z-ordering, a
snapping system and the exact §4 JSON round trip.  Freehand VR strokes are
cleaned up into Bezier paths through :mod:`xrpaint.curve`.
"""

import math

from . import curve

__all__ = [
    "DEFAULT_STROKE",
    "NODE_TYPES",
    "Node",
    "Path",
    "Plane",
    "SnapEngine",
    "SnapResult",
    "SnapSettings",
    "VectorDocument",
    "path_from_stroke",
]

NODE_TYPES = ("corner", "smooth", "symmetric")

DEFAULT_STROKE = {"color": [0.0, 0.0, 0.0, 1.0], "width": 0.5}

_EPS = 1e-12
_COLLINEAR_TOL = 1e-7


def _v(p):
    return (float(p[0]), float(p[1]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _mul(a, s):
    return (a[0] * s, a[1] * s)


def _len(a):
    return math.hypot(a[0], a[1])


def _norm(a):
    n = math.hypot(a[0], a[1])
    if n < _EPS:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


# --------------------------------------------------------------------------
# node
# --------------------------------------------------------------------------

class Node(object):
    """A path node: an anchor point plus two *relative* handles.

    ``handle_in`` points backwards along the path, ``handle_out`` forwards;
    both are stored relative to :attr:`point` exactly as §4 requires, and
    either may be ``None``.

    The node ``type`` is an invariant that the mutators maintain:

    ``corner``      the handles are independent,
    ``smooth``      the handles stay collinear (opposite directions),
    ``symmetric``   ``handle_in == -handle_out``.
    """

    __slots__ = ("point", "handle_in", "handle_out", "type")

    def __init__(self, point, handle_in=None, handle_out=None, type="corner"):
        if type not in NODE_TYPES:
            raise ValueError("unknown node type: %r" % (type,))
        self.point = _v(point)
        self.handle_in = None if handle_in is None else _v(handle_in)
        self.handle_out = None if handle_out is None else _v(handle_out)
        self.type = type

    # -- derived ---------------------------------------------------------
    @property
    def in_point(self):
        """Absolute position of the incoming handle."""
        return _add(self.point, self.handle_in or (0.0, 0.0))

    @property
    def out_point(self):
        """Absolute position of the outgoing handle."""
        return _add(self.point, self.handle_out or (0.0, 0.0))

    def copy(self):
        return Node(self.point, self.handle_in, self.handle_out, self.type)

    # -- mutators keeping the invariant ----------------------------------
    def set_point(self, p):
        """Move the anchor.  Handles are relative, so they follow along."""
        self.point = _v(p)

    def move(self, dx, dy):
        self.point = (self.point[0] + dx, self.point[1] + dy)

    def set_in_point(self, p):
        self.set_handle_in(_sub(_v(p), self.point))

    def set_out_point(self, p):
        self.set_handle_out(_sub(_v(p), self.point))

    def set_handle_out(self, h):
        h = None if h is None else _v(h)
        self.handle_out = h
        if h is None:
            return
        if self.type == "symmetric":
            self.handle_in = _mul(h, -1.0)
        elif self.type == "smooth" and self.handle_in is not None:
            L = _len(self.handle_in)
            d = _norm(h)
            self.handle_in = _mul(d, -L) if L > _EPS else (0.0, 0.0)

    def set_handle_in(self, h):
        h = None if h is None else _v(h)
        self.handle_in = h
        if h is None:
            return
        if self.type == "symmetric":
            self.handle_out = _mul(h, -1.0)
        elif self.type == "smooth" and self.handle_out is not None:
            L = _len(self.handle_out)
            d = _norm(h)
            self.handle_out = _mul(d, -L) if L > _EPS else (0.0, 0.0)

    def set_type(self, type, enforce=True):
        """Change the node type, re-establishing the new constraint."""
        if type not in NODE_TYPES:
            raise ValueError("unknown node type: %r" % (type,))
        self.type = type
        if enforce:
            self.enforce()
        return self

    def enforce(self):
        """Force the handles to satisfy the node's constraint."""
        hin = self.handle_in
        hout = self.handle_out
        if self.type == "corner":
            return self
        if hin is None and hout is None:
            return self
        # the shared direction is the average of 'out' and '-in'
        d_out = _norm(hout) if hout is not None else (0.0, 0.0)
        d_in = _norm(_mul(hin, -1.0)) if hin is not None else (0.0, 0.0)
        d = _add(d_out, d_in)
        if _len(d) < _EPS:
            d = d_out if _len(d_out) > _EPS else d_in
        d = _norm(d)
        if _len(d) < _EPS:
            return self
        if self.type == "symmetric":
            lens = [_len(h) for h in (hout, hin) if h is not None]
            L = sum(lens) / len(lens)
            self.handle_out = _mul(d, L)
            self.handle_in = _mul(d, -L)
        else:  # smooth: keep each length, share the direction
            if hout is not None:
                self.handle_out = _mul(d, _len(hout))
            if hin is not None:
                self.handle_in = _mul(d, -_len(hin))
        return self

    def is_valid(self, tol=1e-6):
        """True when the handles satisfy the node's declared constraint."""
        hin = self.handle_in
        hout = self.handle_out
        if self.type == "corner":
            return True
        if hin is None or hout is None:
            return True
        if self.type == "symmetric":
            return (abs(hin[0] + hout[0]) <= tol
                    and abs(hin[1] + hout[1]) <= tol)
        li = _len(hin)
        lo = _len(hout)
        if li < tol or lo < tol:
            return True
        c = _dot(_norm(hin), _norm(hout))
        return c <= -1.0 + max(tol, _COLLINEAR_TOL)

    def classify(self, tol=1e-6):
        """The strictest type the current handles already satisfy."""
        hin = self.handle_in
        hout = self.handle_out
        if hin is None or hout is None:
            return "corner"
        if _len(hin) < tol or _len(hout) < tol:
            return "corner"
        if abs(hin[0] + hout[0]) <= tol and abs(hin[1] + hout[1]) <= tol:
            return "symmetric"
        if _dot(_norm(hin), _norm(hout)) <= -1.0 + tol:
            return "smooth"
        return "corner"

    def transform(self, mat):
        """Apply a 2x3 affine ``((a, b, tx), (c, d, ty))``."""
        (a, b, tx), (c, d, ty) = mat
        x, y = self.point
        self.point = (a * x + b * y + tx, c * x + d * y + ty)
        for name in ("handle_in", "handle_out"):
            h = getattr(self, name)
            if h is not None:
                setattr(self, name, (a * h[0] + b * h[1],
                                     c * h[0] + d * h[1]))
        return self

    # -- §4 JSON ---------------------------------------------------------
    def to_dict(self):
        return {
            "point": [self.point[0], self.point[1]],
            "in": None if self.handle_in is None
            else [self.handle_in[0], self.handle_in[1]],
            "out": None if self.handle_out is None
            else [self.handle_out[0], self.handle_out[1]],
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["point"], d.get("in"), d.get("out"),
                   d.get("type", "corner"))

    def __eq__(self, other):
        return (isinstance(other, Node) and other.point == self.point
                and other.handle_in == self.handle_in
                and other.handle_out == self.handle_out
                and other.type == self.type)

    def __repr__(self):
        return "Node(%.4g, %.4g, %s)" % (self.point[0], self.point[1],
                                         self.type)


# --------------------------------------------------------------------------
# path
# --------------------------------------------------------------------------

_path_counter = [0]


def _new_path_id():
    _path_counter[0] += 1
    return "p%d" % _path_counter[0]


class Path(object):
    """An open or closed chain of :class:`Node` with stroke/fill styling."""

    def __init__(self, nodes=None, closed=False, id=None, stroke=None,
                 fill=None, target="draft"):
        self.nodes = list(nodes) if nodes else []
        self.closed = bool(closed)
        self.id = id or _new_path_id()
        self.stroke = dict(DEFAULT_STROKE) if stroke is None else dict(stroke)
        self.fill = None if fill is None else dict(fill)
        self.target = target

    # -- container -------------------------------------------------------
    def __len__(self):
        return len(self.nodes)

    def __iter__(self):
        return iter(self.nodes)

    def __getitem__(self, i):
        return self.nodes[i]

    def copy(self):
        p = Path([n.copy() for n in self.nodes], self.closed, self.id,
                 dict(self.stroke), None if self.fill is None
                 else dict(self.fill), self.target)
        return p

    # -- geometry --------------------------------------------------------
    def segment_count(self):
        n = len(self.nodes)
        if n < 2:
            return 0
        return n if self.closed else n - 1

    def to_beziers(self):
        """The cubic segments of this path, in order."""
        out = []
        n = len(self.nodes)
        for i in range(self.segment_count()):
            a = self.nodes[i]
            b = self.nodes[(i + 1) % n]
            out.append((a.point, a.out_point, b.in_point, b.point))
        return out

    @classmethod
    def from_beziers(cls, beziers, closed=False, **kw):
        """Build a path whose segments are exactly ``beziers``."""
        beziers = [tuple(b) for b in beziers]
        p = cls(**kw)
        if not beziers:
            return p
        for i, b in enumerate(beziers):
            p0, c1, c2, p3 = b
            if i == 0:
                p.nodes.append(Node(p0, None, _sub(c1, p0)))
            else:
                p.nodes[-1].handle_out = _sub(c1, p.nodes[-1].point)
            p.nodes.append(Node(p3, _sub(c2, p3), None))
        if closed:
            first = p.nodes[0]
            last = p.nodes[-1]
            if _len(_sub(first.point, last.point)) < 1e-9:
                first.handle_in = last.handle_in
                p.nodes.pop()
            p.closed = True
        for nd in p.nodes:
            nd.type = nd.classify()
        return p

    def bbox(self):
        """``(xmin, ymin, xmax, ymax)`` or ``None`` for an empty path."""
        segs = self.to_beziers()
        if not segs:
            if not self.nodes:
                return None
            x, y = self.nodes[0].point
            return (x, y, x, y)
        boxes = [curve.bezier_bbox(b) for b in segs]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def length(self):
        return curve.path_length(self.to_beziers())

    def flatten(self, tol=0.05):
        pts = curve.flatten_path(self.to_beziers(), tol)
        return pts

    # -- editing ---------------------------------------------------------
    def append_node(self, node):
        self.nodes.append(node)
        return node

    def insert_node(self, index, node):
        self.nodes.insert(max(0, min(int(index), len(self.nodes))), node)
        return node

    def delete_node(self, index):
        """Remove a node; an open path with < 2 nodes stays valid but empty."""
        if not (0 <= index < len(self.nodes)):
            raise IndexError("no such node: %r" % (index,))
        node = self.nodes.pop(index)
        if self.closed and len(self.nodes) < 3:
            self.closed = False
        return node

    def split_segment(self, seg_index, t):
        """Insert a node inside segment ``seg_index`` at parameter ``t``.

        The shape of the path is preserved exactly (de Casteljau split).
        Returns the index of the new node.
        """
        n = len(self.nodes)
        if not (0 <= seg_index < self.segment_count()):
            raise IndexError("no such segment: %r" % (seg_index,))
        a = self.nodes[seg_index]
        bi = (seg_index + 1) % n
        b = self.nodes[bi]
        bez = (a.point, a.out_point, b.in_point, b.point)
        left, right = curve.bezier_split(bez, float(t))
        a.handle_out = _sub(left[1], a.point)
        b.handle_in = _sub(right[2], b.point)
        mid = Node(left[3], _sub(left[2], left[3]), _sub(right[1], left[3]),
                   "corner")
        mid.type = mid.classify(1e-9)
        self.nodes.insert(seg_index + 1, mid)
        return seg_index + 1

    def split_at_node(self, index):
        """Split the path at a node.  Returns a list of resulting paths.

        A closed path becomes one open path starting and ending at ``index``;
        an open path becomes two paths sharing the node.
        """
        if not (0 <= index < len(self.nodes)):
            raise IndexError("no such node: %r" % (index,))
        if self.closed:
            nodes = [self.nodes[(index + i) % len(self.nodes)].copy()
                     for i in range(len(self.nodes))]
            nodes.append(self.nodes[index].copy())
            nodes[0].handle_in = None
            nodes[-1].handle_out = None
            self.nodes = nodes
            self.closed = False
            return [self]
        if index == 0 or index == len(self.nodes) - 1:
            return [self]
        left_nodes = [n.copy() for n in self.nodes[:index + 1]]
        right_nodes = [n.copy() for n in self.nodes[index:]]
        left_nodes[-1].handle_out = None
        right_nodes[0].handle_in = None
        self.nodes = left_nodes
        other = Path(right_nodes, False, None, dict(self.stroke),
                     None if self.fill is None else dict(self.fill),
                     self.target)
        return [self, other]

    def join(self, other, tol=1e-6, reverse_if_needed=True):
        """Append ``other`` to this path, welding coincident endpoints."""
        if not other.nodes:
            return self
        if not self.nodes:
            self.nodes = [n.copy() for n in other.nodes]
            return self
        o = other.copy()
        if reverse_if_needed:
            d_end_start = _len(_sub(self.nodes[-1].point, o.nodes[0].point))
            d_end_end = _len(_sub(self.nodes[-1].point, o.nodes[-1].point))
            if d_end_end < d_end_start:
                o.reverse()
        if _len(_sub(self.nodes[-1].point, o.nodes[0].point)) <= tol:
            last = self.nodes[-1]
            first = o.nodes[0]
            last.handle_out = first.handle_out
            last.type = last.classify()
            o.nodes.pop(0)
        self.nodes.extend(o.nodes)
        return self

    def close(self, weld_tol=1e-6):
        """Close the path, welding a duplicated last node onto the first."""
        if len(self.nodes) < 2:
            return self
        first = self.nodes[0]
        last = self.nodes[-1]
        if _len(_sub(first.point, last.point)) <= weld_tol \
                and len(self.nodes) > 2:
            first.handle_in = last.handle_in
            first.type = first.classify()
            self.nodes.pop()
        self.closed = True
        return self

    def open_path(self):
        self.closed = False
        return self

    def reverse(self):
        """Reverse the node order, swapping the handles."""
        self.nodes.reverse()
        for n in self.nodes:
            n.handle_in, n.handle_out = n.handle_out, n.handle_in
        return self

    def transform(self, mat):
        for n in self.nodes:
            n.transform(mat)
        return self

    def translate(self, dx, dy):
        return self.transform(((1.0, 0.0, float(dx)),
                               (0.0, 1.0, float(dy))))

    def scale(self, sx, sy=None, origin=(0.0, 0.0)):
        sy = sx if sy is None else sy
        ox, oy = origin
        return self.transform(((sx, 0.0, ox - sx * ox),
                               (0.0, sy, oy - sy * oy)))

    def rotate(self, angle, origin=(0.0, 0.0)):
        c = math.cos(angle)
        s = math.sin(angle)
        ox, oy = origin
        return self.transform(((c, -s, ox - c * ox + s * oy),
                               (s, c, oy - s * ox - c * oy)))

    def enforce_node_types(self):
        for n in self.nodes:
            n.enforce()
        return self

    # -- picking ---------------------------------------------------------
    def closest_node(self, point):
        """``(index, distance)`` of the nearest anchor, or ``(-1, inf)``."""
        best = (-1, float("inf"))
        for i, n in enumerate(self.nodes):
            d = _len(_sub(n.point, _v(point)))
            if d < best[1]:
                best = (i, d)
        return best

    def closest_handle(self, point):
        """``(index, 'in'|'out', distance)`` of the nearest handle end."""
        best = (-1, None, float("inf"))
        for i, n in enumerate(self.nodes):
            if n.handle_in is not None:
                d = _len(_sub(n.in_point, _v(point)))
                if d < best[2]:
                    best = (i, "in", d)
            if n.handle_out is not None:
                d = _len(_sub(n.out_point, _v(point)))
                if d < best[2]:
                    best = (i, "out", d)
        return best

    def closest_point(self, point):
        """``(segment, t, point, distance)`` on the outline."""
        return curve.closest_point_on_path(self.to_beziers(), _v(point))

    # -- §4 JSON ---------------------------------------------------------
    def to_dict(self):
        d = {
            "id": self.id,
            "closed": self.closed,
            "nodes": [n.to_dict() for n in self.nodes],
            "stroke": None if self.stroke is None else dict(self.stroke),
            "fill": None if self.fill is None else dict(self.fill),
            "target": self.target,
        }
        return d

    @classmethod
    def from_dict(cls, d):
        return cls([Node.from_dict(n) for n in d.get("nodes", [])],
                   bool(d.get("closed", False)),
                   d.get("id"),
                   d.get("stroke"),
                   d.get("fill"),
                   d.get("target", "draft"))

    def __repr__(self):
        return "Path(%r, %d nodes%s)" % (self.id, len(self.nodes),
                                         ", closed" if self.closed else "")


# --------------------------------------------------------------------------
# working plane
# --------------------------------------------------------------------------

class Plane(object):
    """The working plane a vector document is drawn on (§4 ``plane``)."""

    __slots__ = ("origin", "rotation")

    def __init__(self, origin=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)):
        self.origin = tuple(float(v) for v in origin)
        self.rotation = tuple(float(v) for v in rotation)

    def to_dict(self):
        return {"origin": list(self.origin), "rotation": list(self.rotation)}

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        return cls(d.get("origin", (0.0, 0.0, 0.0)),
                   d.get("rotation", (0.0, 0.0, 0.0, 1.0)))

    def _basis(self):
        """Right-handed (u, v, n) basis from the quaternion."""
        x, y, z, w = self.rotation
        n = math.sqrt(x * x + y * y + z * z + w * w)
        if n < _EPS:
            x, y, z, w = 0.0, 0.0, 0.0, 1.0
            n = 1.0
        x, y, z, w = x / n, y / n, z / n, w / n
        u = (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w))
        v = (2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w))
        nrm = (2 * (x * z + y * w), 2 * (y * z - x * w),
               1 - 2 * (x * x + y * y))
        return u, v, nrm

    def to_world(self, p2d):
        """Lift a 2D document point onto the plane in 3D."""
        u, v, _ = self._basis()
        x, y = float(p2d[0]), float(p2d[1])
        return (self.origin[0] + u[0] * x + v[0] * y,
                self.origin[1] + u[1] * x + v[1] * y,
                self.origin[2] + u[2] * x + v[2] * y)

    def to_plane(self, p3d):
        """Project a 3D point into the plane's 2D coordinates."""
        u, v, _ = self._basis()
        d = (p3d[0] - self.origin[0], p3d[1] - self.origin[1],
             p3d[2] - self.origin[2])
        return (d[0] * u[0] + d[1] * u[1] + d[2] * u[2],
                d[0] * v[0] + d[1] * v[1] + d[2] * v[2])

    def normal(self):
        return self._basis()[2]

    def __repr__(self):
        return "Plane(%r, %r)" % (self.origin, self.rotation)


# --------------------------------------------------------------------------
# document
# --------------------------------------------------------------------------

class VectorDocument(object):
    """The §4 ``vector`` document: a working plane plus ordered paths."""

    VERSION = 1

    def __init__(self, plane=None, unit_scale=0.001, paths=None, version=None):
        self.version = int(self.VERSION if version is None else version)
        self.plane = plane if isinstance(plane, Plane) else Plane.from_dict(
            plane if isinstance(plane, dict) else None)
        self.unit_scale = float(unit_scale)
        self.paths = list(paths) if paths else []

    # -- container -------------------------------------------------------
    def __len__(self):
        return len(self.paths)

    def __iter__(self):
        return iter(self.paths)

    def __getitem__(self, i):
        return self.paths[i]

    def add_path(self, path=None, index=None):
        if path is None:
            path = Path()
        if index is None:
            self.paths.append(path)
        else:
            self.paths.insert(max(0, min(int(index), len(self.paths))), path)
        return path

    def remove_path(self, path_or_index):
        idx = self.index_of(path_or_index)
        if idx < 0:
            raise IndexError("no such path: %r" % (path_or_index,))
        return self.paths.pop(idx)

    def index_of(self, path_or_index):
        if isinstance(path_or_index, int):
            if 0 <= path_or_index < len(self.paths):
                return path_or_index
            return -1
        if isinstance(path_or_index, str):
            for i, p in enumerate(self.paths):
                if p.id == path_or_index:
                    return i
            return -1
        for i, p in enumerate(self.paths):
            if p is path_or_index:
                return i
        return -1

    def path_by_id(self, pid):
        i = self.index_of(pid)
        return self.paths[i] if i >= 0 else None

    def bbox(self):
        boxes = [p.bbox() for p in self.paths]
        boxes = [b for b in boxes if b is not None]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    # -- z order ---------------------------------------------------------
    def set_z_index(self, path_or_index, z):
        i = self.index_of(path_or_index)
        if i < 0:
            raise IndexError("no such path")
        z = max(0, min(int(z), len(self.paths) - 1))
        p = self.paths.pop(i)
        self.paths.insert(z, p)
        return z

    def raise_path(self, path_or_index):
        i = self.index_of(path_or_index)
        if i < 0 or i >= len(self.paths) - 1:
            return i
        return self.set_z_index(i, i + 1)

    def lower_path(self, path_or_index):
        i = self.index_of(path_or_index)
        if i <= 0:
            return i
        return self.set_z_index(i, i - 1)

    def bring_to_front(self, path_or_index):
        return self.set_z_index(path_or_index, len(self.paths) - 1)

    def send_to_back(self, path_or_index):
        return self.set_z_index(path_or_index, 0)

    # -- freehand --------------------------------------------------------
    def add_stroke(self, points, error=1.0, corner_angle=60.0, closed=False,
                   stroke=None, fill=None, target="draft", smooth=True):
        """Convert freehand VR samples into a clean Bezier path and add it."""
        p = path_from_stroke(points, error=error, corner_angle=corner_angle,
                             closed=closed, stroke=stroke, fill=fill,
                             target=target, smooth=smooth)
        if p is not None:
            self.add_path(p)
        return p

    # -- §4 JSON ---------------------------------------------------------
    def to_json(self):
        return {
            "version": self.version,
            "plane": self.plane.to_dict(),
            "unit_scale": self.unit_scale,
            "paths": [p.to_dict() for p in self.paths],
        }

    to_dict = to_json

    @classmethod
    def from_json(cls, data):
        if isinstance(data, (bytes, bytearray)):
            import json
            data = json.loads(bytes(data).decode("utf-8"))
        elif isinstance(data, str):
            import json
            data = json.loads(data)
        return cls(Plane.from_dict(data.get("plane")),
                   float(data.get("unit_scale", 0.001)),
                   [Path.from_dict(p) for p in data.get("paths", [])],
                   data.get("version", cls.VERSION))

    from_dict = from_json

    def dumps(self, indent=None):
        import json
        return json.dumps(self.to_json(), indent=indent)

    def copy(self):
        return VectorDocument.from_json(self.to_json())

    def __repr__(self):
        return "VectorDocument(%d paths)" % (len(self.paths),)


# --------------------------------------------------------------------------
# freehand -> path
# --------------------------------------------------------------------------

def path_from_stroke(points, error=1.0, corner_angle=60.0, closed=False,
                     stroke=None, fill=None, target="draft", smooth=True,
                     simplify_tol=0.0):
    """Fit a clean Bezier :class:`Path` through freehand sample points.

    Nodes at detected corners keep the ``corner`` type; every other join is
    made exactly ``smooth`` so the node invariants hold by construction.
    """
    pts = curve.remove_duplicates(points, 1e-9)
    if len(pts) < 2:
        return None
    segs, corners = curve.fit_curve(pts, error=error,
                                    corner_angle=corner_angle,
                                    simplify_tol=simplify_tol,
                                    return_corners=True)
    if not segs:
        return None
    path = Path.from_beziers(segs, closed=closed, stroke=stroke, fill=fill,
                             target=target)
    if smooth:
        corner_set = set(corners)
        last = len(path.nodes) - 1
        for i, node in enumerate(path.nodes):
            if i in corner_set or i == 0 or i == last:
                if node.classify() == "corner":
                    node.type = "corner"
                continue
            if node.handle_in is not None and node.handle_out is not None:
                node.set_type("smooth")
    return path


# --------------------------------------------------------------------------
# snapping
# --------------------------------------------------------------------------

class SnapSettings(object):
    """Which snaps are active and how strong they are (document units)."""

    __slots__ = ("grid", "grid_size", "node", "midpoint", "tangent", "angle",
                 "angle_step", "radius", "enabled")

    def __init__(self, grid=True, grid_size=1.0, node=True, midpoint=True,
                 tangent=True, angle=True, angle_step=math.pi / 12.0,
                 radius=2.0, enabled=True):
        self.grid = bool(grid)
        self.grid_size = float(grid_size)
        self.node = bool(node)
        self.midpoint = bool(midpoint)
        self.tangent = bool(tangent)
        self.angle = bool(angle)
        self.angle_step = float(angle_step)
        self.radius = float(radius)
        self.enabled = bool(enabled)

    def to_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)


class SnapResult(object):
    """The outcome of a snap query."""

    __slots__ = ("point", "kind", "path_id", "node_index", "distance")

    def __init__(self, point, kind=None, path_id=None, node_index=None,
                 distance=0.0):
        self.point = _v(point)
        self.kind = kind
        self.path_id = path_id
        self.node_index = node_index
        self.distance = float(distance)

    @property
    def snapped(self):
        return self.kind is not None

    def __iter__(self):
        return iter(self.point)

    def __repr__(self):
        return "SnapResult(%.4g, %.4g, %s)" % (self.point[0], self.point[1],
                                               self.kind)


class SnapEngine(object):
    """Grid / node / midpoint / tangent / angle snapping.

    ``snap()`` returns the first match in priority order: node, midpoint,
    tangent, angle (relative to ``origin``), grid.
    """

    #: priority order, strongest first
    ORDER = ("node", "midpoint", "tangent", "angle", "grid")

    def __init__(self, settings=None):
        self.settings = settings or SnapSettings()

    def snap(self, point, document=None, origin=None, exclude=None):
        s = self.settings
        p = _v(point)
        if not s.enabled:
            return SnapResult(p)
        r = s.radius
        candidates = []
        if document is not None and (s.node or s.midpoint or s.tangent):
            for path in document.paths:
                if exclude is not None and (path is exclude
                                            or path.id == exclude):
                    continue
                if s.node:
                    for i, nd in enumerate(path.nodes):
                        d = _len(_sub(nd.point, p))
                        if d <= r:
                            candidates.append(("node", nd.point, d, path.id,
                                               i))
                if s.midpoint:
                    for i, bez in enumerate(path.to_beziers()):
                        mid = curve.bezier_point(bez, 0.5)
                        d = _len(_sub(mid, p))
                        if d <= r:
                            candidates.append(("midpoint", mid, d, path.id,
                                               i))
                if s.tangent:
                    hit = self._tangent_snap(path, p, r)
                    if hit is not None:
                        candidates.append(hit)
        if s.angle and origin is not None:
            hit = self._angle_snap(p, _v(origin), r)
            if hit is not None:
                candidates.append(hit)
        if s.grid and s.grid_size > 0.0:
            g = s.grid_size
            gp = (round(p[0] / g) * g, round(p[1] / g) * g)
            d = _len(_sub(gp, p))
            if d <= r:
                candidates.append(("grid", gp, d, None, None))
        if not candidates:
            return SnapResult(p)
        rank = dict((k, i) for i, k in enumerate(self.ORDER))
        candidates.sort(key=lambda c: (rank.get(c[0], 99), c[2]))
        kind, pt, d, pid, idx = candidates[0]
        return SnapResult(pt, kind, pid, idx, d)

    def _tangent_snap(self, path, p, radius):
        """Snap onto the line carried by a node's handle."""
        best = None
        for i, nd in enumerate(path.nodes):
            for h in (nd.handle_out, nd.handle_in):
                if h is None or _len(h) < _EPS:
                    continue
                d = _norm(h)
                w = _sub(p, nd.point)
                t = _dot(w, d)
                if t <= 0.0:
                    continue
                proj = _add(nd.point, _mul(d, t))
                dist = _len(_sub(proj, p))
                if dist <= radius and (best is None or dist < best[2]):
                    best = ("tangent", proj, dist, path.id, i)
        return best

    def _angle_snap(self, p, origin, radius):
        step = self.settings.angle_step
        if step <= 0.0:
            return None
        v = _sub(p, origin)
        L = _len(v)
        if L < _EPS:
            return None
        a = math.atan2(v[1], v[0])
        snapped = round(a / step) * step
        pt = (origin[0] + math.cos(snapped) * L,
              origin[1] + math.sin(snapped) * L)
        d = _len(_sub(pt, p))
        if d <= radius:
            return ("angle", pt, d, None, None)
        return None
