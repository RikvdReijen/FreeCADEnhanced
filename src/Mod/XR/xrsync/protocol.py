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
"""Wire protocol description for the XR LAN sync service (ARCHITECTURE.md §3).

This module is deliberately *pure*: no sockets, no HTTP, no FreeCAD.  It holds
the endpoint constants, the request/response dataclasses with their JSON
encoders/decoders, token and pairing code generation, and the UDP discovery
beacon codec.  Both :mod:`xrsync.server` and :mod:`xrsync.client` (and, by
mirroring, the C++ Quest client) are written against it, which makes the whole
protocol unit testable with nothing but the standard library.
"""

from __future__ import annotations

import dataclasses
import json
import re
import secrets
import string
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from urllib.parse import urlencode

__all__ = [
    "PROTOCOL_VERSION",
    "DEFAULT_PORT",
    "DISCOVERY_PORT",
    "API_PREFIX",
    "EP_HELLO",
    "EP_PAIR",
    "EP_DOCUMENTS",
    "EP_SCENE",
    "EP_SCENE_HASH",
    "EP_EVENTS",
    "EP_ENVIRONMENTS",
    "EP_ENVIRONMENT",
    "EP_PAINT",
    "EP_VECTOR",
    "EP_THUMBNAIL",
    "EP_PRESENCE",
    "EP_LOCK",
    "EP_MOVE",
    "EP_VOICE",
    "EP_QR",
    "EP_ROOM",
    "EP_ROOM_STATE",
    "EP_ROOM_ANCHOR",
    "EP_ROOM_LEAVE",
    "EP_EDIT",
    "EP_EDITS",
    "EP_VCS",
    "PUBLIC_ENDPOINTS",
    "CONTENT_TYPE_FCXR",
    "CONTENT_TYPE_JSON",
    "CONTENT_TYPE_PNG",
    "EVENT_DOC_CHANGED",
    "EVENT_DOC_OPENED",
    "EVENT_DOC_CLOSED",
    "EVENT_SELECTION",
    "EVENT_PAIRED",
    "EVENT_SERVER_STOPPING",
    "EVENT_PING",
    "EVENT_PEER_JOINED",
    "EVENT_PEER_LEFT",
    "EVENT_LOCK",
    "EVENT_UNLOCK",
    "EVENT_OBJECT_MOVED",
    "EVENT_VOICE",
    "EVENT_QR",
    "EVENT_ROOM",
    "EVENT_EDIT",
    "EVENT_VCS",
    "RoomJoin",
    "RoomStateUpdate",
    "RoomAnchor",
    "RoomResponse",
    "EditRequest",
    "EditResponse",
    "EditsResponse",
    "VcsRequest",
    "PresenceUpdate",
    "PeerInfo",
    "PresenceResponse",
    "LockRequest",
    "LockResponse",
    "ObjectMove",
    "VoiceTranscript",
    "QrDetection",
    "MIN_LOD",
    "MAX_LOD",
    "DEFAULT_LOD",
    "clamp_lod",
    "ProtocolError",
    "Message",
    "HelloResponse",
    "PairRequest",
    "PairResponse",
    "DocumentInfo",
    "DocumentsResponse",
    "SceneHashResponse",
    "Event",
    "EventsResponse",
    "EnvironmentInfo",
    "EnvironmentsResponse",
    "ApplyResponse",
    "ErrorResponse",
    "DiscoveryOffer",
    "encode_json",
    "decode_json",
    "generate_token",
    "generate_pairing_code",
    "is_valid_pairing_code",
    "check_pairing_code",
    "auth_header",
    "parse_bearer",
    "DISCOVERY_REQUEST",
    "encode_discovery_request",
    "parse_discovery_request",
    "encode_discovery_offer",
    "parse_discovery_offer",
    "scene_path",
    "scene_hash_path",
    "events_path",
    "environment_path",
    "thumbnail_path",
]

#: bumped whenever the wire format changes incompatibly
PROTOCOL_VERSION = 1

DEFAULT_PORT = 47810
DISCOVERY_PORT = 47811

API_PREFIX = "/api/v1"

