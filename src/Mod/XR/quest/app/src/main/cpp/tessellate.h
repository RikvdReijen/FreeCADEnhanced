// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Tessellation of the ARCHITECTURE.md §2 shape primitives.
//
// The exact conventions (winding, normals, UVs, vertex order, defaults) are
// written down in quest/docs/TESSELLATION.md — that document and this file are
// the contract the Python builder in `xrenv/spec.py` has to match, so change
// them together or the desktop and the headset will disagree about which way
// a face points.
//
// Summary:
//   * right handed, Y up, metres; every primitive is centred on the node
//     origin unless the table says otherwise
//   * triangles are counter-clockwise seen from outside/front
//   * `plane` and `extrusion` profiles live in XY; `plane` faces +Z,
//     `extrusion` sweeps along +Z; `cylinder`/`cone` are axis-aligned to +Y
#pragma once

#include <string>
#include <vector>

#include "json.h"
#include "mesh_data.h"

namespace fcxr {

// Tessellates one `shape` object from an environment spec. Returns false and
// fills `error` for an unknown type or invalid parameters.
bool tessellateShape(const json::Value& shape, MeshData* out, std::string* error = nullptr);

// ---- individual primitives (also used directly by the UI and paint code) --
void tessBox(Vec3 size, MeshData* out);
void tessCylinder(float radius, float height, int sides, bool caps, MeshData* out);
void tessCone(float radius, float topRadius, float height, int sides, bool caps, MeshData* out);
void tessSphere(float radius, int rings, int sectors, MeshData* out);
void tessTorus(float radius, float tubeRadius, int sides, int rings, MeshData* out);
void tessTube(const std::vector<Vec3>& path, float radius, int sides, bool caps, MeshData* out);
void tessPlane(Vec2 size, int subdivU, int subdivV, MeshData* out);
bool tessExtrusion(const std::vector<Vec2>& profile, float height, bool closed, MeshData* out);
void tessGrid(Vec2 size, float pitch, float bar, MeshData* out);
void tessHoneycomb(Vec2 size, float cell, float wall, float height, MeshData* out);
void tessText(const std::string& text, float height, float depth, MeshData* out);

// Appends an arbitrarily oriented box. `size` is the full extent along the
// box's own axes, `rotation` maps those axes into the parent frame.
void appendBox(MeshData* out, Vec3 centre, const Quat& rotation, Vec3 size);

// Ear clipping triangulation of a simple polygon in the XY plane.
// `poly` may wind either way; the emitted triangles are counter-clockwise
// (i.e. front facing towards +Z). Returns false for degenerate input.
bool triangulatePolygon(const std::vector<Vec2>& poly, std::vector<uint32_t>* triangles);

}  // namespace fcxr
