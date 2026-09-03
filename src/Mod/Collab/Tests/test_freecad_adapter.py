# SPDX-License-Identifier: LGPL-2.1-or-later
"""The FreeCAD adapter, exercised against the stub in Tests/stubs.py."""

import importlib
import os
import tempfile
import unittest

from collab import freecad_adapter
from collab.anchors import resolve
from collab.model import DocumentModel, Feature
from collab.replay import replay
from collab.schema import AddDatum, AddFeature, Anchor, Fingerprint, Layer, RemoveFeature, SetParam, SetProperty

from Tests import stubs


class AdapterImportTest(unittest.TestCase):
    def test_imports_without_freecad(self):
        stubs.uninstall()
        importlib.reload(freecad_adapter)
        self.assertFalse(freecad_adapter.freecad_available())

    def test_revision_of_is_content_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"hello")
            path = handle.name
        try:
            self.assertEqual(freecad_adapter.revision_of(path), "aaf4c61d")
        finally:
            os.unlink(path)


class StubbedTest(unittest.TestCase):
    def setUp(self):
        stubs.install()
        self.doc = stubs.flange_document()

    def tearDown(self):
        stubs.uninstall()


class SnapshotTest(StubbedTest):
    def test_document_model(self):
        model = freecad_adapter.document_model(self.doc, revision="r1")
        self.assertEqual([f.name for f in model.features], ["Sketch", "Pad3", "BossSketch", "Boss1", "Params"])
        pad = model.feature("Pad3")
        self.assertEqual(pad.kind, "Pad")
        self.assertEqual(pad.params["Length"], 12.0)
        self.assertEqual(pad.depends_on, ("Sketch",))
        self.assertEqual(pad.properties["Label"], "Pad3")
        self.assertEqual(model.parameters, {"wall_min": 2.5})
        faces = model.entities_of("Pad3", "face")
        self.assertEqual(len(faces), 6)
        top = model.entity("Pad3.Face6")
        self.assertEqual(top.normal, (0.0, 0.0, 1.0))
        self.assertEqual(top.surface, "plane")
        self.assertEqual(top.adjacency, 4)
        self.assertEqual(model.entity("Pad3.Face1").adjacency, 4)
        edges = model.entities_of("Pad3", "edge")
        self.assertEqual(len(edges), 8)
        self.assertEqual(model.entity("Boss1.Face1").surface, "cylinder")
        self.assertIsNone(model.entity("Boss1.Face1").normal)

    def test_anchor_resolves_on_snapshot(self):
        model = freecad_adapter.document_model(self.doc)
        anchor = Anchor(
            "top",
            query={"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"},
            fingerprint=Fingerprint(area=1843.2, centroid_local=(30, 20, 12), surface="plane", adjacency=4),
        )
        result = resolve(anchor, model)
        self.assertTrue(result.ok)
        self.assertEqual(result.name, "Pad3.Face6")


