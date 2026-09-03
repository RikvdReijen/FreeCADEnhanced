# VR mesh sculpting — `xrsculpt`

`xrsculpt` is the sculpting subsystem of the XR workbench: an editable triangle
mesh, a set of sculpt brushes driven by the VR controllers, symmetry, vertex
masking, adaptive topology — and, at the centre of it, a **sculpt layer stack**.

Every stroke accumulates into a named, independently weighted layer. A pass can
afterwards be dialled back, muted, re-ordered, duplicated, inverted, merged or
baked, and the strokes underneath it are still there, unchanged and re-editable.
That is the feature everything else in the package is arranged around.

The package follows the conventions of its sibling `xrpaint` throughout:
parameters object plus preset table, a stroke resampler that turns jerky
controller poses into evenly spaced stamps, a layer stack with bounded undo, a
mode-controller session with a plain-Python event API, and lazy FreeCAD imports
so the whole thing is unit-testable without FreeCAD (ARCHITECTURE.md §6).

```
xrsculpt/
├── __init__.py    public API and __all__
├── mesh.py        SculptMesh: buffers, normals, adjacency, spatial index, dirty regions
├── layers.py      SculptLayer, LayerStack, History  ← the centrepiece
├── brushes.py     eleven brushes, six falloffs, pressure, the stroke resampler
├── symmetry.py    mirror X/Y/Z and radial, applied at stroke level
├── masking.py     VertexMask: paint, invert, blur, cavity, freeze
├── topology.py    subdivision, decimation, remeshing, and the vertex remap
├── session.py     SculptSession: the mode controller the XR loop drives
├── io.py          FCSL container + the FCXR `sculpt` manifest section
└── prefs.py       lazy FreeCAD preference lookup
```

--------------------------------------------------------------------------------

## 1. The layer stack

### What a layer stores, and why

A layer is a **sparse map from vertex index to a full vector offset**
`(dx, dy, dz)` in object space. Not a scalar displacement along a stored normal,
which is the obvious alternative — one float instead of three, and what a ZBrush
*morph* target does. Three reasons it was rejected:

1. **It cannot express half the brushes.** Grab, snake hook, pinch, scrape,
   smooth and flatten all move vertices *tangentially*. A scalar-along-normal
   layer silently drops the tangential component, so a grab pass recorded into
   one replays as a different shape from the one that was sculpted. A layer you
   cannot trust to replay what you did is not a layer.
2. **The normal it is "along" goes stale.** The whole point of storing
   displacement rather than positions is that it can be re-evaluated after the
   layers *below* it change. The moment a lower layer moves the surface, the
   stored normal is no longer the surface normal — so the layer either drifts
   (re-derive the normal each evaluation, and the same slider value gives a
   different shape depending on what else is enabled) or is not actually along
   the normal any more (keep the old one). Either way the name is a lie.
3. **Order independence falls out for free.** Evaluation becomes
   `base + Σ wᵢ·offsetᵢ` — a plain weighted sum. Addition commutes, so
   reordering additive layers cannot change the shape; changing a weight is
   exactly linear; setting a weight back restores the previous positions bit for
   bit. None of that survives a per-layer renormalisation step.

The cost is three doubles per touched vertex instead of one, and because storage
is sparse that is a cost on the vertices a stroke actually touched. **A layer
covering 500 vertices of a 200 000-vertex mesh holds 500 entries and about
14 kB, whatever the mesh size.** Storage is two parallel `array` buffers
(`array('i')` indices, `array('d')` offsets) plus a dict from vertex index to
slot: O(1) lookup, insert and update; nothing ever proportional to `V`.

### Evaluation

```
displacement(v) = accumulate over layers, bottom to top:
    add      →  acc += weight · offset[v]
    replace  →  acc  = weight · offset[v]        (on the vertices it touches)
position(v)  = base(v) + displacement(v)
```

* **Effective weight** is `weight if visible else 0.0`. Hiding a layer is
  therefore *exactly* equivalent to setting its weight to zero, and both restore
  the mesh underneath bit for bit.
* Weights are deliberately **unclamped**: negative inverts the pass, above one
  exaggerates it.
* `add` is what every brush writes and what merge/bake are defined over.
  `replace` is an override pass — "this region is exactly this shape, whatever
  is underneath" — order-sensitive by definition, still deterministic.

