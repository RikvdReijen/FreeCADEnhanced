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
"""Tessellation: face by face, with the consequences that follow from that."""

import unittest

from gbcore.tessellate import (
    QUALITY,
    TessellationSettings,
    meshes_from_shape,
    tessellate_shape,
)
from Tests.stubs import StubFace, StubShape, make_box_shape, make_faced_box_shape


class SettingsTest(unittest.TestCase):
    def test_a_non_positive_deviation_is_refused(self):
        with self.assertRaises(ValueError):
            TessellationSettings(0.0)
        with self.assertRaises(ValueError):
            TessellationSettings(-1.0)

    def test_an_absolute_deviation_is_used_as_given(self):
        settings = TessellationSettings(0.25)
        self.assertEqual(settings.deviation_for(make_faced_box_shape(10.0)), 0.25)

    def test_a_relative_deviation_follows_the_model_size(self):
        """0.1 mm is right for a bracket and far too fine for a building."""
        settings = TessellationSettings(0.001, relative=True)
        small = make_faced_box_shape(10.0)
        large = make_faced_box_shape(10000.0)
        self.assertLess(settings.deviation_for(small), settings.deviation_for(large))
        self.assertAlmostEqual(settings.deviation_for(small), 10.0 * 1.7320508 * 0.001)

    def test_the_presets_get_finer_in_the_order_they_are_named(self):
        deviations = [QUALITY[name].deviation for name in ("draft", "normal", "fine", "very fine")]
        self.assertEqual(deviations, sorted(deviations, reverse=True))


class TessellateTest(unittest.TestCase):
    def test_a_shape_is_tessellated_one_group_per_face(self):
        groups = tessellate_shape(make_faced_box_shape(10.0))
        self.assertEqual(len(groups), 6)
        self.assertEqual([g[2] for g in groups], [0, 1, 2, 3, 4, 5])

    def test_a_shape_without_faces_is_tessellated_whole(self):
        groups = tessellate_shape(make_box_shape(10.0))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0][1]), 36)

    def test_the_settings_reach_the_shape(self):
        shape = make_faced_box_shape(10.0)
        tessellate_shape(shape, TessellationSettings(0.05, 15.0))
        self.assertEqual(shape.Faces[0].tessellate_calls[-1], (0.05, 15.0))

    def test_a_build_that_only_takes_a_deviation_still_works(self):
        """Older FreeCAD builds, and Mesh objects, take one argument."""

        class OneArgumentFace(StubFace):
            def tessellate(self, deviation, angular=None):
                if angular is not None:
                    raise TypeError("tessellate() takes 1 positional argument")
                return (list(self.points), list(self.facets))

        face = OneArgumentFace([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 2)])
        groups = tessellate_shape(StubShape([], [], [face]))
        self.assertEqual(len(groups), 1)

    def test_a_face_that_tessellates_to_nothing_is_skipped(self):
        shape = make_faced_box_shape(10.0)
        shape.Faces[0].points = []
        shape.Faces[0].facets = []
        self.assertEqual(len(tessellate_shape(shape)), 5)

    def test_a_reversed_face_is_turned_around(self):
        """OCC winds a face by its surface parameters, not by the solid."""
        forward = StubFace([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 2)])
        reversed_face = StubFace([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 2)], "Reversed")
        self.assertEqual(tessellate_shape(StubShape([], [], [forward]))[0][1], [0, 1, 2])
        self.assertEqual(tessellate_shape(StubShape([], [], [reversed_face]))[0][1], [0, 2, 1])


class MeshTest(unittest.TestCase):
    def test_one_material_gives_one_mesh(self):
        meshes = meshes_from_shape(make_faced_box_shape(10.0), "Box", face_materials=[0])
        self.assertEqual(len(meshes), 1)
        self.assertEqual(meshes[0].triangle_count, 12)
        self.assertEqual(meshes[0].name, "Box")

    def test_per_face_materials_split_the_shape(self):
        meshes = meshes_from_shape(
            make_faced_box_shape(10.0), "Box", face_materials=[0, 1, 0, 1, 0, 1]
        )
        self.assertEqual(len(meshes), 2)
        self.assertEqual([m.triangle_count for m in meshes], [6, 6])
        self.assertEqual({m.material for m in meshes}, {0, 1})
        self.assertEqual([m.name for m in meshes], ["Box_0", "Box_1"])

    def test_splitting_can_be_switched_off(self):
        settings = TessellationSettings(per_face_materials=False)
        meshes = meshes_from_shape(
            make_faced_box_shape(10.0), "Box", settings, [0, 1, 0, 1, 0, 1]
        )
        self.assertEqual(len(meshes), 1)

    def test_fewer_colours_than_faces_reuses_the_first(self):
        meshes = meshes_from_shape(make_faced_box_shape(10.0), "Box", face_materials=[3])
        self.assertEqual(len(meshes), 1)
        self.assertEqual(meshes[0].material, 3)

    def test_indices_are_rebased_when_faces_are_merged(self):
        """Each face tessellates from zero; merging has to offset them."""
        mesh = meshes_from_shape(make_faced_box_shape(10.0), "Box", face_materials=[0])[0]
        self.assertEqual(mesh.vertex_count, 24)
        self.assertEqual(max(mesh.indices), 23)
        mesh.validate()

    def test_every_triangle_of_a_box_faces_outward(self):
        """The reversed-face fix, checked on geometry rather than on winding.

        A box whose bottom and sides came back reversed from OCC would render
        inside out; crossing each triangle's edges and comparing with the
        direction away from the centre is what actually catches that.
        """
        mesh = meshes_from_shape(make_faced_box_shape(10.0), "Box", face_materials=[0])[0]
        centre = (5.0, 5.0, 5.0)
        for triangle in range(mesh.triangle_count):
            a, b, c = (mesh.vertex(i) for i in mesh.triangle(triangle))
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            normal = (
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            )
            outward = [a[i] - centre[i] for i in range(3)]
            self.assertGreater(
                sum(normal[i] * outward[i] for i in range(3)),
                0.0,
                "triangle %d faces inward" % triangle,
            )

    def test_normals_are_smooth_within_a_face_and_hard_between_faces(self):
        mesh = meshes_from_shape(make_faced_box_shape(10.0), "Box", face_materials=[0])[0]
        self.assertEqual(len(mesh.normals), len(mesh.positions))
        # The two triangles of the top face share vertices, so all four of its
        # normals agree; the bottom face's point the other way entirely.
        top = [mesh.normals[i * 3:i * 3 + 3] for i in range(4, 8)]
        for normal in top:
            self.assertEqual(normal, top[0])
        bottom = mesh.normals[0:3]
        self.assertNotEqual(bottom, top[0])

    def test_degenerate_triangles_are_dropped(self):
        face = StubFace(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (2, 0, 0)],
            [(0, 1, 2), (0, 1, 1), (3, 3, 3)],
        )
        meshes = meshes_from_shape(StubShape([], [], [face]), "Slivers")
        self.assertEqual(meshes[0].triangle_count, 1)

    def test_a_shape_that_tessellates_to_nothing_produces_no_mesh(self):
        self.assertEqual(meshes_from_shape(StubShape([], [], []), "Empty"), [])

    def test_normals_can_be_left_out(self):
        settings = TessellationSettings(compute_normals=False)
        mesh = meshes_from_shape(make_faced_box_shape(10.0), "Box", settings)[0]
        self.assertEqual(mesh.normals, [])


if __name__ == "__main__":
    unittest.main()
