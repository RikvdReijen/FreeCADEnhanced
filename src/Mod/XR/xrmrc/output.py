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
"""Where captured frames go.

The XR render loop has a hard deadline: miss it and the headset judders, which
is a much worse outcome than a dropped capture frame.  So the contract of every
sink in this module is **never block the caller**.  :meth:`FrameSink.submit`
either takes the frame immediately or refuses it and increments a counter;
anything that might take a while — writing to a pipe, encoding a PNG — is put
behind :class:`AsyncSink`, whose bounded queue drops frames instead of applying
back-pressure.

Three destinations are provided:

``SpectatorWindowSink``
    Hands the frame to the mirror window, which is the zero-configuration case:
    the operator sees the quadrant frame and captures that window in OBS.

``RawFrameSink``
    Writes raw pixels to a file, a FIFO or any other file-like object.  This is
    the "named target the user configures" path: point it at a named pipe and
    feed that to ``ffmpeg`` for a virtual camera, or at a file for offline work.

``ImageSequenceSink``
    Numbered ``.ppm`` files.  Uncompressed and stdlib-only (writing PNG here
    would duplicate ``xrpaint.raster``), useful for stills and calibration.

Plus :class:`CallbackSink` and :class:`NullSink` for tests and for wiring the
frames somewhere else entirely.

Pure stdlib.  Qt is imported lazily and only by the spectator sink.
"""

import os
import queue
import threading
import time

__all__ = [
    "FrameSpec",
    "Frame",
    "SinkStats",
    "FrameSink",
    "NullSink",
    "CallbackSink",
    "RawFrameSink",
    "ImageSequenceSink",
    "SpectatorWindowSink",
    "AsyncSink",
    "RateLimiter",
    "OutputPipeline",
    "PIXEL_RGBA8",
    "PIXEL_RGB8",
    "PIXEL_BGRA8",
    "PIXEL_FORMATS",
    "bytes_per_pixel",
]

PIXEL_RGBA8 = "rgba8"
PIXEL_RGB8 = "rgb8"
PIXEL_BGRA8 = "bgra8"

PIXEL_FORMATS = {
    PIXEL_RGBA8: 4,
    PIXEL_RGB8: 3,
    PIXEL_BGRA8: 4,
}


def bytes_per_pixel(pixel_format):
    try:
        return PIXEL_FORMATS[pixel_format]
    except KeyError:
        raise ValueError(f"unknown pixel format '{pixel_format}'") from None


class FrameSpec:
    """The shape of the frames a sink is about to receive."""

    __slots__ = ("width", "height", "pixel_format", "fps")

    def __init__(self, width, height, pixel_format=PIXEL_RGBA8, fps=30.0):
        self.width = int(width)
        self.height = int(height)
        self.pixel_format = pixel_format
        self.fps = float(fps)
        bytes_per_pixel(pixel_format)  # validate early

    @property
    def stride(self):
        return self.width * bytes_per_pixel(self.pixel_format)

    @property
    def frame_bytes(self):
        return self.stride * self.height

    def as_dict(self):
        return {
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "fps": self.fps,
        }

    def __eq__(self, other):
        if not isinstance(other, FrameSpec):
            return NotImplemented
        return (self.width, self.height, self.pixel_format, self.fps) == (
            other.width,
            other.height,
            other.pixel_format,
            other.fps,
        )

    def __hash__(self):
        return hash((self.width, self.height, self.pixel_format, self.fps))

    def __repr__(self):
        return (
            f"FrameSpec({self.width}x{self.height}, {self.pixel_format}, "
            f"{self.fps:g} fps)"
        )


