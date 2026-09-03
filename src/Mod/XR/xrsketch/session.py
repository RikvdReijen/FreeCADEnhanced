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
"""The sketch mode controller.

:class:`SketchSession` has the same two front doors as
:class:`xrpaint.session.PaintSession`.  :meth:`SketchSession.update` is the
per-frame one, fed with ``xrcore.controllerXR.xrController`` objects; it reads
their poses and button states and turns them into the plain Python events
below.  Those events — :meth:`on_trigger`, :meth:`on_move`, :meth:`on_grip`,
:meth:`on_thumbstick` — take nothing but numbers, so a whole modelling session
can be replayed by a unit test with no headset, no Coin and no FreeCAD.

Grip is always the grab: squeezing with one hand moves what you hold, adding
the second scales and rotates it (:mod:`xrsketch.bimanual`).  With a selection
the grab moves the selected objects; with nothing selected it moves the world,
handing the scale part to :class:`xrenv.scale.ScaleController` rather than
inventing a second notion of scale.  The trigger belongs to the active tool.
"""

import math

from . import bimanual as _bimanual
from . import curves as _curves
from . import primitives as _primitives
from . import reference as _reference
from . import scene as _scene
from . import snapping as _snapping
from . import vecmath as vm
from .vecmath import Transform

__all__ = [
    "SketchSession",
    "TOOLS",
    "TOOL_CURVE",
    "TOOL_MEASURE",
    "TOOL_PEN",
    "TOOL_PRIMITIVE",
    "TOOL_SELECT",
    "TOOL_SUBD",
]

TOOL_SELECT = "SELECT"
TOOL_CURVE = "CURVE"
TOOL_PEN = "PEN"
TOOL_PRIMITIVE = "PRIMITIVE"
TOOL_SUBD = "SUBD"
TOOL_MEASURE = "MEASURE"
TOOLS = (TOOL_SELECT, TOOL_CURVE, TOOL_PEN, TOOL_PRIMITIVE, TOOL_SUBD,
         TOOL_MEASURE)

#: analog thresholds, matching ``xrcore.controllerXR``
TRIGGER_ON = 0.7
TRIGGER_OFF = 0.3


class _HandState(object):
    __slots__ = ("pressed", "value", "grip", "gripping", "position",
                 "rotation")

    def __init__(self):
        self.pressed = False
        self.value = 0.0
        self.grip = 0.0
        self.gripping = False
        self.position = None
        self.rotation = vm.IDENTITY_QUAT


