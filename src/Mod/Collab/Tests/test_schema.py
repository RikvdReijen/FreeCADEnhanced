# SPDX-License-Identifier: LGPL-2.1-or-later
"""The layer format: round-trips, required fields, refusals."""

import json
import unittest

from collab.errors import LayerFormatError
from collab.schema import (
    SCHEMA_VERSION,
    AddFeature,
    Anchor,
    Author,
    Claims,
    Criterion,
    Dependency,
    EditSketch,
    Fingerprint,
    Intent,
    Layer,
    SetParam,
    Validation,
    operation_from_json,
)

SPEC_EXAMPLE = {
    "id": "dev-a41c",
    "schema": 1,
    "name": "Lightweight the mounting flange",
    "author": {"kind": "agent", "id": "claude-opus-5", "session": "session_015Uo5", "human_sponsor": "rik"},
    "created": "2026-09-03T21:40:00Z",
    "base": "8f2e19c4",
    "intent": {
        "goal": "Reduce flange mass by 30% without dropping the safety factor below 2.5",
        "rationale": "Mass budget for the arm assembly is over by 84 g",
        "success_criteria": [
            {"metric": "mass_g", "op": "<=", "value": 84},
            {"metric": "min_safety_factor", "op": ">=", "value": 2.5},
        ],
    },
    "claims": {
        "modifies": ["Body.Flange", "Body.Flange.Sketch"],
        "depends": [
            {"anchor": "a_mount_face", "reason": "pocket depth is measured from it"},
            {"param": "wall_min", "reason": "pockets must leave this wall"},
        ],
        "mode": "advisory",
    },
    "anchors": {
        "a_mount_face": {
            "strategy": "semantic",
            "query": {"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"},
            "fingerprint": {"area": 1843.2, "centroid_local": [0, 0, 12], "surface": "plane", "adjacency": 4},
            "resolved_at_record": "Face6",
        }
    },
    "operations": [
        {
            "op": "add_feature",
            "kind": "Pocket",
            "name": "LightenPocket1",
            "after": "Pad3",
            "sketch": {"plane": "@a_mount_face", "geometry": [], "constraints": []},
            "params": {"depth": 4.0, "type": "Length"},
        },
        {"op": "set_param", "target": "Body.Flange.Fillet2.Radius", "from": 2.0, "to": 1.2},
    ],
    "pinned_touched": [],
    "validation": {
        "recompute": "ok",
        "self_intersection": "none",
        "min_wall_mm": 2.7,
        "mass_g": 79.4,
        "min_safety_factor": 2.61,
        "contracts": "pass",
        "evaluated_at": "8f2e19c4+dev-93b7",
    },
}


class SpecExampleTest(unittest.TestCase):
    def test_spec_example_round_trips(self):
        layer = Layer.from_json(SPEC_EXAMPLE)
        self.assertEqual(layer.id, "dev-a41c")
        self.assertEqual(layer.author.human_sponsor, "rik")
        self.assertEqual(len(layer.operations), 2)
        self.assertIsInstance(layer.operations[0], AddFeature)
        self.assertIsInstance(layer.operations[1], SetParam)
        self.assertEqual(layer.operations[1].from_value, 2.0)
        self.assertEqual(layer.anchors["a_mount_face"].resolved_at_record, "Face6")
        self.assertEqual(layer.validation.evaluated_at, "8f2e19c4+dev-93b7")
        again = Layer.loads(layer.dumps())
        self.assertEqual(again.to_json(), layer.to_json())
        self.assertEqual(json.loads(layer.dumps()), SPEC_EXAMPLE)

    def test_derived_views(self):
        layer = Layer.from_json(SPEC_EXAMPLE)
        self.assertEqual(layer.targets(), ["LightenPocket1", "Body.Flange.Fillet2.Radius"])
        self.assertEqual(layer.operations[0].anchor_refs(), ("a_mount_face",))
        self.assertEqual(layer.operations[0].position(), "Pad3")
        self.assertEqual(layer.sketch_edits(), [])
        self.assertEqual(layer.validation.metrics, {"min_wall_mm": 2.7, "mass_g": 79.4, "min_safety_factor": 2.61})
        self.assertTrue(layer.validation.is_stale_for("8f2e19c4"))
        self.assertFalse(layer.validation.is_stale_for("8f2e19c4+dev-93b7"))


