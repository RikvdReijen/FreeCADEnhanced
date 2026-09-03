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
"""GameBridge importer for Unreal Engine, run from the editor's Python console.

    py "D:/exports/bracket/gamebridge_unreal_import.py"

or, with a manifest somewhere else::

    py "gamebridge_unreal_import.py" "D:/exports/bracket/scene.gbscene" /Game/CAD

It imports every mesh the manifest lists as a static mesh asset, then places one
actor per node with the hierarchy and transforms the manifest describes.

**The one thing to know about this import.**  The exporter has already converted
the model into Unreal's space - centimetres, Z up, left handed, with FreeCAD's
X staying X the way Datasmith and the FBX pipeline do it.  Unreal's glTF
importer will, by default, convert *again*, because a glTF file is normally in
metres and Y up.  Converting twice leaves the model lying on its side at a
hundredth of its size, which is the single most common way a CAD-to-Unreal
bridge goes wrong.

So the import does two things about it.  It asks the glTF pipeline not to
convert the scene, and afterwards it measures each imported asset against the
bounds the manifest recorded and says so, loudly, if they disagree - along with
the setting to change.  A check that runs is worth more than a comment claiming
the option name is right on every engine version.
"""

import json
import os
import sys

MANIFEST_FORMAT = "freecad-gamebridge-scene"
SUPPORTED_FORMAT_VERSION = 1

#: Assets are allowed to differ from the manifest by this fraction before the
#: importer complains.  Tessellation is identical on both sides, so the only
#: honest sources of difference are float rounding and Unreal's own welding.
BOUNDS_TOLERANCE = 0.02


class BridgeImportError(Exception):
    """Raised when an import cannot proceed, with a reason worth showing."""


# ---------------------------------------------------------------------------
# Planning: pure Python, no ``unreal`` module, unit tested in FreeCAD's suite.
# ---------------------------------------------------------------------------


def read_manifest(path):
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise BridgeImportError("%s is not a GameBridge scene manifest" % path)
    if manifest.get("version", 0) > SUPPORTED_FORMAT_VERSION:
        raise BridgeImportError(
            "%s needs a newer importer (manifest format %s, this script reads %s)"
            % (path, manifest.get("version"), SUPPORTED_FORMAT_VERSION)
        )
    target = manifest.get("target", {}).get("name")
    if target != "unreal":
        raise BridgeImportError(
            "this manifest was exported for %s, not for Unreal; re-export with "
            "the Unreal target" % (target or "an unknown target")
        )
    return manifest


