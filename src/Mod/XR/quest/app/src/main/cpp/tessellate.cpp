// SPDX-License-Identifier: LGPL-2.1-or-later
#include "tessellate.h"

#include <algorithm>
#include <cmath>
#include <set>

#include "text_font.h"

namespace fcxr {
namespace {

int clampSegments(int n, int lo, int hi) { return n < lo ? lo : (n > hi ? hi : n); }

// Reads an int field with a default, clamped to a sane range.
int optSegments(const json::Value& v, const char* key, int def, int lo = 3, int hi = 512) {
    const json::Value& f = v[key];
    return clampSegments(f.isNumber() ? f.asInt(def) : def, lo, hi);
}
float optFloat(const json::Value& v, const char* key, float def) {
    const json::Value& f = v[key];
    return f.isNumber() ? f.asFloat(def) : def;
}
bool optBool(const json::Value& v, const char* key, bool def) {
    const json::Value& f = v[key];
    return f.isBool() ? f.asBool(def) : def;
}

}  // namespace

// ---------------------------------------------------------------------- box
//
// 24 vertices, one per face corner, so each face gets a flat normal. Face
// order is +X, -X, +Y, -Y, +Z, -Z; within a face the corners run
// (-u,-v) (+u,-v) (+u,+v) (-u,+v) with UV (0,0) (1,0) (1,1) (0,1) and
// cross(u, v) == normal, which makes the winding counter-clockwise from
// outside.
void tessBox(Vec3 size, MeshData* out) {
    const Vec3 h = size * 0.5f;
    struct Face { Vec3 n, u, v; };
    static const Face kFaces[6] = {
        {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}},
        {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
        {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}},
        {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}},
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},
        {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},
    };
    out->reserveMore(24, 12);
    for (const Face& f : kFaces) {
        // Each of n/u/v is a signed unit axis, so a component-wise product
        // with the half extents is exactly the corner offset along that axis.
        const Vec3 uu = f.u * h;
        const Vec3 vv = f.v * h;
        const Vec3 c = f.n * h;
        const uint32_t base = out->addVertex(c - uu - vv, f.n, Vec2(0, 0));
        out->addVertex(c + uu - vv, f.n, Vec2(1, 0));
        out->addVertex(c + uu + vv, f.n, Vec2(1, 1));
        out->addVertex(c - uu + vv, f.n, Vec2(0, 1));
        out->addQuad(base, base + 1, base + 2, base + 3);
    }
}

void appendBox(MeshData* out, Vec3 centre, const Quat& rotation, Vec3 size) {
    MeshData box;
    tessBox(size, &box);
    out->append(box, mat4TRS(centre, rotation, Vec3(1, 1, 1)));
}

