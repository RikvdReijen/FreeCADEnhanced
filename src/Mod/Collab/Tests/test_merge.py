# SPDX-License-Identifier: LGPL-2.1-or-later
"""Merging two layers: the five conflict classes, and the two-pockets case."""

import unittest

from collab.contracts import Contract, ContractSet
from collab.evaluate import GeometryIssue, ScriptedEvaluator
from collab.merge import CONFLICT_CLASSES, merge
from collab.schema import (
    AddFeature,
    Anchor,
    Author,
    Criterion,
    EditSketch,
    Fingerprint,
    Intent,
    Layer,
    MoveFeature,
    RemoveFeature,
    SetParam,
    SetProperty,
    Validation,
)

from Tests.fixtures import flange


def mount_anchor(name="a_mount_face"):
    return Anchor(
        name,
        query={"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"},
        fingerprint=Fingerprint(area=1843.2, centroid_local=(30, 20, 12), surface="plane", adjacency=5),
        resolved_at_record="Face6",
    )


def pocket_layer(id, name, after="Boss1", depth=4.0, goal=None, criteria=(), anchor="a_mount_face"):
    return Layer(
        id,
        name=name,
        author=Author("agent", "claude", human_sponsor="rik"),
        base="8f2e19c4",
        intent=Intent(goal or name, success_criteria=list(criteria)),
        anchors={anchor: mount_anchor(anchor)},
        operations=[AddFeature("Pocket", name, after=after, sketch={"plane": f"@{anchor}"}, params={"Length": depth})],
    )


class WallEvaluator(ScriptedEvaluator):
    """States the geometric fact the merge has to discover: each pocket
    alone leaves 3 mm of wall; together they leave 0.4 mm."""

    def __init__(self):
        super().__init__(metrics_fn=self.m, issues_fn=self.i, bbox_fn=lambda doc: ((0, 0, 0), (60, 40, 32)))

    @staticmethod
    def pockets(doc):
        return [f for f in doc.features if f.kind == "Pocket"]

    def m(self, doc):
        n = len(self.pockets(doc))
        return {"mass_g": 100.0 - 15.0 * n, "min_wall_mm": {0: 12.0, 1: 3.0}.get(n, 0.4)}

    def i(self, doc):
        wall = self.m(doc)["min_wall_mm"]
        if wall < 2.5:
            return [GeometryIssue("thin_wall", f"minimum wall is {wall} mm, below 2.5 mm", [f.name for f in self.pockets(doc)], wall)]
        return []


