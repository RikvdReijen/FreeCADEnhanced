# SPDX-License-Identifier: LGPL-2.1-or-later
"""Multi-user sessions: the presence registry, locks, and the wire round trip."""

import os
import sys
import time
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrsync import protocol as P  # noqa: E402
from xrsync.client import SyncClient  # noqa: E402
from xrsync.presence import LockTable, PresenceRegistry, peer_colour, peer_id_for  # noqa: E402
from xrsync.server import DocumentBridge, SyncServer  # noqa: E402


class Clock(object):
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RegistryTest(unittest.TestCase):
    def test_join_update_expire(self):
        clock = Clock()
        reg = PresenceRegistry(timeout=5.0, clock=clock)
        state, joined = reg.update("a", {"name": "Rik", "head": {"position": [0, 1.6, 0]}}, device="Quest 3")
        self.assertTrue(joined)
        self.assertEqual(state.name, "Rik")
        self.assertEqual(state.device, "Quest 3")
        self.assertEqual(len(state.colour), 3)
        state, joined = reg.update("a", {"scale": 12.0})
        self.assertFalse(joined)
        self.assertEqual(state.scale, 12.0)
        self.assertEqual(state.head["position"], [0, 1.6, 0], "unchanged keys are kept")
        self.assertEqual(state.seq, 2)
        reg.update("b", {})
        self.assertEqual([p.peer_id for p in reg.peers(exclude="a")], ["b"])
        clock.now = 6.0
        reg.update("b", {})
        self.assertEqual(reg.expire(), ["a"])
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.to_dict()["peers"][0]["peer_id"], "b")

    def test_ids_and_colours(self):
        self.assertEqual(len(peer_id_for("token")), 8)
        self.assertNotEqual(peer_id_for("token"), peer_id_for("other"))
        self.assertNotIn("token", peer_id_for("token"))
        c = peer_colour("abc")
        self.assertEqual(c, peer_colour("abc"))
        self.assertTrue(all(0.0 <= x <= 1.0 for x in c))


class LockTest(unittest.TestCase):
    def test_acquire_release_expire(self):
        clock = Clock()
        locks = LockTable(ttl=10.0, clock=clock)
        ok, lock = locks.acquire("Peg", "a")
        self.assertTrue(ok)
        self.assertEqual(lock.expires, 10.0)
        ok, lock = locks.acquire("Peg", "b")
        self.assertFalse(ok)
        self.assertEqual(lock.holder, "a")
        self.assertFalse(locks.release("Peg", "b"))
        clock.now = 5.0
        ok, lock = locks.acquire("Peg", "a")  # renew
        self.assertEqual(lock.expires, 15.0)
        self.assertEqual(lock.acquired, 0.0)
        clock.now = 16.0
        self.assertIsNone(locks.holder("Peg"), "expired")
        ok, _ = locks.acquire("Peg", "b")
        self.assertTrue(ok)
        locks.acquire("Hole", "b")
        self.assertEqual(sorted(locks.release_all("b")), ["Hole", "Peg"])
        self.assertTrue(locks.release("Nothing", "a"))


class FakeBridge(DocumentBridge):
    def __init__(self):
        self.moves = []

    def list_documents(self):
        return []

    def scene(self, doc, lod):
        return b""

    def scene_hash(self, doc):
        return "0"

    def list_environments(self):
        return []

    def environment(self, env_id):
        return {}

    def apply_paint(self, data, doc):
        return {}

    def apply_vector(self, vector, doc):
        return {}

    def thumbnail(self, doc):
        return b""

    def state(self):
        return {}

    def apply_move(self, move):
        self.moves.append(move)
        return True