// ----------------------------------------------------------------- cylinder
//
// Axis along +Y, centred: y runs from -height/2 to +height/2. Ring vertices
// use x = r*cos(theta), z = r*sin(theta) with theta = 2*pi*i/sides, and the
// seam is duplicated (sides + 1 columns) so the U coordinate is continuous.
// Side quads are (bottom_i, top_i, top_i+1, bottom_i+1) in loop order.
void tessCone(float radius, float topRadius, float height, int sides, bool caps,
              MeshData* out) {
    sides = clampSegments(sides, 3, 512);
    const float hh = height * 0.5f;
    // Profile tangent (dr, dy) -> outward normal (dy, -dr) in the (radial, y)
    // plane; for a cone that is (height, radius - topRadius).
    const float nr = height;
    const float ny = radius - topRadius;
    const float nl = std::sqrt(nr * nr + ny * ny);
    const float nrn = nl > 1e-12f ? nr / nl : 1.0f;
    const float nyn = nl > 1e-12f ? ny / nl : 0.0f;

    out->reserveMore(size_t(sides + 1) * 2 + size_t(sides + 1) * 2 + 2,
                     size_t(sides) * 4);
    const uint32_t base = uint32_t(out->vertexCount());
    for (int i = 0; i <= sides; ++i) {
        const float t = float(i) / float(sides);
        const float a = t * 2.0f * kPi;
        const float ca = std::cos(a), sa = std::sin(a);
        const Vec3 n(ca * nrn, nyn, sa * nrn);
        out->addVertex(Vec3(radius * ca, -hh, radius * sa), n, Vec2(t, 0.0f));
        out->addVertex(Vec3(topRadius * ca, hh, topRadius * sa), n, Vec2(t, 1.0f));
    }
    for (int i = 0; i < sides; ++i) {
        const uint32_t b0 = base + uint32_t(i) * 2;
        const uint32_t t0 = b0 + 1;
        const uint32_t b1 = b0 + 2;
        const uint32_t t1 = b0 + 3;
        if (radius > 1e-9f || topRadius > 1e-9f) out->addQuad(b0, t0, t1, b1);
    }

    if (!caps) return;
    // Bottom cap: normal -Y, fan (centre, p_i, p_i+1).
    if (radius > 1e-9f) {
        const uint32_t c = out->addVertex(Vec3(0, -hh, 0), Vec3(0, -1, 0), Vec2(0.5f, 0.5f));
        const uint32_t ring = uint32_t(out->vertexCount());
        for (int i = 0; i <= sides; ++i) {
            const float a = float(i) / float(sides) * 2.0f * kPi;
            const float ca = std::cos(a), sa = std::sin(a);
            out->addVertex(Vec3(radius * ca, -hh, radius * sa), Vec3(0, -1, 0),
                           Vec2(0.5f + 0.5f * ca, 0.5f + 0.5f * sa));
        }
        for (int i = 0; i < sides; ++i) out->addTriangle(c, ring + uint32_t(i), ring + uint32_t(i) + 1);
    }
    // Top cap: normal +Y, fan (centre, p_i+1, p_i).
    if (topRadius > 1e-9f) {
        const uint32_t c = out->addVertex(Vec3(0, hh, 0), Vec3(0, 1, 0), Vec2(0.5f, 0.5f));
        const uint32_t ring = uint32_t(out->vertexCount());
        for (int i = 0; i <= sides; ++i) {
            const float a = float(i) / float(sides) * 2.0f * kPi;
            const float ca = std::cos(a), sa = std::sin(a);
            out->addVertex(Vec3(topRadius * ca, hh, topRadius * sa), Vec3(0, 1, 0),
                           Vec2(0.5f + 0.5f * ca, 0.5f + 0.5f * sa));
        }
        for (int i = 0; i < sides; ++i) out->addTriangle(c, ring + uint32_t(i) + 1, ring + uint32_t(i));
    }
}

void tessCylinder(float radius, float height, int sides, bool caps, MeshData* out) {
    tessCone(radius, radius, height, sides, caps, out);
}

// ------------------------------------------------------------------- sphere
//
// Latitude ring j = 0 is the +Y pole. phi = pi*j/rings, theta = 2*pi*i/sectors,
// p = (r sin(phi) cos(theta), r cos(phi), r sin(phi) sin(theta)).
// UV: u = i/sectors, v = 1 - j/rings (so v = 1 at the north pole).
void tessSphere(float radius, int rings, int sectors, MeshData* out) {
    rings = clampSegments(rings, 2, 256);
    sectors = clampSegments(sectors, 3, 512);
    const uint32_t base = uint32_t(out->vertexCount());
    out->reserveMore(size_t(rings + 1) * size_t(sectors + 1), size_t(rings) * size_t(sectors) * 2);
    for (int j = 0; j <= rings; ++j) {
        const float fv = float(j) / float(rings);
        const float phi = fv * kPi;
        const float sp = std::sin(phi), cp = std::cos(phi);
        for (int i = 0; i <= sectors; ++i) {
            const float fu = float(i) / float(sectors);
            const float th = fu * 2.0f * kPi;
            const Vec3 n(sp * std::cos(th), cp, sp * std::sin(th));
            out->addVertex(n * radius, n, Vec2(fu, 1.0f - fv));
        }
    }
    const uint32_t stride = uint32_t(sectors + 1);
    for (int j = 0; j < rings; ++j) {
        for (int i = 0; i < sectors; ++i) {
            const uint32_t a = base + uint32_t(j) * stride + uint32_t(i);        // (j, i)
            const uint32_t d = a + 1;                                           // (j, i+1)
            const uint32_t b = a + stride;                                      // (j+1, i)
            const uint32_t c = b + 1;                                           // (j+1, i+1)
            // Loop order a -> d -> c -> b gives outward facing triangles.
            if (j != 0) out->addTriangle(a, d, c);
            if (j != rings - 1) out->addTriangle(a, c, b);
        }
    }
}