def sanitize_package_name(name):
    """Unreal package names take word characters only, and not a leading digit."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(name))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_") or "Asset"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def plan_import(manifest, manifest_path, content_root="/Game/FreeCAD"):
    """Turn a manifest into a list of asset imports and actor placements."""
    directory = os.path.dirname(os.path.abspath(manifest_path))
    document = sanitize_package_name(manifest.get("document") or manifest.get("scene") or "Scene")
    content_path = "%s/%s" % (content_root.rstrip("/"), document)
    mesh_path = "%s/Meshes" % content_path

    imports = []
    package_by_asset = {}
    for asset in manifest.get("assets", ()):
        path = asset.get("path")
        if not path or asset.get("kind") not in ("mesh", "scene"):
            continue
        resolved = os.path.normpath(os.path.join(directory, *path.split("/")))
        asset_name = sanitize_package_name(asset.get("name") or os.path.splitext(os.path.basename(resolved))[0])
        package = "%s/%s" % (mesh_path, asset_name)
        package_by_asset[asset.get("id")] = package
        imports.append(
            {
                "file": resolved,
                "exists": os.path.exists(resolved),
                "asset_name": asset_name,
                "destination": mesh_path,
                "package": package,
                "bounds": asset.get("bounds"),
                "triangles": asset.get("triangles", 0),
                "checksum": asset.get("checksum"),
            }
        )

    actors = []

    def visit(node, parent):
        trs = node.get("trs") or {}
        actor = {
            "name": sanitize_package_name(node.get("name") or node.get("label") or "Actor"),
            "label": node.get("label") or node.get("name"),
            "parent": parent,
            "location": list(trs.get("translation", (0.0, 0.0, 0.0))),
            "rotation": list(trs.get("rotation", (0.0, 0.0, 0.0, 1.0))),
            "scale": list(trs.get("scale", (1.0, 1.0, 1.0))),
            "visible": node.get("visible", True),
            "source": node.get("source"),
            "asset": package_by_asset.get(node.get("asset")),
        }
        actors.append(actor)
        for child in node.get("children", ()):
            visit(child, actor["name"])

    for root in manifest.get("nodes", ()):
        visit(root, None)

    return {
        "contentPath": content_path,
        "meshPath": mesh_path,
        "document": manifest.get("document"),
        "scene": manifest.get("scene"),
        "imports": imports,
        "actors": actors,
        "materials": manifest.get("materials", []),
        "stats": manifest.get("stats", {}),
        "checksum": manifest.get("checksum"),
    }


def bounds_disagree(expected, actual, tolerance=BOUNDS_TOLERANCE):
    """Compare a manifest's bounds with an imported asset's.

    Returns ``None`` when they agree, or a message naming the likely cause.
    The two failures worth telling apart are a uniform scale factor, which means
    the units were converted twice, and a permutation of the extents, which
    means the axes were.
    """
    if not expected or not actual:
        return None
    expected_size = [
        float(expected["max"][axis]) - float(expected["min"][axis]) for axis in range(3)
    ]
    actual_size = [float(actual[1][axis]) - float(actual[0][axis]) for axis in range(3)]
    if max(expected_size) <= 1e-6:
        return None

    def close(a, b):
        return abs(a - b) <= tolerance * max(1e-6, abs(a), abs(b))

    if all(close(e, a) for e, a in zip(expected_size, actual_size)):
        return None

    ratios = [
        a / e for e, a in zip(expected_size, actual_size) if abs(e) > 1e-6
    ]
    if ratios and all(close(r, ratios[0]) for r in ratios):
        return (
            "every axis is %.4g times the exported size, so the units were "
            "converted twice; turn off 'Convert Scene Unit' in the glTF import "
            "options" % ratios[0]
        )
    if sorted(round(v, 4) for v in expected_size) == sorted(round(v, 4) for v in actual_size):
        return (
            "the extents are the same but on different axes, so the axes were "
            "converted twice; turn off 'Convert Scene' in the glTF import options"
        )
    return "the imported asset is %s, the export says it should be %s" % (
        tuple(round(v, 3) for v in actual_size),
        tuple(round(v, 3) for v in expected_size),
    )


def describe_plan(plan):
    stats = plan.get("stats", {})
    return "GameBridge: %d asset(s) into %s, %d actor(s), %d triangle(s)" % (
        len(plan["imports"]),
        plan["contentPath"],
        len(plan["actors"]),
        stats.get("triangles", 0),
    )


# ---------------------------------------------------------------------------
# Execution: everything below needs the editor's ``unreal`` module.
# ---------------------------------------------------------------------------


def _import_options():
    """glTF import options with Unreal's own scene conversion switched off.

    The option names have moved between engine versions and between the legacy
    importer and Interchange, so each is set defensively; whatever is left over
    is caught by the bounds check after the import.
    """
    import unreal

    options = None
    for factory_name in ("GLTFImportOptions", "InterchangeGenericAssetsPipeline"):
        factory = getattr(unreal, factory_name, None)
        if factory is not None:
            options = factory()
            break
    if options is None:
        return None
    for attribute, value in (
        ("convert_scene", False),
        ("convert_scene_unit", False),
        ("force_front_x_axis", False),
        ("import_uniform_scale", 1.0),
    ):
        try:
            options.set_editor_property(attribute, value)
        except Exception:
            # Not every version exposes every option; the bounds check reports
            # anything that slipped through.
            pass
    return options


def _import_asset(entry):
    import unreal

    task = unreal.AssetImportTask()
    task.filename = entry["file"]
    task.destination_path = entry["destination"]
    task.destination_name = entry["asset_name"]
    task.automated = True
    task.replace_existing = True
    task.save = True
    options = _import_options()
    if options is not None:
        task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return list(task.get_editor_property("imported_object_paths") or [])


def _asset_bounds(package):
    """The imported asset's local bounds, as ``(min, max)`` in centimetres."""
    import unreal

    asset = unreal.EditorAssetLibrary.load_asset(package)
    if asset is None:
        return None
    try:
        box = asset.get_bounding_box()
    except AttributeError:
        return None
    return (
        (box.min.x, box.min.y, box.min.z),
        (box.max.x, box.max.y, box.max.z),
    )


