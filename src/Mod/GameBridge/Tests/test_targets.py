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
"""The engine profiles, exercised by actually exporting to a temporary folder."""

import json
import os
import shutil
import tempfile
import unittest

from gbcore import Material, Matrix4, Mesh, Node, Scene
from gbtargets import (
    BlenderTarget,
    ExportOptions,
    UnityTarget,
    UnrealTarget,
    export,
    get_target,
    target_names,
)
from Tests.gltfcheck import parse_glb, validate
from Tests.test_gltf import box_scene


class RegistryTest(unittest.TestCase):
    def test_every_advertised_target_can_be_built(self):
        for name in target_names():
            self.assertIsInstance(get_target(name).describe()["name"], str)

    def test_an_unknown_target_names_the_ones_that_exist(self):
        with self.assertRaises(KeyError) as caught:
            get_target("godot")
        self.assertIn("unreal", str(caught.exception))

    def test_a_target_instance_passes_through(self):
        target = UnrealTarget()
        self.assertIs(get_target(target), target)

    def test_the_targets_disagree_about_space_in_the_documented_way(self):
        spaces = {name: get_target(name).convention for name in target_names()}
        self.assertEqual(spaces["unreal"].mm_per_unit, 10.0)
        self.assertEqual(spaces["unity"].mm_per_unit, 1000.0)
        self.assertEqual(spaces["blender"].mm_per_unit, 1000.0)
        self.assertEqual(spaces["blender"].handedness, "right")
        self.assertEqual(spaces["unity"].handedness, "left")


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gamebridge-export-")
        self.addCleanup(shutil.rmtree, self.directory)

    def export(self, target, scene=None, options=None):
        return export(scene or box_scene(), self.directory, target, options)

    def read_manifest(self, result):
        with open(result.manifest_path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_unreal_writes_one_asset_per_mesh_with_its_own_pivot(self):
        result = self.export("unreal")
        names = sorted(os.path.basename(p) for p in result.paths if p.endswith(".glb"))
        self.assertEqual(names, ["SM_Blue_box.glb", "SM_Red_box.glb"])
        for path in result.paths:
            if not path.endswith(".glb"):
                continue
            with open(path, "rb") as handle:
                document, blob = parse_glb(handle.read())
            validate(document, blob)
            # An asset the artist can reuse has its geometry at its own origin,
            # so the file must hold exactly one node and no placement.
            self.assertEqual(len(document["nodes"]), 1)
            self.assertNotIn("matrix", document["nodes"][0])

    def test_unreal_keeps_the_placements_in_the_manifest(self):
        manifest = self.read_manifest(self.export("unreal"))
        blue = manifest["nodes"][0]["children"][1]
        self.assertAlmostEqual(blue["trs"]["translation"][0], 5.0)   # 50 mm in cm
        self.assertEqual(manifest["target"]["name"], "unreal")

    def test_blender_writes_the_whole_hierarchy_into_one_file(self):
        result = self.export("blender")
        meshes = [p for p in result.paths if p.endswith(".glb")]
        self.assertEqual(len(meshes), 1)
        with open(meshes[0], "rb") as handle:
            document, blob = parse_glb(handle.read())
        validate(document, blob)
        self.assertEqual(len(document["nodes"]), 3)

    def test_unity_writes_a_job_file_the_editor_package_watches_for(self):
        result = self.export("unity")
        job_path = os.path.join(self.directory, "scene.gbimport")
        self.assertTrue(os.path.exists(job_path))
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
        self.assertEqual(job["manifest"], "scene.gbscene")
        self.assertTrue(job["createPrefabs"])
        self.assertIn(job_path, result.paths)

    def test_asset_prefixes_follow_each_engine_style_guide(self):
        unreal = self.read_manifest(self.export("unreal"))
        self.assertTrue(all(a["name"].startswith("SM_") for a in unreal["assets"]))
        unity = self.read_manifest(export(box_scene(), self.directory + "/u", "unity"))
        self.assertFalse(any(a["name"].startswith("SM_") for a in unity["assets"]))

    def test_the_importer_script_travels_with_the_export(self):
        for target, script in (
            ("unreal", "gamebridge_unreal_import.py"),
            ("blender", "gamebridge_blender_import.py"),
        ):
            directory = os.path.join(self.directory, target)
            result = export(box_scene(), directory, target)
            self.assertTrue(
                os.path.exists(os.path.join(directory, script)),
                "%s did not ship its importer" % target,
            )
            self.assertEqual(result.warnings, [])

    def test_hidden_objects_are_left_out_by_default(self):
        scene = box_scene()
        list(scene.walk())[1].visible = False
        manifest = self.read_manifest(self.export("unreal", scene))
        self.assertEqual(manifest["stats"]["meshes"], 1)
        self.assertEqual(len(manifest["assets"]), 1)

    def test_hidden_objects_can_be_kept(self):
        scene = box_scene()
        list(scene.walk())[1].visible = False
        options = ExportOptions(include_hidden=True)
        manifest = self.read_manifest(self.export("unreal", scene, options))
        self.assertEqual(len(manifest["assets"]), 2)
        self.assertFalse(manifest["nodes"][0]["children"][0]["visible"])

    def test_dropping_a_hidden_mesh_renumbers_the_rest(self):
        """Removing mesh 0 must not leave node references pointing at it."""
        scene = box_scene()
        list(scene.walk())[1].visible = False
        result = self.export("blender", scene)
        manifest = result.manifest
        remaining = manifest["nodes"][0]["children"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["mesh"], 0)
        self.assertEqual(manifest["stats"]["meshes"], 1)

    def unwelded_scene(self):
        """Two triangles sharing an edge, given the way FreeCAD tessellates:
        face by face, with the shared vertices duplicated."""
        scene = Scene("unwelded", document="Doc")
        mesh = Mesh(
            "Quad",
            [0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 0, 0, 10, 10, 0, 0, 10, 0],
            [0, 1, 2, 3, 4, 5],
        )
        scene.add_root(Node("Quad", Matrix4(), mesh=scene.add_mesh(mesh)))
        return scene

    def test_welding_happens_before_the_write(self):
        scene = self.unwelded_scene()
        self.export("unreal", scene)
        self.assertEqual(scene.meshes[0].vertex_count, 4)

    def test_welding_can_be_switched_off(self):
        scene = self.unwelded_scene()
        self.export("unreal", scene, ExportOptions(weld=False))
        self.assertEqual(scene.meshes[0].vertex_count, 6)

    def test_degenerate_triangles_are_removed_and_reported(self):
        scene = box_scene()
        mesh = scene.meshes[0]
        mesh.indices.extend([0, 0, 1])
        result = self.export("unreal", scene)
        self.assertTrue(any("degenerate" in w for w in result.warnings))

    def test_obj_can_be_written_instead(self):
        result = self.export("unity", options=ExportOptions(mesh_format="obj"))
        objs = [p for p in result.paths if p.endswith(".obj")]
        mtls = [p for p in result.paths if p.endswith(".mtl")]
        self.assertEqual(len(objs), 2)
        self.assertEqual(len(mtls), 2)

    def test_gltf_with_a_sidecar_buffer_is_reported_as_a_file(self):
        result = self.export("blender", options=ExportOptions(mesh_format="gltf"))
        roles = {os.path.splitext(p)[1]: e["role"] for p, e in zip(result.paths, result.files)}
        self.assertEqual(roles.get(".bin"), "buffer")

    def test_an_unsupported_format_is_refused_up_front(self):
        with self.assertRaises(ValueError):
            ExportOptions(mesh_format="fbx")

    def test_a_convention_override_is_honoured(self):
        options = ExportOptions(convention="blender")
        manifest = self.read_manifest(self.export("unreal", options=options))
        self.assertEqual(manifest["target"]["name"], "blender")

    def test_a_subdirectory_keeps_targets_apart(self):
        result = export(box_scene(), self.directory, "unity", ExportOptions(subdirectory="Unity"))
        self.assertTrue(result.directory.endswith(os.path.join(self.directory, "Unity")))
        self.assertTrue(os.path.isdir(result.directory))

    def test_refusing_to_overwrite_is_possible(self):
        self.export("blender")
        with self.assertRaises(IOError):
            self.export("blender", options=ExportOptions(overwrite=False))

    def test_an_empty_scene_exports_a_manifest_and_nothing_else(self):
        result = self.export("unreal", Scene("empty", document="Doc"))
        self.assertEqual(result.manifest["stats"]["triangles"], 0)
        self.assertEqual(result.manifest["assets"], [])
        self.assertTrue(os.path.exists(result.manifest_path))

    def test_colliding_labels_become_distinct_assets(self):
        scene = Scene("dupes", document="Doc")
        material = scene.add_material(Material("Grey"))
        root = scene.add_root(Node("Root"))
        for _ in range(3):
            mesh = Mesh("Pad", [0, 0, 0, 1, 0, 0, 1, 1, 0], [0, 1, 2], material=material)
            root.add(Node("Pad", Matrix4(), mesh=scene.add_mesh(mesh)))
        result = self.export("unreal", scene)
        names = [a["name"] for a in result.manifest["assets"]]
        self.assertEqual(len(set(names)), 3)
        files = [os.path.basename(p) for p in result.paths if p.endswith(".glb")]
        self.assertEqual(len(set(files)), 3)

    def test_the_summary_says_what_happened(self):
        summary = self.export("unreal").summary()
        self.assertIn("Unreal Engine", summary)
        self.assertIn("24 triangle", summary)


if __name__ == "__main__":
    unittest.main()
