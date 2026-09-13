# SPDX-License-Identifier: LGPL-2.1-or-later
"""Standard node library for Grasshopper mode.

Node functions receive an :class:`~grasshoppermode.graph.EvalContext` as
first argument (``ctx.backend`` is the geometry backend) followed by keyword
arguments named after the input ports.  They return a value (single output)
or a dict keyed by output name.
"""

import math
import random

from .geometry import Vec3
from .graph import NodeType, PortSpec, Registry, flatten

P = PortSpec


def _num(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        return _num(v[0], default) if v else default
    return float(v)


def _shapes(ctx, value):
    """Flatten a value into a list of shapes, skipping non-shapes."""
    return [s for s in flatten(value) if s is not None and ctx.backend.is_shape(s)]


def _points(value):
    return [p for p in flatten(value) if isinstance(p, Vec3)]


# ------------------------------------------------------------------ parameters
def n_slider(ctx, value=None):
    lo = float(ctx.node.params.get("min", 0.0))
    hi = float(ctx.node.params.get("max", 10.0))
    v = _num(value, ctx.node.params.get("default", 1.0))
    return max(lo, min(hi, v))


def n_integer(ctx, value=None):
    return int(round(_num(value, 1)))


def n_boolean(ctx, value=None):
    return bool(value)


def n_text(ctx, value=None):
    return "" if value is None else str(value)


def n_panel(ctx, value=None):
    items = flatten(value)
    lines = []
    for item in items[:200]:
        if ctx.backend.is_shape(item):
            lines.append(ctx.backend.describe(item))
        elif isinstance(item, float):
            lines.append("%.6g" % item)
        else:
            lines.append(str(item))
    return "\n".join(lines)


# ------------------------------------------------------------------------ math
def n_add(ctx, a=0.0, b=0.0):
    return _num(a) + _num(b)


def n_subtract(ctx, a=0.0, b=0.0):
    return _num(a) - _num(b)


def n_multiply(ctx, a=1.0, b=1.0):
    return _num(a) * _num(b)


def n_divide(ctx, a=1.0, b=1.0):
    b = _num(b, 1.0)
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return _num(a) / b


def n_power(ctx, a=1.0, b=2.0):
    return _num(a) ** _num(b, 2.0)


def n_modulo(ctx, a=0.0, b=1.0):
    return math.fmod(_num(a), _num(b, 1.0))


def n_negate(ctx, a=0.0):
    return -_num(a)


def n_abs(ctx, a=0.0):
    return abs(_num(a))


def n_sqrt(ctx, a=0.0):
    return math.sqrt(_num(a))


def n_sin(ctx, angle=0.0):
    return math.sin(math.radians(_num(angle)))


def n_cos(ctx, angle=0.0):
    return math.cos(math.radians(_num(angle)))


def n_tan(ctx, angle=0.0):
    return math.tan(math.radians(_num(angle)))


def n_min(ctx, a=0.0, b=0.0):
    return min(_num(a), _num(b))


def n_max(ctx, a=0.0, b=0.0):
    return max(_num(a), _num(b))


def n_round(ctx, a=0.0, digits=0):
    return round(_num(a), int(_num(digits)))


def n_remap(ctx, value=0.0, source_min=0.0, source_max=1.0, target_min=0.0, target_max=10.0):
    smin, smax = _num(source_min), _num(source_max, 1.0)
    tmin, tmax = _num(target_min), _num(target_max, 10.0)
    if smax == smin:
        return tmin
    t = (_num(value) - smin) / (smax - smin)
    return tmin + t * (tmax - tmin)


def n_series(ctx, start=0.0, step=1.0, count=10):
    n = max(0, int(_num(count, 10)))
    return [_num(start) + i * _num(step, 1.0) for i in range(n)]


def n_range(ctx, start=0.0, end=1.0, steps=10):
    n = max(1, int(_num(steps, 10)))
    a, b = _num(start), _num(end, 1.0)
    return [a + (b - a) * i / float(n) for i in range(n + 1)]


def n_random(ctx, count=10, minimum=0.0, maximum=1.0, seed=1):
    rng = random.Random(int(_num(seed, 1)))
    lo, hi = _num(minimum), _num(maximum, 1.0)
    return [rng.uniform(lo, hi) for _ in range(max(0, int(_num(count, 10))))]


def n_pi(ctx, factor=1.0):
    return math.pi * _num(factor, 1.0)


def n_compare(ctx, a=0.0, b=0.0):
    x, y = _num(a), _num(b)
    return {"less": x < y, "equal": x == y, "greater": x > y}


def n_expression(ctx, x=0.0, y=0.0, z=0.0):
    expr = str(ctx.node.params.get("expression", "x"))
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    allowed.update(
        {
            "x": _num(x),
            "y": _num(y),
            "z": _num(z),
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
        }
    )
    return float(
        eval(expr, {"__builtins__": {}}, allowed)
    )  # noqa: S307 - user expression, sandboxed builtins


# ----------------------------------------------------------------------- lists
def n_list_length(ctx, items=None):
    return len(flatten(items)) if items is not None else 0


def n_list_item(ctx, items=None, index=0, wrap=True):
    seq = flatten(items)
    if not seq:
        return None
    i = int(_num(index))
    if wrap:
        i %= len(seq)
    return seq[i]


def n_merge(ctx, items=None):
    return flatten(items)


def n_flatten(ctx, items=None):
    return flatten(items)


def n_reverse(ctx, items=None):
    return list(reversed(flatten(items)))


def n_sum(ctx, items=None):
    return float(sum(_num(v) for v in flatten(items) if v is not None))


def n_average(ctx, items=None):
    seq = [v for v in flatten(items) if v is not None]
    return float(sum(_num(v) for v in seq)) / len(seq) if seq else 0.0


def n_cull_pattern(ctx, items=None, pattern=None):
    seq = flatten(items)
    pat = [bool(p) for p in flatten(pattern)] or [True]
    return [v for i, v in enumerate(seq) if pat[i % len(pat)]]


def n_shift(ctx, items=None, offset=1, wrap=True):
    seq = flatten(items)
    if not seq:
        return []
    k = int(_num(offset, 1))
    if wrap:
        k %= len(seq)
        return seq[k:] + seq[:k]
    return seq[k:] if k >= 0 else seq[:k]


# ---------------------------------------------------------------------- vector
def n_point_xyz(ctx, x=0.0, y=0.0, z=0.0):
    return Vec3(_num(x), _num(y), _num(z))


def n_vector_xyz(ctx, x=0.0, y=0.0, z=1.0):
    return Vec3(_num(x), _num(y), _num(z, 1.0))


def n_unit_z(ctx, factor=1.0):
    return Vec3(0, 0, _num(factor, 1.0))


def n_unit_x(ctx, factor=1.0):
    return Vec3(_num(factor, 1.0), 0, 0)


def n_unit_y(ctx, factor=1.0):
    return Vec3(0, _num(factor, 1.0), 0)


def n_deconstruct(ctx, point=None):
    p = Vec3.coerce(point) if point is not None else Vec3()
    return {"x": p.x, "y": p.y, "z": p.z}


def n_distance(ctx, a=None, b=None):
    a = Vec3.coerce(a) if a is not None else Vec3()
    b = Vec3.coerce(b) if b is not None else Vec3()
    return a.distance(b)


def n_vector_add(ctx, a=None, b=None):
    return (Vec3.coerce(a) if a else Vec3()) + (Vec3.coerce(b) if b else Vec3())


def n_vector_scale(ctx, vector=None, factor=1.0):
    return (Vec3.coerce(vector) if vector else Vec3(0, 0, 1)) * _num(factor, 1.0)


def n_vector_length(ctx, vector=None):
    return (Vec3.coerce(vector) if vector else Vec3()).length()


def n_cross(ctx, a=None, b=None):
    return (Vec3.coerce(a) if a else Vec3(1, 0, 0)).cross(Vec3.coerce(b) if b else Vec3(0, 1, 0))


def n_polar_point(ctx, radius=1.0, angle=0.0, z=0.0):
    r, a = _num(radius, 1.0), math.radians(_num(angle))
    return Vec3(r * math.cos(a), r * math.sin(a), _num(z))


def n_grid(ctx, count_x=3, count_y=3, spacing=10.0):
    nx, ny, s = max(1, int(_num(count_x, 3))), max(1, int(_num(count_y, 3))), _num(spacing, 10.0)
    return [Vec3(i * s, j * s, 0.0) for j in range(ny) for i in range(nx)]


# ---------------------------------------------------------------------- curves
def n_line(ctx, start=None, end=None):
    a = Vec3.coerce(start) if start is not None else Vec3()
    b = Vec3.coerce(end) if end is not None else Vec3(10, 0, 0)
    return ctx.backend.line(a, b)


def n_polyline(ctx, points=None, closed=False):
    pts = _points(points)
    return ctx.backend.polyline(pts, bool(closed))


def n_circle(ctx, center=None, radius=5.0):
    c = Vec3.coerce(center) if center is not None else Vec3()
    return ctx.backend.circle(c, _num(radius, 5.0))


def n_rectangle(ctx, width=10.0, height=10.0, origin=None):
    o = Vec3.coerce(origin) if origin is not None else Vec3()
    return ctx.backend.rectangle(_num(width, 10.0), _num(height, 10.0), o)


def n_polygon(ctx, center=None, radius=5.0, sides=6):
    c = Vec3.coerce(center) if center is not None else Vec3()
    return ctx.backend.polygon(c, _num(radius, 5.0), max(3, int(_num(sides, 6))))


# ---------------------------------------------------------------------- solids
def n_box(ctx, length=10.0, width=10.0, height=10.0, origin=None):
    o = Vec3.coerce(origin) if origin is not None else Vec3()
    return ctx.backend.box(_num(length, 10.0), _num(width, 10.0), _num(height, 10.0), o)


def n_cylinder(ctx, radius=5.0, height=10.0, origin=None):
    o = Vec3.coerce(origin) if origin is not None else Vec3()
    return ctx.backend.cylinder(_num(radius, 5.0), _num(height, 10.0), o)


def n_sphere(ctx, radius=5.0, center=None):
    c = Vec3.coerce(center) if center is not None else Vec3()
    return ctx.backend.sphere(_num(radius, 5.0), c)


def n_cone(ctx, radius1=5.0, radius2=0.0, height=10.0, origin=None):
    o = Vec3.coerce(origin) if origin is not None else Vec3()
    return ctx.backend.cone(_num(radius1, 5.0), _num(radius2), _num(height, 10.0), o)


def n_extrude(ctx, profile=None, direction=None):
    if profile is None:
        raise ValueError("no profile")
    d = Vec3.coerce(direction) if direction is not None else Vec3(0, 0, 10)
    return ctx.backend.extrude(profile, d)


def n_revolve(ctx, profile=None, center=None, axis=None, angle=360.0):
    if profile is None:
        raise ValueError("no profile")
    c = Vec3.coerce(center) if center is not None else Vec3()
    a = Vec3.coerce(axis) if axis is not None else Vec3(0, 0, 1)
    return ctx.backend.revolve(profile, c, a, _num(angle, 360.0))


# ------------------------------------------------------------------- transform
def n_move(ctx, shape=None, vector=None):
    if shape is None:
        raise ValueError("no shape")
    v = Vec3.coerce(vector) if vector is not None else Vec3()
    if isinstance(shape, Vec3):
        return shape + v
    return ctx.backend.translate(shape, v)


def n_rotate(ctx, shape=None, angle=0.0, center=None, axis=None):
    if shape is None:
        raise ValueError("no shape")
    c = Vec3.coerce(center) if center is not None else Vec3()
    a = Vec3.coerce(axis) if axis is not None else Vec3(0, 0, 1)
    if isinstance(shape, Vec3):
        from .geometry import rotate_point

        return rotate_point(shape, c, a, _num(angle))
    return ctx.backend.rotate(shape, c, a, _num(angle))


def n_scale(ctx, shape=None, factor=1.0, center=None):
    if shape is None:
        raise ValueError("no shape")
    c = Vec3.coerce(center) if center is not None else Vec3()
    return ctx.backend.scale(shape, c, _num(factor, 1.0))


def n_mirror(ctx, shape=None, base=None, normal=None):
    if shape is None:
        raise ValueError("no shape")
    b = Vec3.coerce(base) if base is not None else Vec3()
    n = Vec3.coerce(normal) if normal is not None else Vec3(1, 0, 0)
    return ctx.backend.mirror(shape, b, n)


def n_linear_array(ctx, shape=None, vector=None, count=3):
    if shape is None:
        raise ValueError("no shape")
    v = Vec3.coerce(vector) if vector is not None else Vec3(10, 0, 0)
    return [ctx.backend.translate(shape, v * i) for i in range(max(1, int(_num(count, 3))))]


def n_polar_array(ctx, shape=None, count=6, center=None, axis=None, angle=360.0):
    if shape is None:
        raise ValueError("no shape")
    n = max(1, int(_num(count, 6)))
    c = Vec3.coerce(center) if center is not None else Vec3()
    a = Vec3.coerce(axis) if axis is not None else Vec3(0, 0, 1)
    total = _num(angle, 360.0)
    step = total / n if abs(total - 360.0) < 1e-9 else (total / (n - 1) if n > 1 else 0.0)
    return [ctx.backend.rotate(shape, c, a, step * i) for i in range(n)]


# --------------------------------------------------------------------- boolean
def n_union(ctx, shapes=None):
    return ctx.backend.fuse(_shapes(ctx, shapes))


def n_difference(ctx, base=None, tools=None):
    if base is None:
        raise ValueError("no base shape")
    tools = _shapes(ctx, tools)
    if not tools:
        return base
    return ctx.backend.cut(base, tools)


def n_intersection(ctx, a=None, b=None):
    if a is None or b is None:
        raise ValueError("two shapes needed")
    return ctx.backend.common(a, b)


def n_compound(ctx, shapes=None):
    return ctx.backend.compound(_shapes(ctx, shapes))


# --------------------------------------------------------------------- queries
def n_bounding_box(ctx, shape=None):
    if shape is None:
        raise ValueError("no shape")
    b = ctx.backend.bbox(shape)
    return {
        "min": Vec3(b[0], b[1], b[2]),
        "max": Vec3(b[3], b[4], b[5]),
        "center": ctx.backend.center(shape),
    }


def n_volume(ctx, shape=None):
    return 0.0 if shape is None else ctx.backend.volume(shape)


def n_length(ctx, shape=None):
    return 0.0 if shape is None else ctx.backend.length(shape)


# ---------------------------------------------------------------------- output
def n_preview(ctx, geometry=None, enabled=True):
    return _shapes(ctx, geometry) if enabled else []


def n_bake(ctx, geometry=None):
    """Bake is a marker node; the document layer materialises its input."""
    return _shapes(ctx, geometry)


# ----------------------------------------------------------------------- media
def n_image(ctx):
    p = ctx.node.params
    return {
        "path": p.get("file", ""),
        "width": int(p.get("px_w", 0) or 0),
        "height": int(p.get("px_h", 0) or 0),
        "strokes": len(p.get("strokes") or []),
    }


def build_registry():
    """Create the registry with the standard node library."""
    reg = Registry()
    add = reg.register

    # parameters ------------------------------------------------------------
    add(
        NodeType(
            "param.slider",
            "Number Slider",
            "Params",
            [P("value", "number", 1.0)],
            [P("value", "number")],
            n_slider,
            params={"min": 0.0, "max": 10.0, "step": 0.1, "default": 1.0},
            widget="slider",
            description="A number you can drag. In XR: press the stylus tip on the slider track or pinch the knob.",
        )
    )
    add(
        NodeType(
            "param.integer",
            "Integer",
            "Params",
            [P("value", "int", 1)],
            [P("value", "int")],
            n_integer,
            widget="text",
        )
    )
    add(
        NodeType(
            "param.boolean",
            "Boolean Toggle",
            "Params",
            [P("value", "bool", True)],
            [P("value", "bool")],
            n_boolean,
            widget="toggle",
        )
    )
    add(
        NodeType(
            "param.text",
            "Text",
            "Params",
            [P("value", "text", "")],
            [P("value", "text")],
            n_text,
            widget="text",
        )
    )
    add(
        NodeType(
            "param.panel",
            "Panel",
            "Params",
            [P("value", "any")],
            [P("text", "text")],
            n_panel,
            widget="panel",
            description="Shows whatever is wired into it.",
        )
    )

    # math ------------------------------------------------------------------
    add(
        NodeType(
            "math.add",
            "Add",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 0.0)],
            [P("result", "number")],
            n_add,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.subtract",
            "Subtract",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 0.0)],
            [P("result", "number")],
            n_subtract,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.multiply",
            "Multiply",
            "Math",
            [P("a", "number", 1.0), P("b", "number", 1.0)],
            [P("result", "number")],
            n_multiply,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.divide",
            "Divide",
            "Math",
            [P("a", "number", 1.0), P("b", "number", 1.0)],
            [P("result", "number")],
            n_divide,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.power",
            "Power",
            "Math",
            [P("a", "number", 1.0), P("b", "number", 2.0)],
            [P("result", "number")],
            n_power,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.modulo",
            "Modulo",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 1.0)],
            [P("result", "number")],
            n_modulo,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.negate",
            "Negate",
            "Math",
            [P("a", "number", 0.0)],
            [P("result", "number")],
            n_negate,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.abs",
            "Absolute",
            "Math",
            [P("a", "number", 0.0)],
            [P("result", "number")],
            n_abs,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.sqrt",
            "Square Root",
            "Math",
            [P("a", "number", 0.0)],
            [P("result", "number")],
            n_sqrt,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.sin",
            "Sine",
            "Math",
            [P("angle", "number", 0.0)],
            [P("result", "number")],
            n_sin,
            elementwise=True,
            description="Angle in degrees",
        )
    )
    add(
        NodeType(
            "math.cos",
            "Cosine",
            "Math",
            [P("angle", "number", 0.0)],
            [P("result", "number")],
            n_cos,
            elementwise=True,
            description="Angle in degrees",
        )
    )
    add(
        NodeType(
            "math.tan",
            "Tangent",
            "Math",
            [P("angle", "number", 0.0)],
            [P("result", "number")],
            n_tan,
            elementwise=True,
            description="Angle in degrees",
        )
    )
    add(
        NodeType(
            "math.min",
            "Minimum",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 0.0)],
            [P("result", "number")],
            n_min,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.max",
            "Maximum",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 0.0)],
            [P("result", "number")],
            n_max,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.round",
            "Round",
            "Math",
            [P("a", "number", 0.0), P("digits", "int", 0)],
            [P("result", "number")],
            n_round,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.remap",
            "Remap",
            "Math",
            [
                P("value", "number", 0.0),
                P("source_min", "number", 0.0),
                P("source_max", "number", 1.0),
                P("target_min", "number", 0.0),
                P("target_max", "number", 10.0),
            ],
            [P("result", "number")],
            n_remap,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.series",
            "Series",
            "Math",
            [P("start", "number", 0.0), P("step", "number", 1.0), P("count", "int", 10)],
            [P("series", "list")],
            n_series,
        )
    )
    add(
        NodeType(
            "math.range",
            "Range",
            "Math",
            [P("start", "number", 0.0), P("end", "number", 1.0), P("steps", "int", 10)],
            [P("range", "list")],
            n_range,
        )
    )
    add(
        NodeType(
            "math.random",
            "Random",
            "Math",
            [
                P("count", "int", 10),
                P("minimum", "number", 0.0),
                P("maximum", "number", 1.0),
                P("seed", "int", 1),
            ],
            [P("values", "list")],
            n_random,
        )
    )
    add(
        NodeType(
            "math.pi",
            "Pi",
            "Math",
            [P("factor", "number", 1.0)],
            [P("result", "number")],
            n_pi,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.compare",
            "Compare",
            "Math",
            [P("a", "number", 0.0), P("b", "number", 0.0)],
            [P("less", "bool"), P("equal", "bool"), P("greater", "bool")],
            n_compare,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "math.expression",
            "Expression",
            "Math",
            [P("x", "number", 0.0), P("y", "number", 0.0), P("z", "number", 0.0)],
            [P("result", "number")],
            n_expression,
            elementwise=True,
            params={"expression": "x"},
            widget="text",
            description="Python math expression in x, y, z",
        )
    )

    # lists -----------------------------------------------------------------
    add(
        NodeType(
            "list.length",
            "List Length",
            "Sets",
            [P("items", "list")],
            [P("length", "int")],
            n_list_length,
        )
    )
    add(
        NodeType(
            "list.item",
            "List Item",
            "Sets",
            [P("items", "list"), P("index", "int", 0), P("wrap", "bool", True)],
            [P("item", "any")],
            n_list_item,
        )
    )
    add(
        NodeType(
            "list.merge",
            "Merge",
            "Sets",
            [P("items", "any", multi=True)],
            [P("list", "list")],
            n_merge,
            description="Accepts many wires",
        )
    )
    add(
        NodeType(
            "list.flatten", "Flatten", "Sets", [P("items", "any")], [P("list", "list")], n_flatten
        )
    )
    add(
        NodeType(
            "list.reverse", "Reverse", "Sets", [P("items", "list")], [P("list", "list")], n_reverse
        )
    )
    add(
        NodeType(
            "list.sum", "Mass Addition", "Sets", [P("items", "list")], [P("sum", "number")], n_sum
        )
    )
    add(
        NodeType(
            "list.average",
            "Average",
            "Sets",
            [P("items", "list")],
            [P("average", "number")],
            n_average,
        )
    )
    add(
        NodeType(
            "list.cull",
            "Cull Pattern",
            "Sets",
            [P("items", "list"), P("pattern", "list", [True, False])],
            [P("list", "list")],
            n_cull_pattern,
        )
    )
    add(
        NodeType(
            "list.shift",
            "Shift List",
            "Sets",
            [P("items", "list"), P("offset", "int", 1), P("wrap", "bool", True)],
            [P("list", "list")],
            n_shift,
        )
    )

    # vector ----------------------------------------------------------------
    add(
        NodeType(
            "vec.point",
            "Point XYZ",
            "Vector",
            [P("x", "number", 0.0), P("y", "number", 0.0), P("z", "number", 0.0)],
            [P("point", "point")],
            n_point_xyz,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.vector",
            "Vector XYZ",
            "Vector",
            [P("x", "number", 0.0), P("y", "number", 0.0), P("z", "number", 1.0)],
            [P("vector", "vector")],
            n_vector_xyz,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.unit_x",
            "Unit X",
            "Vector",
            [P("factor", "number", 1.0)],
            [P("vector", "vector")],
            n_unit_x,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.unit_y",
            "Unit Y",
            "Vector",
            [P("factor", "number", 1.0)],
            [P("vector", "vector")],
            n_unit_y,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.unit_z",
            "Unit Z",
            "Vector",
            [P("factor", "number", 1.0)],
            [P("vector", "vector")],
            n_unit_z,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.deconstruct",
            "Deconstruct",
            "Vector",
            [P("point", "point")],
            [P("x", "number"), P("y", "number"), P("z", "number")],
            n_deconstruct,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.distance",
            "Distance",
            "Vector",
            [P("a", "point"), P("b", "point")],
            [P("distance", "number")],
            n_distance,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.add",
            "Vector Add",
            "Vector",
            [P("a", "vector"), P("b", "vector")],
            [P("vector", "vector")],
            n_vector_add,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.scale",
            "Vector Scale",
            "Vector",
            [P("vector", "vector"), P("factor", "number", 1.0)],
            [P("vector", "vector")],
            n_vector_scale,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.length",
            "Vector Length",
            "Vector",
            [P("vector", "vector")],
            [P("length", "number")],
            n_vector_length,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.cross",
            "Cross Product",
            "Vector",
            [P("a", "vector"), P("b", "vector")],
            [P("vector", "vector")],
            n_cross,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.polar",
            "Polar Point",
            "Vector",
            [P("radius", "number", 1.0), P("angle", "number", 0.0), P("z", "number", 0.0)],
            [P("point", "point")],
            n_polar_point,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "vec.grid",
            "Rectangular Grid",
            "Vector",
            [P("count_x", "int", 3), P("count_y", "int", 3), P("spacing", "number", 10.0)],
            [P("points", "list")],
            n_grid,
        )
    )

    # curves ----------------------------------------------------------------
    add(
        NodeType(
            "curve.line",
            "Line",
            "Curve",
            [P("start", "point"), P("end", "point")],
            [P("curve", "shape")],
            n_line,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "curve.polyline",
            "Polyline",
            "Curve",
            [P("points", "list"), P("closed", "bool", False)],
            [P("curve", "shape")],
            n_polyline,
        )
    )
    add(
        NodeType(
            "curve.circle",
            "Circle",
            "Curve",
            [P("center", "point"), P("radius", "number", 5.0)],
            [P("curve", "shape")],
            n_circle,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "curve.rectangle",
            "Rectangle",
            "Curve",
            [P("width", "number", 10.0), P("height", "number", 10.0), P("origin", "point")],
            [P("curve", "shape")],
            n_rectangle,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "curve.polygon",
            "Polygon",
            "Curve",
            [P("center", "point"), P("radius", "number", 5.0), P("sides", "int", 6)],
            [P("curve", "shape")],
            n_polygon,
            elementwise=True,
        )
    )

    # solids ----------------------------------------------------------------
    add(
        NodeType(
            "solid.box",
            "Box",
            "Solid",
            [
                P("length", "number", 10.0),
                P("width", "number", 10.0),
                P("height", "number", 10.0),
                P("origin", "point"),
            ],
            [P("shape", "shape")],
            n_box,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "solid.cylinder",
            "Cylinder",
            "Solid",
            [P("radius", "number", 5.0), P("height", "number", 10.0), P("origin", "point")],
            [P("shape", "shape")],
            n_cylinder,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "solid.sphere",
            "Sphere",
            "Solid",
            [P("radius", "number", 5.0), P("center", "point")],
            [P("shape", "shape")],
            n_sphere,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "solid.cone",
            "Cone",
            "Solid",
            [
                P("radius1", "number", 5.0),
                P("radius2", "number", 0.0),
                P("height", "number", 10.0),
                P("origin", "point"),
            ],
            [P("shape", "shape")],
            n_cone,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "solid.extrude",
            "Extrude",
            "Solid",
            [P("profile", "shape"), P("direction", "vector")],
            [P("shape", "shape")],
            n_extrude,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "solid.revolve",
            "Revolve",
            "Solid",
            [
                P("profile", "shape"),
                P("center", "point"),
                P("axis", "vector"),
                P("angle", "number", 360.0),
            ],
            [P("shape", "shape")],
            n_revolve,
            elementwise=True,
        )
    )

    # transform -------------------------------------------------------------
    add(
        NodeType(
            "xform.move",
            "Move",
            "Transform",
            [P("shape", "any"), P("vector", "vector")],
            [P("shape", "any")],
            n_move,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "xform.rotate",
            "Rotate",
            "Transform",
            [
                P("shape", "any"),
                P("angle", "number", 0.0),
                P("center", "point"),
                P("axis", "vector"),
            ],
            [P("shape", "any")],
            n_rotate,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "xform.scale",
            "Scale",
            "Transform",
            [P("shape", "shape"), P("factor", "number", 1.0), P("center", "point")],
            [P("shape", "shape")],
            n_scale,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "xform.mirror",
            "Mirror",
            "Transform",
            [P("shape", "shape"), P("base", "point"), P("normal", "vector")],
            [P("shape", "shape")],
            n_mirror,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "xform.linear_array",
            "Linear Array",
            "Transform",
            [P("shape", "shape"), P("vector", "vector"), P("count", "int", 3)],
            [P("shapes", "list")],
            n_linear_array,
        )
    )
    add(
        NodeType(
            "xform.polar_array",
            "Polar Array",
            "Transform",
            [
                P("shape", "shape"),
                P("count", "int", 6),
                P("center", "point"),
                P("axis", "vector"),
                P("angle", "number", 360.0),
            ],
            [P("shapes", "list")],
            n_polar_array,
        )
    )

    # boolean ---------------------------------------------------------------
    add(
        NodeType(
            "bool.union",
            "Union",
            "Boolean",
            [P("shapes", "any", multi=True)],
            [P("shape", "shape")],
            n_union,
            description="Fuse all incoming shapes",
        )
    )
    add(
        NodeType(
            "bool.difference",
            "Difference",
            "Boolean",
            [P("base", "shape"), P("tools", "any", multi=True)],
            [P("shape", "shape")],
            n_difference,
        )
    )
    add(
        NodeType(
            "bool.intersection",
            "Intersection",
            "Boolean",
            [P("a", "shape"), P("b", "shape")],
            [P("shape", "shape")],
            n_intersection,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "bool.compound",
            "Compound",
            "Boolean",
            [P("shapes", "any", multi=True)],
            [P("shape", "shape")],
            n_compound,
        )
    )

    # queries ---------------------------------------------------------------
    add(
        NodeType(
            "query.bbox",
            "Bounding Box",
            "Analyse",
            [P("shape", "shape")],
            [P("min", "point"), P("max", "point"), P("center", "point")],
            n_bounding_box,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "query.volume",
            "Volume",
            "Analyse",
            [P("shape", "shape")],
            [P("volume", "number")],
            n_volume,
            elementwise=True,
        )
    )
    add(
        NodeType(
            "query.length",
            "Length",
            "Analyse",
            [P("shape", "shape")],
            [P("length", "number")],
            n_length,
            elementwise=True,
        )
    )

    # output ----------------------------------------------------------------
    add(
        NodeType(
            "out.preview",
            "Preview",
            "Output",
            [P("geometry", "any", multi=True), P("enabled", "bool", True)],
            [P("geometry", "list")],
            n_preview,
            params={"color": "#3b9ddd"},
            description="Shows geometry in the 3D view and floating above the XR canvas",
        )
    )
    add(
        NodeType(
            "out.bake",
            "Bake",
            "Output",
            [P("geometry", "any", multi=True)],
            [P("geometry", "list")],
            n_bake,
            params={"label": "Baked"},
            description="Creates real document objects from its input",
        )
    )
    # media -------------------------------------------------------------------
    add(
        NodeType(
            "media.image",
            "Picture",
            "Media",
            [],
            [P("path", "text"), P("width", "int"), P("height", "int"), P("strokes", "int")],
            n_image,
            params={
                "file": "",
                "url": "",
                "mode": "rendered",
                "px_w": 0,
                "px_h": 0,
                "aspect": 0.75,
                "width": 260,
                "strokes": [],
            },
            widget="image",
            description="A viewpoint picture (rendered / preview / outline). Draw on it with the stylus or a finger; export sends it to your sketching tool.",
        )
    )
    return reg


