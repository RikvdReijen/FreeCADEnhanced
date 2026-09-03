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
"""The engine side of the live link.

This is a real client, not a test double: the Blender add-on uses it, and an
engine-side integration in any Python can.  It keeps a mirror of the document -
node placements and mesh geometry - and hands the application a callback each
time that mirror changes, which is the shape every engine's scene update wants.

Applying a delta is the interesting half.  Geometry is keyed by checksum and
cached, so a part that only moved arrives as a transform with no vertices
attached, and the client is expected to already have the mesh.  If it does not -
because it connected mid-session, or dropped a message - that is a bug worth
noticing rather than papering over, so it asks the server to resync instead of
quietly drawing the wrong shape.
"""

import socket
import threading
import time

from . import protocol

__all__ = ["LinkClient", "SceneMirror"]


class SceneMirror:
    """The client's copy of the document: nodes, geometry and materials."""

    def __init__(self):
        self.document = None
        self.scene = None
        self.target = None
        self.nodes = {}
        self.order = []
        self.meshes = {}
        self.materials = []
        self.checksum = None
        self.sequence = 0

    def clear(self):
        self.__init__()
        return self

    def apply_scene(self, body, blob):
        """Replace everything with a full scene message."""
        self.clear()
        self.document = body.get("document")
        self.scene = body.get("scene")
        self.target = body.get("target")
        self.materials = body.get("materials", [])
        self.checksum = body.get("checksum")
        self.sequence = body.get("sequence", 0)
        for descriptor in body.get("meshes", ()):
            self.meshes[descriptor["id"]] = protocol.decode_mesh_payload(descriptor, blob)
        for node in body.get("nodes", ()):
            self.nodes[node["key"]] = node
            self.order.append(node["key"])
        return self

    def apply_delta(self, delta, blob):
        """Apply an update.  Returns what changed, for the engine to act on."""
        for descriptor in delta.get("meshes", ()):
            self.meshes[descriptor["id"]] = protocol.decode_mesh_payload(descriptor, blob)

        changed, added = [], []
        for node in delta.get("nodes", ()):
            key = node["key"]
            if key in self.nodes:
                changed.append(key)
            else:
                added.append(key)
                self.order.append(key)
            self.nodes[key] = node

        removed = []
        for key in delta.get("removedNodes", ()):
            if self.nodes.pop(key, None) is not None:
                removed.append(key)
            if key in self.order:
                self.order.remove(key)

        for checksum in delta.get("droppedMeshes", ()):
            self.meshes.pop(checksum, None)

        if "materials" in delta:
            self.materials = delta["materials"]
        self.checksum = delta.get("checksum", self.checksum)
        self.sequence = delta.get("sequence", self.sequence)
        return {"added": added, "changed": changed, "removed": removed}

    def missing_geometry(self):
        """Nodes whose mesh the client was told about but has never received."""
        return [
            key
            for key, node in self.nodes.items()
            if node.get("mesh") and node["mesh"] not in self.meshes
        ]

    def mesh_for(self, key):
        node = self.nodes.get(key)
        if node is None or not node.get("mesh"):
            return None
        return self.meshes.get(node["mesh"])

    def stats(self):
        return {
            "nodes": len(self.nodes),
            "meshes": len(self.meshes),
            "materials": len(self.materials),
            "sequence": self.sequence,
        }


class LinkClient:
    """Connects to a FreeCAD live link and keeps a :class:`SceneMirror` current."""

    def __init__(
        self,
        host="127.0.0.1",
        port=54321,
        name="client",
        engine="generic",
        token=None,
        on_change=None,
        on_error=None,
    ):
        self.host = host
        self.port = port
        self.name = name
        self.engine = engine
        self.token = token
        #: Called as ``on_change(mirror, change)`` after every applied message,
        #: where ``change`` is ``None`` for a full scene.
        self.on_change = on_change
        self.on_error = on_error
        self.mirror = SceneMirror()
        self.welcome = None
        self.connected = False
        self._socket = None
        self._reader = protocol.FrameReader()
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------

    def connect(self, timeout=5.0):
        self._socket = socket.create_connection((self.host, self.port), timeout)
        self._socket.settimeout(0.5)
        self._socket.sendall(
            protocol.encode(
                protocol.hello(self.name, self.engine, self.token, ("mesh", "selection"))
            )
        )
        self.connected = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, name="GameBridge-client")
        self._thread.daemon = True
        self._thread.start()
        return self

    def close(self):
        if self._socket is not None and self.connected:
            try:
                self._socket.sendall(protocol.encode(protocol.goodbye()))
            except OSError:
                pass
        self._stop.set()
        if self._socket is not None:
            try:
                # Shut the socket down before closing it: a reader thread parked
                # in recv() is woken by the shutdown, whereas closing the
                # descriptor underneath it may leave it blocked until its
                # timeout expires.
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.connected = False
        return self

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exception):
        self.close()
        return False

    # -- messages --------------------------------------------------------

    def send(self, message):
        if self._socket is None:
            raise IOError("not connected")
        self._socket.sendall(protocol.encode(message))

    def request_resync(self):
        """Ask for a full scene, after a delta that could not be applied."""
        self.mirror.clear()
        self.send(protocol.Message("resync", {}))

    def select(self, names):
        """Tell FreeCAD what the user just clicked on in the engine."""
        self.send(protocol.selection(names, self.engine))

    def wait_for(self, predicate, timeout=5.0, interval=0.01):
        """Block until ``predicate(self)`` holds.  Returns whether it did."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(self):
                return True
            time.sleep(interval)
        return predicate(self)

    # -- plumbing --------------------------------------------------------

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                data = self._socket.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            try:
                for message in self._reader.feed(data):
                    self._handle(message)
            except protocol.ProtocolError as problem:
                self._report(problem)
                break
        self.connected = False

    def _handle(self, message):
        if message.type == "welcome":
            self.welcome = message.body
        elif message.type == "scene":
            self.mirror.apply_scene(message["manifest"], message.blob)
            self._notify(None)
        elif message.type == "update":
            change = self.mirror.apply_delta(message["delta"], message.blob)
            missing = self.mirror.missing_geometry()
            if missing:
                # The mirror is not what the server thinks it is; carrying on
                # would draw the wrong geometry, so start again from a scene.
                self._report(
                    "geometry is missing for %d node(s); asking for a resync"
                    % len(missing)
                )
                self.request_resync()
                return
            self._notify(change)
        elif message.type == "ping":
            self.send(protocol.pong(message.get("sequence", 0)))
        elif message.type == "error":
            self._report(message.get("reason", "unspecified error"))
        elif message.type == "bye":
            self.connected = False

    def _notify(self, change):
        if self.on_change is not None:
            try:
                self.on_change(self.mirror, change)
            except Exception as problem:  # an engine-side bug must not kill the link
                self._report(problem)

    def _report(self, problem):
        if self.on_error is not None:
            self.on_error(problem)
        else:
            print("GameBridge client: %s" % problem)
