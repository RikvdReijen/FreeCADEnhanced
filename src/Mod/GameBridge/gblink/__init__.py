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
"""The live link: FreeCAD pushing a document at a running engine as it changes.

:mod:`~gblink.protocol` is the wire format, :mod:`~gblink.session` works out
what actually changed, :mod:`~gblink.server` runs inside FreeCAD and
:mod:`~gblink.client` is the engine side.  None of them import FreeCAD or an
engine, which is what lets the whole link be tested end to end in one process.
"""

from . import protocol  # noqa: F401
from .client import LinkClient, SceneMirror  # noqa: F401
from .server import DEFAULT_PORT, ClientConnection, LinkServer  # noqa: F401
from .session import LinkSession  # noqa: F401

__all__ = [
    "ClientConnection",
    "DEFAULT_PORT",
    "LinkClient",
    "LinkServer",
    "LinkSession",
    "SceneMirror",
    "protocol",
]
