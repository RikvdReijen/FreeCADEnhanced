# SPDX-License-Identifier: LGPL-2.1-or-later
"""G-code and CAM ``Path`` commands into one toolpath model.

A :class:`Toolpath` is a list of :class:`Segment` — straight moves and
arcs with start, end, feed rate, whether the tool is cutting (extruding,
laser on, spindle on and below the clearance plane), the layer/operation
they belong to, and the time they take. That is what the player animates,
what the machine envelope is checked against, and what is drawn.

Dialects handled by :func:`parse_gcode`: Marlin/Klipper/RepRap (printers:
``G0/G1 E`` extrusion, ``M104/M109`` temperatures, ``;LAYER:`` and
``;Z:`` comments from PrusaSlicer/Cura/Bambu), GRBL/LinuxCNC (mills and
lasers: ``M3/M4/M5`` spindle or laser, ``S`` power, ``G2/G3`` arcs with
``I J K`` or ``R``, ``G20/G21`` units, ``G90/G91``). FreeCAD CAM ``Path``
objects come in through :func:`from_path_commands`.
"""

import math
import re

_WORD = re.compile(r"([A-Za-z])\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")


class Segment(object):
    __slots__ = ("start", "end", "feed", "cutting", "rapid", "layer", "line", "length", "duration", "arc", "extrusion",
                 "power", "z_layer", "operation")

    def __init__(self, start, end, feed, cutting, rapid=False, layer=0, line=0, arc=None, extrusion=0.0, power=None,
                 z_layer=None, operation=""):
        self.start = start
        self.end = end
        #: mm/s
        self.feed = float(feed)
        self.cutting = bool(cutting)
        self.rapid = bool(rapid)
        self.layer = int(layer)
        self.line = int(line)
        #: ``(centre, clockwise)`` for arcs, else None
        self.arc = arc
        self.extrusion = float(extrusion)
        self.power = power
        self.z_layer = z_layer
        self.operation = operation
        self.length = self._length()
        self.duration = self.length / self.feed if self.feed > 0 else 0.0

    def _length(self):
        if self.arc is None:
            return math.dist(self.start, self.end)
        centre, cw = self.arc
        r = math.dist((self.start[0], self.start[1]), (centre[0], centre[1]))
        a0 = math.atan2(self.start[1] - centre[1], self.start[0] - centre[0])
        a1 = math.atan2(self.end[1] - centre[1], self.end[0] - centre[0])
        sweep = (a0 - a1) if cw else (a1 - a0)
        while sweep <= 1e-12:
            sweep += 2.0 * math.pi
        while sweep > 2.0 * math.pi + 1e-12:
            sweep -= 2.0 * math.pi
        dz = self.end[2] - self.start[2]
        return math.sqrt((r * sweep) ** 2 + dz * dz)

    def point_at(self, t):
        """Position at fraction ``t`` (0..1) along the segment."""
        t = max(0.0, min(1.0, t))
        if self.arc is None:
            return tuple(self.start[i] + (self.end[i] - self.start[i]) * t for i in range(3))
        centre, cw = self.arc
        r = math.dist((self.start[0], self.start[1]), (centre[0], centre[1]))
        a0 = math.atan2(self.start[1] - centre[1], self.start[0] - centre[0])
        a1 = math.atan2(self.end[1] - centre[1], self.end[0] - centre[0])
        sweep = (a0 - a1) if cw else (a1 - a0)
        while sweep <= 1e-12:
            sweep += 2.0 * math.pi
        a = a0 - sweep * t if cw else a0 + sweep * t
        return (centre[0] + r * math.cos(a), centre[1] + r * math.sin(a), self.start[2] + (self.end[2] - self.start[2]) * t)

    def polyline(self, max_error=0.05):
        """Points approximating the segment (arcs subdivided to ``max_error`` mm)."""
        if self.arc is None:
            return [self.start, self.end]
        centre, _ = self.arc
        r = math.dist((self.start[0], self.start[1]), (centre[0], centre[1]))
        n = 2
        if r > max_error:
            step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - max_error / r)))
            total = self.length / max(r, 1e-9)
            n = max(2, int(math.ceil(total / max(step, 1e-6))) + 1)
        return [self.point_at(i / float(n - 1)) for i in range(n)]

    def to_dict(self):
        return {"start": list(self.start), "end": list(self.end), "feed": self.feed, "cutting": self.cutting,
                "rapid": self.rapid, "layer": self.layer, "line": self.line, "length": self.length,
                "duration": self.duration, "arc": None if self.arc is None else [list(self.arc[0]), self.arc[1]],
                "extrusion": self.extrusion, "power": self.power, "operation": self.operation}

    def __repr__(self):
        return "Segment(%s -> %s, %s)" % (tuple(round(c, 2) for c in self.start), tuple(round(c, 2) for c in self.end),
                                          "cut" if self.cutting else ("rapid" if self.rapid else "move"))


