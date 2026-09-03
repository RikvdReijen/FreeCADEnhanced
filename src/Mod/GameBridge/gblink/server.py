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
"""The live link server, which runs inside FreeCAD while the user works.

Two properties matter more than anything else here.

**It must never block FreeCAD's GUI thread.**  :meth:`LinkServer.publish` is
called from a document observer, which runs on the main thread during a
recompute.  If publishing waited for a socket, an engine that stopped reading -
because the artist opened a modal dialog, or their machine started swapping -
would freeze the CAD application.  So each client has its own queue and its own
writer thread, and publishing only ever appends to a queue.

**A client that falls behind must not grow the queue without limit.**  When a
queue fills, its pending deltas are thrown away and the client is marked for a
full resync instead.  A delta is only meaningful against the state before it, so
dropping one in the middle would silently corrupt the client's copy of the
model; dropping *all* of them and re-sending the scene is both correct and, for
a client that far behind, usually smaller.

The server binds to the loopback interface.  Binding anywhere else means every
machine that can reach the port can read the model being worked on, so it takes
an explicit argument and a token.
"""

import socket
import threading
import time

try:  # Python 3
    import queue
except ImportError:  # pragma: no cover - FreeCAD is Python 3 only
    import Queue as queue

from gbcore import BRIDGE_VERSION
from gbcore.transform import get_convention

from . import protocol
from .session import LinkSession

__all__ = ["LinkServer", "ClientConnection", "DEFAULT_PORT"]

#: Unassigned by IANA, and far enough from anything common to be a safe default.
DEFAULT_PORT = 54321

#: How many messages may be waiting for one client before it is resynced.
QUEUE_LIMIT = 16

#: How often the accept and writer threads look for a shutdown, in seconds.
ACCEPT_POLL_INTERVAL = 0.1


class ClientConnection:
    """One connected engine, its socket, its queue and its view of the model."""

    def __init__(self, server, sock, address):
        self.server = server
        self.socket = sock
        self.address = address
        self.session = LinkSession(server.convention, server.name)
        self.reader = protocol.FrameReader()
        self.queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.name = "%s:%s" % address[:2]
        self.engine = None
        self.authenticated = server.token is None
        self.connected_at = time.time()
        self.needs_full = True
        self.closing = False
        #: Held while the session is being advanced.  publish_to() is reached
        #: from the GUI thread and from this client's own reader thread, and a
        #: session's idea of what it has sent cannot survive two threads
        #: advancing it at once.
        self.session_lock = threading.Lock()
        self._threads = []

    # -- lifecycle -------------------------------------------------------

    def start(self):
        for target in (self._read_loop, self._write_loop):
            thread = threading.Thread(target=target, name="GameBridge-%s" % target.__name__)
            thread.daemon = True
            thread.start()
            self._threads.append(thread)

    def close(self, reason=None):
        if self.closing:
            return
        self.closing = True
        if reason:
            try:
                self.socket.sendall(protocol.encode(protocol.goodbye(reason)))
            except OSError:
                pass
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass
        # Unblock the writer, which is otherwise parked on an empty queue.
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        self.server._remove(self)

    # -- sending ---------------------------------------------------------

    def send(self, message):
        """Queue a message.  Never blocks, never raises on a slow client."""
        if self.closing:
            return False
        try:
            self.queue.put_nowait(message)
            return True
        except queue.Full:
            self._drop_backlog()
            return False

    def _drop_backlog(self):
        """Throw the backlog away and arrange for a fresh full scene.

        Deltas only make sense in order and against a known state, so a client
        that cannot keep up is better served by one complete scene than by a
        queue of updates it will apply minutes late.
        """
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        self.needs_full = True
        self.session.reset()
        self.server._log(
            "%s fell behind; its backlog was dropped and it will be resynced"
            % self.name
        )

    def _write_loop(self):
        while not self.closing:
            try:
                message = self.queue.get(timeout=ACCEPT_POLL_INTERVAL)
            except queue.Empty:
                continue
            if message is None:
                break
            try:
                self.socket.sendall(protocol.encode(message))
            except (OSError, AttributeError) as problem:
                self.server._log("%s disconnected while sending: %s" % (self.name, problem))
                self.close()
                break

    # -- receiving -------------------------------------------------------

    def _read_loop(self):
        while not self.closing:
            try:
                data = self.socket.recv(65536)
            except (OSError, AttributeError):
                # AttributeError: the socket was closed and cleared by another
                # thread while this one was parked in recv().
                break
            if not data:
                break
            try:
                for message in self.reader.feed(data):
                    self._handle(message)
            except protocol.ProtocolError as problem:
                self.server._log("%s spoke nonsense: %s" % (self.name, problem))
                self.close("protocol error: %s" % problem)
                return
        self.close()

    def _handle(self, message):
        server = self.server
        if message.type == "hello":
            self._handle_hello(message)
            return
        if not self.authenticated:
            self.close("a token is required before anything else")
            return
        if message.type == "ping":
            self.send(protocol.pong(message.get("sequence", 0)))
        elif message.type == "resync":
            self.needs_full = True
            self.session.reset()
            server._log("%s asked for a full resync" % self.name)
            server.publish_to(self)
        elif message.type == "selection":
            server._on_selection(self, message.get("objects", []))
        elif message.type == "bye":
            self.close()
        else:
            server._log("%s sent an unknown message %r" % (self.name, message.type))

    def _handle_hello(self, message):
        server = self.server
        if message.get("protocol") != protocol.PROTOCOL_VERSION:
            self.send(
                protocol.error(
                    "this bridge speaks protocol %d, the client speaks %s"
                    % (protocol.PROTOCOL_VERSION, message.get("protocol")),
                    fatal=True,
                )
            )
            self.close("protocol version mismatch")
            return
        if server.token and message.get("token") != server.token:
            # Say as little as possible about why; a client that guessed wrong
            # learns nothing from a more detailed refusal.
            self.send(protocol.error("authentication failed", fatal=True))
            self.close("authentication failed")
            return
        self.authenticated = True
        self.engine = message.get("engine")
        self.name = "%s (%s)" % (message.get("client") or "client", self.engine or "?")
        self.send(
            protocol.welcome(
                server.name,
                server.document_name,
                server.convention,
                server.session_id,
                BRIDGE_VERSION,
            )
        )
        server._log("%s connected from %s" % (self.name, self.address[0]))
        server.publish_to(self)

    def describe(self):
        return {
            "name": self.name,
            "engine": self.engine,
            "address": self.address[0],
            "connectedFor": round(time.time() - self.connected_at, 1),
            "queued": self.queue.qsize(),
            "session": self.session.stats(),
        }


