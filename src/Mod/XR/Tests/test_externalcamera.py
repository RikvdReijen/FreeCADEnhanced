# SPDX-License-Identifier: LGPL-2.1-or-later
"""``externalcamera.cfg`` parsing, writing, and the geometry derived from it.

The format has no specification — the reference implementation is Valve's
``SteamVR_ExternalCamera.cs`` — so these tests pin down the behaviour that
implementation actually has, including the parts that are surprising: the
Unity-frame euler fields, the right-handed ``m`` matrix, and the fact that a
malformed line is skipped rather than fatal.

No FreeCAD, no Qt, no GPU.
"""

import math
import os
import random
import tempfile
import unittest

from xrmrc import camera as cameramod
from xrmrc import externalcamera as ec


# --------------------------------------------------------------------------
# real-world samples
# --------------------------------------------------------------------------

# The sample quoted all over the SteamVR mixed-reality threads.
SAMPLE_STEAMVR = """\
x=0.001057133
y=0.1042561
z=-0.0736331
rx=272.3893
ry=286.1903
rz=254.3431
fov=62.26534
near=0.1
far=1000
smaa=2
r=255
g=0
b=255
"""

# The other common shape: a calibrated matrix, commented out, plus a scale.
SAMPLE_WITH_COMMENTED_MATRIX = """\
x=0
y=0
z=0
rx=0
ry=0
rz=0
fov=60
near=0.1
far=100
//m=-0.999059,0.015577,-0.040472,-0.0127,-0.016016,-0.999816,0.010544,0.1799,-0.040301,0.011183,0.999125,-0.0846
sceneResolutionScale=0.5
"""

SAMPLE_LIVE_MATRIX = """\
# calibrated on a wet Tuesday
fov=54
near=0.05
far=250

m=1,0,0,0.5,0,1,0,1.2,0,0,1,-2.5
hmdOffset=0.25
nearOffset=-0.1
"""


class ParseSamplesTest(unittest.TestCase):
    def test_steamvr_sample(self):
        config = ec.parse(SAMPLE_STEAMVR)
        self.assertAlmostEqual(config.x, 0.001057133)
        self.assertAlmostEqual(config.y, 0.1042561)
        self.assertAlmostEqual(config.z, -0.0736331)
        self.assertAlmostEqual(config.rx, 272.3893)
        self.assertAlmostEqual(config.ry, 286.1903)
        self.assertAlmostEqual(config.rz, 254.3431)
        self.assertAlmostEqual(config.fov, 62.26534)
        self.assertAlmostEqual(config.near, 0.1)
        self.assertAlmostEqual(config.far, 1000.0)

    def test_unknown_keys_are_kept_not_dropped(self):
        config = ec.parse(SAMPLE_STEAMVR)
        # 'smaa' is not in the reference Config struct at all.
        self.assertEqual(config.unknown, {"smaa": "2"})
        self.assertIn("smaa=2", ec.dumps(config))

    def test_out_of_range_chroma_key_parses_but_warns(self):
        config = ec.parse(SAMPLE_STEAMVR)
        self.assertEqual(config.r, 255.0)
        self.assertEqual(config.b, 255.0)
        warned = {issue.field for issue in config.validate()}
        self.assertIn("r", warned)
        self.assertIn("b", warned)
        self.assertNotIn("g", warned)  # g=0 is in range
        # and nothing here is fatal
        self.assertFalse([i for i in config.validate() if i.is_error])
        # sanitised() brings them back into range
        self.assertEqual(config.sanitised().r, 1.0)

    def test_commented_matrix_is_not_applied(self):
        config = ec.parse(SAMPLE_WITH_COMMENTED_MATRIX)
        self.assertIsNone(config.matrix)
        self.assertEqual(config.unknown, {})
        self.assertAlmostEqual(config.scene_resolution_scale, 0.5)
        self.assertEqual(config.pose().position, (0.0, 0.0, 0.0))

    def test_live_matrix_overrides_euler_fields(self):
        config = ec.parse(SAMPLE_LIVE_MATRIX)
        self.assertIsNotNone(config.matrix)
        # The reference implementation folds the matrix into x/y/z + rx/ry/rz.
        # Unity's frame flips Z, so 'm' translation -2.5 becomes z = +2.5.
        self.assertAlmostEqual(config.x, 0.5)
        self.assertAlmostEqual(config.y, 1.2)
        self.assertAlmostEqual(config.z, 2.5)
        self.assertAlmostEqual(config.hmd_offset, 0.25)
        self.assertAlmostEqual(config.near_offset, -0.1)
        # ... and the OpenXR pose comes straight off the (right handed) matrix.
        pose = config.pose()
        self.assertAlmostEqual(pose.position[0], 0.5)
        self.assertAlmostEqual(pose.position[1], 1.2)
        self.assertAlmostEqual(pose.position[2], -2.5)