class Toolpath(object):
    def __init__(self, segments=(), name="", machine="", units="mm", notes=()):
        self.segments = list(segments)
        self.name = name
        #: "printer", "laser", "mill" or ""
        self.machine = machine
        self.units = units
        self.notes = list(notes)
        self.tool_diameter = None
        self.layer_heights = {}

    def __len__(self):
        return len(self.segments)

    @property
    def duration(self):
        return sum(s.duration for s in self.segments)

    @property
    def cutting_length(self):
        return sum(s.length for s in self.segments if s.cutting)

    @property
    def layers(self):
        return sorted({s.layer for s in self.segments})

    def bounds(self, cutting_only=False):
        pts = []
        for s in self.segments:
            if cutting_only and not s.cutting:
                continue
            pts.extend(s.polyline(0.5) if s.arc else (s.start, s.end))
        if not pts:
            return None
        return (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))

    def segments_of_layer(self, layer):
        return [s for s in self.segments if s.layer == layer]

    def to_dict(self):
        return {"name": self.name, "machine": self.machine, "units": self.units, "duration": self.duration,
                "segments": len(self.segments), "layers": len(self.layers), "notes": list(self.notes)}

    def __repr__(self):
        return "Toolpath(%r, %d segments, %.0fs, %s)" % (self.name, len(self.segments), self.duration, self.machine or "?")


# ----------------------------------------------------------------------
# G-code
# ----------------------------------------------------------------------

_LAYER_COMMENT = re.compile(r";\s*(?:LAYER|layer)[:\s]+(-?\d+)")
_LAYER_CHANGE = re.compile(r";\s*(?:LAYER_CHANGE|CHANGE_LAYER|AFTER_LAYER_CHANGE)")
_Z_COMMENT = re.compile(r";\s*Z\s*:\s*([-+]?\d*\.?\d+)")
_TYPE_COMMENT = re.compile(r";\s*TYPE\s*:\s*(.+)")
_OP_COMMENT = re.compile(r"\((?:Operation|Op|begin operation)[:\s]+([^)]+)\)", re.I)


