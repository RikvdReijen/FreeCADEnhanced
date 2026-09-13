# SPDX-License-Identifier: LGPL-2.1-or-later
"""A minimal stand-in for the FreeCAD and Part modules.

Enough of the API surface for the adapter to be exercised end to end: a
document with a PartDesign body, objects with properties, shapes with faces
and edges. It is installed into ``sys.modules`` by :func:`install` and
removed by :func:`uninstall`, so the rest of the suite never sees it.
"""

import sys
import types


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Rotation:
    def __init__(self, a=None, b=None):
        self.a, self.b = a, b


class Placement:
    def __init__(self, base=None, rotation=None):
        self.Base, self.Rotation = base, rotation


class Quantity:
    def __init__(self, value):
        self.Value = float(value)


class Surface:
    pass


class Plane(Surface):
    pass


class Cylinder(Surface):
    pass


class Line:
    pass


class Circle:
    def __init__(self, *args):
        self.args = args


class LineSegment:
    def __init__(self, *args):
        self.args = args


class Edge:
    def __init__(self, code, length, centroid):
        self._code = code
        self.Length = length
        self.CenterOfMass = Vector(*centroid)
        self.Curve = Line()

    def hashCode(self):
        return self._code


class Face:
    def __init__(self, surface, area, centroid, normal, edges):
        self.Surface = surface
        self.Area = area
        self.CenterOfMass = Vector(*centroid)
        self._normal = normal
        self.Edges = edges
        self.ParameterRange = (0.0, 1.0, 0.0, 1.0)

    def normalAt(self, u, v):
        return Vector(*self._normal)


class Shape:
    def __init__(self, faces=(), edges=(), volume=0.0, area=0.0, bbox=None, valid=True, solids=1):
        self.Faces = list(faces)
        self.Edges = list(edges)
        self.Volume = volume
        self.Area = area
        self._bbox = bbox or (0, 0, 0, 0, 0, 0)
        self._valid = valid
        self.Solids = [object()] * solids
        self.CenterOfMass = Vector(0, 0, 0)

    def isNull(self):
        return not self.Faces and self.Volume == 0.0

    def isValid(self):
        return self._valid

    @property
    def BoundBox(self):
        bb = types.SimpleNamespace()
        bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax = self._bbox
        return bb


class Object:
    def __init__(self, doc, type_id, name, **props):
        self.Document = doc
        self.TypeId = type_id
        self.Name = name
        self.Label = name
        self.Visibility = True
        self.State = ()
        self.OutList = []
        self.InList = []
        self.PropertiesList = ["Label", "Visibility"] + list(props)
        for key, value in props.items():
            setattr(self, key, value)

    def __repr__(self):
        return f"<{self.TypeId} {self.Name}>"


class Body(Object):
    def __init__(self, doc, name):
        super().__init__(doc, "PartDesign::Body", name)
        self.Group = []
        self.Tip = None

    def newObject(self, type_id, name):
        obj = self.Document.addObject(type_id, name)
        self.Group.append(obj)
        obj.InList.append(self)
        self.Tip = obj
        return obj


class Document:
    def __init__(self, filename=""):
        self.FileName = filename
        self.Objects = []
        self.recomputes = 0
        self.removed = []

    def addObject(self, type_id, name):
        if type_id == "PartDesign::Body":
            obj = Body(self, name)
        else:
            obj = Object(self, type_id, name)
            if type_id == "Sketcher::SketchObject":
                obj.geometry = []
                obj.addGeometry = lambda g, c=False: obj.geometry.append(g)
                obj.AttachmentSupport = []
                obj.MapMode = ""
            if type_id.startswith("PartDesign::") and type_id.split("::")[1] in ("Pad", "Pocket"):
                obj.Length = Quantity(10.0)
                obj.PropertiesList.append("Length")
                obj.Profile = None
            if type_id.startswith("PartDesign::") and type_id.split("::")[1] in ("Plane", "Line", "Point"):
                obj.Placement = Placement()
        self.Objects.append(obj)
        return obj

    def getObject(self, name):
        for obj in self.Objects:
            if obj.Name == name:
                return obj
        return None

    def removeObject(self, name):
        obj = self.getObject(name)
        self.Objects.remove(obj)
        self.removed.append(name)
        for other in self.Objects:
            if isinstance(other, Body) and obj in other.Group:
                other.Group.remove(obj)

    def recompute(self):
        self.recomputes += 1


def install():
    app = types.ModuleType("FreeCAD")
    app.Vector, app.Rotation, app.Placement, app.Units = Vector, Rotation, Placement, types.SimpleNamespace(Quantity=Quantity)
    part = types.ModuleType("Part")
    part.Circle, part.LineSegment = Circle, LineSegment
    sys.modules["FreeCAD"] = app
    sys.modules["Part"] = part
    return app, part


def uninstall():
    sys.modules.pop("FreeCAD", None)
    sys.modules.pop("Part", None)


def flange_document(filename=""):
    """A stub document mirroring Tests.fixtures.flange, with real-looking shapes."""
    doc = Document(filename)
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    pad = body.newObject("PartDesign::Pad", "Pad3")
    pad.Length = Quantity(12.0)
    pad.OutList = [sketch]
    e = [Edge(i, 60.0 if i % 2 else 40.0, (30, 20, 0)) for i in range(4)]
    top_edges = [Edge(10 + i, 60.0, (30, 20, 12)) for i in range(4)]
    pad.Shape = Shape(
        faces=[
            Face(Plane(), 2400.0, (30, 20, 0), (0, 0, -1), e),
            Face(Plane(), 720.0, (30, 0, 6), (0, -1, 0), [e[0], top_edges[0]]),
            Face(Plane(), 480.0, (60, 20, 6), (1, 0, 0), [e[1], top_edges[1]]),
            Face(Plane(), 720.0, (30, 40, 6), (0, 1, 0), [e[2], top_edges[2]]),
            Face(Plane(), 480.0, (0, 20, 6), (-1, 0, 0), [e[3], top_edges[3]]),
            Face(Plane(), 1843.2, (30, 20, 12), (0, 0, 1), top_edges),
        ],
        edges=e + top_edges,
        volume=28800.0,
        area=8000.0,
        bbox=(0, 0, 0, 60, 40, 12),
    )
    boss_sketch = body.newObject("Sketcher::SketchObject", "BossSketch")
    boss = body.newObject("PartDesign::Pad", "Boss1")
    boss.Length = Quantity(20.0)
    boss.OutList = [boss_sketch, pad]
    boss.Shape = Shape(
        faces=[Face(Cylinder(), 1256.6, (30, 20, 22), None, []), Face(Plane(), 314.2, (30, 20, 32), (0, 0, 1), [])],
        volume=35083.0,
        area=9500.0,
        bbox=(0, 0, 0, 60, 40, 32),
    )
    varset = doc.addObject("App::VarSet", "Params")
    varset.wall_min = 2.5
    varset.PropertiesList.append("wall_min")
    return doc
