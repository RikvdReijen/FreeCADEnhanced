# SPDX-License-Identifier: LGPL-2.1-or-later
"""Claims, target paths and contracts."""

import os
import shutil
import tempfile
import unittest

from collab import targets
from collab.claims import ClaimRegistry, derive_claims, undeclared_targets
from collab.contracts import Contract, ContractSet, KeepOut, Mating, Violation
from collab.errors import LayerFormatError
from collab.schema import AddFeature, Anchor, Claims, Dependency, Layer, SetParam

from Tests.fixtures import flange


class TargetsTest(unittest.TestCase):
    def test_covers(self):
        self.assertTrue(targets.covers("Body.Flange", "Body.Flange.Sketch"))
        self.assertTrue(targets.covers("Body", "Body.Flange.Fillet2.Radius"))
        self.assertTrue(targets.covers("Pad3", "Pad3.Length"))
        self.assertTrue(targets.covers("Pad3", "Body.Pad3"))
        self.assertFalse(targets.covers("Body.Flange", "Body.Housing"))
        self.assertFalse(targets.covers("param:a", "param:ab"))
        self.assertTrue(targets.covers("param:a", "param:a"))
        self.assertFalse(targets.covers("Pad3", "param:Pad3"))

    def test_split_against_document(self):
        doc = flange()
        self.assertEqual(targets.split("Body.Pad3.Length", doc), ("Pad3", "Length"))
        self.assertEqual(targets.split("Pad3", doc), ("Pad3", ""))
        self.assertEqual(targets.split("param:wall_min", doc), (None, "wall_min"))
        self.assertEqual(targets.split("Nope.Length", doc), (None, "Nope.Length"))

    def test_feature_of_without_document(self):
        self.assertEqual(targets.feature_of("Pad3"), "Pad3")
        self.assertEqual(targets.feature_of("Body.Fillet2.Radius"), "Fillet2")
        self.assertIsNone(targets.feature_of("param:x"))


class ClaimRegistryTest(unittest.TestCase):
    def test_advisory_overlap_warns_and_registers(self):
        reg = ClaimRegistry()
        self.assertEqual(reg.register("a", Claims(modifies=["Body.Flange"])), [])
        issues = reg.register("b", Claims(modifies=["Body.Flange.Sketch"]))
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].kind, "overlap")
        self.assertEqual(issues[0].other, "a")
        self.assertIn("b", reg)

    def test_exclusive_blocks(self):
        reg = ClaimRegistry()
        reg.register("a", Claims(modifies=["Body.Flange"], mode="exclusive"))
        issues = reg.register("b", Claims(modifies=["Body.Flange.Sketch"]))
        self.assertTrue(issues[0].blocking)
        self.assertNotIn("b", reg)
        reg.register("b", Claims(modifies=["Body.Flange.Sketch"]), force=True)
        self.assertIn("b", reg)

    def test_dependency_warnings_both_directions(self):
        reg = ClaimRegistry()
        reg.register("a", Claims(modifies=["Boss1"], depends=[Dependency(param="hole_spacing", reason="bolt pattern")]))
        issues = reg.register("b", Claims(modifies=["param:hole_spacing"], depends=[Dependency(param="wall_min")]))
        kinds = {(i.kind, i.layer) for i in issues}
        self.assertIn(("dependency_threatened", "a"), kinds)
        # a does not modify wall_min, so b gets no dependency_claimed.
        self.assertNotIn(("dependency_claimed", "b"), kinds)

        issues = reg.check("c", Claims(modifies=["X"], depends=[Dependency(param="hole_spacing")]))
        self.assertEqual([i.kind for i in issues], ["dependency_claimed"])
        self.assertEqual(issues[0].other, "b")

    def test_notify_change(self):
        reg = ClaimRegistry()
        reg.register("a", Claims(modifies=["Boss1"], depends=[Dependency(anchor="a_mount_face", reason="depth")]))
        reg.register("b", Claims(modifies=["Pad3"]))
        issues = reg.notify_change(["anchor:a_mount_face"], by_layer="b")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].layer, "a")
        self.assertEqual(issues[0].kind, "dependency_changed")
        self.assertEqual(reg.notify_change(["anchor:a_mount_face"], by_layer="a"), [])
        self.assertEqual(reg.watchers_of("param:nothing"), [])

    def test_persistence(self):
        tmp = tempfile.mkdtemp()
        try:
            reg = ClaimRegistry()
            reg.register("a", Claims(modifies=["Boss1"], depends=[Dependency(param="p", reason="r")], mode="exclusive"))
            path = os.path.join(tmp, "claims.json")
            reg.save(path)
            again = ClaimRegistry.load(path)
            self.assertEqual(again.to_json(), reg.to_json())
            self.assertEqual(again.claims("a").mode, "exclusive")
        finally:
            shutil.rmtree(tmp)
        with self.assertRaises(LayerFormatError):
            ClaimRegistry.from_json([])

    def test_release(self):
        reg = ClaimRegistry()
        reg.register("a", Claims(modifies=["X"]))
        reg.release("a")
        self.assertEqual(len(reg), 0)


