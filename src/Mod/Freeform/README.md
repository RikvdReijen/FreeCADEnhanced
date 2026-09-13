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

## Parametric tools

A second toolbar covers the generative side, borrowed from the vocabulary of
node based tools such as Grasshopper. Each one is a live object: change a
count, a seed, an expression or an attractor and the result regenerates.

| Tool | What it does |
| --- | --- |
| **Expression curve** | A stroke from x(t), y(t), z(t) expressions over a t range. Helices, spirals, Lissajous figures and roses in one dialog; the expressions stay editable. |
| **Offset curve** | Parallel copies of a planar curve, with arc, tangent or intersection corners, optionally filled into a band. |
| **Blend curves** | A tangent continuous bridge between the nearest ends of two curves, with adjustable bulge. |
| **Divide curve** | Evenly spaced points and oriented frames along a curve, by count or by spacing. The frames are published as a `Placements` list for scripting. |
| **Contours** | Section curves (or faces) through a shape at a regular spacing along any direction. |
| **Array along curve** | Copies of an object oriented along a curve, with start/end scale, total twist, and attractor driven scaling. |
| **Surface panels** | The UV grid of any face as panels, points, frames, or copies of another object. Panel size follows attractors. |
| **Voronoi** | Voronoi cells over a planar face, from random seeds or from your own points, optionally inset, output as cells, edges or the Delaunay dual. |
| **Deform** | Twist, taper, bend, stretch, wave, noise, or flow a mesh along a curve. |
| **Relax** | Laplacian relaxation towards a minimal surface, keeping the boundary and any anchor points fixed: a small form finding solver. |
| **Populate** | Scattered points over a planar face, with Lloyd relaxation for even spacing and attractor or image driven density. Feeds the Voronoi tool. |
| **Lattice** | The edges of a mesh or shape as struts with nodes: a space frame from any cage, or a wireframe when the radius is zero. |
| **Tween curves** | Intermediate curves morphing one curve into another, optionally including the originals. |
| **L-system** | A branching structure grown from rewriting rules, with a 3D turtle, per-level step and angle scaling, tapered branches and presets for bushes, trees and Koch curves. |
| **Morph onto surface** | Copies of a shape morphed into the UV cells of a surface: the Grasshopper box morph, with a cell scale for gaps. |
| **Project onto shape** | Curves projected onto a shape along a direction, or pulled onto its nearest points. Draw flat, wrap onto the model. |
| **Two rail sweep** | A profile swept along a path and guided by a second rail, the sweep that a single spine cannot express. |
| **Frame panels** | Every face of a mesh or shape as a panel with a border and an opening, with a gap between neighbours. |

### Panel patterns

The surface panelling tool draws its cells in quad, triangle, diamond,
brick or hexagon patterns, so one surface can be clad as a honeycomb, a
diagrid or a brick bond without changing anything else.

### Jitter

The array and panel tools take a jitter group: `JitterOffset`,
`JitterRotation`, `JitterScale` and a `JitterSeed`. It breaks up the
regularity of a repeated element, and the same seed always reproduces the
same arrangement.

### Attractors and image fields

The array, panel, Voronoi, populate and lattice tools share an attractor
group: link any objects as `Attractors`, set `AttractorRadius`, `MinScale`
and a `Falloff` (linear, smooth or inverse). Elements near an attractor
shrink towards `MinScale`, elements beyond the radius stay full size. A
`MinScale` of zero makes elements disappear entirely at the attractor.

The same group takes an `Image`: point it at a PNG, PGM or PPM file and
element size (or, for populate, point density) follows the brightness of
the picture, mapped over the host surface in its own U and V direction.
`InvertImage` uses the dark areas instead. The reader is pure Python, so
no extra dependency is needed.

### Compared with Grasshopper

Covered here: parametric expression curves, divide curve with frames,
offset, blend, contour sections, array along curve, surface UV panelling
in five patterns, populate with Lloyd relaxation, attractor and image
driven variation, Voronoi and Delaunay tessellation, tween curves,
L-systems, the deformer set (twist, taper, bend, stretch, wave, noise,
flow along curve), surface box morphing, lattices from mesh edges,
framed panels, curve projection and pulling, two rail sweeps, randomised
arrays, and Kangaroo style mesh relaxation.

