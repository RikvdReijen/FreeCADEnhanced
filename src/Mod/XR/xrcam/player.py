# SPDX-License-Identifier: LGPL-2.1-or-later
"""Time-parametric playback of a toolpath.

The player keeps a clock over the toolpath's cumulative segment durations;
``advance(dt)`` moves it at ``speed`` × real time, ``seek`` jumps, and
``position`` is where the tool tip is now. ``completed()`` yields the
segments already run (what has been printed or cut so far) for rendering,
and ``goto_layer`` is the "show me layer 12" request.
"""

import bisect


class Player(object):
    def __init__(self, toolpath, speed=1.0):
        self.toolpath = toolpath
        self.speed = float(speed)
        self.playing = False
        self.time = 0.0
        self.loop = False
        self._starts = []
        total = 0.0
        for s in toolpath.segments:
            self._starts.append(total)
            total += s.duration
        self.duration = total
        self._layer_starts = {}
        for i, s in enumerate(toolpath.segments):
            self._layer_starts.setdefault(s.layer, self._starts[i])

    # -- transport -------------------------------------------------------

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def toggle(self):
        self.playing = not self.playing
        return self.playing

    def stop(self):
        self.playing = False
        self.time = 0.0

    def set_speed(self, factor):
        self.speed = max(0.01, min(1000.0, float(factor)))
        return self.speed

    def seek(self, t):
        self.time = max(0.0, min(self.duration, float(t)))
        return self.time

    def seek_fraction(self, f):
        return self.seek(self.duration * max(0.0, min(1.0, float(f))))

    def goto_layer(self, layer):
        if layer in self._layer_starts:
            return self.seek(self._layer_starts[layer])
        layers = sorted(self._layer_starts)
        if not layers:
            return self.time
        nearest = min(layers, key=lambda l: abs(l - layer))
        return self.seek(self._layer_starts[nearest])

    def advance(self, dt):
        """Advance the clock by real seconds ``dt``. Returns True while playing."""
        if not self.playing:
            return False
        self.time += float(dt) * self.speed
        if self.time >= self.duration:
            if self.loop and self.duration > 0:
                self.time = self.time % self.duration
            else:
                self.time = self.duration
                self.playing = False
        return self.playing

    # -- state -----------------------------------------------------------

    @property
    def index(self):
        """Index of the segment in progress (or the last one at the end)."""
        if not self._starts:
            return -1
        i = bisect.bisect_right(self._starts, self.time) - 1
        return max(0, min(i, len(self._starts) - 1))

    @property
    def segment(self):
        i = self.index
        return self.toolpath.segments[i] if i >= 0 else None

    @property
    def fraction_in_segment(self):
        s = self.segment
        if s is None or s.duration <= 0:
            return 1.0
        return max(0.0, min(1.0, (self.time - self._starts[self.index]) / s.duration))

    @property
    def position(self):
        s = self.segment
        if s is None:
            return (0.0, 0.0, 0.0)
        return s.point_at(self.fraction_in_segment)

    @property
    def progress(self):
        return self.time / self.duration if self.duration > 0 else 1.0

    @property
    def layer(self):
        s = self.segment
        return s.layer if s is not None else 0

    @property
    def cutting(self):
        s = self.segment
        return bool(s and s.cutting)

    def completed(self):
        """Segments fully run, plus the partial current one as ``(segment, fraction)``."""
        i = self.index
        if i < 0:
            return [], None
        done = self.toolpath.segments[:i]
        return done, (self.toolpath.segments[i], self.fraction_in_segment)

    def remaining_time(self):
        return max(0.0, self.duration - self.time) / self.speed

    def status(self):
        return {"playing": self.playing, "time": self.time, "duration": self.duration, "speed": self.speed,
                "progress": self.progress, "layer": self.layer, "segment": self.index, "position": list(self.position),
                "cutting": self.cutting}


def format_time(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
