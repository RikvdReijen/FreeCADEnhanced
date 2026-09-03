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
"""The paint mode controller: wires brushes, layers, strokes and the vector
editor to the VR controllers.

:class:`PaintSession` has two front doors.  :meth:`PaintSession.update` is the
per-frame one, fed with ``xrcore.controllerXR.xrController`` objects; it reads
their button states and picked points and turns them into the plain Python
events below.  Those events -- :meth:`on_trigger`, :meth:`on_move`,
:meth:`on_grip`, :meth:`on_thumbstick` -- are the second front door, and they
take nothing but numbers, so the whole session can be driven by unit tests and
re-implemented verbatim in the Quest app.
"""

import math

from . import brush as _brush
from . import prefs as _prefs
from . import raster
from . import stroke3d as _stroke3d
from . import texture_paint as _tp
from . import ui as _ui
from . import vector as _vector

__all__ = [
    "MODES",
    "MODE_STROKE3D",
    "MODE_TEXTURE",
    "MODE_VECTOR",
    "PaintSession",
]

MODE_TEXTURE = "TEXTURE"
MODE_STROKE3D = "STROKE3D"
MODE_VECTOR = "VECTOR"
MODES = (MODE_TEXTURE, MODE_STROKE3D, MODE_VECTOR)

#: analog thresholds, matching ``xrcore.controllerXR``
TRIGGER_ON = 0.7
TRIGGER_OFF = 0.3


class _HandState(object):
    __slots__ = ("pressed", "value", "grip", "last_point", "drag_target",
                 "drag_path")

    def __init__(self):
        self.pressed = False
        self.value = 0.0
        self.grip = 0.0
        self.last_point = None
        self.drag_target = None
        self.drag_path = None


