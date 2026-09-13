# SPDX-License-Identifier: LGPL-2.1-or-later
"""The shared room: membership, host authority, edits, co-location maths."""

import math
import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrassembly.mates import rotation_about  # noqa: E402
from xrsketch import vecmath as vm  # noqa: E402
from xrsync.room import (Room, colocation_transform, pose_beside, pose_dict, pose_facing, to_local, to_shared,  # noqa: E402
                         world_offset_for_scale)


class RoomTest(unittest.TestCase):
    def test_join_host_leave(self):
        room = Room("bench")
        a, joined = room.join("a", "Rik", "desktop")
        self.assertTrue(joined)
        self.assertEqual(room.host, "a")
        self.assertEqual(a.role, "host")
        b, joined = room.join("b", "Sam", "Quest 3", {"voice": True})
        self.assertEqual(b.role, "guest")
        _, again = room.join("b", "Sam")
        self.assertFalse(again)
        with self.assertRaises(PermissionError):
            room.set_state("b", environment="void")
        self.assertTrue(room.set_state("a", environment="void", scale=12.0))
        self.assertFalse(room.set_state("a", environment="void"))
        seq = room.seq
        room.leave("a")
        self.assertEqual(room.host, "b", "handed over")
        self.assertEqual(room.members["b"].role, "host")
        self.assertGreater(room.seq, seq)
        room.claim_host("b")
        with self.assertRaises(KeyError):
            room.claim_host("zz")
        d = room.to_dict()
        self.assertEqual(d["environment"], "void")
        self.assertEqual(len(d["members"]), 1)

    def test_edit_log(self):
        room = Room()
        room.join("a")
        e1 = room.record_edit("a", [{"op": "set_param", "target": "Pad.Length", "from": 10, "to": 12}], layer="live", applied=True)
        e2 = room.record_edit("b", [{"op": "remove_feature", "target": "Fillet"}])
        self.assertEqual([e.seq for e in room.edits_since(0)], [1, 2])
        self.assertEqual(room.edits_since(1)[0].operations[0]["op"], "remove_feature")
        self.assertEqual(e1.to_dict()["applied"], True)
        self.assertEqual(room.edit_seq, 2)
        self.assertIsNone(e2.applied)

    def test_anchor_observation_defines_the_frame(self):
        room = Room()
        room.join("a")
        room.join("b")
        # peer a sees the code at (1,0,-2) in its frame; b sees it at (0,0,-1) rotated
        ca = room.observe_anchor("a", "bench", {"position": [1, 0, -2], "rotation": [0, 0, 0, 1]})
        self.assertEqual(room.anchor["id"], "bench")
        self.assertTrue(room.members["a"].calibrated)
        q = rotation_about((0, 1, 0), math.pi / 2)
        cb = room.observe_anchor("b", "bench", {"position": [0, 0, -1], "rotation": list(q)})
        # the anchor itself maps to the shared origin from both sides
        for calib, local in ((ca, vm.Transform((1, 0, -2))), (cb, vm.Transform((0, 0, -1), q))):
            shared = to_shared(local, calib)
            self.assertEqual([round(c, 9) for c in shared.translation], [0.0, 0.0, 0.0])
        # a point 1 m in front of the code (its +Z... local -Z) agrees across devices
        p_a = vm.Transform((1, 0, -2)).apply((0, 0, 1))
        p_b = vm.Transform((0, 0, -1), q).apply((0, 0, 1))
        sa, sb = to_shared(vm.Transform(p_a), ca), to_shared(vm.Transform(p_b), cb)
        self.assertEqual([round(c, 9) for c in sa.translation], [round(c, 9) for c in sb.translation])
        self.assertIsNone(room.observe_anchor("b", "other", {"position": [0, 0, 0]}))
        back = to_local(sa, ca)
        self.assertEqual([round(c, 9) for c in back.translation], [round(c, 9) for c in p_a])


class PoseTest(unittest.TestCase):
    def test_colocation_transform(self):
        local = vm.Transform((2, 0, 0), rotation_about((0, 1, 0), 0.3))
        shared = vm.Transform((0, 1, 0), rotation_about((0, 1, 0), -0.5))
        c = colocation_transform(local, shared)
        self.assertTrue(vm.compose(c, local).almost_equal(shared, 1e-9))
        c2 = colocation_transform(pose_dict(local), pose_dict(shared))
        self.assertTrue(c.almost_equal(c2, 1e-9))

    def test_pose_beside_and_facing(self):
        head = vm.Transform((0, 1.6, 0), rotation_about((0, 1, 0), 0.0))  # facing -Z
        beside = pose_beside(head, 0.8, +1.0)
        self.assertEqual([round(c, 6) for c in beside.translation], [0.8, 1.6, 0.0])
        my_forward = beside.apply_vector((0, 0, -1))
        self.assertAlmostEqual(my_forward[2], -1.0, msg="facing the same way")
        facing = pose_facing(head, 1.2)
        self.assertEqual([round(c, 6) for c in facing.translation], [0.0, 1.6, -1.2])
        f = facing.apply_vector((0, 0, -1))
        self.assertAlmostEqual(f[2], 1.0, msg="looking back at the peer")

    def test_scale_change_keeps_pivot(self):
        origin = {"position": [1.0, 0.0, 0.0], "rotation": [0, 0, 0, 1]}
        moved = world_offset_for_scale(origin, 1.0, 2.0, pivot=(0.0, 0.0, 0.0))
        self.assertEqual(moved["position"], [2.0, 0.0, 0.0])
        same = world_offset_for_scale(origin, 1.0, 2.0, pivot=(1.0, 0.0, 0.0))
        self.assertEqual(same["position"], [1.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
