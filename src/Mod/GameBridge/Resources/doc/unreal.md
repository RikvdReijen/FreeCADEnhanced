# GameBridge → Unreal Engine

## Exporting

*GameBridge > Export to Unreal Engine*, pick a folder. You get one static mesh
file per solid under `Meshes/`, a `scene.gbscene` manifest, and a copy of the
importer script.

Names follow Unreal's own style guide: `SM_` for a static mesh, `M_` for a
material. Labels that Unreal would refuse — spaces, brackets, accents, a leading
digit — are folded to something legal and still readable (`M6 bolt (x4)` becomes
`SM_M6_bolt_x4`), and two objects that end up with the same name are numbered
rather than overwriting each other.

## Importing

Open your project, then in the editor's Python console:

```
py "D:/exports/bracket/gamebridge_unreal_import.py"
```

Assets land in `/Game/FreeCAD/<document>/Meshes` and one actor per FreeCAD
object is placed in the current level, with the hierarchy and the transforms
the manifest describes. To put them somewhere else:

```
py "gamebridge_unreal_import.py" "D:/exports/bracket/scene.gbscene" /Game/CAD
```

Python has to be enabled: *Edit > Plugins > Python Editor Script Plugin*.

## Coordinates

The export is already in Unreal's space: centimetres, Z up, left handed, with
FreeCAD's X staying X, the way Datasmith and the FBX pipeline do it. Winding is
reversed to match the mirrored Y.

**Unreal's glTF importer will convert again if you let it**, because a glTF file
is normally metres and Y up. The script asks the import pipeline not to, and
then measures each imported asset against the size FreeCAD recorded. If they
disagree it says which conversion happened:

* *every axis is 100 times the exported size* — units were converted twice; turn
  off **Convert Scene Unit** in the glTF import options.
* *the extents are the same but on different axes* — the coordinate system was
  converted twice; turn off **Convert Scene**.

## What does not come across

Materials are created from the manifest's colours, not imported as Unreal
material assets — a real Unreal material is a graph, and generating one that
nobody wants to keep is worse than leaving the slot for an artist to fill. The
manifest carries base colour, metallic, roughness, emissive and opacity for
whoever is building those materials.

Nanite, Lumen settings, collision and LODs are project decisions and are left
alone. If you want collision generated on import, set it in the project's import
defaults; the bridge does not override them.
