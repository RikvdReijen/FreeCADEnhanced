# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dataflow graph model for Grasshopper mode.

A :class:`Graph` holds :class:`Node` and :class:`Wire` objects.  Nodes are
instances of a :class:`NodeType` from a :class:`Registry`.  Evaluation is
lazy and incremental: only nodes marked dirty (and their downstream) are
recomputed.  The module has no FreeCAD dependency so it can be tested with a
plain interpreter.
"""

import copy
import itertools
import json
import uuid
from collections import OrderedDict, deque

# Port kinds.  ``list`` and ``any`` accept anything; scalar kinds are mapped
# element-wise over lists when the node type sets ``elementwise=True``.
KINDS = ("number", "int", "bool", "text", "point", "vector", "shape", "list", "any")

# Which kinds may be wired into which input kinds.
# ``list`` and ``any`` outputs may feed any input: scalar nodes map over
# lists element-wise (Grasshopper style) and complain at evaluation time when
# an item has the wrong type.
_COMPATIBLE = {
    "number": {"number", "int", "bool", "list", "any"},
    "int": {"int", "number", "bool", "list", "any"},
    "bool": {"bool", "int", "number", "list", "any"},
    "text": {"text", "number", "int", "bool", "list", "any"},
    "point": {"point", "vector", "list", "any"},
    "vector": {"vector", "point", "list", "any"},
    "shape": {"shape", "list", "any"},
    "list": set(KINDS),
    "any": set(KINDS),
}


class GraphError(Exception):
    """Raised for invalid graph operations (cycles, bad ports, ...)."""


class PortSpec:
    """Description of one input or output socket of a node type."""

    __slots__ = ("name", "kind", "default", "doc", "multi", "hidden")

    def __init__(self, name, kind="any", default=None, doc="", multi=False, hidden=False):
        if kind not in KINDS:
            raise ValueError("unknown port kind %r" % (kind,))
        self.name = name
        self.kind = kind
        self.default = default
        self.doc = doc
        # multi inputs collect every incoming wire into a list
        self.multi = multi
        self.hidden = hidden

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "default": self.default,
            "doc": self.doc,
            "multi": self.multi,
        }


class NodeType:
    """A node definition: sockets plus the function that computes outputs."""

    def __init__(
        self,
        type_id,
        label,
        category,
        inputs=(),
        outputs=(),
        func=None,
        elementwise=False,
        params=None,
        description="",
        width=None,
        widget=None,
    ):
        self.type_id = type_id
        self.label = label
        self.category = category
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.func = func
        self.elementwise = elementwise
        self.params = dict(params or {})
        self.description = description
        self.width = width
        # widget hint for the front ends: "slider", "toggle", "text", "panel"
        self.widget = widget

    def input(self, name):
        for spec in self.inputs:
            if spec.name == name:
                return spec
        return None

    def output(self, name):
        for spec in self.outputs:
            if spec.name == name:
                return spec
        return None

    def to_dict(self):
        return {
            "type": self.type_id,
            "label": self.label,
            "category": self.category,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params": copy.deepcopy(self.params),
            "description": self.description,
            "widget": self.widget,
        }


class Registry:
    """Maps type ids to :class:`NodeType` objects."""

    def __init__(self):
        self._types = OrderedDict()

    def register(self, node_type):
        self._types[node_type.type_id] = node_type
        return node_type

    def get(self, type_id):
        try:
            return self._types[type_id]
        except KeyError:
            raise GraphError("unknown node type %r" % (type_id,))

    def __contains__(self, type_id):
        return type_id in self._types

    def __iter__(self):
        return iter(self._types.values())

    def __len__(self):
        return len(self._types)

    def categories(self):
        cats = OrderedDict()
        for t in self._types.values():
            cats.setdefault(t.category, []).append(t)
        return cats

    def search(self, text):
        text = (text or "").strip().lower()
        if not text:
            return list(self._types.values())
        hits = []
        for t in self._types.values():
            hay = " ".join([t.label, t.type_id, t.category, t.description]).lower()
            if text in hay:
                hits.append(t)
        return hits


def _new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


class Node:
    """A node instance placed on the canvas."""

    def __init__(self, node_id, type_id, x=0.0, y=0.0, values=None, params=None):
        self.id = node_id
        self.type_id = type_id
        self.x = float(x)
        self.y = float(y)
        # literal values for unconnected inputs
        self.values = dict(values or {})
        # per-instance parameters (slider range, label, ...)
        self.params = dict(params or {})
        # runtime state
        self.outputs = {}
        self.error = None
        self.dirty = True
        self.eval_ms = 0.0

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type_id,
            "x": self.x,
            "y": self.y,
            "values": _jsonable(self.values),
            "params": _jsonable(self.params),
        }


class Wire:
    __slots__ = ("id", "src_node", "src_port", "dst_node", "dst_port")

    def __init__(self, wire_id, src_node, src_port, dst_node, dst_port):
        self.id = wire_id
        self.src_node = src_node
        self.src_port = src_port
        self.dst_node = dst_node
        self.dst_port = dst_port

    def to_dict(self):
        return {
            "id": self.id,
            "src": [self.src_node, self.src_port],
            "dst": [self.dst_node, self.dst_port],
        }


def _jsonable(value):
    """Best-effort conversion of literal values to JSON friendly data."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_json"):
        return value.to_json()
    return value


