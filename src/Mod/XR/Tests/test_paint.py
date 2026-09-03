# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Unit tests for the raster/brush/layer/texture/3D-stroke side of xrpaint.

Runs under plain ``python3 -m unittest`` from ``src/Mod/XR`` with neither
FreeCAD nor numpy installed; when numpy *is* importable the accelerated code
path is exercised as well and required to be byte identical.
"""

import math
import os
import random
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrpaint import brush, layers, raster, stroke3d, texture_paint, ui  # noqa: E402
from xrpaint.raster import Image, Mask  # noqa: E402
from xrpaint.session import PaintSession  # noqa: E402
from xrpaint.texture_paint import PaintHit  # noqa: E402

HAVE_NUMPY = raster.have_numpy()


class _ScalarPath(object):
    """Context manager forcing the pure Python raster path."""

    def __enter__(self):
        self._old = raster.set_use_numpy(False)
        return self

    def __exit__(self, *exc):
        raster.set_use_numpy(self._old)
        return False


# ==========================================================================
# PNG codec
# ==========================================================================

class TestPngCodec(unittest.TestCase):

    def _random_image(self, w, h, seed=0):
        rng = random.Random(seed)
        img = Image(w, h)
        for i in range(len(img.data)):
            img.data[i] = rng.randrange(256)
        return img

    def test_roundtrip_rgba(self):
        img = self._random_image(37, 23, seed=7)
        blob = raster.encode_png(img)
        self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"))
        back = raster.decode_png(blob)
        self.assertEqual(back.size, (37, 23))
        self.assertEqual(back.data, img.data)

    def test_roundtrip_non_power_of_two_sizes(self):
        for w, h in ((1, 1), (1, 17), (17, 1), (3, 5), (64, 63), (129, 7)):
            img = self._random_image(w, h, seed=w * 100 + h)
            back = raster.decode_png(raster.encode_png(img))
            self.assertEqual((back.width, back.height), (w, h))
            self.assertEqual(back.data, img.data, "%dx%d" % (w, h))

    def test_roundtrip_every_filter_type(self):
        img = self._random_image(29, 11, seed=3)
        for ft in (0, 1, 2, 3, 4):
            blob = raster.encode_png(img, filter_type=ft)
            back = raster.decode_png(blob)
            self.assertEqual(back.data, img.data, "filter %d" % ft)

    def test_adaptive_filtering_is_not_larger_than_none(self):
        img = Image(64, 64, (10, 20, 30, 255))
        adaptive = raster.encode_png(img)
        none = raster.encode_png(img, filter_type=0)
        self.assertLessEqual(len(adaptive), len(none) + 64)

    def test_decode_rejects_corrupt_crc(self):
        img = Image(4, 4, (1, 2, 3, 255))
        blob = bytearray(raster.encode_png(img))
        blob[-5] ^= 0xFF                       # damage the IEND CRC region
        with self.assertRaises(ValueError):
            raster.decode_png(bytes(blob))

    def test_decode_rejects_non_png(self):
        with self.assertRaises(ValueError):
            raster.decode_png(b"not a png at all")

    def _make_png(self, width, height, depth, ctype, rows, extra=()):
        import struct
        out = bytearray(b"\x89PNG\r\n\x1a\n")

        def chunk(tag, payload):
            return (struct.pack(">I", len(payload)) + tag + payload
                    + struct.pack(">I", zlib.crc32(tag + payload)
                                  & 0xFFFFFFFF))

        out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth,
                                          ctype, 0, 0, 0))
        for tag, payload in extra:
            out += chunk(tag, payload)
        raw = bytearray()
        for row in rows:
            raw.append(0)
            raw.extend(row)
        out += chunk(b"IDAT", zlib.compress(bytes(raw)))
        out += chunk(b"IEND", b"")
        return bytes(out)

    def test_decode_greyscale(self):
        blob = self._make_png(2, 2, 8, 0, [[0, 128], [255, 64]])
        img = raster.decode_png(blob)
        self.assertEqual(img.get_pixel(0, 0), (0, 0, 0, 255))
        self.assertEqual(img.get_pixel(1, 0), (128, 128, 128, 255))
        self.assertEqual(img.get_pixel(1, 1), (64, 64, 64, 255))

    def test_decode_rgb(self):
        blob = self._make_png(2, 1, 8, 2, [[1, 2, 3, 4, 5, 6]])
        img = raster.decode_png(blob)
        self.assertEqual(img.get_pixel(0, 0), (1, 2, 3, 255))
        self.assertEqual(img.get_pixel(1, 0), (4, 5, 6, 255))

    def test_decode_palette_with_transparency(self):
        plte = bytes([255, 0, 0, 0, 255, 0])
        trns = bytes([0, 255])
        blob = self._make_png(2, 1, 8, 3, [[0, 1]],
                              extra=[(b"PLTE", plte), (b"tRNS", trns)])
        img = raster.decode_png(blob)
        self.assertEqual(img.get_pixel(0, 0), (255, 0, 0, 0))
        self.assertEqual(img.get_pixel(1, 0), (0, 255, 0, 255))

    def test_decode_grey_alpha(self):
        blob = self._make_png(2, 1, 8, 4, [[10, 20, 30, 40]])
        img = raster.decode_png(blob)
        self.assertEqual(img.get_pixel(0, 0), (10, 10, 10, 20))
        self.assertEqual(img.get_pixel(1, 0), (30, 30, 30, 40))

    def test_decode_rejects_interlaced(self):
        import struct
        blob = bytearray(b"\x89PNG\r\n\x1a\n")
        payload = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 1)
        blob += (struct.pack(">I", len(payload)) + b"IHDR" + payload
                 + struct.pack(">I", zlib.crc32(b"IHDR" + payload)
                               & 0xFFFFFFFF))
        with self.assertRaises(ValueError):
            raster.decode_png(bytes(blob))


# ==========================================================================
# blending
# ==========================================================================

class TestBlendModes(unittest.TestCase):
    """Expected values here are worked out by hand from the documented model:

    ``a = src_a/255 * coverage``, ``inv = dst_a/255 * (1 - a)``,
    ``out_a = a + inv``, ``out_c = (blend(src, dst) * a + dst * inv) / out_a``.
    """

    def _blit(self, dst_color, src_color, coverage, mode):
        """Blit one pixel with full mask coverage and a float ``coverage``.

        The mask is 8 bit, so an exact 0.5 has to come through the float
        opacity argument rather than through the mask value.
        """
        img = Image(1, 1, dst_color)
        m = Mask(1, 1, b"\xff")
        raster.blit_mask(img, m, 0, 0, src_color, mode, coverage)
        return img.get_pixel(0, 0)

    def test_normal_full_coverage_replaces(self):
        self.assertEqual(
            self._blit((10, 20, 30, 255), (200, 100, 50, 255), 1.0, "normal"),
            (200, 100, 50, 255))

    def test_normal_half_coverage(self):
        # a = 0.5, dst opaque -> inv = 0.5, out_a = 1
        # r = 255*0.5 + 0*0.5 = 127.5 -> 128
        # b = 0*0.5 + 255*0.5 = 127.5 -> 128
        self.assertEqual(
            self._blit((0, 0, 255, 255), (255, 0, 0, 255), 0.5, "normal"),
            (128, 0, 128, 255))

    def test_normal_over_transparent_keeps_source(self):
        self.assertEqual(
            self._blit((0, 0, 0, 0), (12, 34, 56, 255), 1.0, "normal"),
            (12, 34, 56, 255))

    def test_multiply(self):
        # 128*200/255 = 100.39 -> 100 ; 128*100/255 = 50.196 -> 50
        # 128*50/255  =  25.098 -> 25
        self.assertEqual(
            self._blit((200, 100, 50, 255), (128, 128, 128, 255), 1.0,
                       "multiply"),
            (100, 50, 25, 255))

    def test_screen(self):
        # 255 - (255-128)*(255-d)/255
        # d=200 -> 227.61 -> 228 ; d=100 -> 177.80 -> 178 ; d=50 -> 152.90 ->153
        self.assertEqual(
            self._blit((200, 100, 50, 255), (128, 128, 128, 255), 1.0,
                       "screen"),
            (228, 178, 153, 255))

    def test_add_saturates(self):
        self.assertEqual(
            self._blit((200, 100, 250, 255), (50, 60, 70, 255), 1.0, "add"),
            (250, 160, 255, 255))

    def test_erase_only_touches_alpha(self):
        # out_a = 1 * (1 - 0.5) = 0.5 -> 127.5 -> 128
        self.assertEqual(
            self._blit((255, 0, 0, 255), (0, 0, 0, 255), 0.5, "erase"),
            (255, 0, 0, 128))

    def test_erase_with_full_coverage_clears_alpha(self):
        self.assertEqual(
            self._blit((255, 0, 0, 255), (0, 0, 0, 255), 1.0, "erase"),
            (255, 0, 0, 0))

    def test_zero_coverage_is_a_no_op(self):
        for mode in raster.BLEND_MODES:
            self.assertEqual(
                self._blit((7, 8, 9, 200), (255, 255, 255, 255), 0.0, mode),
                (7, 8, 9, 200), mode)

    def test_blend_pixel_matches_blit(self):
        dst = (60, 90, 120, 200)
        src = (10, 200, 30, 180)
        for mode in raster.BLEND_MODES:
            expected = raster.blend_pixel(dst, src, 0.5, mode)
            self.assertEqual(self._blit(dst, src, 0.5, mode), expected, mode)

    def test_unknown_mode_raises(self):
        img = Image(1, 1)
        with self.assertRaises(ValueError):
            raster.blit_mask(img, Mask(1, 1, b"\xff"), 0, 0, (1, 2, 3, 255),
                             "overlay")

    def test_composite_semi_transparent_over_semi_transparent(self):
        # dst a = 0.5 (128), src a = 0.5 with coverage 1
        # a = 0.5, inv = (128/255)*(0.5) = 0.25098, out_a = 0.75098 -> 192
        out = self._blit((0, 0, 0, 128), (255, 255, 255, 128), 1.0, "normal")
        self.assertEqual(out[3], 192)
        # colour = (255*0.50196 + 0*0.25098)/0.75294
        a = 128 / 255.0
        inv = (128 / 255.0) * (1.0 - a)
        oa = a + inv
        expected = int((255.0 * a + 0.0 * inv) / oa + 0.5)
        self.assertEqual(out[0], expected)


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestNumpyParity(unittest.TestCase):
    """The accelerated path must be byte identical to the scalar one."""

    def _noise(self, w, h, seed):
        rng = random.Random(seed)
        img = Image(w, h)
        for i in range(len(img.data)):
            img.data[i] = rng.randrange(256)
        return img

    def test_blit_parity_all_modes(self):
        rng = random.Random(11)
        mask = Mask(9, 7, bytes(rng.randrange(256) for _ in range(63)))
        for mode in raster.BLEND_MODES:
            base = self._noise(41, 29, seed=5)
            a = base.copy()
            b = base.copy()
            with _ScalarPath():
                raster.blit_brush(a, mask, 12.4, 9.6, (10, 200, 60, 173),
                                  mode, 0.73, 0.61)
            raster.set_use_numpy(True)
            raster.blit_brush(b, mask, 12.4, 9.6, (10, 200, 60, 173), mode,
                              0.73, 0.61)
            self.assertEqual(a.data, b.data, mode)

    def test_composite_parity_all_modes(self):
        for mode in raster.BLEND_MODES:
            bottom = self._noise(23, 17, seed=1)
            top = self._noise(23, 17, seed=2)
            a = bottom.copy()
            b = bottom.copy()
            with _ScalarPath():
                raster.composite(a, top, 0.63, mode, out=a)
            raster.set_use_numpy(True)
            raster.composite(b, top, 0.63, mode, out=b)
            self.assertEqual(a.data, b.data, mode)

    def test_downsample_parity_odd_sizes(self):
        for w, h in ((31, 17), (2, 2), (5, 1), (64, 64)):
            img = self._noise(w, h, seed=w + h)
            with _ScalarPath():
                a = img.downsample_box()
            raster.set_use_numpy(True)
            b = img.downsample_box()
            self.assertEqual(a.size, b.size)
            self.assertEqual(a.data, b.data, "%dx%d" % (w, h))

    def test_full_stroke_parity(self):
        params = brush.preset("airbrush")
        stamps = brush.stamp_along_path([(5, 5), (60, 40), (10, 55)], params)
        a = Image(64, 64)
        b = Image(64, 64)
        with _ScalarPath():
            brush.paint_stamps(a, stamps, (200, 30, 90, 255), params)
        raster.set_use_numpy(True)
        brush.paint_stamps(b, stamps, (200, 30, 90, 255), params)
        self.assertEqual(a.data, b.data)


# ==========================================================================
# image basics
# ==========================================================================

class TestImageBasics(unittest.TestCase):

    def test_fill_and_rect_fill(self):
        img = Image(5, 4, (1, 2, 3, 4))
        self.assertEqual(img.get_pixel(4, 3), (1, 2, 3, 4))
        img.fill((9, 9, 9, 9), (1, 1, 3, 3))
        self.assertEqual(img.get_pixel(1, 1), (9, 9, 9, 9))
        self.assertEqual(img.get_pixel(2, 2), (9, 9, 9, 9))
        self.assertEqual(img.get_pixel(3, 1), (1, 2, 3, 4))
        self.assertEqual(img.get_pixel(0, 0), (1, 2, 3, 4))

    def test_float_colours_are_scaled(self):
        img = Image(1, 1, (1.0, 0.0, 0.5, 1.0))
        self.assertEqual(img.get_pixel(0, 0), (255, 0, 128, 255))

    def test_out_of_bounds_access_is_safe(self):
        img = Image(2, 2)
        self.assertEqual(img.get_pixel(-1, 0), (0, 0, 0, 0))
        img.set_pixel(9, 9, (1, 1, 1, 1))       # must not raise

    def test_crop_and_paste(self):
        img = Image(6, 5)
        img.fill((10, 20, 30, 255), (2, 1, 5, 4))
        sub = img.crop((2, 1, 5, 4))
        self.assertEqual(sub.size, (3, 3))
        out = Image(6, 5)
        out.paste(sub, 2, 1)
        self.assertEqual(out.data, img.data)

    def test_paste_clips_at_the_border(self):
        img = Image(4, 4)
        sub = Image(3, 3, (255, 0, 0, 255))
        rect = img.paste(sub, 3, 3)
        self.assertEqual(rect, (3, 3, 4, 4))
        self.assertEqual(img.get_pixel(3, 3), (255, 0, 0, 255))
        self.assertIsNone(img.paste(sub, 40, 40))

    def test_bilinear_sample_hits_texel_centres(self):
        img = Image(2, 2)
        img.set_pixel(0, 0, (0, 0, 0, 255))
        img.set_pixel(1, 0, (100, 0, 0, 255))
        img.set_pixel(0, 1, (0, 100, 0, 255))
        img.set_pixel(1, 1, (100, 100, 0, 255))
        s = img.sample_bilinear(0.25, 0.25)
        self.assertAlmostEqual(s[0], 0.0, places=6)
        s = img.sample_bilinear(0.5, 0.5)
        self.assertAlmostEqual(s[0], 50.0, places=6)
        self.assertAlmostEqual(s[1], 50.0, places=6)

    def test_bilinear_sample_is_linear_between_texels(self):
        img = Image(4, 1)
        img.set_pixel(0, 0, (0, 0, 0, 255))
        img.set_pixel(1, 0, (200, 0, 0, 255))
        # 25% of the way from texel 0 to texel 1
        u = (0.5 + 0.25) / 4.0
        self.assertAlmostEqual(img.sample_bilinear(u, 0.5)[0], 50.0,
                               places=6)

    def test_downsample_box_average(self):
        img = Image(2, 2)
        img.set_pixel(0, 0, (0, 0, 0, 0))
        img.set_pixel(1, 0, (100, 0, 0, 0))
        img.set_pixel(0, 1, (0, 0, 0, 0))
        img.set_pixel(1, 1, (100, 0, 0, 0))
        small = img.downsample_box()
        self.assertEqual(small.size, (1, 1))
        self.assertEqual(small.get_pixel(0, 0), (50, 0, 0, 0))

    def test_downsample_odd_size_repeats_last_row(self):
        img = Image(3, 1, (40, 0, 0, 255))
        img.set_pixel(2, 0, (100, 0, 0, 255))
        small = img.downsample_box()
        self.assertEqual(small.size, (2, 1))
        self.assertEqual(small.get_pixel(0, 0)[0], 40)
        # the odd column is duplicated, so the average stays 100
        self.assertEqual(small.get_pixel(1, 0)[0], 100)

    def test_mipmap_chain_reaches_one_by_one(self):
        chain = Image(37, 5).mipmaps()
        self.assertEqual(chain[0].size, (37, 5))
        self.assertEqual(chain[-1].size, (1, 1))
        for a, b in zip(chain, chain[1:]):
            self.assertLessEqual(b.width, a.width)
            self.assertLessEqual(b.height, a.height)

    def test_rejects_zero_size(self):
        with self.assertRaises(ValueError):
            Image(0, 4)


# ==========================================================================
# brush engine
# ==========================================================================

class TestBrush(unittest.TestCase):

    def test_every_preset_builds_a_non_empty_mask(self):
        for name in brush.PRESETS:
            params = brush.preset(name)
            mask = brush.make_mask(params)
            self.assertGreater(mask.width, 0, name)
            self.assertGreater(mask.coverage(), 0.0, name)
            self.assertEqual(mask.width % 2, 1, name)   # odd -> has a centre

    def test_round_mask_is_a_disc_of_the_right_size(self):
        params = brush.BrushParams(kind="round", radius=8.0, hardness=1.0)
        mask = brush.make_mask(params)
        self.assertEqual(mask.width, 17)
        c = 8
        self.assertEqual(mask.get(c, c), 255)
        self.assertEqual(mask.get(0, 0), 0)              # corner is outside
        self.assertGreater(mask.get(c + 7, c), 0)        # inside the radius
        self.assertEqual(mask.get(c + 9, c), 0)          # past the radius
        # area within 12% of pi r^2
        self.assertAlmostEqual(mask.coverage() / (math.pi * 64.0), 1.0,
                               delta=0.12)

    def test_soft_brush_falls_off_monotonically(self):
        params = brush.BrushParams(kind="soft", radius=12.0, hardness=0.2)
        mask = brush.make_mask(params)
        c = (mask.width - 1) // 2
        vals = [mask.get(c + d, c) for d in range(0, c + 1)]
        for a, b in zip(vals, vals[1:]):
            self.assertGreaterEqual(a, b)
        self.assertEqual(vals[-1], 0)

    def test_pressure_curves(self):
        self.assertAlmostEqual(brush.apply_pressure_curve(0.5, "linear"), 0.5)
        self.assertAlmostEqual(brush.apply_pressure_curve(0.5, "soft"), 0.25)
        self.assertAlmostEqual(brush.apply_pressure_curve(0.25, "hard"), 0.5)
        self.assertAlmostEqual(brush.apply_pressure_curve(0.5, 2.0), 0.25)
        self.assertEqual(brush.apply_pressure_curve(0.0, "constant"), 0.0)
        self.assertEqual(brush.apply_pressure_curve(0.1, "constant"), 1.0)
        self.assertEqual(brush.apply_pressure_curve(5.0), 1.0)
        with self.assertRaises(ValueError):
            brush.apply_pressure_curve(0.5, "wobbly")

    def test_spacing_along_a_fast_sweep(self):
        """One huge jump must still produce evenly spaced stamps."""
        params = brush.BrushParams(radius=10.0, spacing=0.25,
                                   size_pressure=False, jitter=0.0)
        sampler = brush.StrokeSampler(params)
        stamps = sampler.begin(0.0, 0.0, 1.0)
        stamps += sampler.move(100.0, 0.0, 1.0)     # a single 100px frame
        xs = [s.x for s in stamps]
        self.assertEqual(len(stamps), 21)           # 0, 5, ... 100
        for a, b in zip(xs, xs[1:]):
            self.assertAlmostEqual(b - a, 5.0, places=9)
        self.assertAlmostEqual(xs[0], 0.0)
        self.assertAlmostEqual(xs[-1], 100.0)

    def test_spacing_is_continuous_across_frames(self):
        """Leftover distance must carry over between move() calls."""
        params = brush.BrushParams(radius=10.0, spacing=0.25,
                                   size_pressure=False)
        one = brush.StrokeSampler(params)
        a = one.begin(0.0, 0.0, 1.0) + one.move(100.0, 0.0, 1.0)
        many = brush.StrokeSampler(params)
        b = many.begin(0.0, 0.0, 1.0)
        for i in range(1, 8):
            b += many.move(100.0 * i / 7.0, 0.0, 1.0)
        self.assertEqual(len(a), len(b))
        for sa, sb in zip(a, b):
            self.assertAlmostEqual(sa.x, sb.x, places=6)

    def test_fast_sweep_paints_a_continuous_band(self):
        img = Image(128, 32)
        params = brush.BrushParams(radius=5.0, spacing=0.2,
                                   size_pressure=False, hardness=1.0)
        stamps = brush.stamp_along_path([(4, 16), (124, 16)], params)
        brush.paint_stamps(img, stamps, (255, 0, 0, 255), params)
        for x in range(4, 125):
            self.assertGreater(img.get_pixel(x, 16)[3], 0,
                               "gap in the stroke at x=%d" % x)

    def test_pressure_scales_the_stamp_radius(self):
        params = brush.BrushParams(radius=20.0, size_pressure=True)
        s = brush.StrokeSampler(params)
        light = s.begin(0.0, 0.0, 0.1)[0]
        s2 = brush.StrokeSampler(params)
        heavy = s2.begin(0.0, 0.0, 1.0)[0]
        self.assertLess(light.radius, heavy.radius)
        self.assertAlmostEqual(heavy.radius, 20.0, places=6)

    def test_zero_length_move_emits_nothing(self):
        params = brush.BrushParams(radius=4.0)
        s = brush.StrokeSampler(params)
        s.begin(3.0, 3.0, 1.0)
        self.assertEqual(s.move(3.0, 3.0, 1.0), [])

    def test_stamp_along_path_handles_short_input(self):
        params = brush.BrushParams()
        self.assertEqual(brush.stamp_along_path([], params), [])
        self.assertEqual(len(brush.stamp_along_path([(1, 1)], params)), 1)

    def test_jitter_moves_stamps_but_stays_bounded(self):
        params = brush.BrushParams(radius=10.0, jitter=0.5, spacing=0.5,
                                   size_pressure=False)
        stamps = brush.stamp_along_path([(50, 50), (150, 50)], params)
        offs = [abs(s.y - 50.0) for s in stamps]
        self.assertGreater(max(offs), 0.0)
        self.assertLessEqual(max(offs), 0.5 * 10.0 + 1e-9)

    def test_mask_cache_returns_the_same_object(self):
        brush.clear_mask_cache()
        p = brush.BrushParams(radius=7.0)
        self.assertIs(brush.make_mask(p), brush.make_mask(p))
        self.assertIsNot(brush.make_mask(p), brush.make_mask(p, cache=False))

    def test_smudge_and_clone_do_not_crash(self):
        img = Image(64, 64, (10, 200, 30, 255))
        img.fill((250, 0, 0, 255), (0, 0, 32, 64))
        for name in ("smudge", "clone"):
            params = brush.preset(name)
            stamps = brush.stamp_along_path([(20, 32), (44, 32)], params)
            rect = brush.paint_stamps(img, stamps, (0, 0, 0, 255), params)
            self.assertIsNotNone(rect, name)

    def test_invalid_parameters_raise(self):
        with self.assertRaises(ValueError):
            brush.BrushParams(kind="banana")
        with self.assertRaises(ValueError):
            brush.BrushParams(blend="overlay")
        with self.assertRaises(KeyError):
            brush.preset("nope")

    def test_params_roundtrip(self):
        p = brush.preset("chisel")
        q = brush.BrushParams.from_dict(p.to_dict())
        self.assertEqual(p, q)


# ==========================================================================
# layers
# ==========================================================================

class TestLayers(unittest.TestCase):

    def _stack(self):
        st = layers.LayerStack(16, 16)
        st.add_layer("Bottom", color=(255, 0, 0, 255))
        st.add_layer("Top", color=(0, 0, 255, 255))
        return st

    def test_composite_with_opacity(self):
        st = self._stack()
        st.set_opacity(1, 0.5)
        out = st.composite()
        # a = 0.5 over an opaque red layer -> 127.5 -> 128 each side
        self.assertEqual(out.get_pixel(8, 8), (128, 0, 128, 255))

    def test_hidden_layer_is_skipped(self):
        st = self._stack()
        st.set_visible(1, False)
        self.assertEqual(st.composite().get_pixel(0, 0), (255, 0, 0, 255))

    def test_multiply_layer(self):
        st = layers.LayerStack(4, 4)
        st.add_layer("a", color=(200, 100, 50, 255))
        st.add_layer("b", color=(128, 128, 128, 255), blend="multiply")
        self.assertEqual(st.composite().get_pixel(0, 0), (100, 50, 25, 255))

    def test_erase_layer_punches_a_hole(self):
        st = layers.LayerStack(4, 4)
        st.add_layer("a", color=(10, 20, 30, 255))
        st.add_layer("e", color=(0, 0, 0, 255), blend="erase")
        self.assertEqual(st.composite().get_pixel(0, 0)[3], 0)

    def test_add_remove_reorder_rename(self):
        st = self._stack()
        st.add_layer("Third")
        self.assertEqual([l.name for l in st], ["Bottom", "Top", "Third"])
        st.move_layer(2, 0)
        self.assertEqual([l.name for l in st], ["Third", "Bottom", "Top"])
        st.rename(0, "First")
        self.assertEqual(st[0].name, "First")
        st.remove_layer(0)
        self.assertEqual([l.name for l in st], ["Bottom", "Top"])
        with self.assertRaises(IndexError):
            st.remove_layer(9)

    def test_merge_down(self):
        st = self._stack()
        st.set_opacity(1, 0.5)
        expected = st.composite().get_pixel(0, 0)
        st.merge_down(1)
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].image.get_pixel(0, 0), expected)
        with self.assertRaises(IndexError):
            st.merge_down(0)

    def test_flatten(self):
        st = self._stack()
        expected = st.composite()
        st.flatten("Flat")
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].name, "Flat")
        self.assertEqual(st[0].image.data, expected.data)

    def test_lock_and_blend_validation(self):
        st = self._stack()
        st.set_locked(0, True)
        self.assertTrue(st[0].locked)
        st.set_blend(0, "screen")
        self.assertEqual(st[0].blend, "screen")
        with self.assertRaises(ValueError):
            st.set_blend(0, "overlay")

    def test_layer_serialisation_follows_section_4(self):
        st = self._stack()
        recs = st.to_dict(3)
        self.assertEqual(recs[0]["image"], 3)
        self.assertEqual(recs[1]["image"], 4)
        self.assertEqual(set(recs[0]),
                         {"name", "image", "opacity", "blend", "visible",
                          "resolution"})
        self.assertEqual(recs[0]["resolution"], [16, 16])
        back = layers.LayerStack.from_dict(recs)
        self.assertEqual([l.name for l in back], ["Bottom", "Top"])

    def test_by_id_survives_reordering(self):
        st = self._stack()
        top = st[1]
        st.move_layer(1, 0)
        self.assertIs(st.by_id(top.id), top)


class TestHistory(unittest.TestCase):

    def _painted(self):
        st = layers.LayerStack(96, 96)
        layer = st.add_layer("Paint")
        hist = layers.History(st, max_entries=8)
        return st, layer, hist

    def test_undo_redo_restores_exact_pixels(self):
        st, layer, hist = self._painted()
        params = brush.preset("round")
        before = layer.image.copy()
        hist.begin("stroke")
        hist.snapshot(layer, (0, 0, 96, 96))
        stamps = brush.stamp_along_path([(10, 10), (80, 70)], params)
        brush.paint_stamps(layer.image, stamps, (255, 0, 0, 255), params)
        after = layer.image.copy()
        hist.commit()
        self.assertNotEqual(before.data, after.data)

        self.assertIsNotNone(hist.undo())
        self.assertEqual(layer.image.data, before.data)
        self.assertIsNotNone(hist.redo())
        self.assertEqual(layer.image.data, after.data)

    def test_multiple_strokes_undo_in_order(self):
        st, layer, hist = self._painted()
        params = brush.preset("round")
        snapshots = [layer.image.copy()]
        for i in range(3):
            hist.begin("stroke %d" % i)
            hist.snapshot(layer, (0, 0, 96, 96))
            brush.paint_stamps(
                layer.image,
                brush.stamp_along_path([(5 + 20 * i, 5), (5 + 20 * i, 90)],
                                       params),
                (0, 0, 255, 255), params)
            hist.commit()
            snapshots.append(layer.image.copy())
        for i in (2, 1, 0):
            hist.undo()
            self.assertEqual(layer.image.data, snapshots[i].data,
                             "undo step %d" % i)
        for i in (1, 2, 3):
            hist.redo()
            self.assertEqual(layer.image.data, snapshots[i].data,
                             "redo step %d" % i)

    def test_tiles_are_smaller_than_the_whole_image(self):
        st, layer, hist = self._painted()
        params = brush.BrushParams(radius=3.0)
        hist.begin("dab")
        hist.snapshot(layer, (10, 10, 20, 20))
        brush.paint_stamps(layer.image,
                           brush.stamp_along_path([(15, 15)], params),
                           (255, 255, 255, 255), params)
        hist.commit()
        self.assertGreater(hist.nbytes(), 0)
        self.assertLess(hist.nbytes(), len(layer.image.data))

    def test_no_op_entries_are_dropped(self):
        st, layer, hist = self._painted()
        hist.begin("nothing")
        hist.snapshot(layer, (0, 0, 10, 10))
        self.assertIsNone(hist.commit())
        self.assertFalse(hist.can_undo())

    def test_history_is_bounded(self):
        st, layer, hist = self._painted()
        params = brush.BrushParams(radius=2.0)
        for i in range(20):
            hist.begin("s%d" % i)
            hist.snapshot(layer, (0, 0, 96, 96))
            brush.paint_stamps(layer.image,
                               brush.stamp_along_path([(i + 1, i + 1)],
                                                      params),
                               (i + 1, 0, 0, 255), params)
            hist.commit()
        self.assertLessEqual(len(hist.undo_labels()), 8)

    def test_abort_restores_pixels(self):
        st, layer, hist = self._painted()
        params = brush.BrushParams(radius=6.0)
        before = layer.image.copy()
        hist.begin("aborted")
        hist.snapshot(layer, (0, 0, 96, 96))
        brush.paint_stamps(layer.image,
                           brush.stamp_along_path([(40, 40)], params),
                           (1, 2, 3, 255), params)
        hist.abort()
        self.assertEqual(layer.image.data, before.data)
        self.assertFalse(hist.can_undo())

    def test_structural_undo_add_and_remove(self):
        st = layers.LayerStack(8, 8)
        st.add_layer("A", color=(1, 2, 3, 255))
        hist = layers.History(st)
        new = st.add_layer("B", color=(9, 9, 9, 255))
        hist.push_add_layer(new, 1)
        self.assertEqual(len(st), 2)
        hist.undo()
        self.assertEqual(len(st), 1)
        hist.redo()
        self.assertEqual(len(st), 2)
        self.assertEqual(st[1].image.get_pixel(0, 0), (9, 9, 9, 255))

        removed = st[1]
        hist.push_remove_layer(removed, 1)
        st.remove_layer(1)
        self.assertEqual(len(st), 1)
        hist.undo()
        self.assertEqual(len(st), 2)
        self.assertEqual(st[1].image.get_pixel(0, 0), (9, 9, 9, 255))

    def test_property_undo(self):
        st = layers.LayerStack(4, 4)
        layer = st.add_layer("A")
        hist = layers.History(st)
        hist.push_property(layer, "opacity", 1.0, 0.25)
        layer.opacity = 0.25
        hist.undo()
        self.assertEqual(layer.opacity, 1.0)
        hist.redo()
        self.assertEqual(layer.opacity, 0.25)


# ==========================================================================
# texture painting
# ==========================================================================

class TestUvMapping(unittest.TestCase):

    def test_uv_to_pixel_flips_v(self):
        # v = 0 is the bottom of the texture, row 0 of the image is the top
        self.assertAlmostEqual(
            texture_paint.uv_to_pixel((0.0, 1.0), 8, 8, wrap=False)[1], -0.5)
        self.assertAlmostEqual(
            texture_paint.uv_to_pixel((0.0, 0.0), 8, 8, wrap=False)[1], 7.5)
        # with wrapping on, v = 1 tiles back onto v = 0
        self.assertAlmostEqual(texture_paint.uv_to_pixel((0.0, 1.0), 8, 8)[1],
                               7.5)

    def test_uv_pixel_roundtrip(self):
        for x, y in ((0, 0), (3, 5), (7, 7)):
            uv = texture_paint.pixel_to_uv(x, y, 8, 8)
            px = texture_paint.uv_to_pixel(uv, 8, 8)
            self.assertAlmostEqual(px[0], x, places=9)
            self.assertAlmostEqual(px[1], y, places=9)

    def test_uv_wraps(self):
        a = texture_paint.uv_to_pixel((1.25, 0.5), 8, 8)
        b = texture_paint.uv_to_pixel((0.25, 0.5), 8, 8)
        self.assertAlmostEqual(a[0], b[0])


class TestSeamDilation(unittest.TestCase):

    def test_single_pixel_grows_by_the_radius(self):
        img = Image(11, 11)
        img.set_pixel(5, 5, (10, 20, 30, 255))
        texture_paint.dilate_edges(img, 2)
        for y in range(3, 8):
            for x in range(3, 8):
                self.assertEqual(img.get_pixel(x, y), (10, 20, 30, 255),
                                 "(%d, %d)" % (x, y))
        self.assertEqual(img.get_pixel(2, 5), (0, 0, 0, 0))
        self.assertEqual(img.get_pixel(5, 8), (0, 0, 0, 0))

    def test_existing_pixels_are_untouched(self):
        img = Image(9, 9, (1, 2, 3, 255))
        img.set_pixel(4, 4, (200, 100, 50, 255))
        texture_paint.dilate_edges(img, 3)
        self.assertEqual(img.get_pixel(4, 4), (200, 100, 50, 255))
        self.assertEqual(img.get_pixel(0, 0), (1, 2, 3, 255))

    def test_dilation_bleeds_across_an_island_border(self):
        """A stroke that stops at a UV island edge must bleed past it."""
        img = Image(20, 20)
        img.fill((255, 0, 0, 255), (0, 0, 10, 20))    # island ends at x = 10
        texture_paint.dilate_edges(img, 3)
        for x in range(10, 13):
            self.assertGreater(img.get_pixel(x, 10)[3], 0,
                               "seam not dilated at x=%d" % x)
        self.assertEqual(img.get_pixel(14, 10), (0, 0, 0, 0))

    def test_zero_radius_is_a_no_op(self):
        img = Image(4, 4)
        self.assertIsNone(texture_paint.dilate_edges(img, 0))

    def test_painter_dilates_at_the_end_of_a_stroke(self):
        target = texture_paint.PaintTarget("Body", 64, 64)
        target.dilate_radius = 3
        params = brush.BrushParams(radius=3.0, hardness=1.0)
        painter = texture_paint.TexturePainter(target, params,
                                               (255, 255, 255, 255))
        hits = [PaintHit((0, 0, 0), (0, 0, 1), (0.5, 0.5), (0, 0, -1))]
        painter.stroke(hits)
        img = target.stack.active.image
        opaque = [(x, y) for y in range(64) for x in range(64)
                  if img.get_pixel(x, y)[3] > 0]
        xs = [p[0] for p in opaque]
        # radius 3 brush + 3px of dilation reaches roughly 7 px out
        self.assertGreaterEqual(max(xs) - min(xs), 12)


class TestAutoUv(unittest.TestCase):

    def _mesh(self):
        verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                 (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
        idx = [0, 1, 2, 0, 2, 3,        # bottom
               4, 6, 5, 4, 7, 6,        # top
               0, 4, 5, 0, 5, 1,        # front
               2, 6, 7, 2, 7, 3,        # back
               1, 5, 6, 1, 6, 2,        # right
               0, 3, 7, 0, 7, 4]        # left
        return verts, idx

    def _boxes(self, uvset):
        out = []
        for i in range(uvset.triangle_count):
            tri = uvset.triangle_uvs(i)
            out.append((min(p[0] for p in tri), min(p[1] for p in tri),
                        max(p[0] for p in tri), max(p[1] for p in tri)))
        return out

    def test_atlas_uvs_are_in_range(self):
        verts, idx = self._mesh()
        uvset = texture_paint.atlas_uv(verts, idx)
        self.assertEqual(uvset.method, "atlas")
        self.assertEqual(uvset.triangle_count, 12)
        self.assertTrue(uvset.in_range())

    def test_atlas_uvs_do_not_overlap(self):
        verts, idx = self._mesh()
        uvset = texture_paint.atlas_uv(verts, idx)
        boxes = self._boxes(uvset)

        def overlap(a, b):
            return not (a[2] <= b[0] or b[2] <= a[0]
                        or a[3] <= b[1] or b[3] <= a[1])

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                self.assertFalse(overlap(boxes[i], boxes[j]),
                                 "triangles %d and %d overlap" % (i, j))

    def test_atlas_preserves_triangle_aspect(self):
        verts = [(0, 0, 0), (4, 0, 0), (0, 1, 0)]
        uvset = texture_paint.atlas_uv(verts, [0, 1, 2], gutter=0.0)
        a, b, c = uvset.triangle_uvs(0)
        wide = math.hypot(b[0] - a[0], b[1] - a[1])
        tall = math.hypot(c[0] - a[0], c[1] - a[1])
        self.assertAlmostEqual(wide / tall, 4.0, places=6)

    def test_atlas_survives_a_degenerate_triangle(self):
        verts = [(0, 0, 0), (0, 0, 0), (0, 0, 0)]
        uvset = texture_paint.atlas_uv(verts, [0, 1, 2])
        self.assertTrue(uvset.in_range())
        for u, v in uvset.uvs:
            self.assertEqual(u, u)      # not NaN
            self.assertEqual(v, v)

    def test_atlas_of_an_empty_mesh(self):
        uvset = texture_paint.atlas_uv([], [])
        self.assertEqual(uvset.triangle_count, 0)

    def test_planar_uvs_are_in_range(self):
        verts, idx = self._mesh()
        uvset = texture_paint.planar_uv(verts, idx)
        self.assertTrue(uvset.in_range())
        self.assertEqual(len(uvset.vertices), len(verts))

    def test_box_uvs_are_in_range_and_split_per_triangle(self):
        verts, idx = self._mesh()
        uvset = texture_paint.box_uv(verts, idx)
        self.assertTrue(uvset.in_range())
        self.assertEqual(len(uvset.vertices), len(idx))

    def test_generate_uvs_dispatch(self):
        verts, idx = self._mesh()
        for method in ("planar", "box", "atlas"):
            self.assertEqual(
                texture_paint.generate_uvs(verts, idx, method).method, method)
        with self.assertRaises(ValueError):
            texture_paint.generate_uvs(verts, idx, "spherical")

    def test_target_generates_uvs_when_missing(self):
        verts, idx = self._mesh()
        t = texture_paint.PaintTarget("Body", 32, 32)
        self.assertIsNone(t.uvset)
        uvset = t.ensure_uvs(verts, idx, "atlas")
        self.assertIsNotNone(uvset)
        self.assertIs(t.ensure_uvs(verts, idx), uvset)


class TestTexturePainter(unittest.TestCase):

    def _target(self, size=64):
        t = texture_paint.PaintTarget("Body", size, size)
        t.dilate_radius = 0
        return t

    def _hit(self, u, v, pressure=1.0, normal=(0, 0, 1), ray=(0, 0, -1)):
        return PaintHit((0, 0, 0), normal, (u, v), ray, "Body", None, pressure)

    def test_stroke_paints_into_the_active_layer(self):
        t = self._target()
        params = brush.BrushParams(radius=4.0, hardness=1.0,
                                   size_pressure=False)
        p = texture_paint.TexturePainter(t, params, (255, 0, 0, 255))
        hits = [self._hit(0.2 + i * 0.02, 0.5) for i in range(20)]
        rect = p.stroke(hits)
        self.assertIsNotNone(rect)
        img = t.stack.active.image
        px = texture_paint.uv_to_pixel((0.3, 0.5), 64, 64)
        self.assertEqual(img.get_pixel(int(px[0]), int(px[1])),
                         (255, 0, 0, 255))

    def test_backfacing_hits_are_rejected(self):
        t = self._target()
        p = texture_paint.TexturePainter(t, brush.BrushParams(radius=4.0),
                                         (255, 0, 0, 255))
        back = self._hit(0.5, 0.5, normal=(0, 0, 1), ray=(0, 0, 1))
        self.assertFalse(back.facing())
        self.assertFalse(p.begin(back))
        self.assertEqual(p.rejected, 1)
        self.assertEqual(t.stack.active.image.get_pixel(32, 32)[3], 0)

    def test_hit_without_a_ray_is_accepted(self):
        h = PaintHit((0, 0, 0), (0, 0, 1), (0.5, 0.5), None)
        self.assertTrue(h.facing())

    def test_seam_jump_breaks_the_stroke(self):
        t = self._target(128)
        params = brush.BrushParams(radius=2.0, hardness=1.0, spacing=0.2,
                                   size_pressure=False)
        p = texture_paint.TexturePainter(t, params, (255, 255, 255, 255))
        p.stroke([self._hit(0.1, 0.1), self._hit(0.9, 0.9)])
        self.assertEqual(p.seam_breaks, 1)
        img = t.stack.active.image
        # nothing was smeared across the middle of the atlas
        self.assertEqual(img.get_pixel(64, 64)[3], 0)

    def test_locked_layer_cannot_be_painted(self):
        t = self._target()
        t.stack.set_locked(0, True)
        p = texture_paint.TexturePainter(t, brush.BrushParams(),
                                         (255, 0, 0, 255))
        self.assertFalse(p.begin(self._hit(0.5, 0.5)))

    def test_painting_is_undoable(self):
        t = self._target()
        before = t.stack.active.image.copy()
        params = brush.BrushParams(radius=5.0)
        p = texture_paint.TexturePainter(t, params, (0, 255, 0, 255))
        p.stroke([self._hit(0.3, 0.3), self._hit(0.7, 0.7)])
        after = t.stack.active.image.copy()
        self.assertNotEqual(before.data, after.data)
        t.history.undo()
        self.assertEqual(t.stack.active.image.data, before.data)
        t.history.redo()
        self.assertEqual(t.stack.active.image.data, after.data)

    def test_target_serialisation(self):
        t = self._target(32)
        rec = t.to_dict(0)
        self.assertEqual(rec["fc_name"], "Body")
        self.assertEqual(rec["layers"][0]["resolution"], [32, 32])
        back = texture_paint.PaintTarget.from_dict(rec, t.layer_images())
        self.assertEqual(len(back.stack), 1)
        self.assertEqual(back.stack[0].image.size, (32, 32))

    def test_composite_is_cached_until_invalidated(self):
        t = self._target()
        first = t.composite()
        self.assertIs(first, t.composite())
        t.invalidate()
        self.assertIs(first, t.composite())     # same buffer, recomputed


# ==========================================================================
# 3d strokes
# ==========================================================================

class TestStroke3D(unittest.TestCase):

    def _helix(self, n=25, width=0.02):
        s = stroke3d.Stroke3D("ribbon", (1, 0, 0, 1), width)
        for i in range(n):
            a = i * 0.25
            s.add_point((math.cos(a) * 0.3, math.sin(a) * 0.3, i * 0.03),
                        n=(0, 0, 1), pressure=1.0, force=True)
        return s

    def test_ribbon_vertex_and_index_counts(self):
        s = self._helix(25)
        geo = s.build_geometry("ribbon")
        self.assertEqual(geo.vertex_count, 2 * 25)
        self.assertEqual(geo.face_count, 24)
        self.assertEqual(geo.index_count(), 24 * 5)      # 4 indices + (-1)
        self.assertEqual(len(geo.normals), geo.vertex_count)
        self.assertEqual(len(geo.uvs), geo.vertex_count)
        geo.validate()
        self.assertTrue(geo.is_finite())

    def test_tube_counts_include_caps(self):
        s = self._helix(10)
        geo = s.build_geometry("tube", sides=8, caps=True)
        self.assertEqual(geo.vertex_count, 8 * 10)
        self.assertEqual(geo.face_count, 8 * 9 + 2)
        geo.validate()
        self.assertTrue(geo.is_finite())

    def test_tube_without_caps(self):
        s = self._helix(10)
        geo = s.build_geometry("tube", sides=6, caps=False)
        self.assertEqual(geo.face_count, 6 * 9)

    def test_hull_counts(self):
        s = self._helix(12)
        geo = s.build_geometry("hull")
        self.assertEqual(geo.vertex_count, 4 * 12)
        self.assertEqual(geo.face_count, 4 * 11 + 2)
        self.assertTrue(geo.is_finite())

    def test_taper_has_no_caps_and_shrinks_at_the_ends(self):
        s = self._helix(15)
        geo = s.build_geometry("taper", sides=8)
        self.assertEqual(geo.face_count, 8 * 14)
        self.assertTrue(geo.is_finite())

    def test_duplicated_points_produce_a_billboard_without_nan(self):
        s = stroke3d.Stroke3D("tube", width=0.01)
        for _ in range(8):
            s.add_point((1.0, 2.0, 3.0), n=(0, 0, 1), force=True)
        geo = s.build_geometry()
        self.assertEqual(geo.vertex_count, 4)
        self.assertEqual(geo.face_count, 1)
        self.assertTrue(geo.is_finite())
        geo.validate()

    def test_interleaved_duplicates_are_dropped(self):
        s = stroke3d.Stroke3D("ribbon", width=0.01)
        for x in (0.0, 0.0, 0.1, 0.1, 0.1, 0.2):
            s.add_point((x, 0.0, 0.0), n=(0, 0, 1), force=True)
        geo = s.build_geometry()
        self.assertEqual(geo.vertex_count, 6)        # 3 distinct samples
        self.assertEqual(geo.face_count, 2)
        self.assertTrue(geo.is_finite())

    def test_empty_stroke_produces_empty_geometry(self):
        geo = stroke3d.Stroke3D().build_geometry()
        self.assertEqual(geo.vertex_count, 0)
        self.assertEqual(geo.face_count, 0)
        self.assertTrue(geo.is_finite())

    def test_reversing_path_does_not_produce_nan(self):
        s = stroke3d.Stroke3D("tube", width=0.02)
        for x in (0.0, 0.1, 0.2, 0.3, 0.2, 0.1, 0.0):
            s.add_point((x, 0.0, 0.0), n=(0, 1, 0), force=True)
        geo = s.build_geometry(sides=6)
        self.assertTrue(geo.is_finite())
        geo.validate()

    def test_collinear_path_frames_do_not_twist(self):
        pts = [(0.0, 0.0, float(i)) for i in range(6)]
        tans, norms, binorms = stroke3d.parallel_transport_frames(
            pts, (1.0, 0.0, 0.0))
        for n in norms:
            self.assertAlmostEqual(n[0], 1.0, places=9)
            self.assertAlmostEqual(n[1], 0.0, places=9)
        for t, n in zip(tans, norms):
            self.assertAlmostEqual(t[0] * n[0] + t[1] * n[1] + t[2] * n[2],
                                   0.0, places=9)

    def test_frames_stay_orthonormal_on_a_wiggly_path(self):
        pts = [(math.cos(i * 0.4), math.sin(i * 0.7), i * 0.2)
               for i in range(30)]
        tans, norms, binorms = stroke3d.parallel_transport_frames(pts)
        for t, n, b in zip(tans, norms, binorms):
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in n)), 1.0,
                                   places=9)
            self.assertAlmostEqual(sum(a * c for a, c in zip(t, n)), 0.0,
                                   places=9)
            self.assertAlmostEqual(sum(a * c for a, c in zip(n, b)), 0.0,
                                   places=9)

    def test_frames_do_not_flip_at_an_inflection(self):
        """A Frenet frame flips here; parallel transport must not."""
        pts = []
        for i in range(-10, 11):
            x = i * 0.1
            pts.append((x, x ** 3, 0.0))
        _, norms, _ = stroke3d.parallel_transport_frames(pts, (0.0, 0.0, 1.0))
        for a, b in zip(norms, norms[1:]):
            self.assertGreater(sum(p * q for p, q in zip(a, b)), 0.0)

    def test_decimation_keeps_the_shape(self):
        s = stroke3d.Stroke3D("ribbon", width=0.01)
        for i in range(101):
            s.add_point((i * 0.01, 0.0, 0.0), force=True)
        n0 = len(s)
        s.decimate(0.001)
        self.assertLess(len(s), n0)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s.points[-1].p[0], 1.0, places=9)

    def test_min_step_drops_dense_input(self):
        s = stroke3d.Stroke3D("ribbon", width=0.1)     # min_step = 0.025
        self.assertIsNotNone(s.add_point((0, 0, 0)))
        self.assertIsNone(s.add_point((0.001, 0, 0)))
        self.assertIsNotNone(s.add_point((0.5, 0, 0)))
        self.assertEqual(len(s), 2)

    def test_section_4_roundtrip(self):
        s = self._helix(5)
        d = s.to_dict()
        self.assertEqual(set(d), {"brush", "color", "width", "points"})
        self.assertEqual(set(d["points"][0]), {"p", "n", "r", "t"})
        back = stroke3d.Stroke3D.from_dict(d)
        self.assertEqual(back.brush, s.brush)
        self.assertEqual(len(back), len(s))
        self.assertEqual(back.to_dict(), d)

    def test_stroke_set_roundtrip(self):
        ss = stroke3d.StrokeSet([self._helix(4), self._helix(3)])
        data = ss.to_list()
        back = stroke3d.StrokeSet.from_list(data)
        self.assertEqual(len(back), 2)
        self.assertEqual(back.to_list(), data)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            stroke3d.Stroke3D("splatter")
        with self.assertRaises(ValueError):
            self._helix(3).build_geometry("splatter")

    def test_freecad_emitters_fail_with_a_clear_message(self):
        s = self._helix(4)
        for fn in (s.to_freecad_mesh, s.to_part_shape):
            try:
                fn()
            except RuntimeError as exc:
                self.assertIn("unavailable", str(exc))
            except ImportError:
                self.fail("emitters must raise RuntimeError, not ImportError")


# ==========================================================================
# session
# ==========================================================================

class TestPaintSession(unittest.TestCase):

    def _hit(self, u, v, name="Body"):
        return PaintHit((0, 0, 0), (0, 0, 1), (u, v), (0, 0, -1), name, None,
                        1.0)

    def test_mode_switching(self):
        s = PaintSession()
        self.assertIsNone(s.mode)
        for m in ("TEXTURE", "STROKE3D", "VECTOR"):
            s.set_mode(m)
            self.assertEqual(s.mode, m)
        s.set_mode(None)
        self.assertIsNone(s.mode)
        with self.assertRaises(ValueError):
            s.set_mode("SCULPT")

    def test_texture_mode_event_api(self):
        s = PaintSession(mode="TEXTURE")
        s.add_target("Body", 64, 64)
        s.set_color((255, 0, 0, 255))
        s.set_radius(4.0)
        self.assertTrue(s.on_trigger(0, 1.0, hit=self._hit(0.2, 0.5)))
        for i in range(1, 20):
            s.on_move(0, hit=self._hit(0.2 + i * 0.02, 0.5))
        self.assertTrue(s.on_trigger(0, 0.0))
        img = s.active_layer_stack().active.image
        px = texture_paint.uv_to_pixel((0.3, 0.5), 64, 64)
        self.assertGreater(img.get_pixel(int(px[0]), int(px[1]))[3], 0)

    def test_trigger_needs_to_cross_the_threshold(self):
        s = PaintSession(mode="TEXTURE")
        s.add_target("Body", 32, 32)
        self.assertFalse(s.on_trigger(0, 0.5, hit=self._hit(0.5, 0.5)))
        self.assertTrue(s.on_trigger(0, 0.9, hit=self._hit(0.5, 0.5)))
        self.assertFalse(s.on_trigger(0, 0.5, hit=self._hit(0.5, 0.5)))
        self.assertTrue(s.on_trigger(0, 0.1, hit=self._hit(0.5, 0.5)))

    def test_grip_cancels_a_stroke(self):
        s = PaintSession(mode="TEXTURE")
        t = s.add_target("Body", 48, 48)
        before = t.stack.active.image.copy()
        s.on_trigger(0, 1.0, hit=self._hit(0.5, 0.5))
        s.on_move(0, hit=self._hit(0.6, 0.5))
        self.assertTrue(s.on_grip(0, 1.0))
        self.assertEqual(t.stack.active.image.data, before.data)

    def test_stroke3d_mode(self):
        s = PaintSession(mode="STROKE3D")
        s.on_trigger(1, 1.0, position=(0, 0, 0), normal=(0, 0, 1))
        for i in range(1, 25):
            s.on_move(1, position=(i * 0.02, math.sin(i * 0.3) * 0.05, 0.0),
                      normal=(0, 0, 1))
        self.assertTrue(s.on_trigger(1, 0.0))
        self.assertEqual(len(s.strokes), 1)
        geo = s.strokes[0].build_geometry()
        self.assertTrue(geo.is_finite())

    def test_vector_mode_freehand(self):
        s = PaintSession(mode="VECTOR")
        s.on_trigger(0, 1.0, position=(0, 0, 0))
        for i in range(1, 60):
            s.on_move(0, position=(i * 1.0, math.sin(i * 0.08) * 12.0, 0.0))
        self.assertTrue(s.on_trigger(0, 0.0))
        doc = s.vector_document
        self.assertIsNotNone(doc)
        self.assertEqual(len(doc.paths), 1)
        self.assertGreaterEqual(len(doc.paths[0].nodes), 2)

    def test_paint_manifest_roundtrip(self):
        s = PaintSession(mode="TEXTURE")
        t = s.add_target("Body", 32, 32)
        t.stack.add_layer("Second", color=(0, 255, 0, 128))
        s.set_color((255, 0, 0, 255))
        s.on_trigger(0, 1.0, hit=self._hit(0.5, 0.5))
        s.on_trigger(0, 0.0)
        manifest = s.export_paint_manifest()
        images = s.export_paint_images()
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(len(images), 2)
        self.assertEqual(manifest["targets"][0]["layers"][1]["image"], 1)

        other = PaintSession()
        self.assertTrue(other.import_paint_manifest(manifest, images))
        back = other.active_layer_stack()
        self.assertEqual([l.name for l in back], ["Base", "Second"])
        self.assertEqual(back[1].image.data,
                         t.stack[1].image.data)

    def test_manifest_is_none_when_nothing_was_painted(self):
        self.assertIsNone(PaintSession().export_paint_manifest())
        self.assertIsNone(PaintSession().export_vector_manifest())

    def test_export_manifest_holds_both_sections(self):
        s = PaintSession(mode="TEXTURE")
        s.add_target("Body", 16, 16)
        s.set_mode("VECTOR")
        s.on_trigger(0, 1.0, position=(0, 0, 0))
        for i in range(1, 20):
            s.on_move(0, position=(i, i * 0.5, 0))
        s.on_trigger(0, 0.0)
        m = s.export_manifest()
        self.assertIn("paint", m)
        self.assertIn("vector", m)
        self.assertEqual(m["vector"]["version"], 1)

    def test_undo_redo_through_the_session(self):
        s = PaintSession(mode="TEXTURE")
        t = s.add_target("Body", 48, 48)
        before = t.stack.active.image.copy()
        s.set_color((0, 0, 255, 255))
        s.on_trigger(0, 1.0, hit=self._hit(0.5, 0.5))
        s.on_move(0, hit=self._hit(0.6, 0.6))
        s.on_trigger(0, 0.0)
        after = t.stack.active.image.copy()
        self.assertIsNotNone(s.undo())
        self.assertEqual(t.stack.active.image.data, before.data)
        self.assertIsNotNone(s.redo())
        self.assertEqual(t.stack.active.image.data, after.data)

    def test_thumbstick_changes_the_radius(self):
        s = PaintSession(mode="TEXTURE")
        r0 = s.ui.brush.radius
        for _ in range(30):
            s.on_thumbstick(0, 1.0, 0.0)
        self.assertGreater(s.ui.brush.radius, r0)

    def test_ui_widget_routing(self):
        s = PaintSession(mode="TEXTURE")
        st = s.add_target("Body", 16, 16).stack
        s.on_ui_widget("layer_add")
        self.assertEqual(len(st), 2)
        s.on_ui_widget("tool_airbrush")
        self.assertEqual(s.ui.tool, "airbrush")
        self.assertIs(s.painter.params, s.ui.brush)
        s.on_ui_widget("blend_multiply")
        self.assertEqual(s.ui.brush.blend, "multiply")
        s.on_ui_widget("mode_VECTOR")
        self.assertEqual(s.mode, "VECTOR")

    def test_active_layer_stack_is_none_without_a_target(self):
        self.assertIsNone(PaintSession().active_layer_stack())

    def test_detach_is_safe_without_a_scenegraph(self):
        s = PaintSession(mode="TEXTURE")
        s.bind_viewer(None)
        self.assertIsNone(s.attach_scenegraph(None))
        s.detach()
        self.assertIsNone(s.root)

    def test_update_without_controllers(self):
        s = PaintSession(mode="TEXTURE")
        self.assertFalse(s.update(0.016, []))
        self.assertFalse(s.update(0.016, None))

    def test_update_with_a_fake_controller(self):
        class FakeState(object):
            grab = 1.0
            lever_x = 0.0
            lever_y = 0.0

        class FakeController(object):
            def get_buttons_states(self):
                return FakeState()

            def get_global_transf(self):
                raise RuntimeError("no Coin here")

        s = PaintSession(mode="STROKE3D")
        # no pose is resolvable, so nothing happens, but nothing may crash
        self.assertFalse(s.update(0.016, [FakeController()]))


# ==========================================================================
# ui state machine
# ==========================================================================

class TestUiState(unittest.TestCase):

    def test_colour_conversions_roundtrip(self):
        for hsv in ((0.0, 0.0, 0.0), (0.3, 0.7, 0.9), (0.99, 1.0, 1.0),
                    (0.5, 0.0, 0.5)):
            rgb = ui.hsv_to_rgb(*hsv)
            back = ui.rgb_to_hsv(*rgb)
            if hsv[1] > 0.0 and hsv[2] > 0.0:
                self.assertAlmostEqual(back[0], hsv[0], places=6)
            self.assertAlmostEqual(back[1], hsv[1], places=6)
            self.assertAlmostEqual(back[2], hsv[2], places=6)

    def test_wheel_pick_and_position_are_inverse(self):
        w = ui.ColorWheel(0.06, 5, 24)
        for hue, sat in ((0.0, 1.0), (0.25, 0.5), (0.8, 0.75)):
            x, y = w.position_of(hue, sat)
            got = w.pick(x, y)
            self.assertIsNotNone(got)
            self.assertAlmostEqual(got[0], hue, places=6)
            self.assertAlmostEqual(got[1], sat, places=6)
        self.assertIsNone(w.pick(0.2, 0.2))

    def test_wheel_geometry_counts(self):
        w = ui.ColorWheel(0.05, 4, 12)
        pts, cols = w.vertices()
        self.assertEqual(len(pts), 1 + 4 * 12)
        self.assertEqual(len(cols), len(pts))
        faces = w.faces()
        self.assertEqual(len(faces), 12 + 3 * 12)
        for f in faces:
            for i in f:
                self.assertLess(i, len(pts))

    def test_swatches_are_bounded_and_deduplicated(self):
        st = ui.PaintUiState(max_swatches=3)
        for h in (0.0, 0.1, 0.2, 0.3):
            st.set_color_hsv(h, 1.0, 1.0)
            st.push_swatch()
        self.assertEqual(len(st.swatches), 3)
        st.set_color_hsv(0.3, 1.0, 1.0)
        st.push_swatch()
        self.assertEqual(len(st.swatches), 3)

    def test_use_swatch_restores_the_colour(self):
        st = ui.PaintUiState()
        st.set_color_rgb(1.0, 0.0, 0.0)
        st.push_swatch()
        st.set_color_rgb(0.0, 0.0, 1.0)
        st.use_swatch(0)
        self.assertEqual(st.color_rgba255, (255, 0, 0, 255))
        self.assertIsNone(st.use_swatch(9))

    def test_radius_slider_is_logarithmic_and_invertible(self):
        st = ui.PaintUiState()
        for v in (0.0, 0.25, 0.5, 1.0):
            st.set_radius(v, normalised=True)
            self.assertAlmostEqual(st.radius_normalised(), v, places=6)
        st.set_radius(0.0, normalised=True)
        self.assertAlmostEqual(st.brush.radius, ui.MIN_RADIUS_PX)
        st.set_radius(1.0, normalised=True)
        self.assertAlmostEqual(st.brush.radius, ui.MAX_RADIUS_PX)

    def test_widget_dispatch(self):
        st = ui.PaintUiState()
        self.assertEqual(st.on_widget("mode_VECTOR").value, "VECTOR")
        self.assertEqual(st.on_widget("tool_marker").value, "marker")
        self.assertEqual(st.on_widget("vtool_pen").value, "pen")
        self.assertEqual(st.on_widget("hardness_slider", 0.4).value, 0.4)
        self.assertAlmostEqual(st.brush.hardness, 0.4)
        self.assertIsNone(st.on_widget("tool_nonsense"))
        self.assertIsNone(st.on_widget(""))
        self.assertTrue(st.on_widget("toggle_layers").value)
        self.assertFalse(st.on_widget("toggle_layers").value)

    def test_selecting_a_preset_keeps_the_radius(self):
        st = ui.PaintUiState()
        st.set_radius(77.0)
        st.select_tool("chisel")
        self.assertAlmostEqual(st.brush.radius, 77.0)
        self.assertEqual(st.brush.kind, "chisel")

    def test_layer_panel_rows_and_actions(self):
        stack = layers.LayerStack(8, 8)
        stack.add_layer("A")
        stack.add_layer("B")
        panel = ui.LayerPanelModel(stack)
        rows = panel.rows()
        self.assertEqual([r["name"] for r in rows], ["B", "A"])
        self.assertTrue(rows[0]["active"])
        panel.apply(ui.UiAction("layer_visible", None, 0))
        self.assertFalse(stack[0].visible)
        panel.apply(ui.UiAction("layer_add"))
        self.assertEqual(len(stack), 3)
        panel.apply(ui.UiAction("layer_down", None, 2))
        self.assertEqual(stack.active_index, 1)
        self.assertFalse(panel.apply(None))

    def test_layer_panel_never_removes_the_last_layer(self):
        stack = layers.LayerStack(4, 4)
        stack.add_layer("only")
        panel = ui.LayerPanelModel(stack)
        self.assertFalse(panel.apply(ui.UiAction("layer_remove", None, 0)))
        self.assertEqual(len(stack), 1)


class TestPrefs(unittest.TestCase):

    def test_defaults_without_freecad(self):
        from xrpaint import prefs
        prefs.clear_overrides()
        self.assertEqual(prefs.get_int("TextureSize"), 2048)
        self.assertAlmostEqual(prefs.get_float("BrushRadius"), 4.0)
        self.assertEqual(prefs.get_string("BlendMode"), "normal")
        self.assertTrue(prefs.get_bool("PressureEnabled"))
        self.assertTrue(prefs.get_bool("AutoUV"))
        self.assertEqual(prefs.get_int("PaintUndoSteps"), 32)

    def test_get_dispatches_on_the_default_type(self):
        from xrpaint import prefs
        prefs.clear_overrides()
        self.assertIsInstance(prefs.get("TextureSize"), int)
        self.assertIsInstance(prefs.get("BrushRadius"), float)
        self.assertIsInstance(prefs.get("PressureEnabled"), bool)
        self.assertIsInstance(prefs.get("BlendMode"), str)

    def test_overrides(self):
        from xrpaint import prefs
        try:
            prefs.set_override("TextureSize", 512)
            self.assertEqual(prefs.get_int("TextureSize"), 512)
            t = texture_paint.PaintTarget("X")
            self.assertEqual(t.width, 512)
        finally:
            prefs.clear_overrides()


class TestPackageApi(unittest.TestCase):

    def test_public_names_exist(self):
        import xrpaint
        for name in xrpaint.__all__:
            self.assertTrue(hasattr(xrpaint, name), name)

    def test_no_coin_or_freecad_at_import_time(self):
        for mod in ("pivy", "pivy.coin", "FreeCAD", "FreeCADGui", "Draft",
                    "Part", "Mesh"):
            self.assertNotIn(mod, sys.modules,
                             "%s must not be imported by xrpaint" % mod)


if __name__ == "__main__":
    unittest.main()
