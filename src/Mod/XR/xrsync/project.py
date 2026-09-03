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
"""``.fcxrpkg`` working sets: binding a FreeCAD document to an XR remote.

A *project* ties one FreeCAD document to one remote — either a LAN sync server
(host/port/token) or a Google Drive file id — and remembers the hash and time
of the last successful sync so :meth:`XrProject.sync_now` can decide what to
do.  The binding is stored in the document itself (``doc.Meta``) when FreeCAD
supports it, and always mirrored to a ``.fcxrpkg`` JSON sidecar so it survives
documents that were never saved and can be inspected by hand.

Everything degrades gracefully when the network is down: no exception escapes
:meth:`XrProject.sync_now`, it simply returns a status with ``offline`` set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .paths import PROJECTS_DIR, ensure_dir, read_json, write_json, xr_path

__all__ = [
    "PROJECT_EXTENSION",
    "META_KEY",
    "Binding",
    "SyncStatus",
    "XrProject",
    "open_project",
    "pull_drive_file",
    "push_document_to_drive",
    "PushResult",
]

logger = logging.getLogger("xrsync.project")

PROJECT_EXTENSION = ".fcxrpkg"
META_KEY = "XR_Sync"

KIND_NONE = "none"
KIND_LAN = "lan"
KIND_DRIVE = "drive"


# ---------------------------------------------------------------------------
# binding / status
# ---------------------------------------------------------------------------


@dataclass
class Binding:
    """Where a document syncs to, and what we last saw there."""

    doc_name: str = ""
    kind: str = KIND_NONE
    # LAN
    host: str = ""
    port: int = 0
    token: str = ""
    remote_doc: str = ""
    # Drive
    file_id: str = ""
    parent_id: str = ""
    remote_name: str = ""
    # bookkeeping
    last_hash: str = ""
    last_sync: float = 0.0
    last_status: str = ""
    environment: str = ""
    user_scale: float = 0.0
    lod: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "Binding":
        if not isinstance(payload, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})

    @property
    def configured(self) -> bool:
        if self.kind == KIND_LAN:
            return bool(self.host)
        if self.kind == KIND_DRIVE:
            return bool(self.file_id or self.parent_id)
        return False

    def describe(self) -> str:
        if self.kind == KIND_LAN:
            return "http://%s:%d" % (self.host, self.port or 47810)
        if self.kind == KIND_DRIVE:
            return "drive:%s" % (self.remote_name or self.file_id or "?")
        return "not bound"


@dataclass
class SyncStatus:
    """The small status object :meth:`XrProject.sync_now` returns."""

    ok: bool = True
    action: str = "none"
    message: str = ""
    hash: str = ""
    timestamp: float = field(default_factory=time.time)
    offline: bool = False
    remote: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.ok)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return "%s: %s" % (self.action, self.message or ("ok" if self.ok else "failed"))


# ---------------------------------------------------------------------------
# the project
# ---------------------------------------------------------------------------


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "document")


class XrProject:
    """Binds one FreeCAD document to an XR remote."""

    def __init__(self, document: Any = None, binding: Optional[Binding] = None) -> None:
        self.document = document
        self.binding = binding or Binding(doc_name=self._doc_name())
        if not self.binding.doc_name:
            self.binding.doc_name = self._doc_name()
        if binding is None:
            self.load()

    # -- document helpers --------------------------------------------------

    def _doc_name(self) -> str:
        return str(getattr(self.document, "Name", "") or "")

    def _doc_path(self) -> str:
        return str(getattr(self.document, "FileName", "") or "")

    @property
    def sidecar_path(self) -> str:
        """Where the ``.fcxrpkg`` lives for this document."""
        path = self._doc_path()
        if path:
            base, _ = os.path.splitext(path)
            return base + PROJECT_EXTENSION
        return os.path.join(
            xr_path(PROJECTS_DIR), _safe_name(self._doc_name()) + PROJECT_EXTENSION
        )

    # -- persistence -------------------------------------------------------

    def load(self) -> Binding:
        """Load the binding from ``doc.Meta`` or the sidecar."""
        meta = getattr(self.document, "Meta", None)
        if isinstance(meta, dict) and meta.get(META_KEY):
            try:
                self.binding = Binding.from_dict(json.loads(meta[META_KEY]))
                self.binding.doc_name = self.binding.doc_name or self._doc_name()
                return self.binding
            except (TypeError, ValueError):
                logger.warning("ignoring unreadable %s document metadata", META_KEY)
        payload = read_json(self.sidecar_path, default=None)
        if isinstance(payload, dict):
            self.binding = Binding.from_dict(payload.get("binding", payload))
            self.binding.doc_name = self.binding.doc_name or self._doc_name()
        return self.binding

    def save(self) -> str:
        """Persist the binding to the document metadata and the sidecar."""
        payload = {
            "format": "fcxrpkg",
            "version": 1,
            "binding": self.binding.to_dict(),
            "document": {"name": self._doc_name(), "path": self._doc_path()},
        }
        meta = getattr(self.document, "Meta", None)
        if isinstance(meta, dict):
            try:
                meta[META_KEY] = json.dumps(self.binding.to_dict(), sort_keys=True)
                # FreeCAD's Meta is a copy-on-read property: assign it back.
                self.document.Meta = meta
            except Exception as exc:
                logger.debug("cannot store XR metadata in the document: %s", exc)
        ensure_dir(os.path.dirname(os.path.abspath(self.sidecar_path)), private=False)
        return write_json(self.sidecar_path, payload, private=False)

    # -- binding -----------------------------------------------------------

    def bind_lan(
        self,
        host: str,
        port: int = 47810,
        token: str = "",
        remote_doc: str = "",
    ) -> Binding:
        """Bind to a LAN sync server."""
        self.binding.kind = KIND_LAN
        self.binding.host = str(host)
        self.binding.port = int(port)
        self.binding.token = str(token or "")
        self.binding.remote_doc = str(remote_doc or self._doc_name())
        self.save()
        return self.binding

    def bind_drive(
        self, file_id: str, name: str = "", parent_id: str = ""
    ) -> Binding:
        """Bind to a Google Drive file."""
        self.binding.kind = KIND_DRIVE
        self.binding.file_id = str(file_id or "")
        self.binding.remote_name = str(name or "")
        self.binding.parent_id = str(parent_id or "")
        self.save()
        return self.binding

    def unbind(self) -> Binding:
        """Forget the remote (keeps the sidecar so history is not lost)."""
        self.binding = Binding(doc_name=self._doc_name())
        self.save()
        return self.binding

    # -- syncing -----------------------------------------------------------

    def local_hash(self) -> str:
        """Cheap hash of the bound document, or ``""`` without FreeCAD."""
        try:
            from . import scene_export

            return scene_export.scene_hash(self.document)
        except Exception as exc:
            logger.debug("cannot hash the document: %s", exc)
            return ""

    def sync_now(self, force: bool = False) -> SyncStatus:
        """Do whatever this binding needs, and never raise.

        Returns a :class:`SyncStatus`; network problems come back as
        ``action="offline"`` rather than an exception.
        """
        binding = self.binding
        if not binding.configured:
            return SyncStatus(
                ok=False, action="unbound",
                message="this document is not bound to an XR remote",
            )
        try:
            if binding.kind == KIND_LAN:
                status = self._sync_lan(force)
            elif binding.kind == KIND_DRIVE:
                status = self._sync_drive(force)
            else:
                status = SyncStatus(
                    ok=False, action="unbound", message="unknown remote kind %r"
                    % binding.kind,
                )
        except Exception as exc:  # the GUI must never see a traceback from here
            status = self._offline_status(exc)
        binding.last_status = status.action
        if status.ok and status.hash:
            binding.last_hash = status.hash
            binding.last_sync = status.timestamp
        try:
            self.save()
        except OSError as exc:
            logger.warning("cannot persist the XR project state: %s", exc)
        return status

    def _offline_status(self, exc: Exception) -> SyncStatus:
        from .client import TransportError
        from .gdrive import GDriveOfflineError, NotAuthenticatedError, NotConfiguredError

        if isinstance(exc, (TransportError, GDriveOfflineError, OSError)):
            return SyncStatus(
                ok=False, action="offline", offline=True,
                message="the remote is unreachable: %s" % (exc,),
                remote=self.binding.describe(),
            )
        if isinstance(exc, (NotConfiguredError, NotAuthenticatedError)):
            return SyncStatus(
                ok=False, action="not_authenticated", message=str(exc),
                remote=self.binding.describe(),
            )
        logger.exception("XR sync failed")
        return SyncStatus(
            ok=False, action="error", message=str(exc),
            remote=self.binding.describe(),
        )

    # -- LAN ---------------------------------------------------------------

    def client(self):
        """A :class:`xrsync.client.SyncClient` for a LAN binding."""
        from .client import SyncClient

        if self.binding.kind != KIND_LAN:
            raise ValueError("this project is not bound to a LAN server")
        return SyncClient(
            host=self.binding.host,
            port=self.binding.port or 47810,
            token=self.binding.token or None,
        )

    def _sync_lan(self, force: bool) -> SyncStatus:
        from .client import AuthError

        binding = self.binding
        with self.client() as client:
            try:
                remote_hash = client.scene_hash(binding.remote_doc or None)
            except AuthError as exc:
                return SyncStatus(
                    ok=False, action="not_paired", message=str(exc),
                    remote=binding.describe(),
                )
            local = self.local_hash()
            if remote_hash == binding.last_hash and not force:
                return SyncStatus(
                    action="up_to_date", hash=remote_hash, remote=binding.describe(),
                    message="the remote scene has not changed",
                    detail={"local_hash": local},
                )
            return SyncStatus(
                action="remote_changed", hash=remote_hash, remote=binding.describe(),
                message="the remote scene changed — pull() to import it",
                detail={"local_hash": local, "previous_hash": binding.last_hash},
            )

    def pull_lan(self, lod: Optional[int] = None) -> SyncStatus:
        """Fetch the remote scene and import it into the document."""
        from . import scene_import

        binding = self.binding
        try:
            with self.client() as client:
                data = client.scene(
                    binding.remote_doc or None, lod if lod is not None else binding.lod
                )
        except Exception as exc:
            return self._offline_status(exc)
        from .fcxr import content_hash

        digest = content_hash(data)
        try:
            objects = scene_import.import_package(data, self.document)
        except Exception as exc:
            return SyncStatus(ok=False, action="error", message=str(exc))
        binding.last_hash = digest
        binding.last_sync = time.time()
        self.save()
        return SyncStatus(
            action="pulled", hash=digest, remote=binding.describe(),
            message="imported %d object(s)" % len(objects),
            detail={"objects": len(objects)},
        )

    # -- Drive -------------------------------------------------------------

    def drive_sync(self):
        from .gdrive import GoogleDriveSync

        return GoogleDriveSync()

    def _sync_drive(self, force: bool) -> SyncStatus:
        from .gdrive import ConflictError

        binding = self.binding
        sync = self.drive_sync()
        path = self._doc_path()

        if not binding.file_id:
            if not path or not os.path.isfile(path):
                return SyncStatus(
                    ok=False, action="error",
                    message="save the document before pushing it to Drive",
                )
            entry = sync.push(path, parent=binding.parent_id or None)
            binding.file_id = entry.file_id
            binding.remote_name = entry.name
            return SyncStatus(
                action="pushed", hash=entry.md5_checksum or "",
                remote=binding.describe(),
                message="uploaded %s to Google Drive" % entry.name,
                detail=entry.to_dict(),
            )

        state = sync.status(binding.file_id, path or None)
        if state == "offline":
            return SyncStatus(
                ok=False, action="offline", offline=True,
                message="Google Drive is unreachable", remote=binding.describe(),
            )
        if state in ("in_sync", "unknown") and not force:
            if state == "unknown":
                local = sync.pull(binding.file_id)
                record = sync.record(binding.file_id)
                return SyncStatus(
                    action="pulled", remote=binding.describe(),
                    message="downloaded to %s" % local, detail={"local_path": local},
                    hash=(record.md5_checksum if record else "") or "",
                )
            return SyncStatus(
                action="up_to_date", remote=binding.describe(),
                message="the Drive copy matches the local file",
            )
        if state == "remote_changes":
            local = sync.pull(binding.file_id)
            record = sync.record(binding.file_id)
            return SyncStatus(
                action="pulled", remote=binding.describe(),
                message="downloaded the newer Drive copy to %s" % local,
                hash=(record.md5_checksum if record else "") or "",
                detail={"local_path": local},
            )
        if state == "conflict" and not force:
            return SyncStatus(
                ok=False, action="conflict", remote=binding.describe(),
                message="both copies changed — pull to a new file or push with force",
            )
        try:
            entry = sync.push(path, file_id=binding.file_id, force=force)
        except ConflictError as exc:
            return SyncStatus(
                ok=False, action="conflict", message=str(exc),
                remote=binding.describe(),
            )
        binding.remote_name = entry.name
        return SyncStatus(
            action="pushed", hash=entry.md5_checksum or "", remote=binding.describe(),
            message="uploaded %s to Google Drive" % entry.name,
            detail=entry.to_dict(),
        )


def open_project(document: Any = None) -> XrProject:
    """Load (or create) the :class:`XrProject` of ``document``."""
    if document is None:
        try:
            import FreeCAD  # noqa: F401

            document = FreeCAD.ActiveDocument
        except Exception:
            document = None
    return XrProject(document)


# ---------------------------------------------------------------------------
# Drive helpers used by the GUI layer
# ---------------------------------------------------------------------------


def pull_drive_file(client: Any, entry: Any) -> str:
    """Download ``entry`` into the Drive cache and return the local path.

    ``entry`` may be a :class:`xrsync.gdrive.DriveEntry` or a bare file id.
    """
    from .gdrive import GoogleDriveSync

    file_id = getattr(entry, "file_id", None) or str(entry)
    if not file_id:
        raise ValueError("pull_drive_file() needs a file id")
    return GoogleDriveSync(client=client).pull(file_id)


@dataclass
class PushResult:
    """What :func:`push_document_to_drive` uploaded."""

    name: str = ""
    file_id: str = ""
    fcxr_name: str = ""
    fcxr_file_id: str = ""
    parent_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def push_document_to_drive(
    client: Any,
    document: Any,
    parent_id: Optional[str] = None,
    name: Optional[str] = None,
    also_fcxr: bool = True,
) -> PushResult:
    """Upload a document's ``.FCStd`` (and optionally an ``.fcxr``) to Drive.

    An existing file with the same name in ``parent_id`` is updated instead of
    duplicated, so repeated pushes keep one file id.
    """
    from . import scene_export
    from .gdrive import GoogleDriveClient, escape_query_value

    if client is None:
        client = GoogleDriveClient.from_stored_credentials()

    path = str(getattr(document, "FileName", "") or "")
    if path and hasattr(document, "save"):
        try:
            document.save()
        except Exception as exc:
            logger.warning("could not save the document before pushing: %s", exc)
    if not path or not os.path.isfile(path):
        raise ValueError("save the document to disk before pushing it to Drive")

    fcstd_name = name or os.path.basename(path)
    with open(path, "rb") as handle:
        fcstd_data = handle.read()

    def _upload(upload_name: str, data: bytes):
        existing = client.list_files(
            query="name = '%s' and '%s' in parents"
            % (escape_query_value(upload_name), escape_query_value(parent_id or "root")),
            filter_extensions=False,
            max_results=1,
        )
        if existing:
            return client.update(existing[0].file_id, data, name=upload_name)
        return client.upload(upload_name, data, parent=parent_id)

    entry = _upload(fcstd_name, fcstd_data)
    result = PushResult(
        name=entry.name, file_id=entry.file_id, parent_id=parent_id or ""
    )

    if also_fcxr:
        try:
            data = scene_export.export_document_bytes(document)
        except Exception as exc:
            logger.warning("cannot export an .fcxr companion: %s", exc)
        else:
            base, _ = os.path.splitext(fcstd_name)
            fcxr_entry = _upload(base + ".fcxr", data)
            result.fcxr_name = fcxr_entry.name
            result.fcxr_file_id = fcxr_entry.file_id

    project = XrProject(document)
    project.bind_drive(entry.file_id, name=entry.name, parent_id=parent_id or "")
    return result
