# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Walking a FreeCAD document into the intermediate scene.

This is the one module that has to know what a FreeCAD document looks like, and
it is deliberately the only one: everything downstream sees a
:class:`~gbcore.scene.Scene` and never asks what produced it.

Three things about a document are easy to get wrong and are handled explicitly
here.

*Geometry is placed twice if you are careless.*  ``obj.Shape`` already has the
object's placement baked into it, so tessellating it and *also* putting
``obj.Placement`` on the node moves everything twice as far as it should go.
The walker tessellates a copy with the placement cleared, which keeps the
geometry at its own origin - which is what an asset-based engine wants anyway.

*Not every object in a document belongs in the export.*  A pad's sketch, a
boolean's operands, a datum plane: FreeCAD keeps them all in ``doc.Objects`` and
hides them.  Following visibility rather than trying to reason about feature
trees gets this right, because FreeCAD has already done the reasoning.

*A link is not a copy.*  An assembly with forty of the same screw should
tessellate one screw.  Links resolve to their target and reuse its meshes.
"""

from .materials import materials_from_object
from .scene import Node, Scene
from .tessellate import TessellationSettings, meshes_from_shape
from .transform import Matrix4

__all__ = ["DocumentWalker", "scene_from_document", "scene_from_objects"]

#: Types that hold other objects rather than geometry of their own.
GROUP_TYPES = (
    "App::Part",
    "App::DocumentObjectGroup",
    "App::LinkGroup",
    "Assembly::AssemblyObject",
)


class DocumentWalker:
    """Builds a :class:`~gbcore.scene.Scene` from FreeCAD document objects."""

    def __init__(self, settings=None, include_hidden=False, skip_empty=True):
        self.settings = settings or TessellationSettings()
        self.include_hidden = include_hidden
        self.skip_empty = skip_empty
        self.warnings = []
        self._scene = None
        self._mesh_cache = {}
        self._material_cache = {}

    # -- entry points ----------------------------------------------------

    def walk_document(self, document, objects=None):
        """Convert a whole document, or just the objects given."""
        name = getattr(document, "Label", None) or getattr(document, "Name", "Document")
        scene = self._begin(name, getattr(document, "Name", None))
        roots = objects if objects is not None else top_level_objects(document)
        for obj in roots:
            node = self._node_for(obj)
            if node is not None:
                scene.add_root(node)
        if self.skip_empty:
            scene.prune_empty()
        scene.metadata["tessellation"] = self.settings.to_dict()
        return scene

    def walk_objects(self, objects, name="Selection", document=None):
        """Convert a selection, keeping each object as its own root."""
        scene = self._begin(name, document)
        for obj in objects:
            node = self._node_for(obj)
            if node is not None:
                scene.add_root(node)
        if self.skip_empty:
            scene.prune_empty()
        scene.metadata["tessellation"] = self.settings.to_dict()
        return scene

    def _begin(self, name, document):
        self.warnings = []
        self._mesh_cache = {}
        self._material_cache = {}
        self._scene = Scene(name, document=document)
        return self._scene

    # -- nodes -----------------------------------------------------------

    def _node_for(self, obj, visited=None):
        """One document object, as a node with whatever hangs beneath it."""
        visited = visited if visited is not None else set()
        identity = getattr(obj, "Name", None) or id(obj)
        if identity in visited:
            # A link cycle would otherwise recurse until the interpreter gives
            # up; a document can contain one, however little sense it makes.
            self.warnings.append("%s is part of a link cycle and was skipped" % identity)
            return None
        visited = visited | {identity}

        if not self.include_hidden and not is_visible(obj):
            return None

        label = getattr(obj, "Label", None) or getattr(obj, "Name", "Object")
        node = Node(
            str(label),
            self._placement_of(obj),
            visible=is_visible(obj),
            source=getattr(obj, "Name", None),
            metadata=_metadata_for(obj),
        )

        linked = self._linked_object(obj)
        if linked is not None:
            child = self._node_for(linked, visited)
            if child is None:
                return None
            # The link supplies the placement, the target supplies everything
            # else, so the target's own placement stays on the child node.
            node.add(child)
            return node

        children = list(getattr(obj, "Group", ()) or ())
        if children or _type_of(obj) in GROUP_TYPES:
            for child in children:
                child_node = self._node_for(child, visited)
                if child_node is not None:
                    node.add(child_node)
            return node if node.children else None

        meshes = self._meshes_for(obj)
        if not meshes:
            return None
        if len(meshes) == 1:
            node.mesh = meshes[0]
            return node
        # A shape painted with several materials becomes one child per material,
        # because a mesh carries exactly one.
        for index, mesh_index in enumerate(meshes):
            node.add(
                Node(
                    "%s_%d" % (label, index),
                    Matrix4(),
                    mesh=mesh_index,
                    source=getattr(obj, "Name", None),
                )
            )
        return node

    def _placement_of(self, obj):
        placement = getattr(obj, "Placement", None)
        if placement is None:
            return Matrix4()
        try:
            return Matrix4.from_freecad(placement.toMatrix())
        except (AttributeError, ValueError):
            self.warnings.append(
                "%s has a placement the bridge could not read" % getattr(obj, "Name", obj)
            )
            return Matrix4()

    def _linked_object(self, obj):
        """The object a link points at, or ``None`` when this is not a link."""
        if "Link" not in _type_of(obj):
            return None
        for attribute in ("LinkedObject", "getLinkedObject"):
            value = getattr(obj, attribute, None)
            if value is None:
                continue
            try:
                linked = value() if callable(value) else value
            except TypeError:
                continue
            if isinstance(linked, (list, tuple)):
                linked = linked[0] if linked else None
            if linked is not None and linked is not obj:
                return linked
        return None

    # -- geometry --------------------------------------------------------

    def _meshes_for(self, obj):
        """Mesh indices for one object, tessellating it at most once."""
        key = getattr(obj, "Name", None) or id(obj)
        cached = self._mesh_cache.get(key)
        if cached is not None:
            return cached

        materials = self._materials_for(obj)
        meshes = []

        shape = getattr(obj, "Shape", None)
        if shape is not None and not _is_null(shape):
            label = getattr(obj, "Label", None) or str(key)
            try:
                meshes = meshes_from_shape(
                    _local_copy(shape), str(label), self.settings, materials
                )
            except Exception as problem:  # a single bad solid must not stop the export
                self.warnings.append("%s could not be tessellated: %s" % (label, problem))
                meshes = []
        else:
            meshes = self._mesh_feature(obj, materials)

        indices = [self._scene.add_mesh(mesh) for mesh in meshes]
        self._mesh_cache[key] = indices
        return indices

    def _mesh_feature(self, obj, materials):
        """A Mesh::Feature, which carries triangles rather than a B-rep."""
        mesh_object = getattr(obj, "Mesh", None)
        topology = getattr(mesh_object, "Topology", None)
        if not topology:
            return []
        from .scene import Mesh

        points, facets = topology
        positions = []
        for point in points:
            if hasattr(point, "x"):
                positions.extend((point.x, point.y, point.z))
            else:
                positions.extend(point)
        indices = []
        for facet in facets:
            indices.extend(int(value) for value in facet)
        label = getattr(obj, "Label", None) or getattr(obj, "Name", "Mesh")
        mesh = Mesh(str(label), positions, indices, material=materials[0] if materials else None)
        mesh.drop_degenerate_triangles()
        mesh.compute_normals()
        return [] if mesh.is_empty else [mesh]

    def _materials_for(self, obj):
        """Register the object's appearances, returning their scene indices."""
        key = getattr(obj, "Name", None) or id(obj)
        cached = self._material_cache.get(key)
        if cached is not None:
            return cached
        indices = [
            self._scene.add_material(material) for material in materials_from_object(obj)
        ]
        self._material_cache[key] = indices
        return indices


