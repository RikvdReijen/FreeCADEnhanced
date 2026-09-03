# SPDX-License-Identifier: LGPL-2.1-or-later
"""The document model everything else in this module works against.

A :class:`DocumentModel` is a plain-data view of a parametric document: an
ordered feature tree, a set of named parameters, and the topological entities
the current geometry exposes. It is *not* a FreeCAD document — it is the
minimum an anchor resolver, a replay engine and a merge algorithm need to see,
which is what lets all three be tested without FreeCAD, a GPU or a document.

``collab.freecad_adapter`` builds one of these from a real FreeCAD document;
``collab.model.DocumentModel.from_json`` builds one from a fixture.

The critical property, and the reason this type exists at all: entity names
(``Face6``, ``Edge12``) are treated as *volatile*. Nothing in this module may
persist one and expect it to mean the same thing after a recompute. See
``docs/concepts/ai-cad-collaboration/README.md`` §1.
"""

import copy
import json

from .errors import LayerFormatError
from .geom import as_vec

#: Entity kinds this model understands.
ENTITY_KINDS = ("face", "edge", "vertex")


class Entity:
    """One topological entity of the current geometry.

    ``name`` is the volatile topological name. It is carried so that failures
    can be reported in the user's vocabulary ("this used to be Face6"), never
    so that it can be used as an identity across a recompute.
    """

    __slots__ = (
        "name",
        "kind",
        "owner",
        "surface",
        "normal",
        "area",
        "length",
        "centroid_local",
        "adjacency",
        "between",
    )

    def __init__(
        self,
        name,
        kind,
        owner,
        surface="unknown",
        normal=None,
        area=None,
        length=None,
        centroid_local=(0.0, 0.0, 0.0),
        adjacency=0,
        between=(),
    ):
        if kind not in ENTITY_KINDS:
            raise LayerFormatError(f"unknown entity kind {kind!r}", f"entity[{name}].kind")
        self.name = name
        self.kind = kind
        self.owner = owner
        self.surface = surface
        self.normal = as_vec(normal, f"entity[{name}].normal")
        self.area = None if area is None else float(area)
        self.length = None if length is None else float(length)
        self.centroid_local = as_vec(centroid_local, f"entity[{name}].centroid_local")
        self.adjacency = int(adjacency)
        self.between = tuple(between)

    @property
    def size(self):
        """The extent that characterises this entity: area for faces, length for edges."""
        return self.area if self.kind == "face" else self.length

    def to_json(self):
        data = {"name": self.name, "kind": self.kind, "owner": self.owner, "surface": self.surface}
        if self.normal is not None:
            data["normal"] = list(self.normal)
        if self.area is not None:
            data["area"] = self.area
        if self.length is not None:
            data["length"] = self.length
        data["centroid_local"] = list(self.centroid_local)
        data["adjacency"] = self.adjacency
        if self.between:
            data["between"] = list(self.between)
        return data

    @classmethod
    def from_json(cls, data):
        try:
            return cls(
                name=data["name"],
                kind=data["kind"],
                owner=data["owner"],
                surface=data.get("surface", "unknown"),
                normal=data.get("normal"),
                area=data.get("area"),
                length=data.get("length"),
                centroid_local=data.get("centroid_local", (0.0, 0.0, 0.0)),
                adjacency=data.get("adjacency", 0),
                between=data.get("between", ()),
            )
        except KeyError as exc:
            raise LayerFormatError(f"missing field {exc.args[0]!r}", "entity") from None

    def __repr__(self):
        return f"Entity({self.name!r}, {self.kind!r}, owner={self.owner!r})"


class Feature:
    """One node of the feature tree.

    ``depends_on`` names the features this one consumes. It is what makes the
    dependency subtree partitioning in :mod:`collab.merge` possible, and what
    makes ``remove_feature`` able to refuse loudly.
    """

    __slots__ = ("name", "kind", "params", "properties", "depends_on")

    def __init__(self, name, kind, params=None, properties=None, depends_on=()):
        self.name = name
        self.kind = kind
        self.params = dict(params or {})
        self.properties = dict(properties or {})
        self.depends_on = tuple(depends_on)

    def to_json(self):
        data = {"name": self.name, "kind": self.kind}
        if self.params:
            data["params"] = dict(self.params)
        if self.properties:
            data["properties"] = dict(self.properties)
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        return data

    @classmethod
    def from_json(cls, data):
        try:
            return cls(
                name=data["name"],
                kind=data["kind"],
                params=data.get("params"),
                properties=data.get("properties"),
                depends_on=data.get("depends_on", ()),
            )
        except KeyError as exc:
            raise LayerFormatError(f"missing field {exc.args[0]!r}", "feature") from None

    def __repr__(self):
        return f"Feature({self.name!r}, {self.kind!r})"


