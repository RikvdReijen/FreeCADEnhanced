# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""Unit tests for ``xrenv.spec``: validation, tessellation and JSON.

Runs without FreeCAD, pivy or any third party package::

    cd src/Mod/XR && python3 -m unittest
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from collections import Counter

_MOD_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
if _MOD_DIR not in sys.path:
    sys.path.insert(0, _MOD_DIR)

from xrenv import spec as S  # noqa: E402


# ---------------------------------------------------------------------------
# geometry helpers used by the assertions
# ---------------------------------------------------------------------------

WELD = 1e-7


def weld_key(positions, i):
    return (
        round(positions[3 * i] / WELD),
        round(positions[3 * i + 1] / WELD),
        round(positions[3 * i + 2] / WELD),
    )


def signed_volume(positions, indices):
    """Six times the signed volume; positive for CCW outward facing solids."""
    total = 0.0
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        p = positions[3 * a:3 * a + 3]
        q = positions[3 * b:3 * b + 3]
        r = positions[3 * c:3 * c + 3]
        total += (
            p[0] * (q[1] * r[2] - q[2] * r[1])
            - p[1] * (q[0] * r[2] - q[2] * r[0])
            + p[2] * (q[0] * r[1] - q[1] * r[0])
        )
    return total / 6.0


def edge_report(positions, indices):
    """Directed edge census over *welded* vertices.

    A watertight, consistently wound mesh uses every directed edge exactly
    once and every undirected edge exactly twice.
    """
    directed = Counter()
    degenerate = 0
    for t in range(0, len(indices), 3):
        keys = [weld_key(positions, indices[t + k]) for k in range(3)]
        if keys[0] == keys[1] or keys[1] == keys[2] or keys[0] == keys[2]:
            degenerate += 1
            continue
        for k in range(3):
            directed[(keys[k], keys[(k + 1) % 3])] += 1
    reused = [e for e, n in directed.items() if n != 1]
    boundary = [e for e in directed if (e[1], e[0]) not in directed]
    return degenerate, reused, boundary


def face_normals_agree(positions, normals, indices, min_dot=0.0):
    """Smallest dot product between a face normal and its vertex normals."""
    worst = 1.0
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        p = positions[3 * a:3 * a + 3]
        q = positions[3 * b:3 * b + 3]
        r = positions[3 * c:3 * c + 3]
        e1 = [q[k] - p[k] for k in range(3)]
        e2 = [r[k] - p[k] for k in range(3)]
        fn = (
            e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0],
        )
        ln = math.sqrt(sum(v * v for v in fn))
        if ln < 1e-15:
            continue
        fn = [v / ln for v in fn]
        for v in (a, b, c):
            vn = normals[3 * v:3 * v + 3]
            worst = min(worst, sum(fn[k] * vn[k] for k in range(3)))
    return worst


def bbox(positions):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for i in range(0, len(positions), 3):
        for k in range(3):
            lo[k] = min(lo[k], positions[i + k])
            hi[k] = max(hi[k], positions[i + k])
    return lo, hi


# ---------------------------------------------------------------------------
# a minimal but valid spec, used as the base for the validation tests
# ---------------------------------------------------------------------------


