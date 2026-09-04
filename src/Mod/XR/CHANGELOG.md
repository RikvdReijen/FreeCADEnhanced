# Changelog — Virtual Reality (XR) workbench

## xr-v0.2.0 — unreleased

Nine features on top of the first release, each a pure-Python package with
its own tests plus a bridge into the viewer. See
`Resources/doc/FEATURES_V02.md` for the user guide. None of it has run in a
headset yet; the tests cover geometry, formats, protocols and state.

* **Assembly in VR** (`xrassembly`): mating features from shapes and meshes,
  candidates from proximity and alignment while a part is in the hand, a
  sequential closed-form solver that keeps the hand's freedom, preview →
  confirm → release, and Assembly workbench joints on commit.
* **Platform import** (`xrimport`): Thingiverse (official API), Printables
  and MakerWorld (unofficial endpoints), GrabCAD (URL + manual ZIP); STL,
  OBJ, PLY and 3MF readers and writers with no dependencies.
* **Physics-based fit checking** (`xrfit`): BVH collision, minimum-translation
  push-out with an approach hint, sliding, clearance, insertion probes.
* **Voice input** (`xrvoice`): a spoken-command grammar with numbers, units
  and fractions, a dispatcher that refuses what it cannot do safely, Vosk
  offline recognition, typed fallback, headset transcripts.
* **Multi-user sessions** (`xrsync.presence`, ARCHITECTURE §3b): presence,
  locks and object moves on the existing sync server; peer avatars on the
  desktop.
* **CAM toolpath preview** (`xrcam`): G-code and CAM `Path` parsing with
  timing, playback at scale inside the machine environment, travel and
  toolhead-collision checks.
* **In-VR technical drawings** (`xrdraw`): a TechDraw page on a drafting
  table, ray-to-page mapping, dimensions inferred from picks.
* **Haptics** (`xrhaptics`): event patterns, a scheduling engine with
  cooldowns and priorities, an OpenXR vibration action on the desktop, hooks
  from snapping, contact, mates, scans, voice and toolpaths.
* **Scan import and alignment** (`xrscan`): Kabsch/Umeyama, ICP, RANSAC
  plane, principal axes, scale from a known length, a pick-driven session.
* **MX Ink stylus and QR anchors** (`xrink`, `xrqr`): the Logitech
  interaction profile with pressure and button roles; printable anchor codes
  with a pose from four corners and settled snapping.
* 17 new commands, five toolbars and menus, 13 wrist-menu buttons, 14 icons;
  `Tests/test_feature_bridges.py` exercises the glue against the stubs.

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

### Sculpting with layers

* Mesh sculpting with a full brush set (draw, clay, flatten, pinch, smooth,
  grab, snake hook, crease, scrape), symmetry, masking and adaptive detail.
* Every stroke lands in a named **sculpt layer** with its own weight, so a pass
  can be dialled back, muted or reordered without losing what is underneath.
  Layers store sparse per-vertex vector offsets, which is what lets weights be
  exactly linear and reversible — and what makes the tangential brushes
  (grab, snake hook, pinch, scrape, smooth, flatten) representable at all.
* Layers persist in a hidden property on the object, so a sculpt survives
  saving and reopening with no sidecar file, and travel to the headset in a
  validated `sculpt` section of the `.fcxr` container.

### Two-handed design tools

* Bimanual manipulation: one hand moves and rotates, both hands scale and
  rotate about the point between them, with damping and a dead zone.
* Freehand curves fitted to clean Béziers, control-point curves, parametric
  primitives placed between the hands, Catmull–Clark subdivision cages with
  cage editing, and lofted, revolved, swept and patched surfaces.
* A snapping system whose radius respects the current user scale, a
  layer-and-collection scene with undo, reference image planes, and
  measurement that stays truthful when you are miniaturised.

### Mixed reality capture

* Four-quadrant output for LIV, OBS and the SteamVR tools, driven by the same
  tracked third-person camera the viewer already had; `externalcamera.cfg` is
  parsed, written and hot-reloaded.
* Camera sources beyond a tracker: fixed, following the headset, or orbiting.
* **No native LIV binding, and that is not something this project can fix.**
  LIV publishes no SDK for native applications, its reserved OpenXR extensions
  are disabled placeholders with no specification, and its Unity and Unreal
  SDKs are Windows-only and require DirectX 11, which an OpenGL/Coin3D viewer
  cannot provide. `Resources/doc/MIXED_REALITY_CAPTURE.md` records the evidence
  with sources, and the module ships an honest capability probe instead of a
  fake binding.

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

* 1,414 unit tests that run without FreeCAD, a GPU or a headset, including a
  stub harness that imports the whole ported OpenXR engine.
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
* Sculpting, the design toolset and mixed reality capture are desktop-only;
  the headset application does not have them yet.
* Nothing in this release has been exercised in an actual headset. The tests
  cover the geometry, the formats and the protocols; the OpenXR session, the
  rendering and the controller interaction have not been run against hardware.
