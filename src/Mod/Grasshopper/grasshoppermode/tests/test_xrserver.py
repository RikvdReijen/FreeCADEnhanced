# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercises the stdlib server with a hand-rolled WebSocket client."""

import base64
import json
import os
import socket
import struct
import tempfile
import unittest

from grasshoppermode import protocol
from grasshoppermode.geometry import StubBackend
from grasshoppermode.graph import Graph
from grasshoppermode.nodes import default_registry, example_graph
from grasshoppermode.session import Session
from grasshoppermode.xrserver import XRServer


class WSClient:
    def __init__(self, port, path="/ws"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                "GET %s HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, key)
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1024)
        assert b"101" in head.split(b"\r\n")[0], head
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def send(self, msg, opcode=0x1):
        payload = protocol.encode(msg).encode() if isinstance(msg, dict) else msg
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_frame(self):
        b1, b2 = self._read(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        return opcode, self._read(length)

    def recv(self):
        while True:
            opcode, payload = self.recv_frame()
            if opcode == 0x1:
                return json.loads(payload.decode())
            if opcode == 0x8:
                raise ConnectionError("closed by server")

    def recv_until(self, t):
        while True:
            msg = self.recv()
            if msg["t"] == t:
                return msg

    def close(self):
        self.send(struct.pack("!H", 1000), opcode=0x8)
        self.sock.close()


def http_get(port, path):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(("GET %s HTTP/1.1\r\nHost: localhost\r\n\r\n" % path).encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    head, body = data.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ")[1])
    return status, head.decode("latin-1"), body


class TestXRServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.static = tempfile.mkdtemp()
        with open(os.path.join(cls.static, "index.html"), "w") as fh:
            fh.write("<html><body>xr client</body></html>")
        with open(os.path.join(cls.static, "app.js"), "w") as fh:
            fh.write("console.log('hi')")
        cls.graph = Graph(default_registry(), StubBackend(), canvas_id="srv1")
        example_graph(cls.graph)
        cls.session = Session(cls.graph)
        cls.server = XRServer(cls.session, cls.static, host="127.0.0.1", port=0).start()
        cls.port = cls.server.bound_port

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_static_and_api(self):
        status, head, body = http_get(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn(b"xr client", body)
        status, head, body = http_get(self.port, "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", head)
        status, head, body = http_get(self.port, "/api/state")
        self.assertEqual(json.loads(body)["canvas"], "srv1")
        status, head, body = http_get(self.port, "/api/info")
        self.assertIn("urls", json.loads(body))
        self.assertEqual(http_get(self.port, "/missing.js")[0], 404)
        self.assertEqual(http_get(self.port, "/../etc/passwd")[0], 403)

    def test_marker_pages(self):
        status, head, body = http_get(self.port, "/marker.svg?i=1&mm=100")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", head)
        self.assertIn(b'width="100.000mm"', body)
        self.assertIn(b"marker 1", body)
        status, head, body = http_get(self.port, "/marker.json?i=0")
        data = json.loads(body)
        self.assertEqual(len(data["modules"]), len(data["modules"][0]))
        self.assertEqual(data["marker"]["index"], 0)
        status, head, body = http_get(self.port, "/marker.html")
        self.assertEqual(status, 200)
        self.assertIn(b"marker.svg?i=0", body)

    def test_websocket_roundtrip(self):
        ws = WSClient(self.port)
        hello = ws.recv_until("hello")
        self.assertEqual(hello["canvas"], "srv1")
        ws.recv_until("preview")
        node = self.graph.nodes[list(self.graph.nodes)[0]]
        ws.send({"t": "tap", "x": node.x + 3, "y": node.y + 3, "rid": 5})
        seen = {}
        while "ack" not in seen or "selection" not in seen:
            msg = ws.recv()
            seen[msg["t"]] = msg
        self.assertTrue(seen["ack"]["ok"])
        self.assertEqual(seen["ack"]["rid"], 5)
        self.assertEqual(seen["selection"]["selection"], [node.id])
        # ping/pong
        ws.send(b"hi", opcode=0x9)
        opcode, payload = ws.recv_frame()
        self.assertEqual((opcode, payload), (0xA, b"hi"))
        # bad json -> error ack
        ws.send(b"{not json", opcode=0x1)
        err = ws.recv_until("ack")
        self.assertFalse(err["ok"])
        # a large frame (> 64 KiB) goes through the 8-byte length path
        big = {"t": "log", "text": "x" * 70000, "rid": 6}
        ws.send(big)
        self.assertTrue(ws.recv_until("ack")["ok"])
        # second client sees the first's edits
        ws2 = WSClient(self.port)
        ws2.recv_until("preview")
        ws.send({"t": "move", "node": node.id, "x": 500, "y": 600, "phase": "end"})
        patch = ws2.recv_until("patch")
        self.assertEqual(patch["node"]["x"], 500)
        ws2.close()
        ws.close()
        # give the server a moment to drop the clients
        import time

        for _ in range(50):
            if not self.session.clients:
                break
            time.sleep(0.05)
        self.assertEqual(self.session.clients, {})

    def test_media_route(self):
        import base64

        from grasshoppermode.tests.test_media import tiny_png

        png = "data:image/png;base64," + base64.b64encode(tiny_png(3, 3)).decode()
        ack = self.session.handle("x", {"t": "snapshot", "png": png, "mode": "preview"})
        status, head, body = http_get(self.port, ack["url"])
        self.assertEqual(status, 200)
        self.assertIn("image/png", head)
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(
            http_get(self.port, "/media/%s/nope.png" % self.session.media.canvas_id)[0], 404
        )
        self.assertEqual(http_get(self.port, "/media/other/x.png")[0], 404)

    def test_urls(self):
        urls = self.server.urls()
        self.assertTrue(any(u.startswith("http://localhost:%d/xr" % self.port) for u in urls))
        self.assertTrue(self.server.base_url().startswith("http://"))


if __name__ == "__main__":
    unittest.main()
