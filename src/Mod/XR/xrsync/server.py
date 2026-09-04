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
"""The desktop LAN sync server (ARCHITECTURE.md §3).

Threaded ``http.server`` implementation of every endpoint in §3 plus the UDP
discovery responder.  It never touches a FreeCAD document itself: all document
access goes through a :class:`DocumentBridge`, so the GUI layer can supply a
bridge that marshals every call onto the main thread (see
:class:`MarshalledBridge`).  A :class:`DirectDocumentBridge` that calls FreeCAD
straight away is provided for console use and tests.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import presence as _presence
from . import protocol as P
from .paths import DEVICES_FILE, read_json, write_json, xr_path

__all__ = [
    "DocumentBridge",
    "DirectDocumentBridge",
    "MarshalledBridge",
    "DeviceRegistry",
    "EventLog",
    "SceneCache",
    "SyncServer",
    "XrSyncServer",
    "BridgeError",
]

logger = logging.getLogger("xrsync.server")

#: refuse absurd request bodies outright (paint packages are the big ones)
MAX_BODY_BYTES = 64 * 1024 * 1024
#: how many exported scenes to keep in memory
SCENE_CACHE_SIZE = 8
#: how many events to keep for late long-pollers
EVENT_LOG_SIZE = 512


class BridgeError(Exception):
    """Raised by a :class:`DocumentBridge` for a client-visible failure.

    ``status`` becomes the HTTP status code (404 for an unknown document, ...).
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


# ---------------------------------------------------------------------------
# document bridge
# ---------------------------------------------------------------------------


class DocumentBridge:
    """Everything the server needs from the FreeCAD side.

    The base class implements the parts that do not need a document (the
    environment registry and the XR session state); subclasses fill in the
    rest.  **No method may block the GUI thread**: a GUI caller wraps its
    bridge in :class:`MarshalledBridge` so calls hop to the main thread.
    """

    # -- documents ---------------------------------------------------------

    def list_documents(self) -> List[Dict[str, Any]]:
        """Return one dict per open document (``protocol.DocumentInfo`` shape)."""
        raise NotImplementedError

    def default_document(self) -> Optional[str]:
        """Internal name of the document used when a request omits ``doc``."""
        documents = self.list_documents()
        return documents[0]["name"] if documents else None

    def scene_hash(self, doc: Optional[str]) -> str:
        """Cheap change-detection hash for a document."""
        raise NotImplementedError

    def export_scene(self, doc: Optional[str], lod: int) -> bytes:
        """Export a document as FCXR bytes."""
        raise NotImplementedError

    def thumbnail(self, doc: Optional[str]) -> Optional[bytes]:
        """PNG thumbnail for a document, or ``None``."""
        return None

    # -- inbound edits -----------------------------------------------------

    def apply_paint(self, data: bytes, doc: Optional[str] = None) -> Dict[str, Any]:
        """Apply an FCXR package carrying a ``paint`` manifest section."""
        raise BridgeError("this server cannot apply paint", 501)

    def apply_vector(
        self, vector: Dict[str, Any], doc: Optional[str] = None
    ) -> Dict[str, Any]:
        """Apply a vector document (§4) as Draft geometry."""
        raise BridgeError("this server cannot apply vector documents", 501)

    # -- environments and session state ------------------------------------

    def apply_move(self, move: Dict[str, Any]) -> bool:
        """Apply a peer's object placement (§3b). The base class only broadcasts."""
        return False

    def list_environments(self) -> List[Dict[str, Any]]:
        """Environment ids/names from :mod:`xrenv`, empty when unavailable."""
        registry = _xrenv_registry()
        if registry is None:
            return []
        try:
            infos = list(registry.list_environments())
        except Exception as exc:
            logger.warning("xrenv registry failed: %s", exc)
            return []
        out: List[Dict[str, Any]] = []
        for info in infos:
            out.append(
                {
                    "id": str(_field(info, "id", "")),
                    "name": str(_field(info, "name", "")),
                    "description": str(_field(info, "description", "")),
                    "user_scale": float(_field(info, "user_scale", 1.0) or 1.0),
                }
            )
        return out

    def get_environment(self, env_id: str) -> Optional[Dict[str, Any]]:
        """The declarative spec (§2) of one environment."""
        registry = _xrenv_registry()
        if registry is None:
            return None
        try:
            environment = registry.get(env_id)
        except Exception:
            return None
        if environment is None:
            return None
        spec = getattr(environment, "spec", None)
        return spec if isinstance(spec, dict) else None

    def state(self) -> Dict[str, Any]:
        """Current XR session state (environment id and user scale)."""
        try:
            from xrcore import environment_bridge  # type: ignore
        except Exception:
            return {"environment": None, "scale": 1.0}
        try:
            current = environment_bridge.current_state()
        except Exception as exc:
            logger.warning("environment_bridge.current_state() failed: %s", exc)
            return {"environment": None, "scale": 1.0}
        if isinstance(current, dict):
            return {
                "environment": current.get("environment"),
                "scale": float(current.get("scale", 1.0) or 1.0),
            }
        return {
            "environment": getattr(current, "environment", None),
            "scale": float(getattr(current, "scale", 1.0) or 1.0),
        }


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or an object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _xrenv_registry():
    try:
        from xrenv import registry  # type: ignore
    except Exception:
        return None
    return registry