class Frame:
    """One captured frame.

    ``data`` is whatever the producer had — ``bytes``, a ``memoryview``, a Qt
    image, ``None`` for a frame that only exists as a GPU texture.  Sinks that
    need real bytes check :attr:`has_pixels` and refuse politely otherwise.
    """

    __slots__ = ("data", "spec", "index", "timestamp")

    def __init__(self, data, spec, index=0, timestamp=None):
        self.data = data
        self.spec = spec
        self.index = int(index)
        self.timestamp = time.monotonic() if timestamp is None else float(timestamp)

    @property
    def has_pixels(self):
        return isinstance(self.data, (bytes, bytearray, memoryview))

    def as_bytes(self):
        if not self.has_pixels:
            raise TypeError("this frame does not carry pixel data")
        return bytes(self.data)

    def __repr__(self):
        return f"Frame(index={self.index}, spec={self.spec!r})"


class SinkStats:
    """Counters every sink keeps so a dropped frame is visible, not silent."""

    __slots__ = ("submitted", "written", "dropped", "errors", "last_error",
                 "opened", "last_write_time")

    def __init__(self):
        self.submitted = 0
        self.written = 0
        self.dropped = 0
        self.errors = 0
        self.last_error = None
        self.opened = False
        self.last_write_time = None

    def reset(self):
        self.submitted = 0
        self.written = 0
        self.dropped = 0
        self.errors = 0
        self.last_error = None
        self.last_write_time = None

    @property
    def drop_ratio(self):
        if self.submitted <= 0:
            return 0.0
        return self.dropped / self.submitted

    def as_dict(self):
        return {
            "submitted": self.submitted,
            "written": self.written,
            "dropped": self.dropped,
            "errors": self.errors,
            "drop_ratio": self.drop_ratio,
            "last_error": self.last_error,
            "opened": self.opened,
        }

    def __repr__(self):
        return (
            f"SinkStats(submitted={self.submitted}, written={self.written}, "
            f"dropped={self.dropped}, errors={self.errors})"
        )


class FrameSink:
    """Base class.  ``submit`` must return quickly and must not raise."""

    name = "sink"

    def __init__(self):
        self.stats = SinkStats()
        self.spec = None

    # -- lifecycle ------------------------------------------------------

    def open(self, spec):
        self.spec = spec
        self.stats.opened = True
        return True

    def close(self):
        self.stats.opened = False

    @property
    def is_open(self):
        return self.stats.opened

    # -- frames ---------------------------------------------------------

    def submit(self, frame):
        """Accept ``frame``.  Returns True when it was taken.

        Subclasses override :meth:`_write`; the bookkeeping, the "not open"
        case and the "it threw" case are handled here so no sink can leak an
        exception into the render loop.
        """
        self.stats.submitted += 1
        if not self.stats.opened:
            self.stats.dropped += 1
            return False
        try:
            accepted = self._write(frame)
        except Exception as exc:
            self.stats.errors += 1
            self.stats.dropped += 1
            self.stats.last_error = repr(exc)
            return False
        if accepted:
            self.stats.written += 1
            self.stats.last_write_time = frame.timestamp
        else:
            self.stats.dropped += 1
        return bool(accepted)

    def _write(self, frame):  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self):
        return {"sink": self.name, "spec": self.spec.as_dict() if self.spec else None,
                "stats": self.stats.as_dict()}


class NullSink(FrameSink):
    """Counts frames and throws them away."""

    name = "null"

    def _write(self, frame):
        return True


class CallbackSink(FrameSink):
    """Hands each frame to a callable.

    The callable is invoked on the caller's thread, so wrap this in an
    :class:`AsyncSink` if it does anything slow.
    """

    name = "callback"

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def _write(self, frame):
        result = self.callback(frame)
        return True if result is None else bool(result)


