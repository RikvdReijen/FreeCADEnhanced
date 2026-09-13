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
"""The wire format and the change detection sitting on top of it."""

import struct
import unittest
import zlib

from gbcore import Material, Matrix4, Mesh, Node, Scene
from gbcore.transform import UNITY, UNREAL
from gblink import protocol
from gblink.session import LinkSession, node_key, snapshot
from Tests.test_gltf import box_scene


class FrameTest(unittest.TestCase):
    def test_a_message_survives_the_round_trip(self):
        message = protocol.Message("hello", {"client": "blender", "n": 3}, b"\x01\x02")
        back = protocol.decode(protocol.encode(message))
        self.assertEqual(back.type, "hello")
        self.assertEqual(back["client"], "blender")
        self.assertEqual(back.blob, b"\x01\x02")

    def test_small_messages_are_not_compressed(self):
        frame = protocol.encode(protocol.ping(1))
        self.assertEqual(frame[4] & protocol.FLAG_JSON_DEFLATED, 0)

    def test_large_payloads_are_compressed_and_still_decode(self):
        message = protocol.Message("scene", {"junk": "x" * 50000}, b"y" * 50000)
        frame = protocol.encode(message)
        self.assertTrue(frame[4] & protocol.FLAG_JSON_DEFLATED)
        self.assertTrue(frame[4] & protocol.FLAG_BLOB_DEFLATED)
        self.assertLess(len(frame), 20000)
        back = protocol.decode(frame)
        self.assertEqual(back["junk"], "x" * 50000)
        self.assertEqual(back.blob, b"y" * 50000)

    def test_compression_can_be_refused(self):
        message = protocol.Message("scene", {"junk": "x" * 50000})
        self.assertEqual(protocol.encode(message, compress=False)[4], 0)

    def test_a_foreign_frame_is_rejected(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"HTTP/1.1 200 OK\r\n\r\n" + b"\x00" * 32)

    def test_a_truncated_frame_is_rejected(self):
        frame = protocol.encode(protocol.ping())
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(frame[:-2])

    def test_a_frame_without_a_type_is_rejected(self):
        payload = b'{"nope":1}'
        frame = protocol.MAGIC + struct.pack("<BxxxII", 0, len(payload), 0) + payload
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(frame)

    def test_a_frame_that_is_not_json_is_rejected(self):
        payload = b"not json at all"
        frame = protocol.MAGIC + struct.pack("<BxxxII", 0, len(payload), 0) + payload
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(frame)


class FrameReaderTest(unittest.TestCase):
    """TCP splits and coalesces at will; the reader has to cope with both."""

    def setUp(self):
        self.reader = protocol.FrameReader()

    def test_a_message_split_across_reads_is_reassembled(self):
        frame = protocol.encode(protocol.Message("scene", {"a": 1}, b"z" * 100))
        for cut in (1, 8, 17, 40):
            self.reader.reset()
            self.assertEqual(self.reader.feed(frame[:cut]), [])
            self.assertGreater(self.reader.pending, 0)
            messages = self.reader.feed(frame[cut:])
            self.assertEqual(len(messages), 1)
            self.assertEqual(self.reader.pending, 0)

    def test_several_messages_in_one_read_all_come_out(self):
        data = b"".join(protocol.encode(protocol.ping(i)) for i in range(5))
        messages = self.reader.feed(data)
        self.assertEqual([m["sequence"] for m in messages], [0, 1, 2, 3, 4])

    def test_a_byte_at_a_time_still_works(self):
        frame = protocol.encode(protocol.Message("update", {"delta": {}}, b"q" * 64))
        received = []
        for index in range(len(frame)):
            received.extend(self.reader.feed(frame[index:index + 1]))
        self.assertEqual(len(received), 1)

    def test_garbage_in_the_stream_is_fatal_rather_than_silently_skipped(self):
        with self.assertRaises(protocol.ProtocolError):
            self.reader.feed(b"GET / HTTP/1.0\r\n\r\n" + b"\x00" * 32)

    def test_an_absurd_length_is_refused_before_anything_is_allocated(self):
        header = protocol.MAGIC + struct.pack("<BxxxII", 0, 0xFFFFFFF0, 0)
        with self.assertRaises(protocol.ProtocolError):
            self.reader.feed(header)