class PaintSession(object):
    """Texture painting, air strokes and the vector editor in one session."""

    def __init__(self, ui_state=None, vector_document=None, mode=None):
        self.ui = ui_state or _ui.PaintUiState()
        self._mode = None
        self.targets = {}
        self.target_order = []
        self.active_target_name = None
        self.painter = _tp.TexturePainter()
        self.strokes = _stroke3d.StrokeSet()
        self._vector_document = vector_document
        self.snap = _vector.SnapEngine()
        self.gizmo = _ui.NodeGizmoModel()
        self.layer_panel = _ui.LayerPanelModel()
        self.palette = []
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.near_plane = 0.01
        self.far_plane = 100.0
        self.camera = None
        self.messages = []
        self.changed = False
        self._hands = {}
        self._stroke = None
        self._freehand = []
        self._pen_path = None
        self._stroke_sep = None
        self._vector_sep = None
        self._time = 0.0
        self.vector_fit_error = 1.0
        self.vector_corner_angle = 60.0
        self.stroke3d_width = 0.01
        if mode is not None:
            self.set_mode(mode)

    # ------------------------------------------------------------------
    # mode
    # ------------------------------------------------------------------
    @property
    def mode(self):
        """``"TEXTURE"``, ``"STROKE3D"``, ``"VECTOR"`` or ``None``."""
        return self._mode

    @mode.setter
    def mode(self, value):
        self.set_mode(value)

    def set_mode(self, mode):
        """Switch mode; ``None`` disables painting entirely."""
        self.cancel_all()
        if mode is None:
            self._mode = None
            self.ui.set_mode(None)
            return None
        m = str(mode).upper()
        if m not in MODES:
            raise ValueError("unknown paint mode: %r" % (mode,))
        self._mode = m
        self.ui.set_mode(m)
        return m

    # ------------------------------------------------------------------
    # scenegraph / viewer binding
    # ------------------------------------------------------------------
    def attach_scenegraph(self, root):
        """Attach to a Coin ``SoSeparator`` the session may add nodes to."""
        self.root = root
        if root is None:
            return None
        try:
            from pivy.coin import SoSeparator
            if self._stroke_sep is None:
                self._stroke_sep = SoSeparator()
            if self._vector_sep is None:
                self._vector_sep = SoSeparator()
            root.addChild(self._stroke_sep)
            root.addChild(self._vector_sep)
        except Exception as exc:
            self.messages.append("scenegraph not attached: %s" % exc)
        return root

    def bind_viewer(self, widget):
        """Bind the XR widget; ``None`` clears the binding."""
        self.viewer = widget
        if widget is None:
            return None
        for attr in ("vp_reg", "vpReg", "viewport_region", "viewportRegion"):
            vp = getattr(widget, attr, None)
            if vp is not None:
                self.viewport_region = vp
                break
        for attr in ("camera", "cam", "xr_camera"):
            cam = getattr(widget, attr, None)
            if cam is not None:
                self.camera = cam
                break
        return widget

    def detach(self):
        """Cancel in-flight edits and unhook from the scenegraph/viewer."""
        self.cancel_all()
        root = self.root
        if root is not None:
            for node in (self._stroke_sep, self._vector_sep):
                if node is None:
                    continue
                try:
                    i = root.findChild(node)
                    if i >= 0:
                        root.removeChild(i)
                except Exception:
                    pass
            for t in self.targets.values():
                if t.bridge is not None:
                    try:
                        t.bridge.detach(root)
                    except Exception:
                        pass
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.camera = None
        return None

    # ------------------------------------------------------------------
    # targets and layers
    # ------------------------------------------------------------------
    def add_target(self, fc_name, width=None, height=None, uvset=None):
        """Create (or return) the paint target for a FreeCAD object."""
        if fc_name in self.targets:
            return self.targets[fc_name]
        t = _tp.PaintTarget(fc_name, width, height, uvset)
        self.targets[fc_name] = t
        self.target_order.append(fc_name)
        if self.active_target_name is None:
            self.set_active_target(fc_name)
        return t

    def remove_target(self, fc_name):
        t = self.targets.pop(fc_name, None)
        if fc_name in self.target_order:
            self.target_order.remove(fc_name)
        if self.active_target_name == fc_name:
            self.active_target_name = (self.target_order[-1]
                                       if self.target_order else None)
            self.painter.target = self.active_target()
        return t

    def active_target(self):
        if self.active_target_name is None:
            return None
        return self.targets.get(self.active_target_name)

    def set_active_target(self, fc_name):
        if fc_name is not None and fc_name not in self.targets:
            return None
        self.active_target_name = fc_name
        t = self.active_target()
        self.painter.target = t
        return t

    def active_layer_stack(self):
        """The active target's :class:`~xrpaint.layers.LayerStack`."""
        t = self.active_target()
        return t.stack if t is not None else None

    def invalidate_composite(self):
        """Force every target to recomposite and re-upload."""
        for t in self.targets.values():
            t.invalidate()
        self.changed = True

    def upload_dirty(self):
        """Push pending pixels into Coin; returns the number of uploads."""
        n = 0
        for t in self.targets.values():
            bridge = t.bridge
            if bridge is None or bridge.dirty is None:
                continue
            try:
                if t.upload(bridge.dirty) is not None:
                    n += 1
            except Exception as exc:
                self.messages.append("texture upload failed: %s" % exc)
        return n

    # ------------------------------------------------------------------
    # vector document
    # ------------------------------------------------------------------
    @property
    def vector_document(self):
        return self._vector_document

    @vector_document.setter
    def vector_document(self, doc):
        self._vector_document = doc
        self._pen_path = None
        self._freehand = []

    def ensure_vector_document(self):
        if self._vector_document is None:
            self._vector_document = _vector.VectorDocument()
        return self._vector_document

    # ------------------------------------------------------------------
    # tools
    # ------------------------------------------------------------------
    def set_tool(self, name):
        ok = self.ui.select_tool(name)
        if ok:
            self.painter.params = self.ui.brush
        return ok

    def set_color(self, rgba):
        if len(rgba) >= 3 and max(rgba[:3]) > 1.0:
            self.ui.set_color_rgb(rgba[0] / 255.0, rgba[1] / 255.0,
                                  rgba[2] / 255.0,
                                  (rgba[3] / 255.0) if len(rgba) > 3 else 1.0)
        else:
            self.ui.set_color_rgb(rgba[0], rgba[1], rgba[2],
                                  rgba[3] if len(rgba) > 3 else 1.0)
        return self.ui.color_rgba255

    def set_blend(self, mode):
        return self.ui.set_blend(mode)

    def set_radius(self, value, normalised=False):
        return self.ui.set_radius(value, normalised)

    def undo(self):
        t = self.active_target()
        if t is None:
            return None
        e = t.history.undo()
        if e is not None:
            t.invalidate()
            self.changed = True
        return e

    def redo(self):
        t = self.active_target()
        if t is None:
            return None
        e = t.history.redo()
        if e is not None:
            t.invalidate()
            self.changed = True
        return e

    def on_ui_widget(self, name, value=0.0):
        """Feed a menu pick into the UI state and act on the result."""
        action = self.ui.on_widget(name, value)
        return self.apply_action(action)

    def apply_action(self, action):
        """Execute a :class:`~xrpaint.ui.UiAction`."""
        if action is None:
            return None
        name = action.name
        if name == "mode":
            self.set_mode(action.value)
        elif name in ("tool", "vector_tool"):
            self.painter.params = self.ui.brush
        elif name in ("radius", "hardness", "flow", "opacity", "spacing",
                      "blend"):
            self.painter.params = self.ui.brush
        elif name == "color":
            self.painter.color = self.ui.color_rgba255
        elif name == "undo":
            self.undo()
        elif name == "redo":
            self.redo()
        elif name == "clear_layer":
            st = self.active_layer_stack()
            t = self.active_target()
            if st is not None and st.active is not None:
                t.history.begin("clear layer")
                t.history.snapshot(st.active, (0, 0, st.width, st.height))
                st.active.image.clear()
                t.history.commit()
                t.invalidate()
                self.changed = True
        elif name.startswith("layer"):
            self.layer_panel.stack = self.active_layer_stack()
            if self.layer_panel.apply(action):
                t = self.active_target()
                if t is not None:
                    t.invalidate()
                self.changed = True
        elif name == "commit_vector":
            return self.commit_vector()
        elif name == "export_svg":
            return self.export_svg()
        return action

    # ------------------------------------------------------------------
    # controller events (the plain Python API)
    # ------------------------------------------------------------------
    def _hand(self, hand):
        h = self._hands.get(hand)
        if h is None:
            h = _HandState()
            self._hands[hand] = h
        return h

    def on_trigger(self, hand=0, value=0.0, hit=None, position=None,
                   normal=None, time=None):
        """Analog trigger.  Returns True when a stroke started or ended."""
        h = self._hand(hand)
        h.value = float(value)
        if time is not None:
            self._time = float(time)
        if not h.pressed and h.value >= TRIGGER_ON:
            h.pressed = True
            return bool(self._begin(hand, hit, position, normal, h.value))
        if h.pressed and h.value <= TRIGGER_OFF:
            h.pressed = False
            return bool(self._end(hand, hit, position, normal))
        return False

    def on_move(self, hand=0, hit=None, position=None, normal=None,
                pressure=None, time=None):
        """Controller motion.  Only does anything while the trigger is held."""
        h = self._hand(hand)
        if time is not None:
            self._time = float(time)
        if not h.pressed:
            h.last_point = position
            return False
        p = h.value if pressure is None else float(pressure)
        return bool(self._continue(hand, hit, position, normal, p))

    def on_grip(self, hand=0, value=0.0):
        """Grip: a squeeze above the threshold cancels the current edit."""
        h = self._hand(hand)
        was = h.grip
        h.grip = float(value)
        if was < TRIGGER_ON <= h.grip:
            if h.pressed:
                h.pressed = False
                self.cancel_all()
                return True
        return False

    def on_thumbstick(self, hand=0, x=0.0, y=0.0, dt=1.0 / 60.0):
        """Thumbstick: X scrubs the brush size, Y the opacity."""
        acted = False
        if abs(x) > 0.2:
            r = self.ui.radius_normalised() + x * dt * 0.8
            self.ui.set_radius(max(0.0, min(1.0, r)), normalised=True)
            self.painter.params = self.ui.brush
            acted = True
        if abs(y) > 0.2:
            o = self.ui.brush.opacity + y * dt * 0.8
            self.ui.brush.opacity = max(0.0, min(1.0, o))
            acted = True
        if acted:
            self.changed = True
        return acted

    # -- dispatch --------------------------------------------------------
    def _begin(self, hand, hit, position, normal, pressure):
        if self._mode == MODE_TEXTURE:
            return self._texture_begin(hit)
        if self._mode == MODE_STROKE3D:
            return self._stroke_begin(position, normal, pressure)
        if self._mode == MODE_VECTOR:
            return self._vector_begin(hand, position, normal)
        return False

    def _continue(self, hand, hit, position, normal, pressure):
        if self._mode == MODE_TEXTURE:
            return self._texture_move(hit)
        if self._mode == MODE_STROKE3D:
            return self._stroke_move(position, normal, pressure)
        if self._mode == MODE_VECTOR:
            return self._vector_move(hand, position)
        return False

    def _end(self, hand, hit, position, normal):
        if self._mode == MODE_TEXTURE:
            return self._texture_end()
        if self._mode == MODE_STROKE3D:
            return self._stroke_end()
        if self._mode == MODE_VECTOR:
            return self._vector_end(hand, position)
        return False

    def cancel_all(self):
        """Abort any stroke in flight without committing it."""
        if self.painter.active:
            self.painter.cancel()
        self._stroke = None
        self._freehand = []
        for h in self._hands.values():
            h.pressed = False
            h.drag_target = None
            h.drag_path = None
        return True

    # -- texture ---------------------------------------------------------
    def _texture_begin(self, hit):
        if hit is None:
            return False
        target = self.active_target()
        if target is None and hit.object_name:
            target = self.add_target(hit.object_name)
        elif (target is not None and hit.object_name
                and hit.object_name != target.fc_name):
            target = self.add_target(hit.object_name)
            self.set_active_target(hit.object_name)
        if target is None:
            return False
        self.painter.target = target
        self.painter.params = self.ui.brush
        self.painter.color = self.ui.color_rgba255
        if not self.ui.pressure_enabled:
            hit = _clone_hit(hit, pressure=1.0)
        ok = self.painter.begin(hit)
        self.changed = self.changed or ok
        return ok

    def _texture_move(self, hit):
        if hit is None or not self.painter.active:
            return False
        if not self.ui.pressure_enabled:
            hit = _clone_hit(hit, pressure=1.0)
        r = self.painter.move(hit)
        if r is not None:
            self.changed = True
        return r is not None

    def _texture_end(self):
        if not self.painter.active:
            return False
        self.painter.end()
        self.ui.push_swatch()
        self.changed = True
        return True

    # -- 3d strokes ------------------------------------------------------
    def _stroke_profile(self):
        t = self.ui.tool
        if t in ("marker", "square", "chisel"):
            return "hull"
        if t in ("airbrush", "spray"):
            return "taper"
        if t == "round":
            return "tube"
        return "ribbon"

    def _stroke_begin(self, position, normal, pressure):
        if position is None:
            return False
        self._stroke = _stroke3d.Stroke3D(
            self._stroke_profile(), self.ui.color_rgba, self.stroke3d_width)
        p = pressure if self.ui.pressure_enabled else 1.0
        self._stroke.add_point(position, normal, p, self._time, force=True)
        self.changed = True
        return True

    def _stroke_move(self, position, normal, pressure):
        if self._stroke is None or position is None:
            return False
        p = pressure if self.ui.pressure_enabled else 1.0
        added = self._stroke.add_point(position, normal, p, self._time)
        if added is not None:
            self.changed = True
        return added is not None

    def _stroke_end(self):
        s = self._stroke
        self._stroke = None
        if s is None:
            return False
        if len(s) < 2:
            return False
        s.decimate()
        self.strokes.add(s)
        self.ui.push_swatch()
        if self._stroke_sep is not None:
            try:
                self._stroke_sep.addChild(s.to_coin())
            except Exception as exc:
                self.messages.append("stroke not added to the scene: %s"
                                     % exc)
        self.changed = True
        return True

    # -- vector ----------------------------------------------------------
    def _to_plane(self, position):
        doc = self.ensure_vector_document()
        if position is None:
            return None
        if len(position) == 2:
            return (float(position[0]), float(position[1]))
        return doc.plane.to_plane(position)

    def _snap(self, p, origin=None, exclude=None):
        self.snap.settings.enabled = self.ui.snap_enabled
        return self.snap.snap(p, self.ensure_vector_document(), origin,
                              exclude).point

    def _vector_begin(self, hand, position, normal):
        p = self._to_plane(position)
        if p is None:
            return False
        doc = self.ensure_vector_document()
        tool = self.ui.vector_tool
        h = self._hand(hand)
        if tool == "draw":
            self._freehand = [p]
            return True
        if tool == "select":
            best = None
            for path in doc.paths:
                if not path.segment_count():
                    continue
                i, t, pt, d = path.closest_point(p)
                if best is None or d < best[1]:
                    best = (path, d)
            if best is not None and best[1] <= self.snap.settings.radius:
                self.ui.selected_path = best[0].id
                self.changed = True
                return True
            self.ui.selected_path = None
            return False
        if tool == "node":
            path = doc.path_by_id(self.ui.selected_path)
            if path is None:
                return False
            target = self.gizmo.pick(path, p, self.snap.settings.radius)
            h.drag_target = target
            h.drag_path = path
            if target is None:
                return False
            if target[0] == "node":
                self.ui.selected_node = target[1]
                self.ui.selected_handle = None
            elif target[0] == "handle":
                self.ui.selected_node = target[1]
                self.ui.selected_handle = target[2]
            elif target[0] == "segment":
                idx = path.split_segment(target[1], target[2])
                self.ui.selected_node = idx
                h.drag_target = ("node", idx)
                self.changed = True
            return True
        if tool == "pen":
            path = self._pen_path
            sp = self._snap(p, origin=(path.nodes[-1].point
                                       if path and path.nodes else None),
                            exclude=path)
            if path is None:
                path = _vector.Path(stroke=self._stroke_style(),
                                    fill=self._fill_style())
                doc.add_path(path)
                self._pen_path = path
                self.ui.selected_path = path.id
            elif len(path.nodes) > 2 and \
                    _dist2(sp, path.nodes[0].point) <= \
                    self.snap.settings.radius:
                path.close()
                self._pen_path = None
                self.changed = True
                return True
            path.append_node(_vector.Node(sp))
            h.drag_target = ("node", len(path.nodes) - 1)
            h.drag_path = path
            self.changed = True
            return True
        return False

    def _vector_move(self, hand, position):
        p = self._to_plane(position)
        if p is None:
            return False
        tool = self.ui.vector_tool
        h = self._hand(hand)
        if tool == "draw":
            if not self._freehand:
                return False
            if _dist2(self._freehand[-1], p) > 1e-9:
                self._freehand.append(p)
                self.changed = True
                return True
            return False
        if tool == "node" and h.drag_target is not None:
            sp = self._snap(p, exclude=h.drag_path)
            if self.gizmo.drag(h.drag_path, h.drag_target, sp):
                self.changed = True
                return True
            return False
        if tool == "pen" and h.drag_target is not None and h.drag_path:
            node = h.drag_path.nodes[h.drag_target[1]]
            node.type = "symmetric"
            node.set_out_point(p)
            self.changed = True
            return True
        return False

    def _vector_end(self, hand, position):
        tool = self.ui.vector_tool
        h = self._hand(hand)
        h.drag_target = None
        h.drag_path = None
        if tool != "draw":
            return False
        pts = self._freehand
        self._freehand = []
        if len(pts) < 2:
            return False
        doc = self.ensure_vector_document()
        closed = (len(pts) > 8
                  and _dist2(pts[0], pts[-1]) <= self.snap.settings.radius)
        path = doc.add_stroke(pts, error=self.vector_fit_error,
                              corner_angle=self.vector_corner_angle,
                              closed=closed, stroke=self._stroke_style(),
                              fill=self._fill_style() if closed else None)
        if path is None:
            return False
        self.ui.selected_path = path.id
        self.changed = True
        return True

    def _stroke_style(self):
        r, g, b, a = self.ui.color_rgba
        return {"color": [r, g, b, a],
                "width": max(0.05, self.ui.brush.radius * 0.05)}

    def _fill_style(self):
        return None

    # ------------------------------------------------------------------
    # per frame update
    # ------------------------------------------------------------------
    def update(self, dt, controllers):
        """Poll the controllers and run one frame of interaction.

        Returns ``True`` when anything changed and the caller should redraw.
        """
        self.changed = False
        self._time += float(dt or 0.0)
        if self._mode is None:
            return False
        for hand, ctl in enumerate(controllers or []):
            if ctl is None:
                continue
            state = self._button_state(ctl)
            if state is None:
                continue
            value, grip, lx, ly = state
            hit = None
            if self._mode == MODE_TEXTURE:
                hit = self._pick(ctl, value)
            position, normal = self._pose(ctl)
            self.on_grip(hand, grip)
            self.on_trigger(hand, value, hit=hit, position=position,
                            normal=normal, time=self._time)
            if self._hand(hand).pressed:
                self.on_move(hand, hit=hit, position=position, normal=normal,
                             pressure=value, time=self._time)
            self.on_thumbstick(hand, lx, ly, dt or 1.0 / 60.0)
        if self.changed:
            self.upload_dirty()
        return self.changed

    def _button_state(self, controller):
        try:
            st = controller.get_buttons_states()
        except Exception:
            return None
        trigger = getattr(st, "trigger", None)
        grab = float(getattr(st, "grab", 0.0))
        value = grab if trigger is None else float(trigger)
        grip = grab if trigger is not None else 0.0
        return (value, grip, float(getattr(st, "lever_x", 0.0)),
                float(getattr(st, "lever_y", 0.0)))

    def _pick(self, controller, pressure):
        if self.root is None or self.viewport_region is None:
            return None
        try:
            return _tp.hit_from_controller(
                controller, self.root, self.viewport_region, self.near_plane,
                self.far_plane, self.camera, pressure, self._time)
        except Exception as exc:
            self.messages.append("pick failed: %s" % exc)
            return None

    def _pose(self, controller):
        try:
            tr = controller.get_global_transf()
            pos = tr.translation.getValue()
            position = (pos[0], pos[1], pos[2])
        except Exception:
            return (None, None)
        try:
            axis = controller.find_ray_axis()
            av = axis.getValue() if hasattr(axis, "getValue") else axis
            normal = (-av[0], -av[1], -av[2])
        except Exception:
            normal = None
        return (position, normal)

    # ------------------------------------------------------------------
    # export / import hooks (ARCHITECTURE.md §4)
    # ------------------------------------------------------------------
    def export_paint_manifest(self):
        """The §4 ``paint`` dictionary, or ``None`` when nothing was painted.

        Layer ``image`` indices refer to the list returned by
        :meth:`export_paint_images`, in the same order.
        """
        if not self.targets and not len(self.strokes):
            return None
        targets = []
        index = 0
        for name in self.target_order:
            t = self.targets.get(name)
            if t is None:
                continue
            targets.append(t.to_dict(index))
            index += len(t.stack)
        palette = [list(c) for c in (self.palette or self.ui.swatches)]
        return {
            "version": 1,
            "targets": targets,
            "strokes3d": self.strokes.to_list(),
            "palette": palette,
        }

    def export_paint_images(self):
        """PNG blobs for every layer, in manifest ``images`` order."""
        out = []
        for name in self.target_order:
            t = self.targets.get(name)
            if t is None:
                continue
            for layer in t.stack.layers:
                out.append(raster.encode_png(layer.image))
        return out

    def import_paint_manifest(self, manifest, images=None):
        """Restore targets, layers and air strokes from a §4 ``paint`` dict."""
        if not manifest:
            return False
        decoded = []
        for img in (images or []):
            if isinstance(img, raster.Image):
                decoded.append(img)
            elif img is None:
                decoded.append(None)
            else:
                decoded.append(raster.decode_png(img))
        self.targets = {}
        self.target_order = []
        self.active_target_name = None
        for rec in manifest.get("targets", []):
            t = _tp.PaintTarget.from_dict(rec, decoded)
            self.targets[t.fc_name] = t
            self.target_order.append(t.fc_name)
        if self.target_order:
            self.set_active_target(self.target_order[0])
        self.strokes = _stroke3d.StrokeSet.from_list(
            manifest.get("strokes3d"))
        self.palette = [tuple(c) for c in manifest.get("palette", [])]
        self.invalidate_composite()
        return True

    def export_vector_manifest(self):
        """The §4 ``vector`` dictionary, or ``None``."""
        doc = self._vector_document
        if doc is None or not doc.paths:
            return None
        return doc.to_json()

    def import_vector_manifest(self, data):
        self._vector_document = _vector.VectorDocument.from_json(data)
        return self._vector_document

    def export_manifest(self):
        """Both §4 sections at once, ready to merge into an FCXR manifest."""
        out = {}
        paint = self.export_paint_manifest()
        if paint is not None:
            out["paint"] = paint
        vec = self.export_vector_manifest()
        if vec is not None:
            out["vector"] = vec
        return out

    def export_svg(self, **kw):
        """Serialise the vector document to SVG text."""
        from . import svg as _svg
        doc = self._vector_document
        if doc is None:
            return None
        return _svg.export_document(doc, **kw)

    def import_svg(self, text, **kw):
        from . import svg as _svg
        self._vector_document = _svg.import_document(text, **kw)
        return self._vector_document

    def commit_vector(self, document=None, **kw):
        """Turn the vector document into real FreeCAD geometry."""
        from . import to_freecad as _tofc
        doc = self._vector_document
        if doc is None or not doc.paths:
            return None
        try:
            return _tofc.commit(doc, document, **kw)
        except RuntimeError as exc:
            self.messages.append(str(exc))
            return None

    def __repr__(self):
        return "PaintSession(mode=%r, %d targets, %d strokes)" % (
            self._mode, len(self.targets), len(self.strokes))


def _clone_hit(hit, pressure=None):
    return _tp.PaintHit(hit.point, hit.normal, hit.uv, hit.ray_dir,
                        hit.object_name, hit.tail,
                        hit.pressure if pressure is None else pressure,
                        hit.time)


def _dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
