# SPDX-License-Identifier: LGPL-2.1-or-later
"""The command line, end to end on a temporary project."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from collab.cli import main
from collab.contracts import Contract, ContractSet
from collab.schema import AddFeature, Anchor, Author, Claims, Criterion, Fingerprint, Intent, Layer, SetParam

from Tests.fixtures import flange


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="collab-cli-")
        self.doc = os.path.join(self.tmp, "flange.FCStd")
        open(self.doc, "wb").close()
        self.model = os.path.join(self.tmp, "flange.model.json")
        with open(self.model, "w") as handle:
            json.dump(flange().to_json(), handle)
        anchor = Anchor(
            "a_mount_face",
            query={"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"},
            fingerprint=Fingerprint(area=1843.2, centroid_local=(30, 20, 12), surface="plane", adjacency=5),
            resolved_at_record="Face6",
        )
        self.a = Layer(
            "dev-a41c", name="Lighten", author=Author("agent", "claude", human_sponsor="rik"), base="8f2e19c4",
            intent=Intent("lighter", success_criteria=[Criterion("mass_g", "<=", 90)]),
            claims=Claims(modifies=["LightenPocket1", "Fillet2"]),
            anchors={"a_mount_face": anchor},
            operations=[
                AddFeature("Pocket", "LightenPocket1", after="Boss1", sketch={"plane": "@a_mount_face"}, params={"Length": 4.0}),
                SetParam("Fillet2.Radius", 2.0, 1.2),
            ],
        )
        self.b = Layer(
            "dev-93b7", name="Taller boss", author=Author("human", "rik"), base="8f2e19c4",
            claims=Claims(modifies=["Boss1"]),
            operations=[SetParam("Boss1.Length", 20.0, 25.0)],
        )
        self.c = Layer(
            "dev-c0de", name="Conflicting fillet", author=Author("human", "sam"), base="8f2e19c4",
            claims=Claims(modifies=["Fillet2"]),
            operations=[SetParam("Fillet2.Radius", 2.0, 3.0)],
        )
        for layer in (self.a, self.b, self.c):
            with open(os.path.join(self.tmp, layer.id + ".json"), "w") as handle:
                handle.write(layer.dumps())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, layer):
        return os.path.join(self.tmp, layer.id + ".json")

    def test_workflow(self):
        code, out, _ = run("init", self.doc, "--base", "8f2e19c4")
        self.assertEqual(code, 0, out)
        code, out, _ = run("list", self.doc)
        self.assertIn("(no layers)", out)

        self.assertEqual(run("add", self.doc, self.path(self.a))[0], 0)
        self.assertEqual(run("add", self.doc, self.path(self.b))[0], 0)
        code, out, _ = run("list", self.doc)
        self.assertEqual(code, 0)
        self.assertIn("[on ] dev-a41c", out)
        self.assertIn("Taller boss", out)

        code, out, _ = run("show", self.doc, "dev-a41c")
        self.assertIn("anchor a_mount_face", out)
        self.assertIn("intent: lighter", out)

        code, out, _ = run("resolve", self.doc, "dev-a41c", "--model", self.model)
        self.assertEqual(code, 0, out)
        self.assertIn("Resolved('a_mount_face' -> 'Face6'", out)

        code, out, _ = run("replay", self.doc, "--model", self.model)
        self.assertEqual(code, 0, out)
        self.assertIn("dev-a41c: ok", out)
        self.assertIn("dev-93b7: ok -> 8f2e19c4+dev-a41c+dev-93b7", out)

        self.assertEqual(run("disable", self.doc, "dev-93b7")[0], 0)
        code, out, _ = run("replay", self.doc)  # model found by convention
        self.assertIn("dev-93b7: muted", out)
        self.assertEqual(run("enable", self.doc, "dev-93b7")[0], 0)

        code, out, _ = run("move", self.doc, "dev-93b7", "--before", "dev-a41c")
        self.assertIn("order: dev-93b7, dev-a41c", out)

        code, out, _ = run("diff", self.doc, "--model", self.model)
        self.assertIn("added:   LightenPocket1", out)
        self.assertIn("Boss1.Length: 20.0 -> 25.0", out)
        self.assertIn("not measured", out)

        code, out, _ = run("check", self.doc)
        self.assertEqual((code, out.strip()), (0, "ok"))

    def test_merge_clean_and_conflicting(self):
        run("init", self.doc, "--base", "8f2e19c4")
        for layer in (self.a, self.b, self.c):
            run("add", self.doc, self.path(layer))
        merged = os.path.join(self.tmp, "merged.json")
        code, out, _ = run("merge", self.doc, "dev-a41c", "dev-93b7", "--model", self.model, "--write", merged)
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)
        self.assertIn("NOT evaluated", out)
        self.assertTrue(os.path.isfile(merged))
        with open(merged) as handle:
            self.assertEqual(Layer.loads(handle.read()).id, "dev-a41c+dev-93b7")

        code, out, _ = run("merge", self.doc, "dev-a41c", "dev-c0de", "--model", self.model)
        self.assertEqual(code, 1)
        self.assertIn("conflict [parametric/value]", out)
        self.assertIn("human picks", out)

        code, out, _ = run("--json", "merge", self.doc, "dev-a41c", "dev-c0de", "--model", self.model)
        data = json.loads(out)
        self.assertFalse(data["ok"])
        self.assertEqual(data["conflicts"][0]["class"], "parametric")

    def test_pinned_escalates_via_contracts_file(self):
        run("init", self.doc, "--base", "8f2e19c4")
        ContractSet(contracts=[Contract("flange")], pinned=["Fillet2.Radius"]).save(os.path.join(self.tmp, "project.contracts.json"))
        run("add", self.doc, self.path(self.a))
        run("add", self.doc, self.path(self.b))
        code, out, _ = run("merge", self.doc, "dev-a41c", "dev-93b7", "--model", self.model)
        self.assertEqual(code, 1)
        self.assertIn("escalate: Fillet2.Radius is pinned", out)
        code, out, _ = run("replay", self.doc, "--model", self.model)
        self.assertIn("pinned: Fillet2.Radius", out)

    def test_claims(self):
        run("init", self.doc, "--base", "8f2e19c4")
        run("add", self.doc, self.path(self.a))
        run("add", self.doc, self.path(self.c))
        code, out, _ = run("claim", self.doc, "dev-a41c", "--exclusive")
        self.assertEqual(code, 0, out)
        code, out, _ = run("claim", self.doc, "dev-c0de")
        self.assertEqual(code, 1)
        self.assertIn("block:", out)
        self.assertEqual(run("claim", self.doc, "dev-c0de", "--force")[0], 0)
        self.assertEqual(run("claim", self.doc, "dev-a41c", "--release")[0], 0)

    def test_rebase(self):
        run("init", self.doc, "--base", "8f2e19c4")
        run("add", self.doc, self.path(self.a))
        renumbered = os.path.join(self.tmp, "new.model.json")
        with open(renumbered, "w") as handle:
            json.dump(flange(revision="9a9a9a9a", renumber=3).to_json(), handle)
        out_path = os.path.join(self.tmp, "rebased.json")
        code, out, _ = run("rebase", self.doc, "dev-a41c", "--model", renumbered, "--out", out_path)
        self.assertEqual(code, 0, out)
        with open(out_path) as handle:
            layer = Layer.loads(handle.read())
        self.assertEqual(layer.base, "9a9a9a9a")
        self.assertEqual(layer.anchors["a_mount_face"].resolved_at_record, "Face9")

    def test_validate_and_add_refusals(self):
        code, out, err = run("validate", self.path(self.a), self.path(self.b))
        self.assertEqual(code, 0, err)
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as handle:
            handle.write('{"id": "bad", "operations": [{"op": "set_param", "target": "X", "to": 1}]}')
        code, out, err = run("validate", bad)
        self.assertEqual(code, 1)
        self.assertIn("from", err)

        run("init", self.doc, "--base", "8f2e19c4")
        sloppy = Layer("sloppy", author=Author("human", "x"), base="8f2e19c4", claims=Claims(modifies=["Nothing"]),
                       operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        with open(self.path(sloppy), "w") as handle:
            handle.write(sloppy.dumps())
        code, out, err = run("add", self.doc, self.path(sloppy))
        self.assertEqual(code, 2)
        self.assertIn("undeclared", err)
        self.assertEqual(run("add", self.doc, self.path(sloppy), "--force")[0], 0)

    def test_errors_are_exit_2(self):
        code, out, err = run("list", self.doc)
        self.assertEqual(code, 2)
        self.assertIn("init first", err)
        run("init", self.doc, "--base", "r")
        os.unlink(self.model)  # nothing to find by convention either
        code, out, err = run("replay", self.doc)
        self.assertEqual(code, 2)
        self.assertIn("--model", err)


if __name__ == "__main__":
    unittest.main()
