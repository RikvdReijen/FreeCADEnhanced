# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tiny HTTP + WebSocket server (standard library only) that hosts the WebXR
client and relays messages to a :class:`~grasshoppermode.session.Session`.

Why not a proper web framework?  FreeCAD ships no ``websockets``/``aiohttp``
and an experimental mode should not add dependencies.  The server runs its
own asyncio loop in a background thread; graph mutations are marshalled to
the caller's thread through the ``dispatch`` callable (FreeCAD passes one
that hops onto the Qt main thread).

Routes
------
``/`` and ``/xr``        the WebXR client (``index.html``)
``/<file>``              static files from the client directory
``/marker.svg``          QR marker for this canvas (``?i=<index>&mm=<size>``)
``/marker.json``         the same marker as a module matrix (for image tracking)
``/marker.html``         printable sheet with the markers and instructions
``/api/state``           JSON snapshot of the graph
``/ws``                  WebSocket endpoint

Secure contexts
---------------
WebXR needs HTTPS or ``localhost``.  Two ways to satisfy that with a Quest:
``adb reverse tcp:8765 tcp:8765`` makes ``http://localhost:8765`` on the
headset reach this server, or start the server with a certificate/key pair
(preferences) and accept the certificate once in the headset browser.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse

from . import protocol, qrcode_gen

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HTTP_HEADER = 64 * 1024
MAX_WS_FRAME = 8 * 1024 * 1024

_OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING, _OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class _Client:
    def __init__(self, client_id, writer, address):
        self.id = client_id
        self.writer = writer
        self.address = address
        self.open = True


