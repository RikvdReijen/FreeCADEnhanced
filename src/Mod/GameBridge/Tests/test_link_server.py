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
"""The live link end to end, over real sockets on the loopback interface.

These are not mocked.  A link is threads plus a socket plus framing, and every
interesting failure - a partial read, a client that stops reading, a delta that
arrives before the scene it applies to - lives in the interaction between those,
which a mock socket would hide.  The whole link fits in one process, so there is
no reason not to run the real thing.
"""

import socket
import time
import unittest

from gbcore import Matrix4, Mesh, Node
from gblink import LinkClient, LinkServer, protocol
from gblink.server import ClientConnection
from Tests.test_gltf import box_scene

TIMEOUT = 10.0


def quiet(_message):
    """Servers under test log nothing; a failure prints its own diagnosis."""


class LinkTestCase(unittest.TestCase):
    def make_server(self, **kwargs):
        kwargs.setdefault("port", 0)
        kwargs.setdefault("convention", "unity")
        kwargs.setdefault("logger", quiet)
        server = LinkServer(**kwargs)
        server.start()
        self.addCleanup(server.stop)
        return server

    def make_client(self, server, connect=True, **kwargs):
        kwargs.setdefault("name", "test")
        kwargs.setdefault("engine", "blender")
        client = LinkClient(port=server.port, **kwargs)
        self.addCleanup(client.close)
        if connect:
            client.connect()
        return client

    def connected_client(self, server, **kwargs):
        client = self.make_client(server, **kwargs)
        self.assertTrue(
            client.wait_for(lambda c: c.welcome is not None, TIMEOUT),
            "the server never sent a welcome",
        )
        return client

    def assertMirrors(self, client, nodes, meshes=None):
        ok = client.wait_for(
            lambda c: len(c.mirror.nodes) == nodes
            and (meshes is None or len(c.mirror.meshes) == meshes),
            TIMEOUT,
        )
        self.assertTrue(ok, "mirror settled at %s" % (client.mirror.stats(),))


class HandshakeTest(LinkTestCase):
    def test_a_client_is_welcomed_with_the_target_space(self):
        server = self.make_server()
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server)
        self.assertEqual(client.welcome["target"]["name"], "unity")
        self.assertEqual(client.welcome["document"], "Doc")
        self.assertEqual(client.welcome["protocol"], protocol.PROTOCOL_VERSION)

    def test_a_client_that_connects_after_a_publish_still_gets_the_scene(self):
        server = self.make_server()
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server)
        self.assertMirrors(client, 3, 2)

    def test_a_client_that_connects_before_a_publish_gets_it_when_it_happens(self):
        server = self.make_server()
        client = self.connected_client(server)
        self.assertEqual(client.mirror.nodes, {})
        server.publish(box_scene(), "Doc")
        self.assertMirrors(client, 3, 2)

    def test_the_server_lists_who_is_connected(self):
        server = self.make_server()
        self.connected_client(server, name="unity-editor", engine="unity")
        self.assertTrue(
            _eventually(lambda: server.client_count == 1), "the client never registered"
        )
        described = server.describe()["clients"][0]
        self.assertEqual(described["engine"], "unity")
        self.assertIn("unity-editor", described["name"])

    def test_a_disconnecting_client_is_forgotten(self):
        server = self.make_server()
        client = self.connected_client(server)
        self.assertTrue(_eventually(lambda: server.client_count == 1))
        client.close()
        self.assertTrue(
            _eventually(lambda: server.client_count == 0), "the client was not removed"
        )


class AuthenticationTest(LinkTestCase):
    def test_the_right_token_is_accepted(self):
        server = self.make_server(token="s3cret")
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server, token="s3cret")
        self.assertMirrors(client, 3)

    def test_the_wrong_token_gets_nothing_and_is_disconnected(self):
        server = self.make_server(token="s3cret")
        server.publish(box_scene(), "Doc")
        errors = []
        client = self.make_client(server, token="wrong", on_error=errors.append)
        self.assertTrue(_eventually(lambda: bool(errors) or not client.connected, 5.0))
        self.assertEqual(client.mirror.nodes, {})
        self.assertTrue(_eventually(lambda: server.client_count == 0, 5.0))

    def test_listening_beyond_loopback_is_refused_by_default(self):
        with self.assertRaises(ValueError) as caught:
            LinkServer(host="0.0.0.0", port=0)
        self.assertIn("could read the document", str(caught.exception))

    def test_listening_beyond_loopback_needs_a_token(self):
        with self.assertRaises(ValueError):
            LinkServer(host="0.0.0.0", port=0, allow_remote=True)
        server = LinkServer(host="0.0.0.0", port=0, allow_remote=True, token="t", logger=quiet)
        self.assertEqual(server.host, "0.0.0.0")


