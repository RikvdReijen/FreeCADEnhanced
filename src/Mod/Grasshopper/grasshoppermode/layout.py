# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared node geometry for hit-testing and rendering.

Every front end (the Qt canvas, the WebXR client) uses the same numbers so a
tap at canvas coordinate (x, y) means the same thing everywhere.  Canvas
units are abstract "canvas pixels"; the XR client scales them to metres.
"""

NODE_WIDTH = 168.0
NODE_MIN_HEIGHT = 44.0
HEADER_HEIGHT = 26.0
PORT_ROW = 22.0
PORT_RADIUS = 7.0
WIDGET_HEIGHT = {"slider": 30.0, "toggle": 0.0, "text": 30.0, "panel": 70.0, "image": 120.0}
PADDING = 8.0


def widget_height(node, ntype):
    if ntype.widget == "image":
        # pictures keep their aspect ratio
        width = float(node.params.get("width") or ntype.width or NODE_WIDTH) - 2 * PADDING
        return max(40.0, width * float(node.params.get("aspect") or 0.75))
    return WIDGET_HEIGHT.get(ntype.widget, 0.0)


def node_size(node, ntype):
    width = float(node.params.get("width") or ntype.width or NODE_WIDTH)
    rows = max(len([p for p in ntype.inputs if not p.hidden]), len(ntype.outputs))
    height = HEADER_HEIGHT + rows * PORT_ROW + PADDING
    height += widget_height(node, ntype)
    return width, max(height, NODE_MIN_HEIGHT)


def node_rect(node, ntype):
    w, h = node_size(node, ntype)
    return (node.x, node.y, w, h)


def input_ports(node, ntype):
    """List of (name, x, y, kind) in canvas coordinates for visible inputs."""
    result = []
    x = node.x
    y = node.y + HEADER_HEIGHT + PORT_ROW / 2.0
    for spec in ntype.inputs:
        if spec.hidden:
            continue
        result.append((spec.name, x, y, spec.kind))
        y += PORT_ROW
    return result


def output_ports(node, ntype):
    w, _h = node_size(node, ntype)
    result = []
    x = node.x + w
    y = node.y + HEADER_HEIGHT + PORT_ROW / 2.0
    for spec in ntype.outputs:
        result.append((spec.name, x, y, spec.kind))
        y += PORT_ROW
    return result


def widget_rect(node, ntype):
    """Rectangle of the inline widget (slider etc.) or None."""
    if not ntype.widget or widget_height(node, ntype) <= 0:
        return None
    w, h = node_size(node, ntype)
    wh = widget_height(node, ntype)
    return (node.x + PADDING, node.y + h - wh - PADDING / 2.0, w - 2 * PADDING, wh)


def point_in_rect(px, py, rect):
    x, y, w, h = rect
    return x <= px <= x + w and y <= py <= y + h


def _dist2(ax, ay, bx, by):
    return (ax - bx) ** 2 + (ay - by) ** 2


def hit_test(graph, px, py, port_slack=1.6):
    """Return what lies under canvas point (px, py).

    Result is a dict ``{"kind": ...}`` where kind is ``port`` (with node,
    port, direction), ``widget`` (node), ``header`` (node), ``node`` (node),
    ``wire`` (wire) or ``empty``.  Topmost (last added) nodes win.
    """
    radius2 = (PORT_RADIUS * port_slack) ** 2
    for node in reversed(list(graph.nodes.values())):
        ntype = graph.node_type(node)
        for name, x, y, kind in input_ports(node, ntype) + output_ports(node, ntype):
            if _dist2(px, py, x, y) <= radius2:
                direction = (
                    "in"
                    if ntype.input(name) is not None
                    and (name, x, y, kind) in input_ports(node, ntype)
                    else "out"
                )
                return {
                    "kind": "port",
                    "node": node.id,
                    "port": name,
                    "direction": direction,
                    "type": kind,
                }
    for node in reversed(list(graph.nodes.values())):
        ntype = graph.node_type(node)
        rect = node_rect(node, ntype)
        if point_in_rect(px, py, rect):
            wr = widget_rect(node, ntype)
            if wr and point_in_rect(px, py, wr):
                return {"kind": "widget", "node": node.id, "widget": ntype.widget, "rect": wr}
            if py <= node.y + HEADER_HEIGHT:
                return {"kind": "header", "node": node.id}
            return {"kind": "node", "node": node.id}
    wire = wire_hit_test(graph, px, py)
    if wire:
        return {"kind": "wire", "wire": wire}
    return {"kind": "empty"}


def wire_path(graph, wire):
    """Return (x1, y1, x2, y2) endpoints of a wire in canvas coordinates."""
    src = graph.nodes[wire.src_node]
    dst = graph.nodes[wire.dst_node]
    p1 = [p for p in output_ports(src, graph.node_type(src)) if p[0] == wire.src_port]
    p2 = [p for p in input_ports(dst, graph.node_type(dst)) if p[0] == wire.dst_port]
    if not p1 or not p2:
        return None
    return (p1[0][1], p1[0][2], p2[0][1], p2[0][2])


def bezier_points(x1, y1, x2, y2, segments=16):
    """Sample the S-curve used to draw wires."""
    dx = max(abs(x2 - x1) * 0.5, 30.0)
    c1x, c1y, c2x, c2y = x1 + dx, y1, x2 - dx, y2
    pts = []
    for i in range(segments + 1):
        t = i / float(segments)
        mt = 1 - t
        x = mt**3 * x1 + 3 * mt**2 * t * c1x + 3 * mt * t**2 * c2x + t**3 * x2
        y = mt**3 * y1 + 3 * mt**2 * t * c1y + 3 * mt * t**2 * c2y + t**3 * y2
        pts.append((x, y))
    return pts


def wire_hit_test(graph, px, py, tolerance=6.0):
    best = None
    best_d2 = tolerance**2
    for wire in graph.wires.values():
        path = wire_path(graph, wire)
        if not path:
            continue
        pts = bezier_points(*path)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            d2 = _segment_dist2(px, py, ax, ay, bx, by)
            if d2 < best_d2:
                best_d2 = d2
                best = wire.id
    return best


def _segment_dist2(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    seg = vx * vx + vy * vy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / seg))
    cx, cy = ax + t * vx, ay + t * vy
    return _dist2(px, py, cx, cy)


def nodes_in_rect(graph, x, y, w, h):
    """Ids of nodes whose rectangle intersects the given rectangle."""
    x0, y0, x1, y1 = min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h)
    hits = []
    for node in graph.nodes.values():
        nx, ny, nw, nh = node_rect(node, graph.node_type(node))
        if nx < x1 and nx + nw > x0 and ny < y1 and ny + nh > y0:
            hits.append(node.id)
    return hits


def slider_value_at(node, ntype, px):
    """Map a canvas x position on a slider widget to a value."""
    wr = widget_rect(node, ntype)
    if wr is None:
        return None
    lo = float(node.params.get("min", 0.0))
    hi = float(node.params.get("max", 10.0))
    step = float(node.params.get("step", 0.0) or 0.0)
    t = 0.0 if wr[2] <= 0 else max(0.0, min(1.0, (px - wr[0]) / wr[2]))
    value = lo + t * (hi - lo)
    if step > 0:
        value = lo + round((value - lo) / step) * step
    return max(lo, min(hi, value))
