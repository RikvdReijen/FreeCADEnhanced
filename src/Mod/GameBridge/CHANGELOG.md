# GameBridge changelog

## 1.0.0

First release. Exports FreeCAD documents to Unreal Engine, Unity and Blender,
and keeps a live link open to a running engine.

### Export

* One place where the four coordinate systems and three unit scales are
  written down, with the two consequences handled rather than remembered:
  mirroring conversions reverse triangle winding, and placements are
  conjugated rather than rotated.
* glTF 2.0 (`.glb` and `.gltf`) and Wavefront OBJ, written to spec with no
  third-party dependency.
* A `.gbscene` manifest carrying the hierarchy, engine-side asset names, the
  FreeCAD object each node came from, and every placement both as a matrix and
  decomposed into translation, rotation and scale.
* Per-engine layout: one asset per solid with its pivot at its own origin for
  Unreal and Unity, one file holding the hierarchy for Blender.
* Face-by-face tessellation, which keeps per-face materials and gives smooth
  shading on curved faces while leaving edges sharp.
* Asset names sanitised per engine, with collisions numbered rather than
  silently overwriting each other.
* FreeCAD's Phong appearance converted to metallic-roughness.

### Live link

* A local server that pushes the document as it is edited, sending only what
  changed: geometry is keyed by checksum, so moving a part costs a transform.
* Never blocks FreeCAD's GUI thread; a client that falls behind is resynced
  rather than queued at without limit.
* Loopback only unless explicitly told otherwise, and then only with a token.
* Selection sync: clicking a part in the engine selects it in FreeCAD.

### Engine side

* Unreal: an editor Python script that imports the assets and places the
  actors, and checks the result against the manifest's bounds.
* Unity: an editor package that watches for exports and rebuilds the hierarchy,
  with the same bounds check.
* Blender: an importer that is also an add-on, plus a live-link add-on that
  mirrors the document into the viewport as you work.

### Elsewhere

* A command-line exporter for build servers.
* 326 unit tests that run on a bare `python3`.
