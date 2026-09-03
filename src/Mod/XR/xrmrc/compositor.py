# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
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
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""The four-quadrant mixed-reality compositor.

Quadrant MRC is the shape every SteamVR-era mixed reality tool expects: one
frame, quartered, carrying the pieces a compositor downstream (OBS, LIV) needs
to sandwich a filmed person between the near and the far half of the scene.

::

    +---------------------+---------------------+
    | foreground colour   | foreground alpha    |
    | (scene nearer than  | (same pass, alpha   |
    |  the split plane)   |  written as luma)   |
    +---------------------+---------------------+
    | background colour   | fourth quadrant     |
    | (the scene as the   | (first-person view  |
    |  capture camera     |  by convention)     |
    |  sees it)           |                     |
    +---------------------+---------------------+

The layout above is not something this project invented — it is what Valve's
``SteamVR_ExternalCamera`` draws, and it is what the OBS crop recipes in the
wild assume.  Note the fourth quadrant in particular: the convention puts the
**first-person view** there, not a background alpha, because the background is
opaque by construction.  ``FOURTH_BACKGROUND_ALPHA`` renders an alpha pass
there instead for the cases where a downstream tool wants one (and for looking
at what the compositor thinks it is doing); ``FOURTH_FIRST_PERSON`` is the
default because it is the convention.