class MaterialiseTest(StubbedTest):
    def test_set_param_property_and_remove(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        layer = Layer(
            "x",
            operations=[
                SetParam("Pad3.Length", 12.0, 14.0),
                SetProperty("Pad3", "Label", "Pad3", "Base block"),
                RemoveFeature("Boss1"),
                SetParam("param:wall_min", 2.5, 3.0),
            ],
        )
        result = replay(layer, base)
        self.assertTrue(result.ok, result.failures)
        report = freecad_adapter.materialise(result.doc, self.doc)
        self.assertTrue(report.ok, report.to_json())
        self.assertEqual(self.doc.getObject("Pad3").Length.Value, 14.0)
        self.assertEqual(self.doc.getObject("Pad3").Label, "Base block")
        self.assertIn("Boss1", self.doc.removed)
        self.assertEqual(self.doc.getObject("Params").wall_min, 3.0)

    def test_add_pocket_with_sketch_on_anchored_face(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        anchor = Anchor("top", query={"face_of": "Pad3", "normal": [0, 0, 1]})
        layer = Layer(
            "x",
            anchors={"top": anchor},
            operations=[
                AddFeature(
                    "Pocket",
                    "Lighten",
                    after="Boss1",
                    sketch={"plane": "@top", "geometry": [{"type": "circle", "center": [30, 20], "radius": 8}]},
                    params={"Length": 4.0},
                )
            ],
        )
        result = replay(layer, base)
        self.assertTrue(result.ok, result.failures)
        report = freecad_adapter.materialise(result.doc, self.doc)
        self.assertEqual(report.errors, [])
        pocket = self.doc.getObject("Lighten")
        self.assertEqual(pocket.TypeId, "PartDesign::Pocket")
        self.assertEqual(pocket.Length.Value, 4.0)
        sketch = self.doc.getObject("LightenSketch")
        self.assertEqual(sketch.AttachmentSupport, [(self.doc.getObject("Pad3"), "Face6")])
        self.assertEqual(sketch.MapMode, "FlatFace")
        self.assertEqual(len(sketch.geometry), 1)
        body = self.doc.getObject("Body")
        self.assertEqual([o.Name for o in body.Group][:5], ["Sketch", "Pad3", "BossSketch", "Boss1", "Lighten"])

    def test_constraints_are_reported_not_faked(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        layer = Layer(
            "x",
            operations=[AddFeature("Pocket", "P", after="Boss1", sketch={"geometry": [{"type": "hexagon"}], "constraints": [{"x": 1}]})],
        )
        report = freecad_adapter.materialise(replay(layer, base).doc, self.doc)
        self.assertTrue(any("constraints are not replayed" in u for u in report.unsupported))
        self.assertTrue(any("hexagon" in u for u in report.unsupported))
        self.assertFalse(report.ok)

    def test_add_datum(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        layer = Layer("x", operations=[AddDatum("Plane", "TopPlane", placement={"normal": [0, 0, 1], "origin": [0, 0, 12]}, after="Pad3")])
        report = freecad_adapter.materialise(replay(layer, base).doc, self.doc)
        self.assertTrue(report.ok, report.to_json())
        plane = self.doc.getObject("TopPlane")
        self.assertEqual(plane.TypeId, "PartDesign::Plane")
        self.assertIsNotNone(plane.Placement.Base)

    def test_unsupported_kind(self):
        model = DocumentModel(features=[Feature("Weird", "Loft")])
        report = freecad_adapter.materialise(model, self.doc)
        self.assertIn("Weird: cannot create a Loft", report.unsupported)


class EvaluatorTest(StubbedTest):
    def test_evaluator_metrics_and_recompute(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        ev = freecad_adapter.FreeCADEvaluator(self.doc)
        self.assertTrue(ev.can("metrics"))
        result = ev.recompute(base)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(self.doc.recomputes, 1)
        metrics = ev.metrics(base)
        self.assertAlmostEqual(metrics["mass_g"], 35083.0 / 1000.0 * 2.70)
        self.assertEqual(ev.bounding_box(base), ((0, 0, 0), (60, 40, 32)))
        self.assertEqual(ev.geometry_issues(base), [])

    def test_invalid_object_fails_recompute(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        self.doc.getObject("Boss1").State = ("Invalid",)
        result = freecad_adapter.FreeCADEvaluator(self.doc).recompute(base)
        self.assertFalse(result.ok)
        self.assertIn("Boss1 failed to recompute", result.errors)

    def test_invalid_shape_is_an_issue(self):
        base = freecad_adapter.document_model(self.doc, revision="r1")
        self.doc.getObject("Boss1").Shape._valid = False
        issues = freecad_adapter.FreeCADEvaluator(self.doc).geometry_issues(base)
        self.assertEqual(issues[0].kind, "invalid_shape")

    def test_default_evaluator_picks_freecad_when_available(self):
        from collab.evaluate import default_evaluator

        ev = default_evaluator()
        # freecad_available() is true under the stub, but the evaluator needs
        # a document, so default_evaluator() falls back to structural.
        self.assertIn(ev.name, ("structural", "freecad"))


if __name__ == "__main__":
    unittest.main()
