# SPDX-License-Identifier: LGPL-2.1-or-later
"""The .layers/ folder."""

import json
import os
import shutil
import tempfile
import unittest

from collab.errors import LayerFormatError, StoreError
from collab.schema import Author, Layer, SetParam
from collab.store import Index, LayerStore, layers_dir_for


def layer(id, base="r1"):
    return Layer(id, name=id, author=Author("human", "rik"), base=base, operations=[SetParam("Pad3.Length", 12.0, 14.0)])


class LayerStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="collab-store-")
        self.doc = os.path.join(self.tmp, "housing.FCStd")
        with open(self.doc, "wb") as handle:
            handle.write(b"PK\x03\x04 not really a zip")
        self.store = LayerStore(self.doc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_folder_name(self):
        self.assertEqual(layers_dir_for("/p/housing.FCStd"), "/p/housing.layers")
        self.assertEqual(self.store.directory, os.path.join(self.tmp, "housing.layers"))
        self.assertEqual(self.store.contracts_path, os.path.join(self.tmp, "project.contracts.json"))

    def test_init_and_index_shape(self):
        self.assertFalse(self.store.exists())
        self.store.init("r1")
        self.assertTrue(self.store.exists())
        with open(self.store.index_path) as handle:
            data = json.load(handle)
        self.assertEqual(data, {"document": "housing.FCStd", "base": "r1", "order": [], "enabled": {}})
        with self.assertRaises(StoreError):
            self.store.init("r1")

    def test_add_list_mute_reorder_remove(self):
        self.store.init("r1")
        self.store.add(layer("dev-93b7"))
        self.store.add(layer("dev-a41c"))
        self.assertEqual([l.id for l in self.store.layers()], ["dev-93b7", "dev-a41c"])

        self.store.set_enabled("dev-a41c", False)
        self.assertEqual([l.id for l in self.store.layers(enabled_only=True)], ["dev-93b7"])
        self.assertTrue(os.path.isfile(self.store.layer_path("dev-a41c")), "muting keeps the work")

        self.store.move("dev-a41c", before="dev-93b7")
        self.assertEqual(self.store.load_index().order, ["dev-a41c", "dev-93b7"])
        self.store.move("dev-a41c", after="dev-93b7")
        self.assertEqual(self.store.load_index().order, ["dev-93b7", "dev-a41c"])
        self.store.move("dev-a41c", to=0)
        self.assertEqual(self.store.load_index().order, ["dev-a41c", "dev-93b7"])
        with self.assertRaises(StoreError):
            self.store.move("dev-a41c", after="x", before="y")

        self.store.remove("dev-93b7")
        self.assertEqual(self.store.load_index().order, ["dev-a41c"])
        self.assertFalse(os.path.exists(self.store.layer_path("dev-93b7")))
        self.assertEqual(self.store.check(), [])

    def test_index_is_one_entry_per_line(self):
        """The one file with ordinary merge-conflict risk must merge as text."""
        self.store.init("r1")
        self.store.add(layer("a"))
        self.store.add(layer("b"))
        with open(self.store.index_path) as handle:
            text = handle.read()
        self.assertIn('    "a",\n    "b"\n', text)
        self.assertIn('    "a": true,\n    "b": true\n', text)

    def test_duplicate_and_wrong_base(self):
        self.store.init("r1")
        self.store.add(layer("a"))
        with self.assertRaises(StoreError):
            self.store.add(layer("a"))
        with self.assertRaises(StoreError) as ctx:
            self.store.add(layer("b", base="r0"))
        self.assertIn("Rebase", str(ctx.exception))

    def test_check_finds_orphans_and_missing(self):
        self.store.init("r1")
        self.store.add(layer("a"))
        self.store.save_layer(layer("orphan"))
        os.unlink(self.store.layer_path("a"))
        problems = self.store.check()
        self.assertTrue(any("orphan" in p for p in problems))
        self.assertTrue(any("a.json is missing" in p for p in problems))

    def test_id_mismatch_is_detected(self):
        self.store.init("r1")
        self.store.add(layer("a"))
        with open(self.store.layer_path("a"), "w") as handle:
            handle.write(layer("b").dumps())
        with self.assertRaises(StoreError):
            self.store.load_layer("a")

    def test_bad_index(self):
        os.makedirs(self.store.directory)
        with open(self.store.index_path, "w") as handle:
            handle.write('{"order": ["a", "a"]}')
        with self.assertRaises(LayerFormatError):
            self.store.load_index()

    def test_atomic_write_leaves_no_temp_files(self):
        self.store.init("r1")
        self.store.add(layer("a"))
        names = os.listdir(self.store.directory)
        self.assertEqual(sorted(names), ["a.json", "index.json"])

    def test_index_defaults(self):
        index = Index.from_json({"order": ["x"]})
        self.assertEqual(index.enabled, {"x": True})


if __name__ == "__main__":
    unittest.main()
