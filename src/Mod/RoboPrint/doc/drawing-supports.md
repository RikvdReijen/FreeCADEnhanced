# Feature request: draw supports directly onto the layers in the 3D view

> Intended as a GitHub issue. Issues are currently disabled on this
> repository, so it is kept here until they are turned on.

## What is missing

The RoboPrint workbench can tell you *where* a print will fail — `Check
toolpath` reports every bead that leans out further than the overhang limit —
but it cannot do anything about it. The only answers today are to change the
slicing mode (conical layers get a lot of overhangs printed with nothing
underneath) or to model a support body by hand in Part/PartDesign and slice it
as a second part.

Neither covers the common case on a large format machine: *most* of the part is
fine, and one or two regions need something underneath them. Modelling those
regions as solids is slow, hard to get right, and has to be redone every time
the layer height or the bead width changes.

## What it should look like

Draw the support where you want it, on the layers, and let the slicer build it.

The interaction should feel like sketching, the way the Freeform workbench
already lets you press-drag-release in the 3D view — except that the stroke is
snapped to the layer it is drawn on rather than to a work plane:

1. Select a `Slices` object and start **Draw support**.
2. The view shows one layer at a time (a layer slider / PageUp-PageDown, with
   the layers above and below ghosted, as every slicer preview does).
3. Drag in the view to paint a region on the current layer. The stroke snaps
   onto the layer's own field value, so on a conical or conformal slice it
   lands on the curved layer, not on a flat plane.
4. The region is carried up (or down) through the following layers until it
   meets the part, with a per-layer taper so a support can start small on the
   plate and spread out under the overhang.
5. The result is a `Support` object: a normal parametric FreeCAD object holding
   the drawn outlines per layer.

Then `Toolpath` consumes it alongside the part, so the supports come out as
paths with role `support`, at their own bead width, spacing and flow.

## Why draw it per layer rather than model a solid

- A support is a *printing* concept, not a *modelling* one. What matters is
  which layers it exists on and how densely it is filled, not its exact B-rep.
- Drawing on the layer means the support automatically follows a non-planar
  slice. A modelled solid sliced conically gives you conical supports whether
  that is what you wanted or not.
- It re-slices. Change the layer height and the drawn regions stay where they
  were put, because they are stored as outlines against layer values, not as
  absolute geometry.

## Proposed design

New object `RoboPrint::Support`:

| Property | What it does |
| --- | --- |
| `Base` | The `Slices` object the support belongs to |
| `Regions` | The drawn outlines, stored per layer (hidden, like `Slices.Points`/`Starts`) |
| `Mode` | `Drawn`, `Automatic` (from the overhang report), or `Both` |
| `Taper` | Degrees the region shrinks per layer going down, so a support stands on a smaller footprint |
| `Pattern` | `Lines`, `Grid`, `Zigzag`, `Tree` |
| `Spacing` | Distance between support beads — much wider than the part's own infill |
| `BeadWidth`, `Flow` | Thinner, under-extruded beads so the support snaps off |
| `Gap` | Clearance between the top of the support and the part above it, in layers |
| `Brim` | Extra outlines on the first layer so a thin support does not tip over |

New commands:

- **Draw support** — the per-layer drawing tool described above.
- **Support from overhangs** — seeds regions automatically from the points
  `compute_overhangs` already flags past the limit, so you can start from the
  analysis and then edit it by hand rather than starting from nothing.

`generate_toolpath` gains a support pass that emits `role="support"` paths, and
the existing machinery then applies without change: they get tool axes, they
get ordered, they are written out by every post-processor, and
`continuity_report` counts the extra travels.

## Acceptance criteria

- [ ] A support drawn on layer *n* appears on layer *n* and every layer below
      it, down to the build plate or to whatever it lands on.
- [ ] Drawing on a conical or conformal slice puts the stroke on the curved
      layer, not on a flat plane.
- [ ] Changing `LayerHeight` on the `Slices` object re-slices and the support
      regions survive.
- [ ] `Gap` leaves the set number of layers clear between the support and the
      part above it.
- [ ] Support paths export with role `support` and are visually distinct in the
      3D view.
- [ ] `Check toolpath` reports overhang for the part *after* supports, so a
      supported region stops being flagged.
- [ ] Headless tests: regions store and reload; taper shrinks per layer; gap is
      respected; supported overhangs report zero.

## Notes

Worth looking at how the existing pieces already fit:

- `roboprint/slicing.py` has `Contour`, `nest_contours` and `simplify_contour`,
  which is what a drawn region needs to be stored as.
- `roboprint/toolpath.py` has `infill_paths`, which already clips a pattern
  against an outline and lifts it onto the field — a support region is just
  another outline to fill.
- `roboprint/analysis.py` has `compute_overhangs`, which is where the automatic
  mode gets its seeds.
- `freeform/tracker.py` has the press-drag-release stroke capture, including
  snapping and live preview; the support tool wants the same tracker with the
  layer's field taking the place of the work plane.

Related: the workbench README's *Not covered* section — supports are the
largest gap, ahead of the kinematic simulation.
