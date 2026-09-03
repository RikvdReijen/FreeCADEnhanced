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
"""The sketch scene: objects, nested layers, selection, arrays and undo.

The layer vocabulary is the one :mod:`xrpaint.layers` uses — ``name``,
``visible``, ``locked``, ``add_layer``, ``remove_layer``, ``move_layer``,
``rename`` — extended with a colour and with nesting, because a 3D sketch is
organised as a tree of collections rather than as a flat stack.  Visibility and
lock are *inherited*: :meth:`Scene.layer_visible` is false when any ancestor is
hidden and :meth:`Scene.layer_locked` is true when any ancestor is locked, so
hiding a parent hides a whole branch without touching the children's own flags.

Objects keep their geometry in object-local coordinates and their placement in
:attr:`SketchObject.transform`, a similarity transform
(:class:`xrsketch.vecmath.Transform`) — which is exactly what two-handed
manipulation produces, so a grab can be written straight back to it.

Undo is a snapshot stack.  Each entry stores the serialised scene before and
after the edit, so :meth:`UndoStack.undo` restores the state *exactly*,
including selection, rather than trying to invert every operation.  Sketch
edits are coarse (one gesture, one entry) so the memory cost is fine; the
tile-based pixel history of :class:`xrpaint.layers.History` solves a different
problem.
"""

import math

from . import vecmath as vm
from .curves import Curve3D
from .primitives import Primitive
from .subd import Cage
from .surfacing import SurfaceMesh
from .vecmath import Transform

__all__ = [
    "Group",
    "Layer",
    "OBJECT_KINDS",
    "Scene",
    "SketchObject",
    "UndoStack",
    "mirror_data",
    "reflect_rotation",
]

OBJECT_KINDS = ("curve", "cage", "primitive", "surface", "image", "measure")

_DATA_TYPES = {
    "curve": Curve3D,
    "cage": Cage,
    "primitive": Primitive,
    "surface": SurfaceMesh,
}


def _data_type(kind):
    if kind in _DATA_TYPES:
        return _DATA_TYPES[kind]
    from . import reference
    if kind == "image":
        return reference.ImagePlane
    if kind == "measure":
        return reference.Measurement
    raise ValueError("unknown object kind: %r" % (kind,))


# --------------------------------------------------------------------------
# layers
# --------------------------------------------------------------------------

class Layer(object):
    """A named collection with visibility, lock, colour and a parent."""

    __slots__ = ("id", "name", "visible", "locked", "color", "parent")

    def __init__(self, layer_id, name, parent=None, visible=True,
                 locked=False, color=(0.8, 0.8, 0.8, 1.0)):
        self.id = str(layer_id)
        self.name = str(name)
        self.parent = None if parent is None else str(parent)
        self.visible = bool(visible)
        self.locked = bool(locked)
        self.color = tuple(float(c) for c in color)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "parent": self.parent,
                "visible": self.visible, "locked": self.locked,
                "color": list(self.color)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d.get("name", "Layer"), d.get("parent"),
                   d.get("visible", True), d.get("locked", False),
                   d.get("color", (0.8, 0.8, 0.8, 1.0)))

    def __repr__(self):
        return "Layer(%r%s%s)" % (
            self.name, "" if self.visible else ", hidden",
            ", locked" if self.locked else "")


class Group(object):
    """A named set of objects that select and transform together."""

    __slots__ = ("id", "name", "members")

    def __init__(self, group_id, name, members=None):
        self.id = str(group_id)
        self.name = str(name)
        self.members = list(members or [])

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "members": list(self.members)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d.get("name", "Group"), d.get("members"))

    def __repr__(self):
        return "Group(%r, %d members)" % (self.name, len(self.members))


