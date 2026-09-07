# SPDX-License-Identifier: LGPL-2.1-or-later
"""Qt node editor for Grasshopper mode (the desktop side of the canvas).

The scene works in canvas units, one to one with :mod:`layout`, and routes
all mouse input through :func:`layout.hit_test` so a click on the desktop
and a stylus tap on the table resolve identically.  Every edit goes through
the shared :class:`~grasshoppermode.graph.Graph`, whose events keep the
scene, the FreeCAD document and any XR clients in sync.
"""

from PySide import QtCore, QtGui, QtWidgets

from . import layout
from .geometry import Vec3
from .graph import GraphError, flatten

CATEGORY_COLORS = {
    "Params": "#3d5a80",
    "Math": "#4a6fa5",
    "Sets": "#6b6b6b",
    "Vector": "#a44a3f",
    "Curve": "#2a9d8f",
    "Solid": "#3a7d44",
    "Transform": "#8a6d3b",
    "Boolean": "#6d4c8a",
    "Analyse": "#5a5a8a",
    "Output": "#b5651d",
}
KIND_COLORS = {
    "number": "#7fb3ff",
    "int": "#7fb3ff",
    "bool": "#c9b3ff",
    "text": "#ffd479",
    "point": "#ff9d9d",
    "vector": "#ff9d9d",
    "shape": "#8fe0a2",
    "list": "#dddddd",
    "any": "#bbbbbb",
}


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "%.4g" % value
    if isinstance(value, Vec3):
        return "(%.3g, %.3g, %.3g)" % (value.x, value.y, value.z)
    if isinstance(value, (list, tuple)):
        items = flatten(value)
        return "[%d]" % len(items) if items else "[]"
    text = str(value)
    return text if len(text) < 28 else text[:25] + "..."