// -------------------------------------------------------------------- torus
//
// Main axis +Y, so the ring lies in the XZ plane. Ring i (around the axis)
// uses theta = 2*pi*i/rings, tube section j uses phi = 2*pi*j/sides.
void tessTorus(float radius, float tubeRadius, int sides, int rings, MeshData* out) {
    sides = clampSegments(sides, 3, 256);
    rings = clampSegments(rings, 3, 512);
    const uint32_t base = uint32_t(out->vertexCount());
    out->reserveMore(size_t(rings + 1) * size_t(sides + 1), size_t(rings) * size_t(sides) * 2);
    for (int i = 0; i <= rings; ++i) {
        const float fu = float(i) / float(rings);
        const float th = fu * 2.0f * kPi;
        const Vec3 radial(std::cos(th), 0.0f, std::sin(th));
        const Vec3 centre = radial * radius;
        for (int j = 0; j <= sides; ++j) {
            const float fv = float(j) / float(sides);
            const float ph = fv * 2.0f * kPi;
            const Vec3 n = radial * std::cos(ph) + Vec3(0, 1, 0) * std::sin(ph);
            out->addVertex(centre + n * tubeRadius, n, Vec2(fu, fv));
        }
    }
    const uint32_t stride = uint32_t(sides + 1);
    for (int i = 0; i < rings; ++i) {
        for (int j = 0; j < sides; ++j) {
            const uint32_t a = base + uint32_t(i) * stride + uint32_t(j);
            const uint32_t b = a + 1;
            const uint32_t c = a + stride + 1;
            const uint32_t d = a + stride;
            out->addQuad(a, b, c, d);
        }
    }
}

