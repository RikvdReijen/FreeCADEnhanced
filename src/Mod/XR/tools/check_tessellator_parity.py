#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Check that the C++ and Python environment tessellators still agree.

An environment is authored once, as a declarative spec, and tessellated twice:
in Python for the desktop Coin3D viewer, and in C++ for the headset.  If the
two drift apart a machine looks different depending on where you stand in it,
and the ways they drift — a flipped winding, an inverted normal, a primitive
pointing down the wrong axis — are precisely the ones that are invisible when
reading either implementation on its own.

    python3 src/Mod/XR/tools/check_tessellator_parity.py [--keep] [env_id ...]

Needs a host C++ compiler; nothing else.  Exits non-zero on a mismatch and
prints the offending shapes.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CPP_DIR = os.path.join(MODULE_ROOT, "quest", "app", "src", "main", "cpp")
DRIVER_SRC = os.path.join(MODULE_ROOT, "quest", "tools", "tess_parity_driver.cpp")
SPEC_DIR = os.path.join(MODULE_ROOT, "Resources", "environments")

TRANSLATION_UNITS = ("tessellate", "math3d", "json", "mesh_data", "text_font")

# A sum landing on -1e-17 prints as "-0.000" on one side and "0.000" on the
# other with no geometric difference; clamp anything that rounds to zero.
EPSILON = 5e-4


class ParityError(RuntimeError):
    pass


def find_compiler():
    for name in ("g++", "clang++", "c++"):
        path = shutil.which(name)
        if path:
            return path
    return None


def build_driver(build_dir, compiler=None):
    """Compile the host driver, returning the path to the binary."""
    compiler = compiler or find_compiler()
    if compiler is None:
        raise ParityError("no host C++ compiler found (tried g++, clang++, c++)")
    binary = os.path.join(build_dir, "tess_parity_driver")
    command = [
        compiler,
        "-std=c++17",
        "-O1",
        f"-I{CPP_DIR}",
        "-o",
        binary,
        DRIVER_SRC,
    ] + [os.path.join(CPP_DIR, f"{unit}.cpp") for unit in TRANSLATION_UNITS]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ParityError(f"compiling the driver failed:\n{result.stderr}")
    return binary


def cpp_digest(binary, spec_path):
    result = subprocess.run([binary, spec_path], capture_output=True, text=True)
    if result.returncode != 0:
        raise ParityError(f"driver failed on {spec_path}:\n{result.stderr}")
    return result.stdout.splitlines()


def python_digest(spec_path):
    if MODULE_ROOT not in sys.path:
        sys.path.insert(0, MODULE_ROOT)
    from xrenv import spec as spec_module

    with open(spec_path, encoding="utf-8") as handle:
        data = json.load(handle)

    lines = []

    def visit(node):
        shape = node.get("shape")
        if shape:
            positions, normals, _uvs, indices = spec_module.tessellate_shape(shape)
            sums = [
                sum(positions[0::3]),
                sum(positions[1::3]),
                sum(positions[2::3]),
                sum(normals[0::3]),
                sum(normals[1::3]),
                sum(normals[2::3]),
            ]
            sums = [0.0 if -EPSILON < value < EPSILON else value for value in sums]
            lines.append(
                "%d %d %d %d %.3f %.3f %.3f %.3f %.3f %.3f"
                % (len(lines), len(positions) // 3, len(indices) // 3, sum(indices), *sums)
            )
        for child in node.get("children", []):
            visit(child)

    for node in data["nodes"]:
        visit(node)
    return lines


def compare(binary, spec_path):
    """Return a list of human-readable mismatches (empty when they agree)."""
    from_cpp = cpp_digest(binary, spec_path)
    from_python = python_digest(spec_path)

    problems = []
    if len(from_cpp) != len(from_python):
        problems.append(
            f"shape count differs: C++ tessellated {len(from_cpp)}, "
            f"Python {len(from_python)}"
        )
    for index, (left, right) in enumerate(zip(from_cpp, from_python)):
        if left != right:
            problems.append(f"  shape {index}:\n    C++    {left}\n    Python {right}")
    return problems, len(from_python)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("environments", nargs="*", help="environment ids (default: all)")
    parser.add_argument("--keep", action="store_true", help="keep the build directory")
    args = parser.parse_args(argv)

    if args.environments:
        specs = [os.path.join(SPEC_DIR, f"{name}.json") for name in args.environments]
    else:
        specs = sorted(
            os.path.join(SPEC_DIR, name)
            for name in os.listdir(SPEC_DIR)
            if name.endswith(".json")
        )
    if not specs:
        print("no environment specs found — run tools/gen_environments.py first")
        return 1

    build_dir = tempfile.mkdtemp(prefix="xr-tess-parity-")
    failures = 0
    try:
        binary = build_driver(build_dir)
        for spec_path in specs:
            name = os.path.splitext(os.path.basename(spec_path))[0]
            problems, count = compare(binary, spec_path)
            if problems:
                failures += 1
                print(f"{name}: MISMATCH over {count} shapes")
                for problem in problems[:10]:
                    print(problem)
                if len(problems) > 10:
                    print(f"  … and {len(problems) - 10} more")
            else:
                print(f"{name}: identical across {count} shapes")
    except ParityError as exc:
        print(f"could not run the check: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"build directory kept at {build_dir}")
        else:
            shutil.rmtree(build_dir, ignore_errors=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
