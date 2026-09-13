# SPDX-License-Identifier: LGPL-2.1-or-later
"""Viewpoint pictures ("snapshots") that live on the node canvas.

A snapshot is a PNG taken from the XR client (headset view of the preview
stage) or from FreeCAD's 3D view, in one of three modes: ``rendered``,
``preview`` (flat colours) or ``outline`` (hidden-line).  It becomes a
``media.image`` node on the same canvas as the Grasshopper nodes, can be
drawn on (vector strokes stored with the node, so they stay editable) and
exported as a flattened PNG for tools such as Vizcom.
"""

import base64
import json
import os
import re
import time
import uuid

MODES = ("rendered", "preview", "outline")
IMAGE_NODE = "media.image"
DEFAULT_IMAGE_WIDTH = 260.0


class MediaStore:
    """Files of one canvas under ``<root>/<canvas id>/``."""

    def __init__(self, root, canvas_id):
        self.root = os.path.abspath(root)
        self.canvas_id = _safe(canvas_id)
        self.dir = os.path.join(self.root, self.canvas_id)

    def ensure(self):
        os.makedirs(self.dir, exist_ok=True)
        return self.dir

    def new_name(self, mode, ext="png"):
        return "%s-%s-%s.%s" % (
            time.strftime("%Y%m%d-%H%M%S"),
            _safe(mode),
            uuid.uuid4().hex[:6],
            ext,
        )

    def path(self, name):
        name = _safe_file(name)
        full = os.path.abspath(os.path.join(self.dir, name))
        if not full.startswith(self.dir + os.sep):
            raise ValueError("invalid media name")
        return full

    def save_png(self, data, mode="rendered", name=None):
        """``data`` is raw PNG bytes or a data URL.  Returns the file name."""
        if isinstance(data, str):
            data = decode_data_url(data)
        if not data.startswith(b"\x89PNG"):
            raise ValueError("not a PNG")
        self.ensure()
        name = name or self.new_name(mode)
        with open(self.path(name), "wb") as fh:
            fh.write(data)
        return name

    def read(self, name):
        with open(self.path(name), "rb") as fh:
            return fh.read()

    def exists(self, name):
        try:
            return os.path.isfile(self.path(name))
        except ValueError:
            return False

    def url(self, name):
        return "/media/%s/%s" % (self.canvas_id, name)

    def list(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(f for f in os.listdir(self.dir) if f.lower().endswith(".png"))


def decode_data_url(text):
    m = re.match(r"^data:image/png;base64,(.*)$", text.strip(), re.S)
    if not m:
        raise ValueError("expected a PNG data URL")
    return base64.b64decode(m.group(1))


def png_size(data):
    """(width, height) from the IHDR chunk."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(text))[:64] or "x"


def _safe_file(name):
    name = str(name)
    if (
        "/" in name
        or "\\" in name
        or not re.match(r"^[A-Za-z0-9_.-]+$", name)
        or name.startswith(".")
    ):
        raise ValueError("invalid media name")
    return name


# ------------------------------------------------------------- graph side
def add_image_node(
    graph, store, name, mode="rendered", x=0.0, y=0.0, pose=None, note="", size=None
):
    """Create a ``media.image`` node for a stored picture."""
    if size is None:
        size = png_size(store.read(name))
    width = DEFAULT_IMAGE_WIDTH
    aspect = size[1] / float(size[0]) if size[0] else 0.75
    node = graph.add_node(
        IMAGE_NODE,
        x,
        y,
        {},
        {
            "file": name,
            "url": store.url(name),
            "mode": mode if mode in MODES else "rendered",
            "px_w": int(size[0]),
            "px_h": int(size[1]),
            "aspect": aspect,
            "width": width,
            "strokes": [],
            "pose": pose or None,
            "label": "%s view" % mode,
            "note": note,
        },
    )
    return node


def add_stroke(graph, node_id, points, color="#ff3b30", width=3.0):
    """Append a polyline (image pixel coordinates) to a picture node."""
    node = graph.nodes[node_id]
    if node.type_id != IMAGE_NODE:
        raise ValueError("not a picture node")
    pts = [[float(p[0]), float(p[1])] for p in points if len(p) >= 2]
    if len(pts) < 1:
        raise ValueError("stroke needs points")
    strokes = list(node.params.get("strokes") or [])
    strokes.append({"points": pts, "color": str(color), "width": float(width)})
    graph.set_param(node_id, "strokes", strokes)
    return len(strokes)


def clear_strokes(graph, node_id):
    graph.set_param(node_id, "strokes", [])


def image_widget_height(node):
    """Height of the picture area for a node (keeps the aspect ratio)."""
    width = float(node.params.get("width") or DEFAULT_IMAGE_WIDTH) - 16.0
    return max(40.0, width * float(node.params.get("aspect") or 0.75))


def strokes_svg(node, size=None):
    """Strokes as an SVG overlay (for exports without a raster library)."""
    w = int(node.params.get("px_w") or 1024)
    h = int(node.params.get("px_h") or 768)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (w, h, w, h)
    ]
    if size:
        parts.append('<image href="%s" width="%d" height="%d"/>' % (size, w, h))
    for s in node.params.get("strokes") or []:
        pts = " ".join("%.1f,%.1f" % (p[0], p[1]) for p in s["points"])
        parts.append(
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="%g" stroke-linecap="round" stroke-linejoin="round"/>'
            % (pts, s.get("color", "#f00"), s.get("width", 3))
        )
    parts.append("</svg>")
    return "".join(parts)


def export_bundle(store, node):
    """Write ``<name>-export.svg`` (picture + strokes) next to the picture.

    A flattened PNG is produced by the client (browser or Qt) which can
    rasterise; the SVG is the dependency-free fallback and keeps strokes
    editable.  Returns the path.
    """
    name = node.params["file"]
    data = store.read(name)
    href = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    svg = strokes_svg(node, href)
    out = store.path(os.path.splitext(name)[0] + "-export.svg")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return out


def node_summary(node):
    return json.dumps(
        {
            "file": node.params.get("file"),
            "mode": node.params.get("mode"),
            "strokes": len(node.params.get("strokes") or []),
        }
    )