// --------------------------------------------------------------------- tube
//
// A circle of `radius` swept along `path` with a rotation minimising frame.
// The cross-section basis is (N, B) with B = cross(N, T), which matches the
// cylinder (T = +Y, N = +X, B = +Z) so the winding rule is the same.
void tessTube(const std::vector<Vec3>& path, float radius, int sides, bool caps,
              MeshData* out) {
    if (path.size() < 2) return;
    sides = clampSegments(sides, 3, 256);

    // Drop duplicated consecutive points, which would give a zero tangent.
    std::vector<Vec3> pts;
    pts.reserve(path.size());
    for (const Vec3& p : path) {
        if (pts.empty() || lengthSq(p - pts.back()) > 1e-14f) pts.push_back(p);
    }
    if (pts.size() < 2) return;
    const size_t n = pts.size();

    std::vector<Vec3> tangents(n);
    for (size_t i = 0; i < n; ++i) {
        Vec3 t;
        if (i == 0) t = pts[1] - pts[0];
        else if (i == n - 1) t = pts[n - 1] - pts[n - 2];
        else t = normalize(pts[i] - pts[i - 1]) + normalize(pts[i + 1] - pts[i]);
        tangents[i] = normalize(t);
        if (lengthSq(tangents[i]) < 0.5f) tangents[i] = normalize(pts[i == 0 ? 1 : i] - pts[i == 0 ? 0 : i - 1]);
    }

    // Initial normal: the axis least aligned with the first tangent.
    Vec3 seed(1, 0, 0);
    if (std::fabs(tangents[0].x) > 0.9f) seed = Vec3(0, 1, 0);
    std::vector<Vec3> normalsN(n);
    normalsN[0] = normalize(cross(seed, tangents[0]));
    for (size_t i = 1; i < n; ++i) {
        // Parallel transport: rotate the previous normal by the rotation that
        // takes the previous tangent onto this one.
        const Quat q = quatFromTo(tangents[i - 1], tangents[i]);
        Vec3 nrm = rotate(q, normalsN[i - 1]);
        // Re-orthogonalise against drift.
        nrm = normalize(nrm - tangents[i] * dot(nrm, tangents[i]));
        normalsN[i] = lengthSq(nrm) > 0.5f ? nrm : normalsN[i - 1];
    }

    // Cumulative arc length for V.
    std::vector<float> arc(n, 0.0f);
    for (size_t i = 1; i < n; ++i) arc[i] = arc[i - 1] + length(pts[i] - pts[i - 1]);
    const float total = arc[n - 1] > 1e-9f ? arc[n - 1] : 1.0f;

    const uint32_t base = uint32_t(out->vertexCount());
    const uint32_t stride = uint32_t(sides + 1);
    out->reserveMore(n * stride, (n - 1) * size_t(sides) * 2);
    for (size_t i = 0; i < n; ++i) {
        const Vec3 T = tangents[i];
        const Vec3 N = normalsN[i];
        const Vec3 B = cross(N, T);
        for (int j = 0; j <= sides; ++j) {
            const float fu = float(j) / float(sides);
            const float a = fu * 2.0f * kPi;
            const Vec3 nrm = N * std::cos(a) + B * std::sin(a);
            out->addVertex(pts[i] + nrm * radius, nrm, Vec2(fu, arc[i] / total));
        }
    }
    for (size_t i = 0; i + 1 < n; ++i) {
        for (int j = 0; j < sides; ++j) {
            const uint32_t a = base + uint32_t(i) * stride + uint32_t(j);
            const uint32_t b = a + stride;      // next ring, same angle
            const uint32_t c = b + 1;
            const uint32_t d = a + 1;
            out->addQuad(a, b, c, d);
        }
    }

    if (!caps) return;
    auto cap = [&](size_t ringIndex, Vec3 nrm, bool reverse) {
        const uint32_t centre = out->addVertex(pts[ringIndex], nrm, Vec2(0.5f, 0.5f));
        const Vec3 T = tangents[ringIndex];
        const Vec3 N = normalsN[ringIndex];
        const Vec3 B = cross(N, T);
        const uint32_t ring = uint32_t(out->vertexCount());
        for (int j = 0; j <= sides; ++j) {
            const float a = float(j) / float(sides) * 2.0f * kPi;
            const float ca = std::cos(a), sa = std::sin(a);
            out->addVertex(pts[ringIndex] + (N * ca + B * sa) * radius, nrm,
                           Vec2(0.5f + 0.5f * ca, 0.5f + 0.5f * sa));
        }
        for (int j = 0; j < sides; ++j) {
            if (reverse) out->addTriangle(centre, ring + uint32_t(j) + 1, ring + uint32_t(j));
            else out->addTriangle(centre, ring + uint32_t(j), ring + uint32_t(j) + 1);
        }
    };
    cap(0, -tangents[0], false);            // start cap faces backwards
    cap(n - 1, tangents[n - 1], true);      // end cap faces forwards
}

// -------------------------------------------------------------------- plane
void tessPlane(Vec2 size, int subdivU, int subdivV, MeshData* out) {
    subdivU = clampSegments(subdivU, 1, 512);
    subdivV = clampSegments(subdivV, 1, 512);
    const float hx = size.x * 0.5f, hy = size.y * 0.5f;
    const uint32_t base = uint32_t(out->vertexCount());
    const Vec3 n(0, 0, 1);
    out->reserveMore(size_t(subdivU + 1) * size_t(subdivV + 1), size_t(subdivU) * size_t(subdivV) * 2);
    for (int j = 0; j <= subdivV; ++j) {
        const float fv = float(j) / float(subdivV);
        for (int i = 0; i <= subdivU; ++i) {
            const float fu = float(i) / float(subdivU);
            out->addVertex(Vec3(-hx + size.x * fu, -hy + size.y * fv, 0.0f), n, Vec2(fu, fv));
        }
    }
    const uint32_t stride = uint32_t(subdivU + 1);
    for (int j = 0; j < subdivV; ++j) {
        for (int i = 0; i < subdivU; ++i) {
            const uint32_t a = base + uint32_t(j) * stride + uint32_t(i);
            out->addQuad(a, a + 1, a + stride + 1, a + stride);
        }
    }
}

// ------------------------------------------------------- polygon / extrusion

static float polygonArea(const std::vector<Vec2>& p) {
    float a = 0.0f;
    for (size_t i = 0, n = p.size(); i < n; ++i) {
        const Vec2& u = p[i];
        const Vec2& v = p[(i + 1) % n];
        a += u.x * v.y - v.x * u.y;
    }
    return a * 0.5f;
}

