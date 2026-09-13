# FreeCAD GameBridge for Unity

Imports scenes exported from FreeCAD's GameBridge workbench, rebuilding the
object hierarchy, transforms, names and provenance from the `.gbscene` manifest.

## Installing

Copy this folder into your project's `Packages/` directory, or add it through
*Window > Package Manager > Add package from disk* and pick `package.json`.

A glTF importer has to be installed as well, because Unity does not read `.glb`
on its own — either [glTFast](https://github.com/atteneder/glTFast) (`com.atteneder.gltfast`)
or [UnityGLTF](https://github.com/KhronosGroup/UnityGLTF). Export as OBJ instead
if you would rather not add one; Unity reads that natively.

## Importing

Export from FreeCAD with the **Unity** target into a folder **inside this
project's `Assets/`** — Unity only sees files that live in the project. When the
editor next regains focus it notices the export and offers to import it.

You can also import by hand from *Window > FreeCAD GameBridge > Import Scene...*.

## What arrives

* One GameObject per FreeCAD object, in the same hierarchy, with the same names.
* Transforms in metres, Y up — the exporter has already converted them.
* A `GameBridgeObject` component on each object, holding the FreeCAD document
  and object name, so a re-import can recognise what it is replacing.
* Optionally a prefab of the whole assembly, written next to the export.

## If the model arrives sideways or a hundred times too big

The mesh files are exported **already converted** into Unity's space. If the
glTF importer converts them a second time the model ends up rotated or scaled,
and the console will say which of the two happened — the importer measures every
asset against the size FreeCAD recorded. The fix is in the glTF importer's own
settings: turn its axis conversion off, or set its scale factor to 1.