class SketchSession(object):
    """Curves, primitives, cages, measuring and two-handed grabbing."""

    def __init__(self, scene=None, tool=None, scale_controller=None):
        self.scene = scene or _scene.Scene()
        self.snap = _snapping.SnapEngine()
        self.scale_controller = scale_controller
        self.grab = _bimanual.BimanualController(
            _bimanual.GrabParams(damping=0.04))
        self.world_grab = None
        self.placement = _primitives.PlacementSession()
        self.measure = _reference.MeasureTool(scale_controller)
        self._tool = TOOL_SELECT
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.camera = None
        self.messages = []
        self.events = []
        self.changed = False
        self.fit_error = 0.002
        self.corner_angle = 60.0
        self.pick_radius = 0.05
        self._hands = {}
        self._stroke = []
        self._stroke_hand = None
        self._pen_curve = None
        self._grab_base = {}
        self._time = 0.0
        if tool is not None:
            self.set_tool(tool)

    # ------------------------------------------------------------------
    # tool
    # ------------------------------------------------------------------
    @property
    def tool(self):
        return self._tool

    @tool.setter
    def tool(self, value):
        self.set_tool(value)

    def set_tool(self, tool):
        """Switch the active tool, abandoning anything half-made."""
        self.cancel_all()
        name = str(tool).upper()
        if name not in TOOLS:
            raise ValueError("unknown sketch tool: %r" % (tool,))
        self._tool = name
        self._emit("tool", tool=name)
        return name

    def set_primitive_kind(self, kind):
        return self.placement.set_kind(kind)

    # ------------------------------------------------------------------
    # binding
    # ------------------------------------------------------------------
    def attach_scenegraph(self, root):
        """Attach to a Coin ``SoSeparator`` the session may add nodes to."""
        self.root = root
        if root is None:
            return None
        try:
            from pivy.coin import SoSeparator
            self._sketch_sep = SoSeparator()
            root.addChild(self._sketch_sep)
        except Exception as exc:
            self.messages.append("scenegraph not attached: %s" % exc)
        return root

    def bind_viewer(self, widget):
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

    def bind_scale(self, controller):
        """Adopt the environment's :class:`xrenv.scale.ScaleController`."""
        self.scale_controller = controller
        self.measure.scale = controller
        self.world_grab = (None if controller is None
                           else _bimanual.WorldGrab(controller))
        return controller

    def detach(self):
        self.cancel_all()
        root = self.root
        sep = getattr(self, "_sketch_sep", None)
        if root is not None and sep is not None:
            try:
                i = root.findChild(sep)
                if i >= 0:
                    root.removeChild(i)
            except Exception:
                pass
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.camera = None
        return None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def user_scale(self):
        ctl = self.scale_controller
        if ctl is None:
            return 1.0
        try:
            return float(ctl.scale)
        except (TypeError, ValueError):
            return 1.0

    def _emit(self, name, **kw):
        kw["event"] = name
        self.events.append(kw)
        return kw

    def drain_events(self):
        """Take the events recorded since the last call."""
        out = self.events
        self.events = []
        return out

    def _hand(self, hand):
        h = self._hands.get(hand)
        if h is None:
            h = _HandState()
            self._hands[hand] = h
        return h

    def snap_targets(self, exclude=None):
        t = _snapping.SnapTargets()
        for obj in self.scene.visible_objects():
            if obj is exclude:
                continue
            data = obj.data
            tr = obj.transform
            if obj.kind == "cage":
                verts = [tr.apply(v) for v in data.vertices]
                for v in verts:
                    t.add_vertex(v, obj)
                for face in data.faces:
                    t.add_face([verts[i] for i in face], obj)
                    for k in range(len(face)):
                        t.add_edge(verts[face[k]],
                                   verts[face[(k + 1) % len(face)]], obj)
            elif obj.kind == "curve":
                for cp in data.points:
                    t.add_vertex(tr.apply(cp.position), obj)
                if not data.closed and data.points:
                    t.add_curve_end(tr.apply(data.points[0].position),
                                    tr.apply_vector(data.tangent_at(0, 0.0)),
                                    obj)
                    t.add_curve_end(
                        tr.apply(data.points[-1].position),
                        tr.apply_vector(data.tangent_at(
                            max(0, data.segment_count() - 1), 1.0)), obj)
            elif obj.kind == "primitive":
                lo, hi = obj.world_bounds()
                t.add_vertex(vm.mul(vm.add(lo, hi), 0.5), obj)
        return t

    def snap_point(self, point, origin=None, exclude=None):
        """Snap a controller position with the current settings."""
        if point is None:
            return None
        result = self.snap.snap(point, self.snap_targets(exclude), origin,
                                self.user_scale, exclude)
        return result.point

    def pick(self, point, radius=None):
        """The nearest visible, unlocked object to ``point``."""
        if point is None:
            return None
        r = self.pick_radius if radius is None else float(radius)
        r = r / max(1e-9, self.user_scale)
        best = None
        for obj in self.scene.visible_objects():
            if self.scene.object_locked(obj):
                continue
            lo, hi = obj.world_bounds()
            centre = vm.mul(vm.add(lo, hi), 0.5)
            half = vm.mul(vm.sub(hi, lo), 0.5)
            d = 0.0
            for i in range(3):
                gap = abs(point[i] - centre[i]) - half[i]
                if gap > 0.0:
                    d += gap * gap
            d = math.sqrt(d)
            if d <= r and (best is None or d < best[1]):
                best = (obj, d)
        return None if best is None else best[0]

    # ------------------------------------------------------------------
    # controller events
    # ------------------------------------------------------------------
    def on_trigger(self, hand=0, value=0.0, position=None, rotation=None,
                   time=None):
        """Analog trigger; returns True when something started or ended."""
        h = self._hand(hand)
        h.value = float(value)
        if time is not None:
            self._time = float(time)
        if position is not None:
            h.position = vm.vec3(position)
        if rotation is not None:
            h.rotation = vm.quat_normalize(rotation)
        if not h.pressed and h.value >= TRIGGER_ON:
            h.pressed = True
            return bool(self._begin(hand))
        if h.pressed and h.value <= TRIGGER_OFF:
            h.pressed = False
            return bool(self._end(hand))
        return False

    def on_move(self, hand=0, position=None, rotation=None, time=None):
        """Controller motion."""
        h = self._hand(hand)
        if time is not None:
            self._time = float(time)
        if position is not None:
            h.position = vm.vec3(position)
        if rotation is not None:
            h.rotation = vm.quat_normalize(rotation)
        if h.gripping:
            self.grab.move(hand, h.position, h.rotation)
            if self.world_grab is not None and not self.scene.selection:
                self.world_grab.move(hand, h.position, h.rotation)
        if not h.pressed:
            return False
        return bool(self._continue(hand))

    def on_grip(self, hand=0, value=0.0, position=None, rotation=None):
        """Grip: the grab.  Returns True when the grab state changed."""
        h = self._hand(hand)
        if position is not None:
            h.position = vm.vec3(position)
        if rotation is not None:
            h.rotation = vm.quat_normalize(rotation)
        was = h.gripping
        h.grip = float(value)
        if not was and h.grip >= TRIGGER_ON:
            h.gripping = True
            self._grab_begin(hand)
            return True
        if was and h.grip <= TRIGGER_OFF:
            h.gripping = False
            self._grab_end(hand)
            return True
        return False

    def on_thumbstick(self, hand=0, x=0.0, y=0.0, dt=1.0 / 60.0):
        """Thumbstick: X cycles the primitive kind, Y nudges the snap grid."""
        acted = False
        if abs(x) > 0.8:
            kinds = _primitives.PRIMITIVE_KINDS
            i = kinds.index(self.placement.kind)
            self.placement.kind = kinds[(i + (1 if x > 0 else -1))
                                        % len(kinds)]
            self._emit("primitive_kind", kind=self.placement.kind)
            acted = True
        if abs(y) > 0.2:
            g = self.snap.settings.grid_size * (1.0 + y * dt)
            self.snap.settings.grid_size = max(1e-4, min(1.0, g))
            acted = True
        if acted:
            self.changed = True
        return acted

    # ------------------------------------------------------------------
    # grabbing
    # ------------------------------------------------------------------
    def _grab_begin(self, hand):
        h = self._hand(hand)
        if h.position is None:
            return False
        if self.scene.selection:
            if not self.grab.active:
                self.grab.set_transform(Transform())
                self._grab_base = dict(
                    (o.id, o.transform.copy())
                    for o in self.scene.selected_objects())
                self.history_begin("grab")
            self.grab.grab(hand, h.position, h.rotation)
        elif self.world_grab is not None:
            self.world_grab.grab(hand, h.position, h.rotation)
        self._emit("grab_begin", hand=hand, world=not self.scene.selection)
        return True

    def _grab_end(self, hand):
        if self.grab.active:
            self.grab.release(hand)
            if not self.grab.active:
                self._grab_base = {}
                self.history_commit()
        if self.world_grab is not None and self.world_grab.active:
            self.world_grab.release(hand)
        self._emit("grab_end", hand=hand)
        return True

    def _grab_update(self, dt):
        if self.grab.active:
            t = self.grab.update(dt)
            for oid, base in self._grab_base.items():
                obj = self.scene.object(oid)
                if obj is not None:
                    obj.transform = vm.compose(t, base)
            self.changed = True
            return True
        if self.world_grab is not None and self.world_grab.active:
            self.world_grab.update(dt)
            self.changed = True
            return True
        return False

    # ------------------------------------------------------------------
    # tool dispatch
    # ------------------------------------------------------------------
    def _begin(self, hand):
        h = self._hand(hand)
        if h.position is None:
            return False
        tool = self._tool
        if tool == TOOL_SELECT:
            obj = self.pick(h.position)
            if obj is None:
                self.scene.deselect_all()
                self._emit("deselect")
                self.changed = True
                return True
            self.scene.select(obj, additive=self._other_pressed(hand))
            self._emit("select", object=obj.id)
            self.changed = True
            return True
        if tool == TOOL_CURVE:
            self._stroke = [self.snap_point(h.position)]
            self._stroke_hand = hand
            return True
        if tool == TOOL_PEN:
            return self._pen_click(h.position)
        if tool == TOOL_PRIMITIVE:
            if self.placement.active:
                # the second hand joins an placement in flight and sets the
                # extent rather than starting a new one
                self.placement.update(self.snap_point(h.position))
                return True
            self.placement.begin(self.snap_point(h.position))
            return True
        if tool == TOOL_MEASURE:
            self.measure.add(h.position)
            self._emit("measure_point", text=self.measure.readout())
            self.changed = True
            return True
        if tool == TOOL_SUBD:
            return self._subd_click(h.position)
        return False

    def _continue(self, hand):
        h = self._hand(hand)
        if h.position is None:
            return False
        if self._tool == TOOL_CURVE and self._stroke_hand == hand:
            if not self._stroke or \
                    vm.dist(self._stroke[-1], h.position) > 1e-5:
                self._stroke.append(vm.vec3(h.position))
                self.changed = True
                return True
            return False
        if self._tool == TOOL_PRIMITIVE and self.placement.active:
            other = self._other_hand(hand)
            point = h.position
            if other is not None and other.pressed and other.position:
                point = other.position
            if self.placement.update(self.snap_point(point)) is not None:
                self.changed = True
                return True
        return False

    def _end(self, hand):
        tool = self._tool
        if tool == TOOL_CURVE and self._stroke_hand == hand:
            pts = self._stroke
            self._stroke = []
            self._stroke_hand = None
            if len(pts) < 2:
                return False
            pts[-1] = self.snap_point(pts[-1])
            curve = _curves.Curve3D.from_freehand(
                pts, error=self.fit_error / max(1e-9, self.user_scale),
                corner_angle=self.corner_angle)
            with self.scene.edit("draw curve"):
                obj = self.scene.add_curve(curve)
            self._emit("curve", object=obj.id,
                       points=len(curve.points))
            self.changed = True
            return True
        if tool == TOOL_PRIMITIVE and self.placement.active:
            prim = self.placement.commit()
            if prim is None:
                return False
            with self.scene.edit("place %s" % prim.kind):
                obj = self.scene.add_primitive(prim)
            self._emit("primitive", object=obj.id, kind=prim.kind)
            self.changed = True
            return True
        return False

    def _other_hand(self, hand):
        for other, state in self._hands.items():
            if other != hand:
                return state
        return None

    def _other_pressed(self, hand):
        other = self._other_hand(hand)
        return bool(other is not None and other.gripping)

    def _pen_click(self, position, close_factor=1.5):
        p = self.snap_point(position, exclude=None)
        curve = self._pen_curve
        if curve is None:
            curve = _curves.Curve3D()
            self._pen_curve = curve
            curve.append_point(p)
            self._emit("pen_point", count=len(curve.points))
            self.changed = True
            return True
        radius = self.snap.settings.effective_radius(self.user_scale)
        if len(curve.points) > 2 and \
                vm.dist(p, curve.points[0].position) <= radius * close_factor:
            self.finish_pen(close=True)
            return True
        curve.append_point(p)
        self._emit("pen_point", count=len(curve.points))
        self.changed = True
        return True

    def finish_pen(self, close=False):
        """Commit the curve being placed point by point."""
        curve = self._pen_curve
        self._pen_curve = None
        if curve is None or len(curve.points) < 2:
            return None
        smooth = _curves.Curve3D.from_points(
            [cp.position for cp in curve.points], closed=close)
        with self.scene.edit("place curve"):
            obj = self.scene.add_curve(smooth)
        self._emit("curve", object=obj.id, points=len(smooth.points))
        self.changed = True
        return obj

    def _subd_click(self, position):
        """Pick and drag a cage vertex of the selected cage."""
        objs = [o for o in self.scene.selected_objects() if o.kind == "cage"]
        if not objs:
            obj = self.pick(position)
            if obj is None or obj.kind != "cage":
                return False
            self.scene.select(obj)
            objs = [obj]
        obj = objs[0]
        inv = obj.transform.inverse()
        local = inv.apply(position)
        cage = obj.data
        if not cage.vertices:
            return False
        index = min(range(len(cage.vertices)),
                    key=lambda i: vm.dist(cage.vertices[i], local))
        self._emit("cage_vertex", object=obj.id, vertex=index)
        self.changed = True
        return True

    def cancel_all(self):
        """Abandon anything in flight without committing it."""
        self._stroke = []
        self._stroke_hand = None
        self._pen_curve = None
        self.placement.cancel()
        self.measure.cancel()
        if self.grab.active:
            self.grab.release_all()
        self._grab_base = {}
        for h in self._hands.values():
            h.pressed = False
            h.gripping = False
        return True

    # ------------------------------------------------------------------
    # undo
    # ------------------------------------------------------------------
    def history_begin(self, label):
        try:
            self.scene.history.begin(label)
            return True
        except RuntimeError:
            return False

    def history_commit(self):
        return self.scene.history.commit()

    def undo(self):
        label = self.scene.history.undo()
        if label is not None:
            self.changed = True
            self._emit("undo", label=label)
        return label

    def redo(self):
        label = self.scene.history.redo()
        if label is not None:
            self.changed = True
            self._emit("redo", label=label)
        return label

    # ------------------------------------------------------------------
    # per frame
    # ------------------------------------------------------------------
    def update(self, dt, controllers):
        """Poll the controllers and run one frame; True when anything moved."""
        self.changed = False
        self._time += float(dt or 0.0)
        for hand, ctl in enumerate(controllers or []):
            if ctl is None:
                continue
            state = self._button_state(ctl)
            if state is None:
                continue
            trigger, grip, lx, ly = state
            position, rotation = self._pose(ctl)
            self.on_grip(hand, grip, position, rotation)
            self.on_trigger(hand, trigger, position=position,
                            rotation=rotation, time=self._time)
            self.on_move(hand, position=position, rotation=rotation,
                         time=self._time)
            self.on_thumbstick(hand, lx, ly, dt or 1.0 / 60.0)
        self._grab_update(dt or 0.0)
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

    def _pose(self, controller):
        try:
            tr = controller.get_global_transf()
            pos = tr.translation.getValue()
            position = (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            return (None, None)
        rotation = vm.IDENTITY_QUAT
        try:
            rot = tr.rotation.getValue()
            value = rot.getValue() if hasattr(rot, "getValue") else rot
            if value is not None and len(value) >= 4:
                rotation = vm.quat_normalize(
                    (float(value[0]), float(value[1]), float(value[2]),
                     float(value[3])))
        except Exception:
            rotation = vm.IDENTITY_QUAT
        return (position, rotation)

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    def to_dict(self):
        return {"tool": self._tool, "scene": self.scene.to_dict(),
                "snap": self.snap.settings.to_dict()}

    def commit_to_document(self, document=None, **kw):
        """Turn the sketch scene into FreeCAD geometry."""
        from . import to_freecad as _tofc
        try:
            return _tofc.commit(self.scene, document, **kw)
        except RuntimeError as exc:
            self.messages.append(str(exc))
            return None

    def __repr__(self):
        return "SketchSession(tool=%r, %d objects)" % (self._tool,
                                                       len(self.scene.objects))