static bool pointInTriangle(Vec2 p, Vec2 a, Vec2 b, Vec2 c) {
    const float d1 = (p.x - b.x) * (a.y - b.y) - (a.x - b.x) * (p.y - b.y);
    const float d2 = (p.x - c.x) * (b.y - c.y) - (b.x - c.x) * (p.y - c.y);
    const float d3 = (p.x - a.x) * (c.y - a.y) - (c.x - a.x) * (p.y - a.y);
    const bool neg = (d1 < 0) || (d2 < 0) || (d3 < 0);
    const bool pos = (d1 > 0) || (d2 > 0) || (d3 > 0);
    return !(neg && pos);
}

bool triangulatePolygon(const std::vector<Vec2>& poly, std::vector<uint32_t>* triangles) {
    if (!triangles || poly.size() < 3) return false;
    triangles->clear();
    const float area = polygonArea(poly);
    if (std::fabs(area) < 1e-16f) return false;

    // Work on a CCW copy; indices map back to the caller's order.
    std::vector<uint32_t> idx(poly.size());
    for (size_t i = 0; i < poly.size(); ++i) idx[i] = uint32_t(i);
    if (area < 0.0f) std::reverse(idx.begin(), idx.end());

    size_t guard = idx.size() * idx.size() + 16;
    while (idx.size() > 3 && guard-- > 0) {
        bool clipped = false;
        for (size_t i = 0; i < idx.size(); ++i) {
            const size_t ip = (i + idx.size() - 1) % idx.size();
            const size_t in = (i + 1) % idx.size();
            const Vec2& a = poly[idx[ip]];
            const Vec2& b = poly[idx[i]];
            const Vec2& c = poly[idx[in]];
            // Convex corner in a CCW polygon has a positive cross product.
            const float crossz = (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y);
            if (crossz <= 0.0f) continue;
            bool contains = false;
            for (size_t k = 0; k < idx.size() && !contains; ++k) {
                if (k == ip || k == i || k == in) continue;
                contains = pointInTriangle(poly[idx[k]], a, b, c);
            }
            if (contains) continue;
            triangles->push_back(idx[ip]);
            triangles->push_back(idx[i]);
            triangles->push_back(idx[in]);
            idx.erase(idx.begin() + long(i));
            clipped = true;
            break;
        }
        if (!clipped) break;  // self intersecting or degenerate; keep what we have
    }
    if (idx.size() == 3) {
        triangles->push_back(idx[0]);
        triangles->push_back(idx[1]);
        triangles->push_back(idx[2]);
    }
    return !triangles->empty();
}

// Profile in XY, swept along +Z from -height/2 to +height/2. A `closed`
// profile also gets flat caps. Outward side normals assume the profile is
// given counter-clockwise; a clockwise profile is flipped on entry so both
// windings produce outward facing walls.
bool tessExtrusion(const std::vector<Vec2>& profileIn, float height, bool closed,
                   MeshData* out) {
    if (profileIn.size() < 2) return false;
    std::vector<Vec2> profile = profileIn;
    // Drop a duplicated closing point.
    if (profile.size() > 2 && length(profile.front() - profile.back()) < 1e-9f)
        profile.pop_back();
    if (profile.size() < 2) return false;
    if (closed && polygonArea(profile) < 0.0f) std::reverse(profile.begin(), profile.end());

    const float hz = height * 0.5f;
    const size_t n = profile.size();
    const size_t segments = closed ? n : n - 1;

    // Perimeter for the U coordinate.
    std::vector<float> arc(n + 1, 0.0f);
    for (size_t i = 0; i < segments; ++i)
        arc[i + 1] = arc[i] + length(profile[(i + 1) % n] - profile[i]);
    const float perimeter = arc[segments] > 1e-9f ? arc[segments] : 1.0f;

    out->reserveMore(segments * 4, segments * 2);
    for (size_t i = 0; i < segments; ++i) {
        const Vec2& p0 = profile[i];
        const Vec2& p1 = profile[(i + 1) % n];
        const Vec2 d = p1 - p0;
        const float dl = length(d);
        if (dl < 1e-12f) continue;
        const Vec3 nrm(d.y / dl, -d.x / dl, 0.0f);  // outward for a CCW profile
        const float u0 = arc[i] / perimeter, u1 = arc[i + 1] / perimeter;
        const uint32_t b0 = out->addVertex(Vec3(p0.x, p0.y, -hz), nrm, Vec2(u0, 0.0f));
        const uint32_t b1 = out->addVertex(Vec3(p1.x, p1.y, -hz), nrm, Vec2(u1, 0.0f));
        const uint32_t f1 = out->addVertex(Vec3(p1.x, p1.y, hz), nrm, Vec2(u1, 1.0f));
        const uint32_t f0 = out->addVertex(Vec3(p0.x, p0.y, hz), nrm, Vec2(u0, 1.0f));
        out->addQuad(b0, b1, f1, f0);
    }

    if (closed && n >= 3) {
        std::vector<uint32_t> tris;
        if (triangulatePolygon(profile, &tris)) {
            // Front cap (+Z): CCW order as triangulated.
            const uint32_t front = uint32_t(out->vertexCount());
            for (const Vec2& p : profile)
                out->addVertex(Vec3(p.x, p.y, hz), Vec3(0, 0, 1), Vec2(p.x, p.y));
            for (size_t i = 0; i + 2 < tris.size(); i += 3)
                out->addTriangle(front + tris[i], front + tris[i + 1], front + tris[i + 2]);
            // Back cap (-Z): reversed winding.
            const uint32_t back = uint32_t(out->vertexCount());
            for (const Vec2& p : profile)
                out->addVertex(Vec3(p.x, p.y, -hz), Vec3(0, 0, -1), Vec2(p.x, p.y));
            for (size_t i = 0; i + 2 < tris.size(); i += 3)
                out->addTriangle(back + tris[i + 2], back + tris[i + 1], back + tris[i]);
        }
    }
    return true;
}