# ---------------------------------------------------------------------------
# Helpers, also useful on their own.
# ---------------------------------------------------------------------------


def _type_of(obj):
    return str(getattr(obj, "TypeId", "") or "")


def _is_null(shape):
    try:
        return bool(shape.isNull())
    except (AttributeError, TypeError):
        return False


def _local_copy(shape):
    """A copy of the shape with its placement removed.

    ``obj.Shape`` carries ``obj.Placement`` inside it.  Exporting that *and*
    putting the placement on the node moves everything twice; stripping it here
    leaves the geometry at its own origin, which is also what an asset-based
    engine wants.
    """
    copy = getattr(shape, "copy", None)
    if copy is None:
        return shape
    try:
        local = copy()
    except Exception:
        return shape
    try:
        placement = local.Placement
        local.Placement = placement.__class__()
    except Exception:
        # Some shapes have no settable placement; theirs was never applied.
        pass
    return local


def is_visible(obj):
    """Whether FreeCAD is currently drawing this object.

    In console mode there is no view provider and nothing is drawn, so
    everything counts as visible - otherwise a headless export would produce an
    empty file, which is a worse answer than an unfiltered one.
    """
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return True
    return bool(getattr(view, "Visibility", True))


def _metadata_for(obj):
    """The few document properties worth carrying into the engine."""
    metadata = {}
    for name, key in (("TypeId", "freecadType"), ("Label2", "description")):
        value = getattr(obj, name, None)
        if value:
            metadata[key] = str(value)
    return metadata


def top_level_objects(document):
    """The objects that are not part of some other object.

    A document is a graph, not a tree: a pad's sketch, a boolean's operands and
    a group's members all appear in ``doc.Objects`` alongside the things that
    contain them.  Exporting from the roots and letting containers pull in their
    own children is what stops an assembly arriving as its own parts scattered
    beside it.
    """
    objects = list(getattr(document, "Objects", ()) or ())
    claimed = set()
    for obj in objects:
        for child in list(getattr(obj, "Group", ()) or ()):
            claimed.add(id(child))
    roots = []
    for obj in objects:
        if id(obj) in claimed:
            continue
        if _type_of(obj) in ("App::Origin", "App::Line", "App::Plane", "App::Placement"):
            continue
        roots.append(obj)
    return roots


def scene_from_document(document, settings=None, include_hidden=False, objects=None):
    """Convenience wrapper around :class:`DocumentWalker`."""
    walker = DocumentWalker(settings, include_hidden)
    scene = walker.walk_document(document, objects)
    scene.metadata["warnings"] = walker.warnings
    return scene


def scene_from_objects(objects, name="Selection", settings=None, include_hidden=False):
    walker = DocumentWalker(settings, include_hidden)
    scene = walker.walk_objects(objects, name)
    scene.metadata["warnings"] = walker.warnings
    return scene
