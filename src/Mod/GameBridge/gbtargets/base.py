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
"""What a target is, and the export every target shares.

A target is the answer to four questions about one engine: which space it works
in, what it will accept as an asset name, how it wants the geometry split into
files, and where in a project those files belong.  Everything else about
exporting - allocating names, welding, writing the files, assembling the
manifest - is the same for all three, and lives here.

The split matters more than it looks.  Blender wants one file holding the whole
hierarchy, because that is what its glTF importer rebuilds in one step.  Unreal
and Unity want one file per mesh with its pivot at the origin, because their
content browsers hold *assets* that get placed into a level, and an asset whose
vertices are 400 units from its own origin is one nobody can reuse.  The same
scene therefore leaves as one file or as twenty depending on where it is going,
and the manifest is what carries the placements in the second case.
"""

import os

from gbcore import Matrix4, NameAllocator
from gbcore.transform import get_convention
from gbformat import AssetRecord, build_manifest, write_glb, write_gltf, write_manifest, write_obj

__all__ = ["ExportOptions", "ExportResult", "Target"]


class ExportOptions:
    """Knobs the user can turn, with defaults that suit a CAD export."""

    def __init__(
        self,
        mesh_format="glb",
        weld=True,
        drop_degenerate=True,
        include_hidden=False,
        include_geometry=True,
        convention=None,
        write_bootstrap=True,
        subdirectory=None,
        overwrite=True,
    ):
        if mesh_format not in ("glb", "gltf", "obj"):
            raise ValueError("unsupported mesh format %r" % (mesh_format,))
        self.mesh_format = mesh_format
        self.weld = weld
        self.drop_degenerate = drop_degenerate
        #: Hidden objects are skipped by default: an exploded view's construction
        #: geometry is hidden for a reason, and importing it wastes the artist's
        #: time deleting it again.
        self.include_hidden = include_hidden
        self.include_geometry = include_geometry
        #: Overrides the target's own space.  Rarely wanted, but a studio with a
        #: house convention needs it.
        self.convention = convention
        self.write_bootstrap = write_bootstrap
        self.subdirectory = subdirectory
        self.overwrite = overwrite

    def to_dict(self):
        return {
            "meshFormat": self.mesh_format,
            "weld": self.weld,
            "dropDegenerate": self.drop_degenerate,
            "includeHidden": self.include_hidden,
        }


class ExportResult:
    """What an export produced, in enough detail to report or to test."""

    def __init__(self, target, directory, manifest=None):
        self.target = target
        self.directory = directory
        self.manifest = manifest
        self.manifest_path = None
        self.files = []
        self.assets = []
        self.warnings = []

    def add_file(self, path, role="asset"):
        self.files.append({"path": path, "role": role})
        return path

    def warn(self, message):
        self.warnings.append(message)
        return message

    @property
    def paths(self):
        return [entry["path"] for entry in self.files]

    def summary(self):
        stats = (self.manifest or {}).get("stats", {})
        return "%s: %d file(s), %d mesh(es), %d triangle(s)%s" % (
            self.target.title,
            len(self.files),
            stats.get("meshes", 0),
            stats.get("triangles", 0),
            " (%d warning(s))" % len(self.warnings) if self.warnings else "",
        )

    def __repr__(self):
        return "ExportResult(%s, %d files)" % (self.target.name, len(self.files))


