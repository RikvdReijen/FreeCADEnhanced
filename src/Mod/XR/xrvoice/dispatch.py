# SPDX-License-Identifier: LGPL-2.1-or-later
"""From an intent to an action.

The :class:`Dispatcher` holds a registry ``{intent_name: handler}``; a
handler takes the :class:`~xrvoice.grammar.Intent` and a :class:`Context`
and returns a message. :func:`default_handlers` wires the vocabulary to the
XR bridges and to FreeCAD (PartDesign fillets and pockets on the current
selection, document parameters, undo). Every FreeCAD call is inside the
handler and guarded, so the dispatcher itself, the argument checking and
the "needs a selection" refusals run without FreeCAD.
"""


class ActionResult(object):
    __slots__ = ("ok", "message", "intent", "detail")

    def __init__(self, ok, message, intent=None, detail=None):
        self.ok = bool(ok)
        self.message = message
        self.intent = intent
        self.detail = detail or {}

    def to_dict(self):
        return {"ok": self.ok, "message": self.message, "intent": self.intent.to_dict() if self.intent else None}

    def __repr__(self):
        return "ActionResult(%s, %r)" % ("ok" if self.ok else "failed", self.message)


class Context(object):
    """What the handlers may look at: selection, document, live sessions.

    Filled by the bridge on the desktop; tests pass a plain instance."""

    def __init__(self, selection=(), document=None, viewer=None, sessions=None, environment=None):
        self.selection = list(selection)
        self.document = document
        self.viewer = viewer
        self.sessions = dict(sessions or {})
        self.environment = environment

    def has(self, need):
        if need == "selection":
            return bool(self.selection)
        if need == "document":
            return self.document is not None
        if need == "viewer":
            return self.viewer is not None
        return True


class Dispatcher(object):
    def __init__(self, handlers=None, confidence_threshold=0.5):
        self.handlers = dict(handlers or {})
        self.confidence_threshold = float(confidence_threshold)
        self.log = []

    def register(self, name, handler):
        self.handlers[name] = handler
        return handler

    def handle(self, intent, context=None):
        context = context or Context()
        if intent is None:
            return self._done(ActionResult(False, "not understood"))
        if intent.confidence < self.confidence_threshold:
            return self._done(ActionResult(False, "not sure I heard that (%.0f%%): %s" % (intent.confidence * 100, intent.text), intent))
        needs = intent.command.needs if intent.command is not None else ()
        for need in needs:
            if not context.has(need):
                return self._done(ActionResult(False, "'%s' needs a %s" % (intent.name, need), intent))
        handler = self.handlers.get(intent.name)
        if handler is None:
            return self._done(ActionResult(False, "no handler for '%s'" % intent.name, intent))
        try:
            outcome = handler(intent, context)
        except Exception as exc:
            return self._done(ActionResult(False, "%s failed: %s" % (intent.name, exc), intent))
        if isinstance(outcome, ActionResult):
            outcome.intent = outcome.intent or intent
            return self._done(outcome)
        return self._done(ActionResult(True, str(outcome) if outcome is not None else intent.name, intent))

    def _done(self, result):
        self.log.append(result)
        if len(self.log) > 100:
            del self.log[:-100]
        return result


# ----------------------------------------------------------------------
# default handlers
# ----------------------------------------------------------------------


def _qty(intent, key="qty"):
    q = intent.params.get(key)
    return None if q is None else q.value


def _mm(value):
    return "%g mm" % value