class DisjointMergeTest(unittest.TestCase):
    def test_disjoint_layers_concatenate(self):
        left = Layer("a", base="8f2e19c4", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        right = Layer("b", base="8f2e19c4", operations=[SetParam("param:hole_spacing", 32.0, 36.0)])
        result = merge(flange(), left, right)
        self.assertTrue(result.ok, result.summary())
        self.assertTrue(result.disjoint)
        self.assertEqual(result.order, ["a", "b"])
        self.assertEqual(result.merged.id, "a+b")
        self.assertEqual(len(result.merged.operations), 2)
        self.assertEqual(result.merged_doc.feature("Boss1").params["Length"], 25.0)
        self.assertEqual(result.merged_doc.parameters["hole_spacing"], 36.0)
        self.assertFalse(result.geometry_evaluated)
        self.assertTrue(any("not evaluated" in w for w in result.warnings))
        self.assertEqual(result.merged.validation["recompute"], "structure_ok")
        self.assertEqual(result.merged.validation["self_intersection"], "not_evaluated")
        self.assertIn("NOT evaluated", result.summary())

    def test_order_is_swapped_when_right_creates_what_left_needs(self):
        left = Layer("a", base="8f2e19c4", operations=[SetParam("NewPad.Length", 5.0, 6.0)])
        right = Layer("b", base="8f2e19c4", operations=[AddFeature("Pad", "NewPad", after="Fillet2", params={"Length": 5.0})])
        result = merge(flange(), left, right)
        # left alone cannot replay: NewPad does not exist on the base.
        self.assertFalse(result.ok)
        self.assertEqual(result.conflicts[0].cls, "reference")
        self.assertEqual(result.conflicts[0].kind, "replay:missing_target")

    def test_identical_set_param_is_kept_once(self):
        left = Layer("a", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        right = Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        result = merge(flange(), left, right)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(len(result.merged.operations), 1)
        self.assertTrue(any("kept once" in w for w in result.warnings))

    def test_merged_layer_provenance(self):
        left = pocket_layer("a", "P1", criteria=[Criterion("mass_g", "<=", 90)])
        right = Layer("b", author=Author("human", "rik"), base="8f2e19c4", intent=Intent("thicker boss"),
                      operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        result = merge(flange(), left, right)
        self.assertTrue(result.ok, result.summary())
        merged = result.merged
        self.assertEqual(merged.author.id, "collab.merge")
        self.assertEqual(merged.author.human_sponsor, "rik")
        self.assertEqual(merged.intent.goal, "P1; thicker boss")
        self.assertEqual([c.describe() for c in merged.intent.success_criteria], ["mass_g <= 90"])
        self.assertEqual(list(merged.anchors), ["a_mount_face"])
        self.assertEqual(merged.base, "8f2e19c4")
        self.assertEqual(merged.validation.evaluated_at, "8f2e19c4+a+b")


class ReferenceConflictTest(unittest.TestCase):
    def test_lost_anchor(self):
        base = flange()
        base.entities = [e for e in base.entities if e.owner != "Pad3"]
        result = merge(base, pocket_layer("a", "P1"), Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)]))
        self.assertFalse(result.ok)
        self.assertEqual(result.conflicts[0].cls, "reference")
        self.assertEqual(result.conflicts[0].kind, "replay:anchor_lost")
        self.assertEqual(result.conflicts[0].detail["resolution"]["recorded_name"], "Face6")
        self.assertIn("re-anchor", result.conflicts[0].handling)

    def test_removed_dependency(self):
        left = Layer("a", operations=[RemoveFeature("Fillet2")])
        right = Layer("b", operations=[SetParam("Fillet2.Radius", 2.0, 1.0)])
        result = merge(flange(), left, right)
        kinds = {(c.cls, c.kind) for c in result.conflicts}
        self.assertIn(("reference", "removed_dependency"), kinds)

    def test_name_collision(self):
        result = merge(flange(), pocket_layer("a", "P1"), pocket_layer("b", "P1", after="Fillet2"))
        self.assertIn(("reference", "name_collision"), {(c.cls, c.kind) for c in result.conflicts})

    def test_anchor_collision(self):
        left = pocket_layer("a", "P1")
        right = pocket_layer("b", "P2", after="Fillet2")
        right.anchors["a_mount_face"].query["select"] = "smallest_area"
        result = merge(flange(), left, right)
        # Both replay fine on their own (the query is unambiguous either way for
        # a single +Z Pad3 face), so the collision is what stops the merge.
        self.assertIn(("reference", "anchor_collision"), {(c.cls, c.kind) for c in result.conflicts})


class OrderConflictTest(unittest.TestCase):
    def test_same_insertion_position(self):
        result = merge(flange(), pocket_layer("a", "P1"), pocket_layer("b", "P2"))
        self.assertFalse(result.ok)
        order = result.conflicts_of("order")
        self.assertEqual(len(order), 1)
        self.assertEqual(order[0].target, "Boss1")
        self.assertEqual(len(order[0].detail["options"]), 2)
        self.assertIn("choose an order", order[0].handling)

    def test_different_positions_merge(self):
        result = merge(flange(), pocket_layer("a", "P1", after="Boss1"), pocket_layer("b", "P2", after="Fillet2"))
        self.assertTrue(result.ok, result.summary())
        names = [f.name for f in result.merged_doc.features]
        self.assertLess(names.index("P1"), names.index("P2"))

    def test_move_under_an_insertion(self):
        left = Layer("a", operations=[MoveFeature("MountPlane", after="Pad3")])
        right = Layer("b", operations=[AddFeature("Pad", "X", after="MountPlane")])
        result = merge(flange(), left, right)
        self.assertIn(("order", "moved_anchor_position"), {(c.cls, c.kind) for c in result.conflicts})