class DirectDocumentBridge(DocumentBridge):
    """Default bridge: talks to FreeCAD directly, in the calling thread.

    Safe from a console session or a worker that owns the document; the GUI
    must wrap it in :class:`MarshalledBridge`.
    """

    def __init__(self, lod: int = 1) -> None:
        self.default_lod = lod

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _app():
        import FreeCAD  # noqa: F401  (lazy on purpose, §6)

        return FreeCAD

    def _document(self, doc: Optional[str]):
        App = self._app()
        if doc:
            try:
                return App.getDocument(doc)
            except Exception:
                raise BridgeError("no such document: %s" % doc, 404) from None
        document = App.ActiveDocument
        if document is None:
            documents = list(App.listDocuments().values())
            document = documents[0] if documents else None
        if document is None:
            raise BridgeError("no open document", 404)
        return document

    # -- DocumentBridge ----------------------------------------------------

    def list_documents(self) -> List[Dict[str, Any]]:
        from . import scene_export

        App = self._app()
        out: List[Dict[str, Any]] = []
        for document in App.listDocuments().values():
            try:
                out.append(scene_export.document_info(document))
            except Exception as exc:
                logger.warning("cannot describe document: %s", exc)
        out.sort(key=lambda info: info.get("name", ""))
        return out

    def default_document(self) -> Optional[str]:
        App = self._app()
        active = getattr(App, "ActiveDocument", None)
        if active is not None:
            return active.Name
        return super().default_document()

    def scene_hash(self, doc: Optional[str]) -> str:
        from . import scene_export

        return scene_export.scene_hash(self._document(doc))

    def export_scene(self, doc: Optional[str], lod: int) -> bytes:
        from . import scene_export

        return scene_export.export_document_bytes(
            self._document(doc), lod=P.clamp_lod(lod, self.default_lod)
        )

    def thumbnail(self, doc: Optional[str]) -> Optional[bytes]:
        from . import scene_export

        return scene_export.document_thumbnail(self._document(doc))

    def apply_paint(self, data: bytes, doc: Optional[str] = None) -> Dict[str, Any]:
        from .fcxr import read

        package = read(data)
        document = self._document(doc)
        try:
            from xrcore import paint_bridge  # type: ignore
        except Exception:
            paint_bridge = None
        applied = 0
        if paint_bridge is not None and hasattr(paint_bridge, "apply_remote_paint"):
            result = paint_bridge.apply_remote_paint(package.paint or {}, package.images)
            applied = int(result) if isinstance(result, int) else 1
        else:
            from . import scene_import

            applied = 1 if scene_import.apply_paint_section(package, document) else 0
        return {
            "ok": True,
            "doc": getattr(document, "Name", None),
            "applied": applied,
            "message": "paint applied" if applied else "no paint bridge available",
        }

    def apply_vector(
        self, vector: Dict[str, Any], doc: Optional[str] = None
    ) -> Dict[str, Any]:
        document = self._document(doc)
        try:
            from xrcore import paint_bridge  # type: ignore
        except Exception:
            paint_bridge = None
        applied = 0
        if paint_bridge is not None and hasattr(paint_bridge, "apply_remote_vector"):
            result = paint_bridge.apply_remote_vector(vector, document)
            applied = int(result) if isinstance(result, int) else len(
                vector.get("paths", []) or []
            )
        return {
            "ok": True,
            "doc": getattr(document, "Name", None),
            "applied": applied,
            "message": "vector applied" if applied else "no vector bridge available",
        }


    def apply_move(self, move: Dict[str, Any]) -> bool:
        App = self._app()
        document = self._document(move.get("doc"))
        obj = document.getObject(move.get("object", ""))
        if obj is None or not hasattr(obj, "Placement"):
            return False
        p = move.get("position") or [0.0, 0.0, 0.0]
        q = move.get("rotation") or [0.0, 0.0, 0.0, 1.0]
        obj.Placement = App.Placement(App.Vector(p[0] * 1000.0, p[1] * 1000.0, p[2] * 1000.0),
                                      App.Rotation(q[0], q[1], q[2], q[3]))
        if move.get("final"):
            document.recompute()
        return True


