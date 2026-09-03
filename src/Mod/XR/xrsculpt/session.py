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
"""The sculpt mode controller.

:class:`SculptSession` has the same two front doors as
:class:`xrpaint.session.PaintSession`.  :meth:`SculptSession.update` is the
per-frame one, fed with ``xrcore.controllerXR.xrController`` objects; it reads
their button states and picked points and turns them into the plain Python
events below.  Those events -- :meth:`on_trigger`, :meth:`on_move`,
:meth:`on_grip`, :meth:`on_thumbstick` -- are the second front door, and they
take nothing but numbers, so the whole session runs under unit test and can be
re-implemented verbatim in the Quest app.

One stroke is one undo step and lands in exactly one layer, symmetry included.
"""

from . import brushes as _brushes
from . import io as _io
from . import layers as _layers
from . import masking as _masking
from . import mesh as _mesh
from . import prefs as _prefs
from . import symmetry as _symmetry
from . import topology as _topology

__all__ = [
    "MODES",
    "MODE_MASK",
    "MODE_SCULPT",
    "SculptSession",
    "SculptTarget",
]

MODE_SCULPT = "SCULPT"
MODE_MASK = "MASK"
MODES = (MODE_SCULPT, MODE_MASK)

#: analog thresholds, matching ``xrcore.controllerXR``
TRIGGER_ON = 0.7
TRIGGER_OFF = 0.3


# --------------------------------------------------------------------------
# one sculptable object
# --------------------------------------------------------------------------

class SculptTarget(object):
    """A mesh, its layer stack, its mask, its symmetry and its undo history.

    ``mesh`` always holds the *evaluated* positions -- base plus the whole
    stack -- so it is what a renderer draws and what a brush measures against.
    ``stack.base`` holds the unsculpted positions.
    """

    def __init__(self, fc_name, mesh=None, stack=None, mask=None,
                 symmetry=None, undo_steps=None):
        self.fc_name = str(fc_name)
        self.mesh = mesh
        if stack is not None:
            self.stack = stack
        elif mesh is not None:
            self.stack = _layers.LayerStack(base=list(mesh.positions))
        else:
            self.stack = _layers.LayerStack(n_vertices=0)
        self.mask = mask if mask is not None else _masking.VertexMask(
            mesh.n_vertices if mesh is not None else 0)
        self.symmetry = symmetry if symmetry is not None else \
            _symmetry.Symmetry()
        steps = undo_steps if undo_steps is not None else \
            _prefs.get_int("SculptUndoSteps")
        self.history = _layers.History(self.stack, max_entries=steps)
        self.dirty = True

    # -- evaluation ------------------------------------------------------
    def evaluate(self, indices=None):
        """Refresh the visible mesh from the stack."""
        if self.mesh is None:
            return None
        self.stack.evaluate(out=self.mesh.positions, indices=indices)
        self.mesh.touch(indices)
        self.dirty = True
        return self.mesh

    def reset(self):
        """Throw the sculpt away and go back to the base mesh."""
        self.stack.layers = []
        self.stack.active_index = -1
        self.history.clear()
        return self.evaluate()

    def ensure_layer(self, name=None):
        return self.stack.ensure_active(name)

    def to_payload(self):
        return _io.SculptPayload(self.stack, self.mask, self.symmetry,
                                 self.fc_name)

    @classmethod
    def from_payload(cls, payload, mesh=None, faces=None):
        """Rebuild a target from a deserialised payload.

        ``mesh`` supplies the triangles (the payload carries positions but not
        connectivity); ``faces`` is an alternative way to give them.
        """
        stack = payload.stack
        if mesh is None:
            tri = faces if faces is not None else []
            mesh = _mesh.SculptMesh(list(stack.base), tri,
                                    payload.fc_name or "Sculpt")
        target = cls(payload.fc_name or mesh.name, mesh, stack,
                     payload.mask, payload.symmetry)
        if target.mask is None or len(target.mask) != mesh.n_vertices:
            target.mask = _masking.VertexMask(mesh.n_vertices)
        target.evaluate()
        return target

    def __repr__(self):
        return "SculptTarget(%r, %s, %d layers)" % (
            self.fc_name, self.mesh, len(self.stack))