class XRServer:
    def __init__(
        self,
        session,
        static_dir,
        host="0.0.0.0",
        port=8765,
        certfile=None,
        keyfile=None,
        dispatch=None,
    ):
        self.session = session
        self.static_dir = os.path.abspath(static_dir)
        self.host = host
        self.port = int(port)
        self.certfile = certfile
        self.keyfile = keyfile
        # dispatch(fn) must run fn on the thread that owns the graph
        self.dispatch = dispatch or (lambda fn: fn())
        self.loop = None
        self.thread = None
        self._server = None
        self._clients = {}
        self._next_id = 1
        self._ready = threading.Event()
        self._error = None
        self.started_at = None
        self.bound_port = None

    # ------------------------------------------------------------ lifecycle
    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def scheme(self):
        return "https" if self.certfile else "http"

    def start(self, wait=True, timeout=5.0):
        if self.running:
            return
        self._ready.clear()
        self._error = None
        self.thread = threading.Thread(target=self._run, name="GrasshopperXRServer", daemon=True)
        self.thread.start()
        if wait:
            self._ready.wait(timeout)
            if self._error:
                raise self._error
        return self

    def stop(self, timeout=5.0):
        if not self.running:
            return
        loop = self.loop
        if loop:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                fut.result(timeout)
            except Exception:  # noqa: BLE001
                pass
            loop.call_soon_threadsafe(loop.stop)
        self.thread.join(timeout)
        self.thread = None

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
            self.loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            self.loop.close()
            self.loop = None

    async def _serve(self):
        ctx = None
        if self.certfile:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.certfile, self.keyfile)
        self._server = await asyncio.start_server(
            self._on_connection, self.host, self.port, ssl=ctx
        )
        self.bound_port = self._server.sockets[0].getsockname()[1]
        self.started_at = time.time()
        self._ready.set()

    async def _shutdown(self):
        for client in list(self._clients.values()):
            try:
                await self._ws_send_raw(client, _OP_CLOSE, struct.pack("!H", 1001))
                client.writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------- helpers
    def urls(self):
        """Candidate URLs to open on the headset/phone."""
        hosts = ["localhost"]
        try:
            hosts.append(socket.gethostbyname(socket.gethostname()))
        except OSError:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            hosts.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        seen = []
        for h in hosts:
            if h not in seen and not h.startswith("127."):
                seen.append(h)
        if "localhost" not in seen:
            seen.insert(0, "localhost")
        port = self.bound_port or self.port
        return ["%s://%s:%d/xr" % (self.scheme, h, port) for h in seen]

    def base_url(self):
        urls = [u for u in self.urls() if "localhost" not in u]
        url = (urls or self.urls())[0]
        return url[: -len("/xr")]

    def send_text(self, client_id, text):
        """Thread-safe send to one client."""
        loop = self.loop
        client = self._clients.get(client_id)
        if loop is None or client is None:
            return
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._ws_send_text(client, text)))

    # ------------------------------------------------------------- HTTP
    async def _on_connection(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        if len(head) > MAX_HTTP_HEADER:
            writer.close()
            return
        try:
            request_line, headers = _parse_head(head)
            method, target, _version = request_line.split(" ", 2)
        except ValueError:
            await self._http_response(writer, 400, b"bad request")
            writer.close()
            return
        path = urllib.parse.urlsplit(target)
        if headers.get("upgrade", "").lower() == "websocket" and path.path == "/ws":
            await self._websocket(reader, writer, headers)
            return
        if method not in ("GET", "HEAD"):
            await self._http_response(writer, 405, b"method not allowed")
        else:
            try:
                status, body, ctype = self.route(path.path, urllib.parse.parse_qs(path.query))
            except Exception as exc:  # noqa: BLE001
                status, body, ctype = 500, ("server error: %s" % exc).encode(), "text/plain"
            await self._http_response(writer, status, b"" if method == "HEAD" else body, ctype)
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()

    def route(self, path, query):
        """Return (status, body_bytes, content_type) for a GET."""
        if path in ("/", "/xr", "/index.html"):
            return self._static("index.html")
        if path == "/marker.svg":
            index = int(query.get("i", ["0"])[0])
            mm = float(query.get("mm", [self.session.marker_mm])[0])
            spec = self.session.marker_spec(index)
            spec.size_mm = mm
            spec.base_url = self.base_url()
            return 200, marker_svg(spec).encode("utf-8"), "image/svg+xml"
        if path == "/marker.json":
            index = int(query.get("i", ["0"])[0])
            spec = self.session.marker_spec(index)
            spec.base_url = self.base_url()
            modules = qrcode_gen.encode_best_available(spec.payload(), "M")
            body = json.dumps(
                {
                    "marker": spec.to_dict(),
                    "modules": [[1 if v else 0 for v in row] for row in modules],
                    "quiet": 4,
                }
            )
            return 200, body.encode("utf-8"), "application/json"
        if path == "/marker.html":
            return (
                200,
                marker_sheet_html(self.session, self.base_url()).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path == "/favicon.ico":
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" rx="3" fill="#3a7d44"/><circle cx="5" cy="8" r="2" fill="#fff"/><circle cx="11" cy="8" r="2" fill="#fff"/><path d="M7 8h2" stroke="#fff"/></svg>'
            return 200, svg.encode("utf-8"), "image/svg+xml"
        if path == "/api/state":
            body = protocol.encode(self.session.graph_message())
            return 200, body.encode("utf-8"), "application/json"
        if path == "/api/info":
            info = {
                "urls": self.urls(),
                "canvas": self.session.graph.canvas_id,
                "clients": len(self._clients),
                "profile": self.session.profile,
                "backend": getattr(self.session.graph.backend, "name", None),
            }
            return 200, json.dumps(info).encode("utf-8"), "application/json"
        return self._static(path.lstrip("/"))

    def _static(self, rel):
        full = os.path.abspath(os.path.join(self.static_dir, rel))
        if not full.startswith(self.static_dir + os.sep) and full != self.static_dir:
            return 403, b"forbidden", "text/plain"
        if not os.path.isfile(full):
            return 404, b"not found", "text/plain"
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in (
            "application/javascript",
            "application/json",
            "image/svg+xml",
        ):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            return 200, fh.read(), ctype

    async def _http_response(self, writer, status, body, ctype="text/plain"):
        reason = {
            200: "OK",
            400: "Bad Request",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
        }.get(status, "OK")
        head = (
            "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n"
            "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
            % (status, reason, ctype, len(body))
        )
        writer.write(head.encode("latin-1") + body)

    # --------------------------------------------------------- WebSocket
    async def _websocket(self, reader, writer, headers):
        key = headers.get("sec-websocket-key")
        if not key:
            await self._http_response(writer, 400, b"missing websocket key")
            writer.close()
            return
        accept = base64.b64encode(hashlib.sha1(key.encode("latin-1") + WS_MAGIC).digest()).decode(
            "ascii"
        )
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % accept
            ).encode("latin-1")
        )
        await writer.drain()
        client = _Client("c%d" % self._next_id, writer, writer.get_extra_info("peername"))
        self._next_id += 1
        self._clients[client.id] = client
        send = lambda msg, cid=client.id: self.send_text(cid, protocol.encode(msg))  # noqa: E731
        self.dispatch(lambda: self.session.add_client(client.id, send))
        try:
            await self._ws_loop(reader, client)
        finally:
            client.open = False
            self._clients.pop(client.id, None)
            self.dispatch(lambda: self.session.remove_client(client.id))
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _ws_loop(self, reader, client):
        fragments = []
        while True:
            try:
                fin, opcode, payload = await _read_frame(reader)
            except (asyncio.IncompleteReadError, ConnectionError, ValueError):
                return
            if opcode == _OP_CLOSE:
                try:
                    await self._ws_send_raw(client, _OP_CLOSE, payload[:2])
                except Exception:  # noqa: BLE001
                    pass
                return
            if opcode == _OP_PING:
                await self._ws_send_raw(client, _OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode in (_OP_TEXT, _OP_BIN) or opcode == 0:
                fragments.append(payload)
                if not fin:
                    continue
                data = b"".join(fragments)
                fragments = []
                if opcode == _OP_BIN:
                    continue
                self._handle_text(client, data)

    def _handle_text(self, client, data):
        try:
            msg = protocol.decode(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self.send_text(
                client.id, protocol.encode(protocol.ack(None, False, "bad message: %s" % exc))
            )
            return

        def work(cid=client.id, msg=msg):
            reply = self.session.handle(cid, msg)
            if reply is not None and (msg.get("rid") is not None or not reply.get("ok", True)):
                self.send_text(cid, protocol.encode(reply))

        self.dispatch(work)

    async def _ws_send_text(self, client, text):
        if not client.open:
            return
        try:
            await self._ws_send_raw(client, _OP_TEXT, text.encode("utf-8"))
        except (ConnectionError, RuntimeError):
            client.open = False

    async def _ws_send_raw(self, client, opcode, payload):
        writer = client.writer
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([n])
        elif n < 65536:
            header += bytes([126]) + struct.pack("!H", n)
        else:
            header += bytes([127]) + struct.pack("!Q", n)
        writer.write(header + payload)
        await writer.drain()


def _parse_head(head):
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    request_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return request_line, headers


async def _read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    if length > MAX_WS_FRAME:
        raise ValueError("frame too large")
    mask = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(length)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


# ------------------------------------------------------------- marker pages
def marker_svg(spec, quiet=4):
    modules = qrcode_gen.encode_best_available(spec.payload(), "M")
    module_mm = spec.size_mm / (len(modules) + 2 * quiet)
    caption = "%g mm - canvas %s - marker %d" % (spec.size_mm, spec.canvas_id, spec.marker_index)
    return qrcode_gen.to_svg(
        modules, module_mm=module_mm, quiet=quiet, label=spec.label, caption=caption
    )


def marker_sheet_html(session, base_url, count=2):
    spec = session.marker_spec()
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Grasshopper mode markers</title>",
        "<style>body{font-family:sans-serif;margin:16mm}h1{font-size:18pt}.m{display:inline-block;margin:8mm;text-align:center}",
        "p{max-width:170mm}@media print{.no{display:none}}</style></head><body>",
        "<h1>FreeCAD Grasshopper mode - canvas markers</h1>",
        "<p class='no'>Print at 100%% scale (no 'fit to page'). The marker must measure <b>%g mm</b> across the full square including the white border. "
        "Place marker 0 at the top-left corner of where you want the node sheet on your table; marker 1 is a spare for a second position.</p>"
        % spec.size_mm,
        "<p class='no'>Headset URL: <code>%s/xr</code></p>" % base_url,
    ]
    for i in range(count):
        parts.append(
            "<div class='m'><img src='marker.svg?i=%d' style='width:%gmm;height:auto'></div>"
            % (i, spec.size_mm)
        )
    parts.append("</body></html>")
    return "".join(parts)
