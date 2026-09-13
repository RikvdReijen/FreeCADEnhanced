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
"""The conversion maths, which every other part of the bridge trusts blindly."""

import math
import unittest

from gbcore.transform import (
    BLENDER,
    FREECAD,
    GLTF,
    Matrix4,
    UNITY,
    UNREAL,
    AxisConvention,
    get_convention,
)


class MatrixTest(unittest.TestCase):
    def test_identity_leaves_points_alone(self):
        self.assertEqual(Matrix4().transform_point((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))
        self.assertTrue(Matrix4().is_identity())

    def test_multiplication_applies_right_hand_side_first(self):
        translate = Matrix4.translation(10.0, 0.0, 0.0)
        scale = Matrix4.scaling(2.0)
        # Scaling then translating must not scale the translation.
        combined = translate * scale
        self.assertEqual(combined.transform_point((1.0, 0.0, 0.0)), (12.0, 0.0, 0.0))
        # The other order does scale it.
        self.assertEqual((scale * translate).transform_point((1.0, 0.0, 0.0)), (22.0, 0.0, 0.0))

    def test_vector_ignores_translation(self):
        m = Matrix4.translation(10.0, 20.0, 30.0)
        self.assertEqual(m.transform_vector((1.0, 0.0, 0.0)), (1.0, 0.0, 0.0))

    def test_determinant_detects_mirroring(self):
        self.assertAlmostEqual(Matrix4().determinant3(), 1.0)
        self.assertAlmostEqual(Matrix4.scaling(1.0, -1.0, 1.0).determinant3(), -1.0)

    def test_column_major_transposes(self):
        m = Matrix4.translation(1.0, 2.0, 3.0)
        # glTF stores the translation in the last *row* of the column-major form.
        self.assertEqual(m.column_major()[12:15], (1.0, 2.0, 3.0))

    def test_trs_round_trip(self):
        angle = math.radians(30.0)
        cos, sin = math.cos(angle), math.sin(angle)
        basis = ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))
        m = Matrix4.from_basis(basis, (5.0, 6.0, 7.0))
        translation, rotation, scale = m.to_trs()
        self.assertEqual(translation, (5.0, 6.0, 7.0))
        for value in scale:
            self.assertAlmostEqual(value, 1.0)
        # A rotation about Z only touches the quaternion's z and w terms.
        self.assertAlmostEqual(rotation[0], 0.0)
        self.assertAlmostEqual(rotation[1], 0.0)
        self.assertAlmostEqual(rotation[2], math.sin(angle / 2.0))
        self.assertAlmostEqual(rotation[3], math.cos(angle / 2.0))

    def test_trs_reports_scale(self):
        m = Matrix4.scaling(2.0, 3.0, 4.0)
        _, rotation, scale = m.to_trs()
        self.assertEqual(scale, (2.0, 3.0, 4.0))
        self.assertEqual(rotation, (0.0, 0.0, 0.0, 1.0))


class ConventionTest(unittest.TestCase):
    def test_freecad_is_a_no_op(self):
        self.assertTrue(FREECAD.is_identity)
        self.assertEqual(FREECAD.convert_point((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))
        self.assertFalse(FREECAD.flips_winding)

    def test_blender_only_rescales(self):
        self.assertEqual(BLENDER.convert_point((1000.0, 2000.0, 3000.0)), (1.0, 2.0, 3.0))
        self.assertFalse(BLENDER.flips_winding)
        self.assertEqual(BLENDER.handedness, "right")

    def test_gltf_is_z_up_to_y_up_without_mirroring(self):
        # FreeCAD's up (+Z) has to come out as glTF's up (+Y).
        self.assertEqual(GLTF.convert_direction((0.0, 0.0, 1.0)), (0.0, 1.0, 0.0))
        # ... and FreeCAD's +Y as glTF's -Z, which is glTF's forward.
        self.assertEqual(GLTF.convert_direction((0.0, 1.0, 0.0)), (0.0, 0.0, -1.0))
        self.assertEqual(GLTF.handedness, "right")
        self.assertFalse(GLTF.flips_winding)

    def test_unity_swaps_y_and_z_and_becomes_left_handed(self):
        self.assertEqual(UNITY.convert_point((1000.0, 2000.0, 3000.0)), (1.0, 3.0, 2.0))
        self.assertEqual(UNITY.handedness, "left")
        self.assertTrue(UNITY.flips_winding)

    def test_unreal_mirrors_y_and_uses_centimetres(self):
        self.assertEqual(UNREAL.convert_point((100.0, 200.0, 300.0)), (10.0, -20.0, 30.0))
        self.assertEqual(UNREAL.handedness, "left")
        self.assertTrue(UNREAL.flips_winding)

    def test_winding_flips_exactly_for_mirroring_targets(self):
        for convention in (FREECAD, GLTF, BLENDER):
            self.assertEqual(convention.convert_triangle((0, 1, 2)), (0, 1, 2))
        for convention in (UNITY, UNREAL):
            self.assertEqual(convention.convert_triangle((0, 1, 2)), (0, 2, 1))

    def test_round_trip_through_every_convention(self):
        point = (12.5, -30.0, 7.25)
        for convention in (FREECAD, GLTF, BLENDER, UNITY, UNREAL):
            back = convention.invert_point(convention.convert_point(point))
            for expected, actual in zip(point, back):
                self.assertAlmostEqual(expected, actual, places=9, msg=convention.name)

    def test_converted_placement_keeps_the_model_together(self):
        """Converting a placement and converting a point must agree.

        This is the property that catches the classic bridge bug: a placement
        converted by rotating the translation but not the basis puts parts in
        the right position facing the wrong way, which only shows up on
        assemblies whose children are rotated.
        """
        angle = math.radians(35.0)
        cos, sin = math.cos(angle), math.sin(angle)
        placement = Matrix4.from_basis(
            ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0)), (40.0, -15.0, 8.0)
        )
        local = (3.0, 4.0, 5.0)
        for convention in (GLTF, BLENDER, UNITY, UNREAL):
            direct = convention.convert_point(placement.transform_point(local))
            staged = convention.convert_matrix(placement).transform_point(
                convention.convert_point(local)
            )
            for expected, actual in zip(direct, staged):
                self.assertAlmostEqual(expected, actual, places=9, msg=convention.name)

    def test_converted_placement_preserves_scale(self):
        for convention in (GLTF, BLENDER, UNITY, UNREAL):
            converted = convention.convert_matrix(Matrix4.scaling(2.0))
            self.assertTrue(converted.almost_equal(Matrix4.scaling(2.0)))

    def test_lookup_is_case_insensitive_and_strict(self):
        self.assertIs(get_convention("UNREAL"), UNREAL)
        self.assertIs(get_convention(UNITY), UNITY)
        with self.assertRaises(KeyError):
            get_convention("godot")

    def test_a_bad_convention_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            AxisConvention("bad", ((1, 0), (0, 1)), 1.0)
        with self.assertRaises(ValueError):
            AxisConvention("bad", ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 0.0)


if __name__ == "__main__":
    unittest.main()
