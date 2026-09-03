# Virtual Reality (XR) workbench

Model, review and paint FreeCAD documents in virtual reality — on a PC with a
tethered headset, or standalone on a Meta Quest 3.

```
src/Mod/XR/
├── xrcore/       OpenXR session, Coin3D scenegraph, controllers, in-VR menus
├── xrenv/        environment switcher — the world around you, and how small you are in it
├── xrpaint/      VR texture painting, 3D strokes, and the vector editor
├── xrsync/       .fcxr scene packages, the LAN companion server, Google Drive
├── quest/        the standalone Meta Quest 3 application (Android/OpenXR)
└── Resources/doc/ARCHITECTURE.md   ← the interface contract everything follows
```

The OpenXR engine is a port of Adrian Przekwas'
[freecad-xr-workbench](https://github.com/kwahoo2/freecad-xr-workbench); see
`NOTICE.md` for attribution and the list of changes made during the port.

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
* The APK is built separately — see `quest/README.md`.

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
5. **Sync server** starts the companion server, **Pair headset…** shows the
   code to type into the Quest application, and **Export scene for headset…**
   writes an `.fcxr` you can copy across by hand.

Preferences live under **Edit → Preferences → Virtual Reality**: the first page
covers the OpenXR viewer, the second the environment, painting and sync.

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

## Google Drive

Google requires every application to use its own OAuth client, so there is no
shared key to ship. Create one (type *Desktop* / *TVs & limited input devices*)
in the [Google Cloud console](https://console.cloud.google.com/apis/credentials),
enable the Drive API, then enter it under **Google Drive account…**. The same
account signs in on the headset with the device-code flow — a short code you
type on your phone — so the Quest can open documents with no PC running.

Tokens are stored in `~/.FreeCAD/xr/gdrive_token.json` with `0600` permissions
and are never logged.

## Tests

The parts that do not need FreeCAD — the FCXR container, the sync protocol, the
environment tessellator, the brush and curve mathematics, the SVG codec and the
workbench glue — are covered by plain unit tests:

```sh
cd src/Mod/XR && python3 -m unittest discover -s Tests -t .
# or
python3 src/Mod/XR/Tests/run_all.py
```
