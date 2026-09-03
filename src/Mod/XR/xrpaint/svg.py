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
"""SVG export and a minimal SVG path importer for the VR vector editor.

Pure standard library (ARCHITECTURE.md §6 names this module explicitly).

The document's 2D coordinate system is mathematical (y up); SVG's is y down.
Export negates y and the importer negates it back, so
``import_document(export_document(doc))`` reproduces the same geometry.
"""

import math
import re
import xml.etree.ElementTree as ET

from . import curve
from .vector import Node, Path, VectorDocument

__all__ = [
    "arc_to_beziers",
    "export_document",
    "export_path_data",
    "format_color",
    "import_document",
    "parse_color",
    "parse_path_data",
]

SVG_NS = "http://www.w3.org/2000/svg"

_NAMED_COLORS = {
    "none": None,
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 128 / 255.0, 0.0),
    "lime": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "aqua": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "fuchsia": (1.0, 0.0, 1.0),
    "gray": (128 / 255.0, 128 / 255.0, 128 / 255.0),
    "grey": (128 / 255.0, 128 / 255.0, 128 / 255.0),
    "silver": (192 / 255.0, 192 / 255.0, 192 / 255.0),
    "maroon": (128 / 255.0, 0.0, 0.0),
    "navy": (0.0, 0.0, 128 / 255.0),
    "olive": (128 / 255.0, 128 / 255.0, 0.0),
    "purple": (128 / 255.0, 0.0, 128 / 255.0),
    "teal": (0.0, 128 / 255.0, 128 / 255.0),
    "orange": (1.0, 165 / 255.0, 0.0),
}


# --------------------------------------------------------------------------
# numbers and colours
# --------------------------------------------------------------------------

def _fmt(v, precision=12):
    s = ("%%.%dg" % precision) % (float(v) + 0.0)
    if s == "-0":
        return "0"
    return s


def parse_color(text):
    """Parse an SVG colour into ``(r, g, b)`` floats, or ``None`` for none."""
    if text is None:
        return None
    t = str(text).strip().lower()
    if t in ("", "none", "transparent"):
        return None
    if t.startswith("#"):
        h = t[1:]
        if len(h) == 3:
            return tuple(int(c * 2, 16) / 255.0 for c in h)
        if len(h) == 4:
            return tuple(int(c * 2, 16) / 255.0 for c in h[:3])
        if len(h) == 6:
            return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        if len(h) == 8:
            return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return None
    m = re.match(r"rgba?\(([^)]*)\)", t)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        vals = []
        for p in parts[:3]:
            if p.endswith("%"):
                vals.append(float(p[:-1]) / 100.0)
            else:
                vals.append(float(p) / 255.0)
        while len(vals) < 3:
            vals.append(0.0)
        return tuple(vals)
    return _NAMED_COLORS.get(t)


def format_color(rgba):
    """``[r, g, b, a]`` floats 0..1 -> ``#rrggbb``."""
    r, g, b = (max(0.0, min(1.0, float(c))) for c in rgba[:3])
    return "#%02x%02x%02x" % (int(r * 255 + 0.5), int(g * 255 + 0.5),
                              int(b * 255 + 0.5))


def _alpha(rgba, default=1.0):
    if rgba is None:
        return default
    if len(rgba) >= 4:
        return max(0.0, min(1.0, float(rgba[3])))
    return default


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def export_path_data(path, flip_y=True, precision=12):
    """The ``d`` attribute for one :class:`~xrpaint.vector.Path`."""
    if not path.nodes:
        return ""
    sy = -1.0 if flip_y else 1.0

    def pt(p):
        return "%s %s" % (_fmt(p[0], precision), _fmt(p[1] * sy, precision))

    out = ["M " + pt(path.nodes[0].point)]
    n = len(path.nodes)
    for i in range(path.segment_count()):
        a = path.nodes[i]
        b = path.nodes[(i + 1) % n]
        if a.handle_out is None and b.handle_in is None:
            if i == path.segment_count() - 1 and path.closed:
                break
            out.append("L " + pt(b.point))
        else:
            out.append("C %s %s %s" % (pt(a.out_point), pt(b.in_point),
                                       pt(b.point)))
    if path.closed:
        out.append("Z")
    return " ".join(out)


