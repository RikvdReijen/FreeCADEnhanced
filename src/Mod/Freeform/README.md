# Freeform workbench

Free-form, sketch-first 3D modelling for FreeCAD, inspired by the workflow of
immersive design tools such as Gravity Sketch: draw strokes in space, give
them thickness, span surfaces between them, mirror them live, and smooth
blocky cages into organic shapes. Everything you create is a normal
parametric FreeCAD object, so it can be edited later and used by every other
workbench.

The workbench is pure Python. Its geometry core and document objects work
headlessly (`FreeCADCmd`), the interactive tools need the GUI.

## Tools

| Tool | What it does |
| --- | --- |
| **Stroke** | Press-drag-release in the 3D view to draw a smooth curve. Draw on the top/front/side plane, on a custom plane, on the geometry under the cursor, or "in the air" on a plane facing the camera. Optional tube thickness (with taper and a round, square, triangle or flat profile), closing, filling, grid snapping, snapping to the ends of existing strokes (a loop that returns to its start closes itself), and live recognition of straight lines, circles and arcs. Keeps running until Escape. |
| **Primitives** | Sphere, box, cylinder, cone and torus placed on the drawing plane: click for the default size, drag to size them. |
| **Thicken** | Turns selected strokes into tubes (start and end diameter). |
| **Ribbon** | A flat or upright band of a given width along a stroke, optionally with thickness. |
| **Surface** | Lofts a smooth (or ruled) surface through two or more strokes; closed strokes can produce a solid. |
| **Patch** | Fills a closed loop of strokes or selected edges with a smooth surface. |
| **Revolve** | Revolves a stroke around the vertical axis of the drawing plane, or around a second, straight stroke. |
| **Sweep** | Sweeps a closed profile stroke along a path stroke (select the path, then the profile). |
| **Extrude** | Extrudes strokes along the normal of the drawing plane; closed strokes become solids. |
| **Subdivide** | Catmull-Clark subdivision of a blocky Part shape or mesh: box in, organic blob out. |
| **Solidify** | Stitches a closed mesh (typically a subdivision surface) into a Part solid ready for booleans and export. |
| **Thicken surface** | Gives ribbons, lofted surfaces and patches a thickness, turning them into solids. |
| **Smooth / Simplify / Recognise / Join** | Post-process strokes: more smoothing passes, fewer points, replace by an exact line/circle/arc, chain several strokes into one. |
| **To sketch** | Converts planar strokes into Sketcher sketches (lines, arcs, circles, B-splines with coincident constraints) for constraining or Part Design. |
| **Mirror** | Live mirrored copies of the selection across the symmetry plane. |
| **Symmetry mode** | While on, every new stroke and primitive gets a live mirror twin. The symmetry plane can be YZ, XZ, XY or any planar face. |
| **Drawing plane** | Top, front, side, facing the camera, on surfaces, from a selected face, or the Draft working plane. |
| **Snap to grid** | Snaps stroke points to the plane grid. |
| **Colour palette** | Pick the colour for everything you draw next; with a selection, recolours it. |
| **New layer** | Creates a (Draft) layer in the current colour and moves the selection into it. |
| **Transform** | The standard transform manipulator, for grabbing and moving things around like in an immersive tool. |

## Objects

All objects are `Part::FeaturePython` features (the subdivision surface is a
`Mesh::FeaturePython`) with these proxies from `freeform.features`:

| Object | Key properties |
| --- | --- |
| `Stroke` | `Points`, `Closed`, `MakeFace`, `Smoothing`, `Tolerance`, `Interpolate`, `Degree`, `Thickness`, `EndThickness`, `Profile`, `ProfileUp`, `TubeSections`, `Length` (read only) |
| `Ribbon` | `Base`, `Width`, `Normal`, `Mode` (Flat/Upright), `Thickness`, `Samples`, `Centered` |
| `Surface` | `Sections`, `Ruled`, `Closed`, `Solid`, `MaxDegree`, `Thickness` |
| `Patch` | `Boundary` (objects or edges), `Thickness` |
| `SubD` | `Base`, `Iterations`, `KeepBoundary` |
| `MeshSolid` | `Base`, `Tolerance`, `Refine` |

Mirror, revolve, sweep, extrude and primitives reuse the built-in
`Part::Mirroring`, `Part::Revolution`, `Part::Sweep`, `Part::Extrusion` and
`Part::Sphere` / `Box` / `Cylinder` / `Cone` / `Torus` features.

## Scripting

```python
import FreeCAD
from FreeCAD import Vector
from freeform import features, geometry

doc = FreeCAD.newDocument()
pts = [Vector(x, 10 * (x / 50.0) ** 2, 0) for x in range(0, 51, 5)]

stroke = features.make_stroke(pts, doc=doc, thickness=3.0)      # a tube
ribbon = features.make_ribbon(stroke, width=8.0, doc=doc)        # a band along it
twin = features.make_mirror(stroke, Vector(0, 0, 0), Vector(1, 0, 0), doc=doc)
box = doc.addObject("Part::Box", "Cage")
blob = features.make_subd(box, iterations=3, doc=doc)            # organic mesh
solid = features.make_mesh_solid(blob, doc=doc)                  # ... as a Part solid
sketch = features.make_sketch(features.make_stroke(pts, doc=doc), doc=doc)  # to Sketcher
doc.recompute()

# the algorithms are available on their own
kind, data = geometry.recognize_stroke(pts)        # ("line" | "circle" | "arc" | None, ...)
smooth = geometry.smooth_points(pts, iterations=3)
fewer = geometry.simplify_points(pts, tolerance=0.5)
points, faces = geometry.catmull_clark(*geometry.polygons_from_shape(box.Shape), iterations=2)
```

## Layout

```
Mod/Freeform/
  Init.py, InitGui.py        workbench registration
  freeform/geometry.py       smoothing, simplification, resampling, shape recognition,
                             mirroring, Catmull-Clark subdivision (no GUI, no documents)
  freeform/features.py       parametric objects and the make_* scripting API
  freeform/workplane.py      drawing plane and symmetry plane
  freeform/tracker.py        Coin3D previews and mouse capture (GUI)
  freeform/palette.py        colour palette, layers, palette widget
  freeform/commands.py       GUI commands and the stroke task panel
  Resources/                 icons, preferences page
  FreeformTest/              unit tests (run: FreeCADCmd -t TestFreeformApp)
```

## Shortcuts

Stroke `F, S` · Thicken `F, T` · Mirror `F, R` · Symmetry mode `F, M` ·
Colour palette `F, C`. Escape ends the stroke and primitive tools. The
symmetry and grid-snap toolbar toggles stay in sync with the stroke panel.

## Preferences

Edit → Preferences → Freeform: default smoothing, simplification tolerance,
tube thickness, mouse step, shape recognition and its tolerance, end
snapping, continuous drawing, default primitive size, ribbon width,
subdivision passes, surface thickness, extrusion length and grid spacing. The palette colour, drawing plane and symmetry plane are remembered
between sessions as well.