class RawFrameSink(FrameSink):
    """Raw pixels to a file, a FIFO, or any writable binary stream.

    This is the documented hand-off to OBS and friends.  The recipe is in
    ``MIXED_REALITY_CAPTURE.md``; the short version on Linux is::

        mkfifo /tmp/freecad-mrc
        ffmpeg -f rawvideo -pix_fmt rgba -s 1920x1080 -r 30 -i /tmp/freecad-mrc \\
               -pix_fmt yuv420p -f v4l2 /dev/video10

    with ``v4l2loopback`` providing ``/dev/video10``, which then shows up in
    OBS as an ordinary video capture device.

    Opening a FIFO blocks until a reader attaches, which would stall the render
    loop, so ``non_blocking`` (the default for a FIFO) opens it with
    ``O_NONBLOCK`` and simply drops frames until something is listening.
    """

    name = "raw"

    def __init__(self, target, non_blocking=None, close_target=None):
        super().__init__()
        self.target = target
        self._stream = None
        self._owns_stream = False
        self._non_blocking = non_blocking
        self._close_target = close_target

    @property
    def path(self):
        return self.target if isinstance(self.target, (str, bytes, os.PathLike)) else None

    def open(self, spec):
        self.spec = spec
        path = self.path
        if path is None:
            self._stream = self.target
            self._owns_stream = bool(self._close_target)
            self.stats.opened = True
            return True
        path = os.fspath(path)
        is_fifo = False
        try:
            import stat

            is_fifo = stat.S_ISFIFO(os.stat(path).st_mode)
        except OSError:
            is_fifo = False
        non_blocking = self._non_blocking
        if non_blocking is None:
            non_blocking = is_fifo
        try:
            if non_blocking and hasattr(os, "O_NONBLOCK"):
                flags = os.O_WRONLY | os.O_NONBLOCK
                descriptor = os.open(path, flags)
                self._stream = os.fdopen(descriptor, "wb", buffering=0)
            else:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                self._stream = open(path, "wb", buffering=0)
        except OSError as exc:
            self.stats.errors += 1
            self.stats.last_error = repr(exc)
            self._stream = None
            self.stats.opened = False
            return False
        self._owns_stream = True
        self.stats.opened = True
        return True

    def close(self):
        stream = self._stream
        self._stream = None
        self.stats.opened = False
        if stream is not None and self._owns_stream:
            try:
                stream.close()
            except OSError:
                pass

    def _write(self, frame):
        if self._stream is None or not frame.has_pixels:
            return False
        try:
            self._stream.write(frame.as_bytes())
        except BlockingIOError:
            # Nothing is reading the pipe yet, or the reader fell behind.
            return False
        except BrokenPipeError as exc:
            self.stats.last_error = repr(exc)
            self.close()
            return False
        return True


class ImageSequenceSink(FrameSink):
    """Numbered binary PPM files in a directory.

    PPM has no alpha, so an RGBA frame is written as RGB.  It is uncompressed
    and needs nothing beyond the stdlib, which is the point: this is for a
    handful of calibration stills, not for recording a session.
    """

    name = "image_sequence"

    def __init__(self, directory, prefix="mrc", limit=None):
        super().__init__()
        self.directory = os.fspath(directory)
        self.prefix = prefix
        self.limit = limit
        self.written_paths = []

    def open(self, spec):
        self.spec = spec
        try:
            os.makedirs(self.directory, exist_ok=True)
        except OSError as exc:
            self.stats.errors += 1
            self.stats.last_error = repr(exc)
            return False
        self.stats.opened = True
        return True

    def _write(self, frame):
        if not frame.has_pixels:
            return False
        if self.limit is not None and len(self.written_paths) >= self.limit:
            return False
        spec = frame.spec or self.spec
        if spec is None:
            return False
        pixels = frame.as_bytes()
        depth = bytes_per_pixel(spec.pixel_format)
        expected = spec.width * spec.height * depth
        if len(pixels) < expected:
            return False
        if spec.pixel_format == PIXEL_RGB8:
            body = pixels[:expected]
        else:
            swap = spec.pixel_format == PIXEL_BGRA8
            out = bytearray(spec.width * spec.height * 3)
            for index in range(spec.width * spec.height):
                source = index * depth
                target = index * 3
                if swap:
                    out[target] = pixels[source + 2]
                    out[target + 1] = pixels[source + 1]
                    out[target + 2] = pixels[source]
                else:
                    out[target] = pixels[source]
                    out[target + 1] = pixels[source + 1]
                    out[target + 2] = pixels[source + 2]
            body = bytes(out)
        path = os.path.join(
            self.directory, f"{self.prefix}_{frame.index:06d}.ppm"
        )
        header = f"P6\n{spec.width} {spec.height}\n255\n".encode("ascii")
        with open(path, "wb") as handle:
            handle.write(header)
            handle.write(body)
        self.written_paths.append(path)
        return True


