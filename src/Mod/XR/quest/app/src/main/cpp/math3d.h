// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Minimal linear algebra for the FreeCAD XR Quest client.
//
// Conventions (must match ARCHITECTURE.md §2):
//   * right handed, Y up, metres
//   * matrices are column major and stored the way GL wants them
//     (m[0..3] is the first *column*), so `Mat4 * Vec4` is the usual
//     column-vector product and uploading with glUniformMatrix4fv(...,
//     GL_FALSE, m.m) is correct.
//   * quaternions are (x, y, z, w), unit length, and rotate as
//     v' = q * v * conj(q).
//
#pragma once

#include <cmath>
#include <cstring>
#include <algorithm>

namespace fcxr {

static constexpr float kPi = 3.14159265358979323846f;

struct Vec2 {
    float x = 0, y = 0;
    Vec2() = default;
    Vec2(float x_, float y_) : x(x_), y(y_) {}
};
inline Vec2 operator+(Vec2 a, Vec2 b) { return {a.x + b.x, a.y + b.y}; }
inline Vec2 operator-(Vec2 a, Vec2 b) { return {a.x - b.x, a.y - b.y}; }
inline Vec2 operator*(Vec2 a, float s) { return {a.x * s, a.y * s}; }
inline float dot(Vec2 a, Vec2 b) { return a.x * b.x + a.y * b.y; }
inline float length(Vec2 a) { return std::sqrt(dot(a, a)); }

struct Vec3 {
    float x = 0, y = 0, z = 0;
    Vec3() = default;
    Vec3(float x_, float y_, float z_) : x(x_), y(y_), z(z_) {}
    explicit Vec3(float s) : x(s), y(s), z(s) {}
    float& operator[](int i) { return (&x)[i]; }
    float operator[](int i) const { return (&x)[i]; }
};
inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline Vec3 operator-(Vec3 a) { return {-a.x, -a.y, -a.z}; }
inline Vec3 operator*(Vec3 a, float s) { return {a.x * s, a.y * s, a.z * s}; }
inline Vec3 operator*(float s, Vec3 a) { return a * s; }
inline Vec3 operator*(Vec3 a, Vec3 b) { return {a.x * b.x, a.y * b.y, a.z * b.z}; }
inline Vec3 operator/(Vec3 a, float s) { return {a.x / s, a.y / s, a.z / s}; }
inline Vec3& operator+=(Vec3& a, Vec3 b) { a = a + b; return a; }
inline Vec3& operator-=(Vec3& a, Vec3 b) { a = a - b; return a; }
inline Vec3& operator*=(Vec3& a, float s) { a = a * s; return a; }
inline float dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline float length(Vec3 a) { return std::sqrt(dot(a, a)); }
inline float lengthSq(Vec3 a) { return dot(a, a); }
inline Vec3 normalize(Vec3 a) {
    float l = length(a);
    return l > 1e-20f ? a / l : Vec3(0, 0, 0);
}
inline Vec3 lerp(Vec3 a, Vec3 b, float t) { return a + (b - a) * t; }
inline Vec3 vmin(Vec3 a, Vec3 b) { return {std::min(a.x, b.x), std::min(a.y, b.y), std::min(a.z, b.z)}; }
inline Vec3 vmax(Vec3 a, Vec3 b) { return {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)}; }

struct Vec4 {
    float x = 0, y = 0, z = 0, w = 0;
    Vec4() = default;
    Vec4(float x_, float y_, float z_, float w_) : x(x_), y(y_), z(z_), w(w_) {}
    Vec4(Vec3 v, float w_) : x(v.x), y(v.y), z(v.z), w(w_) {}
    Vec3 xyz() const { return {x, y, z}; }
    float& operator[](int i) { return (&x)[i]; }
    float operator[](int i) const { return (&x)[i]; }
};
inline Vec4 operator+(Vec4 a, Vec4 b) { return {a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w}; }
inline Vec4 operator-(Vec4 a, Vec4 b) { return {a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w}; }
inline Vec4 operator*(Vec4 a, float s) { return {a.x * s, a.y * s, a.z * s, a.w * s}; }
inline float dot(Vec4 a, Vec4 b) { return a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w; }

// ---------------------------------------------------------------- quaternion
struct Quat {
    float x = 0, y = 0, z = 0, w = 1;
    Quat() = default;
    Quat(float x_, float y_, float z_, float w_) : x(x_), y(y_), z(z_), w(w_) {}
};

inline Quat operator*(const Quat& a, const Quat& b) {
    return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
            a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
            a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}
