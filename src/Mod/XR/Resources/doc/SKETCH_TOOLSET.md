# The VR sketch toolset (`xrsketch`)

Gravity-Sketch-style three-dimensional design for the FreeCAD XR workbench: you
grab the world with both hands to fly through it and resize it, draw curves in
the air, pull primitives out between your palms, and push a subdivision control
cage around with your fingertips. What comes out is committed into the FreeCAD
document as Draft/Part geometry, so the sketching stays fast and loose while the
result stays CAD.

```
src/Mod/XR/xrsketch/
├── vecmath.py      vectors, quaternions, the similarity Transform everything shares
├── bimanual.py     two-handed manipulation — the signature interaction
├── primitives.py   box / sphere / cylinder / cone / torus / plane / tube
├── subd.py         quad control cage, Catmull-Clark, the editing operations
├── curves.py       3D Bezier curve networks and freehand fitting
├── surfacing.py    loft, revolve, sweep, Coons patch, extrude
├── snapping.py     grid / vertex / midpoint / face / curve / angle / symmetry
├── scene.py        objects, nested layers, selection, arrays, undo
├── reference.py    image planes and the measuring tape
├── session.py      the mode controller (attach / update / events)
└── to_freecad.py   commit into the active document
```

Nothing in the package imports `FreeCAD`, `pivy.coin` or numpy at module scope
(ARCHITECTURE.md §6). numpy is not imported *anywhere*, at any time, which is
why the "with numpy" and "without numpy" results are not merely close but the
same code — `Tests/test_sketch_bimanual.py` asserts both facts.

--------------------------------------------------------------------------------
## 1. Interaction model

| Input | Meaning |
|---|---|
| **Grip**, one hand | grab: what you hold follows that hand, position and rotation |
| **Grip**, both hands | grab: translate, rotate *and* uniformly scale about the midpoint |
| **Grip** with nothing selected | the same gesture applied to the whole world |
| **Trigger** | the active tool |
| **Thumbstick X** | cycle the primitive kind |
| **Thumbstick Y** | scrub the snap grid pitch |

Tools: `SELECT`, `CURVE` (freehand), `PEN` (control points), `PRIMITIVE`,
`SUBD`, `MEASURE`.

### The two-handed gesture

A gesture is the similarity

```
G(p) = c_now + s · R · (p − c_start)
```

where `c` is the grab centroid (the hand position with one hand, the midpoint
with two), `R` the rotation carried by the hands — the axis between them plus
their averaged roll, so all three rotational degrees of freedom are captured —
and `s` the ratio of the hand separation. What is written back to the target is
`G ∘ M_base`, the gesture composed with the target's transform when the gesture
last started. Both are similarities, so the composition is exact and inverts
cleanly.

**Transitions do not pop.** When the second hand joins or leaves mid-gesture the
controller *re-baselines*: the current transform becomes the new `M_base` and the
current poses become the new anchors, so the gesture restarts at the identity and
the output is continuous by construction rather than by tuning.

**Tremor does not creep.** Each channel — translation, rotation angle, log scale
— has a *soft* dead zone: the magnitude has the dead zone subtracted instead of
being snapped to zero. A hand crossing the threshold does not jump, and a hand
wandering back inside it returns exactly to the baseline. Damping is a
first-order low pass with a time constant in seconds, applied to the output.

Translation, rotation and uniform scale are independently lockable, and the
*accumulated* scale is clamped to `[min_scale, max_scale]`.

Scale is deliberately uniform only. With two grab points the only well-posed
scale is the ratio of the hand separation, which is uniform; a non-uniform
gesture scale would make the composition of two gestures depend on their order.
Non-uniform size lives in the primitive parameters instead.

### Grabbing the world, and user scale

`xrsketch` does not have its own idea of how big the user is. `WorldGrab` feeds
the uniform part of the gesture into `xrenv.scale.ScaleController` — pulling the
hands apart makes the world bigger, which *is* making the user smaller, so the
user scale is multiplied by the gesture scale about the grab midpoint. The clip
planes, the smooth transition and the held pivot then come from the code that
already owns them. What the ScaleController cannot express, the rigid rotation
and translation of the world, is returned as a `Transform` for the viewer to
apply to the world root node.

