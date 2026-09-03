# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Preference lookup for the paint subsystem.

Values come from the FreeCAD parameter group
``User parameter:BaseApp/Preferences/Mod/XR`` when FreeCAD is importable, and
from the hard-coded defaults below otherwise.  FreeCAD is imported lazily
*inside* the functions (ARCHITECTURE.md §6) so that every module in this
package stays unit-testable without FreeCAD.
"""

__all__ = [
    "DEFAULTS",
    "PARAM_PATH",
    "get",
    "get_bool",
    "get_float",
    "get_int",
    "get_string",
    "set_override",
    "clear_overrides",
]

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/XR"

#: Hard-coded fallbacks, used whenever FreeCAD is unavailable.
DEFAULTS = {
    "TextureSize": 2048,
    "BrushRadius": 4.0,          # millimetres
    "BlendMode": "normal",
    "PressureEnabled": True,
    "AutoUV": True,
    "PaintUndoSteps": 32,
}

_OVERRIDES = {}


def set_override(name, value):
    """Force a value regardless of FreeCAD (used by tests and by the UI)."""
    _OVERRIDES[name] = value


def clear_overrides():
    _OVERRIDES.clear()


def _group():
    """Return the FreeCAD parameter group, or ``None``."""
    try:
        import FreeCAD  # noqa: F401  (lazy on purpose)
    except Exception:
        return None
    try:
        return FreeCAD.ParamGet(PARAM_PATH)
    except Exception:
        return None


def _default(name, default):
    if default is not None:
        return default
    return DEFAULTS.get(name)


def get_int(name, default=None):
    if name in _OVERRIDES:
        return int(_OVERRIDES[name])
    d = int(_default(name, default))
    g = _group()
    if g is None:
        return d
    try:
        return int(g.GetInt(name, d))
    except Exception:
        return d


def get_float(name, default=None):
    if name in _OVERRIDES:
        return float(_OVERRIDES[name])
    d = float(_default(name, default))
    g = _group()
    if g is None:
        return d
    try:
        return float(g.GetFloat(name, d))
    except Exception:
        return d


def get_bool(name, default=None):
    if name in _OVERRIDES:
        return bool(_OVERRIDES[name])
    d = bool(_default(name, default))
    g = _group()
    if g is None:
        return d
    try:
        return bool(g.GetBool(name, d))
    except Exception:
        return d


def get_string(name, default=None):
    if name in _OVERRIDES:
        return str(_OVERRIDES[name])
    d = _default(name, default)
    d = "" if d is None else str(d)
    g = _group()
    if g is None:
        return d
    try:
        return str(g.GetString(name, d))
    except Exception:
        return d


def get(name, default=None):
    """Fetch a known preference, dispatching on the type of its default."""
    d = _default(name, default)
    if isinstance(d, bool):
        return get_bool(name, d)
    if isinstance(d, int):
        return get_int(name, d)
    if isinstance(d, float):
        return get_float(name, d)
    return get_string(name, d)
