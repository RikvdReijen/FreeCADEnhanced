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
            # Only xrcore: it is what was imported against the stubs and has
            # to be re-imported cleanly. Purging xrenv/xrpaint/xrsync as well
            # would drop module objects other test files already hold
            # references to, so their module-level state (preference
            # overrides, registries) would silently split in two.
            if name.startswith("xrcore"):
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

    def test_sculpt_bridge_imports(self):
        from xrcore import sculpt_bridge

        self.assertEqual(sculpt_bridge.MODES, ("SCULPT", "MASK"))
        self.assertIsNone(sculpt_bridge.get_session())
        with self.assertRaises(Exception):
            sculpt_bridge.activate_mode("NOPE")

    def test_service_brokers_every_subsystem_session(self):
        from xrcore import service

        for getter, setter in (
            (service.get_paint_session, service.set_paint_session),
            (service.get_sculpt_session, service.set_sculpt_session),
            (service.get_mrc_session, service.set_mrc_session),
        ):
            self.assertIsNone(getter())
            sentinel = object()
            setter(sentinel)
            self.assertIs(getter(), sentinel)
            setter(None)
            self.assertIsNone(getter())


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


class TestPreferencePages(StubbedImportCase):
    """Both preference pages must survive a load/save round trip.

    A preference page that throws takes the whole FreeCAD preferences dialog
    with it, and nothing else in the suite would notice.
    """

    def test_second_page_round_trips(self):
        from xrcore import preferences_xr

        page = preferences_xr.XRSyncPreferencesPage()
        page.loadSettings()
        page.saveSettings()
        page.loadSettings()

    def test_every_saved_key_is_read_back(self):
        """A key written by saveSettings but never read is a silent dead end."""
        import re

        source_path = os.path.join(MODULE_ROOT, "xrcore", "preferences_xr.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        written = set(re.findall(r'pref\.Set\w+\("(\w+)"', source))
        read = set(re.findall(r'pref\.Get\w+\("(\w+)"', source))
        self.assertEqual(
            written - read, set(), "written but never loaded back into the page"
        )
        self.assertEqual(
            read - written, set(), "loaded but never saved from the page"
        )


class TestVrMenuExtensions(StubbedImportCase):
    """The in-VR menu is the only UI reachable with a headset on."""

    def setUp(self):
        from xrcore import menu_ext

        self.menu_ext = menu_ext

    def test_button_names_do_not_clash_with_the_upstream_menu(self):
        source_path = os.path.join(MODULE_ROOT, "xrcore", "menuCoin.py")
        with open(source_path, encoding="utf-8") as handle:
            upstream = handle.read()
        for name in self.menu_ext.BUTTONS:
            self.assertNotIn(f'"{name}"', upstream)

    def test_buttons_do_not_overlap_each_other(self):
        seen = {}
        for name, (_label, x, y, _group, _width) in self.menu_ext.BUTTONS.items():
            key = (round(x, 3), round(y, 3))
            self.assertNotIn(key, seen, f"{name} sits on top of {seen.get(key)}")
            seen[key] = name

    def test_buttons_sit_left_of_the_upstream_columns(self):
        # The upstream menu occupies x >= -0.05; staying left of that keeps the
        # original layout untouched.
        for name, (_label, x, _y, _group, width) in self.menu_ext.BUTTONS.items():
            self.assertLess(x + width / 2.0, -0.05, f"{name} would overlap the upstream menu")

    def test_paint_modes_share_one_radio_group(self):
        groups = {
            name: spec[3]
            for name, spec in self.menu_ext.BUTTONS.items()
            if "paint" in name
        }
        self.assertEqual(len(set(groups.values())), 1, groups)

    def test_handle_ignores_foreign_widgets(self):
        self.assertFalse(self.menu_ext.handle("free_mov_button"))
        self.assertFalse(self.menu_ext.handle("nonexistent"))

    def test_handle_claims_every_button_it_declares(self):
        for name in self.menu_ext.BUTTONS:
            self.assertTrue(self.menu_ext.handle(name), name)


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


class TestPortIntegrity(unittest.TestCase):
    """Guards on the ported upstream engine.

    The port renamed a package and moved every resource; these checks stop that
    work from silently rotting, and keep the LGPL attribution in place.
    """

    PORTED = (
        "commonXR.py",
        "controllerXR.py",
        "movementXR.py",
        "menuCoin.py",
        "previewCoin.py",
        "qtWidgetRender.py",
        "documentInteraction.py",
        "preferences.py",
    )

    def _read(self, name):
        with open(os.path.join(MODULE_ROOT, "xrcore", name), encoding="utf-8") as handle:
            return handle.read()

    def test_no_stale_upstream_package_references(self):
        for name in os.listdir(os.path.join(MODULE_ROOT, "xrcore")):
            if not name.endswith(".py"):
                continue
            source = self._read(name)
            self.assertNotIn("freecad.XR", source, f"{name} still imports the upstream package")
            self.assertNotIn("XRWorkbench_rc", source, f"{name} still uses the upstream .qrc")

    def test_ported_files_keep_their_upstream_copyright(self):
        for name in self.PORTED:
            source = self._read(name)
            self.assertIn("Adrian Przekwas", source, f"{name} lost its upstream copyright header")
            self.assertIn("Lesser General Public License", source, f"{name} lost its licence header")

    def test_notice_lists_every_ported_file(self):
        with open(os.path.join(MODULE_ROOT, "NOTICE.md"), encoding="utf-8") as handle:
            notice = handle.read()
        for name in self.PORTED:
            self.assertIn(f"xrcore/{name}", notice, f"NOTICE.md does not mention {name}")
        self.assertIn("LGPL-3.0-or-later", notice)
        self.assertTrue(os.path.exists(os.path.join(MODULE_ROOT, "LICENSE-upstream.txt")))

    def test_intra_package_imports_resolve(self):
        import re

        directory = os.path.join(MODULE_ROOT, "xrcore")
        available = {n[:-3] for n in os.listdir(directory) if n.endswith(".py")}
        pattern = re.compile(r"(?:^import xrcore\.(\w+)|^from xrcore(?:\.(\w+))? import)", re.M)
        for name in sorted(available):
            source = self._read(name + ".py")
            for direct, from_mod in pattern.findall(source):
                module = direct or from_mod
                if module:
                    self.assertIn(module, available, f"{name}.py imports missing xrcore.{module}")

    def test_controller_models_are_present(self):
        source = self._read("controllerXR.py")
        self.assertIn('"Resources", "controllers"', source)
        for model in ("left_con.iv", "right_con.iv"):
            path = os.path.join(MODULE_ROOT, "Resources", "controllers", model)
            self.assertTrue(os.path.exists(path), f"missing controller model {model}")
            self.assertIn(model, source)

    def test_preferences_ui_is_present_and_referenced(self):
        source = self._read("preferences.py")
        self.assertIn("XRPreferences.ui", source)
        self.assertIn("Preferences/Mod/XR", source)
        self.assertTrue(
            os.path.exists(os.path.join(MODULE_ROOT, "Resources", "XRPreferences.ui"))
        )

    def test_cmake_ships_every_core_module(self):
        with open(os.path.join(MODULE_ROOT, "CMakeLists.txt"), encoding="utf-8") as handle:
            cmake = handle.read()
        for name in sorted(os.listdir(os.path.join(MODULE_ROOT, "xrcore"))):
            if name.endswith(".py"):
                self.assertIn(f"xrcore/{name}", cmake, f"CMakeLists.txt does not install {name}")

    def test_extension_hooks_are_wired_into_the_engine(self):
        source = self._read("commonXR.py")
        for hook in (
            "attach_extensions",
            "detach_extensions",
            "update_extensions",
            "set_clip_planes",
            "document_bounding_box",
            "paint_separator",
            "doc_separator",
        ):
            self.assertIn(hook, source, f"commonXR.py lost the {hook} hook")


if __name__ == "__main__":
    unittest.main()
