# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stylus state: buttons, pressure, gestures, and what they mean for tools.

:class:`StylusState` is updated from the raw action values each frame and
turns them into events (``tip_down``, ``tip_up``, ``front``, ``back``,
``double_tap``, ``middle``) with debouncing and a pressure curve. The
pressure goes to whichever tool is active: brush width for painting,
brush strength for sculpting, line weight for the vector editor; the
mapping is :class:`PressureMap`.

Default button roles, chosen so the stylus mirrors a controller: tip =
trigger (draw / select), middle cluster = grab, front = confirm/commit,
back = undo, back double-tap = toggle the wrist menu. All remappable.
"""

import math


class PressureMap(object):
    """Pressure (0..1 from the tip force) to a tool parameter."""

    def __init__(self, minimum=0.15, maximum=1.0, curve="linear", deadzone=0.02):
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.curve = curve
        self.deadzone = float(deadzone)

    def apply(self, pressure):
        p = max(0.0, min(1.0, (float(pressure) - self.deadzone) / max(1e-6, 1.0 - self.deadzone)))
        if self.curve == "soft":
            p = math.sqrt(p)
        elif self.curve == "hard":
            p = p * p
        elif self.curve == "smooth":
            p = p * p * (3.0 - 2.0 * p)
        return self.minimum + (self.maximum - self.minimum) * p

    def to_dict(self):
        return {"minimum": self.minimum, "maximum": self.maximum, "curve": self.curve, "deadzone": self.deadzone}


DEFAULT_ROLES = {"tip": "trigger", "middle": "grab", "front": "confirm", "back": "undo", "double_tap": "menu"}


class StylusEvent(object):
    __slots__ = ("kind", "value", "time", "hand")

    def __init__(self, kind, value=None, time=0.0, hand=1):
        self.kind = kind
        self.value = value
        self.time = time
        self.hand = hand

    def __repr__(self):
        return "StylusEvent(%s %r)" % (self.kind, self.value)


class StylusState(object):
    def __init__(self, hand=1, tip_threshold=0.05, tip_release=0.02, debounce=0.03, roles=None, pressure=None):
        self.hand = hand
        self.tip_threshold = float(tip_threshold)
        self.tip_release = float(tip_release)
        self.debounce = float(debounce)
        self.roles = dict(DEFAULT_ROLES)
        if roles:
            self.roles.update(roles)
        self.pressure_map = pressure or PressureMap()
        self.tip_force = 0.0
        self.tip_down = False
        self.middle_force = 0.0
        self.buttons = {"front": False, "middle": False, "back": False}
        self.docked = False
        self.present = False
        self.position = None
        self.rotation = None
        self._last_change = {}
        self._time = 0.0
        self.events = []

    @property
    def pressure(self):
        return self.pressure_map.apply(self.tip_force) if self.tip_down else 0.0

    def role(self, button):
        return self.roles.get(button)

    def update(self, raw, dt=0.0):
        """``raw`` is a dict of the action values (missing keys = unchanged/false):
        tip_force, front_click, middle_force, middle_click, back_click, double_tap, docked,
        position, rotation, present."""
        self._time += float(dt or 0.0)
        self.present = bool(raw.get("present", self.present))
        if "position" in raw:
            self.position = raw["position"]
        if "rotation" in raw:
            self.rotation = raw["rotation"]
        docked = bool(raw.get("docked", self.docked))
        if docked != self.docked:
            self.docked = docked
            self._emit("docked" if docked else "undocked")
        self.tip_force = float(raw.get("tip_force", 0.0))
        if not self.tip_down and self.tip_force >= self.tip_threshold:
            self.tip_down = True
            self._emit("tip_down", self.pressure)
        elif self.tip_down and self.tip_force <= self.tip_release:
            self.tip_down = False
            self._emit("tip_up")
        elif self.tip_down:
            self._emit("tip_pressure", self.pressure)
        self.middle_force = float(raw.get("middle_force", 0.0))
        for name, key in (("front", "front_click"), ("middle", "middle_click"), ("back", "back_click")):
            value = bool(raw.get(key, False))
            if value != self.buttons[name]:
                last = self._last_change.get(name, -1.0)
                if self._time - last < self.debounce:
                    continue
                self.buttons[name] = value
                self._last_change[name] = self._time
                self._emit(name + ("_down" if value else "_up"))
        if raw.get("double_tap"):
            self._emit("double_tap")
        return self.events

    def as_controller_buttons(self):
        """The trigger/grab/stick values the upstream tools read."""
        return {"trigger": self.tip_force if self.tip_down else 0.0,
                "grab": max(self.middle_force, 1.0 if self.buttons["middle"] else 0.0),
                "lever_x": 0.0, "lever_y": 0.0}

    def _emit(self, kind, value=None):
        self.events.append(StylusEvent(kind, value, self._time, self.hand))

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def to_dict(self):
        return {"hand": self.hand, "present": self.present, "docked": self.docked, "tip_down": self.tip_down,
                "tip_force": self.tip_force, "pressure": self.pressure, "buttons": dict(self.buttons), "roles": dict(self.roles)}


def route(event, state):
    """What a stylus event means for the active tool, by the button roles.

    Returns ``(action, value)``: ``("trigger", pressure)``, ``("grab", 1)``,
    ``("confirm", None)``, ``("undo", None)``, ``("menu", None)`` … or None.
    """
    kind = event.kind
    if kind == "tip_down":
        return (state.role("tip"), event.value)
    if kind == "tip_pressure":
        return ("pressure", event.value)
    if kind == "tip_up":
        return (state.role("tip") + "_release", None)
    if kind == "double_tap":
        return (state.role("double_tap"), None)
    for button in ("front", "middle", "back"):
        if kind == button + "_down":
            return (state.role(button), None)
        if kind == button + "_up":
            return (state.role(button) + "_release", None)
    return None
