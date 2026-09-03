# SPDX-License-Identifier: LGPL-2.1-or-later
"""The ``.layers/`` folder beside a document (SPEC §1).

::

    project/
    ├── housing.FCStd              the document, unmodified
    ├── housing.layers/
    │   ├── index.json             order, enabled state, base revision
    │   ├── claims.json            live claims (collab.claims)
    │   ├── dev-a41c.json          one layer
    │   └── dev-93b7.json
    └── project.contracts.json     interface contracts

``index.json`` is the only file with a merge-conflict risk in the ordinary git
sense, and it is small, line-oriented and human-readable — deliberately. It
is written with one entry per line so two people enabling different layers
merge cleanly as text.

Writes are atomic (write to a sibling, then ``os.replace``) so a crash never
leaves a half-written layer for another worktree to read.
"""

import json
import os
import tempfile

from .errors import LayerFormatError, StoreError
from .schema import Layer

INDEX_FILE = "index.json"
CLAIMS_FILE = "claims.json"
CONTRACTS_FILE = "project.contracts.json"
LAYER_SUFFIX = ".json"


def write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=LAYER_SUFFIX, dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def layers_dir_for(document_path):
    """``housing.FCStd`` -> ``housing.layers`` (in the same directory)."""
    root, _ = os.path.splitext(document_path)
    return root + ".layers"


