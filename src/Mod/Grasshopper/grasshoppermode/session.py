# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bridges a :class:`~grasshoppermode.graph.Graph` and XR clients.

The session is transport independent: the server hands it decoded messages
via :meth:`Session.handle` and collects outgoing messages through the
``send``/``broadcast`` callbacks.  The Qt canvas listens to the same graph
events, so edits made on the table appear on the desktop and vice versa.
"""

import time

import os
import tempfile

from . import layout, media, protocol
from .geometry import Vec3
from .graph import GraphError, flatten
from .markers import DEFAULT_MARKER_MM, DEFAULT_SCALE_MM, MarkerSpec

PREVIEW_TOLERANCE = 0.5


def _fmt_value(backend, value, depth=0):
    """Short display string of an output value for the node card."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return "%.4g" % value
    if isinstance(value, str):
        return value if len(value) < 40 else value[:37] + "..."
    if isinstance(value, Vec3):
        return "(%.3g, %.3g, %.3g)" % (value.x, value.y, value.z)
    if isinstance(value, dict):
        return "{%s}" % ", ".join(
            "%s: %s" % (k, _fmt_value(backend, v, depth + 1)) for k, v in list(value.items())[:4]
        )
    if isinstance(value, (list, tuple)):
        items = flatten(value)
        if not items:
            return "[]"
        head = _fmt_value(backend, items[0], depth + 1)
        return "[%d] %s" % (len(items), head) if len(items) > 1 else "[1] %s" % head
    if backend is not None and backend.is_shape(value):
        return backend.describe(value)
    return type(value).__name__


