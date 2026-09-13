# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replaying layers, the stack, rebasing, the geometric diff."""

import unittest

from collab.contracts import ContractSet
from collab.evaluate import ScriptedEvaluator, StructuralEvaluator, check_structure
from collab.replay import GEOMETRIC_PROPERTIES, rebase, replay, replay_stack
from collab.schema import (
    AddDatum,
    AddFeature,
    Anchor,
    EditSketch,
    Fingerprint,
    Layer,
    MoveFeature,
    RemoveFeature,
    SetParam,
    SetProperty,
)
from collab.stack import evaluate_stack, geometric_diff
from collab.store import Index

from Tests.fixtures import flange


def mount_anchor():
    return Anchor(
        "a_mount_face",
        query={"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"},
        fingerprint=Fingerprint(area=1843.2, centroid_local=(30, 20, 12), surface="plane", adjacency=5),
        resolved_at_record="Face6",
    )


def lighten_layer(base="8f2e19c4"):
    return Layer(
        "dev-a41c",
        base=base,
        anchors={"a_mount_face": mount_anchor()},
        operations=[
            AddFeature("Pocket", "LightenPocket1", after="Boss1", sketch={"plane": "@a_mount_face"}, params={"Length": 4.0}),
            SetParam("Fillet2.Radius", 2.0, 1.2),
        ],
    )