class NodeItem(QtWidgets.QGraphicsItem):
    """Paints one node card.  Interaction is handled by the scene."""

    def __init__(self, scene, node):
        super().__init__()
        self.scene_ref = scene
        self.node = node
        self.setZValue(1)
        self.setPos(node.x, node.y)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.setAcceptHoverEvents(True)
        self.hot_port = None

    @property
    def ntype(self):
        return self.scene_ref.graph.node_type(self.node)

    def boundingRect(self):
        w, h = layout.node_size(self.node, self.ntype)
        r = layout.PORT_RADIUS + 2
        return QtCore.QRectF(-r, -2, w + 2 * r, h + 4)

    def paint(self, painter, option, widget=None):
        node, ntype, g = self.node, self.ntype, self.scene_ref.graph
        w, h = layout.node_size(node, ntype)
        selected = node.id in g.selection
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        body = QtGui.QColor("#ffd6d6" if node.error else "#f7f7f7")
        painter.setBrush(body)
        painter.setPen(
            QtGui.QPen(QtGui.QColor("#ff9900" if selected else "#333"), 3 if selected else 1)
        )
        painter.drawRoundedRect(QtCore.QRectF(0, 0, w, h), 6, 6)
        painter.setBrush(QtGui.QColor(CATEGORY_COLORS.get(ntype.category, "#555")))
        painter.setPen(QtCore.Qt.NoPen)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(0, 0, w, layout.HEADER_HEIGHT), 6, 6)
        path.addRect(QtCore.QRectF(0, layout.HEADER_HEIGHT / 2, w, layout.HEADER_HEIGHT / 2))
        painter.drawPath(path.simplified())
        painter.setPen(QtGui.QColor("#fff"))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(9.5)
        painter.setFont(font)
        painter.drawText(
            QtCore.QRectF(8, 0, w - 16, layout.HEADER_HEIGHT),
            QtCore.Qt.AlignVCenter,
            node.params.get("label") or ntype.label,
        )
        font.setBold(False)
        font.setPointSizeF(8)
        painter.setFont(font)
        for name, px, py, kind in layout.input_ports(node, ntype):
            x, y = px - node.x, py - node.y
            self._port(painter, x, y, kind, (name, "in") == self.hot_port)
            connected = bool(g.wires_into(node.id, name))
            text = (
                name
                if connected
                else "%s: %s" % (name, _fmt(node.values.get(name, ntype.input(name).default)))
            )
            painter.setPen(QtGui.QColor("#222"))
            painter.drawText(
                QtCore.QRectF(layout.PORT_RADIUS + 4, y - 9, w / 2 - 10, 18),
                QtCore.Qt.AlignVCenter,
                text,
            )
        for name, px, py, kind in layout.output_ports(node, ntype):
            x, y = px - node.x, py - node.y
            self._port(painter, x, y, kind, (name, "out") == self.hot_port)
            painter.setPen(QtGui.QColor("#222"))
            painter.drawText(
                QtCore.QRectF(w / 2, y - 9, w / 2 - layout.PORT_RADIUS - 4, 18),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                "%s = %s" % (name, _fmt(node.outputs.get(name))),
            )
        wr = layout.widget_rect(node, ntype)
        if wr:
            self._widget(painter, wr[0] - node.x, wr[1] - node.y, wr[2], wr[3])
        if node.error:
            painter.setPen(QtGui.QColor("#b00"))
            painter.drawText(
                QtCore.QRectF(5, h - 16, w - 10, 14), QtCore.Qt.AlignVCenter, node.error
            )

    def _port(self, painter, x, y, kind, hot):
        painter.setBrush(QtGui.QColor(KIND_COLORS.get(kind, "#bbb")))
        painter.setPen(QtGui.QPen(QtGui.QColor("#ff6a00" if hot else "#222"), 2 if hot else 1))
        r = layout.PORT_RADIUS
        painter.drawEllipse(QtCore.QRectF(x - r, y - r, 2 * r, 2 * r))

    def _widget(self, painter, x, y, w, h):
        node, ntype = self.node, self.ntype
        if ntype.widget == "slider":
            lo, hi = float(node.params.get("min", 0)), float(node.params.get("max", 10))
            v = float(node.values.get("value", lo) or 0)
            t = max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#d0d0d0"))
            painter.drawRoundedRect(QtCore.QRectF(x, y + h / 2 - 4, w, 8), 4, 4)
            painter.setBrush(QtGui.QColor("#4a90e2"))
            painter.drawRoundedRect(QtCore.QRectF(x, y + h / 2 - 4, max(8, w * t), 8), 4, 4)
            painter.setBrush(QtGui.QColor("#2c6fbf"))
            painter.drawEllipse(QtCore.QPointF(x + w * t, y + h / 2), 8, 8)
            painter.setPen(QtGui.QColor("#222"))
            painter.drawText(
                QtCore.QRectF(x, y + h / 2 + 6, w, 14), QtCore.Qt.AlignHCenter, "%.4g" % v
            )
        elif ntype.widget == "panel":
            painter.setBrush(QtGui.QColor("#fffbe6"))
            painter.setPen(QtGui.QColor("#999"))
            painter.drawRect(QtCore.QRectF(x, y, w, h))
            painter.setPen(QtGui.QColor("#222"))
            text = "\n".join(str(node.outputs.get("text") or "").split("\n")[:5])
            painter.drawText(
                QtCore.QRectF(x + 3, y + 2, w - 6, h - 4),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                text,
            )
        elif ntype.widget == "text":
            painter.setBrush(QtGui.QColor("#fff"))
            painter.setPen(QtGui.QColor("#999"))
            painter.drawRect(QtCore.QRectF(x, y, w, h))
            painter.setPen(QtGui.QColor("#222"))
            text = node.params.get("expression", node.values.get("value", ""))
            painter.drawText(QtCore.QRectF(x + 4, y, w - 8, h), QtCore.Qt.AlignVCenter, str(text))


class WireItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, scene, wire):
        super().__init__()
        self.scene_ref = scene
        self.wire = wire
        self.setZValue(0)
        g = scene.graph
        src = g.nodes[wire.src_node]
        kind = g.node_type(src).output(wire.src_port).kind
        self.setPen(QtGui.QPen(QtGui.QColor(KIND_COLORS.get(kind, "#333")).darker(160), 2))
        self.refresh()

    def refresh(self):
        path = layout.wire_path(self.scene_ref.graph, self.wire)
        if path:
            self.setPath(_bezier(*path))


