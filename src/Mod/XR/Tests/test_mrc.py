# SPDX-License-Identifier: LGPL-2.1-or-later
"""The MRC compositor, camera, output pipeline, LIV probe and session.

Everything here runs without FreeCAD, without Qt and without a GPU: the
geometry and the layout arithmetic are pure functions, the sinks are fed plain
bytes, and the one class that touches Coin3D is exercised through
``Tests/stubs``.
"""

import math
import os
import tempfile
import threading
import time
import unittest

from xrmrc import camera as cam
from xrmrc import compositor as comp
from xrmrc import externalcamera as ec
from xrmrc import liv as livmod
from xrmrc import output as out
from xrmrc import session as sess


def pose_at(position, forward=(0.0, 0.0, -1.0)):
    """A pose at ``position`` looking along ``forward``."""
    return cam.Pose(position, cam.q_look_rotation(forward))


# ==========================================================================
# quadrant layout
# ==========================================================================


class QuadrantLayoutTest(unittest.TestCase):
    def test_1920x1080_top_left_origin(self):
        rects = comp.quadrant_rects(1920, 1080)
        self.assertEqual(
            rects[comp.QUADRANT_FOREGROUND_COLOUR].as_tuple(), (0, 0, 960, 540)
        )
        self.assertEqual(
            rects[comp.QUADRANT_FOREGROUND_ALPHA].as_tuple(), (960, 0, 960, 540)
        )
        self.assertEqual(
            rects[comp.QUADRANT_BACKGROUND_COLOUR].as_tuple(), (0, 540, 960, 540)
        )
        self.assertEqual(rects[comp.QUADRANT_FOURTH].as_tuple(), (960, 540, 960, 540))

    def test_1920x1080_bottom_left_origin_flips_the_rows(self):
        rects = comp.quadrant_rects(1920, 1080, comp.ORIGIN_BOTTOM_LEFT)
        # Foreground stays on top of the image, which is the *high* row in GL.
        self.assertEqual(
            rects[comp.QUADRANT_FOREGROUND_COLOUR].as_tuple(), (0, 540, 960, 540)
        )
        self.assertEqual(
            rects[comp.QUADRANT_FOREGROUND_ALPHA].as_tuple(), (960, 540, 960, 540)
        )
        self.assertEqual(
            rects[comp.QUADRANT_BACKGROUND_COLOUR].as_tuple(), (0, 0, 960, 540)
        )
        self.assertEqual(rects[comp.QUADRANT_FOURTH].as_tuple(), (960, 0, 960, 540))

    def test_several_resolutions(self):
        cases = {
            (1280, 720): (640, 360),
            (3840, 2160): (1920, 1080),
            (640, 480): (320, 240),
            (1440, 1440): (720, 720),
            (2560, 1080): (1280, 540),
        }
        for (width, height), expected in cases.items():
            with self.subTest(resolution=(width, height)):
                layout = comp.layout_for(width, height)
                self.assertEqual(
                    (layout.quadrant_width, layout.quadrant_height), expected
                )
                for rect in layout.rects.values():
                    self.assertEqual((rect.width, rect.height), expected)

    def test_odd_resolutions_use_integer_division(self):
        # Screen.width / 2 in the reference implementation is integer division;
        # the odd column and row are simply not covered.
        layout = comp.layout_for(1921, 1081)
        self.assertEqual((layout.quadrant_width, layout.quadrant_height), (960, 540))
        self.assertEqual(layout.usable_size, (1920, 1080))
        self.assertEqual(
            layout.rect(comp.QUADRANT_FOURTH).as_tuple(), (960, 540, 960, 540)
        )

    def test_quadrants_tile_without_overlap(self):
        for width, height in ((1920, 1080), (1280, 720), (800, 600)):
            with self.subTest(resolution=(width, height)):
                layout = comp.layout_for(width, height)
                covered = set()
                for rect in layout.rects.values():
                    for x in (rect.x, rect.right - 1):
                        for y in (rect.y, rect.top - 1):
                            self.assertNotIn((x, y), covered)
                            covered.add((x, y))
                self.assertEqual(len(covered), 16)

    def test_quadrant_aspect_equals_frame_aspect(self):
        for width, height in ((1920, 1080), (1280, 720), (640, 480), (2560, 1080)):
            with self.subTest(resolution=(width, height)):
                layout = comp.layout_for(width, height)
                self.assertAlmostEqual(layout.aspect, width / height, places=6)

    def test_degenerate_resolutions(self):
        layout = comp.layout_for(1, 1)
        self.assertFalse(layout.valid)
        layout = comp.layout_for(0, 0)
        self.assertFalse(layout.valid)
        self.assertEqual(layout.aspect, 1.0)

    def test_unknown_origin_rejected(self):
        with self.assertRaises(ValueError):
            comp.quadrant_rects(1920, 1080, "middle")