def check_plan(plan):
    """Refuse a plan that cannot work, before the editor has imported anything.

    An import that fails halfway leaves assets in the content browser that the
    artist then has to identify and delete, so the files are checked up front.
    """
    missing = [entry["file"] for entry in plan["imports"] if not entry["exists"]]
    if missing:
        raise BridgeImportError(
            "the export is incomplete, these files are missing:\n  %s"
            % "\n  ".join(missing)
        )
    return plan


def apply_plan(plan, place_actors=True):
    """Run a plan inside the editor.  Returns a report dictionary."""
    check_plan(plan)

    import unreal

    report = {"imported": [], "warnings": [], "actors": []}
    for entry in plan["imports"]:
        unreal.log("GameBridge: importing %s" % entry["asset_name"])
        _import_asset(entry)
        report["imported"].append(entry["package"])
        problem = bounds_disagree(entry.get("bounds"), _asset_bounds(entry["package"]))
        if problem:
            message = "%s: %s" % (entry["asset_name"], problem)
            report["warnings"].append(message)
            unreal.log_warning("GameBridge: " + message)

    if place_actors:
        report["actors"] = _place_actors(plan)

    for line in report["warnings"]:
        unreal.log_warning("GameBridge: " + line)
    unreal.log("GameBridge: " + describe_plan(plan))
    return report


def _place_actors(plan):
    import unreal

    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawned = {}
    for entry in plan["actors"]:
        location = unreal.Vector(*entry["location"])
        x, y, z, w = entry["rotation"]
        rotation = unreal.Quat(x, y, z, w).rotator()
        scale = unreal.Vector(*entry["scale"])
        if entry["asset"]:
            mesh = unreal.EditorAssetLibrary.load_asset(entry["asset"])
            actor = subsystem.spawn_actor_from_object(mesh, location, rotation)
        else:
            actor = subsystem.spawn_actor_from_class(
                unreal.Actor, location, rotation
            )
        if actor is None:
            continue
        actor.set_actor_scale3d(scale)
        actor.set_actor_label(entry["label"] or entry["name"])
        if not entry["visible"]:
            actor.set_actor_hidden_in_game(True)
            actor.set_is_temporarily_hidden_in_editor(True)
        parent = spawned.get(entry["parent"])
        if parent is not None:
            actor.attach_to_actor(
                parent,
                "",
                unreal.AttachmentRule.KEEP_RELATIVE,
                unreal.AttachmentRule.KEEP_RELATIVE,
                unreal.AttachmentRule.KEEP_RELATIVE,
                False,
            )
        spawned[entry["name"]] = actor
    return list(spawned)


def import_scene(manifest_path, content_root="/Game/FreeCAD", place_actors=True):
    manifest = read_manifest(manifest_path)
    plan = plan_import(manifest, manifest_path, content_root)
    return apply_plan(plan, place_actors)


def _default_manifest():
    """When run with no arguments, import the manifest sitting next to us."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "scene.gbscene")
    return candidate if os.path.exists(candidate) else None


def _main(argv):
    path = argv[1] if len(argv) > 1 else _default_manifest()
    if not path:
        print("usage: py gamebridge_unreal_import.py <scene.gbscene> [/Game/Path]")
        return 2
    root = argv[2] if len(argv) > 2 else "/Game/FreeCAD"
    import_scene(path, root)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