class Session:
    """One canvas shared by any number of clients."""

    def __init__(
        self,
        graph,
        base_url="",
        marker_mm=DEFAULT_MARKER_MM,
        scale_mm=DEFAULT_SCALE_MM,
        profile=protocol.DEFAULT_PROFILE,
        autosave=None,
        media_dir=None,
        export_hook=None,
    ):
        self.graph = graph
        self.media = media.MediaStore(
            media_dir or os.path.join(tempfile.gettempdir(), "grasshopper-media"), graph.canvas_id
        )
        # export_hook(path, node) is called after a picture export (e.g. to hand it to Vizcom)
        self.export_hook = export_hook
        self.base_url = base_url
        self.marker_mm = marker_mm
        self.scale_mm = scale_mm
        self.profile = profile
        self.autosave = autosave  # callable(graph) invoked after edits
        self.clients = {}  # client_id -> dict(send=callable, caps=dict)
        self.anchors = {}  # client_id -> anchor info reported by headsets
        self._drag = {}  # (client_id, node_id) -> start position
        self._preview_cache = {}
        self._suspend_events = 0
        self.graph.on(self._on_graph_event)
        self.log = []

    # ------------------------------------------------------------ clients
    def add_client(self, client_id, send):
        self.clients[client_id] = {"send": send, "caps": {}, "joined": time.time()}
        send(self.hello_message())
        send(self.graph_message())
        send(self.preview_message())
        self.broadcast(protocol.message("clients", count=len(self.clients)))

    def remove_client(self, client_id):
        self.clients.pop(client_id, None)
        self.anchors.pop(client_id, None)
        for key in [k for k in self._drag if k[0] == client_id]:
            del self._drag[key]
        self.broadcast(protocol.message("clients", count=len(self.clients)))

    def send(self, client_id, msg):
        client = self.clients.get(client_id)
        if client:
            client["send"](msg)

    def broadcast(self, msg, exclude=None):
        for cid, client in list(self.clients.items()):
            if cid != exclude:
                client["send"](msg)

    # ----------------------------------------------------------- snapshots
    def marker_spec(self, index=0):
        return MarkerSpec(
            self.graph.canvas_id, self.marker_mm, self.base_url, self.scale_mm, marker_index=index
        )

    def hello_message(self):
        return protocol.message(
            "hello",
            protocol=protocol.PROTOCOL_VERSION,
            canvas=self.graph.canvas_id,
            name=self.graph.name,
            marker=self.marker_spec().to_dict(),
            profile=self.profile,
            profiles=protocol.INTERACTION_PROFILES,
            layout={
                "node_width": layout.NODE_WIDTH,
                "header": layout.HEADER_HEIGHT,
                "port_row": layout.PORT_ROW,
                "port_radius": layout.PORT_RADIUS,
                "padding": layout.PADDING,
                "widget_height": layout.WIDGET_HEIGHT,
            },
            node_types=[t.to_dict() for t in self.graph.registry],
            backend=getattr(self.graph.backend, "name", "none"),
        )

    def node_snapshot(self, node):
        g = self.graph
        ntype = g.node_type(node)
        x, y, w, h = layout.node_rect(node, ntype)
        wr = layout.widget_rect(node, ntype)
        return {
            "id": node.id,
            "type": node.type_id,
            "label": node.params.get("label") or ntype.label,
            "category": ntype.category,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "inputs": [
                {
                    "name": n,
                    "x": px,
                    "y": py,
                    "kind": k,
                    "connected": bool(g.wires_into(node.id, n)),
                    "value": _fmt_value(g.backend, node.values.get(n, ntype.input(n).default)),
                }
                for n, px, py, k in layout.input_ports(node, ntype)
            ],
            "outputs": [
                {
                    "name": n,
                    "x": px,
                    "y": py,
                    "kind": k,
                    "value": _fmt_value(g.backend, node.outputs.get(n)),
                }
                for n, px, py, k in layout.output_ports(node, ntype)
            ],
            "widget": ntype.widget,
            "widget_rect": list(wr) if wr else None,
            "values": {
                k: (v.to_json() if isinstance(v, Vec3) else v)
                for k, v in node.values.items()
                if not isinstance(v, (list, dict))
            },
            "params": {
                k: v
                for k, v in node.params.items()
                if isinstance(v, (int, float, str, bool)) or k in ("strokes", "pose")
            },
            "error": node.error,
            "selected": node.id in g.selection,
            "text": node.outputs.get("text") if ntype.widget == "panel" else None,
        }

    def graph_message(self):
        g = self.graph
        wires = []
        for w in g.wires.values():
            path = layout.wire_path(g, w)
            wires.append(
                {
                    "id": w.id,
                    "src": [w.src_node, w.src_port],
                    "dst": [w.dst_node, w.dst_port],
                    "path": path,
                    "kind": g.node_type(g.nodes[w.src_node]).output(w.src_port).kind,
                }
            )
        return protocol.message(
            "graph",
            canvas=g.canvas_id,
            revision=g.revision,
            nodes=[self.node_snapshot(n) for n in g.nodes.values()],
            wires=wires,
            selection=list(g.selection),
        )

    def preview_message(self, node_ids=None):
        """Tessellate everything reaching preview nodes."""
        g = self.graph
        backend = g.backend
        items = []
        for node in g.nodes.values():
            if node.type_id not in ("out.preview", "out.bake"):
                continue
            if node_ids is not None and node.id not in node_ids:
                continue
            color = node.params.get("color", "#3b9ddd")
            for i, shape in enumerate(flatten(node.outputs.get("geometry"))):
                if shape is None or backend is None or not backend.is_shape(shape):
                    continue
                key = (node.id, i)
                cached = self._preview_cache.get(key)
                if cached is not None and cached[0] is shape:
                    items.append(cached[1])
                    continue
                try:
                    verts, faces = backend.tessellate(shape, PREVIEW_TOLERANCE)
                    lines = backend.edges(shape, PREVIEW_TOLERANCE)
                except Exception as exc:  # noqa: BLE001
                    self.log.append("tessellate failed: %s" % exc)
                    continue
                item = {
                    "node": node.id,
                    "index": i,
                    "color": color,
                    "vertices": [c for v in verts for c in v],
                    "faces": [c for f in faces for c in f],
                    "lines": [[c for p in line for c in p] for line in lines],
                    "bbox": list(backend.bbox(shape)),
                }
                self._preview_cache[key] = (shape, item)
                items.append(item)
        return protocol.message("preview", canvas=g.canvas_id, items=items)

    # -------------------------------------------------------------- events
    def _on_graph_event(self, event, payload):
        if self._suspend_events:
            return
        if event in (
            "node_added",
            "node_removed",
            "wire_added",
            "wire_removed",
            "restored",
            "cleared",
            "param_changed",
        ):
            self.broadcast(self.graph_message())
        elif event == "node_moved":
            node = self.graph.nodes.get(payload["node"])
            if node:
                self.broadcast(
                    protocol.message(
                        "patch",
                        op="node",
                        node=self.node_snapshot(node),
                        wires=self._wires_of(node.id),
                    )
                )
        elif event == "value_changed":
            node = self.graph.nodes.get(payload["node"])
            if node:
                self.broadcast(
                    protocol.message("patch", op="node", node=self.node_snapshot(node), wires=[])
                )
        elif event == "evaluated":
            nodes = [
                self.node_snapshot(self.graph.nodes[n])
                for n in payload["nodes"]
                if n in self.graph.nodes
            ]
            self.broadcast(
                protocol.message("patch", op="values", nodes=nodes, errors=self.graph.errors())
            )
            self.broadcast(self.preview_message())
        elif event == "selection_changed":
            self.broadcast(protocol.message("selection", selection=payload["selection"]))

    def _wires_of(self, node_id):
        g = self.graph
        out = []
        for w in g.wires.values():
            if node_id in (w.src_node, w.dst_node):
                out.append({"id": w.id, "path": layout.wire_path(g, w)})
        return out

    # ------------------------------------------------------------- intents
    def handle(self, client_id, msg):
        """Handle one decoded client message.  Returns the ack message."""
        t = msg.get("t")
        rid = msg.get("rid")
        handler = getattr(self, "on_" + str(t), None)
        if handler is None:
            return protocol.ack(rid, False, "unknown message type %r" % (t,))
        try:
            extra = handler(client_id, msg) or {}
            if self.autosave and t not in (
                "hello",
                "log",
                "pose",
                "anchor",
                "select",
                "tap",
                "pan",
                "zoom",
                "fit",
                "get_graph",
            ):
                self.autosave(self.graph)
            return protocol.ack(rid, True, **extra)
        except (GraphError, KeyError, ValueError, TypeError) as exc:
            return protocol.ack(rid, False, exc)

    def on_hello(self, client_id, msg):
        client = self.clients.get(client_id)
        if client is not None:
            client["caps"] = dict(msg.get("caps") or {})
            if msg.get("profile") in protocol.INTERACTION_PROFILES:
                client["profile"] = msg["profile"]
        return {"clients": len(self.clients)}

    def on_tap(self, client_id, msg):
        """A tap on the table at canvas (x, y)."""
        g = self.graph
        hit = layout.hit_test(g, float(msg["x"]), float(msg["y"]))
        mode = msg.get("mode", "replace")
        if hit["kind"] in ("node", "header", "widget"):
            if hit["kind"] == "widget" and hit.get("widget") == "toggle":
                node = g.nodes[hit["node"]]
                g.set_value(node.id, "value", not bool(node.values.get("value", True)))
                g.evaluate()
            elif hit["kind"] == "widget" and hit.get("widget") == "slider":
                node = g.nodes[hit["node"]]
                value = layout.slider_value_at(node, g.node_type(node), float(msg["x"]))
                if value is not None:
                    g.set_value(node.id, "value", value)
                    g.evaluate()
            g.select([hit["node"]], "toggle" if mode == "toggle" else mode)
        elif hit["kind"] == "wire":
            if msg.get("delete"):
                g.push_undo()
                g.disconnect(hit["wire"])
                g.evaluate()
        elif hit["kind"] == "port":
            pass  # ports react to drags, not taps
        else:
            if mode == "replace":
                g.select([], "replace")
        return {"hit": hit}

    def on_select(self, client_id, msg):
        ids = list(msg.get("ids") or [])
        if "rect" in msg and msg["rect"]:
            x, y, w, h = [float(v) for v in msg["rect"]]
            ids += layout.nodes_in_rect(self.graph, x, y, w, h)
        return {"selection": self.graph.select(ids, msg.get("mode", "replace"))}

    def on_move(self, client_id, msg):
        """Drag of one node (or the selection).  ``phase``: begin/update/end."""
        g = self.graph
        node_id = msg["node"]
        if node_id not in g.nodes:
            raise KeyError("unknown node %s" % node_id)
        phase = msg.get("phase", "update")
        x, y = float(msg["x"]), float(msg["y"])
        key = (client_id, node_id)
        if phase == "begin":
            g.push_undo()
            node = g.nodes[node_id]
            group = list(g.selection) if node_id in g.selection else [node_id]
            self._drag[key] = {
                "origin": (x, y),
                "nodes": {n: (g.nodes[n].x, g.nodes[n].y) for n in group},
            }
            return {}
        drag = self._drag.get(key)
        if drag is None:
            # allow a bare update: treat as begin+update from the node position
            node = g.nodes[node_id]
            drag = {"origin": (node.x, node.y), "nodes": {node_id: (node.x, node.y)}}
            self._drag[key] = drag
        dx, dy = x - drag["origin"][0], y - drag["origin"][1]
        with g.batch():
            for n, (ox, oy) in drag["nodes"].items():
                if n in g.nodes:
                    g.move_node(n, ox + dx, oy + dy)
        if phase == "end":
            self._drag.pop(key, None)
        return {}

    def on_wire(self, client_id, msg):
        g = self.graph
        src, dst = msg["src"], msg["dst"]
        ok, reason = g.can_connect(src[0], src[1], dst[0], dst[1])
        if not ok:
            raise GraphError(reason)
        g.push_undo()
        wire = g.connect(src[0], src[1], dst[0], dst[1])
        g.evaluate()
        return {"wire": wire.id}

    def on_disconnect(self, client_id, msg):
        g = self.graph
        g.push_undo()
        if "wire" in msg:
            g.disconnect(msg["wire"])
        elif "dst" in msg:
            for w in g.wires_into(msg["dst"][0], msg["dst"][1]):
                g.disconnect(w.id)
        g.evaluate()
        return {}

    def on_set_value(self, client_id, msg):
        g = self.graph
        node_id, port = msg["node"], msg.get("port", "value")
        if msg.get("phase", "end") == "begin":
            g.push_undo()
        value = g.set_value(node_id, port, msg["value"])
        g.evaluate()
        return {"value": value.to_json() if isinstance(value, Vec3) else value}

    def on_set_param(self, client_id, msg):
        g = self.graph
        g.push_undo()
        g.set_param(msg["node"], msg["key"], msg["value"])
        g.evaluate()
        return {}

    def on_add_node(self, client_id, msg):
        g = self.graph
        g.push_undo()
        node = g.add_node(
            msg["type"],
            float(msg.get("x", 0)),
            float(msg.get("y", 0)),
            msg.get("values"),
            msg.get("params"),
        )
        # connect straight away when the node was dropped from a port drag
        if msg.get("from"):
            src = msg["from"]
            ntype = g.node_type(node)
            out_kind = g.node_type(g.nodes[src[0]]).output(src[1]).kind
            for spec in ntype.inputs:
                if g.can_connect(src[0], src[1], node.id, spec.name)[0]:
                    g.connect(src[0], src[1], node.id, spec.name)
                    break
            del out_kind
        g.evaluate()
        g.select([node.id])
        return {"node": node.id}

    def on_delete(self, client_id, msg):
        g = self.graph
        ids = list(msg.get("nodes") or [])
        if msg.get("selection"):
            ids += list(g.selection)
        wires = list(msg.get("wires") or [])
        if not ids and not wires:
            return {}
        g.push_undo()
        with g.batch():
            for w in wires:
                g.disconnect(w)
            for n in ids:
                g.remove_node(n)
        g.evaluate()
        return {"deleted": ids}

    def on_duplicate(self, client_id, msg):
        g = self.graph
        ids = list(msg.get("nodes") or g.selection)
        if not ids:
            return {}
        g.push_undo()
        new_ids = g.duplicate_nodes(ids)
        g.evaluate()
        g.select(new_ids)
        return {"nodes": new_ids}

    def on_undo(self, client_id, msg):
        ok = self.graph.undo()
        self.graph.evaluate(force=True)
        return {"undone": ok}

    def on_redo(self, client_id, msg):
        ok = self.graph.redo()
        self.graph.evaluate(force=True)
        return {"redone": ok}

    def on_anchor(self, client_id, msg):
        """Headset tells us how/where it anchored the canvas."""
        self.anchors[client_id] = {
            "method": msg.get("method"),  # image | plane | stylus3 | manual | persisted
            "frame": msg.get("frame"),
            "marker": msg.get("marker"),
            "uuid": msg.get("uuid"),
            "time": time.time(),
        }
        return {}

    def on_pose(self, client_id, msg):
        """Raw poses are only logged; the client does its own detection."""
        return {}

    def on_log(self, client_id, msg):
        self.log.append("[%s] %s" % (client_id, msg.get("text", "")))
        del self.log[:-200]
        return {}

    def on_get_graph(self, client_id, msg):
        self.send(client_id, self.graph_message())
        self.send(client_id, self.preview_message())
        return {}

    # pictures ---------------------------------------------------------
    def on_snapshot(self, client_id, msg):
        """A client took a picture of the preview (PNG data URL)."""
        g = self.graph
        mode = msg.get("mode", "rendered")
        name = self.media.save_png(msg["png"], mode)
        g.push_undo()
        x, y = msg.get("x"), msg.get("y")
        if x is None or y is None:
            # stack pictures below the graph
            bounds = g.bounds()
            x, y = bounds[0], bounds[3] + 200 + 300 * len(
                [n for n in g.nodes.values() if n.type_id == media.IMAGE_NODE]
            )
        node = media.add_image_node(
            g,
            self.media,
            name,
            mode,
            float(x),
            float(y),
            pose=msg.get("pose"),
            note=msg.get("note", ""),
        )
        g.evaluate()
        g.select([node.id])
        return {"node": node.id, "file": name, "url": self.media.url(name)}

    def on_stroke(self, client_id, msg):
        count = media.add_stroke(
            self.graph,
            msg["node"],
            msg["points"],
            msg.get("color", "#ff3b30"),
            msg.get("width", 3.0),
        )
        self.graph.evaluate()
        return {"strokes": count}

    def on_clear_strokes(self, client_id, msg):
        self.graph.push_undo()
        media.clear_strokes(self.graph, msg["node"])
        self.graph.evaluate()
        return {}

    def on_export_picture(self, client_id, msg):
        """Flattened PNG (rendered by the client) or an SVG bundle, then the export hook."""
        node = self.graph.nodes[msg["node"]]
        if node.type_id != media.IMAGE_NODE:
            raise ValueError("not a picture node")
        if msg.get("png"):
            name = os.path.splitext(node.params["file"])[0] + "-export.png"
            self.media.save_png(msg["png"], node.params.get("mode", "rendered"), name=name)
            path = self.media.path(name)
        else:
            path = media.export_bundle(self.media, node)
        result = {"path": path, "url": self.media.url(os.path.basename(path))}
        if self.export_hook:
            hook_result = self.export_hook(path, node)
            if hook_result:
                result["hook"] = str(hook_result)
        return result

    # companions (phone trackpad / IMU) steer the view of the other clients
    def on_pan(self, client_id, msg):
        self.broadcast(
            protocol.message(
                "view", op="pan", dx=float(msg.get("dx", 0)), dy=float(msg.get("dy", 0))
            ),
            exclude=client_id,
        )
        return {}

    def on_zoom(self, client_id, msg):
        self.broadcast(
            protocol.message("view", op="zoom", factor=float(msg.get("factor", 1.0))),
            exclude=client_id,
        )
        return {}

    def on_fit(self, client_id, msg):
        self.broadcast(protocol.message("view", op="fit"), exclude=client_id)
        return {}

    def on_set_profile(self, client_id, msg):
        profile = msg.get("profile")
        if profile not in protocol.INTERACTION_PROFILES:
            raise ValueError("unknown profile %r" % (profile,))
        self.profile = profile
        self.broadcast(protocol.message("profile", profile=profile))
        return {}