// --------------------------------------------------------------------- grid
//
// A lattice of square section bars in the XY plane: bars of `bar` thickness
// spaced `pitch` apart, running along X and along Y, extruded `bar` along Z
// and centred on the origin.
void tessGrid(Vec2 size, float pitch, float bar, MeshData* out) {
    if (pitch <= 1e-6f || bar <= 1e-9f) return;
    const int nx = std::max(2, int(std::floor(size.x / pitch)) + 1);
    const int ny = std::max(2, int(std::floor(size.y / pitch)) + 1);
    const float spanX = float(nx - 1) * pitch;
    const float spanY = float(ny - 1) * pitch;
    for (int i = 0; i < nx; ++i) {
        const float x = -spanX * 0.5f + float(i) * pitch;
        appendBox(out, Vec3(x, 0, 0), Quat(), Vec3(bar, spanY + bar, bar));
    }
    for (int j = 0; j < ny; ++j) {
        const float y = -spanY * 0.5f + float(j) * pitch;
        appendBox(out, Vec3(0, y, 0), Quat(), Vec3(spanX + bar, bar, bar));
    }
}

// ---------------------------------------------------------------- honeycomb
//
// Pointy top hexagons whose flat-to-flat width is `cell`, walls of `wall`
// thickness extruded `height` along Z, centred on the origin. Shared edges
// are emitted once.
void tessHoneycomb(Vec2 size, float cell, float wall, float height, MeshData* out) {
    if (cell <= 1e-5f || wall <= 1e-9f) return;
    const float R = cell / std::sqrt(3.0f);   // circumradius
    const float rowStep = 1.5f * R;
    const int cols = std::max(1, int(std::ceil(size.x / cell)) + 1);
    const int rows = std::max(1, int(std::ceil(size.y / rowStep)) + 1);

    std::set<std::pair<int, int>> emitted;  // quantised edge midpoints
    auto quantise = [](float v) { return int(std::lround(v * 20000.0f)); };

    for (int row = -rows; row <= rows; ++row) {
        for (int col = -cols; col <= cols; ++col) {
            const float cx = float(col) * cell + ((row & 1) ? cell * 0.5f : 0.0f);
            const float cy = float(row) * rowStep;
            if (std::fabs(cx) > size.x * 0.5f + cell || std::fabs(cy) > size.y * 0.5f + cell)
                continue;
            Vec2 v[6];
            for (int k = 0; k < 6; ++k) {
                const float a = degToRad(30.0f + 60.0f * float(k));
                v[k] = Vec2(cx + R * std::cos(a), cy + R * std::sin(a));
            }
            for (int k = 0; k < 6; ++k) {
                const Vec2 a = v[k];
                const Vec2 b = v[(k + 1) % 6];
                const Vec2 mid = (a + b) * 0.5f;
                // Skip walls whose midpoint is outside the requested area.
                if (std::fabs(mid.x) > size.x * 0.5f || std::fabs(mid.y) > size.y * 0.5f)
                    continue;
                if (!emitted.insert({quantise(mid.x), quantise(mid.y)}).second) continue;
                const Vec2 d = b - a;
                const float len = length(d);
                if (len < 1e-9f) continue;
                const float angle = std::atan2(d.y, d.x);
                // Box along +X rotated about Z onto the edge direction; the
                // overlap of `wall` fills the corner joints.
                appendBox(out, Vec3(mid.x, mid.y, 0.0f),
                          quatAxisAngle(Vec3(0, 0, 1), angle),
                          Vec3(len + wall, wall, height));
            }
        }
    }
}

