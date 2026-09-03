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
"""The in-VR paint UI, built on the existing ``xrcore.menuCoin`` widgets.

The module is deliberately split in two:

* :class:`PaintUiState` -- a pure state machine.  No Coin, no FreeCAD, fully
  unit-testable; it also owns the geometry *maths* of the colour wheel and of
  the vector node gizmo.
* :class:`PaintCoinUi` and friends -- thin scenegraph builders that import
  ``pivy.coin`` and ``xrcore.menuCoin`` lazily inside their methods.

Widget names follow the upstream convention so
:meth:`PaintUiState.on_widget` can be fed straight from
``coinMenu.find_picked_widget()``.
"""

import math

from . import brush as _brush
from . import prefs as _prefs

__all__ = [
    "MODES",
    "VECTOR_TOOLS",
    "ColorWheel",
    "LayerPanelModel",
    "NodeGizmoModel",
    "PaintCoinUi",
    "PaintUiState",
    "UiAction",
    "hsv_to_rgb",
    "rgb_to_hsv",
]

MODES = ("TEXTURE", "STROKE3D", "VECTOR")

VECTOR_TOOLS = ("draw", "node", "pen", "select")

MIN_RADIUS_PX = 1.0
MAX_RADIUS_PX = 256.0


# --------------------------------------------------------------------------
# colour maths
# --------------------------------------------------------------------------

def hsv_to_rgb(h, s, v):
    """HSV (all 0..1, hue wraps) to RGB floats 0..1."""
    h = float(h) % 1.0
    s = max(0.0, min(1.0, float(s)))
    v = max(0.0, min(1.0, float(v)))
    if s <= 0.0:
        return (v, v, v)
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i %= 6
    if i == 0:
        return (v, t, p)
    if i == 1:
        return (q, v, p)
    if i == 2:
        return (p, v, t)
    if i == 3:
        return (p, q, v)
    if i == 4:
        return (t, p, v)
    return (v, p, q)


def rgb_to_hsv(r, g, b):
    """RGB floats 0..1 to HSV floats 0..1."""
    r = max(0.0, min(1.0, float(r)))
    g = max(0.0, min(1.0, float(g)))
    b = max(0.0, min(1.0, float(b)))
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d <= 0.0:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d % 6.0) / 6.0
    elif mx == g:
        h = ((b - r) / d + 2.0) / 6.0
    else:
        h = ((r - g) / d + 4.0) / 6.0
    s = 0.0 if mx <= 0.0 else d / mx
    return (h % 1.0, s, mx)


class ColorWheel(object):
    """HSV wheel geometry and hit testing (pure maths, no Coin).

    The wheel lives in its own local XY plane, centred on the origin, with
    hue running counter-clockwise from +X and saturation growing outwards.
    """

    def __init__(self, radius=0.06, rings=6, sectors=24):
        self.radius = float(radius)
        self.rings = max(1, int(rings))
        self.sectors = max(3, int(sectors))

    def vertices(self, value=1.0):
        """``(points, colors)`` for a triangle-fan-free indexed mesh.

        Vertex 0 is the centre; then ``rings * sectors`` rim vertices.
        """
        pts = [(0.0, 0.0, 0.0)]
        cols = [hsv_to_rgb(0.0, 0.0, value)]
        for ri in range(1, self.rings + 1):
            s = ri / float(self.rings)
            r = self.radius * s
            for si in range(self.sectors):
                a = 2.0 * math.pi * si / self.sectors
                pts.append((r * math.cos(a), r * math.sin(a), 0.0))
                cols.append(hsv_to_rgb(si / float(self.sectors), s, value))
        return pts, cols

    def faces(self):
        """Quad/triangle indices matching :meth:`vertices`."""
        out = []
        S = self.sectors
        for si in range(S):
            nxt = (si + 1) % S
            out.append((0, 1 + si, 1 + nxt))
        for ri in range(1, self.rings):
            b0 = 1 + (ri - 1) * S
            b1 = 1 + ri * S
            for si in range(S):
                nxt = (si + 1) % S
                out.append((b0 + si, b1 + si, b1 + nxt, b0 + nxt))
        return out

    def pick(self, x, y):
        """Local (x, y) to ``(hue, saturation)``, or ``None`` outside."""
        d = math.hypot(x, y)
        if d > self.radius:
            return None
        h = (math.atan2(y, x) / (2.0 * math.pi)) % 1.0
        s = 0.0 if self.radius <= 0.0 else min(1.0, d / self.radius)
        return (h, s)

    def position_of(self, hue, sat):
        """Inverse of :meth:`pick`."""
        a = float(hue) * 2.0 * math.pi
        r = self.radius * max(0.0, min(1.0, float(sat)))
        return (r * math.cos(a), r * math.sin(a))