class MeshPayloadTest(unittest.TestCase):
    def mesh(self):
        mesh = Mesh("part", [0, 0, 0, 10, 0, 0, 10, 10, 0], [0, 1, 2])
        return mesh.compute_normals()

    def test_geometry_survives_the_round_trip(self):
        descriptors, blob = protocol.mesh_payload([("abc", self.mesh())], UNITY)
        decoded = protocol.decode_mesh_payload(descriptors[0], blob)
        self.assertEqual(descriptors[0]["id"], "abc")
        self.assertEqual(descriptors[0]["vertexCount"], 3)
        self.assertEqual(len(decoded["positions"]), 9)
        self.assertEqual(decoded["indices"], [0, 2, 1])   # Unity mirrors, so it flips

    def test_the_payload_is_in_the_target_space(self):
        descriptors, blob = protocol.mesh_payload([("a", self.mesh())], UNREAL)
        decoded = protocol.decode_mesh_payload(descriptors[0], blob)
        # 10 mm becomes 1 cm, and Unreal mirrors Y.
        self.assertAlmostEqual(decoded["positions"][3], 1.0, places=5)
        self.assertAlmostEqual(decoded["positions"][7], -1.0, places=5)

    def test_several_meshes_share_one_blob_without_overlapping(self):
        meshes = [("a", self.mesh()), ("b", self.mesh())]
        descriptors, blob = protocol.mesh_payload(meshes, UNITY)
        first_end = descriptors[0]["indices"][0] + descriptors[0]["indices"][1]
        self.assertEqual(descriptors[1]["positions"][0], first_end)
        for descriptor in descriptors:
            self.assertEqual(len(protocol.decode_mesh_payload(descriptor, blob)["positions"]), 9)

    def test_a_descriptor_pointing_outside_the_blob_is_refused(self):
        descriptors, blob = protocol.mesh_payload([("a", self.mesh())], UNITY)
        descriptors[0]["positions"] = [0, len(blob) + 64]
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_mesh_payload(descriptors[0], blob)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.scene = box_scene()
        self.session = LinkSession("unity")

    def full(self):
        return self.session.full_scene(self.scene).body["manifest"]

    def test_the_first_message_carries_everything(self):
        body = self.full()
        self.assertEqual(len(body["nodes"]), 3)
        self.assertEqual(len(body["meshes"]), 2)
        self.assertEqual(len(body["materials"]), 2)

    def test_an_unchanged_scene_produces_no_message_at_all(self):
        self.full()
        self.assertIsNone(self.session.update(self.scene))

    def test_a_moved_part_costs_a_transform_and_no_geometry(self):
        self.full()
        list(self.scene.walk())[1].transform = Matrix4.translation(5.0, 0.0, 0.0)
        message = self.session.update(self.scene)
        delta = message["delta"]
        self.assertEqual(len(delta["nodes"]), 1)
        self.assertEqual(delta["meshes"], [])
        self.assertEqual(message.blob, b"")

    def test_changed_geometry_is_sent_and_the_old_copy_released(self):
        self.full()
        self.scene.meshes[0].positions[0] += 1.0
        delta = self.session.update(self.scene)["delta"]
        self.assertEqual(len(delta["meshes"]), 1)
        self.assertEqual(len(delta["droppedMeshes"]), 1)

    def test_identical_geometry_is_only_sent_once(self):
        scene = Scene("twins", document="Doc")
        material = scene.add_material(Material("Grey"))
        root = scene.add_root(Node("Root"))
        for name in ("Left", "Right"):
            mesh = Mesh("Pad", [0, 0, 0, 1, 0, 0, 1, 1, 0], [0, 1, 2], material=material)
            root.add(Node(name, Matrix4.translation(0, 0, 0), mesh=scene.add_mesh(mesh)))
        body = LinkSession("unity").full_scene(scene).body["manifest"]
        self.assertEqual(len(body["meshes"]), 1)
        self.assertEqual(len({n["mesh"] for n in body["nodes"] if "mesh" in n}), 1)

    def test_a_removed_part_is_reported_as_removed(self):
        self.full()
        self.scene.roots[0].children.pop(0)
        delta = self.session.update(self.scene)["delta"]
        self.assertEqual(delta["removedNodes"], ["path:Root/Red box"])
        self.assertEqual(len(delta["droppedMeshes"]), 1)

    def test_an_added_part_arrives_with_its_geometry(self):
        self.full()
        mesh = Mesh("New", [0, 0, 0, 1, 0, 0, 1, 1, 0], [0, 1, 2])
        self.scene.roots[0].add(Node("New", Matrix4(), mesh=self.scene.add_mesh(mesh)))
        delta = self.session.update(self.scene)["delta"]
        self.assertEqual([n["name"] for n in delta["nodes"]], ["New"])
        self.assertEqual(len(delta["meshes"]), 1)

    def test_visibility_alone_is_a_change(self):
        self.full()
        list(self.scene.walk())[1].visible = False
        delta = self.session.update(self.scene)["delta"]
        self.assertEqual(len(delta["nodes"]), 1)
        self.assertFalse(delta["nodes"][0]["visible"])

    def test_float_noise_in_an_untouched_placement_is_not_a_change(self):
        """A recompute can perturb the last bit of a matrix nobody edited."""
        self.full()
        node = list(self.scene.walk())[2]
        values = list(node.transform.m)
        values[3] += 1e-12
        node.transform = Matrix4(values)
        self.scene.meshes[0].positions[0] += 1.0   # force some change to send
        delta = self.session.update(self.scene)["delta"]
        self.assertNotIn("Blue box", [n["name"] for n in delta["nodes"]])

    def test_materials_are_only_resent_when_they_change(self):
        self.full()
        self.scene.meshes[0].positions[0] += 1.0
        self.assertNotIn("materials", self.session.update(self.scene)["delta"])
        self.scene.materials[0].base_color = (0.0, 1.0, 0.0, 1.0)
        self.assertIn("materials", self.session.update(self.scene)["delta"])

    def test_the_sequence_number_advances_with_every_message(self):
        self.full()
        numbers = []
        for offset in range(3):
            self.scene.meshes[0].positions[0] += 1.0
            numbers.append(self.session.update(self.scene)["delta"]["sequence"])
        self.assertEqual(numbers, [2, 3, 4])

    def test_the_first_update_without_state_is_a_full_scene(self):
        message = self.session.update(self.scene)
        self.assertEqual(message.type, "scene")

    def test_a_reset_forces_the_next_message_to_be_full(self):
        self.full()
        self.session.reset()
        self.assertEqual(self.session.update(self.scene).type, "scene")

    def test_transforms_are_converted_into_the_client_space(self):
        body = self.full()
        blue = [n for n in body["nodes"] if n["name"] == "Blue box"][0]
        self.assertAlmostEqual(blue["trs"]["translation"][0], 0.05)   # 50 mm in m


