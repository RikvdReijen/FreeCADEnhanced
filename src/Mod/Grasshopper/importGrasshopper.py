# SPDX-License-Identifier: LGPL-2.1-or-later
"""Import/export of ``.fcgh`` files (the JSON graph of a Grasshopper mode
definition) so definitions can be shared without a whole document."""

import os

import FreeCAD


def open(filename):  # noqa: A001 - FreeCAD importer API
    doc = FreeCAD.newDocument(os.path.splitext(os.path.basename(filename))[0])
    insert(filename, doc.Name)
    return doc


def insert(filename, docname):
    from grasshoppermode import document as ghdoc

    doc = FreeCAD.getDocument(docname)
    with __builtins__["open"](filename, "r", encoding="utf-8") as fh:
        text = fh.read()
    obj = ghdoc.make_definition(doc, os.path.splitext(os.path.basename(filename))[0], text)
    doc.recompute()
    return obj


def export(objects, filename):
    from grasshoppermode import document as ghdoc

    for obj in objects:
        if ghdoc.is_definition(obj):
            with __builtins__["open"](filename, "w", encoding="utf-8") as fh:
                fh.write(ghdoc.definition_json(obj))
            return
    raise ValueError("select a Grasshopper mode definition to export")
