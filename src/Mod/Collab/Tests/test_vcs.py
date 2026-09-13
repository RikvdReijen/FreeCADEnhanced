# SPDX-License-Identifier: LGPL-2.1-or-later
"""Workspaces, versions, history, merge, releases and sync."""

import json
import os
import shutil
import tempfile
import unittest

from collab.vcs import (LocalTransport, Policy, ReleaseManager, Repository, VcsError, next_revision, pull, push,
                        serve, transport_from_json)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as handle:
        return handle.read()


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcvcs-")
        write(self.tmp, "housing.FCStd", "v1 binary")
        write(self.tmp, "housing.layers/index.json", json.dumps({"document": "housing.FCStd", "base": "r1", "order": [], "enabled": {}}))
        self.repo = Repository.init(self.tmp, author="rik")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RepositoryTest(RepoCase):
    def test_init_and_status(self):
        self.assertTrue(self.repo.exists())
        self.assertEqual(self.repo.current()["workspace"], "Main")
        self.assertEqual(self.repo.status(), [])
        self.assertEqual(sorted(self.repo.tracked_paths()), ["housing.FCStd", "housing.layers/index.json"])
        write(self.tmp, "housing.FCStd", "v2 binary")
        write(self.tmp, "notes.md", "hi")
        write(self.tmp, "scratch.tmp", "ignored")
        kinds = {c.path: c.kind for c in self.repo.status()}
        self.assertEqual(kinds, {"housing.FCStd": "modified", "notes.md": "added"})
        with self.assertRaises(VcsError):
            Repository.init(self.tmp)

    def test_commit_history_and_verify(self):
        with self.assertRaises(VcsError):
            self.repo.commit("nothing", "rik")
        write(self.tmp, "housing.FCStd", "v2 binary")
        snap = self.repo.commit("thicker wall", "rik", meta={"mass_g": 84})
        self.assertEqual(snap.message, "thicker wall")
        self.assertEqual(snap.meta["mass_g"], 84)
        history = self.repo.history()
        self.assertEqual([s.message for s in history], ["thicker wall", "Initial state"])
        self.assertEqual(history[0].parents, [history[1].id])
        self.assertEqual(self.repo.verify(), [])
        self.assertEqual(self.repo.resolve(snap.id[:8]), snap.id)
        self.assertEqual(self.repo.resolve("Main"), snap.id)
        with self.assertRaises(VcsError):
            self.repo.resolve("nope")

    def test_workspaces_and_checkout(self):
        write(self.tmp, "housing.FCStd", "v2 binary")
        self.repo.commit("v2", "rik")
        self.repo.create_workspace("lightweight")
        self.assertEqual(self.repo.current()["workspace"], "lightweight")
        write(self.tmp, "housing.FCStd", "v3 light")
        self.repo.commit("pockets", "claude")
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v3 light")
        self.repo.checkout("Main")
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v2 binary")
        self.assertEqual(self.repo.head("lightweight") != self.repo.head("Main"), True)
        write(self.tmp, "housing.FCStd", "dirty")
        with self.assertRaises(VcsError):
            self.repo.checkout("lightweight")
        self.repo.checkout("lightweight", force=True)
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v3 light")
        with self.assertRaises(VcsError):
            self.repo.create_workspace("lightweight")
        with self.assertRaises(VcsError):
            self.repo.delete_workspace("lightweight")
        self.repo.checkout("Main")
        self.repo.delete_workspace("lightweight")
        self.assertNotIn("lightweight", self.repo.workspaces())

    def test_versions_are_immutable_and_read_only(self):
        write(self.tmp, "housing.FCStd", "v2 binary")
        self.repo.commit("v2", "rik")
        version = self.repo.create_version("V1 to the shop", "rik", notes="first release")
        self.assertEqual(version.snapshot, self.repo.head("Main"))
        with self.assertRaises(VcsError):
            self.repo.create_version("V1 to the shop", "rik")
        write(self.tmp, "housing.FCStd", "v3")
        with self.assertRaises(VcsError):
            self.repo.create_version("V2", "rik")  # dirty tree
        self.repo.commit("v3", "rik")
        self.repo.checkout("V1 to the shop")
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v2 binary")
        self.assertIsNone(self.repo.current()["workspace"])
        write(self.tmp, "housing.FCStd", "edit on a version")
        with self.assertRaises(VcsError):
            self.repo.commit("no", "rik")
        self.repo.checkout("Main", force=True)
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v3")
        # branch from the version
        self.repo.create_workspace("fix-from-V1", from_ref="V1 to the shop")
        self.assertEqual(read(self.tmp, "housing.FCStd"), "v2 binary")

    def test_diff(self):
        write(self.tmp, "housing.FCStd", "v2")
        write(self.tmp, "housing.layers/dev-a41c.json", "{}")
        a = self.repo.history()[0].id
        b = self.repo.commit("layer", "rik").id
        detail = self.repo.diff_detail(a, b)
        self.assertEqual({d["path"]: d["kind"] for d in detail}, {"housing.FCStd": "modified", "housing.layers/dev-a41c.json": "added"})
        self.assertEqual(next(d for d in detail if "layer" in d)["layer"], "dev-a41c")


