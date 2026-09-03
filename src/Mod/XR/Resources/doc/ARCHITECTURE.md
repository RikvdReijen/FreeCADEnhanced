# FreeCAD XR — Architecture and interface contracts

This document is the single source of truth for the interfaces shared between the
subsystems of the XR workbench. Every module below must conform to it.

The workbench is installed to `Mod/XR` which is on `sys.path`, so the top level
Python packages are imported as `xrcore`, `xrenv`, `xrpaint`, `xrsync`.
(The package must *not* be called `xr` — that name belongs to `pyopenxr`.)

```
src/Mod/XR/
├── Init.py, InitGui.py        workbench registration
├── xrcore/                    OpenXR session, Coin scenegraph, controllers, menus
├── xrenv/                     environment switcher + miniaturisation
├── xrpaint/                   VR texture painting + vector editor
├── xrsync/                    scene export, LAN sync server, Google Drive
├── Resources/environments/    generated declarative environment specs (JSON)
└── quest/                     standalone Meta Quest 3 APK (OpenXR + GLES3)
```

--------------------------------------------------------------------------------
## 1. FCXR container format (v1)

`.fcxr` is the portable scene package moved between desktop FreeCAD, the Quest
headset, the LAN sync server and Google Drive. It is a GLB-style chunked binary
so it can be parsed with zero third-party dependencies on both ends.

```
Header (12 bytes, little endian)
  uint8[4] magic   = 'F','C','X','R'
  uint32   version = 1
  uint32   total_length      (whole file, including this header)

Chunk (repeated until total_length)
  uint32   payload_length    (not including this 8 byte chunk header)
  uint8[4] type              'JSON' | 'BIN\0' | 'PNG\0'
  uint8[payload_length] payload
  padding to a 4 byte boundary ( 0x20 for JSON, 0x00 for binary )
```

* Exactly one `JSON` chunk, first. UTF-8 encoded manifest (schema below).
* Zero or one `BIN\0` chunk — the single binary buffer all accessors index into.
* Zero or more `PNG\0` chunks — textures, in manifest `images` order.

**`payload_length` excludes the padding.** This is where FCXR parts company
with GLB, which counts padding in the length. Readers take `payload_length`
bytes and then advance to the next 4-byte boundary before reading the following
chunk header; a reader that assumes GLB semantics desynchronises on the second
chunk. Writers emit the unpadded length. (`xrsync/fcxr.py` also *accepts* the
padded form when reading, so files from a lenient writer still load.)

### Manifest schema

```jsonc
{
  "asset":    { "generator": "FreeCAD-XR 1.0", "version": 1,
                "unit_scale": 0.001,              // document units -> metres
                "created": "2026-09-03T10:00:00Z",
                "source_document": "Part.FCStd" },
  "scene":    { "root": 0, "environment": "bambu_x1c", "user_scale": 12.0 },
  "nodes":  [ { "name": "Body",
                "mesh": 0,                        // index or null
                "translation": [x,y,z],           // metres
                "rotation":    [x,y,z,w],         // quaternion
                "scale":       [x,y,z],
                "children":    [1,2],
                "fc_name":     "Body",            // FreeCAD internal name
                "visible":     true } ],
  "meshes": [ { "name": "Body",
                "primitives": [ { "positions": 0,   // accessor index
                                  "normals":   1,
                                  "uvs":       2,   // or null
                                  "indices":   3,
                                  "material":  0 } ] } ],
  "accessors": [ { "offset": 0, "length": 4096,
                   "type": "VEC3", "component": "F32", "count": 341 } ],
  "materials": [ { "name": "Steel",
                   "base_color": [r,g,b,a],       // linear 0..1
                   "metallic": 0.0, "roughness": 0.6,
                   "emissive": [0,0,0],
                   "base_color_texture": 0,       // image index or null
                   "double_sided": false } ],
  "images":   [ { "name": "paint_0", "mime": "image/png", "chunk": 0 } ],
  "paint":    { ... see §4 ... },                 // optional
  "vector":   { ... see §4 ... }                  // optional
}
```

Component types: `F32` (4 bytes), `U32`, `U16`, `U8`. Types: `SCALAR`, `VEC2`,
`VEC3`, `VEC4`. Accessor `offset` is relative to the start of the `BIN` payload
and must be 4 byte aligned.

Four more rules that readers have to get right:

* **Index accessors are `U16` or `U32`**, picked automatically from the vertex
  count. A reader must handle both; assuming 32-bit indices breaks on small
  meshes.
* **Accessor positions are in metres**, the same unit as node translations.
  `asset.unit_scale` records the factor that was *already applied* — it is
  provenance, not something to multiply by again.
* **A synthetic root node carries a −90° rotation about X**, which is what
  reconciles FreeCAD's Z-up documents with the Y-up world of §2 and OpenXR
  without rewriting a single vertex. `asset.up_axis` is `"Y"`. Apply the node
  transforms normally; do not add an up-axis correction of your own.
* **`asset.created` is normally absent.** It is opt-in, because a timestamp in
  the manifest would change `content_hash` on every export and defeat the
  change polling in §3. Do not require the field.