class ReplayTest(unittest.TestCase):
    def test_happy_path(self):
        base = flange()
        result = replay(lighten_layer(), base)
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.applied, [0, 1])
        doc = result.doc
        self.assertEqual(doc.revision, "8f2e19c4+dev-a41c")
        pocket = doc.feature("LightenPocket1")
        self.assertEqual(pocket.params["sketch"]["plane"], "Face6")
        self.assertIn("Pad3", pocket.depends_on)  # owner of the anchored face
        self.assertIn("Boss1", pocket.depends_on)  # inserted after it
        self.assertEqual(doc.index_of("LightenPocket1"), doc.index_of("Boss1") + 1)
        self.assertEqual(doc.feature("Fillet2").params["Radius"], 1.2)
        self.assertEqual(result.changed, ["LightenPocket1", "Fillet2.Radius"])
        self.assertEqual(result.recompute.status, "structure_ok")
        # The base was not touched.
        self.assertIsNone(base.feature("LightenPocket1"))
        self.assertEqual(base.feature("Fillet2").params["Radius"], 2.0)

    def test_replays_after_renumbering(self):
        result = replay(lighten_layer(), flange(renumber=3))
        self.assertTrue(result.ok)
        self.assertEqual(result.doc.feature("LightenPocket1").params["sketch"]["plane"], "Face9")

    def test_param_moved_is_reported(self):
        base = flange()
        base.feature("Fillet2").params["Radius"] = 1.5
        result = replay(lighten_layer(), base)
        self.assertFalse(result.ok)
        self.assertEqual([f.kind for f in result.failures], ["param_moved"])
        self.assertIn("someone else moved it", result.failures[0].message)
        self.assertEqual(result.applied, [0])

    def test_param_moved_even_when_target_agrees(self):
        base = flange()
        base.feature("Fillet2").params["Radius"] = 1.2  # already at the target value
        result = replay(Layer("x", operations=[SetParam("Fillet2.Radius", 2.0, 1.2)]), base)
        self.assertEqual([f.kind for f in result.failures], ["param_moved"])

    def test_lost_anchor_stops_replay(self):
        base = flange()
        base.entities = [e for e in base.entities if e.owner != "Pad3"]
        result = replay(lighten_layer(), base)
        self.assertFalse(result.ok)
        self.assertEqual(result.failures[0].kind, "anchor_lost")
        self.assertIn("was Face6", result.failures[0].message)
        self.assertEqual(result.applied, [])
        self.assertEqual(result.resolutions["a_mount_face"].status, "lost")

    def test_stop_on_failure_false_continues(self):
        base = flange()
        base.entities = [e for e in base.entities if e.owner != "Pad3"]
        result = replay(lighten_layer(), base, stop_on_failure=False)
        self.assertEqual(result.applied, [1])
        self.assertEqual([f.kind for f in result.failures], ["anchor_lost"])

    def test_remove_with_dependents_refused(self):
        result = replay(Layer("x", operations=[RemoveFeature("Pad3")]), flange())
        self.assertEqual(result.failures[0].kind, "dependents_exist")
        self.assertIn("Boss1", result.failures[0].message)

    def test_remove_leaf(self):
        result = replay(Layer("x", operations=[RemoveFeature("Fillet2")]), flange())
        self.assertTrue(result.ok)
        self.assertIsNone(result.doc.feature("Fillet2"))
        self.assertEqual(result.doc.entities_of("Fillet2"), [])

    def test_move_feature(self):
        result = replay(Layer("x", operations=[MoveFeature("MountPlane", after="Pad3")]), flange())
        self.assertTrue(result.ok)
        doc = result.doc
        self.assertEqual(doc.index_of("MountPlane"), doc.index_of("Pad3") + 1)
        bad = replay(Layer("x", operations=[MoveFeature("Pad3", after="Boss1")]), flange())
        self.assertEqual(bad.failures[0].kind, "bad_position")

    def test_move_before_dependency_fails_recompute(self):
        # Fillet2 depends on Boss1; put it at the head of the tree.
        result = replay(Layer("x", operations=[MoveFeature("Fillet2", after=None)]), flange())
        self.assertFalse(result.ok)
        self.assertEqual(result.failures[0].kind, "recompute")
        self.assertIn("comes after it", result.failures[0].message)

    def test_add_datum_then_anchor_to_it(self):
        layer = Layer(
            "x",
            anchors={"top": Anchor("top", strategy="datum", query={"datum": "TopPlane"})},
            operations=[
                AddDatum("Plane", "TopPlane", placement={"normal": [0, 0, 1], "origin": [0, 0, 12]}, after="Pad3"),
                AddFeature("Pocket", "P", after="TopPlane", sketch={"plane": "@top"}),
            ],
        )
        result = replay(layer, flange())
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.doc.feature("TopPlane").kind, "DatumPlane")
        self.assertEqual(result.doc.feature("P").params["sketch"]["plane"], "TopPlane")
        self.assertEqual(result.resolutions["top"].via, "datum")

    def test_duplicate_name(self):
        result = replay(Layer("x", operations=[AddFeature("Pad", "Pad3", after="Sketch")]), flange())
        self.assertEqual(result.failures[0].kind, "duplicate_name")

    def test_edit_sketch(self):
        result = replay(Layer("x", operations=[EditSketch("BossSketch", geometry=[{"add": "circle"}])]), flange())
        self.assertTrue(result.ok)
        self.assertEqual(result.doc.feature("BossSketch").params["edits"][0]["layer"], "x")
        bad = replay(Layer("x", operations=[EditSketch("Pad3")]), flange())
        self.assertEqual(bad.failures[0].kind, "not_a_sketch")

    def test_set_property_refuses_geometry(self):
        for prop in ("Length", "Radius", "Placement"):
            self.assertIn(prop, GEOMETRIC_PROPERTIES)
        bad = replay(Layer("x", operations=[SetProperty("Pad3", "Length", 12, 14)]), flange())
        self.assertEqual(bad.failures[0].kind, "geometric_property")
        good = replay(Layer("x", operations=[SetProperty("Pad3", "Label", None, "Base block")]), flange())
        self.assertTrue(good.ok)
        self.assertEqual(good.doc.feature("Pad3").properties["Label"], "Base block")

    def test_document_parameter(self):
        result = replay(Layer("x", operations=[SetParam("param:wall_min", 2.5, 3.0)]), flange())
        self.assertTrue(result.ok)
        self.assertEqual(result.doc.parameters["wall_min"], 3.0)
        missing = replay(Layer("x", operations=[SetParam("param:nope", 1, 2)]), flange())
        self.assertEqual(missing.failures[0].kind, "missing_target")

    def test_missing_targets(self):
        for op in (SetParam("Nope.Length", 1, 2), SetParam("Pad3.Nope", 1, 2), SetParam("Pad3", 1, 2), RemoveFeature("Nope")):
            result = replay(Layer("x", operations=[op]), flange())
            self.assertEqual(result.failures[0].kind, "missing_target", op)

    def test_pinned_is_reported_not_refused(self):
        contracts = ContractSet(pinned=["Fillet2.Radius"])
        result = replay(lighten_layer(), flange(), pinned=contracts)
        self.assertTrue(result.ok)
        self.assertEqual(result.pinned_touched, ["Fillet2.Radius"])

    def test_to_json(self):
        data = replay(lighten_layer(), flange()).to_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["resolutions"]["a_mount_face"]["status"], "resolved")


class StructureTest(unittest.TestCase):
    def test_cycle_and_order(self):
        from collab.model import DocumentModel, Feature

        doc = DocumentModel(features=[Feature("A", "Pad", depends_on=["B"]), Feature("B", "Pad", depends_on=["A"])])
        errors, _ = check_structure(doc)
        self.assertTrue(any("cycle" in e for e in errors))
        self.assertTrue(any("comes after" in e for e in errors))
        doc = DocumentModel(features=[Feature("A", "Pad"), Feature("A", "Pad")])
        errors, _ = check_structure(doc)
        self.assertTrue(any("duplicate" in e for e in errors))