class Index:
    __slots__ = ("document", "base", "order", "enabled")

    def __init__(self, document="", base="", order=(), enabled=None):
        self.document = document
        self.base = base
        self.order = list(order)
        self.enabled = dict(enabled or {})
        for layer_id in self.order:
            self.enabled.setdefault(layer_id, True)

    def to_json(self):
        return {
            "document": self.document,
            "base": self.base,
            "order": list(self.order),
            "enabled": {k: self.enabled.get(k, True) for k in self.order},
        }

    def dumps(self):
        # One entry per line: this is the file that gets merged as text.
        lines = ["{", f'  "document": {json.dumps(self.document)},', f'  "base": {json.dumps(self.base)},']
        lines.append('  "order": [')
        for i, layer_id in enumerate(self.order):
            comma = "," if i < len(self.order) - 1 else ""
            lines.append(f"    {json.dumps(layer_id)}{comma}")
        lines.append("  ],")
        lines.append('  "enabled": {')
        for i, layer_id in enumerate(self.order):
            comma = "," if i < len(self.order) - 1 else ""
            lines.append(f"    {json.dumps(layer_id)}: {json.dumps(bool(self.enabled.get(layer_id, True)))}{comma}")
        lines.append("  }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            raise LayerFormatError("index must be an object", INDEX_FILE)
        order = data.get("order", [])
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise LayerFormatError("'order' must be a list of layer ids", INDEX_FILE)
        if len(set(order)) != len(order):
            raise LayerFormatError("'order' lists a layer twice", INDEX_FILE)
        enabled = data.get("enabled", {})
        if not isinstance(enabled, dict):
            raise LayerFormatError("'enabled' must be a map of id to bool", INDEX_FILE)
        return cls(
            document=data.get("document", ""),
            base=data.get("base", ""),
            order=order,
            enabled={k: bool(v) for k, v in enabled.items()},
        )


class LayerStore:
    """Read and write the layer folder for one document."""

    def __init__(self, document_path=None, directory=None):
        if directory is None:
            if document_path is None:
                raise StoreError("LayerStore needs a document path or a directory")
            directory = layers_dir_for(document_path)
        self.directory = os.path.abspath(directory)
        self.document_path = document_path

    # -- paths ----------------------------------------------------------

    @property
    def index_path(self):
        return os.path.join(self.directory, INDEX_FILE)

    @property
    def claims_path(self):
        return os.path.join(self.directory, CLAIMS_FILE)

    @property
    def contracts_path(self):
        return os.path.join(os.path.dirname(self.directory), CONTRACTS_FILE)

    def layer_path(self, layer_id):
        return os.path.join(self.directory, layer_id + LAYER_SUFFIX)

    def exists(self):
        return os.path.isfile(self.index_path)

    # -- index ----------------------------------------------------------

    def init(self, base, document=None):
        """Create the folder and an empty index. Refuses to clobber an existing one."""
        if self.exists():
            raise StoreError(f"{self.directory} already has an {INDEX_FILE}")
        os.makedirs(self.directory, exist_ok=True)
        if document is None:
            document = os.path.basename(self.document_path) if self.document_path else ""
        index = Index(document=document, base=base)
        self.save_index(index)
        return index

    def load_index(self):
        if not self.exists():
            raise StoreError(f"no {INDEX_FILE} in {self.directory}; run init first")
        with open(self.index_path, "r", encoding="utf-8") as handle:
            try:
                return Index.from_json(json.load(handle))
            except json.JSONDecodeError as exc:
                raise LayerFormatError(f"not valid JSON: {exc}", INDEX_FILE) from None

    def save_index(self, index):
        write_atomic(self.index_path, index.dumps())

    # -- layers ---------------------------------------------------------

    def load_layer(self, layer_id):
        path = self.layer_path(layer_id)
        if not os.path.isfile(path):
            raise StoreError(f"layer {layer_id!r} has no file at {path}")
        with open(path, "r", encoding="utf-8") as handle:
            layer = Layer.loads(handle.read(), path=os.path.basename(path))
        if layer.id != layer_id:
            raise StoreError(f"{path} declares id {layer.id!r}, expected {layer_id!r}")
        return layer

    def save_layer(self, layer):
        os.makedirs(self.directory, exist_ok=True)
        write_atomic(self.layer_path(layer.id), layer.dumps())

    def layers(self, enabled_only=False):
        """Layers in index order."""
        index = self.load_index()
        return [
            self.load_layer(layer_id)
            for layer_id in index.order
            if not enabled_only or index.enabled.get(layer_id, True)
        ]

    def add(self, layer, enabled=True, position=None):
        """Save a layer and append it to the index (or insert at ``position``)."""
        index = self.load_index()
        if layer.id in index.order:
            raise StoreError(f"layer {layer.id!r} is already in the index")
        if index.base and layer.base and layer.base != index.base:
            raise StoreError(
                f"layer {layer.id!r} was recorded against base {layer.base!r}; "
                f"this store is at {index.base!r}. Rebase the layer (collab.replay) first."
            )
        self.save_layer(layer)
        if position is None:
            index.order.append(layer.id)
        else:
            index.order.insert(position, layer.id)
        index.enabled[layer.id] = bool(enabled)
        self.save_index(index)
        return index

    def remove(self, layer_id, delete_file=True):
        index = self.load_index()
        if layer_id not in index.order:
            raise StoreError(f"layer {layer_id!r} is not in the index")
        index.order.remove(layer_id)
        index.enabled.pop(layer_id, None)
        self.save_index(index)
        if delete_file:
            try:
                os.unlink(self.layer_path(layer_id))
            except FileNotFoundError:
                pass
        return index

    def set_enabled(self, layer_id, enabled):
        """Mute or unmute a layer. The work stays; only the evaluation changes."""
        index = self.load_index()
        if layer_id not in index.order:
            raise StoreError(f"layer {layer_id!r} is not in the index")
        index.enabled[layer_id] = bool(enabled)
        self.save_index(index)
        return index

    def move(self, layer_id, to=None, after=None, before=None):
        """Reorder. Exactly one of ``to`` (index), ``after`` or ``before`` (ids)."""
        index = self.load_index()
        if layer_id not in index.order:
            raise StoreError(f"layer {layer_id!r} is not in the index")
        given = [x for x in (to, after, before) if x is not None]
        if len(given) != 1:
            raise StoreError("move() takes exactly one of to=, after=, before=")
        order = [x for x in index.order if x != layer_id]
        if to is not None:
            position = max(0, min(int(to), len(order)))
        else:
            ref = after if after is not None else before
            if ref not in order:
                raise StoreError(f"reference layer {ref!r} is not in the index")
            position = order.index(ref) + (1 if after is not None else 0)
        order.insert(position, layer_id)
        index.order = order
        self.save_index(index)
        return index

    # -- consistency ----------------------------------------------------

    def check(self):
        """Problems with the folder: orphan files, missing files, base drift."""
        problems = []
        try:
            index = self.load_index()
        except (StoreError, LayerFormatError) as exc:
            return [str(exc)]
        on_disk = {
            name[: -len(LAYER_SUFFIX)]
            for name in os.listdir(self.directory)
            if name.endswith(LAYER_SUFFIX) and name not in (INDEX_FILE, CLAIMS_FILE) and not name.startswith(".")
        }
        for layer_id in index.order:
            if layer_id not in on_disk:
                problems.append(f"index lists {layer_id!r} but {layer_id}{LAYER_SUFFIX} is missing")
                continue
            try:
                layer = self.load_layer(layer_id)
            except (StoreError, LayerFormatError) as exc:
                problems.append(str(exc))
                continue
            if index.base and layer.base and layer.base != index.base:
                problems.append(f"{layer_id} was recorded against {layer.base!r}; index base is {index.base!r}")
        for orphan in sorted(on_disk - set(index.order)):
            problems.append(f"{orphan}{LAYER_SUFFIX} is on disk but not in the index")
        return problems