**Determinism.** Contributions are summed in stack order, one per layer per
vertex, so the result is a function of the layer contents and their order and
nothing else — in particular not of the order the strokes were made in.
Evaluating the same stack twice gives byte-identical output. The one honest
caveat: floating point addition is not associative, so *reordering* additive
layers can move the last bit even though the mathematics is invariant. The tests
assert bit equality for repeated evaluation and 12-decimal equality across a
reorder, which is the strongest true statement available.

### Operations

| operation | exactness |
|---|---|
| add / remove / rename / reorder / duplicate | structural, fully undoable |
| weight, visibility, lock, blend | live re-evaluation, exactly reversible |
| invert, clear | in place on the sparse entries |
| **merge down** | the merged layer evaluates identically to the pair; only defined for `add` layers (a `replace` layer overrides everything below it, not just its neighbour, so no pairwise merge is equivalent in general — the call raises `ValueError` rather than quietly producing a different shape) |
| **flatten** | same, over the whole stack |
| **bake to base** | folds the stack into the base positions; evaluating afterwards gives the same mesh. `remove=False` keeps the layers as empty shells so their names, weights and order survive |

### Undo

`History` stores **sparse deltas**, never a mesh copy. A stroke opens an entry,
snapshots the before-offsets of the vertices it is about to touch
(de-duplicated within the entry), and on commit captures the after-offsets:
one `array('d')` per side plus the layer id. A 500-vertex dab costs about 12 kB.
Structural operations snapshot whole layers, which are sparse too, and `bake`
additionally snapshots the base positions because that is the one operation that
changes them. The stack is bounded by both an entry count (`SculptUndoSteps`,
default 64) and a byte budget (32 MB).

One VR stroke — symmetry, spacing and all — is exactly one undo entry and lands
in exactly one layer.

--------------------------------------------------------------------------------

## 2. Mesh, brushes, symmetry, masks, topology

### `mesh.py`

`SculptMesh` is a flat `array('d')` of positions and `array('i')` of triangle
indices, plus four lazily built, individually invalidated derived structures:
per-vertex normals, one-ring adjacency (CSR), vertex-to-face incidence (CSR),
and a uniform spatial hash grid. Conversions to and from FreeCAD `Mesh` and
`Part` shapes import `Mesh`/`Part` inside the function, never at module scope.

Two things make it survive a large mesh:

* **The normal cache is refreshed, not discarded.** A dab moves `k` vertices, so
  only those and their one-rings can have changed a normal. The refresh is
  `O(k · valence)` instead of `O(V + F)`. Because the vertex-face table is built
  by scanning the face list in order, a vertex accumulates its incident face
  normals in the same order either way, so the incremental result is *identical*
  to a full rebuild, not merely close. (Measured on a 163 842-vertex icosphere:
  0.40 ms per refresh against 686 ms for a full rebuild.)
* **The grid is rebuilt on drift, not on movement.** It survives small
  movements and is rebuilt only once the accumulated displacement can change
  which cell a vertex lives in — tens of dabs apart, not every dab. It is a
  hash, not a bounded lattice, so a vertex dragged far outside the original
  extent still lands in a valid cell instead of piling into a border one.

### `brushes.py`

Eleven brushes: `draw`, `inflate`, `clay` (and the `clay_strips` preset),
`flatten`, `scrape`, `pinch` (with `contrast` as its inverted preset), `smooth`
(with a volume-preserving `polish` preset), `grab`, `snake_hook`, `crease`,
`erase`. Six falloff curves — `smooth`, `sphere`, `root`, `sharp`, `linear`,
`constant` — each bounded by `f(0)=1`, `f(1)=0` and monotonically non-increasing
in between.

Units are chosen so a brush behaves the same on a 2 mm detail and a 2 m body:
the material brushes displace by `strength · falloff · radius`, and the
move-towards-a-target brushes (flatten, scrape, pinch, smooth, erase) move
`strength · falloff` of the way there — a convex combination, so with
`strength ≤ 1` nothing can overshoot and smoothing converges monotonically
instead of ringing.

`StrokeSampler` is the 3D counterpart of `xrpaint.brush.StrokeSampler`: it walks
the segment between two controller poses emitting a dab every
`spacing · 2 · radius` metres and carries the leftover distance across frames,
so a sweep that jumps twenty radii in one frame still lays an evenly spaced
trail. `erase` is the one brush that reads the layer rather than the surface: it
scales the active layer's stored offsets towards zero.

### `symmetry.py`

