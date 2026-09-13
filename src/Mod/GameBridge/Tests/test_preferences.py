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
"""Preferences, and the command line that bypasses them.

Both are checked without FreeCAD present, which is also the situation the code
has to survive: a missing parameter tree yields the defaults rather than an
exception, or the module could not be imported outside the application at all.
"""

import importlib.util
import os
import unittest

from gbcore.preferences import DEFAULTS, PARAMETER_PATH, Preferences

TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
)


def load_tool(name):
    path = os.path.join(TOOLS, name)
    spec = importlib.util.spec_from_file_location("gb_tool_" + name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = load_tool("gamebridge_export.py")


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        self.preferences = Preferences()

    def test_the_defaults_come_back_without_freecad(self):
        self.assertEqual(self.preferences.get("Target"), "unreal")
        self.assertEqual(self.preferences.get("LinkPort"), 54321)
        self.assertIs(self.preferences.get("Weld"), True)

    def test_an_unknown_key_returns_the_given_default(self):
        self.assertEqual(self.preferences.get("Nonesuch", "fallback"), "fallback")
        self.assertIsNone(self.preferences.get("Nonesuch"))

    def test_setting_without_freecad_reports_failure_rather_than_raising(self):
        self.assertFalse(self.preferences.set("Target", "unity"))

    def test_every_default_has_a_supported_type(self):
        for key, value in DEFAULTS.items():
            self.assertIn(type(value), (str, bool, int, float), key)

    def test_the_parameter_path_is_the_module_s_own(self):
        self.assertTrue(PARAMETER_PATH.endswith("Mod/GameBridge"))

    def test_all_returns_every_setting(self):
        self.assertEqual(set(self.preferences.all()), set(DEFAULTS))


class DerivedObjectTest(unittest.TestCase):
    def test_tessellation_settings_follow_the_preferences(self):
        preferences = Preferences(defaults=dict(DEFAULTS, Deviation=0.02, AngularDeviation=8.0))
        settings = preferences.tessellation_settings()
        self.assertEqual(settings.deviation, 0.02)
        self.assertEqual(settings.angular_deviation, 8.0)

    def test_export_options_follow_the_preferences(self):
        preferences = Preferences(defaults=dict(DEFAULTS, MeshFormat="obj", Weld=False))
        options = preferences.export_options()
        self.assertEqual(options.mesh_format, "obj")
        self.assertFalse(options.weld)

    def test_an_empty_token_becomes_no_token(self):
        self.assertIsNone(Preferences().link_settings()["token"])
        with_token = Preferences(defaults=dict(DEFAULTS, LinkToken="abc"))
        self.assertEqual(with_token.link_settings()["token"], "abc")


class CommandLineTest(unittest.TestCase):
    def test_the_defaults_match_the_documented_ones(self):
        arguments = exporter.parse_arguments(["model.FCStd"])
        self.assertEqual(arguments.target, "unreal")
        self.assertEqual(arguments.out, ".")
        self.assertIsNone(arguments.deviation)
        self.assertIsNone(arguments.quality)
        self.assertFalse(arguments.include_hidden)

    def test_a_named_quality_is_accepted(self):
        """The presets are reachable here, which is why they still exist."""
        from gbcore.tessellate import QUALITY

        arguments = exporter.parse_arguments(["m.FCStd", "--quality", "fine"])
        self.assertEqual(arguments.quality, "fine")
        self.assertLess(QUALITY["fine"].deviation, QUALITY["normal"].deviation)

    def test_options_are_parsed(self):
        arguments = exporter.parse_arguments(
            ["m.FCStd", "--target", "unity", "--out", "/tmp/x", "--format", "obj",
             "--deviation", "0.05", "--include-hidden", "--no-weld",
             "--object", "Body", "--object", "Pad"]
        )
        self.assertEqual(arguments.target, "unity")
        self.assertEqual(arguments.out, "/tmp/x")
        self.assertEqual(arguments.format, "obj")
        self.assertEqual(arguments.deviation, 0.05)
        self.assertTrue(arguments.include_hidden)
        self.assertTrue(arguments.no_weld)
        self.assertEqual(arguments.objects, ["Body", "Pad"])

    def test_an_unknown_engine_is_refused(self):
        with self.assertRaises(SystemExit):
            exporter.parse_arguments(["m.FCStd", "--target", "godot"])

    def test_arguments_before_the_separator_belong_to_freecadcmd(self):
        """freecadcmd hands the whole command line over, its own part included."""
        strip = exporter.strip_launcher_arguments
        self.assertEqual(strip(["-c", "--", "m.FCStd", "--target", "unity"]),
                         ["m.FCStd", "--target", "unity"])
        self.assertEqual(strip(["m.FCStd"]), ["m.FCStd"])

    def test_running_outside_freecad_says_how_to_run_it(self):
        self.assertEqual(exporter.main(["--", "nothing.FCStd"]), 2)


if __name__ == "__main__":
    unittest.main()