class AspectHandlingTest(unittest.TestCase):
    def test_letterbox_into_a_wider_rect(self):
        rect = comp.Rect(0, 0, 960, 540)  # 16:9
        fitted = comp.fit_rect_to_aspect(rect, 4.0 / 3.0)
        self.assertEqual(fitted.height, 540)
        self.assertEqual(fitted.width, 720)
        self.assertEqual(fitted.x, (960 - 720) // 2)

    def test_letterbox_into_a_taller_rect(self):
        rect = comp.Rect(0, 0, 480, 640)
        fitted = comp.fit_rect_to_aspect(rect, 16.0 / 9.0)
        self.assertEqual(fitted.width, 480)
        self.assertEqual(fitted.height, 270)
        self.assertEqual(fitted.y, (640 - 270) // 2)

    def test_matching_aspect_is_untouched(self):
        rect = comp.Rect(10, 20, 960, 540)
        self.assertEqual(comp.fit_rect_to_aspect(rect, 16.0 / 9.0), rect)

    def test_vfov_conversion_preserves_horizontal_fov(self):
        source_aspect = 4.0 / 3.0
        target_aspect = 16.0 / 9.0
        vfov = 60.0
        new_vfov = comp.vfov_for_aspect(vfov, source_aspect, target_aspect)
        self.assertLess(new_vfov, vfov)  # wider frame, shorter vertical FOV

        def hfov(v, a):
            return math.degrees(2 * math.atan(math.tan(math.radians(v) / 2) * a))

        self.assertAlmostEqual(
            hfov(vfov, source_aspect), hfov(new_vfov, target_aspect), places=9
        )

    def test_vfov_conversion_is_identity_for_the_same_aspect(self):
        self.assertAlmostEqual(comp.vfov_for_aspect(55.0, 1.5, 1.5), 55.0, places=9)


# ==========================================================================
# the split plane
# ==========================================================================


class SplitDistanceTest(unittest.TestCase):
    def test_hand_computed_head_on(self):
        # Camera 5 m back along +Z looking at -Z; HMD at the origin, 1.6 m up.
        # The split is the projection of (target - camera) on the camera's
        # horizontal forward, which is exactly 5 m.
        camera = pose_at((0.0, 1.2, 5.0))
        hmd = pose_at((0.0, 1.6, 0.0))
        self.assertAlmostEqual(comp.split_distance(camera, hmd, 0.0, 0.1, 100.0), 5.0)

    def test_camera_height_does_not_change_the_split(self):
        hmd = pose_at((0.0, 1.6, 0.0))
        low = comp.split_distance(pose_at((0.0, 0.2, 5.0)), hmd, 0.0, 0.1, 100.0)
        high = comp.split_distance(pose_at((0.0, 4.0, 5.0)), hmd, 0.0, 0.1, 100.0)
        self.assertAlmostEqual(low, high)

    def test_tilting_the_camera_does_not_move_the_split(self):
        # Both forwards are flattened, so a camera looking steeply down still
        # splits at the same horizontal distance.  Without the flattening this
        # would come out as 5 / cos(tilt).
        hmd = pose_at((0.0, 1.6, 0.0))
        level = pose_at((0.0, 2.5, 5.0), (0.0, 0.0, -1.0))
        tilted = pose_at((0.0, 2.5, 5.0), (0.0, -1.0, -1.0))
        self.assertAlmostEqual(
            comp.split_distance(level, hmd, 0.0, 0.1, 100.0),
            comp.split_distance(tilted, hmd, 0.0, 0.1, 100.0),
        )

    def test_sideways_offset_projects_onto_the_forward_axis(self):
        # Camera at (3, _, 4) looking along -Z: only the Z separation counts.
        camera = pose_at((3.0, 1.2, 4.0))
        hmd = pose_at((0.0, 1.6, 0.0))
        self.assertAlmostEqual(comp.split_distance(camera, hmd, 0.0, 0.1, 100.0), 4.0)

    def test_hmd_offset_pushes_the_plane_along_the_head_s_facing(self):
        camera = pose_at((0.0, 1.2, 5.0))
        hmd = pose_at((0.0, 1.6, 0.0))  # facing -Z, i.e. towards the camera... no:
        # the HMD's forward is -Z, so a positive hmdOffset moves the target
        # further from the camera, which sits at +Z.
        self.assertAlmostEqual(
            comp.split_distance(camera, hmd, 0.3, 0.1, 100.0), 5.3
        )
        self.assertAlmostEqual(
            comp.split_distance(camera, hmd, -0.3, 0.1, 100.0), 4.7
        )

    def test_clamped_to_a_centimetre_inside_the_clip_range(self):
        hmd = pose_at((0.0, 1.6, 0.0))
        far_away = pose_at((0.0, 1.2, 900.0))
        self.assertAlmostEqual(
            comp.split_distance(far_away, hmd, 0.0, 0.1, 100.0),
            100.0 - comp.SPLIT_EPSILON,
        )
        behind = pose_at((0.0, 1.2, -3.0))  # HMD is behind the camera
        self.assertAlmostEqual(
            comp.split_distance(behind, hmd, 0.0, 0.1, 100.0),
            0.1 + comp.SPLIT_EPSILON,
        )

    def test_no_hmd_falls_back_to_the_near_plane(self):
        camera = pose_at((0.0, 1.2, 5.0))
        self.assertAlmostEqual(
            comp.split_distance(camera, None, 0.0, 0.2, 50.0),
            0.2 + comp.SPLIT_EPSILON,
        )

    def test_camera_looking_straight_down_is_not_a_division_by_zero(self):
        camera = pose_at((0.0, 3.0, 0.0), (0.0, -1.0, 0.0))
        hmd = pose_at((0.0, 1.6, 0.0))
        value = comp.split_distance(camera, hmd, 0.0, 0.1, 100.0)
        self.assertTrue(math.isfinite(value))
        self.assertGreaterEqual(value, 0.1)

    def test_very_narrow_clip_range(self):
        hmd = pose_at((0.0, 1.6, 0.0))
        camera = pose_at((0.0, 1.6, 5.0))
        value = comp.split_distance(camera, hmd, 0.0, 1.0, 1.005)
        self.assertTrue(1.0 <= value <= 1.005)

    def test_inverted_clip_range_raises(self):
        with self.assertRaises(ValueError):
            comp.split_distance(pose_at((0, 0, 1)), None, 0.0, 10.0, 1.0)


class ForegroundClipTest(unittest.TestCase):
    def test_near_offset_moves_the_plane(self):
        self.assertAlmostEqual(comp.foreground_clip_distance(5.0, 0.25, 0.1, 100.0), 5.25)
        self.assertAlmostEqual(comp.foreground_clip_distance(5.0, -0.5, 0.1, 100.0), 4.5)

    def test_clamped_to_the_clip_range(self):
        self.assertAlmostEqual(comp.foreground_clip_distance(5.0, -50.0, 0.1, 100.0), 0.1)
        self.assertAlmostEqual(comp.foreground_clip_distance(5.0, 500.0, 0.1, 100.0), 100.0)

    def test_background_full_scene_starts_at_near(self):
        self.assertAlmostEqual(
            comp.background_near_distance(5.0, 1.0, 0.1, 100.0, comp.BACKGROUND_FULL_SCENE),
            0.1,
        )

    def test_background_beyond_split_uses_far_offset(self):
        self.assertAlmostEqual(
            comp.background_near_distance(
                5.0, 1.0, 0.1, 100.0, comp.BACKGROUND_BEYOND_SPLIT
            ),
            6.0,
        )

    def test_unknown_background_mode_raises(self):
        with self.assertRaises(ValueError):
            comp.background_near_distance(5.0, 0.0, 0.1, 100.0, "sideways")


class PerspectiveMatrixTest(unittest.TestCase):
    def test_hand_computed(self):
        matrix = comp.perspective_matrix(90.0, 1.0, 1.0, 3.0)
        expected = (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, -2.0, -3.0),
            (0.0, 0.0, -1.0, 0.0),
        )
        for got_row, want_row in zip(matrix, expected):
            for got, want in zip(got_row, want_row):
                self.assertAlmostEqual(got, want)

    def test_matches_the_config_helper(self):
        config = ec.parse("fov=72\nnear=0.3\nfar=42\n")
        self.assertEqual(
            comp.perspective_matrix(72.0, 1.5, 0.3, 42.0), config.projection(1.5)
        )

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            comp.perspective_matrix(60.0, 0.0, 0.1, 10.0)
        with self.assertRaises(ValueError):
            comp.perspective_matrix(60.0, 1.0, 10.0, 1.0)


# ==========================================================================
# the compositor
# ==========================================================================


class CompositorTest(unittest.TestCase):
    def setUp(self):
        self.config = ec.parse("fov=60\nnear=0.1\nfar=100\nhmdOffset=0.2\n")
        self.compositor = comp.QuadrantCompositor(self.config, 1920, 1080)
        self.camera_pose = pose_at((0.0, 1.5, 4.0))
        self.hmd_pose = pose_at((0.0, 1.6, 0.0))

    def test_plan_has_one_pass_per_quadrant(self):
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertEqual(len(plan.passes), 4)
        self.assertEqual(
            [item.quadrant for item in plan.passes], list(comp.QUADRANTS)
        )

    def test_foreground_passes_are_clipped_background_is_not(self):
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertTrue(plan.pass_for(comp.QUADRANT_FOREGROUND_COLOUR).clipped)
        self.assertTrue(plan.pass_for(comp.QUADRANT_FOREGROUND_ALPHA).clipped)
        self.assertFalse(plan.pass_for(comp.QUADRANT_BACKGROUND_COLOUR).clipped)

    def test_the_two_foreground_passes_share_a_clip_distance(self):
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        colour = plan.pass_for(comp.QUADRANT_FOREGROUND_COLOUR)
        alpha = plan.pass_for(comp.QUADRANT_FOREGROUND_ALPHA)
        self.assertEqual(colour.clip_distance, alpha.clip_distance)
        self.assertEqual(colour.mode, comp.PASS_COLOUR)
        self.assertEqual(alpha.mode, comp.PASS_ALPHA)

    def test_split_matches_the_standalone_function(self):
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertAlmostEqual(
            plan.split,
            comp.split_distance(self.camera_pose, self.hmd_pose, 0.2, 0.1, 100.0),
        )
        self.assertAlmostEqual(plan.split, 4.2)

    def test_fourth_quadrant_defaults_to_the_first_person_view(self):
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertEqual(
            plan.pass_for(comp.QUADRANT_FOURTH).mode, comp.PASS_FIRST_PERSON
        )

    def test_fourth_quadrant_can_be_a_background_alpha(self):
        compositor = comp.QuadrantCompositor(
            self.config, 1920, 1080, fourth_quadrant=comp.FOURTH_BACKGROUND_ALPHA
        )
        plan = compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertEqual(plan.pass_for(comp.QUADRANT_FOURTH).mode, comp.PASS_ALPHA)

    def test_background_beyond_split_moves_the_background_near_plane(self):
        compositor = comp.QuadrantCompositor(
            self.config, 1920, 1080, background_mode=comp.BACKGROUND_BEYOND_SPLIT
        )
        plan = compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertAlmostEqual(plan.background_near, plan.split)
        self.assertAlmostEqual(
            plan.pass_for(comp.QUADRANT_BACKGROUND_COLOUR).near, plan.split
        )

    def test_chroma_key_reaches_the_background_clear_colour(self):
        config = ec.parse("r=0\ng=1\nb=0\na=1\n")
        compositor = comp.QuadrantCompositor(config, 1920, 1080)
        plan = compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertEqual(
            plan.pass_for(comp.QUADRANT_BACKGROUND_COLOUR).clear_colour,
            (0.0, 1.0, 0.0, 1.0),
        )
        self.assertEqual(
            plan.pass_for(comp.QUADRANT_FOREGROUND_COLOUR).clear_colour,
            (0.0, 0.0, 0.0, 0.0),
        )

    def test_resolution_change_rebuilds_the_layout(self):
        self.compositor.set_resolution(1280, 720)
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertEqual(
            plan.pass_for(comp.QUADRANT_FOURTH).rect.as_tuple(), (640, 360, 640, 360)
        )
        self.assertAlmostEqual(plan.aspect, 1280 / 720)

    def test_config_change_is_picked_up(self):
        self.compositor.set_config(ec.parse("fov=30\nnear=0.5\nfar=20\n"))
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertAlmostEqual(plan.vfov_deg, 30.0)
        self.assertAlmostEqual(plan.near, 0.5)
        self.assertAlmostEqual(plan.far, 20.0)

    def test_lens_override_adjusts_for_the_quadrant_shape(self):
        lens = cam.LensSettings(60.0, 4.0, 3.0)  # a 4:3 lens
        compositor = comp.QuadrantCompositor(self.config, 1920, 1080, lens=lens)
        # Rendered into a 16:9 quadrant, the vertical FOV shrinks so the
        # horizontal FOV is preserved.
        self.assertLess(compositor.vfov_deg, 60.0)
        self.assertAlmostEqual(
            compositor.vfov_deg,
            comp.vfov_for_aspect(60.0, 4.0 / 3.0, 1920 / 1080),
        )
        # ... and the content rectangle is letterboxed rather than stretched.
        content = compositor.content_rect(comp.QUADRANT_FOREGROUND_COLOUR)
        self.assertAlmostEqual(content.aspect, 4.0 / 3.0, places=2)

    def test_frame_skip_gate(self):
        compositor = comp.QuadrantCompositor(ec.parse("frameSkip=2\n"), 1920, 1080)
        rendered = [n for n in range(12) if compositor.should_render(n)]
        self.assertEqual(rendered, [0, 3, 6, 9])

    def test_no_frame_skip_renders_everything(self):
        self.assertTrue(all(self.compositor.should_render(n) for n in range(10)))

    def test_bad_modes_rejected(self):
        with self.assertRaises(ValueError):
            comp.QuadrantCompositor(self.config, fourth_quadrant="upside-down")
        with self.assertRaises(ValueError):
            comp.QuadrantCompositor(self.config, background_mode="upside-down")

    def test_describe_is_json_shaped(self):
        data = self.compositor.describe()
        self.assertIn("layout", data)
        self.assertIn("fourth_quadrant", data)
        plan = self.compositor.plan(self.camera_pose, self.hmd_pose)
        self.assertIsInstance(plan.as_dict()["passes"], list)


class CoinRendererTest(unittest.TestCase):
    """The Coin adapter, exercised through the stub scenegraph."""

    def setUp(self):
        from Tests import stubs

        stubs.install()
        self.addCleanup(stubs.uninstall)

    def test_clip_switch_builds_without_a_real_coin(self):
        renderer = comp.CoinQuadrantRenderer()
        self.assertIsNotNone(renderer.clip_switch())
        # built once and cached
        self.assertIs(renderer.clip_switch(), renderer.clip_switch())

    def test_render_is_a_no_op_without_a_widget(self):
        renderer = comp.CoinQuadrantRenderer()
        compositor = comp.QuadrantCompositor(ec.default_config())
        plan = compositor.plan(pose_at((0, 1, 3)), pose_at((0, 1.6, 0)))
        self.assertFalse(renderer.render(plan))
        self.assertEqual(renderer.frames_rendered, 0)

    def test_detach_forgets_everything(self):
        renderer = comp.CoinQuadrantRenderer()
        renderer.clip_switch()
        renderer.attach(object())
        renderer.detach()
        self.assertIsNone(renderer.widget)
        self.assertFalse(renderer.ready)


# ==========================================================================
# camera: sources, smoothing, lens
# ==========================================================================


class PoseMathTest(unittest.TestCase):
    def test_look_rotation_aims_minus_z(self):
        for target in ((1, 0, 0), (0, 0, 1), (-1, 0, 0), (0.3, 0.4, -0.5)):
            with self.subTest(target=target):
                pose = cam.Pose((0, 0, 0), cam.q_look_rotation(target))
                for got, want in zip(pose.forward(), cam.v_normalize(target)):
                    self.assertAlmostEqual(got, want)

    def test_look_rotation_straight_up_is_not_degenerate(self):
        pose = cam.Pose((0, 0, 0), cam.q_look_rotation((0, 1, 0)))
        for got, want in zip(pose.forward(), (0.0, 1.0, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_compose_and_inverse(self):
        parent = cam.Pose((1, 2, 3), cam.q_from_axis_angle((0, 1, 0), 0.7))
        child = cam.Pose((0, 0, -1), cam.q_from_axis_angle((1, 0, 0), -0.3))
        combined = parent.compose(child)
        self.assertTrue(
            parent.inverse().compose(combined).approx_equal(child, 1e-9)
        )

    def test_slerp_endpoints_and_midpoint(self):
        a = cam.q_identity()
        b = cam.q_from_axis_angle((0, 1, 0), math.pi / 2)
        self.assertAlmostEqual(abs(cam.q_dot(cam.q_slerp(a, b, 0.0), a)), 1.0)
        self.assertAlmostEqual(abs(cam.q_dot(cam.q_slerp(a, b, 1.0), b)), 1.0)
        mid = cam.q_slerp(a, b, 0.5)
        expected = cam.q_from_axis_angle((0, 1, 0), math.pi / 4)
        self.assertAlmostEqual(abs(cam.q_dot(mid, expected)), 1.0, places=9)

    def test_slerp_takes_the_short_way(self):
        a = cam.q_identity()
        b = cam.q_from_axis_angle((0, 1, 0), 2 * math.pi - 0.2)  # i.e. -0.2 rad
        mid = cam.q_slerp(a, b, 0.5)
        angle = 2 * math.acos(min(1.0, abs(mid[3])))
        self.assertLess(angle, 0.2)

    def test_matrix_quaternion_round_trip(self):
        import random

        random.seed(99)
        for _ in range(300):
            axis = tuple(random.uniform(-1, 1) for _ in range(3))
            if cam.v_length(axis) < 1e-6:
                continue
            q = cam.q_from_axis_angle(axis, random.uniform(-math.pi, math.pi))
            back = cam.q_from_matrix3(cam.q_to_matrix3(q))
            self.assertAlmostEqual(abs(cam.q_dot(q, back)), 1.0, places=9)

    def test_horizontal_flattens_and_normalises(self):
        self.assertEqual(cam.horizontal((0.0, 5.0, -3.0)), (0.0, 0.0, -1.0))
        self.assertEqual(cam.horizontal((0.0, 1.0, 0.0)), (0.0, 0.0, -1.0))


class SmoothingTest(unittest.TestCase):
    def test_alpha_is_bounded(self):
        for dt in (0.001, 1 / 90, 0.5, 10.0):
            for tau in (0.01, 0.1, 1.0):
                alpha = cam.smoothing_alpha(dt, tau)
                self.assertGreater(alpha, 0.0)
                self.assertLessEqual(alpha, 1.0)

    def test_zero_time_constant_follows_exactly(self):
        self.assertEqual(cam.smoothing_alpha(1 / 90, 0.0), 1.0)

    def test_first_update_snaps(self):
        smoother = cam.PoseSmoother(0.1, 0.1)
        target = pose_at((3.0, 0.0, 0.0))
        self.assertTrue(smoother.update(target, 1 / 90).approx_equal(target))

    def test_converges(self):
        smoother = cam.PoseSmoother(0.05, 0.05)
        smoother.reset(pose_at((0.0, 0.0, 0.0)))
        target = pose_at((1.0, 2.0, -3.0), (1.0, 0.0, 0.0))
        for _ in range(600):
            current = smoother.update(target, 1 / 90)
        self.assertTrue(current.approx_equal(target, 1e-4))

    def test_never_overshoots_position(self):
        smoother = cam.PoseSmoother(0.05, 0.05)
        smoother.reset(pose_at((0.0, 0.0, 0.0)))
        target = pose_at((10.0, 0.0, 0.0))
        previous = 0.0
        for _ in range(500):
            x = smoother.update(target, 1 / 90).position[0]
            self.assertGreaterEqual(x, previous - 1e-12)  # monotone
            self.assertLessEqual(x, 10.0 + 1e-9)          # never past the target
            previous = x

    def test_never_overshoots_rotation(self):
        smoother = cam.PoseSmoother(0.05, 0.05)
        start = cam.Pose((0, 0, 0), cam.q_identity())
        smoother.reset(start)
        target = cam.Pose((0, 0, 0), cam.q_from_axis_angle((0, 1, 0), 2.0))
        previous = 0.0
        for _ in range(500):
            current = smoother.update(target, 1 / 90)
            angle = 2 * math.acos(min(1.0, abs(current.orientation[3])))
            self.assertGreaterEqual(angle, previous - 1e-9)
            self.assertLessEqual(angle, 2.0 + 1e-9)
            previous = angle

    def test_a_huge_jump_does_not_overshoot_either(self):
        smoother = cam.PoseSmoother(0.05, 0.05)
        smoother.reset(pose_at((0.0, 0.0, 0.0)))
        # A tracker glitch: 1000 m in one frame.
        result = smoother.update(pose_at((1000.0, 0.0, 0.0)), 1.0)
        self.assertLess(result.position[0], 1000.0)
        self.assertGreater(result.position[0], 0.0)

    def test_smoothing_is_frame_rate_independent(self):
        target = pose_at((1.0, 0.0, 0.0))
        results = []
        for steps, dt in ((90, 1 / 90), (45, 1 / 45), (300, 1 / 300)):
            smoother = cam.PoseSmoother(0.1, 0.1)
            smoother.reset(pose_at((0.0, 0.0, 0.0)))
            for _ in range(steps):
                current = smoother.update(target, dt)
            results.append(current.position[0])
        for value in results[1:]:
            self.assertAlmostEqual(value, results[0], places=6)

    def test_none_target_leaves_the_pose_alone(self):
        smoother = cam.PoseSmoother()
        smoother.reset(pose_at((1.0, 0.0, 0.0)))
        self.assertEqual(smoother.update(None, 0.1).position, (1.0, 0.0, 0.0))


class PoseSourceTest(unittest.TestCase):
    def test_fixed(self):
        pose = pose_at((1.0, 2.0, 3.0))
        source = cam.FixedPose(pose)
        self.assertTrue(source.update(0.1, cam.CameraContext()).approx_equal(pose))

    def test_tracked_applies_the_offset(self):
        offset = cam.Pose((0.0, 0.05, -0.1))
        source = cam.TrackedPose(offset)
        self.assertIsNone(source.update(0.1, cam.CameraContext()))
        source.submit(pose_at((1.0, 1.0, 1.0)))
        result = source.update(0.1, cam.CameraContext())
        self.assertAlmostEqual(result.position[1], 1.05)
        self.assertAlmostEqual(result.position[2], 0.9)
        source.invalidate()
        self.assertIsNone(source.update(0.1, cam.CameraContext()))

    def test_tracked_falls_back_to_the_context(self):
        source = cam.TrackedPose()
        context = cam.CameraContext(tracker_pose=pose_at((2.0, 0.0, 0.0)))
        self.assertAlmostEqual(source.update(0.1, context).position[0], 2.0)

    def test_follow_hmd_sits_behind_and_looks_back(self):
        source = cam.FollowHmd(distance=2.0, height=0.5)
        hmd = pose_at((0.0, 1.6, 0.0))  # looking along -Z
        result = source.update(0.1, cam.CameraContext(hmd_pose=hmd))
        # behind the HMD is +Z
        self.assertAlmostEqual(result.position[2], 2.0)
        self.assertAlmostEqual(result.position[1], 2.1)
        # and it looks back at the head
        forward = result.forward()
        towards = cam.v_normalize(cam.v_sub(hmd.position, result.position))
        for got, want in zip(forward, towards):
            self.assertAlmostEqual(got, want)

    def test_follow_hmd_ignores_head_pitch(self):
        source = cam.FollowHmd(distance=2.0, height=0.0)
        level = pose_at((0.0, 1.6, 0.0), (0.0, 0.0, -1.0))
        looking_down = pose_at((0.0, 1.6, 0.0), (0.0, -1.0, -0.2))
        a = source.update(0.1, cam.CameraContext(hmd_pose=level))
        b = source.update(0.1, cam.CameraContext(hmd_pose=looking_down))
        for got, want in zip(a.position, b.position):
            self.assertAlmostEqual(got, want)

    def test_follow_hmd_side_offset(self):
        source = cam.FollowHmd(distance=0.0, height=0.0, side=1.0)
        hmd = pose_at((0.0, 1.6, 0.0))
        result = source.update(0.1, cam.CameraContext(hmd_pose=hmd))
        self.assertAlmostEqual(result.position[0], 1.0)

    def test_follow_hmd_without_an_hmd(self):
        self.assertIsNone(cam.FollowHmd().update(0.1, cam.CameraContext()))

    def test_orbit_advances_and_stays_on_the_circle(self):
        source = cam.Orbit(radius=3.0, height=1.0, degrees_per_second=90.0)
        hmd = pose_at((0.0, 1.6, 0.0))
        context = cam.CameraContext(hmd_pose=hmd)
        first = source.update(0.0, context)
        self.assertAlmostEqual(first.position[2], 3.0)
        self.assertAlmostEqual(first.position[1], 2.6)
        source.update(1.0, context)  # a quarter turn
        second = source.update(0.0, context)
        self.assertAlmostEqual(second.position[0], 3.0, places=6)
        self.assertAlmostEqual(second.position[2], 0.0, places=6)
        # always looking at the centre
        towards = cam.v_normalize(cam.v_sub(hmd.position, second.position))
        for got, want in zip(second.forward(), towards):
            self.assertAlmostEqual(got, want)

    def test_orbit_around_a_fixed_centre(self):
        source = cam.Orbit(radius=1.0, height=0.0, centre=(5.0, 0.0, 5.0))
        result = source.update(0.0, cam.CameraContext())
        self.assertAlmostEqual(result.position[0], 5.0)
        self.assertAlmostEqual(result.position[2], 6.0)

    def test_make_source_and_unknown_name(self):
        self.assertIsInstance(cam.make_source("orbit", radius=1.0), cam.Orbit)
        with self.assertRaises(KeyError):
            cam.make_source("dolly")


class LensTest(unittest.TestCase):
    def test_default_matches_a_6mm_lens_on_a_pi_hq_sensor(self):
        lens = cam.LensSettings()
        self.assertAlmostEqual(lens.focal_length(), 6.0, places=2)
        self.assertAlmostEqual(lens.aspect, 6.29 / 4.71)

    def test_focal_length_round_trip(self):
        for focal in (2.8, 6.0, 16.0, 50.0):
            with self.subTest(focal=focal):
                lens = cam.LensSettings.from_focal_length(focal)
                self.assertAlmostEqual(lens.focal_length(), focal, places=9)

    def test_horizontal_fov_exceeds_vertical_on_a_wide_sensor(self):
        lens = cam.LensSettings(60.0, 16.0, 9.0)
        self.assertGreater(lens.hfov_deg, lens.vfov_deg)

    def test_from_preferences(self):
        values = {
            "TPPCamVFov": 50.0,
            "TPPCamAspectW": 16.0,
            "TPPCamAspectH": 9.0,
        }
        lens = cam.LensSettings.from_preferences(
            lambda key, default: values.get(key, default)
        )
        self.assertAlmostEqual(lens.vfov_deg, 50.0)
        self.assertAlmostEqual(lens.aspect, 16.0 / 9.0)

    def test_zero_focal_rejected(self):
        with self.assertRaises(ValueError):
            cam.LensSettings.from_focal_length(0.0)


class MRCCameraTest(unittest.TestCase):
    def test_update_smooths_towards_the_source(self):
        camera = cam.MRCCamera(cam.FixedPose(pose_at((10.0, 0.0, 0.0))))
        camera.smoother.reset(pose_at((0.0, 0.0, 0.0)))
        first = camera.update(1 / 90, cam.CameraContext())
        self.assertGreater(first.position[0], 0.0)
        self.assertLess(first.position[0], 10.0)
        self.assertAlmostEqual(camera.raw_pose.position[0], 10.0)

    def test_a_source_with_no_pose_keeps_the_last_one(self):
        camera = cam.MRCCamera(cam.TrackedPose())
        self.assertIsNone(camera.update(1 / 90, cam.CameraContext()))
        camera.source.submit(pose_at((1.0, 0.0, 0.0)))
        held = camera.update(1 / 90, cam.CameraContext())
        camera.source.invalidate()
        self.assertIs(camera.update(1 / 90, cam.CameraContext()), held)

    def test_set_source_clears_the_smoother(self):
        camera = cam.MRCCamera(cam.FixedPose(pose_at((1.0, 0.0, 0.0))))
        camera.update(1 / 90, cam.CameraContext())
        camera.set_source(cam.FixedPose(pose_at((5.0, 0.0, 0.0))))
        self.assertIsNone(camera.pose)
        self.assertAlmostEqual(
            camera.update(1 / 90, cam.CameraContext()).position[0], 5.0
        )

    def test_from_preferences_reads_the_tpp_keys(self):
        values = {
            "TPPCamVFov": 42.88,
            "TPPCamXTransl": 100.0,   # mm
            "TPPCamZTransl": 20.0,    # -> Y, matching read_preferences
            "TPPCamYTransl": -50.0,   # -> Z
            "MRCCamSource": "tracked",
        }
        camera = cam.MRCCamera.from_preferences(
            lambda key, default: values.get(key, default),
            lambda key, default: values.get(key, default),
        )
        self.assertIsInstance(camera.source, cam.TrackedPose)
        offset = camera.source.offset.position
        self.assertAlmostEqual(offset[0], 0.1)
        self.assertAlmostEqual(offset[1], 0.02)
        self.assertAlmostEqual(offset[2], -0.05)

    def test_from_preferences_other_sources(self):
        for name, expected in (
            ("follow_hmd", cam.FollowHmd),
            ("orbit", cam.Orbit),
            ("fixed", cam.FixedPose),
            ("nonsense", cam.FixedPose),
        ):
            with self.subTest(source=name):
                camera = cam.MRCCamera.from_preferences(
                    lambda key, default: default,
                    lambda key, default: name,
                )
                self.assertIsInstance(camera.source, expected)

    def test_describe_is_json_shaped(self):
        camera = cam.MRCCamera(cam.FixedPose(pose_at((1.0, 0.0, 0.0))))
        camera.update(1 / 90, cam.CameraContext())
        data = camera.describe()
        self.assertEqual(data["source"]["source"], "fixed")
        self.assertIn("lens", data)
        self.assertIsNotNone(data["pose"])


# ==========================================================================
# output
# ==========================================================================


class BlockingSink(out.FrameSink):
    """A sink that will not finish until it is told to."""

    name = "blocking"

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.seen = []

    def _write(self, frame):
        self.gate.wait(timeout=5.0)
        self.seen.append(frame.index)
        return True


class RateLimiterTest(unittest.TestCase):
    def test_first_frame_always_passes(self):
        limiter = out.RateLimiter(30.0)
        self.assertTrue(limiter.should_emit(now=0.0))

    def test_holds_back_between_intervals(self):
        limiter = out.RateLimiter(30.0)
        limiter.should_emit(now=0.0)
        self.assertFalse(limiter.should_emit(now=0.01))
        self.assertTrue(limiter.should_emit(now=0.034))
        self.assertEqual(limiter.emitted, 2)
        self.assertEqual(limiter.skipped, 1)

    def test_ninety_hertz_down_to_thirty(self):
        limiter = out.RateLimiter(30.0)
        for frame in range(90):
            limiter.should_emit(now=frame / 90.0)
        self.assertEqual(limiter.emitted, 30)
        self.assertEqual(limiter.skipped, 60)

    def test_zero_fps_means_unlimited(self):
        limiter = out.RateLimiter(0.0)
        for _ in range(10):
            self.assertTrue(limiter.should_emit(now=0.0))
        self.assertEqual(limiter.skipped, 0)

    def test_a_long_stall_does_not_cause_a_burst(self):
        limiter = out.RateLimiter(30.0)
        limiter.should_emit(now=0.0)
        limiter.should_emit(now=100.0)  # a very late frame
        self.assertFalse(limiter.should_emit(now=100.001))


class SinkTest(unittest.TestCase):
    def setUp(self):
        self.spec = out.FrameSpec(4, 2, out.PIXEL_RGBA8, 30.0)
        self.payload = bytes(range(32))

    def test_frame_spec_arithmetic(self):
        self.assertEqual(self.spec.stride, 16)
        self.assertEqual(self.spec.frame_bytes, 32)
        with self.assertRaises(ValueError):
            out.FrameSpec(1, 1, "yuv420p")

    def test_null_sink_counts(self):
        sink = out.NullSink()
        sink.open(self.spec)
        self.assertTrue(sink.submit(out.Frame(self.payload, self.spec)))
        self.assertEqual(sink.stats.written, 1)
        self.assertEqual(sink.stats.dropped, 0)

    def test_a_closed_sink_drops(self):
        sink = out.NullSink()
        self.assertFalse(sink.submit(out.Frame(self.payload, self.spec)))
        self.assertEqual(sink.stats.dropped, 1)

    def test_an_exception_is_counted_not_raised(self):
        def boom(frame):
            raise RuntimeError("the encoder fell over")

        sink = out.CallbackSink(boom)
        sink.open(self.spec)
        self.assertFalse(sink.submit(out.Frame(self.payload, self.spec)))
        self.assertEqual(sink.stats.errors, 1)
        self.assertIn("fell over", sink.stats.last_error)

    def test_callback_sink_passes_the_frame_through(self):
        seen = []
        sink = out.CallbackSink(seen.append)
        sink.open(self.spec)
        sink.submit(out.Frame(self.payload, self.spec, index=7))
        self.assertEqual(seen[0].index, 7)

    def test_raw_frame_sink_writes_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "frames.raw")
            sink = out.RawFrameSink(path)
            self.assertTrue(sink.open(self.spec))
            sink.submit(out.Frame(self.payload, self.spec))
            sink.submit(out.Frame(self.payload, self.spec))
            sink.close()
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), self.payload * 2)
            self.assertEqual(sink.stats.written, 2)

    def test_raw_frame_sink_refuses_a_frame_without_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = out.RawFrameSink(os.path.join(directory, "f.raw"))
            sink.open(self.spec)
            self.assertFalse(sink.submit(out.Frame(None, self.spec)))
            self.assertEqual(sink.stats.dropped, 1)
            sink.close()

    def test_raw_frame_sink_to_a_stream(self):
        import io

        buffer = io.BytesIO()
        sink = out.RawFrameSink(buffer)
        sink.open(self.spec)
        sink.submit(out.Frame(self.payload, self.spec))
        self.assertEqual(buffer.getvalue(), self.payload)

    def test_raw_frame_sink_on_an_unwritable_path(self):
        sink = out.RawFrameSink("/proc/definitely/not/writable/frames.raw")
        self.assertFalse(sink.open(self.spec))
        self.assertIsNotNone(sink.stats.last_error)

    def test_image_sequence_sink_writes_ppm(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = out.ImageSequenceSink(directory, prefix="shot")
            sink.open(self.spec)
            sink.submit(out.Frame(self.payload, self.spec, index=3))
            path = os.path.join(directory, "shot_000003.ppm")
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as handle:
                data = handle.read()
            self.assertTrue(data.startswith(b"P6\n4 2\n255\n"))
            self.assertEqual(len(data), len(b"P6\n4 2\n255\n") + 4 * 2 * 3)
            # RGBA in, RGB out: the first pixel keeps its first three bytes
            self.assertEqual(data[-24:-21], self.payload[0:3])

    def test_image_sequence_sink_swaps_bgra(self):
        spec = out.FrameSpec(1, 1, out.PIXEL_BGRA8)
        with tempfile.TemporaryDirectory() as directory:
            sink = out.ImageSequenceSink(directory)
            sink.open(spec)
            sink.submit(out.Frame(bytes((10, 20, 30, 255)), spec))
            with open(sink.written_paths[0], "rb") as handle:
                data = handle.read()
            self.assertEqual(data[-3:], bytes((30, 20, 10)))

    def test_image_sequence_sink_honours_its_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = out.ImageSequenceSink(directory, limit=2)
            sink.open(self.spec)
            for index in range(5):
                sink.submit(out.Frame(self.payload, self.spec, index=index))
            self.assertEqual(len(sink.written_paths), 2)
            self.assertEqual(sink.stats.dropped, 3)

    def test_image_sequence_sink_rejects_a_short_buffer(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = out.ImageSequenceSink(directory)
            sink.open(self.spec)
            self.assertFalse(sink.submit(out.Frame(b"short", self.spec)))

    def test_spectator_sink_uses_the_widget_hook(self):
        seen = []

        class FakeWidget:
            def present_mrc_frame(self, frame):
                seen.append(frame)
                return True

        sink = out.SpectatorWindowSink(FakeWidget())
        self.assertTrue(sink.open(self.spec))
        self.assertTrue(sink.submit(out.Frame(None, self.spec)))
        self.assertEqual(len(seen), 1)

    def test_spectator_sink_without_a_widget(self):
        sink = out.SpectatorWindowSink()
        self.assertFalse(sink.open(self.spec))
        self.assertFalse(sink.submit(out.Frame(None, self.spec)))

    def test_spectator_sink_with_a_widget_that_lacks_the_hook(self):
        sink = out.SpectatorWindowSink(object())
        sink.open(self.spec)
        self.assertFalse(sink.submit(out.Frame(None, self.spec)))


class DroppedFrameTest(unittest.TestCase):
    """The point of the whole output design: a slow sink must not block."""

    def setUp(self):
        self.spec = out.FrameSpec(4, 2, out.PIXEL_RGBA8, 30.0)
        self.payload = bytes(32)

    def test_a_slow_sink_drops_frames_instead_of_blocking(self):
        inner = BlockingSink()
        sink = out.AsyncSink(inner, max_queue=2, drop_oldest=False)
        self.assertTrue(sink.open(self.spec))
        self.addCleanup(sink.close)
        self.addCleanup(inner.gate.set)

        started = time.monotonic()
        for index in range(20):
            sink.submit(out.Frame(self.payload, self.spec, index=index))
        elapsed = time.monotonic() - started

        # Twenty frames into a sink that is wedged: the calls must have
        # returned essentially instantly.
        self.assertLess(elapsed, 1.0)
        self.assertEqual(sink.stats.submitted, 20)
        # At most one in the worker plus two in the queue got through.
        self.assertLessEqual(sink.stats.written, 3)
        self.assertGreaterEqual(sink.stats.dropped, 17)
        self.assertEqual(sink.stats.written + sink.stats.dropped, 20)

        inner.gate.set()
        self.assertTrue(sink.flush(timeout=5.0))

    def test_drop_oldest_keeps_the_stream_live(self):
        inner = BlockingSink()
        sink = out.AsyncSink(inner, max_queue=2, drop_oldest=True)
        sink.open(self.spec)
        self.addCleanup(sink.close)
        self.addCleanup(inner.gate.set)

        for index in range(10):
            sink.submit(out.Frame(self.payload, self.spec, index=index))
        self.assertGreater(sink.stats.dropped, 0)

        inner.gate.set()
        self.assertTrue(sink.flush(timeout=5.0))
        # Whatever survived includes the most recent frame.
        self.assertIn(9, inner.seen)

    def test_a_fast_sink_loses_nothing(self):
        inner = out.NullSink()
        sink = out.AsyncSink(inner, max_queue=64)
        sink.open(self.spec)
        self.addCleanup(sink.close)
        for index in range(50):
            sink.submit(out.Frame(self.payload, self.spec, index=index))
        self.assertTrue(sink.flush(timeout=5.0))
        self.assertEqual(sink.stats.dropped, 0)
        self.assertEqual(inner.stats.written, 50)

    def test_describe_reports_the_inner_sink(self):
        sink = out.AsyncSink(out.NullSink())
        sink.open(self.spec)
        self.addCleanup(sink.close)
        data = sink.describe()
        self.assertEqual(data["inner"]["sink"], "null")
        self.assertIn("pending", data)


class OutputPipelineTest(unittest.TestCase):
    def setUp(self):
        self.spec = out.FrameSpec(4, 2, out.PIXEL_RGBA8, 30.0)

    def test_fan_out(self):
        pipeline = out.OutputPipeline(fps=0.0)
        a = pipeline.add_sink(out.NullSink())
        b = pipeline.add_sink(out.NullSink())
        pipeline.open(self.spec)
        self.assertEqual(pipeline.submit(bytes(32)), 2)
        self.assertEqual(a.stats.written, 1)
        self.assertEqual(b.stats.written, 1)
        self.assertEqual(pipeline.frames_emitted, 1)

    def test_frame_indices_increase(self):
        pipeline = out.OutputPipeline(fps=0.0)
        seen = []
        pipeline.add_sink(out.CallbackSink(lambda frame: seen.append(frame.index)))
        pipeline.open(self.spec)
        for _ in range(3):
            pipeline.submit(bytes(32))
        self.assertEqual(seen, [0, 1, 2])

    def test_rate_limiting_is_asked_before_the_read_back(self):
        pipeline = out.OutputPipeline(fps=30.0)
        pipeline.add_sink(out.NullSink())
        pipeline.open(self.spec)
        self.assertTrue(pipeline.wants_frame(now=0.0))
        self.assertFalse(pipeline.wants_frame(now=0.001))
        self.assertEqual(pipeline.frames_rate_limited, 1)

    def test_a_pipeline_with_no_sinks_still_wants_frames(self):
        pipeline = out.OutputPipeline(fps=0.0)
        self.assertTrue(pipeline.wants_frame())
        self.assertEqual(pipeline.submit(bytes(32)), 0)

    def test_remove_sink_closes_it(self):
        pipeline = out.OutputPipeline(fps=0.0)
        sink = pipeline.add_sink(out.NullSink())
        pipeline.open(self.spec)
        self.assertTrue(sink.is_open)
        self.assertTrue(pipeline.remove_sink(sink))
        self.assertFalse(sink.is_open)
        self.assertFalse(pipeline.remove_sink(sink))

    def test_status_is_json_shaped(self):
        pipeline = out.OutputPipeline(fps=30.0)
        pipeline.add_sink(out.NullSink())
        pipeline.open(self.spec)
        status = pipeline.status()
        self.assertEqual(status["spec"]["width"], 4)
        self.assertEqual(len(status["sinks"]), 1)
        self.assertIn("dropped", status)


# ==========================================================================
# LIV
# ==========================================================================


class LivProbeTest(unittest.TestCase):
    def test_no_config_means_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            status = livmod.probe(
                (os.path.join(directory, "externalcamera.cfg"),),
                extensions=[],
                system="Windows",
            )
            self.assertFalse(status.available)
            self.assertEqual(status.mode, livmod.MODE_UNAVAILABLE)
            self.assertIn("externalcamera.cfg", status.summary())

    def test_a_config_enables_the_external_camera_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "externalcamera.cfg")
            ec.save(ec.default_config(), path)
            status = livmod.probe((path,), extensions=[], system="Windows")
            self.assertTrue(status.available)
            self.assertEqual(status.mode, livmod.MODE_EXTERNAL_CAMERA)
            self.assertEqual(status.config_path, path)

    def test_the_native_sdk_check_always_fails_and_says_why(self):
        status = livmod.probe((), extensions=[], system="Windows")
        native = status.check("native_sdk")
        self.assertFalse(native.ok)
        self.assertIn("DirectX 11", native.detail)

    def test_no_liv_openxr_extension_today(self):
        status = livmod.probe((), extensions=["XR_KHR_opengl_enable"], system="Linux")
        self.assertFalse(status.check("openxr_extension").ok)
        self.assertIn("187-196", status.check("openxr_extension").detail)

    def test_a_future_liv_extension_would_be_noticed(self):
        # The probe looks for the registered author-tag prefix, not for a name
        # we invented, so it starts working by itself if LIV ever ships one.
        status = livmod.probe(
            (), extensions=["XR_KHR_opengl_enable", "XR_LIV_extension_187"],
            system="Windows",
        )
        self.assertTrue(status.check("openxr_extension").ok)
        self.assertEqual(status.mode, livmod.MODE_NATIVE_SDK)
        self.assertTrue(status.available)

    def test_reserved_extension_names_match_the_registry(self):
        self.assertEqual(len(livmod.RESERVED_OPENXR_EXTENSIONS), 10)
        self.assertEqual(
            livmod.RESERVED_OPENXR_EXTENSIONS[0], "XR_LIV_extension_187"
        )
        self.assertEqual(
            livmod.RESERVED_OPENXR_EXTENSIONS[-1], "XR_LIV_extension_196"
        )
        for name in livmod.RESERVED_OPENXR_EXTENSIONS:
            self.assertTrue(name.startswith(livmod.LIV_OPENXR_EXTENSION_PREFIX))

    def test_platform_check(self):
        self.assertTrue(
            livmod.probe((), extensions=[], system="Windows").check("platform").ok
        )
        linux = livmod.probe((), extensions=[], system="Linux").check("platform")
        self.assertFalse(linux.ok)
        self.assertIn("Linux", linux.detail)

    def test_unknown_runtime_is_reported_as_unknown_not_absent(self):
        status = livmod.probe((), extensions=None, system="Linux")
        detail = status.check("openxr_extension").detail
        self.assertIn("could not be", detail)

    def test_report_lists_every_check(self):
        status = livmod.probe((), extensions=[], system="Linux")
        report = status.report()
        for check in status.checks:
            self.assertIn(check.name, report)
        self.assertIn("mode", status.as_dict())

    def test_liv_available_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(
                livmod.probe(
                    (os.path.join(directory, "nope.cfg"),), extensions=[]
                ).available
            )


class LivIntegrationTest(unittest.TestCase):
    def test_prepare_creates_a_starting_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "externalcamera.cfg")
            integration = livmod.LivIntegration((path,))
            config, written = integration.prepare()
            self.assertEqual(written, path)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(config, ec.default_config())
            self.assertTrue(integration.available)

    def test_prepare_uses_an_existing_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "externalcamera.cfg")
            ec.save(ec.parse("fov=88\nnear=0.2\nfar=40\n"), path)
            integration = livmod.LivIntegration((path,))
            config, found = integration.prepare()
            self.assertEqual(found, path)
            self.assertAlmostEqual(config.fov, 88.0)

    def test_prepare_can_refuse_to_create(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "externalcamera.cfg")
            integration = livmod.LivIntegration((path,))
            config, written = integration.prepare(create_missing=False)
            self.assertIsNone(written)
            self.assertFalse(os.path.exists(path))
            self.assertIsNotNone(config)

    def test_describe_is_json_shaped(self):
        with tempfile.TemporaryDirectory() as directory:
            integration = livmod.LivIntegration(
                (os.path.join(directory, "externalcamera.cfg"),)
            )
            data = integration.describe()
            self.assertIn("mode", data)
            self.assertIn("checks", data)