class MergeTest(RepoCase):
    def test_fast_forward(self):
        self.repo.create_workspace("feature")
        write(self.tmp, "housing.layers/dev-1.json", "{}")
        self.repo.commit("add layer", "claude")
        self.repo.checkout("Main")
        outcome = self.repo.merge("feature")
        self.assertTrue(outcome.fast_forward)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "housing.layers/dev-1.json")))
        self.assertEqual(self.repo.head("Main"), self.repo.head("feature"))

    def test_three_way_disjoint(self):
        self.repo.create_workspace("a")
        write(self.tmp, "housing.layers/dev-a.json", "{}")
        self.repo.commit("a", "x")
        self.repo.checkout("Main")
        self.repo.create_workspace("b")
        write(self.tmp, "project.contracts.json", "{}")
        self.repo.commit("b", "y")
        self.repo.checkout("Main")
        outcome = self.repo.merge("a")
        outcome = self.repo.merge("b", author="rik")
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.snapshot.parents), 2)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "housing.layers/dev-a.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "project.contracts.json")))
        self.assertEqual(self.repo.verify(), [])

    def test_binary_conflict_and_resolution(self):
        self.repo.create_workspace("a")
        write(self.tmp, "housing.FCStd", "A's model")
        self.repo.commit("a", "x")
        self.repo.checkout("Main")
        self.repo.create_workspace("b")
        write(self.tmp, "housing.FCStd", "B's model")
        self.repo.commit("b", "y")
        self.repo.checkout("Main")
        self.repo.merge("a")
        outcome = self.repo.merge("b")
        self.assertFalse(outcome.ok)
        self.assertEqual([c[0] for c in outcome.conflicts], ["housing.FCStd"])
        self.assertIsNone(outcome.snapshot)
        self.assertEqual(read(self.tmp, "housing.FCStd"), "A's model", "nothing touched on conflict")
        outcome = self.repo.merge("b", resolutions={"housing.FCStd": "theirs"})
        self.assertTrue(outcome.ok)
        self.assertEqual(read(self.tmp, "housing.FCStd"), "B's model")

    def test_layer_index_merges_by_union(self):
        idx = lambda order, enabled: json.dumps({"document": "housing.FCStd", "base": "r1", "order": order, "enabled": enabled})
        self.repo.create_workspace("a")
        write(self.tmp, "housing.layers/index.json", idx(["dev-a"], {"dev-a": True}))
        self.repo.commit("a", "x")
        self.repo.checkout("Main")
        self.repo.create_workspace("b")
        write(self.tmp, "housing.layers/index.json", idx(["dev-b"], {"dev-b": False}))
        self.repo.commit("b", "y")
        self.repo.checkout("Main")
        self.repo.merge("a")
        outcome = self.repo.merge("b")
        self.assertTrue(outcome.ok, outcome.to_dict())
        merged = json.loads(read(self.tmp, "housing.layers/index.json"))
        self.assertEqual(merged["order"], ["dev-a", "dev-b"])
        self.assertEqual(merged["enabled"], {"dev-a": True, "dev-b": False})
        self.assertIn(("housing.layers/index.json", "merged"), outcome.taken)

    def test_already_merged_and_dirty(self):
        self.repo.create_workspace("a")
        self.repo.checkout("Main")
        outcome = self.repo.merge("a")
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.fast_forward)
        write(self.tmp, "housing.FCStd", "dirty")
        with self.assertRaises(VcsError):
            self.repo.merge("a")


