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
"""The capture controller: modes, hot reload and status.

:class:`MRCSession` is the object the GUI commands, the in-VR menu and the XR
render loop all talk to.  It holds the mode, the camera, the compositor and the
output pipeline, watches ``externalcamera.cfg`` for changes, and answers
:meth:`status` for anything that wants to display what is going on.

It is deliberately a plain Python object with no FreeCAD imports: it can be
driven entirely from a test, which is how the mode transitions and the hot
reload in ``Tests/test_mrc.py`` are checked.

Modes
-----

``OFF``
    Nothing is captured.

``TPP``
    The existing tracked third-person camera: one full-frame view from the
    capture camera, no quadrants, no alpha.  This is what
    ``Toggle third-person camera`` already does, expressed through the same
    controller so a user can move between it and MRC without restarting.

``QUADRANT_MRC``
    The four-quadrant frame of :mod:`xrmrc.compositor`, for OBS or any other
    compositor that understands the convention.

``LIV``
    The same frame, plus the ``externalcamera.cfg`` handling LIV's legacy
    quadrant mode expects.  ``LIV`` differs from ``QUADRANT_MRC`` in what it
    guarantees about the calibration file, not in what it draws.
"""

import os
import time

from . import compositor as compositor_mod
from . import externalcamera, liv, output
from .camera import CameraContext, MRCCamera, TrackedPose

__all__ = [
    "MODE_OFF",
    "MODE_TPP",
    "MODE_QUADRANT_MRC",
    "MODE_LIV",
    "MODES",
    "CAPTURE_MODES",
    "ConfigWatcher",
    "MRCSession",
]

MODE_OFF = "OFF"
MODE_TPP = "TPP"
MODE_QUADRANT_MRC = "QUADRANT_MRC"
MODE_LIV = "LIV"

MODES = (MODE_OFF, MODE_TPP, MODE_QUADRANT_MRC, MODE_LIV)
#: The modes that actually produce frames.
CAPTURE_MODES = (MODE_TPP, MODE_QUADRANT_MRC, MODE_LIV)


class ConfigWatcher:
    """Polls ``externalcamera.cfg`` and reloads it when it changes.

    Polling rather than a file-system watcher, for three reasons: it needs no
    platform-specific machinery, it costs one ``stat`` per check and the check
    is rate limited, and it cannot wedge the render loop on a notification
    thread.  ``SteamVR_ExternalCamera`` uses a ``FileSystemWatcher``; the
    observable behaviour — an edited calibration takes effect without a restart
    — is the same.

    A file that fails to parse leaves the previous configuration in place and
    is recorded in :attr:`last_error`.  A half-written file is common (some
    calibration tools truncate and rewrite), so treating a bad parse as fatal
    would make the feature flaky.
    """

    def __init__(self, path=None, interval=1.0):
        self.path = os.fspath(path) if path else None
        self.interval = float(interval)
        self.config = None
        self.last_error = None
        self.reload_count = 0
        self.error_count = 0
        self._signature = None
        self._next_check = 0.0

    # -- plumbing -------------------------------------------------------

    def set_path(self, path):
        self.path = os.fspath(path) if path else None
        self._signature = None
        self._next_check = 0.0
        return self.path

    def _stat_signature(self):
        if not self.path:
            return None
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        # Size as well as mtime: an editor that rewrites within the same
        # coarse mtime tick would otherwise be missed.
        return (info.st_mtime_ns, info.st_size)

    @property
    def exists(self):
        return bool(self.path) and os.path.isfile(self.path)

    # -- reloading ------------------------------------------------------

    def load(self, force=False):
        """Read the file now.  Returns the config, or ``None`` if unchanged."""
        signature = self._stat_signature()
        if signature is None:
            if self.config is not None and not force:
                return None
            self.last_error = f"no such file: {self.path}"
            return None
        if signature == self._signature and not force:
            return None
        try:
            config = externalcamera.load(self.path, base=self.config)
        except (OSError, ValueError) as exc:
            self.error_count += 1
            self.last_error = repr(exc)
            self._signature = signature
            return None
        blocking = [issue for issue in config.validate() if issue.is_error]
        if blocking:
            self.error_count += 1
            self.last_error = "; ".join(str(issue) for issue in blocking)
            self._signature = signature
            return None
        self._signature = signature
        if config == self.config:
            # The file changed but the calibration did not — a tool rewriting
            # it in place, or a truncation that left every value inherited.
            self.last_error = None
            return None
        self.config = config
        self.reload_count += 1
        self.last_error = None
        return config

    def poll(self, now=None):
        """Rate-limited :meth:`load`.  Safe to call every frame."""
        now = time.monotonic() if now is None else float(now)
        if now < self._next_check:
            return None
        self._next_check = now + self.interval
        return self.load()

    def status(self):
        return {
            "path": self.path,
            "exists": self.exists,
            "reloads": self.reload_count,
            "errors": self.error_count,
            "last_error": self.last_error,
        }


