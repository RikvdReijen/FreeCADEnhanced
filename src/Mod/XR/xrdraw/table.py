# SPDX-License-Identifier: LGPL-2.1-or-later
"""A drafting table in the environment, with a TechDraw page on it.

The table is a rectangle in world space (metres, Y up) tilted like a real
drawing board; the page is scaled to fit it. Two mappings do all the work:
a controller ray onto the page (``ray_to_page`` → page millimetres, as
TechDraw counts them: X right, Y up from the bottom-left corner) and page
millimetres back into the world (``page_to_world``) for drawing the
dimension preview and the hover cursor.
"""

import math

from xrsketch import vecmath as vm

#: ISO page sizes in mm (width, height), landscape
PAGE_SIZES = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
              "Letter": (279.4, 215.9), "Tabloid": (431.8, 279.4)}


class DraftingTable(object):
    def __init__(self, position=(0.0, 0.9, -0.6), tilt_deg=20.0, yaw_deg=0.0, size=(0.9, 0.65), page_size="A3",
                 margin=0.03):
        self.position = vm.vec3(position)
        self.tilt_deg = float(tilt_deg)
        self.yaw_deg = float(yaw_deg)
        self.size = (float(size[0]), float(size[1]))
        self.page_mm = PAGE_SIZES.get(page_size, page_size if isinstance(page_size, tuple) else PAGE_SIZES["A3"])
        self.page_name = page_size if isinstance(page_size, str) else "custom"
        self.margin = float(margin)

    # -- frame -----------------------------------------------------------

    @property
    def rotation(self):
        """Table frame: local X right, local Y up the slope, local Z the normal (towards the viewer)."""
        from xrassembly.mates import rotation_about

        # Local +Z (the page normal) starts along world +Z; a flat table's normal
        # is +Y, and tilting the far edge up by tilt_deg brings the normal back
        # towards the user: rotate about X by -(90° - tilt).
        tilt = rotation_about((1, 0, 0), -math.radians(90.0 - self.tilt_deg))
        yaw = rotation_about((0, 1, 0), math.radians(self.yaw_deg))
        return vm.quat_normalize(_qmul(yaw, tilt))

    @property
    def transform(self):
        return vm.Transform(self.position, self.rotation)

    @property
    def normal(self):
        return self.transform.apply_vector((0.0, 0.0, 1.0))

    @property
    def page_scale(self):
        """metres per page millimetre, so the page fits the table inside the margin."""
        w, h = self.size[0] - 2 * self.margin, self.size[1] - 2 * self.margin
        return min(w / self.page_mm[0], h / self.page_mm[1])

    def page_corner_local(self):
        """Local (x, y) of the page's bottom-left corner, centred on the table."""
        s = self.page_scale
        return (-self.page_mm[0] * s / 2.0, -self.page_mm[1] * s / 2.0)

    # -- mappings --------------------------------------------------------

    def page_to_world(self, x_mm, y_mm, lift=0.0):
        s = self.page_scale
        cx, cy = self.page_corner_local()
        return self.transform.apply((cx + x_mm * s, cy + y_mm * s, lift))

    def world_to_page(self, point):
        local = self.transform.inverse().apply(point)
        s = self.page_scale
        cx, cy = self.page_corner_local()
        return ((local[0] - cx) / s, (local[1] - cy) / s, local[2])

    def ray_to_page(self, origin, direction):
        """Intersect a ray with the table plane. Returns ``(x_mm, y_mm, on_page, distance)`` or ``None``."""
        n = self.normal
        d = vm.normalize(direction)
        denom = vm.dot(n, d)
        if abs(denom) < 1e-9:
            return None
        t = vm.dot(n, vm.sub(self.position, origin)) / denom
        if t < 0:
            return None
        hit = vm.add(origin, vm.mul(d, t))
        x, y, _ = self.world_to_page(hit)
        on_page = 0.0 <= x <= self.page_mm[0] and 0.0 <= y <= self.page_mm[1]
        return (x, y, on_page, t)

    def corners_world(self):
        w, h = self.page_mm
        return [self.page_to_world(0, 0), self.page_to_world(w, 0), self.page_to_world(w, h), self.page_to_world(0, h)]

    def to_dict(self):
        return {"position": list(self.position), "tilt_deg": self.tilt_deg, "yaw_deg": self.yaw_deg,
                "size": list(self.size), "page": self.page_name, "page_mm": list(self.page_mm), "scale": self.page_scale}


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)
