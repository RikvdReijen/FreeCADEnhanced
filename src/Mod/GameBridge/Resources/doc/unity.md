# GameBridge → Unity

## Setting up, once

Copy `clients/unity` into your project's `Packages/` folder, or add it through
*Window > Package Manager > Add package from disk*.

Unity does not read `.glb` on its own, so install a glTF importer as well —
[glTFast](https://github.com/atteneder/glTFast) or
[UnityGLTF](https://github.com/KhronosGroup/UnityGLTF). If you would rather not
add one, export as OBJ instead: Unity reads that natively, at the cost of PBR
material values.

## Exporting

*GameBridge > Export to Unity*, and pick a folder **inside your project's
`Assets/`** — Unity only sees files that live in the project. When the editor
next regains focus it notices the export and offers to import it. You can also
import by hand from *Window > FreeCAD GameBridge > Import Scene...*.

## What arrives

One GameObject per FreeCAD object, in the same hierarchy, with the same names
and transforms — metres, Y up, already converted. Each carries a
`GameBridgeObject` component holding the FreeCAD document and object name, which
is what lets a re-import recognise what it is replacing instead of leaving you
with two of everything.

A prefab of the whole assembly is written next to the export, and objects are
marked static by default (a CAD assembly usually is).

## Coordinates

As with Unreal, the mesh files are exported already converted, and a glTF
importer that converts a second time will rotate or rescale them. The importer
measures each asset against the size FreeCAD recorded and tells you which
happened. The fix is in the glTF importer's own settings — turn off its axis
conversion, or set its scale factor to 1.

## Live link

The live link speaks a socket protocol rather than Unity's asset database, so
there is no Unity live client in this release; the loop for Unity is export and
re-import, which the job file makes close to one keystroke. The protocol is
documented in `gblink/protocol.py` and is not Blender-specific — a C# client is
perfectly possible, and the Blender one is the reference implementation.
