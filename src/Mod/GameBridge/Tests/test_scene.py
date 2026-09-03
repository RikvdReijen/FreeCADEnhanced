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
"""The intermediate scene: geometry housekeeping and graph invariants."""

import unittest

from gbcore.scene import Material, Mesh, Node, Scene, SceneError
from gbcore.transform import Matrix4


def quad():
    """Two triangles sharing an edge, given as six unwelded vertices."""
    return Mesh(
        "quad",
        positions=[
            0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0,
        ],
        indices=[0, 1, 2, 3, 4, 5],
    )


class MeshTest(unittest.TestCase):
    def test_counts(self):
        mesh = quad()
        self.assertEqual(mesh.vertex_count, 6)
        self.assertEqual(mesh.triangle_count, 2)
        self.assertFalse(mesh.is_empty)
        self.assertTrue(Mesh("empty").is_empty)

    def test_bounds(self):
        self.assertEqual(quad().bounds(), ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)))
        self.assertIsNone(Mesh("empty").bounds())

    def test_computed_normals_point_along_the_winding(self):
        mesh = quad().compute_normals()
        self.assertEqual(mesh.normals[:3], [0.0, 0.0, 1.0])
        self.assertEqual(len(mesh.normals), len(mesh.positions))

    def test_normals_are_not_recomputed_unless_forced(self):
        mesh = quad()
        mesh.normals = [0.0, 1.0, 0.0] * 6
        mesh.compute_normals()
        self.assertEqual(mesh.normals[:3], [0.0, 1.0, 0.0])
        mesh.compute_normals(force=True)
        self.assertEqual(mesh.normals[:3], [0.0, 0.0, 1.0])

    def test_welding_merges_the_shared_edge(self):
        mesh = quad().weld()
        self.assertEqual(mesh.vertex_count, 4)
        self.assertEqual(mesh.triangle_count, 2)
        self.assertEqual(mesh.bounds(), ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)))

    def test_welding_keeps_hard_edges_apart(self):
        """Two coincident vertices with different normals are a crease."""
        mesh = quad()
        mesh.normals = [0.0, 0.0, 1.0] * 3 + [1.0, 0.0, 0.0] * 3
        mesh.weld()
        self.assertEqual(mesh.vertex_count, 6)

    def test_welding_is_idempotent(self):
        mesh = quad().weld()
        indices = list(mesh.indices)
        mesh.weld()
        self.assertEqual(mesh.indices, indices)

    def test_degenerate_triangles_are_dropped(self):
        mesh = quad()
        mesh.indices.extend([0, 0, 1])          # repeated index
        mesh.indices.extend([0, 1, 1])          # repeated index
        mesh.positions.extend([2.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        mesh.indices.extend([6, 7, 8])          # zero area
        mesh.drop_degenerate_triangles()
        self.assertEqual(mesh.triangle_count, 2)

    def test_flipping_reverses_every_triangle(self):
        mesh = quad().flip_winding()
        self.assertEqual(mesh.indices, [0, 2, 1, 3, 5, 4])

    def test_transforming_bakes_the_matrix(self):
        mesh = quad().compute_normals()
        moved = mesh.transformed(Matrix4.translation(10.0, 0.0, 0.0))
        self.assertEqual(moved.bounds(), ((10.0, 0.0, 0.0), (11.0, 1.0, 0.0)))
        self.assertEqual(mesh.bounds(), ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)))
        self.assertEqual(moved.normals[:3], [0.0, 0.0, 1.0])

    def test_mirroring_transform_also_flips_the_winding(self):
        mesh = quad()
        mirrored = mesh.transformed(Matrix4.scaling(1.0, -1.0, 1.0))
        self.assertEqual(mirrored.indices, [0, 2, 1, 3, 5, 4])

    def test_checksum_follows_the_geometry(self):
        a, b = quad(), quad()
        self.assertEqual(a.checksum(), b.checksum())
        b.positions[0] += 0.001
        self.assertNotEqual(a.checksum(), b.checksum())

    def test_validation_catches_bad_indices(self):
        mesh = quad()
        mesh.indices[0] = 99
        with self.assertRaises(SceneError):
            mesh.validate()

    def test_validation_catches_ragged_arrays(self):
        mesh = quad()
        mesh.positions.append(1.0)
        with self.assertRaises(SceneError):
            mesh.validate()

    def test_validation_catches_mismatched_normals(self):
        mesh = quad()
        mesh.normals = [0.0, 0.0, 1.0]
        with self.assertRaises(SceneError):
            mesh.validate()

    def test_validation_catches_infinities(self):
        mesh = quad()
        mesh.positions[0] = float("inf")
        with self.assertRaises(SceneError):
            mesh.validate()

    def test_dict_round_trip(self):
        mesh = quad().compute_normals()
        restored = Mesh.from_dict(mesh.to_dict())
        self.assertEqual(restored.checksum(), mesh.checksum())

    def test_dict_can_omit_geometry(self):
        data = quad().to_dict(include_geometry=False)
        self.assertNotIn("positions", data)
        self.assertEqual(data["triangleCount"], 2)