EP_HELLO = API_PREFIX + "/hello"
EP_PAIR = API_PREFIX + "/pair"
EP_DOCUMENTS = API_PREFIX + "/documents"
EP_SCENE = API_PREFIX + "/scene"
EP_SCENE_HASH = API_PREFIX + "/scene/hash"
EP_EVENTS = API_PREFIX + "/events"
EP_ENVIRONMENTS = API_PREFIX + "/environments"
EP_ENVIRONMENT = API_PREFIX + "/environment"
EP_PAINT = API_PREFIX + "/paint"
EP_VECTOR = API_PREFIX + "/vector"
EP_THUMBNAIL = API_PREFIX + "/thumbnail"
# multi-user session (ARCHITECTURE.md §3b)
EP_PRESENCE = API_PREFIX + "/presence"
EP_LOCK = API_PREFIX + "/lock"
EP_MOVE = API_PREFIX + "/move"
EP_VOICE = API_PREFIX + "/voice"
EP_QR = API_PREFIX + "/qr"
# shared room, edits and product-data sync (ARCHITECTURE.md §3c)
EP_ROOM = API_PREFIX + "/room"
EP_ROOM_STATE = API_PREFIX + "/room/state"
EP_ROOM_ANCHOR = API_PREFIX + "/room/anchor"
EP_ROOM_LEAVE = API_PREFIX + "/room/leave"
EP_EDIT = API_PREFIX + "/edit"
EP_EDITS = API_PREFIX + "/edits"
EP_VCS = API_PREFIX + "/vcs"

#: endpoints reachable without an ``Authorization: Bearer`` header
PUBLIC_ENDPOINTS = frozenset({EP_HELLO, EP_PAIR})

CONTENT_TYPE_FCXR = "application/x-fcxr"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_PNG = "image/png"

EVENT_DOC_CHANGED = "doc_changed"
EVENT_DOC_OPENED = "doc_opened"
EVENT_DOC_CLOSED = "doc_closed"
EVENT_SELECTION = "selection"
EVENT_PAIRED = "paired"
EVENT_SERVER_STOPPING = "server_stopping"
EVENT_PING = "ping"
EVENT_PEER_JOINED = "peer_joined"
EVENT_PEER_LEFT = "peer_left"
EVENT_LOCK = "lock"
EVENT_UNLOCK = "unlock"
EVENT_OBJECT_MOVED = "object_moved"
EVENT_VOICE = "voice"
EVENT_QR = "qr"
EVENT_ROOM = "room"
EVENT_EDIT = "edit"
EVENT_VCS = "vcs"

MIN_LOD = 0
MAX_LOD = 3
DEFAULT_LOD = 1

#: how long a pairing code stays valid, in seconds
PAIRING_TIMEOUT = 120.0
#: default long poll timeout for ``/api/v1/events``
EVENTS_TIMEOUT = 25.0
#: hard cap so a client cannot pin a worker thread forever
EVENTS_MAX_TIMEOUT = 120.0

PAIRING_CODE_LENGTH = 6

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")
_PAIRING_CODE_RE = re.compile(r"^[0-9]{%d}$" % PAIRING_CODE_LENGTH)


class ProtocolError(Exception):
    """Raised when a message cannot be encoded or decoded."""


def clamp_lod(lod: Any, default: int = DEFAULT_LOD) -> int:
    """Coerce ``lod`` into the valid 0..3 range."""
    try:
        value = int(lod)
    except (TypeError, ValueError):
        return default
    return max(MIN_LOD, min(MAX_LOD, value))


# ---------------------------------------------------------------------------
# message base
# ---------------------------------------------------------------------------

T = TypeVar("T", bound="Message")


@dataclass
class Message:
    """Base class for the protocol dataclasses.

    Decoding is deliberately lenient about unknown keys so a newer peer can add
    fields without breaking an older one, and strict about types it does use.
    """

    # Unannotated class attributes (deliberately *not* dataclass fields):
    # ``{field_name: Message subclass}`` for nested objects and nested lists.
    _NESTED_TYPES = {}
    _NESTED_LIST_TYPES = {}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in dataclasses.fields(self):
            if f.name.startswith("_"):
                continue
            value = getattr(self, f.name)
            if isinstance(value, Message):
                value = value.to_dict()
            elif isinstance(value, list):
                value = [v.to_dict() if isinstance(v, Message) else v for v in value]
            out[f.name] = value
        return out

    def to_json(self) -> str:
        return encode_json(self.to_dict())

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_dict(cls: Type[T], data: Any) -> T:
        if not isinstance(data, dict):
            raise ProtocolError("%s: expected a JSON object, got %s"
                                % (cls.__name__, type(data).__name__))
        nested = cls._NESTED_TYPES
        nested_list = cls._NESTED_LIST_TYPES
        kwargs: Dict[str, Any] = {}
        known = {f.name for f in dataclasses.fields(cls) if not f.name.startswith("_")}
        for key, value in data.items():
            if key not in known:
                continue  # forward compatible: ignore unknown fields
            if key in nested and value is not None:
                value = nested[key].from_dict(value)
            elif key in nested_list:
                if value is None:
                    value = []
                if not isinstance(value, list):
                    raise ProtocolError("%s.%s: expected a list" % (cls.__name__, key))
                value = [nested_list[key].from_dict(v) for v in value]
            kwargs[key] = value
        try:
            return cls(**kwargs)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ProtocolError("%s: %s" % (cls.__name__, exc)) from None

    @classmethod
    def from_json(cls: Type[T], data: Union[str, bytes, bytearray]) -> T:
        return cls.from_dict(decode_json(data))