class SketchObject(object):
    """One thing in the scene: geometry, a placement and its flags."""

    __slots__ = ("id", "name", "kind", "data", "transform", "layer",
                 "visible", "locked", "color")

    def __init__(self, object_id, kind, data, name=None, transform=None,
                 layer=None, visible=True, locked=False, color=None):
        if kind not in OBJECT_KINDS:
            raise ValueError("unknown object kind: %r" % (kind,))
        self.id = str(object_id)
        self.kind = kind
        self.data = data
        self.name = str(name or getattr(data, "name", None) or self.id)
        self.transform = (transform.copy() if isinstance(transform, Transform)
                          else Transform())
        self.layer = None if layer is None else str(layer)
        self.visible = bool(visible)
        self.locked = bool(locked)
        self.color = None if color is None else tuple(float(c) for c in color)

    def local_bounds(self):
        data = self.data
        for name in ("bounds", "bbox"):
            fn = getattr(data, name, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    pass
        pts = getattr(data, "points", None)
        if pts:
            try:
                coords = [vm.vec3(getattr(p, "position", p)) for p in pts]
                return (tuple(min(c[i] for c in coords) for i in range(3)),
                        tuple(max(c[i] for c in coords) for i in range(3)))
            except Exception:
                pass
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    def world_bounds(self):
        lo, hi = self.local_bounds()
        corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])]
        world = [self.transform.apply(c) for c in corners]
        return (tuple(min(c[i] for c in world) for i in range(3)),
                tuple(max(c[i] for c in world) for i in range(3)))

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "layer": self.layer, "visible": self.visible,
                "locked": self.locked,
                "color": None if self.color is None else list(self.color),
                "transform": self.transform.to_dict(),
                "data": self.data.to_dict()}

    @classmethod
    def from_dict(cls, d):
        data = _data_type(d["kind"]).from_dict(d["data"])
        return cls(d["id"], d["kind"], data, d.get("name"),
                   Transform.from_dict(d.get("transform")), d.get("layer"),
                   d.get("visible", True), d.get("locked", False),
                   d.get("color"))

    def __repr__(self):
        return "SketchObject(%r, %s)" % (self.name, self.kind)


# --------------------------------------------------------------------------
# mirroring
# --------------------------------------------------------------------------

def reflect_rotation(q, normal):
    """The rotation ``M R M`` obtained by mirroring a rotation in a plane.

    Conjugating by the reflection keeps the determinant positive, so a
    mirrored placement is still a rotation and still fits in a
    :class:`~xrsketch.vecmath.Transform`.
    """
    n = vm.normalize(normal)
    m = [[(1.0 if i == j else 0.0) - 2.0 * n[i] * n[j] for j in range(3)]
         for i in range(3)]
    r = vm.quat_to_mat3(q)
    return vm.quat_from_mat3(vm.mat3_mul(m, vm.mat3_mul(r, m)))


def mirror_data(kind, data, origin, normal):
    """A mirrored copy of an object's geometry (winding reversed with it)."""
    n = vm.normalize(normal)
    if vm.length(n) < 0.5:
        raise ValueError("mirror plane needs a non-zero normal")
    if kind == "curve":
        from . import curves as _curves
        return _curves.mirror(data, origin, n)
    if kind == "cage":
        out = data.copy()
        out.vertices = [vm.reflect_point(v, origin, n) for v in out.vertices]
        out.faces = [tuple(reversed(f)) for f in out.faces]
        out.face_uvs = [None if uv is None else list(reversed(uv))
                        for uv in out.face_uvs]
        out.invalidate()
        return out
    if kind == "surface":
        grid = [[vm.reflect_point(p, origin, n) for p in reversed(row)]
                for row in data.grid]
        return SurfaceMesh(grid, data.closed_u, data.closed_v, data.kind,
                           data.name)
    if kind == "primitive":
        out = data.copy()
        t = out.transform
        out.transform = Transform(vm.reflect_point(t.translation, origin, n),
                                  reflect_rotation(t.rotation, n), t.scale)
        if out.kind == "tube":
            out.params["path"] = tuple(
                vm.reflect_vector(p, n) for p in out.params["path"])
        return out
    out = data.copy() if hasattr(data, "copy") else data
    return out


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