# --------------------------------------------------------------------------
# ui actions
# --------------------------------------------------------------------------

class UiAction(object):
    """Something the session has to do because a widget was touched."""

    __slots__ = ("name", "value", "index")

    def __init__(self, name, value=None, index=None):
        self.name = name
        self.value = value
        self.index = index

    def __eq__(self, other):
        return (isinstance(other, UiAction) and other.name == self.name
                and other.value == self.value and other.index == self.index)

    def __repr__(self):
        return "UiAction(%r, %r, %r)" % (self.name, self.value, self.index)


# --------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------

class PaintUiState(object):
    """Everything the VR paint UI remembers.  Pure Python, no Coin."""

    MAX_SWATCHES = 12

    def __init__(self, mode=None, max_swatches=None):
        self.mode = mode if mode in MODES else None
        self.brush = _brush.preset("round")
        self.brush.radius = self._radius_from_prefs()
        self.brush.blend = _prefs.get_string("BlendMode") or "normal"
        self.tool = "round"
        self.vector_tool = "draw"
        self.pressure_enabled = _prefs.get_bool("PressureEnabled")
        self.hue = 0.0
        self.saturation = 0.0
        self.value = 0.0
        self.alpha = 1.0
        self.max_swatches = int(max_swatches or self.MAX_SWATCHES)
        self.swatches = []
        self.palette = []
        self.active_layer = 0
        self.layer_panel_open = False
        self.color_picker_open = False
        self.selected_path = None
        self.selected_node = None
        self.selected_handle = None
        self.wheel = ColorWheel()
        self.snap_enabled = True
        self.symmetry = False
        self.log = []

    # -- colour ----------------------------------------------------------
    def _radius_from_prefs(self):
        # BrushRadius is in millimetres; the brush works in texels, so use a
        # sensible default mapping of 4 texels per millimetre.
        return max(MIN_RADIUS_PX,
                   min(MAX_RADIUS_PX, _prefs.get_float("BrushRadius") * 4.0))

    @property
    def color_rgb(self):
        return hsv_to_rgb(self.hue, self.saturation, self.value)

    @property
    def color_rgba(self):
        r, g, b = self.color_rgb
        return (r, g, b, self.alpha)

    @property
    def color_rgba255(self):
        r, g, b = self.color_rgb
        return (int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5),
                int(self.alpha * 255 + 0.5))

    def set_color_hsv(self, h=None, s=None, v=None, a=None):
        if h is not None:
            self.hue = float(h) % 1.0
        if s is not None:
            self.saturation = max(0.0, min(1.0, float(s)))
        if v is not None:
            self.value = max(0.0, min(1.0, float(v)))
        if a is not None:
            self.alpha = max(0.0, min(1.0, float(a)))
        return self.color_rgba

    def set_color_rgb(self, r, g, b, a=None):
        self.hue, self.saturation, self.value = rgb_to_hsv(r, g, b)
        if a is not None:
            self.alpha = max(0.0, min(1.0, float(a)))
        return self.color_rgba

    def pick_wheel(self, x, y):
        """Feed a local wheel hit; returns the new colour or ``None``."""
        hit = self.wheel.pick(x, y)
        if hit is None:
            return None
        self.set_color_hsv(hit[0], hit[1])
        return self.color_rgba

    def push_swatch(self, color=None):
        """Remember a colour; most recent first, de-duplicated, bounded."""
        c = tuple(round(float(v), 6) for v in (color or self.color_rgba))
        if c in self.swatches:
            self.swatches.remove(c)
        self.swatches.insert(0, c)
        del self.swatches[self.max_swatches:]
        return list(self.swatches)

    def use_swatch(self, index):
        if not (0 <= index < len(self.swatches)):
            return None
        c = self.swatches[index]
        self.set_color_rgb(c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0)
        return self.color_rgba

    # -- brush -----------------------------------------------------------
    def select_tool(self, name):
        if name in _brush.PRESETS:
            radius = self.brush.radius
            blend = self.brush.blend
            self.brush = _brush.preset(name)
            self.brush.radius = radius
            self.brush.blend = blend
            self.tool = name
            return True
        if name in VECTOR_TOOLS:
            self.vector_tool = name
            return True
        return False

    def set_radius(self, value, normalised=False):
        if normalised:
            v = max(0.0, min(1.0, float(value)))
            # perceptually even: geometric interpolation
            value = MIN_RADIUS_PX * (MAX_RADIUS_PX / MIN_RADIUS_PX) ** v
        self.brush.radius = max(MIN_RADIUS_PX,
                                min(MAX_RADIUS_PX, float(value)))
        return self.brush.radius

    def radius_normalised(self):
        r = max(MIN_RADIUS_PX, min(MAX_RADIUS_PX, self.brush.radius))
        return math.log(r / MIN_RADIUS_PX) / math.log(MAX_RADIUS_PX
                                                      / MIN_RADIUS_PX)

    def set_blend(self, mode):
        from . import raster
        if mode not in raster.BLEND_MODES:
            return False
        self.brush.blend = mode
        return True

    def set_mode(self, mode):
        if mode is None:
            self.mode = None
            return True
        mode = str(mode).upper()
        if mode not in MODES:
            return False
        self.mode = mode
        return True

    # -- widget dispatch -------------------------------------------------
    def on_widget(self, name, value=0.0):
        """Handle a widget hit; returns a :class:`UiAction` or ``None``.

        Widget naming: ``mode_<mode>``, ``tool_<preset>``, ``vtool_<tool>``,
        ``blend_<mode>``, ``swatch_<i>``, ``layer_<verb>``,
        ``layer_vis_<i>``, ``layer_sel_<i>``, and the sliders
        ``radius_slider``, ``hardness_slider``, ``flow_slider``,
        ``opacity_slider``, ``spacing_slider``, ``alpha_slider``,
        ``hue_slider``, ``sat_slider``, ``value_slider``.
        """
        if not name:
            return None
        self.log.append((name, value))
        del self.log[:-64]

        if name.startswith("mode_"):
            m = name[5:].upper()
            if self.set_mode(m):
                return UiAction("mode", m)
            return None
        if name.startswith("tool_"):
            t = name[5:]
            if self.select_tool(t):
                return UiAction("tool", t)
            return None
        if name.startswith("vtool_"):
            t = name[6:]
            if self.select_tool(t):
                return UiAction("vector_tool", t)
            return None
        if name.startswith("blend_"):
            m = name[6:]
            if self.set_blend(m):
                return UiAction("blend", m)
            return None
        if name.startswith("swatch_"):
            try:
                i = int(name[7:])
            except ValueError:
                return None
            if self.use_swatch(i) is None:
                return None
            return UiAction("color", self.color_rgba, i)
        if name.startswith("layer_vis_"):
            try:
                i = int(name[10:])
            except ValueError:
                return None
            return UiAction("layer_visible", None, i)
        if name.startswith("layer_sel_"):
            try:
                i = int(name[10:])
            except ValueError:
                return None
            self.active_layer = i
            return UiAction("layer_select", None, i)
        if name in ("layer_add", "layer_remove", "layer_up", "layer_down",
                    "layer_merge", "layer_flatten"):
            return UiAction(name, None, self.active_layer)
        if name in ("undo", "redo", "clear_layer", "commit_vector",
                    "export_svg"):
            return UiAction(name)
        if name == "toggle_layers":
            self.layer_panel_open = not self.layer_panel_open
            return UiAction("toggle_layers", self.layer_panel_open)
        if name == "toggle_color":
            self.color_picker_open = not self.color_picker_open
            return UiAction("toggle_color", self.color_picker_open)
        if name == "toggle_snap":
            self.snap_enabled = not self.snap_enabled
            return UiAction("toggle_snap", self.snap_enabled)
        if name == "toggle_symmetry":
            self.symmetry = not self.symmetry
            return UiAction("toggle_symmetry", self.symmetry)
        if name == "toggle_pressure":
            self.pressure_enabled = not self.pressure_enabled
            return UiAction("toggle_pressure", self.pressure_enabled)

        v = max(0.0, min(1.0, float(value)))
        if name == "radius_slider":
            return UiAction("radius", self.set_radius(v, normalised=True))
        if name == "hardness_slider":
            self.brush.hardness = v
            return UiAction("hardness", v)
        if name == "flow_slider":
            self.brush.flow = v
            return UiAction("flow", v)
        if name == "opacity_slider":
            self.brush.opacity = v
            return UiAction("opacity", v)
        if name == "spacing_slider":
            self.brush.spacing = max(0.01, v)
            return UiAction("spacing", self.brush.spacing)
        if name == "alpha_slider":
            self.alpha = v
            return UiAction("color", self.color_rgba)
        if name == "hue_slider":
            self.set_color_hsv(h=v)
            return UiAction("color", self.color_rgba)
        if name == "sat_slider":
            self.set_color_hsv(s=v)
            return UiAction("color", self.color_rgba)
        if name in ("value_slider", "val_slider"):
            self.set_color_hsv(v=v)
            return UiAction("color", self.color_rgba)
        return None

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "mode": self.mode,
            "tool": self.tool,
            "vector_tool": self.vector_tool,
            "color": list(self.color_rgba),
            "swatches": [list(s) for s in self.swatches],
            "brush": self.brush.to_dict(),
            "active_layer": self.active_layer,
            "pressure_enabled": self.pressure_enabled,
            "snap_enabled": self.snap_enabled,
        }

    def __repr__(self):
        return "PaintUiState(mode=%r, tool=%r)" % (self.mode, self.tool)