inline Quat conjugate(const Quat& q) { return {-q.x, -q.y, -q.z, q.w}; }
inline Quat normalize(const Quat& q) {
    float l = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
    if (l < 1e-20f) return Quat();
    return {q.x / l, q.y / l, q.z / l, q.w / l};
}
inline Vec3 rotate(const Quat& q, Vec3 v) {
    // v + 2 * cross(q.xyz, cross(q.xyz, v) + q.w * v)
    Vec3 u(q.x, q.y, q.z);
    Vec3 t = cross(u, v) + v * q.w;
    return v + cross(u, t) * 2.0f;
}
inline Quat quatAxisAngle(Vec3 axis, float radians) {
    Vec3 a = normalize(axis);
    float s = std::sin(radians * 0.5f);
    return {a.x * s, a.y * s, a.z * s, std::cos(radians * 0.5f)};
}
// Shortest-arc rotation taking `from` to `to` (both are normalised on entry).
inline Quat quatFromTo(Vec3 from, Vec3 to) {
    Vec3 f = normalize(from), t = normalize(to);
    float d = dot(f, t);
    if (d > 0.999999f) return Quat();
    if (d < -0.999999f) {
        // 180 degrees: any perpendicular axis will do.
        Vec3 axis = cross(Vec3(1, 0, 0), f);
        if (lengthSq(axis) < 1e-12f) axis = cross(Vec3(0, 1, 0), f);
        return quatAxisAngle(axis, kPi);
    }
    Vec3 c = cross(f, t);
    return normalize(Quat(c.x, c.y, c.z, 1.0f + d));
}
inline Quat slerp(const Quat& a, Quat b, float t) {
    float d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
    if (d < 0.0f) { b = Quat(-b.x, -b.y, -b.z, -b.w); d = -d; }
    if (d > 0.9995f) {
        return normalize(Quat(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t,
                              a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t));
    }
    float theta = std::acos(std::max(-1.0f, std::min(1.0f, d)));
    float s = std::sin(theta);
    float wa = std::sin((1 - t) * theta) / s, wb = std::sin(t * theta) / s;
    return normalize(Quat(a.x * wa + b.x * wb, a.y * wa + b.y * wb,
                          a.z * wa + b.z * wb, a.w * wa + b.w * wb));
}

// ------------------------------------------------------------------- matrix
// Column major: m[col * 4 + row].
struct Mat4 {
    float m[16];
    Mat4() { identity(); }
    void identity() {
        std::memset(m, 0, sizeof(m));
        m[0] = m[5] = m[10] = m[15] = 1.0f;
    }
    float& at(int row, int col) { return m[col * 4 + row]; }
    float at(int row, int col) const { return m[col * 4 + row]; }
};

inline Mat4 operator*(const Mat4& a, const Mat4& b) {
    Mat4 r;
    for (int c = 0; c < 4; ++c) {
        for (int row = 0; row < 4; ++row) {
            float s = 0;
            for (int k = 0; k < 4; ++k) s += a.m[k * 4 + row] * b.m[c * 4 + k];
            r.m[c * 4 + row] = s;
        }
    }
    return r;
}
inline Vec4 operator*(const Mat4& a, const Vec4& v) {
    Vec4 r;
    for (int row = 0; row < 4; ++row) {
        r[row] = a.m[0 * 4 + row] * v.x + a.m[1 * 4 + row] * v.y +
                 a.m[2 * 4 + row] * v.z + a.m[3 * 4 + row] * v.w;
    }
    return r;
}
// Transform a point (w = 1) ignoring any projective component.
inline Vec3 transformPoint(const Mat4& a, Vec3 p) {
    Vec4 r = a * Vec4(p, 1.0f);
    return {r.x, r.y, r.z};
}
inline Vec3 transformDir(const Mat4& a, Vec3 d) {
    Vec4 r = a * Vec4(d, 0.0f);
    return {r.x, r.y, r.z};
}

inline Mat4 mat4Translate(Vec3 t) {
    Mat4 r;
    r.at(0, 3) = t.x; r.at(1, 3) = t.y; r.at(2, 3) = t.z;
    return r;
}
inline Mat4 mat4Scale(Vec3 s) {
    Mat4 r;
    r.at(0, 0) = s.x; r.at(1, 1) = s.y; r.at(2, 2) = s.z;
    return r;
}
inline Mat4 mat4FromQuat(const Quat& q) {
    Mat4 r;
    float x = q.x, y = q.y, z = q.z, w = q.w;
    r.at(0, 0) = 1 - 2 * (y * y + z * z);
    r.at(0, 1) = 2 * (x * y - z * w);
    r.at(0, 2) = 2 * (x * z + y * w);
    r.at(1, 0) = 2 * (x * y + z * w);
    r.at(1, 1) = 1 - 2 * (x * x + z * z);
    r.at(1, 2) = 2 * (y * z - x * w);
    r.at(2, 0) = 2 * (x * z - y * w);
    r.at(2, 1) = 2 * (y * z + x * w);
    r.at(2, 2) = 1 - 2 * (x * x + y * y);
    return r;
}
inline Mat4 mat4TRS(Vec3 t, const Quat& q, Vec3 s) {
    Mat4 r = mat4FromQuat(q);
    for (int c = 0; c < 3; ++c)
        for (int row = 0; row < 3; ++row) r.at(row, c) *= s[c];
    r.at(0, 3) = t.x; r.at(1, 3) = t.y; r.at(2, 3) = t.z;
    return r;
}
// Inverse of a rigid transform with uniform-or-nonuniform scale is not handled;
// this is the general 4x4 inverse (Cramer). Returns identity for singular input.
Mat4 mat4Inverse(const Mat4& src);

