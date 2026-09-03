# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                          *
# *   This file is part of FreeCAD.                                          *
# *                                                                          *
# *   FreeCAD is free software: you can redistribute it and/or modify it     *
# *   under the terms of the GNU Lesser General Public License as            *
# *   published by the Free Software Foundation, either version 2.1 of the   *
# *   License, or (at your option) any later version.                        *
# *                                                                          *
# *   FreeCAD is distributed in the hope that it will be useful, but         *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU      *
# *   Lesser General Public License for more details.                        *
# ***************************************************************************
"""Where xrsync keeps its state on disk.

Everything lives under ``~/.FreeCAD/xr`` (overridable with ``FREECAD_XR_HOME``,
which the tests use to stay out of the real user directory).  Secrets — paired
device tokens, Google Drive tokens — are written with 0600 permissions.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from typing import Any, Optional

__all__ = [
    "xr_home",
    "xr_path",
    "ensure_dir",
    "read_json",
    "write_json",
    "DEVICES_FILE",
    "GDRIVE_TOKEN_FILE",
    "GDRIVE_CLIENT_FILE",
    "DRIVE_CACHE_DIR",
    "PROJECTS_DIR",
]

DEVICES_FILE = "paired_devices.json"
GDRIVE_TOKEN_FILE = "gdrive_token.json"
GDRIVE_CLIENT_FILE = "gdrive_client.json"
DRIVE_CACHE_DIR = "drive-cache"
PROJECTS_DIR = "projects"

_ENV_HOME = "FREECAD_XR_HOME"


def xr_home() -> str:
    """Return the xrsync state directory (``~/.FreeCAD/xr`` by default)."""
    override = os.environ.get(_ENV_HOME)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("FREECAD_USER_HOME")
    if base:
        return os.path.join(os.path.abspath(os.path.expanduser(base)), "xr")
    return os.path.join(os.path.expanduser("~"), ".FreeCAD", "xr")


def xr_path(*parts: str) -> str:
    """Path inside :func:`xr_home`."""
    return os.path.join(xr_home(), *parts)


def ensure_dir(path: str, private: bool = True) -> str:
    """Create ``path`` (and parents), 0700 for private directories."""
    os.makedirs(path, exist_ok=True)
    if private:
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:  # pragma: no cover - exotic filesystems
            pass
    return path


def read_json(path: str, default: Any = None) -> Any:
    """Read a JSON file, returning ``default`` when it is missing or broken."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path: str, data: Any, private: bool = True) -> str:
    """Atomically write JSON; ``private`` files get 0600 permissions."""
    directory = os.path.dirname(os.path.abspath(path))
    ensure_dir(directory, private=private)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if private:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