def export_document(document, precision=12, margin=None, flip_y=True,
                    background=None):
    """Serialise a :class:`~xrpaint.vector.VectorDocument` to SVG text.

    The physical size is derived from ``unit_scale`` (document units to
    metres), so a millimetre document (``unit_scale = 0.001``) comes out with
    ``width``/``height`` in millimetres and a 1:1 ``viewBox``.
    """
    doc = document
    bbox = doc.bbox()
    if bbox is None:
        bbox = (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = bbox
    if margin is None:
        stroke_max = 0.0
        for p in doc.paths:
            if p.stroke:
                stroke_max = max(stroke_max, float(p.stroke.get("width", 0.0)))
        margin = max(stroke_max, (x1 - x0 + y1 - y0) * 0.02, 1e-6)
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    if flip_y:
        vb_x, vb_y = x0, -y1
    else:
        vb_x, vb_y = x0, y0
    # document units -> millimetres
    mm = float(doc.unit_scale) * 1000.0

    svg = ET.Element("svg")
    svg.set("xmlns", SVG_NS)
    svg.set("version", "1.1")
    svg.set("width", "%smm" % _fmt(w * mm, precision))
    svg.set("height", "%smm" % _fmt(h * mm, precision))
    svg.set("viewBox", "%s %s %s %s" % (_fmt(vb_x, precision),
                                        _fmt(vb_y, precision),
                                        _fmt(w, precision),
                                        _fmt(h, precision)))
    svg.set("data-unit-scale", _fmt(doc.unit_scale, precision))
    if background:
        rect = ET.SubElement(svg, "rect")
        rect.set("x", _fmt(vb_x, precision))
        rect.set("y", _fmt(vb_y, precision))
        rect.set("width", _fmt(w, precision))
        rect.set("height", _fmt(h, precision))
        rect.set("fill", format_color(background))

    group = ET.SubElement(svg, "g")
    group.set("id", "xrpaint-vector")
    for p in doc.paths:
        el = ET.SubElement(group, "path")
        el.set("id", str(p.id))
        d = export_path_data(p, flip_y=flip_y, precision=precision)
        el.set("d", d)
        fill = p.fill.get("color") if p.fill else None
        if fill is None:
            el.set("fill", "none")
        else:
            el.set("fill", format_color(fill))
            fa = _alpha(fill)
            if fa < 1.0:
                el.set("fill-opacity", _fmt(fa, precision))
        stroke = p.stroke.get("color") if p.stroke else None
        if stroke is None:
            el.set("stroke", "none")
        else:
            el.set("stroke", format_color(stroke))
            el.set("stroke-width",
                   _fmt(p.stroke.get("width", 0.5), precision))
            sa = _alpha(stroke)
            if sa < 1.0:
                el.set("stroke-opacity", _fmt(sa, precision))
            el.set("stroke-linecap", "round")
            el.set("stroke-linejoin", "round")
        if p.target and p.target != "draft":
            el.set("data-target", str(p.target))
    body = ET.tostring(svg, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


# --------------------------------------------------------------------------
# path data parsing
# --------------------------------------------------------------------------

_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD_RE = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]")


def _tokenize_path(d):
    """Yield ``(command, [numbers])`` tuples from an SVG ``d`` string."""
    i = 0
    n = len(d)
    out = []
    while i < n:
        ch = d[i]
        if ch.isspace() or ch == ",":
            i += 1
            continue
        if _CMD_RE.match(ch):
            cmd = ch
            i += 1
            nums = []
            while i < n:
                if d[i].isspace() or d[i] == ",":
                    i += 1
                    continue
                if _CMD_RE.match(d[i]):
                    break
                m = _NUM_RE.match(d, i)
                if not m or m.end() == m.start():
                    i += 1
                    continue
                nums.append(float(m.group()))
                i = m.end()
            out.append((cmd, nums))
        else:
            i += 1
    return out


def _quad_to_cubic(p0, q, p1):
    c1 = (p0[0] + 2.0 / 3.0 * (q[0] - p0[0]),
          p0[1] + 2.0 / 3.0 * (q[1] - p0[1]))
    c2 = (p1[0] + 2.0 / 3.0 * (q[0] - p1[0]),
          p1[1] + 2.0 / 3.0 * (q[1] - p1[1]))
    return (p0, c1, c2, p1)


def arc_to_beziers(p0, rx, ry, x_rot_deg, large_arc, sweep, p1):
    """SVG elliptical arc -> a list of cubic Beziers (spec appendix F.6)."""
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    rx = abs(float(rx))
    ry = abs(float(ry))
    if rx < 1e-12 or ry < 1e-12 or (abs(x1 - x0) < 1e-15
                                    and abs(y1 - y0) < 1e-15):
        return [curve.line_to_bezier((x0, y0), (x1, y1))]
    phi = math.radians(float(x_rot_deg))
    cos_p = math.cos(phi)
    sin_p = math.sin(phi)
    dx2 = (x0 - x1) / 2.0
    dy2 = (y0 - y1) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2
    # scale the radii up if they are too small to span the endpoints
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if den <= 0.0:
        return [curve.line_to_bezier((x0, y0), (x1, y1))]
    coef = math.sqrt(max(0.0, num / den))
    if bool(large_arc) == bool(sweep):
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n < 1e-15:
            return 0.0
        c = max(-1.0, min(1.0, dot / n))
        a = math.acos(c)
        if ux * vy - uy * vx < 0.0:
            a = -a
        return a

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry
    theta1 = angle(1.0, 0.0, ux, uy)
    dtheta = angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0.0:
        dtheta += 2.0 * math.pi

    nseg = int(math.ceil(abs(dtheta) / (math.pi / 2.0) - 1e-9))
    nseg = max(1, nseg)
    delta = dtheta / nseg
    t = 4.0 / 3.0 * math.tan(delta / 4.0)
    out = []
    th = theta1
    for _ in range(nseg):
        cos1 = math.cos(th)
        sin1 = math.sin(th)
        th2 = th + delta
        cos2 = math.cos(th2)
        sin2 = math.sin(th2)

        def point(c, s):
            return (cx + rx * cos_p * c - ry * sin_p * s,
                    cy + rx * sin_p * c + ry * cos_p * s)

        def deriv(c, s):
            return (-rx * cos_p * s - ry * sin_p * c,
                    -rx * sin_p * s + ry * cos_p * c)

        pa = point(cos1, sin1)
        pb = point(cos2, sin2)
        da = deriv(cos1, sin1)
        db = deriv(cos2, sin2)
        c1 = (pa[0] + t * da[0], pa[1] + t * da[1])
        c2 = (pb[0] - t * db[0], pb[1] - t * db[1])
        out.append((pa, c1, c2, pb))
        th = th2
    if out:
        out[0] = ((x0, y0),) + out[0][1:]
        out[-1] = out[-1][:3] + ((x1, y1),)
    return out


def parse_path_data(d, flip_y=False):
    """Parse an SVG ``d`` attribute into subpaths.

    Returns a list of dicts ``{"beziers": [...], "closed": bool,
    "start": (x, y)}``.  Every command of the SVG grammar is supported:
    ``M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z/z``.
    """
    sy = -1.0 if flip_y else 1.0
    subpaths = []
    cur = None
    pos = (0.0, 0.0)
    start = (0.0, 0.0)
    last_cubic_ctrl = None
    last_quad_ctrl = None

    def begin(p):
        return {"beziers": [], "closed": False, "start": p}

    def emit(bez):
        if cur is not None:
            cur["beziers"].append(bez)

    for cmd, nums in _tokenize_path(d):
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            i = 0
            while i + 1 < len(nums):
                p = (nums[i], nums[i + 1])
                if rel:
                    p = (pos[0] + p[0], pos[1] + p[1])
                if i == 0:
                    if cur is not None and cur["beziers"]:
                        subpaths.append(cur)
                    cur = begin(p)
                    start = p
                else:
                    emit(curve.line_to_bezier(pos, p))
                pos = p
                i += 2
            last_cubic_ctrl = last_quad_ctrl = None
        elif c == "L":
            i = 0
            while i + 1 < len(nums):
                p = (nums[i], nums[i + 1])
                if rel:
                    p = (pos[0] + p[0], pos[1] + p[1])
                emit(curve.line_to_bezier(pos, p))
                pos = p
                i += 2
            last_cubic_ctrl = last_quad_ctrl = None
        elif c == "H":
            for v in nums:
                p = (pos[0] + v, pos[1]) if rel else (v, pos[1])
                emit(curve.line_to_bezier(pos, p))
                pos = p
            last_cubic_ctrl = last_quad_ctrl = None
        elif c == "V":
            for v in nums:
                p = (pos[0], pos[1] + v) if rel else (pos[0], v)
                emit(curve.line_to_bezier(pos, p))
                pos = p
            last_cubic_ctrl = last_quad_ctrl = None
        elif c == "C":
            i = 0
            while i + 5 < len(nums):
                c1 = (nums[i], nums[i + 1])
                c2 = (nums[i + 2], nums[i + 3])
                p = (nums[i + 4], nums[i + 5])
                if rel:
                    c1 = (pos[0] + c1[0], pos[1] + c1[1])
                    c2 = (pos[0] + c2[0], pos[1] + c2[1])
                    p = (pos[0] + p[0], pos[1] + p[1])
                emit((pos, c1, c2, p))
                last_cubic_ctrl = c2
                last_quad_ctrl = None
                pos = p
                i += 6
        elif c == "S":
            i = 0
            while i + 3 < len(nums):
                c2 = (nums[i], nums[i + 1])
                p = (nums[i + 2], nums[i + 3])
                if rel:
                    c2 = (pos[0] + c2[0], pos[1] + c2[1])
                    p = (pos[0] + p[0], pos[1] + p[1])
                if last_cubic_ctrl is None:
                    c1 = pos
                else:
                    c1 = (2.0 * pos[0] - last_cubic_ctrl[0],
                          2.0 * pos[1] - last_cubic_ctrl[1])
                emit((pos, c1, c2, p))
                last_cubic_ctrl = c2
                last_quad_ctrl = None
                pos = p
                i += 4
        elif c == "Q":
            i = 0
            while i + 3 < len(nums):
                q = (nums[i], nums[i + 1])
                p = (nums[i + 2], nums[i + 3])
                if rel:
                    q = (pos[0] + q[0], pos[1] + q[1])
                    p = (pos[0] + p[0], pos[1] + p[1])
                emit(_quad_to_cubic(pos, q, p))
                last_quad_ctrl = q
                last_cubic_ctrl = None
                pos = p
                i += 4
        elif c == "T":
            i = 0
            while i + 1 < len(nums):
                p = (nums[i], nums[i + 1])
                if rel:
                    p = (pos[0] + p[0], pos[1] + p[1])
                if last_quad_ctrl is None:
                    q = pos
                else:
                    q = (2.0 * pos[0] - last_quad_ctrl[0],
                         2.0 * pos[1] - last_quad_ctrl[1])
                emit(_quad_to_cubic(pos, q, p))
                last_quad_ctrl = q
                last_cubic_ctrl = None
                pos = p
                i += 2
        elif c == "A":
            i = 0
            while i + 6 < len(nums):
                rx = nums[i]
                ry = nums[i + 1]
                rot = nums[i + 2]
                laf = bool(int(round(nums[i + 3])))
                swf = bool(int(round(nums[i + 4])))
                p = (nums[i + 5], nums[i + 6])
                if rel:
                    p = (pos[0] + p[0], pos[1] + p[1])
                for b in arc_to_beziers(pos, rx, ry, rot, laf, swf, p):
                    emit(b)
                pos = p
                last_cubic_ctrl = last_quad_ctrl = None
                i += 7
        elif c == "Z":
            if cur is not None:
                if abs(pos[0] - start[0]) > 1e-12 \
                        or abs(pos[1] - start[1]) > 1e-12:
                    emit(curve.line_to_bezier(pos, start))
                cur["closed"] = True
                subpaths.append(cur)
                cur = None
            pos = start
            last_cubic_ctrl = last_quad_ctrl = None
    if cur is not None and cur["beziers"]:
        subpaths.append(cur)
    if sy < 0.0:
        for sp in subpaths:
            sp["start"] = (sp["start"][0], sp["start"][1] * sy)
            sp["beziers"] = [tuple((p[0], p[1] * sy) for p in b)
                             for b in sp["beziers"]]
    return subpaths


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)"
                           r"\s*\(([^)]*)\)")