class UndoStack(object):
    """Bounded snapshot undo/redo, shaped like
    ``xrpaint.layers.History``."""

    def __init__(self, scene, max_entries=64):
        self.scene = scene
        self.max_entries = int(max_entries)
        self._undo = []
        self._redo = []
        self._open = None

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo_labels(self):
        return [e[0] for e in self._undo]

    def redo_labels(self):
        return [e[0] for e in self._redo]

    def clear(self):
        self._undo = []
        self._redo = []
        self._open = None

    def begin(self, label="edit"):
        if self._open is not None:
            raise RuntimeError("an undo entry is already open")
        self._open = (str(label), self.scene.to_dict())
        return self._open

    def commit(self, drop_if_empty=True):
        entry = self._open
        self._open = None
        if entry is None:
            return None
        after = self.scene.to_dict()
        if drop_if_empty and after == entry[1]:
            return None
        record = (entry[0], entry[1], after)
        self._undo.append(record)
        self._redo = []
        while len(self._undo) > self.max_entries:
            self._undo.pop(0)
        return record

    def abort(self):
        entry = self._open
        self._open = None
        if entry is not None:
            self.scene.restore(entry[1])
        return entry

    def undo(self):
        if not self._undo:
            return None
        record = self._undo.pop()
        self.scene.restore(record[1])
        self._redo.append(record)
        return record[0]

    def redo(self):
        if not self._redo:
            return None
        record = self._redo.pop()
        self.scene.restore(record[2])
        self._undo.append(record)
        return record[0]

    def __repr__(self):
        return "UndoStack(%d undo, %d redo)" % (len(self._undo),
                                                len(self._redo))


class _Edit(object):
    """Context manager wrapping one undoable edit."""

    def __init__(self, history, label):
        self.history = history
        self.label = label

    def __enter__(self):
        self.history.begin(self.label)
        return self.history

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.history.commit()
        else:
            self.history.abort()
        return False


# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------

