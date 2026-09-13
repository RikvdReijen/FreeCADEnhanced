# SPDX-License-Identifier: LGPL-2.1-or-later
"""Turning the other subsystems' events into haptic triggers.

Each subsystem emits plain event objects with a ``kind``; these adapters map
them onto pattern kinds and hands, so the subsystems stay ignorant of
haptics and the engine stays ignorant of geometry.
"""

#: xrfit.FitEvent.kind -> pattern kind
FIT_EVENTS = {"contact": "contact", "blocked": "blocked", "seated": "seated", "clear": "clear",
              "grab": "grab", "release": "release"}
#: xrassembly.AssemblyEvent.kind -> pattern kind
ASSEMBLY_EVENTS = {"snap": "snap", "unsnap": "unsnap", "constraint": "constraint", "unconstrain": "unconstrain",
                   "grab": "grab", "release": "release"}
#: xrscan.ScanEvent.kind -> pattern kind
SCAN_EVENTS = {"pick_scan": "pick", "pick_model": "pick", "pick_length": "pick", "aligned": "aligned",
               "refined": "aligned", "seated": "seated", "scaled": "aligned"}
#: xrcam warnings and xrvoice outcomes
CAM_EVENTS = {"collision": "warning", "out_of_bounds": "warning"}
VOICE_EVENTS = {"heard": "heard", "misheard": "misheard", "done": "ui_click"}


def feed(engine, events, mapping, hand=1, now=None):
    """Trigger the engine for every event whose kind is in ``mapping``.

    ``events`` may carry ``depth`` (fit) which becomes the magnitude.
    Returns the number of triggers that fired."""
    fired = 0
    for event in events:
        kind = mapping.get(getattr(event, "kind", None))
        if kind is None:
            continue
        magnitude = getattr(event, "depth", None)
        if magnitude is None:
            detail = getattr(event, "detail", None)
            if isinstance(detail, dict):
                magnitude = detail.get("magnitude")
        if engine.trigger(kind, hand, magnitude, now):
            fired += 1
    return fired


def snap_feedback(engine, previous, result, hand=1, now=None):
    """For :class:`xrsketch.snapping.SnapResult`: tick when a snap begins,
    a lighter tick when the target changes, nothing while it holds."""
    was = getattr(previous, "kind", None)
    now_kind = getattr(result, "kind", None)
    if now_kind and not was:
        return engine.trigger("snap", hand, None, now)
    if now_kind and was and (getattr(previous, "target", None), getattr(previous, "index", None)) != (
            getattr(result, "target", None), getattr(result, "index", None)):
        return engine.trigger("ui_click", hand, None, now)
    if was and not now_kind:
        return engine.trigger("unsnap", hand, None, now)
    return False
