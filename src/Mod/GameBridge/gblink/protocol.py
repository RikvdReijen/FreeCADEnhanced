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
"""The wire format for the live link.

The link carries a CAD model while somebody is editing it, so the traffic is
lopsided: a handful of tiny messages saying "this part moved" and the occasional
enormous one carrying a re-tessellated solid.  A frame therefore has two parts -
a JSON header describing the message and an optional binary blob carrying
geometry - and each may be compressed independently.  Packing vertex data as
JSON numbers would roughly triple it and cost more to parse than it saves.

Frame layout, little endian throughout::

    magic     4 bytes   'GBL1'
    flags     1 byte    bit 0: JSON is deflated, bit 1: blob is deflated
    reserved  3 bytes   zero
    json      4 bytes   length of the JSON header
    blob      4 bytes   length of the binary payload, 0 when there is none

Nothing here knows what a FreeCAD document is, and nothing imports the engine
side.  That is what lets the same module run in FreeCAD, in Blender's Python and
in the test suite.
"""

import json
import struct
import zlib

__all__ = [
    "MAGIC",
    "PROTOCOL_VERSION",
    "HEADER_SIZE",
    "MAX_FRAME",
    "Message",
    "ProtocolError",
    "encode",
    "decode",
    "FrameReader",
    "hello",
    "welcome",
    "scene_message",
    "update_message",
    "mesh_payload",
    "decode_mesh_payload",
    "ping",
    "pong",
    "error",
    "goodbye",
    "selection",
]

MAGIC = b"GBL1"
PROTOCOL_VERSION = 1
HEADER_SIZE = 16

#: Refuse a frame claiming to be larger than this.  A 256 MB tessellation is not
#: a scene anyone is editing live, it is a corrupt length field or a port that
#: something else is talking on.
MAX_FRAME = 256 * 1024 * 1024

FLAG_JSON_DEFLATED = 0x01
FLAG_BLOB_DEFLATED = 0x02

#: Below this, compressing costs more than it saves.
COMPRESS_THRESHOLD = 4096


class ProtocolError(Exception):
    """Raised when a frame cannot be trusted; the caller should drop the link."""


class Message:
    """One decoded frame: a type, a JSON body and an optional binary blob."""

    __slots__ = ("type", "body", "blob")

    def __init__(self, type_, body=None, blob=b""):
        self.type = str(type_)
        self.body = dict(body or {})
        self.blob = blob or b""

    def get(self, key, default=None):
        return self.body.get(key, default)

    def __getitem__(self, key):
        return self.body[key]

    def __contains__(self, key):
        return key in self.body

    def __repr__(self):
        return "Message(%r, %d field(s), %d blob byte(s))" % (
            self.type,
            len(self.body),
            len(self.blob),
        )


def encode(message, compress=True):
    """Serialise a :class:`Message` into bytes."""
    body = dict(message.body)
    body["type"] = message.type
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    blob = message.blob
    flags = 0
    if compress and len(payload) > COMPRESS_THRESHOLD:
        payload = zlib.compress(payload, 6)
        flags |= FLAG_JSON_DEFLATED
    if compress and len(blob) > COMPRESS_THRESHOLD:
        blob = zlib.compress(blob, 6)
        flags |= FLAG_BLOB_DEFLATED
    if len(payload) + len(blob) > MAX_FRAME:
        raise ProtocolError("message is too large to send (%d bytes)" % (len(payload) + len(blob)))
    header = MAGIC + struct.pack("<BxxxII", flags, len(payload), len(blob))
    return header + payload + blob


def decode(frame):
    """Parse one complete frame back into a :class:`Message`."""
    if len(frame) < HEADER_SIZE:
        raise ProtocolError("frame is shorter than its header")
    if frame[:4] != MAGIC:
        raise ProtocolError("this is not a GameBridge frame (bad magic)")
    flags, json_length, blob_length = struct.unpack("<BxxxII", frame[4:HEADER_SIZE])
    end_json = HEADER_SIZE + json_length
    end_blob = end_json + blob_length
    if len(frame) < end_blob:
        raise ProtocolError("frame is truncated")
    payload = frame[HEADER_SIZE:end_json]
    blob = frame[end_json:end_blob]
    if flags & FLAG_JSON_DEFLATED:
        payload = zlib.decompress(payload)
    if flags & FLAG_BLOB_DEFLATED:
        blob = zlib.decompress(blob)
    try:
        body = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as problem:
        raise ProtocolError("frame does not hold valid JSON: %s" % problem)
    if not isinstance(body, dict) or "type" not in body:
        raise ProtocolError("frame has no message type")
    message_type = body.pop("type")
    return Message(message_type, body, blob)


