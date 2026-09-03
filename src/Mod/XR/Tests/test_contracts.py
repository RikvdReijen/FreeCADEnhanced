# SPDX-License-Identifier: LGPL-2.1-or-later
"""The API surface the GUI layer depends on.

``xrcore`` reaches into :mod:`xrenv`, :mod:`xrpaint` and :mod:`xrsync` through a
small, fixed set of names, documented in ``Resources/doc/ARCHITECTURE.md``. A
subsystem is free to change its internals; renaming one of these names breaks
the workbench in a way that only shows up with a headset on, so they are pinned
here.

Each group is skipped while its subsystem is still being built, and starts
enforcing as soon as the module imports.
"""

import inspect
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)


def _try_import(name):
    try:
        return __import__(name, fromlist=["_"])
    except ImportError:
        return None


class ContractCase(unittest.TestCase):
    """Helpers for asserting that an object carries a set of names."""

    def assert_has(self, obj, *names):
        for name in names:
            self.assertTrue(
                hasattr(obj, name),
                f"{getattr(obj, '__name__', obj)} is missing '{name}' "
                "(see Resources/doc/ARCHITECTURE.md)",
            )

    def assert_callable(self, obj, *names):
        self.assert_has(obj, *names)
        for name in names:
            self.assertTrue(callable(getattr(obj, name)), f"{name} is not callable")

    def assert_accepts(self, function, *parameters):
        """The function must accept these keyword parameters."""
        signature = inspect.signature(function)
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            return
        for parameter in parameters:
            self.assertIn(
                parameter,
                signature.parameters,
                f"{function.__name__}() must accept '{parameter}'",
            )


# --------------------------------------------------------------------------
# xrsync
# --------------------------------------------------------------------------

fcxr = _try_import("xrsync.fcxr")
protocol = _try_import("xrsync.protocol")
sync_server = _try_import("xrsync.server")
sync_client = _try_import("xrsync.client")
gdrive = _try_import("xrsync.gdrive")
scene_export = _try_import("xrsync.scene_export")
scene_import = _try_import("xrsync.scene_import")
project = _try_import("xrsync.project")


@unittest.skipIf(fcxr is None, "xrsync.fcxr not available yet")
class TestFcxrContract(ContractCase):
    def test_container_constants_match_the_specification(self):
        self.assertEqual(fcxr.FCXR_MAGIC, b"FCXR")
        self.assertEqual(fcxr.FCXR_VERSION, 1)
        self.assertEqual(fcxr.CHUNK_JSON, b"JSON")
        self.assertEqual(fcxr.CHUNK_BIN, b"BIN\x00")
        self.assertEqual(fcxr.CHUNK_PNG, b"PNG\x00")

    def test_writer_and_reader_exist(self):
        self.assert_callable(
            fcxr.FcxrWriter,
            "add_mesh",
            "add_material",
            "add_image",
            "add_node",
            "set_scene",
            "set_paint",
            "set_vector",
            "to_bytes",
            "write",
        )
        self.assert_has(fcxr, "FcxrReader", "FcxrDocument", "FcxrError")


@unittest.skipIf(scene_export is None, "xrsync.scene_export not available yet")
class TestSceneExportContract(ContractCase):
    def test_entry_points(self):
        self.assert_callable(scene_export, "export_document", "export_objects", "scene_hash")
        self.assert_accepts(scene_export.export_document, "lod")
        self.assert_accepts(scene_export.export_objects, "document", "lod")


@unittest.skipIf(scene_import is None, "xrsync.scene_import not available yet")
class TestSceneImportContract(ContractCase):
    def test_entry_point(self):
        self.assert_callable(scene_import, "import_package")


