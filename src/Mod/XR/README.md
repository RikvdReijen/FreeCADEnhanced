# Virtual Reality (XR) workbench

Model, review and paint FreeCAD documents in virtual reality — on a PC with a
tethered headset, or standalone on a Meta Quest 3.

```
src/Mod/XR/
├── xrcore/       OpenXR session, Coin3D scenegraph, controllers, in-VR menus
├── xrenv/        environment switcher — the world around you, and how small you are in it
├── xrpaint/      VR texture painting, 3D strokes, and the vector editor
├── xrsculpt/     mesh sculpting with a sculpt-layer stack
├── xrsketch/     two-handed design tools in the Gravity Sketch idiom
├── xrmrc/        mixed reality capture for LIV, OBS and spectator views
├── xrsync/       .fcxr scene packages, the LAN companion server, Google Drive
├── quest/        the standalone Meta Quest 3 application (Android/OpenXR)
└── Resources/doc/ARCHITECTURE.md   ← the interface contract everything follows
```

The OpenXR engine is a port of Adrian Przekwas'
[freecad-xr-workbench](https://github.com/kwahoo2/freecad-xr-workbench); see
`NOTICE.md` for attribution and the list of changes made during the port.

## xr-v0.2 (in progress)

Assembly by hand, fit checking by collision, voice commands, shared
sessions, toolpath preview inside the machine, drawings on a drafting
table, haptics, scan alignment, the MX Ink stylus and QR anchors — see
`Resources/doc/FEATURES_V02.md`. Ten new packages, all testable without
FreeCAD; nothing has run in a headset yet.

## What runs where

FreeCAD's kernel does not run on Android, so "FreeCAD on a Quest" is not one
program but two that stay in step:

| | Desktop FreeCAD | Quest 3 application |
|---|---|---|
| Parametric modelling, sketches, the full toolset | ✅ | ✗ |
| Viewing a document in VR at any scale | ✅ (tethered) | ✅ (standalone) |
| Environment switcher, miniaturisation | ✅ | ✅ |
| Texture painting, 3D strokes | ✅ | ✅ |
| Vector editor | ✅ | ✅ |
| Sculpting with sculpt layers | ✅ | ✗ |
| Two-handed design tools, subdivision cages | ✅ | ✗ |
| Mixed reality capture (LIV, OBS) | ✅ | ✗ |
| Turning VR drawings into Draft/Part geometry | ✅ | via sync |
| Works with the PC switched off | ✗ | ✅ |

The headset consumes `.fcxr` scene packages (§1 of `ARCHITECTURE.md`). It gets
them three ways: over the LAN from the companion server, from Google Drive, or
sideloaded. Edits made in the headset — paintings, 3D strokes, vector paths —
travel back the same way and are applied to the document.

## Requirements

**Desktop**

* An OpenXR runtime supporting `XR_KHR_opengl_enable` (SteamVR 2.11.2+, Monado).
* `pip install pyopenxr PyOpenGL`
* Optional: `numpy` accelerates texture painting; everything works without it.

Choose a runtime explicitly with `XR_RUNTIME_JSON=/path/to/runtime.json freecad`.

**Quest 3**

* Developer mode enabled, `adb` available.
* Android Studio with NDK r26+, and a one-off `gradle wrapper` (the wrapper jar
  is a binary and is not checked in).
* The APK is built separately — see `quest/README.md`, which also lists what
  the standalone app deliberately does not do.

## Getting started

1. Switch to the **Virtual Reality** workbench and press **Open XR viewer**
   (`X`, `R`). The active document appears in the headset at 1:1.
2. **Environment…** (`X`, `E`) chooses the world around you. Pick the printer
   chamber and you are dropped onto the build plate at roughly 15 cm tall, with
   the gantry overhead and your model sitting on the plate beside you. **Shrink
   me** / **Grow me** change scale from anywhere; the near clip plane follows so
   your hands stay visible when you are small.
3. **Texture painting** (`X`, `P`) puts a brush in your hand. Paint straight
   onto the model; the stroke lands in a UV texture with layers, blend modes
   and undo. Objects without UVs get them generated automatically.
4. **Vector editor** (`X`, `V`) gives you a working plane and Bézier paths with
   node and handle editing. **Commit vector paths** turns them into Draft
   wires, B-splines and faces; **Export vector paths as SVG…** writes a file.
5. **Sculpting** (`X`, `S`) puts a brush on the surface. Every stroke lands in
   the **active sculpt layer**, so a whole pass can be dialled back to 30%,
   muted, or reordered later without losing the strokes underneath —
   **Sculpt layers…** is where you do that. **Mask painting** protects what you
   do not want moved, and **Subdivide** adds detail where you are working.
6. **Select and move** (`X`, `G`) is the two-handed idiom: grab with one hand to
   move and rotate, grab with both to scale and rotate about the point between
   them. **Freehand curve** fits clean Béziers to a stroke drawn in the air,
   **Control point pen** places them by hand, **Primitives** are placed between
   your hands, and **Subdivision cage** lets you push a control cage around and
   watch the smooth surface follow. **Measure** stays truthful when you are
   miniaturised. **Commit sketch** turns all of it into Draft curves, Part
   surfaces and parametric primitives.
7. **Mixed reality capture** films you inside the scene — see below.
8. **Sync server** starts the companion server, **Pair headset…** shows the
   code to type into the Quest application, and **Export scene for headset…**
   writes an `.fcxr` you can copy across by hand.

Preferences live under **Edit → Preferences → Virtual Reality**: the first page
covers the OpenXR viewer, the second the environment, painting and sync.

### Serving without a GUI

A machine with no display can still feed the headset:

```sh
freecadcmd src/Mod/XR/tools/xr_sync_daemon.py -- --watch ~/cad --pair
```

It opens every `.FCStd` under the directory, announces itself on the local
network, and prints a pairing code. `--once` turns the same command into a batch
exporter that writes an `.fcxr` beside each document and exits.

## Environments

| id | what it is | your scale |
|---|---|---|
| `studio` | neutral cyclorama with soft lights and a reference grid | 1:1 |
| `void` | dark space with a horizon grid — no distractions | 1:1 |
| `bambu_x1c` | the inside of an enclosed CoreXY 3D printer chamber | miniature |
| `laser_cutter` | the inside of a large-format CO₂ laser cutter | miniature |

Environments are declarative JSON (§2 of `ARCHITECTURE.md`), generated from the
Python builders in `xrenv/environments/` by `tools/gen_environments.py`. Both
the desktop Coin3D renderer and the Quest GLES renderer read the same spec, so
the two look alike. Drop your own JSON into `~/.FreeCAD/xr/environments/` and it
appears in the switcher.

The machine environments are original procedural representations built for this
project; they contain no manufacturer-supplied CAD, artwork or firmware.

## Mixed reality capture

**Mixed reality capture** emits the four-quadrant layout that LIV, OBS and the
SteamVR tools consume, driven by the same tracked third-person camera the
workbench already had. Point it at an `externalcamera.cfg` — the calibration
file the ecosystem shares — and it hot-reloads whenever you change it. If you
have no tracker, the camera can also follow or orbit the headset.

A word on LIV specifically: **there is no native LIV binding here, and that is
not an omission we can fix.** LIV publishes no SDK for native applications, its
reserved OpenXR extensions are all disabled placeholders with no specification,
and its Unity and Unreal SDKs are Windows-only and require DirectX 11, which an
OpenGL/Coin3D viewer cannot provide. What works is the documented external
camera path that LIV itself consumes. `Resources/doc/MIXED_REALITY_CAPTURE.md`
records the evidence, with sources, and what would have to change.

## Sculpting and sketching

`xrsculpt` is sculpting with **layers**: each layer holds a sparse per-vertex
displacement with its own weight, so a pass is a dial rather than a commitment.
A layer stores full vector offsets rather than a scalar along a stored normal,
because grab, snake hook, pinch, scrape, smooth and flatten all move vertices
sideways — the scalar form cannot represent them, and the stored normal goes
stale as soon as a layer below changes. Layers ride along in the document, in a
hidden property on the object.

`xrsketch` is the two-handed design toolset: bimanual grab, freehand and control
point curves, parametric primitives, Catmull–Clark subdivision cages, lofted and
revolved surfaces, snapping, layers and collections, reference image planes and
measurement. See `Resources/doc/SKETCH_TOOLSET.md`.

## Google Drive

Google requires every application to use its own OAuth client, so there is no
shared key to ship. Create one (type *Desktop* / *TVs & limited input devices*)
in the [Google Cloud console](https://console.cloud.google.com/apis/credentials),
enable the Drive API, then enter it under **Google Drive account…**. The same
account signs in on the headset with the device-code flow — a short code you
type on your phone — so the Quest can open documents with no PC running.

Tokens are stored in `~/.FreeCAD/xr/gdrive_token.json` with `0600` permissions
and are never logged.

## The standalone Quest 3 application

`quest/` is a self-contained Gradle project — roughly 9,400 lines of C++, 750 of
Java and 200 of GLSL — with no third-party dependencies beyond the OpenXR
loader and the NDK: the JSON parser, the PNG codec and the HTTP client are all
part of it.

It renders the same environments from the same specs, loads `.fcxr` scenes over
the LAN or from Google Drive, paints, edits vectors, and sends the results back.
It does not open `.FCStd` files — that needs FreeCAD's kernel, so the app says
so plainly rather than half-working.

### Keeping the two renderers honest

The environment tessellator exists twice: `xrenv/spec.py` for the desktop and
`quest/app/src/main/cpp/tessellate.cpp` for the headset. Two implementations of
the same geometry drift in ways nobody notices by reading them — a flipped
winding, an inverted normal, a cylinder on the wrong axis — so they are compared
directly:

```sh
python3 src/Mod/XR/tools/check_tessellator_parity.py
```

It builds a small host driver from the Quest sources, runs both tessellators
over every shipped environment, and diffs the results. All 2,075 shapes across
the five environments currently agree exactly. The same check runs as a unit
test when a host C++ compiler is available, and skips when there is not one.

## Tests

The parts that do not need FreeCAD — the FCXR container, the sync protocol, the
environment tessellator, the brush and curve mathematics, the SVG codec and the
workbench glue — are covered by plain unit tests:

```sh
cd src/Mod/XR && python3 -m unittest discover -s Tests -t .
# or
python3 src/Mod/XR/Tests/run_all.py
```
