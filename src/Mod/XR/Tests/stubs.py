# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stand-in modules for FreeCAD, Qt and Coin3D.

The GUI glue of the workbench (``xrcore.commands``, ``xrcore.ui_dialogs``,
``xrcore.environment_bridge`` …) imports ``FreeCAD``, ``PySide`` and
``pivy.coin`` at module scope, which is right for a workbench but means a plain
``python3 -m unittest`` run cannot see those modules at all.  Installing these
stubs lets the tests import that code and exercise its pure logic, which is
enough to catch the mistakes that otherwise only surface with a headset
attached: typos, wrong attribute names, and drift between a bridge and the
subsystem it wraps.

They deliberately do *not* try to emulate FreeCAD's behaviour.  Anything that
needs real geometry belongs in FreeCAD's own test suite.
"""

import sys
import types

__all__ = ["install", "uninstall", "StubParameterGroup", "recorded_commands"]

recorded_commands = {}


class _AutoClass:
    """A permissive stand-in that can be instantiated, called and subclassed."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs

    def __call__(self, *args, **kwargs):
        return _AutoClass()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _AutoClass()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def __bool__(self):
        return True


def _auto_module(name, extra=None):
    """A module whose every attribute is a fresh subclassable stub class."""
    module = types.ModuleType(name)
    cache = {}

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr not in cache:
            cache[attr] = type(attr, (_AutoClass,), {})
        return cache[attr]

    module.__getattr__ = __getattr__
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    return module


class StubParameterGroup:
    """Enough of FreeCAD's ``ParameterGrp`` for the preference lookups."""

    def __init__(self):
        self.values = {}

    def _get(self, key, default):
        return self.values.get(key, default)

    def GetInt(self, key, default=0):
        return int(self._get(key, default))

    def GetFloat(self, key, default=0.0):
        return float(self._get(key, default))

    def GetBool(self, key, default=False):
        return bool(self._get(key, default))

    def GetString(self, key, default=""):
        return str(self._get(key, default))

    def SetInt(self, key, value):
        self.values[key] = int(value)

    def SetFloat(self, key, value):
        self.values[key] = float(value)

    def SetBool(self, key, value):
        self.values[key] = bool(value)

    def SetString(self, key, value):
        self.values[key] = str(value)


_INSTALLED = []
_PARAMETERS = {}


def _make_freecad():
    module = types.ModuleType("FreeCAD")

    console = types.SimpleNamespace(
        PrintMessage=lambda *a: None,
        PrintWarning=lambda *a: None,
        PrintError=lambda *a: None,
        PrintLog=lambda *a: None,
    )

    def param_get(path):
        return _PARAMETERS.setdefault(path, StubParameterGroup())

    module.Console = console
    module.ParamGet = param_get
    module.ActiveDocument = None
    module.Qt = types.SimpleNamespace(translate=lambda ctx, text, *a: text)
    module.getUserAppDataDir = lambda: "/tmp/freecad-xr-tests"
    module.addImportType = lambda *a: None
    module.addExportType = lambda *a: None
    module.newDocument = lambda *a, **k: None
    module.getDocument = lambda *a, **k: None
    module.openDocument = lambda *a, **k: None
    module.BoundBox = type("BoundBox", (_AutoClass,), {})
    module.Vector = type("Vector", (_AutoClass,), {})
    return module


def _make_freecadgui():
    module = types.ModuleType("FreeCADGui")

    def add_command(name, instance):
        recorded_commands[name] = instance

    module.addCommand = add_command
    module.addWorkbench = lambda wb: None
    module.addIconPath = lambda path: None
    module.addLanguagePath = lambda path: None
    module.addPreferencePage = lambda page, group: None
    module.getMainWindow = lambda: None
    module.insert = lambda *a, **k: None
    module.PySideUic = types.SimpleNamespace(loadUi=lambda path: _AutoClass())
    module.Workbench = type("Workbench", (_AutoClass,), {})
    return module


def install():
    """Install the stubs, remembering what was replaced."""
    if _INSTALLED:
        return

    qtcore = _auto_module(
        "PySide.QtCore",
        {"QT_TRANSLATE_NOOP": lambda context, text: text},
    )
    qtcore.Qt = _AutoClass()
    qtcore.Signal = lambda *a, **k: _AutoClass()

    modules = {
        "FreeCAD": _make_freecad(),
        "FreeCADGui": _make_freecadgui(),
        "PySide": types.ModuleType("PySide"),
        "PySide.QtCore": qtcore,
        "PySide.QtGui": _auto_module("PySide.QtGui"),
        "PySide.QtWidgets": _auto_module("PySide.QtWidgets"),
        "PySide.QtOpenGLWidgets": _auto_module("PySide.QtOpenGLWidgets"),
        "pivy": types.ModuleType("pivy"),
        "pivy.coin": _auto_module(
            "pivy.coin",
            {"SO_SWITCH_ALL": -3, "SO_SWITCH_NONE": -1},
        ),
    }
    modules["PySide"].QtCore = modules["PySide.QtCore"]
    modules["PySide"].QtGui = modules["PySide.QtGui"]
    modules["PySide"].QtWidgets = modules["PySide.QtWidgets"]
    modules["pivy"].coin = modules["pivy.coin"]

    for name, module in modules.items():
        _INSTALLED.append((name, sys.modules.get(name)))
        sys.modules[name] = module


def uninstall():
    """Undo :func:`install`."""
    while _INSTALLED:
        name, previous = _INSTALLED.pop()
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    recorded_commands.clear()
    _PARAMETERS.clear()
