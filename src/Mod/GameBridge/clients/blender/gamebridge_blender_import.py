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
"""GameBridge importer for Blender: add-on and headless script in one file.

Three ways to use it:

* drop it into Blender's add-ons folder and enable *Import-Export: FreeCAD
  GameBridge*, which adds ``File > Import > FreeCAD GameBridge (.gbscene)``;
* run it against an export directly::

      blender --background --python gamebridge_blender_import.py -- scene.gbscene

* import :func:`plan_import` from anything, including a plain interpreter,
  because the planning half deliberately does not touch ``bpy``.

That last point is the reason for the shape of this file.  Everything that
decides *what* to import - reading the manifest, checking its version, resolving
paths, working out which objects to rename and what to tag them with - is pure
Python and is unit tested in FreeCAD's own test suite.  Only :func:`apply_plan`
talks to Blender, and it is a short function that does what the plan says.
"""

import json
import os
import sys

bl_info = {
    "name": "FreeCAD GameBridge",
    "author": "FreeCAD Project Association",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > FreeCAD GameBridge (.gbscene)",
    "description": "Import scenes exported from FreeCAD by the GameBridge workbench",
    "category": "Import-Export",
}

MANIFEST_FORMAT = "freecad-gamebridge-scene"
SUPPORTED_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# The planning half: no bpy, no Blender, unit testable.
# ---------------------------------------------------------------------------


class ImportError_(Exception):
    """Raised when a manifest cannot be imported, with a reason worth showing."""


def read_manifest(path):
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ImportError_("%s is not a GameBridge scene manifest" % path)
    version = manifest.get("version", 0)
    if version > SUPPORTED_FORMAT_VERSION:
        raise ImportError_(
            "%s needs a newer GameBridge add-on (manifest format %s, this add-on "
            "reads %s)" % (path, version, SUPPORTED_FORMAT_VERSION)
        )
    return manifest


def plan_import(manifest, manifest_path, collection_name=None):
    """Work out what to import and what to do with it afterwards.

    Returns a dictionary with the files to import, the collection to put them
    in, the renames to apply and the custom properties to set.  Keeping this
    separate from the Blender calls means the awkward parts - a manifest written
    on Windows, an export whose axis conversion Blender must not repeat, a node
    tree several levels deep - can be tested without launching Blender.
    """
    directory = os.path.dirname(os.path.abspath(manifest_path))
    target = manifest.get("target", {})
    scene_name = manifest.get("scene") or manifest.get("document") or "FreeCAD"

    files = []
    for asset in manifest.get("assets", ()):
        path = asset.get("path")
        if not path:
            continue
        # Manifest paths are always written with forward slashes.
        resolved = os.path.normpath(os.path.join(directory, *path.split("/")))
        files.append(
            {
                "path": resolved,
                "format": os.path.splitext(resolved)[1].lower().lstrip("."),
                "name": asset.get("name"),
                "kind": asset.get("kind", "mesh"),
                "exists": os.path.exists(resolved),
            }
        )

    annotations = []

    def visit(node, parent):
        entry = {
            "name": node.get("name") or node.get("label") or "Object",
            "label": node.get("label"),
            "parent": parent,
            "visible": node.get("visible", True),
            "properties": {
                "freecad_document": manifest.get("document") or "",
                "freecad_object": node.get("source") or "",
                "gamebridge_asset": node.get("asset", -1),
            },
        }
        annotations.append(entry)
        for child in node.get("children", ()):
            visit(child, entry["name"])

    for root in manifest.get("nodes", ()):
        visit(root, None)

    return {
        "collection": collection_name or scene_name,
        "files": files,
        "annotations": annotations,
        "document": manifest.get("document"),
        "scene": scene_name,
        # The exporter already converted millimetres to metres and left the axes
        # alone, so Blender's importer must not apply its own Y-up conversion on
        # top; doing so is what lays an imported CAD model on its side.
        "pre_converted": target.get("name") == "blender",
        "target": target.get("name"),
        "checksum": manifest.get("checksum"),
        "stats": manifest.get("stats", {}),
    }


def describe_plan(plan):
    """A one-line summary, for the status bar or a headless run's log."""
    stats = plan.get("stats", {})
    return "GameBridge: %s - %d file(s), %d object(s), %d triangle(s)" % (
        plan["collection"],
        len(plan["files"]),
        len(plan["annotations"]),
        stats.get("triangles", 0),
    )


# ---------------------------------------------------------------------------
# The Blender half.
# ---------------------------------------------------------------------------


