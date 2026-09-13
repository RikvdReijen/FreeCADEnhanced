# SPDX-License-Identifier: LGPL-2.1-or-later
"""Mesh file parsers and writers with no dependencies: STL, OBJ, PLY, 3MF.

Every reader returns a list of :class:`xrfit.mesh.TriMesh` (one per object
or 3MF build item) with vertices in the file's units; ``units_of`` says what
those units are when the format records it (3MF does, the others do not).
STEP/IGES/FCStd need a kernel and are handed to FreeCAD's own importers by
:mod:`xrimport.convert`.

Readers are strict about structure and lenient about content: a broken
triangle is dropped with a note rather than aborting a 200 MB scan.
"""

import io
import os
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

from xrfit.mesh import TriMesh

MESH_EXTENSIONS = (".stl", ".obj", ".ply", ".3mf")
KERNEL_EXTENSIONS = (".step", ".stp", ".iges", ".igs", ".fcstd", ".brep", ".brp")
SUPPORTED_EXTENSIONS = MESH_EXTENSIONS + KERNEL_EXTENSIONS

#: 3MF unit names to millimetres.
UNIT_TO_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


class FormatError(ValueError):
    pass


class ReadResult(object):
    __slots__ = ("meshes", "unit_mm", "notes", "format")

    def __init__(self, meshes, unit_mm=None, notes=(), format=""):
        self.meshes = list(meshes)
        #: millimetres per file unit, or None when the format does not say
        self.unit_mm = unit_mm
        self.notes = list(notes)
        self.format = format

    @property
    def triangle_count(self):
        return sum(len(m) for m in self.meshes)

    def __repr__(self):
        return "ReadResult(%s, %d meshes, %d triangles)" % (self.format, len(self.meshes), self.triangle_count)


def sniff(data, filename=""):
    """Best guess at a format from the bytes and the name."""
    ext = os.path.splitext(filename)[1].lower()
    head = data[:16] if isinstance(data, (bytes, bytearray)) else b""
    if head.startswith(b"PK\x03\x04"):
        return "3mf"
    if head.startswith(b"ply"):
        return "ply"
    if ext in (".stl",):
        return "stl"
    if ext in (".obj",):
        return "obj"
    if ext == ".ply":
        return "ply"
    if ext == ".3mf":
        return "3mf"
    text = head.lstrip()
    if text.startswith(b"solid"):
        return "stl"
    if text.startswith(b"v ") or text.startswith(b"#") or text.startswith(b"o "):
        return "obj"
    if len(data) >= 84:
        return "stl"
    raise FormatError("cannot tell the format of %r" % (filename or "the data",))


def read(data, filename=""):
    fmt = sniff(data, filename)
    reader = {"stl": read_stl, "obj": read_obj, "ply": read_ply, "3mf": read_3mf}[fmt]
    return reader(data, os.path.basename(filename))


def read_file(path):
    with open(path, "rb") as handle:
        return read(handle.read(), path)


# ----------------------------------------------------------------------
# STL
# ----------------------------------------------------------------------


def _weld(triangles_xyz, name, notes, tol=0.0):
    """Turn a list of triangle point triples into an indexed mesh, welding
    identical vertices (exact match, or rounded when ``tol`` > 0)."""
    verts, index, tris = [], {}, []
    dropped = 0
    for tri in triangles_xyz:
        ids = []
        for p in tri:
            key = p if tol <= 0 else tuple(round(c / tol) for c in p)
            i = index.get(key)
            if i is None:
                i = len(verts)
                verts.append(p)
                index[key] = i
            ids.append(i)
        if len(set(ids)) < 3:
            dropped += 1
            continue
        tris.append(tuple(ids))
    if dropped:
        notes.append("%s: dropped %d degenerate triangle(s)" % (name, dropped))
    return TriMesh(verts, tris, name)


def read_stl(data, name="stl"):
    notes = []
    if len(data) >= 84 and not data.lstrip()[:5] == b"solid" or (
        len(data) >= 84 and _looks_binary_stl(data)
    ):
        count = struct.unpack_from("<I", data, 80)[0]
        expected = 84 + count * 50
        if expected != len(data):
            notes.append("binary STL length %d does not match %d facets" % (len(data), count))
            count = max(0, (len(data) - 84) // 50)
        tris = []
        for k in range(count):
            off = 84 + k * 50
            v = struct.unpack_from("<12f", data, off)
            tris.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]), (v[9], v[10], v[11])))
        base = os.path.splitext(name)[0] or "stl"
        return ReadResult([_weld(tris, base, notes)], None, notes, "stl")
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    meshes = []
    current, tri, solid = [], [], "stl"
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0].lower()
        if key == "solid":
            solid = " ".join(parts[1:]) or os.path.splitext(name)[0] or "stl"
        elif key == "vertex" and len(parts) >= 4:
            try:
                tri.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                notes.append("bad vertex line: %s" % line.strip())
        elif key == "endfacet":
            if len(tri) == 3:
                current.append(tuple(tri))
            else:
                notes.append("facet with %d vertices skipped" % len(tri))
            tri = []
        elif key == "endsolid":
            if current:
                meshes.append(_weld(current, solid, notes))
            current = []
    if current:
        meshes.append(_weld(current, solid, notes))
    if not meshes:
        raise FormatError("no facets in ASCII STL")
    return ReadResult(meshes, None, notes, "stl")