class ParseToleranceTest(unittest.TestCase):
    def test_crlf_line_endings(self):
        config = ec.parse(SAMPLE_STEAMVR.replace("\n", "\r\n"))
        self.assertAlmostEqual(config.fov, 62.26534)
        self.assertEqual(config.unknown, {"smaa": "2"})

    def test_lone_cr_line_endings(self):
        config = ec.parse("fov=45\rnear=0.2\rfar=50\r")
        self.assertAlmostEqual(config.fov, 45.0)
        self.assertAlmostEqual(config.near, 0.2)
        self.assertAlmostEqual(config.far, 50.0)

    def test_utf8_bom_and_bytes(self):
        raw = ("﻿fov=33\nnear=0.3\nfar=30\n").encode("utf-8")
        config = ec.parse(raw)
        self.assertAlmostEqual(config.fov, 33.0)

    def test_blank_lines_and_every_comment_marker(self):
        config = ec.parse(
            "\n\n  \n// a comment\n# another\n; and another\nfov=70\n\n"
        )
        self.assertAlmostEqual(config.fov, 70.0)
        self.assertEqual(config.unknown, {})

    def test_whitespace_around_keys_and_values(self):
        config = ec.parse("  fov  =  70.5  \n\tnear\t=\t0.15\t\n")
        self.assertAlmostEqual(config.fov, 70.5)
        self.assertAlmostEqual(config.near, 0.15)

    def test_trailing_inline_comment(self):
        config = ec.parse("fov=70 // vertical, measured\nnear=0.2 # close\n")
        self.assertAlmostEqual(config.fov, 70.0)
        self.assertAlmostEqual(config.near, 0.2)

    def test_case_insensitive_key_fallback(self):
        config = ec.parse("FOV=80\nsceneresolutionscale=0.75\nHMDOFFSET=0.4\n")
        self.assertAlmostEqual(config.fov, 80.0)
        self.assertAlmostEqual(config.scene_resolution_scale, 0.75)
        self.assertAlmostEqual(config.hmd_offset, 0.4)

    def test_boolean_spellings(self):
        for text, expected in (
            ("disableStandardAssets=true", True),
            ("disableStandardAssets=True", True),
            ("disableStandardAssets=1", True),
            ("disableStandardAssets=false", False),
            ("disableStandardAssets=0", False),
            ("disableStandardAssets=no", False),
        ):
            with self.subTest(text=text):
                self.assertIs(ec.parse(text).disable_standard_assets, expected)

    def test_unparsable_value_is_recorded_not_fatal(self):
        config = ec.parse("fov=wide\nnear=0.1\nfar=100\n")
        self.assertAlmostEqual(config.fov, 60.0)  # kept the default
        self.assertTrue(any(issue.field == "fov" for issue in config.errors))
        self.assertTrue(any(issue.is_error for issue in config.validate()))

    def test_strict_mode_raises(self):
        with self.assertRaises(ec.ExternalCameraError):
            ec.parse("fov=wide\n", strict=True)

    def test_line_with_no_separator_is_skipped(self):
        config = ec.parse("this is not a setting\nfov=42\n")
        self.assertAlmostEqual(config.fov, 42.0)
        self.assertTrue(config.errors)

    def test_non_finite_values_are_rejected(self):
        config = ec.parse("near=nan\nfar=inf\n")
        self.assertAlmostEqual(config.near, 0.1)
        self.assertAlmostEqual(config.far, 100.0)
        self.assertEqual(len(config.errors), 2)

    def test_short_matrix_is_ignored(self):
        config = ec.parse("m=1,0,0,0,1,0\nfov=50\n")
        self.assertIsNone(config.matrix)
        self.assertAlmostEqual(config.fov, 50.0)
        self.assertTrue(any(issue.field == "m" for issue in config.errors))

    def test_empty_and_none_input(self):
        self.assertEqual(ec.parse(""), ec.default_config())
        self.assertEqual(ec.parse(None), ec.default_config())


