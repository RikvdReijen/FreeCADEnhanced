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
"""The glTF writer, checked by re-reading what it wrote."""

import json
import os
import shutil
import tempfile
import unittest

from gbcore import Material, Matrix4, Mesh, Node, Scene
from gbcore.transform import BLENDER, GLTF, UNITY, UNREAL
from gbformat.gltf import GLTFWriter, write_glb, write_gltf
from Tests.gltfcheck import parse_glb, read_accessor, validate


def box_scene():
    """A red box at the origin and a blue one 50 mm along X, under one root."""
    from Tests.stubs import make_box_shape

    scene = Scene("Assembly", document="Doc")
    red = scene.add_material(Material("Red", (0.9, 0.1, 0.1)))
    blue = scene.add_material(Material("Blue", (0.1, 0.2, 0.9)))
    root = scene.add_root(Node("Root"))
    for index, (label, size, material, offset) in enumerate(
        (("Red box", 10.0, red, 0.0), ("Blue box", 20.0, blue, 50.0))
    ):
        shape = make_box_shape(size)
        positions = []
        for point in shape.points:
            positions.extend(point)
        indices = []
        for facet in shape.facets:
            indices.extend(facet)
        mesh = Mesh(label, positions, indices, material=material)
        mesh.compute_normals()
        root.add(Node(label, Matrix4.translation(offset, 0.0, 0.0), mesh=scene.add_mesh(mesh)))
    return scene


class GLBTest(unittest.TestCase):
    def setUp(self):
        self.scene = box_scene()
        self.document, self.blob = parse_glb(GLTFWriter(self.scene).to_glb())

    def test_the_container_is_valid(self):
        self.assertTrue(validate(self.document, self.blob))

    def test_the_scene_graph_survives(self):
        nodes = self.document["nodes"]
        self.assertEqual(len(nodes), 3)
        self.assertEqual(self.document["scenes"][0]["nodes"], [0])
        self.assertEqual([nodes[c]["name"] for c in nodes[0]["children"]],
                         ["Red box", "Blue box"])

    def test_materials_come_across_as_metallic_roughness(self):
        materials = self.document["materials"]
        self.assertEqual(len(materials), 2)
        pbr = materials[0]["pbrMetallicRoughness"]
        self.assertAlmostEqual(pbr["baseColorFactor"][0], 0.9, places=6)
        self.assertIn("metallicFactor", pbr)

    def test_positions_are_converted_to_metres_and_y_up(self):
        positions = read_accessor(self.document, self.blob, 0)
        # The box is 10 mm on a side, so 0.01 m; FreeCAD's Z becomes glTF's Y.
        self.assertAlmostEqual(max(p[1] for p in positions), 0.01, places=6)
        self.assertAlmostEqual(min(p[2] for p in positions), -0.01, places=6)

    def test_position_bounds_are_present_and_correct(self):
        accessor = self.document["accessors"][0]
        positions = read_accessor(self.document, self.blob, 0)
        for axis in range(3):
            self.assertAlmostEqual(accessor["min"][axis], min(p[axis] for p in positions), places=6)
            self.assertAlmostEqual(accessor["max"][axis], max(p[axis] for p in positions), places=6)

    def test_node_transforms_are_column_major(self):
        blue = self.document["nodes"][2]
        # 50 mm along X becomes 0.05 m, in the last row of the column-major form.
        self.assertAlmostEqual(blue["matrix"][12], 0.05, places=6)
        self.assertAlmostEqual(blue["matrix"][13], 0.0, places=6)

    def test_an_identity_transform_is_left_out(self):
        self.assertNotIn("matrix", self.document["nodes"][0])

    def test_indices_stay_sixteen_bit_for_a_small_mesh(self):
        indices = self.document["accessors"][self.document["meshes"][0]["primitives"][0]["indices"]]
        self.assertEqual(indices["componentType"], 5123)

    def test_indices_widen_for_a_large_mesh(self):
        scene = Scene("big")
        count = 70000
        mesh = Mesh("big", [0.0] * (count * 3), [0, 1, 2])
        for i in range(count):
            mesh.positions[i * 3] = float(i)
        scene.add_root(Node("n", mesh=scene.add_mesh(mesh)))
        document, blob = parse_glb(GLTFWriter(scene).to_glb())
        validate(document, blob)
        indices = document["accessors"][document["meshes"][0]["primitives"][0]["indices"]]
        self.assertEqual(indices["componentType"], 5125)

    def test_provenance_is_recorded(self):
        extras = self.document["asset"]["extras"]
        self.assertEqual(extras["freecadDocument"], "Doc")
        self.assertEqual(extras["sourceUnit"], "mm")
        self.assertEqual(extras["axisConvention"]["name"], "gltf")
        self.assertIn("GameBridge", self.document["asset"]["generator"])

    def test_the_freecad_object_name_travels_with_the_node(self):
        scene = box_scene()
        list(scene.walk())[1].source = "Box"
        document, _ = parse_glb(GLTFWriter(scene).to_glb())
        self.assertEqual(document["nodes"][1]["extras"]["freecadName"], "Box")

    def test_hidden_nodes_are_marked_in_extras(self):
        scene = box_scene()
        list(scene.walk())[1].visible = False
        document, _ = parse_glb(GLTFWriter(scene).to_glb())
        self.assertFalse(document["nodes"][1]["extras"]["visible"])

    def test_an_empty_scene_still_produces_a_valid_file(self):
        document, blob = parse_glb(GLTFWriter(Scene("empty")).to_glb())
        validate(document, blob)
        self.assertEqual(document["nodes"], [])
        self.assertEqual(blob, b"")


