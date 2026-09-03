# Mixed reality capture (MRC) — research, design and integration

This document covers the `xrmrc` package: what mixed reality capture actually
is in the SteamVR/OBS/LIV ecosystem, what could be established from primary
sources, what could **not**, and what `xrcore` has to expose for the feature to
work.

Everything asserted here has a source. Where a claim could not be verified from
a primary source it is marked **unverified** and the reason is given. No API
name, extension name, struct layout or configuration field in this document or
in `xrmrc` was invented.

---

## 1. What mixed reality capture is

Filming a person inside a VR scene needs the scene drawn twice more from a
*third* camera that is co-located with a real camera:

* the part of the scene **in front of** the person, with an alpha channel, and
* the part **behind** them.

A compositor then stacks background → filmed person (keyed or matted) → 
foreground, and the person appears to stand inside the virtual world with
geometry passing in front of and behind them. Everything else — the calibration
file, the quadrant layout, the split plane — is machinery for getting those two
layers plus a matte out of a game and into a compositor.

---

## 2. The four-quadrant convention

### 2.1 What the layout is

The de-facto standard, established by Valve's SteamVR Unity plugin and adopted
by LIV's legacy mode and by every OBS recipe in the wild, is a single frame
split into quarters:

```
+-----------------------------+-----------------------------+
| foreground colour           | foreground alpha            |
| scene nearer than the split | matte for the quadrant left |
+-----------------------------+-----------------------------+
| background colour           | first-person (HMD) view     |
| the scene from the camera   |                             |
+-----------------------------+-----------------------------+
```

This comes straight from the reference implementation,
[`SteamVR_ExternalCamera.cs`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Scripts/SteamVR_ExternalCamera.cs):

* `RenderNear()` draws `Rect(0, 0, w, h)` with `colorMat` and
  `Rect(w, 0, w, h)` with `alphaMat`, where `w = Screen.width / 2` and
  `h = Screen.height / 2`. `Graphics.DrawTexture` works in GUI coordinates,
  whose origin is **top-left**, so those are the top-left and top-right
  quadrants.
* `RenderFar()` draws `Rect(0, h, w, h)` with `colorMat` — the bottom-left
  quadrant.
* `OnEnable()` moves every ordinary game camera to
  `cam.rect = new Rect(0.5f, 0.0f, 0.5f, 0.5f)`. Unity's `Camera.rect` is a
  normalised viewport with a **bottom-left** origin, so that is the bottom-right
  quadrant.

Two independent secondary sources describe the same layout in the same words:

* [How To Live Stream Mixed Reality (StreamShark)](https://streamshark.io/blog/live-stream-mixed-reality/)
  — "the top left is 'foreground', the top right is the 'foreground alpha
  layer', the bottom left is the 'background' and finally the bottom right is
  the first person view", together with the OBS crop numbers for a 1920×1080
  input (foreground: bottom 540, right 960; foreground alpha: left 960, bottom
  540; background: top 540, right 960; first-person: left 960, top 540).
* [About "Mixed Reality" (Dario Laverde)](https://medium.com/@dariony/about-mixed-reality-and-a-how-to-part-1-28387e792a4).

### 2.2 An honest correction to the brief

The task described the four quadrants as *foreground colour, foreground alpha,
background colour, background alpha*. **The ecosystem's fourth quadrant is the
first-person view, not a background alpha.** The background layer is opaque by
construction — it is the bottom of the stack — so a matte for it carries no
information.

`xrmrc.compositor` therefore defaults to `FOURTH_FIRST_PERSON` (the convention)
and offers `FOURTH_BACKGROUND_ALPHA` and `FOURTH_BLANK` as explicit opt-ins,
the former being useful for debugging what the compositor thinks the background
is. The quadrant *names* in the code keep the four-way vocabulary so the
mapping is obvious either way.

### 2.3 The background pass draws the whole scene

`RenderFar()` renders with `cam.nearClipPlane = config.near` and
`cam.farClipPlane = config.far` — the **whole** depth range, not just the part
beyond the split. Near geometry therefore appears in the background quadrant as
well as the foreground one; the composite is still correct because the
foreground layer is drawn over the person, which is drawn over the background.

`xrmrc` reproduces this as `BACKGROUND_FULL_SCENE` (the default, so our output
matches what every tool expects) and offers `BACKGROUND_BEYOND_SPLIT`, which
moves the background's near plane out to the split so the two layers are
disjoint. That variant is *not* the convention and is off by default.

### 2.4 Alpha

The reference implementation does not compute alpha analytically. It clears the
camera to `Color.clear` (alpha 0), renders, and then blits through two
different materials: `Custom/SteamVR_ColorOut` for the colour quadrant and
`Custom/SteamVR_AlphaOut` for the matte quadrant. The matte is simply the
rendered alpha channel written out as luminance, so "did anything draw here"
becomes "is this pixel white".

Two details of the reference implementation matter:

* the near pass is re-rendered with any `PostProcessingBehaviour` disabled
  before the alpha blit, "since they override alpha" — post-processing that
  writes opaque alpha destroys the matte;
* `clipMaterial.color` is set from the config's `r`/`g`/`b`/`a` — the "chroma
  key override" — which is what colours the clipping surface.

---

## 3. The foreground/background split

### 3.1 How the split plane is placed

`SteamVR_ExternalCamera.GetTargetDistance()`:

```csharp
var forward = new Vector3(offset.forward.x, 0.0f, offset.forward.z).normalized;
var targetPos = target.position
              + new Vector3(target.forward.x, 0.0f, target.forward.z).normalized
                * config.hmdOffset;
var distance = -(new Plane(forward, targetPos)).GetDistanceToPoint(offset.position);
return Mathf.Clamp(distance, config.near + 0.01f, config.far - 0.01f);
```

Unwound, since `Plane(n, p).GetDistanceToPoint(q) == dot(n, q) - dot(n, p)`:

> **split = dot( horizontal(camera.forward), targetPos − camera.position )**,
> clamped to `[near + 0.01, far − 0.01]`,
> where `targetPos = hmd.position + horizontal(hmd.forward) * hmdOffset`.

Both forward vectors are flattened onto the ground plane first. That matters:
without flattening, tilting the camera downwards would drag the split plane
towards the floor and the subject would slide out of the foreground layer.

`hmdOffset` pushes the split in front of (or behind) the player's face along
their own horizontal facing — the knob for "my hands keep getting cut in half".

### 3.2 How the split is applied

`RenderNear()` does **not** pull the camera's far clip plane in. It places a
1000×1000 quad carrying a "clear everything" material at

```
dist = Mathf.Clamp(GetTargetDistance() + config.nearOffset, config.near, config.far)
```

metres in front of the camera, parented to the camera so it always faces it.
The comment in the source explains why: *"using camera clip causes problems with
shadows"*. `xrmrc.compositor.CoinQuadrantRenderer` makes the same choice, for
the same reason, with an `SoFaceSet` under an `SoSwitch`.

`nearOffset` therefore nudges the plane; `farOffset` is declared in the config
struct and **is never read** by the reference implementation. `xrmrc` exposes
it, but only the non-default `BACKGROUND_BEYOND_SPLIT` mode uses it, and this
is stated in the code.

### 3.3 Implemented in `xrmrc`

`compositor.split_distance()`, `compositor.foreground_clip_distance()` and
`compositor.background_near_distance()`. Pure functions; covered by
`Tests/test_mrc.py`.

---

## 4. `externalcamera.cfg`

### 4.1 There is no specification

The only normative artefact is the reference implementation. Valve never
documented the format: [ValveSoftware/openvr#800](https://github.com/ValveSoftware/openvr/issues/800),
"Steamvr quadrant mode for mixed reality — Are there any examples /
documentation for this? I don't see it anywhere", is still unanswered.

`SteamVR_Render.cs` sets `externalCameraConfigPath = "externalcamera.cfg"`, a
path relative to the process working directory (i.e. next to the game
executable), and the external camera is enabled by the file's mere presence.

### 4.2 Every field

From the `Config` struct and `ReadConfig()` of
[`SteamVR_ExternalCamera.cs`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Scripts/SteamVR_ExternalCamera.cs):

| key | type | meaning |
|---|---|---|
| `x`, `y`, `z` | float | camera position offset from the tracked device, metres, **Unity frame** (left handed, +Z forward) |
| `rx`, `ry`, `rz` | float | camera rotation, degrees, Unity euler (`Quaternion.Euler(rx, ry, rz)` — applied Z, then X, then Y) |
| `fov` | float | **vertical** field of view in degrees (assigned to `Camera.fieldOfView`) |
| `near` | float | near clip distance, metres |
| `far` | float | far clip distance, metres |
| `sceneResolutionScale` | float | overrides `SteamVR_Camera.sceneResolutionScale` while capture is on; ignored when ≤ 0 |
| `frameSkip` | float | render the capture camera only when `frameCount % (frameSkip + 1) == 0`; negative treated as 0 |
| `nearOffset` | float | added to the split distance before the clip quad is placed |
| `farOffset` | float | **declared but never read** by the reference implementation |
| `hmdOffset` | float | metres along the HMD's horizontal forward, moving the split plane relative to the player |
| `r`, `g`, `b`, `a` | float | "chroma key override" — the colour of the clipping surface |
| `disableStandardAssets` | bool | disables `MonoBehaviour`s whose type name starts with `UnityStandardAssets.` while the capture camera renders |
| `m` | 12 floats, comma separated | an OpenVR `HmdMatrix34_t`; when present it **overwrites** `x/y/z/rx/ry/rz` |

Parsing rules the reference implementation actually follows:

* `line.Split('=')` with `split.Length == 2` — a line without exactly one `=`
  is skipped. This is why the widely-circulated sample files disable a matrix
  by writing `//m=…`: the key becomes `//m`, matches no field, and is ignored.
* every key except `m` and `disableStandardAssets` is set by **reflection** on
  the field of the same name, as a `float`. Unknown keys are silently ignored.
* the whole of `ReadConfig` is wrapped in `try { … } catch { }` — a malformed
  file leaves the previously-loaded values in place rather than failing.
* changes are picked up live through a `System.IO.FileSystemWatcher` on
  `NotifyFilters.LastWrite`.

### 4.3 Defaults

**There are no defaults in the format.** Every field of
[`SteamVR_ExternalCamera.prefab`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Resources/SteamVR_ExternalCamera.prefab)
ships at `0`, which is why the Valve engineer's guidance in the SteamVR
developer forum was that "at a minimum, you need to set the near and far clip
distances since they are treated explicitly".

The values in the sample files that circulate in the community are
`fov=60 near=0.1 far=100` (sometimes `far=1000`). `xrmrc` uses those as its own
defaults so that a partial file still renders something, and says so in the
code — they are *our* defaults, not the format's.

Real-world files also carry keys that are not in the struct at all — `smaa=2`
appears in one of the most-copied samples. `xrmrc.externalcamera` preserves
unknown keys verbatim so a load/save cycle does not throw away another tool's
setting.

### 4.4 Coordinate systems — the one subtle part

`x/y/z/rx/ry/rz` are in Unity's **left-handed, +Z-forward** frame. `m` is an
OpenVR `HmdMatrix34_t`, which is **right handed, −Z forward** — the same
convention as OpenXR, and therefore the same as everything else in this
workbench.

`SteamVR_Utils.RigidTransform(HmdMatrix34_t)` is the bridge, and it is exactly
the reflection `S = diag(1, 1, −1)`:

```csharp
m[0,0]= pose.m0;  m[0,1]= pose.m1;  m[0,2]=-pose.m2;  m[0,3]= pose.m3;
m[1,0]= pose.m4;  m[1,1]= pose.m5;  m[1,2]=-pose.m6;  m[1,3]= pose.m7;
m[2,0]=-pose.m8;  m[2,1]=-pose.m9;  m[2,2]= pose.m10; m[2,3]=-pose.m11;
```

i.e. `p_unity = S·p_xr` (only Z flips) and `R_unity = S·R_xr·S`, which in
quaternions is `q_unity = (−x, −y, z, w)`. The map is an involution, so the
same expression converts both ways. `xrmrc.camera.unity_to_xr_position` /
`unity_to_xr_orientation` implement it, and `Tests/test_externalcamera.py`
checks over two thousand random poses that the `m` route and the euler route
produce the *same* OpenXR pose to within 4.4 × 10⁻¹⁶.

---

## 5. LIV

### 5.1 What is publicly documented

* LIV ships **SDK v2.0 for Unity** and an **SDK for Unreal**. Both are obtained
  from LIV's developer portal (`dev.liv.tv`) behind a login, with documentation
  at `docs.liv.tv` / `mrc-docs.liv.tv`.
* The architecture is described by LIV as: the SDK "spawns a camera inside your
  game which is controlled by LIV", the game renders "into a background and
  foreground, to allow the user's body to be composited in", the two are
  "separated by clipping geometry, based on the user's location within the
  scene", and the textures are "submitted for composition" to an out-of-engine
  compositor that does latency compensation. That is the same model as §2–3
  above, moved from a quadrant frame into a shared-texture bridge.
* The Unreal SDK documentation states **DirectX 11 is required for all LIV
  functionality and DirectX 12 is not supported**, and that the supported
  platform is Windows.
* LIV's Unity SDK bridge is documented as excluded from non-Windows platforms.
* For creators, LIV's help desk instructs that the OpenXR runtime must be set
  to **SteamVR** for LIV mixed reality to work.
* Legacy support: LIV can consume a game that has no SDK at all through the
  plain `externalcamera.cfg` quadrant path.

Sources: [LIV SDK integration (help desk)](https://help.liv.tv/hc/en-us/articles/4402107186194-LIV-SDK-integration),
[LIV on PCVR](https://mrc-docs.liv.tv/intro/platform-pcvr),
[LIV Unreal SDK](https://mrc-docs.liv.tv/sdk-for-unreal),
[LIV SDK v2.0 for Unity](https://mrc-docs.liv.tv/sdk-for-unity),
[Virtual Desktop OpenXR runtime must be set to SteamVR](https://help.liv.tv/hc/en-us/articles/26092454696082-Virtual-Desktop-OpenXR-runtime-must-be-set-to-SteamVR).

> **Caveat on these sources.** Every `liv.tv` domain is blocked by this
> environment's network egress proxy, so the LIV pages above could be read only
> through search-result summaries, not fetched directly. The claims taken from
> them are the ones that appeared consistently across independent summaries and
> that are corroborated elsewhere (the DX11 requirement, the Windows-only
> bridge, the Unity/Unreal-only SDK list, the SteamVR-runtime requirement). Any
> reader with access should re-check them against the pages themselves; nothing
> in `xrmrc` depends on them beyond the capability probe's wording.

### 5.2 What does **not** exist

**There is no public native or C LIV SDK.** LIV's GitHub organisation
([github.com/LIV](https://github.com/orgs/LIV/repositories)) holds twenty
repositories: per-game mods (`BoneworksLIV`, `GorillaTagLIV`, `BonelabLIV`,
`ow-liv`), tooling (`RotatoExpress`, `libdshowcapture`, an `FFmpeg` mirror, a
`swig` fork for IL2CPP), and two graphics-API extension wranglers,
[`XREW`](https://github.com/LIV/XREW) (OpenXR) and `VKEW` (Vulkan). `XREW` is a
generic `xrGetInstanceProcAddr` wrangler — it is not an MRC interface. There is
no `LIV/SDK` repository and no headers to build against.

**There is no LIV OpenXR extension.** LIV holds a registered author tag in the
Khronos OpenXR registry (`<tag name="LIV" author="LIV" …>`), and has reserved
ten extension slots — `XR_LIV_extension_187` through `XR_LIV_extension_196`.
Every one of them is declared exactly like this in
[`xr.xml`](https://github.com/KhronosGroup/OpenXR-SDK/blob/main/specification/registry/xr.xml):

```xml
<extension name="XR_LIV_extension_187" number="187" type="instance" supported="disabled">
    <require>
        <enum value="1"                                name="XR_LIV_extension_187_SPEC_VERSION"/>
        <enum value="&quot;XR_LIV_extension_187&quot;" name="XR_LIV_EXTENSION_187_EXTENSION_NAME"/>
    </require>
</extension>
```

`supported="disabled"` with no structures, no commands and no specification
text: these are reserved numbers, not an API. (Checked against the registry at
OpenXR 1.1.63.)

### 5.3 Therefore

A native LIV binding is **not possible** from this workbench, for three
independent reasons, any one of which would be sufficient:

1. no public native/C SDK exists to bind to;
2. the SDKs that do exist are engine plugins distributed under a portal login,
   which we could not vendor into FreeCAD even if they fit;
3. the SDK requires DirectX 11, and the XR viewer is Coin3D on OpenGL, on
   Windows and Linux both.

`xrmrc.liv` says exactly this rather than shipping a fake binding.
`xrmrc.liv.probe()` returns a `LivStatus` with one `Check` per condition —
`native_sdk`, `openxr_extension`, `platform`, `externalcamera_cfg` — each with
a human-readable reason, and `liv_available()` is True only when LIV can
actually be driven. The OpenXR check looks for the **author-tag prefix**
`XR_LIV_` in the runtime's advertised extension list rather than for a name we
guessed, so if LIV ever publishes one of those reserved slots the probe starts
reporting it by itself.

What `xrmrc` *does* implement for LIV is the external-camera path LIV's legacy
quadrant mode consumes: `MODE_LIV` guarantees a valid `externalcamera.cfg`
exists (writing a starting one if not) and produces the quadrant frame of §2.

### 5.4 What would be needed for the proprietary path

To ship a real LIV SDK integration someone would have to:

* obtain the LIV SDK from `dev.liv.tv` and accept its licence — and establish
  that its terms permit redistribution inside an LGPL workbench, which is the
  first thing that would have to be checked and is not something this work can
  assert either way;
* have LIV publish, or provide under that licence, a **native** interface: the
  shared-memory/shared-texture bridge protocol the Unity and Unreal SDKs speak
  to the compositor. None of it is public today;
* provide DirectX 11 textures. On Windows that means a D3D11 interop path for
  the Coin3D/OpenGL render target (`WGL_NV_DX_interop2` is the usual route);
  on Linux it is simply not available, so LIV would remain Windows-only here.

Until that first point is settled, the legacy quadrant path is not a fallback
but the *right* answer: it needs nothing proprietary, LIV consumes it, and so
does everything else.

---

## 6. Meta Quest MRC

For the standalone Quest application (`quest/`), the picture is different and
better documented.

**Camera discovery has a real, published OpenXR extension.**
`XR_OCULUS_external_camera` (extension 227, spec version 1) is in the Khronos
registry as `supported="openxr"` with a full definition:

```
xrEnumerateExternalCamerasOCULUS(session, cameraCapacityInput,
                                 cameraCountOutput, cameras)

XrExternalCameraOCULUS            { type, next, name[32], intrinsics, extrinsics }
XrExternalCameraIntrinsicsOCULUS  { lastChangeTime, fov, virtualNearPlaneDistance,
                                    virtualFarPlaneDistance, imageSensorPixelResolution }
XrExternalCameraExtrinsicsOCULUS  { lastChangeTime, cameraStatusFlags,
                                    attachedToDevice, relativePose }
```

with `XrExternalCameraStatusFlagsOCULUS` = `CONNECTED`, `CALIBRATING`,
`CALIBRATION_FAILED`, `CALIBRATED`, `CAPTURING`, and
`XrExternalCameraAttachedToDeviceOCULUS` = `NONE`, `HMD`, `LTOUCH`, `RTOUCH`.
Source: [`xr.xml`](https://github.com/KhronosGroup/OpenXR-SDK/blob/main/specification/registry/xr.xml).
`pyopenxr` already exposes all of it (`xr.enumerate_external_cameras_oculus`,
`xr.OCULUS_EXTERNAL_CAMERA_EXTENSION_NAME`, `xr.ExternalCameraOCULUS`), so the
desktop side could use it too where the runtime offers it.

**Frame submission does not.** There is no OpenXR extension for submitting MRC
frames on Quest. Meta's documented native path is **OVRMRCLib**, shipped in the
Meta native development package under the Oculus SDK licence, with
`ovrm_GetExternalCameraCount()`, `ovrm_GetExternalCameraIntrinsics()`,
`ovrm_GetExternalCameraExtrinsics()`, `ovrm_SyncMrcFrame()`,
`ovrm_EncodeMrcFrame()` and `ovrm_EncodeMrcFrameWithDualTextures()`; it loads a
camera configuration file and encodes/streams the MRC output over Wi-Fi to OBS.
Source: [Native Android Mixed Reality Capture](https://developers.meta.com/horizon/documentation/native/android/android-native-mrc/).

> **Caveat.** `developers.meta.com` is also blocked by this environment's egress
> proxy; the OVRMRCLib function names above come from search-result summaries of
> that page, corroborated by community forum threads about including
> `OVR_Mrc_Shim.h` in a native `hello_xr`. Treat the exact signatures as
> **unverified** until read from the SDK's own header. The *shape* of the claim
> — a Meta-licensed native library, not an OpenXR extension — is consistent
> across all sources found.

**Not implemented here.** `xrmrc` targets the desktop viewer; the Quest app is
a separate C++ program (`quest/`) and adding OVRMRCLib to it is a separate
piece of work with its own licence question. It is written down here so the next
person does not have to redo the research.

**A different, standards-based option** exists for headsets whose runtime
supports it: `XR_MSFT_secondary_view_configuration` (extension 54) together
with `XR_MSFT_first_person_observer` (extension 55), which adds the view
configuration type
`XR_VIEW_CONFIGURATION_TYPE_SECONDARY_MONO_FIRST_PERSON_OBSERVER_MSFT`. The
runtime asks the application for an extra first-person view when capture
starts. Both are in the Khronos registry as `supported="openxr"`, and `pyopenxr`
exposes them. This is HoloLens/Magic Leap territory rather than SteamVR, so it
is not what a FreeCAD user on a tethered PC headset will hit, but it is the one
genuinely standardised MRC mechanism in OpenXR and is worth knowing about.

---

## 7. What `xrmrc` implements

```
xrmrc/
├── camera.py          poses, quaternions, Unity↔OpenXR, pose sources,
│                      smoothing, lens/focal settings
├── externalcamera.py  parse / write / validate externalcamera.cfg,
│                      pose + projection
├── compositor.py      quadrant rectangles, split plane, projections,
│                      FramePlan; CoinQuadrantRenderer for the GL side
├── output.py          frame sinks: spectator window, raw/FIFO, image
│                      sequence; rate limiting, async queue, drop counting
├── liv.py             capability probe + the external-camera path
├── session.py         MRCSession: modes, hot reload, status
└── __init__.py        public API
```

### 7.1 Built on the existing third-person camera

The workbench already has a tracked third-person camera: `setup_tpp_camera`,
`setup_tpp_camera_scene`, `update_tpp_camera`, `prepare_tracker` and the
`fbo_tpp` / `fbo_tpp_texture` path in `xrcore/commonXR.py`, documented in
`TPP_Camera_Tracker.md`. MRC is that camera rendered four times into four
viewports instead of once into one, so `xrmrc` **reuses** it rather than
building a second one:

* the same `/user/vive_tracker_htcx/role/camera` device drives
  `camera.TrackedPose`;
* the same `TPPCam*` preferences are read by `camera.LensSettings` and
  `camera._tracker_offset_from_preferences`, including the millimetre→metre
  conversion and the Y/Z swap that `read_preferences` performs;
* the same `SoPerspectiveCamera`, `tpp_cam_root` and `fbo_tpp` are used by
  `compositor.CoinQuadrantRenderer`.

One nice confirmation that the preference semantics were read correctly: the
`TPPCamAspectW`/`TPPCamAspectH` defaults of 6.29 and 4.71 are the millimetre
dimensions of a Raspberry Pi HQ camera sensor, and
`2·atan(4.71 / (2 · 6 mm)) = 42.88°` is exactly the `TPPCamVFov` default. So
those two keys are both the aspect ratio and the sensor size, and
`LensSettings.focal_length()` recovers the 6 mm lens from them.

### 7.2 Modes

`session.MRCSession` has four: `OFF`, `TPP` (one full-frame pass — the classic
third-person camera, expressed as a one-pass `FramePlan`), `QUADRANT_MRC`, and
`LIV` (the same frame, plus the guarantee that a valid `externalcamera.cfg`
exists). Transitions all go through `set_mode`, so the output pipeline is
opened and closed in exactly one place.

Which FOV wins is worth stating plainly. In `QUADRANT_MRC` and `LIV` the
**calibration file's `fov`** decides the shot, because that is what every other
tool in the ecosystem assumes and because a user's measured calibration should
not be quietly overridden. `MRCSession(use_camera_lens=True)` (or
`set_lens_override(True)`) flips that so the `TPPCam*` lens wins instead, which
is how the in-VR menu can reframe without editing a calibration file. `TPP`
mode always uses the lens, because `TPPCamVFov` *is* the third-person camera's
field of view and always has been.

### 7.3 Never stalling the render loop

Three separate mechanisms, because a capture that judders the headset is worse
than no capture:

* `OutputPipeline.wants_frame()` is asked *before* the GPU read-back, so frames
  the rate limiter does not want are never read back at all;
* `compositor.should_render()` applies the calibration's own `frameSkip`;
* `AsyncSink` puts a slow sink behind a bounded queue with `put_nowait`; when
  the queue is full a frame is dropped and counted rather than the caller
  blocked. `drop_oldest` (default) keeps the stream live; the alternative keeps
  a contiguous run.

Every sink counts `submitted`, `written`, `dropped` and `errors`, and
`MRCSession.status()` surfaces the totals.

### 7.4 Getting frames into OBS

Three documented routes, in increasing order of effort:

1. **Capture the spectator window.** `SpectatorWindowSink` presents the
   quadrant frame in the viewer's existing mirror window; add it in OBS as a
   window capture and apply the four crop filters from §2.1. Zero
   configuration, and the same thing users already do with the third-person
   camera.
2. **A named pipe into ffmpeg.** `RawFrameSink` writes raw pixels to any path.
   On Linux, with [`v4l2loopback`](https://github.com/umlaeute/v4l2loopback/wiki/OBS-Studio):

   ```sh
   sudo modprobe v4l2loopback devices=1 video_nr=10 card_label="FreeCAD MRC" exclusive_caps=1
   mkfifo /tmp/freecad-mrc
   ffmpeg -f rawvideo -pix_fmt rgba -s 1920x1080 -r 30 -i /tmp/freecad-mrc \
          -pix_fmt yuv420p -f v4l2 /dev/video10
   ```

   `/dev/video10` then appears in OBS as an ordinary video capture device.
   Start the reader before the sink: opening a FIFO with no reader fails, and
   `RawFrameSink` opens FIFOs `O_NONBLOCK` precisely so it drops frames instead
   of blocking the render loop.
3. **Stills.** `ImageSequenceSink` writes numbered binary PPM files — stdlib
   only, uncompressed, meant for calibration rather than recording.

### 7.5 Pose sources and smoothing

`camera.py` offers `FixedPose` (a tripod), `TrackedPose` (the Vive tracker),
`FollowHmd` (hangs behind and above the player in their *yaw* frame only, so
head pitch and roll do not roll the shot) and `Orbit`.

`PoseSmoother` uses frame-rate-independent exponential damping,
`alpha = 1 − exp(−dt/tau)`, with separate time constants for position and
rotation. Because `alpha ∈ (0, 1)` for every positive `dt`, and because both
`lerp` and shortest-arc `slerp` are convex combinations, the smoothed pose is
always *between* where it was and where it is going: it converges and can never
overshoot, however far the tracker jumps. `Tests/test_mrc.py` checks both
properties.

---

## 8. Testing

```sh
cd src/Mod/XR && python3 -m unittest discover -s Tests -t .
```

* `Tests/test_externalcamera.py` — parsing real-world samples with comments,
  blank lines, unknown keys, CRLF, BOM and out-of-range values; exact
  round-tripping; hand-computed poses and projection matrices; and the
  agreement between the `m` route and the euler route over thousands of random
  poses.
* `Tests/test_mrc.py` — quadrant rectangles at several resolutions, aspect
  ratios and both pixel origins; the split distance against hand-computed
  values; smoothing convergence and non-overshoot; frame dropping under a slow
  sink; mode transitions; hot reload of a changed file and survival of a
  malformed one.

No FreeCAD, no Qt, no GPU.

---

## 9. Integration required in xrcore

`xrmrc` is complete and testable on its own, but it cannot draw or be reached
without a small number of hooks inside `xrcore/commonXR.py`. **This package
does not modify that file**; here is exactly what it needs.

### 9.1 Own an `MRCSession`

In `XRwidget.__init__` (near where `self.tpp_camera`, `self.tpp_cam_enabled`
and `self.tpp_cam_available` are set up, around line 351):

```python
from xrmrc.session import MRCSession
from xrmrc.compositor import CoinQuadrantRenderer

self.mrc_session = MRCSession(
    config_paths=(<per-user path, e.g. os.path.join(FreeCAD.getUserAppDataDir(), "xr", "externalcamera.cfg")>,),
)
self.mrc_renderer = CoinQuadrantRenderer(self)
self.mrc_session.attach_renderer(self.mrc_renderer)
```

`MRCSession` imports nothing from FreeCAD, so it is safe to construct here.
`xrcore.service` should gain `get_mrc_session()` / `set_mrc_session()` in the
same shape as `get_paint_session()`, so the GUI commands and the in-VR menu can
reach it without importing `commonXR`.

### 9.2 Forward the tracker pose

In `update_tpp_camera` (line ~1533), which already receives the located tracker
pose, after building `tracker_rot` / `tracker_pos`:

```python
from xrmrc.camera import Pose
self.mrc_session.submit_tracker_pose(
    Pose(
        (space_location.pose.position.x,
         space_location.pose.position.y,
         space_location.pose.position.z),
        (space_location.pose.orientation.x,
         space_location.pose.orientation.y,
         space_location.pose.orientation.z,
         space_location.pose.orientation.w),
    ),
    valid=self.tpp_cam_available,
)
```

Note this must be called even when `self.tpp_cam_enabled` is False, because MRC
can be running while the TPP mirror is not. The cleanest placement is in the
tracker block of `handle_xr_input` (line ~1509), right beside the
`self.tpp_cam_available = …` assignment, rather than inside `update_tpp_camera`.

The pose wanted is the **raw** tracker pose in the projection layer's space;
`xrmrc.camera.TrackedPose` applies the `TPPCam*` offset itself, and
`MRCSession` applies the world transform separately (see 9.5).

### 9.3 Drive the capture in the render loop

In `render_frame`, in the mirror block (line ~2253), *before* the existing
`if (self.tpp_cam_enabled and self.tpp_cam_available):` branch:

```python
if self.mrc_session.active:
    plan = self.mrc_session.update(
        dt=self.frame_delta_seconds,       # see 9.6
        hmd_pose=self.hmd_pose(),          # see 9.4
        now=None,
    )
    if plan is not None:
        self.fbo_tpp.bind()
        self.mrc_session.render(plan)      # draws all four viewports
        self.gl_ofc.glCopyTextureSubImage2D(
            self.fbo_tpp_texture.textureId(), 0, 0, 0, 0, 0, w, h)
        self.fbo_tpp.release()
        self.update()
```

`MRCSession.update()` returns `None` cheaply whenever there is nothing to draw
(off, rate limited, `frameSkip`, no camera pose yet), and never raises.

The existing TPP branch can stay exactly as it is; MRC simply takes precedence
when it is active.

### 9.4 Expose the HMD pose

MRC needs the HMD's pose in the same space the tracker is located in, to place
the split plane. The eye view states are already fetched into
`self.eye_view_states`; the head pose is their midpoint. A small helper is the
cleanest thing to add:

```python
def hmd_pose(self):
    """Head pose in the projection layer's space, or None."""
    states = self.eye_view_states
    if not states:
        return None
    from xrmrc.camera import Pose, q_slerp, v_lerp
    left, right = states[0].pose, states[1].pose
    position = v_lerp((left.position.x, left.position.y, left.position.z),
                      (right.position.x, right.position.y, right.position.z), 0.5)
    orientation = q_slerp(
        (left.orientation.x, left.orientation.y, left.orientation.z, left.orientation.w),
        (right.orientation.x, right.orientation.y, right.orientation.z, right.orientation.w),
        0.5)
    return Pose(position, orientation)
```

### 9.5 Apply the world transform

`update_tpp_camera` combines the tracker pose with `self.world_transform` so the
third-person camera follows the user's artificial movement. `CoinQuadrantRenderer`
needs the same thing, and the cleanest way to give it that is a read-only
accessor rather than reaching into the node:

```python
def world_pose(self):
    """The artificial-movement transform, as an xrmrc Pose."""
    from xrmrc.camera import Pose
    t = self.world_transform.translation.getValue()
    r = self.world_transform.rotation.getValue().getValue()  # (x, y, z, w)
    return Pose((t[0], t[1], t[2]), r)
```

`MRCSession` will compose it in front of the camera pose exactly as
`update_tpp_camera` does with `combineLeft`.

### 9.6 A frame delta

The smoother needs the seconds since the last frame. `self.old_time` already
exists in `__init__` (line ~360); exposing the delta the render loop computes
as `self.frame_delta_seconds` (a plain float, seconds) is all that is needed.

### 9.7 Present a frame in the mirror window

`xrmrc.output.SpectatorWindowSink` calls `widget.present_mrc_frame(frame)`.
With the render path of 9.3, the frame is already in `fbo_tpp_texture`, so the
hook is a one-liner that makes `paintGL` pick that texture:

```python
def present_mrc_frame(self, frame):
    self._mrc_presenting = True
    self.update()
    return True
```

and in `paintGL` (line ~2293) the texture choice becomes:

```python
if getattr(self, "_mrc_presenting", False) or (self.tpp_cam_enabled and self.tpp_cam_available):
    texture = self.fbo_tpp_texture
else:
    texture = self.fbo_texture
```

### 9.8 Clean-up

In `terminate` (near the `fbo_tpp` teardown, line ~2450):

```python
self.mrc_session.stop()
self.mrc_renderer.detach()
```

### 9.9 The clip quad

`CoinQuadrantRenderer.clip_switch()` returns an `SoSwitch` holding the "erase
everything beyond the split plane" quad. It has to be a child of the TPP scene
root, added at the **end** of `setup_tpp_camera_scene` so it draws last:

```python
self.tpp_sgrp.addChild(self.mrc_renderer.clip_switch())
```

The renderer keeps it switched off except during the two foreground passes, so
it is invisible to the ordinary TPP path.

### 9.10 Menu and commands (optional, but this is where it belongs)

`xrcore/menu_ext.py` takes new entries in `BUTTONS` — `xr_mrc_cycle_button`
("MRC Mode") and `xr_mrc_toggle_button` ("Capture") — dispatched in `handle()`
to `session.get_mrc_session().cycle()` and `.toggle()`, with
`MRCSession.summary()` feeding the status label. A desktop command mirroring
`toggle_tpp_camera` completes it. None of that touches `commonXR.py`.

### 9.11 Build and documentation plumbing

`CMakeLists.txt` needs the new package installed, in the same shape as the
others:

```cmake
FILE(GLOB XR_Mrc_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrmrc/*.py)
SOURCE_GROUP("xrmrc" FILES ${XR_Mrc_SRCS})
INSTALL(FILES ${XR_Mrc_SRCS} DESTINATION Mod/XR/xrmrc)
```

and `${XR_Mrc_SRCS}` added to whichever target list `${XR_Paint_SRCS}` is in.

`ARCHITECTURE.md`'s package tree and `README.md`'s directory listing each want
one more line for `xrmrc/`, and `README.md`'s "Getting started" section is the
natural home for a short "Mixed reality capture" entry pointing here.

### 9.12 Preferences

`xrmrc.camera.MRCCamera.from_preferences` reads the existing `TPPCam*` keys
plus these new ones, all optional and all defaulted:

| key | type | default | meaning |
|---|---|---|---|
| `MRCCamSource` | string | `tracked` | `tracked`, `fixed`, `follow_hmd`, `orbit` |
| `MRCCamDistance` | float | 1.8 | follow: metres behind the player |
| `MRCCamHeight` | float | 0.4 | follow/orbit: metres above |
| `MRCCamSide` | float | 0.0 | follow: metres to the right |
| `MRCCamRadius` | float | 2.5 | orbit: radius in metres |
| `MRCCamOrbitSpeed` | float | 12.0 | orbit: degrees per second |
| `MRCCamPositionSmoothing` | float | 0.08 | seconds; 0 disables |
| `MRCCamRotationSmoothing` | float | 0.12 | seconds; 0 disables |

---

## 10. Sources

Primary:

* [`SteamVR_ExternalCamera.cs`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Scripts/SteamVR_ExternalCamera.cs) — the reference implementation of the format and the quadrant layout
* [`SteamVR_Render.cs`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Scripts/SteamVR_Render.cs) — `externalCameraConfigPath`, `frameSkip` gating
* [`SteamVR_Utils.cs`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Scripts/SteamVR_Utils.cs) — `RigidTransform(HmdMatrix34_t)`, the OpenVR↔Unity handedness conversion
* [`SteamVR_ExternalCamera.prefab`](https://github.com/ValveSoftware/steamvr_unity_plugin/blob/master/Assets/SteamVR/Resources/SteamVR_ExternalCamera.prefab) — every config field defaults to 0
* [OpenXR registry `xr.xml`](https://github.com/KhronosGroup/OpenXR-SDK/blob/main/specification/registry/xr.xml) — `XR_LIV_extension_187…196` (`supported="disabled"`), `XR_OCULUS_external_camera`, `XR_MSFT_secondary_view_configuration`, `XR_MSFT_first_person_observer`
* [`pyopenxr` 1.1.5301](https://pypi.org/project/pyopenxr/) — which of those the workbench's own OpenXR binding exposes
* [ValveSoftware/openvr#800](https://github.com/ValveSoftware/openvr/issues/800) — quadrant mode was never documented
* [github.com/LIV](https://github.com/orgs/LIV/repositories) — the full public repository list; no native SDK
* [LIV/XREW](https://github.com/LIV/XREW) — LIV's OpenXR extension wrangler (not an MRC interface)

Secondary — these corroborate the quadrant layout and the OBS workflow, and
were read as search-result summaries (their domains are blocked by this
environment's egress proxy); the layout claim they support is independently
established from the reference implementation above, so nothing rests on them
alone:

* [How To Live Stream Mixed Reality — StreamShark](https://streamshark.io/blog/live-stream-mixed-reality/)
  — the quadrant description and the 1920×1080 OBS crop numbers quoted in §2.1
* [About "Mixed Reality" (and a how-to part 1) — Dario Laverde](https://medium.com/@dariony/about-mixed-reality-and-a-how-to-part-1-28387e792a4)

Further reading found during the research but **not read** (blocked, listed only
so the next person knows they exist):

* [Making High Quality Mixed Reality VR Trailers and Videos — Kert Gartner](http://www.kertgartner.com/making-mixed-reality-vr-trailers-and-videos)
* [Mixed Reality Cam — Vivecraft](https://www.vivecraft.org/mixed-reality-cam/)

Tooling:

* [v4l2loopback + OBS Studio](https://github.com/umlaeute/v4l2loopback/wiki/OBS-Studio)
  — the `modprobe` line and the OBS side of the virtual-camera recipe in §7.4

LIV and Meta documentation (see the caveats in §5.1 and §6 — these domains are
blocked by this environment's egress proxy and were reachable only as
search-result summaries):

* [LIV SDK integration](https://help.liv.tv/hc/en-us/articles/4402107186194-LIV-SDK-integration)
* [LIV on PCVR](https://mrc-docs.liv.tv/intro/platform-pcvr)
* [LIV SDK v2.0 for Unity](https://mrc-docs.liv.tv/sdk-for-unity)
* [LIV Unreal SDK](https://mrc-docs.liv.tv/sdk-for-unreal)
* [Native Android Mixed Reality Capture — Meta](https://developers.meta.com/horizon/documentation/native/android/android-native-mrc/)
* [Mixed Reality Capture (PC) — Meta](https://developers.meta.com/horizon/documentation/native/pc/dg-mrc/)