class SpectatorWindowSink(FrameSink):
    """Shows the frame in the viewer's mirror window.

    The XR viewer already owns a ``QOpenGLWidget`` mirror and already knows how
    to blit a texture into it — that is exactly what the third-person camera
    does today.  So this sink does not build a window of its own; it asks the
    widget to present the MRC framebuffer instead of the eye framebuffer, which
    keeps a single presentation path and one place to get colour management
    wrong.  The ``present_mrc_frame`` hook is listed in
    ``MIXED_REALITY_CAPTURE.md``.
    """

    name = "spectator"

    def __init__(self, widget=None):
        super().__init__()
        self.widget = widget

    def attach(self, widget):
        self.widget = widget
        return self

    def open(self, spec):
        self.spec = spec
        self.stats.opened = self.widget is not None
        return self.stats.opened

    def _write(self, frame):
        widget = self.widget
        if widget is None:
            return False
        present = getattr(widget, "present_mrc_frame", None)
        if present is None:
            return False
        result = present(frame)
        return True if result is None else bool(result)


class AsyncSink(FrameSink):
    """Runs another sink on a worker thread behind a bounded queue.

    This is what makes "capture must never stall the XR render loop" true for
    sinks that can be slow.  ``submit`` does a ``put_nowait``; when the queue is
    full the frame is dropped and counted, and the render loop carries on.

    ``drop_oldest`` decides *which* frame is lost when the sink cannot keep up.
    Dropping the oldest keeps the stream as live as possible, which is what a
    spectator view or a virtual camera wants; dropping the newest preserves a
    contiguous run, which is what a recording wants.
    """

    name = "async"

    def __init__(self, sink, max_queue=3, drop_oldest=True, name=None):
        super().__init__()
        self.sink = sink
        self.max_queue = max(int(max_queue), 1)
        self.drop_oldest = bool(drop_oldest)
        self.thread_name = name or f"xrmrc-{sink.name}"
        self._queue = None
        self._thread = None
        self._stop = threading.Event()
        self._busy = False

    @property
    def pending(self):
        return self._queue.qsize() if self._queue is not None else 0

    def open(self, spec):
        self.spec = spec
        if not self.sink.open(spec):
            self.stats.opened = False
            return False
        self._queue = queue.Queue(maxsize=self.max_queue)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=self.thread_name, daemon=True
        )
        self._thread.start()
        self.stats.opened = True
        return True

    def close(self):
        self.stats.opened = False
        self._stop.set()
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.sink.close()
        self._queue = None

    def flush(self, timeout=2.0):
        """Wait for the queue to drain.  Tests use it; the render loop must not."""
        deadline = time.monotonic() + timeout
        while True:
            work_queue = self._queue
            if work_queue is None:
                return True
            if work_queue.empty() and not self._busy:
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.001)

    def _write(self, frame):
        work_queue = self._queue
        if work_queue is None:
            return False
        try:
            work_queue.put_nowait(frame)
            return True
        except queue.Full:
            pass
        if not self.drop_oldest:
            return False
        try:
            work_queue.get_nowait()
            work_queue.task_done()
        except queue.Empty:
            return False
        try:
            work_queue.put_nowait(frame)
        except queue.Full:
            return False
        # The evicted frame never reaches the inner sink, so it counts as a
        # drop even though this submit succeeded.  ``dropped`` therefore means
        # "frames the inner sink never saw", and on a busy queue
        # ``written + dropped`` can exceed ``submitted`` by the eviction count.
        self.stats.dropped += 1
        return True

    def _run(self):
        work_queue = self._queue
        while not self._stop.is_set():
            try:
                frame = work_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._busy = True
            try:
                if frame is None:
                    break
                self.sink.submit(frame)
            finally:
                self._busy = False
                work_queue.task_done()

    def describe(self):
        data = super().describe()
        data["inner"] = self.sink.describe()
        data["pending"] = self.pending
        return data


