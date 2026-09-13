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
"""Asset naming: the rules each engine enforces, and collision handling."""

import unittest

from gbcore.naming import (
    BLENDER_POLICY,
    FREECAD_POLICY,
    NameAllocator,
    NamePolicy,
    UNITY_POLICY,
    UNREAL_POLICY,
    get_policy,
)


class PolicyTest(unittest.TestCase):
    def test_plain_names_survive_untouched(self):
        for policy in (UNREAL_POLICY, UNITY_POLICY, BLENDER_POLICY):
            self.assertEqual(policy.sanitize("Bracket"), "Bracket")

    def test_unreal_strips_everything_but_word_characters(self):
        self.assertEqual(UNREAL_POLICY.sanitize("M6 bolt (x4)"), "M6_bolt_x4")
        self.assertEqual(UNREAL_POLICY.sanitize("a/b\\c"), "a_b_c")

    def test_unity_keeps_spaces_and_hyphens(self):
        self.assertEqual(UNITY_POLICY.sanitize("Front-Left Wheel"), "Front-Left Wheel")

    def test_blender_keeps_dots_because_it_uses_them_itself(self):
        self.assertEqual(BLENDER_POLICY.sanitize("Body.001"), "Body.001")

    def test_accents_are_folded_rather_than_dropped(self):
        self.assertEqual(UNREAL_POLICY.sanitize("Gehäuse"), "Gehause")
        self.assertEqual(UNREAL_POLICY.sanitize("Größe"), "Grosse")
        self.assertEqual(UNREAL_POLICY.sanitize("Ölfilter"), "Olfilter")

    def test_leading_digits_are_prefixed_for_unreal(self):
        self.assertEqual(UNREAL_POLICY.sanitize("2mm plate"), "_2mm_plate")

    def test_blender_tolerates_a_leading_digit(self):
        self.assertEqual(BLENDER_POLICY.sanitize("2mm plate"), "2mm plate")

    def test_empty_and_unrepresentable_names_fall_back(self):
        self.assertEqual(UNREAL_POLICY.sanitize(""), "Object")
        self.assertEqual(UNREAL_POLICY.sanitize("***"), "Object")
        self.assertEqual(UNREAL_POLICY.sanitize(None, "Fallback"), "Fallback")

    def test_reserved_device_names_are_escaped(self):
        for reserved in ("CON", "com1", "NUL"):
            self.assertNotIn(UNREAL_POLICY.sanitize(reserved).upper(), ("CON", "COM1", "NUL"))

    def test_unreal_reserved_words_are_escaped(self):
        self.assertNotEqual(UNREAL_POLICY.sanitize("None"), "None")

    def test_long_names_are_truncated_to_the_limit(self):
        policy = NamePolicy("short", max_length=8)
        self.assertEqual(policy.sanitize("AVeryLongAssetName"), "AVeryLon")

    def test_runs_of_separators_collapse(self):
        self.assertEqual(UNREAL_POLICY.sanitize("a   b"), "a_b")
        self.assertEqual(UNREAL_POLICY.sanitize("  padded  "), "padded")

    def test_freecad_s_own_policy_keeps_the_name_it_was_given(self):
        """It exists to strip control characters, not to rewrite labels."""
        self.assertEqual(FREECAD_POLICY.sanitize("Pad"), "Pad")
        self.assertEqual(FREECAD_POLICY.sanitize("M6 bolt (x4)"), "M6 bolt (x4)")
        self.assertEqual(FREECAD_POLICY.sanitize("Gehäuse"), "Gehäuse")
        self.assertEqual(FREECAD_POLICY.sanitize("Bad\x07name"), "Badname")

    def test_blender_folds_accents_rather_than_blanking_them(self):
        self.assertEqual(BLENDER_POLICY.sanitize("Gehäuse"), "Gehause")
        self.assertNotIn("_", BLENDER_POLICY.sanitize("Ölfilter"))

    def test_unknown_policy_names_fall_back_to_unreal(self):
        self.assertIs(get_policy("nonesuch"), UNREAL_POLICY)
        self.assertIs(get_policy(BLENDER_POLICY), BLENDER_POLICY)


class AllocatorTest(unittest.TestCase):
    def test_collisions_after_sanitising_get_numbered(self):
        allocator = NameAllocator(UNREAL_POLICY)
        names = [allocator.allocate(n) for n in ("Pad 1", "Pad-1", "Pad_1")]
        self.assertEqual(names, ["Pad_1", "Pad_1_001", "Pad_1_002"])
        self.assertEqual(len(set(names)), 3)

    def test_numbering_is_case_insensitive(self):
        """Unreal's asset registry treats SM_Body and sm_body as one asset."""
        allocator = NameAllocator(UNREAL_POLICY)
        self.assertEqual(allocator.allocate("Body"), "Body")
        self.assertEqual(allocator.allocate("body"), "body_001")

    def test_the_same_key_always_gets_the_same_name(self):
        allocator = NameAllocator(UNREAL_POLICY)
        first = allocator.allocate("Body", key="obj1")
        self.assertEqual(allocator.allocate("Body", key="obj1"), first)
        self.assertEqual(allocator.allocate("Body", key="obj2"), "Body_001")
        self.assertEqual(len(allocator), 2)

    def test_suffixes_stay_within_the_length_limit(self):
        allocator = NameAllocator(NamePolicy("short", max_length=8))
        names = [allocator.allocate("VeryLongName") for _ in range(3)]
        self.assertEqual(names, ["VeryLong", "Very_001", "Very_002"])
        for name in names:
            self.assertLessEqual(len(name), 8)

    def test_reserving_blocks_a_name(self):
        allocator = NameAllocator(UNREAL_POLICY)
        allocator.reserve("Root")
        self.assertEqual(allocator.allocate("Root"), "Root_001")
        self.assertIn("root", allocator)

    def test_membership_and_lookup(self):
        allocator = NameAllocator(UNREAL_POLICY)
        allocator.allocate("Body", key="k")
        self.assertIn("Body", allocator)
        self.assertNotIn("Missing", allocator)
        self.assertEqual(allocator.get("k"), "Body")
        self.assertIsNone(allocator.get("nope"))

    def test_a_hundred_identical_labels_stay_unique(self):
        allocator = NameAllocator(UNREAL_POLICY)
        names = [allocator.allocate("Pad") for _ in range(100)]
        self.assertEqual(len(set(names)), 100)


if __name__ == "__main__":
    unittest.main()