def encode_json(obj: Any) -> str:
    """Compact, deterministic JSON encoding used everywhere on the wire."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("cannot encode message: %s" % (exc,)) from None


def decode_json(data: Union[str, bytes, bytearray]) -> Any:
    """Decode JSON, raising :class:`ProtocolError` on anything malformed."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("payload is not UTF-8: %s" % (exc,)) from None
    try:
        return json.loads(data)
    except ValueError as exc:
        raise ProtocolError("payload is not valid JSON: %s" % (exc,)) from None


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


@dataclass
class HelloResponse(Message):
    """``GET /api/v1/hello`` — server info, no auth required."""

    protocol: int = PROTOCOL_VERSION
    server: str = "FreeCAD-XR"
    name: str = ""
    id: str = ""
    auth_required: bool = True
    paired: bool = False
    pairing_active: bool = False
    port: int = DEFAULT_PORT
    version: str = ""
    features: List[str] = field(default_factory=list)


@dataclass
class PairRequest(Message):
    """``POST /api/v1/pair`` body."""

    code: str = ""
    device: str = "unknown"

    def validate(self) -> None:
        if not is_valid_pairing_code(self.code):
            raise ProtocolError("pairing code must be %d digits" % PAIRING_CODE_LENGTH)
        if not self.device or len(self.device) > 128:
            raise ProtocolError("device name must be 1..128 characters")


@dataclass
class PairResponse(Message):
    """``POST /api/v1/pair`` reply."""

    token: str = ""
    device: str = ""
    server_id: str = ""
    protocol: int = PROTOCOL_VERSION


@dataclass
class DocumentInfo(Message):
    """One entry of ``GET /api/v1/documents``."""

    name: str = ""
    label: str = ""
    hash: str = ""
    path: Optional[str] = None
    touched: bool = False
    object_count: int = 0


@dataclass
class DocumentsResponse(Message):
    """``GET /api/v1/documents`` reply."""

    documents: List[DocumentInfo] = field(default_factory=list)
    active: Optional[str] = None


DocumentsResponse._NESTED_LIST_TYPES = {"documents": DocumentInfo}


@dataclass
class SceneHashResponse(Message):
    """``GET /api/v1/scene/hash`` reply."""

    doc: str = ""
    hash: str = ""


@dataclass
class Event(Message):
    """One entry of the server event log."""

    seq: int = 0
    type: str = EVENT_PING
    doc: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    time: float = 0.0


@dataclass
class EventsResponse(Message):
    """``GET /api/v1/events?since=`` reply."""

    events: List[Event] = field(default_factory=list)
    last_seq: int = 0


EventsResponse._NESTED_LIST_TYPES = {"events": Event}


@dataclass
class EnvironmentInfo(Message):
    """One entry of ``GET /api/v1/environments``."""

    id: str = ""
    name: str = ""
    description: str = ""
    user_scale: float = 1.0


@dataclass
class EnvironmentsResponse(Message):
    """``GET /api/v1/environments`` reply."""

    environments: List[EnvironmentInfo] = field(default_factory=list)


EnvironmentsResponse._NESTED_LIST_TYPES = {"environments": EnvironmentInfo}


@dataclass
class ApplyResponse(Message):
    """Reply of ``POST /api/v1/paint`` and ``POST /api/v1/vector``."""

    ok: bool = True
    doc: Optional[str] = None
    applied: int = 0
    message: str = ""


@dataclass
class ErrorResponse(Message):
    """Uniform error body; ``status`` mirrors the HTTP status code."""

    error: str = "error"
    message: str = ""
    status: int = 400


