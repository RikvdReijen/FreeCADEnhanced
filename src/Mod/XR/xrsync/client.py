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
"""Desktop-side client for the XR LAN sync server (ARCHITECTURE.md §3).

Used to exercise :mod:`xrsync.server` from tests and from the desktop, and
mirrored one-to-one by the C++ Quest client.  ``http.client`` and ``socket``
only — no third party HTTP library.
"""

from __future__ import annotations

import http.client
import logging
import os
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

from . import protocol as P

__all__ = [
    "SyncError",
    "TransportError",
    "AuthError",
    "HttpError",
    "SyncClient",
    "XrSyncClient",
    "discover",
]

logger = logging.getLogger("xrsync.client")


class SyncError(Exception):
    """Base class for every client side failure."""


class TransportError(SyncError):
    """The server could not be reached (offline, refused, timed out)."""


class HttpError(SyncError):
    """The server answered with an error status."""

    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__("HTTP %d: %s" % (status, message))
        self.status = int(status)
        self.message = message
        self.payload = payload


class AuthError(HttpError):
    """401/403 — the device is not paired (any more)."""


class SyncClient:
    """A thin, synchronous client for one sync server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = P.DEFAULT_PORT,
        token: Optional[str] = None,
        timeout: float = 10.0,
        device: str = "FreeCAD desktop",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.token = token
        self.timeout = float(timeout)
        self.device = device
        #: headers added to every request (``X-Peer`` tells an unauthenticated
        #: server which peer this is; ``X-Device`` names it)
        self.extra_headers: Dict[str, str] = {"X-Device": device}
        self._connection: Optional[http.client.HTTPConnection] = None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_offer(cls, offer: P.DiscoveryOffer, **kwargs: Any) -> "SyncClient":
        """Build a client from a discovery beacon."""
        return cls(host=offer.address or offer.name, port=offer.port, **kwargs)

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def __enter__(self) -> "SyncClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- plumbing ----------------------------------------------------------

    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        connection = self._connection
        if connection is not None and getattr(connection, "timeout", None) != timeout:
            self.close()
            connection = None
        if connection is None:
            connection = http.client.HTTPConnection(
                self.host, self.port, timeout=timeout
            )
            self._connection = connection
        return connection

    def request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        timeout: Optional[float] = None,
        authenticate: bool = True,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Perform one request, retrying once on a stale keep-alive socket."""
        headers: Dict[str, str] = {"Accept": "*/*"}
        headers.update(self.extra_headers)
        if authenticate and self.token:
            headers.update(P.auth_header(self.token))
        if content_type:
            headers["Content-Type"] = content_type
        if body is not None:
            headers["Content-Length"] = str(len(body))
        effective_timeout = self.timeout if timeout is None else float(timeout)

        last_error: Optional[Exception] = None
        for attempt in (0, 1):
            try:
                connection = self._connect(effective_timeout)
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                return (
                    response.status,
                    {k.lower(): v for k, v in response.getheaders()},
                    data,
                )
            except (http.client.HTTPException, ConnectionError, OSError) as exc:
                last_error = exc
                self.close()
                if attempt == 0 and isinstance(exc, (http.client.HTTPException, ConnectionError)):
                    continue
                break
        raise TransportError(
            "cannot reach %s: %s" % (self.base_url, last_error)
        ) from last_error

    def _check(self, status: int, data: bytes) -> None:
        if 200 <= status < 300:
            return
        payload: Any = None
        message = data[:400].decode("utf-8", "replace")
        try:
            payload = P.decode_json(data)
            if isinstance(payload, dict):
                message = payload.get("message") or message
        except P.ProtocolError:
            pass
        if status in (401, 403):
            raise AuthError(status, message, payload)
        raise HttpError(status, message, payload)

    def _get_json(
        self, path: str, timeout: Optional[float] = None, authenticate: bool = True
    ) -> Any:
        status, _, data = self.request(
            "GET", path, timeout=timeout, authenticate=authenticate
        )
        self._check(status, data)
        return P.decode_json(data)

    # -- endpoints ---------------------------------------------------------

    def hello(self) -> P.HelloResponse:
        """``GET /api/v1/hello`` — works before pairing."""
        return P.HelloResponse.from_dict(self._get_json(P.EP_HELLO, authenticate=False))

    def pair(self, code: str, device: Optional[str] = None) -> P.PairResponse:
        """``POST /api/v1/pair``; stores the token on success."""
        request = P.PairRequest(code=str(code), device=device or self.device)
        request.validate()
        status, _, data = self.request(
            "POST",
            P.EP_PAIR,
            body=request.to_bytes(),
            content_type=P.CONTENT_TYPE_JSON,
            authenticate=False,
        )
        self._check(status, data)
        response = P.PairResponse.from_json(data)
        if not response.token:
            raise SyncError("the server returned an empty token")
        self.token = response.token
        return response

    def documents(self) -> List[P.DocumentInfo]:
        """``GET /api/v1/documents``."""
        return P.DocumentsResponse.from_dict(self._get_json(P.EP_DOCUMENTS)).documents

    def documents_response(self) -> P.DocumentsResponse:
        return P.DocumentsResponse.from_dict(self._get_json(P.EP_DOCUMENTS))

    def scene(
        self, doc: Optional[str] = None, lod: int = P.DEFAULT_LOD
    ) -> bytes:
        """``GET /api/v1/scene`` — the raw ``.fcxr`` body."""
        status, headers, data = self.request("GET", P.scene_path(doc, lod))
        self._check(status, data)
        content_type = headers.get("content-type", "")
        if content_type and not content_type.startswith(P.CONTENT_TYPE_FCXR):
            raise SyncError("unexpected scene content type %r" % content_type)
        return data

    def scene_to_file(
        self, path: str, doc: Optional[str] = None, lod: int = P.DEFAULT_LOD
    ) -> str:
        data = self.scene(doc, lod)
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        return path

    def scene_hash(self, doc: Optional[str] = None) -> str:
        """``GET /api/v1/scene/hash`` — cheap change poll."""
        return P.SceneHashResponse.from_dict(
            self._get_json(P.scene_hash_path(doc))
        ).hash

    def events(
        self, since: int = 0, timeout: float = P.EVENTS_TIMEOUT
    ) -> P.EventsResponse:
        """``GET /api/v1/events?since=`` — long poll."""
        timeout = max(0.0, min(P.EVENTS_MAX_TIMEOUT, float(timeout)))
        payload = self._get_json(
            P.events_path(since, timeout), timeout=timeout + 10.0
        )
        return P.EventsResponse.from_dict(payload)

    def poll_events(
        self, since: int = 0, timeout: float = P.EVENTS_TIMEOUT
    ) -> Tuple[List[P.Event], int]:
        """Convenience wrapper returning ``(events, last_seq)``."""
        response = self.events(since, timeout)
        return response.events, response.last_seq

    def environments(self) -> List[P.EnvironmentInfo]:
        """``GET /api/v1/environments``."""
        return P.EnvironmentsResponse.from_dict(
            self._get_json(P.EP_ENVIRONMENTS)
        ).environments

    def environment(self, env_id: str) -> Dict[str, Any]:
        """``GET /api/v1/environment?id=`` — the declarative spec (§2)."""
        spec = self._get_json(P.environment_path(env_id))
        if not isinstance(spec, dict):
            raise SyncError("environment spec is not an object")
        return spec

    def state(self) -> Dict[str, Any]:
        """``GET /api/v1/state`` — current environment id and user scale."""
        payload = self._get_json(P.API_PREFIX + "/state")
        return payload if isinstance(payload, dict) else {}

    def push_paint(self, data: bytes, doc: Optional[str] = None) -> P.ApplyResponse:
        """``POST /api/v1/paint`` — an ``.fcxr`` with a ``paint`` section."""
        path = P.EP_PAINT + ("?doc=%s" % doc if doc else "")
        status, _, body = self.request(
            "POST", path, body=bytes(data), content_type=P.CONTENT_TYPE_FCXR
        )
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    def push_vector(
        self, vector: Dict[str, Any], doc: Optional[str] = None
    ) -> P.ApplyResponse:
        """``POST /api/v1/vector`` — a vector document (§4)."""
        path = P.EP_VECTOR + ("?doc=%s" % doc if doc else "")
        status, _, body = self.request(
            "POST",
            path,
            body=P.encode_json(vector).encode("utf-8"),
            content_type=P.CONTENT_TYPE_JSON,
        )
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    def thumbnail(self, doc: Optional[str] = None) -> bytes:
        """``GET /api/v1/thumbnail?doc=`` — a PNG."""
        status, _, data = self.request("GET", P.thumbnail_path(doc))
        self._check(status, data)
        return data

    # -- higher level ------------------------------------------------------

    def wait_for_change(
        self,
        doc: Optional[str] = None,
        since: int = 0,
        timeout: float = P.EVENTS_TIMEOUT,
    ) -> Tuple[bool, int]:
        """Long poll until ``doc`` changes; returns ``(changed, last_seq)``."""
        events, last_seq = self.poll_events(since, timeout)
        for event in events:
            if event.type == P.EVENT_DOC_CHANGED and (doc is None or event.doc == doc):
                return True, last_seq
        return False, last_seq


