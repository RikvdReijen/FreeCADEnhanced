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
"""Export a FreeCAD document to a game engine from the command line.

    freecadcmd gamebridge_export.py -- bracket.FCStd --target unreal --out ./export

Useful for the case a GUI cannot serve: a build server regenerating engine
assets whenever the CAD source changes, so that nobody has to remember to
re-export before a milestone.

Run under ``freecadcmd`` (or ``FreeCADCmd``), which provides the FreeCAD module
without starting a GUI.
"""

import argparse
import os
import sys


def _module_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def strip_launcher_arguments(argv):
    """Drop the arguments meant for freecadcmd rather than for us.

    ``freecadcmd script.py -- --target unity`` hands the whole command line to
    the script, launcher arguments included, so ours are whatever follows the
    separator.
    """
    argv = list(argv)
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return argv


def parse_arguments(argv):
    parser = argparse.ArgumentParser(
        prog="gamebridge_export",
        description="Export a FreeCAD document for Unreal Engine, Unity or Blender.",
    )
    parser.add_argument("document", help="the .FCStd file to export")
    parser.add_argument(
        "--target", default="unreal", choices=["unreal", "unity", "blender"],
        help="which engine to export for (default: unreal)",
    )
    parser.add_argument(
        "--out", default=".", help="folder to write the export into (default: .)"
    )
    parser.add_argument(
        "--format", default=None, choices=["glb", "gltf", "obj"],
        help="mesh file format (default: the target's own)",
    )
    parser.add_argument(
        "--deviation", type=float, default=0.1,
        help="tessellation deviation in millimetres (default: 0.1)",
    )
    parser.add_argument(
        "--angular-deviation", type=float, default=20.0,
        help="tessellation angular deviation in degrees (default: 20)",
    )
    parser.add_argument(
        "--relative-deviation", action="store_true",
        help="treat the deviation as a fraction of the model size instead of millimetres",
    )
    parser.add_argument(
        "--include-hidden", action="store_true",
        help="export hidden objects too, which are left out by default",
    )
    parser.add_argument(
        "--no-weld", action="store_true", help="do not merge duplicate vertices"
    )
    parser.add_argument(
        "--object", action="append", dest="objects", default=None,
        help="export only this object, by name; may be given more than once",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only report failures"
    )
    return parser.parse_args(argv)


def main(argv=None):
    argv = strip_launcher_arguments(sys.argv[1:] if argv is None else argv)
    arguments = parse_arguments(argv)

    sys.path.insert(0, _module_root())

    try:
        import FreeCAD
    except ImportError:
        sys.stderr.write(
            "This tool has to run inside FreeCAD:\n"
            "    freecadcmd %s -- document.FCStd --target unreal\n" % __file__
        )
        return 2

    from gbcore.tessellate import TessellationSettings
    from gbtargets import ExportOptions, get_target
    from gbcore.document import DocumentWalker

    path = os.path.abspath(arguments.document)
    if not os.path.exists(path):
        sys.stderr.write("no such document: %s\n" % path)
        return 2

    document = FreeCAD.openDocument(path)
    document.recompute()

    settings = TessellationSettings(
        deviation=arguments.deviation,
        angular_deviation=arguments.angular_deviation,
        relative=arguments.relative_deviation,
    )
    walker = DocumentWalker(settings, arguments.include_hidden)
    objects = None
    if arguments.objects:
        objects = []
        for name in arguments.objects:
            obj = document.getObject(name)
            if obj is None:
                sys.stderr.write("no object named %r in the document\n" % name)
                return 2
            objects.append(obj)
    scene = walker.walk_document(document, objects)

    target_class = get_target(arguments.target).__class__
    options = ExportOptions(
        mesh_format=arguments.format or target_class.default_format,
        weld=not arguments.no_weld,
        include_hidden=arguments.include_hidden,
    )
    result = get_target(arguments.target, options).export(scene, arguments.out)

    for warning in walker.warnings + result.warnings:
        sys.stderr.write("warning: %s\n" % warning)
    if not arguments.quiet:
        print(result.summary())
        print("manifest: %s" % result.manifest_path)
    FreeCAD.closeDocument(document.Name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
