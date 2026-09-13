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
"""Walking a document: what gets exported, where it ends up, and what does not."""

import unittest

from gbcore.document import DocumentWalker, is_visible, scene_from_document, top_level_objects
from gbcore.tessellate import TessellationSettings
from Tests.stubs import (
    StubAppearance,
    StubDocument,
    StubGroup,
    StubLink,
    StubObject,
    StubPlacement,
    StubShape,
    StubViewObject,
    make_assembly_document,
    make_box_shape,
    make_faced_box_shape,
    make_two_part_document,
)


class SimpleDocumentTest(unittest.TestCase):
    def setUp(self):
        self.scene = scene_from_document(make_two_part_document())

    def test_every_visible_part_becomes_a_node(self):
        self.assertEqual([n.name for n in self.scene.walk()], ["Red box", "Blue box"])
        self.assertEqual(self.scene.stats()["meshes"], 2)

    def test_placements_land_on_the_nodes(self):
        blue = list(self.scene.walk())[1]
        self.assertEqual(blue.transform.translation_part, (50.0, 0.0, 0.0))

    def test_the_freecad_object_name_is_recorded(self):
        self.assertEqual([n.source for n in self.scene.walk()], ["Box", "Box001"])

    def test_appearances_become_materials(self):
        self.assertEqual(len(self.scene.materials), 2)
        self.assertAlmostEqual(self.scene.materials[0].base_color[0], 0.9)

    def test_the_scene_records_how_it_was_tessellated(self):
        self.assertIn("tessellation", self.scene.metadata)
        self.assertEqual(self.scene.metadata["warnings"], [])

    def test_the_result_validates(self):
        self.scene.validate()


class PlacementTest(unittest.TestCase):
    """The bug where geometry is placed twice, which is easy to write and
    hard to see until an assembly is twice as wide as it should be."""

    def scene_for(self, offset):
        shape = make_faced_box_shape(10.0)
        shape.Placement = StubPlacement.from_translation(offset, 0.0, 0.0)
        obj = StubObject(
            "Body", "Bracket", shape,
            placement=StubPlacement.from_translation(offset, 0.0, 0.0),
        )
        return scene_from_document(StubDocument("D", [obj]))

    def test_geometry_arrives_at_its_own_origin(self):
        scene = self.scene_for(100.0)
        self.assertEqual(scene.meshes[0].bounds(), ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))

    def test_the_placement_is_applied_exactly_once(self):
        scene = self.scene_for(100.0)
        self.assertEqual(scene.bounds(), ((100.0, 0.0, 0.0), (110.0, 10.0, 10.0)))

    def test_a_part_at_the_origin_is_unaffected(self):
        self.assertEqual(
            self.scene_for(0.0).bounds(), ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
        )


class AssemblyTest(unittest.TestCase):
    def setUp(self):
        self.scene = scene_from_document(make_assembly_document())

    def test_a_container_becomes_a_group_node(self):
        root = self.scene.roots[0]
        self.assertEqual(root.name, "Assembly")
        self.assertIsNone(root.mesh)
        self.assertEqual(root.transform.translation_part, (0.0, 200.0, 0.0))
        self.assertEqual(len(root.children), 2)

    def test_a_link_shares_the_geometry_it_points_at(self):
        """Forty of the same screw should tessellate one screw."""
        self.assertEqual(self.scene.stats()["meshes"], 1)
        meshes = [n.mesh for n in self.scene.walk() if n.mesh is not None]
        self.assertEqual(meshes, [0, 0])

    def test_a_link_places_the_geometry_where_the_link_is(self):
        link = self.scene.roots[0].children[1]
        self.assertEqual(link.name, "Bracket copy")
        self.assertEqual(link.transform.translation_part, (100.0, 0.0, 0.0))

    def test_nested_placements_accumulate(self):
        positions = {
            node.name: matrix.translation_part
            for node, matrix in self.scene.world_transforms()
        }
        self.assertEqual(positions["Assembly"], (0.0, 200.0, 0.0))
        # The container moves the whole assembly, the link moves its own copy.
        self.assertEqual(positions["Bracket copy"], (100.0, 200.0, 0.0))

    def test_a_link_cycle_is_reported_rather_than_recursed_into(self):
        """A document can contain one, however little sense it makes."""
        first = StubLink("L1", None, "First")
        second = StubLink("L2", first, "Second")
        first.LinkedObject = second
        walker = DocumentWalker()
        walker.walk_document(StubDocument("cycle", [first]))
        self.assertTrue(any("cycle" in w for w in walker.warnings), walker.warnings)


