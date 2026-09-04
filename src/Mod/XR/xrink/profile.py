# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Logitech MX Ink interaction profile.

The stylus is exposed through the OpenXR extension
``XR_LOGITECH_mx_ink_interaction`` as the interaction profile
``/interaction_profiles/logitech/mx_ink_stylus_logitech``. Its input
paths, from the extension specification:

==============================================  ==========  =============
path (under /user/hand/<side>/)                 type        what it is
==============================================  ==========  =============
input/tip_logitech/force                        float       tip pressure
input/cluster_front_logitech/click              boolean     front button
input/cluster_front_logitech/value              float       (analog)
input/cluster_middle_logitech/force             float       middle button pressure
input/cluster_middle_logitech/click             boolean
input/cluster_back_logitech/click               boolean     back button
input/cluster_back_logitech/double_tap_logitech boolean     double tap on the back
input/dock_logitech/docked_logitech             boolean     in the charging dock
input/aim/pose, input/grip/pose                 pose
output/haptic                                   vibration
==============================================  ==========  =============

:func:`suggested_bindings` produces the ``(action_name, path)`` list the
viewer feeds to ``xrSuggestInteractionProfileBindings``; the actions it
names are the extra ones :mod:`xrcore.ink_bridge` creates alongside the
upstream pose/trigger/stick actions.
"""

EXTENSION = "XR_LOGITECH_mx_ink_interaction"
PROFILE = "/interaction_profiles/logitech/mx_ink_stylus_logitech"

#: action name -> (OpenXR action type, subpath)
ACTIONS = {
    "ink_tip_force": ("FLOAT_INPUT", "input/tip_logitech/force"),
    "ink_front_click": ("BOOLEAN_INPUT", "input/cluster_front_logitech/click"),
    "ink_front_value": ("FLOAT_INPUT", "input/cluster_front_logitech/value"),
    "ink_middle_force": ("FLOAT_INPUT", "input/cluster_middle_logitech/force"),
    "ink_middle_click": ("BOOLEAN_INPUT", "input/cluster_middle_logitech/click"),
    "ink_back_click": ("BOOLEAN_INPUT", "input/cluster_back_logitech/click"),
    "ink_back_double_tap": ("BOOLEAN_INPUT", "input/cluster_back_logitech/double_tap_logitech"),
    "ink_docked": ("BOOLEAN_INPUT", "input/dock_logitech/docked_logitech"),
    "ink_aim_pose": ("POSE_INPUT", "input/aim/pose"),
    "ink_grip_pose": ("POSE_INPUT", "input/grip/pose"),
    "ink_haptic": ("VIBRATION_OUTPUT", "output/haptic"),
}

#: The upstream viewer's actions the stylus should also drive, so the
#: existing tools work with it unchanged: tip force is the trigger, the
#: middle cluster the grab, the aim pose the controller pose.
UPSTREAM_ALIASES = {
    "pose": "input/aim/pose",
    "grab": "input/tip_logitech/force",
}

HANDS = ("left", "right")


def suggested_bindings(hands=HANDS, include_upstream=True):
    """``[(action_name, "/user/hand/<side>/<subpath>")]`` for the profile."""
    out = []
    for hand in hands:
        for name, (_, subpath) in ACTIONS.items():
            out.append((name, "/user/hand/%s/%s" % (hand, subpath)))
        if include_upstream:
            for name, subpath in UPSTREAM_ALIASES.items():
                out.append((name, "/user/hand/%s/%s" % (hand, subpath)))
    return out


def is_supported(extensions):
    """Given the runtime's extension names, is the stylus profile available?"""
    return EXTENSION in set(extensions)
