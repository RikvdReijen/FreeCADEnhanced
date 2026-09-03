# SPDX-License-Identifier: LGPL-2.1-or-later
"""Import the ported OpenXR engine against stub PySide6/PyOpenGL/pyopenxr.

The engine cannot run without a headset, but it can be *imported*, and that
alone catches the kind of breakage a rename or a refactor introduces: a module
that no longer resolves, a class body that raises, a hook that quietly
disappeared. Every module here is upstream code we adopted, so this is the
regression net for the port.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from Tests import stubs  # noqa: E402

PORTED_MODULES = (
    "xrcore.preferences",
    "xrcore.controllerXR",
    "xrcore.movementXR",
    "xrcore.menuCoin",
    "xrcore.documentInteraction",
    "xrcore.previewCoin",
    "xrcore.qtWidgetRender",
    "xrcore.commonXR",
)


class TestEngineImports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs.install_engine_stubs()
        cls._imported = {}
        # documentInteraction announces a missing optional helper on stdout.
        with redirect_stdout(io.StringIO()):
            for name in PORTED_MODULES:
                cls._imported[name] = __import__(name, fromlist=["_"])

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith(("xrcore", "xrenv", "xrpaint", "xrsync")):
                del sys.modules[name]
        stubs.uninstall()

    def test_every_ported_module_imports(self):
        for name in PORTED_MODULES:
            self.assertIn(name, self._imported)

    def test_widget_exposes_the_extension_hooks(self):
        widget = self._imported["xrcore.commonXR"].XRwidget
        for hook in (
            "attach_extensions",
            "detach_extensions",
            "update_extensions",
            "set_clip_planes",
            "document_bounding_box",
        ):
            self.assertTrue(callable(getattr(widget, hook, None)), hook)

    def test_upstream_entry_points_survived_the_port(self):
        module = self._imported["xrcore.commonXR"]
        for entry in (
            "open_xr_viewer",
            "close_xr_viewer",
            "open_xr_mirror",
            "close_xr_mirror",
            "toggle_tpp_camera",
            "reload_scenegraph",
        ):
            self.assertTrue(callable(getattr(module, entry, None)), entry)

    def test_interaction_modes_are_intact(self):
        modes = self._imported["xrcore.commonXR"].InteractMode
        names = {member.name for member in modes}
        self.assertEqual(
            names,
            {
                "TELEPORT",
                "LINE_BUILDER",
                "CUBE_BUILDER",
                "SELECT_MODE",
                "DRAG_MODE",
                "WORKING_PLANE",
            },
        )

    def test_commands_reach_the_engine_entry_points(self):
        """Each viewer command calls a function the engine actually exports."""
        module = self._imported["xrcore.commonXR"]
        source_path = os.path.join(MODULE_ROOT, "xrcore", "commands.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        for entry in (
            "open_xr_viewer",
            "close_xr_viewer",
            "open_xr_mirror",
            "close_xr_mirror",
            "toggle_tpp_camera",
            "reload_scenegraph",
        ):
            self.assertIn(f"commonXR.{entry}()", source)
            self.assertTrue(hasattr(module, entry))

    def test_controller_and_movement_api_used_by_the_engine(self):
        controller = self._imported["xrcore.controllerXR"]
        movement = self._imported["xrcore.movementXR"]
        for name in (
            "find_picked_coin_object",
            "get_picked_tex_coords",
            "get_picked_normal",
            "get_buttons_states",
            "get_global_transf",
        ):
            self.assertTrue(hasattr(controller.xrController, name), name)
        for name in ("calculate_transformation", "set_movement_type", "find_floor"):
            self.assertTrue(hasattr(movement.xrMovement, name), name)


if __name__ == "__main__":
    unittest.main()
