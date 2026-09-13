# SPDX-License-Identifier: LGPL-2.1-or-later
"""The room, edit and product-data endpoints over the real HTTP server."""

import os
import shutil
import sys
import tempfile
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)
COLLAB_ROOT = os.path.join(os.path.dirname(MODULE_ROOT), "Collab")
if os.path.isdir(COLLAB_ROOT) and COLLAB_ROOT not in sys.path:
    sys.path.insert(0, COLLAB_ROOT)

from xrsync import protocol as P  # noqa: E402
from xrsync.client import HttpError, SyncClient  # noqa: E402
from xrsync.server import SyncServer  # noqa: E402

from Tests.test_presence import FakeBridge  # noqa: E402

try:
    from collab.vcs import Repository, push, pull  # noqa: E402
    HAVE_COLLAB = True
except ImportError:
    HAVE_COLLAB = False


class WireCase(unittest.TestCase):
    def setUp(self):
        self.server = SyncServer(port=0, bridge=FakeBridge(), auth_required=False, discovery=False, devices_path=os.devnull)
        self.server.start()
        self.a = SyncClient("127.0.0.1", self.server.port, device="desktop")
        self.a.extra_headers = {"X-Peer": "a"}
        self.b = SyncClient("127.0.0.1", self.server.port, device="Quest B")
        self.b.extra_headers = {"X-Peer": "b"}

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.server.stop()


class RoomWireTest(WireCase):
    def test_join_state_anchor_leave(self):
        first = self.a.room(join=True, name="Rik", capabilities={"voice": True})
        self.assertTrue(first.is_host)
        self.assertEqual(first.room["host"], first.peer_id)
        second = self.b.room(join=True, name="Sam")
        self.assertFalse(second.is_host)
        self.assertEqual([m["name"] for m in second.room["members"]], ["Rik", "Sam"])
        with self.assertRaises(HttpError):
            self.b.room_set(environment="void")  # guests may not
        reply = self.a.room_set(environment="void", scale=12.0, origin={"position": [0, 0, -1], "rotation": [0, 0, 0, 1]})
        self.assertEqual((reply.room["environment"], reply.room["scale"]), ("void", 12.0))
        look = self.b.room(join=False)
        self.assertEqual(look.room["origin"]["position"], [0, 0, -1])
        # co-location: the first observation defines the shared anchor
        cal = self.b.room_anchor("bench", [1, 0, -2], [0, 0, 0, 1])
        self.assertIsNotNone(cal.calibration)
        self.assertEqual(cal.calibration["position"], [-1.0, 0.0, 2.0])
        self.assertEqual(cal.room["anchor"]["id"], "bench")
        cal_a = self.a.room_anchor("bench", [0, 0, 0], [0, 0, 0, 1])
        self.assertEqual(cal_a.calibration["position"], [0.0, 0.0, 0.0])
        self.assertTrue(all(m["calibrated"] for m in cal_a.room["members"]))
        self.assertTrue(self.a.room_leave().ok)
        after = self.b.room(join=False)
        self.assertEqual(after.room["host"], second.peer_id, "host handed over")
        self.assertTrue(after.is_host)
        taken = self.b.room_set(claim_host=True, environment="studio")
        self.assertTrue(taken.is_host)
        events, _ = self.b.poll_events(0, timeout=0.1)
        changes = [e.data.get("change") for e in events if e.type == P.EVENT_ROOM]
        self.assertEqual(changes, ["joined", "joined", "state", "calibrated", "calibrated", "left", "state"])

    def test_edits_with_and_without_sink(self):
        ops = [{"op": "set_param", "target": "Pad.Length", "from": 10, "to": 12}]
        reply = self.b.push_edit(ops, layer="live", message="taller")
        self.assertTrue(reply.ok)
        self.assertEqual((reply.seq, reply.applied, reply.message), (1, None, "broadcast"))
        seen = []
        self.server.edit_sink = lambda edit, peer: seen.append((edit, peer)) or {"applied": True, "revision": "r2", "message": "applied"}
        reply = self.a.push_edit(ops, doc="Housing")
        self.assertEqual((reply.seq, reply.applied, reply.revision), (2, True, "r2"))
        self.assertEqual(seen[0][0]["operations"], ops)
        listing = self.b.edits(since=1)
        self.assertEqual([e["seq"] for e in listing.edits], [2])
        self.assertEqual(listing.edit_seq, 2)
        with self.assertRaises(P.ProtocolError):
            self.a.push_edit([])
        self.server.edit_sink = lambda edit, peer: (_ for _ in ()).throw(ValueError("no such target"))
        with self.assertRaises(HttpError):
            self.a.push_edit(ops)
        events, _ = self.a.poll_events(0, timeout=0.1)
        self.assertEqual([e.type for e in events], [P.EVENT_EDIT, P.EVENT_EDIT])
        self.assertEqual(events[1].data["revision"], "r2")


@unittest.skipUnless(HAVE_COLLAB, "collab.vcs not importable")
class VcsWireTest(WireCase):
    def setUp(self):
        super().setUp()
        self.hub_dir = tempfile.mkdtemp(prefix="hub-")
        self.local_dir = tempfile.mkdtemp(prefix="local-")
        for d in (self.hub_dir, self.local_dir):
            with open(os.path.join(d, "housing.FCStd"), "w") as h:
                h.write("v1")
        self.hub = Repository.init(self.hub_dir, "hub")
        # the local clone shares the hub's initial snapshot
        shutil.copytree(os.path.join(self.hub_dir, ".fcvcs"), os.path.join(self.local_dir, ".fcvcs"))
        self.local = Repository(self.local_dir)
        self.server.vcs_repo = self.hub

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.hub_dir, ignore_errors=True)
        shutil.rmtree(self.local_dir, ignore_errors=True)

    def test_push_and_pull_over_http(self):
        with open(os.path.join(self.local_dir, "housing.FCStd"), "w") as h:
            h.write("v2 from the headset side")
        self.local.commit("v2", "sam")
        self.local.create_version("V1", "sam")
        transport = self.a.vcs_transport()
        report = push(self.local, transport)
        self.assertEqual(report.refs, ["Main", "version:V1"])
        self.assertEqual(self.hub.head("Main"), self.local.head("Main"))
        self.assertEqual(self.hub.verify(), [])
        self.assertIn("V1", self.hub.versions())
        # another clone pulls
        other_dir = tempfile.mkdtemp(prefix="other-")
        try:
            shutil.copytree(os.path.join(self.local_dir, ".fcvcs"), os.path.join(other_dir, ".fcvcs"))
            other = Repository(other_dir)
            other._set_head("Main", self.local.history()[-1].id)
            other._write("versions.json", {})
            report = pull(other, self.b.vcs_transport())
            self.assertTrue(report.fast_forward)
            self.assertEqual(other.head("Main"), self.hub.head("Main"))
            other.checkout("Main", force=True)
            with open(os.path.join(other_dir, "housing.FCStd")) as h:
                self.assertEqual(h.read(), "v2 from the headset side")
        finally:
            shutil.rmtree(other_dir, ignore_errors=True)
        events, _ = self.a.poll_events(0, timeout=0.1)
        self.assertTrue(any(e.type == P.EVENT_VCS for e in events))

    def test_no_repository(self):
        self.server.vcs_repo = None
        with self.assertRaises(HttpError):
            self.a.vcs("refs")
        with self.assertRaises(P.ProtocolError):
            self.a.vcs("format_disk")


if __name__ == "__main__":
    unittest.main()
