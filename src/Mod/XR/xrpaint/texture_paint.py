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
"""Texture painting bound to a FreeCAD object.

A controller ray hit (position, surface normal, texture coordinates) is turned
into texture space stamps, painted into the active layer of a per object layer
stack, composited, dilated past the UV seams and pushed into the Coin
scenegraph as an ``SoTexture2`` with an incremental dirty-rect upload.

``pivy.coin`` and ``FreeCAD`` are imported lazily inside functions, so the
whole geometry/pixel side of this module is unit-testable on its own.
"""

import math

from . import brush as _brush
from . import layers as _layers
from . import prefs as _prefs
from . import raster
from .raster import Image

__all__ = [
    "CoinTextureBridge",
    "PaintHit",
    "PaintTarget",
    "TexturePainter",
    "UVSet",
    "atlas_uv",
    "box_uv",
    "dilate_edges",
    "generate_uvs",
    "hit_from_controller",
    "pixel_to_uv",
    "planar_uv",
    "uv_to_pixel",
]

_EPS = 1e-12


# --------------------------------------------------------------------------
# uv <-> pixel
# --------------------------------------------------------------------------

def uv_to_pixel(uv, width, height, flip_v=True, wrap=True):
    """Texture coordinates to float pixel coordinates.

    ``v = 0`` is the *bottom* of the texture (the OpenGL/Coin convention) while
    row 0 of an :class:`~xrpaint.raster.Image` is the top, so ``flip_v``
    defaults to true.
    """
    u = float(uv[0])
    v = float(uv[1])
    if wrap:
        u = u - math.floor(u)
        v = v - math.floor(v)
    if flip_v:
        v = 1.0 - v
    return (u * width - 0.5, v * height - 0.5)


def pixel_to_uv(x, y, width, height, flip_v=True):
    """Inverse of :func:`uv_to_pixel` (pixel centres)."""
    u = (float(x) + 0.5) / width
    v = (float(y) + 0.5) / height
    if flip_v:
        v = 1.0 - v
    return (u, v)


# --------------------------------------------------------------------------
# seam dilation
# --------------------------------------------------------------------------

