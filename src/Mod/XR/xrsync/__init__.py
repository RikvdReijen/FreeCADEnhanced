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
"""``xrsync`` — FCXR packaging, LAN sync and Google Drive for the XR workbench.

Public surface (see ``Resources/doc/ARCHITECTURE.md`` §1, §3 and §4)::

    from xrsync import FcxrWriter, FcxrReader, read, content_hash
    from xrsync import SyncServer, SyncClient, DirectDocumentBridge
    from xrsync import export_document, import_package, scene_hash
    from xrsync import GoogleDriveClient, GoogleDriveSync, XrProject

Submodules are imported lazily (PEP 562) so ``import xrsync`` stays cheap and
so importing the pure modules never drags in sockets or FreeCAD.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

__version__ = "1.0"

#: name -> submodule that provides it
_EXPORTS: Dict[str, str] = {
    # fcxr container (§1)
    "FCXR_MAGIC": "fcxr",
    "FCXR_VERSION": "fcxr",
    "FcxrError": "fcxr",
    "FcxrDocument": "fcxr",
    "FcxrReader": "fcxr",
    "FcxrWriter": "fcxr",
    "content_hash": "fcxr",
    "read": "fcxr",
    "validate_manifest": "fcxr",
    "validate_paint": "fcxr",
    "validate_sculpt": "fcxr",
    "validate_vector": "fcxr",
    # protocol (§3)
    "PROTOCOL_VERSION": "protocol",
    "DEFAULT_PORT": "protocol",
    "DISCOVERY_PORT": "protocol",
    "ProtocolError": "protocol",
    "HelloResponse": "protocol",
    "PairRequest": "protocol",
    "PairResponse": "protocol",
    "DocumentInfo": "protocol",
    "DocumentsResponse": "protocol",
    "Event": "protocol",
    "EventsResponse": "protocol",
    "EnvironmentInfo": "protocol",
    "EnvironmentsResponse": "protocol",
    "ApplyResponse": "protocol",
    "ErrorResponse": "protocol",
    "DiscoveryOffer": "protocol",
    "generate_token": "protocol",
    "generate_pairing_code": "protocol",
    # export / import
    "ExportOptions": "scene_export",
    "export_document": "scene_export",
    "export_document_bytes": "scene_export",
    "export_objects": "scene_export",
    "export_selection": "scene_export",
    "scene_hash": "scene_export",
    "document_info": "scene_export",
    "document_thumbnail": "scene_export",
    "deviation_for_lod": "scene_export",
    "import_package": "scene_import",
    "extract_meshes": "scene_import",
    "MeshSpec": "scene_import",
    # server / client (§3)
    "SyncServer": "server",
    "XrSyncServer": "server",
    "DocumentBridge": "server",
    "DirectDocumentBridge": "server",
    "MarshalledBridge": "server",
    "DeviceRegistry": "server",
    "BridgeError": "server",
    "SyncClient": "client",
    "XrSyncClient": "client",
    "SyncError": "client",
    "TransportError": "client",
    "AuthError": "client",
    "HttpError": "client",
    "discover": "client",
    # Google Drive
    "GoogleDriveClient": "gdrive",
    "GoogleDriveSync": "gdrive",
    "DeviceCodeFlow": "gdrive",
    "LoopbackFlow": "gdrive",
    "DriveEntry": "gdrive",
    "GDriveError": "gdrive",
    "GDriveOfflineError": "gdrive",
    "NotConfiguredError": "gdrive",
    "NotAuthenticatedError": "gdrive",
    "ConflictError": "gdrive",
    "account_status": "gdrive",
    "load_client_config": "gdrive",
    "save_client_config": "gdrive",
    "sign_out": "gdrive",
    # projects
    "XrProject": "project",
    "Binding": "project",
    "SyncStatus": "project",
    "open_project": "project",
    "pull_drive_file": "project",
    "push_document_to_drive": "project",
    # paths
    "xr_home": "paths",
    "xr_path": "paths",
}

_SUBMODULES: Tuple[str, ...] = (
    "fcxr",
    "protocol",
    "scene_export",
    "scene_import",
    "server",
    "client",
    "gdrive",
    "project",
    "paths",
)

__all__ = sorted(set(_EXPORTS) | set(_SUBMODULES)) + ["__version__"]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access for the re-exports above."""
    if name in _SUBMODULES:
        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    value = getattr(importlib.import_module("." + module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> Any:
    return sorted(set(__all__) | set(globals()))
