# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for importing an FCXR package back into a FreeCAD document.

The scene-graph flattening, unit conversion and colour conversion are pure and
tested directly; the document-creating part of ``import_package`` is exercised
against injected fake ``FreeCAD``/``Mesh``/``xrcore.paint_bridge`` modules, so
no FreeCAD is needed here either.
"""

import math
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsync import scene_import  # noqa: E402
from xrsync.fcxr import FcxrError, FcxrWriter, read  # noqa: E402
from xrsync.scene_export import _srgb_to_linear  # noqa: E402
from xrsync.scene_import import (  # noqa: E402
    IDENTITY,
    Transform,
    extract_meshes,
    linear_to_srgb,
)

# the exporter's Z-up -> Y-up root rotation
Z_UP_TO_Y_UP = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))

MM = 0.001  # metres per millimetre


def build_package(
    y_up=True,
    translation=(0.01, 0.0, 0.0),
    node_scale=(1.0, 1.0, 1.0),
    visible=True,
    with_indices=True,
    colour=(1.0, 0.0, 0.0),
    alpha=1.0,
):
    """A one-triangle scene shaped exactly like the exporter's output."""
    writer = FcxrWriter(source_document="Bracket.FCStd")
    if y_up:
        writer.set_asset_field("up_axis", "Y")
    material = writer.add_material(
        "Red",
        base_color=[
            _srgb_to_linear(colour[0]),
            _srgb_to_linear(colour[1]),
            _srgb_to_linear(colour[2]),
            alpha,
        ],
    )
    positions = [(0.0, 0.0, 0.0), (1.0 * MM, 0.0, 0.0), (0.0, 1.0 * MM, 0.0)]
    mesh = writer.add_mesh(
        "Bracket",
        positions=positions,
        normals=[(0.0, 0.0, 1.0)] * 3,
        indices=[(0, 1, 2)] if with_indices else None,
        material=material,
    )
    child = writer.add_node(
        "Bracket",
        mesh=mesh,
        translation=translation,
        scale=node_scale,
        fc_name="Bracket",
        visible=visible,
    )
    root = writer.add_node(
        "Bracket.FCStd",
        children=[child],
        rotation=Z_UP_TO_Y_UP if y_up else (0.0, 0.0, 0.0, 1.0),
        fc_name="Bracket.FCStd",
    )
    writer.set_scene(root=root)
    return read(writer.to_bytes())