def _parse_transform(text):
    """Parse an SVG transform list into a 2x3 affine (row major)."""
    mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    if not text:
        return mat
    for name, args in _TRANSFORM_RE.findall(text):
        vals = [float(v) for v in _NUM_RE.findall(args)]
        if name == "matrix" and len(vals) >= 6:
            a, b, c, d, e, f = vals[:6]
            m = ((a, c, e), (b, d, f))
        elif name == "translate":
            tx = vals[0] if vals else 0.0
            ty = vals[1] if len(vals) > 1 else 0.0
            m = ((1.0, 0.0, tx), (0.0, 1.0, ty))
        elif name == "scale":
            sx = vals[0] if vals else 1.0
            sy = vals[1] if len(vals) > 1 else sx
            m = ((sx, 0.0, 0.0), (0.0, sy, 0.0))
        elif name == "rotate":
            a = math.radians(vals[0] if vals else 0.0)
            cx = vals[1] if len(vals) > 2 else 0.0
            cy = vals[2] if len(vals) > 2 else 0.0
            co = math.cos(a)
            si = math.sin(a)
            m = ((co, -si, cx - co * cx + si * cy),
                 (si, co, cy - si * cx - co * cy))
        elif name == "skewX":
            t = math.tan(math.radians(vals[0] if vals else 0.0))
            m = ((1.0, t, 0.0), (0.0, 1.0, 0.0))
        elif name == "skewY":
            t = math.tan(math.radians(vals[0] if vals else 0.0))
            m = ((1.0, 0.0, 0.0), (t, 1.0, 0.0))
        else:
            continue
        mat = _mat_mul(mat, m)
    return mat


