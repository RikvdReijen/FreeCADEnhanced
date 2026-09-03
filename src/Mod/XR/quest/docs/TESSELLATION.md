<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Tessellation contract

`app/src/main/cpp/tessellate.cpp` is a direct port of the reference
tessellator in `xrenv/spec.py`. It mirrors it **triangle for triangle**: same
vertex order, same seam duplication, same UVs, same defaults, and the same
rejection of degenerate input. This file records what that means so the two
can be kept in step.

The C++ output is checked against the Python output element by element (see
"Cross-checking" below); at the time of writing the two agree exactly on
every primitive, with a maximum difference of 1e-6 attributable to printing
`float` at six decimals.

## Conventions

* Right handed, **Y up**, metres, matching OpenXR.
* Every primitive is centred on the node origin unless the §2 table says
  otherwise.
* `cylinder`, `cone`, `sphere` and `torus` are aligned with **+Y**.
* `plane`, `grid`, `honeycomb`, `extrusion` and `text` lie in the **XY plane**
  and grow along **+Z**.
* Triangles are **counter-clockwise seen from outside**; vertex normals point
  outward.
* UVs use the OpenGL convention, v = 0 at the bottom of the image.
* An environment's `bounds: [w, d, h]` means `x ∈ [-w/2, +w/2]`,
  `z ∈ [-d/2, +d/2]`, `y ∈ [0, h]`: the floor is at y = 0, not centred.
* An anchor's local **+Z is its surface normal** and its `size` spans local X
  and Y — FreeCAD's own placement convention.

## Defaults

| primitive   | defaults                                              |
|-------------|-------------------------------------------------------|
| `cylinder`  | `sides` 24, `caps` true                               |
| `cone`      | `radius` 0, `top_radius` 0, `sides` 24, `caps` true    |
| `sphere`    | `rings` 12, `sectors` 24                              |
| `torus`     | `sides` 12, `rings` 24                                |
| `tube`      | `sides` 12, `caps` true                               |
| `plane`     | `subdiv` [1, 1]                                       |
| `extrusion` | `closed` true                                         |
| `text`      | advance 0.78, glyph width 0.56, stroke 0.115, line pitch 1.4 |

Identity `translation` / `rotation` / `scale` are omitted from the generated
JSON, so readers must default them to (0,0,0), the identity quaternion and
(1,1,1). That omission is what takes the printer spec from 647 KB to 465 KB.

## Per primitive

* **box** — 24 vertices, one per face corner. Face order +X, -X, +Y, -Y, +Z,
  -Z; corners run (-u,-v) (+u,-v) (+u,+v) (-u,+v) with UV (0,0) (1,0) (1,1)
  (0,1); `cross(u, v) == normal`.
* **cone / cylinder** — a cylinder is a cone with equal radii. Each side is
  emitted as its own quad (four fresh vertices), so there is no shared ring.
  Side normals come from the profile tangent: `n = normalize((h·cosθ,
  r0 − r1, h·sinθ))`. A zero radius end becomes a triangle fan whose apex
  normal is the normalised mean of the two adjacent side normals. Caps are
  per-triangle fans: bottom `(centre, p_i, p_i+1)`, top `(centre, p_i+1, p_i)`.
* **sphere** — ring 0 is the +Y pole; `p = (r·sinφ·cosθ, r·cosφ, r·sinφ·sinθ)`,
  UV `(j/sectors, 1 − i/rings)`. Quads are `(i,j) (i,j+1) (i+1,j+1) (i+1,j)`,
  degenerating to one triangle at each pole.
* **torus** — main axis +Y. `n = (cosψ·cosφ, sinψ, cosψ·sinφ)`,
  `p = ((R + r·cosψ)·cosφ, r·sinψ, (R + r·cosψ)·sinφ)`, UV `(i/rings, j/sides)`.
* **tube** — a circle swept along the path with a rotation minimising frame
  (double reflection), `B = cross(T, N)`. **The frame is computed in double
  precision**: the reference decides a reflection is degenerate with a 1e-16
  threshold, which single precision can never reach, and getting that wrong
  rotates whole rings of the tube.
* **plane** — grid from (-sx/2, -sy/2), UV `(i/su, j/sv)`, normal +Z.
* **extrusion** — profile in XY swept from -h/2 to +h/2 along Z. A clockwise
  profile is reversed on entry so both windings give outward walls. Caps are
  ear-clipped (`triangulate_polygon`), front CCW and back reversed, with UV =
  the profile coordinate.
* **grid** — square section bars of `bar` thickness on a `pitch` lattice,
  extruded `bar` along Z. Counts are `floor(size / (2·pitch))` each way from
  the centre; `bar` must be smaller than `pitch`.
* **honeycomb** — pointy-top hexagons whose flat-to-flat width is `cell`,
  emitted as **de-duplicated wall boxes** (`ln + wall` long, `wall` wide,
  `height` deep), not as hollow prisms: prisms would put coincident faces
  between neighbouring cells and z-fight. Duplicate walls are detected by
  quantising the edge midpoint to `R/100` with round-half-to-even, as Python's
  `round()` does.
* **text** — the built-in stroke font, each segment extruded into a box. The
  glyph table is generated from `xrenv.spec._GLYPHS` by
  `tools/gen_glyphs.py`; run it (or `--check` it) whenever the Python font
  changes.
* **mesh** — positions/indices required, normals and UVs optional; missing
  normals are area weighted smooth normals.

## Where double precision is mandatory

Four primitives take `double` parameters rather than `float`, because their
loop bounds and containment tests sit on exact boundaries in the generated
specs, and rounding the inputs to `float` adds or drops whole features:
`tube`, `extrusion`, `grid` and `honeycomb`. `math3d.h` also carries a
double `kPiD` for the same reason — promoting the `float` pi into double
arithmetic injects a 1e-7 error that flips those boundary tests.

## Cross-checking

    # dump both implementations for the same shapes and diff them
    python3 -c "import sys; sys.path.insert(0,'src/Mod/XR'); \
        from xrenv.spec import tessellate_shape; ..."

The harness used during development feeds a JSON array of shapes to both
`xrenv.spec.tessellate_shape` and a small C++ driver around
`fcxr::tessellateShape`, then compares vertex counts, positions, normals, UVs
and indices in order. Any divergence in vertex *count* is a real
incompatibility; divergence below ~1e-5 in the values is float storage.
