# SPDX-License-Identifier: LGPL-2.1-or-later
"""Haptics on the desktop viewer: an OpenXR vibration action behind the engine.

``create_actions(widget)`` is called from ``XRwidget.prepare_xr_controls``
after the upstream actions exist and adds ``output/haptic`` for both hands
to every interaction profile the viewer suggests; ``pump()`` runs once per
frame and delivers the pulses the :class:`xrhaptics.HapticEngine` has
scheduled. Everything else — patterns, cooldowns, priorities — lives in
:mod:`xrhaptics`, so the bridge is only the last centimetre.
"""

import FreeCAD

from xrcore import service

__all__ = ["engine", "attach", "detach", "create_actions", "haptic_bindings", "pump", "feed", "set_intensity",
           "set_enabled", "test_pulse"]

_engine = None


class OpenXRBackend(object):
    """``xrApplyHapticFeedback`` through pyopenxr on the viewer's session."""

    def __init__(self, widget):
        self.widget = widget
        self.available = getattr(widget, "haptic_action", None) is not None

    def pulse(self, hand, amplitude, duration, frequency):
        widget = self.widget
        action = getattr(widget, "haptic_action", None)
        session = getattr(widget, "session", None)
        paths = getattr(widget, "hand_paths", None)
        if action is None or session is None or paths is None:
            return
        try:
            import xr

            vibration = xr.HapticVibration(
                amplitude=float(amplitude),
                duration=xr.Duration(int(max(duration, 0.001) * 1e9)),
                frequency=float(frequency) if frequency else xr.FREQUENCY_UNSPECIFIED,
            )
            info = xr.HapticActionInfo(action=action, subaction_path=paths[int(hand)])
            xr.apply_haptic_feedback(session, info, xr.HapticBaseHeader.from_address(vibration.address()) if hasattr(vibration, "address") else vibration)
        except Exception as exc:  # never let haptics take the frame down
            FreeCAD.Console.PrintLog(f"XR: haptic pulse failed: {exc}\n")


def engine():
    global _engine
    if _engine is None:
        from xrhaptics import HapticEngine

        prefs = service.preferences()
        _engine = HapticEngine(intensity=prefs.GetFloat("HapticsIntensity", 1.0), enabled=prefs.GetBool("HapticsEnabled", True))
        service.set_feature("haptics", _engine)
    return _engine


def create_actions(widget, xr_module):
    """Add the vibration output action to the widget's action set."""
    try:
        widget.haptic_action = xr_module.create_action(
            action_set=widget.action_set,
            create_info=xr_module.ActionCreateInfo(
                action_type=xr_module.ActionType.VIBRATION_OUTPUT,
                action_name="haptic",
                localized_action_name="Haptic feedback",
                count_subaction_paths=len(widget.hand_paths),
                subaction_paths=widget.hand_paths,
            ),
        )
    except Exception as exc:
        widget.haptic_action = None
        FreeCAD.Console.PrintWarning(f"XR: no haptic action ({exc}); haptics disabled\n")
    return widget.haptic_action


def haptic_bindings(widget, xr_module):
    """``ActionSuggestedBinding`` entries to append to every profile's list."""
    action = getattr(widget, "haptic_action", None)
    if action is None:
        return []
    out = []
    for side in ("left", "right"):
        path = xr_module.string_to_path(widget.instance, "/user/hand/%s/output/haptic" % side)
        out.append(xr_module.ActionSuggestedBinding(action, path))
    return out


def attach(widget):
    eng = engine()
    eng.backend = OpenXRBackend(widget)
    return eng


def detach():
    global _engine
    if _engine is not None:
        _engine.stop()
        from xrhaptics import NullBackend

        _engine.backend = NullBackend()


def pump():
    """Per frame: send due pulses."""
    if _engine is None:
        return 0
    return _engine.update()


def feed(events, mapping, hand=1):
    from xrhaptics import feed as _feed

    return _feed(engine(), events, mapping, hand)


def set_intensity(value):
    eng = engine()
    eng.intensity = max(0.0, min(1.0, float(value)))
    service.preferences().SetFloat("HapticsIntensity", eng.intensity)
    return eng.intensity


def set_enabled(enabled):
    eng = engine()
    eng.enabled = bool(enabled)
    service.preferences().SetBool("HapticsEnabled", eng.enabled)
    return eng.enabled


def test_pulse(hand=None):
    """A pattern on demand, for the preferences page and the menu."""
    eng = engine()
    eng.trigger("constraint", hand)
    eng.flush()
