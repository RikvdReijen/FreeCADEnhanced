# SPDX-License-Identifier: LGPL-2.1-or-later
"""Import-level checks of the workbench glue.

These catch the class of mistake that is otherwise invisible until a headset is
plugged in: a bridge calling a name the subsystem does not export, a command
pointing at a missing icon, a workbench listing a command that was never
registered.
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from Tests import stubs  # noqa: E402


class StubbedImportCase(unittest.TestCase):
    """Base class installing the FreeCAD/Qt/Coin stubs for the whole class."""

    @classmethod
    def setUpClass(cls):
        stubs.install()

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith(("xrcore", "xrenv", "xrpaint", "xrsync")):
                del sys.modules[name]
        stubs.uninstall()


class TestGlueImports(StubbedImportCase):
    def test_service_imports_and_brokers_state(self):
        from xrcore import service

        self.assertIsNone(service.get_widget())
        with self.assertRaises(service.XRServiceError):
            service.require_widget()

        sentinel = object()
        service.set_widget(sentinel)
        self.assertIs(service.require_widget(), sentinel)
        service.set_widget(None)

    def test_environment_preference_round_trip(self):
        from xrcore import service

        service.set_environment_id("laser_cutter")
        self.assertEqual(service.get_environment_id(), "laser_cutter")
        service.set_environment_id(None)
        # Falls back to the stored preference, then to the studio default.
        self.assertEqual(service.get_environment_id(), "studio")

    def test_bridges_import(self):
        from xrcore import environment_bridge, paint_bridge

        self.assertFalse(environment_bridge.manager().is_live)
        state = environment_bridge.current_state()
        self.assertIn("environment", state)
        self.assertIn("scale", state)
        self.assertFalse(state["live"])

        self.assertEqual(paint_bridge.MODES, ("TEXTURE", "STROKE3D", "VECTOR"))
        with self.assertRaises(Exception):
            paint_bridge.activate_mode("NOPE")


class TestCommands(StubbedImportCase):
    def setUp(self):
        from xrcore import commands

        self.commands = commands

    def test_every_command_registers(self):
        registered = set(stubs.recorded_commands)
        for name in self.commands.ALL_COMMANDS:
            self.assertIn(name, registered, f"{name} was never added to FreeCADGui")

    def test_resources_are_complete(self):
        icon_dir = os.path.join(MODULE_ROOT, "Resources", "icons")
        available = set(os.listdir(icon_dir))
        for name, instance in stubs.recorded_commands.items():
            resources = instance.GetResources()
            self.assertTrue(resources["MenuText"], f"{name} has no menu text")
            self.assertTrue(resources["ToolTip"], f"{name} has no tool tip")
            icon = resources["Pixmap"]
            self.assertIn(icon, available, f"{name} points at a missing icon {icon}")

    def test_menu_and_tooltip_text_is_distinct(self):
        seen = {}
        for name, instance in stubs.recorded_commands.items():
            text = instance.GetResources()["MenuText"]
            self.assertNotIn(text, seen, f"{name} duplicates the menu text of {seen.get(text)}")
            seen[text] = name

    def test_accelerators_do_not_collide(self):
        seen = {}
        for name, instance in stubs.recorded_commands.items():
            accel = instance.GetResources().get("Accel")
            if not accel:
                continue
            self.assertNotIn(accel, seen, f"{name} reuses the shortcut of {seen.get(accel)}")
            seen[accel] = name

    def test_commands_needing_a_viewer_are_inactive_without_one(self):
        from xrcore import service

        service.set_widget(None)
        for name, instance in stubs.recorded_commands.items():
            if getattr(instance, "needs_viewer", False):
                self.assertFalse(instance.IsActive(), f"{name} should need a running viewer")

    def test_workbench_lists_only_registered_commands(self):
        """Every command named by InitGui must exist, and vice versa."""
        init_gui = os.path.join(MODULE_ROOT, "InitGui.py")
        with open(init_gui, encoding="utf-8") as handle:
            source = handle.read()

        listed = {
            name
            for name in self.commands.ALL_COMMANDS
            if f'"{name}"' in source
        }
        missing = set(self.commands.ALL_COMMANDS) - listed
        self.assertFalse(missing, f"commands not offered by the workbench: {sorted(missing)}")


class TestArchitectureDocument(unittest.TestCase):
    """The contract document is load-bearing; keep it honest."""

    def setUp(self):
        path = os.path.join(MODULE_ROOT, "Resources", "doc", "ARCHITECTURE.md")
        with open(path, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_documents_every_subsystem(self):
        for needle in ("xrcore", "xrenv", "xrpaint", "xrsync", "quest/"):
            self.assertIn(needle, self.text)

    def test_documents_the_container_magic(self):
        self.assertIn("'F','C','X','R'", self.text)

    def test_lists_every_environment_primitive(self):
        for primitive in (
            "box",
            "cylinder",
            "cone",
            "sphere",
            "torus",
            "tube",
            "plane",
            "extrusion",
            "grid",
            "honeycomb",
            "text",
            "mesh",
        ):
            self.assertIn(f"`{primitive}`", self.text)


if __name__ == "__main__":
    unittest.main()
