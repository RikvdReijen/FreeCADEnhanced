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
"""The OBJ fallback, whose whole point is that it is readable."""

import os
import shutil
import tempfile
import unittest

from gbcore import Material, Matrix4, Mesh, Node, Scene
from gbformat.obj import OBJWriter, write_obj
from Tests.test_gltf import box_scene


class OBJTest(unittest.TestCase):
    def setUp(self):
        self.text = OBJWriter(box_scene(), "blender", "scene.mtl").to_obj()
        self.lines = self.text.splitlines()

    def counts(self, prefix):
        return [line for line in self.lines if line.startswith(prefix + " ")]

    def test_the_hierarchy_is_flattened_into_objects(self):
        self.assertEqual(self.counts("o"), ["o Red_box", "o Blue_box"])

    def test_every_box_contributes_its_vertices(self):
        self.assertEqual(len(self.counts("v")), 16)
        self.assertEqual(len(self.counts("vn")), 16)
        self.assertEqual(len(self.counts("f")), 24)

    def test_world_transforms_are_baked_in(self):
        """OBJ has no hierarchy, so the blue box must arrive pre-moved."""
        xs = [float(line.split()[1]) for line in self.counts("v")]
        self.assertAlmostEqual(max(xs), 0.07, places=6)  # 50 mm + 20 mm, in metres

    def test_indices_are_one_based_and_run_across_the_file(self):
        faces = self.counts("f")
        first = int(faces[0].split()[1].split("/")[0])
        self.assertEqual(first, 1)
        # The second object's faces must be offset past the first object's 8
        # vertices, which is the bug every hand-rolled OBJ writer starts with.
        second_object = int(faces[12].split()[1].split("/")[0])
        self.assertGreater(second_object, 8)

    def test_materials_are_referenced_and_declared(self):
        self.assertIn("mtllib scene.mtl", self.lines)
        self.assertIn("usemtl Red", self.lines)
        mtl = OBJWriter(box_scene(), "blender", "scene.mtl").to_mtl()
        self.assertIn("newmtl Red", mtl)
        self.assertIn("Pr ", mtl)   # roughness, for importers that know PBR
        self.assertIn("Ns ", mtl)   # and shininess, for the ones that do not

    def test_mirroring_targets_reverse_the_faces(self):
        straight = OBJWriter(box_scene(), "blender").to_obj().splitlines()
        mirrored = OBJWriter(box_scene(), "unreal").to_obj().splitlines()
        a = [l for l in straight if l.startswith("f ")][0].split()[1:]
        b = [l for l in mirrored if l.startswith("f ")][0].split()[1:]
        self.assertEqual([a[0], a[2], a[1]], b)

    def test_hidden_nodes_are_left_out(self):
        scene = box_scene()
        list(scene.walk())[1].visible = False
        text = OBJWriter(scene, "blender").to_obj()
        self.assertNotIn("o Red_box", text)
        self.assertIn("o Blue_box", text)

    def test_transparency_reaches_the_mtl(self):
        scene = Scene("s")
        material = scene.add_material(Material("Glass", (1, 1, 1, 0.25), alpha_mode="BLEND"))
        mesh = Mesh("m", [0, 0, 0, 1, 0, 0, 1, 1, 0], [0, 1, 2], material=material)
        scene.add_root(Node("n", Matrix4(), mesh=scene.add_mesh(mesh)))
        mtl = OBJWriter(scene).to_mtl()
        self.assertIn("d 0.250000", mtl)
        self.assertIn("illum 4", mtl)

    def test_material_names_are_made_safe_and_unique(self):
        scene = Scene("s")
        scene.add_material(Material("Red paint", (1, 0, 0)), deduplicate=False)
        scene.add_material(Material("Red/paint", (0, 1, 0)), deduplicate=False)
        names = OBJWriter(scene).material_names()
        self.assertEqual(names, ["Red_paint", "Red_paint_001"])

    def test_an_empty_scene_writes_a_header_and_nothing_else(self):
        text = OBJWriter(Scene("empty")).to_obj()
        self.assertTrue(text.startswith("# Exported from FreeCAD"))
        self.assertNotIn("\nv ", text)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gamebridge-obj-")
        self.addCleanup(shutil.rmtree, self.directory)

    def test_writing_produces_both_files(self):
        path = write_obj(box_scene(), os.path.join(self.directory, "scene.obj"))
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(self.directory, "scene.mtl")))
        with open(path, encoding="utf-8") as handle:
            self.assertIn("mtllib scene.mtl", handle.read())

    def test_a_scene_without_materials_writes_no_library(self):
        scene = box_scene()
        scene.materials = []
        for mesh in scene.meshes:
            mesh.material = None
        write_obj(scene, os.path.join(self.directory, "plain.obj"))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "plain.mtl")))


if __name__ == "__main__":
    unittest.main()
