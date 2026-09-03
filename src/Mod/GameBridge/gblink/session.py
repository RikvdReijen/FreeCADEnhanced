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
"""Working out what actually changed, so the link sends as little as possible.

A live link that re-sends the whole document on every recompute is a link that
stops being live somewhere around the tenth part.  Almost every edit in FreeCAD
touches one solid: its geometry changes, or more often only its placement does,
and everything else in the assembly is exactly what the engine already has.

So a session keeps a snapshot of what it has sent - each node's transform and
visibility, and a checksum of each distinct piece of geometry - and sends only
the difference.  Geometry is keyed by its checksum rather than by which node
carries it, which means two identical parts cost one transfer, and moving a part
costs a transform and no geometry at all.
"""

from gbcore import BRIDGE_VERSION, SCENE_FORMAT_VERSION
from gbcore.transform import get_convention

from . import protocol

__all__ = ["LinkSession", "node_key", "snapshot"]


def node_key(node, path, taken=None):
    """A stable identity for a node across recomputes.

    FreeCAD's internal object name never changes, not even when the user renames
    the label, so it is the right key when there is one.  Nodes without one -
    grouping nodes the exporter invented - fall back to their path through the
    tree, which is stable as long as the tree shape is.

    One object name can appear on several nodes, and both cases are common: a
    solid painted with two materials becomes one node per material, and forty
    links to the same body are forty nodes whose source is that body.  So a
    repeated key is disambiguated by its position, which keeps every node
    distinct without giving up the rename-proof identity for the ordinary case.
    Ignoring this loses nodes: the session would collapse them and the client
    would draw one screw where the assembly has forty.
    """
    key = "obj:%s" % node.source if node.source else "path:%s" % "/".join(path)
    if taken is None or key not in taken:
        return key
    candidate = "%s#%d" % (key, len(path))
    counter = 0
    while candidate in taken:
        counter += 1
        candidate = "%s#%d.%d" % (key, len(path), counter)
    return candidate


def snapshot(scene):
    """Everything a session needs to know about a scene to diff it later."""
    nodes = {}
    order = []
    stack = [(root, [], None) for root in reversed(scene.roots)]
    while stack:
        node, path, parent = stack.pop()
        here = path + [node.name]
        key = node_key(node, here, nodes)
        mesh = scene.meshes[node.mesh] if node.mesh is not None else None
        nodes[key] = {
            "key": key,
            "name": node.name,
            "parent": parent,
            "source": node.source,
            "visible": node.visible,
            "transform": tuple(node.transform.m),
            "mesh": mesh.checksum() if mesh is not None else None,
            "meshIndex": node.mesh,
        }
        order.append(key)
        for child in reversed(node.children):
            stack.append((child, here, key))
    return nodes, order