// Inverse of a rotation+translation matrix (no scale) — cheap and exact.
inline Mat4 mat4InverseRigid(const Mat4& a) {
    Mat4 r;
    for (int row = 0; row < 3; ++row)
        for (int c = 0; c < 3; ++c) r.at(row, c) = a.at(c, row);
    Vec3 t(a.at(0, 3), a.at(1, 3), a.at(2, 3));
    r.at(0, 3) = -(r.at(0, 0) * t.x + r.at(0, 1) * t.y + r.at(0, 2) * t.z);
    r.at(1, 3) = -(r.at(1, 0) * t.x + r.at(1, 1) * t.y + r.at(1, 2) * t.z);
    r.at(2, 3) = -(r.at(2, 0) * t.x + r.at(2, 1) * t.y + r.at(2, 2) * t.z);
    return r;
}

// Asymmetric projection built from OpenXR's tangent-angle FOV.
// Produces a GL-style clip volume (z in [-w, w]).
Mat4 projectionFromFov(float angleLeft, float angleRight, float angleUp,
                       float angleDown, float nearZ, float farZ);

// Normal matrix (inverse transpose of the upper 3x3) packed as a Mat4 so it can
// go straight into a mat3 uniform via the top-left block.
Mat4 normalMatrix(const Mat4& model);

// ------------------------------------------------------------------- bounds
struct Aabb {
    Vec3 lo{1e30f, 1e30f, 1e30f};
    Vec3 hi{-1e30f, -1e30f, -1e30f};
    bool valid() const { return lo.x <= hi.x; }
    void add(Vec3 p) { lo = vmin(lo, p); hi = vmax(hi, p); }
    void add(const Aabb& b) { if (b.valid()) { lo = vmin(lo, b.lo); hi = vmax(hi, b.hi); } }
    Vec3 centre() const { return (lo + hi) * 0.5f; }
    Vec3 extent() const { return (hi - lo) * 0.5f; }
    float radius() const { return valid() ? length(extent()) : 0.0f; }
};

// Transform an AABB by a matrix (returns the AABB of the transformed corners).
Aabb transformAabb(const Mat4& m, const Aabb& b);

// Six frustum planes (nx, ny, nz, d) with `dot(n, p) + d >= 0` inside,
// extracted from a view-projection matrix (Gribb/Hartmann).
struct Frustum {
    Vec4 planes[6];
    void fromViewProj(const Mat4& vp);
    bool intersects(const Aabb& b) const;
};

// ------------------------------------------------------------------ helpers
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float saturate(float v) { return clampf(v, 0.0f, 1.0f); }
inline float degToRad(float d) { return d * (kPi / 180.0f); }

// sRGB <-> linear (IEC 61966-2-1). Materials in FCXR carry *linear* colours;
// PNG textures are sRGB encoded and are converted by the sampler.
inline float srgbToLinear(float c) {
    return c <= 0.04045f ? c / 12.92f : std::pow((c + 0.055f) / 1.055f, 2.4f);
}
inline float linearToSrgb(float c) {
    return c <= 0.0031308f ? c * 12.92f : 1.055f * std::pow(c, 1.0f / 2.4f) - 0.055f;
}

// Ray/plane, ray/triangle and ray/AABB used by picking and painting.
bool rayPlane(Vec3 ro, Vec3 rd, Vec3 planePoint, Vec3 planeNormal, float* tOut);
// Moller-Trumbore. Returns true and fills t/u/v (barycentric of v1/v2).
bool rayTriangle(Vec3 ro, Vec3 rd, Vec3 v0, Vec3 v1, Vec3 v2, bool doubleSided,
                 float* t, float* u, float* v);
bool rayAabb(Vec3 ro, Vec3 invDir, const Aabb& b, float tMax, float* tMin);

}  // namespace fcxr
