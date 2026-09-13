# SPDX-License-Identifier: LGPL-2.1-or-later
"""Importing from the sharing platforms and from downloaded archives.

``import_url(url)`` resolves a Thingiverse, Printables, MakerWorld or
GrabCAD link, downloads the model files it can and imports them into the
active document (meshes as ``Mesh::Feature``, or solids when the
preference says so). Tokens live in the XR preferences
(``ImportTokenThingiverse``). ``import_archive(path)`` takes a ZIP the
user downloaded by hand — GrabCAD's only route.
"""

import FreeCAD

from xrcore import service

__all__ = ["import_url", "import_archive", "import_file", "tokens"]


def tokens():
    prefs = service.preferences()
    return {"thingiverse": prefs.GetString("ImportTokenThingiverse", "") or None}


def _report(result):
    for note in result.notes:
        FreeCAD.Console.PrintMessage("XR import: %s\n" % note)
    for skipped in result.skipped:
        FreeCAD.Console.PrintWarning("XR import: skipped %s\n" % skipped)
    FreeCAD.Console.PrintMessage("XR import: %d object(s) created\n" % len(result.objects))
    return result


def import_url(url, document=None, as_solid=None):
    from xrimport import convert, source_for

    source = source_for(url, tokens=tokens())
    ref = source.resolve(url)
    FreeCAD.Console.PrintMessage("XR import: %s — %s by %s, %d file(s)\n" % (ref.source, ref.title, ref.author or "?", len(ref.files)))
    for note in ref.notes:
        FreeCAD.Console.PrintMessage("XR import: %s\n" % note)
    if as_solid is None:
        as_solid = service.preferences().GetBool("ImportAsSolid", False)
    result = convert.import_model(ref, source, document or FreeCAD.ActiveDocument, as_solid=as_solid)
    return _report(result)


def import_archive(path, document=None, as_solid=None):
    from xrimport import convert

    if as_solid is None:
        as_solid = service.preferences().GetBool("ImportAsSolid", False)
    return _report(convert.import_archive(path, document or FreeCAD.ActiveDocument, as_solid=as_solid))


def import_file(path, document=None, as_solid=None, scale_mm=None):
    from xrimport import convert

    if as_solid is None:
        as_solid = service.preferences().GetBool("ImportAsSolid", False)
    return _report(convert.import_path(path, document or FreeCAD.ActiveDocument, scale_mm, as_solid))
