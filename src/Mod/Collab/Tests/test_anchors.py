# SPDX-License-Identifier: LGPL-2.1-or-later
"""Anchor resolution against a document whose topology is renumbered."""

import ast
import os
import unittest

from collab import anchors
from collab.anchors import Ambiguous, Lost, ResolveOptions, Resolved, resolve, record_anchor
from collab.model import Entity
from collab.schema import Anchor, Fingerprint

from Tests.fixtures import flange


def top_face_anchor(**extra):
    query = {"face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area"}
    query.update(extra)
    return Anchor(
        "a_mount_face",
        strategy="semantic",
        query=query,
        fingerprint=Fingerprint(area=1843.2, centroid_local=(30, 20, 12), surface="plane", adjacency=5),
        resolved_at_record="Face6",
    )


class SemanticQueryTest(unittest.TestCase):
    def test_resolves_top_face_in_base(self):
        result = resolve(top_face_anchor(), flange())
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Face6")
        self.assertEqual(result.via, "semantic")
        self.assertEqual(result.confidence, 1.0)

    def test_survives_renumbering(self):
        """The anchor was recorded as Face6; after renumbering the same face is Face9."""
        result = resolve(top_face_anchor(), flange(renumber=3))
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Face9")
        self.assertEqual(result.owner, "Pad3")

    def test_signed_normal(self):
        """+Z must not match the bottom face just because it is parallel."""
        anchor = Anchor("bottom", query={"face_of": "Pad3", "normal": [0, 0, -1]})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Face1")

    def test_edge_between_two_features(self):
        anchor = Anchor("seam", query={"edge_between": ["Pad3", "Boss1"], "length": 62.83, "tol": 0.1})
        result = resolve(anchor, flange(renumber=5))
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Edge17")

    def test_length_predicate_with_relative_tolerance(self):
        anchor = Anchor("long_edge", query={"edge_of": "Pad3", "length": 60.0, "select": "only"})
        result = resolve(anchor, flange())
        # Two 60 mm edges: 'only' must refuse rather than pick.
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual({e.name for e, _ in result.candidates}, {"Edge1", "Edge3"})

    def test_missing_owner_is_lost_with_a_reason(self):
        anchor = Anchor("gone", query={"face_of": "Pad99", "normal": [0, 0, 1]})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Lost)
        self.assertTrue(any("Pad99" in note for note in result.notes))


class SelectorTest(unittest.TestCase):
    def test_largest_area_refuses_a_near_tie(self):
        doc = flange()
        anchor = Anchor("side", query={"face_of": "Pad3", "area": 720.0, "select": "largest_area"})
        result = resolve(anchor, doc)
        # Face2 and Face4 both have area 720: the selector must not toss a coin.
        self.assertIsInstance(result, Ambiguous)
        self.assertIn("differ by less than", " ".join(result.notes))

    def test_largest_area_picks_a_clear_winner(self):
        anchor = Anchor("top", query={"face_of": "Pad3", "select": "largest_area"})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Face1")  # 2400 > 1843.2

    def test_nearest_to(self):
        anchor = Anchor("near", query={"face_of": "Pad3", "select": "nearest_to", "nearest_to": [61, 20, 6]})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.name, "Face3")

    def test_unknown_selector_is_ambiguous_not_a_crash(self):
        anchor = Anchor("bad", query={"face_of": "Pad3", "select": "biggest"})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Ambiguous)
        self.assertIn("unknown selector", " ".join(result.notes))


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_disambiguates_within_semantic_set(self):
        """Two side faces share area; the fingerprint's centroid tells them apart."""
        anchor = Anchor(
            "front",
            query={"face_of": "Pad3", "area": 720.0},
            fingerprint=Fingerprint(area=720.0, centroid_local=(30, 0, 6), surface="plane", adjacency=4),
        )
        result = resolve(anchor, flange(renumber=10))
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.via, "semantic+fingerprint")
        self.assertEqual(result.name, "Face12")
        self.assertLessEqual(result.confidence, 1.0)

    def test_fingerprint_finds_face_after_owner_renamed(self):
        """The owner was renamed; the semantic query fails; the fingerprint recovers it."""
        doc = flange()
        for entity in doc.entities:
            if entity.owner == "Pad3":
                entity.owner = "Base"
        doc.feature("Pad3").name = "Base"
        result = resolve(top_face_anchor(), doc)
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.via, "fingerprint")
        self.assertEqual(result.name, "Face6")
        self.assertIn("lower confidence", " ".join(result.notes))

    def test_fingerprint_rejects_surface_type_change(self):
        doc = flange()
        for entity in doc.entities:
            entity.owner = "X"  # break every semantic match
        top = doc.entity("Face6")
        top.surface = "cylinder"
        result = resolve(top_face_anchor(), doc)
        self.assertNotIsInstance(result, Resolved)

    def test_close_second_is_ambiguous(self):
        doc = flange()
        for entity in doc.entities:
            entity.owner = "X"
        # Add a near-identical face 0.5 mm away.
        doc.entities.append(
            Entity("Face99", "face", "X", "plane", (0, 0, 1), 1843.2, None, (30, 20, 12.5), 5)
        )
        result = resolve(top_face_anchor(), doc)
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual(result.via, "fingerprint")
        self.assertEqual({e.name for e, _ in result.candidates}, {"Face6", "Face99"})

    def test_lost_reports_recorded_name_and_nearest(self):
        doc = flange()
        doc.entities = [e for e in doc.entities if e.kind != "face"]
        doc.entities.append(Entity("Face1", "face", "Other", "plane", (0, 0, 1), 5.0, None, (0, 0, 0), 4))
        result = resolve(top_face_anchor(), doc)
        self.assertIsInstance(result, Lost)
        self.assertEqual(result.recorded_name, "Face6")
        self.assertEqual(result.nearest[0][0].name, "Face1")
        as_json = result.to_json()
        self.assertEqual(as_json["status"], "lost")
        self.assertEqual(as_json["recorded_name"], "Face6")

    def test_tolerances_are_configurable(self):
        doc = flange()
        for entity in doc.entities:
            entity.owner = "X"
        doc.entity("Face6").area = 1843.2 * 1.20  # 20 % drift
        self.assertIsInstance(resolve(top_face_anchor(), doc), Lost)
        loose = ResolveOptions(fingerprint_tol=0.3)
        self.assertIsInstance(resolve(top_face_anchor(), doc, loose), Resolved)