# ==========================================================================
# session
# ==========================================================================


def fixed_camera(position=(0.0, 1.5, 3.0)):
    return cam.MRCCamera(cam.FixedPose(pose_at(position)))


class ModeTransitionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "externalcamera.cfg")
        ec.save(ec.parse("fov=60\nnear=0.1\nfar=100\n"), self.path)
        self.session = sess.MRCSession(
            config_paths=(self.path,), camera=fixed_camera(), watch_interval=0.0
        )
        self.session.pipeline.limiter.fps = 0.0

    def test_starts_off(self):
        self.assertEqual(self.session.mode, sess.MODE_OFF)
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session.update(1 / 90, pose_at((0, 1.6, 0))))

    def test_start_and_stop(self):
        self.assertEqual(self.session.start(), sess.MODE_QUADRANT_MRC)
        self.assertTrue(self.session.active)
        self.assertTrue(self.session.pipeline.is_open or not self.session.pipeline.sinks)
        self.assertEqual(self.session.stop(), sess.MODE_OFF)
        self.assertFalse(self.session.active)

    def test_start_off_is_rejected(self):
        with self.assertRaises(ValueError):
            self.session.start(sess.MODE_OFF)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.session.set_mode("CINEMATIC")

    def test_setting_the_same_mode_is_a_no_op(self):
        self.session.start(sess.MODE_TPP)
        history = self.session.mode_history
        self.assertEqual(self.session.set_mode(sess.MODE_TPP), sess.MODE_TPP)
        self.assertEqual(self.session.mode_history, history)

    def test_every_transition_is_recorded(self):
        self.session.set_mode(sess.MODE_TPP)
        self.session.set_mode(sess.MODE_QUADRANT_MRC)
        self.session.set_mode(sess.MODE_LIV)
        self.session.set_mode(sess.MODE_OFF)
        self.assertEqual(
            self.session.mode_history,
            (sess.MODE_OFF, sess.MODE_TPP, sess.MODE_QUADRANT_MRC,
             sess.MODE_LIV, sess.MODE_OFF),
        )

    def test_direct_transitions_between_capture_modes(self):
        sink = self.session.add_sink(out.NullSink())
        self.session.set_mode(sess.MODE_QUADRANT_MRC)
        self.assertTrue(sink.is_open)
        self.session.set_mode(sess.MODE_TPP)
        # closed and reopened exactly once each
        self.assertTrue(sink.is_open)
        self.session.stop()
        self.assertFalse(sink.is_open)

    def test_toggle(self):
        self.assertEqual(self.session.toggle(), sess.MODE_QUADRANT_MRC)
        self.assertEqual(self.session.toggle(), sess.MODE_OFF)
        self.assertEqual(self.session.toggle(sess.MODE_LIV), sess.MODE_LIV)
        self.assertEqual(self.session.toggle(sess.MODE_QUADRANT_MRC),
                         sess.MODE_QUADRANT_MRC)

    def test_cycle_visits_every_mode_and_returns(self):
        seen = [self.session.mode]
        for _ in range(len(sess.MODES)):
            seen.append(self.session.cycle())
        self.assertEqual(seen[-1], sess.MODE_OFF)
        self.assertEqual(set(seen), set(sess.MODES))

    def test_liv_mode_guarantees_a_calibration(self):
        os.remove(self.path)
        session = sess.MRCSession(
            config_paths=(self.path,), camera=fixed_camera(), watch_interval=0.0
        )
        session.set_mode(sess.MODE_LIV)
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(session.liv.mode, livmod.MODE_EXTERNAL_CAMERA)
        session.stop()

    def test_tpp_mode_plans_a_single_full_frame_pass(self):
        self.session.set_mode(sess.MODE_TPP)
        plan = self.session.update(1 / 90, pose_at((0.0, 1.6, 0.0)), now=0.0)
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.passes), 1)
        self.assertEqual(
            plan.passes[0].rect.as_tuple(), (0, 0, self.session.width, self.session.height)
        )
        self.assertFalse(plan.passes[0].clipped)

    def test_quadrant_mode_plans_four_passes(self):
        self.session.set_mode(sess.MODE_QUADRANT_MRC)
        plan = self.session.update(1 / 90, pose_at((0.0, 1.6, 0.0)), now=0.0)
        self.assertEqual(len(plan.passes), 4)

    def test_update_returns_none_when_the_camera_has_no_pose(self):
        session = sess.MRCSession(
            config_paths=(self.path,),
            camera=cam.MRCCamera(cam.TrackedPose()),
            watch_interval=0.0,
        )
        session.pipeline.limiter.fps = 0.0
        session.start()
        self.assertIsNone(session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0))
        session.submit_tracker_pose(pose_at((0.0, 1.5, 3.0)))
        self.assertIsNotNone(session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0))

    def test_submit_tracker_pose_is_a_no_op_for_other_sources(self):
        self.assertFalse(self.session.submit_tracker_pose(pose_at((0, 0, 0))))

    def test_frame_skip_from_the_calibration(self):
        self.session.set_config(ec.parse("frameSkip=1\nfov=60\nnear=0.1\nfar=100\n"))
        self.session.set_mode(sess.MODE_QUADRANT_MRC)
        planned = 0
        for _ in range(10):
            if self.session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0):
                planned += 1
        self.assertEqual(planned, 5)

    def test_rate_limiting(self):
        self.session.set_fps(30.0)
        self.session.set_mode(sess.MODE_QUADRANT_MRC)
        planned = 0
        for frame in range(90):
            if self.session.update(1 / 90, pose_at((0, 1.6, 0)), now=frame / 90.0):
                planned += 1
        self.assertEqual(planned, 30)

    def test_set_resolution_reopens_the_pipeline(self):
        sink = self.session.add_sink(out.NullSink())
        self.session.start()
        self.session.set_resolution(1280, 720)
        self.assertEqual(self.session.compositor.layout.quadrant_width, 640)
        self.assertTrue(sink.is_open)
        self.assertEqual(sink.spec.width, 1280)

    def test_render_without_a_renderer(self):
        self.assertFalse(self.session.render(None))
        self.session.start()
        plan = self.session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0)
        self.assertFalse(self.session.render(plan))

    def test_render_with_a_renderer(self):
        class FakeRenderer:
            def __init__(self):
                self.seen = []

            def render(self, plan):
                self.seen.append(plan)
                return True

        renderer = self.session.attach_renderer(FakeRenderer())
        self.session.start()
        plan = self.session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0)
        self.assertTrue(self.session.render(plan))
        self.assertEqual(len(renderer.seen), 1)

    def test_submit_frame_reaches_the_sinks(self):
        sink = self.session.add_sink(out.NullSink())
        self.session.start()
        self.assertEqual(self.session.submit_frame(bytes(4)), 1)
        self.assertEqual(sink.stats.written, 1)

    def test_status_and_summary(self):
        self.session.start()
        status = self.session.status()
        self.assertEqual(status["mode"], sess.MODE_QUADRANT_MRC)
        self.assertEqual(status["resolution"], [1920, 1080])
        self.assertIn("camera", status)
        self.assertIn("watcher", status)
        self.assertIn("output", status)
        self.assertIn("MRC", self.session.summary())
        self.session.stop()
        self.assertEqual(self.session.summary(), "MRC: off")

    def test_update_never_raises(self):
        class ExplodingCamera:
            lens = cam.LensSettings()
            source = cam.FixedPose()

            def update(self, dt, context):
                raise RuntimeError("boom")

            def describe(self):
                return {}

        self.session.camera = ExplodingCamera()
        self.session.start()
        self.assertIsNone(self.session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0))
        self.assertIn("boom", self.session.last_error)


class HotReloadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "externalcamera.cfg")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def test_watcher_loads_then_reports_no_change(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=0.0)
        first = watcher.load()
        self.assertIsNotNone(first)
        self.assertIsNone(watcher.load())
        self.assertEqual(watcher.reload_count, 1)

    def test_watcher_picks_up_a_changed_file(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=0.0)
        watcher.load()
        self.write("fov=100\nnear=0.05\nfar=250\nhmdOffset=0.4\n")
        reloaded = watcher.poll(now=1.0)
        self.assertIsNotNone(reloaded)
        self.assertAlmostEqual(reloaded.fov, 100.0)
        self.assertAlmostEqual(reloaded.hmd_offset, 0.4)
        self.assertEqual(watcher.reload_count, 2)
        self.assertIsNone(watcher.last_error)

    def test_watcher_ignores_a_malformed_file_and_keeps_the_old_one(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=0.0)
        good = watcher.load()
        self.write("near=50\nfar=1\nfov=nonsense\n")  # inverted range + bad float
        self.assertIsNone(watcher.poll(now=1.0))
        self.assertIs(watcher.config, good)
        self.assertEqual(watcher.error_count, 1)
        self.assertIsNotNone(watcher.last_error)
        # and it recovers when the file is fixed
        self.write("fov=75\nnear=0.2\nfar=90\n")
        recovered = watcher.poll(now=2.0)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered.fov, 75.0)
        self.assertIsNone(watcher.last_error)

    def test_watcher_survives_a_truncated_file(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=0.0)
        watcher.load()
        self.write("")  # a calibration tool mid-rewrite
        self.assertIsNone(watcher.poll(now=1.0))
        self.assertIsNotNone(watcher.config)

    def test_watcher_survives_a_deleted_file(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=0.0)
        config = watcher.load()
        os.remove(self.path)
        self.assertIsNone(watcher.poll(now=1.0))
        self.assertIs(watcher.config, config)
        self.assertFalse(watcher.exists)

    def test_watcher_with_no_path(self):
        watcher = sess.ConfigWatcher(None)
        self.assertIsNone(watcher.load())
        self.assertFalse(watcher.exists)
        self.assertIsNone(watcher.status()["path"])

    def test_watcher_is_rate_limited(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        watcher = sess.ConfigWatcher(self.path, interval=10.0)
        watcher.poll(now=0.0)
        self.write("fov=120\nnear=0.1\nfar=100\n")
        self.assertIsNone(watcher.poll(now=1.0))   # too soon
        self.assertIsNotNone(watcher.poll(now=11.0))

    def test_session_hot_reloads_during_update(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        session = sess.MRCSession(
            config_paths=(self.path,), camera=fixed_camera(), watch_interval=0.0
        )
        session.pipeline.limiter.fps = 0.0
        session.start()
        plan = session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0)
        self.assertAlmostEqual(plan.vfov_deg, 60.0)

        self.write("fov=95\nnear=0.05\nfar=60\nhmdOffset=0.5\n")
        plan = session.update(1 / 90, pose_at((0, 1.6, 0)), now=1.0)
        self.assertAlmostEqual(plan.vfov_deg, 95.0)
        self.assertAlmostEqual(plan.near, 0.05)
        self.assertAlmostEqual(plan.far, 60.0)
        session.stop()

    def test_session_survives_a_malformed_reload(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        session = sess.MRCSession(
            config_paths=(self.path,), camera=fixed_camera(), watch_interval=0.0
        )
        session.pipeline.limiter.fps = 0.0
        session.start()
        session.update(1 / 90, pose_at((0, 1.6, 0)), now=0.0)

        self.write("near=oops\nfar=also-oops\nfov=-4\n")
        plan = session.update(1 / 90, pose_at((0, 1.6, 0)), now=1.0)
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan.vfov_deg, 60.0)  # the good one is still in force
        self.assertGreater(session.watcher.error_count, 0)
        self.assertIsNotNone(session.status()["watcher"]["last_error"])
        session.stop()

    def test_explicit_reload(self):
        self.write("fov=60\nnear=0.1\nfar=100\n")
        session = sess.MRCSession(config_paths=(self.path,), camera=fixed_camera())
        self.write("fov=33\nnear=0.1\nfar=100\n")
        self.assertIsNotNone(session.reload_config())
        self.assertAlmostEqual(session.config.fov, 33.0)


class PackageSurfaceTest(unittest.TestCase):
    def test_public_api_is_importable(self):
        import xrmrc

        for name in xrmrc.__all__:
            self.assertTrue(hasattr(xrmrc, name), name)

    def test_no_gui_modules_are_imported_at_module_scope(self):
        import sys

        for module in ("FreeCAD", "FreeCADGui", "pivy.coin", "PySide6"):
            self.assertNotIn(module, sys.modules, f"{module} leaked into the import")


if __name__ == "__main__":
    unittest.main()
