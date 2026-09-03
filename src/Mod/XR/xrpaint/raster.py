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
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# ***************************************************************************
"""Dependency free RGBA8 raster image buffer for the XR paint subsystem.

The buffer is a plain :class:`bytearray` of ``width * height * 4`` bytes in
straight (non premultiplied) RGBA order.  Everything in this module works with
the standard library alone; when :mod:`numpy` happens to be importable it is
used to accelerate the inner loops.  **Both code paths are required to produce
byte identical results** -- the arithmetic below is written so that the scalar
and the vectorised version evaluate the very same IEEE-754 double expressions
in the very same order.

Nothing here imports ``pivy``, ``FreeCAD`` or ``FreeCADGui``.
"""

import math
import struct
import zlib

__all__ = [
    "BLEND_MODES",
    "Image",
    "Mask",
    "blend_pixel",
    "blit_brush",
    "composite",
    "decode_png",
    "encode_png",
    "have_numpy",
    "set_use_numpy",
    "use_numpy",
]


BLEND_MODES = ("normal", "multiply", "add", "screen", "erase")

# --------------------------------------------------------------------------
# optional numpy acceleration
# --------------------------------------------------------------------------

_NUMPY = None
_NUMPY_PROBED = False
_USE_NUMPY = True


def _numpy():
    """Return the numpy module or ``None``; probed lazily, once."""
    global _NUMPY, _NUMPY_PROBED
    if not _NUMPY_PROBED:
        _NUMPY_PROBED = True
        try:
            import numpy as _np  # noqa: F401
            _NUMPY = _np
        except Exception:
            _NUMPY = None
    return _NUMPY


def have_numpy():
    """True when numpy is importable in this interpreter."""
    return _numpy() is not None


def use_numpy():
    """True when the accelerated path is both available and enabled."""
    return _USE_NUMPY and _numpy() is not None


def set_use_numpy(enabled):
    """Enable/disable the numpy path (tests use this to compare both)."""
    global _USE_NUMPY
    old = _USE_NUMPY
    _USE_NUMPY = bool(enabled)
    return old


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _u8(v):
    """Round a non negative float to the nearest byte, saturating."""
    i = int(v + 0.5)
    if i < 0:
        return 0
    if i > 255:
        return 255
    return i


def _as_rgba(color):
    """Accept 3 or 4 component colours, ints 0..255 or floats 0..1."""
    if color is None:
        return (0, 0, 0, 0)
    c = list(color)
    if len(c) == 3:
        c.append(1.0 if _looks_float(c) else 255)
    if _looks_float(c):
        return tuple(_u8(_clamp(float(x), 0.0, 1.0) * 255.0) for x in c)
    return tuple(int(_clamp(int(x), 0, 255)) for x in c)


def _looks_float(c):
    for x in c:
        if isinstance(x, float):
            return True
    return False


# --------------------------------------------------------------------------
# blending
# --------------------------------------------------------------------------

def _blend_rgb(mode, s, d):
    """Per channel blend function, both arguments and result are 0..255."""
    if mode == "normal" or mode == "erase":
        return s
    if mode == "multiply":
        return s * d / 255.0
    if mode == "screen":
        return 255.0 - (255.0 - s) * (255.0 - d) / 255.0
    if mode == "add":
        v = s + d
        return 255.0 if v > 255.0 else v
    raise ValueError("unknown blend mode: %r" % (mode,))


def blend_pixel(dst, src, alpha, mode="normal"):
    """Blend one straight-alpha RGBA pixel over another.

    ``dst``/``src`` are 4-tuples of ints 0..255, ``alpha`` an extra 0..1
    coverage multiplier (brush mask * flow * opacity).  Returns a 4-tuple.

    The model is "blend the colours, then source-over composite", which is what
    every layer based paint program does.
    """
    sr, sg, sb, sa = src
    dr, dg, db, da8 = dst
    a = sa * alpha / 255.0
    if a <= 0.0:
        return (dr, dg, db, da8)
    da = da8 / 255.0
    if mode == "erase":
        oa = da * (1.0 - a)
        return (dr, dg, db, _u8(oa * 255.0))
    br = _blend_rgb(mode, float(sr), float(dr))
    bg = _blend_rgb(mode, float(sg), float(dg))
    bb = _blend_rgb(mode, float(sb), float(db))
    inv = da * (1.0 - a)
    oa = a + inv
    if oa <= 0.0:
        return (0, 0, 0, 0)
    return (
        _u8((br * a + dr * inv) / oa),
        _u8((bg * a + dg * inv) / oa),
        _u8((bb * a + db * inv) / oa),
        _u8(oa * 255.0),
    )