class NoStaleNameFallbackTest(unittest.TestCase):
    """SPEC §4: there is no fourth step that reuses ``resolved_at_record``."""

    def test_stale_name_pointing_at_wrong_face_is_not_used(self):
        doc = flange(renumber=3)
        # After renumbering, "Face6" is the +X side face, not the top. If the
        # resolver fell back to the recorded name it would return this face.
        self.assertEqual(doc.entity("Face6").normal, (1.0, 0.0, 0.0))
        for entity in doc.entities:
            entity.owner = "X"
        doc.entity("Face9").area = 1.0  # make the real top face unrecognisable
        result = resolve(top_face_anchor(), doc)
        self.assertNotIsInstance(result, Resolved)

    def test_resolved_at_record_is_only_read_for_diagnostics(self):
        """Static check: the resolver's source reads the field in exactly one place,
        and that place is inside the ``Lost`` constructor."""
        with open(os.path.join(os.path.dirname(anchors.__file__), "anchors.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        reads = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "resolved_at_record":
                reads.append(node.lineno)
        self.assertEqual(len(reads), 1, f"resolved_at_record read at lines {reads}")
        lost_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Lost")
        self.assertTrue(lost_cls.lineno <= reads[0] <= lost_cls.end_lineno)


class DatumTest(unittest.TestCase):
    def test_datum_by_name(self):
        anchor = Anchor("mp", strategy="datum", query={"datum": "MountPlane"})
        result = resolve(anchor, flange(renumber=40))
        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.via, "datum")
        self.assertEqual(result.feature.name, "MountPlane")
        self.assertEqual(result.name, "MountPlane")

    def test_datum_anchor_refuses_non_datum(self):
        anchor = Anchor("mp", strategy="datum", query={"datum": "Pad3"})
        result = resolve(anchor, flange())
        self.assertIsInstance(result, Lost)
        self.assertIn("not a datum", " ".join(result.notes))


class RecordAnchorTest(unittest.TestCase):
    def test_record_derives_a_query_and_fingerprint(self):
        doc = flange()
        anchor, now = record_anchor("boss_top", doc.entity("Face8"), doc)
        self.assertEqual(anchor.query["face_of"], "Boss1")
        self.assertEqual(anchor.resolved_at_record, "Face8")
        self.assertAlmostEqual(anchor.fingerprint.area, 314.2)
        self.assertIsInstance(now, Resolved)
        # …and it still resolves after renumbering.
        self.assertEqual(resolve(anchor, flange(renumber=7)).name, "Face15")

    def test_record_reports_ambiguity_immediately(self):
        doc = flange()
        anchor, now = record_anchor("side", doc.entity("Face2"), doc, query={"face_of": "Pad3", "area": 720.0})
        # Two faces have area 720; the fingerprint separates them by centroid.
        self.assertIsInstance(now, Resolved)
        self.assertEqual(now.name, "Face2")
        self.assertEqual(now.via, "semantic+fingerprint")

    def test_resolve_all(self):
        doc = flange()
        results = anchors.resolve_all({"a": top_face_anchor(), "b": Anchor("b", query={"face_of": "Nope"})}, doc)
        self.assertTrue(results["a"].ok)
        self.assertFalse(results["b"].ok)


if __name__ == "__main__":
    unittest.main()
