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
"""``xrmrc`` — mixed reality capture for the FreeCAD XR workbench.

Filming someone inside a VR scene means drawing that scene twice more: once for
the part in front of them and once for the part behind, so a compositor can put
the filmed person between the two.  This package produces exactly that, in the
four-quadrant layout the SteamVR/OBS/LIV ecosystem settled on years ago, driven
by the same ``externalcamera.cfg`` calibration every other tool in that
ecosystem reads.

It is built on the third-person camera the workbench already has — a tracked
device, its own perspective camera, its own framebuffer — rather than beside
it; a Vive tracker calibrated for ``Toggle third-person camera`` needs no second
calibration here.

Public API
----------

Calibration
    :class:`~xrmrc.externalcamera.ExternalCameraConfig`,
    :func:`~xrmrc.externalcamera.parse`, :func:`~xrmrc.externalcamera.load`,
    :func:`~xrmrc.externalcamera.dumps`, :func:`~xrmrc.externalcamera.save`,
    :func:`~xrmrc.externalcamera.validate`,
    :func:`~xrmrc.externalcamera.find_config`

Camera
    :class:`~xrmrc.camera.Pose`, :class:`~xrmrc.camera.MRCCamera`,
    :class:`~xrmrc.camera.LensSettings`, :class:`~xrmrc.camera.PoseSmoother`,
    :class:`~xrmrc.camera.FixedPose`, :class:`~xrmrc.camera.TrackedPose`,
    :class:`~xrmrc.camera.FollowHmd`, :class:`~xrmrc.camera.Orbit`

Compositor
    :class:`~xrmrc.compositor.QuadrantCompositor`,
    :class:`~xrmrc.compositor.QuadrantLayout`,
    :class:`~xrmrc.compositor.FramePlan`,
    :func:`~xrmrc.compositor.quadrant_rects`,
    :func:`~xrmrc.compositor.split_distance`,
    :class:`~xrmrc.compositor.CoinQuadrantRenderer`

Output
    :class:`~xrmrc.output.OutputPipeline`, :class:`~xrmrc.output.FrameSpec`,
    :class:`~xrmrc.output.RawFrameSink`,
    :class:`~xrmrc.output.SpectatorWindowSink`,
    :class:`~xrmrc.output.ImageSequenceSink`, :class:`~xrmrc.output.AsyncSink`

LIV
    :func:`~xrmrc.liv.liv_available`, :func:`~xrmrc.liv.probe`,
    :class:`~xrmrc.liv.LivStatus`, :class:`~xrmrc.liv.LivIntegration`

Session
    :class:`~xrmrc.session.MRCSession`, :data:`~xrmrc.session.MODES`

No module here imports ``FreeCAD``, ``FreeCADGui``, ``PySide`` or ``pivy.coin``
at import time; the two places that need them (the Coin renderer and the
spectator sink) do it inside a function, so the whole package imports and tests
without FreeCAD present (ARCHITECTURE.md §6).
"""

from . import camera, compositor, externalcamera, liv, output, session
from .camera import (
    CameraContext,
    FixedPose,
    FollowHmd,
    LensSettings,
    MRCCamera,
    Orbit,
    Pose,
    PoseSmoother,
    PoseSource,
    TrackedPose,
    make_source,
)
from .compositor import (
    BACKGROUND_BEYOND_SPLIT,
    BACKGROUND_FULL_SCENE,
    FOURTH_BACKGROUND_ALPHA,
    FOURTH_FIRST_PERSON,
    ORIGIN_BOTTOM_LEFT,
    ORIGIN_TOP_LEFT,
    QUADRANT_BACKGROUND_COLOUR,
    QUADRANT_FOREGROUND_ALPHA,
    QUADRANT_FOREGROUND_COLOUR,
    QUADRANT_FOURTH,
    QUADRANTS,
    CoinQuadrantRenderer,
    FramePlan,
    QuadrantCompositor,
    QuadrantLayout,
    Rect,
    RenderPass,
    foreground_clip_distance,
    perspective_matrix,
    quadrant_rects,
    split_distance,
)
from .externalcamera import (
    CONFIG_FILENAME,
    ExternalCameraConfig,
    ExternalCameraError,
    Issue,
    default_config,
    dumps,
    find_config,
    load,
    parse,
    save,
    validate,
)
from .liv import (
    MODE_EXTERNAL_CAMERA,
    MODE_NATIVE_SDK,
    MODE_UNAVAILABLE,
    Check,
    LivIntegration,
    LivStatus,
    liv_available,
    probe,
)
from .output import (
    PIXEL_BGRA8,
    PIXEL_RGB8,
    PIXEL_RGBA8,
    AsyncSink,
    CallbackSink,
    Frame,
    FrameSink,
    FrameSpec,
    ImageSequenceSink,
    NullSink,
    OutputPipeline,
    RateLimiter,
    RawFrameSink,
    SinkStats,
    SpectatorWindowSink,
)
from .session import (
    CAPTURE_MODES,
    MODE_LIV,
    MODE_OFF,
    MODE_QUADRANT_MRC,
    MODE_TPP,
    MODES,
    ConfigWatcher,
    MRCSession,
)

__version__ = "1.0"

__all__ = [
    # modules
    "camera", "compositor", "externalcamera", "liv", "output", "session",
    # calibration
    "ExternalCameraConfig", "ExternalCameraError", "Issue", "CONFIG_FILENAME",
    "parse", "load", "dumps", "save", "validate", "find_config",
    "default_config",
    # camera
    "Pose", "MRCCamera", "LensSettings", "PoseSmoother", "PoseSource",
    "CameraContext", "FixedPose", "TrackedPose", "FollowHmd", "Orbit",
    "make_source",
    # compositor
    "QuadrantCompositor", "QuadrantLayout", "FramePlan", "RenderPass", "Rect",
    "CoinQuadrantRenderer", "quadrant_rects", "split_distance",
    "foreground_clip_distance", "perspective_matrix",
    "QUADRANTS", "QUADRANT_FOREGROUND_COLOUR", "QUADRANT_FOREGROUND_ALPHA",
    "QUADRANT_BACKGROUND_COLOUR", "QUADRANT_FOURTH",
    "FOURTH_FIRST_PERSON", "FOURTH_BACKGROUND_ALPHA",
    "ORIGIN_TOP_LEFT", "ORIGIN_BOTTOM_LEFT",
    "BACKGROUND_FULL_SCENE", "BACKGROUND_BEYOND_SPLIT",
    # output
    "OutputPipeline", "FrameSpec", "Frame", "FrameSink", "SinkStats",
    "NullSink", "CallbackSink", "RawFrameSink", "ImageSequenceSink",
    "SpectatorWindowSink", "AsyncSink", "RateLimiter",
    "PIXEL_RGBA8", "PIXEL_RGB8", "PIXEL_BGRA8",
    # liv
    "liv_available", "probe", "LivStatus", "LivIntegration", "Check",
    "MODE_UNAVAILABLE", "MODE_EXTERNAL_CAMERA", "MODE_NATIVE_SDK",
    # session
    "MRCSession", "ConfigWatcher", "MODES", "CAPTURE_MODES",
    "MODE_OFF", "MODE_TPP", "MODE_QUADRANT_MRC", "MODE_LIV",
    "__version__",
]