class TransformTest(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(IDENTITY.apply((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))

    def test_translation_and_scale(self):
        transform = Transform(translation=(1.0, 0.0, 0.0), scale=(2.0, 2.0, 2.0))
        self.assertEqual(transform.apply((1.0, 1.0, 1.0)), (3.0, 2.0, 2.0))

    def test_quarter_turn_about_x(self):
        transform = Transform(rotation=(math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)))
        x, y, z = transform.apply((0.0, 1.0, 0.0))
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(z, 1.0)

    def test_composition_matches_sequential_application(self):
        parent = Transform(translation=(1.0, 2.0, 3.0),
                           rotation=(0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)))
        child = Transform(translation=(0.0, 1.0, 0.0), scale=(2.0, 2.0, 2.0))
        point = (1.0, 0.0, 0.5)
        composed = parent.compose(child).apply(point)
        sequential = parent.apply(child.apply(point))
        for a, b in zip(composed, sequential):
            self.assertAlmostEqual(a, b)

    def test_y_up_root_is_undone(self):
        combined = scene_import._Y_UP_TO_Z_UP.compose(Transform(rotation=Z_UP_TO_Y_UP))
        for a, b in zip(combined.apply((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0)):
            self.assertAlmostEqual(a, b)


class ColourTest(unittest.TestCase):
    def test_round_trip(self):
        for value in (0.0, 0.05, 0.25, 0.5, 0.8, 1.0):
            self.assertAlmostEqual(linear_to_srgb(_srgb_to_linear(value)), value,
                                   places=6)

    def test_clamping(self):
        self.assertEqual(linear_to_srgb(-1.0), 0.0)
        self.assertAlmostEqual(linear_to_srgb(2.0), 1.0, places=9)


class ExtractMeshesTest(unittest.TestCase):
    def test_units_and_axes_are_restored(self):
        specs = extract_meshes(build_package())
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.fc_name, "Bracket")
        # metres back to millimetres, Y-up back to Z-up
        for point, expected in zip(spec.points,
                                   [(0, 0, 0), (1, 0, 0), (0, 1, 0)]):
            for a, b in zip(point, expected):
                self.assertAlmostEqual(a, b, places=6)
        for a, b in zip(spec.translation, (10.0, 0.0, 0.0)):
            self.assertAlmostEqual(a, b, places=6)
        for a, b in zip(spec.rotation, (0.0, 0.0, 0.0, 1.0)):
            self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(spec.facets, [(0, 1, 2)])
        self.assertEqual(spec.triangle_count, 1)

    def test_z_up_packages_are_left_alone(self):
        specs = extract_meshes(build_package(y_up=False))
        for a, b in zip(specs[0].translation, (10.0, 0.0, 0.0)):
            self.assertAlmostEqual(a, b, places=6)
        for a, b in zip(specs[0].points[1], (1.0, 0.0, 0.0)):
            self.assertAlmostEqual(a, b, places=6)

    def test_node_scale_is_baked_into_the_points(self):
        specs = extract_meshes(build_package(node_scale=(3.0, 1.0, 1.0)))
        self.assertAlmostEqual(specs[0].points[1][0], 3.0, places=6)

    def test_material_colour_and_transparency(self):
        specs = extract_meshes(build_package(colour=(1.0, 0.0, 0.0), alpha=0.5))
        self.assertAlmostEqual(specs[0].color[0], 1.0, places=5)
        self.assertAlmostEqual(specs[0].color[1], 0.0, places=5)
        self.assertEqual(specs[0].transparency, 50)

    def test_missing_indices_fall_back_to_a_triangle_soup(self):
        specs = extract_meshes(build_package(with_indices=False))
        self.assertEqual(specs[0].facets, [(0, 1, 2)])

    def test_hidden_nodes_can_be_skipped(self):
        package = build_package(visible=False)
        self.assertEqual(len(extract_meshes(package, include_hidden=True)), 1)
        self.assertFalse(extract_meshes(package, include_hidden=True)[0].visible)
        self.assertEqual(extract_meshes(package, include_hidden=False), [])

    def test_empty_scene(self):
        writer = FcxrWriter()
        writer.set_scene(root=0)
        self.assertEqual(extract_meshes(read(writer.to_bytes())), [])

    def test_nested_transforms_accumulate(self):
        writer = FcxrWriter()
        mesh = writer.add_mesh("m", positions=[(0.0, 0.0, 0.0)] * 3,
                               indices=[(0, 1, 2)])
        leaf = writer.add_node("leaf", mesh=mesh, translation=(0.001, 0.0, 0.0))
        middle = writer.add_node("middle", children=[leaf],
                                 translation=(0.002, 0.0, 0.0))
        root = writer.add_node("root", children=[middle],
                               translation=(0.004, 0.0, 0.0))
        writer.set_scene(root=root)
        specs = extract_meshes(read(writer.to_bytes()))
        self.assertAlmostEqual(specs[0].translation[0], 7.0, places=6)

    def test_multiple_primitives_get_distinct_names(self):
        writer = FcxrWriter()
        positions = [(0.0, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.001, 0.0)]
        mesh = writer.add_mesh("m", positions=positions, indices=[(0, 1, 2)])
        writer.add_primitive(mesh, positions=positions, indices=[(0, 1, 2)])
        node = writer.add_node("Part", mesh=mesh, fc_name="Part")
        writer.set_scene(root=node)
        specs = extract_meshes(read(writer.to_bytes()))
        self.assertEqual([s.fc_name for s in specs], ["Part_0", "Part_1"])

    def test_wrong_argument_type(self):
        with self.assertRaises(FcxrError):
            extract_meshes({"not": "a document"})


# ---------------------------------------------------------------------------
# fake FreeCAD for the document-creating half
# ---------------------------------------------------------------------------


class FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __eq__(self, other):
        return (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __repr__(self):
        return "Vector(%g, %g, %g)" % (self.x, self.y, self.z)


class FakeRotation:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.Q = (float(x), float(y), float(z), float(w))


class FakePlacement:
    def __init__(self, base=None, rotation=None):
        self.Base = base or FakeVector()
        self.Rotation = rotation or FakeRotation()


class FakeMeshData:
    def __init__(self):
        self.facets = []

    def addFacet(self, a, b, c):  # noqa: N802 - FreeCAD's spelling
        self.facets.append((a, b, c))

    @property
    def CountFacets(self):  # noqa: N802
        return len(self.facets)


class FakeViewObject:
    def __init__(self):
        self.ShapeColor = (0.8, 0.8, 0.8)
        self.Transparency = 0
        self.Visibility = True


class FakeObject:
    def __init__(self, type_id, name):
        self.TypeId = type_id
        self.Name = name
        self.Label = name
        self.Mesh = None
        self.Placement = FakePlacement()
        self.ViewObject = FakeViewObject()


class FakeDocument:
    def __init__(self, name="Test"):
        self.Name = name
        self.Label = name
        self.Objects = []
        self.recomputed = 0

    def getObject(self, name):  # noqa: N802
        for obj in self.Objects:
            if obj.Name == name:
                return obj
        return None

    def addObject(self, type_id, name):  # noqa: N802
        obj = FakeObject(type_id, name)
        self.Objects.append(obj)
        return obj

    def recompute(self):
        self.recomputed += 1


class FakeModulesMixin:
    """Installs fake ``FreeCAD``/``Mesh``/``xrcore.paint_bridge`` modules."""

    def install_fakes(self, with_paint_bridge=True):
        saved = {
            name: sys.modules.get(name)
            for name in ("FreeCAD", "Mesh", "xrcore", "xrcore.paint_bridge")
        }
        self.addCleanup(self._restore, saved)

        freecad = types.ModuleType("FreeCAD")
        freecad.Vector = FakeVector
        freecad.Rotation = FakeRotation
        freecad.Placement = FakePlacement
        freecad.ActiveDocument = None
        freecad.newDocument = lambda name: FakeDocument(name)
        console = types.SimpleNamespace(
            PrintWarning=lambda *a: None, PrintMessage=lambda *a: None
        )
        freecad.Console = console
        sys.modules["FreeCAD"] = freecad

        mesh_module = types.ModuleType("Mesh")
        mesh_module.Mesh = FakeMeshData
        sys.modules["Mesh"] = mesh_module

        self.paint_calls = []
        self.vector_calls = []
        if with_paint_bridge:
            bridge = types.ModuleType("xrcore.paint_bridge")
            bridge.apply_remote_paint = lambda paint, images: self.paint_calls.append(
                (paint, images)
            )
            bridge.apply_remote_vector = lambda vector, doc: self.vector_calls.append(
                (vector, doc)
            )
            package = types.ModuleType("xrcore")
            package.__path__ = []
            package.paint_bridge = bridge
            sys.modules["xrcore"] = package
            sys.modules["xrcore.paint_bridge"] = bridge
        else:
            # Shadow the real xrcore package with one that has no paint
            # bridge, so this exercises the console-mode path even when the
            # GUI layer is present in the source tree.
            package = types.ModuleType("xrcore")
            package.__path__ = []
            sys.modules["xrcore"] = package
            sys.modules["xrcore.paint_bridge"] = None
        return freecad

    @staticmethod
    def _restore(saved):
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class ImportPackageTest(unittest.TestCase, FakeModulesMixin):
    def setUp(self):
        self.freecad = self.install_fakes()
        self.document = FakeDocument("Bracket")

    def test_creates_a_mesh_feature(self):
        created = scene_import.import_package(build_package(), self.document)
        self.assertEqual(len(created), 1)
        obj = created[0]
        self.assertEqual(obj.TypeId, "Mesh::Feature")
        self.assertEqual(obj.Name, "Bracket")
        self.assertEqual(obj.Mesh.CountFacets, 1)
        self.assertEqual(self.document.recomputed, 1)

    def test_placement_is_restored_in_millimetres(self):
        obj = scene_import.import_package(build_package(), self.document)[0]
        self.assertAlmostEqual(obj.Placement.Base.x, 10.0, places=6)
        self.assertAlmostEqual(obj.Placement.Base.y, 0.0, places=6)

    def test_facet_points_are_in_millimetres(self):
        obj = scene_import.import_package(build_package(), self.document)[0]
        a, b, c = obj.Mesh.facets[0]
        self.assertAlmostEqual(a.x, 0.0, places=6)
        self.assertAlmostEqual(b.x, 1.0, places=6)
        self.assertAlmostEqual(c.y, 1.0, places=6)

    def test_view_properties_are_restored(self):
        obj = scene_import.import_package(
            build_package(colour=(1.0, 0.0, 0.0), alpha=0.25), self.document
        )[0]
        self.assertAlmostEqual(obj.ViewObject.ShapeColor[0], 1.0, places=5)
        self.assertEqual(obj.ViewObject.Transparency, 75)
        self.assertTrue(obj.ViewObject.Visibility)

    def test_existing_objects_are_matched_not_duplicated(self):
        first = scene_import.import_package(build_package(), self.document)[0]
        second = scene_import.import_package(
            build_package(translation=(0.02, 0.0, 0.0)), self.document
        )[0]
        self.assertIs(first, second)
        self.assertEqual(len(self.document.Objects), 1)
        self.assertAlmostEqual(second.Placement.Base.x, 20.0, places=6)

    def test_a_non_mesh_object_with_the_same_name_is_not_overwritten(self):
        existing = self.document.addObject("Part::Feature", "Bracket")
        created = scene_import.import_package(build_package(), self.document)
        self.assertIsNot(created[0], existing)
        self.assertEqual(len(self.document.Objects), 2)

    def test_a_document_is_created_when_none_is_given(self):
        created = scene_import.import_package(build_package(), None)
        self.assertEqual(len(created), 1)

    def test_bytes_and_documents_are_both_accepted(self):
        package = build_package()
        self.assertEqual(
            len(scene_import.import_package(package.to_bytes(), self.document)), 1
        )
        self.assertEqual(len(scene_import.import_package(package, self.document)), 1)

    def test_corrupt_packages_are_rejected(self):
        with self.assertRaises(FcxrError):
            scene_import.import_package(b"not an fcxr", self.document)


class PaintHandoffTest(unittest.TestCase, FakeModulesMixin):
    PAINT = {
        "version": 1,
        "targets": [
            {"fc_name": "Bracket",
             "layers": [{"name": "Base", "image": 0, "blend": "normal",
                         "opacity": 1.0, "visible": True,
                         "resolution": [512, 512]}]}
        ],
    }
    VECTOR = {
        "version": 1,
        "paths": [
            {"id": "p1", "closed": False,
             "nodes": [{"point": [0, 0], "in": None, "out": None, "type": "corner"}],
             "stroke": {"color": [0, 0, 0, 1], "width": 0.5}, "fill": None,
             "target": "draft"}
        ],
    }
    PNG = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def package(self):
        writer = FcxrWriter()
        writer.add_image("layer0", self.PNG)
        writer.set_scene(root=0)
        writer.set_paint(self.PAINT)
        writer.set_vector(self.VECTOR)
        return read(writer.to_bytes())

    def test_paint_and_vector_reach_the_bridge(self):
        self.install_fakes(with_paint_bridge=True)
        document = FakeDocument("Bracket")
        scene_import.import_package(self.package(), document)
        self.assertEqual(len(self.paint_calls), 1)
        paint, images = self.paint_calls[0]
        self.assertEqual(paint, self.PAINT)
        self.assertEqual(images, [self.PNG])
        self.assertEqual(len(self.vector_calls), 1)
        self.assertEqual(self.vector_calls[0][0], self.VECTOR)
        self.assertIs(self.vector_calls[0][1], document)

    def test_console_mode_without_a_paint_bridge_still_imports(self):
        self.install_fakes(with_paint_bridge=False)
        document = FakeDocument("Bracket")
        scene_import.import_package(self.package(), document)
        self.assertEqual(document.recomputed, 1)
        self.assertFalse(scene_import.apply_paint_section(self.package(), document))
        self.assertFalse(scene_import.apply_vector_section(self.package(), document))

    def test_sections_are_optional(self):
        self.install_fakes(with_paint_bridge=True)
        writer = FcxrWriter()
        writer.set_scene(root=0)
        package = read(writer.to_bytes())
        self.assertFalse(scene_import.apply_paint_section(package))
        self.assertFalse(scene_import.apply_vector_section(package))


if __name__ == "__main__":
    unittest.main()