def minimal_spec(**overrides):
    spec = {
        "id": "unit_test",
        "name": "Unit test environment",
        "description": "",
        "version": S.SPEC_VERSION,
        "user_scale": 4.0,
        "bounds": [2.0, 2.0, 2.0],
        "spawn": [0.0, 0.0, 0.0],
        "ambient": [0.1, 0.1, 0.1],
        "lights": [
            {
                "type": "directional",
                "direction": [0.0, -1.0, 0.0],
                "position": [0.0, 0.0, 0.0],
                "color": [1.0, 1.0, 1.0],
                "intensity": 1.0,
                "cutoff_deg": 45.0,
                "range": 4.0,
            }
        ],
        "materials": [
            {
                "name": "grey",
                "base_color": [0.5, 0.5, 0.5, 1.0],
                "metallic": 0.0,
                "roughness": 0.5,
                "emissive": [0.0, 0.0, 0.0],
                "texture": None,
            }
        ],
        "anchors": {
            "build_plate": {
                "position": [0.0, 0.1, 0.0],
                "rotation": [-0.7071068, 0.0, 0.0, 0.7071068],
                "size": [0.5, 0.5],
            }
        },
        "nodes": [
            {
                "name": "cube",
                "shape": {"type": "box", "size": [0.2, 0.2, 0.2]},
                "material": 0,
                "translation": [0.0, 0.1, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
            }
        ],
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------


class TestValidation(unittest.TestCase):

    def test_minimal_spec_is_valid(self):
        self.assertEqual(S.validate_spec(minimal_spec()), [])

    def test_non_dict_rejected(self):
        self.assertTrue(S.validate_spec([1, 2, 3]))
        self.assertTrue(S.validate_spec("not a spec"))

    def _assert_problem(self, spec, needle):
        problems = S.validate_spec(spec)
        self.assertTrue(problems, "expected a problem mentioning %r" % needle)
        joined = " | ".join(problems)
        self.assertIn(needle, joined)

    def test_missing_and_bad_identity(self):
        self._assert_problem(minimal_spec(id=""), "id")
        self._assert_problem(minimal_spec(id="has space"), "id")
        self._assert_problem(minimal_spec(name=""), "name")

    def test_version_must_match(self):
        self._assert_problem(minimal_spec(version=99), "version")
        self._assert_problem(minimal_spec(version="1"), "version")

    def test_user_scale_range(self):
        self._assert_problem(minimal_spec(user_scale=0.0), "user_scale")
        self._assert_problem(minimal_spec(user_scale=-3.0), "user_scale")
        self._assert_problem(minimal_spec(user_scale=1e6), "user_scale")
        self._assert_problem(minimal_spec(user_scale=float("nan")), "user_scale")

    def test_bounds_and_spawn(self):
        self._assert_problem(minimal_spec(bounds=[1.0, 2.0]), "bounds")
        self._assert_problem(minimal_spec(bounds=[0.0, 2.0, 2.0]), "bounds")
        # spawn outside the interior box on each axis
        self._assert_problem(minimal_spec(spawn=[5.0, 0.0, 0.0]), "spawn")
        self._assert_problem(minimal_spec(spawn=[0.0, -1.0, 0.0]), "spawn")
        self._assert_problem(minimal_spec(spawn=[0.0, 0.0, 9.0]), "spawn")

    def test_lights(self):
        self._assert_problem(minimal_spec(lights=[]), "lights")
        self._assert_problem(
            minimal_spec(lights=[{"type": "laser", "color": [1, 1, 1]}]), "type")
        self._assert_problem(
            minimal_spec(lights=[{"type": "directional", "direction": [0, 0, 0],
                                  "color": [1, 1, 1]}]), "zero length")
        self._assert_problem(
            minimal_spec(lights=[{"type": "spot", "position": [0, 1, 0],
                                  "direction": [0, -1, 0], "color": [1, 1, 1],
                                  "cutoff_deg": 120.0}]), "cutoff_deg")

    def test_materials(self):
        self._assert_problem(minimal_spec(materials=[]), "materials")
        bad = minimal_spec()
        bad["materials"] = [dict(bad["materials"][0]), dict(bad["materials"][0])]
        self._assert_problem(bad, "duplicate material name")
        bad = minimal_spec()
        bad["materials"][0]["base_color"] = [2.0, 0.0, 0.0, 1.0]
        self._assert_problem(bad, "base_color")
        bad = minimal_spec()
        bad["materials"][0]["roughness"] = 4.0
        self._assert_problem(bad, "roughness")

    def test_anchors(self):
        self._assert_problem(minimal_spec(anchors={}), "anchors")
        bad = minimal_spec()
        bad["anchors"]["build_plate"]["size"] = [0.0, 0.5]
        self._assert_problem(bad, "size")
        bad = minimal_spec()
        bad["anchors"]["build_plate"]["rotation"] = [0.0, 0.0, 0.0, 4.0]
        self._assert_problem(bad, "not normalised")

    def test_node_material_index_range(self):
        bad = minimal_spec()
        bad["nodes"][0]["material"] = 7
        self._assert_problem(bad, "out of range")

    def test_node_needs_shape_or_children(self):
        bad = minimal_spec()
        bad["nodes"] = [{"name": "empty"}]
        self._assert_problem(bad, "neither a shape nor children")

    def test_node_zero_scale(self):
        bad = minimal_spec()
        bad["nodes"][0]["scale"] = [1.0, 0.0, 1.0]
        self._assert_problem(bad, "zero scale")

    def test_node_rotation_must_be_normalised(self):
        bad = minimal_spec()
        bad["nodes"][0]["rotation"] = [0.0, 0.0, 0.0, 0.5]
        self._assert_problem(bad, "not normalised")

    def test_children_are_validated(self):
        bad = minimal_spec()
        bad["nodes"] = [{
            "name": "group",
            "children": [{"name": "kid", "shape": {"type": "sphere", "radius": -1.0}}],
        }]
        self._assert_problem(bad, "children[0]")

    def test_no_geometry_rejected(self):
        self._assert_problem(minimal_spec(nodes=[]), "no geometry")

    def test_shape_problems(self):
        cases = [
            ({"type": "wormhole"}, "unknown shape type"),
            ({"type": "box", "size": [0.0, 1.0, 1.0]}, "size"),
            ({"type": "cylinder", "radius": 0.0, "height": 1.0}, "radius"),
            ({"type": "cylinder", "radius": 1.0, "height": 1.0, "sides": 2}, "sides"),
            ({"type": "cone", "radius": 0.0, "top_radius": 0.0, "height": 1.0},
             "both radii are zero"),
            ({"type": "sphere", "radius": 1.0, "rings": 1, "sectors": 8}, "rings"),
            ({"type": "torus", "radius": 1.0, "tube_radius": 0.0}, "tube_radius"),
            ({"type": "tube", "path": [[0, 0, 0]], "radius": 0.1}, "at least 2 points"),
            ({"type": "plane", "size": [1.0, 1.0], "subdiv": [0, 1]}, "subdiv"),
            ({"type": "extrusion", "profile": [[0, 0], [1, 0]], "height": 1.0,
              "closed": True}, "at least 3 points"),
            ({"type": "grid", "size": [1, 1], "pitch": 0.05, "bar": 0.10}, "smaller"),
            ({"type": "honeycomb", "size": [1, 1], "cell": 0.02, "wall": 0.02,
              "height": 0.01}, "wall"),
            ({"type": "text", "string": "", "height": 0.1, "depth": 0.01}, "string"),
            ({"type": "mesh", "positions": [0, 0, 0], "indices": [0, 1, 2]},
             "positions"),
            ({"type": "mesh", "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0],
              "indices": [0, 1, 9]}, "out of range"),
        ]
        for shape, needle in cases:
            with self.subTest(shape=shape.get("type"), needle=needle):
                bad = minimal_spec()
                bad["nodes"][0]["shape"] = shape
                self._assert_problem(bad, needle)


# ---------------------------------------------------------------------------


class TestTessellator(unittest.TestCase):
    """Every primitive: triangle counts, winding, normals, bbox, watertightness."""

    def assert_solid(self, shape, expect_tris=None, expect_bbox=None,
                     min_normal_dot=0.55, watertight=True, positive_volume=True):
        pos, nrm, uv, idx = S.tessellate_shape(shape)
        n = len(pos) // 3
        self.assertEqual(len(nrm), len(pos), "one normal per position")
        self.assertEqual(len(uv), 2 * n, "one uv per position")
        self.assertEqual(len(idx) % 3, 0, "indices form triangles")
        self.assertTrue(idx, "at least one triangle")
        self.assertTrue(all(0 <= i < n for i in idx), "indices in range")
        for k in range(0, len(nrm), 3):
            ln = math.sqrt(nrm[k] ** 2 + nrm[k + 1] ** 2 + nrm[k + 2] ** 2)
            self.assertAlmostEqual(ln, 1.0, places=5, msg="normals are unit length")

        if expect_tris is not None:
            self.assertEqual(len(idx) // 3, expect_tris)

        degenerate, reused, boundary = edge_report(pos, idx)
        self.assertEqual(degenerate, 0, "no degenerate triangles")
        if watertight:
            self.assertEqual(reused, [], "no directed edge used twice (bad winding)")
            self.assertEqual(boundary, [], "no boundary edges (not watertight)")

        if positive_volume:
            self.assertGreater(
                signed_volume(pos, idx), 0.0,
                "CCW front faces: the enclosed signed volume must be positive")

        self.assertGreaterEqual(
            face_normals_agree(pos, nrm, idx), min_normal_dot,
            "vertex normals point the same way as their faces (outward)")

        if expect_bbox is not None:
            lo, hi = bbox(pos)
            for k in range(3):
                self.assertAlmostEqual(lo[k], expect_bbox[0][k], places=5)
                self.assertAlmostEqual(hi[k], expect_bbox[1][k], places=5)
        return pos, nrm, uv, idx

    def test_box(self):
        self.assert_solid(
            {"type": "box", "size": [0.2, 0.4, 0.6]},
            expect_tris=12,
            expect_bbox=((-0.1, -0.2, -0.3), (0.1, 0.2, 0.3)),
            min_normal_dot=0.999,
        )

    def test_box_volume_matches_size(self):
        pos, _n, _u, idx = S.tessellate_shape({"type": "box", "size": [0.2, 0.4, 0.6]})
        self.assertAlmostEqual(signed_volume(pos, idx), 0.2 * 0.4 * 0.6, places=9)

    def test_cylinder_capped(self):
        sides = 16
        pos, _n, _u, idx = self.assert_solid(
            {"type": "cylinder", "radius": 0.1, "height": 0.4, "sides": sides},
            expect_tris=4 * sides,
            expect_bbox=((-0.1, -0.2, -0.1), (0.1, 0.2, 0.1)),
        )
        # the polygonal approximation is inscribed, so it is a touch smaller
        exact = math.pi * 0.1 ** 2 * 0.4
        self.assertLess(signed_volume(pos, idx), exact)
        self.assertGreater(signed_volume(pos, idx), exact * 0.9)

    def test_cylinder_uncapped_is_open(self):
        pos, _n, _u, idx = S.tessellate_shape(
            {"type": "cylinder", "radius": 0.1, "height": 0.4, "sides": 12,
             "caps": False})
        self.assertEqual(len(idx) // 3, 24)
        _deg, _reused, boundary = edge_report(pos, idx)
        self.assertTrue(boundary, "an uncapped cylinder is deliberately open")

    def test_cone_apex(self):
        sides = 16
        self.assert_solid(
            {"type": "cone", "radius": 0.1, "top_radius": 0.0, "height": 0.3,
             "sides": sides},
            expect_tris=2 * sides,
            expect_bbox=((-0.1, -0.15, -0.1), (0.1, 0.15, 0.1)),
        )

    def test_cone_inverted_apex(self):
        self.assert_solid(
            {"type": "cone", "radius": 0.0, "top_radius": 0.08, "height": 0.3,
             "sides": 12},
            expect_tris=24,
        )

    def test_cone_truncated(self):
        self.assert_solid(
            {"type": "cone", "radius": 0.1, "top_radius": 0.05, "height": 0.3,
             "sides": 20},
            expect_tris=4 * 20,
        )

    def test_sphere(self):
        rings, sectors = 10, 16
        pos, _n, _u, idx = self.assert_solid(
            {"type": "sphere", "radius": 0.15, "rings": rings, "sectors": sectors},
            expect_tris=2 * sectors * (rings - 1),
            expect_bbox=((-0.15, -0.15, -0.15), (0.15, 0.15, 0.15)),
        )
        exact = 4.0 / 3.0 * math.pi * 0.15 ** 3
        self.assertLess(signed_volume(pos, idx), exact)
        self.assertGreater(signed_volume(pos, idx), exact * 0.85)

    def test_sphere_normals_are_radial(self):
        pos, nrm, _u, _i = S.tessellate_shape(
            {"type": "sphere", "radius": 0.2, "rings": 8, "sectors": 12})
        for i in range(0, len(pos), 3):
            for k in range(3):
                self.assertAlmostEqual(nrm[i + k], pos[i + k] / 0.2, places=6)

    def test_torus(self):
        rings, sides = 20, 10
        pos, _n, _u, _i = self.assert_solid(
            {"type": "torus", "radius": 0.2, "tube_radius": 0.04, "sides": sides,
             "rings": rings},
            expect_tris=2 * rings * sides,
            min_normal_dot=0.9,
        )
        lo, hi = bbox(pos)
        # the tube polygon is inscribed, so the extremes fall on sampled angles
        max_sin = max(abs(math.sin(2.0 * math.pi * j / sides)) for j in range(sides))
        self.assertAlmostEqual(hi[0], 0.24, places=6)
        self.assertAlmostEqual(lo[0], -0.24, places=6)
        self.assertAlmostEqual(hi[1], 0.04 * max_sin, places=6)
        self.assertAlmostEqual(lo[1], -0.04 * max_sin, places=6)

    def test_tube_frames_stay_orthogonal(self):
        shape = {
            "type": "tube",
            "path": [[0, 0, 0], [0, 0.2, 0], [0.2, 0.3, 0], [0.4, 0.3, 0.2]],
            "radius": 0.02,
            "sides": 10,
        }
        pos, nrm, _u, idx = self.assert_solid(
            shape, expect_tris=(4 - 1) * 10 * 2 + 2 * 10, min_normal_dot=0.55)
        # every ring vertex sits exactly `radius` from its path point
        path = shape["path"]
        for ring, centre in enumerate(path):
            for j in range(11):
                i = ring * 11 + j
                d = math.sqrt(sum((pos[3 * i + k] - centre[k]) ** 2 for k in range(3)))
                self.assertAlmostEqual(d, 0.02, places=6)

    def test_tube_straight_run_has_no_twist(self):
        pos, nrm, _u, _i = S.tessellate_shape(
            {"type": "tube", "path": [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
             "radius": 0.1, "sides": 8})
        # rings 0 and 2 must line up vertex for vertex apart from the X offset
        for j in range(9):
            a, c = j, 2 * 9 + j
            self.assertAlmostEqual(pos[3 * a + 1], pos[3 * c + 1], places=9)
            self.assertAlmostEqual(pos[3 * a + 2], pos[3 * c + 2], places=9)

    def test_plane_is_single_sided(self):
        pos, nrm, _u, idx = S.tessellate_shape(
            {"type": "plane", "size": [1.0, 2.0], "subdiv": [3, 4]})
        self.assertEqual(len(idx) // 3, 2 * 3 * 4)
        lo, hi = bbox(pos)
        self.assertAlmostEqual(lo[0], -0.5)
        self.assertAlmostEqual(hi[1], 1.0)
        self.assertAlmostEqual(lo[2], 0.0)
        self.assertAlmostEqual(hi[2], 0.0)
        for i in range(0, len(nrm), 3):
            self.assertAlmostEqual(nrm[i + 2], 1.0, places=9)
        self.assertGreaterEqual(face_normals_agree(pos, nrm, idx), 0.999)

    def test_extrusion_closed_solid(self):
        profile = [[0, 0], [0.1, 0], [0.1, 0.02], [0.04, 0.02], [0.04, 0.1], [0, 0.1]]
        pos, _n, _u, idx = self.assert_solid(
            {"type": "extrusion", "profile": profile, "height": 0.5, "closed": True},
            expect_tris=2 * len(profile) + 2 * (len(profile) - 2),
            min_normal_dot=0.999,
        )
        # cross section area 0.1*0.02 + 0.04*0.08 = 0.0052
        self.assertAlmostEqual(signed_volume(pos, idx), 0.0052 * 0.5, places=9)

    def test_extrusion_reverses_clockwise_profile(self):
        ccw = [[0, 0], [1, 0], [1, 1], [0, 1]]
        cw = list(reversed(ccw))
        a = S.tessellate_shape({"type": "extrusion", "profile": ccw, "height": 1.0})
        b = S.tessellate_shape({"type": "extrusion", "profile": cw, "height": 1.0})
        self.assertGreater(signed_volume(a[0], a[3]), 0.0)
        self.assertGreater(signed_volume(b[0], b[3]), 0.0)
        self.assertAlmostEqual(signed_volume(a[0], a[3]), 1.0, places=9)

    def test_extrusion_open_profile_is_a_ribbon(self):
        pos, _n, _u, idx = S.tessellate_shape(
            {"type": "extrusion", "profile": [[0, 0], [1, 0], [1, 1]],
             "height": 0.2, "closed": False})
        self.assertEqual(len(idx) // 3, 4)
        _deg, _reused, boundary = edge_report(pos, idx)
        self.assertTrue(boundary)

    def test_grid(self):
        pos, _n, _u, idx = self.assert_solid(
            {"type": "grid", "size": [1.0, 1.0], "pitch": 0.2, "bar": 0.02},
            min_normal_dot=0.999,
        )
        # 5 bars each way from -0.4..0.4 plus the centre one: 11 x + 11 y
        # bars land on every multiple of the pitch that fits: 5 each way
        self.assertEqual(len(idx) // 3, 10 * 12)
        lo, hi = bbox(pos)
        self.assertAlmostEqual(hi[0], 0.5)
        self.assertAlmostEqual(hi[2], 0.01)

    def test_honeycomb(self):
        pos, _n, _u, idx = self.assert_solid(
            {"type": "honeycomb", "size": [0.2, 0.2], "cell": 0.03, "wall": 0.002,
             "height": 0.02},
            min_normal_dot=0.999,
        )
        self.assertEqual(len(idx) % 12, 0, "the walls are closed boxes")
        self.assertGreater(len(idx) // 3, 200, "a 200 mm bed of 30 mm cells is busy")
        lo, hi = bbox(pos)
        self.assertLessEqual(hi[0], 0.13)
        self.assertAlmostEqual(hi[2], 0.01, places=6)

    def test_text(self):
        pos, _n, _u, idx = self.assert_solid(
            {"type": "text", "string": "CAUTION 230V", "height": 0.02,
             "depth": 0.002},
            min_normal_dot=0.999,
        )
        self.assertEqual(len(idx) % 12, 0, "every stroke is a closed box")
        lo, hi = bbox(pos)
        stroke = 0.02 * 0.115
        # centred on the node origin to within the stroke cap overshoot
        self.assertAlmostEqual((lo[0] + hi[0]) * 0.5, 0.0, delta=stroke)
        self.assertAlmostEqual((lo[1] + hi[1]) * 0.5, 0.0, delta=stroke)
        self.assertAlmostEqual(hi[2] - lo[2], 0.002, places=9)

    def test_text_multiline_is_taller(self):
        one = S.tessellate_shape({"type": "text", "string": "A", "height": 0.02,
                                  "depth": 0.001})
        two = S.tessellate_shape({"type": "text", "string": "A\nB", "height": 0.02,
                                  "depth": 0.001})
        self.assertGreater(bbox(two[0])[1][1] - bbox(two[0])[0][1],
                           bbox(one[0])[1][1] - bbox(one[0])[0][1])

    def test_text_unknown_glyphs_are_skipped_not_fatal(self):
        pos, _n, _u, idx = S.tessellate_shape(
            {"type": "text", "string": "AµµB", "height": 0.02,
             "depth": 0.001})
        self.assertTrue(idx)

    def test_mesh_passthrough(self):
        shape = {
            "type": "mesh",
            "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            "indices": [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3],
        }
        pos, nrm, uv, idx = S.tessellate_shape(shape)
        self.assertEqual(len(idx) // 3, 4)
        self.assertEqual(len(nrm), len(pos))
        self.assertEqual(len(uv), 8)
        self.assertGreater(signed_volume(pos, idx), 0.0)
        degenerate, reused, boundary = edge_report(pos, idx)
        self.assertEqual((degenerate, reused, boundary), (0, [], []))

    def test_mesh_keeps_supplied_normals(self):
        shape = {
            "type": "mesh",
            "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0],
            "normals": [0, 0, 1, 0, 0, 1, 0, 0, 1],
            "indices": [0, 1, 2],
        }
        _p, nrm, _u, _i = S.tessellate_shape(shape)
        self.assertEqual(nrm, [0.0, 0.0, 1.0] * 3)

    def test_all_primitives_are_covered(self):
        covered = {
            "box": {"type": "box", "size": [1, 1, 1]},
            "cylinder": {"type": "cylinder", "radius": 1, "height": 1},
            "cone": {"type": "cone", "radius": 1, "top_radius": 0.5, "height": 1},
            "sphere": {"type": "sphere", "radius": 1},
            "torus": {"type": "torus", "radius": 1, "tube_radius": 0.2},
            "tube": {"type": "tube", "path": [[0, 0, 0], [1, 0, 0]], "radius": 0.1},
            "plane": {"type": "plane", "size": [1, 1]},
            "extrusion": {"type": "extrusion",
                          "profile": [[0, 0], [1, 0], [0, 1]], "height": 1},
            "grid": {"type": "grid", "size": [1, 1], "pitch": 0.25, "bar": 0.02},
            "honeycomb": {"type": "honeycomb", "size": [0.2, 0.2], "cell": 0.05,
                          "wall": 0.005, "height": 0.02},
            "text": {"type": "text", "string": "X", "height": 0.1, "depth": 0.01},
            "mesh": {"type": "mesh",
                     "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                     "indices": [0, 1, 2]},
        }
        self.assertEqual(set(covered), set(S.SHAPE_TYPES))
        for name, shape in covered.items():
            with self.subTest(primitive=name):
                pos, _n, _u, idx = S.tessellate_shape(shape)
                self.assertTrue(idx, "%s produced no triangles" % name)


class TestDegenerateRejection(unittest.TestCase):

    BAD = [
        {"type": "nope"},
        {"type": "box", "size": [0.0, 1.0, 1.0]},
        {"type": "box", "size": [1.0, -1.0, 1.0]},
        {"type": "cylinder", "radius": 0.0, "height": 1.0},
        {"type": "cylinder", "radius": 1.0, "height": 0.0},
        {"type": "cylinder", "radius": 1.0, "height": 1.0, "sides": 2},
        {"type": "cone", "radius": 0.0, "top_radius": 0.0, "height": 1.0},
        {"type": "cone", "radius": 1.0, "top_radius": 1.0, "height": 0.0},
        {"type": "sphere", "radius": -1.0},
        {"type": "sphere", "radius": 1.0, "rings": 1},
        {"type": "torus", "radius": 1.0, "tube_radius": 0.0},
        {"type": "torus", "radius": 1.0, "tube_radius": 0.1, "sides": 2},
        {"type": "tube", "path": [[0, 0, 0]], "radius": 0.1},
        {"type": "tube", "path": [[0, 0, 0], [0, 0, 0]], "radius": 0.1},
        {"type": "tube", "path": [[0, 0, 0], [1, 0, 0]], "radius": 0.0},
        {"type": "plane", "size": [0.0, 1.0]},
        {"type": "plane", "size": [1.0, 1.0], "subdiv": [0, 1]},
        {"type": "extrusion", "profile": [[0, 0], [1, 0]], "height": 1.0},
        {"type": "extrusion", "profile": [[0, 0], [1, 0], [2, 0]], "height": 1.0},
        {"type": "extrusion", "profile": [[0, 0], [1, 0], [1, 1]], "height": 0.0},
        {"type": "grid", "size": [1, 1], "pitch": 0.1, "bar": 0.2},
        {"type": "grid", "size": [1, 1], "pitch": 0.0, "bar": 0.01},
        {"type": "honeycomb", "size": [0.1, 0.1], "cell": 0.02, "wall": 0.02,
         "height": 0.01},
        {"type": "honeycomb", "size": [0.1, 0.1], "cell": 0.02, "wall": 0.001,
         "height": 0.0},
        {"type": "text", "string": "", "height": 0.1, "depth": 0.01},
        {"type": "text", "string": "A", "height": 0.0, "depth": 0.01},
        {"type": "text", "string": "µµ", "height": 0.1, "depth": 0.01},
        {"type": "mesh", "positions": [0, 0, 0], "indices": [0, 0, 0]},
        {"type": "mesh", "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "indices": [0, 1, 5]},
        {"type": "mesh", "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "indices": []},
        "not a shape",
        None,
    ]

    def test_degenerate_shapes_raise(self):
        for shape in self.BAD:
            with self.subTest(shape=shape):
                with self.assertRaises(S.TessellationError):
                    S.tessellate_shape(shape)


# ---------------------------------------------------------------------------


class TestJsonRoundTrip(unittest.TestCase):

    def test_text_round_trip(self):
        spec = minimal_spec()
        text = S.spec_to_json(spec)
        self.assertTrue(text.endswith("\n"))
        again = S.spec_from_json(text)
        self.assertEqual(again["id"], spec["id"])
        self.assertEqual(S.spec_to_json(again), text)

    def test_output_is_deterministic_and_sorted(self):
        spec = minimal_spec()
        a = S.spec_to_json(spec)
        b = S.spec_to_json(json.loads(a))
        self.assertEqual(a, b)
        keys = list(json.loads(a).keys())
        self.assertEqual(keys, sorted(keys))

    def test_float_rounding_normalises_negative_zero(self):
        spec = minimal_spec()
        spec["nodes"][0]["translation"] = [-0.0, 1.0 / 3.0, 1e-12]
        text = S.spec_to_json(spec)
        got = json.loads(text)["nodes"][0]["translation"]
        self.assertEqual(got, [0.0, 0.333333, 0.0])
        self.assertNotIn("-0.0", text)

    def test_file_round_trip(self):
        spec = minimal_spec()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "unit_test.json")
            S.save_spec(spec, path)
            self.assertTrue(os.path.isfile(path))
            loaded = S.load_spec(path)
        self.assertEqual(S.validate_spec(loaded), [])
        self.assertEqual(S.spec_to_json(loaded), S.spec_to_json(spec))

    def test_spec_from_json_rejects_non_object(self):
        with self.assertRaises(ValueError):
            S.spec_from_json("[1, 2, 3]")

    def test_dataclass_round_trip(self):
        spec = minimal_spec()
        obj = S.EnvironmentSpec.from_dict(spec)
        self.assertEqual(obj.id, "unit_test")
        self.assertEqual(obj.user_scale, 4.0)
        self.assertEqual(obj.materials[0].name, "grey")
        self.assertEqual(obj.anchors["build_plate"].name, "build_plate")
        self.assertEqual(obj.nodes[0].shape["type"], "box")
        back = obj.to_dict()
        self.assertEqual(S.validate_spec(back), [])
        self.assertEqual(S.spec_to_json(back), S.spec_to_json(spec))

    def test_anchor_name_is_not_serialised(self):
        anchor = S.Anchor(name="build_plate", position=[0, 1, 0], size=[1, 1])
        self.assertNotIn("name", anchor.to_dict())


# ---------------------------------------------------------------------------


class TestSpecHelpers(unittest.TestCase):

    def test_iter_nodes_composes_transforms(self):
        spec = minimal_spec()
        spec["nodes"] = [{
            "name": "parent",
            "translation": [1.0, 0.0, 0.0],
            "children": [{
                "name": "child",
                "translation": [0.0, 2.0, 0.0],
                "shape": {"type": "box", "size": [0.1, 0.1, 0.1]},
                "material": 0,
            }],
        }]
        found = {n["name"]: w for n, w in S.iter_nodes(spec)}
        self.assertEqual(set(found), {"parent", "child"})
        self.assertEqual(S.mat_apply_point(found["child"], (0, 0, 0)), (1.0, 2.0, 0.0))
        self.assertEqual(S.count_parts(spec), 1)

    def test_iter_nodes_applies_rotation_and_scale(self):
        spec = minimal_spec()
        # 90 degrees about Y sends +Z onto +X
        spec["nodes"] = [{
            "name": "turned",
            "rotation": [0.0, 0.7071068, 0.0, 0.7071068],
            "scale": [2.0, 2.0, 2.0],
            "shape": {"type": "box", "size": [0.1, 0.1, 0.1]},
            "material": 0,
        }]
        _node, world = next(iter(S.iter_nodes(spec)))
        x, y, z = S.mat_apply_point(world, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(x, 2.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        self.assertAlmostEqual(z, 0.0, places=6)

    def test_spec_bounds(self):
        spec = minimal_spec()
        lo, hi = S.spec_bounds(spec)
        self.assertAlmostEqual(lo[0], -0.1, places=6)
        self.assertAlmostEqual(hi[1], 0.2, places=6)

    def test_tessellate_spec_transforms_into_world_space(self):
        spec = minimal_spec()
        parts = S.tessellate_spec(spec)
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part["name"], "cube")
        self.assertEqual(part["material"], 0)
        lo, hi = bbox(part["positions"])
        self.assertAlmostEqual(lo[1], 0.0, places=6)
        self.assertAlmostEqual(hi[1], 0.2, places=6)

    def test_quat_to_mat_is_orthonormal(self):
        q = [0.2, -0.4, 0.5, 0.7416198]
        m = S.quat_to_mat(q)
        for i in range(3):
            self.assertAlmostEqual(sum(m[i][k] ** 2 for k in range(3)), 1.0, places=5)
            for j in range(3):
                if i != j:
                    self.assertAlmostEqual(
                        sum(m[i][k] * m[j][k] for k in range(3)), 0.0, places=5)

    def test_triangulate_polygon_handles_concave(self):
        poly = [(0, 0), (2, 0), (2, 2), (1, 1), (0, 2)]
        tris = S.triangulate_polygon(poly)
        self.assertEqual(len(tris), 3)
        area = 0.0
        for a, b, c in tris:
            pa, pb, pc = poly[a], poly[b], poly[c]
            area += 0.5 * ((pb[0] - pa[0]) * (pc[1] - pa[1])
                           - (pb[1] - pa[1]) * (pc[0] - pa[0]))
        self.assertAlmostEqual(area, 3.0, places=9)


if __name__ == "__main__":
    unittest.main()