def default_handlers():
    """Handlers for the built-in vocabulary. FreeCAD-dependent ones import lazily."""
    h = {}

    def fillet(intent, ctx):
        return _partdesign_dress("Fillet", "Radius", _qty(intent), ctx, "fillet")

    def chamfer(intent, ctx):
        return _partdesign_dress("Chamfer", "Size", _qty(intent), ctx, "chamfer")

    def pocket(intent, ctx):
        depth = _qty(intent)
        return _partdesign_sketch_feature("Pocket", depth, ctx, through=intent.params.get("through_all", False))

    def pad(intent, ctx):
        return _partdesign_sketch_feature("Pad", _qty(intent), ctx)

    def hole(intent, ctx):
        return _partdesign_hole(_qty(intent), ctx)

    def shell(intent, ctx):
        return _partdesign_thickness(_qty(intent), ctx)

    def set_param(intent, ctx):
        return _set_named_parameter(intent.params.get("name", ""), _qty(intent), ctx)

    def move(intent, ctx):
        return _move_selection(intent.params["vector"], _qty(intent), ctx)

    def rotate(intent, ctx):
        return _rotate_selection(intent.params.get("axis", "z"), _qty(intent, "angle"), ctx)

    def scale_selection(intent, ctx):
        return ActionResult(False, "scaling document objects by voice is not supported; use the two-handed grab", intent)

    def undo(intent, ctx):
        return _document_call(ctx, "undo", "undone")

    def redo(intent, ctx):
        return _document_call(ctx, "redo", "redone")

    def recompute(intent, ctx):
        return _document_call(ctx, "recompute", "recomputed")

    def save(intent, ctx):
        return _document_call(ctx, "save", "saved")

    def delete(intent, ctx):
        return _delete_selection(ctx)

    def hide(intent, ctx):
        return _set_visibility(ctx, False)

    def show(intent, ctx):
        return _set_visibility(ctx, True)

    def select_all(intent, ctx):
        return _select_all(ctx)

    def deselect(intent, ctx):
        return _clear_selection(ctx)

    def scale_user(intent, ctx):
        return _bridge_call("environment_bridge", "nudge_scale", 1.25 if intent.params.get("direction") == "shrink" else 0.8,
                            message="shrunk" if intent.params.get("direction") == "shrink" else "grown")

    def scale_reset(intent, ctx):
        return _bridge_call("environment_bridge", "reset_scale", message="life size")

    def environment(intent, ctx):
        return _switch_environment(intent.params.get("name", ""))

    def environment_next(intent, ctx):
        return _bridge_call("environment_bridge", "cycle_environment", 1, message="next environment")

    def environment_prev(intent, ctx):
        return _bridge_call("environment_bridge", "cycle_environment", -1, message="previous environment")

    def mode(intent, ctx):
        return _switch_mode(intent.params.get("mode"))

    def tool(intent, ctx):
        return _bridge_call("sketch_bridge", "activate_tool", intent.params["tool"].upper(), message="tool %s" % intent.params["tool"])

    def snap(intent, ctx):
        return _set_snapping(intent.params.get("enabled", True), ctx)

    def grid(intent, ctx):
        return _set_grid(intent, ctx)

    def capture(intent, ctx):
        return _capture(intent.params.get("enabled", True))

    def mate(intent, ctx):
        session = ctx.sessions.get("assembly")
        if session is None:
            return ActionResult(False, "no assembly session", intent)
        m = session.confirm()
        return ActionResult(m is not None, "mated %s" % m.kind if m else "nothing to mate", intent)

    def release(intent, ctx):
        for key in ("assembly", "fit"):
            session = ctx.sessions.get(key)
            if session is not None and getattr(session, "grabbed", None):
                session.release()
                return ActionResult(True, "released", intent)
        session = ctx.sessions.get("assembly")
        if session is not None and session.unconstrain():
            return ActionResult(True, "unconstrained", intent)
        return ActionResult(False, "nothing held", intent)

    def commit(intent, ctx):
        return _commit_current(ctx)

    def cancel(intent, ctx):
        return _cancel_current(ctx)

    def measure(intent, ctx):
        return _bridge_call("sketch_bridge", "activate_tool", "MEASURE", message="measure")

    def voice_help(intent, ctx):
        from .grammar import help_text

        return ActionResult(True, help_text(), intent)

    def voice_off(intent, ctx):
        session = ctx.sessions.get("voice")
        if session is not None:
            session.stop()
        return ActionResult(True, "voice off", intent)

    def play(intent, ctx):
        return _cam_call(ctx, "play", "playing")

    def pause(intent, ctx):
        return _cam_call(ctx, "pause", "paused")

    def playback_speed(intent, ctx):
        return _cam_call(ctx, "set_speed", "speed set", intent.params.get("factor", 1.0))

    def layer(intent, ctx):
        return _cam_call(ctx, "goto_layer", "layer", intent.params.get("n", 0))

    def dimension(intent, ctx):
        session = ctx.sessions.get("draw")
        if session is None:
            return ActionResult(False, "no drawing session", intent)
        made = session.place_dimension()
        return ActionResult(made is not None, "dimension placed" if made else "pick two points on the sheet first", intent)

    for name, fn in list(locals().items()):
        if callable(fn) and not name.startswith("_") and name not in ("h",):
            h[name] = fn
    return h