class LinkSession:
    """Tracks one connected client's view of the document.

    The server owns one of these per client, because two clients that connected
    at different times have seen different things and a delta is only meaningful
    against what its recipient already has.
    """

    def __init__(self, convention="unity", name="FreeCAD"):
        self.convention = get_convention(convention)
        self.name = name
        self.sequence = 0
        self._nodes = {}
        self._order = []
        self._materials = None
        self._meshes_sent = {}
        self._scene_checksum = None

    # -- state -----------------------------------------------------------

    @property
    def has_state(self):
        return self._scene_checksum is not None

    def reset(self):
        """Forget everything, so the next message is a full scene again."""
        self._nodes = {}
        self._order = []
        self._materials = None
        self._meshes_sent = {}
        self._scene_checksum = None
        return self

    def stats(self):
        return {
            "nodes": len(self._nodes),
            "meshes": len(self._meshes_sent),
            "sequence": self.sequence,
        }

    # -- messages --------------------------------------------------------

    def full_scene(self, scene):
        """A complete scene message, and the state to diff the next one against."""
        self.reset()
        nodes, order = snapshot(scene)
        wanted = {}
        for state in nodes.values():
            if state["mesh"] is not None and state["mesh"] not in wanted:
                wanted[state["mesh"]] = scene.meshes[state["meshIndex"]]
        descriptors, blob = protocol.mesh_payload(sorted(wanted.items()), self.convention)

        self.sequence += 1
        body = {
            "version": SCENE_FORMAT_VERSION,
            "bridgeVersion": BRIDGE_VERSION,
            "sequence": self.sequence,
            "document": scene.document,
            "scene": scene.name,
            "target": self.convention.to_dict(),
            "checksum": scene.checksum(),
            "materials": [m.to_dict() for m in scene.materials],
            "meshes": descriptors,
            "nodes": [self._node_body(nodes[key]) for key in order],
            "stats": scene.stats(),
        }
        self._nodes = nodes
        self._order = order
        self._materials = body["materials"]
        self._meshes_sent = {checksum: True for checksum in wanted}
        self._scene_checksum = body["checksum"]
        return protocol.scene_message(body, blob)

    def update(self, scene):
        """The difference since the last message, or ``None`` if there is none.

        Returns a full scene the first time, because a delta against nothing is
        just a scene with extra steps.
        """
        if not self.has_state:
            return self.full_scene(scene)

        checksum = scene.checksum()
        if checksum == self._scene_checksum:
            return None

        nodes, order = snapshot(scene)
        changed = []
        needed = {}
        for key in order:
            state = nodes[key]
            previous = self._nodes.get(key)
            if previous is None or _differs(previous, state):
                changed.append(state)
                mesh_checksum = state["mesh"]
                if mesh_checksum and mesh_checksum not in self._meshes_sent:
                    needed.setdefault(mesh_checksum, scene.meshes[state["meshIndex"]])

        removed = [key for key in self._order if key not in nodes]

        # Geometry the client can release: sent once, and now referenced by
        # nothing.  Without this a long session leaks every version of every
        # solid the user has ever recomputed.
        still_used = {state["mesh"] for state in nodes.values() if state["mesh"]}
        dropped = [c for c in self._meshes_sent if c not in still_used]

        descriptors, blob = protocol.mesh_payload(sorted(needed.items()), self.convention)

        materials = [m.to_dict() for m in scene.materials]
        material_change = materials if materials != self._materials else None

        self.sequence += 1
        delta = {
            "sequence": self.sequence,
            "checksum": checksum,
            "meshes": descriptors,
            "nodes": [self._node_body(state) for state in changed],
            "removedNodes": removed,
            "droppedMeshes": dropped,
            "stats": scene.stats(),
        }
        if material_change is not None:
            delta["materials"] = material_change

        self._nodes = nodes
        self._order = order
        self._scene_checksum = checksum
        self._materials = materials
        for checksum_sent in needed:
            self._meshes_sent[checksum_sent] = True
        for checksum_dropped in dropped:
            self._meshes_sent.pop(checksum_dropped, None)
        return protocol.update_message(delta, blob)

    # -- helpers ---------------------------------------------------------

    def _node_body(self, state):
        """One node, with its placement already in the client's space."""
        from gbcore import Matrix4

        transform = self.convention.convert_matrix(Matrix4(state["transform"]))
        translation, rotation, scale = transform.to_trs()
        body = {
            "key": state["key"],
            "name": state["name"],
            "parent": state["parent"],
            "visible": state["visible"],
            "transform": list(transform.m),
            "trs": {
                "translation": list(translation),
                "rotation": list(rotation),
                "scale": list(scale),
            },
        }
        if state["source"]:
            body["source"] = state["source"]
        if state["mesh"]:
            body["mesh"] = state["mesh"]
        return body


def _differs(previous, current):
    """Whether a node has changed in a way the client has to hear about."""
    for field in ("parent", "visible", "mesh", "name"):
        if previous.get(field) != current.get(field):
            return True
    # Placements are compared with a tolerance: FreeCAD recomputes can perturb
    # the last bit of a matrix that the user did not touch, and re-sending every
    # node in the assembly because of that defeats the point of a delta.
    for a, b in zip(previous["transform"], current["transform"]):
        if abs(a - b) > 1e-9:
            return True
    return False