@dataclass
class DiscoveryOffer(Message):
    """A parsed ``FCXR-OFFER`` beacon."""

    name: str = ""
    port: int = DEFAULT_PORT
    id: str = ""
    version: int = PROTOCOL_VERSION
    address: Optional[str] = None

    @property
    def base_url(self) -> str:
        host = self.address or self.name
        return "http://%s:%d" % (host, self.port)


# ---------------------------------------------------------------------------
# tokens and pairing codes
# ---------------------------------------------------------------------------


@dataclass
class PresenceUpdate(Message):
    """``POST /api/v1/presence`` body: where the sender is and what it holds."""

    name: Optional[str] = None
    head: Optional[Dict[str, Any]] = None
    hands: List[Dict[str, Any]] = field(default_factory=list)
    selection: List[str] = field(default_factory=list)
    environment: Optional[str] = None
    scale: Optional[float] = None
    doc: Optional[str] = None
    tool: Optional[str] = None

    def validate(self) -> None:
        for pose in ([self.head] if self.head else []) + list(self.hands):
            if not isinstance(pose, dict):
                raise ProtocolError("poses must be objects")
            for key, n in (("position", 3), ("rotation", 4)):
                value = pose.get(key)
                if value is not None and (not isinstance(value, list) or len(value) != n):
                    raise ProtocolError("%s must have %d numbers" % (key, n))
        if self.scale is not None and float(self.scale) <= 0:
            raise ProtocolError("scale must be positive")


@dataclass
class PeerInfo(Message):
    peer_id: str = ""
    device: str = ""
    name: str = ""
    colour: List[float] = field(default_factory=list)
    head: Optional[Dict[str, Any]] = None
    hands: List[Dict[str, Any]] = field(default_factory=list)
    selection: List[str] = field(default_factory=list)
    environment: Optional[str] = None
    scale: float = 1.0
    doc: Optional[str] = None
    tool: Optional[str] = None
    last_seen: float = 0.0
    seq: int = 0


@dataclass
class PresenceResponse(Message):
    """``GET/POST /api/v1/presence`` reply: everyone *else*, plus your own id."""

    peer_id: str = ""
    peers: List[PeerInfo] = field(default_factory=list)
    locks: List[Dict[str, Any]] = field(default_factory=list)
    server_time: float = 0.0


PresenceResponse._NESTED_LIST_TYPES = {"peers": PeerInfo}


@dataclass
class LockRequest(Message):
    """``POST /api/v1/lock``: take (``acquire``) or drop a lock on an object."""

    object: str = ""
    acquire: bool = True
    ttl: Optional[float] = None

    def validate(self) -> None:
        if not self.object or not isinstance(self.object, str):
            raise ProtocolError("lock request needs an object name")


@dataclass
class LockResponse(Message):
    ok: bool = False
    object: str = ""
    holder: Optional[str] = None
    expires: float = 0.0
    message: str = ""


@dataclass
class ObjectMove(Message):
    """``POST /api/v1/move``: an object placement, world metres / quaternion."""

    object: str = ""
    position: List[float] = field(default_factory=list)
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    doc: Optional[str] = None
    final: bool = False

    def validate(self) -> None:
        if not self.object:
            raise ProtocolError("move needs an object name")
        if len(self.position) != 3 or len(self.rotation) != 4:
            raise ProtocolError("move needs a 3-vector position and a quaternion")


@dataclass
class VoiceTranscript(Message):
    """``POST /api/v1/voice``: a transcript recognised on the headset."""

    text: str = ""
    confidence: float = 1.0
    final: bool = True
    language: Optional[str] = None

    def validate(self) -> None:
        if not isinstance(self.text, str):
            raise ProtocolError("transcript text must be a string")


@dataclass
class QrDetection(Message):
    """``POST /api/v1/qr``: a code the headset camera saw, with its corners in world metres."""

    text: str = ""
    corners: List[List[float]] = field(default_factory=list)
    time: float = 0.0

    def validate(self) -> None:
        if not self.text:
            raise ProtocolError("qr detection needs the decoded text")
        if len(self.corners) != 4 or any(len(c) != 3 for c in self.corners):
            raise ProtocolError("qr detection needs four xyz corners")