# --------------------------------------------------------------------------
# layer panel model
# --------------------------------------------------------------------------

class LayerPanelModel(object):
    """Rows of the layer panel, derived from a stack.  Pure Python."""

    def __init__(self, stack=None):
        self.stack = stack

    def rows(self):
        """Top layer first, matching how the panel is drawn."""
        if self.stack is None:
            return []
        out = []
        n = len(self.stack.layers)
        for i in range(n - 1, -1, -1):
            layer = self.stack.layers[i]
            out.append({
                "index": i,
                "row": n - 1 - i,
                "name": layer.name,
                "visible": layer.visible,
                "locked": layer.locked,
                "opacity": layer.opacity,
                "blend": layer.blend,
                "active": (i == self.stack.active_index),
                "label": "%s%s %d%%" % ("" if layer.visible else "(hidden) ",
                                        layer.name,
                                        int(layer.opacity * 100 + 0.5)),
            })
        return out

    def apply(self, action):
        """Apply a :class:`UiAction` produced by :meth:`PaintUiState.on_widget`.

        Returns ``True`` when the stack changed.
        """
        st = self.stack
        if st is None or action is None:
            return False
        name = action.name
        i = action.index if action.index is not None else st.active_index
        n = len(st.layers)
        if name == "layer_add":
            st.add_layer()
            return True
        if name == "layer_remove" and n > 1 and 0 <= i < n:
            st.remove_layer(i)
            return True
        if name == "layer_up" and 0 <= i < n - 1:
            st.move_layer(i, i + 1)
            return True
        if name == "layer_down" and 0 < i < n:
            st.move_layer(i, i - 1)
            return True
        if name == "layer_merge" and 0 < i < n:
            st.merge_down(i)
            return True
        if name == "layer_flatten" and n > 1:
            st.flatten()
            return True
        if name == "layer_visible" and 0 <= i < n:
            st.set_visible(i, not st.layers[i].visible)
            return True
        if name == "layer_select" and 0 <= i < n:
            st.active_index = i
            return True
        return False