Not covered: the node graph itself (these are document objects in the tree,
driven by the property editor and FreeCAD's own expression engine), data
trees and list operations, physics solving beyond Laplacian relaxation,
and the analysis and simulation plug-ins.

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
| `Expression` | a `Stroke` plus `XExpression`, `YExpression`, `ZExpression`, `TMin`, `TMax`, `Samples` |
| `Offset` | `Base`, `Distance`, `Join`, `Fill` |
| `Blend` | `First`, `Second`, `Bulge` |
| `Divide` | `Base`, `Count`, `Spacing`, `Up`, `FrameSize`, `Placements` (read only) |
| `Contours` | `Base`, `Direction`, `Spacing`, `Start`, `Faces` |
| `CurveArray` | `Base`, `Path`, `Count`, `Align`, `Up`, `Twist`, `StartScale`, `EndScale` + attractors |
| `SurfaceGrid` | `Base`, `CountU`, `CountV`, `Output`, `Pattern`, `Item`, `PanelScale`, `ItemScale` + attractors |
| `Voronoi` | `Base`, `Count`, `Seed`, `Points`, `Inset`, `Output` + attractors |
| `Deform` | `Base`, `Mode`, `Amount`, `Axis`, `Direction`, `Origin`, `AutoOrigin`, `Wavelength`, `Seed`, `Path`, `AlongNormals` |
| `Relax` | `Base`, `Iterations`, `Strength`, `KeepBoundary`, `Anchors` |
| `Populate` | `Base`, `Count`, `Seed`, `Relax`, `Placements` (read only) + attractors |
| `Lattice` | `Base`, `Radius`, `Nodes`, `NodeScale`, `UseShapeEdges` + attractors |
| `Tween` | `First`, `Second`, `Count`, `Samples`, `IncludeEnds`, `Flip` |
| `LSystem` | `Axiom`, `Rules`, `Generations`, `Step`, `Angle`, `StepScale`, `AngleScale`, `Direction`, `Thickness`, `Taper`, `MaxBranches` |
| `BoxMorph` | `Base`, `Target`, `CountU`, `CountV`, `Height`, `Offset`, `CellScale` |
| `Project` | `Base`, `Target`, `Mode`, `Direction`, `Samples` |
| `Sweep2` | `Profiles`, `Path`, `Rail`, `Solid`, `KeepContact` |
| `Frame` | `Base`, `Width`, `Shrink`, `Filled`, `MergeCoplanar` + attractors |

The attractor group adds `Attractors`, `AttractorRadius`, `MinScale`,
`Falloff`, `Image` and `InvertImage` to the objects that use it; the
jitter group adds `JitterOffset`, `JitterRotation`, `JitterScale` and
`JitterSeed` to the array and panel objects.

`SubD` also publishes its quad topology in a hidden `Polygons` property,
so panelling, framing and lattices see its quads rather than the
diagonals of the triangulated mesh underneath.

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

# the parametric generators
from freeform import generators, parametric

helix = generators.make_expression("30*cos(t)", "30*sin(t)", "3*t", 0, 6 * math.pi, doc=doc)
rail = generators.make_curve_array(box, helix, count=40, doc=doc)   # copies along it
rail.Twist = 360                                                     # rotating as they go
panel = doc.addObject("Part::Plane", "Panel")
cells = generators.make_voronoi(panel, count=30, seed=7, inset=1.0, doc=doc)
tent = generators.make_relax(blob, iterations=80, doc=doc)           # form finding
seeds = generators.make_populate(panel, count=40, relax=5, doc=doc)   # even scatter
cells.Points = [seeds]                                               # Voronoi from them
frame = generators.make_lattice(blob, radius=1.0, doc=doc)           # struts on its edges
panels = generators.make_frame(blob, width=0.25, doc=doc)            # framed panels
wrapped = generators.make_project(stroke, blob, doc=doc)             # onto the surface
tree = generators.make_lsystem("F", ["F=F[+F]F[-F]F"], 4, doc=doc)   # grown structure
doc.recompute()

# the algorithms are available on their own
kind, data = geometry.recognize_stroke(pts)        # ("line" | "circle" | "arc" | None, ...)
smooth = geometry.smooth_points(pts, iterations=3)
fewer = geometry.simplify_points(pts, tolerance=0.5)
points, faces = geometry.catmull_clark(*geometry.polygons_from_shape(box.Shape), iterations=2)

frames = parametric.frames_along_wire(helix.Shape.Wires[0], count=20)
triangles = parametric.delaunay_2d([(0, 0), (10, 0), (10, 10), (0, 10)])
scale = parametric.attractor_factor(Vector(5, 0, 0), [Vector(0, 0, 0)], radius=10)
relaxed = parametric.relax_mesh(points, faces, iterations=50)
```

## Layout

```
Mod/Freeform/
  Init.py, InitGui.py        workbench registration
  freeform/geometry.py       smoothing, simplification, resampling, shape recognition,
                             mirroring, Catmull-Clark subdivision (no GUI, no documents)
  freeform/parametric.py     remapping, attractors, safe expressions, curve frames,
                             Delaunay, Voronoi, panel patterns, populating,
                             L-systems, image reading, deformers, relaxation (no GUI)
  freeform/generators.py     the parametric objects built on top of them
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