Symmetry is applied at **stroke** level: one dab in becomes `2ⁿ · radial` dabs
out, all applied to the same layer in the same pass. A symmetric sculpt is
therefore still a single, dial-back-able pass, and a mirrored stroke cannot
drift out of sync with its original.

Mirroring is exact: it negates one component of the dab centre, normal and
direction and changes nothing else, so a mesh whose vertices are themselves
exact mirror images receives exactly negated displacements — the test asserts
equality, not closeness. Vertices *on* a mirror plane are the one case needing a
tolerance: they fall inside both dabs, and the two near-opposite moves would
leave a hairline seam. `Symmetry.constrain` zeroes the across-plane component of
the stored offset for every vertex whose *base* position is within tolerance of
the plane, so seam vertices slide along the plane and never off it. Radial
symmetry is a real rotation, so radial copies are accurate rather than bit-exact.

### `masking.py`

One float per vertex, `0` free … `1` protected. The brushes call exactly one
method — `factor(i)` — so it is a plain array lookup and two comparisons.
`freeze` is the hard version: any vertex above the threshold returns exactly
zero rather than `1 − m`, so a half-painted mask stops a brush dead instead of
bleeding through at half strength. Paint (with falloff), invert, blur
(double-buffered, so the result does not depend on vertex order), sharpen, clear,
and mask-by-cavity, which measures the signed distance to the one-ring centroid
along the vertex normal and normalises it by the mean edge length.

Storage is dense (800 kB for 200k vertices) on purpose: it is read once per
candidate vertex per dab, and a dict lookup there costs more than the memory
saves.

### `topology.py`

Subdivision is **conforming**: an edge is split for both incident triangles or
for neither, and each triangle is re-triangulated by how many of its edges were
split (1 → 2, 2 → 3, 3 → 4 triangles). No T-junctions, so the mesh stays
manifold, keeps its winding, and gains no holes. Decimation collapses short
edges shortest-first, guarded by the **link condition** (the endpoints may share
exactly as many neighbours as they have common faces — violating it pinches the
surface into a non-manifold vertex) and by never collapsing boundary vertices,
so a sheet keeps exactly the holes it had. `remesh` is the standard
split-long/collapse-short pair with a `[4/5, 4/3] · target` fixed point, and
takes an optional `(center, radius)` region so detail can be added under the
brush only.

Every operation returns a **new** mesh plus a `TopologyMap`, and nothing is done
in place: a layer is indexed by vertex, and a topology change that quietly
renumbered the vertices would scramble every layer on the stack. The map carries
each new vertex's parents, and `remap_stack` / `remap_layer` / `remap_mask` /
`remap_positions` carry the base positions, the layers and the mask across.
Because a midpoint's displacement is the mean of its parents' displacements, a
subdivided sculpt evaluates to exactly the same surface through the old
vertices — the test asserts it to 12 decimals.

**UVs.** Nothing in `topology.py` preserves UVs, and the module says so in its
docstring rather than pretending otherwise:

* subdivision *could* carry them — every new vertex is the midpoint of exactly
  two old ones, so linear interpolation is correct except across a UV seam,
  where it is wrong in the usual visible way. `SculptMesh` has no UV channel
  yet, so the question is deferred; when one is added, midpoint interpolation
  plus explicit seam handling is the thing to write.
* collapse and remesh **cannot**, in any meaningful sense: a collapse merges two
  vertices that may carry different UVs, and remeshing invents vertices with no
  correspondence in the original parameterisation. A remeshed mesh needs its
  texture re-projected; there is no fixing it up afterwards.

A topology change also clears the undo history, because the entries below it
index vertices that no longer exist.

### `session.py`

`SculptSession` mirrors `xrpaint.session.PaintSession` deliberately closely, so
the two are learned once: `attach_scenegraph` / `bind_viewer` / `detach`,
`update(dt, controllers)` as the per-frame front door, and `on_trigger`,
`on_move`, `on_grip`, `on_thumbstick` as the plain-Python one that takes nothing
but numbers. Modes are `SCULPT` and `MASK`. A `SculptTarget` bundles the
evaluated mesh, the layer stack (whose `base` holds the unsculpted positions),
the mask, the symmetry and the history for one FreeCAD object.

--------------------------------------------------------------------------------

## 3. Serialisation

### FCSL v1 — the self-contained blob

`io.dumps` / `io.loads` produce a small chunked container built the same way as
FCXR (§1 of ARCHITECTURE.md): a fixed header, a JSON directory, then one binary
payload every entry indexes into.

