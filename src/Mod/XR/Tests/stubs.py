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

__all__ = [
    "install",
    "uninstall",
    "install_engine_stubs",
    "StubParameterGroup",
    "recorded_commands",
]

recorded_commands = {}


class _AutoMeta(type):
    """Metaclass so ``SomeStub.some_attribute`` works without an instance.

    ``commonXR`` calls ``QGuiApplication.platformName()`` on the class itself
    while deciding which windowing interface to use, so plain instance-level
    ``__getattr__`` is not enough.
    """

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _AutoCallable(name)


class _AutoCallable:
    """Callable stub that answers to attribute access and comparison."""

    def __init__(self, name="stub"):
        self._name = name

    def __call__(self, *args, **kwargs):
        # platformName() decides the OpenGL windowing interface; "xcb" is the
        # branch that exercises the most code.
        if self._name == "platformName":
            return "xcb"
        return _AutoCallable(self._name)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _AutoCallable(name)

    def __eq__(self, other):
        return isinstance(other, _AutoCallable) and other._name == self._name

    def __hash__(self):
        return hash(("_AutoCallable", self._name))


class _AutoClass(metaclass=_AutoMeta):
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

    # Qt lookups like QComboBox.findData/findText return an int index, and the
    # calling code compares it against 0 to decide whether the item was found.
    # A stub that has no real items has genuinely not found anything, so it
    # behaves as Qt's "not found" sentinel rather than blowing up on ``>=``.
    def __index__(self):
        return -1

    def __int__(self):
        return -1

    def __float__(self):
        return -1.0

    def __lt__(self, other):
        return -1 < other

    def __le__(self, other):
        return -1 <= other

    def __gt__(self, other):
        return -1 > other

    def __ge__(self, other):
        return -1 >= other


def _constant_module(name, extra=None):
    """A module whose attributes are unique integers.

    ``pyopenxr`` flag constants get combined with ``|`` and PyOpenGL constants
    are used as dictionary keys, so unlike the class stubs these have to be
    real, hashable, bit-combinable numbers.
    """
    module = types.ModuleType(name)
    cache = {}
    counter = [1]

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr not in cache:
            cache[attr] = counter[0]
            counter[0] <<= 1
        return cache[attr]

    module.__getattr__ = __getattr__
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    return module


def _openxr_module():
    """Stand-in for ``pyopenxr``.

    Names in SCREAMING_CASE are flag constants that get OR-ed together, while
    CamelCase names are ctypes structures — ``commonXR`` puts one of them in a
    ``ctypes.POINTER`` annotation, which only accepts a real ctypes type.
    """
    import ctypes

    module = types.ModuleType("xr")
    cache = {}
    counter = [1]

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr not in cache:
            if attr.replace("_", "").isupper():
                cache[attr] = counter[0]
                counter[0] <<= 1
            else:
                cache[attr] = type(attr, (ctypes.Structure,), {})
        return cache[attr]

    module.__getattr__ = __getattr__
    return module


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

    # Anything else FreeCAD exposes (Placement, Rotation, Base, Units …) turns
    # into a permissive stub class on first access.
    cache = {}

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr not in cache:
            cache[attr] = type(attr, (_AutoClass,), {})
        return cache[attr]

    module.__getattr__ = __getattr__
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
        "Part": _auto_module("Part"),
        "Draft": _auto_module("Draft"),
        "Mesh": _auto_module("Mesh"),
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


_ENGINE_MODULES = (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtWidgets",
    "shiboken6",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLX",
    "OpenGL.EGL",
    "OpenGL.WGL",
    "xr",
)


def install_engine_stubs():
    """Additionally stub PySide6, PyOpenGL and pyopenxr.

    That is enough for ``import xrcore.commonXR`` to succeed, which turns the
    port of the upstream OpenXR engine into something CI can check.
    """
    install()

    pyside6 = types.ModuleType("PySide6")
    submodules = {}
    for sub in ("QtCore", "QtGui", "QtOpenGL", "QtOpenGLWidgets", "QtWidgets"):
        module = _auto_module(f"PySide6.{sub}")
        submodules[sub] = module
        setattr(pyside6, sub, module)
    submodules["QtCore"].SIGNAL = lambda text: text

    opengl = types.ModuleType("OpenGL")
    gl_modules = {}
    for sub in ("GL", "GLX", "EGL", "WGL"):
        module = _constant_module(f"OpenGL.{sub}")
        gl_modules[sub] = module
        setattr(opengl, sub, module)

    modules = {
        "PySide6": pyside6,
        "shiboken6": _auto_module("shiboken6"),
        "OpenGL": opengl,
        "xr": _openxr_module(),
    }
    for sub, module in submodules.items():
        modules[f"PySide6.{sub}"] = module
    for sub, module in gl_modules.items():
        modules[f"OpenGL.{sub}"] = module

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
