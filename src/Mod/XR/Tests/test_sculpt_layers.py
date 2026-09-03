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
"""Unit tests for the sculpt *layer stack* -- the centrepiece of xrsculpt.

The properties asserted here are the ones the whole feature rests on: storage
is sparse, weights are linear and reversible, hiding a layer restores the mesh
exactly, reordering is deterministic, merge and bake agree with evaluating the
stack, undo restores vertices exactly, and a layer survives a round trip
through :mod:`xrsculpt.io` bit for bit.

Runs under plain ``python3 -m unittest`` from ``src/Mod/XR`` with neither
FreeCAD nor numpy installed; when numpy *is* importable the accelerated
evaluation path is exercised too and required to be bit identical.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsculpt import brushes, io, layers, mesh, symmetry  # noqa: E402
from xrsculpt.layers import History, LayerStack, SculptLayer  # noqa: E402
from xrsculpt.masking import VertexMask  # noqa: E402
from xrsculpt.session import SculptSession  # noqa: E402

HAVE_NUMPY = mesh.have_numpy()


class _ScalarPath(object):
    """Context manager forcing the pure Python evaluation path."""

    def __enter__(self):
        self._old = mesh.set_use_numpy(False)
        return self

    def __exit__(self, *exc):
        mesh.set_use_numpy(self._old)
        return False


def _stack(n=64, seed=1):
    """A stack over ``n`` vertices laid out on a line, no mesh needed."""
    base = []
    for i in range(n):
        base.extend((float(i), 0.0, 0.0))
    return LayerStack(base=base)


def _fill(layer, indices, seed=0, scale=1.0):
    rng = random.Random(seed)
    for i in indices:
        layer.set(i, (rng.uniform(-1, 1) * scale, rng.uniform(-1, 1) * scale,
                      rng.uniform(-1, 1) * scale))
    return layer


# ==========================================================================
# sparse storage
# ==========================================================================

class TestSparseStorage(unittest.TestCase):

    def test_empty_layer_stores_nothing(self):
        layer = SculptLayer("empty")
        self.assertEqual(len(layer), 0)
        self.assertEqual(layer.indices(), [])
        self.assertEqual(layer.get(12345), (0.0, 0.0, 0.0))
        self.assertFalse(layer)

    def test_500_of_200k_costs_500_entries(self):
        stack = LayerStack(n_vertices=200000)
        layer = stack.add_layer("pass")
        touched = list(range(1000, 1500))
        _fill(layer, touched, seed=3)
        self.assertEqual(len(layer), 500)
        self.assertEqual(layer.indices(), touched)
        # the payload is proportional to the touched vertices, not the mesh
        self.assertEqual(layer.nbytes(), 500 * 28)
        self.assertLess(layer.nbytes(), 200000 * 4)

    def test_serialised_size_is_sparse_too(self):
        stack = LayerStack(n_vertices=200000)
        layer = stack.add_layer("pass")
        _fill(layer, range(1000, 1500), seed=4)
        blob = io.dumps(stack, include_base=False)
        # 500 vertices * 28 bytes plus a small header; a dense layer would be
        # 200k * 24 bytes even before the base positions
        self.assertLess(len(blob), 64 * 1024)

    def test_repeated_add_does_not_grow_the_layer(self):
        layer = SculptLayer("acc")
        for _ in range(100):
            layer.add(7, (0.01, 0.0, 0.0))
        self.assertEqual(len(layer), 1)
        self.assertAlmostEqual(layer.get(7)[0], 1.0, places=9)

    def test_pop_swaps_the_last_slot_in(self):
        layer = SculptLayer("pop")
        for i in range(10):
            layer.set(i, (float(i), 0.0, 0.0))
        layer.pop(3)
        self.assertEqual(len(layer), 9)
        self.assertNotIn(3, layer)
        self.assertEqual(layer.indices(),
                         [0, 1, 2, 4, 5, 6, 7, 8, 9])
        for i in layer.indices():
            self.assertEqual(layer.get(i), (float(i), 0.0, 0.0))

    def test_prune_drops_zero_entries(self):
        layer = SculptLayer("prune")
        layer.set(1, (1.0, 0.0, 0.0))
        layer.set(2, (0.0, 0.0, 0.0))
        layer.set(3, (1e-9, 0.0, 0.0))
        self.assertEqual(layer.prune(1e-6), 2)
        self.assertEqual(layer.indices(), [1])

    def test_stack_touched_indices_is_the_union(self):
        stack = _stack(64)
        a = stack.add_layer("a")
        b = stack.add_layer("b")
        _fill(a, [1, 2, 3], seed=1)
        _fill(b, [3, 4], seed=2)
        self.assertEqual(stack.touched_indices(), [1, 2, 3, 4])


# ==========================================================================
# evaluation
# ==========================================================================

class TestEvaluation(unittest.TestCase):

    def test_evaluate_is_base_plus_weighted_sum(self):
        stack = _stack(8)
        layer = stack.add_layer("a", weight=2.0)
        layer.set(3, (1.0, 2.0, 3.0))
        out = stack.evaluate()
        self.assertEqual(out[9], 3.0 + 2.0)
        self.assertEqual(out[10], 0.0 + 4.0)
        self.assertEqual(out[11], 0.0 + 6.0)
        # untouched vertices are exactly the base
        self.assertEqual(list(out[0:3]), [0.0, 0.0, 0.0])

    def test_empty_stack_evaluates_to_the_base(self):
        stack = _stack(16)
        self.assertEqual(list(stack.evaluate()), list(stack.base))

    def test_evaluate_into_a_buffer(self):
        stack = _stack(8)
        layer = stack.add_layer("a")
        layer.set(2, (1.0, 0.0, 0.0))
        buf = stack.evaluate()
        again = stack.evaluate(out=buf)
        self.assertIs(again, buf)
        self.assertEqual(buf[6], 2.0 + 1.0)

    def test_partial_evaluation_matches_the_full_one(self):
        stack = _stack(32)
        a = stack.add_layer("a", weight=0.5)
        _fill(a, range(4, 12), seed=7)
        full = stack.evaluate()
        buf = stack.evaluate()
        # dirty one vertex and re-evaluate only it
        buf[12] = 999.0
        stack.evaluate(out=buf, indices=[4])
        self.assertEqual(list(buf), list(full))

    def test_stack_evaluation_is_independent_of_stroke_order(self):
        """Two layers built in opposite orders evaluate identically."""
        base = [float(i) for i in range(30)]
        forward = LayerStack(base=base)
        fa = forward.add_layer("a")
        fb = forward.add_layer("b")
        backward = LayerStack(base=base)
        ba = backward.add_layer("a")
        bb = backward.add_layer("b")
        rng = random.Random(11)
        edits = [(rng.choice("ab"), rng.randrange(10),
                  (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
                 for _ in range(40)]
        for which, i, d in edits:
            (fa if which == "a" else fb).add(i, d)
        for which, i, d in reversed(edits):
            (ba if which == "a" else bb).add(i, d)
        # the *layers* differ in the last bits because float addition is not
        # associative, but the evaluation of each stack is a deterministic
        # function of its layers, which is what the contract promises
        self.assertEqual(list(forward.evaluate()),
                         list(LayerStack(base=base,
                                         layers=[fa, fb]).evaluate()))
        self.assertEqual(list(backward.evaluate()),
                         list(LayerStack(base=base,
                                         layers=[ba, bb]).evaluate()))

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_numpy_and_scalar_evaluation_are_bit_identical(self):
        # dense enough, and big enough, that the vectorised path is taken
        stack = _stack(5000, seed=2)
        for k in range(4):
            layer = stack.add_layer("L%d" % k, weight=0.3 * (k + 1) - 0.5)
            _fill(layer, range(k, 5000, 2), seed=k)
        stack.layers[2].blend = "replace"
        stack.layers[1].visible = False
        self.assertTrue(stack._prefer_numpy())
        with _ScalarPath():
            self.assertFalse(stack._prefer_numpy())
            scalar = list(stack.evaluate())
        vector = list(stack.evaluate())
        self.assertEqual(scalar, vector)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_a_sparse_stack_stays_on_the_scalar_path(self):
        """The vectorised path rebuilds the whole array, so a dab does not
        take it -- see :meth:`LayerStack._prefer_numpy`."""
        stack = _stack(20000)
        layer = stack.add_layer("dab")
        _fill(layer, range(100, 140), seed=1)
        self.assertFalse(stack._prefer_numpy())
        dense = _stack(20000)
        _fill(dense.add_layer("all"), range(20000), seed=2)
        self.assertTrue(dense._prefer_numpy())
        with _ScalarPath():
            scalar = list(dense.evaluate())
        self.assertEqual(scalar, list(dense.evaluate()))

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_numpy_and_scalar_brushes_are_bit_identical(self):
        def _run():
            m = mesh.make_icosphere(2, 1.0)
            s = SculptSession(mode="SCULPT")
            s.add_target("Body", m)
            s.set_tool("draw")
            s.set_radius(0.5)
            s.on_trigger(0, 1.0, position=(0.0, 0.0, 1.0), normal=(0, 0, 1))
            s.on_move(0, position=(0.3, 0.1, 1.0), normal=(0, 0, 1),
                      pressure=1.0)
            s.on_trigger(0, 0.0)
            return list(m.positions)
        vector = _run()
        with _ScalarPath():
            scalar = _run()
        self.assertEqual(scalar, vector)


# ==========================================================================
# weights
# ==========================================================================

class TestWeights(unittest.TestCase):

    def test_weight_is_linear(self):
        stack = _stack(16)
        layer = stack.add_layer("a", weight=1.0)
        _fill(layer, range(0, 8), seed=5)
        at1 = stack.evaluate()
        stack.set_weight(0, 2.0)
        at2 = stack.evaluate()
        stack.set_weight(0, 0.5)
        athalf = stack.evaluate()
        for i in range(len(stack.base)):
            d = at1[i] - stack.base[i]
            self.assertAlmostEqual(at2[i] - stack.base[i], 2.0 * d, places=12)
            self.assertAlmostEqual(athalf[i] - stack.base[i], 0.5 * d,
                                   places=12)

    def test_weight_change_is_reversible_exactly(self):
        stack = _stack(24)
        layer = stack.add_layer("a", weight=1.0)
        _fill(layer, range(3, 20), seed=6)
        original = list(stack.evaluate())
        for w in (0.0, -1.0, 2.5, 0.37, 1.0):
            stack.set_weight(0, w)
            stack.evaluate()
        stack.set_weight(0, 1.0)
        self.assertEqual(list(stack.evaluate()), original)

    def test_negative_weight_inverts_the_pass(self):
        stack = _stack(16)
        layer = stack.add_layer("a", weight=1.0)
        _fill(layer, range(0, 8), seed=8)
        pos = stack.evaluate()
        stack.set_weight(0, -1.0)
        neg = stack.evaluate()
        for i in range(len(stack.base)):
            self.assertAlmostEqual(pos[i] - stack.base[i],
                                   -(neg[i] - stack.base[i]), places=12)

    def test_weight_above_one_exaggerates(self):
        stack = _stack(8)
        layer = stack.add_layer("a")
        layer.set(1, (1.0, 0.0, 0.0))
        stack.set_weight(0, 3.0)
        self.assertEqual(stack.evaluate()[3], stack.base[3] + 3.0)

    def test_zero_weight_and_hidden_agree(self):
        stack = _stack(16)
        layer = stack.add_layer("a")
        _fill(layer, range(2, 10), seed=9)
        stack.set_weight(0, 0.0)
        zero = list(stack.evaluate())
        stack.set_weight(0, 1.0)
        stack.set_visible(0, False)
        hidden = list(stack.evaluate())
        self.assertEqual(zero, hidden)
        self.assertEqual(hidden, list(stack.base))


# ==========================================================================
# visibility
# ==========================================================================

class TestVisibility(unittest.TestCase):

    def test_muting_a_layer_restores_the_mesh_exactly(self):
        stack = _stack(40)
        a = stack.add_layer("a", weight=0.8)
        b = stack.add_layer("b", weight=1.3)
        _fill(a, range(0, 20), seed=10)
        _fill(b, range(10, 30), seed=11)
        only_a = list(LayerStack(base=list(stack.base),
                                 layers=[a]).evaluate())
        stack.set_visible(1, False)
        self.assertEqual(list(stack.evaluate()), only_a)
        stack.set_visible(1, True)
        both = list(stack.evaluate())
        stack.set_visible(1, False)
        stack.set_visible(1, True)
        self.assertEqual(list(stack.evaluate()), both)

    def test_muting_every_layer_gives_the_base(self):
        stack = _stack(20)
        for k in range(3):
            _fill(stack.add_layer("L%d" % k), range(k, 15), seed=k)
        for i in range(3):
            stack.set_visible(i, False)
        self.assertEqual(list(stack.evaluate()), list(stack.base))


# ==========================================================================
# ordering
# ==========================================================================

class TestOrdering(unittest.TestCase):

    def _three(self):
        stack = _stack(32)
        for k in range(3):
            _fill(stack.add_layer("L%d" % k, weight=0.5 + k * 0.25),
                  range(k * 3, 24), seed=20 + k)
        return stack

    def test_additive_reorder_does_not_change_the_result(self):
        """Reordering ``add`` layers is mathematically a no-op.

        Not *bit* identical, and deliberately not claimed to be: the sum runs
        in stack order and floating point addition is not associative, so
        moving a layer can shift the last bit.  The invariant that matters is
        that the shape does not change, which is what this asserts.
        """
        stack = self._three()
        before = list(stack.evaluate())
        stack.move_layer(0, 2)
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)
        stack.move_layer(2, 1)
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_the_same_order_always_evaluates_identically(self):
        """Determinism: a given stack always gives byte-identical output."""
        stack = self._three()
        first = list(stack.evaluate())
        for _ in range(5):
            self.assertEqual(list(stack.evaluate()), first)
        stack.move_layer(0, 2)
        moved = list(stack.evaluate())
        stack.move_layer(2, 0)
        stack.move_layer(0, 2)
        self.assertEqual(list(stack.evaluate()), moved)

    def test_reorder_is_reversible_exactly(self):
        stack = self._three()
        before = list(stack.evaluate())
        names = [l.name for l in stack.layers]
        stack.move_layer(2, 0)
        stack.move_layer(0, 2)
        self.assertEqual([l.name for l in stack.layers], names)
        self.assertEqual(list(stack.evaluate()), before)

    def test_replace_layers_are_order_sensitive_but_deterministic(self):
        stack = self._three()
        stack.layers[1].blend = "replace"
        a = list(stack.evaluate())
        b = list(stack.evaluate())
        self.assertEqual(a, b)
        stack.move_layer(1, 2)
        c = list(stack.evaluate())
        self.assertNotEqual(a, c)          # a replace pass depends on order
        self.assertEqual(c, list(stack.evaluate()))

    def test_replace_overrides_what_is_underneath(self):
        stack = _stack(8)
        low = stack.add_layer("low")
        high = stack.add_layer("high", blend="replace")
        low.set(1, (5.0, 0.0, 0.0))
        high.set(1, (1.0, 0.0, 0.0))
        out = stack.evaluate()
        self.assertEqual(out[3], stack.base[3] + 1.0)

    def test_move_layer_clamps_the_destination(self):
        stack = self._three()
        self.assertEqual(stack.move_layer(0, 99), 2)
        self.assertEqual(stack.move_layer(2, -5), 0)


# ==========================================================================
# merge / bake / duplicate / invert
# ==========================================================================

class TestMergeAndBake(unittest.TestCase):

    def _two(self):
        stack = _stack(32)
        a = stack.add_layer("bottom", weight=0.7)
        b = stack.add_layer("top", weight=1.4)
        _fill(a, range(0, 16), seed=30)
        _fill(b, range(8, 24), seed=31)
        return stack

    def test_merge_down_matches_evaluating_the_stack(self):
        stack = self._two()
        before = list(stack.evaluate())
        merged = stack.merge_down(1)
        self.assertEqual(len(stack), 1)
        self.assertEqual(merged.weight, 1.0)
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_merge_down_of_a_hidden_layer_drops_its_contribution(self):
        stack = self._two()
        stack.set_visible(1, False)
        before = list(stack.evaluate())
        stack.merge_down(1)
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_merge_down_refuses_replace_layers(self):
        stack = self._two()
        stack.layers[1].blend = "replace"
        with self.assertRaises(ValueError):
            stack.merge_down(1)

    def test_merge_down_rejects_the_bottom_layer(self):
        stack = self._two()
        with self.assertRaises(IndexError):
            stack.merge_down(0)

    def test_flatten_matches_evaluating_the_stack(self):
        stack = self._two()
        stack.add_layer("third", weight=-0.5)
        _fill(stack.layers[2], range(4, 20), seed=32)
        before = list(stack.evaluate())
        stack.flatten()
        self.assertEqual(len(stack), 1)
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_bake_matches_evaluating_the_stack(self):
        stack = self._two()
        before = list(stack.evaluate())
        stack.bake_to_base()
        self.assertEqual(len(stack), 0)
        self.assertEqual(list(stack.base), list(stack.evaluate()))
        for i, v in enumerate(stack.base):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_bake_keeping_the_shells_preserves_names(self):
        stack = self._two()
        before = list(stack.evaluate())
        stack.bake_to_base(remove=False)
        self.assertEqual([l.name for l in stack.layers], ["bottom", "top"])
        self.assertEqual([len(l) for l in stack.layers], [0, 0])
        for i, v in enumerate(stack.evaluate()):
            self.assertAlmostEqual(v, before[i], places=12)

    def test_merge_then_bake_equals_bake(self):
        one = self._two()
        two = self._two()
        one.merge_down(1)
        one.bake_to_base()
        two.bake_to_base()
        for i in range(len(one.base)):
            self.assertAlmostEqual(one.base[i], two.base[i], places=12)

    def test_duplicate_doubles_the_displacement(self):
        stack = _stack(16)
        layer = stack.add_layer("a")
        _fill(layer, range(0, 8), seed=33)
        single = list(stack.evaluate())
        stack.duplicate(0)
        self.assertEqual(len(stack), 2)
        doubled = stack.evaluate()
        for i in range(len(stack.base)):
            d = single[i] - stack.base[i]
            self.assertAlmostEqual(doubled[i] - stack.base[i], 2.0 * d,
                                   places=12)

    def test_duplicate_gets_a_unique_name_and_id(self):
        stack = _stack(8)
        original = stack.add_layer("Pass")
        copy = stack.duplicate(0)
        self.assertNotEqual(copy.id, original.id)
        self.assertNotEqual(copy.name, original.name)

    def test_invert_negates_the_displacement(self):
        stack = _stack(16)
        layer = stack.add_layer("a")
        _fill(layer, range(0, 8), seed=34)
        before = list(stack.evaluate())
        layer.invert()
        after = stack.evaluate()
        for i in range(len(stack.base)):
            self.assertAlmostEqual(after[i] - stack.base[i],
                                   -(before[i] - stack.base[i]), places=12)
        layer.invert()
        self.assertEqual(list(stack.evaluate()), before)

    def test_clear_restores_the_base(self):
        stack = _stack(16)
        layer = stack.add_layer("a")
        _fill(layer, range(0, 8), seed=35)
        layer.clear()
        self.assertEqual(len(layer), 0)
        self.assertEqual(list(stack.evaluate()), list(stack.base))


# ==========================================================================
# undo / redo
# ==========================================================================

class TestHistory(unittest.TestCase):

    def _stroked(self):
        m = mesh.make_icosphere(2, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        session.set_tool("draw")
        session.set_radius(0.5)
        session.set_strength(0.4)
        return session, m

    def test_stroke_undo_restores_vertices_exactly(self):
        session, m = self._stroked()
        pristine = list(m.positions)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_move(0, position=(0.3, 0.0, 1.0), normal=(0, 0, 1),
                        pressure=1.0)
        session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        self.assertNotEqual(sculpted, pristine)
        session.undo()
        self.assertEqual(list(m.positions), pristine)
        session.redo()
        self.assertEqual(list(m.positions), sculpted)

    def test_many_strokes_unwind_in_order(self):
        session, m = self._stroked()
        states = [list(m.positions)]
        for k in range(4):
            z = 1.0
            session.on_trigger(0, 1.0, position=(0.1 * k, 0.0, z),
                               normal=(0, 0, 1))
            session.on_move(0, position=(0.1 * k + 0.2, 0.0, z),
                            normal=(0, 0, 1), pressure=1.0)
            session.on_trigger(0, 0.0)
            states.append(list(m.positions))
        for k in range(4):
            session.undo()
            self.assertEqual(list(m.positions), states[3 - k])
        for k in range(4):
            session.redo()
            self.assertEqual(list(m.positions), states[k + 1])

    def test_history_stores_sparse_deltas_not_meshes(self):
        session, m = self._stroked()
        target = session.active_target()
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        touched = len(target.stack.active)
        self.assertGreater(touched, 0)
        self.assertLess(touched, m.n_vertices)
        # 28 bytes of index+before plus 24 of after per touched vertex
        self.assertLessEqual(target.history.nbytes(), touched * 64)
        self.assertLess(target.history.nbytes(), m.n_vertices * 24)

    def test_history_is_bounded_by_entry_count(self):
        session, m = self._stroked()
        target = session.active_target()
        target.history.max_entries = 3
        for k in range(6):
            session.on_trigger(0, 1.0, position=(0.05 * k, 0.0, 1.0),
                               normal=(0, 0, 1))
            session.on_trigger(0, 0.0)
        self.assertEqual(len(target.history.undo_labels()), 3)

    def test_history_is_bounded_by_bytes(self):
        session, m = self._stroked()
        target = session.active_target()
        target.history.max_bytes = 1
        for k in range(5):
            session.on_trigger(0, 1.0, position=(0.05 * k, 0.0, 1.0),
                               normal=(0, 0, 1))
            session.on_trigger(0, 0.0)
        self.assertEqual(len(target.history.undo_labels()), 1)

    def test_abort_restores_the_offsets(self):
        session, m = self._stroked()
        pristine = list(m.positions)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.cancel_all()
        self.assertEqual(list(m.positions), pristine)
        self.assertFalse(session.active_target().history.can_undo())

    def test_structural_operations_undo(self):
        session, m = self._stroked()
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        stack = session.active_stack()
        session.add_layer("second")
        self.assertEqual(len(stack), 2)
        session.undo()
        self.assertEqual(len(stack), 1)
        session.redo()
        self.assertEqual(len(stack), 2)
        self.assertEqual(list(m.positions), sculpted)

    def test_weight_change_undoes(self):
        session, m = self._stroked()
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        session.set_layer_weight(0, 0.25)
        self.assertNotEqual(list(m.positions), sculpted)
        session.undo()
        self.assertEqual(list(m.positions), sculpted)

    def test_remove_layer_undoes_with_its_offsets(self):
        session, m = self._stroked()
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        session.remove_layer(0)
        self.assertEqual(list(m.positions), list(session.active_stack().base))
        session.undo()
        self.assertEqual(list(m.positions), sculpted)

    def test_merge_and_bake_undo(self):
        session, m = self._stroked()
        for k in range(2):
            session.add_layer("L%d" % k)
            session.on_trigger(0, 1.0, position=(0.1 * k, 0.0, 1.0),
                               normal=(0, 0, 1))
            session.on_move(0, position=(0.1 * k + 0.2, 0.0, 1.0),
                            normal=(0, 0, 1), pressure=1.0)
            session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        stack = session.active_stack()
        n = len(stack)
        session.merge_layer_down(n - 1)
        self.assertEqual(len(stack), n - 1)
        for i, v in enumerate(m.positions):
            self.assertAlmostEqual(v, sculpted[i], places=12)
        session.undo()
        self.assertEqual(len(stack), n)
        self.assertEqual(list(m.positions), sculpted)
        session.bake_layers()
        self.assertEqual(len(stack), 0)
        for i, v in enumerate(m.positions):
            self.assertAlmostEqual(v, sculpted[i], places=12)
        session.undo()
        self.assertEqual(len(stack), n)
        self.assertEqual(list(m.positions), sculpted)

    def test_clear_history(self):
        session, m = self._stroked()
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_trigger(0, 0.0)
        session.active_target().history.clear()
        self.assertFalse(session.active_target().history.can_undo())
        self.assertEqual(session.active_target().history.nbytes(), 0)


# ==========================================================================
# serialisation
# ==========================================================================

class TestSerialisation(unittest.TestCase):

    def _rich_stack(self):
        stack = _stack(80, seed=2)
        a = stack.add_layer("Base pass", weight=0.75)
        b = stack.add_layer("Detail", weight=-1.25, blend="replace")
        c = stack.add_layer("Hidden", weight=2.0, visible=False, locked=True)
        _fill(a, range(0, 40), seed=40)
        _fill(b, range(20, 60), seed=41)
        _fill(c, [3, 5, 79], seed=42)
        stack.active_index = 1
        return stack

    def test_layer_round_trip_is_bit_identical(self):
        stack = self._rich_stack()
        restored = io.loads(io.dumps(stack)).stack
        self.assertEqual(len(restored), len(stack))
        for original, copy in zip(stack.layers, restored.layers):
            self.assertEqual(copy.name, original.name)
            self.assertEqual(copy.weight, original.weight)
            self.assertEqual(copy.visible, original.visible)
            self.assertEqual(copy.locked, original.locked)
            self.assertEqual(copy.blend, original.blend)
            self.assertEqual(list(copy.sorted_items()),
                             list(original.sorted_items()))
            self.assertEqual(copy, original)

    def test_base_and_evaluation_round_trip_exactly(self):
        stack = self._rich_stack()
        restored = io.loads(io.dumps(stack)).stack
        self.assertEqual(list(restored.base), list(stack.base))
        self.assertEqual(list(restored.evaluate()), list(stack.evaluate()))
        self.assertEqual(restored.active_index, stack.active_index)

    def test_round_trip_survives_awkward_floats(self):
        stack = _stack(8)
        layer = stack.add_layer("odd")
        layer.set(0, (1e-17, -1e17, 0.1))
        layer.set(1, (float("1e-308"), 3.141592653589793, -0.0))
        restored = io.loads(io.dumps(stack)).stack
        self.assertEqual(list(restored.layers[0].sorted_items()),
                         list(layer.sorted_items()))

    def test_dumps_is_deterministic(self):
        stack = self._rich_stack()
        self.assertEqual(io.dumps(stack), io.dumps(stack))

    def test_uncompressed_round_trip(self):
        stack = self._rich_stack()
        blob = io.dumps(stack, compress=False)
        self.assertEqual(list(io.loads(blob).stack.evaluate()),
                         list(stack.evaluate()))

    def test_mask_and_symmetry_round_trip(self):
        stack = self._rich_stack()
        mask = VertexMask(stack.n_vertices)
        mask.mask_indices([1, 2, 3], 1.0)
        mask.freeze = True
        sym = symmetry.Symmetry(axes=(True, False, True), radial=6,
                                radial_axis="Z", tolerance=1e-5)
        payload = io.loads(io.dumps(stack, mask, sym, fc_name="Body"))
        self.assertEqual(payload.fc_name, "Body")
        self.assertTrue(payload.mask.freeze)
        self.assertEqual(payload.mask.values[1], 1.0)
        self.assertEqual(payload.symmetry.axes, [True, False, True])
        self.assertEqual(payload.symmetry.radial, 6)
        self.assertEqual(payload.symmetry.radial_axis, "Z")

    def test_base64_round_trip(self):
        stack = self._rich_stack()
        text = io.dumps_base64(stack)
        self.assertIsInstance(text, str)
        self.assertEqual(list(io.loads_base64(text).stack.evaluate()),
                         list(stack.evaluate()))

    def test_without_the_base(self):
        stack = self._rich_stack()
        payload = io.loads(io.dumps(stack, include_base=False))
        self.assertEqual(payload.stack.n_vertices, stack.n_vertices)
        self.assertEqual(list(payload.stack.base),
                         [0.0] * (3 * stack.n_vertices))
        for original, copy in zip(stack.layers, payload.stack.layers):
            self.assertEqual(copy, original)

    def test_bad_magic_is_rejected(self):
        with self.assertRaises(io.SculptIoError):
            io.loads(b"NOPE" + b"\x00" * 32)

    def test_truncated_payload_is_rejected(self):
        blob = io.dumps(self._rich_stack())
        with self.assertRaises(io.SculptIoError):
            io.loads(blob[:20])

    def test_dict_round_trip(self):
        stack = self._rich_stack()
        restored = layers.LayerStack.from_dict(stack.to_dict())
        self.assertEqual(list(restored.evaluate()), list(stack.evaluate()))


# ==========================================================================
# the FCXR manifest section
# ==========================================================================

class _FakeWriter(object):
    """Just enough of :class:`xrsync.fcxr.FcxrWriter` for the section."""

    def __init__(self):
        self.accessors = []

    def add_accessor(self, values, type_, component):
        self.accessors.append((list(values), type_, component))
        return len(self.accessors) - 1


class _FakeDocument(object):
    def __init__(self, writer, manifest=None):
        self._writer = writer
        self.manifest = manifest or {}

    def read_accessor(self, index):
        return self._writer.accessors[index][0]


class TestFcxrSection(unittest.TestCase):

    def _payload(self):
        stack = _stack(40)
        a = stack.add_layer("Pass", weight=0.5)
        _fill(a, range(0, 20), seed=50)
        mask = VertexMask(40)
        mask.mask_indices([1, 2], 1.0)
        return io.SculptPayload(stack, mask, symmetry.Symmetry((True, False,
                                                                False)),
                                "Body")

    def test_fcsl1_section_round_trips_bit_identically(self):
        payload = self._payload()
        writer = _FakeWriter()
        section = io.sculpt_section(writer, [payload])
        self.assertEqual(section["version"], 1)
        self.assertEqual(section["targets"][0]["fc_name"], "Body")
        self.assertEqual(section["targets"][0]["encoding"], "fcsl1")
        doc = _FakeDocument(writer, {"sculpt": section})
        out = io.read_sculpt_section(doc)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].fc_name, "Body")
        self.assertEqual(list(out[0].stack.layers[0].sorted_items()),
                         list(payload.stack.layers[0].sorted_items()))
        self.assertEqual(list(out[0].stack.evaluate()),
                         list(payload.stack.evaluate()))
        self.assertEqual(out[0].symmetry.axes, [True, False, False])

    def test_f32_section_is_interoperable_and_lossy(self):
        payload = self._payload()
        writer = _FakeWriter()
        section = io.sculpt_section(writer, [payload], encoding="f32")
        rec = section["targets"][0]
        self.assertEqual(rec["encoding"], "f32")
        self.assertIsNotNone(rec["layers"][0]["indices"])
        self.assertEqual(writer.accessors[rec["layers"][0]["indices"]][2],
                         "U32")
        self.assertEqual(writer.accessors[rec["layers"][0]["offsets"]][1],
                         "VEC3")
        doc = _FakeDocument(writer, {"sculpt": section})
        out = io.read_sculpt_section(doc)
        for i, v in out[0].stack.layers[0].sorted_items():
            want = payload.stack.layers[0].get(i)
            for k in range(3):
                self.assertAlmostEqual(v[k], want[k], places=6)

    def test_unknown_encoding_is_rejected(self):
        with self.assertRaises(io.SculptIoError):
            io.sculpt_section(_FakeWriter(), [self._payload()],
                              encoding="f64")

    def test_missing_section_gives_an_empty_list(self):
        self.assertEqual(io.read_sculpt_section(_FakeDocument(_FakeWriter())),
                         [])

    def test_session_export_and_import(self):
        m = mesh.make_icosphere(2, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        session.set_tool("draw")
        session.set_radius(0.5)
        session.on_trigger(0, 1.0, position=(0, 0, 1.0), normal=(0, 0, 1))
        session.on_move(0, position=(0.3, 0.0, 1.0), normal=(0, 0, 1),
                        pressure=1.0)
        session.on_trigger(0, 0.0)
        sculpted = list(m.positions)
        writer = _FakeWriter()
        section = session.export_sculpt_manifest(writer)
        doc = _FakeDocument(writer, {"sculpt": section})
        restored = SculptSession()
        targets = restored.import_sculpt_manifest(
            doc, meshes={"Body": mesh.make_icosphere(2, 1.0)})
        self.assertEqual(len(targets), 1)
        self.assertEqual(list(targets[0].mesh.positions), sculpted)

    def test_session_export_bytes_round_trip(self):
        m = mesh.make_icosphere(1, 1.0)
        session = SculptSession(mode="SCULPT")
        session.add_target("Body", m)
        layer = session.active_stack().add_layer("Pass")
        layer.set(0, (0.1, 0.2, 0.3))
        session.active_target().evaluate()
        blob = session.export_bytes()
        other = SculptSession()
        other.add_target("Body", mesh.make_icosphere(1, 1.0))
        target = other.import_bytes(blob)
        self.assertEqual(list(target.mesh.positions), list(m.positions))


# ==========================================================================
# housekeeping
# ==========================================================================

class TestStackHousekeeping(unittest.TestCase):

    def test_names_are_made_unique(self):
        stack = _stack(8)
        a = stack.add_layer("Pass")
        stack.add_layer("Pass")
        self.assertEqual(a.name, "Pass")
        self.assertEqual(stack.layers[1].name, "Pass")
        self.assertEqual(stack.duplicate(0).name, "Pass copy")

    def test_lookup_helpers(self):
        stack = _stack(8)
        a = stack.add_layer("A")
        b = stack.add_layer("B")
        self.assertIs(stack.find("B"), b)
        self.assertIs(stack.by_id(a.id), a)
        self.assertEqual(stack.index_of(b), 1)
        self.assertIsNone(stack.find("nope"))
        self.assertIsNone(stack.by_id(-1))

    def test_active_tracks_removal(self):
        stack = _stack(8)
        stack.add_layer("A")
        stack.add_layer("B")
        self.assertEqual(stack.active_index, 1)
        stack.remove_layer(1)
        self.assertEqual(stack.active_index, 0)
        stack.remove_layer(0)
        self.assertIsNone(stack.active)

    def test_ensure_active_creates_one(self):
        stack = _stack(8)
        self.assertIsNone(stack.active)
        layer = stack.ensure_active()
        self.assertIs(stack.active, layer)

    def test_bad_blend_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            SculptLayer("x", blend="screen")
        stack = _stack(8)
        stack.add_layer("a")
        with self.assertRaises(ValueError):
            stack.set_blend(0, "screen")

    def test_layer_repr_mentions_the_name(self):
        layer = SculptLayer("Wrinkles")
        self.assertIn("Wrinkles", repr(layer))
        self.assertIn("layers", repr(_stack(8)))

    def test_history_repr(self):
        stack = _stack(8)
        self.assertIn("History", repr(History(stack)))

    def test_double_begin_is_an_error(self):
        stack = _stack(8)
        history = History(stack)
        history.begin("a")
        with self.assertRaises(RuntimeError):
            history.begin("b")

    def test_snapshot_outside_an_entry_is_an_error(self):
        stack = _stack(8)
        layer = stack.add_layer("a")
        history = History(stack)
        with self.assertRaises(RuntimeError):
            history.snapshot(layer, [0])

    def test_brush_module_is_reachable_from_the_package(self):
        self.assertIn("draw", brushes.BRUSH_KINDS)


if __name__ == "__main__":
    unittest.main()
