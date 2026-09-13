# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bringing models in from files and from the sharing platforms.

::

    formats.py   STL / OBJ / PLY / 3MF readers and writers, no dependencies
    sources.py   Thingiverse (official API), Printables and MakerWorld
                 (unofficial endpoints), GrabCAD (URL recognition + manual ZIP)
    convert.py   files and archives into document objects; kernel formats
                 handed to FreeCAD's importers

The pure parts — parsing, URL resolution, planning — run without FreeCAD.
"""

from .formats import FormatError, ReadResult, read, read_file, sniff
from .sources import (ALL_SOURCES, GrabCAD, MakerWorld, ModelFile, ModelRef, Printables,
                      Source, SourceError, Thingiverse, resolve, source_for)
from .convert import ImportResult, import_archive, import_model, import_path, plan

__all__ = [
    "FormatError", "ReadResult", "read", "read_file", "sniff",
    "ALL_SOURCES", "GrabCAD", "MakerWorld", "ModelFile", "ModelRef", "Printables",
    "Source", "SourceError", "Thingiverse", "resolve", "source_for",
    "ImportResult", "import_archive", "import_model", "import_path", "plan",
]