```
Header (12 bytes, little endian)
  uint8[4] magic   = 'F','C','S','L'
  uint32   version = 1
  uint32   json_length

JSON directory (json_length bytes, UTF-8, sorted keys, no whitespace)
Payload        (the rest of the file; zlib deflated when
                header["compression"] == "zlib")
```

```jsonc
{
  "version": 1,
  "vertex_count": 200000,
  "active": 1,
  "fc_name": "Body",                    // optional
  "compression": "zlib" | "none",
  "uncompressed_length": 12345,
  "base":   { "offset": 0, "length": 4800000 } | null,   // float64 xyz
  "mask":   { "offset": .., "length": .., "freeze": false,
              "freeze_threshold": 0.5 } | null,          // uint8 per vertex
  "symmetry": { "axes": [true,false,false], "origin": [0,0,0],
                "tolerance": 1e-6, "radial": 0,
                "radial_axis": "Y" } | null,
  "layers": [ { "name": "Pass 1", "weight": 1.0, "visible": true,
                "locked": false, "blend": "add" | "replace",
                "count": 500,
                "indices": { "offset": .., "length": .. },   // int32
                "offsets": { "offset": .., "length": .. } } ] // float64 xyz
}
```

Layer entries are written in ascending vertex order and the JSON has sorted keys
and no whitespace, so identical stacks produce identical bytes. `dumps_base64`
wraps it for a FreeCAD string property, which is how a sculpt survives a
document save.

### Proposed FCXR manifest section — `manifest["sculpt"]`

*(Proposed here, per the brief; `ARCHITECTURE.md` §4 is not edited by this
change. `xrsync.fcxr.validate_manifest` already ignores unknown top-level keys,
so a reader that does not know about `sculpt` still loads the file.)*

```jsonc
"sculpt": {
  "version": 1,
  "targets": [ {
    "fc_name": "Body",
    "vertex_count": 200000,
    "active": 1,
    "encoding": "fcsl1" | "f32",
    "symmetry": { ... as above ... },          // optional

    // encoding == "fcsl1"  (lossless, the default)
    "blob": 7,                                 // U8/SCALAR accessor: an FCSL v1 payload
    "layers": [ { "name": "Pass 1", "weight": 1.0, "visible": true,
                  "locked": false, "blend": "add", "count": 500 } ],
                                               // metadata only, for a reader
                                               // that wants a layer list
                                               // without inflating the blob

    // encoding == "f32"  (interoperable, lossy)
    "base": 4,                                 // VEC3/F32 accessor or null
    "mask": 6,                                 // SCALAR/U8 accessor or null
    "mask_freeze": false,
    "layers": [ { "name": "Pass 1", "weight": 1.0, "visible": true,
                  "locked": false, "blend": "add", "count": 500,
                  "indices": 8,                // SCALAR/U32 accessor or null
                  "offsets": 9 } ]             // VEC3/F32 accessor or null
  } ]
}
```

**Why `fcsl1` is the default.** A layer is an *edit*, and an edit that does not
come back exactly is a layer whose weight slider no longer returns the mesh to
where it was. `F32` would quantise every offset on every save, so the round trip
would be lossy in a way the user can see after a few passes. The FCXR accessor
vocabulary has no 64-bit float (§1: `F32`, `U32`, `U16`, `U8`), so rather than
extend it, the lossless payload rides in a byte accessor and the manifest says
so. `f32` remains available and explicitly marked for readers that want the
plain form — the Quest app, a debugger, a converter.

Both encodings keep everything bulky in the single `BIN` chunk behind accessors
and everything human-interesting in the JSON, exactly as §1 requires, and both
are deterministic so `content_hash` stays meaningful for the §3 change polling.

--------------------------------------------------------------------------------

## 4. Measured performance

Pure Python 3.11, one core, no GPU. Icosphere with **163 842 vertices / 327 680
faces** unless stated. numpy present or absent gives byte-identical results
throughout (verified by hashing the evaluated positions in both configurations).