Everything above the OpenGL line lives in pure functions and small data
classes: :func:`quadrant_rects`, :func:`split_distance`,
:func:`foreground_clip_distance`, :func:`perspective_matrix` and
:class:`QuadrantCompositor` need no GPU, no Coin3D and no headset, and are
covered by ``Tests/test_mrc.py``.  :class:`CoinQuadrantRenderer` is the thin
adapter that actually draws, and it imports ``pivy.coin`` lazily.
"""

import math

from . import externalcamera
from .camera import clamp, horizontal, v_dot, v_sub

__all__ = [
    "Rect",
    "QuadrantLayout",
    "RenderPass",
    "FramePlan",
    "QuadrantCompositor",
    "CoinQuadrantRenderer",
    "QUADRANT_FOREGROUND_COLOUR",
    "QUADRANT_FOREGROUND_ALPHA",
    "QUADRANT_BACKGROUND_COLOUR",
    "QUADRANT_FOURTH",
    "QUADRANTS",
    "FOURTH_FIRST_PERSON",
    "FOURTH_BACKGROUND_ALPHA",
    "FOURTH_BLANK",
    "FOURTH_MODES",
    "ORIGIN_TOP_LEFT",
    "ORIGIN_BOTTOM_LEFT",
    "BACKGROUND_FULL_SCENE",
    "BACKGROUND_BEYOND_SPLIT",
    "BACKGROUND_MODES",
    "PASS_COLOUR",
    "PASS_ALPHA",
    "PASS_FIRST_PERSON",
    "PASS_BLANK",
    "SPLIT_EPSILON",
    "quadrant_size",
    "quadrant_rects",
    "layout_for",
    "split_distance",
    "foreground_clip_distance",
    "background_near_distance",
    "perspective_matrix",
    "fit_rect_to_aspect",
    "vfov_for_aspect",
]

QUADRANT_FOREGROUND_COLOUR = "foreground_colour"
QUADRANT_FOREGROUND_ALPHA = "foreground_alpha"
QUADRANT_BACKGROUND_COLOUR = "background_colour"
QUADRANT_FOURTH = "fourth"

QUADRANTS = (
    QUADRANT_FOREGROUND_COLOUR,
    QUADRANT_FOREGROUND_ALPHA,
    QUADRANT_BACKGROUND_COLOUR,
    QUADRANT_FOURTH,
)

#: What goes in the bottom-right quadrant.
FOURTH_FIRST_PERSON = "first_person"
FOURTH_BACKGROUND_ALPHA = "background_alpha"
FOURTH_BLANK = "blank"
FOURTH_MODES = (FOURTH_FIRST_PERSON, FOURTH_BACKGROUND_ALPHA, FOURTH_BLANK)

#: Pixel origin of the quadrant rectangles.  Image and video tooling counts
#: rows from the top; OpenGL viewports count them from the bottom.  Both are
#: offered because we produce the frame with GL and consume it as an image.
ORIGIN_TOP_LEFT = "top-left"
ORIGIN_BOTTOM_LEFT = "bottom-left"

#: How the background pass is clipped.  ``BACKGROUND_FULL_SCENE`` reproduces
#: the reference implementation, whose ``RenderFar`` draws the *whole* scene
#: from ``near`` to ``far``; the near geometry appears in the background too,
#: but the foreground layer is drawn over it so the composite is still right.
#: ``BACKGROUND_BEYOND_SPLIT`` pushes the background's near plane out to the
#: split instead, which costs nothing and makes the two layers disjoint.
BACKGROUND_FULL_SCENE = "full_scene"
BACKGROUND_BEYOND_SPLIT = "beyond_split"
BACKGROUND_MODES = (BACKGROUND_FULL_SCENE, BACKGROUND_BEYOND_SPLIT)

PASS_COLOUR = "colour"
PASS_ALPHA = "alpha"
PASS_FIRST_PERSON = "first_person"
PASS_BLANK = "blank"

#: The reference implementation keeps the split plane a centimetre clear of
#: both clip planes so the clip quad is never coplanar with them.
SPLIT_EPSILON = 0.01


class Rect:
    """An integer pixel rectangle: origin plus size."""

    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x, y, width, height):
        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)

    @property
    def right(self):
        return self.x + self.width

    @property
    def top(self):
        return self.y + self.height

    @property
    def aspect(self):
        if self.height <= 0:
            return 1.0
        return self.width / self.height

    def as_tuple(self):
        return (self.x, self.y, self.width, self.height)

    def contains(self, x, y):
        return self.x <= x < self.right and self.y <= y < self.top

    def __eq__(self, other):
        if not isinstance(other, Rect):
            return NotImplemented
        return self.as_tuple() == other.as_tuple()

    def __hash__(self):
        return hash(self.as_tuple())

    def __repr__(self):
        return f"Rect(x={self.x}, y={self.y}, width={self.width}, height={self.height})"


def quadrant_size(width, height):
    """Size of one quadrant.

    Integer division, exactly as ``Screen.width / 2`` does in the reference
    implementation: an odd frame width leaves the last column unused rather
    than producing quadrants of different sizes, which is what any downstream
    crop filter assumes.
    """
    return (max(int(width) // 2, 0), max(int(height) // 2, 0))


def quadrant_rects(width, height, origin=ORIGIN_TOP_LEFT):
    """The four quadrant rectangles of a ``width`` x ``height`` frame."""
    if origin not in (ORIGIN_TOP_LEFT, ORIGIN_BOTTOM_LEFT):
        raise ValueError(f"unknown pixel origin '{origin}'")
    qw, qh = quadrant_size(width, height)
    if origin == ORIGIN_TOP_LEFT:
        top, bottom = 0, qh
    else:
        top, bottom = qh, 0
    return {
        QUADRANT_FOREGROUND_COLOUR: Rect(0, top, qw, qh),
        QUADRANT_FOREGROUND_ALPHA: Rect(qw, top, qw, qh),
        QUADRANT_BACKGROUND_COLOUR: Rect(0, bottom, qw, qh),
        QUADRANT_FOURTH: Rect(qw, bottom, qw, qh),
    }


class QuadrantLayout:
    """The geometry of one MRC output frame."""

    __slots__ = ("width", "height", "origin", "rects")

    def __init__(self, width, height, origin=ORIGIN_TOP_LEFT):
        self.width = max(int(width), 0)
        self.height = max(int(height), 0)
        self.origin = origin
        self.rects = quadrant_rects(self.width, self.height, origin)

    @property
    def quadrant_width(self):
        return self.rects[QUADRANT_FOREGROUND_COLOUR].width

    @property
    def quadrant_height(self):
        return self.rects[QUADRANT_FOREGROUND_COLOUR].height

    @property
    def aspect(self):
        """Aspect ratio of a quadrant.

        A quadrant is half as wide and half as tall as the frame, so this is
        also the aspect of the whole frame — up to the pixel an odd resolution
        loses to the integer division.
        """
        if self.quadrant_height <= 0:
            return 1.0
        return self.quadrant_width / self.quadrant_height

    @property
    def usable_size(self):
        """The part of the frame the quadrants actually cover."""
        return (self.quadrant_width * 2, self.quadrant_height * 2)

    @property
    def valid(self):
        return self.quadrant_width > 0 and self.quadrant_height > 0

    def rect(self, quadrant):
        return self.rects[quadrant]

    def as_dict(self):
        return {
            "width": self.width,
            "height": self.height,
            "origin": self.origin,
            "quadrant": [self.quadrant_width, self.quadrant_height],
            "aspect": self.aspect,
            "rects": {name: rect.as_tuple() for name, rect in self.rects.items()},
        }

    def __eq__(self, other):
        if not isinstance(other, QuadrantLayout):
            return NotImplemented
        return (self.width, self.height, self.origin) == (
            other.width,
            other.height,
            other.origin,
        )

    def __hash__(self):
        return hash((self.width, self.height, self.origin))

    def __repr__(self):
        return (
            f"QuadrantLayout(width={self.width}, height={self.height}, "
            f"origin={self.origin!r})"
        )


def layout_for(width, height, origin=ORIGIN_TOP_LEFT):
    return QuadrantLayout(width, height, origin)


# --------------------------------------------------------------------------
# the split plane
# --------------------------------------------------------------------------


def split_distance(camera_pose, hmd_pose, hmd_offset=0.0, near=0.1, far=100.0,
                   epsilon=SPLIT_EPSILON):
    """Distance from the camera to the plane the person stands on.

    This is ``SteamVR_ExternalCamera.GetTargetDistance`` in the OpenXR frame.
    The camera's forward vector and the HMD's are both flattened onto the
    ground plane first, then the HMD is pushed ``hmd_offset`` metres along its
    own horizontal forward — that is what lets a calibration place the split
    slightly in front of or behind the player's face — and the distance is the
    projection of the camera-to-target vector onto the camera's horizontal
    forward.

    Flattening matters: without it, tilting the camera down would drag the
    split plane towards the floor and the player would fall out of the
    foreground.  The result is clamped a centimetre clear of both clip planes,
    as in the reference implementation.

    Returns ``near + epsilon`` when there is no HMD to measure against, which
    is what the reference implementation does when it has no target.
    """
    if far <= near:
        raise ValueError(f"unusable clip range near={near} far={far}")
    low = near + epsilon
    high = far - epsilon
    if low > high:  # a clip range narrower than 2 cm
        low = high = (near + far) * 0.5
    if hmd_pose is None or camera_pose is None:
        return low

    forward = horizontal(camera_pose.forward())
    head_forward = horizontal(hmd_pose.forward())
    target = (
        hmd_pose.position[0] + head_forward[0] * hmd_offset,
        hmd_pose.position[1] + head_forward[1] * hmd_offset,
        hmd_pose.position[2] + head_forward[2] * hmd_offset,
    )
    distance = v_dot(forward, v_sub(target, camera_pose.position))
    return clamp(distance, low, high)


def foreground_clip_distance(split, near_offset=0.0, near=0.1, far=100.0):
    """Where the clip plane that erases the background actually sits.

    ``RenderNear`` moves its clip quad to ``clamp(GetTargetDistance() +
    nearOffset, near, far)``.  ``nearOffset`` is the knob a creator turns when
    their arms keep getting cut in half: push the plane a little further away
    and more of the body lands in the foreground layer.
    """
    return clamp(split + near_offset, near, far)


def background_near_distance(split, far_offset=0.0, near=0.1, far=100.0,
                             mode=BACKGROUND_FULL_SCENE):
    """The near plane of the background pass.

    With ``BACKGROUND_FULL_SCENE`` (the reference behaviour) this is simply
    ``near``: ``RenderFar`` draws the whole scene and never looks at
    ``farOffset``, which the config struct declares but nothing reads.  With
    ``BACKGROUND_BEYOND_SPLIT`` the background starts at the split plane, with
    ``farOffset`` available as its own nudge.
    """
    if mode == BACKGROUND_FULL_SCENE:
        return near
    if mode != BACKGROUND_BEYOND_SPLIT:
        raise ValueError(f"unknown background mode '{mode}'")
    return clamp(split + far_offset, near, far)


# --------------------------------------------------------------------------
# projection and aspect
# --------------------------------------------------------------------------


def perspective_matrix(vfov_deg, aspect, near, far):
    """Row-major 4x4 OpenGL perspective matrix (right handed, -Z forward)."""
    if aspect <= 0.0:
        raise ValueError("aspect ratio must be positive")
    if near <= 0.0 or far <= near:
        raise ValueError(f"unusable clip range near={near} far={far}")
    half = math.radians(clamp(vfov_deg, 1e-3, 179.999)) * 0.5
    focal = 1.0 / math.tan(half)
    return (
        (focal / aspect, 0.0, 0.0, 0.0),
        (0.0, focal, 0.0, 0.0),
        (0.0, 0.0, (far + near) / (near - far), 2.0 * far * near / (near - far)),
        (0.0, 0.0, -1.0, 0.0),
    )


def fit_rect_to_aspect(rect, aspect):
    """The largest sub-rectangle of ``rect`` with the given aspect ratio.

    Used when the calibrated lens does not have the shape of the quadrant: the
    picture is letterboxed rather than stretched, because a stretched MRC layer
    will not line up with the filmed footage.
    """
    if aspect <= 0.0:
        raise ValueError("aspect ratio must be positive")
    if rect.width <= 0 or rect.height <= 0:
        return Rect(rect.x, rect.y, 0, 0)
    if rect.aspect > aspect:  # rect is too wide - pillarbox
        width = int(round(rect.height * aspect))
        width = min(width, rect.width)
        return Rect(rect.x + (rect.width - width) // 2, rect.y, width, rect.height)
    height = int(round(rect.width / aspect))
    height = min(height, rect.height)
    return Rect(rect.x, rect.y + (rect.height - height) // 2, rect.width, height)


def vfov_for_aspect(vfov_deg, source_aspect, target_aspect):
    """Vertical FOV that keeps the *horizontal* FOV when the shape changes.

    Rendering a 4:3 calibration into a 16:9 quadrant should widen the picture,
    not crop it; holding the horizontal FOV fixed and re-deriving the vertical
    one is the conversion that does that.
    """
    if source_aspect <= 0.0 or target_aspect <= 0.0:
        raise ValueError("aspect ratios must be positive")
    half_v = math.radians(clamp(vfov_deg, 1e-3, 179.999)) * 0.5
    half_h = math.atan(math.tan(half_v) * source_aspect)
    new_half_v = math.atan(math.tan(half_h) / target_aspect)
    return math.degrees(new_half_v * 2.0)


# --------------------------------------------------------------------------
# the per-frame plan
# --------------------------------------------------------------------------


class RenderPass:
    """One draw the backend has to perform to fill one quadrant."""

    __slots__ = ("quadrant", "rect", "mode", "near", "far", "clip_distance", "clear_colour")

    def __init__(self, quadrant, rect, mode, near, far, clip_distance=None,
                 clear_colour=(0.0, 0.0, 0.0, 0.0)):
        self.quadrant = quadrant
        self.rect = rect
        self.mode = mode
        self.near = float(near)
        self.far = float(far)
        #: Distance at which everything further away is erased, or ``None`` for
        #: a pass that draws the whole depth range.
        self.clip_distance = clip_distance
        self.clear_colour = tuple(clear_colour)

    @property
    def clipped(self):
        return self.clip_distance is not None

    def as_dict(self):
        return {
            "quadrant": self.quadrant,
            "rect": self.rect.as_tuple(),
            "mode": self.mode,
            "near": self.near,
            "far": self.far,
            "clip_distance": self.clip_distance,
        }

    def __repr__(self):
        return (
            f"RenderPass({self.quadrant!r}, {self.rect!r}, mode={self.mode!r}, "
            f"clip_distance={self.clip_distance})"
        )


class FramePlan:
    """Everything a backend needs to draw one MRC frame.

    Produced by :meth:`QuadrantCompositor.plan`, which is pure: give it two
    poses and it tells you the rectangles, the split, the projection and the
    passes without touching a GPU.
    """

    __slots__ = (
        "layout", "camera_pose", "hmd_pose", "projection", "vfov_deg", "aspect",
        "near", "far", "split", "clip_distance", "background_near", "chroma_key",
        "passes", "frame_divisor",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    def pass_for(self, quadrant):
        for render_pass in self.passes:
            if render_pass.quadrant == quadrant:
                return render_pass
        return None

    def as_dict(self):
        return {
            "layout": self.layout.as_dict(),
            "camera_pose": self.camera_pose.as_dict() if self.camera_pose else None,
            "vfov_deg": self.vfov_deg,
            "aspect": self.aspect,
            "near": self.near,
            "far": self.far,
            "split": self.split,
            "clip_distance": self.clip_distance,
            "background_near": self.background_near,
            "chroma_key": list(self.chroma_key),
            "passes": [item.as_dict() for item in self.passes],
        }

    def __repr__(self):
        return (
            f"FramePlan(layout={self.layout!r}, split={self.split:.3f}, "
            f"clip_distance={self.clip_distance:.3f})"
        )


class QuadrantCompositor:
    """Turns a calibration plus two poses into a four-quadrant frame plan.

    The compositor owns no GPU state at all; it is the arithmetic the renderer
    needs.  Swap in a new :class:`~xrmrc.externalcamera.ExternalCameraConfig`
    with :meth:`set_config` when the file changes on disk, and a new resolution
    with :meth:`set_resolution` when the window is resized — both are cheap.
    """

    def __init__(self, config=None, width=1920, height=1080,
                 origin=ORIGIN_TOP_LEFT,
                 fourth_quadrant=FOURTH_FIRST_PERSON,
                 background_mode=BACKGROUND_FULL_SCENE,
                 lens=None):
        if fourth_quadrant not in FOURTH_MODES:
            raise ValueError(f"unknown fourth quadrant mode '{fourth_quadrant}'")
        if background_mode not in BACKGROUND_MODES:
            raise ValueError(f"unknown background mode '{background_mode}'")
        self.config = config or externalcamera.default_config()
        self.layout = QuadrantLayout(width, height, origin)
        self.fourth_quadrant = fourth_quadrant
        self.background_mode = background_mode
        #: Optional lens override.  When set, its vertical FOV wins over the
        #: ``fov`` in the calibration file, which is what lets the in-VR menu
        #: change the shot without rewriting the user's calibration.
        self.lens = lens
        self.frames_planned = 0

    # -- configuration --------------------------------------------------

    def set_config(self, config):
        self.config = config
        return self

    def set_resolution(self, width, height, origin=None):
        self.layout = QuadrantLayout(
            width, height, origin or self.layout.origin
        )
        return self.layout

    def set_lens(self, lens):
        self.lens = lens
        return self

    # -- derived --------------------------------------------------------

    @property
    def vfov_deg(self):
        """Vertical FOV to render with, adjusted to the quadrant's shape.

        The calibration states a vertical FOV *for the lens it was measured
        with*.  If the output quadrant has a different aspect ratio, honouring
        the vertical FOV literally would change the horizontal FOV and the
        rendered layer would no longer line up with the filmed footage, so the
        horizontal FOV is what is held fixed.
        """
        source = self.lens
        if source is None:
            return self.config.fov
        return vfov_for_aspect(source.vfov_deg, source.aspect, self.layout.aspect)

    @property
    def near(self):
        return self.config.near

    @property
    def far(self):
        return self.config.far

    def content_rect(self, quadrant):
        """The quadrant rectangle, letterboxed to the lens if one is set."""
        rect = self.layout.rect(quadrant)
        if self.lens is None:
            return rect
        return fit_rect_to_aspect(rect, self.lens.aspect)

    # -- the plan -------------------------------------------------------

    def plan(self, camera_pose, hmd_pose=None):
        """The :class:`FramePlan` for one frame.  Pure; no side effects."""
        config = self.config
        near = config.near
        far = config.far
        split = split_distance(
            camera_pose, hmd_pose, config.hmd_offset, near, far
        )
        clip = foreground_clip_distance(split, config.near_offset, near, far)
        background_near = background_near_distance(
            split, config.far_offset, near, far, self.background_mode
        )
        aspect = self.layout.aspect
        vfov = self.vfov_deg
        matrix = perspective_matrix(vfov, aspect, near, far)

        rects = self.layout.rects
        chroma = config.chroma_key
        passes = [
            RenderPass(
                QUADRANT_FOREGROUND_COLOUR,
                rects[QUADRANT_FOREGROUND_COLOUR],
                PASS_COLOUR,
                near,
                far,
                clip_distance=clip,
                clear_colour=(0.0, 0.0, 0.0, 0.0),
            ),
            RenderPass(
                QUADRANT_FOREGROUND_ALPHA,
                rects[QUADRANT_FOREGROUND_ALPHA],
                PASS_ALPHA,
                near,
                far,
                clip_distance=clip,
                clear_colour=(0.0, 0.0, 0.0, 0.0),
            ),
            RenderPass(
                QUADRANT_BACKGROUND_COLOUR,
                rects[QUADRANT_BACKGROUND_COLOUR],
                PASS_COLOUR,
                background_near,
                far,
                clip_distance=None,
                clear_colour=(chroma[0], chroma[1], chroma[2], 1.0),
            ),
        ]
        if self.fourth_quadrant == FOURTH_FIRST_PERSON:
            passes.append(
                RenderPass(
                    QUADRANT_FOURTH,
                    rects[QUADRANT_FOURTH],
                    PASS_FIRST_PERSON,
                    near,
                    far,
                )
            )
        elif self.fourth_quadrant == FOURTH_BACKGROUND_ALPHA:
            passes.append(
                RenderPass(
                    QUADRANT_FOURTH,
                    rects[QUADRANT_FOURTH],
                    PASS_ALPHA,
                    background_near,
                    far,
                )
            )
        else:
            passes.append(
                RenderPass(
                    QUADRANT_FOURTH, rects[QUADRANT_FOURTH], PASS_BLANK, near, far
                )
            )

        self.frames_planned += 1
        return FramePlan(
            layout=self.layout,
            camera_pose=camera_pose,
            hmd_pose=hmd_pose,
            projection=matrix,
            vfov_deg=vfov,
            aspect=aspect,
            near=near,
            far=far,
            split=split,
            clip_distance=clip,
            background_near=background_near,
            chroma_key=chroma,
            passes=passes,
            frame_divisor=config.frame_divisor,
        )

    def should_render(self, frame_index):
        """Frame-skip gate, matching ``SteamVR_Render.RenderExternalCamera``."""
        divisor = self.config.frame_divisor
        return divisor <= 1 or int(frame_index) % divisor == 0

    def describe(self):
        return {
            "layout": self.layout.as_dict(),
            "fourth_quadrant": self.fourth_quadrant,
            "background_mode": self.background_mode,
            "vfov_deg": self.vfov_deg,
            "near": self.near,
            "far": self.far,
            "frames_planned": self.frames_planned,
        }


# --------------------------------------------------------------------------
# the Coin3D / OpenGL side
# --------------------------------------------------------------------------


class CoinQuadrantRenderer:
    """Draws a :class:`FramePlan` with the viewer's Coin3D scene manager.

    This is deliberately the only part of the compositor that knows about
    ``pivy.coin``, and it holds no scenegraph of its own: it borrows the
    third-person camera and the TPP scene root that ``xrcore.commonXR`` already
    builds (``setup_tpp_camera`` / ``setup_tpp_camera_scene``), points them
    where the plan says and renders four viewports into the existing
    ``fbo_tpp``.  The hooks it needs are listed at the end of
    ``Resources/doc/MIXED_REALITY_CAPTURE.md``.

    The foreground passes need one thing the TPP camera does not have: a way to
    erase everything beyond the split plane.  The reference implementation does
    it with a huge quad on a "clear everything" material rather than by pulling
    the far clip plane in, because moving the clip plane also moves the shadows.
    We have the same choice and make the same one; :meth:`_clip_node` builds
    that quad lazily.
    """

    def __init__(self, widget=None):
        self.widget = widget
        self._clip_separator = None
        self._clip_transform = None
        self._clip_switch = None
        self._clip_colour = None
        self.frames_rendered = 0
        self.last_error = None

    # -- lifecycle ------------------------------------------------------

    def attach(self, widget):
        self.widget = widget
        return self

    def detach(self):
        self.widget = None
        self._clip_separator = None
        self._clip_transform = None
        self._clip_switch = None
        self._clip_colour = None

    @property
    def ready(self):
        widget = self.widget
        return widget is not None and getattr(widget, "tpp_cam_root", None) is not None

    # -- the clip quad --------------------------------------------------

    def _clip_node(self):
        """A large unlit quad that clears colour and alpha behind itself."""
        if self._clip_separator is not None:
            return self._clip_separator
        from pivy.coin import (
            SO_SWITCH_NONE,
            SoBaseColor,
            SoCoordinate3,
            SoFaceSet,
            SoLightModel,
            SoSeparator,
            SoSwitch,
            SoTransform,
        )

        separator = SoSeparator()
        transform = SoTransform()
        material = SoBaseColor()
        lighting = SoLightModel()
        lighting.model = SoLightModel.BASE_COLOR
        coordinates = SoCoordinate3()
        half = 500.0
        coordinates.point.setValues(
            0,
            4,
            [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)],
        )
        face = SoFaceSet()
        face.numVertices.setValue(4)
        separator.addChild(transform)
        separator.addChild(lighting)
        separator.addChild(material)
        separator.addChild(coordinates)
        separator.addChild(face)

        switch = SoSwitch()
        switch.whichChild = SO_SWITCH_NONE
        switch.addChild(separator)

        self._clip_separator = separator
        self._clip_transform = transform
        self._clip_switch = switch
        self._clip_colour = material
        return separator

    def clip_switch(self):
        """The ``SoSwitch`` the viewer should add in front of the TPP scene."""
        self._clip_node()
        return self._clip_switch

    def _place_clip(self, plan, enabled):
        from pivy.coin import SO_SWITCH_ALL, SO_SWITCH_NONE, SbVec3f

        self._clip_node()
        if not enabled or plan.camera_pose is None:
            self._clip_switch.whichChild = SO_SWITCH_NONE
            return
        pose = plan.camera_pose
        forward = pose.forward()
        centre = (
            pose.position[0] + forward[0] * plan.clip_distance,
            pose.position[1] + forward[1] * plan.clip_distance,
            pose.position[2] + forward[2] * plan.clip_distance,
        )
        self._clip_transform.translation.setValue(SbVec3f(*centre))
        self._clip_transform.rotation.setValue(*_coin_rotation(pose.orientation))
        colour = plan.chroma_key
        self._clip_colour.rgb.setValue(colour[0], colour[1], colour[2])
        self._clip_switch.whichChild = SO_SWITCH_ALL

    # -- drawing --------------------------------------------------------

    def render(self, plan):
        """Render the four quadrants.  Returns True when it actually drew.

        Never raises into the XR render loop: a failed capture frame is worth a
        dropped frame, not a dropped session.
        """
        if not self.ready or plan is None or not plan.layout.valid:
            return False
        try:
            self._render(plan)
        except Exception as exc:  # pragma: no cover - needs a live GL context
            self.last_error = repr(exc)
            return False
        self.frames_rendered += 1
        return True

    def _render(self, plan):  # pragma: no cover - needs a live GL context
        widget = self.widget
        manager = widget.m_sceneManager
        viewport = widget.vp_reg
        camera = widget.tpp_camera

        self._apply_camera(camera, plan)
        for render_pass in plan.passes:
            if render_pass.mode == PASS_BLANK:
                continue
            rect = render_pass.rect
            self._place_clip(plan, render_pass.clipped)
            camera.nearDistance.setValue(render_pass.near)
            camera.farDistance.setValue(render_pass.far)
            viewport.setViewportPixels(rect.x, rect.y, rect.width, rect.height)
            manager.setViewportRegion(viewport)
            if render_pass.mode == PASS_FIRST_PERSON:
                manager.setSceneGraph(widget.root_scene[0])
            else:
                manager.setSceneGraph(widget.tpp_cam_root)
            manager.render()
        self._place_clip(plan, False)

    def _apply_camera(self, camera, plan):  # pragma: no cover - needs Coin
        from pivy.coin import SbVec3f

        pose = plan.camera_pose
        if pose is None:
            return
        camera.position.setValue(SbVec3f(*pose.position))
        camera.orientation.setValue(*_coin_rotation(pose.orientation))
        camera.heightAngle.setValue(math.radians(plan.vfov_deg))
        camera.aspectRatio.setValue(plan.aspect)


def _coin_rotation(quaternion):
    """``SbRotation`` takes ``(x, y, z, w)`` — the same order we use."""
    return (quaternion[0], quaternion[1], quaternion[2], quaternion[3])