class UpdateTest(LinkTestCase):
    def setUp(self):
        self.server = self.make_server()
        self.scene = box_scene()
        self.server.publish(self.scene, "Doc")
        self.client = self.connected_client(self.server)
        self.assertMirrors(self.client, 3, 2)

    def test_a_move_reaches_the_client_without_resending_geometry(self):
        blob_sizes = []
        list(self.scene.walk())[1].transform = Matrix4.translation(30.0, 0.0, 0.0)
        self.server.publish(self.scene)
        moved = self.client.wait_for(
            lambda c: c.mirror.nodes["path:Root/Red box"]["trs"]["translation"][0] == 0.03,
            TIMEOUT,
        )
        self.assertTrue(moved, "the move never arrived")
        self.assertEqual(len(self.client.mirror.meshes), 2)
        del blob_sizes

    def test_new_geometry_reaches_the_client(self):
        mesh = Mesh("Extra", [0, 0, 0, 5, 0, 0, 5, 5, 0], [0, 1, 2])
        self.scene.roots[0].add(
            Node("Extra", Matrix4(), mesh=self.scene.add_mesh(mesh))
        )
        self.server.publish(self.scene)
        self.assertMirrors(self.client, 4, 3)
        self.assertIsNotNone(self.client.mirror.mesh_for("path:Root/Extra"))

    def test_a_deletion_reaches_the_client(self):
        self.scene.roots[0].children.pop(0)
        self.server.publish(self.scene)
        self.assertMirrors(self.client, 2, 1)
        self.assertNotIn("path:Root/Red box", self.client.mirror.nodes)

    def test_the_change_callback_says_what_changed(self):
        changes = []
        self.client.on_change = lambda mirror, change: changes.append(change)
        list(self.scene.walk())[1].transform = Matrix4.translation(1.0, 0.0, 0.0)
        self.server.publish(self.scene)
        self.assertTrue(_eventually(lambda: bool(changes), TIMEOUT))
        self.assertEqual(changes[-1]["changed"], ["path:Root/Red box"])
        self.assertEqual(changes[-1]["added"], [])

    def test_publishing_an_unchanged_scene_sends_nothing(self):
        sequence = self.client.mirror.sequence
        for _ in range(5):
            self.server.publish(self.scene)
        time.sleep(0.2)
        self.assertEqual(self.client.mirror.sequence, sequence)

    def test_a_client_can_ask_for_a_full_resync(self):
        self.client.request_resync()
        self.assertTrue(
            self.client.wait_for(lambda c: len(c.mirror.nodes) == 3 and c.mirror.sequence > 1, TIMEOUT)
        )
        self.assertEqual(len(self.client.mirror.meshes), 2)

    def test_the_mirror_geometry_is_in_the_client_space(self):
        mesh = self.client.mirror.mesh_for("path:Root/Red box")
        # A 10 mm box, in metres, with Unity's Y and Z swapped.
        self.assertAlmostEqual(max(mesh["positions"]), 0.01, places=6)

    def test_a_hundred_rapid_updates_all_land_in_order(self):
        for step in range(100):
            list(self.scene.walk())[1].transform = Matrix4.translation(float(step), 0.0, 0.0)
            self.server.publish(self.scene)
        arrived = self.client.wait_for(
            lambda c: abs(
                c.mirror.nodes["path:Root/Red box"]["trs"]["translation"][0] - 0.099
            ) < 1e-9,
            TIMEOUT,
        )
        self.assertTrue(arrived, "the last update never arrived")


class SelectionTest(LinkTestCase):
    def test_an_engine_selection_reaches_the_freecad_side(self):
        server = self.make_server()
        received = []
        server.selection_callback = lambda connection, names: received.append(names)
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server)
        client.select(["Box", "Box001"])
        self.assertTrue(_eventually(lambda: bool(received), TIMEOUT))
        self.assertEqual(received[0], ["Box", "Box001"])

    def test_a_callback_that_raises_does_not_kill_the_link(self):
        server = self.make_server()

        def explode(connection, names):
            raise RuntimeError("the GUI side blew up")

        server.selection_callback = explode
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server)
        client.select(["Box"])
        time.sleep(0.2)
        self.assertTrue(client.connected)
        self.assertEqual(server.client_count, 1)


class RobustnessTest(LinkTestCase):
    def test_something_that_is_not_a_client_is_disconnected_not_crashed(self):
        server = self.make_server()
        server.publish(box_scene(), "Doc")
        raw = socket.create_connection(("127.0.0.1", server.port), 5.0)
        self.addCleanup(raw.close)
        raw.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertTrue(_eventually(lambda: server.client_count == 0, 5.0))
        self.assertTrue(server.running)
        # And a real client still works afterwards.
        client = self.connected_client(server)
        self.assertMirrors(client, 3)

    def test_a_client_that_stops_reading_never_blocks_the_publisher(self):
        """The property that keeps FreeCAD's GUI thread responsive.

        An engine that stops reading - a modal dialog, a machine that started
        swapping - must not be able to stall a recompute.  The connection here
        is given a socket nobody reads and no writer thread, which is exactly
        the state a stalled engine puts it in, and then asked to take far more
        messages than its queue holds.
        """
        server = self.make_server()
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        connection = ClientConnection(server, ours, ("127.0.0.1", 1))
        connection.authenticated = True

        limit = connection.queue.maxsize
        accepted = 0
        for index in range(limit * 4):
            if connection.send(protocol.ping(index)):
                accepted += 1
            # Whatever happens, the queue must not keep growing.
            self.assertLessEqual(connection.queue.qsize(), limit)

        self.assertLess(accepted, limit * 4)
        self.assertTrue(
            connection.needs_full,
            "a client whose backlog was dropped must be resynced, not left with "
            "deltas it can no longer apply",
        )
        self.assertTrue(connection.session.has_state is False)

    def test_two_clients_keep_independent_views(self):
        server = self.make_server()
        scene = box_scene()
        server.publish(scene, "Doc")
        first = self.connected_client(server, name="first")
        self.assertMirrors(first, 3)

        scene.roots[0].children.pop(0)
        server.publish(scene)
        self.assertMirrors(first, 2)

        # The second client has never seen the deleted part, so it must be sent
        # a scene rather than a delta that refers to something it does not have.
        second = self.connected_client(server, name="second")
        self.assertMirrors(second, 2, 1)
        self.assertEqual(second.mirror.sequence, 1)

    def test_stopping_the_server_closes_the_clients(self):
        server = self.make_server()
        server.publish(box_scene(), "Doc")
        client = self.connected_client(server)
        server.stop()
        self.assertTrue(_eventually(lambda: not client.connected, 5.0))


def _eventually(predicate, timeout=TIMEOUT, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


if __name__ == "__main__":
    unittest.main()