class RefusalTest(unittest.TestCase):
    def test_set_param_without_from_is_refused(self):
        with self.assertRaises(LayerFormatError) as ctx:
            operation_from_json({"op": "set_param", "target": "X.Y", "to": 1})
        self.assertIn("from", str(ctx.exception))

    def test_unknown_operation_is_refused_not_skipped(self):
        with self.assertRaises(LayerFormatError) as ctx:
            operation_from_json({"op": "teleport", "target": "X"})
        self.assertIn("teleport", str(ctx.exception))
        self.assertIn("Refusing to skip", str(ctx.exception))

    def test_undeclared_anchor_reference_is_refused(self):
        with self.assertRaises(LayerFormatError) as ctx:
            Layer("x", operations=[AddFeature("Pocket", "P", after="Pad", sketch={"plane": "@nope"})])
        self.assertIn("nope", str(ctx.exception))
        self.assertEqual(ctx.exception.path, "operations[0]")

    def test_newer_schema_is_refused(self):
        with self.assertRaises(LayerFormatError):
            Layer.from_json({"id": "x", "schema": SCHEMA_VERSION + 1})

    def test_agent_author_needs_sponsor(self):
        with self.assertRaises(LayerFormatError):
            Author("agent", "claude")
        Author("human", "rik")  # fine

    def test_bad_ids(self):
        for bad in ("", "../x", "a b", 5):
            with self.assertRaises(LayerFormatError):
                Layer(bad)

    def test_bad_criterion_op(self):
        with self.assertRaises(LayerFormatError):
            Criterion("mass_g", "~", 1)

    def test_dependency_needs_exactly_one_kind(self):
        with self.assertRaises(LayerFormatError):
            Dependency(anchor="a", param="p")
        with self.assertRaises(LayerFormatError):
            Dependency()

    def test_anchor_strategy_requirements(self):
        with self.assertRaises(LayerFormatError):
            Anchor("a", strategy="fingerprint")
        with self.assertRaises(LayerFormatError):
            Anchor("a", strategy="datum")
        with self.assertRaises(LayerFormatError):
            Anchor("a", strategy="magic", query={"x": 1})

    def test_invalid_json_is_a_format_error(self):
        with self.assertRaises(LayerFormatError):
            Layer.loads("{not json")

    def test_unknown_claim_mode(self):
        with self.assertRaises(LayerFormatError):
            Claims(mode="polite")


class CriterionTest(unittest.TestCase):
    def test_check_semantics(self):
        c = Criterion("mass_g", "<=", 84)
        self.assertEqual(c.check({"mass_g": 79.4}), (True, 79.4))
        self.assertEqual(c.check({"mass_g": 90}), (False, 90))
        # Unknown is not failing.
        self.assertEqual(c.check({}), (None, None))
        self.assertEqual(c.check({"mass_g": "n/a"}), (None, "n/a"))

    def test_intent_check(self):
        intent = Intent("g", success_criteria=[Criterion("a", ">", 1), Criterion("b", "==", 2)])
        results = intent.check({"a": 2, "b": 3})
        self.assertEqual([(r[1]) for r in results], [True, False])


class ForwardCompatibilityTest(unittest.TestCase):
    def test_unknown_top_level_keys_are_preserved(self):
        data = dict(SPEC_EXAMPLE)
        data["x-review"] = {"approved_by": "nobody"}
        layer = Layer.from_json(data)
        self.assertEqual(layer.extras["x-review"], {"approved_by": "nobody"})
        self.assertEqual(layer.to_json()["x-review"], {"approved_by": "nobody"})

    def test_dependency_shorthand(self):
        claims = Claims.from_json({"modifies": ["A"], "depends": ["param:hole_spacing", "a_face"]})
        self.assertEqual([d.key for d in claims.depends], ["param:hole_spacing", "anchor:a_face"])

    def test_validation_unknown_fields_kept(self):
        v = Validation({"recompute": "ok", "custom": 3, "evaluated_at": "r"})
        self.assertEqual(v.to_json(), {"recompute": "ok", "evaluated_at": "r", "custom": 3})
        self.assertEqual(v.metrics, {"custom": 3})

    def test_fingerprint_of_entity(self):
        from collab.model import Entity

        fp = Fingerprint.of(Entity("Face1", "face", "Pad", "plane", (0, 0, 1), 10.0, None, (1, 2, 3), 4))
        self.assertEqual(fp.to_json(), {"area": 10.0, "surface": "plane", "adjacency": 4, "centroid_local": [1.0, 2.0, 3.0]})

    def test_edit_sketch_round_trip(self):
        op = operation_from_json({"op": "edit_sketch", "target": "Sketch", "geometry": [{"add": "line"}]})
        self.assertIsInstance(op, EditSketch)
        self.assertEqual(op.to_json(), {"op": "edit_sketch", "target": "Sketch", "geometry": [{"add": "line"}]})


if __name__ == "__main__":
    unittest.main()