# -- FreeCAD-side helpers (each guarded) --------------------------------


def _freecad():
    try:
        import FreeCAD
        return FreeCAD
    except ImportError:
        return None


def _gui():
    try:
        import FreeCADGui
        return FreeCADGui
    except ImportError:
        return None


def _selection_objects(ctx):
    """``[(object, [subnames])]`` from the context selection."""
    out = []
    for item in ctx.selection:
        if isinstance(item, tuple):
            out.append((item[0], list(item[1]) if len(item) > 1 else []))
        else:
            out.append((item, []))
    return out


def _body_of(obj):
    for parent in getattr(obj, "InList", []) or []:
        if getattr(parent, "TypeId", "") == "PartDesign::Body":
            return parent
    return None


def _partdesign_dress(kind, prop, value, ctx, verb):
    if value is None:
        return ActionResult(False, "how big? say a size, e.g. 'fillet two millimetres'")
    message = "%s %s" % (verb, _mm(value))
    App = _freecad()
    if App is None:
        return ActionResult(True, message + " (dry run)", detail={kind: value})
    doc = ctx.document
    objs = _selection_objects(ctx)
    if not objs:
        return ActionResult(False, "select the edges first")
    obj, subs = objs[0]
    edges = [s for s in subs if s.startswith("Edge")] or subs
    if not edges:
        return ActionResult(False, "select edges, not the whole body")
    body = _body_of(obj)
    feature = (body.newObject if body is not None else doc.addObject)("PartDesign::" + kind, kind)
    feature.Base = (obj, edges)
    setattr(feature, prop, value)
    if hasattr(obj, "Visibility"):
        obj.Visibility = False
    doc.recompute()
    return ActionResult(True, message, detail={"object": feature.Name})


def _partdesign_sketch_feature(kind, depth, ctx, through=False):
    if depth is None and not through:
        return ActionResult(False, "how deep? say a length")
    App = _freecad()
    if App is None:
        return ActionResult(True, "%s %s (dry run)" % (kind.lower(), "through all" if through else _mm(depth)))
    doc = ctx.document
    objs = _selection_objects(ctx)
    sketch = next((o for o, _ in objs if "Sketch" in getattr(o, "TypeId", "")), None)
    if sketch is None:
        return ActionResult(False, "select a sketch first")
    body = _body_of(sketch)
    feature = (body.newObject if body is not None else doc.addObject)("PartDesign::" + kind, kind)
    feature.Profile = sketch
    if through:
        feature.Type = "ThroughAll"
    else:
        feature.Length = depth
    sketch.Visibility = False
    doc.recompute()
    return ActionResult(True, "%s %s" % (kind.lower(), "through all" if through else _mm(depth)), detail={"object": feature.Name})


def _partdesign_hole(diameter, ctx):
    if diameter is None:
        return ActionResult(False, "what diameter?")
    App = _freecad()
    if App is None:
        return ActionResult(True, "hole %s (dry run)" % _mm(diameter))
    objs = _selection_objects(ctx)
    sketch = next((o for o, _ in objs if "Sketch" in getattr(o, "TypeId", "")), None)
    if sketch is None:
        return ActionResult(False, "select the sketch with the hole centres first")
    body = _body_of(sketch)
    feature = (body.newObject if body is not None else ctx.document.addObject)("PartDesign::Hole", "Hole")
    feature.Profile = sketch
    feature.Diameter = diameter
    ctx.document.recompute()
    return ActionResult(True, "hole %s" % _mm(diameter), detail={"object": feature.Name})


def _partdesign_thickness(thickness, ctx):
    if thickness is None:
        return ActionResult(False, "what wall thickness?")
    App = _freecad()
    if App is None:
        return ActionResult(True, "shell %s (dry run)" % _mm(thickness))
    objs = _selection_objects(ctx)
    if not objs:
        return ActionResult(False, "select the faces to remove first")
    obj, subs = objs[0]
    body = _body_of(obj)
    feature = (body.newObject if body is not None else ctx.document.addObject)("PartDesign::Thickness", "Thickness")
    feature.Base = (obj, subs)
    feature.Value = thickness
    ctx.document.recompute()
    return ActionResult(True, "shell %s" % _mm(thickness), detail={"object": feature.Name})


