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
"""The manifest, which is the only thing the engine importers actually parse."""

import math
import os
import shutil
import tempfile
import unittest

from gbcore import SCENE_FORMAT_VERSION, Matrix4, Node
from gbcore.transform import UNITY, UNREAL
from gbformat.manifest import (
    AssetRecord,
    build_manifest,
    read_manifest,
    write_manifest,
)
from Tests.test_gltf import box_scene


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.scene = box_scene()
        self.assets = [
            AssetRecord.for_mesh(index, "SM_%s" % mesh.name.replace(" ", "_"),
                                 "Meshes/%d.glb" % index, mesh, index)
            for index, mesh in enumerate(self.scene.meshes)
        ]
        self.manifest = build_manifest(self.scene, UNREAL, self.assets)

    def test_it_identifies_itself(self):
        self.assertEqual(self.manifest["format"], "freecad-gamebridge-scene")
        self.assertEqual(self.manifest["version"], SCENE_FORMAT_VERSION)
        self.assertIn("bridgeVersion", self.manifest)
        self.assertTrue(self.manifest["generated"].endswith("Z"))

    def test_it_states_both_spaces(self):
        self.assertEqual(self.manifest["source"], {"unit": "mm", "upAxis": "+Z", "handedness": "right"})
        target = self.manifest["target"]
        self.assertEqual(target["name"], "unreal")
        self.assertEqual(target["mmPerUnit"], 10.0)
        self.assertTrue(target["flipsWinding"])

    def test_the_hierarchy_is_preserved(self):
        roots = self.manifest["nodes"]
        self.assertEqual(len(roots), 1)
        self.assertEqual([c["label"] for c in roots[0]["children"]], ["Red box", "Blue box"])

    def test_transforms_are_in_target_units(self):
        blue = self.manifest["nodes"][0]["children"][1]
        self.assertAlmostEqual(blue["trs"]["translation"][0], 5.0)   # 50 mm in cm
        self.assertAlmostEqual(blue["transform"][3], 5.0)

    def test_the_trs_agrees_with_the_matrix(self):
        """Unity and Unreal read the TRS; Blender reads the matrix."""
        angle = math.radians(30.0)
        cos, sin = math.cos(angle), math.sin(angle)
        scene = box_scene()
        scene.roots[0].transform = Matrix4.from_basis(
            ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0)), (10.0, 20.0, 30.0)
        )
        node = build_manifest(scene, UNITY)["nodes"][0]
        rebuilt = Matrix4(node["transform"])
        translation, rotation, scale = rebuilt.to_trs()
        for expected, actual in zip(node["trs"]["translation"], translation):
            self.assertAlmostEqual(expected, actual, places=9)
        for expected, actual in zip(node["trs"]["rotation"], rotation):
            self.assertAlmostEqual(expected, actual, places=9)
        for expected, actual in zip(node["trs"]["scale"], scale):
            self.assertAlmostEqual(expected, actual, places=9)

    def test_assets_are_linked_to_the_nodes_that_use_them(self):
        red = self.manifest["nodes"][0]["children"][0]
        self.assertEqual(red["asset"], 0)
        asset = self.manifest["assets"][0]
        self.assertEqual(asset["path"], "Meshes/0.glb")
        self.assertEqual(asset["triangles"], 12)
        self.assertIn("checksum", asset)

    def test_engine_names_override_the_labels(self):
        scene = box_scene()
        node = list(scene.walk())[1]
        manifest = build_manifest(scene, UNREAL, node_names={id(node): "SM_RedBox"})
        entry = manifest["nodes"][0]["children"][0]
        self.assertEqual(entry["name"], "SM_RedBox")
        self.assertEqual(entry["label"], "Red box")

    def test_windows_paths_are_normalised(self):
        asset = AssetRecord(0, "a", "Meshes\\sub\\a.glb")
        self.assertEqual(asset.to_dict()["path"], "Meshes/sub/a.glb")

    def test_bounds_stay_a_min_and_a_max_after_mirroring(self):
        """Unreal mirrors Y, which would otherwise leave min above max."""
        bounds = self.manifest["bounds"]
        for axis in range(3):
            self.assertLessEqual(bounds["min"][axis], bounds["max"][axis])
        self.assertAlmostEqual(bounds["min"][1], -2.0)   # 20 mm box, mirrored, in cm

    def test_an_empty_scene_has_no_bounds_but_stays_valid(self):
        from gbcore import Scene

        manifest = build_manifest(Scene("empty"), UNITY)
        self.assertNotIn("bounds", manifest)
        self.assertEqual(manifest["nodes"], [])

    def test_metadata_and_extras_are_carried_through(self):
        scene = box_scene()
        scene.metadata["tessellation"] = {"deviation": 0.1}
        manifest = build_manifest(scene, UNITY, extra={"session": "abc"})
        self.assertEqual(manifest["metadata"]["tessellation"]["deviation"], 0.1)
        self.assertEqual(manifest["session"], "abc")

    def test_the_scene_checksum_is_recorded_for_incremental_imports(self):
        self.assertEqual(self.manifest["checksum"], self.scene.checksum())

    def test_invisible_nodes_are_flagged_rather_than_dropped(self):
        scene = box_scene()
        list(scene.walk())[1].visible = False
        manifest = build_manifest(scene, UNITY)
        self.assertFalse(manifest["nodes"][0]["children"][0]["visible"])


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gamebridge-manifest-")
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = os.path.join(self.directory, "scene.gbscene")

    def test_write_then_read(self):
        manifest = build_manifest(box_scene(), UNREAL)
        write_manifest(manifest, self.path)
        self.assertEqual(read_manifest(self.path)["checksum"], manifest["checksum"])

    def test_a_foreign_file_is_rejected(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"format": "something-else"}')
        with self.assertRaises(ValueError):
            read_manifest(self.path)

    def test_a_newer_format_is_refused_rather_than_misread(self):
        manifest = build_manifest(box_scene(), UNREAL)
        manifest["version"] = SCENE_FORMAT_VERSION + 1
        write_manifest(manifest, self.path)
        with self.assertRaises(ValueError):
            read_manifest(self.path)


if __name__ == "__main__":
    unittest.main()