class VisibilityTest(unittest.TestCase):
    def document(self):
        visible = StubObject("A", "Visible", make_box_shape(10.0))
        hidden = StubObject("B", "Hidden", make_box_shape(10.0), visible=False)
        return StubDocument("D", [visible, hidden])

    def test_hidden_objects_are_left_out_by_default(self):
        """A pad's sketch and a boolean's operands are hidden for a reason."""
        scene = scene_from_document(self.document())
        self.assertEqual([n.name for n in scene.walk()], ["Visible"])
        self.assertEqual(scene.stats()["meshes"], 1)

    def test_hidden_objects_can_be_asked_for(self):
        scene = scene_from_document(self.document(), include_hidden=True)
        self.assertEqual([n.name for n in scene.walk()], ["Visible", "Hidden"])
        self.assertFalse(list(scene.walk())[1].visible)

    def test_an_object_with_no_view_provider_counts_as_visible(self):
        """In console mode nothing is drawn; filtering on that would export
        an empty file, which is a worse answer than an unfiltered one."""
        obj = StubObject("A", "Headless", make_box_shape(10.0))
        obj.ViewObject = None
        self.assertTrue(is_visible(obj))
        self.assertEqual(len(list(scene_from_document(StubDocument("D", [obj])).walk())), 1)


class MaterialSplitTest(unittest.TestCase):
    def test_a_painted_solid_becomes_one_child_per_material(self):
        obj = StubObject("Body", "Painted", make_faced_box_shape(10.0))
        obj.ViewObject = StubViewObject(
            [
                StubAppearance(diffuse=(1.0, 0.0, 0.0)),
                StubAppearance(diffuse=(0.0, 1.0, 0.0)),
            ]
            * 3
        )
        scene = scene_from_document(StubDocument("D", [obj]))
        root = scene.roots[0]
        self.assertEqual(root.name, "Painted")
        self.assertIsNone(root.mesh)
        self.assertEqual(len(root.children), 2)
        self.assertEqual({c.source for c in root.children}, {"Body"})
        self.assertEqual(len(scene.materials), 2)


class RootsTest(unittest.TestCase):
    def test_objects_inside_a_group_are_not_also_roots(self):
        child = StubObject("Child", "Child", make_box_shape(1.0))
        group = StubGroup("Part", [child], "Container")
        document = StubDocument("D", [group, child])
        self.assertEqual([o.Name for o in top_level_objects(document)], ["Part"])

    def test_origins_and_datums_are_skipped(self):
        origin = StubObject("Origin", "Origin", type_id="App::Origin")
        body = StubObject("Body", "Body", make_box_shape(1.0))
        document = StubDocument("D", [origin, body])
        self.assertEqual([o.Name for o in top_level_objects(document)], ["Body"])

    def test_only_the_objects_asked_for_are_exported(self):
        document = make_two_part_document()
        scene = scene_from_document(document, objects=[document.Objects[1]])
        self.assertEqual([n.name for n in scene.walk()], ["Blue box"])


class RobustnessTest(unittest.TestCase):
    def test_one_shape_that_will_not_tessellate_does_not_stop_the_export(self):
        class Exploding(StubShape):
            def tessellate(self, deviation, angular=None):
                raise RuntimeError("BRep_API: command not done")

        broken = StubObject("Bad", "Broken", Exploding([], [], []))
        good = StubObject("Good", "Fine", make_box_shape(10.0))
        walker = DocumentWalker()
        scene = walker.walk_document(StubDocument("D", [broken, good]))
        self.assertEqual([n.name for n in scene.walk()], ["Fine"])
        self.assertTrue(any("Broken" in w for w in walker.warnings))

    def test_a_null_shape_is_skipped_quietly(self):
        shape = make_box_shape(10.0)
        shape.isNull_result = True
        obj = StubObject("Empty", "Empty", shape)
        scene = scene_from_document(StubDocument("D", [obj]))
        self.assertEqual(list(scene.walk()), [])

    def test_an_empty_document_gives_an_empty_scene(self):
        scene = scene_from_document(StubDocument("Empty", []))
        self.assertEqual(scene.roots, [])
        self.assertEqual(scene.stats()["triangles"], 0)

    def test_the_tessellation_settings_are_honoured(self):
        shape = make_faced_box_shape(10.0)
        obj = StubObject("Body", "Body", shape)
        walker = DocumentWalker(TessellationSettings(0.02, 12.0))
        walker.walk_document(StubDocument("D", [obj]))
        # The walker tessellates a copy, so the call landed on the copy's faces.
        self.assertEqual(shape.copies, 1)


if __name__ == "__main__":
    unittest.main()