def dilate_edges(image, radius=2, rect=None, threshold=0):
    """Bleed opaque colour outwards into transparent pixels.

    UV islands do not tile: bilinear filtering right at an island border mixes
    painted texels with the empty background and shows a dark line along every
    seam.  Growing the painted area by a couple of texels removes it.

    Returns the affected rectangle or ``None``.
    """
    radius = int(radius)
    if radius <= 0:
        return None
    w = image.width
    h = image.height
    if rect is None:
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        x0, y0, x1, y1 = image._clip_rect(rect)
    # the bleed can reach 'radius' pixels beyond the dirty rect
    x0 = max(0, x0 - radius)
    y0 = max(0, y0 - radius)
    x1 = min(w, x1 + radius)
    y1 = min(h, y1 + radius)
    if x0 >= x1 or y0 >= y1:
        return None
    data = image.data
    for _ in range(radius):
        writes = []
        for y in range(y0, y1):
            row = y * w
            for x in range(x0, x1):
                o = (row + x) * 4
                if data[o + 3] > threshold:
                    continue
                acc = [0, 0, 0, 0]
                cnt = 0
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1, -1), (1, -1), (-1, 1), (1, 1)):
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    no = (ny * w + nx) * 4
                    if data[no + 3] <= threshold:
                        continue
                    acc[0] += data[no]
                    acc[1] += data[no + 1]
                    acc[2] += data[no + 2]
                    acc[3] += data[no + 3]
                    cnt += 1
                if cnt:
                    writes.append((o, acc[0] // cnt, acc[1] // cnt,
                                   acc[2] // cnt, acc[3] // cnt))
        if not writes:
            break
        for o, r, g, b, a in writes:
            data[o] = r
            data[o + 1] = g
            data[o + 2] = b
            data[o + 3] = a
    return (x0, y0, x1, y1)


# --------------------------------------------------------------------------
# automatic UV generation
# --------------------------------------------------------------------------

class UVSet(object):
    """A generated UV parameterisation.

    ``vertices``/``indices`` may differ from the input mesh: box and atlas
    unwrapping split vertices per triangle so that every corner can carry its
    own texture coordinate.
    """

    __slots__ = ("vertices", "uvs", "indices", "method")

    def __init__(self, vertices, uvs, indices, method):
        self.vertices = list(vertices)
        self.uvs = list(uvs)
        self.indices = list(indices)
        self.method = method

    @property
    def triangle_count(self):
        return len(self.indices) // 3

    def triangle_uvs(self, i):
        a, b, c = self.indices[3 * i:3 * i + 3]
        return (self.uvs[a], self.uvs[b], self.uvs[c])

    def in_range(self, tol=1e-9):
        for u, v in self.uvs:
            if u < -tol or u > 1.0 + tol or v < -tol or v > 1.0 + tol:
                return False
        return True

    def __repr__(self):
        return "UVSet(%s, %d verts, %d tris)" % (
            self.method, len(self.vertices), self.triangle_count)


def _v3(p):
    return (float(p[0]), float(p[1]), float(p[2]))


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len3(a):
    return math.sqrt(max(0.0, _dot3(a, a)))


def _norm3(a):
    n = _len3(a)
    if n < _EPS:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _bbox3(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def planar_uv(vertices, indices=None, axis=None, margin=0.0):
    """Project along the mesh's thinnest axis (or ``axis``: 0/1/2).

    Vertices are not duplicated, so this is the cheapest fallback; it does
    overlap for anything that is not a height field.
    """
    verts = [_v3(v) for v in vertices]
    if not verts:
        return UVSet([], [], list(indices or []), "planar")
    lo, hi = _bbox3(verts)
    ext = [hi[i] - lo[i] for i in range(3)]
    if axis is None:
        axis = ext.index(min(ext))
    ax = (axis + 1) % 3
    ay = (axis + 2) % 3
    du = ext[ax] if ext[ax] > _EPS else 1.0
    dv = ext[ay] if ext[ay] > _EPS else 1.0
    s = 1.0 - 2.0 * margin
    uvs = []
    for v in verts:
        u = margin + s * (v[ax] - lo[ax]) / du
        w = margin + s * (v[ay] - lo[ay]) / dv
        uvs.append((min(1.0, max(0.0, u)), min(1.0, max(0.0, w))))
    idx = list(indices) if indices is not None else list(range(len(verts)))
    return UVSet(verts, uvs, idx, "planar")


_BOX_AXES = (
    (0, (1, 2), 1.0),   # +X
    (0, (1, 2), -1.0),  # -X
    (1, (2, 0), 1.0),   # +Y
    (1, (2, 0), -1.0),  # -Y
    (2, (0, 1), 1.0),   # +Z
    (2, (0, 1), -1.0),  # -Z
)


def box_uv(vertices, indices, margin=0.01):
    """Six-sided box projection into a 3x2 chart grid.

    Each triangle goes into the chart of its dominant normal direction.  Charts
    do not overlap each other, but triangles *within* a chart can, so this is a
    quick preview unwrap rather than a lightmap quality one.
    """
    verts = [_v3(v) for v in vertices]
    idx = list(indices)
    if not verts or len(idx) < 3:
        return UVSet(verts, [(0.0, 0.0)] * len(verts), idx, "box")
    lo, hi = _bbox3(verts)
    ext = [max(_EPS, hi[i] - lo[i]) for i in range(3)]
    out_v = []
    out_uv = []
    out_i = []
    cell_w = 1.0 / 3.0
    cell_h = 0.5
    for t in range(len(idx) // 3):
        tri = [verts[idx[3 * t + k]] for k in range(3)]
        n = _cross3(_sub3(tri[1], tri[0]), _sub3(tri[2], tri[0]))
        comps = (n[0], n[1], n[2])
        best = max(range(3), key=lambda i: abs(comps[i]))
        chart = best * 2 + (0 if comps[best] >= 0.0 else 1)
        axis, (ax, ay), sign = _BOX_AXES[chart]
        cx = (chart % 3) * cell_w
        cy = (chart // 3) * cell_h
        for p in tri:
            u = (p[ax] - lo[ax]) / ext[ax]
            v = (p[ay] - lo[ay]) / ext[ay]
            if sign < 0.0:
                u = 1.0 - u
            u = margin + (1.0 - 2.0 * margin) * u
            v = margin + (1.0 - 2.0 * margin) * v
            out_i.append(len(out_v))
            out_v.append(p)
            out_uv.append((cx + u * cell_w, cy + v * cell_h))
    return UVSet(out_v, out_uv, out_i, "box")


def atlas_uv(vertices, indices, gutter=0.15, preserve_shape=True):
    """Pack every triangle into its own cell of a square grid.

    Each triangle is unfolded isometrically into 2D, uniformly scaled to fit
    its cell and inset by ``gutter`` (a fraction of the cell).  Because the
    cells are disjoint, the resulting UVs never overlap -- which is exactly
    what a paint-anything fallback needs.
    """
    verts = [_v3(v) for v in vertices]
    idx = list(indices)
    ntri = len(idx) // 3
    if ntri == 0:
        return UVSet(verts, [(0.0, 0.0)] * len(verts), idx, "atlas")
    cols = int(math.ceil(math.sqrt(ntri)))
    rows = int(math.ceil(ntri / float(cols)))
    cw = 1.0 / cols
    ch = 1.0 / rows
    g = min(0.45, max(0.0, float(gutter)))
    out_v = []
    out_uv = []
    out_i = []
    for t in range(ntri):
        tri = [verts[idx[3 * t + k]] for k in range(3)]
        flat = _unfold_triangle(tri) if preserve_shape else [
            (0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        xs = [p[0] for p in flat]
        ys = [p[1] for p in flat]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        col = t % cols
        row = t // cols
        ix = col * cw + g * cw
        iy = row * ch + g * ch
        iw = cw * (1.0 - 2.0 * g)
        ih = ch * (1.0 - 2.0 * g)
        if w <= _EPS and h <= _EPS:
            s = 0.0
        else:
            s = min(iw / w if w > _EPS else float("inf"),
                    ih / h if h > _EPS else float("inf"))
            if s == float("inf"):
                s = 0.0
        ox = ix + (iw - w * s) * 0.5
        oy = iy + (ih - h * s) * 0.5
        for k in range(3):
            u = ox + (flat[k][0] - min(xs)) * s
            v = oy + (flat[k][1] - min(ys)) * s
            out_i.append(len(out_v))
            out_v.append(tri[k])
            out_uv.append((min(1.0, max(0.0, u)), min(1.0, max(0.0, v))))
    return UVSet(out_v, out_uv, out_i, "atlas")


def _unfold_triangle(tri):
    """Isometric 2D layout of a 3D triangle (edge 0-1 on the u axis)."""
    a, b, c = tri
    e1 = _sub3(b, a)
    L = _len3(e1)
    if L < _EPS:
        return [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    u = (e1[0] / L, e1[1] / L, e1[2] / L)
    e2 = _sub3(c, a)
    x = _dot3(e2, u)
    perp = (e2[0] - u[0] * x, e2[1] - u[1] * x, e2[2] - u[2] * x)
    y = _len3(perp)
    return [(0.0, 0.0), (L, 0.0), (x, y)]


def generate_uvs(vertices, indices, method="atlas", **kw):
    """Dispatch to :func:`planar_uv`, :func:`box_uv` or :func:`atlas_uv`."""
    method = (method or "atlas").lower()
    if method == "planar":
        return planar_uv(vertices, indices, **kw)
    if method == "box":
        return box_uv(vertices, indices, **kw)
    if method == "atlas":
        return atlas_uv(vertices, indices, **kw)
    raise ValueError("unknown auto-UV method: %r" % (method,))


# --------------------------------------------------------------------------
# controller hits
# --------------------------------------------------------------------------

class PaintHit(object):
    """A resolved controller ray hit on a paintable surface."""

    __slots__ = ("point", "normal", "uv", "ray_dir", "object_name", "tail",
                 "pressure", "time")

    def __init__(self, point=None, normal=None, uv=None, ray_dir=None,
                 object_name=None, tail=None, pressure=1.0, time=0.0):
        self.point = None if point is None else _v3(point)
        self.normal = None if normal is None else _v3(normal)
        self.uv = None if uv is None else (float(uv[0]), float(uv[1]))
        self.ray_dir = None if ray_dir is None else _v3(ray_dir)
        self.object_name = object_name
        self.tail = tail
        self.pressure = float(pressure)
        self.time = float(time)

    @property
    def valid(self):
        return self.uv is not None

    def facing(self, tolerance=0.0):
        """True when the surface faces the controller (not a backface).

        With no ray direction available the hit is accepted -- a picked point
        without a ray is by construction the front-most surface.
        """
        if self.normal is None or self.ray_dir is None:
            return True
        n = _norm3(self.normal)
        d = _norm3(self.ray_dir)
        if _len3(n) < 0.5 or _len3(d) < 0.5:
            return True
        return _dot3(n, d) < -float(tolerance)

    def __repr__(self):
        return "PaintHit(uv=%r, obj=%r)" % (self.uv, self.object_name)


def hit_from_controller(controller, separator, vp_reg, near=0.01, far=100.0,
                        camera=None, pressure=None, time=0.0):
    """Resolve a hit from an :class:`xrcore.controllerXR.xrController`.

    Uses the upstream engine's ``find_picked_coin_object`` /
    ``get_picked_tex_coords`` / ``get_picked_normal`` / ``get_buttons_states``.
    Returns ``None`` when nothing was picked.
    """
    picked, coords = controller.find_picked_coin_object(
        separator, vp_reg, near, far, camera)
    if not picked:
        return None
    tex = controller.get_picked_tex_coords()
    normal = controller.get_picked_normal()
    ray_dir = None
    try:
        axis = controller.find_ray_axis()
        av = axis.getValue() if hasattr(axis, "getValue") else axis
        ray_dir = (-av[0], -av[1], -av[2])
    except Exception:
        ray_dir = None
    if pressure is None:
        try:
            pressure = float(controller.get_buttons_states().grab)
        except Exception:
            pressure = 1.0
    name = None
    tail = controller.get_picked_tail()
    try:
        name = tail.getName().getString()
    except Exception:
        name = None
    return PaintHit(coords, normal, (tex[0], tex[1]), ray_dir, name, tail,
                    pressure, time)


# --------------------------------------------------------------------------
# Coin bridge
# --------------------------------------------------------------------------

class CoinTextureBridge(object):
    """Owns the ``SoTexture2`` a painted texture is uploaded into.

    Coin stores texture rows bottom-up, ours are top-down, so rows are flipped
    on the way out.  Uploads are incremental: only the accumulated dirty
    rectangle is pushed, which is what keeps painting at frame rate.
    """

    def __init__(self, width, height, wrap="REPEAT", model="MODULATE"):
        self.width = int(width)
        self.height = int(height)
        self.wrap = wrap
        self.model = model
        self.texture = None
        self.binding = None
        self.dirty = None
        self._built = False

    # -- scenegraph ------------------------------------------------------
    def build(self):
        """Create (once) and return ``(SoTexture2, SoTextureCoordinateBinding)``."""
        if self._built:
            return self.texture, self.binding
        from pivy.coin import SoTexture2, SoTextureCoordinateBinding
        tex = SoTexture2()
        try:
            tex.wrapS = getattr(SoTexture2, self.wrap)
            tex.wrapT = getattr(SoTexture2, self.wrap)
            tex.model = getattr(SoTexture2, self.model)
        except Exception:
            pass
        binding = SoTextureCoordinateBinding()
        try:
            binding.value = SoTextureCoordinateBinding.PER_VERTEX_INDEXED
        except Exception:
            pass
        self.texture = tex
        self.binding = binding
        self._built = True
        return tex, binding

    def attach(self, separator, index=0):
        """Insert the texture nodes at the head of ``separator``."""
        tex, binding = self.build()
        try:
            separator.insertChild(binding, index)
            separator.insertChild(tex, index)
        except Exception:
            separator.addChild(tex)
            separator.addChild(binding)
        return separator

    def detach(self, separator):
        for node in (self.texture, self.binding):
            if node is None:
                continue
            try:
                i = separator.findChild(node)
                if i >= 0:
                    separator.removeChild(i)
            except Exception:
                pass

    # -- uploads ---------------------------------------------------------
    def mark_dirty(self, rect):
        if rect is None:
            return
        if self.dirty is None:
            self.dirty = tuple(rect)
        else:
            a = self.dirty
            self.dirty = (min(a[0], rect[0]), min(a[1], rect[1]),
                          max(a[2], rect[2]), max(a[3], rect[3]))

    def mark_all_dirty(self):
        self.dirty = (0, 0, self.width, self.height)

    def rows_bottom_up(self, image, rect=None):
        """Extract ``rect`` from ``image`` as bottom-up RGBA bytes."""
        if rect is None:
            rect = (0, 0, image.width, image.height)
        x0, y0, x1, y1 = image._clip_rect(rect)
        stride = image.width * 4
        n = (x1 - x0) * 4
        out = bytearray()
        for y in range(y1 - 1, y0 - 1, -1):
            o = y * stride + x0 * 4
            out += image.data[o:o + n]
        return bytes(out), (x0, y0, x1 - x0, y1 - y0)

    def upload(self, image, rect=None, force_full=False):
        """Push pixels into the Coin texture; returns the uploaded rect."""
        tex, _ = self.build()
        from pivy.coin import SbVec2s
        if rect is None:
            rect = self.dirty
        full = force_full or rect is None or (
            rect[0] <= 0 and rect[1] <= 0 and rect[2] >= image.width
            and rect[3] >= image.height)
        if full:
            blob, (x, y, w, h) = self.rows_bottom_up(image, None)
            tex.image.setValue(SbVec2s(image.width, image.height), 4, blob)
            self.dirty = None
            return (0, 0, image.width, image.height)
        blob, (x0, y0, w, h) = self.rows_bottom_up(image, rect)
        if w <= 0 or h <= 0:
            self.dirty = None
            return None
        # Coin's origin is the lower left corner
        oy = image.height - (y0 + h)
        try:
            tex.image.setSubValue(SbVec2s(w, h), SbVec2s(x0, oy), blob)
        except Exception:
            blob, _ = self.rows_bottom_up(image, None)
            tex.image.setValue(SbVec2s(image.width, image.height), 4, blob)
            self.dirty = None
            return (0, 0, image.width, image.height)
        self.dirty = None
        return (x0, y0, x0 + w, y0 + h)


# --------------------------------------------------------------------------
# paint target
# --------------------------------------------------------------------------

class PaintTarget(object):
    """The paintable state attached to one FreeCAD object."""

    def __init__(self, fc_name, width=None, height=None, uvset=None,
                 undo_steps=None):
        if width is None:
            width = _prefs.get_int("TextureSize")
        if height is None:
            height = width
        self.fc_name = fc_name
        self.width = int(width)
        self.height = int(height)
        self.stack = _layers.LayerStack(self.width, self.height)
        self.stack.add_layer("Base")
        if undo_steps is None:
            undo_steps = _prefs.get_int("PaintUndoSteps")
        self.history = _layers.History(self.stack, max_entries=int(undo_steps))
        self.uvset = uvset
        self.bridge = None
        self.composite_cache = None
        self._composite_dirty = True
        self.dilate_radius = 3

    # -- layers ----------------------------------------------------------
    @property
    def layers(self):
        return self.stack.layers

    def active_layer(self):
        return self.stack.active

    def invalidate(self, rect=None):
        self._composite_dirty = True
        self.stack.invalidate(rect)
        if self.bridge is not None:
            self.bridge.mark_dirty(rect if rect is not None
                                   else (0, 0, self.width, self.height))

    def composite(self, dilate=True):
        """Composite the visible layers and dilate past the UV seams."""
        if self.composite_cache is None:
            self.composite_cache = Image(self.width, self.height)
        if self._composite_dirty:
            self.composite_cache.clear()
            self.stack.composite(into=self.composite_cache)
            if dilate and self.dilate_radius > 0:
                dilate_edges(self.composite_cache, self.dilate_radius)
            self._composite_dirty = False
        return self.composite_cache

    # -- coin ------------------------------------------------------------
    def ensure_bridge(self):
        if self.bridge is None:
            self.bridge = CoinTextureBridge(self.width, self.height)
        return self.bridge

    def upload(self, rect=None):
        bridge = self.ensure_bridge()
        img = self.composite()
        return bridge.upload(img, rect)

    # -- auto uv ---------------------------------------------------------
    def ensure_uvs(self, vertices=None, indices=None, method=None):
        """Generate UVs when the object has none (auto-UV fallback)."""
        if self.uvset is not None:
            return self.uvset
        if vertices is None or indices is None:
            return None
        if method is None:
            method = "atlas" if _prefs.get_bool("AutoUV") else "planar"
        self.uvset = generate_uvs(vertices, indices, method)
        return self.uvset

    # -- §4 JSON ---------------------------------------------------------
    def to_dict(self, first_image_index=0):
        return {
            "fc_name": self.fc_name,
            "layers": self.stack.to_dict(first_image_index),
        }

    def layer_images(self):
        return [l.image for l in self.stack.layers]

    @classmethod
    def from_dict(cls, d, images=None):
        stack = _layers.LayerStack.from_dict(d.get("layers", []), images)
        t = cls(d.get("fc_name"), stack.width, stack.height)
        t.stack = stack
        t.history = _layers.History(stack)
        t.invalidate()
        return t

    def __repr__(self):
        return "PaintTarget(%r, %dx%d, %d layers)" % (
            self.fc_name, self.width, self.height, len(self.stack))


# --------------------------------------------------------------------------
# the painter
# --------------------------------------------------------------------------

class TexturePainter(object):
    """Drives one painting stroke over a :class:`PaintTarget`.

    ``seam_jump`` is the UV distance above which two consecutive hits are
    assumed to be on different UV islands; the stroke is broken there instead
    of drawing a streak straight across the atlas.
    """

    def __init__(self, target=None, params=None, color=(0, 0, 0, 255),
                 seam_jump=0.25, backface_tolerance=0.0, undo_label="paint"):
        self.target = target
        self.params = params or _brush.preset("round")
        self.color = color
        self.seam_jump = float(seam_jump)
        self.backface_tolerance = float(backface_tolerance)
        self.undo_label = undo_label
        self.sampler = None
        self._last_uv = None
        self._dirty = None
        self._layer = None
        self.rejected = 0
        self.seam_breaks = 0
        self.stamp_count = 0

    # -- helpers ---------------------------------------------------------
    @property
    def active(self):
        return self.sampler is not None

    def _px(self, uv):
        return uv_to_pixel(uv, self.target.width, self.target.height)

    def _accept(self, hit):
        if hit is None or not hit.valid:
            return False
        if not hit.facing(self.backface_tolerance):
            self.rejected += 1
            return False
        return True

    def _paint(self, stamps):
        if not stamps or self._layer is None:
            return None
        img = self._layer.image
        # snapshot before the pixels change, tile by tile
        rect = self._stamps_rect(stamps)
        if rect is not None:
            self.target.history.snapshot(self._layer, rect)
        d = _brush.paint_stamps(img, stamps, self.color, self.params)
        self.stamp_count += len(stamps)
        if d is not None:
            self._dirty = _union(self._dirty, d)
            self.target.invalidate(d)
        return d

    def _stamps_rect(self, stamps):
        rect = None
        for st in stamps:
            r = int(math.ceil(st.radius)) + 2
            b = (int(st.x - r), int(st.y - r), int(st.x + r) + 1,
                 int(st.y + r) + 1)
            rect = _union(rect, b)
        return rect

    # -- stroke API ------------------------------------------------------
    def begin(self, hit):
        """Start a stroke; returns True when the hit was accepted."""
        if self.target is None or not self._accept(hit):
            return False
        layer = self.target.active_layer()
        if layer is None or layer.locked:
            return False
        self._layer = layer
        self.target.history.begin(self.undo_label)
        self.sampler = _brush.StrokeSampler(self.params)
        x, y = self._px(hit.uv)
        self._last_uv = hit.uv
        self._dirty = None
        self.rejected = 0
        self.seam_breaks = 0
        self.stamp_count = 0
        self._paint(self.sampler.begin(x, y, hit.pressure))
        return True

    def move(self, hit):
        """Continue the stroke; returns the dirty rect of this step."""
        if self.sampler is None:
            return None
        if not self._accept(hit):
            return None
        x, y = self._px(hit.uv)
        if self._last_uv is not None:
            du = abs(hit.uv[0] - self._last_uv[0])
            dv = abs(hit.uv[1] - self._last_uv[1])
            du = min(du, 1.0 - du)
            dv = min(dv, 1.0 - dv)
            if math.hypot(du, dv) > self.seam_jump:
                # different UV island: restart instead of streaking across
                self.seam_breaks += 1
                self.sampler.end()
                self.sampler = _brush.StrokeSampler(self.params)
                self._last_uv = hit.uv
                return self._paint(self.sampler.begin(x, y, hit.pressure))
        self._last_uv = hit.uv
        return self._paint(self.sampler.move(x, y, hit.pressure))

    def end(self):
        """Finish the stroke: dilate the seams and close the undo entry."""
        if self.sampler is None:
            return None
        self.sampler.end()
        self.sampler = None
        dirty = self._dirty
        if dirty is not None and self._layer is not None:
            r = self.target.dilate_radius
            if r > 0:
                grown = (dirty[0] - r, dirty[1] - r, dirty[2] + r,
                         dirty[3] + r)
                self.target.history.snapshot(self._layer, grown)
                dilate_edges(self._layer.image, r, dirty)
                dirty = self._layer.image._clip_rect(grown)
                self.target.invalidate(dirty)
        self.target.history.commit()
        self._layer = None
        self._last_uv = None
        self._dirty = None
        return dirty

    def cancel(self):
        if self.sampler is not None:
            self.sampler.end()
            self.sampler = None
        self.target.history.abort()
        self.target.invalidate()
        self._layer = None
        self._last_uv = None
        self._dirty = None

    # -- one-shot --------------------------------------------------------
    def dab(self, hit):
        """Paint a single stamp (a click without a drag)."""
        if self.begin(hit):
            return self.end()
        return None

    def stroke(self, hits):
        """Paint a whole list of hits in one undo step."""
        it = iter(hits)
        first = None
        for h in it:
            if self._accept(h):
                first = h
                break
        if first is None:
            return None
        if not self.begin(first):
            return None
        for h in it:
            self.move(h)
        return self.end()


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