| operation | time |
|---|---|
| one-time: build adjacency + vertex-face CSR | 0.99 s |
| one-time: spatial grid build | 0.63 s |
| one-time: full normal rebuild | 0.69 s |
| **radius query** (r = 0.03, returns 37 verts) | **31 µs** |
| the same query, brute force `O(V)` | 42 ms (≈ 1 350× slower) |
| **one brush dab** (draw, 37 verts) | **1.5 ms** |
| **incremental normal refresh after a dab** | **0.40 ms** (vs 686 ms rebuilding) |
| partial stack re-evaluation after a dab | < 0.1 ms |
| serialise one 37-vertex layer (FCSL, zlib, no base) | < 0.1 ms, 575 bytes |
| adaptive subdivision under a brush (r = 0.1) | 1.3 s, 163 842 → 165 104 verts |

Per-stroke behaviour is what matters in VR, and every per-dab cost above is
proportional to the vertices under the cursor, not to the mesh. At 1.5 ms a dab
and a 72 Hz frame budget of 13.9 ms, a stroke can lay down eight or nine dabs
per frame on a 160k mesh — comfortably more than the sampler emits at normal
controller speeds.

The three one-time costs are paid once per topology, not per stroke, and the
grid additionally rebuilds when the accumulated drift exceeds half a cell. The
one operation that stays `O(V + F)` per call is a topology change, which is a
deliberate user action rather than something a stroke triggers.

**numpy** is optional and used in exactly one place — evaluating the layer stack
when it is dense — where the vectorised form performs the same float64
multiply-add per vertex in the same order and is therefore bit-identical. On a
41k-vertex mesh with three full-mesh layers it is ~3× faster (30 ms → 15 ms);
for a sparse stack it is an order of magnitude *slower*, because it rebuilds the
whole array, so `LayerStack._prefer_numpy` takes the scalar path below a quarter
of the mesh. Normals stay scalar unconditionally: a vectorised accumulation sums
the per-face normals in a different order and lands one ULP away, and a normal
that depended on whether numpy happens to be installed would make every brush
result depend on it too.

Sparse storage, in numbers: a 500-vertex layer on a 200 000-vertex mesh is 500
entries, 14 000 bytes in memory, and 12 594 bytes serialised (FCSL, zlib,
header included)
— against 4.8 MB for a dense float64 layer or 9.6 MB for a mesh copy per undo
step.

--------------------------------------------------------------------------------

## 5. Tests

```
cd src/Mod/XR && python3 -m unittest discover -s Tests -t .
```

* `Tests/test_sculpt_layers.py` (78 tests) — sparse storage really is sparse;
  weights are linear and reversible; muting restores the mesh exactly;
  reordering is deterministic; merge-down, flatten and bake all agree with
  evaluating the stack; undo/redo restores vertices exactly, including
  structural operations; serialise/deserialise is bit-identical; the FCXR
  section round trips in both encodings.
* `Tests/test_sculpt.py` (130 tests) — every brush moves the vertices it should
  and leaves masked and frozen ones untouched; falloff curves are monotonic and
  bounded; smoothing converges and does not blow up; a fast sweep still deposits
  evenly spaced dabs; symmetry is mirror-exact; subdivision adds vertices while
  preserving manifoldness, the boundary and the Euler characteristic, and
  decimation reduces them while staying manifold; spatial index queries agree
  with brute force; incremental normals equal a full rebuild.

Both files run with neither FreeCAD nor numpy installed, and are checked in both
configurations.

--------------------------------------------------------------------------------

## 6. Integration required in `xrcore` (and two files outside it)

`xrsculpt` is self-contained and driven entirely through `SculptSession`. The
hooks below are what the workbench needs to add; nothing in `xrsculpt` has to
change to accept them.

### 6.1 `xrcore/service.py` — session slot

Add a sculpt session slot alongside the paint one:

```python
def get_sculpt_session(): ...
def set_sculpt_session(session): ...
```

### 6.2 `xrcore/sculpt_bridge.py` — new module, mirroring `paint_bridge.py`

```python
ensure_session()                  -> xrsculpt.session.SculptSession
get_session()                     -> SculptSession | None
attach(widget, sculpt_root)       # session.attach_scenegraph(root); session.bind_viewer(widget)
detach()                          # session.detach()
activate_mode(mode)               # mode in ("SCULPT", "MASK")
deactivate()                      # session.set_mode(None)
handle_frame(dt, controllers)     -> bool     # session.update(dt, controllers)
add_target(obj)                   # session.add_target_object(obj)  (tessellates a Part shape)
commit_to_document(document=None) # write the evaluated mesh back to the Mesh::Feature
sculpt_manifest(writer)           # session.export_sculpt_manifest(writer)
apply_remote_sculpt(document, section)  # session.import_sculpt_manifest(...)
store_on_object(obj)              # obj.<StringProperty> = xrsculpt.io.dumps_base64(...)
load_from_object(obj)             # session.import_bytes(base64.b64decode(...))
```

