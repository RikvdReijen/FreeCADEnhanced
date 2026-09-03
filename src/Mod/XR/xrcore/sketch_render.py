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
"""Coin3D renderer for the sketch scene.

:mod:`xrsketch` deliberately builds no Coin nodes — it is pure geometry, which
is what makes it testable without a headset. This module is the other half:
it walks the scene and rebuilds the viewer's ``sketch_separator``.

Rebuilding is gated on ``session.changed``, so a frame that altered nothing
costs one attribute read. That matters because this runs inside the XR render
loop: the whole scene is rebuilt when it does fire, which is cheap for the
object counts a person can draw by hand, and far simpler than incremental
node surgery. If a scene ever grows past that, ``session.drain_events()``
exposes the discrete events needed to go incremental.
"""

__all__ = ["SketchRenderer"]

# Objects drawn as wireframe get a slightly forward offset so they do not
# z-fight with the surfaces they sit on.
_LINE_OFFSET = 1.0005


class SketchRenderer:
    """Rebuilds a Coin subgraph from an :mod:`xrsketch` scene."""

    def __init__(self, root=None, session=None):
        self.root = root
        self.session = session
        self._built_revision = None

    # ------------------------------------------------------------------

    def attach(self, root, session):
        self.root = root
        self.session = session
        self._built_revision = None

    def detach(self):
        if self.root is not None:
            try:
                self.root.removeAllChildren()
            except Exception:
                pass
        self.root = None
        self.session = None
        self._built_revision = None

    # ------------------------------------------------------------------

    def update(self, force=False):
        """Rebuild if the scene changed. Returns True when it rebuilt."""
        session = self.session
        if session is None or self.root is None:
            return False
        if not force and not getattr(session, "changed", False):
            return False
        try:
            self.rebuild()
        except Exception:
            # A drawing fault must never take the headset down; the next
            # changed frame will try again.
            return False
        # ``changed`` is the session's own dirty flag; clearing it here is what
        # stops us rebuilding the same scene every frame.
        try:
            session.changed = False
        except Exception:
            pass
        return True

    def rebuild(self):
        from pivy.coin import SoSeparator

        scene = getattr(self.session, "scene", None)
        self.root.removeAllChildren()
        if scene is None:
            return 0

        built = 0
        for obj in self._visible_objects(scene):
            # ``obj`` is a SketchObject: the geometry is in ``.data`` and its
            # placement in ``.transform``, so the node goes under a transform
            # of its own rather than being baked into the vertices.
            node = self._node_for(getattr(obj, "data", obj), obj)
            if node is None:
                continue
            wrapper = SoSeparator()
            placement = self._transform_node(getattr(obj, "transform", None))
            if placement is not None:
                wrapper.addChild(placement)
            wrapper.addChild(node)
            self.root.addChild(wrapper)
            built += 1
        return built

    @staticmethod
    def _transform_node(transform):
        """An SoTransform for a sketch Transform, or None when it is identity."""
        if transform is None:
            return None
        translation = tuple(getattr(transform, "translation", (0.0, 0.0, 0.0)))
        rotation = tuple(getattr(transform, "rotation", (0.0, 0.0, 0.0, 1.0)))
        scale = getattr(transform, "scale", 1.0)
        try:
            scale = float(scale)
            uniform = (scale, scale, scale)
        except TypeError:
            uniform = tuple(float(v) for v in scale)
        if (translation == (0.0, 0.0, 0.0)
                and rotation == (0.0, 0.0, 0.0, 1.0)
                and uniform == (1.0, 1.0, 1.0)):
            return None

        from pivy.coin import SbRotation, SbVec3f, SoTransform

        node = SoTransform()
        node.translation.setValue(SbVec3f(*[float(v) for v in translation]))
        node.rotation.setValue(SbRotation(*[float(v) for v in rotation]))
        node.scaleFactor.setValue(SbVec3f(*uniform))
        return node

    # ------------------------------------------------------------------
    # scene walking
    # ------------------------------------------------------------------

    def _visible_objects(self, scene):
        """Objects in draw order, skipping hidden ones and hidden layers."""
        objects = getattr(scene, "objects", None)
        if objects is None:
            return []
        visible = []
        for obj in objects:
            if not self._is_visible(scene, obj):
                continue
            visible.append(obj)
        return visible

    @staticmethod
    def _is_visible(scene, obj):
        if not getattr(obj, "visible", True):
            return False
        layer_id = getattr(obj, "layer", None)
        if layer_id is None:
            return True
        resolver = getattr(scene, "layer_visible", None)
        if callable(resolver):
            try:
                return bool(resolver(layer_id))
            except Exception:
                return True
        return True

    def _node_for(self, geometry, obj=None):
        """Dispatch on what the geometry can produce, not on its class name.

        The sketch scene holds several unrelated geometry types; asking each
        one what it can emit keeps this renderer from having to import and
        isinstance-check every class in :mod:`xrsketch`.
        """
        colour = getattr(obj, "color", None) if obj is not None else None
        if colour is None:
            colour = getattr(geometry, "color", None)
        obj = geometry

        # A control cage: limit surface plus the cage as a wireframe overlay.
        if hasattr(obj, "limit_surface"):
            return self._cage_node(obj, colour)
        # A parametric primitive or an evaluated surface.
        if hasattr(obj, "mesh"):
            return self._mesh_node(obj.mesh(), colour)
        if hasattr(obj, "points") and hasattr(obj, "quads"):
            return self._quad_mesh_node(obj, colour)
        # A curve.
        if hasattr(obj, "flatten"):
            return self._line_node(obj.flatten(self._curve_tolerance()), colour)
        # A reference image plane.
        if hasattr(obj, "corners"):
            return self._image_node(obj)
        # A measurement.
        if hasattr(obj, "labels"):
            return self._measurement_node(obj, colour)
        return None

    def _curve_tolerance(self):
        """Flattening tolerance in metres, tightened when the user is small."""
        base = 0.002
        try:
            from xrcore import environment_bridge

            scale = environment_bridge.current_state().get("scale", 1.0)
            if scale > 1.0:
                return base / float(scale)
        except Exception:
            pass
        return base

    # ------------------------------------------------------------------
    # node builders
    # ------------------------------------------------------------------

    @staticmethod
    def _material(colour, transparency=0.0):
        from pivy.coin import SoMaterial

        material = SoMaterial()
        if colour is not None:
            rgb = tuple(colour)[:3]
            material.diffuseColor.setValue(*rgb)
            if len(tuple(colour)) > 3:
                material.transparency.setValue(1.0 - float(tuple(colour)[3]))
        if transparency:
            material.transparency.setValue(float(transparency))
        return material

    def _line_node(self, points, colour, closed=False):
        from pivy.coin import SoLineSet, SoSeparator, SoVertexProperty

        points = list(points or ())
        if len(points) < 2:
            return None
        if closed:
            points = points + [points[0]]

        separator = SoSeparator()
        separator.addChild(self._material(colour))
        vertices = SoVertexProperty()
        for index, point in enumerate(points):
            vertices.vertex.set1Value(index, float(point[0]), float(point[1]), float(point[2]))
        line = SoLineSet()
        line.vertexProperty = vertices
        line.numVertices.setValue(len(points))
        separator.addChild(line)
        return separator

    def _mesh_node(self, mesh, colour):
        """Build an SoIndexedFaceSet from the (positions, normals, uvs, indices)
        tuple the sketch and environment modules both produce."""
        if mesh is None:
            return None
        try:
            positions, normals, _uvs, indices = mesh
        except (TypeError, ValueError):
            return None
        return self._indexed_face_set(positions, normals, indices, colour)

    def _quad_mesh_node(self, obj, colour):
        """A surface that exposes points() and quads() rather than a mesh tuple."""
        points = list(obj.points() or ())
        quads = list(obj.quads() or ())
        if not points or not quads:
            return None

        positions = []
        for point in points:
            positions.extend((float(point[0]), float(point[1]), float(point[2])))
        indices = []
        for quad in quads:
            # Triangulate: a quad becomes two triangles sharing a diagonal.
            if len(quad) == 4:
                a, b, c, d = quad
                indices.extend((a, b, c, a, c, d))
            elif len(quad) == 3:
                indices.extend(tuple(quad))
        return self._indexed_face_set(positions, None, indices, colour)

    def _indexed_face_set(self, positions, normals, indices, colour):
        from pivy.coin import (
            SoIndexedFaceSet,
            SoNormalBinding,
            SoSeparator,
            SoShapeHints,
            SoVertexProperty,
        )

        positions = list(positions or ())
        indices = list(indices or ())
        if len(positions) < 9 or len(indices) < 3:
            return None

        separator = SoSeparator()
        hints = SoShapeHints()
        hints.vertexOrdering = SoShapeHints.COUNTERCLOCKWISE
        hints.shapeType = SoShapeHints.SOLID
        separator.addChild(hints)
        separator.addChild(self._material(colour))

        vertices = SoVertexProperty()
        for index in range(len(positions) // 3):
            vertices.vertex.set1Value(
                index,
                float(positions[index * 3]),
                float(positions[index * 3 + 1]),
                float(positions[index * 3 + 2]),
            )
        if normals:
            for index in range(len(normals) // 3):
                vertices.normal.set1Value(
                    index,
                    float(normals[index * 3]),
                    float(normals[index * 3 + 1]),
                    float(normals[index * 3 + 2]),
                )
            binding = SoNormalBinding()
            binding.value = SoNormalBinding.PER_VERTEX_INDEXED
            separator.addChild(binding)

        faces = SoIndexedFaceSet()
        faces.vertexProperty = vertices
        cursor = 0
        for triangle in range(len(indices) // 3):
            for corner in range(3):
                faces.coordIndex.set1Value(cursor, int(indices[triangle * 3 + corner]))
                cursor += 1
            faces.coordIndex.set1Value(cursor, -1)
            cursor += 1
        separator.addChild(faces)
        return separator

    def _cage_node(self, cage, colour):
        """The limit surface, with the control cage drawn over it."""
        from pivy.coin import SoSeparator

        separator = SoSeparator()
        level = self._subdivision_level()
        try:
            surface = cage.limit_surface(level)
            triangles = surface.triangles()
        except Exception:
            triangles = None
        if triangles is not None:
            node = self._mesh_node(triangles, colour)
            if node is not None:
                separator.addChild(node)

        # The cage overlay makes the editable structure visible; without it a
        # subdivision surface gives the user nothing to grab.
        try:
            for edge in cage.edge_positions():
                line = self._line_node(edge, (0.35, 0.65, 1.0))
                if line is not None:
                    separator.addChild(line)
        except Exception:
            pass
        return separator if separator.getNumChildren() else None

    @staticmethod
    def _subdivision_level():
        try:
            from xrcore.service import preferences

            return max(0, min(4, preferences().GetInt("SketchSubdivisionLevel", 2)))
        except Exception:
            return 2

    def _image_node(self, plane):
        from pivy.coin import (
            SoIndexedFaceSet,
            SoSeparator,
            SoTexture2,
            SoTextureCoordinate2,
            SoVertexProperty,
        )

        try:
            corners = list(plane.corners())
        except Exception:
            return None
        if len(corners) != 4:
            return None

        separator = SoSeparator()
        opacity = float(getattr(plane, "opacity", 1.0))
        separator.addChild(self._material(None, transparency=1.0 - opacity))

        image_bytes = getattr(plane, "image_bytes", None)
        size = getattr(plane, "image_size", None)
        if image_bytes and size:
            texture = SoTexture2()
            try:
                texture.image.setValue(size, getattr(plane, "image_components", 3), image_bytes)
                separator.addChild(texture)
            except Exception:
                pass

        coords = SoTextureCoordinate2()
        for index, uv in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            coords.point.set1Value(index, float(uv[0]), float(uv[1]))
        separator.addChild(coords)

        vertices = SoVertexProperty()
        for index, corner in enumerate(corners):
            vertices.vertex.set1Value(
                index, float(corner[0]), float(corner[1]), float(corner[2])
            )
        faces = SoIndexedFaceSet()
        faces.vertexProperty = vertices
        for index, value in enumerate((0, 1, 2, 3, -1)):
            faces.coordIndex.set1Value(index, value)
        separator.addChild(faces)
        return separator

    def _measurement_node(self, measurement, colour):
        from pivy.coin import SoSeparator

        separator = SoSeparator()
        points = list(getattr(measurement, "points", ()) or ())
        line = self._line_node(points, colour or (1.0, 0.75, 0.1))
        if line is not None:
            separator.addChild(line)

        try:
            labels = list(measurement.labels())
        except Exception:
            labels = []
        for label in labels:
            node = self._label_node(label)
            if node is not None:
                separator.addChild(node)
        return separator if separator.getNumChildren() else None

    def _label_node(self, label):
        """A billboarded readout. ``label`` is (text, position) or an object."""
        from pivy.coin import SbVec3f, SoAsciiText, SoBillboard, SoScale, SoTranslation

        if isinstance(label, (tuple, list)) and len(label) >= 2:
            text, position = label[0], label[1]
        else:
            text = getattr(label, "text", None)
            position = getattr(label, "position", None)
        if not text or position is None:
            return None

        billboard = SoBillboard()
        translation = SoTranslation()
        translation.translation.setValue(
            SbVec3f(float(position[0]), float(position[1]), float(position[2]))
        )
        billboard.addChild(translation)
        billboard.addChild(self._material((1.0, 1.0, 1.0)))
        scale = SoScale()
        # Readouts are authored in millimetres of text height; Coin text is in
        # scene units, which are metres here.
        scale.scaleFactor.setValue(SbVec3f(0.004, 0.004, 0.004))
        billboard.addChild(scale)
        ascii_text = SoAsciiText()
        ascii_text.string.setValue(str(text))
        billboard.addChild(ascii_text)
        return billboard