def _mat_mul(a, b):
    (a00, a01, a02), (a10, a11, a12) = a
    (b00, b01, b02), (b10, b11, b12) = b
    return ((a00 * b00 + a01 * b10, a00 * b01 + a01 * b11,
             a00 * b02 + a01 * b12 + a02),
            (a10 * b00 + a11 * b10, a10 * b01 + a11 * b11,
             a10 * b02 + a11 * b12 + a12))


def _is_identity(m):
    return (abs(m[0][0] - 1) < 1e-12 and abs(m[0][1]) < 1e-12
            and abs(m[0][2]) < 1e-12 and abs(m[1][0]) < 1e-12
            and abs(m[1][1] - 1) < 1e-12 and abs(m[1][2]) < 1e-12)


def _tag(el):
    t = el.tag
    if isinstance(t, str) and t.startswith("{"):
        return t.split("}", 1)[1]
    return t


def _style_dict(el):
    out = {}
    style = el.get("style")
    if style:
        for part in style.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def _attr(el, name, inherited, default=None):
    style = _style_dict(el)
    if name in style:
        return style[name]
    v = el.get(name)
    if v is not None:
        return v
    return inherited.get(name, default)


def import_document(text, flip_y=True, unit_scale=None, error=None):
    """Parse SVG text into a :class:`~xrpaint.vector.VectorDocument`.

    Only ``<path>`` geometry plus the basic shapes ``rect``, ``circle``,
    ``ellipse``, ``line``, ``polyline`` and ``polygon`` are read; groups and
    their ``transform`` attributes are applied.
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    root = ET.fromstring(text)
    doc = VectorDocument()
    if unit_scale is not None:
        doc.unit_scale = float(unit_scale)
    else:
        us = root.get("data-unit-scale")
        if us:
            try:
                doc.unit_scale = float(us)
            except ValueError:
                pass
    _walk(root, doc, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), {}, flip_y)
    return doc


def _walk(el, doc, mat, inherited, flip_y):
    mat = _mat_mul(mat, _parse_transform(el.get("transform")))
    inh = dict(inherited)
    for key in ("fill", "stroke", "stroke-width", "fill-opacity",
                "stroke-opacity", "opacity"):
        v = _attr(el, key, {})
        if v is not None:
            inh[key] = v
    tag = _tag(el)
    if tag in ("path", "rect", "circle", "ellipse", "line", "polyline",
               "polygon"):
        _add_shape(el, doc, mat, inh, flip_y, tag)
    for child in el:
        _walk(child, doc, mat, inh, flip_y)


def _shape_to_d(el, tag):
    """Convert a basic shape element into path data."""
    def f(name, default=0.0):
        v = el.get(name)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    if tag == "path":
        return el.get("d") or ""
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        if w <= 0 or h <= 0:
            return ""
        return "M %g %g L %g %g L %g %g L %g %g Z" % (
            x, y, x + w, y, x + w, y + h, x, y + h)
    if tag in ("circle", "ellipse"):
        cx, cy = f("cx"), f("cy")
        if tag == "circle":
            rx = ry = f("r")
        else:
            rx, ry = f("rx"), f("ry")
        if rx <= 0 or ry <= 0:
            return ""
        return ("M %g %g A %g %g 0 1 0 %g %g A %g %g 0 1 0 %g %g Z"
                % (cx - rx, cy, rx, ry, cx + rx, cy, rx, ry, cx - rx, cy))
    if tag == "line":
        return "M %g %g L %g %g" % (f("x1"), f("y1"), f("x2"), f("y2"))
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in _NUM_RE.findall(el.get("points") or "")]
        if len(nums) < 4:
            return ""
        parts = ["M %g %g" % (nums[0], nums[1])]
        for i in range(2, len(nums) - 1, 2):
            parts.append("L %g %g" % (nums[i], nums[i + 1]))
        if tag == "polygon":
            parts.append("Z")
        return " ".join(parts)
    return ""


def _add_shape(el, doc, mat, inherited, flip_y, tag):
    d = _shape_to_d(el, tag)
    if not d:
        return
    subpaths = parse_path_data(d, flip_y=flip_y)
    if not subpaths:
        return
    fill_txt = _attr(el, "fill", inherited, "none" if tag != "path" else None)
    stroke_txt = _attr(el, "stroke", inherited, None)
    fill_rgb = parse_color(fill_txt)
    stroke_rgb = parse_color(stroke_txt)
    try:
        width = float(_attr(el, "stroke-width", inherited, 1.0))
    except (TypeError, ValueError):
        width = 1.0
    try:
        fo = float(_attr(el, "fill-opacity", inherited, 1.0))
    except (TypeError, ValueError):
        fo = 1.0
    try:
        so = float(_attr(el, "stroke-opacity", inherited, 1.0))
    except (TypeError, ValueError):
        so = 1.0
    stroke = None
    if stroke_rgb is not None:
        stroke = {"color": [stroke_rgb[0], stroke_rgb[1], stroke_rgb[2], so],
                  "width": width}
    fill = None
    if fill_rgb is not None:
        fill = {"color": [fill_rgb[0], fill_rgb[1], fill_rgb[2], fo]}
    target = el.get("data-target") or "draft"
    base_id = el.get("id")
    for i, sp in enumerate(subpaths):
        beziers = sp["beziers"]
        if not beziers:
            continue
        pid = base_id if (base_id and len(subpaths) == 1) else None
        if base_id and len(subpaths) > 1:
            pid = "%s_%d" % (base_id, i)
        p = Path.from_beziers(beziers, closed=sp["closed"], id=pid,
                              stroke=stroke, fill=fill, target=target)
        if not _is_identity(mat):
            m = mat
            if flip_y:
                # the matrix acts in SVG space, our nodes are already flipped
                flip = ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
                m = _mat_mul(flip, _mat_mul(mat, flip))
            p.transform(m)
        if stroke is None and fill is None:
            p.stroke = None
        doc.add_path(p)
