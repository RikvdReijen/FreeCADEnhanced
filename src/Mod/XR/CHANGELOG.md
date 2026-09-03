# Changelog — Virtual Reality (XR) workbench

## xr-v0.1.0 — 2026-09-03

First release of the XR workbench: a VR modelling, painting and review
environment for FreeCAD, plus a standalone Meta Quest 3 application that keeps
in step with the desktop.

### The workbench

* OpenXR viewer for tethered headsets, ported from Adrian Przekwas'
  [freecad-xr-workbench](https://github.com/kwahoo2/freecad-xr-workbench) with
  its LGPL-3.0 headers and attribution intact (`NOTICE.md`). Built as
  `Mod/XR`, enabled by the `BUILD_XR` CMake option.
* 23 GUI commands, a second preferences page, and an in-VR wrist menu carrying
  the controls that matter while a headset is on — the desktop dialogs are
  unreachable from inside VR, so environment switching, scale and the painting
  modes all have menu entries of their own.

### Environment switcher and miniaturisation

* Five environments: a neutral studio, a dark void, a workshop, and two machine
  interiors you stand *inside* while modelling — an enclosed CoreXY 3D printer
  chamber (969 parts) and a large-format CO₂ laser cutter (651 parts).
* Shrinking the user is implemented by growing the world, with the near clip
  plane following the scale so a 15 cm tall person's hands do not disappear
  into it, and the document dropped onto the machine's build plate or bed.
* Environments are declarative JSON, generated from Python builders and read by
  both renderers. Users can drop their own into `~/.FreeCAD/xr/environments/`.

### Painting and vector editing

* Texture painting directly onto model surfaces, with layers, blend modes,
  tile-based undo, seam dilation and automatic UV generation for objects that
  have none.
* Tilt-Brush-style 3D strokes with parallel-transport framing, convertible into
  document geometry.
* A vector editor: Bézier paths with node and handle editing, Schneider curve
  fitting from freehand VR strokes, snapping, SVG import and export, and
  commitment into Draft wires, B-splines and faces.

### Headset sync

* `.fcxr` scene packages — a dependency-free chunked binary container.
* A companion server on the local network with pairing codes and a device
  registry, plus `tools/xr_sync_daemon.py` for machines with no display.
* Google Drive with the device-code flow, so the headset can open documents
  with no PC running. Users supply their own OAuth client.

### The standalone Quest 3 application

* A Gradle project under `quest/`: OpenXR with passthrough, a GLES 3.2
  renderer, Touch Plus and hand-tracking input, the FCXR reader and writer, the
  environment runtime, painting, vector editing, the sync client and Drive
  OAuth — with no third-party dependencies beyond the OpenXR loader and the
  NDK. Its JSON parser, PNG codec and HTTP client are all part of the project.
* It does not open `.FCStd` files; that needs FreeCAD's kernel, and the app says
  so rather than half-working.

### Verification

* 724 unit tests that run without FreeCAD, a GPU or a headset, including a stub
  harness that imports the whole ported OpenXR engine.
* A parity check that compiles the Quest tessellator on the host and diffs it
  against the Python one over every shipped environment — all 2,075 shapes
  agree exactly.

### Known limitations

* The Quest APK has been compiled and cross-checked on a host compiler but has
  never been built for, or run on, a headset. Four setup steps are needed before
  the first build; see `quest/README.md`.
* The desktop viewer needs an OpenXR runtime with `XR_KHR_opengl_enable`, plus
  `pyopenxr` and `PyOpenGL`.
* Google Drive is inert until an OAuth client is configured.
