# GameBridge → Blender

Blender shares FreeCAD's axes and handedness, and it is where the live link is
most useful, because it is where CAD geometry usually goes to be dressed up.

It is also the one target whose files are **not** pre-converted, which is worth
knowing if you look inside one. Blender's glTF importer always rotates Y-up to
Z-up and offers no way to switch that off, so the export is written in standard
glTF space and the importer's own conversion lands it the right way up. Doing it
here as well is what would put the model on its side.

## Importing an export

Install `clients/blender/gamebridge_blender_import.py` as an add-on
(*Edit > Preferences > Add-ons > Install*), then
*File > Import > FreeCAD GameBridge (.gbscene)*.

Headless, for a pipeline:

```
blender --background --python gamebridge_blender_import.py -- scene.gbscene
```

Everything lands in a collection named after the document. A second import
replaces that collection rather than stacking another copy beside it. Each
object gets `freecad_document` and `freecad_object` custom properties, so you
can always get back to the part that produced it.

## The live link

Install `clients/blender/gamebridge_blender_live.py` as well. In FreeCAD, start
*GameBridge > Live link*. In Blender, open the 3D viewport sidebar (<kbd>N</kbd>),
go to the **GameBridge** tab and press *Connect*.

From then on, editing the model in FreeCAD updates it in Blender. Only what
changed is sent, so moving a part is instant and re-tessellating a solid costs
only that solid.

The add-on needs to find FreeCAD's `Mod/GameBridge` folder, because it imports
the link's protocol from there rather than carrying a second copy of it. The
usual install locations are searched automatically; if FreeCAD is somewhere
unusual, set the path in the add-on's preferences or in the `GAMEBRIDGE_MODULE`
environment variable.

## Materials

Materials come across as base colour, metallic and roughness, which map
straight onto the Principled BSDF. FreeCAD's per-face colours become separate
mesh parts, so a painted model keeps its paint.

## Modifiers and edits survive a re-import of the live link

Objects are matched by their FreeCAD identity, not by name, so an object you
renamed in Blender is still updated rather than duplicated. Its mesh data is
rebuilt in place, which means modifiers, materials assigned in Blender and
object-level settings are kept. Vertex-level work — sculpting, hand-placed
seams — is not: it is attached to geometry that no longer exists after a
recompute.
