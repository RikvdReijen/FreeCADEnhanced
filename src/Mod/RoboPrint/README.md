# RoboPrint workbench

Large format robotic 3D printing for FreeCAD: slice a part into layers that are
flat, conical, cylindrical, spherical or wrapped onto an existing surface, build
the toolpath with a tool orientation for every point, check it against the
limits that matter on a robot, and write the program for the controller.

This is the free-software counterpart to what tools such as
[AiBuild](https://ai-build.com/) and [Adaxis](https://adaxis.eu/) do: it takes
the part and produces a robot program, with the process values (bead width, bead
height, speed, flow) carried per point rather than set once for the whole job.

Everything is a normal parametric FreeCAD object, so changing the layer height
or the bead width re-slices and re-builds the toolpath, and every other
workbench can consume the result.

The workbench is pure Python. The slicer, the toolpath generator, the checks and
the post-processors all run headlessly (`FreeCADCmd`); only the dialogs need the
GUI.

## Tools

| Tool | What it does |
| --- | --- |
| **Slice** | Slices the selected solid or mesh into layers. Five modes, below. Pick the layer height, the cone angle for conical mode, and how the seams line up. |
| **Toolpath** | Turns the slices into printing paths: perimeters, infill, point spacing, tool orientation, travel clearance and spiralisation. |
| **Check toolpath** | Reports overhang, tool tilt, cornering, extrusion continuity, distance from the model surface, nozzle clearance, material and time. |
| **Export program** | Writes the toolpath as CSV frames, 3- or 5-axis G-code, KUKA KRL, ABB RAPID or a Universal Robots script. |

## Slicing modes

Every mode is the same marching-triangles slicer run over a different scalar
field, so the whole toolchain downstream — offsets, infill, tool orientation,
overhang analysis — works the same way whatever the layers look like.

| Mode | The field | What it is for |
| --- | --- | --- |
| **Planar** | height along the build axis | Ordinary flat layers. Tilt the build axis to print the part at an angle. |
| **Conical** | height minus radius × tan(angle) | Cone-shaped layers. The nozzle leans by the cone angle, which is how an overhang gets printed with nothing underneath it. |
| **Cylindrical** | distance from an axis | Layers wrapped around an axis: printing onto a mandrel, a pipe or a rotary table. |
| **Spherical** | distance from a point | Nested shells around a point. |
| **Conformal** | distance to a substrate | Layers that follow a face or a scan mesh you select — repair and cladding, where the surface underneath is not flat. |

Because a curved field needs a mesh finer than the layer height (a box
tessellates to twelve triangles, which a cone would slice into nothing), the
mesh is subdivided until its longest edge is shorter than one layer before a
non-planar field is applied. Only edges that are actually too long are split,
and a split edge splits both triangles sharing it, so the mesh stays watertight
and a tessellation that is already fine over the curved part of a shape is left
alone.

The slicer then buckets triangles by the layers their field range can reach, so
a layer only visits the triangles that can cross it, and simplifies each contour
back to the tessellation tolerance — marching triangles emits a point per
crossed edge and nearly all of them are collinear. Together these take a conical
slice of a small part from about ten minutes to about two seconds.

## The toolpath

The bead is laid along its centre line, so the first perimeter runs **half a
bead inside the surface** and every further perimeter a whole bead further in.
Holes go the other way. A feature narrower than a single bead keeps its outline,
since one bead down the middle is the best the nozzle can do at that size.

Infill patterns are `Lines`, `Grid`, `Triangles` and `Concentric`; the lines are
clipped against the layer outline and then lifted back onto the field, so infill
on a cone stays on the cone. Spacing of zero means beads that just touch, which
is a solid fill.

`Spiral` joins the stack of outer walls into one continuous helix, ramping each
loop by one layer over its own length, so the extruder never stops.

### Tool orientation

| Mode | The tool axis |
| --- | --- |
| **Fixed** | Always the build axis. |
| **LayerNormal** | The gradient of the field, so the nozzle stands perpendicular to the layer it is printing — this is what makes conical and conformal printing work. |
| **Tilted** | The layer normal, leaned by the lead angle along the direction of travel. |

`MaxTilt` clamps the lean so the wrist never has to go further than the cell
allows, and `MaxOrientationChange` smooths the axis from point to point so the
robot does not snap between orientations mid-bead.

### Travel

`TravelClearance` lifts the nozzle along the **tool axis** between paths, then
crosses and comes back down — the robotic equivalent of a z-hop, except that on
a leaning nozzle "up" is the tool axis and not the build axis. Zero moves
straight across.

### Modifiers

`CornerCompensation` slows the nozzle down and thickens the bead through turns
sharper than `CornerThreshold`, which stops a corner being starved by the
deceleration. `ReinforceOverhangs` adds material where the path leans out past
`OverhangLimit`, ramping in over the range between the limit and horizontal.

## The checks

`Check toolpath` runs the metrics a robotic print is actually judged on:

| Check | What it measures |
| --- | --- |
| **Overhang** | For each bead, the angle to the nearest *segment* of the layer below, projected onto the layer plane. A bead landing inside the outline of the layer below counts as supported, so an inward step reports zero. |
| **Tool tilt** | Worst lean of the tool axis from the build axis, and how many points exceed the limit. |
| **Cornering** | Turns sharper than the threshold, and the sharpest one. |
| **Continuity** | How many times extrusion is interrupted, and the total travel. |
| **Surface tolerance** | Distance from the toolpath to the *skin* of the model — a collapsed offset or too coarse a layer shows up here. |
| **Nozzle clearance** | The print head modelled as a cylinder standing on the nozzle along the tool axis, sampled against what has already been printed. A cheap proximity test, not a solid collision check: it finds the obvious crashes, not every one. |
| **Material and time** | Bead cross-section as a stadium — `(w − h)·h + π(h/2)²` — times the path length, with the per-point speeds giving the time. |

## Post-processors

| Format | What it writes |
| --- | --- |
| **CSV** | Position, quaternion, tool axis and the process values, one row per point. The interchange format for when nothing else fits. |
| **G-code (3 axis)** | `X Y Z E`, for a gantry or a printer controller. |
| **G-code (5 axis)** | Adds two rotary words carrying the tool direction. |
| **KUKA KRL** | A `DEF` module of `LIN` motions with `A B C` from the tool frame. |
| **ABB RAPID** | A module of `MoveL` targets with the quaternion in RAPID's `(w, x, y, z)` order. |
| **UR Script** | `movel(p[...])` with the rotation vector, in metres. |

`Volumetric` extrusion writes `E` as cubic millimetres, which is what a pellet
extruder wants; otherwise it is a filament length for the given diameter.

The tool frame puts the tool Z **against** the print axis (the nozzle points at
the work) and the tool X along the direction of travel, so the rotation is fully
determined rather than left free about the tool axis.

## Objects

| Object | Properties worth knowing |
| --- | --- |
| **Slices** | `Base`, `Mode`, `LayerHeight`, `Axis`, `Origin`, `ConeAngle`, `Surface`, `FirstLayer`, `Tolerance`, `Seam`. Reports `LayerCount` and `ContourCount`. |
| **Toolpath** | `Base` (a Slices object), `BeadWidth`, `BeadHeight`, `Perimeters`, `InfillPattern`, `InfillSpacing`, `InfillAngle`, `PointSpacing`, `Smoothing`, `Spiral`, `Orientation`, `LeadAngle`, `MaxTilt`, `MaxOrientationChange`, `Speed`, `TravelSpeed`, `TravelClearance`, `Density`, `CornerCompensation`, `ReinforceOverhangs`. Reports `PathCount`, `PointCount`, `PathLength`, `Volume`, `Mass`, `Hours`, `MaxOverhang`, `MaxTiltUsed`. |

Both store the result in hidden properties, so a saved document reopens with its
slices and toolpath intact and can be exported again without re-slicing.

## Scripting

```python
import Part
from roboprint import features, analysis

doc = App.newDocument()
body = doc.addObject("Part::Feature", "Part")
body.Shape = Part.makeCone(90, 60, 260).cut(Part.makeCone(82, 52, 260))
doc.recompute()

slices = features.make_slices(body, "Conical", 5.0, doc=doc, ConeAngle=30.0)
path = features.make_toolpath(slices, bead_width=12.0, doc=doc)
path.Orientation = "LayerNormal"
path.Spiral = True
doc.recompute()

print(path.PathLength, path.Mass, path.Hours, path.MaxOverhang)
features.export_toolpath(path, "KUKA KRL", "/tmp/part.src")
```

The layers below are usable on their own, without a document:

```python
from roboprint import slicing, toolpath, analysis, postprocessors

layers, values = slicing.slice_mesh(shape, "Conical", 5.0, angle=30.0)
paths = toolpath.generate_toolpath(
    layers, values, bead_width=12.0, layer_height=5.0, perimeters=2,
    infill_pattern="Grid", orientation="LayerNormal",
    field=slicing.conical_field(30.0), max_tilt=45.0,
)
analysis.compute_overhangs(paths, 5.0)
report = analysis.quality_report(paths, 5.0, shape=shape)
program = postprocessors.post_process(paths, "ABB RAPID")
```

## Layout

```
roboprint/slicing.py         the fields, the marching triangles, the contours
roboprint/toolpath.py        offsets, infill, ordering, spiral, tool axes, modifiers
roboprint/analysis.py        bead geometry, overhang, the quality metrics, estimates
roboprint/postprocessors.py  the tool frames and the six output formats
roboprint/features.py        the two document objects and their view providers
roboprint/commands.py        the four GUI commands and their dialogs
```

## Prior art

The design follows what the commercial robotic slicers have settled on: process
values per point rather than per job, overhang analysis feeding modifiers that
change deposition rather than the path, and a small fixed set of published
quality metrics. Where an open-source project had already solved something well,
it was read for the approach rather than the code — `compas_slicer` for how a
scalar field generalises non-planar slicing, `pyslm` for the hatching geometry,
and CuraEngine for the vocabulary around seams and thin walls.

## Preferences

Edit → Preferences → RoboPrint sets the defaults for new objects: slicing mode
and layer height, bead width, perimeters, infill and spacing, tool orientation,
printing and travel speed, travel clearance, material density and filament
diameter, overhang and tilt limits, the nozzle radius and length used for the
clearance check, and the default export format.

## Not covered

**Supports.** The overhang check tells you where a print will fail, but nothing
here builds a support for it. Today the answers are to slice conically, which
gets a lot of overhangs printed with nothing underneath, or to model a support
body by hand and slice it as a second part. A tool for drawing supports
straight onto the layers is proposed in
[`doc/drawing-supports.md`](doc/drawing-supports.md).

This slices and posts; it does not simulate the robot. There is no kinematic
solver, so joint limits, singularities and reach are not checked — the nozzle
clearance test is a proximity test against printed material, nothing more. Run
the program through the cell's own simulator before you run it on the machine.
There is also no thermal model: layer time is reported, but the cooling that
decides whether a large bead holds its shape is not.