def _looks_binary_stl(data):
    if len(data) < 84:
        return False
    count = struct.unpack_from("<I", data, 80)[0]
    return 84 + count * 50 == len(data)


def write_stl(mesh, binary=True):
    """Bytes of a single-solid STL."""
    if binary:
        out = bytearray(struct.pack("<80sI", b"FreeCAD XR", len(mesh)))
        for i in range(len(mesh)):
            a, b, c = mesh.triangle(i)
            n = mesh.normal(i)
            out += struct.pack("<12fH", n[0], n[1], n[2], *a, *b, *c, 0)
        return bytes(out)
    lines = ["solid %s" % (mesh.name or "mesh")]
    for i in range(len(mesh)):
        n = mesh.normal(i)
        lines.append("  facet normal %g %g %g" % n)
        lines.append("    outer loop")
        for p in mesh.triangle(i):
            lines.append("      vertex %g %g %g" % p)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid %s" % (mesh.name or "mesh"))
    return ("\n".join(lines) + "\n").encode("utf-8")


# ----------------------------------------------------------------------
# OBJ
# ----------------------------------------------------------------------


def read_obj(data, name="obj"):
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    notes = []
    verts = []
    meshes = []
    current_name = os.path.splitext(name)[0] or "obj"
    current = []

    def flush():
        if current:
            meshes.append(_reindex(verts, current, current_name))

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        if key == "v" and len(parts) >= 4:
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                notes.append("bad vertex: %s" % line)
        elif key in ("o", "g"):
            flush()
            current = []
            current_name = " ".join(parts[1:]) or current_name
        elif key == "f":
            ids = []
            for token in parts[1:]:
                idx = token.split("/")[0]
                try:
                    i = int(idx)
                except ValueError:
                    ids = None
                    break
                ids.append(i - 1 if i > 0 else len(verts) + i)
            if not ids or len(ids) < 3:
                notes.append("bad face: %s" % line)
                continue
            for k in range(1, len(ids) - 1):  # fan triangulation
                current.append((ids[0], ids[k], ids[k + 1]))
    flush()
    if not meshes:
        raise FormatError("no faces in OBJ")
    return ReadResult(meshes, None, notes, "obj")


def _reindex(all_verts, tris, name):
    used = {}
    verts, out = [], []
    for tri in tris:
        ids = []
        for i in tri:
            if i < 0 or i >= len(all_verts):
                ids = None
                break
            j = used.get(i)
            if j is None:
                j = len(verts)
                verts.append(all_verts[i])
                used[i] = j
            ids.append(j)
        if ids and len(set(ids)) == 3:
            out.append(tuple(ids))
    return TriMesh(verts, out, name)


def write_obj(mesh):
    lines = ["# FreeCAD XR", "o %s" % (mesh.name or "mesh")]
    lines.extend("v %g %g %g" % v for v in mesh.vertices)
    lines.extend("f %d %d %d" % (a + 1, b + 1, c + 1) for a, b, c in mesh.triangles)
    return ("\n".join(lines) + "\n").encode("utf-8")


# ----------------------------------------------------------------------
# PLY
# ----------------------------------------------------------------------

_PLY_TYPES = {
    "char": "b", "int8": "b", "uchar": "B", "uint8": "B", "short": "h", "int16": "h",
    "ushort": "H", "uint16": "H", "int": "i", "int32": "i", "uint": "I", "uint32": "I",
    "float": "f", "float32": "f", "double": "d", "float64": "d",
}


def read_ply(data, name="ply"):
    notes = []
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        raise FormatError("not a PLY file")
    header = data[:end].decode("ascii", "replace").splitlines()
    body_start = data.index(b"\n", end) + 1
    fmt = None
    elements = []  # (name, count, [(prop, type) or ("list", count_type, item_type, name)])
    for line in header[1:]:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            elements.append((parts[1], int(parts[2]), []))
        elif parts[0] == "property" and elements:
            if parts[1] == "list":
                elements[-1][2].append(("list", parts[2], parts[3], parts[4]))
            else:
                elements[-1][2].append((parts[1], parts[2]))
    verts, faces = [], []
    if fmt == "ascii":
        tokens = data[body_start:].decode("ascii", "replace").split()
        pos = 0
        for ename, count, props in elements:
            for _ in range(count):
                values = {}
                for prop in props:
                    if prop[0] == "list":
                        n = int(tokens[pos]); pos += 1
                        values[prop[3]] = [float(tokens[pos + k]) for k in range(n)]
                        pos += n
                    else:
                        values[prop[1]] = float(tokens[pos]); pos += 1
                _ply_collect(ename, values, verts, faces)
    elif fmt in ("binary_little_endian", "binary_big_endian"):
        endian = "<" if fmt == "binary_little_endian" else ">"
        pos = body_start
        for ename, count, props in elements:
            for _ in range(count):
                values = {}
                for prop in props:
                    if prop[0] == "list":
                        ct, it = _PLY_TYPES[prop[1]], _PLY_TYPES[prop[2]]
                        n = struct.unpack_from(endian + ct, data, pos)[0]
                        pos += struct.calcsize(ct)
                        items = struct.unpack_from(endian + it * n, data, pos)
                        pos += struct.calcsize(it) * n
                        values[prop[3]] = list(items)
                    else:
                        t = _PLY_TYPES[prop[0]]
                        values[prop[1]] = struct.unpack_from(endian + t, data, pos)[0]
                        pos += struct.calcsize(t)
                _ply_collect(ename, values, verts, faces)
    else:
        raise FormatError("unknown PLY format %r" % fmt)
    tris = []
    for face in faces:
        ids = [int(i) for i in face]
        for k in range(1, len(ids) - 1):
            tris.append((ids[0], ids[k], ids[k + 1]))
    if not tris:
        notes.append("PLY has no faces (a point cloud); vertices kept")
    mesh = TriMesh(verts, [t for t in tris if max(t) < len(verts)], os.path.splitext(name)[0] or "ply")
    return ReadResult([mesh], None, notes, "ply")


