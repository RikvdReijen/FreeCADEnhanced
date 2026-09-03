#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Host side FCXR v1 validator.

This reads a ``.fcxr`` container with *exactly* the rules the C++ reader in
``quest/app/src/main/cpp/fcxr.cpp`` uses, so the two implementations can be
cross-checked against the same fixture files:

    python3 quest/tools/verify_fcxr.py scene.fcxr
    python3 quest/tools/verify_fcxr.py --manifest scene.fcxr | jq .

Rules (ARCHITECTURE.md §1):

* header  ``'FCXR'``, uint32 version == 1, uint32 total_length (whole file)
* chunk   uint32 payload_length (padding *not* included), char[4] type,
          payload, then padding to the next 4 byte boundary — 0x20 for the
          JSON chunk, 0x00 for binary chunks
* exactly one JSON chunk and it comes first; at most one ``BIN\\0`` chunk;
  any number of ``PNG\\0`` chunks whose order is the ``images[].chunk`` index
* accessor ``offset`` is relative to the BIN payload and is 4 byte aligned

Only the standard library is used, so this runs anywhere the workbench's own
tests run (see ARCHITECTURE.md §6).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib

MAGIC = b"FCXR"
VERSION = 1

COMPONENT_SIZE = {"F32": 4, "U32": 4, "U16": 2, "U8": 1}
TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


class FcxrError(Exception):
    """Raised for any structural problem in the container."""


def _pad4(n: int) -> int:
    return (4 - (n & 3)) & 3


class Fcxr:
    def __init__(self, manifest, binary, pngs):
        self.manifest = manifest
        self.bin = binary
        self.pngs = pngs

    # -- accessors ---------------------------------------------------------
    def accessor_bytes(self, index: int) -> bytes:
        acc = self.manifest["accessors"][index]
        stride = COMPONENT_SIZE[acc["component"]] * TYPE_COUNT[acc["type"]]
        return self.bin[acc["offset"] : acc["offset"] + stride * acc["count"]]

    def read_accessor(self, index: int):
        """Returns a list of tuples (or scalars for SCALAR accessors)."""
        acc = self.manifest["accessors"][index]
        n = TYPE_COUNT[acc["type"]]
        fmt = {"F32": "f", "U32": "I", "U16": "H", "U8": "B"}[acc["component"]]
        raw = self.accessor_bytes(index)
        values = struct.unpack("<%d%s" % (acc["count"] * n, fmt), raw)
        if n == 1:
            return list(values)
        return [tuple(values[i * n : i * n + n]) for i in range(acc["count"])]


def parse(data: bytes) -> Fcxr:
    if len(data) < 12:
        raise FcxrError("file is shorter than the 12 byte header")
    if data[:4] != MAGIC:
        raise FcxrError("bad magic %r (expected %r)" % (data[:4], MAGIC))
    version, total_length = struct.unpack_from("<II", data, 4)
    if version != VERSION:
        raise FcxrError("unsupported version %d" % version)
    if total_length < 12:
        raise FcxrError("bad total_length %d" % total_length)
    if total_length > len(data):
        raise FcxrError(
            "total_length %d exceeds the %d bytes present" % (total_length, len(data))
        )
    if total_length != len(data):
        sys.stderr.write(
            "warning: %d trailing bytes after total_length\n" % (len(data) - total_length)
        )

    manifest = None
    binary = b""
    pngs = []
    pos = 12
    first = True
    while pos + 8 <= total_length:
        (payload_length,) = struct.unpack_from("<I", data, pos)
        ctype = data[pos + 4 : pos + 8]
        start = pos + 8
        if payload_length > total_length - start:
            raise FcxrError("chunk at %d runs past the end of the file" % pos)
        payload = data[start : start + payload_length]

        if ctype == b"JSON":
            if manifest is not None:
                raise FcxrError("more than one JSON chunk")
            if not first:
                raise FcxrError("the JSON chunk must be the first chunk")
            manifest = json.loads(payload.decode("utf-8"))
        elif ctype == b"BIN\0":
            if binary:
                raise FcxrError("more than one BIN chunk")
            binary = payload
        elif ctype == b"PNG\0":
            pngs.append(payload)
        else:
            sys.stderr.write("warning: skipping unknown chunk type %r\n" % ctype)

        pad = _pad4(payload_length)
        expect = b" " * pad if ctype == b"JSON" else b"\0" * pad
        actual = data[start + payload_length : start + payload_length + pad]
        if pad and actual != expect:
            sys.stderr.write(
                "warning: chunk %r padding is %r, expected %r\n" % (ctype, actual, expect)
            )
        pos = start + payload_length + pad
        first = False

    if manifest is None:
        raise FcxrError("no JSON chunk")
    return Fcxr(manifest, binary, pngs)