def check_plan(plan):
    """Refuse a plan that cannot work, before Blender has touched anything.

    Half an import is worse than none: the artist has to work out which objects
    are new before they can undo it.  So the files are all checked up front.
    """
    missing = [f["path"] for f in plan["files"] if not f["exists"]]
    if missing:
        raise ImportError_(
            "the export is incomplete, these files are missing:\n  %s"
            % "\n  ".join(missing)
        )
    unknown = sorted({f["format"] for f in plan["files"] if f["format"] not in ("glb", "gltf", "obj")})
    if unknown:
        raise ImportError_("cannot import %s file(s)" % ", ".join(unknown))
    return plan


def apply_plan(plan, replace=True):
    """Execute a plan inside Blender.  Returns the collection it filled."""
    check_plan(plan)

    import bpy

    collection = bpy.data.collections.get(plan["collection"])
    if collection and replace:
        # A re-import replaces the previous one rather than piling a second copy
        # on top of it, which is what makes iterating on a CAD model bearable.
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    elif collection is None:
        collection = bpy.data.collections.new(plan["collection"])
        bpy.context.scene.collection.children.link(collection)

    before = set(bpy.data.objects)
    for entry in plan["files"]:
        if entry["format"] in ("glb", "gltf"):
            bpy.ops.import_scene.gltf(filepath=entry["path"])
        elif entry["format"] == "obj":
            if hasattr(bpy.ops.wm, "obj_import"):
                bpy.ops.wm.obj_import(filepath=entry["path"])
            else:  # Blender 3.x and earlier
                bpy.ops.import_scene.obj(filepath=entry["path"])
        else:  # check_plan has already ruled this out
            raise ImportError_("cannot import %s" % entry["path"])

    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        for existing in list(obj.users_collection):
            existing.objects.unlink(obj)
        collection.objects.link(obj)

    _annotate(imported, plan)
    return collection


def _annotate(objects, plan):
    """Copy the FreeCAD provenance onto the imported objects.

    Blender's glTF importer already carries node extras across as custom
    properties, but only for nodes it kept; matching on the manifest as well
    means an object that the importer merged or renamed still ends up tagged.
    """
    by_name = {}
    for obj in objects:
        by_name.setdefault(obj.name, obj)
        # Blender appends .001 to a name it had to make unique.
        by_name.setdefault(obj.name.rsplit(".", 1)[0], obj)

    for entry in plan["annotations"]:
        obj = by_name.get(entry["name"]) or by_name.get(entry["label"])
        if obj is None:
            continue
        for key, value in entry["properties"].items():
            if value not in ("", -1):
                obj[key] = value
        if not entry["visible"]:
            obj.hide_viewport = True
            obj.hide_render = True


def import_scene(path, collection_name=None, replace=True):
    """Import one ``.gbscene`` manifest.  The entry point everything else uses."""
    manifest = read_manifest(path)
    plan = plan_import(manifest, path, collection_name)
    collection = apply_plan(plan, replace)
    print(describe_plan(plan))
    return collection


# ---------------------------------------------------------------------------
# Add-on registration.  Skipped when Blender is not present.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - exercised only inside Blender
    import bpy
    from bpy.props import BoolProperty, StringProperty
    from bpy_extras.io_utils import ImportHelper

    class GAMEBRIDGE_OT_import(bpy.types.Operator, ImportHelper):
        """Import a scene exported from FreeCAD by GameBridge"""

        bl_idname = "import_scene.gamebridge"
        bl_label = "Import FreeCAD GameBridge"
        bl_options = {"REGISTER", "UNDO"}

        filename_ext = ".gbscene"
        filter_glob: StringProperty(default="*.gbscene", options={"HIDDEN"})
        replace_existing: BoolProperty(
            name="Replace existing",
            description="Clear the collection from a previous import instead of "
            "adding a second copy",
            default=True,
        )

        def execute(self, context):
            try:
                collection = import_scene(self.filepath, replace=self.replace_existing)
            except ImportError_ as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            self.report({"INFO"}, "Imported into collection '%s'" % collection.name)
            return {"FINISHED"}

    def menu_func_import(self, context):
        self.layout.operator(
            GAMEBRIDGE_OT_import.bl_idname, text="FreeCAD GameBridge (.gbscene)"
        )

    def register():
        bpy.utils.register_class(GAMEBRIDGE_OT_import)
        bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        bpy.utils.unregister_class(GAMEBRIDGE_OT_import)

except ImportError:  # not running inside Blender

    def register():
        raise RuntimeError("this add-on has to be registered from inside Blender")

    def unregister():
        register()


def _main(argv):
    """``blender --background --python thisfile.py -- scene.gbscene``"""
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = [a for a in argv[1:] if a.endswith(".gbscene")]
    if not argv:
        print("usage: blender --background --python %s -- scene.gbscene" % __file__)
        return 2
    for path in argv:
        import_scene(path)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
