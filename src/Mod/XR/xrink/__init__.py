# SPDX-License-Identifier: LGPL-2.1-or-later
"""Logitech MX Ink stylus support.

``profile`` is the OpenXR interaction profile (extension, paths, suggested
bindings); ``stylus`` turns the raw action values into debounced events,
pressure through a curve, and tool actions by button role. The desktop
viewer binds the profile in ``xrcore.ink_bridge``; the Quest app has the
same paths in ``input.cpp``.
"""

from .profile import ACTIONS, EXTENSION, PROFILE, UPSTREAM_ALIASES, is_supported, suggested_bindings
from .stylus import DEFAULT_ROLES, PressureMap, StylusEvent, StylusState, route

__all__ = ["ACTIONS", "EXTENSION", "PROFILE", "UPSTREAM_ALIASES", "is_supported", "suggested_bindings",
           "DEFAULT_ROLES", "PressureMap", "StylusEvent", "StylusState", "route"]
