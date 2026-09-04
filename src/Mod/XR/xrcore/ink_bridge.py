# SPDX-License-Identifier: LGPL-2.1-or-later
"""Logitech MX Ink on the desktop viewer.

When the runtime exposes ``XR_LOGITECH_mx_ink_interaction``, the viewer
creates the stylus actions next to the upstream ones and suggests the
profile's bindings — including the aliases that make the tip act as the
trigger and the aim pose as the controller pose, so every existing tool
works with the pen unchanged. ``poll(widget, xr)`` reads the actions each
frame into a :class:`xrink.StylusState`; pressure is pushed to the paint
and sculpt brushes, and the button roles (front = confirm, back = undo,
double-tap = menu) are dispatched here.
"""

import FreeCAD

from xrcore import service

__all__ = ["extension_name", "wanted_extensions", "create_actions", "suggest_bindings", "poll", "stylus", "pressure",
           "available"]

_state = None
_available = False


def extension_name():
    from xrink import EXTENSION

    return EXTENSION


def wanted_extensions(supported):
    """The stylus extension, if the runtime has it (call while building the instance)."""
    from xrink import EXTENSION, is_supported

    return [EXTENSION] if is_supported(supported) else []


def available():
    return _available


def stylus():
    global _state
    if _state is None:
        from xrink import StylusState

        prefs = service.preferences()
        _state = StylusState(hand=prefs.GetInt("InkHand", 1))
        service.set_feature("stylus", _state)
    return _state


def create_actions(widget, xr):
    """Create the stylus actions on the widget's action set (no-op without the extension)."""
    global _available
    from xrink import ACTIONS

    widget.ink_actions = {}
    if not getattr(widget, "ink_extension_enabled", False):
        return widget.ink_actions
    for name, (kind, _) in ACTIONS.items():
        try:
            widget.ink_actions[name] = xr.create_action(
                action_set=widget.action_set,
                create_info=xr.ActionCreateInfo(
                    action_type=getattr(xr.ActionType, kind),
                    action_name=name,
                    localized_action_name=name.replace("_", " "),
                    count_subaction_paths=len(widget.hand_paths),
                    subaction_paths=widget.hand_paths,
                ),
            )
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"XR: MX Ink action {name} failed: {exc}\n")
    _available = bool(widget.ink_actions)
    return widget.ink_actions


def suggest_bindings(widget, xr):
    """Suggest the MX Ink profile bindings (stylus actions + upstream aliases)."""
    from xrink import PROFILE, suggested_bindings

    actions = getattr(widget, "ink_actions", None)
    if not actions:
        return False
    upstream = {"pose": getattr(widget, "pose_action", None), "grab": getattr(widget, "grab_action", None)}
    bindings = []
    for name, path in suggested_bindings():
        action = actions.get(name) or upstream.get(name)
        if action is None:
            continue
        bindings.append(xr.ActionSuggestedBinding(action, xr.string_to_path(widget.instance, path)))
    if not bindings:
        return False
    try:
        xr.suggest_interaction_profile_bindings(
            instance=widget.instance,
            suggested_bindings=xr.InteractionProfileSuggestedBinding(
                interaction_profile=xr.string_to_path(widget.instance, PROFILE),
                count_suggested_bindings=len(bindings),
                suggested_bindings=(xr.ActionSuggestedBinding * len(bindings))(*bindings),
            ),
        )
        FreeCAD.Console.PrintMessage("XR: Logitech MX Ink bindings suggested\n")
        return True
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"XR: MX Ink bindings rejected: {exc}\n")
        return False


def poll(widget, xr, dt=0.0):
    """Read the stylus actions after xrSyncActions; route the events."""
    actions = getattr(widget, "ink_actions", None)
    if not actions:
        return None
    state = stylus()
    hand = state.hand
    subaction = widget.hand_paths[hand]
    raw = {}

    def read(name, kind):
        action = actions.get(name)
        if action is None:
            return None
        info = xr.ActionStateGetInfo(action=action, subaction_path=subaction)
        try:
            if kind == "float":
                st = xr.get_action_state_float(widget.session, info)
                return float(st.current_state) if st.is_active else None
            st = xr.get_action_state_boolean(widget.session, info)
            return bool(st.current_state) if st.is_active else None
        except Exception:
            return None

    tip = read("ink_tip_force", "float")
    raw["present"] = tip is not None
    if tip is not None:
        raw["tip_force"] = tip
    for key, name, kind in (("front_click", "ink_front_click", "bool"), ("middle_force", "ink_middle_force", "float"),
                            ("middle_click", "ink_middle_click", "bool"), ("back_click", "ink_back_click", "bool"),
                            ("double_tap", "ink_back_double_tap", "bool"), ("docked", "ink_docked", "bool")):
        value = read(name, kind)
        if value is not None:
            raw[key] = value
    events = state.update(raw, dt)
    _route(events, state)
    return state


def _route(events, state):
    from xrink import route

    for event in state.drain_events():
        action = route(event, state)
        if action is None:
            continue
        name, value = action
        try:
            if name == "pressure":
                _apply_pressure(value)
            elif name == "trigger":
                _apply_pressure(value)
                try:
                    from xrcore import haptics_bridge

                    haptics_bridge.engine().trigger("tip", state.hand)
                except Exception:
                    pass
            elif name == "confirm":
                _confirm()
            elif name == "undo":
                _undo()
            elif name == "menu":
                widget = service.get_widget()
                toggle = getattr(widget, "toggle_wrist_menu", None)
                if toggle is not None:
                    toggle()
        except Exception as exc:
            FreeCAD.Console.PrintLog(f"XR: stylus action {name} failed: {exc}\n")


def _apply_pressure(value):
    """Pressure -> the active brush (paint radius, sculpt strength)."""
    paint = service.get_paint_session()
    if paint is not None and hasattr(paint, "set_radius"):
        try:
            paint.set_radius(value, normalised=True)
        except TypeError:
            paint.set_radius(value)
    sculpt = service.get_sculpt_session()
    if sculpt is not None and hasattr(sculpt, "set_strength"):
        sculpt.set_strength(value)


def _confirm():
    for name, fn in (("assembly", "confirm"),):
        session = service.get_feature(name)
        if session is not None and getattr(session, "preview", None) is not None:
            getattr(session, fn)()
            return
    draw = service.get_feature("draw")
    if draw is not None and draw.picks:
        from xrcore import draw_bridge

        draw_bridge.place_dimension()
        return
    from xrcore import sketch_bridge

    sketch_bridge.commit_sketch()


def _undo():
    for name in ("draw", "scan"):
        session = service.get_feature(name)
        if session is not None and hasattr(session, "undo") and session.undo():
            return
    sculpt = service.get_sculpt_session()
    if sculpt is not None:
        sculpt.undo()
        return
    from xrcore import sketch_bridge

    sketch_bridge.undo()


def pressure():
    return stylus().pressure if _state is not None else 0.0