@dataclass
class RoomJoin(Message):
    """``POST /api/v1/room``: join (or refresh membership of) the shared room."""

    name: Optional[str] = None
    device: Optional[str] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoomStateUpdate(Message):
    """``POST /api/v1/room/state`` (host only): what everyone should be in."""

    doc: Optional[str] = None
    revision: Optional[str] = None
    environment: Optional[str] = None
    scale: Optional[float] = None
    origin: Optional[Dict[str, Any]] = None
    anchor: Optional[Dict[str, Any]] = None
    claim_host: bool = False

    def validate(self) -> None:
        if self.scale is not None and float(self.scale) <= 0:
            raise ProtocolError("scale must be positive")
        for name, pose in (("origin", self.origin), ("anchor", (self.anchor or {}).get("pose"))):
            if pose is None:
                continue
            if not isinstance(pose, dict):
                raise ProtocolError("%s must be an object" % name)
            for key, n in (("position", 3), ("rotation", 4)):
                value = pose.get(key)
                if value is not None and (not isinstance(value, list) or len(value) != n):
                    raise ProtocolError("%s.%s must have %d numbers" % (name, key, n))


@dataclass
class RoomAnchor(Message):
    """``POST /api/v1/room/anchor``: where this device sees the shared anchor, in its own frame."""

    anchor_id: str = ""
    pose: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.anchor_id:
            raise ProtocolError("anchor_id is required")
        for key, n in (("position", 3), ("rotation", 4)):
            value = self.pose.get(key)
            if value is None or not isinstance(value, list) or len(value) != n:
                raise ProtocolError("pose.%s must have %d numbers" % (key, n))


@dataclass
class RoomResponse(Message):
    """The room as the server sees it, plus this peer's id and calibration (when known)."""

    peer_id: str = ""
    room: Dict[str, Any] = field(default_factory=dict)
    calibration: Optional[Dict[str, Any]] = None
    is_host: bool = False


@dataclass
class EditRequest(Message):
    """``POST /api/v1/edit``: deviation-layer operations (collab.schema) to apply and share."""

    operations: List[Dict[str, Any]] = field(default_factory=list)
    layer: Optional[str] = None
    message: str = ""
    doc: Optional[str] = None

    def validate(self) -> None:
        if not isinstance(self.operations, list) or not self.operations:
            raise ProtocolError("an edit needs at least one operation")
        for op in self.operations:
            if not isinstance(op, dict) or "op" not in op:
                raise ProtocolError("each operation is an object with an 'op'")


@dataclass
class EditResponse(Message):
    ok: bool = False
    seq: int = 0
    applied: Optional[bool] = None
    message: str = ""
    revision: Optional[str] = None


@dataclass
class EditsResponse(Message):
    edits: List[Dict[str, Any]] = field(default_factory=list)
    edit_seq: int = 0


@dataclass
class VcsRequest(Message):
    """``POST /api/v1/vcs``: one product-data sync operation (collab.vcs.sync.serve)."""

    op: str = ""
    id: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    data: Optional[str] = None   # base64 for blobs
    kind: Optional[str] = None
    name: Optional[str] = None
    expected: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def validate(self) -> None:
        if self.op not in ("refs", "has_snapshot", "get_snapshot", "put_snapshot", "has_blob", "get_blob", "put_blob", "set_ref"):
            raise ProtocolError("unknown vcs op %r" % self.op)


def generate_token(nbytes: int = 32) -> str:
    """Return a fresh URL-safe bearer token."""
    if nbytes < 16:
        raise ProtocolError("tokens must carry at least 16 bytes of entropy")
    return secrets.token_urlsafe(nbytes)


def is_valid_token(token: Any) -> bool:
    """Cheap shape check for a bearer token (never a substitute for lookup)."""
    return isinstance(token, str) and bool(_TOKEN_RE.match(token))


def generate_pairing_code() -> str:
    """Return a fresh cryptographically random 6 digit pairing code."""
    return "".join(secrets.choice(string.digits) for _ in range(PAIRING_CODE_LENGTH))


def is_valid_pairing_code(code: Any) -> bool:
    """True when ``code`` has the right shape (6 ASCII digits)."""
    return isinstance(code, str) and bool(_PAIRING_CODE_RE.match(code))


def check_pairing_code(expected: Optional[str], given: Any) -> bool:
    """Constant time pairing code comparison."""
    if not is_valid_pairing_code(expected) or not is_valid_pairing_code(given):
        return False
    return secrets.compare_digest(str(expected), str(given))


def auth_header(token: str) -> Dict[str, str]:
    """The ``Authorization`` header for ``token``."""
    return {"Authorization": "Bearer %s" % token}


