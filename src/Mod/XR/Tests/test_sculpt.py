# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""Unit tests for the sculpt mesh, brushes, masks, symmetry and topology.

Runs under plain ``python3 -m unittest`` from ``src/Mod/XR`` with neither
FreeCAD nor numpy installed.  The layer stack itself is tested separately in
``Tests/test_sculpt_layers.py``.
"""

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsculpt import brushes, mesh, prefs  # noqa: E402
from xrsculpt import topology  # noqa: E402
from xrsculpt.brushes import BrushParams, Dab, apply_dab  # noqa: E402
from xrsculpt.layers import LayerStack  # noqa: E402
from xrsculpt.masking import VertexMask  # noqa: E402
from xrsculpt.mesh import SculptMesh  # noqa: E402
from xrsculpt.session import SculptSession  # noqa: E402
from xrsculpt.symmetry import Symmetry  # noqa: E402


def _rig(m=None):
    """A mesh, a stack over it, and the active layer -- the brush test rig."""
    m = m if m is not None else mesh.make_icosphere(3, 1.0)
    stack = LayerStack(base=list(m.positions))
    layer = stack.add_layer("Pass")
    return m, stack, layer


def _moved(before, after, tolerance=1e-12):
    """Indices whose position changed by more than ``tolerance``."""
    out = []
    for i in range(len(before) // 3):
        o = i * 3
        d = math.sqrt((after[o] - before[o]) ** 2
                      + (after[o + 1] - before[o + 1]) ** 2
                      + (after[o + 2] - before[o + 2]) ** 2)
        if d > tolerance:
            out.append(i)
    return out


# ==========================================================================
# the mesh
# ==========================================================================

class TestSculptMesh(unittest.TestCase):

    def test_construction_accepts_flat_and_nested(self):
        flat = SculptMesh([0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2])
        nested = SculptMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        self.assertEqual(list(flat.positions), list(nested.positions))
        self.assertEqual(list(flat.faces), list(nested.faces))
        self.assertEqual(flat.n_vertices, 3)
        self.assertEqual(flat.n_faces, 1)

    def test_bad_input_is_rejected(self):
        with self.assertRaises(ValueError):
            SculptMesh([0, 0], [])
        with self.assertRaises(ValueError):
            SculptMesh([0, 0, 0], [0, 1])
        with self.assertRaises(ValueError):
            SculptMesh([0, 0, 0], [0, 0, 7])
        with self.assertRaises(ValueError):
            SculptMesh([0, 0, 0], [(0, 0, 0, 0)])

    def test_icosphere_is_closed_and_manifold(self):
        for level in (0, 1, 2):
            m = mesh.make_icosphere(level)
            self.assertTrue(m.is_closed(), level)
            self.assertTrue(m.is_manifold(), level)
            self.assertEqual(m.boundary_edges(), [])
            # Euler characteristic of a sphere
            edges = len(m.edge_face_count())
            self.assertEqual(m.n_vertices - edges + m.n_faces, 2)

    def test_grid_has_a_boundary(self):
        g = mesh.make_grid_mesh(4, 4, 1.0)
        self.assertTrue(g.is_manifold())
        self.assertFalse(g.is_closed())
        self.assertEqual(len(g.boundary_edges()), 16)

    def test_normals_point_outwards_on_a_sphere(self):
        m = mesh.make_icosphere(2, 1.0)
        n = m.normals()
        for i in range(m.n_vertices):
            p = m.vertex(i)
            o = i * 3
            dot = p[0] * n[o] + p[1] * n[o + 1] + p[2] * n[o + 2]
            self.assertGreater(dot, 0.9)

    def test_normals_are_unit_length(self):
        m = mesh.make_icosphere(2, 3.0)
        n = m.normals()
        for i in range(m.n_vertices):
            o = i * 3
            self.assertAlmostEqual(
                math.sqrt(n[o] ** 2 + n[o + 1] ** 2 + n[o + 2] ** 2), 1.0,
                places=12)

    def test_normals_are_recomputed_after_a_move(self):
        m = mesh.make_grid_mesh(2, 2, 1.0)
        first = list(m.normals())
        m.move_vertex(4, (0.0, 0.0, 1.0))
        second = list(m.normals())
        self.assertNotEqual(first, second)

    def test_incremental_normals_equal_a_full_rebuild(self):
        """A dab refreshes only the moved vertices and their one-rings, and
        must land on exactly the same numbers as recomputing everything."""
        rng = random.Random(4)
        incremental = mesh.make_icosphere(3, 1.0)
        full = mesh.make_icosphere(3, 1.0)
        incremental.normals()                      # prime the cache
        moved = [rng.randrange(incremental.n_vertices) for _ in range(15)]
        for i in moved:
            incremental.positions[i * 3 + 2] += 0.01
            full.positions[i * 3 + 2] += 0.01
        incremental.touch(moved)
        full.touch(None)
        self.assertEqual(list(incremental.normals()), list(full.normals()))

    def test_incremental_normals_after_several_dabs(self):
        m = mesh.make_icosphere(3, 1.0)
        reference = mesh.make_icosphere(3, 1.0)
        stack = LayerStack(base=list(m.positions))
        layer = stack.add_layer("p")
        params = BrushParams(kind="inflate", radius=0.4, strength=0.2)
        for k in range(5):
            dab = Dab((0.0, 0.1 * k, 1.0), (0.0, 0.0, 1.0), radius=0.4,
                      strength=0.2)
            apply_dab(m, layer, params, dab, stack=stack)
        reference.positions[:] = m.positions
        reference.touch(None)
        self.assertEqual(list(m.normals()), list(reference.normals()))

    def test_vertex_faces_is_ascending_and_complete(self):
        m = mesh.make_icosphere(1)
        off, idx = m.vertex_faces()
        self.assertEqual(off[m.n_vertices], m.n_faces * 3)
        for i in range(m.n_vertices):
            faces = list(idx[off[i]:off[i + 1]])
            self.assertEqual(faces, sorted(faces))
            for f in faces:
                self.assertIn(i, m.face(f))

    def test_adjacency_is_symmetric_and_sorted(self):
        m = mesh.make_icosphere(1)
        for i in range(m.n_vertices):
            nbrs = m.neighbours(i)
            self.assertEqual(list(nbrs), sorted(nbrs))
            self.assertEqual(len(set(nbrs)), len(nbrs))
            for j in nbrs:
                self.assertIn(i, m.neighbours(j))
        self.assertEqual(m.degree(0), len(m.neighbours(0)))

    def test_one_ring_centroid(self):
        m = mesh.make_grid_mesh(2, 2, 1.0)
        c = m.one_ring_centroid(4)          # the middle vertex
        self.assertAlmostEqual(c[0], 0.0, places=12)
        self.assertAlmostEqual(c[1], 0.0, places=12)

    def test_bounds_and_centroid(self):
        m = mesh.make_icosphere(1, 2.0)
        lo, hi = m.bounds()
        for k in range(3):
            self.assertAlmostEqual(lo[k], -2.0, places=6)
            self.assertAlmostEqual(hi[k], 2.0, places=6)
        for v in m.centroid():
            self.assertAlmostEqual(v, 0.0, places=9)

    def test_volume_of_a_sphere(self):
        exact = 4.0 / 3.0 * math.pi
        coarse = mesh.make_icosphere(2, 1.0).volume()
        fine = mesh.make_icosphere(4, 1.0).volume()
        # an inscribed polyhedron under-estimates, and converges as it is
        # refined
        self.assertLess(coarse, fine)
        self.assertLess(fine, exact)
        self.assertAlmostEqual(fine, exact, delta=0.01)

    def test_average_edge_length_is_cached(self):
        m = mesh.make_icosphere(2, 1.0)
        first = m.average_edge_length()
        self.assertGreater(first, 0.0)
        self.assertEqual(m.average_edge_length(), first)

    def test_dirty_tracking(self):
        m = mesh.make_icosphere(1)
        m.clear_dirty()
        self.assertEqual(m.dirty_indices(), [])
        self.assertIsNone(m.dirty_bounds())
        m.touch([3, 1, 2])
        self.assertEqual(m.dirty_indices(), [1, 2, 3])
        self.assertIsNotNone(m.dirty_bounds())
        m.clear_dirty()
        m.touch(None)
        self.assertEqual(len(m.dirty_indices()), m.n_vertices)

    def test_copy_is_independent(self):
        m = mesh.make_icosphere(1)
        c = m.copy()
        c.set_vertex(0, (9.0, 9.0, 9.0))
        self.assertNotEqual(list(c.positions), list(m.positions))
        self.assertEqual(list(c.faces), list(m.faces))

    def test_dict_round_trip(self):
        m = mesh.make_icosphere(1)
        c = SculptMesh.from_dict(m.to_dict())
        self.assertEqual(list(c.positions), list(m.positions))
        self.assertEqual(list(c.faces), list(m.faces))
        self.assertEqual(c.name, m.name)

    def test_freecad_conversion_rejects_wrong_types(self):
        with self.assertRaises(TypeError):
            SculptMesh.from_mesh_object(object())
        with self.assertRaises(TypeError):
            SculptMesh.from_shape(object())

    def test_from_mesh_object_duck_typing(self):
        class _Topo(object):
            Topology = ([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                        [(0, 1, 2)])
        m = SculptMesh.from_mesh_object(_Topo(), "Duck")
        self.assertEqual(m.n_vertices, 3)
        self.assertEqual(m.name, "Duck")


# ==========================================================================
# the spatial index
# ==========================================================================

class TestSpatialIndex(unittest.TestCase):

    def test_queries_agree_with_brute_force(self):
        m = mesh.make_icosphere(3, 1.0)
        rng = random.Random(7)
        for _ in range(40):
            c = (rng.uniform(-1.2, 1.2), rng.uniform(-1.2, 1.2),
                 rng.uniform(-1.2, 1.2))
            r = rng.uniform(0.01, 1.5)
            self.assertEqual(m.vertices_in_radius(c, r),
                             m.vertices_in_radius_bruteforce(c, r))

    def test_queries_agree_on_a_scattered_cloud(self):
        rng = random.Random(11)
        verts = []
        for _ in range(600):
            verts.extend((rng.uniform(-50, 50), rng.uniform(-2, 2),
                          rng.uniform(-0.01, 0.01)))
        faces = []
        for i in range(0, 597, 3):
            faces.extend((i, i + 1, i + 2))
        m = SculptMesh(verts, faces)
        for _ in range(30):
            c = (rng.uniform(-60, 60), rng.uniform(-3, 3), 0.0)
            r = rng.uniform(0.1, 20.0)
            self.assertEqual(m.vertices_in_radius(c, r),
                             m.vertices_in_radius_bruteforce(c, r))

    def test_query_results_are_sorted(self):
        m = mesh.make_icosphere(3, 1.0)
        idx = m.vertices_in_radius((0, 0, 1), 0.6)
        self.assertEqual(idx, sorted(idx))
        self.assertTrue(idx)

    def test_zero_radius_finds_nothing(self):
        m = mesh.make_icosphere(1)
        self.assertEqual(m.vertices_in_radius((0, 0, 0), 0.0), [])

    def test_index_survives_a_vertex_leaving_the_original_extent(self):
        m = mesh.make_icosphere(2, 1.0)
        m.grid()
        m.set_vertex(0, (100.0, 100.0, 100.0))
        m.refresh_index()
        self.assertEqual(m.vertices_in_radius((100, 100, 100), 0.5), [0])

    def test_index_rebuilds_once_the_drift_exceeds_half_a_cell(self):
        m = mesh.make_icosphere(2, 1.0)
        grid = m.grid()
        m.touch([0], drift=grid.cell * 10.0)
        m.grid()
        self.assertEqual(m.grid_drift, 0.0)

    def test_grid_repr(self):
        m = mesh.make_icosphere(1)
        self.assertIn("SpatialGrid", repr(m.grid()))


# ==========================================================================
# falloff curves
# ==========================================================================

class TestFalloff(unittest.TestCase):

    def test_every_curve_is_bounded_and_monotonic(self):
        for name in brushes.FALLOFFS:
            previous = None
            for k in range(0, 201):
                t = k / 200.0
                v = brushes.falloff(name, t)
                self.assertGreaterEqual(v, 0.0, name)
                self.assertLessEqual(v, 1.0, name)
                if previous is not None:
                    self.assertLessEqual(v, previous + 1e-15,
                                         "%s not monotonic at t=%f"
                                         % (name, t))
                previous = v

    def test_endpoints(self):
        for name in brushes.FALLOFFS:
            self.assertEqual(brushes.falloff(name, 0.0), 1.0, name)
            self.assertEqual(brushes.falloff(name, 1.0), 0.0, name)
            self.assertEqual(brushes.falloff(name, -0.5), 1.0, name)
            self.assertEqual(brushes.falloff(name, 5.0), 0.0, name)

    def test_constant_is_flat_until_the_rim(self):
        for t in (0.0, 0.25, 0.5, 0.99):
            self.assertEqual(brushes.falloff("constant", t), 1.0)
        self.assertEqual(brushes.falloff("constant", 1.0), 0.0)

    def test_sharp_falls_faster_than_linear_faster_than_root(self):
        for t in (0.2, 0.5, 0.8):
            self.assertLess(brushes.falloff("sharp", t),
                            brushes.falloff("linear", t))
            self.assertLess(brushes.falloff("linear", t),
                            brushes.falloff("root", t))

    def test_unknown_curve_is_rejected(self):
        with self.assertRaises(ValueError):
            brushes.falloff("nope", 0.5)
        with self.assertRaises(ValueError):
            BrushParams(falloff="nope")

    def test_pressure_curves(self):
        for curve in brushes.PRESSURE_CURVES:
            self.assertEqual(brushes.apply_pressure_curve(0.0, curve), 0.0)
            self.assertEqual(brushes.apply_pressure_curve(1.0, curve), 1.0)
        self.assertAlmostEqual(brushes.apply_pressure_curve(0.5, "soft"), 0.25)
        self.assertAlmostEqual(brushes.apply_pressure_curve(0.25, "hard"), 0.5)
        self.assertEqual(brushes.apply_pressure_curve(0.1, "constant"), 1.0)
        self.assertAlmostEqual(brushes.apply_pressure_curve(0.5, 2.0), 0.25)
        with self.assertRaises(ValueError):
            brushes.apply_pressure_curve(0.5, "nope")


# ==========================================================================
# brush parameters and presets
# ==========================================================================

class TestBrushParams(unittest.TestCase):

    def test_every_preset_builds(self):
        for name in brushes.PRESETS:
            params = brushes.preset(name)
            self.assertEqual(params.name, name)
            self.assertIn(params.kind, brushes.BRUSH_KINDS)

    def test_unknown_preset(self):
        with self.assertRaises(KeyError):
            brushes.preset("nope")
        with self.assertRaises(ValueError):
            BrushParams(kind="nope")

    def test_dict_round_trip(self):
        params = brushes.preset("clay_strips")
        self.assertEqual(BrushParams.from_dict(params.to_dict()), params)

    def test_copy_with_overrides(self):
        params = brushes.preset("draw")
        other = params.copy(radius=0.5, name="big")
        self.assertEqual(other.radius, 0.5)
        self.assertEqual(other.name, "big")
        self.assertEqual(params.radius, brushes.preset("draw").radius)

    def test_invert_flips_the_signed_strength(self):
        params = BrushParams(strength=0.4)
        self.assertEqual(params.signed_strength(), 0.4)
        params.invert = True
        self.assertEqual(params.signed_strength(), -0.4)

    def test_repr(self):
        self.assertIn("draw", repr(brushes.preset("draw")))
        self.assertIn("Dab", repr(Dab((0, 0, 0))))


# ==========================================================================
# stroke resampling
# ==========================================================================

class TestStrokeResampling(unittest.TestCase):

    def test_a_fast_sweep_still_deposits_evenly(self):
        """One frame that jumps twenty radii still lays an even trail."""
        params = BrushParams(radius=0.1, spacing=0.25)
        sampler = brushes.StrokeSampler(params)
        dabs = sampler.begin((0.0, 0.0, 0.0))
        dabs += sampler.move((2.0, 0.0, 0.0))
        self.assertGreater(len(dabs), 30)
        step = params.spacing * 2.0 * params.radius
        for a, b in zip(dabs, dabs[1:]):
            d = math.dist(a.center, b.center)
            self.assertAlmostEqual(d, step, places=9)

    def test_spacing_survives_the_frame_boundary(self):
        """The leftover distance carries across calls, so nothing drifts."""
        params = BrushParams(radius=0.1, spacing=0.3)
        sampler = brushes.StrokeSampler(params)
        dabs = sampler.begin((0.0, 0.0, 0.0))
        x = 0.0
        rng = random.Random(3)
        for _ in range(25):
            x += rng.uniform(0.001, 0.4)
            dabs += sampler.move((x, 0.0, 0.0))
        step = params.spacing * 2.0 * params.radius
        for a, b in zip(dabs, dabs[1:]):
            self.assertAlmostEqual(math.dist(a.center, b.center), step,
                                   places=9)

    def test_a_tiny_move_emits_nothing(self):
        params = BrushParams(radius=0.1, spacing=0.5)
        sampler = brushes.StrokeSampler(params)
        sampler.begin((0.0, 0.0, 0.0))
        self.assertEqual(sampler.move((1e-13, 0.0, 0.0)), [])

    def test_move_before_begin_begins(self):
        sampler = brushes.StrokeSampler(BrushParams())
        self.assertEqual(len(sampler.move((1.0, 0.0, 0.0))), 1)
        self.assertTrue(sampler.active)
        sampler.end()
        self.assertFalse(sampler.active)

    def test_resample_stroke_accepts_three_shapes(self):
        params = BrushParams(radius=0.1, spacing=0.5)
        plain = brushes.resample_stroke([(0, 0, 0), (1, 0, 0)], params)
        with_normal = brushes.resample_stroke(
            [((0, 0, 0), (0, 0, 1)), ((1, 0, 0), (0, 0, 1))], params)
        with_pressure = brushes.resample_stroke(
            [((0, 0, 0), (0, 0, 1), 1.0), ((1, 0, 0), (0, 0, 1), 1.0)], params)
        self.assertEqual(len(plain), len(with_normal))
        self.assertEqual(len(plain), len(with_pressure))
        self.assertEqual(brushes.resample_stroke([], params), [])

    def test_distance_accumulates_along_the_stroke(self):
        params = BrushParams(radius=0.1, spacing=0.5)
        dabs = brushes.resample_stroke([(0, 0, 0), (1, 0, 0)], params)
        for a, b in zip(dabs, dabs[1:]):
            self.assertGreater(b.distance, a.distance)

    def test_pressure_scales_radius_and_strength(self):
        params = BrushParams(radius=0.2, strength=0.5, size_pressure=True,
                             strength_pressure=True)
        sampler = brushes.StrokeSampler(params)
        soft = sampler.begin((0, 0, 0), None, 0.5)[0]
        sampler.end()
        sampler = brushes.StrokeSampler(params)
        hard = sampler.begin((0, 0, 0), None, 1.0)[0]
        self.assertLess(soft.radius, hard.radius)
        self.assertLess(soft.strength, hard.strength)


# ==========================================================================
# the brushes
# ==========================================================================

class TestBrushes(unittest.TestCase):

    def _dab(self, kind, **kw):
        params = BrushParams(kind=kind, radius=0.5, strength=0.4,
                             **{k: v for k, v in kw.items()
                                if k in BrushParams.__slots__})
        direction = kw.get("direction", (0.05, 0.0, 0.0))
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), direction, 0.5,
                  params.signed_strength())
        return params, dab

    def test_every_brush_moves_the_vertices_under_it(self):
        for kind in brushes.BRUSH_KINDS:
            m, stack, layer = _rig()
            if kind == "erase":
                for i in m.vertices_in_radius((0, 0, 1), 0.5):
                    layer.set(i, (0.0, 0.0, 0.1))
                stack.apply_to(m)
            before = list(m.positions)
            params, dab = self._dab(kind)
            touched = apply_dab(m, layer, params, dab, stack=stack)
            self.assertTrue(touched, "%s touched nothing" % kind)
            moved = _moved(before, m.positions)
            self.assertTrue(moved, "%s moved nothing" % kind)
            inside = set(m.vertices_in_radius((0, 0, 1), 0.5))
            for i in moved:
                self.assertIn(i, inside, "%s moved a vertex outside the dab"
                              % kind)

    def test_no_brush_touches_anything_outside_its_radius(self):
        for kind in brushes.BRUSH_KINDS:
            m, stack, layer = _rig()
            if kind == "erase":
                for i in range(m.n_vertices):
                    layer.set(i, (0.0, 0.0, 0.1))
                stack.apply_to(m)
            before = list(m.positions)
            params, dab = self._dab(kind)
            apply_dab(m, layer, params, dab, stack=stack)
            outside = set(range(m.n_vertices)) - set(
                m.vertices_in_radius((0, 0, 1), 0.5))
            for i in outside:
                o = i * 3
                self.assertEqual(m.positions[o], before[o], kind)
                self.assertEqual(m.positions[o + 1], before[o + 1], kind)
                self.assertEqual(m.positions[o + 2], before[o + 2], kind)

    def test_every_brush_leaves_masked_vertices_untouched(self):
        for kind in brushes.BRUSH_KINDS:
            m, stack, layer = _rig()
            if kind == "erase":
                for i in range(m.n_vertices):
                    layer.set(i, (0.0, 0.0, 0.1))
                stack.apply_to(m)
            inside = m.vertices_in_radius((0, 0, 1), 0.5)
            self.assertGreater(len(inside), 4, kind)
            masked = set(inside[:len(inside) // 2])
            vmask = VertexMask(m.n_vertices)
            vmask.mask_indices(masked, 1.0)
            before = list(m.positions)
            params, dab = self._dab(kind)
            apply_dab(m, layer, params, dab, mask=vmask, stack=stack)
            for i in masked:
                o = i * 3
                self.assertEqual(m.positions[o], before[o], kind)
                self.assertEqual(m.positions[o + 1], before[o + 1], kind)
                self.assertEqual(m.positions[o + 2], before[o + 2], kind)
            self.assertTrue(_moved(before, m.positions), kind)

    def test_freeze_stops_a_partial_mask_dead(self):
        m, stack, layer = _rig()
        inside = m.vertices_in_radius((0, 0, 1), 0.5)
        half = set(inside[:len(inside) // 2])
        vmask = VertexMask(m.n_vertices)
        vmask.mask_indices(half, 0.6)
        params, dab = self._dab("draw")

        # without freeze a 0.6 mask lets 40% of the brush through
        soft_mesh, soft_stack, soft_layer = _rig(m.copy())
        before = list(soft_mesh.positions)
        apply_dab(soft_mesh, soft_layer, params, dab, mask=vmask,
                  stack=soft_stack)
        self.assertTrue(set(_moved(before, soft_mesh.positions)) & half)

        vmask.freeze = True
        before = list(m.positions)
        apply_dab(m, layer, params, dab, mask=vmask, stack=stack)
        for i in half:
            o = i * 3
            self.assertEqual(m.positions[o + 2], before[o + 2])
        self.assertTrue(_moved(before, m.positions))

    def test_a_locked_layer_refuses_the_dab(self):
        m, stack, layer = _rig()
        layer.locked = True
        before = list(m.positions)
        params, dab = self._dab("draw")
        self.assertEqual(apply_dab(m, layer, params, dab, stack=stack), [])
        self.assertEqual(list(m.positions), before)

    def test_draw_pushes_along_the_stroke_normal(self):
        m, stack, layer = _rig(mesh.make_grid_mesh(10, 10, 0.1))
        params = BrushParams(kind="draw", radius=0.25, strength=0.5)
        dab = Dab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=0.25,
                  strength=0.5)
        apply_dab(m, layer, params, dab, stack=stack)
        for i in m.vertices_in_radius((0, 0, 0), 0.24):
            self.assertGreater(m.positions[i * 3 + 2], 0.0)
        # the centre moves the most
        centre = min(range(m.n_vertices),
                     key=lambda i: abs(m.positions[i * 3])
                     + abs(m.positions[i * 3 + 1]))
        self.assertAlmostEqual(m.positions[centre * 3 + 2],
                               0.5 * 0.25, places=9)

    def test_inverted_draw_carves(self):
        m, stack, layer = _rig(mesh.make_grid_mesh(10, 10, 0.1))
        params = BrushParams(kind="draw", radius=0.25, strength=0.5,
                             invert=True)
        dab = Dab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=0.25,
                  strength=params.signed_strength())
        apply_dab(m, layer, params, dab, stack=stack)
        for i in m.vertices_in_radius((0, 0, 0), 0.2):
            self.assertLess(m.positions[i * 3 + 2], 0.0)

    def test_inflate_follows_the_vertex_normal(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="inflate", radius=0.5, strength=0.3)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.5, strength=0.3)
        apply_dab(m, layer, params, dab, stack=stack)
        for i in m.vertices_in_radius((0, 0, 1), 0.4):
            p = m.vertex(i)
            self.assertGreater(math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2),
                               1.0)

    def test_flatten_reduces_the_spread_about_the_plane(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="flatten", radius=0.6, strength=1.0)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6, strength=1.0)
        idx = m.vertices_in_radius((0, 0, 1), 0.6)
        spread = max(m.vertex(i)[2] for i in idx) \
            - min(m.vertex(i)[2] for i in idx)
        apply_dab(m, layer, params, dab, stack=stack)
        after = max(m.vertex(i)[2] for i in idx) \
            - min(m.vertex(i)[2] for i in idx)
        self.assertLess(after, spread)

    def test_scrape_only_shaves_and_never_fills(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="scrape", radius=0.6, strength=0.6,
                             plane_offset=-0.05)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6, strength=0.6)
        before = list(m.positions)
        apply_dab(m, layer, params, dab, stack=stack)
        moved = _moved(before, m.positions)
        self.assertTrue(moved)
        for i in moved:
            self.assertLess(m.positions[i * 3 + 2], before[i * 3 + 2])

    def test_clay_only_builds_and_never_carves(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="clay", radius=0.6, strength=0.6,
                             plane_offset=0.1)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6, strength=0.6)
        before = list(m.positions)
        apply_dab(m, layer, params, dab, stack=stack)
        moved = _moved(before, m.positions)
        self.assertTrue(moved)
        for i in moved:
            self.assertGreater(m.positions[i * 3 + 2], before[i * 3 + 2])

    def test_pinch_pulls_towards_the_axis(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="pinch", radius=0.6, strength=0.5)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6, strength=0.5)
        idx = m.vertices_in_radius((0, 0, 1), 0.5)
        before = {i: math.hypot(m.vertex(i)[0], m.vertex(i)[1]) for i in idx}
        apply_dab(m, layer, params, dab, stack=stack)
        closer = 0
        for i in idx:
            after = math.hypot(m.vertex(i)[0], m.vertex(i)[1])
            self.assertLessEqual(after, before[i] + 1e-12)
            if after < before[i] - 1e-12:
                closer += 1
        self.assertGreater(closer, 0)

    def test_inverted_pinch_magnifies(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="pinch", radius=0.6, strength=0.5,
                             invert=True)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6,
                  strength=params.signed_strength())
        idx = m.vertices_in_radius((0, 0, 1), 0.5)
        before = {i: math.hypot(m.vertex(i)[0], m.vertex(i)[1]) for i in idx}
        apply_dab(m, layer, params, dab, stack=stack)
        farther = sum(1 for i in idx
                      if math.hypot(m.vertex(i)[0], m.vertex(i)[1])
                      > before[i] + 1e-12)
        self.assertGreater(farther, 0)

    def test_grab_translates_the_region(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="grab", radius=0.5, strength=1.0,
                             falloff="constant")
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.1, 0.0, 0.0),
                  radius=0.5, strength=1.0)
        before = list(m.positions)
        touched = apply_dab(m, layer, params, dab, stack=stack)
        for i in touched:
            self.assertAlmostEqual(m.positions[i * 3] - before[i * 3], 0.1,
                                   places=12)

    def test_snake_hook_drags_the_tip(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="snake_hook", radius=0.5, strength=1.0)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.2),
                  radius=0.5, strength=1.0)
        before = list(m.positions)
        touched = apply_dab(m, layer, params, dab, stack=stack)
        self.assertTrue(touched)
        for i in touched:
            self.assertGreater(m.positions[i * 3 + 2], before[i * 3 + 2])

    def test_crease_pushes_in_and_pinches(self):
        m, stack, layer = _rig()
        params = BrushParams(kind="crease", radius=0.5, strength=0.4,
                             crease_pinch=0.8)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.5, strength=0.4)
        idx = m.vertices_in_radius((0, 0, 1), 0.4)
        before = {i: (m.vertex(i)[2], math.hypot(m.vertex(i)[0],
                                                 m.vertex(i)[1]))
                  for i in idx}
        apply_dab(m, layer, params, dab, stack=stack)
        for i in idx:
            z, r = before[i]
            self.assertLessEqual(m.vertex(i)[2], z + 1e-12)
            self.assertLessEqual(math.hypot(m.vertex(i)[0], m.vertex(i)[1]),
                                 r + 1e-12)

    def test_erase_reduces_the_active_layer(self):
        m, stack, layer = _rig()
        idx = m.vertices_in_radius((0, 0, 1), 0.5)
        for i in idx:
            layer.set(i, (0.0, 0.0, 0.2))
        stack.apply_to(m)
        params = BrushParams(kind="erase", radius=0.5, strength=1.0,
                             falloff="constant")
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.5, strength=1.0)
        apply_dab(m, layer, params, dab, stack=stack)
        for i in idx:
            self.assertAlmostEqual(layer.get(i)[2], 0.0, places=12)
        self.assertEqual(list(m.positions), list(stack.base))

    def test_erase_leaves_other_layers_alone(self):
        m, stack, layer = _rig()
        below = stack.layers[0]
        idx = m.vertices_in_radius((0, 0, 1), 0.5)
        for i in idx:
            below.set(i, (0.0, 0.0, 0.2))
        top = stack.add_layer("Top")
        stack.apply_to(m)
        params = BrushParams(kind="erase", radius=0.5, strength=1.0)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.5, strength=1.0)
        apply_dab(m, top, params, dab, stack=stack)
        for i in idx:
            self.assertAlmostEqual(below.get(i)[2], 0.2, places=12)

    def test_a_dab_that_hits_nothing_is_a_no_op(self):
        m, stack, layer = _rig()
        params, dab = self._dab("draw")
        far = dab.copy(center=(50.0, 50.0, 50.0))
        self.assertEqual(apply_dab(m, layer, params, far, stack=stack), [])

    def test_without_a_stack_the_mesh_moves_by_the_layer_weight(self):
        m, stack, layer = _rig()
        layer.weight = 0.5
        params = BrushParams(kind="draw", radius=0.5, strength=0.4)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.5, strength=0.4)
        before = list(m.positions)
        touched = apply_dab(m, layer, params, dab)
        for i in touched:
            o = i * 3
            self.assertAlmostEqual(m.positions[o + 2] - before[o + 2],
                                   layer.get(i)[2] * 0.5, places=12)


# ==========================================================================
# smoothing
# ==========================================================================

class TestSmoothing(unittest.TestCase):

    def _noisy_grid(self, seed=5):
        m = mesh.make_grid_mesh(12, 12, 0.1)
        rng = random.Random(seed)
        for i in range(m.n_vertices):
            m.positions[i * 3 + 2] = rng.uniform(-0.05, 0.05)
        m.touch(None)
        return m

    def _roughness(self, m):
        total = 0.0
        for i in range(m.n_vertices):
            c = m.one_ring_centroid(i)
            p = m.vertex(i)
            total += math.dist(p, c)
        return total

    def test_smoothing_converges_monotonically(self):
        m = self._noisy_grid()
        stack = LayerStack(base=list(m.positions))
        layer = stack.add_layer("smooth")
        params = BrushParams(kind="smooth", radius=2.0, strength=0.5,
                             falloff="constant")
        dab = Dab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=2.0, strength=0.5)
        previous = self._roughness(m)
        for _ in range(20):
            apply_dab(m, layer, params, dab, stack=stack)
            now = self._roughness(m)
            self.assertLessEqual(now, previous + 1e-12)
            previous = now
        self.assertLess(previous, self._roughness(self._noisy_grid()) * 0.5)

    def test_smoothing_never_blows_up(self):
        m = self._noisy_grid(seed=6)
        stack = LayerStack(base=list(m.positions))
        layer = stack.add_layer("smooth")
        lo, hi = m.bounds()
        params = BrushParams(kind="smooth", radius=5.0, strength=1.0,
                             falloff="constant")
        dab = Dab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=5.0, strength=1.0)
        for _ in range(100):
            apply_dab(m, layer, params, dab, stack=stack)
        for i in range(m.n_vertices):
            for k in range(3):
                v = m.positions[i * 3 + k]
                self.assertEqual(v, v)              # not NaN
                self.assertGreaterEqual(v, lo[k] - 1e-6)
                self.assertLessEqual(v, hi[k] + 1e-6)

    def test_volume_preserving_smoothing_keeps_the_radius(self):
        plain = mesh.make_icosphere(3, 1.0)
        keep = mesh.make_icosphere(3, 1.0)
        for m, preserve in ((plain, False), (keep, True)):
            stack = LayerStack(base=list(m.positions))
            layer = stack.add_layer("s")
            params = BrushParams(kind="smooth", radius=5.0, strength=0.6,
                                 falloff="constant",
                                 volume_preserving=preserve)
            dab = Dab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=5.0,
                      strength=0.6)
            for _ in range(10):
                apply_dab(m, layer, params, dab, stack=stack)

        def _mean_radius(m):
            total = 0.0
            for i in range(m.n_vertices):
                p = m.vertex(i)
                total += math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
            return total / m.n_vertices

        self.assertLess(_mean_radius(plain), 0.999)
        self.assertGreater(_mean_radius(keep), _mean_radius(plain))
        self.assertAlmostEqual(_mean_radius(keep), 1.0, places=3)


# ==========================================================================
# masks
# ==========================================================================

class TestMasking(unittest.TestCase):

    def test_factor_is_one_minus_the_mask(self):
        m = VertexMask(4)
        m[1] = 0.25
        self.assertEqual(m.factor(0), 1.0)
        self.assertAlmostEqual(m.factor(1), 0.75, places=6)

    def test_freeze_is_a_hard_stop(self):
        m = VertexMask(4, freeze=True, freeze_threshold=0.5)
        m[1] = 0.6
        m[2] = 0.4
        self.assertEqual(m.factor(1), 0.0)
        self.assertAlmostEqual(m.factor(2), 0.6, places=6)
        self.assertTrue(m.is_frozen(1))
        self.assertFalse(m.is_frozen(2))

    def test_values_are_clamped(self):
        m = VertexMask(2)
        m[0] = 5.0
        m[1] = -5.0
        self.assertEqual(m[0], 1.0)
        self.assertEqual(m[1], 0.0)

    def test_invert_and_clear(self):
        m = VertexMask(3)
        m[0] = 1.0
        m.invert()
        self.assertEqual(list(m.values), [0.0, 1.0, 1.0])
        m.clear()
        self.assertFalse(m.any())
        m.fill(0.5)
        self.assertEqual(m.count(), 3)

    def test_paint_modes(self):
        m = VertexMask(3)
        m.paint({0: 0.5}, mode="add")
        m.paint({0: 0.2}, mode="add")
        self.assertAlmostEqual(m[0], 0.7, places=6)
        m.paint({0: 0.3}, mode="subtract")
        self.assertAlmostEqual(m[0], 0.4, places=6)
        m.paint({0: 0.9}, mode="set")
        self.assertAlmostEqual(m[0], 0.9, places=6)

    def test_paint_sphere_follows_the_falloff(self):
        mm = mesh.make_grid_mesh(10, 10, 0.1)
        m = VertexMask(mm.n_vertices)
        touched = m.paint_sphere(mm, (0.0, 0.0, 0.0), 0.25, curve="linear")
        self.assertTrue(touched)
        centre = min(touched, key=lambda i: abs(mm.vertex(i)[0])
                     + abs(mm.vertex(i)[1]))
        self.assertAlmostEqual(m[centre], 1.0, places=6)
        for i in range(mm.n_vertices):
            if i not in touched:
                self.assertEqual(m[i], 0.0)

    def test_blur_evens_a_spike_out(self):
        mm = mesh.make_grid_mesh(8, 8, 0.1)
        m = VertexMask(mm.n_vertices)
        centre = mm.n_vertices // 2
        m[centre] = 1.0
        peak = m[centre]
        m.blur(mm, 2)
        self.assertLess(m[centre], peak)
        self.assertGreater(sum(m.values), 0.0)

    def test_blur_is_order_independent(self):
        mm = mesh.make_grid_mesh(6, 6, 0.1)
        a = VertexMask(mm.n_vertices)
        b = VertexMask(mm.n_vertices)
        for i in (3, 10, 20):
            a[i] = 1.0
            b[i] = 1.0
        a.blur(mm, 1)
        b.blur(mm, 1)
        self.assertEqual(list(a.values), list(b.values))

    def test_cavity_masking_finds_the_pits(self):
        mm = mesh.make_grid_mesh(10, 10, 0.1)
        dent = mm.n_vertices // 2
        mm.positions[dent * 3 + 2] = -0.05
        mm.touch(None)
        m = VertexMask(mm.n_vertices)
        m.mask_by_cavity(mm, blur=0)
        self.assertGreater(m[dent], 0.0)
        corner = 0
        self.assertLessEqual(m[corner], m[dent])

    def test_sharpen_pushes_to_the_extremes(self):
        m = VertexMask(3)
        m[0] = 0.3
        m[1] = 0.8
        m.sharpen(0.5)
        self.assertEqual(list(m.values), [0.0, 1.0, 0.0])

    def test_resize_and_copy(self):
        m = VertexMask(4)
        m[1] = 1.0
        m.resize(6)
        self.assertEqual(len(m), 6)
        m.resize(2)
        self.assertEqual(len(m), 2)
        c = m.copy()
        c[0] = 1.0
        self.assertNotEqual(list(c.values), list(m.values))

    def test_byte_round_trip(self):
        m = VertexMask(4)
        m[0] = 1.0
        m[1] = 0.5019607843137255      # 128/255, exact under the quantiser
        blob = m.to_bytes()
        self.assertEqual(len(blob), 4)
        back = VertexMask.from_bytes(blob)
        for i in range(4):
            self.assertAlmostEqual(back[i], m[i], places=6)

    def test_dict_round_trip(self):
        m = VertexMask(3, freeze=True)
        m[2] = 1.0
        back = VertexMask.from_dict(m.to_dict())
        self.assertTrue(back.freeze)
        self.assertEqual(list(back.values), list(m.values))

    def test_repr(self):
        self.assertIn("VertexMask", repr(VertexMask(2)))


# ==========================================================================
# symmetry
# ==========================================================================

class TestSymmetry(unittest.TestCase):

    def _mirror_index(self, m, axis=0):
        """``{index: mirrored index}`` for a mesh that is an exact mirror."""
        lookup = {}
        for i in range(m.n_vertices):
            lookup[m.vertex(i)] = i
        out = {}
        for i in range(m.n_vertices):
            p = list(m.vertex(i))
            p[axis] = -p[axis]
            j = lookup.get(tuple(p))
            if j is not None:
                out[i] = j
        return out

    def test_the_test_grid_really_is_an_exact_mirror(self):
        # a power-of-two spacing keeps every coordinate exactly representable,
        # so the mirror is a bit pattern flip and the test can assert equality
        m = mesh.make_grid_mesh(8, 8, 0.25)
        pairs = self._mirror_index(m)
        self.assertEqual(len(pairs), m.n_vertices)

    def test_mirrored_strokes_are_exact(self):
        m, stack, layer = _rig(mesh.make_grid_mesh(8, 8, 0.25))
        pairs = self._mirror_index(m)
        sym = Symmetry(axes=(True, False, False))
        params = BrushParams(kind="draw", radius=0.45, strength=0.5)
        dab = Dab((0.5, 0.25, 0.0), (0.0, 0.0, 1.0), radius=0.45,
                  strength=0.5)
        touched = set()
        for d in sym.expand(dab):
            touched.update(apply_dab(m, layer, params, d, stack=stack))
        self.assertTrue(touched)
        for i in touched:
            j = pairs[i]
            self.assertIn(j, touched)
            a = layer.get(i)
            b = layer.get(j)
            self.assertEqual(a[2], b[2])          # exact, not almost
            self.assertEqual(a[0], -b[0])
            self.assertEqual(a[1], b[1])

    def test_expand_produces_2n_dabs(self):
        dab = Dab((0.3, 0.4, 0.5), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
        self.assertEqual(len(Symmetry().expand(dab)), 1)
        self.assertEqual(len(Symmetry(axes=(True, False, False))
                             .expand(dab)), 2)
        self.assertEqual(len(Symmetry(axes=(True, True, False))
                             .expand(dab)), 4)
        self.assertEqual(len(Symmetry(axes=(True, True, True))
                             .expand(dab)), 8)

    def test_a_dab_on_the_plane_is_not_duplicated(self):
        dab = Dab((0.0, 0.4, 0.5), (0.0, 0.0, 1.0))
        self.assertEqual(len(Symmetry(axes=(True, False, False))
                             .expand(dab)), 1)

    def test_mirrored_centres_and_normals(self):
        dab = Dab((0.3, 0.4, 0.5), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        out = Symmetry(axes=(True, False, False)).expand(dab)
        self.assertEqual(out[1].center, (-0.3, 0.4, 0.5))
        self.assertEqual(out[1].normal, (-1.0, 0.0, 0.0))
        self.assertEqual(out[1].direction, (0.0, 1.0, 0.0))

    def test_mirror_about_a_moved_origin(self):
        sym = Symmetry(axes=(True, False, False), origin=(1.0, 0.0, 0.0))
        out = sym.expand(Dab((3.0, 0.0, 0.0)))
        self.assertEqual(out[1].center, (-1.0, 0.0, 0.0))

    def test_radial_symmetry_repeats_the_dab(self):
        sym = Symmetry(radial=6, radial_axis="Z")
        out = sym.expand(Dab((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        self.assertEqual(len(out), 6)
        for d in out:
            self.assertAlmostEqual(math.hypot(d.center[0], d.center[1]), 1.0,
                                   places=12)
        angles = sorted(round(math.atan2(d.center[1], d.center[0]), 6)
                        for d in out)
        self.assertEqual(len(set(angles)), 6)

    def test_radial_and_mirror_combine(self):
        sym = Symmetry(axes=(True, False, False), radial=3, radial_axis="Z")
        self.assertEqual(len(sym.expand(Dab((1.0, 0.3, 0.0)))), 6)

    def test_plane_vertices_uses_the_tolerance(self):
        m = mesh.make_grid_mesh(4, 4, 1.0)
        sym = Symmetry(axes=(True, False, False), tolerance=1e-9)
        on_plane = sym.plane_vertices(m.positions)
        self.assertTrue(on_plane)
        for i in on_plane:
            self.assertAlmostEqual(m.vertex(i)[0], 0.0, places=12)

    def test_constrain_keeps_seam_vertices_on_the_plane(self):
        m, stack, layer = _rig(mesh.make_grid_mesh(8, 8, 0.25))
        sym = Symmetry(axes=(True, False, False), tolerance=1e-9)
        seam = sym.plane_vertices(stack.base)
        for i in seam:
            layer.set(i, (0.05, 0.0, 0.1))
        self.assertEqual(sym.constrain(layer, stack.base), len(seam))
        stack.apply_to(m)
        for i in seam:
            self.assertEqual(layer.get(i)[0], 0.0)
            self.assertEqual(m.vertex(i)[0], stack.base[i * 3])
            self.assertAlmostEqual(layer.get(i)[2], 0.1, places=12)

    def test_symmetric_session_stroke_stays_in_one_layer(self):
        m = mesh.make_grid_mesh(8, 8, 0.25)
        session = SculptSession(mode="SCULPT")
        session.add_target("Grid", m)
        session.set_symmetry("X", True)
        session.set_tool("draw")
        session.set_radius(0.45)
        session.on_trigger(0, 1.0, position=(0.5, 0.25, 0.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        self.assertEqual(len(session.active_stack()), 1)
        pairs = TestSymmetry()._mirror_index(m)
        layer = session.active_layer
        for i in layer.indices():
            self.assertEqual(layer.get(i)[2], layer.get(pairs[i])[2])

    def test_axis_helpers(self):
        sym = Symmetry()
        self.assertFalse(sym.enabled)
        self.assertTrue(sym.toggle_axis("Y"))
        self.assertTrue(sym.enabled)
        self.assertFalse(sym.toggle_axis(1))
        sym.set_radial(4, "Z")
        self.assertTrue(sym.enabled)
        sym.clear()
        self.assertFalse(sym.enabled)
        with self.assertRaises(ValueError):
            sym.set_axis("W")

    def test_dict_round_trip(self):
        sym = Symmetry(axes=(True, False, True), origin=(1, 2, 3),
                       tolerance=1e-4, radial=5, radial_axis="X")
        back = Symmetry.from_dict(sym.to_dict())
        self.assertEqual(back.axes, sym.axes)
        self.assertEqual(back.origin, sym.origin)
        self.assertEqual(back.tolerance, sym.tolerance)
        self.assertEqual(back.radial, sym.radial)
        self.assertEqual(back.radial_axis, sym.radial_axis)
        self.assertIn("Symmetry", repr(sym))


# ==========================================================================
# topology
# ==========================================================================

class TestTopology(unittest.TestCase):

    def test_uniform_subdivision_adds_vertices_without_holes(self):
        m = mesh.make_icosphere(1, 1.0)
        out, topo = topology.subdivide_uniform(m)
        self.assertGreater(out.n_vertices, m.n_vertices)
        self.assertEqual(out.n_faces, m.n_faces * 4)
        self.assertTrue(out.is_manifold())
        self.assertTrue(out.is_closed())
        self.assertEqual(out.boundary_edges(), [])
        edges = len(out.edge_face_count())
        self.assertEqual(out.n_vertices - edges + out.n_faces, 2)
        self.assertEqual(topo.new_count, out.n_vertices)

    def test_subdivision_keeps_a_grid_boundary_intact(self):
        m = mesh.make_grid_mesh(3, 3, 1.0)
        out, _ = topology.subdivide_uniform(m)
        self.assertTrue(out.is_manifold())
        # each of the 12 boundary edges becomes two
        self.assertEqual(len(out.boundary_edges()),
                         len(m.boundary_edges()) * 2)

    def test_two_levels(self):
        m = mesh.make_icosphere(0, 1.0)
        out, topo = topology.subdivide_uniform(m, levels=2)
        self.assertEqual(out.n_faces, m.n_faces * 16)
        self.assertTrue(out.is_closed())
        self.assertEqual(topo.new_count, out.n_vertices)

    def test_adaptive_subdivision_is_conforming(self):
        m = mesh.make_icosphere(2, 1.0)
        out, topo = topology.subdivide_in_radius(m, (0, 0, 1), 0.5)
        self.assertGreater(out.n_vertices, m.n_vertices)
        self.assertLess(out.n_faces, m.n_faces * 4)
        self.assertTrue(out.is_manifold())
        self.assertTrue(out.is_closed())
        edges = len(out.edge_face_count())
        self.assertEqual(out.n_vertices - edges + out.n_faces, 2)

    def test_adaptive_subdivision_respects_min_edge(self):
        m = mesh.make_icosphere(2, 1.0)
        out, _ = topology.subdivide_in_radius(m, (0, 0, 1), 0.5,
                                              min_edge=10.0)
        self.assertEqual(out.n_vertices, m.n_vertices)

    def test_adaptive_subdivision_outside_the_mesh_is_a_no_op(self):
        m = mesh.make_icosphere(1, 1.0)
        out, topo = topology.subdivide_in_radius(m, (50, 50, 50), 0.1)
        self.assertEqual(out.n_vertices, m.n_vertices)
        self.assertEqual(topo.old_to_new, list(range(m.n_vertices)))

    def test_decimation_reduces_and_stays_manifold(self):
        m = mesh.make_icosphere(3, 1.0)
        out, topo = topology.collapse_short_edges(
            m, m.average_edge_length() * 1.5)
        self.assertLess(out.n_vertices, m.n_vertices)
        self.assertLess(out.n_faces, m.n_faces)
        self.assertTrue(out.is_manifold())
        self.assertTrue(out.is_closed())
        edges = len(out.edge_face_count())
        self.assertEqual(out.n_vertices - edges + out.n_faces, 2)

    def test_decimation_never_makes_a_hole_in_a_sheet(self):
        m = mesh.make_grid_mesh(8, 8, 0.1)
        out, _ = topology.collapse_short_edges(
            m, m.average_edge_length() * 1.5)
        self.assertTrue(out.is_manifold())
        self.assertEqual(len(out.boundary_edges()), len(m.boundary_edges()))

    def test_decimation_below_the_threshold_is_a_no_op(self):
        m = mesh.make_icosphere(2, 1.0)
        out, topo = topology.collapse_short_edges(m, 1e-9)
        self.assertEqual(out.n_vertices, m.n_vertices)
        self.assertEqual(topo.old_to_new, list(range(m.n_vertices)))

    def test_remesh_moves_edges_towards_the_target(self):
        m = mesh.make_icosphere(2, 1.0)
        target = m.average_edge_length() * 0.6
        out, _ = topology.remesh(m, target, iterations=3)
        self.assertTrue(out.is_manifold())
        self.assertTrue(out.is_closed())
        self.assertLess(abs(out.average_edge_length() - target),
                        abs(m.average_edge_length() - target))

    def test_remesh_rejects_a_bad_target(self):
        with self.assertRaises(ValueError):
            topology.remesh(mesh.make_icosphere(1), 0.0)

    def test_subdivision_preserves_the_sculpted_surface(self):
        """A midpoint's displacement is the mean of its parents', so the
        subdivided sculpt passes exactly through the old vertices."""
        m, stack, layer = _rig(mesh.make_icosphere(2, 1.0))
        params = BrushParams(kind="draw", radius=0.6, strength=0.4)
        dab = Dab((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), radius=0.6, strength=0.4)
        apply_dab(m, layer, params, dab, stack=stack)
        before = list(m.positions)
        out, topo = topology.subdivide_uniform(m)
        topo.remap_stack(stack)
        evaluated = stack.evaluate()
        for old in range(m.n_vertices):
            new = topo.old_to_new[old]
            for k in range(3):
                self.assertAlmostEqual(evaluated[new * 3 + k],
                                       before[old * 3 + k], places=12)

    def test_remap_mask_interpolates(self):
        m = mesh.make_icosphere(1, 1.0)
        vmask = VertexMask(m.n_vertices)
        vmask.fill(1.0)
        out, topo = topology.subdivide_uniform(m)
        topo.remap_mask(vmask)
        self.assertEqual(len(vmask), out.n_vertices)
        for v in vmask.values:
            self.assertAlmostEqual(v, 1.0, places=6)

    def test_remap_layer_is_still_sparse(self):
        m = mesh.make_icosphere(2, 1.0)
        stack = LayerStack(base=list(m.positions))
        layer = stack.add_layer("a")
        layer.set(0, (0.1, 0.0, 0.0))
        out, topo = topology.subdivide_uniform(m)
        remapped = topo.remap_layer(layer)
        self.assertLess(len(remapped), out.n_vertices)
        self.assertGreater(len(remapped), 0)

    def test_topology_map_repr(self):
        m = mesh.make_icosphere(1)
        _, topo = topology.subdivide_uniform(m)
        self.assertIn("TopologyMap", repr(topo))

    def test_session_subdivide_keeps_the_shape(self):
        m = mesh.make_icosphere(2, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        session.set_tool("draw")
        session.set_radius(0.6)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        target = session.active_target()
        before = {i: target.mesh.vertex(i)
                  for i in range(target.mesh.n_vertices)}
        n_before = target.mesh.n_vertices
        session.subdivide()
        self.assertGreater(target.mesh.n_vertices, n_before)
        self.assertFalse(target.history.can_undo())
        for old, p in before.items():
            for k in range(3):
                self.assertAlmostEqual(target.mesh.vertex(old)[k], p[k],
                                       places=12)


# ==========================================================================
# the session
# ==========================================================================

class _FakeButtons(object):
    def __init__(self, trigger=0.0, grab=0.0, lever_x=0.0, lever_y=0.0):
        self.trigger = trigger
        self.grab = grab
        self.lever_x = lever_x
        self.lever_y = lever_y


class _FakeTransform(object):
    def __init__(self, position):
        self.translation = self
        self._p = position

    def getValue(self):
        return self._p


class _FakeController(object):
    """Duck type of ``xrcore.controllerXR.xrController``, for update()."""

    def __init__(self, position=(0.0, 0.0, 1.0), trigger=0.0):
        self.position = position
        self.buttons = _FakeButtons(trigger=trigger)

    def get_buttons_states(self):
        return self.buttons

    def get_global_transf(self):
        return _FakeTransform(self.position)

    def find_ray_axis(self):
        return (0.0, 0.0, -1.0)


class TestSession(unittest.TestCase):

    def test_modes(self):
        session = SculptSession()
        self.assertIsNone(session.mode)
        session.mode = "sculpt"
        self.assertEqual(session.mode, "SCULPT")
        session.set_mode("MASK")
        self.assertEqual(session.mode, "MASK")
        session.set_mode(None)
        self.assertIsNone(session.mode)
        with self.assertRaises(ValueError):
            session.set_mode("PAINT")

    def test_targets(self):
        session = SculptSession()
        m = mesh.make_icosphere(1)
        t = session.add_target("Body", m)
        self.assertIs(session.add_target("Body", m), t)
        self.assertIs(session.active_target(), t)
        self.assertEqual(session.active_stack().n_vertices, m.n_vertices)
        self.assertIsNone(session.set_active_target("nope"))
        session.remove_target("Body")
        self.assertIsNone(session.active_target())

    def test_update_drives_a_stroke(self):
        m = mesh.make_icosphere(3, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        session.set_tool("draw")
        session.set_radius(0.5)
        controller = _FakeController((0.0, 0.0, 1.0), trigger=1.0)
        self.assertTrue(session.update(1.0 / 72.0, [controller]))
        controller.position = (0.2, 0.0, 1.0)
        session.update(1.0 / 72.0, [controller])
        controller.buttons.trigger = 0.0
        session.update(1.0 / 72.0, [controller])
        self.assertEqual(len(session.active_stack()), 1)
        self.assertGreater(len(session.active_layer), 0)
        self.assertTrue(session.active_target().history.can_undo())

    def test_update_without_a_mode_does_nothing(self):
        session = SculptSession()
        session.add_target("Body", mesh.make_icosphere(1))
        self.assertFalse(session.update(0.1, [_FakeController(trigger=1.0)]))

    def test_update_tolerates_a_broken_controller(self):
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", mesh.make_icosphere(1))
        self.assertFalse(session.update(0.1, [None, object()]))

    def test_grip_cancels_a_stroke(self):
        m = mesh.make_icosphere(2, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        session.set_radius(0.5)
        pristine = list(m.positions)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        self.assertTrue(session.on_grip(0, 1.0))
        self.assertEqual(list(m.positions), pristine)

    def test_thumbstick_scrubs_radius_and_strength(self):
        session = SculptSession(mode="SCULPT")
        r = session.brush.radius
        s = session.brush.strength
        self.assertTrue(session.on_thumbstick(0, 1.0, 1.0, dt=0.5))
        self.assertGreater(session.brush.radius, r)
        self.assertGreater(session.brush.strength, s)
        self.assertFalse(session.on_thumbstick(0, 0.0, 0.0))

    def test_tool_selection(self):
        session = SculptSession()
        self.assertTrue(session.set_tool("clay_strips"))
        self.assertEqual(session.brush.kind, "clay")
        self.assertFalse(session.set_tool("nope"))
        self.assertTrue(session.set_falloff("sharp"))
        self.assertFalse(session.set_falloff("nope"))
        self.assertTrue(session.set_invert(True))
        self.assertEqual(session.set_strength(0.9), 0.9)

    def test_mask_mode_paints_instead_of_sculpting(self):
        m = mesh.make_grid_mesh(10, 10, 0.1)
        session = SculptSession(mode="MASK")
        session.add_target("Grid", m)
        session.set_radius(0.25)
        pristine = list(m.positions)
        session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        self.assertEqual(list(m.positions), pristine)
        self.assertTrue(session.active_mask().any())
        session.invert_mask()
        session.blur_mask(1)
        session.mask_by_cavity()
        session.clear_mask()
        self.assertFalse(session.active_mask().any())
        self.assertTrue(session.set_freeze(True))

    def test_layer_operations_need_a_target(self):
        session = SculptSession()
        with self.assertRaises(RuntimeError):
            session.add_layer()

    def test_layer_operations(self):
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", mesh.make_icosphere(1))
        session.add_layer("A")
        session.add_layer("B")
        self.assertEqual(len(session.active_stack()), 2)
        session.rename_layer(0, "Bottom")
        self.assertEqual(session.active_stack()[0].name, "Bottom")
        session.set_layer_visible(0, False)
        self.assertFalse(session.active_stack()[0].visible)
        session.set_layer_locked(0, True)
        session.set_layer_blend(1, "replace")
        self.assertEqual(session.move_layer(0, 1), 1)
        session.duplicate_layer(0)
        self.assertEqual(len(session.active_stack()), 3)
        session.invert_layer(0)
        session.clear_layer(0)
        session.remove_layer(0)
        self.assertEqual(len(session.active_stack()), 2)
        with self.assertRaises(ValueError):
            session.set_layer_blend(0, "screen")

    def test_merge_down_needs_a_layer_below(self):
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", mesh.make_icosphere(1))
        session.add_layer("only")
        self.assertIsNone(session.merge_layer_down(0))

    def test_detach_and_bind(self):
        session = SculptSession(mode="SCULPT")
        session.attach_scenegraph(object())
        widget = type("W", (), {"vp_reg": "VP", "camera": "CAM"})()
        session.bind_viewer(widget)
        self.assertEqual(session.viewport_region, "VP")
        self.assertEqual(session.camera, "CAM")
        session.detach()
        self.assertIsNone(session.root)
        self.assertIsNone(session.viewport_region)
        self.assertIsNone(session.bind_viewer(None))

    def test_reset_throws_the_sculpt_away(self):
        m = mesh.make_icosphere(2, 1.0)
        session = SculptSession(mode="SCULPT")
        target = session.add_target("Body", m)
        session.set_radius(0.5)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        target.reset()
        self.assertEqual(list(m.positions), list(target.stack.base))
        self.assertFalse(target.history.can_undo())

    def test_repr(self):
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", mesh.make_icosphere(0))
        self.assertIn("SculptSession", repr(session))
        self.assertIn("SculptTarget", repr(session.active_target()))


# ==========================================================================
# preferences
# ==========================================================================

class TestPrefs(unittest.TestCase):

    def tearDown(self):
        prefs.clear_overrides()

    def test_defaults_without_freecad(self):
        self.assertEqual(prefs.get("SculptBrush"), "draw")
        self.assertIsInstance(prefs.get("SculptRadius"), float)
        self.assertIsInstance(prefs.get("SculptUndoSteps"), int)
        self.assertIsInstance(prefs.get("SculptPressureEnabled"), bool)

    def test_overrides_win(self):
        prefs.set_override("SculptRadius", 0.25)
        self.assertEqual(prefs.get_float("SculptRadius"), 0.25)
        prefs.set_override("SculptBrush", "clay")
        self.assertEqual(prefs.get_string("SculptBrush"), "clay")
        prefs.set_override("SculptUndoSteps", 7)
        self.assertEqual(prefs.get_int("SculptUndoSteps"), 7)
        prefs.set_override("SculptSymmetryX", True)
        self.assertTrue(prefs.get_bool("SculptSymmetryX"))
        prefs.clear_overrides()
        self.assertNotEqual(prefs.get_float("SculptRadius"), 0.25)

    def test_unknown_key_falls_back_to_the_given_default(self):
        self.assertEqual(prefs.get_int("Nope", 3), 3)
        self.assertEqual(prefs.get_string("Nope", "x"), "x")


if __name__ == "__main__":
    unittest.main()