def _set_named_parameter(name, value, ctx):
    if not name or value is None:
        return ActionResult(False, "say which parameter and the value, e.g. 'set wall to three millimetres'")
    key = name.strip().replace(" ", "_")
    App = _freecad()
    if App is None:
        return ActionResult(True, "%s = %g (dry run)" % (key, value))
    doc = ctx.document
    for obj in getattr(doc, "Objects", []):
        tid = getattr(obj, "TypeId", "")
        if tid == "Spreadsheet::Sheet":
            cell = getattr(obj, "getCellFromAlias", lambda a: None)(key)
            if cell:
                obj.set(cell, str(value))
                doc.recompute()
                return ActionResult(True, "%s = %g" % (key, value))
        elif tid == "App::VarSet" and hasattr(obj, key):
            setattr(obj, key, value)
            doc.recompute()
            return ActionResult(True, "%s = %g" % (key, value))
    # a property on the selected object?
    for obj, _ in _selection_objects(ctx):
        prop = next((p for p in getattr(obj, "PropertiesList", []) if p.lower() == key.lower()), None)
        if prop:
            current = getattr(obj, prop)
            setattr(obj, prop, type(current)(value) if not hasattr(current, "Value") else value)
            doc.recompute()
            return ActionResult(True, "%s.%s = %g" % (obj.Name, prop, value))
    return ActionResult(False, "no parameter called '%s'" % key)


def _move_selection(vector, distance, ctx):
    if distance is None:
        return ActionResult(False, "how far?")
    App = _freecad()
    if App is None:
        return ActionResult(True, "move %s by %s (dry run)" % (vector, _mm(distance)))
    moved = 0
    for obj, _ in _selection_objects(ctx):
        if hasattr(obj, "Placement"):
            pl = obj.Placement
            pl.Base = pl.Base + App.Vector(vector[0] * distance, vector[1] * distance, vector[2] * distance)
            obj.Placement = pl
            moved += 1
    ctx.document.recompute()
    return ActionResult(moved > 0, "moved %d object(s) %s" % (moved, _mm(distance)))


def _rotate_selection(axis, angle, ctx):
    if angle is None:
        return ActionResult(False, "how many degrees?")
    App = _freecad()
    if App is None:
        return ActionResult(True, "rotate %g° about %s (dry run)" % (angle, axis))
    vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    rotated = 0
    for obj, _ in _selection_objects(ctx):
        if hasattr(obj, "Placement"):
            pl = obj.Placement
            pl.Rotation = App.Rotation(App.Vector(*vec), angle).multiply(pl.Rotation)
            obj.Placement = pl
            rotated += 1
    ctx.document.recompute()
    return ActionResult(rotated > 0, "rotated %d object(s) %g°" % (rotated, angle))


def _document_call(ctx, method, message):
    doc = ctx.document
    if doc is None:
        return ActionResult(False, "no document")
    fn = getattr(doc, method, None)
    if fn is None:
        return ActionResult(False, "document has no %s" % method)
    fn()
    return ActionResult(True, message)


def _delete_selection(ctx):
    objs = _selection_objects(ctx)
    if ctx.document is None or not objs:
        return ActionResult(False, "nothing selected")
    for obj, _ in objs:
        ctx.document.removeObject(obj.Name)
    return ActionResult(True, "deleted %d object(s)" % len(objs))


def _set_visibility(ctx, visible):
    objs = _selection_objects(ctx)
    if not visible and not objs:
        return ActionResult(False, "nothing selected")
    targets = [o for o, _ in objs] if objs else list(getattr(ctx.document, "Objects", []) or [])
    for obj in targets:
        if hasattr(obj, "Visibility"):
            obj.Visibility = visible
    return ActionResult(True, ("shown" if visible else "hidden") + " %d object(s)" % len(targets))


def _select_all(ctx):
    Gui = _gui()
    if Gui is None or ctx.document is None:
        return ActionResult(False, "no GUI")
    for obj in ctx.document.Objects:
        Gui.Selection.addSelection(obj)
    return ActionResult(True, "selected everything")


def _clear_selection(ctx):
    Gui = _gui()
    if Gui is None:
        return ActionResult(True, "selection cleared (dry run)")
    Gui.Selection.clearSelection()
    return ActionResult(True, "selection cleared")