class FrameReader:
    """Reassembles frames from a byte stream.

    TCP has no message boundaries, so a reader that assumes one ``recv`` is one
    message works perfectly until the first mesh large enough to be split - and
    then fails in a way that looks like corruption rather than framing.
    """

    def __init__(self, max_frame=MAX_FRAME):
        self._buffer = bytearray()
        self.max_frame = max_frame

    def feed(self, data):
        """Add received bytes; returns every complete message they finished."""
        if data:
            self._buffer.extend(data)
        messages = []
        while True:
            if len(self._buffer) < HEADER_SIZE:
                break
            if self._buffer[:4] != MAGIC:
                raise ProtocolError("stream is out of sync (bad magic)")
            flags, json_length, blob_length = struct.unpack(
                "<BxxxII", bytes(self._buffer[4:HEADER_SIZE])
            )
            total = HEADER_SIZE + json_length + blob_length
            if json_length + blob_length > self.max_frame:
                raise ProtocolError("frame claims %d bytes, which is too large" % total)
            if len(self._buffer) < total:
                break
            frame = bytes(self._buffer[:total])
            del self._buffer[:total]
            messages.append(decode(frame))
        return messages

    @property
    def pending(self):
        """Bytes held for a frame that has not finished arriving."""
        return len(self._buffer)

    def reset(self):
        self._buffer = bytearray()


# ---------------------------------------------------------------------------
# Message constructors.  Having them in one place is what keeps the field names
# from drifting between the server, the clients and the tests.
# ---------------------------------------------------------------------------


def hello(client_name, engine, token=None, capabilities=()):
    body = {
        "protocol": PROTOCOL_VERSION,
        "client": client_name,
        "engine": engine,
        "capabilities": list(capabilities),
    }
    if token:
        body["token"] = token
    return Message("hello", body)


def welcome(server_name, document, convention, session, bridge_version):
    return Message(
        "welcome",
        {
            "protocol": PROTOCOL_VERSION,
            "server": server_name,
            "document": document,
            "target": convention.to_dict(),
            "session": session,
            "bridgeVersion": bridge_version,
        },
    )


def scene_message(manifest, blob=b""):
    """A full scene: the manifest, with geometry in the blob."""
    return Message("scene", {"manifest": manifest}, blob)


def update_message(delta, blob=b""):
    """Only what changed since the last message."""
    return Message("update", {"delta": delta}, blob)


def ping(sequence=0):
    return Message("ping", {"sequence": sequence})


def pong(sequence=0):
    return Message("pong", {"sequence": sequence})


def error(reason, fatal=False):
    return Message("error", {"reason": str(reason), "fatal": bool(fatal)})


def goodbye(reason="closing"):
    return Message("bye", {"reason": reason})


def selection(names, source="engine"):
    """Selection sync, so clicking a part in the engine highlights it in FreeCAD."""
    return Message("selection", {"objects": list(names), "source": source})


# ---------------------------------------------------------------------------
# Geometry packing.
# ---------------------------------------------------------------------------


def mesh_payload(meshes, convention):
    """Pack meshes into one binary blob, converted into the target's space.

    ``meshes`` is an iterable of ``(identifier, mesh)``; the identifier is
    whatever the caller uses to refer to the geometry, which for a live session
    is the mesh's checksum.  Returns ``(descriptors, blob)``.  Each descriptor says where in the blob its
    arrays are, so the receiving side can slice them out without parsing
    anything; the blob itself is plain little-endian float32 and uint32, which
    is what every engine wants to hand to its own buffer upload.
    """
    descriptors = []
    blob = bytearray()
    for identifier, mesh in meshes:
        positions = []
        for i in range(0, len(mesh.positions), 3):
            positions.extend(convention.convert_point(mesh.positions[i:i + 3]))
        normals = []
        for i in range(0, len(mesh.normals), 3):
            normals.extend(convention.convert_direction(mesh.normals[i:i + 3]))
        indices = list(mesh.indices)
        if convention.flips_winding:
            for i in range(0, len(indices), 3):
                indices[i + 1], indices[i + 2] = indices[i + 2], indices[i + 1]

        descriptor = {
            "id": identifier,
            "name": mesh.name,
            "material": mesh.material,
            "checksum": mesh.checksum(),
            "vertexCount": len(positions) // 3,
            "triangleCount": len(indices) // 3,
            "positions": [len(blob), len(positions) * 4],
        }
        blob.extend(struct.pack("<%df" % len(positions), *positions))
        if normals:
            descriptor["normals"] = [len(blob), len(normals) * 4]
            blob.extend(struct.pack("<%df" % len(normals), *normals))
        if mesh.uvs:
            descriptor["uvs"] = [len(blob), len(mesh.uvs) * 4]
            blob.extend(struct.pack("<%df" % len(mesh.uvs), *mesh.uvs))
        descriptor["indices"] = [len(blob), len(indices) * 4]
        blob.extend(struct.pack("<%dI" % len(indices), *indices))
        descriptors.append(descriptor)
    return descriptors, bytes(blob)


def decode_mesh_payload(descriptor, blob):
    """Slice one mesh back out of a blob, as plain Python lists."""
    result = {"name": descriptor.get("name"), "material": descriptor.get("material")}
    for key, code in (("positions", "f"), ("normals", "f"), ("uvs", "f"), ("indices", "I")):
        span = descriptor.get(key)
        if not span:
            continue
        offset, length = span
        if offset < 0 or offset + length > len(blob):
            raise ProtocolError(
                "mesh %r says its %s run past the end of the payload"
                % (descriptor.get("name"), key)
            )
        count = length // 4
        result[key] = list(struct.unpack_from("<%d%s" % (count, code), blob, offset))
    return result