#: historical name kept as an alias

    # -- multi-user session (ARCHITECTURE.md §3b) --------------------------

    def presence(self, update: Optional[Dict[str, Any]] = None) -> P.PresenceResponse:
        """``POST /api/v1/presence`` with my pose (or ``GET`` when ``update`` is None):
        everyone else's presence and the lock table."""
        if update is None:
            return P.PresenceResponse.from_dict(self._get_json(P.EP_PRESENCE))
        message = update if isinstance(update, P.PresenceUpdate) else P.PresenceUpdate.from_dict(update)
        message.validate()
        status, _, body = self.request("POST", P.EP_PRESENCE, body=message.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.PresenceResponse.from_json(body)

    def lock(self, object_name: str, acquire: bool = True, ttl: Optional[float] = None) -> P.LockResponse:
        """``POST /api/v1/lock`` — take or release the lock on an object. A
        refused lock is a normal answer (``ok`` False), not an error."""
        request = P.LockRequest(object=object_name, acquire=acquire, ttl=ttl)
        request.validate()
        status, _, body = self.request("POST", P.EP_LOCK, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        if status not in (200, 409):
            self._check(status, body)
        return P.LockResponse.from_json(body)

    def push_move(self, object_name: str, position: Any, rotation: Any, doc: Optional[str] = None,
                  final: bool = False) -> P.ApplyResponse:
        """``POST /api/v1/move`` — broadcast (and apply) an object placement."""
        move = P.ObjectMove(object=object_name, position=[float(c) for c in position],
                            rotation=[float(c) for c in rotation], doc=doc, final=final)
        move.validate()
        status, _, body = self.request("POST", P.EP_MOVE, body=move.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    def push_voice(self, text: str, confidence: float = 1.0, final: bool = True,
                   language: Optional[str] = None) -> P.ApplyResponse:
        """``POST /api/v1/voice`` — a transcript for the desktop to act on."""
        transcript = P.VoiceTranscript(text=text, confidence=float(confidence), final=bool(final), language=language)
        transcript.validate()
        status, _, body = self.request("POST", P.EP_VOICE, body=transcript.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    def push_qr(self, text: str, corners: Any, time_: float = 0.0) -> P.ApplyResponse:
        """``POST /api/v1/qr`` — a code the camera saw, corners in world metres."""
        detection = P.QrDetection(text=text, corners=[[float(c) for c in corner] for corner in corners], time=float(time_))
        detection.validate()
        status, _, body = self.request("POST", P.EP_QR, body=detection.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    # -- shared room, edits and product data (ARCHITECTURE.md §3c) ----------

    def room(self, join: bool = True, name: Optional[str] = None, capabilities: Optional[Dict[str, Any]] = None) -> P.RoomResponse:
        """``POST /api/v1/room`` (join) or ``GET`` (look)."""
        if not join:
            return P.RoomResponse.from_dict(self._get_json(P.EP_ROOM))
        request = P.RoomJoin(name=name, device=self.device, capabilities=dict(capabilities or {}))
        status, _, body = self.request("POST", P.EP_ROOM, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.RoomResponse.from_json(body)

    def room_set(self, **fields: Any) -> P.RoomResponse:
        """``POST /api/v1/room/state`` — host only (pass ``claim_host=True`` to take the room)."""
        request = P.RoomStateUpdate.from_dict(fields)
        request.validate()
        status, _, body = self.request("POST", P.EP_ROOM_STATE, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.RoomResponse.from_json(body)

    def room_anchor(self, anchor_id: str, position: Any, rotation: Any) -> P.RoomResponse:
        """``POST /api/v1/room/anchor`` — where I see the shared anchor; the reply carries my calibration."""
        request = P.RoomAnchor(anchor_id=anchor_id, pose={"position": [float(c) for c in position],
                                                          "rotation": [float(c) for c in rotation]})
        request.validate()
        status, _, body = self.request("POST", P.EP_ROOM_ANCHOR, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.RoomResponse.from_json(body)

    def room_leave(self) -> P.ApplyResponse:
        status, _, body = self.request("POST", P.EP_ROOM_LEAVE, body=b"{}", content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.ApplyResponse.from_json(body)

    def push_edit(self, operations: List[Dict[str, Any]], layer: Optional[str] = None, message: str = "",
                  doc: Optional[str] = None) -> P.EditResponse:
        """``POST /api/v1/edit`` — deviation-layer operations for everyone."""
        request = P.EditRequest(operations=list(operations), layer=layer, message=message, doc=doc)
        request.validate()
        status, _, body = self.request("POST", P.EP_EDIT, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        return P.EditResponse.from_json(body)

    def edits(self, since: int = 0) -> P.EditsResponse:
        return P.EditsResponse.from_dict(self._get_json(P.EP_EDITS + "?since=%d" % int(since)))

    def vcs(self, op: str, **kw: Any) -> Any:
        """One product-data op against the server's repository; see collab.vcs.sync."""
        import base64

        request = P.VcsRequest(op=op, id=kw.get("id") or kw.get("snapshot_id"),
                               snapshot=kw.get("snapshot") if isinstance(kw.get("snapshot"), dict) else None,
                               data=base64.b64encode(kw["data"]).decode("ascii") if isinstance(kw.get("data"), (bytes, bytearray)) else None,
                               kind=kw.get("kind"), name=kw.get("name"), expected=kw.get("expected"), meta=kw.get("meta"))
        if op == "set_ref":
            request.id = kw.get("snapshot")
        request.validate()
        status, _, body = self.request("POST", P.EP_VCS, body=request.to_bytes(), content_type=P.CONTENT_TYPE_JSON)
        self._check(status, body)
        result = P.decode_json(body).get("result")
        if isinstance(result, dict) and set(result) == {"data"}:
            return base64.b64decode(result["data"])
        return result

    def vcs_transport(self) -> Any:
        """A ``collab.vcs.sync.Transport`` speaking to this server (needs the Collab module)."""
        from collab.vcs.sync import transport_from_json

        return transport_from_json(lambda op, **kw: self.vcs(op, **kw))
XrSyncClient = SyncClient


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def discover(
    timeout: float = 2.0,
    port: int = P.DISCOVERY_PORT,
    broadcast: str = "255.255.255.255",
    retries: int = 2,
) -> List[P.DiscoveryOffer]:
    """Broadcast ``FCXR-DISCOVER?v=1`` and collect the offers that come back."""
    offers: Dict[str, P.DiscoveryOffer] = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise TransportError("cannot open a UDP socket: %s" % (exc,)) from None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        try:
            sock.bind(("", 0))
        except OSError:
            pass
        request = P.encode_discovery_request()
        deadline = time.monotonic() + max(0.1, float(timeout))
        next_send = 0.0
        sends = 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send and sends <= retries:
                for address in (broadcast, "127.0.0.1"):
                    try:
                        sock.sendto(request, (address, port))
                    except OSError:
                        continue
                sends += 1
                next_send = now + 0.5
            try:
                data, address = sock.recvfrom(P.MAX_BEACON_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                offer = P.parse_discovery_offer(data, address=address[0])
            except P.ProtocolError:
                continue
            offers[offer.id or "%s:%d" % (offer.address, offer.port)] = offer
    finally:
        sock.close()
    return list(offers.values())
