# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD document objects for Grasshopper mode.

A *definition* is a ``Part::FeaturePython`` whose ``Definition`` property
holds the graph as JSON.  Its ``Shape`` is the compound of everything wired
into Preview nodes, so the parametric result shows in the 3D view and
survives save/load.  *Baking* turns the geometry reaching Bake nodes into
ordinary ``Part::Feature`` objects.
"""

import json

import FreeCAD

from . import geometry, graph as ghgraph, nodes, resources

TYPE_NAME = "Grasshopper::Definition"


def _registry():
    return nodes.default_registry()


class GrasshopperDefinition:
    """Proxy of the definition object."""

    def __init__(self, obj, definition_json=None):
        self.Type = TYPE_NAME
        self._graph = None
        self._saving = False
        self._listener = None
        obj.Proxy = self
        if not hasattr(obj, "Definition"):
            obj.addProperty(
                "App::PropertyString", "Definition", "Grasshopper", "Graph definition (JSON)"
            )
            obj.addProperty(
                "App::PropertyString",
                "CanvasId",
                "Grasshopper",
                "Canvas id printed on the XR marker",
            )
            obj.addProperty(
                "App::PropertyBool",
                "AutoRecompute",
                "Grasshopper",
                "Recompute the document when the graph changes",
            )
            obj.addProperty(
                "App::PropertyInteger", "NodeCount", "Grasshopper", "Number of nodes (read only)"
            )
            obj.addProperty(
                "App::PropertyStringList",
                "Errors",
                "Grasshopper",
                "Node errors of the last evaluation",
            )
            obj.setEditorMode("NodeCount", 1)
            obj.setEditorMode("Errors", 1)
            obj.AutoRecompute = True
        if definition_json:
            obj.Definition = definition_json
        if not obj.Definition:
            g = ghgraph.Graph(_registry(), geometry.default_backend())
            obj.Definition = g.to_json()
        try:
            obj.CanvasId = json.loads(obj.Definition).get("canvas", {}).get("id", "")
        except ValueError:
            pass

    # ------------------------------------------------------------ graph
    def graph(self, obj):
        """The live :class:`Graph` (built lazily from the JSON)."""
        if self._graph is None:
            backend = geometry.default_backend()
            try:
                self._graph = ghgraph.Graph.from_json(obj.Definition or "{}", _registry(), backend)
            except ValueError:
                self._graph = ghgraph.Graph(_registry(), backend)
            self._graph.name = obj.Label
            self._listener = self._make_listener(obj)
            self._graph.on(self._listener)
            self._graph.evaluate()
        return self._graph

    def _make_listener(self, obj):
        structural = {
            "node_added",
            "node_removed",
            "node_moved",
            "value_changed",
            "param_changed",
            "wire_added",
            "wire_removed",
            "restored",
            "cleared",
        }
        doc_name, obj_name = obj.Document.Name, obj.Name

        def listener(event, payload):
            if event not in structural:
                return
            doc = (
                FreeCAD.getDocument(doc_name)
                if doc_name in [d.Name for d in FreeCAD.listDocuments().values()]
                else None
            )
            target = doc.getObject(obj_name) if doc else None
            if target is None:
                return
            self.save(target)

        return listener

    def save(self, obj):
        """Write the graph JSON back into the property."""
        if self._graph is None or self._saving:
            return
        self._saving = True
        try:
            text = self._graph.to_json()
            if obj.Definition != text:
                obj.Definition = text
            obj.NodeCount = len(self._graph.nodes)
            if obj.AutoRecompute:
                obj.touch()
        finally:
            self._saving = False

    def onChanged(self, obj, prop):
        if prop == "Definition" and not self._saving and self._graph is not None:
            # changed from outside (undo, property editor, file load): rebuild
            if self._listener:
                self._graph.off(self._listener)
            self._graph = None
        if prop == "Label" and self._graph is not None:
            self._graph.name = obj.Label

    def execute(self, obj):
        g = self.graph(obj)
        g.evaluate()
        backend = g.backend
        shapes = []
        for node in g.nodes.values():
            if node.type_id == "out.preview":
                for shape in ghgraph.flatten(node.outputs.get("geometry")):
                    if shape is not None and backend.is_shape(shape):
                        shapes.append(shape)
        errors = [
            "%s: %s" % (g.nodes[n].params.get("label") or g.node_type(g.nodes[n]).label, e)
            for n, e in g.errors().items()
        ]
        obj.Errors = errors
        obj.NodeCount = len(g.nodes)
        if getattr(backend, "name", "") == "part":
            import Part

            obj.Shape = Part.makeCompound(shapes) if shapes else Part.Shape()
        for err in errors:
            FreeCAD.Console.PrintWarning("Grasshopper %s: %s\n" % (obj.Label, err))

    # ----------------------------------------------------- persistence
    def dumps(self):
        return {"Type": self.Type}

    def loads(self, state):
        self.Type = (state or {}).get("Type", TYPE_NAME)
        self._graph = None
        self._saving = False
        self._listener = None

    # legacy pickling names used by older FreeCAD versions
    __getstate__ = dumps
    __setstate__ = loads


class ViewProviderDefinition:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return resources.icon_path("GrasshopperWorkbench.svg")

    def doubleClicked(self, vobj):
        from . import commands

        commands.open_canvas(vobj.Object)
        return True

    def setupContextMenu(self, vobj, menu):
        from . import commands

        try:
            from PySide import QtGui
        except ImportError:  # pragma: no cover
            return
        action = menu.addAction("Open Grasshopper canvas")
        action.triggered.connect(lambda: commands.open_canvas(vobj.Object))
        action2 = menu.addAction("Bake geometry")
        action2.triggered.connect(lambda: bake(vobj.Object))
        del QtGui

    def claimChildren(self):
        return []

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps
    __setstate__ = loads


# ------------------------------------------------------------- helpers
def is_definition(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None) == TYPE_NAME


def make_definition(doc, name="GrasshopperDefinition", definition_json=None, example=False):
    """Create a definition object in ``doc``."""
    obj = doc.addObject("Part::FeaturePython", name)
    proxy = GrasshopperDefinition(obj, definition_json)
    if example:
        g = proxy.graph(obj)
        nodes.example_graph(g)
        proxy.save(obj)
    if FreeCAD.GuiUp:
        ViewProviderDefinition(obj.ViewObject)
    obj.touch()
    return obj


def graph_for(obj):
    if not is_definition(obj):
        raise TypeError("%s is not a Grasshopper definition" % obj.Name)
    return obj.Proxy.graph(obj)


def definition_json(obj):
    graph_for(obj)
    return obj.Proxy._graph.to_json(indent=1)


def definitions(doc=None):
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        return []
    return [o for o in doc.Objects if is_definition(o)]


def bake(obj):
    """Create Part::Feature objects from every Bake node.  Returns them."""
    g = graph_for(obj)
    g.evaluate()
    backend = g.backend
    if getattr(backend, "name", "") != "part":
        raise RuntimeError("baking needs the Part workbench")
    import Part

    doc = obj.Document
    created = []
    for node in g.nodes.values():
        if node.type_id != "out.bake":
            continue
        label = node.params.get("label") or "Baked"
        shapes = [
            s
            for s in ghgraph.flatten(node.outputs.get("geometry"))
            if s is not None and backend.is_shape(s)
        ]
        for i, shape in enumerate(shapes):
            feature = doc.addObject("Part::Feature", "Baked")
            feature.Label = label if len(shapes) == 1 else "%s %d" % (label, i + 1)
            feature.Shape = shape
            created.append(feature)
    if not created:
        FreeCAD.Console.PrintWarning("Nothing reaches a Bake node in %s\n" % obj.Label)
    doc.recompute()
    return created