class ConventionTest(unittest.TestCase):
    """Writing in a pre-converted space, which the engine importers ask for."""

    def positions_for(self, convention):
        document, blob = parse_glb(GLTFWriter(box_scene(), convention).to_glb())
        validate(document, blob)
        return document, blob, read_accessor(document, blob, 0)

    def test_unreal_writes_centimetres_and_mirrors_y(self):
        document, _, positions = self.positions_for(UNREAL)
        self.assertAlmostEqual(max(p[2] for p in positions), 1.0, places=6)
        self.assertAlmostEqual(min(p[1] for p in positions), -1.0, places=6)
        self.assertAlmostEqual(document["nodes"][2]["matrix"][12], 5.0, places=6)

    def test_unity_writes_metres_and_swaps_y_with_z(self):
        _, _, positions = self.positions_for(UNITY)
        self.assertAlmostEqual(max(p[1] for p in positions), 0.01, places=6)
        self.assertAlmostEqual(max(p[2] for p in positions), 0.01, places=6)

    def test_blender_keeps_z_up(self):
        _, _, positions = self.positions_for(BLENDER)
        self.assertAlmostEqual(max(p[2] for p in positions), 0.01, places=6)

    def test_mirroring_conventions_reverse_the_winding(self):
        """The very first triangle has to come out the other way round."""
        straight = GLTFWriter(box_scene(), GLTF).to_glb()
        mirrored = GLTFWriter(box_scene(), UNREAL).to_glb()
        a_doc, a_blob = parse_glb(straight)
        b_doc, b_blob = parse_glb(mirrored)
        a = read_accessor(a_doc, a_blob, 2)[:3]
        b = read_accessor(b_doc, b_blob, 2)[:3]
        self.assertEqual([a[0], a[2], a[1]], b)

    def test_a_mirrored_export_still_faces_outward(self):
        """Winding and geometry have to be mirrored together, not one of them.

        Cross the first triangle's edges in the exported data and check the
        result still points away from the box's centre; flipping only one of
        the two is the mistake that turns a model inside out.
        """
        for convention in (GLTF, BLENDER, UNITY, UNREAL):
            document, blob = parse_glb(GLTFWriter(box_scene(), convention).to_glb())
            positions = read_accessor(document, blob, 0)
            indices = read_accessor(document, blob, 2)
            centre = [sum(p[axis] for p in positions) / len(positions) for axis in range(3)]
            a, b, c = (positions[i] for i in indices[:3])
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            normal = (
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            )
            outward = [a[i] - centre[i] for i in range(3)]
            dot = sum(normal[i] * outward[i] for i in range(3))
            self.assertGreater(dot, 0.0, "%s turned the box inside out" % convention.name)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gamebridge-gltf-")
        self.addCleanup(shutil.rmtree, self.directory)

    def test_write_glb_produces_one_readable_file(self):
        path = write_glb(box_scene(), os.path.join(self.directory, "scene.glb"))
        with open(path, "rb") as handle:
            document, blob = parse_glb(handle.read())
        validate(document, blob)

    def test_write_gltf_produces_a_sidecar_buffer(self):
        path = write_gltf(box_scene(), os.path.join(self.directory, "scene.gltf"))
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["buffers"][0]["uri"], "scene.bin")
        sidecar = os.path.join(self.directory, "scene.bin")
        self.assertTrue(os.path.exists(sidecar))
        self.assertEqual(os.path.getsize(sidecar), document["buffers"][0]["byteLength"])
        validate(document)

    def test_embedded_gltf_needs_no_sidecar(self):
        path = write_gltf(box_scene(), os.path.join(self.directory, "embedded.gltf"), embed=True)
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertTrue(document["buffers"][0]["uri"].startswith("data:application/octet-stream;base64,"))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "embedded.bin")))


if __name__ == "__main__":
    unittest.main()