class HonestClaimsTest(unittest.TestCase):
    def test_undeclared_targets(self):
        layer = Layer(
            "x",
            claims=Claims(modifies=["Body.Flange"]),
            operations=[SetParam("Body.Flange.Fillet2.Radius", 2.0, 1.2), SetParam("Body.Housing.Wall", 3, 4)],
        )
        self.assertEqual(undeclared_targets(layer), ["Body.Housing.Wall"])

    def test_derive_claims(self):
        layer = Layer(
            "x",
            anchors={"a": Anchor("a", query={"face_of": "Pad3"})},
            operations=[AddFeature("Pocket", "P1", after="Pad3", sketch={"plane": "@a"})],
        )
        claims = derive_claims(layer, mode="exclusive")
        self.assertEqual(claims.modifies, ["P1"])
        self.assertEqual([d.key for d in claims.depends], ["anchor:a"])
        self.assertEqual(claims.mode, "exclusive")
        self.assertEqual(undeclared_targets(Layer("y", claims=claims, operations=layer.operations, anchors=layer.anchors)), [])


class ContractTest(unittest.TestCase):
    def contracts(self):
        return ContractSet(
            contracts=[
                Contract(
                    "motor_mount",
                    mating=[Mating("face_A", datum="MountPlane", bolts="M4x4 @ 32mm PCD")],
                    keep_out=[KeepOut("shaft_sweep", "cylinder", r=14, h=40)],
                    envelope={"bbox": [80, 80, 25]},
                    budget={"mass_g": 120, "material": "AlSi10Mg"},
                ),
                Contract("bracket", keep_out=[KeepOut("clip", "box", size=[10, 10, 10])]),
            ],
            pinned=["param:safety_factor", "Body.Flange.Material"],
        )

    def test_round_trip(self):
        cs = self.contracts()
        again = ContractSet.from_json(cs.to_json())
        self.assertEqual(again.to_json(), cs.to_json())
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "project.contracts.json")
            cs.save(path)
            loaded = ContractSet.load(path)
            self.assertEqual(loaded.to_json(), cs.to_json())
            self.assertEqual(loaded.path, path)
        finally:
            shutil.rmtree(tmp)

    def test_budget_and_envelope(self):
        cs = self.contracts()
        violations, skipped = cs.check("motor_mount", metrics={"mass_g": 131.0, "material": "PLA"}, bbox=((0, 0, 0), (81, 60, 20)))
        rules = sorted(v.rule for v in violations)
        self.assertEqual(rules, ["budget.mass_g", "budget.material", "envelope.bbox"])
        self.assertTrue(any("keep-out" in s for s in skipped), "keep-outs unchecked without an evaluator")
        violations, skipped = cs.check("motor_mount", metrics={"mass_g": 100}, bbox=None)
        self.assertEqual(violations, [])
        self.assertTrue(any("envelope" in s for s in skipped))

    def test_unknown_part_has_nothing_to_check(self):
        self.assertEqual(self.contracts().check("nope", {}), ([], []))

    def test_pinned(self):
        cs = self.contracts()
        self.assertTrue(cs.is_pinned("param:safety_factor"))
        self.assertTrue(cs.is_pinned("Body.Flange.Material"))
        self.assertFalse(cs.is_pinned("Body.Flange.Fillet2.Radius"))
        layer = Layer("x", operations=[SetParam("Body.Flange.Material", "Al", "PLA")], pinned_touched=["param:other"])
        self.assertEqual(cs.pinned_touched_by(layer), ["param:other", "Body.Flange.Material"])

    def test_breaking_changes(self):
        old = self.contracts().get("motor_mount")
        new = Contract.from_json(old.to_json())
        new.mating[0].bolts = "M5x4 @ 32mm PCD"
        new.keep_out.append(KeepOut("fan", "sphere", r=20))
        new.budget["mass_g"] = 110
        changes = new.breaking_changes_from(old)
        self.assertIn("mating feature 'face_A' changed", changes)
        self.assertIn("keep-out 'fan' added", changes)
        self.assertIn("budget mass_g changed from 120 to 110", changes)

    def test_bad_shapes(self):
        with self.assertRaises(LayerFormatError):
            KeepOut("k", "torus")
        with self.assertRaises(LayerFormatError):
            Contract.from_json({"mating": []})
        self.assertEqual(Violation("p", "r", "m").to_json()["severity"], "error")


if __name__ == "__main__":
    unittest.main()
