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
"""The engine-side importers, tested where they can be: their planning half.

The Blender and Unreal clients are split so that everything deciding *what* to
import is plain Python and everything touching ``bpy`` or ``unreal`` is a short
function that does what the plan says.  That split is what makes these tests
possible at all, and it is worth keeping: a bridge whose engine side is only
ever tested by hand is a bridge that breaks silently on the next release.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

from gbtargets import export
from Tests.test_gltf import box_scene

CLIENTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clients"
)


def load_client(relative_path, name):
    """Import a client script by path; they are not on sys.path by design."""
    path = os.path.join(CLIENTS, relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blender_client = load_client(
    os.path.join("blender", "gamebridge_blender_import.py"), "gb_blender_client"
)
unreal_client = load_client(
    os.path.join("unreal", "gamebridge_unreal_import.py"), "gb_unreal_client"
)


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gamebridge-client-")
        self.addCleanup(shutil.rmtree, self.directory)

    def exported(self, target):
        result = export(box_scene(), os.path.join(self.directory, target), target)
        return result.manifest_path


class BlenderClientTest(ClientTestCase):
    def setUp(self):
        ClientTestCase.setUp(self)
        self.path = self.exported("blender")
        self.manifest = blender_client.read_manifest(self.path)
        self.plan = blender_client.plan_import(self.manifest, self.path)

    def test_it_finds_the_file_the_manifest_points_at(self):
        self.assertEqual(len(self.plan["files"]), 1)
        entry = self.plan["files"][0]
        self.assertTrue(entry["exists"], entry["path"])
        self.assertEqual(entry["format"], "glb")

    def test_it_plans_one_annotation_per_node_in_tree_order(self):
        names = [a["label"] for a in self.plan["annotations"]]
        self.assertEqual(names, ["Root", "Red box", "Blue box"])
        self.assertIsNone(self.plan["annotations"][0]["parent"])
        self.assertEqual(self.plan["annotations"][1]["parent"], "Root")

    def test_it_knows_the_export_is_already_converted(self):
        self.assertTrue(self.plan["pre_converted"])
        self.assertEqual(self.plan["target"], "blender")

    def test_the_collection_is_named_after_the_scene(self):
        self.assertEqual(self.plan["collection"], "Assembly")
        named = blender_client.plan_import(self.manifest, self.path, "Custom")
        self.assertEqual(named["collection"], "Custom")

    def test_provenance_reaches_the_custom_properties(self):
        scene = box_scene()
        list(scene.walk())[1].source = "Box"
        result = export(scene, os.path.join(self.directory, "sourced"), "blender")
        plan = blender_client.plan_import(result.manifest, result.manifest_path)
        self.assertEqual(plan["annotations"][1]["properties"]["freecad_object"], "Box")
        self.assertEqual(plan["annotations"][1]["properties"]["freecad_document"], "Doc")

    def test_windows_style_paths_in_a_manifest_still_resolve(self):
        manifest = dict(self.manifest)
        manifest["assets"] = [dict(manifest["assets"][0])]
        manifest["assets"][0]["path"] = "Meshes/deep/file.glb"
        plan = blender_client.plan_import(manifest, self.path)
        self.assertEqual(
            plan["files"][0]["path"],
            os.path.normpath(os.path.join(os.path.dirname(self.path), "Meshes", "deep", "file.glb")),
        )

    def test_a_missing_file_is_reported_rather_than_half_imported(self):
        plan = dict(self.plan)
        plan["files"] = [dict(plan["files"][0], exists=False, path="/nowhere/x.glb")]
        with self.assertRaises(blender_client.ImportError_) as caught:
            blender_client.check_plan(plan)
        self.assertIn("/nowhere/x.glb", str(caught.exception))

    def test_a_format_blender_cannot_read_is_refused_up_front(self):
        plan = dict(self.plan)
        plan["files"] = [dict(plan["files"][0], format="step")]
        with self.assertRaises(blender_client.ImportError_):
            blender_client.check_plan(plan)

    def test_a_complete_plan_passes_the_check(self):
        self.assertIs(blender_client.check_plan(self.plan), self.plan)

    def test_a_foreign_file_is_refused(self):
        path = os.path.join(self.directory, "not-ours.gbscene")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"format": "something-else"}')
        with self.assertRaises(blender_client.ImportError_):
            blender_client.read_manifest(path)

    def test_a_newer_manifest_asks_for_a_newer_add_on(self):
        path = os.path.join(self.directory, "future.gbscene")
        manifest = dict(self.manifest, version=99)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        with self.assertRaises(blender_client.ImportError_) as caught:
            blender_client.read_manifest(path)
        self.assertIn("newer", str(caught.exception))

    def test_the_summary_mentions_what_arrived(self):
        summary = blender_client.describe_plan(self.plan)
        self.assertIn("Assembly", summary)
        self.assertIn("24 triangle", summary)


class UnrealClientTest(ClientTestCase):
    def setUp(self):
        ClientTestCase.setUp(self)
        self.path = self.exported("unreal")
        self.manifest = unreal_client.read_manifest(self.path)
        self.plan = unreal_client.plan_import(self.manifest, self.path)

    def test_assets_go_to_a_content_path_named_after_the_document(self):
        self.assertEqual(self.plan["contentPath"], "/Game/FreeCAD/Doc")
        self.assertEqual(self.plan["meshPath"], "/Game/FreeCAD/Doc/Meshes")
        packages = sorted(entry["package"] for entry in self.plan["imports"])
        self.assertEqual(
            packages,
            ["/Game/FreeCAD/Doc/Meshes/SM_Blue_box", "/Game/FreeCAD/Doc/Meshes/SM_Red_box"],
        )

    def test_the_content_root_can_be_moved(self):
        plan = unreal_client.plan_import(self.manifest, self.path, "/Game/CAD")
        self.assertEqual(plan["contentPath"], "/Game/CAD/Doc")

    def test_every_import_points_at_a_file_that_exists(self):
        self.assertEqual(len(self.plan["imports"]), 2)
        for entry in self.plan["imports"]:
            self.assertTrue(entry["exists"], entry["file"])

    def test_actors_carry_the_placement_and_the_hierarchy(self):
        actors = {a["name"]: a for a in self.plan["actors"]}
        self.assertEqual(set(actors), {"Root", "Red_box", "Blue_box"})
        self.assertIsNone(actors["Root"]["parent"])
        self.assertEqual(actors["Blue_box"]["parent"], "Root")
        self.assertAlmostEqual(actors["Blue_box"]["location"][0], 5.0)  # 50 mm in cm
        self.assertEqual(actors["Blue_box"]["asset"], "/Game/FreeCAD/Doc/Meshes/SM_Blue_box")

    def test_a_manifest_for_another_engine_is_refused(self):
        other = self.exported("unity")
        with self.assertRaises(unreal_client.BridgeImportError) as caught:
            unreal_client.read_manifest(other)
        self.assertIn("unity", str(caught.exception))

    def test_a_missing_file_is_reported_before_anything_is_imported(self):
        plan = dict(self.plan)
        plan["imports"] = [dict(plan["imports"][0], exists=False, file="/nowhere/x.glb")]
        with self.assertRaises(unreal_client.BridgeImportError) as caught:
            unreal_client.check_plan(plan)
        self.assertIn("/nowhere/x.glb", str(caught.exception))

    def test_package_names_are_made_legal(self):
        sanitize = unreal_client.sanitize_package_name
        self.assertEqual(sanitize("M6 bolt (x4)"), "M6_bolt_x4")
        self.assertEqual(sanitize("2mm"), "_2mm")
        self.assertEqual(sanitize("***"), "Asset")
        self.assertEqual(sanitize("Bracket"), "Bracket")


class BoundsCheckTest(unittest.TestCase):
    """The check that catches an engine converting what was already converted."""

    def bounds(self, size):
        return {"min": [0.0, 0.0, 0.0], "max": list(size)}

    def test_matching_bounds_are_silent(self):
        self.assertIsNone(
            unreal_client.bounds_disagree(
                self.bounds((1.0, 2.0, 3.0)), ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))
            )
        )

    def test_rounding_differences_are_tolerated(self):
        self.assertIsNone(
            unreal_client.bounds_disagree(
                self.bounds((1.0, 2.0, 3.0)), ((0.0, 0.0, 0.0), (1.001, 2.002, 3.001))
            )
        )

    def test_a_uniform_factor_is_named_as_a_double_unit_conversion(self):
        message = unreal_client.bounds_disagree(
            self.bounds((1.0, 2.0, 3.0)), ((0.0, 0.0, 0.0), (100.0, 200.0, 300.0))
        )
        self.assertIn("units were", message)
        self.assertIn("100", message)

    def test_permuted_extents_are_named_as_a_double_axis_conversion(self):
        message = unreal_client.bounds_disagree(
            self.bounds((1.0, 2.0, 3.0)), ((0.0, 0.0, 0.0), (3.0, 1.0, 2.0))
        )
        self.assertIn("axes were", message)

    def test_anything_else_is_reported_with_both_sizes(self):
        message = unreal_client.bounds_disagree(
            self.bounds((1.0, 2.0, 3.0)), ((0.0, 0.0, 0.0), (1.0, 2.0, 7.0))
        )
        self.assertIn("7.0", message)

    def test_missing_information_is_not_an_error(self):
        self.assertIsNone(unreal_client.bounds_disagree(None, ((0, 0, 0), (1, 1, 1))))
        self.assertIsNone(unreal_client.bounds_disagree(self.bounds((1, 1, 1)), None))

    def test_a_zero_sized_asset_is_not_reported_as_a_conversion_problem(self):
        self.assertIsNone(
            unreal_client.bounds_disagree(
                self.bounds((0.0, 0.0, 0.0)), ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
            )
        )


if __name__ == "__main__":
    unittest.main()