class ParametricConflictTest(unittest.TestCase):
    def test_two_values(self):
        left = Layer("a", intent=Intent("lighter"), operations=[SetParam("Fillet2.Radius", 2.0, 1.2)])
        right = Layer("b", intent=Intent("stronger"), operations=[SetParam("Fillet2.Radius", 2.0, 3.0)])
        result = merge(flange(), left, right)
        self.assertFalse(result.ok)
        conflicts = result.conflicts_of("parametric")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].kind, "value")
        self.assertEqual(conflicts[0].detail["left"]["intent"], "lighter")
        self.assertEqual(conflicts[0].detail["right"]["intent"], "stronger")
        self.assertIn("human picks", conflicts[0].handling)

    def test_someone_else_moved_it(self):
        # Both against the same base, but right recorded a stale 'from'.
        left = Layer("a", operations=[SetParam("Fillet2.Radius", 2.0, 1.2)])
        right = Layer("b", operations=[SetParam("Fillet2.Radius", 1.5, 1.2)])
        result = merge(flange(), left, right)
        # right cannot even replay on the base: its 'from' does not match.
        self.assertEqual(result.conflicts[0].cls, "parametric")
        self.assertEqual(result.conflicts[0].kind, "replay:param_moved")

    def test_property_conflict(self):
        left = Layer("a", operations=[SetProperty("Pad3", "Label", None, "Base")])
        right = Layer("b", operations=[SetProperty("Pad3", "Label", None, "Block")])
        result = merge(flange(), left, right)
        self.assertEqual([c.kind for c in result.conflicts_of("parametric")], ["property"])


class IntentConflictTest(unittest.TestCase):
    def test_two_sketch_edits_are_refused(self):
        left = Layer("a", operations=[EditSketch("BossSketch", geometry=[{"add": "circle"}])])
        right = Layer("b", operations=[EditSketch("BossSketch", constraints=[{"add": "radius"}])])
        result = merge(flange(), left, right)
        self.assertFalse(result.ok)
        intent = result.conflicts_of("intent")
        self.assertEqual(len(intent), 1)
        self.assertEqual(intent[0].kind, "sketch")
        self.assertIn("level 4", intent[0].message)

    def test_sketch_edits_on_different_sketches_merge(self):
        left = Layer("a", operations=[EditSketch("BossSketch", geometry=[{"add": "circle"}])])
        right = Layer("b", operations=[EditSketch("Sketch", constraints=[{"add": "radius"}])])
        result = merge(flange(), left, right)
        self.assertTrue(result.ok, result.summary())


class GeometricConflictTest(unittest.TestCase):
    """Two pockets that individually leave 3 mm of wall together leave 0.4 mm."""

    def layers(self):
        left = pocket_layer("dev-a41c", "LightenPocket1", after="Boss1",
                            criteria=[Criterion("mass_g", "<=", 90), Criterion("min_wall_mm", ">=", 2.5)])
        right = pocket_layer("dev-93b7", "LightenPocket2", after="Fillet2",
                             criteria=[Criterion("min_wall_mm", ">=", 2.5)])
        return left, right

    def test_structural_evaluator_cannot_see_it(self):
        left, right = self.layers()
        result = merge(flange(), left, right)
        self.assertTrue(result.ok, "with no geometry, the merge is syntactically clean — and says so")
        self.assertFalse(result.geometry_evaluated)
        self.assertTrue(all(c.unknown for c in result.criteria))

    def test_scripted_evaluator_finds_it(self):
        left, right = self.layers()
        result = merge(flange(), left, right, evaluator=WallEvaluator())
        self.assertFalse(result.ok)
        self.assertTrue(result.geometry_evaluated)
        kinds = [(c.cls, c.kind) for c in result.conflicts]
        self.assertIn(("geometric", "thin_wall"), kinds)
        self.assertIn(("geometric", "criteria_regression"), kinds)
        regressions = [c for c in result.criteria if c.regressed]
        self.assertEqual({(c.layer, c.criterion.metric) for c in regressions},
                         {("dev-a41c", "min_wall_mm"), ("dev-93b7", "min_wall_mm")})
        mass = next(c for c in result.criteria if c.criterion.metric == "mass_g")
        self.assertTrue(mass.alone and mass.together)
        self.assertEqual(result.metrics["min_wall_mm"], 0.4)
        self.assertIn("REGRESSED", result.summary())
        self.assertIsNotNone(result.merged, "the merged layer is still produced for a human to look at")
        self.assertEqual(result.merged.validation["self_intersection"], "found")

    def test_geometric_issue_present_alone_is_not_a_merge_conflict(self):
        class Always(WallEvaluator):
            def i(self, doc):
                return [GeometryIssue("note", "always", [])]

        left, right = self.layers()
        result = merge(flange(), left, right, evaluator=Always())
        # The criteria still regress (the metrics say 0.4 mm); the *issue*
        # that both sides already had is not reported as new.
        self.assertNotIn("note", [c.kind for c in result.conflicts_of("geometric")])

    def test_criterion_failing_alone_is_a_warning_not_a_regression(self):
        left = pocket_layer("a", "P1", criteria=[Criterion("mass_g", "<=", 10)])
        right = Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        result = merge(flange(), left, right, evaluator=WallEvaluator())
        self.assertTrue(result.ok, result.summary())
        self.assertTrue(any("already failed alone" in w for w in result.warnings))