class MRCSession:
    """Start, stop and drive mixed reality capture."""

    def __init__(self, config_paths=(), camera=None, pipeline=None,
                 width=1920, height=1080, fps=30.0,
                 origin=compositor_mod.ORIGIN_TOP_LEFT,
                 watch_interval=1.0, use_camera_lens=False):
        self.config_paths = tuple(config_paths)
        self.mode = MODE_OFF
        self.camera = camera or MRCCamera()
        self.watcher = ConfigWatcher(
            externalcamera.find_config(self.config_paths), watch_interval
        )
        self.config = self.watcher.load(force=True) or externalcamera.default_config()
        #: When False (the default) the calibration file's ``fov`` decides the
        #: shot, which is what every other tool in the ecosystem assumes.  Set
        #: it to True to let the camera's own :class:`LensSettings` — the
        #: ``TPPCam*`` preferences — override the file instead, which is how
        #: the in-VR menu can reframe without editing someone's calibration.
        self.use_camera_lens = bool(use_camera_lens)
        self.compositor = compositor_mod.QuadrantCompositor(
            self.config, width, height, origin,
            lens=self.camera.lens if self.use_camera_lens else None,
        )
        self.pipeline = pipeline or output.OutputPipeline(fps=fps)
        self.liv = liv.LivIntegration(self.config_paths, self.watcher.path)
        self.renderer = None
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        # counters
        self.frames_planned = 0
        self.frames_skipped = 0
        self.started_at = None
        self.last_plan = None
        self.last_error = None
        self._mode_history = [MODE_OFF]

    # ------------------------------------------------------------------
    # mode
    # ------------------------------------------------------------------

    @property
    def active(self):
        return self.mode in CAPTURE_MODES

    @property
    def mode_history(self):
        return tuple(self._mode_history)

    def set_mode(self, mode):
        """Move to ``mode``.  Returns the mode actually in force.

        Every transition goes through here, including OFF, so starting and
        stopping the output pipeline happens in exactly one place.  Asking for
        the mode you are already in is a no-op rather than a restart.
        """
        if mode not in MODES:
            raise ValueError(
                f"unknown capture mode '{mode}'; known: {', '.join(MODES)}"
            )
        if mode == self.mode:
            return self.mode
        previous = self.mode
        if previous in CAPTURE_MODES:
            self._stop_output()
        self.mode = mode
        self._mode_history.append(mode)
        if mode in CAPTURE_MODES:
            self._start_output()
        else:
            self.started_at = None
        return self.mode

    def start(self, mode=MODE_QUADRANT_MRC):
        """Begin capturing in ``mode`` (quadrant MRC unless told otherwise)."""
        if mode == MODE_OFF:
            raise ValueError("start() needs a capturing mode; use stop() instead")
        return self.set_mode(mode)

    def stop(self):
        return self.set_mode(MODE_OFF)

    def toggle(self, mode=MODE_QUADRANT_MRC):
        """Convenience for a menu button: in that mode, or off."""
        return self.stop() if self.mode == mode else self.start(mode)

    def cycle(self):
        """Step through the modes — what the in-VR menu button does."""
        index = MODES.index(self.mode)
        return self.set_mode(MODES[(index + 1) % len(MODES)])

    # -- output lifecycle -----------------------------------------------

    def _frame_spec(self):
        return output.FrameSpec(self.width, self.height, output.PIXEL_RGBA8, self.fps)

    def _start_output(self):
        self.started_at = time.monotonic()
        if self.mode == MODE_LIV:
            try:
                config, path = self.liv.prepare()
            except OSError as exc:
                self.last_error = repr(exc)
            else:
                if path:
                    self.watcher.set_path(path)
                if config is not None:
                    self.set_config(config)
        self.pipeline.open(self._frame_spec())
        return True

    def _stop_output(self):
        self.pipeline.close()
        self.started_at = None

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    def set_config(self, config):
        self.config = config
        self.compositor.set_config(config)
        return config

    def set_resolution(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self.compositor.set_resolution(width, height)
        if self.active:
            # Reopening is the honest way to change frame size: a sink that has
            # already told a downstream consumer 1920x1080 cannot start sending
            # something else halfway through.
            self.pipeline.close()
            self.pipeline.open(self._frame_spec())
        return self.compositor.layout

    def set_fps(self, fps):
        self.fps = float(fps)
        self.pipeline.limiter.fps = self.fps
        return self.fps

    def set_camera(self, camera):
        self.camera = camera
        self.compositor.set_lens(camera.lens if self.use_camera_lens else None)
        return camera

    def set_lens_override(self, enabled):
        """Choose between the calibration's ``fov`` and the camera's lens."""
        self.use_camera_lens = bool(enabled)
        self.compositor.set_lens(self.camera.lens if enabled else None)
        return self.use_camera_lens

    def add_sink(self, sink):
        return self.pipeline.add_sink(sink)

    def attach_renderer(self, renderer):
        """Give the session the GPU backend that draws a :class:`FramePlan`."""
        self.renderer = renderer
        return renderer

    def reload_config(self, force=True):
        """Re-read the calibration now, ignoring the poll interval."""
        config = self.watcher.load(force=force)
        if config is not None:
            self.set_config(config)
        return config

    # ------------------------------------------------------------------
    # per frame
    # ------------------------------------------------------------------

    def submit_tracker_pose(self, pose, valid=True):
        """Forward a located tracker pose to a tracked camera source."""
        source = self.camera.source
        if isinstance(source, TrackedPose):
            return source.submit(pose, valid)
        return False

    def update(self, dt, hmd_pose=None, tracker_pose=None, now=None):
        """Advance one frame and return the :class:`FramePlan`, or ``None``.

        Returns ``None`` — cheaply — whenever there is nothing to draw: the
        session is off, the rate limiter says this frame is not wanted, the
        calibration's own frame skip says so, or the camera has no pose yet.
        The caller is the XR render loop, so this must never raise; anything
        unexpected is recorded on :attr:`last_error` and swallowed.
        """
        if not self.active:
            return None
        try:
            return self._update(dt, hmd_pose, tracker_pose, now)
        except Exception as exc:  # pragma: no cover - defensive
            self.last_error = repr(exc)
            return None

    def _update(self, dt, hmd_pose, tracker_pose, now):
        reloaded = self.watcher.poll(now)
        if reloaded is not None:
            self.set_config(reloaded)

        context = CameraContext(hmd_pose, tracker_pose, None, now or time.monotonic())
        pose = self.camera.update(dt, context)
        if pose is None:
            self.frames_skipped += 1
            return None

        if not self.compositor.should_render(self.frames_planned + self.frames_skipped):
            self.frames_skipped += 1
            return None
        if not self.pipeline.wants_frame(now):
            self.frames_skipped += 1
            return None

        if self.mode == MODE_TPP:
            plan = self._tpp_plan(pose)
        else:
            plan = self.compositor.plan(pose, hmd_pose)
        self.frames_planned += 1
        self.last_plan = plan
        return plan

    def _tpp_plan(self, pose):
        """A single full-frame pass — the classic third-person camera.

        Reusing :class:`~xrmrc.compositor.FramePlan` for this means the backend
        has one thing to draw rather than two, and the difference between the
        third-person camera and MRC becomes what it should be: how many passes
        are in the list.

        The FOV here comes from the camera's lens regardless of
        :attr:`use_camera_lens`, because that lens *is* ``TPPCamVFov`` — the
        preference the existing third-person camera has always used.  Only
        quadrant MRC defers to the calibration file, which is where the rest of
        the ecosystem expects the number to come from.
        """
        layout = compositor_mod.QuadrantLayout(
            self.width, self.height, self.compositor.layout.origin
        )
        aspect = self.width / self.height if self.height else 1.0
        vfov = self.camera.lens.vfov_deg if self.camera.lens else self.config.fov
        near = self.config.near
        far = self.config.far
        full = compositor_mod.Rect(0, 0, self.width, self.height)
        single = compositor_mod.RenderPass(
            compositor_mod.QUADRANT_FOREGROUND_COLOUR,
            full,
            compositor_mod.PASS_COLOUR,
            near,
            far,
        )
        return compositor_mod.FramePlan(
            layout=layout,
            camera_pose=pose,
            hmd_pose=None,
            projection=compositor_mod.perspective_matrix(vfov, aspect, near, far),
            vfov_deg=vfov,
            aspect=aspect,
            near=near,
            far=far,
            split=far,
            clip_distance=far,
            background_near=near,
            chroma_key=self.config.chroma_key,
            passes=[single],
            frame_divisor=self.config.frame_divisor,
        )

    def render(self, plan):
        """Draw ``plan`` with the attached renderer, if there is one."""
        if plan is None or self.renderer is None:
            return False
        return bool(self.renderer.render(plan))

    def submit_frame(self, data, timestamp=None):
        """Hand captured pixels to the output pipeline."""
        return self.pipeline.submit(data, self._frame_spec(), timestamp)

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    @property
    def uptime(self):
        return 0.0 if self.started_at is None else time.monotonic() - self.started_at

    def status(self):
        """Everything a dialog, a menu label or a log line could want."""
        issues = self.config.validate()
        return {
            "mode": self.mode,
            "active": self.active,
            "uptime": self.uptime,
            "resolution": [self.width, self.height],
            "fps": self.fps,
            "frames_planned": self.frames_planned,
            "frames_skipped": self.frames_skipped,
            "camera": self.camera.describe(),
            "compositor": self.compositor.describe(),
            "config": {
                "path": self.watcher.path,
                "fov": self.config.fov,
                "near": self.config.near,
                "far": self.config.far,
                "hmd_offset": self.config.hmd_offset,
                "issues": [str(issue) for issue in issues],
            },
            "watcher": self.watcher.status(),
            "output": self.pipeline.status(),
            "liv": self.liv.describe() if self.mode == MODE_LIV else None,
            "last_error": self.last_error,
        }

    def summary(self):
        """A single line for the in-VR status label."""
        if not self.active:
            return "MRC: off"
        plan = self.last_plan
        split = f"{plan.split:.2f} m" if plan else "-"
        dropped = self.pipeline.dropped
        return (
            f"MRC: {self.mode}  {self.width}x{self.height}@{self.fps:g}  "
            f"split {split}  dropped {dropped}"
        )

    def __repr__(self):
        return f"MRCSession(mode={self.mode!r}, resolution={self.width}x{self.height})"