class Target:
    """Base class for the engine profiles.

    Subclasses set the class attributes and, if the engine needs one, override
    :meth:`write_bootstrap` to emit a launcher script.
    """

    #: Short identifier, used on the command line and in the manifest.
    name = "generic"
    #: Human readable, used in the GUI and in messages.
    title = "Generic"
    #: The space the mesh files are written in.
    convention_name = "gltf"
    #: The space the model ends up in once the engine has imported it, when
    #: that differs - Blender's importer converts, the others are asked not to.
    #: The manifest describes this one, because it is where the placements the
    #: engine applies have to be expressed.
    manifest_convention_name = None
    #: Name of the sanitising policy from :mod:`gbcore.naming`.
    policy_name = "unreal"
    #: One file per mesh (asset-based engines) or one file per scene.
    split_meshes = False
    #: Prefixes the engine's own naming conventions expect.
    mesh_prefix = ""
    material_prefix = ""
    #: Where files go inside the export directory.
    mesh_directory = "Meshes"
    #: The manifest's file name, relative to the export directory.
    manifest_name = "scene.gbscene"
    #: Default container.  Overridden by :attr:`ExportOptions.mesh_format`.
    default_format = "glb"

    def __init__(self, options=None):
        self.options = options or ExportOptions(mesh_format=self.default_format)

    # -- profile ---------------------------------------------------------

    @property
    def convention(self):
        """The space the geometry is written in."""
        return get_convention(self.options.convention or self.convention_name)

    @property
    def manifest_convention(self):
        """The space the engine will hold the model in once imported."""
        if self.options.convention:
            return get_convention(self.options.convention)
        return get_convention(self.manifest_convention_name or self.convention_name)

    def asset_name(self, label, allocator, key=None, prefix=None):
        """A unique, engine-legal name for one asset."""
        prefix = self.mesh_prefix if prefix is None else prefix
        return allocator.allocate(prefix + str(label), key=key)

    def describe(self):
        return {
            "name": self.name,
            "title": self.title,
            "convention": self.convention.to_dict(),
            "importedConvention": self.manifest_convention.to_dict(),
            "splitMeshes": self.split_meshes,
            "meshPrefix": self.mesh_prefix,
            "materialPrefix": self.material_prefix,
        }

    # -- export ----------------------------------------------------------

    def prepare(self, scene, result):
        """Clean the scene up in place before anything is written.

        Welding and degenerate removal happen here rather than in the
        tessellator because they are per-export decisions: a live link sending
        twenty updates a second may well want to skip both.
        """
        options = self.options
        if not options.include_hidden:
            _drop_hidden(scene)
        for mesh in scene.meshes:
            if options.drop_degenerate:
                before = mesh.triangle_count
                mesh.drop_degenerate_triangles()
                removed = before - mesh.triangle_count
                if removed:
                    result.warn(
                        "%s: dropped %d degenerate triangle(s)" % (mesh.name, removed)
                    )
            if options.weld:
                mesh.weld()
            if not mesh.normals:
                mesh.compute_normals()
        for name in scene.drop_empty_meshes():
            # Dropped rather than kept and warned about: an empty mesh writes an
            # accessor with no elements, which no glTF loader will accept.
            result.warn("%s: dropped, it has no geometry left after cleanup" % name)
        scene.prune_empty()
        return scene

    def export(self, scene, directory):
        """Write ``scene`` into ``directory``.  Returns an :class:`ExportResult`."""
        options = self.options
        directory = os.path.abspath(directory)
        if options.subdirectory:
            directory = os.path.join(directory, options.subdirectory)
        result = ExportResult(self, directory)
        os.makedirs(directory, exist_ok=True)

        self.prepare(scene, result)
        scene.validate()

        allocator = NameAllocator(self.policy_name)
        node_names = {}
        for node in scene.walk():
            node_names[id(node)] = self.asset_name(
                node.name, allocator, key=id(node), prefix=""
            )

        assets = self.write_assets(scene, directory, result, allocator)
        manifest = build_manifest(
            scene,
            self.manifest_convention,
            assets,
            node_names,
            extra={"exporter": self.describe(), "options": options.to_dict()},
        )
        result.manifest = manifest
        result.assets = assets
        result.manifest_path = write_manifest(
            manifest, os.path.join(directory, self.manifest_name)
        )
        result.add_file(result.manifest_path, "manifest")
        if options.write_bootstrap:
            self.write_bootstrap(scene, directory, result)
        return result

    def write_assets(self, scene, directory, result, allocator):
        """Write the geometry, as one file or as one file per mesh."""
        if not self.split_meshes:
            return self._write_single_file(scene, directory, result)
        return self._write_per_mesh(scene, directory, result, allocator)

    def _write_single_file(self, scene, directory, result):
        name = _safe_stem(scene.name or scene.document or "scene")
        path = self._write_scene_file(scene, directory, name, result)
        record = AssetRecord(
            0,
            name,
            os.path.relpath(path, directory),
            kind="scene",
            triangles=sum(m.triangle_count for m in scene.meshes),
            vertices=sum(m.vertex_count for m in scene.meshes),
            checksum=scene.checksum(),
        )
        return [record]

    def _write_per_mesh(self, scene, directory, result, allocator):
        """One asset per mesh, pivot at the origin, placements in the manifest."""
        from gbcore import Node, Scene

        mesh_directory = os.path.join(directory, self.mesh_directory)
        os.makedirs(mesh_directory, exist_ok=True)
        assets = []
        for index, mesh in enumerate(scene.meshes):
            name = self.asset_name(mesh.name, allocator, key=("mesh", index))
            single = Scene(name, document=scene.document)
            single.materials = scene.materials
            single.add_mesh(mesh)
            single.add_root(Node(name, Matrix4(), mesh=0))
            path = self._write_scene_file(single, mesh_directory, name, result)
            assets.append(
                AssetRecord.for_mesh(
                    index, name, os.path.relpath(path, directory), mesh, index
                )
            )
        return assets

    def _write_scene_file(self, scene, directory, stem, result):
        fmt = self.options.mesh_format
        path = os.path.join(directory, "%s.%s" % (stem, fmt))
        if os.path.exists(path) and not self.options.overwrite:
            raise IOError("%s already exists" % path)
        convention = self.convention
        if fmt == "glb":
            write_glb(scene, path, convention)
        elif fmt == "gltf":
            write_gltf(scene, path, convention)
            sidecar = os.path.join(directory, stem + ".bin")
            if os.path.exists(sidecar):
                result.add_file(sidecar, "buffer")
        else:
            write_obj(scene, path, convention)
            library = os.path.join(directory, stem + ".mtl")
            if os.path.exists(library):
                result.add_file(library, "material")
        result.add_file(path, "mesh")
        return path

    def write_bootstrap(self, scene, directory, result):
        """Emit whatever the engine needs to run the import.  Optional."""
        return None


def _drop_hidden(scene):
    """Remove hidden nodes, and any mesh nothing references afterwards."""

    def keep(node):
        node.children = [c for c in node.children if keep(c)]
        return node.visible

    scene.roots = [r for r in scene.roots if keep(r)]

    used = sorted({n.mesh for n in scene.walk() if n.mesh is not None})
    if len(used) == len(scene.meshes):
        return scene
    remap = {old: new for new, old in enumerate(used)}
    scene.meshes = [scene.meshes[old] for old in used]
    for node in scene.walk():
        if node.mesh is not None:
            node.mesh = remap[node.mesh]
    return scene


def _safe_stem(text):
    stem = "".join(c if (c.isalnum() or c in "_-") else "_" for c in str(text))
    return stem.strip("_") or "scene"
