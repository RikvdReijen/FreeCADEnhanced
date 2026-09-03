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
"""Control cages: Catmull-Clark, the limit surface and the editing operations.

Every editing test ends by asserting the cage is still a valid manifold — no
non-manifold edge, no orphaned half-edge, no bowtie vertex — because an
operation that produces the right counts and a broken topology is worse than
one that refuses.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsketch import vecmath as vm                             # noqa: E402
from xrsketch.subd import (Cage, HalfEdgeMesh, Selection,       # noqa: E402
                           SubdError, cube_cage, grid_cage)


class _CageAssertions(unittest.TestCase):

    def assertValid(self, cage, message=""):
        problems = cage.check()
        self.assertEqual(problems, [], "%s%s" % (message, problems))

    def assertNoDuplicateVertices(self, cage, tolerance=1e-9):
        seen = []
        for i, v in enumerate(cage.vertices):
            for j, w in enumerate(seen):
                if vm.dist(v, w) <= tolerance:
                    self.fail("vertices %d and %d coincide at %r" % (j, i, v))
            seen.append(v)


class TestTopology(_CageAssertions):

    def test_a_cube_is_a_valid_closed_manifold(self):
        cage = cube_cage(2.0)
        self.assertValid(cage)
        self.assertTrue(cage.is_closed())
        self.assertEqual((cage.vertex_count, cage.edge_count,
                          cage.face_count), (8, 12, 6))
        # Euler: V - E + F = 2 for a sphere
        self.assertEqual(cage.vertex_count - cage.edge_count
                         + cage.face_count, 2)

    def test_every_half_edge_has_a_mutual_twin(self):
        topo = cube_cage(1.0).topology()
        self.assertEqual(len(topo), 24)
        for h in range(len(topo)):
            self.assertGreaterEqual(topo.twin[h], 0)
            self.assertEqual(topo.twin[topo.twin[h]], h)
            self.assertEqual(topo.origin[topo.next[h]], topo.dest(h))
            self.assertEqual(topo.next[topo.prev[h]], h)

    def test_vertex_fans_close_up(self):
        cage = cube_cage(1.0)
        topo = cage.topology()
        for v in range(cage.vertex_count):
            fan = topo.fan(v)
            self.assertIsNotNone(fan)
            self.assertEqual(len(fan), 3)
            self.assertEqual(cage.valence(v), 3)

    def test_open_grid_has_a_boundary(self):
        cage = grid_cage(2, 2)
        self.assertValid(cage)
        self.assertFalse(cage.is_closed())
        self.assertIsNotNone(cage.boundary_neighbours(0))
        self.assertIsNone(cage.boundary_neighbours(4))   # the centre vertex
        self.assertEqual(cage.valence(4), 4)

    def test_non_manifold_edge_is_reported(self):
        cage = cube_cage(1.0)
        cage.vertices.append((0.0, 0.0, 2.0))
        # a third face on the edge (0, 1)
        cage.faces.append((1, 0, 8))
        cage.face_uvs.append(None)
        cage.invalidate()
        problems = cage.check()
        self.assertTrue(problems)
        self.assertTrue(any("3 faces" in p or "non-manifold" in p
                            for p in problems), problems)

    def test_inconsistent_winding_is_reported(self):
        cage = cube_cage(1.0)
        cage.faces[0] = tuple(reversed(cage.faces[0]))
        cage.invalidate()
        self.assertTrue(any("directed edge" in p for p in cage.check()))

    def test_repeated_corner_and_bad_index_are_reported(self):
        cage = Cage([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 1)])
        self.assertTrue(any("repeats" in p for p in cage.check()))
        cage = Cage([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1, 7)])
        self.assertTrue(any("references vertex" in p for p in cage.check()))
        self.assertRaises(SubdError, cage.validate)

    def test_bowtie_vertex_is_reported(self):
        # two quads meeting only at one shared vertex
        verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                 (1, 1, 0), (2, 1, 0), (2, 2, 0), (1, 2, 0)]
        cage = Cage(verts, [(0, 1, 2, 3), (2, 5, 6, 7)])
        problems = cage.check()
        self.assertEqual(problems, [])       # a boundary fan is legal
        # but closing both quads into one non-manifold vertex is not
        cage = Cage(verts, [(0, 1, 2, 3), (3, 2, 0, 1)])
        self.assertTrue(cage.check())

    def test_unused_vertices_are_optional_problems(self):
        cage = cube_cage(1.0)
        cage.vertices.append((5.0, 5.0, 5.0))
        cage.invalidate()
        self.assertEqual(cage.check(), [])
        self.assertTrue(cage.check(allow_unused=False))
        self.assertEqual(cage.compact(), 1)
        self.assertValid(cage)


class TestCatmullClark(_CageAssertions):

    def test_one_level_of_a_cube(self):
        cage = cube_cage(2.0).subdivide(1)
        self.assertValid(cage)
        # 8 corners + 12 edge points + 6 face points, 6 * 4 quads
        self.assertEqual(cage.vertex_count, 26)
        self.assertEqual(cage.face_count, 24)
        self.assertTrue(all(len(f) == 4 for f in cage.faces))
        self.assertTrue(cage.is_closed())

    def test_face_and_edge_counts_follow_the_refinement_rule(self):
        cage = cube_cage(1.0)
        for _ in range(3):
            v, e, f = cage.vertex_count, cage.edge_count, cage.face_count
            corners = sum(len(face) for face in cage.faces)
            cage = cage.subdivide(1)
            # one new quad per corner, one new vertex per old vertex, edge
            # and face
            self.assertEqual(cage.face_count, corners)
            self.assertEqual(cage.vertex_count, v + e + f)
            self.assertValid(cage)

    def test_valence_is_preserved_and_new_vertices_are_regular(self):
        cage = cube_cage(1.0).subdivide(2)
        valences = sorted(cage.valence(v) for v in range(cage.vertex_count))
        self.assertEqual(valences.count(3), 8)      # the original corners
        self.assertEqual(valences.count(4), cage.vertex_count - 8)

    def test_the_limit_mask_is_a_fixed_point_of_subdivision(self):
        """The limit point must not move as the cage is refined."""
        cage = cube_cage(2.0)
        expected = cage.limit_points()[0]
        current = cage
        for _ in range(5):
            current = current.subdivide(1)
            self.assertAlmostEqual(
                vm.dist(current.limit_points()[0], expected), 0.0, places=12)

    def test_subdivision_converges_to_the_limit_point(self):
        cage = cube_cage(2.0)
        limit = cage.limit_points()[0]
        self.assertAlmostEqual(vm.dist(limit, (-0.5, -0.5, -0.5)), 0.0,
                               places=12)
        current = cage
        previous = vm.dist(current.vertices[0], limit)
        for _ in range(5):
            current = current.subdivide(1)
            distance = vm.dist(current.vertices[0], limit)
            self.assertLess(distance, previous)
            previous = distance
        self.assertLess(previous, 1e-3)

    def test_limit_mask_matches_deep_subdivision_for_an_odd_valence(self):
        cage = cube_cage(2.0)
        cage.extrude_face(0, 1.0)               # makes valence 3 and 4 mixes
        self.assertValid(cage)
        limits = cage.limit_points()
        deep = cage.subdivide(4)
        for v in range(cage.vertex_count):
            self.assertLess(vm.dist(deep.vertices[v], limits[v]), 5e-3,
                            "vertex %d did not converge" % v)

    def test_boundary_vertices_follow_the_bspline_rule(self):
        cage = grid_cage(2, 2, (1.0, 1.0))
        limits = cage.limit_points()
        # a boundary corner of the unit grid: (P0 + 4P + P1) / 6
        self.assertAlmostEqual(limits[0][0], 1.0 / 12.0, places=12)
        self.assertAlmostEqual(limits[0][1], 1.0 / 12.0, places=12)
        deep = cage.subdivide(5)
        self.assertLess(vm.dist(deep.vertices[0], limits[0]), 1e-3)
        # the boundary stays planar and inside the original outline
        for v in cage.subdivide(2).vertices:
            self.assertGreaterEqual(v[0], -1e-12)
            self.assertLessEqual(v[0], 1.0 + 1e-12)

    def test_the_limit_surface_helper_lands_on_the_limit(self):
        cage = cube_cage(2.0)
        surface = cage.limit_surface(2)
        self.assertValid(surface)
        deep = cage.subdivide(5)
        for v in surface.vertices[:26]:
            near = min(vm.dist(v, w) for w in deep.vertices)
            self.assertLess(near, 1e-2)

    def test_subdivision_is_shape_preserving_and_shrinking(self):
        cage = cube_cage(2.0)
        lo, hi = cage.subdivide(3).bounds()
        for i in range(3):
            self.assertGreater(lo[i], -1.0)
            self.assertLess(hi[i], 1.0)


class TestEditing(_CageAssertions):

    def test_move_vertices(self):
        cage = cube_cage(2.0)
        cage.move_vertices([0, 1], (0.0, 0.0, -1.0))
        self.assertAlmostEqual(cage.vertices[0][2], -2.0, places=12)
        self.assertValid(cage)
        self.assertRaises(SubdError, cage.move_vertices, [99], (0, 0, 0))

    def test_extrude_face_counts(self):
        cage = cube_cage(2.0)
        v0, f0 = cage.vertex_count, cage.face_count
        index = cage.extrude_face(0, 1.0)
        self.assertEqual(cage.vertex_count, v0 + 4)
        self.assertEqual(cage.face_count, f0 + 4)
        self.assertEqual(len(cage.faces[index]), 4)
        self.assertValid(cage, "after extrude: ")
        self.assertTrue(cage.is_closed())
        # the cap really moved along the normal
        for vi in cage.faces[index]:
            self.assertAlmostEqual(cage.vertices[vi][2], -2.0, places=12)

    def test_extrude_an_ngon(self):
        cage = cube_cage(2.0)
        cage.bevel_edge(0, 1, 0.4)               # makes two pentagons
        pent = [i for i, f in enumerate(cage.faces) if len(f) == 5][0]
        v0, f0 = cage.vertex_count, cage.face_count
        cage.extrude_face(pent, 0.3)
        self.assertEqual(cage.vertex_count, v0 + 5)
        self.assertEqual(cage.face_count, f0 + 5)
        self.assertValid(cage, "after extruding a pentagon: ")

    def test_inset_face_counts(self):
        cage = cube_cage(2.0)
        v0, f0 = cage.vertex_count, cage.face_count
        index = cage.inset_face(0, 0.3)
        self.assertEqual(cage.vertex_count, v0 + 4)
        self.assertEqual(cage.face_count, f0 + 4)
        self.assertValid(cage, "after inset: ")
        # the inner face is smaller and coplanar with the original
        for vi in cage.faces[index]:
            v = cage.vertices[vi]
            self.assertAlmostEqual(v[2], -1.0, places=12)
            self.assertLess(abs(v[0]), 1.0)

    def test_inset_relative(self):
        cage = cube_cage(2.0)
        index = cage.inset_face(1, 0.5, relative=True)
        for vi in cage.faces[index]:
            self.assertAlmostEqual(abs(cage.vertices[vi][0]), 0.5, places=12)
        self.assertValid(cage)

    def test_bevel_edge_on_a_cube(self):
        cage = cube_cage(2.0)
        quad = cage.bevel_edge(0, 1, 0.4)
        self.assertEqual(cage.vertex_count, 10)      # 8 - 2 + 4
        self.assertEqual(cage.face_count, 7)         # 6 + the chamfer quad
        self.assertEqual(len(cage.faces[quad]), 4)
        self.assertEqual(sorted(len(f) for f in cage.faces),
                         [4, 4, 4, 4, 4, 5, 5])
        self.assertValid(cage, "after bevel: ")
        self.assertTrue(cage.is_closed())

    def test_bevel_at_a_high_valence_vertex(self):
        cage = cube_cage(2.0)
        cage.loop_cut(0, 1)                     # raises the valence to 4
        cage.validate()
        edge = None
        for (a, b) in cage.edge_keys():
            if cage.valence(a) > 3 and cage.valence(b) > 3:
                edge = (a, b)
                break
        self.assertIsNotNone(edge)
        v0, f0 = cage.vertex_count, cage.face_count
        cage.bevel_edge(edge[0], edge[1], 0.2)
        self.assertEqual(cage.vertex_count, v0 + 2)   # each end splits in two
        self.assertEqual(cage.face_count, f0 + 3)     # quad + two corner fills
        self.assertValid(cage, "after a high valence bevel: ")

    def test_bevel_refuses_a_boundary_edge(self):
        cage = grid_cage(2, 2)
        self.assertRaises(SubdError, cage.bevel_edge, 0, 1, 0.1)

    def test_loop_cut_on_a_cube(self):
        cage = cube_cage(2.0)
        edges, faces, closed = cage.edge_ring(0, 1)
        self.assertTrue(closed)
        self.assertEqual(len(edges), 4)
        self.assertEqual(len(faces), 4)
        count = cage.loop_cut(0, 1)
        self.assertEqual(count, 4)
        self.assertEqual(cage.vertex_count, 12)
        self.assertEqual(cage.face_count, 10)
        self.assertTrue(all(len(f) == 4 for f in cage.faces))
        self.assertValid(cage, "after loop cut: ")
        self.assertTrue(cage.is_closed())

    def test_two_loop_cuts_stay_valid(self):
        cage = cube_cage(2.0)
        cage.loop_cut(0, 1)
        keys = cage.edge_keys()
        cut = None
        for a, b in keys:
            try:
                cage.copy().edge_ring(a, b)
            except SubdError:
                continue
            cut = (a, b)
            break
        cage.loop_cut(*cut)
        self.assertValid(cage, "after two loop cuts: ")

    def test_loop_cut_refuses_a_non_quad_ring(self):
        cage = cube_cage(2.0)
        cage.bevel_edge(0, 1, 0.4)
        bad = None
        for a, b in cage.edge_keys():
            faces = cage.topology().edges[(a, b)]
            if any(len(cage.faces[f]) != 4 for f in faces):
                bad = (a, b)
                break
        self.assertIsNotNone(bad)
        self.assertRaises(SubdError, cage.loop_cut, *bad)

    def test_bridge_two_faces(self):
        cage = cube_cage(2.0)
        other = cube_cage(2.0, center=(0.0, 0.0, 6.0))
        offset = cage.vertex_count
        cage.vertices.extend(other.vertices)
        cage.faces.extend(tuple(v + offset for v in f) for f in other.faces)
        cage.face_uvs.extend([None] * other.face_count)
        cage.invalidate()
        self.assertValid(cage)
        v0, f0 = cage.vertex_count, cage.face_count
        made = cage.bridge_faces(1, 6 + 0)       # +Z of the first, -Z of the
        self.assertEqual(made, 4)                # second
        self.assertEqual(cage.vertex_count, v0)
        self.assertEqual(cage.face_count, f0 - 2 + 4)
        self.assertValid(cage, "after bridge: ")
        self.assertTrue(cage.is_closed())

    def test_bridge_refuses_mismatched_faces(self):
        cage = cube_cage(2.0)
        cage.bevel_edge(0, 1, 0.3)
        pent = [i for i, f in enumerate(cage.faces) if len(f) == 5][0]
        quad = [i for i, f in enumerate(cage.faces) if len(f) == 4][0]
        self.assertRaises(SubdError, cage.bridge_faces, pent, quad)
        self.assertRaises(SubdError, cage.bridge_faces, quad, quad)

    def test_delete_faces_and_vertices(self):
        cage = cube_cage(2.0)
        cage.delete_faces([0])
        self.assertEqual(cage.face_count, 5)
        self.assertEqual(cage.vertex_count, 8)   # every corner is still used
        self.assertValid(cage, "after delete: ")
        self.assertFalse(cage.is_closed())
        cage = cube_cage(2.0)
        cage.delete_vertices([0])
        self.assertEqual(cage.face_count, 3)
        self.assertEqual(cage.vertex_count, 7)
        self.assertValid(cage, "after deleting a vertex: ")

    def test_merge_vertices_welds_and_cleans_up(self):
        cage = cube_cage(2.0)
        kept = cage.merge_vertices([0, 1])
        self.assertEqual(kept, 0)
        self.assertEqual(cage.vertex_count, 7)
        self.assertEqual(cage.vertices[0], (0.0, -1.0, -1.0))
        # the two faces that used both vertices lost a corner, none collapsed
        self.assertEqual(cage.face_count, 6)
        self.assertValid(cage, "after merge: ")

    def test_weld_by_distance(self):
        cage = cube_cage(2.0)
        other = cube_cage(2.0, center=(2.0, 0.0, 0.0))
        offset = cage.vertex_count
        cage.vertices.extend(other.vertices)
        cage.faces.extend(tuple(v + offset for v in f) for f in other.faces)
        cage.face_uvs.extend([None] * other.face_count)
        cage.invalidate()
        merged = cage.weld(1e-9)
        self.assertEqual(merged, 4)              # the shared square
        self.assertEqual(cage.vertex_count, 12)

    def test_selection_helpers(self):
        cage = cube_cage(2.0)
        sel = Selection()
        sel.add_vertex(0).add_edge(1, 0).add_face(2)
        self.assertFalse(sel.empty)
        self.assertEqual(sel.edges, {(0, 1)})
        self.assertEqual(sel.vertex_set(cage), {0, 1, 4, 5})
        self.assertFalse(sel.toggle_vertex(0))
        sel.clear()
        self.assertTrue(sel.empty)


class TestMirror(_CageAssertions):

    def test_mirror_welds_the_seam(self):
        cage = grid_cage(2, 2, (1.0, 1.0))
        before = cage.vertex_count
        added, faces = cage.mirror((1.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        # the three vertices on the plane are shared, not duplicated
        self.assertEqual(added, before - 3)
        self.assertEqual(faces, 4)
        self.assertEqual(cage.vertex_count, 15)
        self.assertValid(cage, "after mirror: ")
        self.assertNoDuplicateVertices(cage)

    def test_mirror_produces_consistent_winding(self):
        cage = cube_cage(1.0, center=(1.0, 0.0, 0.0))
        cage.delete_faces([5])                  # open the -X side
        cage.mirror((0.5, 0.0, 0.0), (1.0, 0.0, 0.0))
        self.assertValid(cage, "after mirroring an open cage: ")
        self.assertNoDuplicateVertices(cage)

    def test_mirror_needs_a_normal(self):
        cage = cube_cage(1.0)
        self.assertRaises(SubdError, cage.mirror, (0, 0, 0), (0, 0, 0))

    def test_mirrored_geometry_is_the_reflection(self):
        cage = grid_cage(1, 1, (1.0, 1.0))
        cage.mirror((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        xs = sorted(round(v[0], 9) for v in cage.vertices)
        self.assertEqual(xs, [-1.0, -1.0, 0.0, 0.0, 1.0, 1.0])


class TestUVs(_CageAssertions):
    """The docstring promises which operations keep UVs; check it."""

    def test_a_fresh_cube_can_carry_uvs(self):
        cage = cube_cage(1.0, with_uvs=True)
        self.assertTrue(cage.has_uvs())
        self.assertTrue(cage.uv_complete())

    def test_subdivision_preserves_uvs(self):
        cage = cube_cage(1.0, with_uvs=True).subdivide(2)
        self.assertTrue(cage.uv_complete())
        for uv in cage.face_uvs:
            for u, v in uv:
                self.assertGreaterEqual(u, -1e-12)
                self.assertLessEqual(u, 1.0 + 1e-12)

    def test_move_and_mirror_preserve_uvs(self):
        cage = cube_cage(1.0, with_uvs=True)
        cage.move_vertices([0], (0.1, 0.0, 0.0))
        self.assertTrue(cage.uv_complete())
        cage.mirror((2.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        self.assertTrue(cage.uv_complete())

    def test_loop_cut_preserves_uvs(self):
        cage = cube_cage(1.0, with_uvs=True)
        cage.loop_cut(0, 1)
        self.assertTrue(cage.uv_complete())

    def test_extrude_inset_and_bevel_drop_uvs_as_documented(self):
        for operation in ("extrude", "inset", "bevel"):
            cage = cube_cage(1.0, with_uvs=True)
            if operation == "extrude":
                cage.extrude_face(0, 0.5)
            elif operation == "inset":
                cage.inset_face(0, 0.2)
            else:
                cage.bevel_edge(0, 1, 0.2)
            self.assertFalse(cage.uv_complete(),
                             "%s should not invent UVs" % operation)
            self.assertTrue(cage.has_uvs(),
                            "%s should keep the untouched faces' UVs"
                            % operation)


class TestSerialisation(_CageAssertions):

    def test_round_trip(self):
        cage = cube_cage(2.0, with_uvs=True)
        cage.extrude_face(0, 1.0)
        clone = Cage.from_dict(cage.to_dict())
        self.assertEqual(clone.faces, cage.faces)
        self.assertEqual(clone.vertices, cage.vertices)
        self.assertEqual(clone.face_uvs, cage.face_uvs)
        self.assertValid(clone)

    def test_copy_is_independent(self):
        cage = cube_cage(2.0)
        clone = cage.copy()
        clone.move_vertices([0], (1.0, 0.0, 0.0))
        self.assertNotEqual(clone.vertices[0], cage.vertices[0])

    def test_geometry_helpers(self):
        cage = cube_cage(2.0)
        self.assertAlmostEqual(cage.face_area(0), 4.0, places=12)
        self.assertAlmostEqual(vm.dist(cage.face_centroid(0),
                                       (0.0, 0.0, -1.0)), 0.0, places=12)
        normal = cage.face_normal(0)
        self.assertAlmostEqual(vm.dot(normal, (0.0, 0.0, -1.0)), 1.0,
                               places=12)
        self.assertEqual(len(cage.triangles()), 12)
        normals = cage.vertex_normals()
        self.assertEqual(len(normals), 8)
        self.assertAlmostEqual(vm.length(normals[0]), 1.0, places=12)


if __name__ == "__main__":
    unittest.main()
