<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# FreeCAD XR — Meta Quest 3 application

A standalone OpenXR viewer and editor for the FCXR scene packages the FreeCAD
XR workbench exports. It renders the same declarative environments the desktop
does, lets you walk around a miniaturised machine, paint on the loaded model,
draw Bézier paths on a working plane, and send the result back.

**FreeCAD itself does not run on the headset.** There is no OCC kernel, no
Python and no document recompute here. Everything the app shows arrives as a
finished `.fcxr` package (ARCHITECTURE.md §1) or an environment spec (§2), and
everything you change goes home as a paint or vector document (§4) over the LAN
sync protocol (§3). What that split means in practice is spelled out under
[Standalone vs synced](#standalone-vs-synced).

```
quest/
├── README.md                  this file
├── settings.gradle, build.gradle, gradle.properties, gradlew[.bat]
├── local.properties.sample    copy to local.properties and edit
├── docs/TESSELLATION.md       the §2 tessellation contract, shared with xrenv/spec.py
├── tools/verify_fcxr.py       host-side FCXR validator (mirrors the C++ reader)
├── tools/gen_glyphs.py        regenerates the stroke font from xrenv/spec.py
└── app/src/main/
    ├── AndroidManifest.xml
    ├── assets/environments/   copied from Resources/environments by Gradle
    ├── assets/shaders/        GLSL ES 3.20
    ├── cpp/                   the whole renderer, ~9 kLOC, no third party code
    └── java/org/freecad/xr/   activity, Drive, SAF picker, JNI surface
```

## Prerequisites

* **Android Studio** Koala or newer (or a standalone Android SDK).
* **Android SDK Platform 34** and **Build Tools 34.x**.
* **NDK r26** or newer (`26.3.11579264` is what `app/build.gradle` pins; change
  `ndkVersion` if you have a different one installed).
* **JDK 17**.
* An OpenXR loader, either of:
  * the **Meta OpenXR Mobile SDK** unpacked somewhere, with its path in
    `local.properties` as `metaXrSdkDir`; or
  * nothing at all, in which case the build pulls the Khronos
    `org.khronos.openxr:openxr_loader_for_android` AAR from Maven Central,
    which also runs on Quest.
* `python3` on PATH if you want the Gradle `checkGlyphTable` task to run.

There are **no third-party C++ dependencies** beyond that loader, GLES3 and the
NDK. The JSON parser, PNG codec, HTTP client, stroke font and tessellator are
all in `app/src/main/cpp/`.

## Building

```sh
cd src/Mod/XR/quest
cp local.properties.sample local.properties && $EDITOR local.properties
./gradlew assembleDebug
```

The APK lands in `app/build/outputs/apk/debug/app-debug.apk`.

`gradle/wrapper/gradle-wrapper.jar` is a binary and is not checked in. Create it
once with `gradle wrapper --gradle-version 8.7`, or just open `quest/` in
Android Studio, which writes it on the first sync. Everything else the wrapper
needs is already here.

The `copyEnvironments` task copies `../Resources/environments/*.json` into
`app/src/main/assets/environments/` before the assets are merged. Those specs
are generated on the desktop by `tools/gen_environments.py`; if the directory is
empty the build still succeeds and the app starts with no rooms to choose from.

### Signing and sideloading

Debug builds are signed with Android Studio's debug key, which is all a
sideload needs:

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n org.freecad.xr/.MainActivity     # or launch it from
                                                        # "Unknown Sources"
adb logcat -s FreeCADXR                                 # everything logs here
```

For a release build put a keystore in `local.properties`:

```properties
storeFile=/home/you/keys/freecadxr.jks
storePassword=…
keyAlias=freecadxr
keyPassword=…
```

then `./gradlew assembleRelease`.

### Enabling developer mode on a Quest 3

1. In the Meta Horizon phone app, open **Devices → your headset → Headset
   settings → Developer mode** and turn it on. (Meta requires the account to
   belong to a developer organisation; creating one is free and takes a
   minute.)
2. Reboot the headset, plug in USB-C, put it on and **allow USB debugging** on
   the prompt that appears inside VR.
3. `adb devices` should now list it. `adb install -r …` works from there.

Sideloaded apps appear in the library under **Unknown Sources**.

## Standalone vs synced

Everything below works **with no desktop at all**, from files already on the
headset:

* loading `.fcxr` packages from the on-device library, imported through the
  system file picker or downloaded from Google Drive;
* every environment shipped in the APK, switching between them, and the
  `user_scale` miniaturisation;
* passthrough (`XR_FB_passthrough`) on or off;
* texture painting with layers, blend modes and undo, and 3D ribbon strokes;
* vector mode: drawing and editing Bézier paths on a working plane;
* saving all of that back into the local library.

These need the desktop **companion server** (started from the workbench,
default port 47810):

* discovering and pairing with a desktop, and the six digit pairing code;
* listing the open documents and fetching a scene at a chosen LOD;
* following the desktop live: the event long poll refetches a document when it
  changes, and `/api/v1/state` keeps the environment and scale in step;
* pushing a paint document or a vector document back into the FreeCAD document.

These are **not implemented** in this first version, deliberately, rather than
being stubbed out:

* opening `.FCStd` files directly — the headset has no OCC kernel, so the
  desktop must export an `.fcxr` first (a `.FCStd` picked in the file browser is
  rejected with a message saying so);
* editing the model itself: no sketches, no features, no constraints. Painting
  and vector paths are the only things that travel back.
* depth-layer submission (`XR_KHR_composition_layer_depth`) and the
  reprojection it enables — the projection layer is submitted without depth;
* Adam7 interlaced PNGs (the desktop never writes them; the decoder rejects
  them with a clear error).

## Google Drive

Drive uses the OAuth 2.0 **device authorisation flow**, which is the right fit
for a headset with no keyboard: the app shows a short code and a URL on a panel
in VR, you type them on a phone, and the app polls the token endpoint until the
grant appears. Refresh tokens live in `EncryptedSharedPreferences`.

1. In the Google Cloud console create an OAuth client of type **TVs and Limited
   Input devices** and enable the **Drive API**.
2. Put the client id in `local.properties`:

   ```properties
   googleClientId=1234567890-abcdefghij.apps.googleusercontent.com
   googleClientSecret=
   ```

   The id is read through `BuildConfig`; it is never hard coded in a source
   file, and with none configured the Drive panel simply says Drive is not set
   up.
3. The default scope is `drive.file`, which sees only files this app or the
   desktop workbench (signed in with the same OAuth client) created. Widen it
   with `driveScope=` in `local.properties` if you want to browse a whole
   Drive — note that broader Drive scopes need Google's verification before
   they work for anyone but you.

## Pairing with the desktop over the LAN

1. Start the sync server from the XR workbench on the desktop. It listens on
   TCP **47810** and answers discovery beacons on UDP **47811**.
2. In the headset open **Menu → Desktop → Search the network**. The app
   broadcasts `FCXR-DISCOVER?v=1` and lists whatever answers with an
   `FCXR-OFFER`.
3. Pick your desktop, dial in the six digit code the workbench is showing with
   the `< - + >` buttons, and press **Pair**. The token is stored on the
   headset and reused on every later run.
4. **Documents** lists what is open on the desktop; **Fetch** downloads one and
   loads it. **Follow desktop** starts the event long poll so the headset
   refetches when the document changes.

Both machines must be on the same subnet, and broadcast traffic must not be
blocked — guest and client-isolation Wi-Fi networks are the usual culprit when
discovery finds nothing. You can always set the address by hand instead.

## Wire format notes

Details that ARCHITECTURE.md leaves implicit and that the reader in `fcxr.cpp`
depends on. They were settled with the Python implementation in
`xrsync/fcxr.py`; change them in both places or not at all.

1. **A chunk's `payload_length` excludes the padding.** This is the literal
   reading of §1 and differs from GLB, where the length includes it. Take
   `payload_length` bytes, then skip to the next 4 byte boundary before reading
   the next chunk header. A reader that assumes GLB semantics desynchronises on
   the second chunk.
2. **Index accessors are `U16` or `U32`**, chosen by the writer according to
   the vertex count. Both are legal §1 component types; the reader widens both
   to `uint32`. Accessor offsets stay 4 byte aligned either way, and the reader
   rejects a file where they are not.
3. **Accessor positions are in metres**, the same unit as node translations.
   `asset.unit_scale` records the factor that has *already* been applied — it is
   provenance, not something to multiply by. Multiplying again shrinks the model
   by a thousand.
4. **There is a synthetic root node rotated −90° about X**, carrying FreeCAD's
   Z-up content into the Y-up world of §2 and OpenXR without touching vertices,
   and `asset.up_axis` is `"Y"`. Apply node transforms normally and it comes out
   right; do not add a Z-up correction on top. The reader logs a warning if
   `up_axis` is anything else, because it has no correction of its own.
5. **`asset.created` is normally absent.** It is opt-in so that content hashing
   stays deterministic for change polling. Nothing may require it, and the
   writer here omits it (and `source_document`, and an identity `scene`) when
   empty for the same reason.

Two further notes about the §2 side, from `xrenv/spec.py`:

* **Primitive axes**: `cylinder`, `cone`, `sphere` and `torus` are +Y aligned;
  `plane`, `grid`, `honeycomb`, `extrusion` and `text` lie in XY and grow along
  +Z. Getting this wrong tips every rail and motor onto its side.
* **`bounds: [w, d, h]`** means `x ∈ [-w/2, +w/2]`, `z ∈ [-d/2, +d/2]`,
  `y ∈ [0, h]` — the floor is at y = 0, not centred. **`honeycomb`** is emitted
  as de-duplicated wall boxes, never as hollow hex prisms (prisms z-fight along
  every shared cell wall). An **anchor's +Z is its surface normal** and its
  `size` spans local X and Y, which is what lets a document be placed with one
  transform. All four are enforced by `docs/TESSELLATION.md` and the port in
  `tessellate.cpp`.

The C++ writer produces a manifest with sorted keys and the same compact
separators as `json.dumps(..., sort_keys=True, separators=(",",":"))`, so a
document read and written unchanged round-trips byte for byte — with the one
exception that floats are stored as `float` in the reader, so re-serialising a
document that came from Python widens `0.2` to `0.20000000298023224`. Hash
based change detection must therefore always use the desktop's own bytes.

## Controls

| input | action |
|-------|--------|
| right trigger | use the current tool (paint, place a node, press a button) |
| right grip | close the active vector path |
| A / X | undo (stroke, ribbon or path) |
| B / Y | toggle passthrough |
| left menu (or left Y) | open and close the main panel |
| left thumbstick | slide, in the direction you are facing |
| right thumbstick | 30° snap turn |
| left wrist | the always-visible tool and status panel |

Hand tracking, when the runtime offers it, feeds the same interface: the ray
runs along the index finger and a thumb/index pinch is the trigger.

## How it fits together

| file | what it owns |
|------|--------------|
| `main.cpp` | lifecycle glue and the render thread (§5 asks for a plain Activity, so this replaces `android_native_app_glue`) |
| `app.cpp`, `app_ui.cpp` | frame loop, tools, sync plumbing; the in-VR panels |
| `xr_session.cpp` | instance, session, swapchains, spaces, frame loop, passthrough |
| `input.cpp` | action sets, Touch Plus bindings, hand tracking fallback |
| `renderer.cpp`, `gl_util.cpp` | instanced PBR, overlay stream, MSAA eye targets |
| `env_spec.cpp`, `tessellate.cpp` | §2 specs and the twelve primitives |
| `environment.cpp` | environment switching, cross-fade, miniaturisation |
| `document.cpp` | FCXR document → GPU meshes, placement, ray picking |
| `paint.cpp`, `vector_edit.cpp` | §4 paint layers and ribbons; Bézier paths |
| `sync_client.cpp` | §3 over BSD sockets, on a worker thread |
| `fcxr.cpp`, `json.cpp`, `png.cpp` | the formats, with no external libraries |
| `storage.cpp` | the on-device library, thumbnails and settings |

Three threads: the render thread (OpenXR, GL, all app state), the sync client's
HTTP worker, and a scratch thread that parses and tessellates an environment
spec so switching rooms does not drop frames. Java never touches either of the
first two — `jni_bridge.cpp` only enqueues work.

## Performance notes

The budget is roughly **75k triangles of environment plus the loaded document**
at 90 Hz. The environment specs are instanced hard: the Bambu X1C's 969 parts
are only 192 distinct shapes, and the laser cutter's 651 are 137, so the
renderer tessellates each shape once and draws the repeats with
`glDrawElementsInstanced`. Per eye there is frustum culling on world-space
AABBs, and draws are sorted by (material, mesh) so a run of identical parts
collapses into one call.

MSAA uses `GL_EXT_multisampled_render_to_texture` when the driver has it (free
on the Adreno tiler), an explicit multisample renderbuffer plus a blit
otherwise, and falls back to no MSAA if neither works.

## Verifying interoperability

```sh
# Validate a package with the same rules the C++ reader uses
python3 tools/verify_fcxr.py scene.fcxr
python3 tools/verify_fcxr.py --manifest scene.fcxr | jq .

# Keep the in-app font in step with the Python one
python3 tools/gen_glyphs.py --check
```

`verify_fcxr.py` deliberately re-implements §1 rather than importing
`xrsync.fcxr`, so a disagreement between the two Python readers is as visible
as a disagreement between Python and C++.