// --------------------------------------------------------------------- text
//
// The built-in stroke font extruded to `depth`. Each stroke segment becomes a
// box of kFontStrokeWidth * height cross-section, with a small cube at every
// joint so corners stay solid. The generated geometry's XY bounding box is
// then centred on the node origin (Z is already centred), which is the rule
// the Python builder must follow too.
void tessText(const std::string& text, float height, float depth, MeshData* out) {
    if (text.empty() || height <= 1e-6f) return;
    std::vector<std::vector<Vec2>> strokes;
    fontLayout(text, height, &strokes);
    if (strokes.empty()) return;

    const float t = std::max(1e-4f, kFontStrokeWidth * height);
    const float d = std::max(1e-4f, depth);
    MeshData tmp;
    for (const std::vector<Vec2>& stroke : strokes) {
        for (size_t i = 0; i + 1 < stroke.size(); ++i) {
            const Vec2 a = stroke[i];
            const Vec2 b = stroke[i + 1];
            const Vec2 dv = b - a;
            const float len = length(dv);
            if (len < 1e-9f) continue;
            const Vec2 mid = (a + b) * 0.5f;
            appendBox(&tmp, Vec3(mid.x, mid.y, 0.0f),
                      quatAxisAngle(Vec3(0, 0, 1), std::atan2(dv.y, dv.x)),
                      Vec3(len, t, d));
        }
        for (const Vec2& p : stroke)
            appendBox(&tmp, Vec3(p.x, p.y, 0.0f), Quat(), Vec3(t, t, d));
    }
    const Aabb b = tmp.bounds();
    if (!b.valid()) return;
    const Vec3 c = b.centre();
    for (Vec3& p : tmp.positions) {
        p.x -= c.x;
        p.y -= c.y;
    }
    out->append(tmp);
}

// ----------------------------------------------------------------- dispatch