class MarshalledBridge(DocumentBridge):
    """Wraps a bridge so every call is executed by ``dispatch``.

    ``dispatch(callable) -> result`` is supplied by the GUI layer and is
    expected to run the callable on the FreeCAD main thread and return its
    result (re-raising exceptions).  This is what keeps HTTP worker threads out
    of the document.
    """

    def __init__(
        self, inner: DocumentBridge, dispatch: Callable[[Callable[[], Any]], Any]
    ) -> None:
        self.inner = inner
        self.dispatch = dispatch

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.inner, name)
        return self.dispatch(lambda: method(*args, **kwargs))

    def list_documents(self) -> List[Dict[str, Any]]:
        return self._call("list_documents")

    def default_document(self) -> Optional[str]:
        return self._call("default_document")

    def scene_hash(self, doc: Optional[str]) -> str:
        return self._call("scene_hash", doc)

    def export_scene(self, doc: Optional[str], lod: int) -> bytes:
        return self._call("export_scene", doc, lod)

    def thumbnail(self, doc: Optional[str]) -> Optional[bytes]:
        return self._call("thumbnail", doc)

    def apply_paint(self, data: bytes, doc: Optional[str] = None) -> Dict[str, Any]:
        return self._call("apply_paint", data, doc)

    def apply_vector(
        self, vector: Dict[str, Any], doc: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._call("apply_vector", vector, doc)

    def list_environments(self) -> List[Dict[str, Any]]:
        return self._call("list_environments")

    def apply_move(self, move: Dict[str, Any]) -> bool:
        return bool(self._call("apply_move", move))

    def get_environment(self, env_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_environment", env_id)

    def state(self) -> Dict[str, Any]:
        return self._call("state")


# ---------------------------------------------------------------------------
# paired devices
# ---------------------------------------------------------------------------


class DeviceRegistry:
    """Bearer tokens of paired devices, persisted to ``paired_devices.json``."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or xr_path(DEVICES_FILE)
        self._lock = threading.RLock()
        self._devices: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        data = read_json(self.path, default={})
        devices = {}
        if isinstance(data, dict):
            entries = data.get("devices", data)
            if isinstance(entries, dict):
                for token, info in entries.items():
                    if isinstance(token, str) and isinstance(info, dict):
                        devices[token] = info
        with self._lock:
            self._devices = devices

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "devices": dict(self._devices)}
        try:
            write_json(self.path, payload, private=True)
        except OSError as exc:
            logger.warning("cannot persist paired devices: %s", exc)

    # -- API ---------------------------------------------------------------

    def add(self, device: str, token: Optional[str] = None) -> str:
        token = token or P.generate_token()
        with self._lock:
            self._devices[token] = {
                "device": device,
                "paired_at": time.time(),
                "last_seen": time.time(),
            }
        self.save()
        return token

    def verify(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            info = self._devices.get(token)
            if info is None:
                return False
            info["last_seen"] = time.time()
        return True

    def device_name(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        with self._lock:
            info = self._devices.get(token)
            return info.get("device") if info else None

    def revoke(self, token: str) -> bool:
        with self._lock:
            removed = self._devices.pop(token, None) is not None
        if removed:
            self.save()
        return removed

    def clear(self) -> None:
        with self._lock:
            self._devices.clear()
        self.save()

    def devices(self) -> List[Dict[str, Any]]:
        """Paired devices *without* their tokens (never log or ship tokens)."""
        with self._lock:
            return [
                {
                    "device": info.get("device", "?"),
                    "paired_at": info.get("paired_at"),
                    "last_seen": info.get("last_seen"),
                }
                for info in self._devices.values()
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._devices)


# ---------------------------------------------------------------------------
# event log
# ---------------------------------------------------------------------------


class EventLog:
    """Sequence numbered event log with long-poll support."""

    def __init__(self, maxlen: int = EVENT_LOG_SIZE) -> None:
        self._events: Deque[P.Event] = deque(maxlen=maxlen)
        self._seq = 0
        self._condition = threading.Condition()
        self._closed = False

    @property
    def last_seq(self) -> int:
        with self._condition:
            return self._seq

    def publish(
        self, type_: str, doc: Optional[str] = None, **data: Any
    ) -> P.Event:
        with self._condition:
            self._seq += 1
            event = P.Event(
                seq=self._seq, type=type_, doc=doc, data=dict(data), time=time.time()
            )
            self._events.append(event)
            self._condition.notify_all()
        return event

    def since(self, seq: int) -> List[P.Event]:
        with self._condition:
            return [event for event in self._events if event.seq > seq]

    def wait(self, since: int, timeout: float) -> Tuple[List[P.Event], int]:
        """Block until an event newer than ``since`` shows up or ``timeout``."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                pending = [event for event in self._events if event.seq > since]
                if pending or self._closed:
                    return pending, self._seq
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], self._seq
                self._condition.wait(min(remaining, 1.0))

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


# ---------------------------------------------------------------------------
# scene cache
# ---------------------------------------------------------------------------


class SceneCache:
    """LRU cache of exported scenes keyed by ``(document, lod, hash)``."""

    def __init__(self, maxsize: int = SCENE_CACHE_SIZE) -> None:
        self.maxsize = max(1, int(maxsize))
        self._entries: "OrderedDict[Tuple[str, int, str], bytes]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Tuple[str, int, str]) -> Optional[bytes]:
        with self._lock:
            data = self._entries.get(key)
            if data is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return data

    def put(self, key: Tuple[str, int, str], data: bytes) -> None:
        with self._lock:
            self._entries[key] = data
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FreeCAD-XR/%d" % P.PROTOCOL_VERSION
    sys_version = ""

    # the owning SyncServer, injected by _HttpServer
    sync: "SyncServer" = None  # type: ignore[assignment]

    #: the consumed request body (see :meth:`_read_body`)
    _request_body: bytes = b""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = P.CONTENT_TYPE_JSON,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):  # client walked away
                pass

    def _send_json(self, status: int, payload: Any) -> None:
        if isinstance(payload, P.Message):
            payload = payload.to_dict()
        self._send(status, P.encode_json(payload).encode("utf-8"))

    def _send_error(self, status: int, message: str, error: str = "error") -> None:
        self._send_json(
            status, P.ErrorResponse(error=error, message=message, status=status)
        )

    def _authorised(self, path: str) -> bool:
        if path in P.PUBLIC_ENDPOINTS or not self.sync.auth_required:
            return True
        token = P.parse_bearer(self.headers.get("Authorization"))
        return self.sync.devices.verify(token)

    def _read_body(self) -> bytes:
        """Consume the whole request body exactly once.

        It must always be read (or the connection closed), otherwise the
        leftover bytes are parsed as the next request on a keep-alive
        connection.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            raise BridgeError("bad Content-Length", 400) from None
        if length < 0:
            self.close_connection = True
            raise BridgeError("bad Content-Length", 400)
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise BridgeError("request body too large", 413)
        if length == 0:
            return b""
        data = self.rfile.read(length)
        if len(data) != length:
            self.close_connection = True
            raise BridgeError("truncated request body", 400)
        return data

    def _body(self) -> bytes:
        """The already consumed request body."""
        return self._request_body

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or parsed.path
        query = parse_qs(parsed.query)
        self._request_body = b""
        try:
            if not self._authorised(path):
                # do not read a body from an unauthenticated peer: drop the
                # connection instead so its unread bytes cannot be mistaken
                # for a follow-up request
                self.close_connection = True
                self._send_error(401, "pair this device first", "unauthorised")
                return
            handler = _ROUTES.get((method, path))
            if handler is None:
                if self.command == "POST":
                    self._request_body = self._read_body()
                self._send_error(404, "no such endpoint: %s" % parsed.path, "not_found")
                return
            if self.command == "POST":
                self._request_body = self._read_body()
            handler(self, query)
        except BridgeError as exc:
            self._send_error(exc.status, str(exc), "bridge_error")
        except P.ProtocolError as exc:
            self._send_error(400, str(exc), "bad_request")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # never let a worker thread die silently
            logger.exception("unhandled error serving %s", self.path)
            self._send_error(500, "internal error: %s" % (exc,), "internal_error")

    # -- endpoint implementations -----------------------------------------

    @staticmethod
    def _first(query: Dict[str, List[str]], key: str) -> Optional[str]:
        values = query.get(key)
        return values[0] if values else None

    def ep_hello(self, query: Dict[str, List[str]]) -> None:
        self._send_json(200, self.sync.hello())

    def ep_pair(self, query: Dict[str, List[str]]) -> None:
        request = P.PairRequest.from_json(self._body() or b"{}")
        request.validate()
        token = self.sync.complete_pairing(request.code, request.device)
        if token is None:
            self._send_error(403, "pairing code rejected", "pairing_failed")
            return
        self._send_json(
            200,
            P.PairResponse(
                token=token, device=request.device, server_id=self.sync.server_id
            ),
        )

    def ep_documents(self, query: Dict[str, List[str]]) -> None:
        documents = [P.DocumentInfo.from_dict(d) for d in self.sync.bridge.list_documents()]
        self._send_json(
            200,
            P.DocumentsResponse(
                documents=documents, active=self.sync.bridge.default_document()
            ),
        )

    def ep_scene(self, query: Dict[str, List[str]]) -> None:
        doc = self._first(query, "doc")
        lod = P.clamp_lod(self._first(query, "lod"))
        data, digest = self.sync.scene(doc, lod)
        self._send(
            200,
            data,
            P.CONTENT_TYPE_FCXR,
            {
                "X-FCXR-Hash": digest,
                "X-FCXR-Lod": str(lod),
                "Content-Disposition": 'attachment; filename="%s.fcxr"'
                % (doc or "scene"),
            },
        )

    def ep_scene_hash(self, query: Dict[str, List[str]]) -> None:
        doc = self._first(query, "doc")
        digest = self.sync.bridge.scene_hash(doc)
        self._send_json(200, P.SceneHashResponse(doc=doc or "", hash=digest))

    def ep_events(self, query: Dict[str, List[str]]) -> None:
        try:
            since = int(self._first(query, "since") or 0)
        except ValueError:
            raise BridgeError("'since' must be an integer", 400) from None
        try:
            timeout = float(self._first(query, "timeout") or P.EVENTS_TIMEOUT)
        except ValueError:
            timeout = P.EVENTS_TIMEOUT
        timeout = max(0.0, min(P.EVENTS_MAX_TIMEOUT, timeout))
        events, last_seq = self.sync.events.wait(since, timeout)
        self._send_json(200, P.EventsResponse(events=events, last_seq=last_seq))

    def ep_environments(self, query: Dict[str, List[str]]) -> None:
        environments = [
            P.EnvironmentInfo.from_dict(e) for e in self.sync.bridge.list_environments()
        ]
        self._send_json(200, P.EnvironmentsResponse(environments=environments))

    def ep_environment(self, query: Dict[str, List[str]]) -> None:
        env_id = self._first(query, "id")
        if not env_id:
            raise BridgeError("missing 'id' parameter", 400)
        spec = self.sync.bridge.get_environment(env_id)
        if spec is None:
            raise BridgeError("no such environment: %s" % env_id, 404)
        self._send_json(200, spec)

    def ep_state(self, query: Dict[str, List[str]]) -> None:
        self._send_json(200, self.sync.bridge.state())

    def ep_paint(self, query: Dict[str, List[str]]) -> None:
        data = self._body()
        if not data:
            raise BridgeError("empty paint package", 400)
        from .fcxr import FCXR_MAGIC

        if not data.startswith(FCXR_MAGIC):
            raise BridgeError("the paint body is not an FCXR package", 400)
        doc = self._first(query, "doc")
        result = self.sync.bridge.apply_paint(data, doc) or {}
        self.sync.events.publish(P.EVENT_DOC_CHANGED, doc=result.get("doc") or doc,
                                 source="paint")
        self._send_json(200, P.ApplyResponse.from_dict(result))

    def ep_vector(self, query: Dict[str, List[str]]) -> None:
        payload = P.decode_json(self._body() or b"{}")
        if not isinstance(payload, dict):
            raise BridgeError("vector document must be a JSON object", 400)
        from .fcxr import FcxrError, validate_vector

        try:
            validate_vector(payload)
        except FcxrError as exc:
            raise BridgeError(str(exc), 400) from None
        doc = self._first(query, "doc")
        result = self.sync.bridge.apply_vector(payload, doc) or {}
        self.sync.events.publish(P.EVENT_DOC_CHANGED, doc=result.get("doc") or doc,
                                 source="vector")
        self._send_json(200, P.ApplyResponse.from_dict(result))

    def ep_thumbnail(self, query: Dict[str, List[str]]) -> None:
        doc = self._first(query, "doc")
        png = self.sync.bridge.thumbnail(doc)
        if not png:
            raise BridgeError("no thumbnail for this document", 404)
        self._send(200, png, P.CONTENT_TYPE_PNG)

    # -- multi-user session (ARCHITECTURE.md §3b) --------------------------

    def _peer_id(self) -> str:
        token = P.parse_bearer(self.headers.get("Authorization"))
        if token:
            return _presence.peer_id_for(token)
        # unauthenticated servers: one peer per client address
        return _presence.peer_id_for("%s:%s" % (self.client_address[0], self.headers.get("X-Peer", "")))

    def _device_name(self) -> str:
        token = P.parse_bearer(self.headers.get("Authorization"))
        return self.sync.devices.device_name(token) or self.headers.get("X-Device", "") or "peer"

    def _presence_response(self, peer_id: str) -> P.PresenceResponse:
        registry = self.sync.presence
        return P.PresenceResponse(
            peer_id=peer_id,
            peers=[P.PeerInfo.from_dict(p.to_dict()) for p in registry.peers(exclude=peer_id)],
            locks=[l.to_dict() for l in self.sync.locks.locks()],
            server_time=time.time(),
        )

    def ep_presence(self, query: Dict[str, List[str]]) -> None:
        peer_id = self._peer_id()
        if self.command == "POST":
            update = P.PresenceUpdate.from_json(self._body() or b"{}")
            update.validate()
            state, joined = self.sync.presence.update(peer_id, update.to_dict(), self._device_name())
            if joined:
                self.sync.events.publish(P.EVENT_PEER_JOINED, peer=peer_id, name=state.name, device=state.device,
                                         colour=list(state.colour))
        for gone in self.sync.presence.expire():
            for name in self.sync.locks.release_all(gone):
                self.sync.events.publish(P.EVENT_UNLOCK, object=name, peer=gone, reason="expired")
            self.sync.events.publish(P.EVENT_PEER_LEFT, peer=gone, reason="timeout")
        self._send_json(200, self._presence_response(peer_id))

    def ep_lock(self, query: Dict[str, List[str]]) -> None:
        peer_id = self._peer_id()
        request = P.LockRequest.from_json(self._body() or b"{}")
        request.validate()
        if request.acquire:
            granted, lock = self.sync.locks.acquire(request.object, peer_id, request.ttl)
            if granted:
                self.sync.events.publish(P.EVENT_LOCK, object=request.object, peer=peer_id, expires=lock.expires)
                self._send_json(200, P.LockResponse(True, request.object, lock.holder, lock.expires, "locked"))
            else:
                self._send_json(409, P.LockResponse(False, request.object, lock.holder, lock.expires,
                                                    "held by %s" % lock.holder))
            return
        released = self.sync.locks.release(request.object, peer_id)
        if released:
            self.sync.events.publish(P.EVENT_UNLOCK, object=request.object, peer=peer_id, reason="released")
        self._send_json(200 if released else 409, P.LockResponse(released, request.object, self.sync.locks.holder(request.object),
                                                                 0.0, "released" if released else "held by someone else"))

    def ep_move(self, query: Dict[str, List[str]]) -> None:
        peer_id = self._peer_id()
        move = P.ObjectMove.from_json(self._body() or b"{}")
        move.validate()
        holder = self.sync.locks.holder(move.object)
        if holder is not None and holder != peer_id:
            raise BridgeError("%s is held by %s" % (move.object, holder), 409)
        applied = False
        apply = getattr(self.sync.bridge, "apply_move", None)
        if apply is not None:
            applied = bool(apply(move.to_dict()))
        self.sync.events.publish(P.EVENT_OBJECT_MOVED, doc=move.doc, object=move.object, peer=peer_id,
                                 position=list(move.position), rotation=list(move.rotation), final=move.final,
                                 applied=applied)
        self._send_json(200, P.ApplyResponse(ok=True, doc=move.doc, message="moved" if applied else "broadcast"))

    def ep_voice(self, query: Dict[str, List[str]]) -> None:
        peer_id = self._peer_id()
        transcript = P.VoiceTranscript.from_json(self._body() or b"{}")
        transcript.validate()
        handled = None
        if self.sync.voice_sink is not None:
            handled = self.sync.voice_sink(transcript.to_dict(), peer_id)
        self.sync.events.publish(P.EVENT_VOICE, peer=peer_id, text=transcript.text, confidence=transcript.confidence,
                                 final=transcript.final)
        self._send_json(200, P.ApplyResponse(ok=True, message=str(handled) if handled is not None else "queued"))

    def ep_qr(self, query: Dict[str, List[str]]) -> None:
        peer_id = self._peer_id()
        detection = P.QrDetection.from_json(self._body() or b"{}")
        detection.validate()
        handled = None
        if self.sync.qr_sink is not None:
            handled = self.sync.qr_sink(detection.to_dict(), peer_id)
        self.sync.events.publish(P.EVENT_QR, peer=peer_id, text=detection.text, corners=list(detection.corners))
        self._send_json(200, P.ApplyResponse(ok=True, message=str(handled) if handled is not None else "queued"))


_ROUTES: Dict[Tuple[str, str], Callable[[_Handler, Dict[str, List[str]]], None]] = {
    ("GET", P.EP_HELLO): _Handler.ep_hello,
    ("POST", P.EP_PAIR): _Handler.ep_pair,
    ("GET", P.EP_DOCUMENTS): _Handler.ep_documents,
    ("GET", P.EP_SCENE): _Handler.ep_scene,
    ("GET", P.EP_SCENE_HASH): _Handler.ep_scene_hash,
    ("GET", P.EP_EVENTS): _Handler.ep_events,
    ("GET", P.EP_ENVIRONMENTS): _Handler.ep_environments,
    ("GET", P.EP_ENVIRONMENT): _Handler.ep_environment,
    ("GET", P.API_PREFIX + "/state"): _Handler.ep_state,
    ("POST", P.EP_PAINT): _Handler.ep_paint,
    ("POST", P.EP_VECTOR): _Handler.ep_vector,
    ("GET", P.EP_THUMBNAIL): _Handler.ep_thumbnail,
    ("GET", P.EP_PRESENCE): _Handler.ep_presence,
    ("POST", P.EP_PRESENCE): _Handler.ep_presence,
    ("POST", P.EP_LOCK): _Handler.ep_lock,
    ("POST", P.EP_MOVE): _Handler.ep_move,
    ("POST", P.EP_VOICE): _Handler.ep_voice,
    ("POST", P.EP_QR): _Handler.ep_qr,
}


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], sync: "SyncServer") -> None:
        self.sync = sync
        handler = type("_BoundHandler", (_Handler,), {"sync": sync})
        super().__init__(address, handler)

    def handle_error(self, request, client_address) -> None:  # noqa: D102
        logger.debug("connection error from %s", client_address, exc_info=True)


# ---------------------------------------------------------------------------
# discovery responder
# ---------------------------------------------------------------------------


class _DiscoveryResponder(threading.Thread):
    """Answers ``FCXR-DISCOVER?v=1`` UDP broadcasts with an offer (§3)."""

    def __init__(self, sync: "SyncServer", port: int) -> None:
        super().__init__(name="xrsync-discovery", daemon=True)
        self.sync = sync
        self.port = port
        self._socket: Optional[socket.socket] = None
        self._stop = threading.Event()

    def bind(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            sock.bind(("", self.port))
        except OSError as exc:
            logger.info("discovery beacon disabled: %s", exc)
            return False
        self._socket = sock
        self.port = sock.getsockname()[1]
        return True

    def run(self) -> None:
        sock = self._socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, address = sock.recvfrom(P.MAX_BEACON_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                P.parse_discovery_request(data)
            except P.ProtocolError:
                continue
            offer = P.encode_discovery_offer(
                name=self.sync.name, port=self.sync.port, id=self.sync.server_id
            )
            try:
                sock.sendto(offer, address)
            except OSError as exc:  # pragma: no cover - transient network errors
                logger.debug("cannot answer discovery from %s: %s", address, exc)

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------


class SyncServer:
    """The XR LAN sync server.

    ::

        server = SyncServer(bridge=DirectDocumentBridge())
        server.start()
        code, expires_in = server.begin_pairing()
        ...
        server.stop()
    """

    def __init__(
        self,
        port: Optional[int] = None,
        bridge: Optional[DocumentBridge] = None,
        host: str = "0.0.0.0",
        name: Optional[str] = None,
        devices_path: Optional[str] = None,
        auth_required: bool = True,
        discovery: bool = True,
        discovery_port: int = P.DISCOVERY_PORT,
        cache_size: int = SCENE_CACHE_SIZE,
        server_id: Optional[str] = None,
    ) -> None:
        self.host = host
        self.requested_port = P.DEFAULT_PORT if port is None else int(port)
        self.bridge = bridge if bridge is not None else DirectDocumentBridge()
        self.name = name or socket.gethostname() or "FreeCAD"
        self.auth_required = bool(auth_required)
        self.server_id = server_id or uuid.uuid4().hex
        self.devices = DeviceRegistry(devices_path)
        self.events = EventLog()
        self.cache = SceneCache(cache_size)
        #: multi-user session state (ARCHITECTURE.md §3b)
        self.presence = _presence.PresenceRegistry()
        self.locks = _presence.LockTable()
        #: callables ``(payload_dict, peer_id) -> result`` set by the voice / QR bridges
        self.voice_sink: Optional[Callable[[Dict[str, Any], str], Any]] = None
        self.qr_sink: Optional[Callable[[Dict[str, Any], str], Any]] = None

        self._discovery_enabled = bool(discovery)
        self._discovery_port = int(discovery_port)
        self._http: Optional[_HttpServer] = None
        self._thread: Optional[threading.Thread] = None
        self._responder: Optional[_DiscoveryResponder] = None
        self._lock = threading.RLock()

        self._pairing_code: Optional[str] = None
        self._pairing_expires: float = 0.0
        self._pairing_completed = False
        self._pairing_device: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "SyncServer":
        """Bind and start serving; idempotent."""
        with self._lock:
            if self._http is not None:
                return self
            try:
                self._http = _HttpServer((self.host, self.requested_port), self)
            except OSError as exc:
                raise OSError(
                    "cannot bind the XR sync server to %s:%d (%s)"
                    % (self.host, self.requested_port, exc)
                ) from None
            self._thread = threading.Thread(
                target=self._http.serve_forever,
                kwargs={"poll_interval": 0.2},
                name="xrsync-http",
                daemon=True,
            )
            self._thread.start()
            if self._discovery_enabled:
                responder = _DiscoveryResponder(self, self._discovery_port)
                if responder.bind():
                    responder.start()
                    self._responder = responder
        logger.info("XR sync server listening on %s", self.url)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Stop serving and release the sockets; idempotent."""
        with self._lock:
            http, thread, responder = self._http, self._thread, self._responder
            self._http = self._thread = self._responder = None
        if http is None:
            return
        try:
            self.events.publish(P.EVENT_SERVER_STOPPING)
        except Exception:
            pass
        self.events.close()
        if responder is not None:
            responder.stop()
        try:
            http.shutdown()
        finally:
            http.server_close()
        if thread is not None:
            thread.join(timeout)
        self.cache.clear()
        logger.info("XR sync server stopped")

    def is_running(self) -> bool:
        with self._lock:
            return self._http is not None

    def __enter__(self) -> "SyncServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- addresses ---------------------------------------------------------

    @property
    def port(self) -> int:
        """The port actually bound (resolves ``port=0``)."""
        with self._lock:
            if self._http is not None:
                return int(self._http.server_address[1])
        return self.requested_port

    @property
    def url(self) -> str:
        """Primary URL, using the best guess at this machine's LAN address."""
        return "http://%s:%d" % (self._primary_address(), self.port)

    @property
    def discovery_port(self) -> int:
        """The UDP port the discovery responder bound, or 0 when disabled."""
        with self._lock:
            return self._responder.port if self._responder is not None else 0

    def urls(self) -> List[str]:
        """One URL per reachable local address (loopback last)."""
        return ["http://%s:%d" % (address, self.port) for address in self._addresses()]

    def _primary_address(self) -> str:
        addresses = self._addresses()
        return addresses[0] if addresses else "127.0.0.1"

    def _addresses(self) -> List[str]:
        if self.host not in ("", "0.0.0.0", "::"):
            return [self.host]
        found: List[str] = []
        # The usual trick: a connect() on a UDP socket picks the outgoing
        # interface without sending a single packet.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
                found.append(probe.getsockname()[0])
            finally:
                probe.close()
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if address not in found:
                    found.append(address)
        except (OSError, socket.gaierror):
            pass
        found = [a for a in found if not a.startswith("127.")]
        found.append("127.0.0.1")
        return found

    # -- pairing -----------------------------------------------------------

    def begin_pairing(self, timeout: float = P.PAIRING_TIMEOUT) -> Tuple[str, float]:
        """Start a pairing window; returns ``(code, expires_in_seconds)``."""
        with self._lock:
            self._pairing_code = P.generate_pairing_code()
            self._pairing_expires = time.monotonic() + float(timeout)
            self._pairing_completed = False
            self._pairing_device = None
            return self._pairing_code, float(timeout)

    def cancel_pairing(self) -> None:
        """Close the pairing window without pairing anything."""
        with self._lock:
            self._pairing_code = None
            self._pairing_expires = 0.0

    def pairing_active(self) -> bool:
        with self._lock:
            return bool(
                self._pairing_code and time.monotonic() < self._pairing_expires
            )

    def pairing_completed(self) -> bool:
        """True once a device consumed the current code."""
        with self._lock:
            return self._pairing_completed

    @property
    def paired_device(self) -> Optional[str]:
        with self._lock:
            return self._pairing_device

    def complete_pairing(self, code: str, device: str) -> Optional[str]:
        """Validate a pairing code and mint a token, or return ``None``."""
        with self._lock:
            expected = self._pairing_code
            expired = time.monotonic() >= self._pairing_expires
            if expected is None or expired or not P.check_pairing_code(expected, code):
                return None
            self._pairing_code = None
            self._pairing_expires = 0.0
            self._pairing_completed = True
            self._pairing_device = device
        token = self.devices.add(device)
        self.events.publish(P.EVENT_PAIRED, device=device)
        logger.info("paired device %r", device)
        return token

    # -- content -----------------------------------------------------------

    def hello(self) -> P.HelloResponse:
        return P.HelloResponse(
            protocol=P.PROTOCOL_VERSION,
            server="FreeCAD-XR",
            name=self.name,
            id=self.server_id,
            auth_required=self.auth_required,
            paired=len(self.devices) > 0,
            pairing_active=self.pairing_active(),
            port=self.port,
            version=_freecad_version(),
            features=["fcxr", "paint", "vector", "environments", "thumbnail"],
        )

    def scene(self, doc: Optional[str], lod: int) -> Tuple[bytes, str]:
        """Export (or serve from cache) a scene, returning ``(bytes, hash)``."""
        digest = self.bridge.scene_hash(doc)
        key = (doc or "", int(lod), digest)
        data = self.cache.get(key)
        if data is None:
            data = self.bridge.export_scene(doc, lod)
            if not isinstance(data, (bytes, bytearray)):
                raise BridgeError("the bridge did not return FCXR bytes", 500)
            data = bytes(data)
            self.cache.put(key, data)
        return data, digest

    def notify_document_changed(self, doc: Optional[str] = None, **data: Any) -> P.Event:
        """Publish a ``doc_changed`` event (called by the GUI layer)."""
        return self.events.publish(P.EVENT_DOC_CHANGED, doc=doc, **data)

    def publish(self, type_: str, doc: Optional[str] = None, **data: Any) -> P.Event:
        return self.events.publish(type_, doc=doc, **data)


def _freecad_version() -> str:
    try:
        import FreeCAD  # noqa: F401

        return ".".join(str(part) for part in FreeCAD.Version()[:3])
    except Exception:
        return ""


#: historical name kept as an alias
XrSyncServer = SyncServer
