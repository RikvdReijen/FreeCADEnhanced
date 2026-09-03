// SPDX-License-Identifier: LGPL-2.1-or-later
#include "mesh_data.h"

namespace fcxr {

void MeshData::append(const MeshData& other) {
    const uint32_t base = static_cast<uint32_t>(positions.size());
    positions.insert(positions.end(), other.positions.begin(), other.positions.end());
    normals.insert(normals.end(), other.normals.begin(), other.normals.end());
    uvs.insert(uvs.end(), other.uvs.begin(), other.uvs.end());
    indices.reserve(indices.size() + other.indices.size());
    for (uint32_t i : other.indices) indices.push_back(base + i);
}

void MeshData::append(const MeshData& other, const Mat4& xf) {
    const uint32_t base = static_cast<uint32_t>(positions.size());
    const Mat4 nm = normalMatrix(xf);
    reserveMore(other.positions.size(), other.triangleCount());
    for (size_t i = 0; i < other.positions.size(); ++i) {
        positions.push_back(transformPoint(xf, other.positions[i]));
        Vec3 n = i < other.normals.size() ? other.normals[i] : Vec3(0, 1, 0);
        normals.push_back(normalize(transformDir(nm, n)));
        uvs.push_back(i < other.uvs.size() ? other.uvs[i] : Vec2(0, 0));
    }
    for (uint32_t i : other.indices) indices.push_back(base + i);
}

void MeshData::computeSmoothNormals() {
    normals.assign(positions.size(), Vec3(0, 0, 0));
    for (size_t i = 0; i + 2 < indices.size(); i += 3) {
        const uint32_t a = indices[i], b = indices[i + 1], c = indices[i + 2];
        if (a >= positions.size() || b >= positions.size() || c >= positions.size()) continue;
        // Un-normalised cross product => area weighted accumulation.
        Vec3 n = cross(positions[b] - positions[a], positions[c] - positions[a]);
        normals[a] += n;
        normals[b] += n;
        normals[c] += n;
    }
    for (Vec3& n : normals) {
        float l = length(n);
        n = l > 1e-12f ? n / l : Vec3(0, 1, 0);
    }
}

void MeshData::makeFlat() {
    MeshData out;
    out.reserveMore(indices.size(), triangleCount());
    for (size_t i = 0; i + 2 < indices.size(); i += 3) {
        const uint32_t a = indices[i], b = indices[i + 1], c = indices[i + 2];
        if (a >= positions.size() || b >= positions.size() || c >= positions.size()) continue;
        Vec3 n = normalize(cross(positions[b] - positions[a], positions[c] - positions[a]));
        Vec2 ua = a < uvs.size() ? uvs[a] : Vec2(0, 0);
        Vec2 ub = b < uvs.size() ? uvs[b] : Vec2(0, 0);
        Vec2 uc = c < uvs.size() ? uvs[c] : Vec2(0, 0);
        uint32_t i0 = out.addVertex(positions[a], n, ua);
        uint32_t i1 = out.addVertex(positions[b], n, ub);
        uint32_t i2 = out.addVertex(positions[c], n, uc);
        out.addTriangle(i0, i1, i2);
    }
    *this = std::move(out);
}

Aabb MeshData::bounds() const {
    Aabb b;
    for (const Vec3& p : positions) b.add(p);
    return b;
}

}  // namespace fcxr