class ReleaseTest(RepoCase):
    def setUp(self):
        super().setUp()
        write(self.tmp, "housing.FCStd", "v2")
        self.repo.commit("v2", "rik")
        self.repo.create_version("V1", "rik")
        self.rm = ReleaseManager(self.repo)
        self.rm.set_policy(Policy(approvers=["lead", "qa"], prefix="HS-", width=4))

    def test_revisions(self):
        self.assertEqual(next_revision(None), "A")
        self.assertEqual(next_revision("A"), "B")
        self.assertEqual(next_revision("Z"), "AA")
        self.assertEqual(next_revision("AZ"), "BA")
        self.assertEqual(next_revision(None, "numeric"), "1")
        self.assertEqual(next_revision("4", "numeric"), "5")

    def test_numbers(self):
        p = self.rm.assign_number("Housing")
        self.assertEqual(p.number, "HS-0001")
        self.assertEqual(self.rm.assign_number("Housing").number, "HS-0001", "assigned once")
        self.assertEqual(self.rm.assign_number("Lid").number, "HS-0002")
        with self.assertRaises(VcsError):
            self.rm.assign_number("Other", number="HS-0002")
        self.assertEqual(self.rm.assign_number("Special", number="LEGACY-9").number, "LEGACY-9")

    def test_release_flow(self):
        with self.assertRaises(VcsError):
            self.rm.create_candidate("V9", ["Housing"], "rik")
        rc = self.rm.create_candidate("V1", ["Housing", "Lid"], "rik", notes="first shipment")
        self.assertEqual(rc.state, "pending")
        self.assertEqual(rc.number, "R001")
        with self.assertRaises(VcsError):
            self.rm.create_candidate("V1", ["Housing"], "rik")  # already pending
        with self.assertRaises(VcsError):
            self.rm.approve(rc.id, "stranger")
        with self.assertRaises(VcsError):
            self.rm.approve(rc.id, "rik")  # author, and not an approver
        rc = self.rm.approve(rc.id, "lead", "looks good")
        self.assertEqual(rc.state, "pending", "needs both approvers")
        rc = self.rm.approve(rc.id, "qa")
        self.assertEqual(rc.state, "released")
        parts = self.rm.parts()
        self.assertEqual(parts["Housing"].revision, "A")
        self.assertEqual(parts["Housing"].released, "V1")
        self.assertEqual([c.id for c in self.rm.where_used("Lid")], [rc.id])
        with self.assertRaises(VcsError):
            self.rm.approve(rc.id, "lead")
        bom = self.rm.bill_of_materials()
        self.assertEqual([(r["number"], r["revision"]) for r in bom], [("HS-0001", "A"), ("HS-0002", "A")])
        # second release advances the revision
        write(self.tmp, "housing.FCStd", "v3")
        self.repo.commit("v3", "rik")
        self.repo.create_version("V2", "rik")
        rc2 = self.rm.create_candidate("V2", ["Housing"], "rik")
        self.rm.approve(rc2.id, "lead")
        self.rm.approve(rc2.id, "qa")
        self.assertEqual(self.rm.part("Housing").revision, "B")
        self.assertEqual(self.rm.part("Lid").revision, "A")
        self.assertEqual(self.rm.bill_of_materials("V1")[0]["revision"], "A")
        obs = self.rm.obsolete(rc.id, "lead", "superseded by R002")
        self.assertEqual(obs.state, "obsolete")
        with self.assertRaises(VcsError):
            self.rm.obsolete(rc.id, "lead")

    def test_reject_and_reopen(self):
        rc = self.rm.create_candidate("V1", ["Housing"], "rik")
        rc = self.rm.reject(rc.id, "qa", "wall too thin")
        self.assertEqual(rc.state, "rejected")
        self.assertEqual(rc.log[-1]["comment"], "wall too thin")
        rc = self.rm.reopen(rc.id, "rik")
        self.assertEqual((rc.state, rc.approvals), ("pending", {}))
        with self.assertRaises(VcsError):
            self.rm.reopen(rc.id, "rik")

    def test_single_approver_policy(self):
        self.rm.set_policy(Policy(approvers=[]))
        rc = self.rm.create_candidate("V1", ["Housing"], "rik")
        self.assertEqual(self.rm.approve(rc.id, "anyone").state, "released")


class SyncTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.remote_dir = tempfile.mkdtemp(prefix="fcvcs-remote-")
        write(self.remote_dir, "housing.FCStd", "v1 binary")
        write(self.remote_dir, "housing.layers/index.json", read(self.tmp, "housing.layers/index.json"))
        # the hub starts from the same initial snapshot: copy the repository
        shutil.rmtree(os.path.join(self.remote_dir, ".fcvcs"), ignore_errors=True)
        shutil.copytree(os.path.join(self.tmp, ".fcvcs"), os.path.join(self.remote_dir, ".fcvcs"))
        self.remote = Repository(self.remote_dir)
        self.transport = LocalTransport(self.remote)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.remote_dir, ignore_errors=True)

    def test_push_pull_round_trip(self):
        write(self.tmp, "housing.FCStd", "v2")
        self.repo.commit("v2", "rik")
        self.repo.create_version("V1", "rik")
        report = push(self.repo, self.transport)
        self.assertEqual(report.snapshots, 1)
        self.assertEqual(report.blobs, 1)
        self.assertEqual(report.refs, ["Main", "version:V1"])
        self.assertEqual(self.remote.head("Main"), self.repo.head("Main"))
        self.assertIn("V1", self.remote.versions())
        self.assertEqual(self.remote.verify(), [])
        # a second machine pulls
        third_dir = tempfile.mkdtemp(prefix="fcvcs-third-")
        try:
            shutil.copytree(os.path.join(self.remote_dir, ".fcvcs"), os.path.join(third_dir, ".fcvcs"))
            third = Repository(third_dir)
            third._set_head("Main", self.repo.history()[-1].id)  # behind
            third._write("versions.json", {})
            report = pull(third, self.transport)
            self.assertTrue(report.fast_forward)
            self.assertEqual(third.head("Main"), self.repo.head("Main"))
            self.assertIn("V1", third.versions())
            third.checkout("Main", force=True)
            self.assertEqual(read(third_dir, "housing.FCStd"), "v2")
        finally:
            shutil.rmtree(third_dir, ignore_errors=True)

    def test_diverged_push_is_refused(self):
        write(self.tmp, "housing.FCStd", "mine")
        self.repo.commit("mine", "rik")
        write(self.remote_dir, "housing.FCStd", "theirs")
        self.remote.commit("theirs", "sam")
        report = push(self.repo, self.transport)
        self.assertEqual(report.diverged, ["Main"])
        self.assertNotEqual(self.remote.head("Main"), self.repo.head("Main"))
        report = pull(self.repo, self.transport)
        self.assertEqual(report.diverged, ["Main"])
        self.assertIn("remote/Main", self.repo.workspaces())
        outcome = self.repo.merge("remote/Main", resolutions={"housing.FCStd": "ours"})
        self.assertTrue(outcome.ok)
        report = push(self.repo, self.transport)
        self.assertEqual(report.refs, ["Main"])
        self.assertEqual(self.remote.head("Main"), self.repo.head("Main"))

    def test_json_transport(self):
        calls = []

        def handler(op, **kw):
            calls.append(op)
            return serve(self.remote, op, **kw)

        t = transport_from_json(handler)
        write(self.tmp, "housing.FCStd", "v2")
        self.repo.commit("v2", "rik")
        report = push(self.repo, t)
        self.assertEqual(report.refs, ["Main"])
        self.assertIn("put_blob", calls)
        self.assertEqual(self.remote.head("Main"), self.repo.head("Main"))
        with self.assertRaises(VcsError):
            serve(self.remote, "explode")


if __name__ == "__main__":
    unittest.main()