# --------------------------------------------------------------------------
# hand state
# --------------------------------------------------------------------------

class _HandState(object):
    __slots__ = ("pressed", "value", "grip", "last_point", "sampler",
                 "anchor", "layer")

    def __init__(self):
        self.pressed = False
        self.value = 0.0
        self.grip = 0.0
        self.last_point = None
        self.sampler = None
        self.anchor = None
        self.layer = None


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------

class SculptSession(object):
    """VR mesh sculpting with a layer stack."""

    def __init__(self, mode=None, brush=None):
        self._mode = None
        self.targets = {}
        self.target_order = []
        self.active_target_name = None
        self.brush = brush or _brushes.preset(
            _prefs.get_string("SculptBrush") or "draw")
        self.mask_radius_scale = 1.0
        self.mask_strength = 1.0
        self.pressure_enabled = _prefs.get_bool("SculptPressureEnabled")
        self.dynamic_detail = _prefs.get_bool("SculptDynamicDetail")
        self.target_edge = _prefs.get_float("SculptTargetEdge")
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.camera = None
        self.near_plane = 0.01
        self.far_plane = 100.0
        self.messages = []
        self.changed = False
        self._hands = {}
        self._time = 0.0
        self._stroke_count = 0
        if mode is not None:
            self.set_mode(mode)

    # ------------------------------------------------------------------
    # mode
    # ------------------------------------------------------------------
    @property
    def mode(self):
        """``"SCULPT"``, ``"MASK"`` or ``None``."""
        return self._mode

    @mode.setter
    def mode(self, value):
        self.set_mode(value)

    def set_mode(self, mode):
        """Switch mode; ``None`` disables sculpting entirely."""
        self.cancel_all()
        if mode is None:
            self._mode = None
            return None
        m = str(mode).upper()
        if m not in MODES:
            raise ValueError("unknown sculpt mode: %r" % (mode,))
        self._mode = m
        return m

    # ------------------------------------------------------------------
    # scenegraph / viewer binding
    # ------------------------------------------------------------------
    def attach_scenegraph(self, root):
        """Attach to a Coin ``SoSeparator`` the session may draw into."""
        self.root = root
        return root

    def bind_viewer(self, widget):
        """Bind the XR widget; ``None`` clears the binding."""
        self.viewer = widget
        if widget is None:
            self.viewport_region = None
            self.camera = None
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
        self.root = None
        self.viewer = None
        self.viewport_region = None
        self.camera = None
        return None

    # ------------------------------------------------------------------
    # targets
    # ------------------------------------------------------------------
    def add_target(self, fc_name, mesh=None, **kw):
        """Create (or return) the sculpt target for a FreeCAD object."""
        if fc_name in self.targets:
            return self.targets[fc_name]
        t = SculptTarget(fc_name, mesh, **kw)
        self.targets[fc_name] = t
        self.target_order.append(fc_name)
        if self.active_target_name is None:
            self.set_active_target(fc_name)
        return t

    def add_target_object(self, obj, deflection=None, fc_name=None):
        """Add a FreeCAD document object, tessellating a shape if needed."""
        name = fc_name or getattr(obj, "Name", None) or str(obj)
        if hasattr(obj, "Mesh"):
            m = _mesh.SculptMesh.from_mesh_object(obj, name)
        else:
            d = deflection if deflection is not None else \
                _prefs.get_float("SculptTessellation")
            m = _mesh.SculptMesh.from_shape(obj, d, name)
        return self.add_target(name, m)

    def remove_target(self, fc_name):
        t = self.targets.pop(fc_name, None)
        if fc_name in self.target_order:
            self.target_order.remove(fc_name)
        if self.active_target_name == fc_name:
            self.active_target_name = (self.target_order[-1]
                                       if self.target_order else None)
        return t

    def active_target(self):
        if self.active_target_name is None:
            return None
        return self.targets.get(self.active_target_name)

    def set_active_target(self, fc_name):
        if fc_name is not None and fc_name not in self.targets:
            return None
        self.active_target_name = fc_name
        return self.active_target()

    def active_stack(self):
        t = self.active_target()
        return t.stack if t is not None else None

    def active_mask(self):
        t = self.active_target()
        return t.mask if t is not None else None

    def active_symmetry(self):
        t = self.active_target()
        return t.symmetry if t is not None else None

    # ------------------------------------------------------------------
    # tools
    # ------------------------------------------------------------------
    def set_tool(self, name):
        """Select a brush preset by name."""
        try:
            self.brush = _brushes.preset(name)
        except KeyError:
            return False
        return True

    def set_brush(self, params):
        self.brush = params
        return params

    def set_radius(self, value):
        self.brush.radius = max(1e-9, float(value))
        return self.brush.radius

    def set_strength(self, value):
        self.brush.strength = float(value)
        return self.brush.strength

    def set_falloff(self, name):
        if name not in _brushes.FALLOFFS:
            return False
        self.brush.falloff = name
        return True

    def set_invert(self, on):
        self.brush.invert = bool(on)
        return self.brush.invert

    # ------------------------------------------------------------------
    # layer operations (each one is a single undo step)
    # ------------------------------------------------------------------
    def _target_or_fail(self):
        t = self.active_target()
        if t is None:
            raise RuntimeError("no active sculpt target")
        return t

    @property
    def active_layer(self):
        st = self.active_stack()
        return st.active if st is not None else None

    def set_active_layer(self, index):
        st = self.active_stack()
        return st.set_active(index) if st is not None else None

    def add_layer(self, name=None, index=None, **kw):
        t = self._target_or_fail()
        before = t.stack.active_index
        layer = t.stack.add_layer(name, index, **kw)
        t.history.record_structural("add layer", {
            "op": "add", "index": t.stack.index_of(layer),
            "layer_id": layer.id, "active_before": before})
        self.changed = True
        return layer

    def remove_layer(self, index=None):
        t = self._target_or_fail()
        index = t.stack.active_index if index is None else int(index)
        before = t.stack.active_index
        layer = t.stack.layers[index]
        saved = layer.copy(new_id=False)
        t.stack.remove_layer(index)
        t.history.record_structural("remove layer", {
            "op": "remove", "index": index, "layer_id": layer.id,
            "saved": saved, "active_before": before})
        t.evaluate()
        self.changed = True
        return layer

    def move_layer(self, src, dst):
        t = self._target_or_fail()
        before = t.stack.active_index
        dst = t.stack.move_layer(int(src), int(dst))
        t.history.record_structural("move layer", {
            "op": "move", "src": int(src), "dst": dst,
            "active_before": before})
        t.evaluate()
        self.changed = True
        return dst

    def rename_layer(self, index, name):
        return self._set_layer_property(index, "name", str(name))

    def set_layer_weight(self, index, value):
        return self._set_layer_property(index, "weight", float(value))

    def set_layer_visible(self, index, value):
        return self._set_layer_property(index, "visible", bool(value))

    def set_layer_locked(self, index, value):
        return self._set_layer_property(index, "locked", bool(value))

    def set_layer_blend(self, index, mode):
        if mode not in _layers.BLEND_MODES:
            raise ValueError("unknown layer blend mode: %r" % (mode,))
        return self._set_layer_property(index, "blend", mode)

    def _set_layer_property(self, index, field, value):
        t = self._target_or_fail()
        layer = t.stack.layers[int(index)]
        old = getattr(layer, field)
        if old == value:
            return layer
        setattr(layer, field, value)
        t.history.record_structural("layer %s" % field, {
            "op": "property", "layer_id": layer.id, "field": field,
            "old": old, "new": value,
            "active_before": t.stack.active_index})
        if field in ("weight", "visible", "blend"):
            t.evaluate()
        self.changed = True
        return layer

    def duplicate_layer(self, index=None, name=None):
        t = self._target_or_fail()
        index = t.stack.active_index if index is None else int(index)
        before = t.stack.active_index
        copy = t.stack.duplicate(index, name)
        t.history.record_structural("duplicate layer", {
            "op": "add", "index": t.stack.index_of(copy),
            "layer_id": copy.id, "active_before": before})
        t.evaluate()
        self.changed = True
        return copy

    def invert_layer(self, index=None):
        return self._replace_layers("invert layer", index, index,
                                    lambda ls: [ls[0].copy(new_id=False)
                                                .invert()])

    def clear_layer(self, index=None):
        return self._replace_layers("clear layer", index, index,
                                    lambda ls: [ls[0].copy(new_id=False)
                                                .clear()])

    def merge_layer_down(self, index=None):
        t = self._target_or_fail()
        index = t.stack.active_index if index is None else int(index)
        if index <= 0:
            return None
        return self._replace_layers("merge down", index - 1, index,
                                    _merge_pair)

    def bake_layers(self):
        """Fold the whole stack into the base mesh; one undo step."""
        t = self._target_or_fail()
        before_base = list(t.stack.base)
        snaps = t.history.snapshot_layers(range(len(t.stack.layers)))
        t.stack.bake_to_base()
        t.history.record_structural("bake layers", {
            "op": "replace", "start": 0, "before": snaps, "after": [],
            "active_before": -1}, base_before=before_base)
        t.evaluate()
        self.changed = True
        return t.stack

    def _replace_layers(self, label, start, end, fn):
        """Swap a contiguous run of layers for the result of ``fn``."""
        t = self._target_or_fail()
        start = t.stack.active_index if start is None else int(start)
        end = start if end is None else int(end)
        lo = min(start, end)
        hi = max(start, end)
        if not (0 <= lo <= hi < len(t.stack.layers)):
            return None
        before = t.history.snapshot_layers(range(lo, hi + 1))
        active_before = t.stack.active_index
        replacement = fn([t.stack.layers[i] for i in range(lo, hi + 1)])
        del t.stack.layers[lo:hi + 1]
        for k, layer in enumerate(replacement):
            t.stack.layers.insert(lo + k, layer)
        t.stack.active_index = min(lo, len(t.stack.layers) - 1)
        after = t.history.snapshot_layers(range(lo, lo + len(replacement)))
        t.history.record_structural(label, {
            "op": "replace", "start": lo, "before": before, "after": after,
            "active_before": active_before})
        t.evaluate()
        self.changed = True
        return replacement

    # ------------------------------------------------------------------
    # undo / redo
    # ------------------------------------------------------------------
    def undo(self):
        t = self.active_target()
        if t is None:
            return None
        e = t.history.undo()
        if e is not None:
            t.evaluate()
            self.changed = True
        return e

    def redo(self):
        t = self.active_target()
        if t is None:
            return None
        e = t.history.redo()
        if e is not None:
            t.evaluate()
            self.changed = True
        return e

    # ------------------------------------------------------------------
    # masks
    # ------------------------------------------------------------------
    def clear_mask(self):
        m = self.active_mask()
        if m is None:
            return None
        m.clear()
        self.changed = True
        return m

    def invert_mask(self):
        m = self.active_mask()
        if m is None:
            return None
        m.invert()
        self.changed = True
        return m

    def blur_mask(self, iterations=1):
        t = self.active_target()
        if t is None or t.mesh is None:
            return None
        t.mask.blur(t.mesh, iterations)
        self.changed = True
        return t.mask

    def mask_by_cavity(self, **kw):
        t = self.active_target()
        if t is None or t.mesh is None:
            return None
        t.mask.mask_by_cavity(t.mesh, **kw)
        self.changed = True
        return t.mask

    def set_freeze(self, on=True):
        m = self.active_mask()
        if m is None:
            return None
        m.freeze = bool(on)
        return m.freeze

    # ------------------------------------------------------------------
    # symmetry
    # ------------------------------------------------------------------
    def set_symmetry(self, axis, on=True):
        s = self.active_symmetry()
        return s.set_axis(axis, on) if s is not None else None

    def set_radial_symmetry(self, count, axis=None):
        s = self.active_symmetry()
        return s.set_radial(count, axis) if s is not None else None

    # ------------------------------------------------------------------
    # topology
    # ------------------------------------------------------------------
    def subdivide(self, levels=1):
        """Uniformly subdivide the active target, carrying the layers over."""
        t = self._target_or_fail()
        new_mesh, topo = _topology.subdivide_uniform(t.mesh, levels)
        return self._retopologise(t, new_mesh, topo, "subdivide")

    def subdivide_under(self, center, radius, min_edge=None):
        """Add detail inside a sphere -- the adaptive-detail brush."""
        t = self._target_or_fail()
        edge = self.target_edge if min_edge is None else float(min_edge)
        new_mesh, topo = _topology.subdivide_in_radius(t.mesh, center, radius,
                                                       min_edge=edge)
        return self._retopologise(t, new_mesh, topo, "subdivide under brush")

    def decimate(self, min_length):
        t = self._target_or_fail()
        new_mesh, topo = _topology.collapse_short_edges(t.mesh,
                                                        float(min_length))
        return self._retopologise(t, new_mesh, topo, "decimate")

    def remesh(self, target_edge=None, iterations=3, region=None):
        t = self._target_or_fail()
        edge = self.target_edge if target_edge is None else float(target_edge)
        new_mesh, topo = _topology.remesh(t.mesh, edge, iterations, region)
        return self._retopologise(t, new_mesh, topo, "remesh")

    def _retopologise(self, target, new_mesh, topo, label):
        """Swap in a new topology, remapping the stack and the mask.

        Every topology change clears the undo history: the entries below it
        index vertices that no longer exist, and replaying them would silently
        move the wrong ones.
        """
        topo.remap_stack(target.stack)
        topo.remap_mask(target.mask)
        target.mesh = new_mesh
        target.mask.resize(new_mesh.n_vertices)
        target.history.clear()
        target.evaluate()
        self.changed = True
        self.messages.append("%s: %d -> %d vertices"
                             % (label, topo.old_count, topo.new_count))
        return new_mesh

    # ------------------------------------------------------------------
    # controller events (the plain Python API)
    # ------------------------------------------------------------------
    def _hand(self, hand):
        h = self._hands.get(hand)
        if h is None:
            h = _HandState()
            self._hands[hand] = h
        return h

    def on_trigger(self, hand=0, value=0.0, position=None, normal=None,
                   time=None):
        """Analog trigger.  Returns True when a stroke started or ended."""
        h = self._hand(hand)
        h.value = float(value)
        if time is not None:
            self._time = float(time)
        if not h.pressed and h.value >= TRIGGER_ON:
            h.pressed = True
            return bool(self._begin(hand, position, normal, h.value))
        if h.pressed and h.value <= TRIGGER_OFF:
            h.pressed = False
            return bool(self._end(hand))
        return False

    def on_move(self, hand=0, position=None, normal=None, pressure=None,
                time=None):
        """Controller motion.  Only does anything while the trigger is held."""
        h = self._hand(hand)
        if time is not None:
            self._time = float(time)
        if not h.pressed:
            h.last_point = position
            return False
        p = h.value if pressure is None else float(pressure)
        return bool(self._continue(hand, position, normal, p))

    def on_grip(self, hand=0, value=0.0):
        """Grip: a squeeze above the threshold cancels the current stroke."""
        h = self._hand(hand)
        was = h.grip
        h.grip = float(value)
        if was < TRIGGER_ON <= h.grip and h.pressed:
            h.pressed = False
            self.cancel_all()
            return True
        return False

    def on_thumbstick(self, hand=0, x=0.0, y=0.0, dt=1.0 / 60.0):
        """Thumbstick: X scrubs the brush radius, Y the strength."""
        acted = False
        if abs(x) > 0.2:
            self.brush.radius = max(1e-4, self.brush.radius
                                    * (1.0 + x * dt * 2.0))
            acted = True
        if abs(y) > 0.2:
            s = self.brush.strength + y * dt * 0.8
            self.brush.strength = max(-2.0, min(2.0, s))
            acted = True
        if acted:
            self.changed = True
        return acted

    # -- stroke dispatch -------------------------------------------------
    def _begin(self, hand, position, normal, pressure):
        t = self.active_target()
        if t is None or t.mesh is None or position is None:
            return False
        h = self._hand(hand)
        h.sampler = _brushes.StrokeSampler(self.brush)
        h.anchor = (float(position[0]), float(position[1]),
                    float(position[2]))
        p = pressure if self.pressure_enabled else 1.0
        if self._mode == MODE_MASK:
            h.layer = None
            dabs = h.sampler.begin(position, normal, p, self._time)
            return self._paint_mask(t, dabs)
        h.layer = t.ensure_layer()
        t.history.begin("sculpt %s" % self.brush.kind)
        self._stroke_count += 1
        dabs = h.sampler.begin(position, normal, p, self._time)
        return self._apply(t, h, dabs)

    def _continue(self, hand, position, normal, pressure):
        t = self.active_target()
        h = self._hand(hand)
        if t is None or h.sampler is None or position is None:
            return False
        p = pressure if self.pressure_enabled else 1.0
        dabs = h.sampler.move(position, normal, p, self._time)
        if not dabs:
            return False
        if self._mode == MODE_MASK:
            return self._paint_mask(t, dabs)
        return self._apply(t, h, dabs)

    def _end(self, hand):
        h = self._hand(hand)
        if h.sampler is not None:
            h.sampler.end()
        h.sampler = None
        h.anchor = None
        h.layer = None
        t = self.active_target()
        if t is None or self._mode == MODE_MASK:
            return False
        entry = t.history.commit()
        self.changed = True
        return entry is not None

    def cancel_all(self):
        """Abort any stroke in flight without committing it."""
        for t in self.targets.values():
            if t.history.open_entry is not None:
                t.history.abort()
                t.evaluate()
        for h in self._hands.values():
            h.pressed = False
            h.sampler = None
            h.anchor = None
            h.layer = None
        return True

    # -- the actual sculpting -------------------------------------------
    def _apply(self, target, hand_state, dabs):
        layer = hand_state.layer
        if layer is None or layer.locked:
            return False
        touched = set()
        for dab in dabs:
            dab = self._adjust(hand_state, dab)
            for d in target.symmetry.expand(dab):
                idx = _brushes.apply_dab(target.mesh, layer, self.brush, d,
                                         mask=target.mask,
                                         stack=target.stack,
                                         history=target.history)
                touched.update(idx)
        if not touched:
            return False
        if target.symmetry.enabled:
            n = target.symmetry.constrain(layer, target.stack.base,
                                          sorted(touched))
            if n:
                target.stack.evaluate(out=target.mesh.positions,
                                      indices=sorted(touched))
                target.mesh.touch(sorted(touched))
        target.dirty = True
        self.changed = True
        return True

    def _adjust(self, hand_state, dab):
        """Grab anchors its sphere; every other brush follows the tip."""
        if self.brush.kind == "grab" and hand_state.anchor is not None:
            return dab.copy(center=hand_state.anchor)
        return dab

    def _paint_mask(self, target, dabs):
        acted = False
        for dab in dabs:
            for d in target.symmetry.expand(dab):
                idx = target.mask.paint_sphere(
                    target.mesh, d.center, d.radius * self.mask_radius_scale,
                    self.mask_strength * (1.0 if not self.brush.invert
                                          else -1.0),
                    self.brush.falloff,
                    "subtract" if self.brush.invert else "add")
                acted = acted or bool(idx)
        self.changed = self.changed or acted
        return acted

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
            position, normal = self._pick(ctl)
            self.on_grip(hand, grip)
            self.on_trigger(hand, value, position=position, normal=normal,
                            time=self._time)
            if self._hand(hand).pressed:
                self.on_move(hand, position=position, normal=normal,
                             pressure=value, time=self._time)
            self.on_thumbstick(hand, lx, ly, dt or 1.0 / 60.0)
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

    def _pick(self, controller):
        """The point and normal the controller is pointing at, if any."""
        point = None
        normal = None
        if self.root is not None and self.viewport_region is not None:
            try:
                hit, coords = controller.find_picked_coin_object(
                    self.root, self.viewport_region, self.near_plane,
                    self.far_plane, self.camera)
                if hit:
                    point = (float(coords[0]), float(coords[1]),
                             float(coords[2]))
                    n = controller.get_picked_normal()
                    normal = (float(n[0]), float(n[1]), float(n[2]))
            except Exception as exc:
                self.messages.append("pick failed: %s" % exc)
        if point is None:
            point, normal = self._pose(controller)
        return (point, normal)

    def _pose(self, controller):
        try:
            tr = controller.get_global_transf()
            pos = tr.translation.getValue()
            point = (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            return (None, None)
        try:
            axis = controller.find_ray_axis()
            av = axis.getValue() if hasattr(axis, "getValue") else axis
            normal = (-float(av[0]), -float(av[1]), -float(av[2]))
        except Exception:
            normal = None
        return (point, normal)

    # ------------------------------------------------------------------
    # export / import
    # ------------------------------------------------------------------
    def payloads(self):
        """One :class:`~xrsculpt.io.SculptPayload` per target, in order."""
        return [self.targets[n].to_payload() for n in self.target_order
                if n in self.targets]

    def export_bytes(self, fc_name=None):
        """FCSL bytes for one target (default: the active one)."""
        t = self.targets.get(fc_name) if fc_name else self.active_target()
        if t is None:
            return None
        return _io.dumps(t.stack, t.mask, t.symmetry, t.fc_name)

    def import_bytes(self, data, mesh=None):
        """Restore a target from FCSL bytes; returns the target."""
        payload = _io.loads(data)
        name = payload.fc_name or "Sculpt"
        existing = self.targets.get(name)
        faces = mesh.faces if mesh is not None else (
            existing.mesh.faces if existing is not None
            and existing.mesh is not None else [])
        target = SculptTarget.from_payload(payload, mesh,
                                           faces if mesh is None else None)
        self.targets[name] = target
        if name not in self.target_order:
            self.target_order.append(name)
        if self.active_target_name is None:
            self.active_target_name = name
        self.changed = True
        return target

    def export_sculpt_manifest(self, writer, encoding="fcsl1"):
        """The ``sculpt`` section of an FCXR manifest, or ``None``."""
        payloads = self.payloads()
        if not payloads:
            return None
        return _io.sculpt_section(writer, payloads, encoding)

    def import_sculpt_manifest(self, document, section=None, meshes=None):
        """Restore every target from an FCXR ``sculpt`` section."""
        payloads = _io.read_sculpt_section(document, section)
        out = []
        for payload in payloads:
            name = payload.fc_name or "Sculpt"
            m = (meshes or {}).get(name)
            existing = self.targets.get(name)
            faces = None
            if m is None and existing is not None \
                    and existing.mesh is not None:
                faces = existing.mesh.faces
            target = SculptTarget.from_payload(payload, m, faces)
            self.targets[name] = target
            if name not in self.target_order:
                self.target_order.append(name)
            out.append(target)
        if out and self.active_target_name is None:
            self.active_target_name = out[0].fc_name
        self.changed = True
        return out

    def __repr__(self):
        return "SculptSession(mode=%r, %d targets, brush=%s)" % (
            self._mode, len(self.targets), self.brush.kind)


def _merge_pair(pair):
    """``[lower, upper] -> [merged]``, for ``merge_layer_down``."""
    lower, upper = pair
    if lower.blend != "add" or upper.blend != "add":
        raise ValueError("merge down is only exact for 'add' layers")
    merged = _layers.SculptLayer(lower.name, 1.0, True, lower.locked, "add")
    merged.add_layer(lower, lower.effective_weight)
    merged.add_layer(upper, upper.effective_weight)
    return [merged]
