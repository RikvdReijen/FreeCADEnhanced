# SPDX-License-Identifier: LGPL-2.1-or-later
"""From files on disk to objects in the document.

``import_path`` handles a single file of any supported format;
``import_archive`` unpacks a ZIP (a GrabCAD download, a Thingiverse
"download all") and imports every supported file inside it. Mesh formats are
read here and become ``Mesh::Feature`` objects — or ``Part::Feature`` solids
when ``as_solid`` is set and the mesh is closed; kernel formats (STEP, IGES,
BREP, FCStd) go to FreeCAD's own importers. ``scale_mm`` converts file units
into the document's millimetres.

The FreeCAD calls live in one function each and are guarded, so everything
up to "make the object" runs and is tested without FreeCAD.
"""

import os
import tempfile
import zipfile

from . import formats


class ImportResult(object):
    __slots__ = ("objects", "meshes", "skipped", "notes", "source")

    def __init__(self):
        self.objects = []
        self.meshes = []
        self.skipped = []
        self.notes = []
        self.source = None

    def to_dict(self):
        return {"objects": list(self.objects), "meshes": [m.name for m in self.meshes],
                "skipped": list(self.skipped), "notes": list(self.notes), "source": self.source}

    def __repr__(self):
        return "ImportResult(%d objects, %d skipped)" % (len(self.objects), len(self.skipped))


def plan(path, scale_mm=None):
    """Decide how a file will be imported without touching FreeCAD.

    Returns ``("mesh", ReadResult)`` for mesh formats (parsed), ``("kernel",
    ext)`` for STEP & co, or ``("unsupported", ext)``.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in formats.MESH_EXTENSIONS:
        result = formats.read_file(path)
        factor = scale_mm if scale_mm is not None else (result.unit_mm or 1.0)
        if factor != 1.0:
            result.meshes = [_scaled(m, factor) for m in result.meshes]
            result.notes.append("scaled by %g to millimetres" % factor)
        return "mesh", result
    if ext in formats.KERNEL_EXTENSIONS:
        return "kernel", ext
    return "unsupported", ext


def _scaled(mesh, factor):
    from xrfit.mesh import TriMesh

    return TriMesh([(x * factor, y * factor, z * factor) for x, y, z in mesh.vertices], mesh.triangles, mesh.name)


def import_path(path, document=None, scale_mm=None, as_solid=False, result=None):
    result = result or ImportResult()
    kind, payload = plan(path, scale_mm)
    if kind == "unsupported":
        result.skipped.append("%s: unsupported format %s" % (os.path.basename(path), payload))
        return result
    if kind == "kernel":
        names = _import_with_freecad(path, document)
        result.objects.extend(names)
        if not names:
            result.skipped.append("%s: FreeCAD importer unavailable" % os.path.basename(path))
        return result
    result.notes.extend(payload.notes)
    for mesh in payload.meshes:
        result.meshes.append(mesh)
        name = _make_mesh_object(mesh, document, as_solid)
        if name:
            result.objects.append(name)
    return result


def import_archive(path, document=None, scale_mm=None, as_solid=False, result=None):
    """Import every supported file from a ZIP."""
    result = result or ImportResult()
    result.source = path
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result.skipped.append("%s: not a readable ZIP archive (%s)" % (os.path.basename(path), exc))
        return result
    with tempfile.TemporaryDirectory(prefix="xrimport-") as tmp:
        for info in archive.infolist():
            if info.is_dir():
                continue
            ext = os.path.splitext(info.filename)[1].lower()
            if ext not in formats.SUPPORTED_EXTENSIONS:
                result.skipped.append("%s: not a model file" % info.filename)
                continue
            target = os.path.join(tmp, os.path.basename(info.filename))
            with archive.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            import_path(target, document, scale_mm, as_solid, result)
    return result


def import_model(ref, source, document=None, dest_dir=None, scale_mm=None, as_solid=False, files=None):
    """Download a resolved :class:`ModelRef`'s files and import them."""
    result = ImportResult()
    result.source = ref.url
    result.notes.extend(ref.notes)
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="xrimport-")
    wanted = files if files is not None else ref.printable_files
    if not wanted:
        result.skipped.append("%s: no downloadable model files" % ref.title)
        return result
    for model_file in wanted:
        try:
            local = source.download(model_file, dest_dir)
        except Exception as exc:
            result.skipped.append("%s: download failed: %s" % (model_file.name, exc))
            continue
        if local.lower().endswith(".zip"):
            import_archive(local, document, scale_mm, as_solid, result)
        else:
            import_path(local, document, scale_mm, as_solid, result)
    return result


# ----------------------------------------------------------------------
# FreeCAD side
# ----------------------------------------------------------------------


def _freecad():
    try:
        import FreeCAD
    except ImportError:
        return None
    return FreeCAD


def _document(document):
    App = _freecad()
    if App is None:
        return None
    if document is not None:
        return document
    doc = getattr(App, "ActiveDocument", None)
    return doc or App.newDocument("Import")


def _make_mesh_object(mesh, document, as_solid):
    doc = _document(document)
    if doc is None:
        return None
    try:
        import Mesh

        fc_mesh = Mesh.Mesh()
        fc_mesh.addFacets([mesh.triangle(i) for i in range(len(mesh))])
        label = mesh.name or "Mesh"
        if as_solid:
            try:
                import Part

                shape = Part.Shape()
                shape.makeShapeFromMesh(fc_mesh.Topology, 0.05)
                solid = Part.makeSolid(shape)
                obj = doc.addObject("Part::Feature", label)
                obj.Shape = solid
                return obj.Name
            except Exception:
                pass  # fall back to a mesh object
        obj = doc.addObject("Mesh::Feature", label)
        obj.Mesh = fc_mesh
        return obj.Name
    except ImportError:
        return None


def _import_with_freecad(path, document):
    doc = _document(document)
    if doc is None:
        return []
    ext = os.path.splitext(path)[1].lower()
    before = {o.Name for o in getattr(doc, "Objects", [])}
    try:
        if ext == ".fcstd":
            App = _freecad()
            App.openDocument(path)
            return []
        import Import

        Import.insert(path, doc.Name)
    except ImportError:
        try:
            import Part

            Part.insert(path, doc.Name)
        except Exception:
            return []
    except Exception:
        return []
    return [o.Name for o in getattr(doc, "Objects", []) if o.Name not in before]