class NodeIdentityTest(unittest.TestCase):
    def multi_material_scene(self):
        """What the walker builds for a solid painted with two colours: a
        parent and two children, all three carrying the same object name."""
        scene = Scene("painted", document="Doc")
        parent = scene.add_root(Node("Pad", Matrix4(), source="Pad"))
        for index in range(2):
            mesh = Mesh("Pad_%d" % index, [0, 0, 0, 1, 0, 0, 1, 1, float(index)], [0, 1, 2])
            parent.add(
                Node("Pad_%d" % index, Matrix4(), mesh=scene.add_mesh(mesh), source="Pad")
            )
        return scene

    def test_nodes_sharing_an_object_name_stay_distinct(self):
        """Collapsing them loses geometry: a two-material solid would arrive
        with one of its halves missing, and forty links to one body would
        arrive as one body."""
        scene = self.multi_material_scene()
        nodes, order = snapshot(scene)
        self.assertEqual(len(order), 3)
        self.assertEqual(len(set(order)), 3)

    def test_every_mesh_is_still_sent(self):
        body = LinkSession("unity").full_scene(self.multi_material_scene()).body["manifest"]
        self.assertEqual(len(body["nodes"]), 3)
        self.assertEqual(len(body["meshes"]), 2)

    def test_the_keys_are_the_same_on_the_next_recompute(self):
        first, _ = snapshot(self.multi_material_scene())
        second, _ = snapshot(self.multi_material_scene())
        self.assertEqual(sorted(first), sorted(second))

    def test_many_links_to_one_body_stay_separate(self):
        scene = Scene("assembly", document="Doc")
        root = scene.add_root(Node("Assembly"))
        mesh = scene.add_mesh(Mesh("Screw", [0, 0, 0, 1, 0, 0, 1, 1, 0], [0, 1, 2]))
        for index in range(40):
            root.add(
                Node("Screw", Matrix4.translation(index * 10.0, 0, 0), mesh=mesh, source="Screw")
            )
        _, order = snapshot(scene)
        self.assertEqual(len(set(order)), 41)

    def test_a_freecad_object_name_is_the_key_when_there_is_one(self):
        node = Node("Label", Matrix4(), source="Box001")
        self.assertEqual(node_key(node, ["Root", "Label"]), "obj:Box001")

    def test_renaming_a_labelled_object_does_not_change_its_identity(self):
        scene = Scene("s")
        scene.add_root(Node("Old label", Matrix4(), source="Box"))
        first, _ = snapshot(scene)
        scene.roots[0].name = "New label"
        second, _ = snapshot(scene)
        self.assertEqual(list(first), list(second))

    def test_nodes_without_a_source_fall_back_to_their_path(self):
        node = Node("Group", Matrix4())
        self.assertEqual(node_key(node, ["Root", "Group"]), "path:Root/Group")


if __name__ == "__main__":
    unittest.main()