# --------------------------------------------------------------------------
# vector node gizmo model
# --------------------------------------------------------------------------

class NodeGizmoModel(object):
    """Node/handle editing gizmo: pure geometry plus hit testing."""

    def __init__(self, pick_radius=1.5):
        self.pick_radius = float(pick_radius)

    def geometry(self, path):
        """``{"nodes": [...], "handles": [...], "lines": [...]}`` in 2D."""
        nodes = []
        handles = []
        lines = []
        if path is None:
            return {"nodes": nodes, "handles": handles, "lines": lines}
        for i, nd in enumerate(path.nodes):
            nodes.append({"index": i, "point": nd.point, "type": nd.type})
            if nd.handle_in is not None:
                handles.append({"index": i, "which": "in",
                                "point": nd.in_point})
                lines.append((nd.point, nd.in_point))
            if nd.handle_out is not None:
                handles.append({"index": i, "which": "out",
                                "point": nd.out_point})
                lines.append((nd.point, nd.out_point))
        return {"nodes": nodes, "handles": handles, "lines": lines}

    def pick(self, path, point, radius=None):
        """What is under ``point``.

        Returns ``("node", i)``, ``("handle", i, "in"|"out")``,
        ``("segment", i, t)`` or ``None``.  Handles win over nodes so a node
        with two handles stays editable.
        """
        if path is None or not path.nodes:
            return None
        r = self.pick_radius if radius is None else float(radius)
        hi, which, hd = path.closest_handle(point)
        ni, nd = path.closest_node(point)
        if hd <= r and (hd <= nd or nd > r):
            return ("handle", hi, which)
        if nd <= r:
            return ("node", ni)
        if path.segment_count():
            si, t, pt, d = path.closest_point(point)
            if d <= r:
                return ("segment", si, t)
        return None

    def drag(self, path, target, point):
        """Apply a drag of the picked element to ``point``.

        Node constraints are enforced by :class:`~xrpaint.vector.Node`.
        """
        if path is None or target is None:
            return False
        kind = target[0]
        if kind == "node":
            path.nodes[target[1]].set_point(point)
            return True
        if kind == "handle":
            node = path.nodes[target[1]]
            if target[2] == "in":
                node.set_in_point(point)
            else:
                node.set_out_point(point)
            return True
        return False