class OutOfRangeTest(unittest.TestCase):
    def test_inverted_clip_range_is_an_error(self):
        issues = ec.parse("near=10\nfar=1\n").validate()
        self.assertTrue(issues[0].is_error)
        self.assertEqual(issues[0].field, "far")

    def test_zero_near_is_an_error(self):
        issues = ec.parse("near=0\n").validate()
        self.assertTrue(any(i.is_error and i.field == "near" for i in issues))

    def test_absurd_fov_is_an_error(self):
        for value in ("0", "-30", "180", "900"):
            with self.subTest(fov=value):
                issues = ec.parse(f"fov={value}\n").validate()
                self.assertTrue(any(i.is_error and i.field == "fov" for i in issues))

    def test_warnings_do_not_block(self):
        config = ec.parse("fov=170\nnear=0.001\nfar=50000\nhmdOffset=40\n")
        issues = config.validate()
        self.assertTrue(issues)
        self.assertFalse([i for i in issues if i.is_error])

    def test_negative_frame_skip_warns_and_is_clamped(self):
        config = ec.parse("frameSkip=-3\n")
        self.assertTrue(any(i.field == "frameSkip" for i in config.validate()))
        self.assertEqual(config.frame_divisor, 1)

    def test_frame_divisor(self):
        self.assertEqual(ec.parse("frameSkip=0\n").frame_divisor, 1)
        self.assertEqual(ec.parse("frameSkip=1\n").frame_divisor, 2)
        self.assertEqual(ec.parse("frameSkip=2.9\n").frame_divisor, 3)

    def test_sanitised_is_always_renderable(self):
        config = ec.parse("near=-1\nfar=-5\nfov=400\nr=9\na=-2\n").sanitised()
        self.assertGreater(config.near, 0.0)
        self.assertGreater(config.far, config.near)
        self.assertLess(config.fov, 180.0)
        self.assertEqual(config.r, 1.0)
        self.assertEqual(config.a, 0.0)
        self.assertFalse([i for i in config.validate() if i.is_error])


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------


class RoundTripTest(unittest.TestCase):
    def test_text_round_trip(self):
        for sample in (SAMPLE_STEAMVR, SAMPLE_WITH_COMMENTED_MATRIX, SAMPLE_LIVE_MATRIX):
            with self.subTest(sample=sample.splitlines()[0]):
                config = ec.parse(sample)
                self.assertEqual(ec.parse(ec.dumps(config)), config)

    def test_round_trip_is_bit_exact_for_random_calibrations(self):
        random.seed(20260903)
        for _ in range(200):
            config = ec.ExternalCameraConfig()
            config.x = random.uniform(-2, 2)
            config.y = random.uniform(-2, 2)
            config.z = random.uniform(-2, 2)
            config.rx = random.uniform(0, 360)
            config.ry = random.uniform(0, 360)
            config.rz = random.uniform(0, 360)
            config.fov = random.uniform(20, 140)
            config.near = random.uniform(0.01, 0.5)
            config.far = random.uniform(10, 500)
            config.hmd_offset = random.uniform(-1, 1)
            config.near_offset = random.uniform(-1, 1)
            self.assertEqual(ec.parse(ec.dumps(config)), config)

    def test_matrix_round_trip_is_bit_exact(self):
        random.seed(4242)
        for _ in range(200):
            axis = tuple(random.uniform(-1, 1) for _ in range(3))
            if cameramod.v_length(axis) < 1e-6:
                continue
            pose = cameramod.Pose(
                tuple(random.uniform(-3, 3) for _ in range(3)),
                cameramod.q_from_axis_angle(axis, random.uniform(-math.pi, math.pi)),
            )
            config = ec.ExternalCameraConfig()
            config.set_pose(pose, use_matrix=True)
            self.assertEqual(ec.parse(ec.dumps(config)), config)
            self.assertTrue(ec.parse(ec.dumps(config)).pose().approx_equal(pose))

    def test_save_and_load(self):
        config = ec.parse(SAMPLE_STEAMVR)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested", ec.CONFIG_FILENAME)
            ec.save(config, path)
            self.assertTrue(os.path.isfile(path))
            # written atomically: no temporary left behind
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertEqual(ec.load(path), config)

    def test_dumps_without_header(self):
        text = ec.dumps(ec.default_config(), header=False)
        self.assertFalse(text.startswith("//"))
        self.assertEqual(ec.parse(text), ec.default_config())