`commit_to_document` wants the same `openTransaction` / `commitTransaction` /
`recompute` shape as `paint_bridge.commit_vector_document`, and should call
`SculptMesh.write_back(obj)` (which builds a `Mesh.Mesh` lazily).

### 6.3 `xrcore/commonXR.py` — the render loop

Two calls, next to the existing paint ones:

* after the paint scenegraph is built:
  `sculpt_bridge.attach(self, <an SoSeparator under the scene root>)`;
* once per frame, after the controller poses are updated and next to
  `paint_bridge.handle_frame(dt, controllers)`:
  `sculpt_bridge.handle_frame(dt, [left_controller, right_controller])`.

The session needs nothing else from the loop. It reads the controllers through
`get_buttons_states()`, `get_global_transf()`, `find_ray_axis()`,
`find_picked_coin_object(root, vp_reg, near, far, camera)` and
`get_picked_normal()` — all already on `xrcore.controllerXR.xrController` — and
every one of them is called inside a `try`, so a controller that lacks any of
them degrades to using the controller position rather than raising.

`bind_viewer(widget)` picks up `vp_reg`/`viewport_region` and `camera` off the
widget by name, exactly as `PaintSession.bind_viewer` does, so no new attribute
is needed on the viewer.

### 6.4 `xrcore/commands.py` and `InitGui.py` — desktop commands

`XR_SculptMode`, `XR_SculptMaskMode`, `XR_SculptBake`, `XR_SculptSubdivide`,
`XR_SculptRemesh`, `XR_SculptLayerPanel`, `XR_SculptCommit`, each a thin wrapper
over `sculpt_bridge`, registered in the workbench toolbar/menu next to the paint
commands. Icons under `Resources/icons/`.

### 6.5 `xrcore/menuCoin.py` / `menu_ext.py` — in-VR menu

Widgets for: brush preset (the eleven names in `xrsculpt.brushes.PRESETS`),
radius, strength, falloff, invert; symmetry X/Y/Z toggles and the radial count;
mask paint/invert/blur/clear/freeze; and the layer panel — add, remove, rename,
weight slider, visibility, lock, reorder, duplicate, merge down, bake. The
session already exposes one method per item, so the menu handler is a dispatch
table, not logic.

### 6.6 `xrcore/preferences_xr.py` and `Resources/XRPreferences.ui`

The keys in `xrsculpt/prefs.py`, all under the existing
`User parameter:BaseApp/Preferences/Mod/XR` group: `SculptBrush`,
`SculptRadius`, `SculptStrength`, `SculptFalloff`, `SculptSpacing`,
`SculptPressureEnabled`, `SculptSymmetryX/Y/Z`, `SculptSymmetryTolerance`,
`SculptUndoSteps`, `SculptTessellation`, `SculptDynamicDetail`,
`SculptTargetEdge`.

### 6.7 Outside `xrcore`

* **`xrsync/fcxr.py`** — `FcxrWriter.set_sculpt(dict)` plus emitting
  `manifest["sculpt"]` from `build_manifest`, an `FcxrDocument.sculpt` property,
  and a `validate_sculpt` in the same style as `validate_paint` (check
  `version == 1`, that every accessor index is in range and has the type this
  document says: `SCALAR`/`U8` for `blob` and `mask`, `SCALAR`/`U32` for layer
  `indices`, `VEC3`/`F32` for `offsets` and `base`). `xrsculpt.io.sculpt_section`
  already builds the dict from anything with `add_accessor`, and
  `read_sculpt_section` reads it back from anything with `read_accessor`, so the
  two sides only need wiring. Optionally `POST /api/v1/sculpt` in
  `xrsync/server.py`, mirroring `/api/v1/paint`.
* **`src/Mod/XR/CMakeLists.txt`** — one line, since the package is not covered by
  the existing globs:
  ```cmake
  FILE(GLOB XR_Sculpt_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrsculpt/*.py)
  ```
  and `${XR_Sculpt_SRCS}` added to `all_files`. (`Resources/doc/*.md` is already
  globbed, so this document needs nothing.)
* **`Resources/doc/ARCHITECTURE.md`** — §4 gains the `sculpt` section proposed in
  §3 above, and the package list at the top gains `xrsculpt/`.