class EvalContext:
    """Passed to every node function during evaluation."""

    def __init__(self, graph, backend, node):
        self.graph = graph
        self.backend = backend
        self.node = node


class Graph:
    """A dataflow graph with incremental evaluation, undo and events."""

    FORMAT_VERSION = 1

    def __init__(self, registry, backend=None, canvas_id=None, name="Untitled"):
        self.registry = registry
        self.backend = backend
        self.canvas_id = canvas_id or uuid.uuid4().hex[:12]
        self.name = name
        self.nodes = OrderedDict()
        self.wires = OrderedDict()
        self.selection = []
        self._listeners = []
        self._undo = []
        self._redo = []
        self._batch = 0
        self._pending_events = []
        self.revision = 0

    # ------------------------------------------------------------------ events
    def on(self, callback):
        """Register ``callback(event_name, payload_dict)``."""
        self._listeners.append(callback)
        return callback

    def off(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event, **payload):
        self.revision += 1
        if self._batch:
            self._pending_events.append((event, payload))
            return
        for cb in list(self._listeners):
            cb(event, payload)

    class _Batch:
        def __init__(self, graph):
            self.graph = graph

        def __enter__(self):
            self.graph._batch += 1

        def __exit__(self, *exc):
            self.graph._batch -= 1
            if self.graph._batch == 0:
                pending, self.graph._pending_events = self.graph._pending_events, []
                for event, payload in pending:
                    for cb in list(self.graph._listeners):
                        cb(event, payload)

    def batch(self):
        """Context manager that defers events until the block ends."""
        return Graph._Batch(self)

    # -------------------------------------------------------------------- undo
    def push_undo(self):
        self._undo.append(self.to_dict(include_runtime=False))
        if len(self._undo) > 100:
            self._undo.pop(0)
        self._redo = []

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self.to_dict(include_runtime=False))
        self._load_dict(self._undo.pop())
        self._emit("restored")
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self.to_dict(include_runtime=False))
        self._load_dict(self._redo.pop())
        self._emit("restored")
        return True

    # ------------------------------------------------------------------- nodes
    def node_type(self, node):
        return self.registry.get(node.type_id)

    def add_node(self, type_id, x=0.0, y=0.0, values=None, params=None, node_id=None):
        ntype = self.registry.get(type_id)
        node_id = node_id or _new_id("n")
        if node_id in self.nodes:
            raise GraphError("duplicate node id %r" % (node_id,))
        node = Node(node_id, type_id, x, y, values, params)
        # literals (possibly freshly loaded from JSON) get their port kind
        for key in list(node.values):
            spec = ntype.input(key)
            if spec is None:
                del node.values[key]
            else:
                node.values[key] = coerce(spec.kind, node.values[key])
        # instance params start from the type defaults
        merged = copy.deepcopy(ntype.params)
        merged.update(node.params)
        node.params = merged
        self.nodes[node_id] = node
        self._emit("node_added", node=node_id)
        return node

    def remove_node(self, node_id):
        node = self.nodes.pop(node_id, None)
        if node is None:
            return
        for wire in [w for w in self.wires.values() if node_id in (w.src_node, w.dst_node)]:
            self.disconnect(wire.id)
        if node_id in self.selection:
            self.selection.remove(node_id)
        self._emit("node_removed", node=node_id)

    def move_node(self, node_id, x, y):
        node = self.nodes[node_id]
        node.x, node.y = float(x), float(y)
        self._emit("node_moved", node=node_id, x=node.x, y=node.y)

    def set_value(self, node_id, port, value):
        """Set the literal for an unconnected input (or a widget value)."""
        node = self.nodes[node_id]
        ntype = self.node_type(node)
        spec = ntype.input(port)
        if spec is None:
            raise GraphError("node %s has no input %r" % (node_id, port))
        value = coerce(spec.kind, value)
        node.values[port] = value
        self._mark_dirty(node_id)
        self._emit("value_changed", node=node_id, port=port, value=_jsonable(value))
        return value

    def set_param(self, node_id, key, value):
        node = self.nodes[node_id]
        node.params[key] = value
        self._mark_dirty(node_id)
        self._emit("param_changed", node=node_id, key=key, value=_jsonable(value))

    # ------------------------------------------------------------------- wires
    def can_connect(self, src_node, src_port, dst_node, dst_port):
        if src_node not in self.nodes or dst_node not in self.nodes:
            return False, "unknown node"
        if src_node == dst_node:
            return False, "cannot connect a node to itself"
        src_type = self.node_type(self.nodes[src_node])
        dst_type = self.node_type(self.nodes[dst_node])
        out_spec = src_type.output(src_port)
        in_spec = dst_type.input(dst_port)
        if out_spec is None:
            return False, "no output %r on %s" % (src_port, src_type.label)
        if in_spec is None:
            return False, "no input %r on %s" % (dst_port, dst_type.label)
        if out_spec.kind not in _COMPATIBLE[in_spec.kind]:
            return False, "%s output cannot feed %s input" % (out_spec.kind, in_spec.kind)
        if self._creates_cycle(src_node, dst_node):
            return False, "connection would create a cycle"
        return True, ""

    def _creates_cycle(self, src_node, dst_node):
        # walking downstream from dst must never reach src
        seen = set()
        todo = deque([dst_node])
        while todo:
            current = todo.popleft()
            if current == src_node:
                return True
            if current in seen:
                continue
            seen.add(current)
            for w in self.wires.values():
                if w.src_node == current:
                    todo.append(w.dst_node)
        return False

    def connect(self, src_node, src_port, dst_node, dst_port, wire_id=None):
        ok, reason = self.can_connect(src_node, src_port, dst_node, dst_port)
        if not ok:
            raise GraphError(reason)
        in_spec = self.node_type(self.nodes[dst_node]).input(dst_port)
        if not in_spec.multi:
            # single inputs are replaced by the new wire
            for w in list(self.wires.values()):
                if w.dst_node == dst_node and w.dst_port == dst_port:
                    self.disconnect(w.id)
        for w in self.wires.values():
            if (w.src_node, w.src_port, w.dst_node, w.dst_port) == (
                src_node,
                src_port,
                dst_node,
                dst_port,
            ):
                return w
        wire = Wire(wire_id or _new_id("w"), src_node, src_port, dst_node, dst_port)
        self.wires[wire.id] = wire
        self._mark_dirty(dst_node)
        self._emit("wire_added", wire=wire.id, **wire.to_dict())
        return wire

    def disconnect(self, wire_id):
        wire = self.wires.pop(wire_id, None)
        if wire is None:
            return
        if wire.dst_node in self.nodes:
            self._mark_dirty(wire.dst_node)
        self._emit("wire_removed", wire=wire_id)

    def wires_into(self, node_id, port=None):
        return [
            w
            for w in self.wires.values()
            if w.dst_node == node_id and (port is None or w.dst_port == port)
        ]

    def wires_out_of(self, node_id, port=None):
        return [
            w
            for w in self.wires.values()
            if w.src_node == node_id and (port is None or w.src_port == port)
        ]

    # --------------------------------------------------------------- selection
    def select(self, node_ids, mode="replace"):
        node_ids = [n for n in node_ids if n in self.nodes]
        if mode == "replace":
            new = list(OrderedDict.fromkeys(node_ids))
        elif mode == "add":
            new = list(OrderedDict.fromkeys(self.selection + node_ids))
        elif mode == "toggle":
            new = list(self.selection)
            for n in node_ids:
                if n in new:
                    new.remove(n)
                else:
                    new.append(n)
        elif mode == "remove":
            new = [n for n in self.selection if n not in node_ids]
        else:
            raise GraphError("unknown selection mode %r" % (mode,))
        if new != self.selection:
            self.selection = new
            self._emit("selection_changed", selection=list(new))
        return list(self.selection)

    # -------------------------------------------------------------- evaluation
    def _mark_dirty(self, node_id):
        todo = deque([node_id])
        while todo:
            current = todo.popleft()
            node = self.nodes.get(current)
            if node is None or (node.dirty and current != node_id):
                continue
            node.dirty = True
            for w in self.wires.values():
                if w.src_node == current:
                    todo.append(w.dst_node)

    def topological_order(self):
        indeg = {n: 0 for n in self.nodes}
        for w in self.wires.values():
            indeg[w.dst_node] += 1
        ready = deque(n for n, d in indeg.items() if d == 0)
        order = []
        while ready:
            n = ready.popleft()
            order.append(n)
            for w in self.wires.values():
                if w.src_node == n:
                    indeg[w.dst_node] -= 1
                    if indeg[w.dst_node] == 0:
                        ready.append(w.dst_node)
        if len(order) != len(self.nodes):
            raise GraphError("graph contains a cycle")
        return order

    def gather_inputs(self, node):
        """Resolve every input of ``node`` to a value."""
        ntype = self.node_type(node)
        result = {}
        for spec in ntype.inputs:
            wires = self.wires_into(node.id, spec.name)
            if wires:
                values = []
                for w in wires:
                    src = self.nodes[w.src_node]
                    values.append(src.outputs.get(w.src_port))
                result[spec.name] = values if spec.multi else values[0]
            elif spec.name in node.values:
                result[spec.name] = node.values[spec.name]
            else:
                result[spec.name] = copy.deepcopy(spec.default)
        return result

    def evaluate(self, force=False):
        """Evaluate dirty nodes in dependency order.  Returns evaluated ids."""
        import time

        evaluated = []
        for node_id in self.topological_order():
            node = self.nodes[node_id]
            if not (force or node.dirty):
                continue
            ntype = self.node_type(node)
            inputs = self.gather_inputs(node)
            started = time.perf_counter()
            try:
                outputs = _call_node(self, ntype, node, inputs)
                node.outputs = outputs
                node.error = None
            except Exception as exc:  # noqa: BLE001 - user node code may fail in any way
                node.outputs = {spec.name: None for spec in ntype.outputs}
                node.error = "%s: %s" % (type(exc).__name__, exc)
            node.eval_ms = (time.perf_counter() - started) * 1000.0
            node.dirty = False
            evaluated.append(node_id)
            # downstream nodes must recompute even if they were clean
            for w in self.wires_out_of(node_id):
                self.nodes[w.dst_node].dirty = True
        if evaluated:
            self._emit("evaluated", nodes=list(evaluated))
        return evaluated

    def errors(self):
        return {n.id: n.error for n in self.nodes.values() if n.error}

    # ----------------------------------------------------------- serialisation
    def to_dict(self, include_runtime=False):
        data = {
            "format": "freecad-grasshopper-mode",
            "version": self.FORMAT_VERSION,
            "canvas": {"id": self.canvas_id, "name": self.name},
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "wires": [w.to_dict() for w in self.wires.values()],
            "selection": list(self.selection),
        }
        if include_runtime:
            data["errors"] = self.errors()
        return data

    def to_json(self, **kwargs):
        return json.dumps(self.to_dict(), **kwargs)

    def _load_dict(self, data):
        self.nodes = OrderedDict()
        self.wires = OrderedDict()
        self.selection = []
        canvas = data.get("canvas") or {}
        self.canvas_id = canvas.get("id", self.canvas_id)
        self.name = canvas.get("name", self.name)
        with self.batch():
            for nd in data.get("nodes", []):
                if nd["type"] not in self.registry:
                    # keep unknown nodes out rather than failing the whole load
                    continue
                self.add_node(
                    nd["type"],
                    nd.get("x", 0),
                    nd.get("y", 0),
                    nd.get("values"),
                    nd.get("params"),
                    node_id=nd["id"],
                )
            for wd in data.get("wires", []):
                try:
                    self.connect(
                        wd["src"][0], wd["src"][1], wd["dst"][0], wd["dst"][1], wire_id=wd.get("id")
                    )
                except (GraphError, KeyError):
                    continue
            self.selection = [n for n in data.get("selection", []) if n in self.nodes]

    @classmethod
    def from_dict(cls, data, registry, backend=None):
        graph = cls(registry, backend)
        graph._load_dict(data)
        return graph

    @classmethod
    def from_json(cls, text, registry, backend=None):
        return cls.from_dict(json.loads(text), registry, backend)

    def clear(self):
        self.nodes = OrderedDict()
        self.wires = OrderedDict()
        self.selection = []
        self._emit("cleared")

    # --------------------------------------------------------------- utilities
    def duplicate_nodes(self, node_ids, dx=30.0, dy=30.0):
        """Duplicate nodes and the wires between them.  Returns new ids."""
        mapping = {}
        with self.batch():
            for nid in node_ids:
                node = self.nodes.get(nid)
                if node is None:
                    continue
                clone = self.add_node(
                    node.type_id,
                    node.x + dx,
                    node.y + dy,
                    copy.deepcopy(node.values),
                    copy.deepcopy(node.params),
                )
                mapping[nid] = clone.id
            for w in list(self.wires.values()):
                if w.src_node in mapping and w.dst_node in mapping:
                    self.connect(mapping[w.src_node], w.src_port, mapping[w.dst_node], w.dst_port)
        return list(mapping.values())

    def bounds(self):
        if not self.nodes:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [n.x for n in self.nodes.values()]
        ys = [n.y for n in self.nodes.values()]
        return (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------------- helpers
def coerce(kind, value):
    """Coerce a literal to a port kind.  Lists are coerced element-wise."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and kind not in ("list", "any", "point", "vector"):
        return [coerce(kind, v) for v in value]
    if kind == "number":
        return float(value)
    if kind == "int":
        return int(round(float(value)))
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind == "text":
        return str(value)
    if kind in ("point", "vector"):
        from .geometry import Vec3

        return Vec3.coerce(value)
    return value


def _is_listy(value):
    return isinstance(value, (list, tuple))


def _call_node(graph, ntype, node, inputs):
    ctx = EvalContext(graph, graph.backend, node)
    if ntype.func is None:
        # pass-through: match by name, else positionally
        names = [s.name for s in ntype.inputs]
        return {
            spec.name: (
                inputs.get(spec.name)
                if spec.name in inputs
                else (inputs.get(names[i]) if i < len(names) else None)
            )
            for i, spec in enumerate(ntype.outputs)
        }
    scalar_ports = [s for s in ntype.inputs if s.kind not in ("list", "any") and not s.multi]
    list_inputs = {s.name: inputs[s.name] for s in scalar_ports if _is_listy(inputs.get(s.name))}
    if ntype.elementwise and list_inputs:
        # Grasshopper style "longest list" matching: shorter lists repeat
        # their last item.
        length = max(len(v) for v in list_inputs.values())
        per_output = {spec.name: [] for spec in ntype.outputs}
        for i in range(length):
            call_inputs = dict(inputs)
            for name, values in list_inputs.items():
                call_inputs[name] = values[min(i, len(values) - 1)] if values else None
            out = _normalise_outputs(ntype, ntype.func(ctx, **call_inputs))
            for k, v in out.items():
                per_output[k].append(v)
        return per_output
    return _normalise_outputs(ntype, ntype.func(ctx, **inputs))


def _normalise_outputs(ntype, result):
    if isinstance(result, dict):
        return {spec.name: result.get(spec.name) for spec in ntype.outputs}
    if len(ntype.outputs) == 1:
        return {ntype.outputs[0].name: result}
    if isinstance(result, (list, tuple)) and len(result) == len(ntype.outputs):
        return {spec.name: value for spec, value in zip(ntype.outputs, result)}
    raise GraphError(
        "node %s returned %r for outputs %s"
        % (ntype.type_id, result, [s.name for s in ntype.outputs])
    )


def flatten(value):
    """Flatten nested lists (used by list nodes and preview collection)."""
    if _is_listy(value):
        return list(itertools.chain.from_iterable(flatten(v) for v in value))
    return [value]