def _ply_collect(ename, values, verts, faces):
    if ename == "vertex":
        verts.append((values.get("x", 0.0), values.get("y", 0.0), values.get("z", 0.0)))
    elif ename == "face":
        face = values.get("vertex_indices", values.get("vertex_index"))
        if face:
            faces.append(face)


# ----------------------------------------------------------------------
# 3MF
# ----------------------------------------------------------------------

_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def read_3mf(data, name="3mf"):
    notes = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FormatError("not a 3MF (zip) file")
    model_names = [n for n in archive.namelist() if n.lower().endswith(".model")]
    if not model_names:
        raise FormatError("3MF has no .model part")
    # The root model is the one in 3D/; Bambu/Prusa put per-object models under 3D/Objects/.
    model_names.sort(key=lambda n: (n.count("/"), n))
    meshes_by_id = {}
    unit_mm = 1.0
    build = []
    for model_name in model_names:
        root = ET.fromstring(archive.read(model_name))
        unit = root.get("unit", "millimeter")
        unit_mm = UNIT_TO_MM.get(unit, 1.0)
        for obj in root.iter(_NS + "object"):
            oid = obj.get("id")
            mesh = obj.find(_NS + "mesh")
            if mesh is None:
                continue
            verts = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                     for v in mesh.find(_NS + "vertices").iter(_NS + "vertex")]
            tris = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
                    for t in mesh.find(_NS + "triangles").iter(_NS + "triangle")]
            meshes_by_id[(model_name, oid)] = TriMesh(verts, tris, obj.get("name") or "object_%s" % oid)
        build_el = root.find(_NS + "build")
        if build_el is not None:
            for item in build_el.iter(_NS + "item"):
                build.append((model_name, item.get("objectid"), item.get("transform")))
    meshes = []
    for model_name, oid, transform in build:
        mesh = meshes_by_id.get((model_name, oid))
        if mesh is None:
            # a component reference into another model file
            for (mname, mid), m in meshes_by_id.items():
                if mid == oid:
                    mesh = m
                    break
        if mesh is None:
            notes.append("build item %s has no mesh (components are not expanded)" % oid)
            continue
        if transform:
            mesh = _apply_3mf_transform(mesh, transform)
        meshes.append(mesh)
    if not meshes:
        meshes = list(meshes_by_id.values())
    if not meshes:
        raise FormatError("3MF contains no meshes")
    return ReadResult(meshes, unit_mm, notes, "3mf")


def _apply_3mf_transform(mesh, text):
    m = [float(x) for x in text.split()]
    if len(m) != 12:
        return mesh
    verts = [(m[0] * x + m[3] * y + m[6] * z + m[9],
              m[1] * x + m[4] * y + m[7] * z + m[10],
              m[2] * x + m[5] * y + m[8] * z + m[11]) for x, y, z in mesh.vertices]
    return TriMesh(verts, mesh.triangles, mesh.name)


def write_3mf(meshes, unit="millimeter"):
    """A minimal, valid 3MF package with one object per mesh."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<model unit="%s" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">' % unit,
             "<resources>"]
    for k, mesh in enumerate(meshes):
        lines.append('<object id="%d" name="%s" type="model"><mesh><vertices>' % (k + 1, _xml(mesh.name or "mesh")))
        lines.extend('<vertex x="%g" y="%g" z="%g"/>' % v for v in mesh.vertices)
        lines.append("</vertices><triangles>")
        lines.extend('<triangle v1="%d" v2="%d" v3="%d"/>' % t for t in mesh.triangles)
        lines.append("</triangles></mesh></object>")
    lines.append("</resources><build>")
    lines.extend('<item objectid="%d"/>' % (k + 1) for k in range(len(meshes)))
    lines.append("</build></model>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        archive.writestr("_rels/.rels",
                         '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        archive.writestr("3D/3dmodel.model", "\n".join(lines))
    return buffer.getvalue()


def _xml(text):
    return re.sub(r"[<>&\"]", lambda m: {"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;"}[m.group(0)], text)