def _bridge_call(module_name, function, *args, message=""):
    try:
        module = __import__("xrcore." + module_name, fromlist=[function])
    except ImportError as exc:
        return ActionResult(False, "%s unavailable: %s" % (module_name, exc))
    getattr(module, function)(*args)
    return ActionResult(True, message or function)


def _switch_environment(name):
    name = name.strip().lower()
    aliases = {"printer": "bambu_x1c", "3d printer": "bambu_x1c", "bambu": "bambu_x1c", "laser": "laser_cutter",
               "laser cutter": "laser_cutter", "cutter": "laser_cutter", "workshop": "workshop", "shop": "workshop",
               "studio": "studio", "void": "void", "dark": "void", "black": "void"}
    env_id = aliases.get(name, name.replace(" ", "_"))
    return _bridge_call("environment_bridge", "set_environment", env_id, message="environment %s" % env_id)


def _switch_mode(mode):
    if mode == "model":
        for bridge, fn in (("paint_bridge", "deactivate"), ("sculpt_bridge", "deactivate"), ("sketch_bridge", "deactivate")):
            _bridge_call(bridge, fn)
        return ActionResult(True, "modelling")
    table = {"paint": ("paint_bridge", "activate_mode", "TEXTURE"), "vector": ("paint_bridge", "activate_mode", "VECTOR"),
             "sculpt": ("sculpt_bridge", "activate_mode", "SCULPT"), "sketch": ("sketch_bridge", "activate_tool", "SELECT"),
             "assembly": ("assembly_bridge", "activate"), "fit": ("fit_bridge", "activate"),
             "scan": ("scan_bridge", "activate"), "drawing": ("draw_bridge", "activate"), "cam": ("cam_bridge", "activate")}
    if mode not in table:
        return ActionResult(False, "unknown mode %r" % mode)
    entry = table[mode]
    return _bridge_call(entry[0], entry[1], *entry[2:], message="%s mode" % mode)


def _set_snapping(enabled, ctx):
    session = ctx.sessions.get("sketch")
    if session is None:
        return _bridge_call("sketch_bridge", "set_snapping", enabled, message="snap %s" % ("on" if enabled else "off"))
    session.snap.settings.enabled = enabled
    return ActionResult(True, "snap %s" % ("on" if enabled else "off"))


def _set_grid(intent, ctx):
    session = ctx.sessions.get("sketch")
    size = _qty(intent)
    if session is None:
        return ActionResult(False, "no sketch session")
    if size is not None:
        session.snap.settings.grid = size / 1000.0
        return ActionResult(True, "grid %s" % _mm(size))
    session.snap.settings.grid_enabled = intent.params.get("enabled", True)
    return ActionResult(True, "grid %s" % ("on" if session.snap.settings.grid_enabled else "off"))


def _capture(enabled):
    try:
        from xrcore import service

        session = service.require_mrc_session()
    except Exception as exc:
        return ActionResult(False, "capture unavailable: %s" % exc)
    if enabled and not session.active:
        session.toggle()
    elif not enabled and session.active:
        session.toggle()
    return ActionResult(True, "capture %s" % ("on" if enabled else "off"))


def _commit_current(ctx):
    for key, method in (("assembly", "confirm"), ("draw", "place_dimension"), ("scan", "align_from_pairs")):
        session = ctx.sessions.get(key)
        if session is not None and hasattr(session, method):
            try:
                getattr(session, method)()
                return ActionResult(True, "%s: committed" % key)
            except Exception as exc:
                return ActionResult(False, "%s: %s" % (key, exc))
    result = _bridge_call("sketch_bridge", "commit_sketch", message="sketch committed")
    return result


def _cancel_current(ctx):
    for key in ("assembly", "fit"):
        session = ctx.sessions.get(key)
        if session is not None and getattr(session, "grabbed", None):
            session.release()
            return ActionResult(True, "released")
    session = ctx.sessions.get("sketch")
    if session is not None and hasattr(session, "cancel_all"):
        session.cancel_all()
        return ActionResult(True, "cancelled")
    return ActionResult(True, "nothing to cancel")


def _cam_call(ctx, method, message, *args):
    session = ctx.sessions.get("cam")
    if session is None:
        return ActionResult(False, "no toolpath loaded")
    getattr(session, method)(*args)
    return ActionResult(True, message)
