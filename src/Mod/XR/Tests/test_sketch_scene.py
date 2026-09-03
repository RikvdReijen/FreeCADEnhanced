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
"""The sketch scene, the primitives that go in it, reference material, the
session that drives it all, and the commit into a FreeCAD document.

The document commit runs against ``Tests.stubs`` plus a recording document, so
the wiring is checked without FreeCAD present.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrenv.scale import ScaleController                        # noqa: E402
from xrsketch import curves as C                               # noqa: E402
from xrsketch import primitives as P                           # noqa: E402
from xrsketch import reference as R                            # noqa: E402
from xrsketch import scene as SC                               # noqa: E402
from xrsketch import surfacing as S                            # noqa: E402
from xrsketch import vecmath as vm                             # noqa: E402
from xrsketch.subd import cube_cage                            # noqa: E402
from xrsketch.vecmath import Transform                         # noqa: E402


def a_box(size=0.1, at=(0.0, 0.0, 0.0)):
    half = size * 0.5
    return P.Primitive.from_two_points(
        "box", vm.sub(at, (half, half, half)), vm.add(at, (half, half, half)))


# ==========================================================================
# layers
# ==========================================================================

class TestLayers(unittest.TestCase):

    def setUp(self):
        self.scene = SC.Scene()
        self.root = self.scene.layers[0]
        self.mid = self.scene.add_layer("Mid", self.root.id)
        self.leaf = self.scene.add_layer("Leaf", self.mid.id)

    def test_nesting(self):
        self.assertEqual(self.scene.layer_children(self.root.id),
                         [self.mid])
        self.assertEqual([l.id for l in
                          self.scene.layer_descendants(self.root.id)],
                         [self.mid.id, self.leaf.id])
        self.assertEqual([l.id for l in
                          self.scene.layer_ancestors(self.leaf.id)],
                         [self.mid.id, self.root.id])

    def test_visibility_is_inherited(self):
        self.assertTrue(self.scene.layer_visible(self.leaf.id))
        self.scene.set_layer_visible(self.mid.id, False)
        self.assertFalse(self.scene.layer_visible(self.leaf.id))
        # the leaf's own flag is untouched
        self.assertTrue(self.scene.layer(self.leaf.id).visible)
        self.scene.set_layer_visible(self.mid.id, True)
        self.assertTrue(self.scene.layer_visible(self.leaf.id))

    def test_lock_is_inherited(self):
        self.assertFalse(self.scene.layer_locked(self.leaf.id))
        self.scene.set_layer_locked(self.root.id, True)
        self.assertTrue(self.scene.layer_locked(self.leaf.id))
        self.assertTrue(self.scene.layer_locked(self.mid.id))
        self.assertFalse(self.scene.layer(self.leaf.id).locked)

    def test_objects_follow_their_layer(self):
        obj = self.scene.add_cage(cube_cage(0.1), layer=self.leaf.id)
        self.assertTrue(self.scene.object_visible(obj))
        self.scene.set_layer_visible(self.root.id, False)
        self.assertFalse(self.scene.object_visible(obj))
        self.assertEqual(self.scene.visible_objects(), [])
        self.scene.set_layer_visible(self.root.id, True)
        self.scene.set_layer_locked(self.mid.id, True)
        self.assertTrue(self.scene.object_locked(obj))
        self.assertEqual(self.scene.select(obj), [])

    def test_colour_and_rename(self):
        self.scene.set_layer_color(self.mid.id, (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(self.scene.layer_color(self.mid.id),
                         (1.0, 0.0, 0.0, 1.0))
        self.scene.rename(self.mid.id, "Body")
        self.assertIs(self.scene.find_layer("Body"), self.mid)

    def test_reparenting_refuses_cycles(self):
        self.assertRaises(ValueError, self.scene.move_layer, self.root.id,
                          self.leaf.id)
        self.assertRaises(ValueError, self.scene.move_layer, self.mid.id,
                          self.mid.id)
        self.assertRaises(KeyError, self.scene.move_layer, self.mid.id, "L99")
        self.scene.move_layer(self.leaf.id, self.root.id)
        self.assertEqual(self.scene.layer_children(self.mid.id), [])

    def test_removing_a_layer_reparents_by_default(self):
        obj = self.scene.add_cage(cube_cage(0.1), layer=self.leaf.id)
        self.scene.remove_layer(self.mid.id)
        self.assertEqual(self.scene.layer(self.leaf.id).parent, self.root.id)
        self.assertIsNotNone(self.scene.object(obj.id))

    def test_removing_a_layer_with_its_contents(self):
        self.scene.add_cage(cube_cage(0.1), layer=self.leaf.id)
        self.scene.remove_layer(self.mid.id, reparent=False)
        self.assertIsNone(self.scene.layer(self.leaf.id))
        self.assertEqual(len(self.scene.objects), 0)

    def test_the_last_layer_cannot_go(self):
        scene = SC.Scene()
        self.assertRaises(ValueError, scene.remove_layer,
                          scene.layers[0].id)
        self.assertRaises(KeyError, scene.remove_layer, "nope")

    def test_objects_in_layer_can_include_nested(self):
        deep = self.scene.add_cage(cube_cage(0.1), layer=self.leaf.id)
        shallow = self.scene.add_cage(cube_cage(0.1), layer=self.mid.id)
        self.assertEqual(self.scene.objects_in_layer(self.mid.id, False),
                         [shallow])
        self.assertEqual(set(self.scene.objects_in_layer(self.mid.id, True)),
                         {shallow, deep})


# ==========================================================================
# selection and grouping
# ==========================================================================

class TestSelection(unittest.TestCase):

    def setUp(self):
        self.scene = SC.Scene()
        self.a = self.scene.add_primitive(a_box(0.1, (0.0, 0.0, 0.0)))
        self.b = self.scene.add_primitive(a_box(0.1, (1.0, 0.0, 0.0)))
        self.c = self.scene.add_primitive(a_box(0.1, (2.0, 0.0, 0.0)))

    def test_single_and_additive(self):
        self.assertEqual(self.scene.select(self.a), [self.a])
        self.assertEqual(self.scene.select(self.b), [self.b])
        self.assertEqual(self.scene.select(self.c, additive=True),
                         [self.b, self.c])

    def test_toggle(self):
        self.scene.select(self.a)
        self.assertFalse(self.scene.toggle(self.a))
        self.assertEqual(self.scene.selection, [])
        self.assertTrue(self.scene.toggle(self.a))

    def test_select_all_and_deselect(self):
        self.assertEqual(len(self.scene.select_all()), 3)
        self.assertEqual(self.scene.deselect_all(), [])

    def test_box_selection_contained_and_overlapping(self):
        selected = self.scene.select_in_box((-0.2, -0.2, -0.2),
                                            (1.2, 0.2, 0.2))
        self.assertEqual(set(selected), {self.a, self.b})
        touching = self.scene.select_in_box((0.94, -0.2, -0.2),
                                            (1.0, 0.2, 0.2),
                                            contained=False)
        self.assertEqual(touching, [self.b])
        self.assertEqual(self.scene.select_in_box((10, 10, 10), (11, 11, 11)),
                         [])

    def test_box_selection_skips_hidden_and_locked(self):
        self.b.visible = False
        self.c.locked = True
        selected = self.scene.select_in_box((-1, -1, -1), (5, 5, 5))
        self.assertEqual(selected, [self.a])

    def test_select_by_layer(self):
        other = self.scene.add_layer("Other")
        self.scene.move_to_layer([self.b, self.c], other.id)
        self.assertEqual(set(self.scene.select_by_layer(other.id)),
                         {self.b, self.c})
        self.assertRaises(KeyError, self.scene.move_to_layer, [self.a], "L99")

    def test_grouping_selects_together(self):
        group = self.scene.group([self.a, self.b], "Pair")
        self.assertEqual(len(group.members), 2)
        self.assertEqual(set(self.scene.select(self.a)), {self.a, self.b})
        self.assertIs(self.scene.group_of(self.b), group)
        self.assertEqual(set(self.scene.group_objects(group)),
                         {self.a, self.b})
        self.assertTrue(self.scene.ungroup(group))
        self.assertEqual(self.scene.select(self.a), [self.a])

    def test_grouping_merges_existing_groups(self):
        first = self.scene.group([self.a, self.b])
        merged = self.scene.group([self.a, self.c])
        self.assertEqual(len(merged.members), 3)
        self.assertEqual(len(self.scene.groups), 1)
        self.assertIsNot(merged, first)

    def test_grouping_needs_two_objects(self):
        self.assertRaises(ValueError, self.scene.group, [self.a])

    def test_removing_an_object_cleans_the_selection_and_group(self):
        self.scene.group([self.a, self.b, self.c])
        self.scene.select(self.a)
        self.assertTrue(self.scene.remove(self.a))
        self.assertNotIn(self.a.id, self.scene.selection)
        self.assertEqual(len(self.scene.groups[0].members), 2)
        self.assertFalse(self.scene.remove("nope"))


# ==========================================================================
# duplication and arrays
# ==========================================================================

class TestArrays(unittest.TestCase):

    def setUp(self):
        self.scene = SC.Scene()
        self.box = self.scene.add_primitive(a_box(0.1, (1.0, 0.0, 0.0)))

    def test_duplicate(self):
        clones = self.scene.duplicate([self.box], (0.0, 0.5, 0.0))
        self.assertEqual(len(clones), 1)
        self.assertEqual(len(self.scene.objects), 2)
        self.assertAlmostEqual(clones[0].transform.translation[1], 0.5,
                               places=12)
        self.assertIsNot(clones[0].data, self.box.data)
        self.assertTrue(clones[0].name.endswith("copy"))

    def test_duplicate_defaults_to_the_selection(self):
        self.scene.select(self.box)
        self.assertEqual(len(self.scene.duplicate()), 1)

    def test_linear_array_counts_and_placements(self):
        made = self.scene.array_linear([self.box], 4, (0.5, 0.0, 0.0))
        self.assertEqual(len(made), 4)
        self.assertEqual(len(self.scene.objects), 4)
        xs = [round(o.transform.translation[0], 9) for o in made]
        self.assertEqual(xs, [1.0, 1.5, 2.0, 2.5])

    def test_linear_array_without_the_original(self):
        made = self.scene.array_linear([self.box], 3, (0.5, 0.0, 0.0),
                                       include_original=False)
        self.assertEqual(len(made), 2)
        self.assertEqual(len(self.scene.objects), 3)

    def test_a_count_of_one_makes_nothing_new(self):
        made = self.scene.array_linear([self.box], 1, (0.5, 0.0, 0.0))
        self.assertEqual(made, [self.box])
        self.assertRaises(ValueError, self.scene.array_linear, [self.box], 0,
                          (1, 0, 0))

    def test_radial_array_full_circle(self):
        made = self.scene.array_radial([self.box], 4, 2.0 * math.pi,
                                       (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertEqual(len(made), 4)
        positions = [o.transform.apply((0.0, 0.0, 0.0)) for o in made]
        expected = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0),
                    (0.0, -1.0, 0.0)]
        for got, want in zip(positions, expected):
            self.assertAlmostEqual(vm.dist(got, want), 0.0, places=9)

    def test_radial_array_partial_sweep_spans_the_angle(self):
        made = self.scene.array_radial([self.box], 3, math.pi / 2.0,
                                       (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        last = made[-1].transform.apply((0.0, 0.0, 0.0))
        self.assertAlmostEqual(vm.dist(last, (0.0, 1.0, 0.0)), 0.0, places=9)

    def test_radial_array_needs_an_axis(self):
        self.assertRaises(ValueError, self.scene.array_radial, [self.box], 3,
                          math.pi, (0, 0, 0), (0, 0, 0))

    def test_mirror_array_reflects_the_geometry(self):
        made = self.scene.array_mirror([self.box], (0.0, 0.0, 0.0),
                                       (1.0, 0.0, 0.0))
        self.assertEqual(len(made), 2)
        lo, hi = made[-1].world_bounds()
        self.assertAlmostEqual(lo[0], -1.05, places=9)
        self.assertAlmostEqual(hi[0], -0.95, places=9)

    def test_mirroring_a_cage_reverses_the_winding(self):
        cage = self.scene.add_cage(cube_cage(0.2))
        made = self.scene.array_mirror([cage], (1.0, 0.0, 0.0),
                                       (1.0, 0.0, 0.0))
        mirrored = made[-1].data
        self.assertEqual(mirrored.check(), [])
        self.assertAlmostEqual(vm.dot(mirrored.face_normal(0),
                                      cage.data.face_normal(0)), 1.0,
                               places=12)

    def test_reflect_rotation_stays_a_rotation(self):
        q = vm.quat_from_axis_angle((0.3, 1.0, -0.2), 0.9)
        r = SC.reflect_rotation(q, (1.0, 0.0, 0.0))
        m = vm.quat_to_mat3(r)
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
               - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        self.assertAlmostEqual(det, 1.0, places=9)

    def test_mirror_data_needs_a_normal(self):
        self.assertRaises(ValueError, SC.mirror_data, "cage",
                          cube_cage(1.0), (0, 0, 0), (0, 0, 0))


# ==========================================================================
# undo
# ==========================================================================

class TestUndo(unittest.TestCase):

    def setUp(self):
        self.scene = SC.Scene()

    def test_undo_restores_the_state_exactly(self):
        self.scene.add_primitive(a_box())
        before = self.scene.to_dict()
        with self.scene.edit("add"):
            self.scene.add_cage(cube_cage(0.2))
        after = self.scene.to_dict()
        self.assertNotEqual(before, after)
        self.assertTrue(self.scene.history.can_undo())
        self.assertEqual(self.scene.history.undo(), "add")
        self.assertEqual(self.scene.to_dict(), before)
        self.assertEqual(self.scene.history.redo(), "add")
        self.assertEqual(self.scene.to_dict(), after)

    def test_undo_restores_the_selection_too(self):
        a = self.scene.add_primitive(a_box())
        self.scene.select(a)
        before = list(self.scene.selection)
        with self.scene.edit("deselect"):
            self.scene.deselect_all()
        self.scene.history.undo()
        self.assertEqual(self.scene.selection, before)

    def test_several_steps_unwind_in_order(self):
        labels = []
        for i in range(4):
            with self.scene.edit("step %d" % i):
                self.scene.add_primitive(a_box(0.1, (i, 0.0, 0.0)))
            labels.append("step %d" % i)
        self.assertEqual(self.scene.history.undo_labels(), labels)
        for i in reversed(range(4)):
            self.assertEqual(self.scene.history.undo(), "step %d" % i)
        self.assertEqual(len(self.scene.objects), 0)
        self.assertFalse(self.scene.history.can_undo())
        self.assertIsNone(self.scene.history.undo())

    def test_a_new_edit_clears_the_redo_stack(self):
        with self.scene.edit("one"):
            self.scene.add_primitive(a_box())
        self.scene.history.undo()
        self.assertTrue(self.scene.history.can_redo())
        with self.scene.edit("two"):
            self.scene.add_cage(cube_cage(0.1))
        self.assertFalse(self.scene.history.can_redo())

    def test_an_edit_that_changes_nothing_is_dropped(self):
        with self.scene.edit("nothing"):
            pass
        self.assertFalse(self.scene.history.can_undo())

    def test_an_exception_aborts_and_rolls_back(self):
        before = self.scene.to_dict()
        try:
            with self.scene.edit("boom"):
                self.scene.add_primitive(a_box())
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(self.scene.to_dict(), before)
        self.assertFalse(self.scene.history.can_undo())

    def test_nested_edits_are_refused(self):
        self.scene.history.begin("outer")
        self.assertRaises(RuntimeError, self.scene.history.begin, "inner")
        self.scene.history.abort()

    def test_the_stack_is_bounded(self):
        self.scene.history.max_entries = 3
        for i in range(6):
            with self.scene.edit("step %d" % i):
                self.scene.add_primitive(a_box(0.1, (i, 0.0, 0.0)))
        self.assertEqual(len(self.scene.history.undo_labels()), 3)
        self.assertEqual(self.scene.history.undo_labels()[0], "step 3")

    def test_geometry_survives_the_round_trip(self):
        curve = C.Curve3D.from_points([(0, 0, 0), (1, 1, 0), (2, 0, 1)])
        obj = self.scene.add_curve(curve)
        length = obj.data.length()
        with self.scene.edit("clear"):
            self.scene.remove(obj)
        self.scene.history.undo()
        restored = self.scene.objects[0]
        self.assertEqual(restored.kind, "curve")
        self.assertAlmostEqual(restored.data.length(), length, places=9)


# ==========================================================================
# serialisation
# ==========================================================================

class TestSceneSerialisation(unittest.TestCase):

    def test_every_object_kind_round_trips(self):
        scene = SC.Scene()
        scene.add_primitive(a_box())
        scene.add_cage(cube_cage(0.2))
        scene.add_curve(C.Curve3D.from_points([(0, 0, 0), (1, 0, 0)],
                                              smooth=False))
        scene.add_surface(S.extrude([(0, 0, 0), (1, 0, 0)], (0, 1, 0)))
        scene.add("image", R.ImagePlane("blueprint.png"))
        scene.add("measure", R.Measurement("distance",
                                           [(0, 0, 0), (10, 0, 0)]))
        state = scene.to_dict()
        clone = SC.Scene.from_dict(state)
        self.assertEqual(clone.to_dict(), state)
        self.assertEqual(len(clone.objects), 6)

    def test_unknown_kinds_are_refused(self):
        self.assertRaises(ValueError, SC.SketchObject, "O1", "sausage", None)

    def test_bounds(self):
        scene = SC.Scene()
        scene.add_primitive(a_box(0.2, (1.0, 0.0, 0.0)))
        lo, hi = scene.bounds()
        self.assertAlmostEqual(lo[0], 0.9, places=9)
        self.assertAlmostEqual(hi[0], 1.1, places=9)


# ==========================================================================
# primitives
# ==========================================================================

class TestPrimitives(unittest.TestCase):

    def test_every_kind_tessellates(self):
        for kind in P.PRIMITIVE_KINDS:
            prim = P.Primitive.from_two_points(kind, (0, 0, 0),
                                               (0.2, 0.3, 0.1))
            positions, normals, uvs, indices = prim.mesh()
            self.assertTrue(positions)
            self.assertEqual(len(positions), len(normals))
            self.assertEqual(len(indices) % 3, 0)
            for c in positions:
                self.assertTrue(math.isfinite(c))

    def test_two_handed_placement_spans_the_hands(self):
        box = P.Primitive.from_two_points("box", (0, 0, 0), (0.2, 0.3, 0.1))
        self.assertEqual(box.params["size"], (0.2, 0.3, 0.1))
        lo, hi = box.bounds()
        self.assertAlmostEqual(vm.dist(lo, (0, 0, 0)), 0.0, places=9)
        self.assertAlmostEqual(vm.dist(hi, (0.2, 0.3, 0.1)), 0.0, places=9)
        sphere = P.Primitive.from_two_points("sphere", (0, 0, 0), (0, 0, 2))
        self.assertAlmostEqual(sphere.params["radius"], 1.0, places=12)

    def test_a_cylinder_takes_its_axis_from_the_hands(self):
        cyl = P.Primitive.from_two_points("cylinder", (0, 0, 0), (0, 0, 1))
        self.assertAlmostEqual(cyl.params["height"], 1.0, places=12)
        axis = vm.quat_rotate(cyl.transform.rotation, P.AXIS)
        self.assertAlmostEqual(vm.dist(axis, (0.0, 0.0, 1.0)), 0.0, places=9)

    def test_coincident_hands_are_refused(self):
        self.assertRaises(ValueError, P.Primitive.from_two_points, "box",
                          (0, 0, 0), (0, 0, 0))
        self.assertRaises(ValueError, P.Primitive, "dodecahedron")

    def test_parameters_are_validated_and_clamped(self):
        cyl = P.Primitive("cylinder")
        self.assertEqual(cyl.set_param("sides", 1), 3)
        self.assertEqual(cyl.set_param("sides", 9999), 512)
        self.assertRaises(KeyError, cyl.set_param, "radius2", 1.0)
        self.assertRaises(ValueError, cyl.set_param, "radius",
                          float("nan"))
        box = P.Primitive("box")
        self.assertRaises(ValueError, box.set_param, "size", (1.0, 2.0))
        tube = P.Primitive("tube")
        self.assertRaises(ValueError, tube.set_param, "path", [(0, 0, 0)])

    def test_live_editing_after_placement(self):
        box = P.Primitive.from_two_points("box", (0, 0, 0), (0.2, 0.2, 0.2))
        box.update(size=(0.4, 0.2, 0.2))
        lo, hi = box.bounds()
        self.assertAlmostEqual(hi[0] - lo[0], 0.4, places=9)

    def test_shape_dict_matches_the_environment_spec(self):
        from xrenv import spec as _spec
        for kind in P.PRIMITIVE_KINDS:
            prim = P.Primitive(kind)
            shape = prim.shape_dict()
            self.assertEqual(shape["type"], kind)
            positions = _spec.tessellate_shape(shape)[0]
            self.assertTrue(positions)

    def test_placement_session(self):
        session = P.PlacementSession("cylinder")
        self.assertFalse(session.active)
        session.begin((0, 0, 0))
        self.assertTrue(session.active)
        self.assertIsNone(session.update((0, 0, 0)))     # too close
        prim = session.update((0, 0, 0.5))
        self.assertAlmostEqual(prim.params["height"], 0.5, places=12)
        prim = session.update((0, 0, 1.0))
        self.assertAlmostEqual(prim.params["height"], 1.0, places=12)
        self.assertIs(session.commit(), prim)
        self.assertFalse(session.active)
        self.assertRaises(ValueError, session.set_kind, "blob")

    def test_box_and_plane_have_control_cages(self):
        cage = P.Primitive.from_two_points("box", (0, 0, 0),
                                           (0.2, 0.2, 0.2)).to_cage()
        self.assertEqual(cage.check(), [])
        self.assertEqual(cage.face_count, 6)
        self.assertRaises(ValueError, P.Primitive("sphere").to_cage)

    def test_round_trip(self):
        prim = P.Primitive.from_two_points("torus", (0, 0, 0), (0.4, 0, 0))
        clone = P.Primitive.from_dict(prim.to_dict())
        self.assertEqual(clone.params, prim.params)
        self.assertEqual(clone.kind, prim.kind)


# ==========================================================================
# reference material
# ==========================================================================

class TestImagePlane(unittest.TestCase):

    def test_corners_and_uvs(self):
        plane = R.ImagePlane("blueprint.png", (0.4, 0.2), (1.0, 0.0, 0.0))
        corners = plane.corners()
        self.assertEqual(len(corners), 4)
        self.assertAlmostEqual(vm.dist(corners[0], (0.8, -0.1, 0.0)), 0.0,
                               places=12)
        self.assertAlmostEqual(vm.dist(plane.point_at(0.5, 0.5),
                                       (1.0, 0.0, 0.0)), 0.0, places=12)
        u, v = plane.uv_at(plane.point_at(0.25, 0.75))
        self.assertAlmostEqual(u, 0.25, places=12)
        self.assertAlmostEqual(v, 0.75, places=12)
        self.assertTrue(plane.contains(plane.point_at(0.1, 0.1)))
        self.assertFalse(plane.contains(plane.point_at(1.5, 0.1)))

    def test_opacity_and_lock(self):
        plane = R.ImagePlane()
        self.assertTrue(plane.locked)              # reference images start
        self.assertFalse(plane.move((1, 0, 0)))    # pinned down
        plane.set_locked(False)
        self.assertTrue(plane.move((1, 0, 0)))
        self.assertAlmostEqual(plane.origin[0], 1.0, places=12)
        self.assertAlmostEqual(plane.set_opacity(2.0), 1.0, places=12)
        self.assertAlmostEqual(plane.set_opacity(-1.0), 0.0, places=12)

    def test_fit_to_the_source_aspect(self):
        plane = R.ImagePlane(resolution=(1600, 800))
        self.assertEqual(plane.fit_to(width=0.8), (0.8, 0.4))

    def test_round_trip(self):
        plane = R.ImagePlane("x.png", (0.3, 0.2), (1, 2, 3), opacity=0.25)
        clone = R.ImagePlane.from_dict(plane.to_dict())
        self.assertEqual(clone.to_dict(), plane.to_dict())


class TestMeasurement(unittest.TestCase):

    def test_distance_angle_and_polyline(self):
        distance = R.Measurement("distance", [(0, 0, 0), (30, 40, 0)])
        self.assertAlmostEqual(distance.value(), 50.0, places=12)
        self.assertEqual(distance.text(1), "50.0 mm")
        angle = R.Measurement("angle", [(1, 0, 0), (0, 0, 0), (0, 1, 0)])
        self.assertAlmostEqual(math.degrees(angle.value()), 90.0, places=9)
        self.assertEqual(angle.text(), "90.0°")
        poly = R.Measurement("polyline", [(0, 0, 0), (10, 0, 0), (10, 10, 0)])
        self.assertEqual(poly.running_total(), [0.0, 10.0, 20.0])
        self.assertAlmostEqual(poly.value(), 20.0, places=12)
        self.assertEqual(len(poly.labels()), 2)

    def test_incomplete_and_degenerate(self):
        m = R.Measurement("distance", [(0, 0, 0)])
        self.assertFalse(m.complete)
        self.assertRaises(ValueError, m.value)
        self.assertEqual(m.text(), "—")
        self.assertRaises(ValueError,
                          R.Measurement("angle",
                                        [(0, 0, 0)] * 3).angle)
        self.assertRaises(ValueError, R.Measurement, "volume")

    def test_metres_roll_over(self):
        self.assertEqual(R.format_length(1500.0), "1.500 m")
        self.assertEqual(R.format_length(999.0), "999.00 mm")

    def test_round_trip(self):
        m = R.Measurement("polyline", [(0, 0, 0), (1, 0, 0)])
        self.assertEqual(R.Measurement.from_dict(m.to_dict()).to_dict(),
                         m.to_dict())


class TestMeasureUnderMiniaturisation(unittest.TestCase):
    """A 12x-shrunk user must still read true millimetres."""

    def _tool(self, user_scale):
        controller = ScaleController(scale=user_scale, unit_scale=1.0,
                                     duration=0.0)
        return R.MeasureTool(controller, model_scale=0.001)

    def test_the_readout_is_the_model_size_not_the_hand_span(self):
        tool = self._tool(12.0)
        tool.begin("distance", (0.0, 0.0, 0.0))
        tool.add((1.2, 0.0, 0.0))
        self.assertAlmostEqual(tool.current.value(), 100.0, places=9)
        self.assertEqual(tool.readout(1), "100.0 mm")

    def test_the_same_part_reads_the_same_at_every_user_scale(self):
        for user_scale in (1.0, 2.0, 11.0, 12.0, 50.0):
            tool = self._tool(user_scale)
            # a 100 mm part is 0.1 environment metres, drawn user_scale times
            # bigger in the headset
            span = 0.1 * user_scale
            tool.begin("distance", (0.0, 0.0, 0.0))
            tool.add((span, 0.0, 0.0))
            self.assertAlmostEqual(tool.current.value(), 100.0, places=6)

    def test_view_and_model_conversions_are_inverses(self):
        tool = self._tool(12.0)
        point = (0.3, 1.1, -0.4)
        self.assertAlmostEqual(vm.dist(tool.to_view(tool.to_model(point)),
                                       point), 0.0, places=9)
        self.assertAlmostEqual(tool.model_length(1.2), 100.0, places=9)

    def test_a_stored_measurement_does_not_change_when_the_user_rescales(self):
        controller = ScaleController(scale=12.0, unit_scale=1.0, duration=0.0)
        tool = R.MeasureTool(controller, model_scale=0.001)
        tool.begin("distance", (0.0, 0.0, 0.0))
        tool.add((1.2, 0.0, 0.0))
        measurement = tool.commit()
        controller.set_scale(1.0, animate=False)
        self.assertAlmostEqual(measurement.value(), 100.0, places=9)

    def test_without_a_scale_controller_the_tool_is_a_plain_ruler(self):
        tool = R.MeasureTool(None, model_scale=0.001)
        tool.begin("distance", (0.0, 0.0, 0.0))
        tool.add((0.25, 0.0, 0.0))
        self.assertAlmostEqual(tool.current.value(), 250.0, places=9)
        self.assertRaises(ValueError, R.MeasureTool, None, 0.0)

    def test_the_tape_can_be_wound_back(self):
        tool = self._tool(1.0)
        tool.begin("polyline", (0.0, 0.0, 0.0))
        tool.add((0.1, 0.0, 0.0))
        tool.add((0.1, 0.1, 0.0))
        tool.undo_point()
        self.assertAlmostEqual(tool.current.value(), 100.0, places=9)
        self.assertIsNotNone(tool.commit())
        self.assertEqual(len(tool.finished), 1)
        self.assertIsNone(tool.commit())


# ==========================================================================
# the session
# ==========================================================================

class TestSession(unittest.TestCase):

    def setUp(self):
        from xrsketch.session import SketchSession
        self.session = SketchSession()

    def test_tools_switch_and_reject_nonsense(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_CURVE)
        self.assertEqual(self.session.tool, "CURVE")
        self.session.tool = S_.TOOL_SELECT
        self.assertEqual(self.session.tool, "SELECT")
        self.assertRaises(ValueError, self.session.set_tool, "SCULPT")

    def test_drawing_a_curve_adds_it_to_the_scene(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_CURVE)
        self.session.snap.settings.enabled = False
        self.session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0))
        for i in range(1, 40):
            self.session.on_move(0, position=(i * 0.01,
                                              0.05 * math.sin(i * 0.1), 0.0))
        self.assertTrue(self.session.on_trigger(0, 0.0,
                                                position=(0.39, 0.0, 0.0)))
        self.assertEqual(len(self.session.scene.objects), 1)
        self.assertEqual(self.session.scene.objects[0].kind, "curve")
        events = [e["event"] for e in self.session.drain_events()]
        self.assertIn("curve", events)

    def test_a_stroke_of_one_point_makes_nothing(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_CURVE)
        self.session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0))
        self.session.on_trigger(0, 0.0, position=(0.0, 0.0, 0.0))
        self.assertEqual(len(self.session.scene.objects), 0)

    def test_placing_a_primitive_with_two_hands(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_PRIMITIVE)
        self.session.set_primitive_kind("box")
        self.session.snap.settings.enabled = False
        self.session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0))
        self.session.on_trigger(1, 1.0, position=(0.2, 0.2, 0.2))
        self.session.on_move(0, position=(0.0, 0.0, 0.0))
        self.session.on_trigger(0, 0.0, position=(0.0, 0.0, 0.0))
        self.assertEqual(len(self.session.scene.objects), 1)
        prim = self.session.scene.objects[0].data
        self.assertEqual(prim.kind, "box")
        for c in prim.params["size"]:
            self.assertAlmostEqual(c, 0.2, places=9)

    def test_pen_tool_places_points_and_closes(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_PEN)
        self.session.snap.settings.enabled = False
        for p in ((0, 0, 0), (0.2, 0, 0), (0.2, 0.2, 0), (0, 0.2, 0)):
            self.session.on_trigger(0, 1.0, position=p)
            self.session.on_trigger(0, 0.0, position=p)
        obj = self.session.finish_pen(close=True)
        self.assertIsNotNone(obj)
        self.assertTrue(obj.data.closed)
        self.assertEqual(len(obj.data.points), 4)

    def test_select_tool_picks_and_clears(self):
        from xrsketch import session as S_
        obj = self.session.scene.add_primitive(a_box(0.2, (0.0, 0.0, 0.0)))
        self.session.set_tool(S_.TOOL_SELECT)
        self.session.on_trigger(0, 1.0, position=(0.05, 0.0, 0.0))
        self.assertEqual(self.session.scene.selection, [obj.id])
        self.session.on_trigger(0, 0.0, position=(0.05, 0.0, 0.0))
        self.session.on_trigger(0, 1.0, position=(5.0, 5.0, 5.0))
        self.assertEqual(self.session.scene.selection, [])

    def test_grip_grabs_the_selection_and_undo_puts_it_back(self):
        obj = self.session.scene.add_primitive(a_box(0.2))
        self.session.scene.select(obj)
        self.session.grab.params.damping = 0.0
        self.session.grab.params.dead_zone_translation = 0.0
        start = obj.transform.translation
        self.session.on_grip(0, 1.0, position=(0.0, 0.0, 0.0))
        self.session.on_move(0, position=(0.5, 0.0, 0.0))
        self.session.update(1.0 / 90.0, [])
        self.assertAlmostEqual(obj.transform.translation[0], start[0] + 0.5,
                               places=9)
        self.session.on_grip(0, 0.0, position=(0.5, 0.0, 0.0))
        self.assertEqual(self.session.undo(), "grab")
        self.assertAlmostEqual(
            self.session.scene.object(obj.id).transform.translation[0],
            start[0], places=12)

    def test_two_handed_grab_scales_the_selection(self):
        obj = self.session.scene.add_primitive(a_box(0.2))
        self.session.scene.select(obj)
        self.session.grab.params.damping = 0.0
        self.session.grab.params.dead_zone_scale = 0.0
        self.session.on_grip(0, 1.0, position=(-0.2, 0.0, 0.0))
        self.session.on_grip(1, 1.0, position=(0.2, 0.0, 0.0))
        self.session.on_move(0, position=(-0.4, 0.0, 0.0))
        self.session.on_move(1, position=(0.4, 0.0, 0.0))
        self.session.update(1.0 / 90.0, [])
        self.assertAlmostEqual(obj.transform.scale, 2.0, places=9)

    def test_measure_tool_records_points(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_MEASURE)
        self.session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0))
        self.session.on_trigger(0, 0.0, position=(0.0, 0.0, 0.0))
        self.session.on_trigger(0, 1.0, position=(0.1, 0.0, 0.0))
        self.assertTrue(self.session.measure.current.complete)
        self.assertAlmostEqual(self.session.measure.current.value(), 100.0,
                               places=6)

    def test_snapping_uses_the_user_scale(self):
        controller = ScaleController(scale=12.0, unit_scale=1.0, duration=0.0)
        self.session.bind_scale(controller)
        self.assertAlmostEqual(self.session.user_scale, 12.0, places=12)
        self.assertIsNotNone(self.session.world_grab)
        self.session.scene.add_cage(cube_cage(0.2))
        snapped = self.session.snap_point((0.1005, 0.1, 0.1))
        self.assertAlmostEqual(vm.dist(snapped, (0.1, 0.1, 0.1)), 0.0,
                               places=9)

    def test_update_drives_from_controller_objects(self):
        class _State(object):
            trigger = 0.0
            grab = 0.0
            lever_x = 0.0
            lever_y = 0.0

        class _Transform(object):
            class _V(object):
                def __init__(self, value):
                    self._value = value

                def getValue(self):
                    return self._value
            def __init__(self, position):
                self.translation = self._V(position)
                self.rotation = self._V((0.0, 0.0, 0.0, 1.0))

        class _Controller(object):
            def __init__(self, position):
                self.position = position
                self.state = _State()

            def get_buttons_states(self):
                return self.state

            def get_global_transf(self):
                return _Transform(self.position)

        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_CURVE)
        self.session.snap.settings.enabled = False
        left = _Controller((0.0, 0.0, 0.0))
        left.state.trigger = 1.0
        self.session.update(1.0 / 90.0, [left])
        for i in range(1, 20):
            left.position = (i * 0.01, 0.0, 0.0)
            self.session.update(1.0 / 90.0, [left])
        left.state.trigger = 0.0
        self.session.update(1.0 / 90.0, [left])
        self.assertEqual(len(self.session.scene.objects), 1)

    def test_a_broken_controller_is_ignored(self):
        class _Bad(object):
            def get_buttons_states(self):
                raise RuntimeError("no device")
        self.assertFalse(self.session.update(0.016, [_Bad(), None]))

    def test_cancel_all_and_detach(self):
        from xrsketch import session as S_
        self.session.set_tool(S_.TOOL_CURVE)
        self.session.on_trigger(0, 1.0, position=(0.0, 0.0, 0.0))
        self.session.on_move(0, position=(0.1, 0.0, 0.0))
        self.session.cancel_all()
        self.session.on_trigger(0, 0.0, position=(0.1, 0.0, 0.0))
        self.assertEqual(len(self.session.scene.objects), 0)
        self.assertIsNone(self.session.detach())

    def test_thumbstick_cycles_the_primitive_kind(self):
        before = self.session.placement.kind
        self.assertTrue(self.session.on_thumbstick(0, 1.0, 0.0))
        self.assertNotEqual(self.session.placement.kind, before)


# ==========================================================================
# committing to a document
# ==========================================================================

class _FakeObject(object):
    def __init__(self, type_id, name):
        self.TypeId = type_id
        self.Name = name
        self.Label = name
        self.Group = []

    def addObject(self, obj):
        self.Group.append(obj)


class _FakeDocument(object):
    def __init__(self):
        self.objects = []
        self.recomputed = False

    def addObject(self, type_id, name):
        obj = _FakeObject(type_id, name)
        self.objects.append(obj)
        return obj

    def recompute(self):
        self.recomputed = True
        return True


class TestCommit(unittest.TestCase):

    def setUp(self):
        from Tests import stubs
        stubs.install()
        self.addCleanup(stubs.uninstall)
        self.doc = _FakeDocument()

    def test_missing_reason_without_freecad(self):
        from Tests import stubs
        stubs.uninstall()
        from xrsketch import to_freecad
        self.assertFalse(to_freecad.is_available())
        self.assertIn("FreeCAD", to_freecad.missing_reason())
        stubs.install()

    def test_commit_makes_grouped_features(self):
        from xrsketch import to_freecad
        scene = SC.Scene()
        inner = scene.add_layer("Detail", scene.layers[0].id)
        scene.add_primitive(a_box(), layer=inner.id)
        scene.add_primitive(P.Primitive.from_two_points("cylinder",
                                                        (0, 0, 0),
                                                        (0, 0, 0.1)))
        scene.add_cage(cube_cage(0.1))
        scene.add_curve(C.Curve3D.from_points([(0, 0, 0), (0.1, 0.1, 0),
                                               (0.2, 0, 0)]))
        result = to_freecad.commit(scene, document=self.doc)
        self.assertTrue(self.doc.recomputed)
        types = [o.TypeId for o in self.doc.objects]
        self.assertIn("Part::Box", types)
        self.assertIn("Part::Cylinder", types)
        self.assertIn("Mesh::Feature", types)
        self.assertEqual(types.count("App::DocumentObjectGroup"), 3)
        self.assertGreaterEqual(len(result.objects), 4)

    def test_a_reference_image_is_skipped_with_a_message(self):
        from xrsketch import to_freecad
        scene = SC.Scene()
        # ImagePlane ids come from a process-global counter, so the generated
        # name depends on how many planes any earlier test made. Compare
        # against the object's own id rather than a literal.
        plane = R.ImagePlane("blueprint.png")
        scene.add("image", plane)
        result = to_freecad.commit(scene, document=self.doc)
        self.assertEqual(result.skipped, [plane.id])
        self.assertTrue(any("reference image" in m for m in result.messages))

    def test_scale_converts_metres_to_millimetres(self):
        from xrsketch import to_freecad
        scene = SC.Scene()
        scene.add_primitive(a_box(0.1))
        to_freecad.commit(scene, document=self.doc)
        box = [o for o in self.doc.objects if o.TypeId == "Part::Box"][0]
        self.assertAlmostEqual(box.Length, 100.0, places=9)

    def test_no_document_is_reported_clearly(self):
        from xrsketch import to_freecad
        scene = SC.Scene()
        with self.assertRaises(RuntimeError):
            to_freecad.commit(scene, document=object())

    def test_the_session_commits_through_the_same_path(self):
        from xrsketch.session import SketchSession
        session = SketchSession()
        session.scene.add_primitive(a_box())
        result = session.commit_to_document(self.doc)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.objects), 1)


if __name__ == "__main__":
    unittest.main()
