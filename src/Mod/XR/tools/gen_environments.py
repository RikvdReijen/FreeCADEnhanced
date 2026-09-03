#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""Regenerate ``Resources/environments/<id>.json`` from the built-in generators.

Every built-in environment is generated, validated, optionally tessellated and
written out pretty-printed with sorted keys and rounded floats, so the output
is byte-for-byte reproducible and diffs cleanly.  ``Tests/test_environments.py``
re-runs the generators and compares, which fails the build if someone edits a
generator without regenerating.

Usage::

    python3 tools/gen_environments.py                # write everything
    python3 tools/gen_environments.py --check        # fail if out of date
    python3 tools/gen_environments.py bambu_x1c      # just one
    python3 tools/gen_environments.py --stats        # also count triangles
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_MOD_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, os.pardir))
if _MOD_DIR not in sys.path:
    sys.path.insert(0, _MOD_DIR)

from xrenv import registry, spec as spec_mod  # noqa: E402


def _human(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.2f MB" % (n / (1024.0 * 1024.0))


def _generator_ids() -> List[str]:
    """The ids of the built-in generator modules, in declared order."""
    from xrenv import environments as env_pkg
    import pkgutil

    found = [m.name for m in pkgutil.iter_modules(env_pkg.__path__)
             if not m.name.startswith("_")]
    ordered = [i for i in registry.BUILTIN_ORDER if i in found]
    ordered.extend(sorted(i for i in found if i not in registry.BUILTIN_ORDER))
    return ordered


def _tessellation_stats(spec: Dict) -> Tuple[int, int, int]:
    """``(triangles, vertices, distinct shapes)`` for a whole spec."""
    tris = verts = 0
    distinct = set()
    import json as _json

    for node, _world in spec_mod.iter_nodes(spec):
        shape = node.get("shape")
        if shape is None:
            continue
        distinct.add(_json.dumps(shape, sort_keys=True, separators=(",", ":")))
        pos, _n, _u, idx = spec_mod.tessellate_shape(shape)
        tris += len(idx) // 3
        verts += len(pos) // 3
    return tris, verts, len(distinct)


def generate(
    ids: Optional[Sequence[str]] = None,
    out_dir: Optional[str] = None,
    check: bool = False,
    stats: bool = False,
    quiet: bool = False,
) -> int:
    """Generate (or check) the environment JSON.  Returns a process exit code."""
    out_dir = out_dir or registry.resources_dir()
    wanted = list(ids) if ids else _generator_ids()
    known = _generator_ids()
    unknown = [i for i in wanted if i not in known]
    if unknown:
        print("error: no generator module for: %s" % ", ".join(unknown), file=sys.stderr)
        print("available: %s" % ", ".join(known), file=sys.stderr)
        return 2

    if not check and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    failures = 0
    stale: List[str] = []
    rows: List[Tuple[str, ...]] = []

    for module_name in wanted:
        module = importlib.import_module("xrenv.environments." + module_name)
        build = getattr(module, "build", None)
        if not callable(build):
            print("error: xrenv.environments.%s has no build()" % module_name,
                  file=sys.stderr)
            failures += 1
            continue

        t0 = time.time()
        spec = build()
        build_ms = (time.time() - t0) * 1000.0

        env_id = spec.get("id") or module_name
        problems = spec_mod.validate_spec(spec)
        if problems:
            failures += 1
            print("FAIL %s: %d validation problem(s)" % (env_id, len(problems)),
                  file=sys.stderr)
            for p in problems[:20]:
                print("       %s" % p, file=sys.stderr)
            continue

        text = spec_mod.spec_to_json(spec)
        path = os.path.join(out_dir, env_id + ".json")

        tri_s = vert_s = shape_s = ""
        if stats:
            try:
                tris, verts, distinct = _tessellation_stats(spec)
                tri_s, vert_s, shape_s = "%d" % tris, "%d" % verts, "%d" % distinct
            except spec_mod.TessellationError as exc:
                failures += 1
                print("FAIL %s: tessellation error: %s" % (env_id, exc), file=sys.stderr)
                continue

        if check:
            existing = None
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    existing = fh.read()
            if existing != text:
                stale.append(env_id)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        rows.append(
            (
                env_id,
                str(spec_mod.count_parts(spec)),
                str(len(spec.get("materials") or [])),
                str(len(spec.get("lights") or [])),
                "%.1f" % float(spec.get("user_scale", 1.0)),
                _human(len(text.encode("utf-8"))),
                tri_s,
                vert_s,
                shape_s,
                "%.0f ms" % build_ms,
            )
        )

    if not quiet and rows:
        header = ["environment", "parts", "mats", "lights", "scale", "json"]
        if stats:
            header += ["triangles", "vertices", "shapes"]
        header += ["build"]
        cols = [len(h) for h in header]
        trimmed = [r if stats else (r[:6] + r[9:]) for r in rows]
        for r in trimmed:
            for i, cell in enumerate(r):
                cols[i] = max(cols[i], len(cell))
        fmt = "  ".join("%%-%ds" % c for c in cols)
        print(fmt % tuple(header))
        print(fmt % tuple("-" * c for c in cols))
        for r in trimmed:
            print(fmt % tuple(r))

    if check and stale:
        print(
            "\n%d environment(s) out of date: %s\n"
            "run: python3 tools/gen_environments.py" % (len(stale), ", ".join(stale)),
            file=sys.stderr,
        )
        return 1
    if failures:
        return 1
    if not quiet:
        action = "checked" if check else "written to"
        print("\n%d environment(s) %s %s" % (len(rows), action, out_dir))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the built-in XR environment specs.")
    parser.add_argument("ids", nargs="*",
                        help="environment ids to generate (default: all)")
    parser.add_argument("-o", "--out-dir", default=None,
                        help="output directory (default: Resources/environments)")
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit non-zero if anything is stale")
    parser.add_argument("--stats", action="store_true",
                        help="also tessellate and report triangle counts")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)
    return generate(args.ids, args.out_dir, args.check, args.stats, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
