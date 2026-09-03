# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Process-wide state broker for the XR workbench.

The XR render loop, the GUI commands and the sync server all need to reach the
same handful of objects (the live :class:`XRwidget`, the environment currently
loaded, the paint session, the companion server).  Keeping that state in one
small module avoids circular imports between ``xrcore.commonXR`` and the
feature packages, and gives the commands a single place to fail gracefully when
an optional dependency is missing.
"""

import os
import threading

import FreeCAD

__all__ = [
    "get_widget",
    "set_widget",
    "require_widget",
    "get_environment_id",
    "set_environment_id",
    "get_paint_session",
    "set_paint_session",
    "get_sculpt_session",
    "set_sculpt_session",
    "get_sketch_session",
    "set_sketch_session",
    "get_mrc_session",
    "set_mrc_session",
    "sync_server",
    "start_sync_server",
    "stop_sync_server",
    "autostart_if_enabled",
    "preferences",
    "user_dir",
    "XRServiceError",
]


class XRServiceError(RuntimeError):
    """Raised when a command needs something that is not available."""


_lock = threading.RLock()
_state = {
    "widget": None,
    "environment_id": None,
    "paint_session": None,
    "sculpt_session": None,
    "sketch_session": None,
    "mrc_session": None,
    "sync_server": None,
}


def preferences():
    """Parameter group shared with :mod:`xrcore.preferences`."""
    return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/XR")


def user_dir(*parts):
    """Path inside the per-user XR state directory, created on demand."""
    base = os.path.join(FreeCAD.getUserAppDataDir(), "xr")
    path = os.path.join(base, *parts) if parts else base
    directory = path if not parts else os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# live XR viewer
# --------------------------------------------------------------------------


def set_widget(widget):
    with _lock:
        _state["widget"] = widget


def get_widget():
    with _lock:
        return _state["widget"]


def require_widget():
    widget = get_widget()
    if widget is None:
        raise XRServiceError(
            "The XR viewer is not running. Start it first (Virtual Reality → Open XR viewer)."
        )
    return widget


# --------------------------------------------------------------------------
# environment switcher
# --------------------------------------------------------------------------


def set_environment_id(env_id):
    with _lock:
        _state["environment_id"] = env_id
    preferences().SetString("Environment", env_id or "")


def get_environment_id():
    with _lock:
        env_id = _state["environment_id"]
    if env_id:
        return env_id
    stored = preferences().GetString("Environment", "studio")
    return stored or "studio"


# --------------------------------------------------------------------------
# paint session
# --------------------------------------------------------------------------


def set_paint_session(session):
    with _lock:
        _state["paint_session"] = session


def get_paint_session():
    with _lock:
        return _state["paint_session"]


# --------------------------------------------------------------------------
# sculpting
# --------------------------------------------------------------------------


def set_sculpt_session(session):
    with _lock:
        _state["sculpt_session"] = session


def get_sculpt_session():
    with _lock:
        return _state["sculpt_session"]


# --------------------------------------------------------------------------
# sketch toolset
# --------------------------------------------------------------------------


def set_sketch_session(session):
    with _lock:
        _state["sketch_session"] = session


def get_sketch_session():
    with _lock:
        return _state["sketch_session"]


# --------------------------------------------------------------------------
# mixed reality capture
# --------------------------------------------------------------------------


def set_mrc_session(session):
    with _lock:
        _state["mrc_session"] = session


def get_mrc_session():
    """The capture session, or None when the XR viewer is not running."""
    with _lock:
        return _state["mrc_session"]


def require_mrc_session():
    session = get_mrc_session()
    if session is None:
        raise XRServiceError(
            "Mixed reality capture needs the XR viewer running "
            "(Virtual Reality \u2192 Open XR viewer)."
        )
    return session


# --------------------------------------------------------------------------
# companion sync server
# --------------------------------------------------------------------------


def sync_server():
    with _lock:
        return _state["sync_server"]


def start_sync_server(port=None):
    """Start the LAN companion server, returning the running instance."""
    with _lock:
        running = _state["sync_server"]
        if running is not None and running.is_running():
            return running

    from xrsync import server as sync_server_mod

    if port is None:
        port = preferences().GetInt("SyncPort", 0) or None
    instance = sync_server_mod.SyncServer(port=port)
    instance.start()
    with _lock:
        _state["sync_server"] = instance
    FreeCAD.Console.PrintMessage(f"XR: sync server listening on {instance.url}\n")
    return instance


def stop_sync_server():
    with _lock:
        instance = _state["sync_server"]
        _state["sync_server"] = None
    if instance is not None:
        instance.stop()
        FreeCAD.Console.PrintMessage("XR: sync server stopped\n")
    return instance is not None


def autostart_if_enabled():
    if preferences().GetBool("SyncAutostart", False):
        start_sync_server()
