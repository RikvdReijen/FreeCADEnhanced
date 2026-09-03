// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Port of xrenv/spec.py's reference tessellator. Function names and comments
// point at their Python counterparts so the two can be diffed by eye.
#include "tessellate.h"

#include <algorithm>
#include <cmath>
#include <set>

#include "text_font.h"

namespace fcxr {
namespace {

constexpr float kEps = 1e-9f;

// ---- double precision vector helpers (mirrors of _norm3/_cross/_dot) ------

inline Vec3d operator+(Vec3d a, Vec3d b) { return Vec3d(a.x + b.x, a.y + b.y, a.z + b.z); }
inline Vec3d operator-(Vec3d a, Vec3d b) { return Vec3d(a.x - b.x, a.y - b.y, a.z - b.z); }
inline Vec3d operator*(Vec3d a, double s) { return Vec3d(a.x * s, a.y * s, a.z * s); }
inline double dotd(Vec3d a, Vec3d b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3d crossd(Vec3d a, Vec3d b) {
    return Vec3d(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
// Mirrors xrenv.spec._norm3, including its (0, 0, 1) fallback.
inline Vec3d normd(Vec3d v) {
    const double n = std::sqrt(dotd(v, v));
    if (n < 1e-9) return Vec3d(0.0, 0.0, 1.0);
    return Vec3d(v.x / n, v.y / n, v.z / n);
}
inline Vec3 toVec3(Vec3d v) { return Vec3(float(v.x), float(v.y), float(v.z)); }
inline Vec2 toVec2(Vec2d v) { return Vec2(float(v.x), float(v.y)); }
inline double lengthd(Vec2d a, Vec2d b) {
    const double dx = b.x - a.x, dy = b.y - a.y;
    return std::sqrt(dx * dx + dy * dy);
}

// ---- _Mesh.add_quad / _Mesh.add_tri --------------------------------------

void addQuad(MeshData* m, Vec3 p0, Vec3 p1, Vec3 p2, Vec3 p3, Vec3 n0, Vec3 n1, Vec3 n2,
             Vec3 n3, Vec2 uv0, Vec2 uv1, Vec2 uv2, Vec2 uv3) {
    const uint32_t a = m->addVertex(p0, n0, uv0);
    const uint32_t b = m->addVertex(p1, n1, uv1);
    const uint32_t c = m->addVertex(p2, n2, uv2);
    const uint32_t d = m->addVertex(p3, n3, uv3);
    m->addQuad(a, b, c, d);  // (a,b,c) + (a,c,d)
}

void addQuad(MeshData* m, Vec3 p0, Vec3 p1, Vec3 p2, Vec3 p3, Vec3 n) {
    addQuad(m, p0, p1, p2, p3, n, n, n, n, Vec2(0, 0), Vec2(1, 0), Vec2(1, 1), Vec2(0, 1));
}

void addTri(MeshData* m, Vec3 p0, Vec3 p1, Vec3 p2, Vec3 n0, Vec3 n1, Vec3 n2, Vec2 uv0,
            Vec2 uv1, Vec2 uv2) {
    const uint32_t a = m->addVertex(p0, n0, uv0);
    const uint32_t b = m->addVertex(p1, n1, uv1);
    const uint32_t c = m->addVertex(p2, n2, uv2);
    m->addTriangle(a, b, c);
}

void addTri(MeshData* m, Vec3 p0, Vec3 p1, Vec3 p2, Vec3 n) {
    addTri(m, p0, p1, p2, n, n, n, Vec2(0, 0), Vec2(1, 0), Vec2(0.5f, 1));
}

// ---- shape parameter helpers ---------------------------------------------

bool optFloat(const json::Value& shape, const char* key, float* out) {
    const json::Value& v = shape[key];
    if (!v.isNumber()) return false;
    *out = v.asFloat();
    return true;
}
float optFloatOr(const json::Value& shape, const char* key, float def) {
    const json::Value& v = shape[key];
    return v.isNumber() ? v.asFloat() : def;
}
bool optDouble(const json::Value& shape, const char* key, double* out) {
    const json::Value& v = shape[key];
    if (!v.isNumber()) return false;
    *out = v.asDouble();
    return true;
}
// Reads `n` doubles from a JSON array, keeping full precision.
bool readDoubles(const json::Value& v, double* out, size_t n) {
    if (!v.isArray() || v.size() < n) return false;
    for (size_t i = 0; i < n; ++i) {
        if (!v[i].isNumber()) return false;
    }
    for (size_t i = 0; i < n; ++i) out[i] = v[i].asDouble();
    return true;
}
int optIntOr(const json::Value& shape, const char* key, int def) {
    const json::Value& v = shape[key];
    return v.isNumber() ? v.asInt(def) : def;
}
bool optBoolOr(const json::Value& shape, const char* key, bool def) {
    const json::Value& v = shape[key];
    return v.isBool() ? v.asBool(def) : def;
}

// Python's round(): ties go to even. std::nearbyint honours the default
// FE_TONEAREST mode, which is exactly that.
long roundHalfEven(double v) { return long(std::nearbyint(v)); }

}  // namespace

// ------------------------------------------------------------------- box
//
// _BOX_FACES: (normal, tangent, bitangent) with tangent x bitangent == normal.
void tessBox(Vec3 size, MeshData* out, Vec3 centre) {
    struct Face { Vec3 n, t, b; };
    static const Face kFaces[6] = {
        {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}},
        {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
        {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}},
        {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}},
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},
        {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},
    };
    const Vec3 h = size * 0.5f;
    out->reserveMore(24, 12);
    for (const Face& f : kFaces) {
        const Vec3 fc = centre + f.n * h;  // component-wise: face centre
        const float ts = std::fabs(f.t.x) * h.x + std::fabs(f.t.y) * h.y + std::fabs(f.t.z) * h.z;
        const float bs = std::fabs(f.b.x) * h.x + std::fabs(f.b.y) * h.y + std::fabs(f.b.z) * h.z;
        const Vec3 tv = f.t * ts;
        const Vec3 bv = f.b * bs;
        addQuad(out, fc - tv - bv, fc + tv - bv, fc + tv + bv, fc - tv + bv, f.n);
    }
}

void appendTransformed(MeshData* out, const MeshData& src, Vec3 origin, Vec3 ex, Vec3 ey,
                       Vec3 ez) {
    const uint32_t base = uint32_t(out->vertexCount());
    out->reserveMore(src.vertexCount(), src.triangleCount());
    for (size_t i = 0; i < src.positions.size(); ++i) {
        const Vec3& p = src.positions[i];
        out->positions.push_back(origin + ex * p.x + ey * p.y + ez * p.z);
        const Vec3& n = src.normals[i];
        out->normals.push_back(ex * n.x + ey * n.y + ez * n.z);
        out->uvs.push_back(src.uvs[i]);
    }
    for (uint32_t i : src.indices) out->indices.push_back(base + i);
}

// ------------------------------------------------------------ cone/cylinder
//
// Truncated cone along +Y, from -h/2 (radius r0) to +h/2 (radius r1).
void tessCone(float r0, float r1, float height, int sides, bool caps, MeshData* out) {
    const float h2 = height * 0.5f;
    std::vector<float> cs(size_t(sides) + 1), sn(size_t(sides) + 1);
    for (int i = 0; i <= sides; ++i) {
        const double a = 2.0 * kPiD * double(i) / double(sides);
        cs[size_t(i)] = float(std::cos(a));
        sn[size_t(i)] = float(std::sin(a));
    }
    const float dr = r0 - r1;
    const float slope = std::sqrt(height * height + dr * dr);
    if (slope < kEps) return;
    const float ny = dr / slope;
    const float nr = height / slope;

    const bool bottomDegenerate = r0 <= kEps;
    const bool topDegenerate = r1 <= kEps;

    out->reserveMore(size_t(sides) * 4, size_t(sides) * 2);
    for (int i = 0; i < sides; ++i) {
        const float c0 = cs[size_t(i)], s0 = sn[size_t(i)];
        const float c1 = cs[size_t(i) + 1], s1 = sn[size_t(i) + 1];
        const float u0 = float(i) / float(sides), u1 = float(i + 1) / float(sides);
        const Vec3 n0(nr * c0, ny, nr * s0);
        const Vec3 n1(nr * c1, ny, nr * s1);
        const Vec3 b0(r0 * c0, -h2, r0 * s0);
        const Vec3 b1(r0 * c1, -h2, r0 * s1);
        const Vec3 t0(r1 * c0, h2, r1 * s0);
        const Vec3 t1(r1 * c1, h2, r1 * s1);
        Vec3 apexNormal(0, 1, 0);
        if (topDegenerate || bottomDegenerate) {
            // The apex normal is the normalised mean of the two side normals.
            const float am = std::atan2(s0 + s1, c0 + c1);
            apexNormal = Vec3(nr * std::cos(am), ny, nr * std::sin(am));
        }
        if (topDegenerate) {
            addTri(out, b0, Vec3(0, h2, 0), b1, n0, apexNormal, n1, Vec2(u0, 0.0f),
                   Vec2(0.5f * (u0 + u1), 1.0f), Vec2(u1, 0.0f));
        } else if (bottomDegenerate) {
            addTri(out, Vec3(0, -h2, 0), t0, t1, apexNormal, n0, n1,
                   Vec2(0.5f * (u0 + u1), 0.0f), Vec2(u0, 1.0f), Vec2(u1, 1.0f));
        } else {
            addQuad(out, b0, t0, t1, b1, n0, n0, n1, n1, Vec2(u0, 0.0f), Vec2(u0, 1.0f),
                    Vec2(u1, 1.0f), Vec2(u1, 0.0f));
        }
    }

    if (!caps) return;
    if (!bottomDegenerate) {
        const Vec3 nb(0, -1, 0);
        for (int i = 0; i < sides; ++i) {
            const float c0 = cs[size_t(i)], s0 = sn[size_t(i)];
            const float c1 = cs[size_t(i) + 1], s1 = sn[size_t(i) + 1];
            addTri(out, Vec3(0, -h2, 0), Vec3(r0 * c0, -h2, r0 * s0),
                   Vec3(r0 * c1, -h2, r0 * s1), nb, nb, nb, Vec2(0.5f, 0.5f),
                   Vec2(0.5f + 0.5f * c0, 0.5f + 0.5f * s0),
                   Vec2(0.5f + 0.5f * c1, 0.5f + 0.5f * s1));
        }
    }
    if (!topDegenerate) {
        const Vec3 nt(0, 1, 0);
        for (int i = 0; i < sides; ++i) {
            const float c0 = cs[size_t(i)], s0 = sn[size_t(i)];
            const float c1 = cs[size_t(i) + 1], s1 = sn[size_t(i) + 1];
            addTri(out, Vec3(0, h2, 0), Vec3(r1 * c1, h2, r1 * s1),
                   Vec3(r1 * c0, h2, r1 * s0), nt, nt, nt, Vec2(0.5f, 0.5f),
                   Vec2(0.5f + 0.5f * c1, 0.5f + 0.5f * s1),
                   Vec2(0.5f + 0.5f * c0, 0.5f + 0.5f * s0));
        }
    }
}

void tessCylinder(float radius, float height, int sides, bool caps, MeshData* out) {
    tessCone(radius, radius, height, sides, caps, out);
}

// ---------------------------------------------------------------- sphere
void tessSphere(float radius, int rings, int sectors, MeshData* out) {
    const uint32_t base = uint32_t(out->vertexCount());
    const uint32_t stride = uint32_t(sectors + 1);
    out->reserveMore(size_t(rings + 1) * stride, size_t(rings) * size_t(sectors) * 2);
    for (int i = 0; i <= rings; ++i) {
        const double theta = kPiD * double(i) / double(rings);
        const float st = float(std::sin(theta)), ct = float(std::cos(theta));
        for (int j = 0; j <= sectors; ++j) {
            const double phi = 2.0 * kPiD * double(j) / double(sectors);
            const Vec3 n(st * float(std::cos(phi)), ct, st * float(std::sin(phi)));
            out->addVertex(n * radius, n,
                           Vec2(float(j) / float(sectors), 1.0f - float(i) / float(rings)));
        }
    }
    for (int i = 0; i < rings; ++i) {
        for (int j = 0; j < sectors; ++j) {
            const uint32_t a = base + uint32_t(i) * stride + uint32_t(j);
            const uint32_t b = a + 1;
            const uint32_t c = a + stride + 1;
            const uint32_t d = a + stride;
            if (i == 0) out->addTriangle(a, c, d);
            else if (i == rings - 1) out->addTriangle(a, b, c);
            else out->addQuad(a, b, c, d);
        }
    }
}

// ----------------------------------------------------------------- torus
void tessTorus(float R, float r, int sides, int rings, MeshData* out) {
    const uint32_t base = uint32_t(out->vertexCount());
    const uint32_t stride = uint32_t(sides + 1);
    out->reserveMore(size_t(rings + 1) * stride, size_t(rings) * size_t(sides) * 2);
    for (int i = 0; i <= rings; ++i) {
        const double phi = 2.0 * kPiD * double(i) / double(rings);
        const float cp = float(std::cos(phi)), sp = float(std::sin(phi));
        for (int j = 0; j <= sides; ++j) {
            const double psi = 2.0 * kPiD * double(j) / double(sides);
            const float cq = float(std::cos(psi)), sq = float(std::sin(psi));
            const Vec3 n(cq * cp, sq, cq * sp);
            const Vec3 p((R + r * cq) * cp, r * sq, (R + r * cq) * sp);
            out->addVertex(p, n, Vec2(float(i) / float(rings), float(j) / float(sides)));
        }
    }
    for (int i = 0; i < rings; ++i) {
        for (int j = 0; j < sides; ++j) {
            const uint32_t a = base + uint32_t(i) * stride + uint32_t(j);
            out->addQuad(a, a + 1, a + stride + 1, a + stride);
        }
    }
}

// ----------------------------------------------------------------- plane
void tessPlane(Vec2 size, int su, int sv, MeshData* out) {
    const uint32_t base = uint32_t(out->vertexCount());
    const uint32_t stride = uint32_t(su + 1);
    const Vec3 n(0, 0, 1);
    out->reserveMore(size_t(sv + 1) * stride, size_t(su) * size_t(sv) * 2);
    for (int j = 0; j <= sv; ++j) {
        const float y = -size.y * 0.5f + size.y * float(j) / float(sv);
        for (int i = 0; i <= su; ++i) {
            const float x = -size.x * 0.5f + size.x * float(i) / float(su);
            out->addVertex(Vec3(x, y, 0.0f), n,
                           Vec2(float(i) / float(su), float(j) / float(sv)));
        }
    }
    for (int j = 0; j < sv; ++j) {
        for (int i = 0; i < su; ++i) {
            const uint32_t a = base + uint32_t(j) * stride + uint32_t(i);
            out->addQuad(a, a + 1, a + stride + 1, a + stride);
        }
    }
}

// ------------------------------------------------------------- extrusion
namespace {

double polygonArea2(const std::vector<Vec2d>& p) {
    double a = 0.0;
    const size_t n = p.size();
    for (size_t i = 0; i < n; ++i) {
        const Vec2d& u = p[i];
        const Vec2d& v = p[(i + 1) % n];
        a += u.x * v.y - v.x * u.y;
    }
    return a;
}

bool pointInTriangle(double px, double py, double ax, double ay, double bx, double by,
                     double cx, double cy) {
    const double d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
    const double d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
    const double d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
    const bool hasNeg = (d1 < 0) || (d2 < 0) || (d3 < 0);
    const bool hasPos = (d1 > 0) || (d2 > 0) || (d3 > 0);
    return !(hasNeg && hasPos);
}

}  // namespace

bool triangulatePolygon(const std::vector<Vec2d>& profile, std::vector<uint32_t>* triangles) {
    if (!triangles) return false;
    triangles->clear();
    const size_t n = profile.size();
    if (n < 3) return false;
    std::vector<uint32_t> idx(n);
    for (size_t i = 0; i < n; ++i) idx[i] = uint32_t(i);
    if (polygonArea2(profile) < 0.0) std::reverse(idx.begin(), idx.end());

    size_t guard = 0;
    const size_t guardLimit = 4 * n * n + 64;
    while (idx.size() > 3 && guard < guardLimit) {
        ++guard;
        bool earFound = false;
        const size_t count = idx.size();
        for (size_t k = 0; k < count; ++k) {
            const uint32_t i0 = idx[(k + count - 1) % count];
            const uint32_t i1 = idx[k];
            const uint32_t i2 = idx[(k + 1) % count];
            const Vec2d& a = profile[i0];
            const Vec2d& b = profile[i1];
            const Vec2d& c = profile[i2];
            const double crossz = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
            if (crossz <= 1e-14) continue;  // reflex or collinear
            bool bad = false;
            for (uint32_t other : idx) {
                if (other == i0 || other == i1 || other == i2) continue;
                if (pointInTriangle(profile[other].x, profile[other].y, a.x, a.y, b.x, b.y,
                                    c.x, c.y)) {
                    bad = true;
                    break;
                }
            }
            if (bad) continue;
            triangles->push_back(i0);
            triangles->push_back(i1);
            triangles->push_back(i2);
            idx.erase(idx.begin() + long(k));
            earFound = true;
            break;
        }
        if (!earFound) break;
    }
    if (idx.size() == 3) {
        triangles->push_back(idx[0]);
        triangles->push_back(idx[1]);
        triangles->push_back(idx[2]);
    }
    return !triangles->empty();
}

bool tessExtrusion(const std::vector<Vec2d>& profileIn, double height, bool closed,
                   MeshData* out) {
    if (height <= 0.0) return false;
    std::vector<Vec2d> profile;
    for (const Vec2d& p : profileIn) {
        if (profile.empty() || std::fabs(p.x - profile.back().x) > 1e-12 ||
            std::fabs(p.y - profile.back().y) > 1e-12)
            profile.push_back(p);
    }
    if (closed && profile.size() > 1 &&
        std::fabs(profile.front().x - profile.back().x) < 1e-12 &&
        std::fabs(profile.front().y - profile.back().y) < 1e-12)
        profile.pop_back();
    if (closed && profile.size() < 3) return false;
    if (!closed && profile.size() < 2) return false;
    if (closed) {
        const double area2 = polygonArea2(profile);
        if (std::fabs(area2) < 1e-14) return false;
        if (area2 < 0.0) std::reverse(profile.begin(), profile.end());
    }

    const double z0 = -height * 0.5, z1 = height * 0.5;
    const size_t n = profile.size();
    const size_t segs = closed ? n : n - 1;
    std::vector<double> lens(segs);
    double total = 0.0;
    for (size_t i = 0; i < segs; ++i) {
        lens[i] = lengthd(profile[i], profile[(i + 1) % n]);
        total += lens[i];
    }
    if (total < 1e-9) return false;

    double acc = 0.0;
    out->reserveMore(segs * 4, segs * 2);
    for (size_t i = 0; i < segs; ++i) {
        const Vec2d& p0 = profile[i];
        const Vec2d& p1 = profile[(i + 1) % n];
        const double ln = lens[i];
        if (ln < 1e-12) continue;
        const double dx = p1.x - p0.x, dy = p1.y - p0.y;
        const Vec3 nv(float(dy / ln), float(-dx / ln), 0.0f);
        const float u0 = float(acc / total);
        acc += ln;
        const float u1 = float(acc / total);
        addQuad(out, Vec3(float(p0.x), float(p0.y), float(z0)),
                Vec3(float(p1.x), float(p1.y), float(z0)),
                Vec3(float(p1.x), float(p1.y), float(z1)),
                Vec3(float(p0.x), float(p0.y), float(z1)), nv, nv, nv, nv, Vec2(u0, 0.0f),
                Vec2(u1, 0.0f), Vec2(u1, 1.0f), Vec2(u0, 1.0f));
    }

    if (closed) {
        std::vector<uint32_t> tris;
        if (!triangulatePolygon(profile, &tris)) return false;
        const Vec3 front(0, 0, 1), back(0, 0, -1);
        const float fz0 = float(z0), fz1 = float(z1);
        for (size_t i = 0; i + 2 < tris.size(); i += 3) {
            const Vec2 pa = toVec2(profile[tris[i]]);
            const Vec2 pb = toVec2(profile[tris[i + 1]]);
            const Vec2 pc = toVec2(profile[tris[i + 2]]);
            addTri(out, Vec3(pa.x, pa.y, fz1), Vec3(pb.x, pb.y, fz1), Vec3(pc.x, pc.y, fz1),
                   front, front, front, pa, pb, pc);
            addTri(out, Vec3(pa.x, pa.y, fz0), Vec3(pc.x, pc.y, fz0), Vec3(pb.x, pb.y, fz0),
                   back, back, back, pa, pc, pb);
        }
    }
    return true;
}

// ------------------------------------------------------------------ tube
//
// The rotation minimising frame is computed in *double* precision: the
// reference implementation decides that a reflection is degenerate with a
// 1e-16 threshold, which single precision can never reach (the residual after
// cancelling two nearly equal float vectors is around 1e-7). Running the frame
// in double reproduces the Python branch for branch; only the emitted vertices
// are narrowed to float.
bool tessTube(const std::vector<Vec3d>& raw, double radius, int sides, bool caps,
              MeshData* out) {
    std::vector<Vec3d> path;
    for (const Vec3d& p : raw) {
        const Vec3d q = p;
        if (path.empty() ||
            std::max(std::max(std::fabs(q.x - path.back().x), std::fabs(q.y - path.back().y)),
                     std::fabs(q.z - path.back().z)) > 1e-9)
            path.push_back(q);
    }
    if (path.size() < 2) return false;
    const size_t npts = path.size();

    std::vector<Vec3d> tangents(npts);
    for (size_t i = 0; i < npts; ++i) {
        Vec3d t;
        if (i == 0) t = path[1] - path[0];
        else if (i == npts - 1) t = path[npts - 1] - path[npts - 2];
        else {
            const Vec3d a = normd(path[i] - path[i - 1]);
            const Vec3d b = normd(path[i + 1] - path[i]);
            t = a + b;
            if (std::sqrt(dotd(t, t)) < 1e-9) t = b;  // 180 degree reversal
        }
        tangents[i] = normd(t);
    }

    // Initial normal: any vector perpendicular to t0.
    const Vec3d t0 = tangents[0];
    const Vec3d ref = std::fabs(t0.y) < 0.9 ? Vec3d(0, 1, 0) : Vec3d(1, 0, 0);
    std::vector<Vec3d> normalsN;
    normalsN.reserve(npts);
    normalsN.push_back(normd(crossd(ref, t0)));
    // Rotation minimising frames by double reflection.
    for (size_t i = 1; i < npts; ++i) {
        const Vec3d prevT = tangents[i - 1];
        const Vec3d curT = tangents[i];
        const Vec3d prevN = normalsN.back();
        const Vec3d v = curT - prevT;
        const double c1 = dotd(v, v);
        if (c1 < 1e-16) {
            normalsN.push_back(prevN);
            continue;
        }
        const Vec3d nl = prevN - v * ((2.0 / c1) * dotd(v, prevN));
        const Vec3d tl = prevT - v * ((2.0 / c1) * dotd(v, prevT));
        const Vec3d v2 = curT - tl;
        const double c2 = dotd(v2, v2);
        if (c2 < 1e-16) {
            normalsN.push_back(normd(nl));
            continue;
        }
        Vec3d nn = nl - v2 * ((2.0 / c2) * dotd(v2, nl));
        nn = nn - curT * dotd(nn, curT);  // re-orthogonalise against drift
        normalsN.push_back(normd(nn));
    }

    std::vector<double> arc(npts, 0.0);
    for (size_t i = 1; i < npts; ++i) {
        const Vec3d d = path[i] - path[i - 1];
        arc[i] = arc[i - 1] + std::sqrt(dotd(d, d));
    }
    const double total = arc[npts - 1] > 0.0 ? arc[npts - 1] : 1.0;

    const uint32_t base = uint32_t(out->vertexCount());
    const uint32_t stride = uint32_t(sides + 1);
    out->reserveMore(npts * stride, (npts - 1) * size_t(sides) * 2);
    std::vector<Vec3d> frameN(npts), frameB(npts);
    for (size_t i = 0; i < npts; ++i) {
        const Vec3d nvec = normalsN[i];
        const Vec3d bvec = crossd(tangents[i], nvec);
        frameN[i] = nvec;
        frameB[i] = bvec;
        for (int j = 0; j <= sides; ++j) {
            const double a = 2.0 * kPiD * double(j) / double(sides);
            const Vec3d nrm = nvec * std::cos(a) + bvec * std::sin(a);
            out->addVertex(toVec3(path[i] + nrm * radius), toVec3(nrm),
                           Vec2(float(j) / float(sides), float(arc[i] / total)));
        }
    }
    for (size_t i = 0; i + 1 < npts; ++i) {
        for (int j = 0; j < sides; ++j) {
            const uint32_t a = base + uint32_t(i) * stride + uint32_t(j);
            out->addQuad(a, a + 1, a + stride + 1, a + stride);
        }
    }

    if (caps) {
        auto ringPoint = [&](size_t i, double angle) {
            return toVec3(path[i] + (frameN[i] * std::cos(angle) + frameB[i] * std::sin(angle)) *
                                        radius);
        };
        // Start cap faces backwards along the first tangent.
        const Vec3 cn = toVec3(Vec3d(-tangents[0].x, -tangents[0].y, -tangents[0].z));
        for (int j = 0; j < sides; ++j) {
            const double a0 = 2.0 * kPiD * double(j) / double(sides);
            const double a1 = 2.0 * kPiD * double(j + 1) / double(sides);
            addTri(out, toVec3(path[0]), ringPoint(0, a1), ringPoint(0, a0), cn);
        }
        const size_t last = npts - 1;
        const Vec3 tn = toVec3(tangents[last]);
        for (int j = 0; j < sides; ++j) {
            const double a0 = 2.0 * kPiD * double(j) / double(sides);
            const double a1 = 2.0 * kPiD * double(j + 1) / double(sides);
            addTri(out, toVec3(path[last]), ringPoint(last, a0), ringPoint(last, a1), tn);
        }
    }
    return true;
}

// ------------------------------------------------------------------ grid
//
// Lattice extents are computed in double: `floor(size / (2 * pitch))` sits on
// an exact integer boundary for the specs the generator produces, and float
// rounding there would add or drop a whole bar.
bool tessGrid(double sx, double sy, double pitch, double bar, MeshData* out) {
    if (sx <= 0.0 || sy <= 0.0) return false;
    if (pitch <= 0.0 || bar <= 0.0) return false;
    if (bar >= pitch) return false;
    const double p = pitch;
    const int nx = int(std::floor(sx / (2.0 * p)));
    const int ny = int(std::floor(sy / (2.0 * p)));
    // Bars running along X, spaced in Y.
    for (int j = -ny; j <= ny; ++j) {
        const double y = double(j) * p;
        if (std::fabs(y) > sy * 0.5 + 1e-12) continue;
        tessBox(Vec3(float(sx), float(bar), float(bar)), out, Vec3(0.0f, float(y), 0.0f));
    }
    // Bars running along Y, spaced in X.
    for (int i = -nx; i <= nx; ++i) {
        const double x = double(i) * p;
        if (std::fabs(x) > sx * 0.5 + 1e-12) continue;
        tessBox(Vec3(float(bar), float(sy), float(bar)), out, Vec3(float(x), 0.0f, 0.0f));
    }
    return !out->indices.empty();
}

// ------------------------------------------------------------- honeycomb
//
// De-duplicated wall boxes on a pointy-top hex lattice (not hollow prisms:
// prisms would put coincident faces between neighbouring cells and z-fight).
// The lattice is walked in double precision because whether a wall's midpoint
// falls inside the requested area is an exact-boundary test on the generated
// specs.
bool tessHoneycomb(double sx, double sy, double cell, double wall, double height,
                   MeshData* out) {
    if (sx <= 0.0 || sy <= 0.0 || cell <= 0.0 || wall <= 0.0 || height <= 0.0) return false;
    if (wall >= cell * 0.5) return false;

    const double R = cell / std::sqrt(3.0);  // circumradius == side length
    const double rowPitch = 1.5 * R;
    const double colPitch = cell;
    const int ncols = int(std::ceil(sx / colPitch)) + 2;
    const int nrows = int(std::ceil(sy / rowPitch)) + 2;

    double cornerX[6], cornerY[6];
    for (int k = 0; k < 6; ++k) {
        const double a = kPiD / 3.0 * double(k);
        cornerX[k] = R * std::sin(a);
        cornerY[k] = R * std::cos(a);
    }

    // Python's `-n // 2` is a floor division: -((n + 1) / 2) with C++ integer
    // division. The loops mirror range(-n//2 - 1, n//2 + 2).
    const int rowLo = -((nrows + 1) / 2) - 1, rowHi = nrows / 2 + 1;
    const int colLo = -((ncols + 1) / 2) - 1, colHi = ncols / 2 + 1;
    const double hx = sx * 0.5, hy = sy * 0.5;

    std::set<std::pair<long, long>> seen;
    for (int r = rowLo; r <= rowHi; ++r) {
        const double cy = double(r) * rowPitch;
        const double xoff = (r & 1) ? colPitch * 0.5 : 0.0;
        for (int c = colLo; c <= colHi; ++c) {
            const double cx = double(c) * colPitch + xoff;
            if (std::fabs(cx) > hx + colPitch || std::fabs(cy) > hy + rowPitch) continue;
            for (int k = 0; k < 6; ++k) {
                const double p0x = cx + cornerX[k], p0y = cy + cornerY[k];
                const double p1x = cx + cornerX[(k + 1) % 6], p1y = cy + cornerY[(k + 1) % 6];
                const double mx = (p0x + p1x) * 0.5, my = (p0y + p1y) * 0.5;
                if (std::fabs(mx) > hx || std::fabs(my) > hy) continue;
                const std::pair<long, long> key(roundHalfEven(mx / (R * 0.01)),
                                                roundHalfEven(my / (R * 0.01)));
                if (!seen.insert(key).second) continue;
                const double dx = p1x - p0x, dy = p1y - p0y;
                const double ln = std::sqrt(dx * dx + dy * dy);
                if (ln < 1e-12) continue;
                const double ux = dx / ln, uy = dy / ln;
                MeshData bar;
                tessBox(Vec3(float(ln + wall), float(wall), float(height)), &bar);
                appendTransformed(out, bar, Vec3(float(mx), float(my), 0.0f),
                                  Vec3(float(ux), float(uy), 0.0f),
                                  Vec3(float(-uy), float(ux), 0.0f), Vec3(0, 0, 1));
            }
        }
    }
    return !out->indices.empty();
}

// ------------------------------------------------------------------ text
bool tessText(const std::string& text, float height, float depth, MeshData* out) {
    if (height <= 0.0f || depth <= 0.0f || text.empty()) return false;
    const float stroke = height * fontStroke();
    std::vector<std::vector<Vec2>> strokes;
    fontLayout(text, height, /*centred=*/true, &strokes);

    size_t segments = 0;
    for (const std::vector<Vec2>& poly : strokes) {
        for (size_t k = 0; k + 1 < poly.size(); ++k) {
            const Vec2 a = poly[k];
            const Vec2 b = poly[k + 1];
            const Vec2 d = b - a;
            const float ln = length(d);
            if (ln < 1e-9f) continue;
            const float ux = d.x / ln, uy = d.y / ln;
            MeshData seg;
            tessBox(Vec3(ln + stroke, stroke, depth), &seg);
            appendTransformed(out, seg, Vec3((a.x + b.x) * 0.5f, (a.y + b.y) * 0.5f, 0.0f),
                              Vec3(ux, uy, 0.0f), Vec3(-uy, ux, 0.0f), Vec3(0, 0, 1));
            ++segments;
        }
    }
    return segments > 0;
}

// -------------------------------------------------------------- dispatch

bool tessellateShape(const json::Value& shape, MeshData* out, std::string* error) {
    if (!out) return false;
    auto fail = [&](const std::string& msg) {
        if (error) *error = msg;
        return false;
    };
    if (!shape.isObject()) return fail("shape is not an object");
    const std::string type = shape["type"].asString();

    if (type == "box") {
        float s[3];
        if (!json::readFloats(shape["size"], s, 3)) return fail("box: size must be 3 numbers");
        if (std::min(std::min(s[0], s[1]), s[2]) <= 0.0f)
            return fail("box: all size components must be > 0");
        tessBox(Vec3(s[0], s[1], s[2]), out);
    } else if (type == "cylinder") {
        float radius = 0.0f, height = 0.0f;
        if (!optFloat(shape, "radius", &radius) || !optFloat(shape, "height", &height))
            return fail("cylinder: radius and height are required");
        const int sides = optIntOr(shape, "sides", 24);
        if (radius <= 0.0f || height <= 0.0f)
            return fail("cylinder: radius and height must be > 0");
        if (sides < 3) return fail("cylinder: sides must be >= 3");
        tessCylinder(radius, height, sides, optBoolOr(shape, "caps", true), out);
    } else if (type == "cone") {
        float height = 0.0f;
        if (!optFloat(shape, "height", &height)) return fail("cone: height is required");
        const float r0 = optFloatOr(shape, "radius", 0.0f);
        const float r1 = optFloatOr(shape, "top_radius", 0.0f);
        const int sides = optIntOr(shape, "sides", 24);
        if (height <= 0.0f) return fail("cone: height must be > 0");
        if (r0 <= 0.0f && r1 <= 0.0f) return fail("cone: at least one radius must be > 0");
        if (sides < 3) return fail("cone: sides must be >= 3");
        tessCone(r0, r1, height, sides, optBoolOr(shape, "caps", true), out);
    } else if (type == "sphere") {
        float radius = 0.0f;
        if (!optFloat(shape, "radius", &radius)) return fail("sphere: radius is required");
        const int rings = optIntOr(shape, "rings", 12);
        const int sectors = optIntOr(shape, "sectors", 24);
        if (radius <= 0.0f) return fail("sphere: radius must be > 0");
        if (rings < 2 || sectors < 3) return fail("sphere: need rings >= 2 and sectors >= 3");
        tessSphere(radius, rings, sectors, out);
    } else if (type == "torus") {
        float R = 0.0f, r = 0.0f;
        if (!optFloat(shape, "radius", &R) || !optFloat(shape, "tube_radius", &r))
            return fail("torus: radius and tube_radius are required");
        const int sides = optIntOr(shape, "sides", 12);
        const int rings = optIntOr(shape, "rings", 24);
        if (R <= 0.0f || r <= 0.0f) return fail("torus: radius and tube_radius must be > 0");
        if (sides < 3 || rings < 3) return fail("torus: need sides >= 3 and rings >= 3");
        tessTorus(R, r, sides, rings, out);
    } else if (type == "tube") {
        double radius = 0.0;
        if (!optDouble(shape, "radius", &radius)) return fail("tube: radius is required");
        const int sides = optIntOr(shape, "sides", 12);
        if (radius <= 0.0) return fail("tube: radius must be > 0");
        if (sides < 3) return fail("tube: sides must be >= 3");
        std::vector<Vec3d> path;
        const json::Value& pv = shape["path"];
        for (size_t i = 0; i < pv.size(); ++i) {
            double p[3];
            if (!readDoubles(pv[i], p, 3)) return fail("tube: path points must be [x,y,z]");
            path.push_back(Vec3d(p[0], p[1], p[2]));
        }
        if (!tessTube(path, radius, sides, optBoolOr(shape, "caps", true), out))
            return fail("tube: path needs >= 2 distinct points");
    } else if (type == "plane") {
        float s[2];
        if (!json::readFloats(shape["size"], s, 2))
            return fail("plane: size must be 2 numbers");
        int su = 1, sv = 1;
        const json::Value& sd = shape["subdiv"];
        if (sd.size() >= 2) {
            su = sd[0].asInt(1);
            sv = sd[1].asInt(1);
        }
        if (s[0] <= 0.0f || s[1] <= 0.0f) return fail("plane: size must be > 0");
        if (su < 1 || sv < 1) return fail("plane: subdiv must be >= 1");
        tessPlane(Vec2(s[0], s[1]), su, sv, out);
    } else if (type == "extrusion") {
        double height = 0.0;
        if (!optDouble(shape, "height", &height))
            return fail("extrusion: height is required");
        std::vector<Vec2d> profile;
        const json::Value& pv = shape["profile"];
        for (size_t i = 0; i < pv.size(); ++i) {
            double p[2];
            if (!readDoubles(pv[i], p, 2))
                return fail("extrusion: profile points must be [x,y]");
            profile.push_back(Vec2d(p[0], p[1]));
        }
        if (!tessExtrusion(profile, height, optBoolOr(shape, "closed", true), out))
            return fail("extrusion: degenerate profile or height");
    } else if (type == "grid") {
        double s[2];
        if (!readDoubles(shape["size"], s, 2)) return fail("grid: size must be 2 numbers");
        double pitch = 0.0, bar = 0.0;
        if (!optDouble(shape, "pitch", &pitch) || !optDouble(shape, "bar", &bar))
            return fail("grid: pitch and bar are required");
        if (bar >= pitch) return fail("grid: bar must be smaller than pitch");
        if (!tessGrid(s[0], s[1], pitch, bar, out))
            return fail("grid: pitch too large for the requested size");
    } else if (type == "honeycomb") {
        double s[2];
        if (!readDoubles(shape["size"], s, 2))
            return fail("honeycomb: size must be 2 numbers");
        double cell = 0.0, wall = 0.0, height = 0.0;
        if (!optDouble(shape, "cell", &cell) || !optDouble(shape, "wall", &wall) ||
            !optDouble(shape, "height", &height))
            return fail("honeycomb: cell, wall and height are required");
        if (wall >= cell * 0.5)
            return fail("honeycomb: wall must be well below half the cell size");
        if (!tessHoneycomb(s[0], s[1], cell, wall, height, out))
            return fail("honeycomb: cell size too large for the requested area");
    } else if (type == "text") {
        float height = 0.0f, depth = 0.0f;
        if (!optFloat(shape, "height", &height) || !optFloat(shape, "depth", &depth))
            return fail("text: height and depth are required");
        if (height <= 0.0f || depth <= 0.0f)
            return fail("text: height and depth must be > 0");
        const std::string s = shape["string"].asString();
        if (s.empty()) return fail("text: string must not be empty");
        if (!tessText(s, height, depth, out))
            return fail("text: string contains no renderable glyphs");
    } else if (type == "mesh") {
        const json::Value& pv = shape["positions"];
        const json::Value& nv = shape["normals"];
        const json::Value& uv = shape["uvs"];
        const json::Value& iv = shape["indices"];
        if (pv.size() % 3 || pv.size() < 9)
            return fail("mesh: positions must be 3*N floats with N >= 3");
        if (iv.size() % 3 || iv.size() == 0)
            return fail("mesh: indices must be 3*T ints with T >= 1");
        const size_t vcount = pv.size() / 3;
        const bool haveNormals = nv.size() == pv.size();
        if (nv.size() && !haveNormals) return fail("mesh: normals must match positions");
        const bool haveUvs = uv.size() == vcount * 2;
        if (uv.size() && !haveUvs) return fail("mesh: uvs must be 2*N floats");

        const uint32_t base = uint32_t(out->vertexCount());
        out->reserveMore(vcount, iv.size() / 3);
        for (size_t i = 0; i < vcount; ++i) {
            const Vec3 p(pv[i * 3].asFloat(), pv[i * 3 + 1].asFloat(), pv[i * 3 + 2].asFloat());
            const Vec3 n = haveNormals ? Vec3(nv[i * 3].asFloat(), nv[i * 3 + 1].asFloat(),
                                              nv[i * 3 + 2].asFloat())
                                       : Vec3(0, 0, 0);
            const Vec2 t = haveUvs ? Vec2(uv[i * 2].asFloat(), uv[i * 2 + 1].asFloat())
                                   : Vec2(0, 0);
            out->addVertex(p, n, t);
        }
        for (size_t i = 0; i + 2 < iv.size(); i += 3) {
            const int64_t a = iv[i].asInt64(-1), b = iv[i + 1].asInt64(-1),
                          c = iv[i + 2].asInt64(-1);
            if (a < 0 || b < 0 || c < 0 || size_t(a) >= vcount || size_t(b) >= vcount ||
                size_t(c) >= vcount)
                return fail("mesh: index out of range");
            out->addTriangle(base + uint32_t(a), base + uint32_t(b), base + uint32_t(c));
        }
        if (!haveNormals) {
            // Area weighted smooth normals over just the vertices we added,
            // matching the `nrm is None` branch of _tess_mesh.
            for (size_t i = 0; i + 2 < iv.size(); i += 3) {
                const uint32_t a = base + uint32_t(iv[i].asInt64(0));
                const uint32_t b = base + uint32_t(iv[i + 1].asInt64(0));
                const uint32_t c = base + uint32_t(iv[i + 2].asInt64(0));
                const Vec3 fn = cross(out->positions[b] - out->positions[a],
                                      out->positions[c] - out->positions[a]);
                out->normals[a] += fn;
                out->normals[b] += fn;
                out->normals[c] += fn;
            }
            for (size_t i = 0; i < vcount; ++i)
                out->normals[base + i] = normalize(out->normals[base + i]);
        }
    } else {
        return fail("unknown shape type '" + type + "'");
    }

    if (out->indices.empty()) return fail(type + ": produced no triangles");
    return true;
}

}  // namespace fcxr
