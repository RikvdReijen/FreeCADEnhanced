# SPDX-License-Identifier: LGPL-2.1-or-later
"""CAM toolpath preview inside the machine environment.

You are already standing in the printer; watching the real G-code run at
scale is nearly free once the environment exists, and it catches the
collisions people currently discover on the machine.

::

    gcode.py    G-code (Marlin/Klipper/GRBL/LinuxCNC) and FreeCAD CAM Path
                commands -> Toolpath of Segments with feeds and timing
    player.py   time-parametric playback: play, pause, speed, seek, layer
    machine.py  MachineSpec (bed, travel, toolhead) placed on the
                environment's build-plate anchor; bounds and collision checks

:class:`CamSession` is the object the bridge and the voice commands drive.
"""

from .gcode import Segment, Toolpath, from_freecad_job, from_path_commands, parse_gcode
from .player import Player, format_time
from .machine import Issue, MachineSpec, check_bounds, check_collisions, head_mesh, world_polyline


class CamSession(object):
    """A toolpath in a machine, with playback and checks."""

    def __init__(self, toolpath, machine, obstacles=None):
        self.toolpath = toolpath
        self.machine = machine
        self.obstacles = dict(obstacles or {})
        self.player = Player(toolpath)
        self.issues = []
        self.events = []
        self._reported = set()

    @classmethod
    def from_gcode_text(cls, text, machine, name="gcode", **kw):
        return cls(parse_gcode(text, name=name, **kw), machine)

    def check(self, collisions=True, step=5.0):
        self.issues = check_bounds(self.toolpath, self.machine)
        if collisions and self.obstacles:
            self.issues.extend(check_collisions(self.toolpath, self.machine, self.obstacles, step=step))
        self.issues.sort(key=lambda i: i.time)
        return self.issues

    # transport, forwarded so voice and menus have one target
    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def toggle(self):
        return self.player.toggle()

    def set_speed(self, factor):
        return self.player.set_speed(factor)

    def goto_layer(self, layer):
        return self.player.goto_layer(int(layer))

    def seek_fraction(self, f):
        return self.player.seek_fraction(f)

    def update(self, dt):
        """Advance playback; emit an event when playback reaches an issue."""
        before = self.player.time
        playing = self.player.advance(dt)
        after = self.player.time
        for k, issue in enumerate(self.issues):
            if k in self._reported or issue.severity != "error":
                continue
            if before <= issue.time <= after:
                self._reported.add(k)
                self.events.append(_CamEvent(issue.kind, issue))
        return playing

    def tool_position_world(self):
        return self.machine.to_world(self.player.position)

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def status(self):
        s = self.player.status()
        s.update({"issues": len(self.issues), "machine": self.machine.name, "toolpath": self.toolpath.name,
                  "remaining": format_time(self.player.remaining_time())})
        return s


class _CamEvent(object):
    __slots__ = ("kind", "issue", "detail")

    def __init__(self, kind, issue):
        self.kind = kind
        self.issue = issue
        self.detail = {"magnitude": None}

    def __repr__(self):
        return "CamEvent(%s)" % self.kind


__all__ = ["Segment", "Toolpath", "from_freecad_job", "from_path_commands", "parse_gcode", "Player", "format_time",
           "Issue", "MachineSpec", "check_bounds", "check_collisions", "head_mesh", "world_polyline", "CamSession"]
