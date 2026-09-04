# XR workbench — the xr-v0.2 features

Nine additions to the workbench, all built on the same pattern as the
first release: a pure-Python subsystem package that runs without FreeCAD,
a headset or a GPU, a bridge in `xrcore/` that wires it to the viewer, the
document and the wrist menu, and a test file under `Tests/`. This page is
the user-facing guide; each package's `__init__.py` docstring is the
developer one.

| Feature | Package | Bridge | Tests |
|---|---|---|---|
| Assembly in VR | `xrassembly` | `assembly_bridge` | `test_assembly.py` |
| Platform import and converters | `xrimport` | `import_bridge` | `test_import.py` |
| Physics-based fit checking | `xrfit` | `fit_bridge` | `test_fit.py` |
| Voice as a modelling input | `xrvoice` | `voice_bridge` | `test_voice.py` |
| Multi-user sessions | `xrsync.presence` | `presence_bridge` | `test_presence.py` |
| CAM toolpath preview | `xrcam` | `cam_bridge` | `test_cam.py` |
| In-VR technical drawings | `xrdraw` | `draw_bridge` | `test_draw.py` |
| Haptics | `xrhaptics` | `haptics_bridge` | `test_haptics.py` |
| Scan import and alignment | `xrscan` | `scan_bridge` | `test_scan.py` |
| MX Ink stylus, QR anchors | `xrink`, `xrqr` | `ink_bridge`, `qr_bridge` | `test_ink_qr.py` |

The glue itself is covered by `test_feature_bridges.py`, which imports every
bridge against the FreeCAD/Qt/Coin stubs and runs its no-viewer paths.

**What none of this has had: a headset.** Everything below has been
exercised by tests on geometry, formats, protocols and state machines. The
OpenXR haptic action, the stylus profile, the Coin previews and the Quest
voice input are written to the specifications and have not been run on a
device. Treat the first session in a headset as the acceptance test.

---

## Assembly in VR (`xrassembly`)

Placing mate constraints by hand at 1:1 is the thing VR is unambiguously
better at than a mouse. *Assembly mode* (toolbar, wrist menu "Assemble", or
say "assembly mode") reads every part-like object in the document into a
session: its **mating features** — planar faces, cylindrical faces and
circular edges as axes, vertices and circle centres as points — from the
shape, and its pose from the placement. The first part is the ground.

Grip near a part to grab it. As you move it, the session compares its
features with every fixed part's: a face nearly touching another face,
face-to-face, is a **coincident** candidate; an axis nearly on another axis
with a plausible radius is **concentric**; a point on a point is a **point**
mate. The best candidate is previewed — the part *snaps* into it with a
haptic tick and a highlight line — while the hand keeps whatever freedom
the mate leaves: a peg dropped into a bore still follows the hand along and
about the bore. The trigger confirms the mate (double-tap haptic); moving
away drops the preview; letting go releases the part where the confirmed
mates put it. A second mate is offered on top of the first — the shoulder
after the bore — and the solver applies them in order, each within the
freedom the earlier ones left. Over-constraining combinations are not
offered.

*Commit mates* writes placements to every object and, when the Assembly
workbench is loaded, creates joints: concentric + coincident on one part
becomes a **Revolute** joint, concentric alone **Cylindrical**, a face pair a
**Distance** joint at zero, and so on (`to_freecad.joint_type_for`). Without
the workbench the placements alone are written, and the console says so.

Features are exact from a `Part.Shape`. For imported meshes,
`xrassembly.from_mesh` recovers planes by clustering coplanar triangles and
cylinders by fitting the smooth patches; it is approximate, and a shallow
wedge of two real planes at under 35° will be merged into one patch.

## Platform import (`xrimport`)

*Import from URL…* takes a page link and resolves it through the platform:

* **Thingiverse** — the documented REST API. Needs an app token from
  thingiverse.com/developers in the XR preferences (`ImportTokenThingiverse`).
* **Printables** — the site's own GraphQL endpoint, unofficial. Public
  models resolve and download without an account; if Prusa changes the
  schema this stops working and says so.
* **MakerWorld** — the JSON endpoints behind the site, unofficial. Many
  designs are only offered as a print profile (`.3mf` with slicer settings),
  which imports as the mesh it carries.