bool tessellateShape(const json::Value& shape, MeshData* out, std::string* error) {
    if (!out) return false;
    auto fail = [&](const std::string& msg) {
        if (error) *error = msg;
        return false;
    };
    if (!shape.isObject()) return fail("shape is not an object");
    const std::string type = shape["type"].asString();

    if (type == "box") {
        float s[3] = {1, 1, 1};
        json::readFloats(shape["size"], s, 3);
        tessBox(Vec3(s[0], s[1], s[2]), out);
    } else if (type == "cylinder") {
        tessCylinder(optFloat(shape, "radius", 0.5f), optFloat(shape, "height", 1.0f),
                     optSegments(shape, "sides", 24), optBool(shape, "caps", true), out);
    } else if (type == "cone") {
        tessCone(optFloat(shape, "radius", 0.5f), optFloat(shape, "top_radius", 0.0f),
                 optFloat(shape, "height", 1.0f), optSegments(shape, "sides", 24),
                 optBool(shape, "caps", true), out);
    } else if (type == "sphere") {
        tessSphere(optFloat(shape, "radius", 0.5f), optSegments(shape, "rings", 16, 2, 256),
                   optSegments(shape, "sectors", 32), out);
    } else if (type == "torus") {
        tessTorus(optFloat(shape, "radius", 0.5f), optFloat(shape, "tube_radius", 0.1f),
                  optSegments(shape, "sides", 16), optSegments(shape, "rings", 32), out);
    } else if (type == "tube") {
        std::vector<Vec3> path;
        const json::Value& pv = shape["path"];
        for (size_t i = 0; i < pv.size(); ++i) {
            float p[3];
            if (json::readFloats(pv[i], p, 3)) path.push_back(Vec3(p[0], p[1], p[2]));
        }
        if (path.size() < 2) return fail("tube path needs at least two points");
        tessTube(path, optFloat(shape, "radius", 0.01f), optSegments(shape, "sides", 12),
                 optBool(shape, "caps", true), out);
    } else if (type == "plane") {
        float s[2] = {1, 1};
        json::readFloats(shape["size"], s, 2);
        int su = 1, sv = 1;
        const json::Value& sd = shape["subdiv"];
        if (sd.size() >= 2) {
            su = clampSegments(sd[0].asInt(1), 1, 512);
            sv = clampSegments(sd[1].asInt(1), 1, 512);
        }
        tessPlane(Vec2(s[0], s[1]), su, sv, out);
    } else if (type == "extrusion") {
        std::vector<Vec2> profile;
        const json::Value& pv = shape["profile"];
        for (size_t i = 0; i < pv.size(); ++i) {
            float p[2];
            if (json::readFloats(pv[i], p, 2)) profile.push_back(Vec2(p[0], p[1]));
        }
        if (profile.size() < 2) return fail("extrusion profile needs at least two points");
        if (!tessExtrusion(profile, optFloat(shape, "height", 1.0f),
                           optBool(shape, "closed", true), out))
            return fail("extrusion profile is degenerate");
    } else if (type == "grid") {
        float s[2] = {1, 1};
        json::readFloats(shape["size"], s, 2);
        tessGrid(Vec2(s[0], s[1]), optFloat(shape, "pitch", 0.1f),
                 optFloat(shape, "bar", 0.01f), out);
    } else if (type == "honeycomb") {
        float s[2] = {1, 1};
        json::readFloats(shape["size"], s, 2);
        tessHoneycomb(Vec2(s[0], s[1]), optFloat(shape, "cell", 0.05f),
                      optFloat(shape, "wall", 0.004f), optFloat(shape, "height", 0.01f), out);
    } else if (type == "text") {
        tessText(shape["string"].asString(), optFloat(shape, "height", 0.05f),
                 optFloat(shape, "depth", 0.005f), out);
    } else if (type == "mesh") {
        const json::Value& pv = shape["positions"];
        const json::Value& nv = shape["normals"];
        const json::Value& iv = shape["indices"];
        if (pv.size() < 9 || pv.size() % 3) return fail("mesh positions must be a multiple of 3");
        const size_t vcount = pv.size() / 3;
        const uint32_t base = uint32_t(out->vertexCount());
        const bool haveNormals = nv.size() == pv.size();
        out->reserveMore(vcount, iv.size() / 3);
        for (size_t i = 0; i < vcount; ++i) {
            const Vec3 p(pv[i * 3].asFloat(), pv[i * 3 + 1].asFloat(), pv[i * 3 + 2].asFloat());
            const Vec3 n = haveNormals ? Vec3(nv[i * 3].asFloat(), nv[i * 3 + 1].asFloat(),
                                              nv[i * 3 + 2].asFloat())
                                       : Vec3(0, 1, 0);
            out->addVertex(p, n, Vec2(0, 0));
        }
        if (iv.size() >= 3) {
            for (size_t i = 0; i + 2 < iv.size(); i += 3) {
                const int64_t a = iv[i].asInt64(-1), b = iv[i + 1].asInt64(-1),
                              c = iv[i + 2].asInt64(-1);
                if (a < 0 || b < 0 || c < 0 || size_t(a) >= vcount || size_t(b) >= vcount ||
                    size_t(c) >= vcount)
                    return fail("mesh index out of range");
                out->addTriangle(base + uint32_t(a), base + uint32_t(b), base + uint32_t(c));
            }
        } else {
            for (uint32_t i = 0; i + 2 < uint32_t(vcount); i += 3)
                out->addTriangle(base + i, base + i + 1, base + i + 2);
        }
        if (!haveNormals) out->computeSmoothNormals();
    } else {
        return fail("unknown shape type '" + type + "'");
    }
    return true;
}

}  // namespace fcxr
