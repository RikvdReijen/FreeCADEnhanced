# SPDX-License-Identifier: LGPL-2.1-or-later
"""A pose from the four corners of a detected code.

The scanner (the headset's passthrough camera, or a phone) reports where
the code's corners are in world space, in order: top-left, top-right,
bottom-right, bottom-left as printed. From those: the centre is the
origin, the +X axis runs along the top edge, +Y up the left edge, +Z out
of the paper (right-handed), and the measured edge length against the
printed ``size`` gives a scale check — a code seen at 79 mm when it was
printed at 80 is a 1.2 % ranging error, which the session reports rather
than absorbs.

Corners can be noisy, so the frame is orthonormalised (Gram–Schmidt with
the diagonal-average normal) and the residual is returned as a quality.
"""

import math

from xrsketch import vecmath as vm


class CodePose(object):
    __slots__ = ("transform", "edge_mm", "size_mm", "residual", "corners")

    def __init__(self, transform, edge_mm, size_mm, residual, corners):
        self.transform = transform
        #: mean measured edge length, mm
        self.edge_mm = edge_mm
        self.size_mm = size_mm
        #: RMS corner error after fitting the square, metres
        self.residual = residual
        self.corners = corners

    @property
    def scale_error(self):
        """Measured over printed size minus one: 0 is perfect."""
        return self.edge_mm / self.size_mm - 1.0 if self.size_mm else 0.0

    @property
    def normal(self):
        return self.transform.apply_vector((0.0, 0.0, 1.0))

    def to_dict(self):
        return {"transform": self.transform.to_dict(), "edge_mm": self.edge_mm, "size_mm": self.size_mm,
                "residual": self.residual, "scale_error": self.scale_error}

    def __repr__(self):
        return "CodePose(edge %.1f mm of %.1f, residual %.2g)" % (self.edge_mm, self.size_mm, self.residual)


def pose_from_corners(corners, size_mm):
    """Corners in metres (4 × xyz, TL TR BR BL) -> :class:`CodePose`."""
    if len(corners) != 4:
        raise ValueError("need four corners, got %d" % len(corners))
    tl, tr, br, bl = [vm.vec3(c) for c in corners]
    centre = vm.mul(vm.add(vm.add(tl, tr), vm.add(br, bl)), 0.25)
    x_axis = vm.add(vm.sub(tr, tl), vm.sub(br, bl))  # both horizontal edges
    y_axis = vm.add(vm.sub(tl, bl), vm.sub(tr, br))  # both vertical edges
    if vm.length(x_axis) < 1e-9 or vm.length(y_axis) < 1e-9:
        raise ValueError("degenerate corners")
    normal = vm.normalize(vm.cross(x_axis, y_axis))
    if vm.length(normal) < 0.5:
        raise ValueError("corners are collinear")
    x = vm.normalize(x_axis)
    y = vm.normalize(vm.cross(normal, x))
    rotation = vm.quat_from_mat3(vm.mat3_from_columns(x, y, normal))
    transform = vm.Transform(centre, rotation)
    edges = [vm.dist(tl, tr), vm.dist(tr, br), vm.dist(br, bl), vm.dist(bl, tl)]
    edge_mm = sum(edges) / 4.0 * 1000.0
    half = edge_mm / 2000.0
    ideal = [(-half, half, 0.0), (half, half, 0.0), (half, -half, 0.0), (-half, -half, 0.0)]
    residual = math.sqrt(sum(vm.dist(transform.apply(i), c) ** 2 for i, c in zip(ideal, (tl, tr, br, bl))) / 4.0)
    return CodePose(transform, edge_mm, float(size_mm), residual, (tl, tr, br, bl))


def up_correction(up):
    """Rotation taking the code frame (+Z out of the paper) to the model
    axis the payload says the paper normal is."""
    from xrassembly.mates import rotation_between

    axis = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1), "-x": (-1, 0, 0), "-y": (0, -1, 0), "-z": (0, 0, -1)}[up]
    return rotation_between(axis, (0, 0, 1))