def parse_gcode(text, default_feed=1500.0, rapid_feed=None, name="gcode"):
    """Parse G-code text into a :class:`Toolpath`.

    ``default_feed`` and ``rapid_feed`` are mm/min, used until the file sets
    ``F``; rapids default to 2× the default feed.
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    segments = []
    notes = []
    pos = [0.0, 0.0, 0.0]
    absolute = True
    e_absolute = True
    e_pos = 0.0
    unit = 1.0
    feed = float(default_feed)
    rapid = float(rapid_feed) if rapid_feed else feed * 2.0
    layer = 0
    z_layer = None
    spindle_on = False
    power = None
    extruding_seen = False
    spindle_seen = False
    operation = ""
    line_no = 0
    lines = text.splitlines()
    for raw in lines:
        line_no += 1
        line = raw.strip()
        if not line:
            continue
        m = _LAYER_COMMENT.search(line)
        if m:
            layer = int(m.group(1))
        elif _LAYER_CHANGE.search(line):
            layer += 1
        m = _Z_COMMENT.search(line)
        if m:
            z_layer = float(m.group(1))
        m = _OP_COMMENT.search(line)
        if m:
            operation = m.group(1).strip()
        # strip comments
        code = line.split(";", 1)[0]
        code = re.sub(r"\([^)]*\)", " ", code).strip()
        if not code:
            continue
        words = _WORD.findall(code)
        if not words:
            continue
        params = {}
        commands = []
        for letter, value in words:
            letter = letter.upper()
            if letter in ("G", "M", "T"):
                commands.append((letter, float(value)))
            else:
                params[letter] = float(value)
        if "F" in params:
            # Cura/PrusaSlicer put the travel rate on G0 and the print rate on G1;
            # keep the two apart so rapids are drawn and timed as rapids.
            if any(letter == "G" and int(number) == 0 for letter, number in commands) and not any(
                    letter == "G" and int(number) in (1, 2, 3) for letter, number in commands):
                rapid = params["F"] * unit
            else:
                feed = params["F"] * unit
        if "S" in params:
            power = params["S"]
        for letter, number in commands:
            if letter == "G":
                g = int(number)
                if g in (20,):
                    unit = 25.4
                elif g == 21:
                    unit = 1.0
                elif g == 90:
                    absolute = True
                elif g == 91:
                    absolute = False
                elif g == 92:
                    if "E" in params:
                        e_pos = params["E"]
                    for k, i in (("X", 0), ("Y", 1), ("Z", 2)):
                        if k in params:
                            pos[i] = params[k] * unit
                elif g in (0, 1, 2, 3):
                    target = list(pos)
                    for k, i in (("X", 0), ("Y", 1), ("Z", 2)):
                        if k in params:
                            target[i] = params[k] * unit if absolute else pos[i] + params[k] * unit
                    de = 0.0
                    if "E" in params:
                        e = params["E"]
                        de = (e - e_pos) if e_absolute else e
                        e_pos = e if e_absolute else e_pos + e
                        if de > 0:
                            extruding_seen = True
                    arc = None
                    if g in (2, 3):
                        cw = g == 2
                        if "R" in params:
                            centre = _arc_centre_from_radius(pos, target, params["R"] * unit, cw)
                        else:
                            centre = (pos[0] + params.get("I", 0.0) * unit, pos[1] + params.get("J", 0.0) * unit)
                        arc = (centre, cw)
                    is_rapid = g == 0
                    cutting = (de > 0) or (spindle_on and not is_rapid)
                    if target != pos or de > 0:
                        segments.append(Segment(tuple(pos), tuple(target), (rapid if is_rapid else feed) / 60.0, cutting,
                                                is_rapid, layer, line_no, arc, de, power if spindle_on else None,
                                                z_layer, operation))
                    pos = target
            elif letter == "M":
                mcode = int(number)
                if mcode in (3, 4):
                    spindle_on = True
                    spindle_seen = True
                elif mcode == 5:
                    spindle_on = False
                elif mcode == 82:
                    e_absolute = True
                elif mcode == 83:
                    e_absolute = False
    machine = "printer" if extruding_seen else ("laser" if spindle_seen and _looks_flat(segments) else ("mill" if spindle_seen else ""))
    if not segments:
        notes.append("no motion commands found")
    path = Toolpath(segments, name, machine, "mm", notes)
    return path


def _looks_flat(segments):
    """A laser never cuts while moving in Z; a mill plunges."""
    return not any(s.cutting and abs(s.end[2] - s.start[2]) > 1e-6 for s in segments)


def _arc_centre_from_radius(start, end, r, cw):
    dx, dy = end[0] - start[0], end[1] - start[1]
    d = math.hypot(dx, dy)
    if d == 0 or abs(r) < d / 2.0:
        return (start[0] + dx / 2.0, start[1] + dy / 2.0)
    h = math.sqrt(max(r * r - (d / 2.0) ** 2, 0.0))
    mx, my = start[0] + dx / 2.0, start[1] + dy / 2.0
    # perpendicular; sign by direction and radius sign (negative R = the long way round)
    sign = -1.0 if (cw and r > 0) or (not cw and r < 0) else 1.0
    return (mx - sign * h * dy / d, my + sign * h * dx / d)


# ----------------------------------------------------------------------
# FreeCAD CAM Path
# ----------------------------------------------------------------------


def from_path_commands(commands, name="Path", clearance_z=None, default_feed=1000.0):
    """A :class:`Toolpath` from FreeCAD ``Path.Command`` objects (or dicts
    ``{"Name": "G1", "Parameters": {"X": .., "F": ..}}``).

    Cutting is inferred from the move type: G1/G2/G3 cut, G0 rapids; when
    ``clearance_z`` is given, feed moves above it count as positioning.
    """
    segments = []
    pos = [0.0, 0.0, 0.0]
    feed = float(default_feed)
    rapid = feed * 3.0
    for idx, cmd in enumerate(commands):
        cname = cmd.get("Name") if isinstance(cmd, dict) else getattr(cmd, "Name", "")
        params = cmd.get("Parameters", {}) if isinstance(cmd, dict) else dict(getattr(cmd, "Parameters", {}) or {})
        cname = (cname or "").upper()
        if cname in ("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03"):
            g = int(cname[1:])
            if "F" in params:
                feed = float(params["F"])
            target = list(pos)
            for k, i in (("X", 0), ("Y", 1), ("Z", 2)):
                if k in params:
                    target[i] = float(params[k])
            arc = None
            if g in (2, 3):
                arc = ((pos[0] + float(params.get("I", 0.0)), pos[1] + float(params.get("J", 0.0))), g == 2)
            is_rapid = g == 0
            cutting = not is_rapid and (clearance_z is None or target[2] < clearance_z)
            if target != pos:
                segments.append(Segment(tuple(pos), tuple(target), (rapid if is_rapid else feed) / 60.0, cutting,
                                        is_rapid, 0, idx, arc, operation=name))
            pos = target
    return Toolpath(segments, name, "mill")


def from_freecad_job(job):
    """Every operation of a CAM ``Job`` object, concatenated in order."""
    segments = []
    notes = []
    clearance = None
    for op in getattr(job, "Operations", None).Group if hasattr(getattr(job, "Operations", None), "Group") else []:
        path = getattr(op, "Path", None)
        if path is None:
            continue
        try:
            clearance = float(op.ClearanceHeight.Value) if hasattr(op, "ClearanceHeight") else clearance
        except Exception:
            pass
        part = from_path_commands(path.Commands, getattr(op, "Label", op.Name), clearance)
        segments.extend(part.segments)
    if not segments:
        notes.append("the job has no operations with paths")
    tp = Toolpath(segments, getattr(job, "Label", "Job"), "mill", notes=notes)
    try:
        tc = job.Tools.Group[0].Tool
        tp.tool_diameter = float(tc.Diameter.Value if hasattr(tc.Diameter, "Value") else tc.Diameter)
    except Exception:
        pass
    return tp
