# GameBridge

Send FreeCAD models to **Unreal Engine**, **Unity** and **Blender** — as an
export, or over a live link that follows your edits.

```
FreeCAD  ──┬── export ──▶  .glb / .obj  +  scene.gbscene  +  an importer script
           │
           └── live link ─▶  a running Blender, or anything speaking the protocol
```

## Why not just export glTF and be done

Because the three targets disagree with FreeCAD and with each other about
almost everything that matters:

| Target   | Handedness | Up | Unit | Assets                    |
|----------|------------|----|------|---------------------------|
| FreeCAD  | right      | +Z | mm   | a document                |
| glTF 2.0 | right      | +Y | m    | a file                    |
| Blender  | right      | +Z | m    | a scene                   |
| Unity    | **left**   | +Y | m    | a project of assets       |
| Unreal   | **left**   | +Z | cm   | a project of assets       |

Two of them are left handed, which means the model is mirrored on the way in
and every triangle has to be wound the other way round or the engine lights and
culls the wrong side of it. Placements have to be *conjugated*, not merely
rotated, or parts land in the right positions facing the wrong way. And an
asset-based engine wants each solid as its own file with its pivot at its own
origin, with the placements carried separately — an asset whose vertices sit
400 units from its own origin is one nobody can reuse.

GameBridge does all of that once, in one place, and tests it.

## Using it

### Export

Select the **GameBridge** workbench, then *Export to Unreal Engine* / *Unity* /
*Blender*, and pick a folder. Select objects first to export only those.

You get:

```
scene.gbscene                     the manifest: hierarchy, names, placements
Meshes/SM_Bracket.glb             one asset per solid (Unreal, Unity)
gamebridge_unreal_import.py       the importer, shipped with the export
```

For Blender it is one `.glb` holding the whole hierarchy, because that is what
its importer rebuilds in a single step.

From a macro, or from a build server:

```python
from gbcore import service
service.export_document("unreal", "/tmp/bracket")
```

```
freecadcmd tools/gamebridge_export.py -- bracket.FCStd --target unity --out ./Assets/CAD
```

### Importing on the other side

* **Unreal** — open your project and run the `gamebridge_unreal_import.py` that
  came with the export from the editor's Python console. Assets land in
  `/Game/FreeCAD/<document>` and the actors are placed for you.
* **Unity** — export into a folder inside your project's `Assets/`. The editor
  package in `clients/unity` notices it and offers to import. A glTF importer
  package (glTFast or UnityGLTF) has to be installed, or export as OBJ.
* **Blender** — install `clients/blender/gamebridge_blender_import.py` as an
  add-on and use *File > Import > FreeCAD GameBridge*, or run it headless:
  `blender --background --python gamebridge_blender_import.py -- scene.gbscene`.

### Live link

*Live link* in the workbench starts a server on `127.0.0.1:54321`, and the
document is pushed to whoever connects as you edit it. In Blender, install
`clients/blender/gamebridge_blender_live.py` and press *Connect* in the
viewport sidebar.

Only what changed is sent. Moving a part costs a transform and no geometry at
all; two identical bolts cost one transfer. The link listens on the loopback
interface and refuses anything else without both an explicit argument and a
token, because the model on the wire is the one you are working on.

## If the model arrives sideways or a hundred times too big

The mesh files are exported **already converted** into the engine's space. If
the engine's glTF importer converts them a second time, the model ends up
rotated or scaled by 100. Both the Unreal and Unity importers measure what
arrived against the size FreeCAD recorded and say which of the two happened,
with the setting to change. The fix is always in the engine's own glTF import
options: turn off its axis conversion, or set its scale factor to 1.

## Layout

```
gbcore/      the intermediate scene, conversions, tessellation, the document walker
gbformat/    glTF/GLB, OBJ and the .gbscene manifest
gbtargets/   what each engine wants: space, names, file layout
gblink/      the live link: protocol, server, client, change detection
clients/     the engine side - Blender add-ons, Unreal scripts, a Unity package
Tests/       runs on a bare python3, no FreeCAD needed
tools/       the command-line exporter
```

## Tests

```
python3 src/Mod/GameBridge/Tests/run_all.py
```

Everything runs on a plain interpreter with no FreeCAD, no Blender and no
engine: the parts that need them are separated from the parts that decide what
to do, and only the latter are tested here. The glTF writer is checked by
re-parsing what it wrote against the spec, not against itself; the live link is
exercised over a real socket rather than a mock.