class DocumentModel:
    """An ordered feature tree, its parameters, and its current topology."""

    def __init__(self, revision="", features=(), entities=(), parameters=None, document=""):
        self.revision = revision
        self.document = document
        self.features = list(features)
        self.entities = list(entities)
        self.parameters = dict(parameters or {})

    # -- lookup ---------------------------------------------------------

    def feature(self, name):
        for feature in self.features:
            if feature.name == name:
                return feature
        return None

    def has_feature(self, name):
        return self.feature(name) is not None

    def index_of(self, name):
        for index, feature in enumerate(self.features):
            if feature.name == name:
                return index
        return -1

    def entity(self, name):
        for entity in self.entities:
            if entity.name == name:
                return entity
        return None

    def entities_of(self, owner, kind=None):
        return [
            entity
            for entity in self.entities
            if entity.owner == owner and (kind is None or entity.kind == kind)
        ]

    def dependents_of(self, name):
        """Every feature that depends on ``name``, transitively, in tree order."""
        found = set()
        pending = [name]
        while pending:
            current = pending.pop()
            for feature in self.features:
                if current in feature.depends_on and feature.name not in found:
                    found.add(feature.name)
                    pending.append(feature.name)
        return [f.name for f in self.features if f.name in found]

    def ancestors_of(self, name):
        """Every feature ``name`` depends on, transitively, in tree order."""
        found = set()
        pending = [name]
        while pending:
            current = pending.pop()
            feature = self.feature(current)
            if feature is None:
                continue
            for parent in feature.depends_on:
                if parent not in found:
                    found.add(parent)
                    pending.append(parent)
        return [f.name for f in self.features if f.name in found]

    # -- mutation -------------------------------------------------------

    def insert_after(self, feature, after):
        """Insert ``feature`` directly after the feature named ``after``.

        ``after=None`` places it at the head of the tree.
        """
        if self.has_feature(feature.name):
            raise LayerFormatError(f"feature {feature.name!r} already exists", "insert_after")
        if after is None:
            self.features.insert(0, feature)
            return 0
        index = self.index_of(after)
        if index < 0:
            raise LayerFormatError(f"no such feature {after!r}", "insert_after")
        self.features.insert(index + 1, feature)
        return index + 1

    def remove_feature(self, name):
        index = self.index_of(name)
        if index < 0:
            raise LayerFormatError(f"no such feature {name!r}", "remove_feature")
        removed = self.features.pop(index)
        self.entities = [entity for entity in self.entities if entity.owner != name]
        return removed

    def move_feature(self, name, after):
        """Move an existing feature to sit directly after ``after``."""
        index = self.index_of(name)
        if index < 0:
            raise LayerFormatError(f"no such feature {name!r}", "move_feature")
        feature = self.features.pop(index)
        try:
            return self.insert_after(feature, after)
        except LayerFormatError:
            self.features.insert(index, feature)
            raise

    # -- whole-model ----------------------------------------------------

    def clone(self):
        return copy.deepcopy(self)

    def to_json(self):
        return {
            "document": self.document,
            "revision": self.revision,
            "parameters": dict(self.parameters),
            "features": [f.to_json() for f in self.features],
            "entities": [e.to_json() for e in self.entities],
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            raise LayerFormatError("document must be an object", "document")
        return cls(
            revision=data.get("revision", ""),
            document=data.get("document", ""),
            features=[Feature.from_json(f) for f in data.get("features", [])],
            entities=[Entity.from_json(e) for e in data.get("entities", [])],
            parameters=data.get("parameters"),
        )

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_json(json.load(handle))

    def __repr__(self):
        return (
            f"DocumentModel({self.document!r}, revision={self.revision!r}, "
            f"{len(self.features)} features, {len(self.entities)} entities)"
        )
