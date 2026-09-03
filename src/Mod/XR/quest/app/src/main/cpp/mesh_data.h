// SPDX-License-Identifier: LGPL-2.1-or-later
//
// CPU side triangle mesh, shared by the environment tessellator, the FCXR
// loader, the paint ribbons and the UI.
//
// Winding is counter-clockwise when seen from outside/front, normals point
// outward, and UVs use the OpenGL convention (v = 0 at the bottom of the
// image). Everything downstream relies on that; see docs/TESSELLATION.md.
#pragma once

#include <cstdint>
#include <vector>

#include "math3d.h"

namespace fcxr {

struct MeshData {
    std::vector<Vec3> positions;
    std::vector<Vec3> normals;
    std::vector<Vec2> uvs;
    std::vector<uint32_t> indices;

    void clear() {
        positions.clear();
        normals.clear();
        uvs.clear();
        indices.clear();
    }
    bool empty() const { return indices.empty() || positions.empty(); }
    size_t vertexCount() const { return positions.size(); }
    size_t triangleCount() const { return indices.size() / 3; }

    uint32_t addVertex(Vec3 p, Vec3 n, Vec2 uv) {
        positions.push_back(p);
        normals.push_back(n);
        uvs.push_back(uv);
        return static_cast<uint32_t>(positions.size() - 1);
    }
    void addTriangle(uint32_t a, uint32_t b, uint32_t c) {
        indices.push_back(a);
        indices.push_back(b);
        indices.push_back(c);
    }
    // Quad given in loop order a-b-c-d; split as (a,b,c) + (a,c,d).
    void addQuad(uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
        addTriangle(a, b, c);
        addTriangle(a, c, d);
    }
    void reserveMore(size_t verts, size_t tris) {
        positions.reserve(positions.size() + verts);
        normals.reserve(normals.size() + verts);
        uvs.reserve(uvs.size() + verts);
        indices.reserve(indices.size() + tris * 3);
    }

    // Appends `other`, optionally transformed by `xf` (normals get the
    // inverse-transpose treatment).
    void append(const MeshData& other);
    void append(const MeshData& other, const Mat4& xf);

    // Replaces the normals with area weighted smooth normals. Used for the
    // `mesh` primitive when the spec omits them.
    void computeSmoothNormals();

    // Makes every triangle flat shaded by splitting shared vertices.
    void makeFlat();

    Aabb bounds() const;
};

}  // namespace fcxr
