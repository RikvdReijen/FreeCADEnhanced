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
"""Phong to metallic-roughness, the one conversion with no exact answer."""

import unittest

from gbcore.materials import (
    material_from_appearance,
    materials_from_object,
    phong_to_pbr,
    shininess_to_roughness,
)
from Tests.stubs import StubAppearance, StubObject, StubViewObject, make_box_shape


class PhongConversionTest(unittest.TestCase):
    def test_roughness_runs_the_right_way(self):
        self.assertAlmostEqual(shininess_to_roughness(0.0), 1.0)
        self.assertAlmostEqual(shininess_to_roughness(1.0), 0.0)
        self.assertLess(shininess_to_roughness(0.9), shininess_to_roughness(0.2))

    def test_out_of_range_shininess_is_clamped(self):
        self.assertAlmostEqual(shininess_to_roughness(-5.0), 1.0)
        self.assertAlmostEqual(shininess_to_roughness(5.0), 0.0)

    def test_a_default_part_stays_a_dielectric(self):
        base, metallic, _, _ = phong_to_pbr((0.8, 0.8, 0.8), (0.1, 0.1, 0.1), 0.2)
        self.assertEqual(metallic, 0.0)
        self.assertEqual(base[:3], (0.8, 0.8, 0.8))

    def test_bright_plastic_with_a_white_highlight_is_not_metal(self):
        _, metallic, _, _ = phong_to_pbr((0.1, 0.4, 0.9), (0.9, 0.9, 0.9), 0.6)
        self.assertEqual(metallic, 0.0)

    def test_a_tinted_highlight_over_a_dark_diffuse_reads_as_metal(self):
        base, metallic, _, _ = phong_to_pbr((0.35, 0.3, 0.1), (0.95, 0.8, 0.3), 0.9)
        self.assertGreater(metallic, 0.5)
        # For a metal the reflectance, not the diffuse, is the base colour.
        self.assertAlmostEqual(base[0], 0.95, places=6)

    def test_transparency_becomes_alpha(self):
        base, _, _, _ = phong_to_pbr((1, 1, 1), transparency=0.25)
        self.assertAlmostEqual(base[3], 0.75)

    def test_emissive_passes_straight_through(self):
        _, _, _, emissive = phong_to_pbr((0, 0, 0), emissive=(0.0, 1.0, 0.5))
        self.assertEqual(emissive, (0.0, 1.0, 0.5))


class AppearanceTest(unittest.TestCase):
    def test_no_appearance_gives_the_freecad_default(self):
        material = material_from_appearance(None, "Fallback")
        self.assertEqual(material.base_color, (0.8, 0.8, 0.8, 1.0))
        self.assertEqual(material.name, "Fallback")

    def test_a_bare_colour_tuple_is_accepted(self):
        material = material_from_appearance((0.2, 0.4, 0.6))
        self.assertEqual(material.base_color[:3], (0.2, 0.4, 0.6))

    def test_a_colour_tuple_carries_its_alpha(self):
        material = material_from_appearance((0.2, 0.4, 0.6, 0.5))
        self.assertAlmostEqual(material.base_color[3], 0.5)
        self.assertEqual(material.alpha_mode, "BLEND")

    def test_an_appearance_object_is_read_field_by_field(self):
        appearance = StubAppearance(diffuse=(0.9, 0.1, 0.1), shininess=0.8)
        material = material_from_appearance(appearance, "Red")
        self.assertAlmostEqual(material.base_color[0], 0.9)
        self.assertLess(material.roughness, 0.5)
        self.assertEqual(material.alpha_mode, "OPAQUE")

    def test_freecad_percent_transparency_is_normalised(self):
        material = material_from_appearance(StubAppearance(), "m", transparency=50)
        self.assertAlmostEqual(material.base_color[3], 0.5)


class ObjectAppearanceTest(unittest.TestCase):
    def test_one_appearance_gives_one_material_named_after_the_object(self):
        obj = StubObject("Box", "Red box", make_box_shape())
        obj.ViewObject = StubViewObject([StubAppearance(diffuse=(0.9, 0.1, 0.1))])
        materials = materials_from_object(obj)
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0].name, "Red box")
        self.assertEqual(materials[0].source, "Box")

    def test_per_face_appearances_are_kept_in_order(self):
        obj = StubObject("Box", "Painted", make_box_shape())
        obj.ViewObject = StubViewObject(
            [
                StubAppearance(diffuse=(1.0, 0.0, 0.0)),
                StubAppearance(diffuse=(0.0, 1.0, 0.0)),
                StubAppearance(diffuse=(0.0, 0.0, 1.0)),
            ]
        )
        materials = materials_from_object(obj)
        self.assertEqual(len(materials), 3)
        self.assertEqual([m.name for m in materials], ["Painted_0", "Painted_1", "Painted_2"])
        self.assertAlmostEqual(materials[1].base_color[1], 1.0)

    def test_a_legacy_diffuse_colour_list_still_works(self):
        obj = StubObject("Box", "Legacy", make_box_shape())
        view = StubViewObject()
        view.ShapeAppearance = []
        view.DiffuseColor = [(1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0)]
        obj.ViewObject = view
        materials = materials_from_object(obj)
        self.assertEqual(len(materials), 2)
        self.assertAlmostEqual(materials[0].base_color[0], 1.0)

    def test_an_object_with_no_view_provider_still_gets_a_material(self):
        obj = StubObject("Box", "Headless", make_box_shape())
        obj.ViewObject = None
        materials = materials_from_object(obj)
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0].name, "Headless")

    def test_view_transparency_applies_to_every_face(self):
        obj = StubObject("Box", "Glass", make_box_shape())
        obj.ViewObject = StubViewObject(
            [StubAppearance(), StubAppearance()], transparency=80
        )
        for material in materials_from_object(obj):
            self.assertAlmostEqual(material.base_color[3], 0.2)
            self.assertEqual(material.alpha_mode, "BLEND")


if __name__ == "__main__":
    unittest.main()
