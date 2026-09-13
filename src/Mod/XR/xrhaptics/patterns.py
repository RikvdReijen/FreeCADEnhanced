# SPDX-License-Identifier: LGPL-2.1-or-later
"""What each event feels like.

A :class:`Pulse` is one vibration (amplitude 0..1, seconds, Hz — the three
numbers OpenXR's ``XrHapticVibration`` takes). A :class:`Pattern` is a
sequence of pulses with gaps, plus a priority and a scaling rule. The
defaults below are tuned so that a snap is a crisp tick, a contact is a
soft bump that grows with penetration, a confirmed constraint is a double
tap, and "blocked" is an unmistakable buzz. Everything is overridable per
event kind through :func:`set_pattern`.
"""


class Pulse(object):
    __slots__ = ("amplitude", "duration", "frequency", "delay")

    def __init__(self, amplitude, duration, frequency=0.0, delay=0.0):
        self.amplitude = max(0.0, min(1.0, float(amplitude)))
        self.duration = max(0.0, float(duration))
        #: 0 means "the runtime's default"
        self.frequency = float(frequency)
        #: seconds after the previous pulse ends
        self.delay = max(0.0, float(delay))

    def scaled(self, factor):
        return Pulse(self.amplitude * factor, self.duration, self.frequency, self.delay)

    def to_dict(self):
        return {"amplitude": self.amplitude, "duration": self.duration, "frequency": self.frequency, "delay": self.delay}

    def __repr__(self):
        return "Pulse(%.2f, %.3fs, %gHz)" % (self.amplitude, self.duration, self.frequency)


class Pattern(object):
    __slots__ = ("kind", "pulses", "priority", "cooldown", "scale_by")

    def __init__(self, kind, pulses, priority=0, cooldown=None, scale_by=None):
        self.kind = kind
        self.pulses = list(pulses)
        #: higher wins when two events land on one hand in the same frame
        self.priority = int(priority)
        #: seconds before the same kind may fire again on the same hand
        self.cooldown = float(cooldown) if cooldown is not None else self.length
        #: ("magnitude", lo, hi): amplitude scales linearly from lo..hi of the event magnitude
        self.scale_by = scale_by

    @property
    def length(self):
        return sum(p.delay + p.duration for p in self.pulses)

    def amplitude_factor(self, magnitude):
        if self.scale_by is None or magnitude is None:
            return 1.0
        lo, hi = self.scale_by
        if hi <= lo:
            return 1.0
        t = (float(magnitude) - lo) / (hi - lo)
        return 0.35 + 0.65 * max(0.0, min(1.0, t))

    def to_dict(self):
        return {"kind": self.kind, "pulses": [p.to_dict() for p in self.pulses], "priority": self.priority,
                "cooldown": self.cooldown, "scale_by": list(self.scale_by) if self.scale_by else None}

    def __repr__(self):
        return "Pattern(%s, %d pulses, p%d)" % (self.kind, len(self.pulses), self.priority)


PATTERNS = {}


def set_pattern(pattern):
    PATTERNS[pattern.kind] = pattern
    return pattern


def pattern_for(kind):
    return PATTERNS.get(kind)


def _defaults():
    set_pattern(Pattern("ui_click", [Pulse(0.3, 0.012, 250)], priority=1))
    set_pattern(Pattern("ui_hover", [Pulse(0.12, 0.008, 300)], priority=0, cooldown=0.08))
    # snapping: a crisp tick
    set_pattern(Pattern("snap", [Pulse(0.6, 0.025, 180)], priority=3, cooldown=0.06))
    set_pattern(Pattern("unsnap", [Pulse(0.25, 0.015, 140)], priority=2))
    # contact: a soft bump scaled by penetration depth (metres)
    set_pattern(Pattern("contact", [Pulse(0.45, 0.02, 120)], priority=4, cooldown=0.05, scale_by=(0.0, 0.005)))
    set_pattern(Pattern("clear", [Pulse(0.15, 0.01, 200)], priority=1))
    set_pattern(Pattern("seated", [Pulse(0.5, 0.04, 150)], priority=4))
    # blocked: an unmistakable buzz while the hand keeps pushing
    set_pattern(Pattern("blocked", [Pulse(0.85, 0.06, 90)], priority=6, cooldown=0.12, scale_by=(0.0, 0.01)))
    # constraint confirmed: a double tap
    set_pattern(Pattern("constraint", [Pulse(0.8, 0.03, 200), Pulse(0.8, 0.03, 200, delay=0.05)], priority=5))
    set_pattern(Pattern("unconstrain", [Pulse(0.4, 0.03, 120)], priority=3))
    set_pattern(Pattern("grab", [Pulse(0.35, 0.015, 160)], priority=2))
    set_pattern(Pattern("release", [Pulse(0.2, 0.015, 200)], priority=2))
    # alignment picks and results
    set_pattern(Pattern("pick", [Pulse(0.4, 0.015, 220)], priority=2))
    set_pattern(Pattern("aligned", [Pulse(0.6, 0.03, 180), Pulse(0.6, 0.03, 180, delay=0.04), Pulse(0.6, 0.03, 180, delay=0.04)], priority=5))
    # voice: heard / not understood
    set_pattern(Pattern("heard", [Pulse(0.25, 0.02, 240)], priority=1))
    set_pattern(Pattern("misheard", [Pulse(0.5, 0.05, 70), Pulse(0.5, 0.05, 70, delay=0.03)], priority=3))
    # toolpath collision warning
    set_pattern(Pattern("warning", [Pulse(0.7, 0.08, 80)], priority=6, cooldown=0.5))
    set_pattern(Pattern("error", [Pulse(0.9, 0.15, 60)], priority=7, cooldown=0.5))
    # a peer joined / left (multi-user)
    set_pattern(Pattern("peer", [Pulse(0.3, 0.02, 200), Pulse(0.3, 0.02, 260, delay=0.06)], priority=1, cooldown=1.0))
    # the stylus tip touching a surface
    set_pattern(Pattern("tip", [Pulse(0.2, 0.01, 280)], priority=2, cooldown=0.04))


_defaults()