def _png_size(payload: bytes):
    if len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise FcxrError("PNG chunk does not start with the PNG signature")
    if payload[12:16] != b"IHDR":
        raise FcxrError("PNG chunk has no leading IHDR")
    w, h, depth, ctype, comp, filt, interlace = struct.unpack_from(">IIBBBBB", payload, 16)
    stored_crc = struct.unpack_from(">I", payload, 29)[0]
    if zlib.crc32(payload[12:29]) & 0xFFFFFFFF != stored_crc:
        raise FcxrError("PNG IHDR CRC mismatch")
    if interlace:
        raise FcxrError("interlaced PNG (the Quest decoder rejects these)")
    return w, h, depth, ctype


def validate(f: Fcxr, verbose: bool = True) -> list:
    """Returns a list of problem strings; empty means the file is valid."""
    problems = []
    m = f.manifest

    def need(cond, msg):
        if not cond:
            problems.append(msg)
        return cond

    need(isinstance(m.get("asset"), dict), "manifest has no asset object")
    need(isinstance(m.get("scene"), dict), "manifest has no scene object")
    asset = m.get("asset", {})
    need(asset.get("version") == 1, "asset.version is not 1")
    need(isinstance(asset.get("unit_scale", 0.001), (int, float)), "asset.unit_scale is not a number")

    nodes = m.get("nodes", [])
    meshes = m.get("meshes", [])
    accessors = m.get("accessors", [])
    materials = m.get("materials", [])
    images = m.get("images", [])

    # -- accessors ---------------------------------------------------------
    for i, acc in enumerate(accessors):
        tag = "accessor %d" % i
        if acc.get("type") not in TYPE_COUNT:
            problems.append("%s: unknown type %r" % (tag, acc.get("type")))
            continue
        if acc.get("component") not in COMPONENT_SIZE:
            problems.append("%s: unknown component %r" % (tag, acc.get("component")))
            continue
        offset, length, count = acc["offset"], acc["length"], acc["count"]
        stride = COMPONENT_SIZE[acc["component"]] * TYPE_COUNT[acc["type"]]
        if offset % 4:
            problems.append("%s: offset %d is not 4 byte aligned" % (tag, offset))
        if stride * count > length:
            problems.append(
                "%s: length %d is smaller than count*stride %d" % (tag, length, stride * count)
            )
        if offset + length > len(f.bin):
            problems.append(
                "%s: range [%d,%d) is outside the %d byte BIN chunk"
                % (tag, offset, offset + length, len(f.bin))
            )

    def check_ref(value, container, tag, what):
        if value is None:
            return
        if not isinstance(value, int) or not (0 <= value < len(container)):
            problems.append("%s: %s index %r out of range" % (tag, what, value))

    # -- nodes -------------------------------------------------------------
    root = m.get("scene", {}).get("root", 0)
    if nodes:
        check_ref(root, nodes, "scene", "root")
    for i, n in enumerate(nodes):
        tag = "node %d (%s)" % (i, n.get("name", ""))
        check_ref(n.get("mesh"), meshes, tag, "mesh")
        for c in n.get("children", []):
            check_ref(c, nodes, tag, "child")
        for key, count in (("translation", 3), ("rotation", 4), ("scale", 3)):
            v = n.get(key)
            if v is not None and (not isinstance(v, list) or len(v) != count):
                problems.append("%s: %s must be %d numbers" % (tag, key, count))

    # cycle / reachability check from the root
    if nodes:
        seen = set()
        stack = [root] if isinstance(root, int) and 0 <= root < len(nodes) else []
        while stack:
            idx = stack.pop()
            if idx in seen:
                problems.append("node graph contains a cycle at node %d" % idx)
                continue
            seen.add(idx)
            for c in nodes[idx].get("children", []):
                if isinstance(c, int) and 0 <= c < len(nodes):
                    stack.append(c)
        if len(seen) != len(nodes) and verbose:
            sys.stderr.write(
                "note: %d of %d nodes are unreachable from scene.root\n" % (len(nodes) - len(seen), len(nodes))
            )

    # -- meshes ------------------------------------------------------------
    expected = {
        "positions": ("VEC3", ("F32",)),
        "normals": ("VEC3", ("F32",)),
        "uvs": ("VEC2", ("F32",)),
        "indices": ("SCALAR", ("U8", "U16", "U32")),
    }
    for i, mesh in enumerate(meshes):
        for j, prim in enumerate(mesh.get("primitives", [])):
            tag = "mesh %d primitive %d" % (i, j)
            check_ref(prim.get("material"), materials, tag, "material")
            if prim.get("positions") is None:
                problems.append("%s: has no positions accessor" % tag)
            for key, (want_type, want_comps) in expected.items():
                idx = prim.get(key)
                check_ref(idx, accessors, tag, key)
                if not isinstance(idx, int) or not (0 <= idx < len(accessors)):
                    continue
                acc = accessors[idx]
                if acc.get("type") != want_type:
                    problems.append(
                        "%s: %s accessor is %s, expected %s" % (tag, key, acc.get("type"), want_type)
                    )
                if acc.get("component") not in want_comps:
                    problems.append(
                        "%s: %s accessor component %s not in %s"
                        % (tag, key, acc.get("component"), want_comps)
                    )
            pos_idx, idx_idx = prim.get("positions"), prim.get("indices")
            if isinstance(idx_idx, int) and 0 <= idx_idx < len(accessors):
                icount = accessors[idx_idx]["count"]
                if icount % 3:
                    problems.append("%s: index count %d is not a multiple of 3" % (tag, icount))
                if isinstance(pos_idx, int) and 0 <= pos_idx < len(accessors):
                    vcount = accessors[pos_idx]["count"]
                    try:
                        worst = max(f.read_accessor(idx_idx)) if icount else -1
                    except Exception as exc:  # pragma: no cover - defensive
                        problems.append("%s: index accessor unreadable (%s)" % (tag, exc))
                        worst = -1
                    if worst >= vcount:
                        problems.append(
                            "%s: index %d addresses vertex %d of %d" % (tag, worst, worst, vcount)
                        )
            for key in ("normals", "uvs"):
                other = prim.get(key)
                if (
                    isinstance(other, int)
                    and isinstance(pos_idx, int)
                    and 0 <= other < len(accessors)
                    and 0 <= pos_idx < len(accessors)
                    and accessors[other]["count"] != accessors[pos_idx]["count"]
                ):
                    problems.append(
                        "%s: %s count %d != positions count %d"
                        % (tag, key, accessors[other]["count"], accessors[pos_idx]["count"])
                    )

    # -- materials / images ------------------------------------------------
    for i, mat in enumerate(materials):
        check_ref(mat.get("base_color_texture"), images, "material %d" % i, "base_color_texture")
        bc = mat.get("base_color")
        if bc is not None and (not isinstance(bc, list) or len(bc) != 4):
            problems.append("material %d: base_color must be 4 numbers" % i)

    for i, img in enumerate(images):
        chunk = img.get("chunk")
        if not isinstance(chunk, int) or not (0 <= chunk < len(f.pngs)):
            problems.append("image %d: chunk index %r out of range" % (i, chunk))
            continue
        try:
            w, h, depth, ctype = _png_size(f.pngs[chunk])
            if verbose:
                print("  image %d %-16s %dx%d depth=%d colour_type=%d" % (i, img.get("name", ""), w, h, depth, ctype))
        except FcxrError as exc:
            problems.append("image %d: %s" % (i, exc))

    # -- paint / vector ----------------------------------------------------
    paint = m.get("paint")
    if isinstance(paint, dict):
        for t, target in enumerate(paint.get("targets", [])):
            for l, layer in enumerate(target.get("layers", [])):
                tag = "paint target %d layer %d" % (t, l)
                check_ref(layer.get("image"), images, tag, "image")
                if layer.get("blend") not in (None, "normal", "multiply", "add", "erase"):
                    problems.append("%s: unknown blend mode %r" % (tag, layer.get("blend")))
                res = layer.get("resolution")
                if res is not None and (not isinstance(res, list) or len(res) != 2):
                    problems.append("%s: resolution must be [w,h]" % tag)
        for s, stroke in enumerate(paint.get("strokes3d", [])):
            for p, point in enumerate(stroke.get("points", [])):
                if len(point.get("p", [])) != 3 or len(point.get("n", [])) != 3:
                    problems.append("paint stroke %d point %d: p and n must be 3 numbers" % (s, p))
                    break

    vector = m.get("vector")
    if isinstance(vector, dict):
        for p, path in enumerate(vector.get("paths", [])):
            for n, node in enumerate(path.get("nodes", [])):
                tag = "vector path %d node %d" % (p, n)
                if len(node.get("point", [])) != 2:
                    problems.append("%s: point must be 2 numbers" % tag)
                for handle in ("in", "out"):
                    h = node.get(handle)
                    if h is not None and len(h) != 2:
                        problems.append("%s: %s handle must be 2 numbers or null" % (tag, handle))
                if node.get("type") not in (None, "corner", "smooth", "symmetric"):
                    problems.append("%s: unknown node type %r" % (tag, node.get("type")))
            if path.get("target") not in (None, "draft", "sketch", "annotation"):
                problems.append("vector path %d: unknown target %r" % (p, path.get("target")))
    return problems