_DEFAULT_REGISTRY = None


def default_registry():
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_registry()
    return _DEFAULT_REGISTRY


def example_graph(graph):
    """Populate ``graph`` with a small demo: a slider driving a polar array of
    boxes cut from a cylinder, previewed."""
    with graph.batch():
        count = graph.add_node(
            "param.slider", 40, 40, {"value": 6}, {"min": 1, "max": 12, "step": 1, "label": "count"}
        )
        radius = graph.add_node(
            "param.slider",
            40,
            160,
            {"value": 30},
            {"min": 5, "max": 60, "step": 1, "label": "radius"},
        )
        size = graph.add_node(
            "param.slider",
            40,
            280,
            {"value": 8},
            {"min": 1, "max": 20, "step": 0.5, "label": "size"},
        )
        cyl = graph.add_node("solid.cylinder", 300, 40, {"height": 6})
        box = graph.add_node("solid.box", 300, 200, {"height": 12})
        polar = graph.add_node("xform.polar_array", 540, 200)
        origin = graph.add_node("vec.point", 300, 340, {"z": -3})
        move = graph.add_node("xform.move", 540, 340)
        move_vec = graph.add_node("vec.vector", 300, 460, {"z": 0})
        diff = graph.add_node("bool.difference", 780, 120)
        preview = graph.add_node("out.preview", 1020, 120)
        graph.connect(radius.id, "value", cyl.id, "radius")
        graph.connect(size.id, "value", box.id, "length")
        graph.connect(size.id, "value", box.id, "width")
        graph.connect(radius.id, "value", move_vec.id, "x")
        graph.connect(move_vec.id, "vector", move.id, "vector")
        graph.connect(box.id, "shape", move.id, "shape")
        graph.connect(move.id, "shape", polar.id, "shape")
        graph.connect(count.id, "value", polar.id, "count")
        graph.connect(cyl.id, "shape", diff.id, "base")
        graph.connect(polar.id, "shapes", diff.id, "tools")
        graph.connect(diff.id, "shape", preview.id, "geometry")
        graph.connect(origin.id, "point", box.id, "origin")
    graph.evaluate()
    return graph
