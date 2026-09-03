# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""The built-in environments, the registry, the scale controller.

Includes the regeneration guard: the JSON committed under
``Resources/environments`` must match what the generators produce right now.
Runs without FreeCAD or pivy::

    cd src/Mod/XR && python3 -m unittest
"""

from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

_MOD_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
if _MOD_DIR not in sys.path:
    sys.path.insert(0, _MOD_DIR)

from xrenv import builder, registry  # noqa: E402
from xrenv import spec as S  # noqa: E402
from xrenv.scale import (  # noqa: E402
    DEFAULT_EYE_HEIGHT,
    FitTransform,
    ScaleController,
    fit_document_to_anchor,
    quat_from_axis_angle,
    quat_rotate,
    transition_duration,
)

#: The generators that must exist and be regenerated into Resources.
BUILTIN_IDS = ("bambu_x1c", "laser_cutter", "workshop", "studio", "void")

#: Environments the user is put *inside* a machine in, at miniature scale.
MACHINE_IDS = ("bambu_x1c", "laser_cutter")

#: Minimum part count per environment.  The two flagships must stay rich.
MIN_PARTS = {
    "bambu_x1c": 400,
    "laser_cutter": 300,
    "workshop": 100,
    "studio": 30,
    "void": 10,
}

_ISOLATED_USER_DIR = None
_PREV_USER_DIR = None


def setUpModule():
    """Point the user environment directory at an empty temp dir.

    Otherwise a spec the developer happens to have in
    ``~/.FreeCAD/xr/environments`` would shadow a built-in and fail the suite.
    """
    global _ISOLATED_USER_DIR, _PREV_USER_DIR
    _PREV_USER_DIR = os.environ.get("XRENV_USER_DIR")
    _ISOLATED_USER_DIR = tempfile.mkdtemp(prefix="xrenv_user_")
    os.environ["XRENV_USER_DIR"] = _ISOLATED_USER_DIR
    registry.refresh(force=True)


def tearDownModule():
    if _PREV_USER_DIR is None:
        os.environ.pop("XRENV_USER_DIR", None)
    else:
        os.environ["XRENV_USER_DIR"] = _PREV_USER_DIR
    if _ISOLATED_USER_DIR:
        shutil.rmtree(_ISOLATED_USER_DIR, ignore_errors=True)
    registry.refresh(force=True)


def build_builtin(env_id):
    module = importlib.import_module("xrenv.environments." + env_id)
    return module.build()


# ---------------------------------------------------------------------------


class TestBuiltinEnvironments(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.specs = {env_id: build_builtin(env_id) for env_id in BUILTIN_IDS}

    def test_every_environment_validates(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                problems = S.validate_spec(spec)
                self.assertEqual(
                    problems, [],
                    "%s: %s" % (env_id, "; ".join(problems[:8])))

    def test_id_matches_module(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                self.assertEqual(spec["id"], env_id)
                self.assertTrue(spec["name"])
                self.assertTrue(spec["description"])

    def test_every_environment_has_an_anchor(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                anchors = spec.get("anchors") or {}
                self.assertTrue(anchors, "%s has no anchors" % env_id)
                for name, anchor in anchors.items():
                    self.assertEqual(len(anchor["position"]), 3)
                    self.assertEqual(len(anchor["size"]), 2)
                    q = anchor["rotation"]
                    self.assertAlmostEqual(
                        math.sqrt(sum(v * v for v in q)), 1.0, places=5,
                        msg="%s/%s rotation must be a unit quaternion" % (env_id, name))

    def test_machines_expose_a_primary_anchor_on_the_work_surface(self):
        expected = {"bambu_x1c": "build_plate", "laser_cutter": "bed_surface"}
        for env_id, anchor_name in expected.items():
            with self.subTest(environment=env_id):
                spec = self.specs[env_id]
                self.assertIn(anchor_name, spec["anchors"])
                anchor = spec["anchors"][anchor_name]
                # the plate sits at the height the user is spawned at
                self.assertAlmostEqual(anchor["position"][1], spec["spawn"][1], places=4)
                # and is a sensible size for the machine
                self.assertGreater(anchor["size"][0], 0.2)
                self.assertGreater(anchor["size"][1], 0.2)

    def test_fallbacks_have_no_primary_anchor(self):
        for env_id in ("studio", "void"):
            with self.subTest(environment=env_id):
                env = registry.get(env_id)
                self.assertIsNone(env.primary_anchor())

    def test_spawn_is_inside_the_bounds(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                w, d, h = spec["bounds"]
                x, y, z = spec["spawn"]
                self.assertGreaterEqual(x, -w / 2.0)
                self.assertLessEqual(x, w / 2.0)
                self.assertGreaterEqual(y, 0.0)
                self.assertLessEqual(y, h)
                self.assertGreaterEqual(z, -d / 2.0)
                self.assertLessEqual(z, d / 2.0)

    def test_user_scale_is_sane(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                us = spec["user_scale"]
                self.assertGreaterEqual(us, 1.0)
                self.assertLessEqual(us, 40.0)
                if env_id in MACHINE_IDS:
                    # the user must read as a small figure standing in a machine
                    apparent = DEFAULT_EYE_HEIGHT / us
                    self.assertGreater(apparent, 0.08, "%s: user is too tiny" % env_id)
                    self.assertLess(apparent, 0.35, "%s: user is too big" % env_id)
                else:
                    self.assertEqual(us, 1.0, "%s should be life size" % env_id)

    def test_bambu_user_is_about_fifteen_centimetres(self):
        us = self.specs["bambu_x1c"]["user_scale"]
        self.assertAlmostEqual(DEFAULT_EYE_HEIGHT / us, 0.15, delta=0.03)

    def test_part_counts_are_non_trivial(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                parts = S.count_parts(spec)
                self.assertGreaterEqual(
                    parts, MIN_PARTS[env_id],
                    "%s has only %d parts" % (env_id, parts))

    def test_lights_and_materials_present(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                self.assertTrue(spec["lights"])
                self.assertTrue(spec["materials"])
                names = [m["name"] for m in spec["materials"]]
                self.assertEqual(len(names), len(set(names)))

    def test_every_material_is_referenced(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                used = set()
                for node, _w in S.iter_nodes(spec):
                    if node.get("material") is not None:
                        used.add(node["material"])
                unused = sorted(set(range(len(spec["materials"]))) - used)
                self.assertEqual(
                    unused, [],
                    "%s declares unused materials: %s"
                    % (env_id, [spec["materials"][i]["name"] for i in unused]))

    def test_every_environment_tessellates(self):
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                triangles = 0
                for node, _world in S.iter_nodes(spec):
                    shape = node.get("shape")
                    if shape is None:
                        continue
                    try:
                        pos, nrm, uv, idx = S.tessellate_shape(shape)
                    except S.TessellationError as exc:
                        self.fail("%s/%s: %s" % (env_id, node.get("name"), exc))
                    self.assertTrue(idx)
                    self.assertEqual(len(nrm), len(pos))
                    self.assertEqual(len(uv) * 3, len(pos) * 2)
                    triangles += len(idx) // 3
                self.assertGreater(triangles, 500)

    def test_geometry_stays_near_the_declared_bounds(self):
        """Nothing may be wildly outside the room the spec advertises."""
        for env_id, spec in self.specs.items():
            with self.subTest(environment=env_id):
                lo, hi = S.spec_bounds(spec)
                w, d, h = spec["bounds"]
                # a 15% overhang covers feet, ducts and lids poking out
                self.assertLessEqual(hi[0], w * 0.5 * 1.15 + 0.05)
                self.assertGreaterEqual(lo[0], -w * 0.5 * 1.15 - 0.05)
                self.assertLessEqual(hi[2], d * 0.5 * 1.15 + 0.05)
                self.assertGreaterEqual(lo[2], -d * 0.5 * 1.15 - 0.05)
                self.assertLessEqual(hi[1], h * 1.20 + 0.05)
                self.assertGreaterEqual(lo[1], -0.25)

    def test_generation_is_deterministic(self):
        for env_id in BUILTIN_IDS:
            with self.subTest(environment=env_id):
                a = S.spec_to_json(build_builtin(env_id))
                b = S.spec_to_json(build_builtin(env_id))
                self.assertEqual(a, b)


# ---------------------------------------------------------------------------


class TestGeneratedJsonIsUpToDate(unittest.TestCase):
    """Regeneration guard for ``Resources/environments``."""

    def test_resources_directory_exists(self):
        self.assertTrue(os.path.isdir(registry.resources_dir()),
                        "run tools/gen_environments.py")

    def test_each_generated_file_matches_the_generator(self):
        for env_id in BUILTIN_IDS:
            with self.subTest(environment=env_id):
                path = os.path.join(registry.resources_dir(), env_id + ".json")
                self.assertTrue(
                    os.path.isfile(path),
                    "%s is missing; run tools/gen_environments.py" % path)
                with open(path, "r", encoding="utf-8") as fh:
                    on_disk = fh.read()
                fresh = S.spec_to_json(build_builtin(env_id))
                self.assertEqual(
                    on_disk, fresh,
                    "%s.json is stale; run tools/gen_environments.py" % env_id)

    def test_generated_files_load_and_validate(self):
        for env_id in BUILTIN_IDS:
            with self.subTest(environment=env_id):
                path = os.path.join(registry.resources_dir(), env_id + ".json")
                spec = S.load_spec(path)
                self.assertEqual(S.validate_spec(spec), [])
                self.assertEqual(spec["id"], env_id)

    def test_gen_environments_check_mode_passes(self):
        sys.path.insert(0, os.path.join(_MOD_DIR, "tools"))
        try:
            gen = importlib.import_module("gen_environments")
            importlib.reload(gen)
            self.assertEqual(gen.generate(check=True, quiet=True), 0)
        finally:
            sys.path.remove(os.path.join(_MOD_DIR, "tools"))


# ---------------------------------------------------------------------------


class TestRegistry(unittest.TestCase):

    def test_builtins_are_listed_first_and_in_order(self):
        infos = registry.list_environments()
        ids = [i.id for i in infos]
        for env_id in BUILTIN_IDS:
            self.assertIn(env_id, ids)
        self.assertEqual(ids[:len(registry.BUILTIN_ORDER)], list(registry.BUILTIN_ORDER))
        # stable across calls
        self.assertEqual(ids, [i.id for i in registry.list_environments()])

    def test_info_fields(self):
        info = registry.get("bambu_x1c").info
        self.assertEqual(info.id, "bambu_x1c")
        self.assertTrue(info.name)
        self.assertTrue(info.description)
        self.assertIsInstance(info.user_scale, float)
        self.assertEqual(len(info.bounds), 3)
        self.assertEqual(len(info.spawn), 3)
        self.assertGreater(info.part_count, 400)

    def test_get_unknown_raises_helpful_key_error(self):
        with self.assertRaises(KeyError) as ctx:
            registry.get("no_such_environment")
        message = str(ctx.exception)
        self.assertIn("no_such_environment", message)
        self.assertIn("bambu_x1c", message)

    def test_environment_api_surface(self):
        env = registry.get("laser_cutter")
        self.assertIsInstance(env.spec, dict)
        self.assertIsInstance(env.user_scale, float)
        self.assertEqual(len(env.spawn), 3)
        anchors = env.anchors
        self.assertIn("bed_surface", anchors)
        self.assertEqual(anchors["bed_surface"].name, "bed_surface")
        self.assertEqual(len(anchors["bed_surface"].position), 3)
        self.assertEqual(len(anchors["bed_surface"].rotation), 4)
        self.assertEqual(len(anchors["bed_surface"].size), 2)
        primary = env.primary_anchor()
        self.assertIsNotNone(primary)
        self.assertEqual(primary.name, "bed_surface")
        self.assertEqual(env.validate(), [])

    def test_spec_is_cached(self):
        env = registry.get("void")
        self.assertIs(env.spec, env.spec)

    def test_build_scenegraph_without_pivy_returns_none(self):
        env = registry.get("void")
        result = env.build_scenegraph()
        if builder.coin_available():
            self.assertIsNotNone(result)
        else:
            self.assertIsNone(result)

    def test_register_and_unregister(self):
        spec = build_builtin("void")
        spec = json.loads(S.spec_to_json(spec))
        spec["id"] = "unit_test_env"
        spec["name"] = "Unit test env"
        env = registry.register(spec)
        try:
            self.assertTrue(registry.has("unit_test_env"))
            self.assertIs(registry.get("unit_test_env"), env)
            self.assertIn("unit_test_env", [i.id for i in registry.list_environments()])
            # a runtime registration survives a refresh
            registry.refresh(force=True)
            self.assertTrue(registry.has("unit_test_env"))
        finally:
            self.assertTrue(registry.unregister("unit_test_env"))
        self.assertFalse(registry.has("unit_test_env"))
        self.assertFalse(registry.unregister("unit_test_env"))

    def test_register_rejects_nonsense(self):
        with self.assertRaises(TypeError):
            registry.register(42)
        with self.assertRaises(ValueError):
            registry.register({"name": "no id"})

    def test_user_directory_shadows_a_builtin(self):
        """A JSON dropped in the user directory replaces the shipped spec."""
        user_dir = registry.user_environment_dir()
        spec = json.loads(S.spec_to_json(build_builtin("void")))
        spec["name"] = "Customised void"
        path = os.path.join(user_dir, "void.json")
        S.save_spec(spec, path)
        try:
            registry.refresh(force=True)
            env = registry.get("void")
            self.assertEqual(env.source, registry.SOURCE_USER)
            self.assertEqual(env.name, "Customised void")
            # and it still comes first in the listing, in the built-in slot
            ids = [i.id for i in registry.list_environments()]
            self.assertEqual(ids[:len(registry.BUILTIN_ORDER)],
                             list(registry.BUILTIN_ORDER))
        finally:
            os.remove(path)
            registry.refresh(force=True)
        self.assertEqual(registry.get("void").source, registry.SOURCE_BUILTIN)

    def test_user_directory_can_add_a_new_environment(self):
        user_dir = registry.user_environment_dir()
        spec = json.loads(S.spec_to_json(build_builtin("void")))
        spec["id"] = "my_room"
        spec["name"] = "My room"
        path = os.path.join(user_dir, "my_room.json")
        S.save_spec(spec, path)
        try:
            registry.refresh(force=True)
            env = registry.get("my_room")
            self.assertEqual(env.name, "My room")
            self.assertEqual(env.validate(), [])
            ids = [i.id for i in registry.list_environments()]
            # user specs come after the built-ins
            self.assertGreater(ids.index("my_room"), ids.index("void"))
        finally:
            os.remove(path)
            registry.refresh(force=True)


# ---------------------------------------------------------------------------


class TestBuilderConversions(unittest.TestCase):
    """The Y-up to Z-up conversion is pure math and testable without pivy."""

    def test_round_trip(self):
        for p in ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (-4.5, 0.25, 7.0)):
            self.assertEqual(builder.zup_to_yup(builder.yup_to_zup(p)), p)

    def test_up_axis_maps_to_z(self):
        self.assertEqual(builder.yup_to_zup((0.0, 1.0, 0.0)), (0.0, 0.0, 1.0))
        self.assertEqual(builder.yup_to_zup((1.0, 0.0, 0.0)), (1.0, 0.0, 0.0))
        self.assertEqual(builder.yup_to_zup((0.0, 0.0, 1.0)), (0.0, -1.0, 0.0))

    def test_conversion_is_right_handed(self):
        x = builder.yup_to_zup((1.0, 0.0, 0.0))
        y = builder.yup_to_zup((0.0, 1.0, 0.0))
        z = builder.yup_to_zup((0.0, 0.0, 1.0))
        cross = (
            x[1] * y[2] - x[2] * y[1],
            x[2] * y[0] - x[0] * y[2],
            x[0] * y[1] - x[1] * y[0],
        )
        self.assertEqual(cross, z)

    def test_build_coin_rejects_an_invalid_spec(self):
        if not builder.coin_available():
            self.skipTest("pivy.coin is not available")
        with self.assertRaises(ValueError):
            builder.build_coin({"id": "broken"})

    def test_build_coin_produces_a_graph(self):
        if not builder.coin_available():
            self.skipTest("pivy.coin is not available")
        root = builder.build_coin(build_builtin("void"))
        self.assertIsNotNone(root)
        self.assertGreater(root.getNumChildren(), 1)


# ---------------------------------------------------------------------------


class TestScaleController(unittest.TestCase):

    def test_defaults(self):
        ctl = ScaleController()
        self.assertAlmostEqual(ctl.scale, 1.0)
        self.assertAlmostEqual(ctl.world_scale, 1.0)
        self.assertEqual(ctl.world_offset, (0.0, 0.0, 0.0))
        self.assertGreater(ctl.duration, 0.0)

    def test_transition_duration_falls_back_without_freecad(self):
        # FreeCAD is not importable in the test environment; the lookup must
        # not raise and must return the documented default.
        self.assertAlmostEqual(transition_duration(), 0.6)
        self.assertAlmostEqual(transition_duration(1.25), 1.25)

    def test_shrinking_the_user_grows_the_world(self):
        """The sign convention the GUI layer relies on."""
        ctl = ScaleController()
        ctl.set_scale(12.0, animate=False)
        self.assertAlmostEqual(ctl.scale, 12.0)
        self.assertAlmostEqual(ctl.world_scale, 12.0)
        ctl.set_scale(4.0, animate=False)
        self.assertAlmostEqual(ctl.world_scale, 4.0)
        self.assertLess(ctl.world_scale, 12.0, "world_scale grows with scale")

    def test_world_offset_keeps_the_spawn_under_the_user(self):
        env = registry.get("bambu_x1c")
        ctl = ScaleController()
        ctl.set_environment(env)
        self.assertAlmostEqual(ctl.scale, env.user_scale)
        # the spawn point must map to the tracking origin, not slide away
        mapped = ctl.to_view(env.spawn)
        for k in range(3):
            self.assertAlmostEqual(mapped[k], 0.0, places=9)
        # and it stays there at any scale
        for scale in (1.0, 3.0, 11.0, 30.0):
            ctl.set_scale(scale, animate=False)
            mapped = ctl.to_view(env.spawn)
            for k in range(3):
                self.assertAlmostEqual(mapped[k], 0.0, places=9)

    def test_environment_geometry_grows_around_the_user(self):
        env = registry.get("bambu_x1c")
        ctl = ScaleController()
        ctl.set_environment(env)
        plate_edge = (env.anchors["build_plate"].size[0] * 0.5, env.spawn[1], 0.0)
        near = ctl.to_view(plate_edge)
        ctl.set_scale(1.0, animate=False)
        far = ctl.to_view(plate_edge)
        self.assertGreater(abs(near[0]), abs(far[0]),
                           "the plate looks bigger when the user is smaller")

    def test_apparent_height_inside_the_printer(self):
        ctl = ScaleController()
        ctl.set_environment(registry.get("bambu_x1c"))
        self.assertAlmostEqual(ctl.apparent_height, 0.15, delta=0.03)
        eye = ctl.eye_position()
        self.assertAlmostEqual(eye[1] - registry.get("bambu_x1c").spawn[1],
                               ctl.apparent_height, places=9)

    def test_local_reference_space_compensates_eye_height(self):
        stage = ScaleController(reference_space="stage")
        local = ScaleController(reference_space="local")
        for ctl in (stage, local):
            ctl.set_environment(registry.get("void"))
            ctl.set_scale(2.0, animate=False)
        self.assertAlmostEqual(stage.world_offset[1], 0.0, places=9)
        self.assertAlmostEqual(local.world_offset[1], -DEFAULT_EYE_HEIGHT, places=9)

    def test_scale_about_point_holds_that_point(self):
        ctl = ScaleController()
        ctl.set_environment(registry.get("laser_cutter"))
        pick = (0.30, 0.12, -0.10)
        before = ctl.to_view(pick)
        ctl.scale_about_point(pick, 24.0, animate=False)
        after = ctl.to_view(pick)
        for k in range(3):
            self.assertAlmostEqual(before[k], after[k], places=9)
        self.assertAlmostEqual(ctl.scale, 24.0)

    def test_scale_about_point_holds_through_the_animation(self):
        ctl = ScaleController(duration=0.5)
        ctl.set_environment(registry.get("bambu_x1c"))
        pick = (0.05, 0.054, 0.02)
        before = ctl.to_view(pick)
        ctl.scale_about_point(pick, 1.0)
        for _ in range(20):
            ctl.step(0.05)
            after = ctl.to_view(pick)
            for k in range(3):
                self.assertAlmostEqual(before[k], after[k], places=9)

    def test_step_eases_and_finishes(self):
        ctl = ScaleController(scale=1.0, duration=0.6)
        ctl.set_scale(16.0)
        self.assertTrue(ctl.animating)
        self.assertAlmostEqual(ctl.scale, 1.0)
        seen = []
        elapsed = 0.0
        while ctl.animating and elapsed < 5.0:
            changed = ctl.step(1.0 / 60.0)
            self.assertTrue(changed)
            seen.append(ctl.scale)
            elapsed += 1.0 / 60.0
        self.assertFalse(ctl.animating)
        self.assertAlmostEqual(ctl.scale, 16.0)
        self.assertGreater(len(seen), 10)
        # monotone, and never overshoots
        for a, b in zip(seen, seen[1:]):
            self.assertLessEqual(a, b + 1e-12)
        self.assertLessEqual(max(seen), 16.0 + 1e-9)
        self.assertGreaterEqual(min(seen), 1.0 - 1e-9)
        # eased, not linear: the mid frame is not the geometric mean
        mid = seen[len(seen) // 2]
        self.assertAlmostEqual(mid, math.sqrt(16.0), delta=4.0)

    def test_step_reports_no_change_once_settled(self):
        ctl = ScaleController()
        ctl.step(0.1)
        self.assertFalse(ctl.step(0.1))
        ctl.set_scale(3.0, animate=False)
        self.assertTrue(ctl.step(0.0))
        self.assertFalse(ctl.step(0.1))

    def test_step_tolerates_rubbish_dt(self):
        ctl = ScaleController(duration=0.4)
        ctl.set_scale(8.0)
        ctl.step(-1.0)
        ctl.step(float("nan"))
        ctl.step("not a number")
        ctl.step(10.0)
        self.assertFalse(ctl.animating)
        self.assertAlmostEqual(ctl.scale, 8.0)

    def test_finish_jumps_to_target(self):
        ctl = ScaleController(duration=1.0)
        ctl.set_scale(9.0)
        ctl.finish()
        self.assertAlmostEqual(ctl.scale, 9.0)
        self.assertFalse(ctl.animating)

    def test_set_scale_ignores_invalid_values(self):
        ctl = ScaleController(scale=2.0)
        for bad in (0.0, -1.0, float("inf"), float("nan"), None, "big"):
            ctl.set_scale(bad, animate=False)
            self.assertAlmostEqual(ctl.scale, 2.0)

    def test_clip_planes_scale_with_the_world(self):
        ctl = ScaleController()
        ctl.set_environment(registry.get("bambu_x1c"))
        near_big, far_big = ctl.clip_planes()
        ctl.set_scale(1.0, animate=False)
        near_small, far_small = ctl.clip_planes()
        self.assertGreater(far_big, far_small,
                           "the far plane must follow the enlarged world")
        for near, far in ((near_big, far_big), (near_small, far_small)):
            self.assertGreater(near, 0.0)
            self.assertGreater(far, near)
            # hands are never clipped ...
            self.assertLessEqual(near, 0.15)
            # ... and the depth range stays inside the precision budget
            self.assertLessEqual(far / near, ctl.max_depth_ratio + 1e-6)

    def test_clip_planes_honour_the_viewer_unit_scale(self):
        metres = ScaleController()
        millis = ScaleController(unit_scale=1000.0)
        for ctl in (metres, millis):
            ctl.set_environment(registry.get("bambu_x1c"))
        nm, fm = metres.clip_planes()
        nk, fk = millis.clip_planes()
        self.assertAlmostEqual(nk, nm * 1000.0, places=6)
        self.assertAlmostEqual(fk, fm * 1000.0, places=3)
        self.assertAlmostEqual(millis.world_offset[1], metres.world_offset[1] * 1000.0,
                               places=6)

    def test_teleport_and_reset_pivot(self):
        env = registry.get("workshop")
        ctl = ScaleController()
        ctl.set_environment(env)
        ctl.teleport((1.0, 0.0, -2.0))
        mapped = ctl.to_view((1.0, 0.0, -2.0))
        for k in range(3):
            self.assertAlmostEqual(mapped[k], 0.0, places=9)
        ctl.reset_pivot()
        mapped = ctl.to_view(env.spawn)
        for k in range(3):
            self.assertAlmostEqual(mapped[k], 0.0, places=9)

    def test_set_environment_accepts_none_and_dicts(self):
        ctl = ScaleController()
        ctl.set_environment(None)
        self.assertAlmostEqual(ctl.scale, 1.0)
        ctl.set_environment(build_builtin("bambu_x1c"))
        self.assertAlmostEqual(ctl.scale, 11.0)


# ---------------------------------------------------------------------------


class TestDocumentPlacement(unittest.TestCase):

    def setUp(self):
        self.env = registry.get("bambu_x1c")
        self.anchor = self.env.primary_anchor()
        self.ctl = ScaleController()
        self.ctl.set_environment(self.env)

    def test_unknown_bbox_returns_none(self):
        self.assertIsNone(self.ctl.fit_document_to_anchor(None))
        self.assertIsNone(fit_document_to_anchor(None, self.anchor))

    def test_missing_anchor_returns_none(self):
        ctl = ScaleController()
        ctl.set_environment(registry.get("void"))
        self.assertIsNone(ctl.fit_document_to_anchor(((0, 0, 0), (10, 10, 10))))

    def test_small_part_keeps_its_real_size(self):
        # a 40 x 30 x 20 mm bracket fits the 256 mm plate: no rescaling
        fit = self.ctl.fit_document_to_anchor(((0, 0, 0), (40, 30, 20)))
        self.assertIsInstance(fit, FitTransform)
        self.assertFalse(fit.clipped)
        self.assertAlmostEqual(fit.scale, 0.001, places=9)

    def test_oversized_document_is_shrunk_to_fit(self):
        fit = self.ctl.fit_document_to_anchor(((0, 0, 0), (2000, 1500, 400)))
        self.assertTrue(fit.clipped)
        self.assertLess(fit.scale, 0.001)
        # the footprint now fits inside 80% of the plate
        w = 2000 * fit.scale
        d = 1500 * fit.scale
        self.assertLessEqual(w, self.anchor.size[0] * 0.8 + 1e-9)
        self.assertLessEqual(d, self.anchor.size[1] * 0.8 + 1e-9)

    def test_document_sits_centred_on_the_plate(self):
        lo, hi = (10.0, -20.0, 5.0), (50.0, 20.0, 45.0)
        fit = self.ctl.fit_document_to_anchor((lo, hi))
        # the centre of the footprint lands on the anchor
        centre = fit.apply(((lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5, lo[2]))
        for k in range(3):
            self.assertAlmostEqual(centre[k], self.anchor.position[k], places=9)
        # nothing sinks below the plate
        for cx in (lo[0], hi[0]):
            for cy in (lo[1], hi[1]):
                p = fit.apply((cx, cy, lo[2]))
                self.assertAlmostEqual(p[1], self.anchor.position[1], places=9)
                q = fit.apply((cx, cy, hi[2]))
                self.assertGreater(q[1], self.anchor.position[1])

    def test_anchor_rotation_puts_the_document_flat_on_the_plate(self):
        # the build plate anchor turns document +Z (up in FreeCAD) into
        # environment +Y (up in the spec)
        up = quat_rotate(self.anchor.rotation, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(up[0], 0.0, places=6)
        self.assertAlmostEqual(up[1], 1.0, places=6)
        self.assertAlmostEqual(up[2], 0.0, places=6)

    def test_bed_surface_anchor_of_the_laser(self):
        env = registry.get("laser_cutter")
        ctl = ScaleController()
        ctl.set_environment(env)
        fit = ctl.fit_document_to_anchor(((0, 0, 0), (600, 400, 3)))
        self.assertIsNotNone(fit)
        anchor = env.primary_anchor()
        base = fit.apply((300.0, 200.0, 0.0))
        for k in range(3):
            self.assertAlmostEqual(base[k], anchor.position[k], places=9)

    def test_allow_upscale(self):
        big = fit_document_to_anchor(((0, 0, 0), (10, 10, 10)), self.anchor,
                                     allow_upscale=True)
        small = fit_document_to_anchor(((0, 0, 0), (10, 10, 10)), self.anchor)
        self.assertGreater(big.scale, small.scale)

    def test_degenerate_inputs(self):
        self.assertIsNone(fit_document_to_anchor(((0, 0, 0), (0, 0, 0)), self.anchor))
        self.assertIsNone(fit_document_to_anchor("nonsense", self.anchor))
        self.assertIsNone(fit_document_to_anchor(((0, 0, 0), (1, 1, 1)), None))
        self.assertIsNone(fit_document_to_anchor(
            ((0, 0, 0), (1, 1, 1)), {"position": [0, 0, 0], "rotation": [0, 0, 0, 1],
                                     "size": [0.0, 1.0]}))
        self.assertIsNone(fit_document_to_anchor(
            ((0, 0, float("nan")), (1, 1, 1)), self.anchor))

    def test_fit_transform_serialises(self):
        fit = self.ctl.fit_document_to_anchor(((0, 0, 0), (40, 30, 20)))
        as_dict = fit.as_dict()
        self.assertEqual(len(as_dict["translation"]), 3)
        self.assertEqual(len(as_dict["rotation"]), 4)
        self.assertIsInstance(as_dict["scale"], float)

    def test_quat_helpers(self):
        q = quat_from_axis_angle((0, 1, 0), math.pi / 2.0)
        v = quat_rotate(q, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(v[0], 1.0, places=6)
        self.assertAlmostEqual(v[1], 0.0, places=6)
        self.assertAlmostEqual(v[2], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