@unittest.skipIf(sync_server is None, "xrsync.server not available yet")
class TestSyncServerContract(ContractCase):
    def test_server_surface(self):
        self.assert_callable(
            sync_server.SyncServer,
            "start",
            "stop",
            "is_running",
            "urls",
            "begin_pairing",
            "pairing_completed",
            "cancel_pairing",
        )
        self.assert_accepts(sync_server.SyncServer.__init__, "port")

    @unittest.skipIf(protocol is None, "xrsync.protocol not available yet")
    def test_endpoints_match_the_specification(self):
        """The documented paths, and a server that routes them."""
        expected = {
            "EP_HELLO": "/api/v1/hello",
            "EP_PAIR": "/api/v1/pair",
            "EP_DOCUMENTS": "/api/v1/documents",
            "EP_SCENE": "/api/v1/scene",
            "EP_SCENE_HASH": "/api/v1/scene/hash",
            "EP_EVENTS": "/api/v1/events",
            "EP_ENVIRONMENTS": "/api/v1/environments",
            "EP_ENVIRONMENT": "/api/v1/environment",
            "EP_PAINT": "/api/v1/paint",
            "EP_VECTOR": "/api/v1/vector",
            "EP_THUMBNAIL": "/api/v1/thumbnail",
        }
        for name, path in expected.items():
            self.assertTrue(hasattr(protocol, name), f"protocol is missing {name}")
            self.assertEqual(getattr(protocol, name), path, name)

        source_path = os.path.join(MODULE_ROOT, "xrsync", "server.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        for name, path in expected.items():
            self.assertTrue(
                name in source or path in source,
                f"the server never routes {path}",
            )


@unittest.skipIf(sync_client is None, "xrsync.client not available yet")
class TestSyncClientContract(ContractCase):
    def test_client_can_do_what_the_headset_needs(self):
        self.assert_callable(sync_client, "discover")
        self.assert_callable(
            sync_client.SyncClient,
            "hello",
            "pair",
            "documents",
            "scene",
            "scene_hash",
            "events",
            "environments",
            "environment",
            "push_paint",
            "push_vector",
        )


@unittest.skipIf(gdrive is None, "xrsync.gdrive not available yet")
class TestGoogleDriveContract(ContractCase):
    def test_module_surface(self):
        self.assert_callable(
            gdrive,
            "account_status",
            "load_client_config",
            "save_client_config",
            "sign_out",
        )
        self.assert_has(gdrive, "NotConfiguredError", "NotAuthenticatedError", "DeviceCodeFlow")
        self.assertTrue(issubclass(gdrive.NotConfiguredError, Exception))
        self.assertTrue(issubclass(gdrive.NotAuthenticatedError, Exception))

    def test_client_surface(self):
        client = gdrive.GoogleDriveClient
        self.assert_callable(
            client,
            "from_stored_credentials",
            "list_children",
            "download",
            "upload",
            "update",
            "ensure_folder",
        )

    def test_device_flow_surface(self):
        self.assert_callable(gdrive.DeviceCodeFlow, "start", "poll_once")

    def test_no_credentials_are_hardcoded(self):
        """A shipped client secret would be a leak, and Google would revoke it.

        Placeholders in the documentation are fine; a credential that actually
        matches Google's format is not.
        """
        import re

        source_path = os.path.join(MODULE_ROOT, "xrsync", "gdrive.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        client_id = re.compile(r"\d{8,}-[A-Za-z0-9_]{20,}\.apps\.googleusercontent\.com")
        client_secret = re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}")
        self.assertIsNone(client_id.search(source), "a real OAuth client id is committed")
        self.assertIsNone(client_secret.search(source), "a real OAuth client secret is committed")


@unittest.skipIf(project is None, "xrsync.project not available yet")
class TestProjectContract(ContractCase):
    def test_drive_helpers(self):
        self.assert_callable(project, "pull_drive_file", "push_document_to_drive")
        self.assert_accepts(
            project.push_document_to_drive, "parent_id", "name", "also_fcxr"
        )


# --------------------------------------------------------------------------
# xrenv
# --------------------------------------------------------------------------

env_registry = _try_import("xrenv.registry")
env_scale = _try_import("xrenv.scale")
env_spec = _try_import("xrenv.spec")


def _environments_registered():
    if env_registry is None:
        return False
    try:
        return bool(env_registry.list_environments())
    except Exception:
        return False


ENVIRONMENTS_READY = _environments_registered()


@unittest.skipIf(env_registry is None, "xrenv.registry not available yet")
class TestEnvironmentRegistryContract(ContractCase):
    def test_module_surface(self):
        self.assert_callable(env_registry, "list_environments", "get", "register")

    @unittest.skipUnless(ENVIRONMENTS_READY, "no environments are built yet")
    def test_environments_expose_what_the_bridge_uses(self):
        environments = env_registry.list_environments()
        self.assertTrue(environments, "no environments are registered")
        for info in environments:
            self.assert_has(info, "id", "name", "description", "user_scale")
            environment = env_registry.get(info.id)
            self.assert_has(environment, "info", "spec", "user_scale", "spawn", "anchors")
            self.assert_callable(environment, "build_scenegraph", "primary_anchor")

    @unittest.skipUnless(ENVIRONMENTS_READY, "no environments are built yet")
    def test_the_studio_fallback_exists(self):
        ids = {info.id for info in env_registry.list_environments()}
        self.assertIn("studio", ids, "xrcore.service falls back to the 'studio' environment")

    def test_unknown_identifiers_are_rejected_clearly(self):
        with self.assertRaises((KeyError, LookupError)):
            env_registry.get("definitely-not-an-environment")


@unittest.skipIf(env_scale is None, "xrenv.scale not available yet")
class TestScaleControllerContract(ContractCase):
    def test_controller_surface(self):
        controller = env_scale.ScaleController
        self.assert_callable(
            controller,
            "set_environment",
            "set_scale",
            "step",
            "clip_planes",
            "fit_document_to_anchor",
        )
        self.assert_accepts(controller.set_scale, "animate")

    def test_shrinking_the_user_grows_the_world(self):
        """The sign convention environment_bridge relies on."""
        controller = env_scale.ScaleController()
        controller.set_scale(1.0, animate=False)
        at_life_size = controller.world_scale
        controller.set_scale(12.0, animate=False)
        self.assertGreater(
            controller.world_scale,
            at_life_size,
            "world_scale must grow as the user shrinks",
        )

    def test_clip_planes_stay_usable_at_every_scale(self):
        """Hands must never be clipped, and the far wall must stay visible.

        Because shrinking the user is implemented by growing the world, the
        near plane does not track the scale — but it must stay inside arm's
        reach at every scale, and the far plane has to grow with the world.
        """
        controller = env_scale.ScaleController()
        previous_far = 0.0
        for scale in (0.25, 1.0, 4.0, 12.0, 50.0, 400.0):
            controller.set_scale(scale, animate=False)
            near, far = controller.clip_planes()
            self.assertGreater(near, 0.0, f"near plane is degenerate at 1:{scale}")
            self.assertLessEqual(
                near,
                0.15,
                f"at 1:{scale} the near plane is beyond arm's reach, so hands and "
                "the brush tip would be clipped",
            )
            self.assertGreater(far, near * 10.0, f"no usable depth range at 1:{scale}")
            self.assertGreaterEqual(
                far,
                previous_far - 1e-9,
                "the far plane must not shrink as the world grows",
            )
            previous_far = far

    def test_fit_tolerates_an_unknown_bounding_box(self):
        controller = env_scale.ScaleController()
        self.assertIsNone(controller.fit_document_to_anchor(None, None))


@unittest.skipIf(env_spec is None, "xrenv.spec not available yet")
class TestEnvironmentSpecContract(ContractCase):
    def test_module_surface(self):
        self.assert_callable(env_spec, "load_spec", "save_spec", "count_parts")

    def test_every_documented_primitive_is_tessellated(self):
        source_path = os.path.join(MODULE_ROOT, "xrenv", "spec.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
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
            self.assertIn(f'"{primitive}"', source, f"spec.py does not handle '{primitive}'")


# --------------------------------------------------------------------------
# xrpaint
# --------------------------------------------------------------------------

paint_session = _try_import("xrpaint.session")
paint_layers = _try_import("xrpaint.layers")
paint_vector = _try_import("xrpaint.vector")
paint_svg = _try_import("xrpaint.svg")
paint_to_freecad = _try_import("xrpaint.to_freecad")


@unittest.skipIf(paint_session is None, "xrpaint.session not available yet")
class TestPaintSessionContract(ContractCase):
    def test_session_surface(self):
        session = paint_session.PaintSession
        self.assert_callable(
            session,
            "set_mode",
            "attach_scenegraph",
            "bind_viewer",
            "detach",
            "update",
            "active_layer_stack",
            "invalidate_composite",
            "export_paint_manifest",
            "import_paint_manifest",
        )

    def test_a_fresh_session_is_idle(self):
        session = paint_session.PaintSession()
        self.assertIsNone(session.mode)
        self.assertIsNone(session.active_layer_stack())

    def test_modes_match_the_bridge(self):
        session = paint_session.PaintSession()
        for mode in ("TEXTURE", "STROKE3D", "VECTOR"):
            session.set_mode(mode)
            self.assertEqual(session.mode, mode)
        session.set_mode(None)
        self.assertIsNone(session.mode)


@unittest.skipIf(paint_layers is None, "xrpaint.layers not available yet")
class TestLayerStackContract(ContractCase):
    BLEND_MODES = ("normal", "multiply", "add", "screen", "erase")

    def test_stack_surface(self):
        stack = paint_layers.LayerStack
        self.assert_callable(stack, "add_layer", "remove_layer", "move_layer", "merge_down")

    def test_layer_attributes_used_by_the_dialog(self):
        stack = paint_layers.LayerStack(64, 64) if _takes_size(paint_layers.LayerStack) else paint_layers.LayerStack()
        layer = stack.add_layer("Base")
        self.assert_has(layer, "name", "visible", "opacity", "blend")
        self.assertIn(layer.blend, self.BLEND_MODES)
        self.assertIsInstance(stack.active_index, int)


def _takes_size(cls):
    try:
        signature = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return False
    required = [
        parameter
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(required) >= 2


@unittest.skipIf(paint_vector is None, "xrpaint.vector not available yet")
class TestVectorDocumentContract(ContractCase):
    def test_document_surface(self):
        document = paint_vector.VectorDocument
        self.assert_callable(document, "from_json", "to_json")

    def test_round_trips_through_the_documented_schema(self):
        document = paint_vector.VectorDocument()
        data = document.to_json()
        self.assertIn("version", data)
        self.assertIn("paths", data)
        restored = paint_vector.VectorDocument.from_json(data)
        self.assertEqual(restored.to_json()["version"], data["version"])


@unittest.skipIf(paint_svg is None, "xrpaint.svg not available yet")
class TestSvgContract(ContractCase):
    def test_export_entry_point(self):
        self.assert_callable(paint_svg, "export_document")


@unittest.skipIf(paint_to_freecad is None, "xrpaint.to_freecad not available yet")
class TestToFreeCadContract(ContractCase):
    def test_commit_entry_point(self):
        self.assert_callable(paint_to_freecad, "commit")


if __name__ == "__main__":
    unittest.main()
