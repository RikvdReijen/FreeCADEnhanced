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
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Tests for the colour palette and layer helpers (headless parts)."""

import unittest

import FreeCAD

from freeform import palette


class TestPalette(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("FreeformPaletteTest")
        palette.set_current_color(None)

    def tearDown(self):
        palette.set_current_color(None)
        FreeCAD.closeDocument(self.doc.Name)

    def test_pack_unpack(self):
        packed = palette.pack_color((1.0, 0.5, 0.0))
        self.assertEqual(packed & 0xFF, 0xFF)
        r, g, b = palette.unpack_color(packed)
        self.assertAlmostEqual(r, 1.0, places=2)
        self.assertAlmostEqual(g, 0.5, places=2)
        self.assertAlmostEqual(b, 0.0, places=2)

    def test_current_color_roundtrip(self):
        self.assertIsNone(palette.get_current_color())
        palette.set_current_color((0.2, 0.4, 0.6))
        rgb = palette.get_current_color()
        self.assertAlmostEqual(rgb[0], 0.2, places=2)
        self.assertAlmostEqual(rgb[2], 0.6, places=2)
        palette.set_current_color(None)
        self.assertIsNone(palette.get_current_color())

    def test_color_name(self):
        self.assertEqual(palette.color_name((0.24, 0.62, 0.92)), "Sky")
        self.assertEqual(palette.color_name((0.13, 0.13, 0.15)), "Ink")

    def test_palette_entries_are_valid(self):
        self.assertGreaterEqual(len(palette.DEFAULT_PALETTE), 12)
        for name, rgb in palette.DEFAULT_PALETTE:
            self.assertTrue(name)
            self.assertEqual(len(rgb), 3)
            for c in rgb:
                self.assertGreaterEqual(c, 0.0)
                self.assertLessEqual(c, 1.0)

    def test_layer_group_fallback(self):
        box = self.doc.addObject("Part::Box", "Box")
        layer = palette.make_layer("Sketch layer", (1.0, 0.0, 0.0), doc=self.doc)
        palette.add_to_layer(layer, [box])
        self.doc.recompute()
        members = layer.Group if hasattr(layer, "Group") else []
        self.assertIn(box, members)
        # adding twice does not duplicate
        palette.add_to_layer(layer, [box])
        self.assertEqual(list(layer.Group).count(box), 1)

    def test_apply_color_headless_is_noop(self):
        box = self.doc.addObject("Part::Box", "Box")
        palette.apply_color([box], (1.0, 0.0, 0.0))  # must not raise without a GUI