class Scene(object):
    """Objects, a layer tree, groups, a selection and an undo stack."""

    def __init__(self, name="Sketch"):
        self.name = str(name)
        self.layers = []
        self.objects = []
        self.groups = []
        self.selection = []
        self.history = UndoStack(self)
        self._counter = 0
        self.active_layer = self.add_layer("Layer 1").id

    # -- ids -------------------------------------------------------------
    def _new_id(self, prefix):
        self._counter += 1
        return "%s%d" % (prefix, self._counter)

    def edit(self, label="edit"):
        """``with scene.edit("move"): ...`` — one undoable step."""
        return _Edit(self.history, label)

    # -- layers ----------------------------------------------------------
    def add_layer(self, name=None, parent=None, color=(0.8, 0.8, 0.8, 1.0)):
        if parent is not None and self.layer(parent) is None:
            raise KeyError("no such layer: %r" % (parent,))
        lid = self._new_id("L")
        layer = Layer(lid, name or ("Layer %d" % (len(self.layers) + 1)),
                      parent, color=color)
        self.layers.append(layer)
        return layer

    def layer(self, layer_id):
        for l in self.layers:
            if l.id == layer_id:
                return l
        return None

    def find_layer(self, name):
        for l in self.layers:
            if l.name == name:
                return l
        return None

    def layer_children(self, layer_id):
        return [l for l in self.layers if l.parent == layer_id]

    def layer_descendants(self, layer_id):
        out = []
        stack = [layer_id]
        while stack:
            cur = stack.pop()
            for child in self.layer_children(cur):
                out.append(child)
                stack.append(child.id)
        return out

    def layer_ancestors(self, layer_id):
        out = []
        layer = self.layer(layer_id)
        seen = set()
        while layer is not None and layer.parent is not None:
            if layer.parent in seen:
                break
            seen.add(layer.parent)
            parent = self.layer(layer.parent)
            if parent is None:
                break
            out.append(parent)
            layer = parent
        return out

    def move_layer(self, layer_id, parent):
        """Re-parent a layer; refuses to build a cycle."""
        layer = self.layer(layer_id)
        if layer is None:
            raise KeyError("no such layer: %r" % (layer_id,))
        if parent is not None:
            if parent == layer_id:
                raise ValueError("a layer cannot be its own parent")
            if self.layer(parent) is None:
                raise KeyError("no such layer: %r" % (parent,))
            if any(d.id == parent for d in self.layer_descendants(layer_id)):
                raise ValueError("that would make a layer cycle")
        layer.parent = None if parent is None else str(parent)
        return layer

    def remove_layer(self, layer_id, reparent=True):
        """Delete a layer.  Its children and objects move up, or go with it."""
        layer = self.layer(layer_id)
        if layer is None:
            raise KeyError("no such layer: %r" % (layer_id,))
        if len(self.layers) == 1:
            raise ValueError("the last layer cannot be removed")
        if reparent:
            for child in self.layer_children(layer_id):
                child.parent = layer.parent
            for obj in self.objects:
                if obj.layer == layer_id:
                    obj.layer = layer.parent or self.layers[0].id
        else:
            doomed = {layer_id} | set(d.id for d in
                                      self.layer_descendants(layer_id))
            self.objects = [o for o in self.objects if o.layer not in doomed]
            self.layers = [l for l in self.layers if l.id not in doomed]
            if self.active_layer not in [l.id for l in self.layers]:
                self.active_layer = self.layers[0].id
            self.selection = [s for s in self.selection
                              if self.object(s) is not None]
            return layer
        self.layers = [l for l in self.layers if l.id != layer_id]
        if self.active_layer == layer_id:
            self.active_layer = self.layers[0].id
        return layer

    def rename(self, layer_id, name):
        layer = self.layer(layer_id)
        if layer is None:
            raise KeyError("no such layer: %r" % (layer_id,))
        layer.name = str(name)
        return layer

    def set_layer_visible(self, layer_id, value):
        self.layer(layer_id).visible = bool(value)
        return value

    def set_layer_locked(self, layer_id, value):
        self.layer(layer_id).locked = bool(value)
        return value

    def set_layer_color(self, layer_id, color):
        self.layer(layer_id).color = tuple(float(c) for c in color)
        return self.layer(layer_id).color

    def layer_visible(self, layer_id):
        """Visible only when the layer *and* every ancestor is."""
        layer = self.layer(layer_id)
        if layer is None:
            return False
        if not layer.visible:
            return False
        return all(a.visible for a in self.layer_ancestors(layer_id))

    def layer_locked(self, layer_id):
        """Locked when the layer *or* any ancestor is."""
        layer = self.layer(layer_id)
        if layer is None:
            return True
        if layer.locked:
            return True
        return any(a.locked for a in self.layer_ancestors(layer_id))

    def layer_color(self, layer_id):
        layer = self.layer(layer_id)
        return layer.color if layer is not None else (1.0, 1.0, 1.0, 1.0)

    # -- objects ---------------------------------------------------------
    def add(self, kind, data, name=None, transform=None, layer=None):
        """Add an object; primitives hand their placement to the object."""
        if kind == "primitive" and transform is None:
            transform = data.transform
            data = data.copy()
            data.transform = Transform()
        obj = SketchObject(self._new_id("O"), kind, data, name, transform,
                           layer or self.active_layer)
        self.objects.append(obj)
        return obj

    def add_curve(self, curve, **kw):
        return self.add("curve", curve, **kw)

    def add_cage(self, cage, **kw):
        return self.add("cage", cage, **kw)

    def add_primitive(self, primitive, **kw):
        return self.add("primitive", primitive, **kw)

    def add_surface(self, surface, **kw):
        return self.add("surface", surface, **kw)

    def object(self, object_id):
        for o in self.objects:
            if o.id == object_id:
                return o
        return None

    def remove(self, obj):
        oid = obj.id if isinstance(obj, SketchObject) else str(obj)
        target = self.object(oid)
        if target is None:
            return False
        self.objects.remove(target)
        self.selection = [s for s in self.selection if s != oid]
        for g in self.groups:
            if oid in g.members:
                g.members.remove(oid)
        self.groups = [g for g in self.groups if len(g.members) > 1]
        return True

    def objects_in_layer(self, layer_id, include_nested=True):
        ids = {layer_id}
        if include_nested:
            ids |= set(d.id for d in self.layer_descendants(layer_id))
        return [o for o in self.objects if o.layer in ids]

    def object_visible(self, obj):
        return bool(obj.visible) and self.layer_visible(obj.layer)

    def object_locked(self, obj):
        return bool(obj.locked) or self.layer_locked(obj.layer)

    def visible_objects(self):
        return [o for o in self.objects if self.object_visible(o)]

    def move_to_layer(self, objects, layer_id):
        if self.layer(layer_id) is None:
            raise KeyError("no such layer: %r" % (layer_id,))
        for obj in _as_objects(self, objects):
            obj.layer = layer_id
        return layer_id

    # -- selection -------------------------------------------------------
    def selected_objects(self):
        return [o for o in (self.object(i) for i in self.selection)
                if o is not None]

    def select(self, objects, additive=False, expand_groups=True):
        """Select one or many objects; locked objects are never selected."""
        if not additive:
            self.selection = []
        for obj in _as_objects(self, objects):
            if self.object_locked(obj):
                continue
            ids = [obj.id]
            if expand_groups:
                group = self.group_of(obj)
                if group is not None:
                    ids = list(group.members)
            for oid in ids:
                target = self.object(oid)
                if target is None or self.object_locked(target):
                    continue
                if oid not in self.selection:
                    self.selection.append(oid)
        return self.selected_objects()

    def toggle(self, obj, expand_groups=True):
        obj = _as_objects(self, obj)[0]
        if obj.id in self.selection:
            ids = [obj.id]
            group = self.group_of(obj) if expand_groups else None
            if group is not None:
                ids = list(group.members)
            self.selection = [s for s in self.selection if s not in ids]
            return False
        self.select(obj, additive=True, expand_groups=expand_groups)
        return True

    def deselect_all(self):
        self.selection = []
        return []

    def select_all(self, include_hidden=False):
        self.selection = [o.id for o in self.objects
                          if not self.object_locked(o)
                          and (include_hidden or self.object_visible(o))]
        return self.selected_objects()

    def select_by_layer(self, layer_id, include_nested=True, additive=False):
        return self.select(self.objects_in_layer(layer_id, include_nested),
                           additive=additive)

    def select_in_box(self, lo, hi, additive=False, contained=True):
        """Box select: fully contained objects, or merely overlapping ones."""
        lo = vm.vec3(lo)
        hi = vm.vec3(hi)
        lo, hi = (tuple(min(lo[i], hi[i]) for i in range(3)),
                  tuple(max(lo[i], hi[i]) for i in range(3)))
        hits = []
        for obj in self.objects:
            if not self.object_visible(obj) or self.object_locked(obj):
                continue
            olo, ohi = obj.world_bounds()
            if contained:
                ok = all(lo[i] <= olo[i] and ohi[i] <= hi[i]
                         for i in range(3))
            else:
                ok = all(olo[i] <= hi[i] and ohi[i] >= lo[i]
                         for i in range(3))
            if ok:
                hits.append(obj)
        return self.select(hits, additive=additive)

    # -- groups ----------------------------------------------------------
    def group(self, objects=None, name=None):
        """Group objects (default: the selection) so they move together."""
        objs = _as_objects(self, objects) if objects is not None \
            else self.selected_objects()
        if len(objs) < 2:
            raise ValueError("grouping needs at least two objects")
        members = []
        for obj in objs:
            existing = self.group_of(obj)
            if existing is not None:
                self.groups.remove(existing)
                members.extend(m for m in existing.members
                               if m not in members)
            elif obj.id not in members:
                members.append(obj.id)
        group = Group(self._new_id("G"), name or ("Group %d"
                                                  % (len(self.groups) + 1)),
                      members)
        self.groups.append(group)
        return group

    def ungroup(self, group):
        gid = group.id if isinstance(group, Group) else str(group)
        for g in list(self.groups):
            if g.id == gid:
                self.groups.remove(g)
                return True
        return False

    def group_of(self, obj):
        oid = obj.id if isinstance(obj, SketchObject) else str(obj)
        for g in self.groups:
            if oid in g.members:
                return g
        return None

    def group_objects(self, group):
        gid = group.id if isinstance(group, Group) else str(group)
        for g in self.groups:
            if g.id == gid:
                return [o for o in (self.object(m) for m in g.members)
                        if o is not None]
        return []

    # -- editing ---------------------------------------------------------
    def transform_objects(self, objects, transform):
        """Compose a transform onto every object's placement."""
        for obj in _as_objects(self, objects):
            obj.transform = vm.compose(transform, obj.transform)
        return objects

    def duplicate(self, objects=None, offset=(0.0, 0.0, 0.0), suffix=" copy"):
        """Copy objects (default: the selection), offset in space."""
        objs = _as_objects(self, objects) if objects is not None \
            else self.selected_objects()
        out = []
        for obj in objs:
            clone = SketchObject.from_dict(obj.to_dict())
            clone.id = self._new_id("O")
            clone.name = obj.name + suffix
            clone.transform = vm.compose(Transform(offset), obj.transform)
            self.objects.append(clone)
            out.append(clone)
        return out

    def array_linear(self, objects, count, offset, include_original=True):
        """``count`` copies in a row, ``offset`` apart (original included)."""
        count = int(count)
        if count < 1:
            raise ValueError("an array needs a count of at least 1")
        objs = _as_objects(self, objects)
        out = list(objs) if include_original else []
        for i in range(1, count):
            step = vm.mul(vm.vec3(offset), float(i))
            out.extend(self.duplicate(objs, step, suffix=" %d" % i))
        return out

    def array_radial(self, objects, count, angle=2.0 * math.pi,
                     center=(0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0),
                     include_original=True, full_circle=None):
        """``count`` copies around an axis.

        A full circle divides ``angle`` by ``count`` so the last copy does not
        land on the first; a partial sweep divides by ``count - 1`` so the
        copies span the whole angle.
        """
        count = int(count)
        if count < 1:
            raise ValueError("an array needs a count of at least 1")
        a = vm.normalize(axis)
        if vm.length(a) < 0.5:
            raise ValueError("a radial array needs a non-zero axis")
        if full_circle is None:
            full_circle = abs(abs(float(angle)) - 2.0 * math.pi) < 1e-9
        divisor = count if full_circle else max(1, count - 1)
        objs = _as_objects(self, objects)
        out = list(objs) if include_original else []
        centre = vm.vec3(center)
        for i in range(1, count):
            q = vm.quat_from_axis_angle(a, float(angle) * i / divisor)
            step = Transform(vm.sub(centre, vm.quat_rotate(q, centre)), q, 1.0)
            for obj in objs:
                clone = SketchObject.from_dict(obj.to_dict())
                clone.id = self._new_id("O")
                clone.name = "%s %d" % (obj.name, i)
                clone.transform = vm.compose(step, obj.transform)
                self.objects.append(clone)
                out.append(clone)
        return out

    def array_mirror(self, objects, origin=(0.0, 0.0, 0.0),
                     normal=(1.0, 0.0, 0.0), include_original=True):
        """Mirrored copies.

        A reflection is not a rotation, so the *geometry* is mirrored
        (:func:`mirror_data`) rather than the placement, which also keeps the
        face winding correct instead of turning every mirrored surface inside
        out.
        """
        objs = _as_objects(self, objects)
        out = list(objs) if include_original else []
        n = vm.normalize(normal)
        for obj in objs:
            local_origin = obj.transform.inverse().apply(origin)
            local_normal = vm.quat_rotate(vm.quat_conjugate(
                obj.transform.rotation), n)
            data = mirror_data(obj.kind, obj.data, local_origin, local_normal)
            clone = SketchObject(self._new_id("O"), obj.kind, data,
                                 obj.name + " mirror", obj.transform,
                                 obj.layer, obj.visible, obj.locked,
                                 obj.color)
            self.objects.append(clone)
            out.append(clone)
        return out

    # -- serialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "name": self.name,
            "counter": self._counter,
            "active_layer": self.active_layer,
            "layers": [l.to_dict() for l in self.layers],
            "groups": [g.to_dict() for g in self.groups],
            "objects": [o.to_dict() for o in self.objects],
            "selection": list(self.selection),
        }

    def restore(self, state):
        """Replace the whole scene state (used by undo)."""
        self.name = state.get("name", self.name)
        self._counter = int(state.get("counter", self._counter))
        self.layers = [Layer.from_dict(d) for d in state.get("layers", [])]
        self.groups = [Group.from_dict(d) for d in state.get("groups", [])]
        self.objects = [SketchObject.from_dict(d)
                        for d in state.get("objects", [])]
        self.selection = list(state.get("selection", []))
        self.active_layer = state.get("active_layer")
        if self.layer(self.active_layer) is None and self.layers:
            self.active_layer = self.layers[0].id
        return self

    @classmethod
    def from_dict(cls, state):
        scene = cls(state.get("name", "Sketch"))
        scene.restore(state)
        return scene

    def bounds(self):
        objs = [o for o in self.objects if self.object_visible(o)]
        if not objs:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        boxes = [o.world_bounds() for o in objs]
        return (tuple(min(b[0][i] for b in boxes) for i in range(3)),
                tuple(max(b[1][i] for b in boxes) for i in range(3)))

    def __len__(self):
        return len(self.objects)

    def __iter__(self):
        return iter(self.objects)

    def __repr__(self):
        return "Scene(%r, %d objects, %d layers)" % (
            self.name, len(self.objects), len(self.layers))


def _as_objects(scene, objects):
    if objects is None:
        return []
    if isinstance(objects, SketchObject):
        return [objects]
    if isinstance(objects, str):
        obj = scene.object(objects)
        return [obj] if obj is not None else []
    out = []
    for item in objects:
        out.extend(_as_objects(scene, item))
    return out