# --------------------------------------------------------------------------
# Mask -- an 8 bit coverage stamp
# --------------------------------------------------------------------------

class Mask(object):
    """An 8 bit coverage bitmap, used as a brush stamp."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width, height, data=None):
        width = int(width)
        height = int(height)
        if width < 0 or height < 0:
            raise ValueError("negative mask size")
        self.width = width
        self.height = height
        n = width * height
        if data is None:
            self.data = bytearray(n)
        else:
            if len(data) != n:
                raise ValueError("mask data length mismatch")
            self.data = bytearray(data)

    def get(self, x, y):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return 0
        return self.data[y * self.width + x]

    def set(self, x, y, v):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.data[y * self.width + x] = _clamp(int(v), 0, 255)

    def coverage(self):
        """Sum of all coverage values divided by 255 (in 'full pixels')."""
        return sum(self.data) / 255.0

    def copy(self):
        return Mask(self.width, self.height, self.data)

    def __eq__(self, other):
        return (isinstance(other, Mask) and other.width == self.width
                and other.height == self.height and other.data == self.data)

    def __repr__(self):
        return "Mask(%d, %d)" % (self.width, self.height)


# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------

class Image(object):
    """A straight-alpha RGBA8 image backed by a ``bytearray``."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width, height, color=None, data=None):
        width = int(width)
        height = int(height)
        if width <= 0 or height <= 0:
            raise ValueError("image size must be positive")
        self.width = width
        self.height = height
        n = width * height * 4
        if data is not None:
            if len(data) != n:
                raise ValueError("image data length mismatch: %d != %d"
                                 % (len(data), n))
            self.data = bytearray(data)
        elif color is None:
            self.data = bytearray(n)
        else:
            self.data = bytearray(n)
            self.fill(color)

    # -- basics ----------------------------------------------------------
    @property
    def size(self):
        return (self.width, self.height)

    def copy(self):
        return Image(self.width, self.height, data=self.data)

    def clear(self):
        self.data = bytearray(self.width * self.height * 4)

    def fill(self, color, rect=None):
        """Fill the whole image or ``rect`` = (x0, y0, x1, y1) exclusive."""
        r, g, b, a = _as_rgba(color)
        px = bytes((r, g, b, a))
        if rect is None:
            self.data[:] = px * (self.width * self.height)
            return (0, 0, self.width, self.height)
        x0, y0, x1, y1 = self._clip_rect(rect)
        if x0 >= x1 or y0 >= y1:
            return None
        row = px * (x1 - x0)
        stride = self.width * 4
        for y in range(y0, y1):
            off = y * stride + x0 * 4
            self.data[off:off + len(row)] = row
        return (x0, y0, x1, y1)

    def _clip_rect(self, rect):
        x0, y0, x1, y1 = rect
        x0 = _clamp(int(x0), 0, self.width)
        y0 = _clamp(int(y0), 0, self.height)
        x1 = _clamp(int(x1), 0, self.width)
        y1 = _clamp(int(y1), 0, self.height)
        return (x0, y0, x1, y1)

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def get_pixel(self, x, y):
        if not self.in_bounds(x, y):
            return (0, 0, 0, 0)
        o = (y * self.width + x) * 4
        d = self.data
        return (d[o], d[o + 1], d[o + 2], d[o + 3])

    def set_pixel(self, x, y, color):
        if not self.in_bounds(x, y):
            return
        r, g, b, a = _as_rgba(color)
        o = (y * self.width + x) * 4
        d = self.data
        d[o] = r
        d[o + 1] = g
        d[o + 2] = b
        d[o + 3] = a

    def blend_pixel(self, x, y, color, alpha=1.0, mode="normal"):
        if not self.in_bounds(x, y):
            return
        o = (y * self.width + x) * 4
        d = self.data
        dst = (d[o], d[o + 1], d[o + 2], d[o + 3])
        r, g, b, a = blend_pixel(dst, _as_rgba(color), alpha, mode)
        d[o] = r
        d[o + 1] = g
        d[o + 2] = b
        d[o + 3] = a

    # -- sub image -------------------------------------------------------
    def crop(self, rect):
        x0, y0, x1, y1 = self._clip_rect(rect)
        w = max(0, x1 - x0)
        h = max(0, y1 - y0)
        if w == 0 or h == 0:
            return Image(1, 1)
        out = Image(w, h)
        stride = self.width * 4
        ostride = w * 4
        for y in range(h):
            so = (y0 + y) * stride + x0 * 4
            oo = y * ostride
            out.data[oo:oo + ostride] = self.data[so:so + ostride]
        return out

    def paste(self, src, x, y):
        """Copy ``src`` over this image at (x, y), no blending."""
        x = int(x)
        y = int(y)
        sx0 = max(0, -x)
        sy0 = max(0, -y)
        sx1 = min(src.width, self.width - x)
        sy1 = min(src.height, self.height - y)
        if sx0 >= sx1 or sy0 >= sy1:
            return None
        stride = self.width * 4
        sstride = src.width * 4
        n = (sx1 - sx0) * 4
        for sy in range(sy0, sy1):
            so = sy * sstride + sx0 * 4
            do = (y + sy) * stride + (x + sx0) * 4
            self.data[do:do + n] = src.data[so:so + n]
        return (x + sx0, y + sy0, x + sx1, y + sy1)

    # -- sampling --------------------------------------------------------
    def sample_nearest(self, u, v, wrap=False):
        x = int(math.floor(u * self.width))
        y = int(math.floor(v * self.height))
        if wrap:
            x %= self.width
            y %= self.height
        else:
            x = _clamp(x, 0, self.width - 1)
            y = _clamp(y, 0, self.height - 1)
        return self.get_pixel(x, y)

    def sample_bilinear(self, u, v, wrap=False):
        """Bilinear sample at normalised (u, v).  Returns 4 floats 0..255.

        Pixel centres sit at ``(i + 0.5) / width``, the usual texture
        convention, so ``sample_bilinear(0.5 / w, 0.5 / h)`` returns pixel
        (0, 0) exactly.
        """
        fx = u * self.width - 0.5
        fy = v * self.height - 0.5
        x0 = int(math.floor(fx))
        y0 = int(math.floor(fy))
        tx = fx - x0
        ty = fy - y0
        x1 = x0 + 1
        y1 = y0 + 1
        if wrap:
            x0 %= self.width
            x1 %= self.width
            y0 %= self.height
            y1 %= self.height
        else:
            x0 = _clamp(x0, 0, self.width - 1)
            x1 = _clamp(x1, 0, self.width - 1)
            y0 = _clamp(y0, 0, self.height - 1)
            y1 = _clamp(y1, 0, self.height - 1)
        p00 = self.get_pixel(x0, y0)
        p10 = self.get_pixel(x1, y0)
        p01 = self.get_pixel(x0, y1)
        p11 = self.get_pixel(x1, y1)
        out = []
        for i in range(4):
            top = p00[i] + (p10[i] - p00[i]) * tx
            bot = p01[i] + (p11[i] - p01[i]) * tx
            out.append(top + (bot - top) * ty)
        return tuple(out)

    def sample_bilinear_px(self, x, y, wrap=False):
        """Bilinear sample in pixel coordinates (pixel centre at i + 0.5)."""
        return self.sample_bilinear((x + 0.5) / self.width,
                                    (y + 0.5) / self.height, wrap)

    # -- mip chain -------------------------------------------------------
    def downsample_box(self):
        """Half sized box filtered copy; correct for odd (non POT) sizes."""
        nw = max(1, (self.width + 1) // 2)
        nh = max(1, (self.height + 1) // 2)
        out = Image(nw, nh)
        np_ = _numpy() if use_numpy() else None
        if np_ is not None:
            a = np_.frombuffer(bytes(self.data), dtype=np_.uint8)
            a = a.reshape(self.height, self.width, 4).astype(np_.float64)
            # pad odd dimensions by repeating the last row/column so that the
            # scalar and vector paths average exactly the same samples
            if self.width % 2:
                a = np_.concatenate([a, a[:, -1:, :]], axis=1)
            if self.height % 2:
                a = np_.concatenate([a, a[-1:, :, :]], axis=0)
            b = a.reshape(nh, 2, nw, 2, 4)
            acc = b[:, 0, :, 0, :] + b[:, 0, :, 1, :]
            acc = acc + b[:, 1, :, 0, :] + b[:, 1, :, 1, :]
            res = np_.floor(acc / 4.0 + 0.5)
            out.data[:] = res.astype(np_.uint8).tobytes()
            return out
        w = self.width
        h = self.height
        get = self.get_pixel
        for y in range(nh):
            sy0 = 2 * y
            sy1 = min(sy0 + 1, h - 1)
            for x in range(nw):
                sx0 = 2 * x
                sx1 = min(sx0 + 1, w - 1)
                p0 = get(sx0, sy0)
                p1 = get(sx1, sy0)
                p2 = get(sx0, sy1)
                p3 = get(sx1, sy1)
                o = (y * nw + x) * 4
                for i in range(4):
                    acc = float(p0[i]) + float(p1[i])
                    acc = acc + float(p2[i]) + float(p3[i])
                    out.data[o + i] = _u8(acc / 4.0)
        return out

    def mipmaps(self, levels=None):
        """Full box filtered mip chain, level 0 is a copy of this image."""
        chain = [self.copy()]
        while chain[-1].width > 1 or chain[-1].height > 1:
            if levels is not None and len(chain) >= levels:
                break
            chain.append(chain[-1].downsample_box())
        return chain

    # -- interop ---------------------------------------------------------
    def tobytes(self):
        return bytes(self.data)

    @classmethod
    def frombytes(cls, width, height, data):
        return cls(width, height, data=data)

    def to_png(self, **kw):
        return encode_png(self, **kw)

    @classmethod
    def from_png(cls, blob):
        return decode_png(blob)

    def __eq__(self, other):
        return (isinstance(other, Image) and other.width == self.width
                and other.height == self.height and other.data == self.data)

    def __repr__(self):
        return "Image(%d, %d)" % (self.width, self.height)


# --------------------------------------------------------------------------
# brush blitting
# --------------------------------------------------------------------------

def blit_brush(dst, mask, cx, cy, color, mode="normal", opacity=1.0,
               flow=1.0):
    """Stamp ``mask`` onto ``dst`` centred on (cx, cy) in ``color``.

    ``cx``/``cy`` are float pixel coordinates of the stamp centre, the stamp is
    snapped to the nearest whole pixel.  Returns the dirty rectangle
    ``(x0, y0, x1, y1)`` (x1/y1 exclusive) or ``None`` when fully clipped.
    """
    if mask.width <= 0 or mask.height <= 0:
        return None
    alpha = float(opacity) * float(flow)
    if alpha <= 0.0:
        return None
    ox = int(math.floor(cx - (mask.width - 1) * 0.5 + 0.5))
    oy = int(math.floor(cy - (mask.height - 1) * 0.5 + 0.5))
    return blit_mask(dst, mask, ox, oy, color, mode, alpha)


def blit_mask(dst, mask, ox, oy, color, mode="normal", alpha=1.0):
    """Stamp ``mask`` with its top-left corner at integer (ox, oy)."""
    if mode not in BLEND_MODES:
        raise ValueError("unknown blend mode: %r" % (mode,))
    x0 = max(0, ox)
    y0 = max(0, oy)
    x1 = min(dst.width, ox + mask.width)
    y1 = min(dst.height, oy + mask.height)
    if x0 >= x1 or y0 >= y1:
        return None
    src = _as_rgba(color)
    np_ = _numpy() if use_numpy() else None
    if np_ is not None:
        _blit_mask_numpy(np_, dst, mask, ox, oy, x0, y0, x1, y1, src, mode,
                         float(alpha))
    else:
        _blit_mask_scalar(dst, mask, ox, oy, x0, y0, x1, y1, src, mode,
                          float(alpha))
    return (x0, y0, x1, y1)


def _blit_mask_scalar(dst, mask, ox, oy, x0, y0, x1, y1, src, mode, alpha):
    sr, sg, sb, sa = src
    d = dst.data
    md = mask.data
    dstride = dst.width * 4
    mstride = mask.width
    erase = (mode == "erase")
    for y in range(y0, y1):
        mrow = (y - oy) * mstride
        drow = y * dstride
        for x in range(x0, x1):
            m = md[mrow + (x - ox)]
            if m == 0:
                continue
            a = sa * (m / 255.0) * alpha / 255.0
            if a <= 0.0:
                continue
            o = drow + x * 4
            dr = d[o]
            dg = d[o + 1]
            db = d[o + 2]
            da = d[o + 3] / 255.0
            if erase:
                d[o + 3] = _u8(da * (1.0 - a) * 255.0)
                continue
            inv = da * (1.0 - a)
            oa = a + inv
            if oa <= 0.0:
                d[o] = 0
                d[o + 1] = 0
                d[o + 2] = 0
                d[o + 3] = 0
                continue
            br = _blend_rgb(mode, float(sr), float(dr))
            bg = _blend_rgb(mode, float(sg), float(dg))
            bb = _blend_rgb(mode, float(sb), float(db))
            d[o] = _u8((br * a + dr * inv) / oa)
            d[o + 1] = _u8((bg * a + dg * inv) / oa)
            d[o + 2] = _u8((bb * a + db * inv) / oa)
            d[o + 3] = _u8(oa * 255.0)


def _blend_rgb_np(np_, mode, s, d):
    if mode == "normal" or mode == "erase":
        return s
    if mode == "multiply":
        return s * d / 255.0
    if mode == "screen":
        return 255.0 - (255.0 - s) * (255.0 - d) / 255.0
    if mode == "add":
        return np_.minimum(s + d, 255.0)
    raise ValueError("unknown blend mode: %r" % (mode,))


def _blit_mask_numpy(np_, dst, mask, ox, oy, x0, y0, x1, y1, src, mode, alpha):
    sr, sg, sb, sa = src
    dw = dst.width
    darr = np_.frombuffer(memoryview(dst.data), dtype=np_.uint8)
    darr = darr.reshape(dst.height, dw, 4)
    sub = darr[y0:y1, x0:x1, :]
    marr = np_.frombuffer(memoryview(mask.data), dtype=np_.uint8)
    marr = marr.reshape(mask.height, mask.width)
    msub = marr[y0 - oy:y1 - oy, x0 - ox:x1 - ox].astype(np_.float64)

    a = sa * (msub / 255.0) * alpha / 255.0
    da = sub[:, :, 3].astype(np_.float64) / 255.0
    touched = a > 0.0
    if not touched.any():
        return
    if mode == "erase":
        oa = da * (1.0 - a)
        newa = np_.floor(oa * 255.0 + 0.5)
        newa = np_.clip(newa, 0.0, 255.0)
        cur = sub[:, :, 3].astype(np_.float64)
        sub[:, :, 3] = np_.where(touched, newa, cur).astype(np_.uint8)
        return
    dr = sub[:, :, 0].astype(np_.float64)
    dg = sub[:, :, 1].astype(np_.float64)
    db = sub[:, :, 2].astype(np_.float64)
    inv = da * (1.0 - a)
    oa = a + inv
    br = _blend_rgb_np(np_, mode, np_.full(dr.shape, float(sr)), dr)
    bg = _blend_rgb_np(np_, mode, np_.full(dg.shape, float(sg)), dg)
    bb = _blend_rgb_np(np_, mode, np_.full(db.shape, float(sb)), db)
    safe = np_.where(oa > 0.0, oa, 1.0)
    zero = (oa <= 0.0)
    out = []
    for bc, dc in ((br, dr), (bg, dg), (bb, db)):
        v = (bc * a + dc * inv) / safe
        v = np_.floor(v + 0.5)
        v = np_.clip(v, 0.0, 255.0)
        v = np_.where(zero, 0.0, v)
        out.append(v)
    va = np_.floor(oa * 255.0 + 0.5)
    va = np_.clip(va, 0.0, 255.0)
    va = np_.where(zero, 0.0, va)
    out.append(va)
    for i in range(4):
        cur = sub[:, :, i].astype(np_.float64)
        sub[:, :, i] = np_.where(touched, out[i], cur).astype(np_.uint8)


def composite(bottom, top, opacity=1.0, mode="normal", out=None, rect=None):
    """Composite ``top`` over ``bottom`` (same size) into ``out``.

    ``out`` may be ``bottom`` itself for an in-place composite.
    """
    if bottom.width != top.width or bottom.height != top.height:
        raise ValueError("composite requires equally sized images")
    if out is None:
        out = bottom.copy()
    elif out is not bottom:
        out.data[:] = bottom.data
    if rect is None:
        rect = (0, 0, bottom.width, bottom.height)
    x0, y0, x1, y1 = out._clip_rect(rect)
    if x0 >= x1 or y0 >= y1:
        return out
    alpha = float(opacity)
    if alpha <= 0.0:
        return out
    np_ = _numpy() if use_numpy() else None
    if np_ is not None:
        _composite_numpy(np_, out, top, x0, y0, x1, y1, alpha, mode)
    else:
        _composite_scalar(out, top, x0, y0, x1, y1, alpha, mode)
    return out


def _composite_scalar(out, top, x0, y0, x1, y1, alpha, mode):
    d = out.data
    s = top.data
    stride = out.width * 4
    erase = (mode == "erase")
    for y in range(y0, y1):
        row = y * stride
        for x in range(x0, x1):
            o = row + x * 4
            sa = s[o + 3]
            if sa == 0:
                continue
            a = sa * alpha / 255.0
            if a <= 0.0:
                continue
            dr = d[o]
            dg = d[o + 1]
            db = d[o + 2]
            da = d[o + 3] / 255.0
            if erase:
                d[o + 3] = _u8(da * (1.0 - a) * 255.0)
                continue
            inv = da * (1.0 - a)
            oa = a + inv
            if oa <= 0.0:
                d[o] = 0
                d[o + 1] = 0
                d[o + 2] = 0
                d[o + 3] = 0
                continue
            br = _blend_rgb(mode, float(s[o]), float(dr))
            bg = _blend_rgb(mode, float(s[o + 1]), float(dg))
            bb = _blend_rgb(mode, float(s[o + 2]), float(db))
            d[o] = _u8((br * a + dr * inv) / oa)
            d[o + 1] = _u8((bg * a + dg * inv) / oa)
            d[o + 2] = _u8((bb * a + db * inv) / oa)
            d[o + 3] = _u8(oa * 255.0)


def _composite_numpy(np_, out, top, x0, y0, x1, y1, alpha, mode):
    darr = np_.frombuffer(memoryview(out.data), dtype=np_.uint8)
    darr = darr.reshape(out.height, out.width, 4)
    sarr = np_.frombuffer(memoryview(top.data), dtype=np_.uint8)
    sarr = sarr.reshape(top.height, top.width, 4)
    sub = darr[y0:y1, x0:x1, :]
    ssub = sarr[y0:y1, x0:x1, :].astype(np_.float64)
    a = ssub[:, :, 3] * alpha / 255.0
    touched = a > 0.0
    if not touched.any():
        return
    da = sub[:, :, 3].astype(np_.float64) / 255.0
    if mode == "erase":
        oa = da * (1.0 - a)
        newa = np_.clip(np_.floor(oa * 255.0 + 0.5), 0.0, 255.0)
        cur = sub[:, :, 3].astype(np_.float64)
        sub[:, :, 3] = np_.where(touched, newa, cur).astype(np_.uint8)
        return
    dr = sub[:, :, 0].astype(np_.float64)
    dg = sub[:, :, 1].astype(np_.float64)
    db = sub[:, :, 2].astype(np_.float64)
    inv = da * (1.0 - a)
    oa = a + inv
    br = _blend_rgb_np(np_, mode, ssub[:, :, 0], dr)
    bg = _blend_rgb_np(np_, mode, ssub[:, :, 1], dg)
    bb = _blend_rgb_np(np_, mode, ssub[:, :, 2], db)
    safe = np_.where(oa > 0.0, oa, 1.0)
    zero = (oa <= 0.0)
    res = []
    for bc, dc in ((br, dr), (bg, dg), (bb, db)):
        v = (bc * a + dc * inv) / safe
        v = np_.clip(np_.floor(v + 0.5), 0.0, 255.0)
        res.append(np_.where(zero, 0.0, v))
    va = np_.clip(np_.floor(oa * 255.0 + 0.5), 0.0, 255.0)
    res.append(np_.where(zero, 0.0, va))
    for i in range(4):
        cur = sub[:, :, i].astype(np_.float64)
        sub[:, :, i] = np_.where(touched, res[i], cur).astype(np_.uint8)


# --------------------------------------------------------------------------
# minimal PNG codec (stdlib zlib + struct only)
# --------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _filter_row(ftype, raw, prev, bpp):
    n = len(raw)
    out = bytearray(n)
    if ftype == 0:
        out[:] = raw
    elif ftype == 1:
        for i in range(n):
            a = raw[i - bpp] if i >= bpp else 0
            out[i] = (raw[i] - a) & 0xFF
    elif ftype == 2:
        for i in range(n):
            out[i] = (raw[i] - prev[i]) & 0xFF
    elif ftype == 3:
        for i in range(n):
            a = raw[i - bpp] if i >= bpp else 0
            out[i] = (raw[i] - ((a + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:
        for i in range(n):
            a = raw[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            out[i] = (raw[i] - _paeth(a, prev[i], c)) & 0xFF
    else:
        raise ValueError("bad PNG filter type %r" % (ftype,))
    return out


def _unfilter_row(ftype, row, prev, bpp):
    n = len(row)
    out = bytearray(row)
    if ftype == 0:
        return out
    if ftype == 1:
        for i in range(bpp, n):
            out[i] = (out[i] + out[i - bpp]) & 0xFF
        return out
    if ftype == 2:
        for i in range(n):
            out[i] = (out[i] + prev[i]) & 0xFF
        return out
    if ftype == 3:
        for i in range(n):
            a = out[i - bpp] if i >= bpp else 0
            out[i] = (out[i] + ((a + prev[i]) >> 1)) & 0xFF
        return out
    if ftype == 4:
        for i in range(n):
            a = out[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            out[i] = (out[i] + _paeth(a, prev[i], c)) & 0xFF
        return out
    raise ValueError("bad PNG filter type %r" % (ftype,))


def encode_png(image, level=6, filter_type=None):
    """Encode an :class:`Image` as an 8 bit RGBA PNG (``bytes``).

    ``filter_type`` selects a fixed PNG row filter 0..4; ``None`` picks one per
    row with the standard minimum-sum-of-absolute-differences heuristic.
    """
    w = image.width
    h = image.height
    stride = w * 4
    bpp = 4
    raw = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        row = image.data[y * stride:(y + 1) * stride]
        if filter_type is None:
            best = None
            best_score = None
            for ft in (0, 1, 2, 3, 4):
                cand = _filter_row(ft, row, prev, bpp)
                score = 0
                for v in cand:
                    score += v if v < 128 else 256 - v
                if best_score is None or score < best_score:
                    best_score = score
                    best = (ft, cand)
            ft, cand = best
        else:
            ft = int(filter_type)
            cand = _filter_row(ft, row, prev, bpp)
        raw.append(ft)
        raw.extend(cand)
        prev = row
    out = bytearray(_PNG_MAGIC)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    out += _chunk(b"IHDR", ihdr)
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), level))
    out += _chunk(b"IEND", b"")
    return bytes(out)


def decode_png(blob):
    """Decode a non interlaced PNG into an RGBA8 :class:`Image`.

    Supports bit depth 8 and 16 and colour types 0 (grey), 2 (RGB),
    3 (palette), 4 (grey+alpha) and 6 (RGBA), plus ``tRNS`` transparency.
    """
    if len(blob) < 8 or bytes(blob[:8]) != _PNG_MAGIC:
        raise ValueError("not a PNG file")
    pos = 8
    width = height = depth = ctype = None
    interlace = 0
    idat = bytearray()
    palette = None
    trns = None
    blob = bytes(blob)
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        payload = blob[pos + 8:pos + 8 + length]
        crc_off = pos + 8 + length
        if crc_off + 4 > len(blob):
            raise ValueError("truncated PNG chunk %r" % (tag,))
        (crc,) = struct.unpack(">I", blob[crc_off:crc_off + 4])
        if crc != (zlib.crc32(tag + payload) & 0xFFFFFFFF):
            raise ValueError("PNG CRC mismatch in chunk %r" % (tag,))
        pos = crc_off + 4
        if tag == b"IHDR":
            (width, height, depth, ctype, comp, filt,
             interlace) = struct.unpack(">IIBBBBB", payload)
            if comp != 0 or filt != 0:
                raise ValueError("unsupported PNG compression/filter")
            if interlace != 0:
                raise ValueError("interlaced PNG is not supported")
            if depth not in (8, 16):
                if not (ctype == 3 and depth in (1, 2, 4)):
                    raise ValueError("unsupported PNG bit depth %d" % depth)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            trns = payload
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None:
        raise ValueError("PNG without IHDR")
    raw = zlib.decompress(bytes(idat))
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise ValueError("unsupported PNG colour type %d" % ctype)
    bits_per_px = channels * depth
    stride = (width * bits_per_px + 7) // 8
    bpp = max(1, bits_per_px // 8)
    img = Image(width, height)
    prev = bytearray(stride)
    off = 0
    for y in range(height):
        if off >= len(raw):
            raise ValueError("truncated PNG image data")
        ftype = raw[off]
        off += 1
        row = raw[off:off + stride]
        if len(row) != stride:
            raise ValueError("truncated PNG scanline")
        off += stride
        row = _unfilter_row(ftype, row, prev, bpp)
        prev = row
        _expand_row(img, row, y, width, depth, ctype, palette, trns)
    return img


def _expand_row(img, row, y, width, depth, ctype, palette, trns):
    d = img.data
    base = y * width * 4
    if depth == 16:
        def comp(i):
            return row[2 * i]
        step16 = True
    else:
        step16 = False

    if ctype == 3:
        if palette is None:
            raise ValueError("indexed PNG without PLTE")
        for x in range(width):
            if depth == 8:
                idx = row[x]
            else:
                per = 8 // depth
                byte = row[x // per]
                shift = 8 - depth * (x % per + 1)
                idx = (byte >> shift) & ((1 << depth) - 1)
            o = base + x * 4
            d[o] = palette[idx * 3]
            d[o + 1] = palette[idx * 3 + 1]
            d[o + 2] = palette[idx * 3 + 2]
            if trns is not None and idx < len(trns):
                d[o + 3] = trns[idx]
            else:
                d[o + 3] = 255
        return

    nch = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    for x in range(width):
        o = base + x * 4
        if step16:
            vals = [row[(x * nch + c) * 2] for c in range(nch)]
        else:
            vals = [row[x * nch + c] for c in range(nch)]
        if ctype == 0:
            g = vals[0]
            d[o] = d[o + 1] = d[o + 2] = g
            d[o + 3] = 255
        elif ctype == 4:
            g = vals[0]
            d[o] = d[o + 1] = d[o + 2] = g
            d[o + 3] = vals[1]
        elif ctype == 2:
            d[o] = vals[0]
            d[o + 1] = vals[1]
            d[o + 2] = vals[2]
            d[o + 3] = 255
        else:
            d[o] = vals[0]
            d[o + 1] = vals[1]
            d[o + 2] = vals[2]
            d[o + 3] = vals[3]
    if ctype in (0, 2) and trns is not None:
        _apply_trns(img, row, y, width, depth, ctype, trns)


def _apply_trns(img, row, y, width, depth, ctype, trns):
    d = img.data
    base = y * width * 4
    step = 2 if depth == 16 else 1
    if ctype == 0:
        (key,) = struct.unpack(">H", trns[:2])
        if depth == 8:
            key &= 0xFF
        for x in range(width):
            v = row[x * step] if depth == 8 else struct.unpack(
                ">H", bytes(row[x * 2:x * 2 + 2]))[0]
            if v == key:
                d[base + x * 4 + 3] = 0
    else:
        keys = struct.unpack(">HHH", trns[:6])
        for x in range(width):
            if depth == 8:
                v = (row[x * 3], row[x * 3 + 1], row[x * 3 + 2])
                k = tuple(c & 0xFF for c in keys)
            else:
                v = struct.unpack(">HHH", bytes(row[x * 6:x * 6 + 6]))
                k = keys
            if v == k:
                d[base + x * 4 + 3] = 0