def parse_bearer(header: Optional[str]) -> Optional[str]:
    """Extract the token from an ``Authorization`` header, or ``None``."""
    if not header or not isinstance(header, str):
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


# ---------------------------------------------------------------------------
# discovery beacon (UDP, ARCHITECTURE.md §3)
# ---------------------------------------------------------------------------

DISCOVERY_REQUEST = "FCXR-DISCOVER?v=%d" % PROTOCOL_VERSION

_DISCOVER_RE = re.compile(r"^FCXR-DISCOVER\?v=(\d+)\s*$")
_OFFER_RE = re.compile(r"^FCXR-OFFER\s+(?P<rest>.+)$")
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]*)")
#: beacons are tiny; refuse anything that is obviously not one
MAX_BEACON_SIZE = 512


def _beacon_text(data: Union[str, bytes, bytearray]) -> str:
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_BEACON_SIZE:
            raise ProtocolError("beacon too large (%d bytes)" % len(data))
        try:
            data = bytes(data).decode("ascii")
        except UnicodeDecodeError:
            raise ProtocolError("beacon is not ASCII") from None
    elif len(data) > MAX_BEACON_SIZE:
        raise ProtocolError("beacon too large")
    return data.strip()


def _sanitise_beacon_value(value: str) -> str:
    """Beacon values are whitespace separated, so collapse whitespace out."""
    return re.sub(r"\s+", "-", str(value).strip()) or "-"


def encode_discovery_request(version: int = PROTOCOL_VERSION) -> bytes:
    """``client -> broadcast`` beacon."""
    return ("FCXR-DISCOVER?v=%d" % int(version)).encode("ascii")


def parse_discovery_request(data: Union[str, bytes, bytearray]) -> int:
    """Parse a discovery request, returning its protocol version."""
    match = _DISCOVER_RE.match(_beacon_text(data))
    if not match:
        raise ProtocolError("not an FCXR discovery request")
    return int(match.group(1))


def encode_discovery_offer(
    name: str,
    port: int = DEFAULT_PORT,
    id: str = "",
    version: int = PROTOCOL_VERSION,
) -> bytes:
    """``server -> unicast`` beacon reply."""
    return (
        "FCXR-OFFER v=%d name=%s port=%d id=%s"
        % (int(version), _sanitise_beacon_value(name), int(port),
           _sanitise_beacon_value(id))
    ).encode("ascii")


def parse_discovery_offer(
    data: Union[str, bytes, bytearray], address: Optional[str] = None
) -> DiscoveryOffer:
    """Parse an ``FCXR-OFFER`` beacon into a :class:`DiscoveryOffer`."""
    match = _OFFER_RE.match(_beacon_text(data))
    if not match:
        raise ProtocolError("not an FCXR offer")
    fields = dict(_KV_RE.findall(match.group("rest")))
    if "port" not in fields:
        raise ProtocolError("offer without a port")
    try:
        port = int(fields["port"])
    except ValueError:
        raise ProtocolError("offer has a non numeric port %r" % fields["port"]) from None
    if not 1 <= port <= 65535:
        raise ProtocolError("offer port %d out of range" % port)
    try:
        version = int(fields.get("v", PROTOCOL_VERSION))
    except ValueError:
        raise ProtocolError("offer has a non numeric version") from None
    return DiscoveryOffer(
        name=fields.get("name", ""),
        port=port,
        id=fields.get("id", ""),
        version=version,
        address=address,
    )


# ---------------------------------------------------------------------------
# path builders (shared by client and tests)
# ---------------------------------------------------------------------------


def _with_query(path: str, params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return path + ("?" + urlencode(clean) if clean else "")


def scene_path(doc: Optional[str] = None, lod: int = DEFAULT_LOD) -> str:
    return _with_query(EP_SCENE, {"doc": doc, "lod": clamp_lod(lod)})


def scene_hash_path(doc: Optional[str] = None) -> str:
    return _with_query(EP_SCENE_HASH, {"doc": doc})


def events_path(since: int = 0, timeout: Optional[float] = None) -> str:
    return _with_query(EP_EVENTS, {"since": int(since), "timeout": timeout})


def environment_path(env_id: str) -> str:
    return _with_query(EP_ENVIRONMENT, {"id": env_id})


def thumbnail_path(doc: Optional[str] = None) -> str:
    return _with_query(EP_THUMBNAIL, {"doc": doc})
