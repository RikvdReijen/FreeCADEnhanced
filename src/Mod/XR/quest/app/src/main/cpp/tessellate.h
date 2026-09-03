// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Tessellation of the ARCHITECTURE.md §2 shape primitives.
//
// This file is a direct port of the reference tessellator in
// `xrenv/spec.py` (`tessellate_shape` and the `_tess_*` helpers). It mirrors
// it triangle for triangle: same vertex order, same seam duplication, same
// UVs, same defaults and the same rejection of degenerate input. When the
// Python side changes, change this file with it — the desktop and the headset
// must agree about which way a face points.
//
// Conventions (from the spec.py module docstring):
//   * right handed, Y up, metres; every primitive is centred on the node
//     origin unless the §2 table says otherwise
//   * `cylinder`, `cone`, `sphere`, `torus` are aligned with +Y
//   * `plane`, `grid`, `honeycomb`, `extrusion`, `text` lie in the XY plane
//     and grow along +Z
//   * triangles are counter-clockwise seen from outside, normals point outward
#pragma once

#include <string>
#include <vector>

#include "json.h"
#include "mesh_data.h"

namespace fcxr {

// Tessellates one `shape` object. Returns false and fills `error` for an
// unknown type or for input the Python reference would reject.
bool tessellateShape(const json::Value& shape, MeshData* out, std::string* error = nullptr);

// ---- individual primitives (also used by the UI and paint code) ----------
// `centre` offsets the box; every other primitive is centred on the origin.
void tessBox(Vec3 size, MeshData* out, Vec3 centre = Vec3(0, 0, 0));
void tessCone(float radius, float topRadius, float height, int sides, bool caps,
              MeshData* out);
void tessCylinder(float radius, float height, int sides, bool caps, MeshData* out);
void tessSphere(float radius, int rings, int sectors, MeshData* out);
void tessTorus(float radius, float tubeRadius, int sides, int rings, MeshData* out);
bool tessTube(const std::vector<Vec3d>& path, double radius, int sides, bool caps,
              MeshData* out);
void tessPlane(Vec2 size, int subdivU, int subdivV, MeshData* out);
// The four primitives below take double parameters: their loop bounds and
// containment tests sit on exact boundaries in the generated specs (a bar
// centred exactly on the edge of a grid, a honeycomb wall whose midpoint lands
// exactly on the border), and rounding those inputs to float adds or drops
// whole features relative to the Python reference.
bool tessExtrusion(const std::vector<Vec2d>& profile, double height, bool closed,
                   MeshData* out);
bool tessGrid(double sizeX, double sizeY, double pitch, double bar, MeshData* out);
bool tessHoneycomb(double sizeX, double sizeY, double cell, double wall, double height,
                   MeshData* out);
bool tessText(const std::string& text, float height, float depth, MeshData* out);

// Appends `src` rotated by the orthonormal frame (ex, ey, ez) and translated to
// `origin` — the port of `_Mesh.transformed_copy_into`.
void appendTransformed(MeshData* out, const MeshData& src, Vec3 origin, Vec3 ex, Vec3 ey,
                       Vec3 ez);

// Ear clipping triangulation of a simple polygon in the XY plane, port of
// `xrenv.spec.triangulate_polygon`. Emitted triangles are counter-clockwise.
bool triangulatePolygon(const std::vector<Vec2d>& polygon, std::vector<uint32_t>* triangles);

}  // namespace fcxr