* **GrabCAD** — has no public download API and downloads need a signed-in
  session. The URL is recognised and the model title read; the files are
  imported from the ZIP you download in a browser, through *Import archive
  or mesh…*.

Readers for STL (ASCII and binary), OBJ (groups, quads, negative indices),
PLY (ASCII and binary) and 3MF (units, build transforms, Bambu/Prusa
multi-model packages) are pure Python; STEP, IGES, BREP and FCStd go to
FreeCAD's own importers. Meshes become `Mesh::Feature` objects, or solids
when `ImportAsSolid` is set and the mesh is closed.

## Physics-based fit checking (`xrfit`)

*Fit check mode* tessellates every part-like object into a collision
session. Grip a part and try to insert it: each frame the hand proposes a
pose, the session tests it against every static part with a
bounding-volume hierarchy and triangle/triangle intersection, and if it
collides it pushes the part back out — along the shortest way out, found
by casting exit rays from the vertices inside the other mesh, and always
*back the way it came* so a thin wall crossed in one frame is not escaped
through the far side. Tangential motion is kept, so a peg slides along a
bore rather than sticking. If push-out cannot resolve it, the part stops
in your hand (**blocked**, a strong buzz).

When the part is free the session measures its **clearance** to the nearest
part and shows it on the wrist-menu status line ("clearance 0.05 mm to
Housing"). A **contact margin** in the preferences (`FitContactMargin`) holds
parts apart by a modelled clearance and reports "seated" when they reach it.
`fit_bridge.probe(name, direction, distance)` sweeps a part along a
direction and reports how far it travels and what stops it.

This is a constraint response, not a physics engine: no mass, friction or
momentum, on purpose — the question is "does it go in and how much room is
there", and a rigid response answers that without tunnelling or jitter.

## Voice as a modelling input (`xrvoice`)

Hands are busy in VR; voice is the free channel. The vocabulary is a small,
explicit grammar rather than a language model: "fillet these edges, two
millimetres", "pocket five millimetres deep", "rotate ninety degrees about
z", "move up two and a half millimetres", "set wall thickness to 3 mm",
"shrink me", "take me to the laser cutter", "sculpt mode", "snap off",
"undo", "play the toolpath", "layer 12", "mate it", "let go", "dimension
that", "what can I say". Numbers come as digits or words, decimals as
"two point five", fractions as "half a millimetre" or "three quarters",
units in mm/cm/m/inch/feet/degrees; without a unit, millimetres.

A command either parses exactly or fails loudly — a fillet with the wrong
radius is worse than one ignored — and each one declares what it needs
(a selection, a document), so "fillet 2 mm" with nothing selected answers
"needs a selection". Modelling commands create PartDesign features on the
selection (fillet, chamfer, pocket, pad, hole, shell), set spreadsheet
aliases and VarSet properties, move and rotate placements; the rest drive
the XR bridges. Without FreeCAD the handlers run as a dry run, which is how
the dispatcher is tested.

Backends: **Vosk** offline recognition when the `vosk` and `sounddevice`
packages are installed and `VoiceModelPath` points at a model — the
grammar's word list is passed to the recogniser, which is what makes
"fillet two millimetres" come out right; **typed text** (*Type a voice
command…*, or `voice_bridge.say`) as the desktop fallback; and **the
headset**, which posts transcripts to `POST /api/v1/voice`. The Quest side
uses Android's `SpeechRecognizer` (`quest/…/VoiceInput.java`). Every
recognised command ticks the controller; a misunderstood one buzzes twice.

## Multi-user sessions (`xrsync.presence`)

Two headsets in one model is a protocol extension, not a rebuild. The sync
server gains three endpoints (`ARCHITECTURE.md` §3b): **presence** —
each device posts its head and hand poses, selection, environment and
scale every frame and gets everyone else's back; **lock** — grabbing an
object takes a lock, releasing drops it, and a peer that goes quiet loses
its locks; **move** — a placement broadcast (and applied on the desktop)
while an object is held. Peers are told apart by a hash of their pairing
token, never the token itself, and get a stable colour.

The desktop publishes itself as the peer `desktop` and draws the others as
small avatars — a head block, two hand blocks, a name — that appear with a
double tick and vanish after five silent seconds. *Who is here* lists them
with what they hold. This is also what makes the collaboration concept in
`docs/concepts/ai-cad-collaboration` testable: two people, or a person and
an agent, editing one model with live claims.

## CAM toolpath preview (`xrcam`)

You are already standing in the printer. *Load toolpath…* reads a G-code
file — Marlin/Klipper/RepRap printers with extrusion and layer comments,
GRBL/LinuxCNC mills and lasers with spindle, arcs (`I J` or `R`), inches
and relative moves — or the selected CAM job's `Path` commands, into
timed segments. The machine's geometry comes from the environment's
build-plate anchor (`MachineSpec.from_environment_spec`), so the path is
drawn at scale exactly where the machine's bed is, cutting moves bright and
rapids grey, with a toolhead marker that runs along it under *Play / pause*
at any speed ("faster", "speed 4 times", "layer 12").

Two checks run on load: moves outside the machine's travel, and — when
obstacles are registered (bed clips, a fixture, an already-printed part)
— a sampled sweep of the toolhead envelope along the path with the same
collision code as the fit check. Issues are listed on load and, when
playback reaches one, felt as a warning buzz.

## In-VR technical drawings (`xrdraw`)

*Drafting table* puts the document's TechDraw page on a tilted board in
front of you, rendered through TechDraw's SVG export, and reads each view's
projected vertices and edges so the controller ray snaps to them. Point,
pull the trigger to pick, pick again, and *Place dimension* (the Confirm
button, or "dimension that") infers the dimension: two vertices give a
distance — X- or Y-aligned when they nearly are — a circle a diameter, an
arc a radius, two lines an angle or, when parallel, their gap, a vertex
and a line the perpendicular distance. Values are divided by the view
scale so the number is the model's. The dimension is created in the page
and the board re-rendered; without FreeCAD it stays a preview.

## Haptics (`xrhaptics`)

Cheap, and snapping without feedback feels unreliable even when it is not.
A pattern per event kind — a crisp tick on snap, a soft bump on contact
that grows with penetration, an unmistakable buzz while blocked, a double
tap when a constraint is confirmed, a triple tap when a scan aligns —
with per-hand cooldowns and priorities so a buzz interrupts a bump and a
click never interrupts either. The desktop viewer creates an OpenXR
vibration action and appends it to every interaction profile's bindings;
the Quest app already has its own pulse. *Haptics* toggles it and
`HapticsIntensity` scales it.

## Scan import and alignment (`xrscan`)

Bring in a mesh of a real object and model to fit it at 1:1. *Import scan…*
shows the scan in the room; with a model object selected, the trigger
picks a point on the scan, then the matching point on the model (nearest
surface point to the controller each time), and three pairs are enough
for *Align scan* — a Kabsch fit, then ICP refinement against the model's
surface (2000 sampled points by default). Two picks and a tape-measure
figure fix a scan exported in the wrong unit; "sit on the plate" finds the
scan's largest plane by RANSAC and puts it on the environment's build
plate. *Commit scan* writes the aligned mesh into the document.

## Logitech MX Ink (`xrink`) and QR anchors (`xrqr`)

When the runtime offers `XR_LOGITECH_mx_ink_interaction`, the viewer binds
the stylus profile: tip pressure becomes the trigger and the brush
pressure (paint radius, sculpt strength), the middle cluster the grab,
the front button Confirm, the back button Undo, a back double-tap the
wrist menu. Roles are remappable and the pressure curve selectable.

A printed QR code is a spatial anchor. *Make anchor code…* writes an SVG
(with the `qrcode` package; otherwise it prints the payload for any
generator) carrying `fcxr://anchor?id=…&size=…` and what to snap to it.
When a camera-equipped device reports the code's four corners
(`POST /api/v1/qr`), the session waits for three consistent detections,
checks the measured edge against the printed size, and moves the model,
a part or — for `target=` codes — reports the offset to an environment
anchor. Detection itself runs on the device; the Quest 3 needs the
Passthrough Camera API, which the app does not yet use.

---

## Preferences added

`User parameter:BaseApp/Preferences/Mod/XR`: `HapticsEnabled`,
`HapticsIntensity`, `VoiceModelPath`, `VoiceConfidence`,
`ImportTokenThingiverse`, `ImportAsSolid`, `FitContactMargin`,
`FitClearanceMax`, `DrawPageSize`, `DrawTableTilt`, `ScanSamplePoints`,
`QrSettleCount`, `QrMaxResidual`, `InkHand`, `PeerName`. They are read
with defaults, so nothing needs setting to start.