class ContractsAndPinnedTest(unittest.TestCase):
    def test_contract_violation_is_geometric(self):
        contracts = ContractSet(contracts=[Contract("flange", budget={"mass_g": 60}, envelope={"bbox": [60, 40, 30]})])
        left = pocket_layer("a", "P1")
        right = Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        result = merge(flange(), left, right, evaluator=WallEvaluator(), contracts=contracts)
        kinds = {(c.cls, c.kind) for c in result.conflicts}
        self.assertIn(("geometric", "contract:budget.mass_g"), kinds)
        self.assertIn(("geometric", "contract:envelope.bbox"), kinds)
        self.assertEqual(result.merged.validation["contracts"], "fail")

    def test_contract_pass(self):
        contracts = ContractSet(contracts=[Contract("flange", budget={"mass_g": 200})])
        result = merge(flange(), pocket_layer("a", "P1"), Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)]),
                       evaluator=WallEvaluator(), contracts=contracts)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.merged.validation["contracts"], "pass")

    def test_pinned_escalates_even_when_clean(self):
        contracts = ContractSet(pinned=["param:hole_spacing"])
        left = Layer("a", operations=[SetParam("param:hole_spacing", 32.0, 36.0)])
        right = Layer("b", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        result = merge(flange(), left, right, contracts=contracts)
        self.assertFalse(result.ok)
        self.assertEqual(result.conflicts, [])
        self.assertEqual(len(result.escalations), 1)
        self.assertIn("pinned", result.escalations[0])
        self.assertEqual(result.merged.pinned_touched, ["param:hole_spacing"])

    def test_declared_pinned_touched_is_honoured(self):
        left = Layer("a", operations=[SetParam("Boss1.Length", 20.0, 25.0)], pinned_touched=["Boss1.Length"])
        right = Layer("b", operations=[SetParam("param:hole_spacing", 32.0, 36.0)])
        result = merge(flange(), left, right, contracts=ContractSet())
        self.assertEqual(len(result.escalations), 1)


class StaleValidationTest(unittest.TestCase):
    def test_stale_validation_is_warned(self):
        left = Layer("a", base="8f2e19c4", operations=[SetParam("Boss1.Length", 20.0, 25.0)],
                     validation=Validation({"mass_g": 79.4, "evaluated_at": "8f2e19c4+dev-93b7"}))
        right = Layer("b", base="deadbeef", operations=[SetParam("param:hole_spacing", 32.0, 36.0)])
        result = merge(flange(), left, right)
        self.assertTrue(any("stale" in w for w in result.warnings))
        self.assertTrue(any("recorded against 'deadbeef'" in w for w in result.warnings))


class ResultShapeTest(unittest.TestCase):
    def test_json_and_classes(self):
        self.assertEqual(CONFLICT_CLASSES, ("reference", "order", "parametric", "geometric", "intent"))
        result = merge(flange(), pocket_layer("a", "P1"), pocket_layer("b", "P2"))
        data = result.to_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["conflicts"][0]["class"], "order")
        self.assertIn("handling", data["conflicts"][0])


if __name__ == "__main__":
    unittest.main()
