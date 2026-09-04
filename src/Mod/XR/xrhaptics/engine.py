# SPDX-License-Identifier: LGPL-2.1-or-later
"""The haptic engine: events in, vibrations out, without a buzz-storm.

``trigger(kind, hand, magnitude)`` looks the pattern up, applies the
per-hand cooldown and priority (a ``blocked`` buzz interrupts a ``contact``
bump, never the reverse), scales the amplitude by the event magnitude and
the user's intensity setting, and schedules the pulses. ``update(now)``
pumps scheduled pulses to the backend — the render loop calls it once per
frame, so a multi-pulse pattern plays out over frames without threads.

Backends implement one method, ``pulse(hand, amplitude, duration, frequency)``.
:class:`RecordingBackend` is for tests and the HUD; ``xrcore.haptics_bridge``
supplies the OpenXR one.
"""

import time as _time

from . import patterns as _patterns


class RecordingBackend(object):
    """Keeps every pulse it is asked for."""

    def __init__(self):
        self.pulses = []
        self.available = True

    def pulse(self, hand, amplitude, duration, frequency):
        self.pulses.append((hand, round(amplitude, 4), round(duration, 4), frequency))

    def clear(self):
        self.pulses = []


class NullBackend(object):
    available = False

    def pulse(self, hand, amplitude, duration, frequency):
        pass


class _Scheduled(object):
    __slots__ = ("at", "hand", "pulse", "kind")

    def __init__(self, at, hand, pulse, kind):
        self.at = at
        self.hand = hand
        self.pulse = pulse
        self.kind = kind


class HapticEngine(object):
    HANDS = (0, 1)

    def __init__(self, backend=None, intensity=1.0, enabled=True, clock=None):
        self.backend = backend or NullBackend()
        self.intensity = float(intensity)
        self.enabled = bool(enabled)
        self._clock = clock or _time.monotonic
        self._queue = []
        self._last = {}       # (hand, kind) -> time last fired
        self._busy_until = {}  # hand -> (until, priority)
        self.fired = []       # (time, hand, kind) history, bounded
        self.dropped = 0

    # -- input -----------------------------------------------------------

    def trigger(self, kind, hand=1, magnitude=None, now=None):
        """Fire the pattern for ``kind`` on ``hand`` (0 left, 1 right, None both).

        Returns True when something was scheduled."""
        if not self.enabled:
            return False
        if hand is None:
            return any([self.trigger(kind, h, magnitude, now) for h in self.HANDS])
        pattern = _patterns.pattern_for(kind)
        if pattern is None:
            return False
        now = self._clock() if now is None else float(now)
        last = self._last.get((hand, kind))
        if last is not None and now - last < pattern.cooldown:
            self.dropped += 1
            return False
        busy = self._busy_until.get(hand)
        if busy is not None and now < busy[0] and busy[1] > pattern.priority:
            self.dropped += 1
            return False
        if busy is not None and now < busy[0] and busy[1] <= pattern.priority:
            # interrupt what is playing
            self._queue = [s for s in self._queue if s.hand != hand]
        factor = pattern.amplitude_factor(magnitude) * self.intensity
        at = now
        for pulse in pattern.pulses:
            at += pulse.delay
            self._queue.append(_Scheduled(at, hand, pulse.scaled(factor), kind))
            at += pulse.duration
        self._busy_until[hand] = (at, pattern.priority)
        self._last[(hand, kind)] = now
        self.fired.append((now, hand, kind))
        if len(self.fired) > 256:
            del self.fired[:-256]
        return True

    def pulse(self, hand, amplitude, duration, frequency=0.0):
        """A raw pulse, bypassing patterns (still scaled by intensity)."""
        if not self.enabled:
            return
        self.backend.pulse(hand, max(0.0, min(1.0, amplitude * self.intensity)), duration, frequency)

    # -- output ----------------------------------------------------------

    def update(self, now=None):
        """Send every pulse whose time has come. Returns how many were sent."""
        now = self._clock() if now is None else float(now)
        due = [s for s in self._queue if s.at <= now]
        if not due:
            return 0
        self._queue = [s for s in self._queue if s.at > now]
        for s in sorted(due, key=lambda s: s.at):
            if s.pulse.amplitude > 0.0 and s.pulse.duration > 0.0:
                self.backend.pulse(s.hand, s.pulse.amplitude, s.pulse.duration, s.pulse.frequency)
        return len(due)

    def flush(self):
        """Send everything queued regardless of time (e.g. on shutdown)."""
        return self.update(float("inf"))

    @property
    def pending(self):
        return len(self._queue)

    def stop(self):
        self._queue = []
        self._busy_until = {}

    def describe(self):
        return "haptics %s, intensity %.0f%%, backend %s" % (
            "on" if self.enabled else "off", self.intensity * 100.0, type(self.backend).__name__)