class MaterialTest(unittest.TestCase):
    def test_colours_are_clamped_and_padded(self):
        material = Material("m", base_color=(2.0, -1.0, 0.5))
        self.assertEqual(material.base_color, (1.0, 0.0, 0.5, 1.0))

    def test_transparency_is_detected_either_way(self):
        self.assertFalse(Material("m").is_transparent)
        self.assertTrue(Material("m", base_color=(1, 1, 1, 0.4)).is_transparent)
        self.assertTrue(Material("m", alpha_mode="BLEND").is_transparent)

    def test_identical_materials_share_a_key(self):
        self.assertEqual(Material("a").key(), Material("b").key())
        self.assertNotEqual(Material("a").key(), Material("a", metallic=1.0).key())

    def test_bad_alpha_mode_is_rejected(self):
        with self.assertRaises(SceneError):
            Material("m", alpha_mode="GHOST")

    def test_dict_round_trip(self):
        material = Material("m", (0.1, 0.2, 0.3, 0.4), 0.5, 0.6, (0.0, 1.0, 0.0), True, "BLEND")
        restored = Material.from_dict(material.to_dict())
        self.assertEqual(restored.key(), material.key())
        self.assertEqual(restored.name, "m")


class SceneTest(unittest.TestCase):
    def build(self):
        scene = Scene("assembly", document="Doc")
        mesh = scene.add_mesh(quad())
        root = scene.add_root(Node("Root", Matrix4.translation(100.0, 0.0, 0.0)))
        root.add(Node("Child", Matrix4.translation(10.0, 0.0, 0.0), mesh=mesh))
        return scene

    def test_walk_is_depth_first(self):
        scene = self.build()
        self.assertEqual([n.name for n in scene.walk()], ["Root", "Child"])

    def test_world_transforms_accumulate(self):
        scene = self.build()
        transforms = dict((n.name, m) for n, m in scene.world_transforms())
        self.assertEqual(transforms["Child"].translation_part, (110.0, 0.0, 0.0))

    def test_bounds_are_in_world_space(self):
        self.assertEqual(self.build().bounds(), ((110.0, 0.0, 0.0), (111.0, 1.0, 0.0)))

    def test_hidden_nodes_are_left_out_of_the_bounds(self):
        scene = self.build()
        list(scene.walk())[1].visible = False
        self.assertIsNone(scene.bounds())

    def test_materials_are_deduplicated(self):
        scene = Scene("s")
        first = scene.add_material(Material("red", (1, 0, 0)))
        second = scene.add_material(Material("also red", (1, 0, 0)))
        self.assertEqual(first, second)
        self.assertEqual(len(scene.materials), 1)
        third = scene.add_material(Material("red again", (1, 0, 0)), deduplicate=False)
        self.assertEqual(third, 1)

    def test_stats(self):
        stats = self.build().stats()
        self.assertEqual(stats["nodes"], 2)
        self.assertEqual(stats["meshes"], 1)
        self.assertEqual(stats["triangles"], 2)
        self.assertEqual(stats["visibleMeshNodes"], 1)

    def test_validation_catches_dangling_references(self):
        scene = self.build()
        list(scene.walk())[1].mesh = 7
        with self.assertRaises(SceneError):
            scene.validate()

    def test_validation_catches_a_node_reachable_twice(self):
        scene = self.build()
        shared = Node("Shared")
        scene.roots[0].add(shared)
        scene.roots[0].children[0].add(shared)
        with self.assertRaises(SceneError):
            scene.validate()

    def test_validation_catches_a_dangling_material(self):
        scene = self.build()
        scene.meshes[0].material = 3
        with self.assertRaises(SceneError):
            scene.validate()

    def test_pruning_removes_groups_that_hold_nothing(self):
        scene = self.build()
        scene.add_root(Node("Empty group", children=[Node("Also empty")]))
        scene.prune_empty()
        self.assertEqual([r.name for r in scene.roots], ["Root"])

    def test_checksum_follows_placements(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first.checksum(), second.checksum())
        second.roots[0].transform = Matrix4.translation(101.0, 0.0, 0.0)
        self.assertNotEqual(first.checksum(), second.checksum())

    def test_checksum_follows_visibility(self):
        first, second = self.build(), self.build()
        list(second.walk())[1].visible = False
        self.assertNotEqual(first.checksum(), second.checksum())

    def test_dict_round_trip(self):
        scene = self.build()
        scene.add_material(Material("red", (1, 0, 0)))
        restored = Scene.from_dict(scene.to_dict())
        restored.validate()
        self.assertEqual(restored.checksum(), scene.checksum())
        self.assertEqual(restored.stats(), scene.stats())


if __name__ == "__main__":
    unittest.main()