--------------------------------------------------------------------------------
## 2. Units and coordinates

* The scene is in **metres**, Y up, matching OpenXR and ARCHITECTURE.md §2.
* Primitives follow the §2 axis conventions exactly: `cylinder`, `cone`,
  `sphere` and `torus` are **+Y aligned**, `plane` lies in XY and grows along
  +Z. `Primitive.shape_dict()` *is* an environment-spec shape dictionary, and
  the tessellation goes through `xrenv.spec.tessellate_shape` rather than being
  written a second time.
* `to_freecad.commit` multiplies by `scale` (1000 by default) to reach the
  document's millimetres.
* Object geometry is stored in object-local coordinates; the placement lives in
  `SketchObject.transform`, which is the same similarity a grab produces.

--------------------------------------------------------------------------------
## 3. Snapping

`SnapSettings.radius` is expressed in **metres of hand travel at 1:1**, not in
model units. Under miniaturisation the world is drawn `user_scale` times larger,
so a centimetre of hand movement covers `1/user_scale` centimetres of the model,
and the radius in model units is `radius / user_scale`. A user shrunk 12× to
stand on a build plate therefore snaps twelve times more finely — which is what
walking up to the detail is *for* — with no screen-space term anywhere.

Priority, strongest first:

```
vertex → curve_end → midpoint → face_center → tangent → symmetry → angle → grid
```

Ties inside a kind are broken by distance. Nothing in range returns a
`SnapResult` with `kind is None`, `snapped == False` and the point unchanged.

--------------------------------------------------------------------------------
## 4. Control cages (`subd.py`)

Faces are lists of vertex indices wound counter-clockwise seen from outside; the
half-edge view is derived on demand. `Cage.check()` verifies indices in range, no
repeated corner, every directed edge used at most once (winding consistency *and*
edge manifoldness), every twin mutual (no orphaned half-edge) and a single face
fan around every vertex (no bowties). Every editing operation is expected to
leave `check()` empty, and the test suite asserts it after each one.

### The limit surface

For an interior vertex of valence `n`:

```
limit = ( n(n−1)·P  +  2·Σ neighbours  +  4·Σ face centroids ) / ( n(n+5) )
```

This is the left eigenvector of the local subdivision matrix for eigenvalue 1, so
it is *exact*: applying it at any level of refinement gives the same point, and
iterated subdivision converges to it. The derivation assumes only that the faces
around the vertex form a single fan, so n-gons are fine. Boundary vertices use
the cubic B-spline limit `(P0 + 4P + P1) / 6` of the boundary polyline, matching
the `(P0 + 6P + P1) / 8` refinement rule. A cube of half-size 1 therefore has its
corner limit point at exactly `(±0.5, ±0.5, ±0.5)`, which the tests check against
five levels of refinement.

### Operations and UVs

UVs are optional and stored per face corner, so a seam is just two faces
disagreeing about a vertex.

| Operation | Effect on counts (`k`-gon, valence `n`) | UVs |
|---|---|---|
| `move_vertices` | — | **preserved** |
| `subdivide` | V+E+F vertices, one quad per corner | **preserved** (bilinear, per face, so seams survive) |
| `limit_points` / `limit_surface` | — | **preserved** |
| `loop_cut` | +1 vertex per ring edge, each ring quad splits in two | **preserved** (linear along the cut) |
| `mirror` | mirrored copy, seam vertices shared | **preserved** (mirrored copy of the source UVs) |
| `delete_faces` / `delete_vertices` | — | **preserved** on what remains |
| `extrude_face` | +k vertices, +k side faces, the cap keeps the index | **dropped** on the new faces |
| `inset_face` | +k vertices, +k border faces | **dropped** on the new faces |
| `bevel_edge` | endpoints split in two: +1 vertex per endpoint, +1 quad (+1 triangle per endpoint of valence > 3) | **dropped** on every face involved |
| `bridge_faces` | −2 faces, +k quads | **dropped** on the new faces |
| `merge_vertices` / `weld` | vertices merged, collapsed faces removed | **dropped** on the faces that changed |