class RateLimiter:
    """Emit at most ``fps`` frames per second, counting what it holds back.

    The XR loop runs at the headset's rate — 72, 90, 120 Hz — while a capture
    is usually wanted at 30 or 60.  Gating here rather than in a sink means the
    frames that are not wanted are never read back off the GPU at all.
    """

    def __init__(self, fps=30.0):
        self.fps = float(fps)
        self._next_time = None
        self.emitted = 0
        self.skipped = 0

    @property
    def interval(self):
        return 1.0 / self.fps if self.fps > 0.0 else 0.0

    def reset(self):
        self._next_time = None
        self.emitted = 0
        self.skipped = 0

    def should_emit(self, now=None):
        if self.fps <= 0.0:
            self.emitted += 1
            return True
        now = time.monotonic() if now is None else float(now)
        if self._next_time is None:
            self._next_time = now + self.interval
            self.emitted += 1
            return True
        if now + 1e-9 < self._next_time:
            self.skipped += 1
            return False
        # Advance in whole intervals so the cadence stays regular, but never
        # try to catch up more than one second of backlog.
        self._next_time += self.interval
        if self._next_time < now - 1.0:
            self._next_time = now + self.interval
        self.emitted += 1
        return True

    def as_dict(self):
        return {"fps": self.fps, "emitted": self.emitted, "skipped": self.skipped}


class OutputPipeline:
    """A rate limiter plus a set of sinks, fanned out non-blockingly."""

    def __init__(self, spec=None, fps=30.0):
        self.spec = spec
        self.limiter = RateLimiter(fps)
        self.sinks = []
        self.frame_index = 0
        self.frames_offered = 0
        self.frames_emitted = 0
        self.frames_rate_limited = 0

    # -- composition ----------------------------------------------------

    def add_sink(self, sink):
        self.sinks.append(sink)
        if self.spec is not None:
            sink.open(self.spec)
        return sink

    def remove_sink(self, sink):
        if sink in self.sinks:
            self.sinks.remove(sink)
            sink.close()
            return True
        return False

    # -- lifecycle ------------------------------------------------------

    def open(self, spec):
        self.spec = spec
        self.limiter.reset()
        ok = True
        for sink in self.sinks:
            ok = sink.open(spec) and ok
        return ok

    def close(self):
        for sink in self.sinks:
            sink.close()

    @property
    def is_open(self):
        return any(sink.is_open for sink in self.sinks)

    # -- frames ---------------------------------------------------------

    def wants_frame(self, now=None):
        """Ask before doing the expensive read-back.

        Only the rate limiter gets a say.  A pipeline with no sinks attached
        still says yes, because the frame is drawn for the spectator window as
        well as for the sinks, and a session that quietly stopped rendering the
        moment its last sink was removed would be baffling.
        """
        self.frames_offered += 1
        if not self.limiter.should_emit(now):
            self.frames_rate_limited += 1
            return False
        return True

    def submit(self, data, spec=None, timestamp=None):
        """Fan a frame out.  Returns the number of sinks that took it."""
        frame = Frame(data, spec or self.spec, self.frame_index, timestamp)
        self.frame_index += 1
        accepted = 0
        for sink in self.sinks:
            if sink.submit(frame):
                accepted += 1
        if accepted:
            self.frames_emitted += 1
        return accepted

    # -- reporting ------------------------------------------------------

    @property
    def dropped(self):
        return sum(sink.stats.dropped for sink in self.sinks)

    def status(self):
        return {
            "spec": self.spec.as_dict() if self.spec else None,
            "rate": self.limiter.as_dict(),
            "frames_offered": self.frames_offered,
            "frames_emitted": self.frames_emitted,
            "frames_rate_limited": self.frames_rate_limited,
            "dropped": self.dropped,
            "sinks": [sink.describe() for sink in self.sinks],
        }