class StackTest(unittest.TestCase):
    def layers(self):
        a = Layer("dev-93b7", base="8f2e19c4", operations=[SetParam("Boss1.Length", 20.0, 25.0)])
        b = lighten_layer()
        return [a, b]

    def test_stack_and_mute(self):
        base = flange()
        a, b = self.layers()
        index = Index(base="8f2e19c4", order=["dev-93b7", "dev-a41c"])
        result = evaluate_stack(base, [a, b], index)
        self.assertTrue(result.ok)
        self.assertEqual(result.revision, "8f2e19c4+dev-93b7+dev-a41c")
        self.assertEqual(result.doc.feature("Boss1").params["Length"], 25.0)
        self.assertIsNotNone(result.doc.feature("LightenPocket1"))

        index.enabled["dev-93b7"] = False
        muted = evaluate_stack(base, [a, b], index)
        self.assertEqual(muted.skipped, ["dev-93b7"])
        self.assertEqual(muted.doc.feature("Boss1").params["Length"], 20.0)
        self.assertEqual(muted.revision, "8f2e19c4+dev-a41c")

    def test_reorder_changes_result_when_dependent(self):
        base = flange()
        # Two layers that do not commute: one sets Radius 2->1.2, the other 1.2->0.8.
        a = Layer("a", operations=[SetParam("Fillet2.Radius", 2.0, 1.2)])
        b = Layer("b", operations=[SetParam("Fillet2.Radius", 1.2, 0.8)])
        ok = evaluate_stack(base, [a, b], Index(order=["a", "b"]))
        self.assertTrue(ok.ok)
        swapped = evaluate_stack(base, [a, b], Index(order=["b", "a"]))
        self.assertFalse(swapped.ok)
        self.assertEqual(swapped.failed.layer.id, "b")
        self.assertEqual(swapped.failed.failures[0].kind, "param_moved")

    def test_upto(self):
        base = flange()
        result = evaluate_stack(base, self.layers(), upto="dev-93b7")
        self.assertEqual([r.layer.id for r in result.results], ["dev-93b7"])

    def test_replay_stack_helper(self):
        doc, results = replay_stack(self.layers(), flange())
        self.assertEqual(len(results), 2)
        self.assertEqual(doc.revision, "8f2e19c4+dev-93b7+dev-a41c")


class RebaseTest(unittest.TestCase):
    def test_rebase_refreshes_anchors_and_drops_validation(self):
        layer = lighten_layer()
        layer.validation.data["mass_g"] = 79.4
        new_base = flange(revision="9a9a9a9a", renumber=3)
        rebased, result = rebase(layer, new_base)
        self.assertTrue(result.ok)
        self.assertEqual(rebased.base, "9a9a9a9a")
        self.assertEqual(rebased.anchors["a_mount_face"].resolved_at_record, "Face9")
        self.assertEqual(rebased.validation.data, {})
        self.assertEqual(layer.anchors["a_mount_face"].resolved_at_record, "Face6", "original untouched")

    def test_rebase_failure(self):
        new_base = flange()
        new_base.feature("Fillet2").params["Radius"] = 3.0
        rebased, result = rebase(lighten_layer(), new_base)
        self.assertIsNone(rebased)
        self.assertFalse(result.ok)


class GeometricDiffTest(unittest.TestCase):
    def test_structural_diff_says_what_it_cannot_measure(self):
        base = flange()
        after = replay(lighten_layer(), base).doc
        diff = geometric_diff(base, after)
        self.assertEqual(diff.features_added, ["LightenPocket1"])
        self.assertEqual(diff.features_changed, ["Fillet2.Radius: 2.0 -> 1.2"])
        self.assertTrue(any("mass" in n for n in diff.not_measured))
        self.assertIsNone(diff.envelope_delta)
        self.assertIn("not measured", diff.summary())

    def test_scripted_diff_measures(self):
        base = flange()
        after = replay(lighten_layer(), base).doc

        def metrics(doc):
            return {"mass_g": 100.0 - (20.0 if doc.has_feature("LightenPocket1") else 0.0)}

        def bbox(doc):
            return ((0, 0, 0), (60, 40, 32))

        ev = ScriptedEvaluator(metrics_fn=metrics, bbox_fn=bbox)
        diff = geometric_diff(base, after, ev)
        self.assertEqual(diff.metric_deltas["mass_g"], {"before": 100.0, "after": 80.0, "delta": -20.0})
        self.assertEqual(diff.envelope_delta["delta"], (0, 0, 0))
        self.assertIn("mass_g: 100 -> 80 (-20)", diff.summary())
        self.assertEqual(len(diff.not_measured), 1)


class EvaluatorTest(unittest.TestCase):
    def test_structural_capabilities(self):
        ev = StructuralEvaluator()
        self.assertTrue(ev.can("recompute"))
        self.assertFalse(ev.can("metrics"))
        self.assertEqual(ev.metrics(flange()), {})
        self.assertIsNone(ev.geometry_issues(flange()))
        with self.assertRaises(Exception):
            ev.can("teleport")

    def test_scripted_recompute_failure(self):
        ev = ScriptedEvaluator(recompute_fn=lambda doc: "solver diverged")
        result = ev.recompute(flange())
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, ["solver diverged"])


if __name__ == "__main__":
    unittest.main()