class LinkServer:
    """Accepts engine connections and pushes the document at them as it changes."""

    def __init__(
        self,
        host="127.0.0.1",
        port=DEFAULT_PORT,
        convention="unity",
        name="FreeCAD",
        token=None,
        allow_remote=False,
        logger=None,
    ):
        if host not in ("127.0.0.1", "::1", "localhost") and not allow_remote:
            raise ValueError(
                "refusing to listen on %s: anything that can reach that address "
                "could read the document being edited. Pass allow_remote=True "
                "and set a token if that is really what you want." % host
            )
        if allow_remote and not token:
            raise ValueError("a token is required when listening beyond loopback")
        self.host = host
        self.port = port
        self.convention = get_convention(convention)
        self.name = name
        self.token = token
        self.logger = logger
        self.session_id = "%s-%d" % (name, int(time.time()))
        self.document_name = None
        #: Called with (connection, [object names]) when an engine selects
        #: something, so FreeCAD can highlight the same parts.
        self.selection_callback = None
        #: The scene to hand a client that connects between publishes.
        self._scene = None
        self._clients = []
        self._lock = threading.Lock()
        self._socket = None
        self._accept_thread = None
        self.running = False

    # -- lifecycle -------------------------------------------------------

    def start(self):
        if self.running:
            return self
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        # Ask for a real port number when the caller passed 0, so that
        # self.port is always something a client can be told to connect to.
        self.port = self._socket.getsockname()[1]
        self._socket.listen(8)
        # The accept thread polls rather than blocking forever, because closing
        # a listening socket from another thread does not reliably wake a thread
        # already inside accept().  A tenth of a second is short enough that
        # stopping the link feels instant and long enough to cost nothing.
        self._socket.settimeout(ACCEPT_POLL_INTERVAL)
        self.running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="GameBridge-accept"
        )
        self._accept_thread.daemon = True
        self._accept_thread.start()
        self._log("listening on %s:%d" % (self.host, self.port))
        return self

    def stop(self, reason="the FreeCAD side is shutting down"):
        self.running = False
        # Close the listener first so no client can connect while the existing
        # ones are being told the link is going away.
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        for client in list(self._clients):
            client.close(reason)
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
            self._accept_thread = None
        self._log("stopped")
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exception):
        self.stop()
        return False

    def _accept_loop(self):
        while self.running:
            try:
                sock, address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client = ClientConnection(self, sock, address)
            with self._lock:
                self._clients.append(client)
            client.start()

    def _remove(self, client):
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    # -- publishing ------------------------------------------------------

    def publish(self, scene, document_name=None):
        """Push a scene to every client.  Safe to call from the GUI thread."""
        self._scene = scene
        if document_name:
            self.document_name = document_name
        for client in self.clients:
            self.publish_to(client)
        return self

    def publish_to(self, client):
        """Send one client whatever it is missing."""
        scene = self._scene
        if scene is None or not client.authenticated or client.closing:
            return None
        with client.session_lock:
            if client.needs_full or not client.session.has_state:
                message = client.session.full_scene(scene)
                client.needs_full = False
            else:
                message = client.session.update(scene)
        if message is None:
            return None
        client.send(message)
        return message

    def broadcast(self, message):
        """Send the same message to everyone, for pings and notices."""
        for client in self.clients:
            if client.authenticated:
                client.send(message)

    # -- inspection ------------------------------------------------------

    @property
    def clients(self):
        with self._lock:
            return list(self._clients)

    @property
    def client_count(self):
        return len(self.clients)

    def describe(self):
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "session": self.session_id,
            "target": self.convention.to_dict(),
            "document": self.document_name,
            "requiresToken": bool(self.token),
            "clients": [c.describe() for c in self.clients],
        }

    # -- plumbing --------------------------------------------------------

    def _on_selection(self, client, names):
        if self.selection_callback is not None:
            try:
                self.selection_callback(client, names)
            except Exception as problem:  # a bad callback must not kill the link
                self._log("the selection callback raised %s" % problem)

    def _log(self, message):
        line = "GameBridge link: %s" % message
        if self.logger is not None:
            self.logger(line)
        else:
            print(line)