There is no honest way to invent UVs for geometry that did not exist a moment
ago, so those operations set them to `None` and `uv_complete()` reports what is
missing, rather than fabricating a plausible-looking unwrap.

`bevel_edge` chamfers one interior edge into a quad. An endpoint of valence 3
lets the remaining face absorb both halves — it gains a corner, which is the
familiar cube chamfer with no extra triangles; a higher-valence endpoint gets a
small triangle closing the corner instead. Boundary edges are refused.

`mirror` welds the seam: vertices on the plane are shared rather than duplicated,
and the mirrored faces are wound the other way round, so the result stays a valid
manifold with no doubled vertices.

--------------------------------------------------------------------------------
## 5. Curves (`curves.py`)

Control points are anchors with two relative handles and a `corner` / `smooth` /
`symmetric` type — the vocabulary of `xrpaint.vector.Node`, lifted into three
dimensions.

**There is one Bezier implementation in this workbench and this is not it.**
Evaluation, the derivative, de Casteljau splitting and Catmull-Rom conversion are
affine in each coordinate, so a 3D call is two planar calls into
`xrpaint.curve` — one on `(x, y)`, one on `(z, 0)` — and is exact, not an
approximation. Arc length is the one quantity that does not decompose that way,
so it is measured by adaptive flattening.

Freehand strokes go through `xrpaint.curve.fit_curve`, Schneider's algorithm with
corner detection, applied *in the plane of the stroke*: the polyline is cut into
the longest runs that are planar within `plane_tol` (consecutive runs sharing
their boundary sample, so the chain is welded), and each run is fitted in its own
plane. A flat stroke — the common case, since people draw against a surface or a
reference plane — is a single planar fit with full corner detection; a genuinely
spatial stroke degrades into planar pieces rather than into a different
algorithm. `plane_tol` defaults to half the fitting tolerance so it is never the
dominant error.

`join`, `split`, `trim`, `mirror` and `project_to_plane` are exact.
`project_to_surface` samples, projects and refits, which is an approximation
whose accuracy is set by the sample count. `offset` delegates to
`xrpaint.curve.offset_path` inside the curve's own plane and **raises** for a
non-planar curve rather than returning a plausible wrong answer: offsetting a
spatial curve is only defined once you say which surface it should stay on, and
that is `project_to_surface`'s job.

--------------------------------------------------------------------------------
## 6. Surfaces (`surfacing.py`)

Every constructor returns a `SurfaceMesh`: a grid of points that can be
evaluated, ray-cast, turned into a control cage, or drawn. Degenerate input
raises `ValueError` — a loft with one section, a revolve about a zero axis or
through a zero angle, an extrusion along a zero vector, a two-rail sweep whose
rails touch, a boundary loop that does not close — instead of producing NaNs.

`to_part()` converts a surface into OCC geometry **only where the mapping is
faithful**:

| kind | `Part` counterpart |
|---|---|
| `extrude` | `BSplineCurve.toShape().extrude(v)` — exact |
| `revolve` | `shape.revolve(point, axis, angle)` — exact |
| `loft` | `Part.makeLoft(wires, ruled=True)` — matches the linear interpolation between sections used here |
| `coons` | **none** |
| `sweep`, `sweep2` | **none** |

A Coons patch and a two-rail sweep have no faithful `Part` equivalent: OCC would
fit a B-spline surface through the same boundary data, and that surface agrees on
the boundary and differs in the middle. Rather than hand back something that is
nearly right, `to_part` raises `UnsupportedMapping` and names the alternative,
`to_mesh_shape`, which is explicitly an approximation. The same honesty applies
to control cages: a Catmull-Clark limit surface is not a B-spline surface, so
`to_freecad` commits it as a mesh (or, on request, a polygon shell) and says so.