def summarise(f: Fcxr) -> None:
    m = f.manifest
    asset = m.get("asset", {})
    scene = m.get("scene", {})
    print("generator        %s" % asset.get("generator"))
    print("source document  %s" % asset.get("source_document"))
    print("created          %s" % asset.get("created"))
    print("unit_scale       %s" % asset.get("unit_scale"))
    print("environment      %s (user_scale %s)" % (scene.get("environment"), scene.get("user_scale")))
    print("nodes            %d" % len(m.get("nodes", [])))
    print("meshes           %d" % len(m.get("meshes", [])))
    print("accessors        %d" % len(m.get("accessors", [])))
    print("materials        %d" % len(m.get("materials", [])))
    print("images           %d (%d PNG chunks)" % (len(m.get("images", [])), len(f.pngs)))
    print("BIN chunk        %d bytes" % len(f.bin))

    tris = 0
    verts = 0
    accessors = m.get("accessors", [])
    for mesh in m.get("meshes", []):
        for prim in mesh.get("primitives", []):
            idx = prim.get("indices")
            pos = prim.get("positions")
            if isinstance(idx, int) and 0 <= idx < len(accessors):
                tris += accessors[idx]["count"] // 3
            elif isinstance(pos, int) and 0 <= pos < len(accessors):
                tris += accessors[pos]["count"] // 3
            if isinstance(pos, int) and 0 <= pos < len(accessors):
                verts += accessors[pos]["count"]
    print("geometry         %d triangles, %d vertices" % (tris, verts))
    if isinstance(m.get("paint"), dict):
        p = m["paint"]
        layers = sum(len(t.get("layers", [])) for t in p.get("targets", []))
        print("paint            %d targets, %d layers, %d 3D strokes"
              % (len(p.get("targets", [])), layers, len(p.get("strokes3d", []))))
    if isinstance(m.get("vector"), dict):
        print("vector           %d paths" % len(m["vector"].get("paths", [])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate an FCXR v1 container.")
    ap.add_argument("file", help="path to a .fcxr file")
    ap.add_argument("--manifest", action="store_true", help="print the manifest as JSON and exit")
    ap.add_argument("--quiet", action="store_true", help="only report problems")
    args = ap.parse_args(argv)

    with open(args.file, "rb") as fh:
        data = fh.read()
    try:
        f = parse(data)
    except (FcxrError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    if args.manifest:
        json.dump(f.manifest, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
        return 0

    if not args.quiet:
        print("%s: %d bytes" % (args.file, len(data)))
        summarise(f)
    problems = validate(f, verbose=not args.quiet)
    if problems:
        sys.stderr.write("\n%d problem(s):\n" % len(problems))
        for p in problems:
            sys.stderr.write("  - %s\n" % p)
        return 1
    if not args.quiet:
        print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
