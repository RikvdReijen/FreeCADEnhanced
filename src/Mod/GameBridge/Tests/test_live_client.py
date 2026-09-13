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
"""The Blender live-link add-on's two testable halves.

Finding FreeCAD's module folder and turning a scene mirror into Blender-shaped
data are both plain Python, and both are the sort of thing that breaks quietly:
a wrong path gives an add-on that cannot connect, and a mis-shaped vertex list
gives meshes that look almost right.
"""

import os
import unittest

from gbcore import Matrix4
from gblink import LinkClient, LinkServer, SceneMirror
from Tests.test_clients import load_client
from Tests.test_gltf import box_scene

live = load_client(os.path.join("blender", "gamebridge_blender_live.py"), "gb_blender_live")


class ModuleSearchTest(unittest.TestCase):
    def test_an_explicit_path_is_tried_first(self):
        paths = live.candidate_paths("/somewhere/GameBridge")
        self.assertEqual(paths[0], "/somewhere/GameBridge")

    def test_the_environment_variable_is_honoured(self):
        os.environ["GAMEBRIDGE_MODULE"] = "/from/environment"
        self.addCleanup(os.environ.pop, "GAMEBRIDGE_MODULE", None)
        self.assertIn("/from/environment", live.candidate_paths())

    def test_a_tilde_is_expanded(self):
        self.assertTrue(live.candidate_paths("~/GameBridge")[0].startswith(os.path.expanduser("~")))

    def test_the_usual_install_locations_are_searched(self):
        paths = live.candidate_paths()
        self.assertTrue(any("FreeCAD" in path for path in paths))
        self.assertTrue(any(path.startswith("/usr/") for path in paths))

    def test_a_configured_path_beats_an_installed_one(self):
        self.assertEqual(live.find_module_path("/first", exists=lambda path: True), "/first")

    def test_an_installed_copy_is_used_when_the_configured_one_is_not_there(self):
        found = live.find_module_path(
            "/first", exists=lambda path: path.startswith("/usr/share/freecad")
        )
        self.assertEqual(found, "/usr/share/freecad/Mod/GameBridge")

    def test_nothing_found_is_reported_as_none(self):
        self.assertIsNone(live.find_module_path("/nowhere", exists=lambda path: False))

    def test_a_source_checkout_is_found_without_configuration(self):
        """The module we are running from is itself a candidate."""
        self.assertIsNotNone(live.find_module_path())


class MeshDataTest(unittest.TestCase):
    def mirror(self):
        """A mirror filled the way a real session fills it: over the link."""
        server = LinkServer(port=0, convention="blender", logger=lambda m: None)
        server.start()
        self.addCleanup(server.stop)
        server.publish(box_scene(), "Doc")
        client = LinkClient(port=server.port, name="t", engine="blender")
        self.addCleanup(client.close)
        client.connect()
        self.assertTrue(client.wait_for(lambda c: len(c.mirror.nodes) == 3, 10.0))
        return client.mirror

    def test_every_node_becomes_an_object_in_order(self):
        objects = live.mesh_data_from_mirror(self.mirror())
        self.assertEqual([o["name"] for o in objects], ["Root", "Red box", "Blue box"])

    def test_vertices_are_grouped_into_triples(self):
        objects = live.mesh_data_from_mirror(self.mirror())
        box = objects[1]
        self.assertEqual(len(box["vertices"]), 8)
        for vertex in box["vertices"]:
            self.assertEqual(len(vertex), 3)

    def test_faces_are_triangles_of_valid_indices(self):
        box = live.mesh_data_from_mirror(self.mirror())[1]
        self.assertEqual(len(box["faces"]), 12)
        for face in box["faces"]:
            self.assertEqual(len(face), 3)
            for index in face:
                self.assertLess(index, len(box["vertices"]))

    def test_the_matrix_is_a_flat_sixteen_for_blender_to_reshape(self):
        blue = live.mesh_data_from_mirror(self.mirror())[2]
        self.assertEqual(len(blue["matrix"]), 16)
        # 50 mm along X, in metres, in the last column of the row-major form.
        self.assertAlmostEqual(blue["matrix"][3], 0.05)

    def test_the_hierarchy_is_carried_as_parent_keys(self):
        objects = live.mesh_data_from_mirror(self.mirror())
        keys = {o["key"] for o in objects}
        self.assertIsNone(objects[0]["parent"])
        for entry in objects[1:]:
            self.assertIn(entry["parent"], keys)

    def test_a_group_node_has_no_geometry(self):
        root = live.mesh_data_from_mirror(self.mirror())[0]
        self.assertEqual(root["vertices"], [])
        self.assertEqual(root["faces"], [])

    def test_provenance_survives_to_the_blender_side(self):
        server = LinkServer(port=0, convention="blender", logger=lambda m: None)
        server.start()
        self.addCleanup(server.stop)
        scene = box_scene()
        list(scene.walk())[1].source = "Box"
        server.publish(scene, "Doc")
        client = LinkClient(port=server.port, name="t", engine="blender")
        self.addCleanup(client.close)
        client.connect()
        self.assertTrue(client.wait_for(lambda c: len(c.mirror.nodes) == 3, 10.0))
        objects = live.mesh_data_from_mirror(client.mirror)
        self.assertEqual(objects[1]["source"], "Box")

    def test_an_empty_mirror_produces_nothing(self):
        self.assertEqual(live.mesh_data_from_mirror(SceneMirror()), [])

    def test_a_node_referring_to_geometry_that_never_arrived_is_left_bare(self):
        """Rather than raising: the client asks for a resync, and in the
        meantime an object with no vertices is better than a traceback."""
        mirror = SceneMirror()
        mirror.nodes["k"] = {
            "key": "k", "name": "Lonely", "mesh": "missing",
            "transform": list(Matrix4().m), "visible": True,
        }
        mirror.order.append("k")
        objects = live.mesh_data_from_mirror(mirror)
        self.assertEqual(objects[0]["vertices"], [])


if __name__ == "__main__":
    unittest.main()