Reference implementation: `xrsync/fcxr.py` (writer + reader), `quest/app/src/main/cpp/fcxr.cpp`
(reader), cross-checked by `quest/tools/verify_fcxr.py`. Both must round-trip
the `Tests/test_fcxr.py` fixtures.

--------------------------------------------------------------------------------
## 2. Environment spec (declarative, cross-platform)

Environments are authored procedurally in Python and serialised to a declarative
JSON spec so the Quest app can render exactly the same scene without embedding a
Python interpreter. Specs live in `Resources/environments/<id>.json` and are
regenerated by `tools/gen_environments.py`.

```jsonc
{
  "id": "bambu_x1c",
  "name": "Bambu Lab X1 Carbon (chamber interior)",
  "description": "...",
  "version": 1,
  "user_scale": 12.0,          // user is 12x smaller than reality inside it
  "bounds": [w, d, h],         // interior size in metres (real scale)
  "spawn": [x, y, z],          // where the user is placed, metres
  "ambient": [r, g, b],
  "lights": [ { "type": "directional"|"point"|"spot",
                "direction": [x,y,z], "position": [x,y,z],
                "color": [r,g,b], "intensity": 1.0,
                "cutoff_deg": 45.0, "range": 4.0 } ],
  "materials": [ { "name": "anodised_alu", "base_color": [r,g,b,a],
                   "metallic": 0.9, "roughness": 0.35,
                   "emissive": [0,0,0], "texture": "checker"|null } ],
  "anchors": { "build_plate": { "position": [x,y,z], "rotation": [x,y,z,w],
                                "size": [w,d] } },
  "nodes": [ { "name": "frame_extrusion_left",
               "shape": { ... see below ... },
               "material": 3,
               "translation": [x,y,z], "rotation": [x,y,z,w], "scale": [x,y,z],
               "children": [ ... nested nodes ... ] } ]
}
```

Shape primitives (all dimensions in metres, all centred on the node origin
unless stated):

| `type`        | fields                                                        |
|---------------|---------------------------------------------------------------|
| `box`         | `size:[x,y,z]`                                                |
| `cylinder`    | `radius`, `height`, `sides` (default 24), `caps` (bool)       |
| `cone`        | `radius`, `top_radius`, `height`, `sides`                     |
| `sphere`      | `radius`, `rings`, `sectors`                                  |
| `torus`       | `radius`, `tube_radius`, `sides`, `rings`                     |
| `tube`        | `path:[[x,y,z],...]`, `radius`, `sides` — swept circle        |
| `plane`       | `size:[x,y]`, `subdiv:[u,v]` (XY plane, +Z normal)            |
| `extrusion`   | `profile:[[x,y],...]`, `height`, `closed`                     |
| `grid`        | `size:[x,y]`, `pitch`, `bar` — honeycomb/grid lattice         |
| `honeycomb`   | `size:[x,y]`, `cell`, `wall`, `height`                        |
| `text`        | `string`, `height`, `depth`                                   |
| `mesh`        | `positions:[...]`, `normals:[...]`, `indices:[...]`           |

Coordinate system: **Y up, metres, right handed**, matching OpenXR. The Python
Coin builder converts to the Coin/FreeCAD convention internally.

Conventions a second implementation has to match — the reference tessellator is
`xrenv/spec.py`, mirrored by `quest/app/src/main/cpp/env_spec.cpp`:

* **Primitive axes.** `cylinder`, `cone`, `sphere` and `torus` are **+Y
  aligned**. `plane`, `grid`, `honeycomb`, `extrusion` and `text` lie in the
  **XY plane and grow along +Z**, following the `plane` definition above.
* **`bounds`** means `x ∈ [-w/2, +w/2]`, `z ∈ [-d/2, +d/2]`, `y ∈ [0, h]` — the
  floor is at `y = 0`, not centred. `validate_spec` checks the spawn point
  against it.
* **`honeycomb`** is de-duplicated wall boxes on the hex lattice, not hollow hex
  prisms: prisms leave coincident faces between neighbouring cells, which
  z-fights badly across a laser cutter bed.
* **Anchor frame.** An anchor's local **+Z is its surface normal** and `size`
  spans local X and Y — the same convention FreeCAD uses, which is what lets
  `fit_document_to_anchor` place a document with one transform and exactly one
  unit conversion.
* **Identity transforms are omitted** from the serialised JSON; readers default
  `translation` to `(0,0,0)`, `rotation` to the identity quaternion and `scale`
  to `(1,1,1)`. This is worth roughly 30% of the file size on a detailed
  environment, so it is not a rare case.
* Faces are wound **CCW when seen from outside**, and each primitive is checked
  for positive signed volume to prove it.

Repeated parts are shared by reference rather than re-tessellated: the printer's
969 parts are 192 distinct shapes and the laser cutter's 651 are 137, so a
renderer should tessellate per distinct shape and instance the draws.

Python API (`xrenv`):

```python
from xrenv import registry
registry.list_environments()          -> list[EnvironmentInfo]
registry.get("bambu_x1c")             -> Environment
env.build_scenegraph()                -> pivy.coin.SoSeparator
env.spec                              -> dict (the declarative spec)
env.user_scale                        -> float
env.spawn                             -> (x, y, z)
```