# --------------------------------------------------------------------------
# Coin builders (lazy imports)
# --------------------------------------------------------------------------

class PaintCoinUi(object):
    """Builds the in-VR paint menus out of ``xrcore.menuCoin`` widgets.

    Nothing is imported until :meth:`build` runs, so importing
    :mod:`xrpaint.ui` never pulls in Coin.
    """

    def __init__(self, state=None):
        self.state = state or PaintUiState()
        self.menu = None
        self.widgets = {}
        self.wheel_nodes = None
        self.layer_panel = LayerPanelModel()
        self.gizmo = NodeGizmoModel()
        self._gizmo_nodes = None
        self._built = False

    # -- construction ----------------------------------------------------
    def build(self, visible=False):
        """Create the whole palette; returns the ``coinMenu``."""
        from xrcore.menuCoin import coinMenu
        from pivy.coin import SbVec3f, SbRotation
        menu = coinMenu(visible)
        self.menu = menu
        y = 0.30
        self._add_buttons(menu, [("mode_TEXTURE", "Texture"),
                                 ("mode_STROKE3D", "Air Brush"),
                                 ("mode_VECTOR", "Vector")],
                          -0.05, y, radio_group=1, width=0.14)
        y -= 0.05
        presets = list(_brush.PRESETS.keys())
        self._add_buttons(menu, [("tool_%s" % p, p.title()) for p in presets],
                          -0.05, y, radio_group=2, width=0.12, per_row=4)
        y -= 0.10
        from . import raster
        self._add_buttons(menu, [("blend_%s" % b, b.title())
                                 for b in raster.BLEND_MODES],
                          -0.05, y, radio_group=3, width=0.12, per_row=5)
        y -= 0.05
        for name, label, value in (
                ("radius_slider", "Radius", self.state.radius_normalised()),
                ("hardness_slider", "Hardness", self.state.brush.hardness),
                ("flow_slider", "Flow", self.state.brush.flow),
                ("opacity_slider", "Opacity", self.state.brush.opacity),
                ("hue_slider", "Hue", self.state.hue),
                ("sat_slider", "Saturation", self.state.saturation),
                ("value_slider", "Value", self.state.value),
                ("alpha_slider", "Alpha", self.state.alpha)):
            self._add_slider(menu, name, label, value, -0.05, y)
            y -= 0.045
        y -= 0.02
        self._add_buttons(menu, [("layer_add", "New Layer"),
                                 ("layer_remove", "Delete Layer"),
                                 ("layer_up", "Raise"),
                                 ("layer_down", "Lower"),
                                 ("layer_merge", "Merge Down"),
                                 ("undo", "Undo"), ("redo", "Redo")],
                          -0.05, y, width=0.16, per_row=4)
        for w in menu.widget_list:
            self.widgets[w.name] = w
        self._built = True
        return menu

    def _add_buttons(self, menu, items, x0, y, radio_group=0, width=0.12,
                     per_row=3, pitch=None):
        from xrcore.menuCoin import buttonWidget
        from pivy.coin import SbVec3f, SbRotation
        pitch = pitch or (width + 0.01)
        for i, (name, label) in enumerate(items):
            col = i % per_row
            row = i // per_row
            btn = buttonWidget(name, label, radio_group, width)
            btn.set_location(SbVec3f(x0 + col * pitch, y - row * 0.045, -0.3),
                             SbRotation(0, 0, 0, 1))
            menu.widget_list.append(btn)
            menu.menu_node.addChild(btn.get_scenegraph())

    def _add_slider(self, menu, name, label, value, x, y):
        from xrcore.menuCoin import sliderWidget
        from pivy.coin import SbVec3f, SbRotation
        sl = sliderWidget(name, label, max(0.0, min(1.0, float(value))))
        sl.set_location(SbVec3f(x, y, -0.3), SbRotation(0, 0, 0, 1))
        menu.widget_list.append(sl)
        menu.menu_node.addChild(sl.get_scenegraph())

    # -- colour wheel ----------------------------------------------------
    def build_color_wheel(self, wheel=None, value=None):
        """An ``SoSeparator`` holding the HSV wheel as coloured geometry."""
        from pivy.coin import (SoSeparator, SoCoordinate3, SoIndexedFaceSet,
                               SoMaterial, SoMaterialBinding, SoShapeHints)
        wheel = wheel or self.state.wheel
        val = self.state.value if value is None else float(value)
        pts, cols = wheel.vertices(val)
        faces = wheel.faces()
        sep = SoSeparator()
        hints = SoShapeHints()
        hints.vertexOrdering = SoShapeHints.COUNTERCLOCKWISE
        sep.addChild(hints)
        mat = SoMaterial()
        for i, c in enumerate(cols):
            mat.diffuseColor.set1Value(i, c[0], c[1], c[2])
        sep.addChild(mat)
        binding = SoMaterialBinding()
        binding.value = SoMaterialBinding.PER_VERTEX_INDEXED
        sep.addChild(binding)
        coords = SoCoordinate3()
        for i, p in enumerate(pts):
            coords.point.set1Value(i, p[0], p[1], p[2])
        sep.addChild(coords)
        fs = SoIndexedFaceSet()
        idx = []
        for f in faces:
            idx.extend(f)
            idx.append(-1)
        fs.coordIndex.setValues(0, len(idx), idx)
        sep.addChild(fs)
        self.wheel_nodes = sep
        return sep

    # -- node gizmo ------------------------------------------------------
    def build_node_gizmo(self, path):
        """Points for the nodes, points+lines for the handles."""
        from pivy.coin import (SoSeparator, SoVertexProperty, SoPointSet,
                               SoLineSet, SoBaseColor, SbColor, SoDrawStyle)
        geo = self.gizmo.geometry(path)
        sep = SoSeparator()

        line_vp = SoVertexProperty()
        counts = []
        k = 0
        for a, b in geo["lines"]:
            line_vp.vertex.set1Value(k, a[0], a[1], 0.0)
            line_vp.vertex.set1Value(k + 1, b[0], b[1], 0.0)
            k += 2
            counts.append(2)
        if counts:
            lines = SoLineSet()
            lines.vertexProperty = line_vp
            lines.numVertices.setValues(0, len(counts), counts)
            lsep = SoSeparator()
            lcol = SoBaseColor()
            lcol.rgb = SbColor(0.4, 0.4, 0.9)
            lsep.addChild(lcol)
            lsep.addChild(lines)
            sep.addChild(lsep)

        for key, rgb, size in (("nodes", (0.1, 0.9, 0.1), 6.0),
                               ("handles", (0.9, 0.6, 0.1), 4.0)):
            items = geo[key]
            if not items:
                continue
            psep = SoSeparator()
            style = SoDrawStyle()
            style.pointSize = size
            psep.addChild(style)
            col = SoBaseColor()
            col.rgb = SbColor(*rgb)
            psep.addChild(col)
            vp = SoVertexProperty()
            for i, it in enumerate(items):
                p = it["point"]
                vp.vertex.set1Value(i, p[0], p[1], 0.0)
            ps = SoPointSet()
            ps.vertexProperty = vp
            ps.numPoints = len(items)
            psep.addChild(ps)
            sep.addChild(psep)
        self._gizmo_nodes = sep
        return sep

    # -- runtime ---------------------------------------------------------
    def sync_widgets(self):
        """Push the state back into the widgets (after a preset change)."""
        if not self._built or self.menu is None:
            return
        pairs = (("radius_slider", self.state.radius_normalised()),
                 ("hardness_slider", self.state.brush.hardness),
                 ("flow_slider", self.state.brush.flow),
                 ("opacity_slider", self.state.brush.opacity),
                 ("hue_slider", self.state.hue),
                 ("sat_slider", self.state.saturation),
                 ("value_slider", self.state.value),
                 ("alpha_slider", self.state.alpha))
        for name, value in pairs:
            w = self.widgets.get(name)
            if w is not None and hasattr(w, "set_value"):
                w.set_value(max(0.0, min(1.0, float(value))))
        if self.state.mode:
            self.menu.select_widget_by_name("mode_%s" % self.state.mode)
        self.menu.select_widget_by_name("tool_%s" % self.state.tool)
        self.menu.select_widget_by_name("blend_%s" % self.state.brush.blend)

    def handle_pick(self, tail, coords):
        """Route a Coin pick through the menu and into the state machine."""
        if self.menu is None:
            return None
        widget = self.menu.find_picked_widget(tail, coords)
        if widget is None:
            return None
        value = getattr(widget, "value", 0.0)
        return self.state.on_widget(widget.name, value)