# --------------------------------------------------------------------------
# derived pose
# --------------------------------------------------------------------------


class PoseTest(unittest.TestCase):
    def test_translation_flips_z_into_the_openxr_frame(self):
        config = ec.parse("x=1\ny=2\nz=3\n")
        self.assertEqual(config.pose().position, (1.0, 2.0, -3.0))

    def test_identity_rotation_looks_along_minus_z(self):
        forward = ec.parse("").pose().forward()
        for got, want in zip(forward, (0.0, 0.0, -1.0)):
            self.assertAlmostEqual(got, want)

    def test_unity_yaw_of_90_turns_the_camera_to_plus_x(self):
        # Unity is left handed: +90 about Y takes its +Z forward to +X.
        # In OpenXR the camera looks along -Z, and the same shot must end up
        # looking along +X.
        forward = ec.parse("ry=90\n").pose().forward()
        for got, want in zip(forward, (1.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_unity_pitch_of_90_looks_down(self):
        forward = ec.parse("rx=90\n").pose().forward()
        for got, want in zip(forward, (0.0, -1.0, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_unity_roll_of_90_rolls_the_up_vector(self):
        # +90 about Z in Unity rolls the camera; up goes to -X in both frames.
        up = ec.parse("rz=90\n").pose().up()
        for got, want in zip(up, (-1.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_matrix_and_euler_routes_agree(self):
        """The two ways of stating a pose must land in the same place.

        SteamVR reads ``m`` and immediately rewrites ``x/y/z/rx/ry/rz`` from it,
        so the two representations can never disagree there.  Ours must not
        either, and the reflection between the frames is the whole reason it is
        easy to get wrong.
        """
        random.seed(1234)
        worst = 0.0
        for _ in range(2000):
            axis = tuple(random.uniform(-1, 1) for _ in range(3))
            if cameramod.v_length(axis) < 1e-6:
                continue
            original = cameramod.Pose(
                tuple(random.uniform(-4, 4) for _ in range(3)),
                cameramod.q_from_axis_angle(axis, random.uniform(-math.pi, math.pi)),
            )
            with_matrix = ec.ExternalCameraConfig()
            with_matrix.matrix = cameramod.pose_to_matrix34(original)
            with_matrix.apply_matrix()

            euler_only = ec.ExternalCameraConfig(
                x=with_matrix.x, y=with_matrix.y, z=with_matrix.z,
                rx=with_matrix.rx, ry=with_matrix.ry, rz=with_matrix.rz,
            )
            through_matrix = ec.pose(with_matrix)
            through_euler = ec.pose(euler_only)

            self.assertTrue(through_matrix.approx_equal(original, 1e-9))
            self.assertTrue(through_euler.approx_equal(original, 1e-6))
            worst = max(
                worst,
                cameramod.v_length(
                    cameramod.v_sub(through_matrix.position, through_euler.position)
                ),
            )
        self.assertLess(worst, 1e-9)

    def test_matrix_translation_is_taken_verbatim(self):
        # m3, m7, m11 are the translation of an HmdMatrix34_t, and it is
        # already right handed, so nothing is flipped on the way in.
        config = ec.parse("m=1,0,0,1.5,0,1,0,2.5,0,0,1,3.5\n")
        self.assertEqual(config.pose().position, (1.5, 2.5, 3.5))

    def test_set_pose_round_trips_through_euler(self):
        pose = cameramod.Pose((0.4, 1.1, -2.2), cameramod.q_from_axis_angle((0, 1, 0), 0.7))
        config = ec.ExternalCameraConfig()
        config.set_pose(pose)
        self.assertIsNone(config.matrix)
        self.assertTrue(config.pose().approx_equal(pose))

    def test_unity_pose_is_the_file_s_own_frame(self):
        config = ec.parse("x=1\ny=2\nz=3\nry=45\n")
        self.assertEqual(config.unity_pose().position, (1.0, 2.0, 3.0))
        self.assertEqual(
            tuple(round(v, 6) for v in
                  cameramod.quat_to_unity_euler(config.unity_pose().orientation)),
            (0.0, 45.0, 0.0),
        )


# --------------------------------------------------------------------------
# derived projection
# --------------------------------------------------------------------------


class ProjectionTest(unittest.TestCase):
    def test_hand_computed_matrix(self):
        # fov 90 vertical, square aspect, near 1, far 3:
        #   f              = 1 / tan(45 deg)         = 1
        #   m[2][2]        = (far + near)/(near - far) = 4 / -2 = -2
        #   m[2][3]        = 2*far*near/(near - far)   = 6 / -2 = -3
        config = ec.parse("fov=90\nnear=1\nfar=3\n")
        matrix = config.projection(1.0)
        expected = (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, -2.0, -3.0),
            (0.0, 0.0, -1.0, 0.0),
        )
        for row_got, row_want in zip(matrix, expected):
            for got, want in zip(row_got, row_want):
                self.assertAlmostEqual(got, want)

    def test_aspect_only_scales_the_x_row(self):
        config = ec.parse("fov=90\nnear=1\nfar=3\n")
        square = config.projection(1.0)
        wide = config.projection(16.0 / 9.0)
        self.assertAlmostEqual(wide[0][0], square[0][0] * 9.0 / 16.0)
        for row in range(1, 4):
            self.assertEqual(wide[row], square[row])

    def test_fov_is_vertical(self):
        # A point exactly on the top edge of the frustum at z = -near must land
        # on the top of the clip volume: y_clip / w_clip == 1.
        config = ec.parse("fov=60\nnear=0.5\nfar=100\n")
        matrix = config.projection(2.0)
        near = 0.5
        top = near * math.tan(math.radians(60.0) / 2.0)
        point = (0.0, top, -near, 1.0)
        y = sum(matrix[1][i] * point[i] for i in range(4))
        w = sum(matrix[3][i] * point[i] for i in range(4))
        self.assertAlmostEqual(y / w, 1.0)

    def test_near_and_far_map_to_the_clip_range(self):
        config = ec.parse("fov=70\nnear=0.2\nfar=40\n")
        matrix = config.projection(1.5)
        for distance, expected in ((0.2, -1.0), (40.0, 1.0)):
            point = (0.0, 0.0, -distance, 1.0)
            z = sum(matrix[2][i] * point[i] for i in range(4))
            w = sum(matrix[3][i] * point[i] for i in range(4))
            self.assertAlmostEqual(z / w, expected)

    def test_unusable_clip_range_raises(self):
        with self.assertRaises(ValueError):
            ec.parse("near=5\nfar=1\n").projection(1.0)
        with self.assertRaises(ValueError):
            ec.parse("").projection(0.0)


# --------------------------------------------------------------------------
# locating the file
# --------------------------------------------------------------------------


class LocationTest(unittest.TestCase):
    def test_default_paths_include_the_working_directory(self):
        paths = ec.default_paths()
        self.assertIn(
            os.path.abspath(os.path.join(os.getcwd(), ec.CONFIG_FILENAME)), paths
        )

    def test_extra_paths_come_first(self):
        paths = ec.default_paths(("/somewhere/externalcamera.cfg",))
        self.assertEqual(paths[0], os.path.abspath("/somewhere/externalcamera.cfg"))

    def test_environment_override_wins(self):
        previous = os.environ.get("FREECAD_XR_EXTERNALCAMERA")
        os.environ["FREECAD_XR_EXTERNALCAMERA"] = "/env/externalcamera.cfg"
        try:
            self.assertEqual(
                ec.default_paths(("/extra/externalcamera.cfg",))[0],
                os.path.abspath("/env/externalcamera.cfg"),
            )
        finally:
            if previous is None:
                del os.environ["FREECAD_XR_EXTERNALCAMERA"]
            else:
                os.environ["FREECAD_XR_EXTERNALCAMERA"] = previous

    def test_duplicates_are_collapsed(self):
        same = os.path.join(os.getcwd(), ec.CONFIG_FILENAME)
        paths = ec.default_paths((same,))
        self.assertEqual(len(paths), len(set(paths)))

    def test_find_config_returns_the_first_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "missing.cfg")
            present = os.path.join(directory, ec.CONFIG_FILENAME)
            ec.save(ec.default_config(), present)
            self.assertEqual(ec.find_config((missing, present)), present)
            self.assertNotEqual(ec.find_config((missing,)), missing)


if __name__ == "__main__":
    unittest.main()
