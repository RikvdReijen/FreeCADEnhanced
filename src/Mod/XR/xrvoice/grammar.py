# SPDX-License-Identifier: LGPL-2.1-or-later
"""The command grammar: what can be said, and what it means.

A small, explicit vocabulary rather than a language model, because a
modelling command must either parse exactly or fail loudly — "fillet these
edges, 2 mm" applied with the wrong radius is worse than it being ignored.
Each :class:`Command` is a list of token patterns with slots; the first
command whose pattern matches the utterance wins and produces an
:class:`Intent` with typed parameters. ``parse()`` also returns the
alternatives it considered, so the HUD can show "did you mean".

Slots: ``{qty}`` a quantity (see :mod:`xrvoice.numbers`), ``{angle}`` an
angle, ``{axis}`` x/y/z, ``{dir}`` up/down/left/right/forward/back,
``{name}`` free words up to the end, ``{n}`` an integer. Optional words are
written ``[word]`` and alternatives ``(a|b)``.
"""

import re

from . import numbers as num

AXES = ("x", "y", "z")
DIRECTIONS = {"up": (0, 1, 0), "down": (0, -1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
              "forward": (0, 0, -1), "forwards": (0, 0, -1), "back": (0, 0, 1), "backward": (0, 0, 1),
              "backwards": (0, 0, 1), "in": (0, 0, -1), "out": (0, 0, 1), "closer": (0, 0, -1), "away": (0, 0, 1)}
FILLERS = {"please", "the", "this", "these", "those", "that", "selected", "selection", "um", "uh", "now", "to", "by", "of"}


class Intent(object):
    __slots__ = ("name", "params", "text", "confidence", "command")

    def __init__(self, name, params=None, text="", confidence=1.0, command=None):
        self.name = name
        self.params = dict(params or {})
        self.text = text
        self.confidence = float(confidence)
        self.command = command

    def to_dict(self):
        return {"name": self.name, "params": {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in self.params.items()},
                "text": self.text, "confidence": self.confidence}

    def __repr__(self):
        return "Intent(%s %r)" % (self.name, self.params)


class Command(object):
    __slots__ = ("name", "patterns", "help", "needs", "_compiled")

    def __init__(self, name, patterns, help="", needs=()):
        self.name = name
        self.patterns = list(patterns)
        self.help = help
        #: what must be true to run: "selection", "viewer", "document"
        self.needs = tuple(needs)
        self._compiled = [_compile(p) for p in self.patterns]

    def match(self, tokens):
        for pattern in self._compiled:
            params = _match(pattern, tokens, 0, {})
            if params is not None:
                return params
        return None


# pattern compilation ---------------------------------------------------

_SLOT = re.compile(r"^\{(\w+)\}$")


def _compile(pattern):
    """A pattern string into a list of elements: ("word", {alts}, optional) or ("slot", name, False)."""
    out = []
    for raw in pattern.split():
        optional = raw.startswith("[") and raw.endswith("]")
        if optional:
            raw = raw[1:-1]
        m = _SLOT.match(raw)
        if m:
            out.append(("slot", m.group(1), optional))
            continue
        if raw.startswith("(") and raw.endswith(")"):
            raw = raw[1:-1]
        out.append(("word", set(raw.split("|")), optional))
    return out


def _match(pattern, tokens, ti, params, pi=0):
    """Backtracking matcher. Returns params dict or None."""
    while pi < len(pattern):
        kind, spec, optional = pattern[pi]
        # skip fillers between elements
        while ti < len(tokens) and tokens[ti] in FILLERS and not (kind == "slot" and spec == "name"):
            if kind == "word" and tokens[ti] in spec:
                break
            ti += 1
        if kind == "word":
            if ti < len(tokens) and tokens[ti] in spec:
                pi += 1
                ti += 1
                continue
            if optional:
                pi += 1
                continue
            return None
        # slot
        if spec in ("qty", "angle", "n"):
            family = "angle" if spec == "angle" else "length"
            q = num.parse_quantity(tokens, ti, family)
            if q is None:
                if optional:
                    pi += 1
                    continue
                return None
            if spec == "n":
                if q.value != int(q.value):
                    return None
                params["n"] = int(q.value)
            else:
                params[spec] = q
            ti = q.end
            pi += 1
            continue
        if spec == "axis":
            if ti < len(tokens) and tokens[ti] in AXES:
                params["axis"] = tokens[ti]
                ti += 1
                pi += 1
                continue
            if optional:
                pi += 1
                continue
            return None
        if spec == "dir":
            if ti < len(tokens) and tokens[ti] in DIRECTIONS:
                params["dir"] = tokens[ti]
                ti += 1
                pi += 1
                continue
            if optional:
                pi += 1
                continue
            return None
        if spec == "name":
            # greedy: the rest, minus a trailing quantity if the pattern continues with one
            rest = tokens[ti:]
            if pi + 1 < len(pattern) and pattern[pi + 1][0] == "slot":
                # find the last position where the remaining pattern matches
                for cut in range(len(rest), 0, -1):
                    trial = dict(params)
                    trial["name"] = " ".join(rest[:cut])
                    done = _match(pattern, tokens, ti + cut, trial, pi + 1)
                    if done is not None:
                        return done
                return None
            if not rest and not optional:
                return None
            params["name"] = " ".join(rest)
            return params
        return None
    return params if ti >= len(tokens) or all(t in FILLERS for t in tokens[ti:]) else None


# the vocabulary ---------------------------------------------------------

COMMANDS = [
    Command("fillet", ["fillet [edges] {qty}", "round [off] [edges] {qty}", "fillet {qty} [radius]", "radius {qty} [fillet]", "{qty} fillet"],
            "Fillet the selected edges", needs=("selection", "document")),
    Command("chamfer", ["chamfer [edges] {qty}", "chamfer {qty}"], "Chamfer the selected edges", needs=("selection", "document")),
    Command("pocket", ["pocket {qty} [deep]", "cut [down] {qty}", "pocket [through] [all]"], "Pocket the selected sketch", needs=("document",)),
    Command("pad", ["pad {qty}", "extrude {qty}", "pad [up] {qty} [high]"], "Pad the selected sketch", needs=("document",)),
    Command("hole", ["hole {qty} [diameter]", "drill {qty}", "{qty} hole"], "Hole at the selection", needs=("selection", "document")),
    Command("shell", ["shell {qty}", "hollow [out] {qty} [wall]"], "Shell the body", needs=("selection", "document")),
    Command("set_param", ["set {name} {qty}", "make {name} {qty}", "change {name} {qty}"], "Set a named parameter", needs=("document",)),
    Command("move", ["move {dir} {qty}", "move {qty} {dir}", "nudge {dir} {qty}", "shift {dir} {qty}"], "Move the selection", needs=("selection",)),
    Command("rotate", ["rotate {angle} [degrees] (about|around|on) {axis}", "rotate (about|around|on) {axis} {angle}", "turn {angle} (about|around) {axis}", "rotate {angle}"],
            "Rotate the selection", needs=("selection",)),
    Command("scale_selection", ["scale [it] {qty}", "make [it] {qty} (bigger|larger|smaller)", "make [it] (bigger|larger|smaller)"],
            "Scale the selection", needs=("selection",)),
    Command("undo", ["undo [that]", "go back", "revert"], "Undo"),
    Command("redo", ["redo [that]"], "Redo"),
    Command("delete", ["delete [it]", "remove [it]", "get rid of it"], "Delete the selection", needs=("selection",)),
    Command("hide", ["hide [it]"], "Hide the selection", needs=("selection",)),
    Command("show", ["show [it]", "unhide [it]", "show (all|everything)"], "Show hidden objects"),
    Command("select_all", ["select (all|everything)"], "Select everything"),
    Command("deselect", ["deselect", "clear [selection]", "select nothing", "nothing"], "Clear the selection"),
    Command("measure", ["measure [it]", "how (long|big|far|wide|tall) [is] [it]", "distance"], "Measure"),
    Command("commit", ["commit", "apply", "done", "finish", "confirm", "okay", "ok", "yes"], "Confirm / commit the current tool"),
    Command("cancel", ["cancel", "never mind", "nevermind", "no", "abort", "stop that"], "Cancel the current tool"),
    Command("save", ["save [document]", "save it"], "Save the document", needs=("document",)),
    Command("recompute", ["recompute", "refresh", "rebuild"], "Recompute", needs=("document",)),
    Command("scale_user", ["(shrink|smaller) [me]", "(grow|bigger) [me]", "scale (up|down)", "zoom (in|out)"], "Shrink or grow the user"),
    Command("scale_reset", ["(life|real|normal|default|full) (size|scale)", "reset [the] scale", "one to one"], "Life size"),
    Command("environment", ["environment {name}", "(switch|go|change) [to] [the] {name} [environment]",
                            "take me [to] [the] {name}", "put me in [the] {name}"], "Switch environment"),
    Command("environment_next", ["next environment", "next [room|scene]"], "Next environment"),
    Command("environment_prev", ["previous environment", "last environment"], "Previous environment"),
    Command("snap", ["(snap|snapping) (on|off)", "(enable|disable) (snap|snapping)", "turn (on|off) (snap|snapping)"], "Toggle snapping"),
    Command("grid", ["grid {qty}", "grid (on|off)"], "Set the grid"),
    Command("mode", ["(paint|painting|texture) [mode]", "(sculpt|sculpting) [mode]", "(model|modelling|modeling) [mode]",
                     "(vector|vectors) [mode]", "(assemble|assembly) [mode]", "(fit|insert) [mode]", "fit check [mode]",
                     "(sketch|sketching|design) [mode]", "(scan|align|alignment) [mode]", "(drawing|draft) [mode]",
                     "technical drawing [mode]", "(cam|toolpath|gcode) [mode]", "g code [mode]"], "Switch tool mode"),
    Command("tool", ["(select|grab|curve|pen|primitive|subd|cage) [tool]", "use [the] (select|grab|curve|pen|primitive|subd|cage) [tool]"], "Sketch tool"),
    Command("capture", ["(start|begin) (capture|recording|capturing)", "(stop|end) (capture|recording|capturing)", "(capture|record) (on|off)"], "Mixed reality capture"),
    Command("mate", ["mate [it]", "(constrain|attach|lock) [it]", "snap [it] (in|on|together)"], "Confirm the previewed mate"),
    Command("release", ["release [it]", "let go", "drop [it]", "unconstrain", "free [it]"], "Release / unconstrain"),
    Command("voice_help", ["help", "what can i say", "commands", "list [the] commands"], "List the commands"),
    Command("voice_off", ["(stop|disable|mute) (listening|voice|microphone)", "voice off"], "Stop listening"),
    Command("play", ["(play|run|start) [the] (toolpath|gcode|print|job)", "(play|run|start) [the] g code", "play"], "Play the CAM preview"),
    Command("pause", ["pause", "hold [it]", "stop [the] (toolpath|print|job|playback)"], "Pause the CAM preview"),
    Command("playback_speed", ["(speed|playback) {qty} [times]", "{qty} times speed", "(faster|slower)"], "Playback speed"),
    Command("layer", ["[go to] layer {n}", "show layer {n}", "layer {n} [only]"], "Jump to a print layer"),
    Command("dimension", ["dimension [that]", "(add|place) [a] (dimension|measurement)"], "Place a dimension on the drawing"),
]

_BY_NAME = {c.name: c for c in COMMANDS}


def command(name):
    return _BY_NAME[name]


class Parse(object):
    __slots__ = ("intent", "tokens", "text", "alternatives")

    def __init__(self, intent, tokens, text, alternatives=()):
        self.intent = intent
        self.tokens = tokens
        self.text = text
        self.alternatives = list(alternatives)

    @property
    def ok(self):
        return self.intent is not None

    def __repr__(self):
        return "Parse(%r -> %r)" % (self.text, self.intent)


def parse(text, confidence=1.0, commands=None):
    """Parse an utterance. ``Parse.intent`` is ``None`` when nothing matched;
    ``alternatives`` lists commands sharing the first content word."""
    tokens = num.tokenize(text)
    if not tokens:
        return Parse(None, tokens, text)
    commands = commands or COMMANDS
    for cmd in commands:
        params = cmd.match(tokens)
        if params is not None:
            intent = Intent(cmd.name, _post(cmd.name, params, tokens), text, confidence, cmd)
            return Parse(intent, tokens, text)
    first = next((t for t in tokens if t not in FILLERS), None)
    alternatives = [c.name for c in commands if any(first in p.split() or ("(%s" % first) in p or ("|%s" % first) in p for p in c.patterns)] if first else []
    return Parse(None, tokens, text, alternatives)


def _post(name, params, tokens):
    """Fill in the parameters that come from the matched words rather than slots."""
    out = dict(params)
    joined = " ".join(tokens)
    if name == "scale_user":
        out["direction"] = "shrink" if any(w in tokens for w in ("shrink", "smaller", "down", "in")) else "grow"
    elif name == "snap":
        out["enabled"] = any(w in tokens for w in ("on", "enable"))
    elif name == "grid" and "qty" not in out:
        out["enabled"] = "on" in tokens
    elif name == "mode":
        for key, words in (("paint", ("paint", "painting", "texture")), ("sculpt", ("sculpt", "sculpting")),
                           ("model", ("model", "modelling", "modeling")), ("vector", ("vector", "vectors")),
                           ("assembly", ("assemble", "assembly")), ("fit", ("fit", "insert")),
                           ("sketch", ("sketch", "sketching", "design")), ("scan", ("scan", "align", "alignment")),
                           ("drawing", ("drawing", "draft", "technical")), ("cam", ("cam", "toolpath", "g", "gcode"))):
            if any(w in tokens for w in words):
                out["mode"] = key
                break
    elif name == "tool":
        out["tool"] = next(w for w in tokens if w in ("select", "grab", "curve", "pen", "primitive", "subd", "cage"))
        if out["tool"] == "grab":
            out["tool"] = "select"
        if out["tool"] == "cage":
            out["tool"] = "subd"
    elif name == "capture":
        out["enabled"] = any(w in tokens for w in ("start", "begin", "on"))
    elif name == "playback_speed":
        if "faster" in tokens:
            out["factor"] = 2.0
        elif "slower" in tokens:
            out["factor"] = 0.5
        elif "qty" in out:
            out["factor"] = out["qty"].value
    elif name == "scale_selection" and "qty" in out:
        q = out["qty"]
        factor = q.value if q.family == "ratio" else q.value
        if "smaller" in tokens and factor > 1:
            factor = 1.0 / factor
        out["factor"] = factor
    elif name == "pocket" and "through" in tokens:
        out["through_all"] = True
    elif name == "move":
        out["vector"] = DIRECTIONS[out["dir"]]
    if "name" in out:
        words = out["name"].split()
        while words and words[0] in FILLERS:
            words.pop(0)
        while words and words[-1] in FILLERS:
            words.pop()
        out["name"] = " ".join(words)
    if name == "scale_selection" and "factor" not in out:
        out["factor"] = 0.8 if "smaller" in tokens else 1.25
    return out


def help_text(commands=None):
    lines = []
    for cmd in commands or COMMANDS:
        lines.append("%-16s %s   e.g. \"%s\"" % (cmd.name, cmd.help, cmd.patterns[0].replace("[", "").replace("]", "")))
    return "\n".join(lines)