--------------------------------------------------------------------------------
## 3. Sync protocol (HTTP/1.1, no external deps)

The desktop companion server is started from the workbench and speaks plain
HTTP so the Quest client needs only a socket. Default port **47810**;
discovery beacon on UDP **47811**.

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| GET    | `/api/v1/hello`               | server info, protocol version, auth requirement |
| POST   | `/api/v1/pair`                | `{"code":"123456","device":"Quest 3"}` -> `{"token": "..."}` |
| GET    | `/api/v1/documents`           | open documents, each with `name`, `label`, `hash` |
| GET    | `/api/v1/scene?doc=&lod=`     | `.fcxr` body, `Content-Type: application/x-fcxr` |
| GET    | `/api/v1/scene/hash?doc=`     | `{"hash": "..."}` — cheap change poll |
| GET    | `/api/v1/events?since=`       | long poll, `{"events":[{"seq":n,"type":"doc_changed","doc":"..."}]}` |
| GET    | `/api/v1/environments`        | list of environment ids + names |
| GET    | `/api/v1/environment?id=`     | environment spec JSON |
| POST   | `/api/v1/paint`               | `.fcxr` with a `paint` manifest -> applied to the document |
| POST   | `/api/v1/vector`              | vector document JSON -> Draft geometry in the document |
| GET    | `/api/v1/thumbnail?doc=`      | PNG |
| GET    | `/api/v1/state`               | `{"environment": ..., "scale": ...}` — what the desktop is currently showing |

Auth: `Authorization: Bearer <token>` on everything except `/hello` and `/pair`.
Tokens are stored in `~/.FreeCAD/xr/paired_devices.json`. An unauthenticated
request is answered and the connection closed without reading a request body,
so clients must not assume keep-alive survives a 401.

Discovery beacon (UDP broadcast, both directions on 47811):
* client -> broadcast: `FCXR-DISCOVER?v=1`
* server -> unicast reply: `FCXR-OFFER v=1 name=<host> port=47810 id=<uuid>`

--------------------------------------------------------------------------------
## 4. Paint & vector documents

### Paint (`manifest["paint"]`)

```jsonc
{
  "version": 1,
  "targets": [ { "fc_name": "Body",      // painted object
                 "layers": [ { "name": "Base", "image": 0, "opacity": 1.0,
                               "blend": "normal"|"multiply"|"add"|"erase",
                               "visible": true,
                               "resolution": [1024,1024] } ] } ],
  "strokes3d": [ { "brush": "ribbon", "color": [r,g,b,a], "width": 0.01,
                   "points": [ { "p":[x,y,z], "n":[x,y,z], "r":0.01, "t":0.0 } ] } ],
  "palette": [ [r,g,b,a], ... ]
}
```

### Vector (`manifest["vector"]`, also the `/api/v1/vector` body)

```jsonc
{
  "version": 1,
  "plane": { "origin": [x,y,z], "rotation": [x,y,z,w] },   // working plane
  "unit_scale": 0.001,
  "paths": [ { "id": "p1", "closed": true,
               "nodes": [ { "point": [x,y],
                            "in":  [x,y],      // handle, relative, or null
                            "out": [x,y],
                            "type": "corner"|"smooth"|"symmetric" } ],
               "stroke": { "color": [r,g,b,a], "width": 0.5 },
               "fill":   { "color": [r,g,b,a] } | null,
               "target": "draft"|"sketch"|"annotation" } ]
}
```

--------------------------------------------------------------------------------
## 5. Quest 3 application

`quest/` is a self-contained Gradle project producing `FreeCADXR.apk`.

* `NativeActivity`-free: a plain `Activity` + native render thread (`android_native_app_glue`).
* OpenXR 1.1 loader from the Meta OpenXR SDK, GLES 3.2 renderer.
* Features: FCXR scene loading, environment specs, controller interaction,
  miniaturisation, texture painting, vector mode, LAN sync client, Google Drive.
* Java/Kotlin side owns: activity lifecycle, Google Drive OAuth device flow,
  scoped storage, network permissions. Native side owns rendering and interaction.
* Assets: `app/src/main/assets/environments/*.json` (copied from
  `Resources/environments/`), `app/src/main/assets/shaders/*`.

--------------------------------------------------------------------------------
## 6. Coding rules for this workbench

* Python 3.11+, no hard dependency on numpy/requests/zeroconf — degrade
  gracefully and use the stdlib when they are missing.
* Never import `pivy.coin`, `FreeCAD` or `FreeCADGui` at module import time in
  code that must be unit-testable (`xrsync.fcxr`, `xrenv.spec`, `xrpaint.curve`,
  `xrpaint.svg`, `xrsync.protocol`). Do those imports inside functions.
* SPDX header `LGPL-2.1-or-later` on new files; keep upstream headers intact on
  ported files (upstream is LGPL-3.0-or-later, see `LICENSE-upstream.txt`).
* Tests live in `Tests/` and must run under plain `python -m pytest` (or
  `unittest`) with no FreeCAD present.