--------------------------------------------------------------------------------
## 7. Scene, layers and undo (`scene.py`)

Layers use the `xrpaint.layers` vocabulary — `name`, `visible`, `locked`,
`add_layer`, `remove_layer`, `move_layer`, `rename` — plus a colour and nesting.
Visibility and lock are **inherited**: a layer is visible only when it and every
ancestor is, and locked when it or any ancestor is, so hiding a parent hides a
branch without touching the children's own flags. Re-parenting refuses to build a
cycle.

Selection is single, additive, toggle, by layer (with or without nested layers),
box (fully contained or merely overlapping) and select-all; locked and hidden
objects are never selected. Groups make several objects select and move as one.

Arrays are linear, radial (a full circle divides the angle by the count so the
last copy does not land on the first; a partial sweep divides by `count − 1` so
the copies span the whole angle) and mirror. A mirror reflects the *geometry*,
not the placement, because a reflection is not a rotation and cannot live in a
similarity transform — that also keeps the face winding correct instead of
turning every mirrored surface inside out.

Undo is a snapshot stack: each entry stores the serialised scene before and after
the edit, so undo restores the state *exactly*, selection included. Sketch edits
are coarse — one gesture, one entry — so the memory cost is fine; the tile-based
pixel history in `xrpaint.layers` solves a different problem.

--------------------------------------------------------------------------------
## 8. Reference material (`reference.py`)

Image planes are blueprint/backdrop rectangles hung in space with an opacity and
a lock (they start **locked**, because you line one up once and then draw over
it). Nothing here decodes pixels; `source` is whatever the host needs to find
them.

Measurements are point-to-point distance, angle at a middle point, and a
polyline with a running total. **The readouts are true under miniaturisation.**
The chain is

```
viewer units → (÷ world_scale ÷ unit_scale) → environment metres → (÷ model_scale) → document mm
```

so a user standing twelve times shrunk on a build plate, whose hands are 1.2 m
apart in the headset, reads **100 mm** — the real size of the part — and not
1200 mm. Measurements store document units, so the number does not change when
the user grows or shrinks afterwards.

--------------------------------------------------------------------------------
## 9. What is deliberately not here

Gravity Sketch is a much larger program than a workbench module, and some of what
it does either belongs elsewhere in FreeCAD or would be wrong to fake:

* **Sub-D sculpting brushes** (grab, smooth, inflate on a dense mesh) — that is
  `xrsculpt`'s subject, not this one. `xrsketch` moves *cage* vertices;
  sculpting moves surface vertices.
* **Painting, texturing and materials** — already `xrpaint`.
* **Non-uniform two-handed scale.** Not well posed from two grab points; see §1.
* **Automatic surfacing of an arbitrary curve network** (the "auto-patch" that
  guesses which loops should become faces). The patch constructors here take an
  explicit, ordered boundary; guessing is a research problem and a silent
  guess in a CAD tool is worse than a menu.
* **NURBS control-point editing with weights.** Curves are cubic Beziers,
  matching the vector editor and mapping onto Draft's `BezCurve`; rational
  weights would map onto nothing the rest of the workbench understands.
* **Booleans on cages.** Doing them well needs a robust solid kernel; OCC has
  one, and the right place to use it is on the committed `Part` geometry, not on
  a control cage in the headset.
* **Collaborative multi-user editing.** The sync protocol (ARCHITECTURE.md §3)
  moves whole scenes, not per-edit deltas.
* **Rendering.** `xrsketch` produces geometry and evaluable meshes; drawing them
  is the viewer's job (see §10).

--------------------------------------------------------------------------------
## 10. Integration required in `xrcore`

Everything above is testable without a headset, but nothing in `xrsketch`
reaches into the viewer. These are the hooks it needs, mirroring
`xrcore/paint_bridge.py` exactly:

**1. A new module `xrcore/sketch_bridge.py`** with the same shape as
`paint_bridge`:

```python
get_session() / ensure_session()   -> xrsketch.session.SketchSession
attach(widget, sketch_root)        -> session.attach_scenegraph(sketch_root)
                                      session.bind_viewer(widget)
                                      session.bind_scale(
                                          environment_bridge.manager().controller)
detach()                           -> session.detach()
activate_tool(name)                -> session.set_tool(name)   # SELECT, CURVE,
                                      # PEN, PRIMITIVE, SUBD, MEASURE
deactivate()                       -> session.cancel_all()
handle_frame(dt, controllers)      -> session.update(dt, controllers)  # True when
                                      # the sketch tools consumed the input
commit_sketch(document=None)       -> session.commit_to_document(document)
undo() / redo()                    -> session.undo() / session.redo()
```

**2. `xrcore/commonXR.py`** — three call sites, next to the existing paint ones:

* a `self.sketch_separator = SoSeparator()` under the world root (where
  `paint_separator` is created), so drawn geometry has somewhere to live;
* in `attach_extensions()`: `sketch_bridge.attach(self, self.sketch_separator)`;
* in `detach_extensions()`: `sketch_bridge.detach()`;
* in `update_extensions()`: `consumed = bool(sketch_bridge.handle_frame(dt, self.xr_con))`
  in the same chain as `paint_bridge`/`sculpt_bridge`, returning early when it is
  true.

`handle_frame` must be given the controller list **in a stable hand order**
(index 0 left, 1 right); the bimanual controller keys its anchors on that index.

**3. `xrcore/commands.py` and `xrcore/menu_ext.py`** — commands calling
`sketch_bridge.activate_tool(...)` for the six tools, plus **Commit sketch**
(`sketch_bridge.commit_sketch()`) and **Undo/Redo sketch step**. The in-VR menu
entries follow the pattern already used for the paint and sculpt modes.

**4. Rendering the sketch scene.** `xrsketch` deliberately builds no Coin nodes.
The viewer needs a small renderer that walks `session.scene` once per changed
frame and rebuilds the separator:

* `Curve3D.flatten(tol)` → an `SoLineSet`;
* `Primitive.mesh()` → positions/normals/uvs/indices, ready for an
  `SoIndexedFaceSet` (identical in layout to what `xrenv.spec` already feeds the
  environment builder, so that code can be reused verbatim);
* `Cage.limit_surface(level).triangles()` → an `SoIndexedFaceSet`, plus the cage
  itself as an `SoLineSet` overlay;
* `SurfaceMesh.points()` / `.quads()` → an `SoIndexedFaceSet`;
* `ImagePlane.corners()` → a textured quad with `opacity`;
* `Measurement.points` / `.labels()` → a polyline and a text billboard.

`session.changed` is set on every frame that altered anything, and
`session.drain_events()` returns the discrete events (`curve`, `primitive`,
`select`, `grab_begin`, `grab_end`, `measure_point`, `undo`, `redo`, …) if the
viewer would rather update incrementally than rebuild.

**5. The world grab.** `WorldGrab.update()` returns `(user_scale, rigid)`. The
scale half has already been applied to the `ScaleController`, so
`environment_bridge` needs nothing new for it; the `rigid` half is a
`Transform` (translation + rotation, scale 1) that the viewer should apply to
the world root **outside** `environment_bridge.scale_transform`, so the two do
not fight over the same node.

**6. Preferences** (optional, follows `xrpaint.prefs`): `SketchGridSize`,
`SketchSnapRadius`, `SketchFitError`, `SketchGrabDamping`,
`SketchSubdivisionLevel`.

--------------------------------------------------------------------------------
## 11. Tests

```sh
cd src/Mod/XR && python3 -m unittest discover -s Tests -t .
```

`Tests/test_sketch_bimanual.py`, `test_sketch_subd.py`,
`test_sketch_surfacing.py`, `test_sketch_scene.py` and
`test_sketch_snapping.py`. No FreeCAD, no GPU, no numpy; the document commit
runs against `Tests/stubs.py` plus a recording document.
