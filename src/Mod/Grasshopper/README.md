# Grasshopper mode (experimental)

A node-based ("dataflow") parametric canvas for FreeCAD, in the spirit of
[Grasshopper](https://www.grasshopper3d.com/), with a mixed-reality front
end inspired by Grasshopper VR / Embodreal: the canvas lies on your table,
anchored to a printed QR marker, and you edit it with a Logitech MX Ink
stylus or bare hands while the geometry preview floats next to it.
Everything you do on the table shows up in the desktop canvas and the
FreeCAD document immediately, and vice versa.

**Status: experimental.** File layout, node ids and the protocol may change.

## What is in the box

| Piece | Where | Notes |
| --- | --- | --- |
| Dataflow graph engine | `grasshoppermode/graph.py` | incremental evaluation, undo, JSON, Grasshopper-style list matching |
| Node library (75 nodes) | `grasshoppermode/nodes.py` | params, math, sets, vectors, curves, solids, transforms, booleans, analysis, preview/bake |
| Geometry backends | `grasshoppermode/geometry.py` | `Part` (OpenCASCADE) inside FreeCAD, bounding-box stub everywhere else |
| Desktop node editor | `grasshoppermode/canvas_qt.py` | QGraphicsView editor sharing the hit-testing of `layout.py` |
| Document objects | `grasshoppermode/document.py` | `Part::FeaturePython` holding the graph JSON; Shape = previews; Bake to `Part::Feature` |
| XR bridge | `session.py`, `protocol.py`, `xrserver.py` | stdlib HTTP + WebSocket server, no extra dependencies |
| WebXR client | `Resources/xr/` | dependency-free WebGL; AR/VR on Quest and ARCore phones, plus a desktop simulator |
| QR markers | `qrcode_gen.py`, `markers.py` | pure-Python QR encoder (validated bit for bit against `qrcode`), canvas frame maths |
| Viewpoint pictures | `media.py`, `Resources/xr/snapshot.js` | rendered / preview / outline captures as Picture nodes, drawable, exportable |
| Phone companion | `Resources/xr/phone.html` | trackpad + tilt (IMU) to pan/zoom the canvas of the other clients |

## Quick start (desktop)

1. Switch to the **Grasshopper (experimental)** workbench.
2. *Example definition* creates sliders → polar array of boxes → cut from a
   cylinder → Preview, and opens the canvas.
3. Double-click empty space (or press Space) to add nodes, drag from an
   output dot to an input dot to wire, drag sliders, double-click an input
   row to type a literal. Ctrl+Z/Y undo/redo, Ctrl+D duplicate, Del delete,
   F fit, middle mouse pans, wheel zooms.
4. *Bake* turns everything reaching a Bake node into regular Part objects.

## Quick start (mixed reality)

1. *Start XR canvas*. FreeCAD serves the client on port 8765 (settings) and
   shows the URLs plus a QR of the URL.
2. WebXR needs a secure context. On a Quest the simplest way is USB + ADB:
   `adb reverse tcp:8765 tcp:8765`, then open `http://localhost:8765/xr` in
   the Quest browser. Alternatively set a certificate/key in the settings
   and open the `https://` URL. Phones with ARCore just scan the QR.
3. *Print canvas marker* (or open `/marker.html` from the dialog). Print at
   100 %; the size in mm is encoded in the marker. Lay marker 0 on the
   table where the top-left corner of the sheet should be.
4. Enter AR. The client anchors the sheet with the best method available:
   * **image tracking** of the QR (Chrome/ARCore) — live, re-snaps when the
     marker moves;
   * **plane detection** (Quest) — "Snap to table" lays the sheet on the
     nearest horizontal plane in front of you;
   * **three stylus taps** on the printed marker's corners — exact placement
     on Quest where the browser cannot see the QR;
   * **persistent anchors** (Quest) — "Pin" stores an `XRAnchor`; the next
     session restores it before you even look at the marker;
   * **manual** — sheet in front of you, or a vertical floating panel.
5. Interact (see the interaction concepts below). The blue geometry on the
   "stage" beyond the far edge of the sheet is the live preview.

Without a headset, *Desktop simulator* on the same page renders the sheet
on a virtual table: left-drag is the stylus tip, holding **F** makes a flat
hand, **G** adds a second hand for zoom, right-drag orbits.

## Interaction rules

These hold in every profile:

| Action | Gesture |
| --- | --- |
| slide the canvas | flat hand on the sheet (always the canvas, never a node); finger or stylus tip dragged on empty sheet; stylus middle button + drag; left thumbstick; phone trackpad or phone tilt |
| zoom | two flat hands; right thumbstick; two fingers on the phone |
| move a node | finger, tip or controller trigger on the node and drag (the selection moves together) |
| select | tap a node; tap empty sheet to clear |
| move the whole panel | pinch or controller grip on the grab bar above the sheet |
| move the preview | pinch or controller grip on the translucent stage under the preview |
| Quest 3 Touch controllers | trigger = touch at the ray hit, grip = grab, sticks pan/zoom |

Three profiles are built in and switchable at runtime (top bar of the client
or the settings). They only change details; the graph edits are the same.

**C — Floating panel (default)**: the canvas hovers vertically in front of
you. Poke it with a finger (through the panel counts as touching, so no
pinch is needed) or point a controller at it and pull the trigger.

**A — Stylus on the table (MX Ink)**

| Action | Gesture |
| --- | --- |
| select | tap the tip on a node (tap the empty sheet to clear) |
| move node(s) | tip down on the header/body and drag; a selected group moves together |
| wire | drag from an output dot, drop on an input dot or on a node body (first free input); drag from a wired input unplugs it |
| slider | tip on the track, drag |
| add node | hold the tip still on the empty sheet → palette (favourites, "more…" pages through categories); releasing a wire on empty sheet opens the palette and auto-connects |
| node menu | hold the tip on a node → delete / duplicate / unplug all |
| cut wire | hold the tip on a wire, or tap it with the rear button held |
| pan | flat hand slides the sheet (also: middle button + drag) |
| zoom | two flat hands |
| multi-select | front button toggles, or the *Multi-select* button |

The tip "press" is the MX Ink tip button/pressure when reported, otherwise
the tip height above the sheet with hysteresis (6 mm down / 12 mm up). Both
are configurable in *Help → Stylus setup*, and *Debug* shows the live
gamepad button indices so the mapping can be corrected for a given browser
version.

**B — Bare hands on the table**: the index fingertip replaces the stylus
tip for taps and drags; a finger on the empty sheet slides it.

## Viewpoint pictures

Like a camera in Gravity Sketch: the 📷 buttons (client) or the
*Viewpoint picture* commands (FreeCAD, photographing the real 3D view with
the matching draw style) capture the preview from where you stand in one of
three modes:

* **rendered** – shaded;
* **preview** – flat colours with edges;
* **outline** – hidden-line edges on white, ready for sketching over.

Each capture becomes a *Picture* node on the same canvas as the Grasshopper
nodes (PNG stored under the user's FreeCAD data folder, `Grasshopper/pictures`).
Draw on the picture area with the stylus, a finger or the mouse; strokes are
stored as vectors with the node, so they can be cleared and survive
save/load. *Export picture* flattens picture + strokes into a PNG and runs
the export hook: copies the image to the clipboard, opens the export URL
(Vizcom by default, configurable) and optionally runs a command with
`{file}` — enough to drop the picture straight into Vizcom or any other
sketch-to-render tool.

## Phone companion

Open `/phone` on a phone connected to the same canvas. One finger on the pad
pans, two fingers zoom, *Tilt to pan* uses the phone's orientation sensor
(dead zone, speed grows with tilt; iOS asks for permission and needs https).
Fit/undo/redo/delete buttons are there too.

## Architecture

```
FreeCAD document  <->  Graph (Python)  <->  Session  <->  WebSocket  <->  WebXR client
      |                     |                                                  |
   Part shapes        Qt canvas (desktop)                             WebGL sheet + previews
```

* `layout.py` is the single source of truth for node rectangles and port
  positions. The Qt scene and the browser both hit-test with the same
  numbers, so a stylus tap and a mouse click resolve identically.
* The browser sends *intents* (`tap`, `move`, `wire`, `set_value`,
  `add_node`, …) and receives snapshots/patches; the graph is authoritative.
* Preview geometry is tessellated in FreeCAD and streamed as meshes plus
  edge polylines; the client centres it on the "stage" and scales it to a
  30 cm footprint.
* The server thread never touches the graph: it hands callables to the Qt
  main thread through a queued signal.

## Tests

```
cd src/Mod/Grasshopper
python3 -m unittest discover -s . -p "test_*.py" -t .
```

88 tests: graph, nodes, layout, QR encoder, marker maths, media, session and
WebSocket server run with a plain interpreter; `test_xr_client.py` drives
the browser client through the desktop simulator (taps, drags, flat-hand
pans, wiring, palette, grabbing the stage and the panel, the phone
companion, pictures and drawing) when `playwright` is installed, skipped
otherwise; `test_freecad.py` needs FreeCAD with Part and is picked up by
`FreeCAD.__unit_test__` (`TestGrasshopperApp`).

## Design decisions so far

* flat hand always moves the canvas; a finger on a node moves the node, a
  finger on empty canvas moves the canvas;
* floating panel is the default placement, table sheet stays available;
* the preview stage and the panel are grabbable and can be carried anywhere;
* Quest 3 with the standard Touch controllers is the first target, MX Ink
  and bare hands are supported alongside;
* one canvas holds both the Grasshopper nodes and the viewpoint pictures.