class WireTest(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge()
        self.server = SyncServer(port=0, bridge=self.bridge, auth_required=False, discovery=False, devices_path=os.devnull)
        self.server.start()
        self.a = SyncClient("127.0.0.1", self.server.port, device="Quest A")
        self.b = SyncClient("127.0.0.1", self.server.port, device="Quest B")

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.server.stop()

    def test_presence_round_trip(self):
        # Unauthenticated: peers are told apart by an X-Peer header per client.
        self.a.extra_headers = {"X-Peer": "a"}
        self.b.extra_headers = {"X-Peer": "b"}
        reply = self.a.presence({"name": "Rik", "head": {"position": [0, 1.6, 0], "rotation": [0, 0, 0, 1]},
                                 "hands": [{"position": [-0.3, 1.2, -0.3], "grip": 0.0}], "environment": "studio", "scale": 1.0})
        self.assertTrue(reply.peer_id)
        self.assertEqual(reply.peers, [], "nobody else yet")
        reply_b = self.b.presence({"name": "Sam", "head": {"position": [1, 1.6, 0]}})
        self.assertEqual([p.name for p in reply_b.peers], ["Rik"])
        self.assertEqual(reply_b.peers[0].head["position"], [0, 1.6, 0])
        self.assertNotEqual(reply_b.peer_id, reply.peer_id)
        listing = self.a.presence()
        self.assertEqual([p.name for p in listing.peers], ["Sam"])
        events, _ = self.a.poll_events(0, timeout=0.1)
        self.assertEqual([e.type for e in events], [P.EVENT_PEER_JOINED, P.EVENT_PEER_JOINED])
        self.assertEqual(events[0].data["name"], "Rik")

    def test_locks_and_moves(self):
        self.a.extra_headers = {"X-Peer": "a"}
        self.b.extra_headers = {"X-Peer": "b"}
        lock = self.a.lock("Peg")
        self.assertTrue(lock.ok)
        refused = self.b.lock("Peg")
        self.assertFalse(refused.ok)
        self.assertEqual(refused.holder, lock.holder)
        from xrsync.client import HttpError

        with self.assertRaises(HttpError):
            self.b.push_move("Peg", [0, 0, 0], [0, 0, 0, 1])
        moved = self.a.push_move("Peg", [0.1, 0.2, 0.3], [0, 0, 0, 1], doc="Asm", final=True)
        self.assertTrue(moved.ok)
        self.assertEqual(self.bridge.moves[0]["object"], "Peg")
        self.assertEqual(moved.message, "moved")
        self.assertTrue(self.a.lock("Peg", acquire=False).ok)
        self.assertTrue(self.b.lock("Peg").ok, "free after release")
        events, _ = self.b.poll_events(0, timeout=0.1)
        types = [e.type for e in events]
        self.assertEqual(types, [P.EVENT_LOCK, P.EVENT_OBJECT_MOVED, P.EVENT_UNLOCK, P.EVENT_LOCK])
        self.assertEqual(events[1].data["position"], [0.1, 0.2, 0.3])
        self.assertTrue(events[1].data["final"])

    def test_voice_and_qr_sinks(self):
        heard = []
        self.server.voice_sink = lambda payload, peer: heard.append((payload["text"], peer)) or "ok"
        seen = []
        self.server.qr_sink = lambda payload, peer: seen.append(payload) or "snapped"
        reply = self.a.push_voice("fillet two millimetres", 0.9)
        self.assertEqual(reply.message, "ok")
        self.assertEqual(heard[0][0], "fillet two millimetres")
        corners = [[0, 0, 0], [0.08, 0, 0], [0.08, -0.08, 0], [0, -0.08, 0]]
        reply = self.a.push_qr("fcxr://anchor?id=x&size=80", corners, 1.5)
        self.assertEqual(reply.message, "snapped")
        self.assertEqual(seen[0]["corners"][1], [0.08, 0, 0])
        events, _ = self.a.poll_events(0, timeout=0.1)
        self.assertEqual([e.type for e in events], [P.EVENT_VOICE, P.EVENT_QR])
        with self.assertRaises(Exception):
            self.a.push_qr("x", [[0, 0, 0]])

    def test_validation_errors(self):
        from xrsync.client import HttpError

        with self.assertRaises(P.ProtocolError):
            self.a.presence({"head": {"position": [1, 2]}})
        with self.assertRaises(P.ProtocolError):
            self.a.lock("")
        status, _, body = self.a.request("POST", P.EP_LOCK, body=b'{"object": ""}', content_type=P.CONTENT_TYPE_JSON)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