def _bezier(x1, y1, x2, y2):
    dx = max(abs(x2 - x1) * 0.5, 30.0)
    p = QtGui.QPainterPath(QtCore.QPointF(x1, y1))
    p.cubicTo(x1 + dx, y1, x2 - dx, y2, x2, y2)
    return p


class CanvasScene(QtWidgets.QGraphicsScene):
    """Scene bound to a graph; all editing logic lives here."""

    graphEdited = QtCore.Signal()

    def __init__(self, graph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.items_by_node = {}
        self.items_by_wire = {}
        self.setSceneRect(-2000, -2000, 8000, 6000)
        self.setBackgroundBrush(QtGui.QColor("#e9ebef"))
        self._press = None
        self._temp_wire = None
        self._marquee = None
        self._eval_timer = QtCore.QTimer(self)
        self._eval_timer.setSingleShot(True)
        self._eval_timer.setInterval(40)
        self._eval_timer.timeout.connect(self._evaluate_now)
        self.graph.on(self._on_graph_event)
        self.rebuild()

    # ------------------------------------------------------------ sync
    def rebuild(self):
        for item in list(self.items_by_node.values()) + list(self.items_by_wire.values()):
            self.removeItem(item)
        self.items_by_node = {}
        self.items_by_wire = {}
        for node in self.graph.nodes.values():
            self._add_node_item(node)
        for wire in self.graph.wires.values():
            self._add_wire_item(wire)

    def _add_node_item(self, node):
        item = NodeItem(self, node)
        self.addItem(item)
        self.items_by_node[node.id] = item
        return item

    def _add_wire_item(self, wire):
        item = WireItem(self, wire)
        self.addItem(item)
        self.items_by_wire[wire.id] = item
        return item

    def _on_graph_event(self, event, payload):
        if event == "node_added":
            node = self.graph.nodes.get(payload["node"])
            if node and node.id not in self.items_by_node:
                self._add_node_item(node)
        elif event == "node_removed":
            item = self.items_by_node.pop(payload["node"], None)
            if item:
                self.removeItem(item)
        elif event == "node_moved":
            item = self.items_by_node.get(payload["node"])
            if item:
                item.setPos(payload["x"], payload["y"])
                for w in self.graph.wires.values():
                    if payload["node"] in (w.src_node, w.dst_node) and w.id in self.items_by_wire:
                        self.items_by_wire[w.id].refresh()
        elif event in ("value_changed", "param_changed"):
            item = self.items_by_node.get(payload["node"])
            if item:
                item.update()
        elif event == "evaluated":
            for nid in payload["nodes"]:
                item = self.items_by_node.get(nid)
                if item:
                    item.update()
        elif event == "wire_added":
            wire = self.graph.wires.get(payload["wire"])
            if wire and wire.id not in self.items_by_wire:
                self._add_wire_item(wire)
            for nid in (wire.src_node, wire.dst_node) if wire else ():
                if nid in self.items_by_node:
                    self.items_by_node[nid].update()
        elif event == "wire_removed":
            item = self.items_by_wire.pop(payload["wire"], None)
            if item:
                self.removeItem(item)
            self.update()
        elif event in ("restored", "cleared"):
            self.rebuild()
        elif event == "selection_changed":
            for item in self.items_by_node.values():
                item.update()
        if event != "evaluated":
            self.graphEdited.emit()

    def request_evaluate(self):
        self._eval_timer.start()

    def _evaluate_now(self):
        self.graph.evaluate()
        self.update()

    # --------------------------------------------------------- editing
    def add_node(self, type_id, x, y, from_port=None):
        g = self.graph
        g.push_undo()
        node = g.add_node(type_id, x, y)
        if from_port:
            for spec in g.node_type(node).inputs:
                if g.can_connect(from_port[0], from_port[1], node.id, spec.name)[0]:
                    g.connect(from_port[0], from_port[1], node.id, spec.name)
                    break
        g.select([node.id])
        self.request_evaluate()
        return node

    def delete_selection(self):
        g = self.graph
        if not g.selection:
            return
        g.push_undo()
        with g.batch():
            for nid in list(g.selection):
                g.remove_node(nid)
        self.request_evaluate()

    def duplicate_selection(self):
        g = self.graph
        if not g.selection:
            return
        g.push_undo()
        new_ids = g.duplicate_nodes(list(g.selection))
        g.select(new_ids)
        self.request_evaluate()

    def undo(self):
        if self.graph.undo():
            self.graph.evaluate(force=True)

    def redo(self):
        if self.graph.redo():
            self.graph.evaluate(force=True)

    # ----------------------------------------------------------- mouse
    def mousePressEvent(self, event):
        pos = event.scenePos()
        x, y = pos.x(), pos.y()
        g = self.graph
        hit = layout.hit_test(g, x, y)
        ctrl = bool(event.modifiers() & QtCore.Qt.ControlModifier)
        if event.button() == QtCore.Qt.RightButton:
            self._context_menu(event, hit)
            return
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press = {"hit": hit, "x": x, "y": y, "dragging": False}
        if hit["kind"] == "port":
            if hit["direction"] == "out":
                self._start_temp_wire(hit["node"], hit["port"], x, y, from_output=True)
            else:
                wires = g.wires_into(hit["node"], hit["port"])
                if wires:
                    g.push_undo()
                    w = wires[0]
                    g.disconnect(w.id)
                    self._start_temp_wire(w.src_node, w.src_port, x, y, from_output=True)
                    self.request_evaluate()
                else:
                    self._start_temp_wire(hit["node"], hit["port"], x, y, from_output=False)
        elif hit["kind"] in ("header", "node"):
            if hit["node"] not in g.selection:
                g.select([hit["node"]], "toggle" if ctrl else "replace")
            elif ctrl:
                g.select([hit["node"]], "toggle")
            self._press["start"] = {
                nid: (g.nodes[nid].x, g.nodes[nid].y) for nid in g.selection if nid in g.nodes
            }
        elif hit["kind"] == "widget":
            node = g.nodes[hit["node"]]
            ntype = g.node_type(node)
            if hit["widget"] == "slider":
                g.push_undo()
                self._slider_to(node, ntype, x)
            elif hit["widget"] == "toggle":
                g.push_undo()
                g.set_value(node.id, "value", not bool(node.values.get("value", True)))
                self.request_evaluate()
            elif hit["widget"] in ("text",):
                self._edit_text(node, ntype)
        elif hit["kind"] == "wire":
            if ctrl:
                g.push_undo()
                g.disconnect(hit["wire"])
                self.request_evaluate()
        else:
            if not ctrl:
                g.select([], "replace")
            self._marquee = QtWidgets.QGraphicsRectItem(QtCore.QRectF(x, y, 0, 0))
            self._marquee.setBrush(QtGui.QColor(60, 120, 255, 50))
            self._marquee.setPen(QtGui.QPen(QtGui.QColor(60, 120, 255), 1, QtCore.Qt.DashLine))
            self._marquee.setZValue(5)
            self.addItem(self._marquee)
            self._press["add"] = ctrl

    def _slider_to(self, node, ntype, x):
        value = layout.slider_value_at(node, ntype, x)
        if value is not None and value != node.values.get("value"):
            self.graph.set_value(node.id, "value", value)
            self.request_evaluate()

    def _start_temp_wire(self, node_id, port, x, y, from_output):
        self._temp_wire = QtWidgets.QGraphicsPathItem()
        self._temp_wire.setPen(QtGui.QPen(QtGui.QColor("#ff6a00"), 2, QtCore.Qt.DashLine))
        self._temp_wire.setZValue(4)
        self.addItem(self._temp_wire)
        self._press["wire"] = {
            "node": node_id,
            "port": port,
            "from_output": from_output,
            "x0": x,
            "y0": y,
        }

    def mouseMoveEvent(self, event):
        pos = event.scenePos()
        x, y = pos.x(), pos.y()
        pr = self._press
        if not pr:
            self._hover(x, y)
            super().mouseMoveEvent(event)
            return
        g = self.graph
        hit = pr["hit"]
        if "wire" in pr and self._temp_wire is not None:
            w = pr["wire"]
            self._temp_wire.setPath(
                _bezier(w["x0"], w["y0"], x, y)
                if w["from_output"]
                else _bezier(x, y, w["x0"], w["y0"])
            )
            self._hover(x, y)
        elif hit["kind"] in ("header", "node"):
            if not pr["dragging"] and abs(x - pr["x"]) + abs(y - pr["y"]) > 4:
                pr["dragging"] = True
                g.push_undo()
            if pr["dragging"]:
                dx, dy = x - pr["x"], y - pr["y"]
                with g.batch():
                    for nid, (ox, oy) in pr["start"].items():
                        if nid in g.nodes:
                            g.move_node(nid, ox + dx, oy + dy)
        elif hit["kind"] == "widget" and hit["widget"] == "slider":
            node = g.nodes.get(hit["node"])
            if node:
                self._slider_to(node, g.node_type(node), x)
        elif self._marquee is not None:
            self._marquee.setRect(
                QtCore.QRectF(QtCore.QPointF(pr["x"], pr["y"]), QtCore.QPointF(x, y)).normalized()
            )

    def _hover(self, x, y):
        hit = layout.hit_test(self.graph, x, y)
        for item in self.items_by_node.values():
            hot = (
                (hit["port"], hit["direction"])
                if hit["kind"] == "port" and hit["node"] == item.node.id
                else None
            )
            if hot != item.hot_port:
                item.hot_port = hot
                item.update()

    def mouseReleaseEvent(self, event):
        pos = event.scenePos()
        x, y = pos.x(), pos.y()
        pr, self._press = self._press, None
        g = self.graph
        if self._temp_wire is not None:
            self.removeItem(self._temp_wire)
            self._temp_wire = None
        if pr and "wire" in pr:
            w = pr["wire"]
            hit = layout.hit_test(g, x, y)
            try:
                if hit["kind"] == "port" and w["from_output"] and hit["direction"] == "in":
                    g.push_undo()
                    g.connect(w["node"], w["port"], hit["node"], hit["port"])
                elif hit["kind"] == "port" and not w["from_output"] and hit["direction"] == "out":
                    g.push_undo()
                    g.connect(hit["node"], hit["port"], w["node"], w["port"])
                elif hit["kind"] in ("node", "header") and w["from_output"]:
                    node = g.nodes[hit["node"]]
                    for spec in g.node_type(node).inputs:
                        if g.can_connect(w["node"], w["port"], node.id, spec.name)[0]:
                            g.push_undo()
                            g.connect(w["node"], w["port"], node.id, spec.name)
                            break
                elif (
                    hit["kind"] == "empty"
                    and w["from_output"]
                    and abs(x - w["x0"]) + abs(y - w["y0"]) > 30
                ):
                    self._palette(QtGui.QCursor.pos(), x, y, from_port=(w["node"], w["port"]))
                self.request_evaluate()
            except GraphError as exc:
                self.parent_status("Cannot connect: %s" % exc)
        elif self._marquee is not None:
            rect = self._marquee.rect()
            self.removeItem(self._marquee)
            self._marquee = None
            if rect.width() > 2 or rect.height() > 2:
                ids = layout.nodes_in_rect(g, rect.x(), rect.y(), rect.width(), rect.height())
                g.select(ids, "add" if pr and pr.get("add") else "replace")
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        pos = event.scenePos()
        x, y = pos.x(), pos.y()
        g = self.graph
        hit = layout.hit_test(g, x, y)
        if hit["kind"] == "empty":
            self._palette(QtGui.QCursor.pos(), x, y)
        elif hit["kind"] in ("node", "header", "widget"):
            node = g.nodes[hit["node"]]
            ntype = g.node_type(node)
            if ntype.widget == "slider":
                self._edit_slider(node)
            elif hit["kind"] == "node":
                self._edit_input(node, ntype, y)
            else:
                self._edit_label(node)

    def parent_status(self, text):
        view = self.views()[0] if self.views() else None
        widget = view.parent() if view else None
        if hasattr(widget, "status"):
            widget.status(text)

    # ------------------------------------------------------ dialogs
    def _palette(self, global_pos, x, y, from_port=None):
        menu = NodePaletteMenu(self.graph.registry)
        chosen = menu.pick(global_pos)
        if chosen:
            self.add_node(chosen, x, y, from_port)

    def _context_menu(self, event, hit):
        g = self.graph
        menu = QtWidgets.QMenu()
        pos = event.scenePos()
        add = menu.addAction("Add node...")
        if hit["kind"] in ("node", "header", "widget"):
            if hit["node"] not in g.selection:
                g.select([hit["node"]])
            dup = menu.addAction("Duplicate")
            delete = menu.addAction("Delete")
            unplug = menu.addAction("Disconnect all")
            label = menu.addAction("Rename...")
        else:
            dup = delete = unplug = label = None
        if hit["kind"] == "wire":
            cut = menu.addAction("Delete wire")
        else:
            cut = None
        menu.addSeparator()
        undo = menu.addAction("Undo")
        redo = menu.addAction("Redo")
        chosen = menu.exec_(event.screenPos())
        if chosen is None:
            return
        if chosen == add:
            self._palette(event.screenPos(), pos.x(), pos.y())
        elif chosen == dup:
            self.duplicate_selection()
        elif chosen == delete:
            self.delete_selection()
        elif chosen == unplug:
            g.push_undo()
            with g.batch():
                for w in list(g.wires.values()):
                    if w.src_node in g.selection or w.dst_node in g.selection:
                        g.disconnect(w.id)
            self.request_evaluate()
        elif chosen == label:
            self._edit_label(g.nodes[hit["node"]])
        elif chosen == cut:
            g.push_undo()
            g.disconnect(hit["wire"])
            self.request_evaluate()
        elif chosen == undo:
            self.undo()
        elif chosen == redo:
            self.redo()

    def _edit_label(self, node):
        text, ok = QtWidgets.QInputDialog.getText(
            None, "Node label", "Label:", text=node.params.get("label", "")
        )
        if ok:
            self.graph.push_undo()
            self.graph.set_param(node.id, "label", text)

    def _edit_slider(self, node):
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle("Slider settings")
        form = QtWidgets.QFormLayout(dlg)
        fields = {}
        for key in ("label", "min", "max", "step"):
            edit = QtWidgets.QLineEdit(str(node.params.get(key, "")))
            form.addRow(key, edit)
            fields[key] = edit
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.graph.push_undo()
            with self.graph.batch():
                self.graph.set_param(node.id, "label", fields["label"].text())
                for key in ("min", "max", "step"):
                    try:
                        self.graph.set_param(node.id, key, float(fields[key].text()))
                    except ValueError:
                        pass
            self.request_evaluate()

    def _edit_text(self, node, ntype):
        if "expression" in node.params:
            text, ok = QtWidgets.QInputDialog.getText(
                None,
                "Expression",
                "Expression in x, y, z:",
                text=str(node.params.get("expression", "x")),
            )
            if ok:
                self.graph.push_undo()
                self.graph.set_param(node.id, "expression", text)
                self.request_evaluate()
        else:
            text, ok = QtWidgets.QInputDialog.getText(
                None, "Value", "Value:", text=str(node.values.get("value", ""))
            )
            if ok:
                self.graph.push_undo()
                self.graph.set_value(node.id, "value", text)
                self.request_evaluate()

    def _edit_input(self, node, ntype, y):
        """Double-click on an input row edits its literal."""
        for name, px, py, kind in layout.input_ports(node, ntype):
            if abs(py - y) <= layout.PORT_ROW / 2 and not self.graph.wires_into(node.id, name):
                current = node.values.get(name, ntype.input(name).default)
                if isinstance(current, Vec3):
                    current = "%g, %g, %g" % (current.x, current.y, current.z)
                text, ok = QtWidgets.QInputDialog.getText(
                    None,
                    "Input %s" % name,
                    "%s (%s):" % (name, kind),
                    text=str(current if current is not None else ""),
                )
                if ok:
                    value = text
                    if kind in ("point", "vector"):
                        try:
                            value = [float(v) for v in text.replace(";", ",").split(",")]
                        except ValueError:
                            self.parent_status("Expected x, y, z")
                            return
                    try:
                        self.graph.push_undo()
                        self.graph.set_value(node.id, name, value)
                    except (ValueError, GraphError) as exc:
                        self.parent_status(str(exc))
                    self.request_evaluate()
                return


class NodePaletteMenu(QtWidgets.QMenu):
    """Category sub-menus plus a search box at the top."""

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.choice = None
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search nodes...")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._accept_first)
        action = QtWidgets.QWidgetAction(self)
        action.setDefaultWidget(self.search)
        self.addAction(action)
        self.result_actions = []
        self.category_menus = []
        for category, types in registry.categories().items():
            sub = self.addMenu(category)
            self.category_menus.append(sub)
            for t in types:
                a = sub.addAction(t.label)
                a.setData(t.type_id)
                a.setToolTip(t.description)
        self.triggered.connect(self._on_triggered)

    def _on_triggered(self, action):
        if action.data():
            self.choice = action.data()

    def _filter(self, text):
        for a in self.result_actions:
            self.removeAction(a)
        self.result_actions = []
        hits = self.registry.search(text)[:12] if text else []
        for t in hits:
            a = QtWidgets.QAction("%s  (%s)" % (t.label, t.category), self)
            a.setData(t.type_id)
            self.insertAction(
                self.category_menus[0].menuAction() if self.category_menus else None, a
            )
            self.result_actions.append(a)

    def _accept_first(self):
        if self.result_actions:
            self.choice = self.result_actions[0].data()
            self.close()

    def pick(self, global_pos):
        self.search.setFocus()
        self.exec_(global_pos)
        return self.choice


class CanvasView(QtWidgets.QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self._pan = None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton or (
            event.button() == QtCore.Qt.LeftButton and event.modifiers() & QtCore.Qt.AltModifier
        ):
            self._pan = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            d = event.pos() - self._pan
            self._pan = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan is not None:
            self._pan = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        scene = self.scene()
        key, mods = event.key(), event.modifiers()
        if key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            scene.delete_selection()
        elif key == QtCore.Qt.Key_Z and mods & QtCore.Qt.ControlModifier:
            scene.redo() if mods & QtCore.Qt.ShiftModifier else scene.undo()
        elif key == QtCore.Qt.Key_Y and mods & QtCore.Qt.ControlModifier:
            scene.redo()
        elif key == QtCore.Qt.Key_D and mods & QtCore.Qt.ControlModifier:
            scene.duplicate_selection()
        elif key == QtCore.Qt.Key_A and mods & QtCore.Qt.ControlModifier:
            scene.graph.select(list(scene.graph.nodes))
        elif key == QtCore.Qt.Key_F:
            self.fit_all()
        elif key == QtCore.Qt.Key_Space:
            pos = self.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            scene._palette(QtGui.QCursor.pos(), pos.x(), pos.y())
        else:
            super().keyPressEvent(event)

    def fit_all(self):
        items = list(self.scene().items_by_node.values())
        if not items:
            return
        rect = items[0].sceneBoundingRect()
        for item in items[1:]:
            rect = rect.united(item.sceneBoundingRect())
        self.fitInView(rect.adjusted(-40, -40, 40, 40), QtCore.Qt.KeepAspectRatio)


class CanvasWidget(QtWidgets.QWidget):
    """Toolbar + view for one definition object."""

    def __init__(self, graph, title="Grasshopper canvas", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.graph = graph
        self.scene = CanvasScene(graph, self)
        self.view = CanvasView(self.scene, self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QtWidgets.QToolBar()
        lay.addWidget(self.toolbar)
        lay.addWidget(self.view)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("padding: 2px 6px; color: #444;")
        lay.addWidget(self.status_label)
        self.toolbar.addAction(
            "Add node", lambda: self.scene._palette(QtGui.QCursor.pos(), *self._center())
        )
        self.toolbar.addAction("Fit", self.view.fit_all)
        self.toolbar.addAction("Recompute", lambda: self.graph.evaluate(force=True))
        self.toolbar.addAction("Undo", self.scene.undo)
        self.toolbar.addAction("Redo", self.scene.redo)
        self.toolbar.addAction("Delete", self.scene.delete_selection)
        self.toolbar.addSeparator()
        self.extra_actions = {}
        QtCore.QTimer.singleShot(0, self.view.fit_all)

    def _center(self):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        return c.x(), c.y()

    def add_action(self, key, text, callback, checkable=False):
        action = self.toolbar.addAction(text, callback)
        action.setCheckable(checkable)
        self.extra_actions[key] = action
        return action

    def status(self, text):
        self.status_label.setText(text)
