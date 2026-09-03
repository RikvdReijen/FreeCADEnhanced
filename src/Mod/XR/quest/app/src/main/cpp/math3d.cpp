// SPDX-License-Identifier: LGPL-2.1-or-later
#include "math3d.h"

namespace fcxr {

Mat4 mat4Inverse(const Mat4& src) {
    const float* m = src.m;
    float inv[16];

    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] +
             m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] -
             m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] +
             m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] -
              m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] -
             m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] +
             m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] -
             m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] +
              m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] +
             m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] -
             m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] +
              m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] -
              m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] -
             m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] +
             m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] -
              m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] +
              m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];

    float det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    Mat4 out;
    if (std::fabs(det) < 1e-20f) return out;  // identity
    det = 1.0f / det;
    for (int i = 0; i < 16; ++i) out.m[i] = inv[i] * det;
    return out;
}

Mat4 projectionFromFov(float angleLeft, float angleRight, float angleUp,
                       float angleDown, float nearZ, float farZ) {
    const float tanLeft = std::tan(angleLeft);
    const float tanRight = std::tan(angleRight);
    const float tanDown = std::tan(angleDown);
    const float tanUp = std::tan(angleUp);
    const float tanWidth = tanRight - tanLeft;
    const float tanHeight = tanUp - tanDown;

    Mat4 r;
    std::memset(r.m, 0, sizeof(r.m));
    r.at(0, 0) = 2.0f / tanWidth;
    r.at(0, 2) = (tanRight + tanLeft) / tanWidth;
    r.at(1, 1) = 2.0f / tanHeight;
    r.at(1, 2) = (tanUp + tanDown) / tanHeight;
    r.at(2, 2) = -(farZ + nearZ) / (farZ - nearZ);
    r.at(2, 3) = -(2.0f * farZ * nearZ) / (farZ - nearZ);
    r.at(3, 2) = -1.0f;
    return r;
}

Mat4 normalMatrix(const Mat4& model) {
    // Inverse-transpose of the upper 3x3.
    const float a = model.at(0, 0), b = model.at(0, 1), c = model.at(0, 2);
    const float d = model.at(1, 0), e = model.at(1, 1), f = model.at(1, 2);
    const float g = model.at(2, 0), h = model.at(2, 1), i = model.at(2, 2);

    const float A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    float det = a * A + b * B + c * C;
    Mat4 out;
    if (std::fabs(det) < 1e-20f) return out;
    det = 1.0f / det;
    // adj = transpose of the cofactor matrix; inverse = adj/det;
    // we then want the transpose of that, i.e. cofactor/det.
    out.at(0, 0) = A * det;
    out.at(0, 1) = B * det;
    out.at(0, 2) = C * det;
    out.at(1, 0) = -(b * i - c * h) * det;
    out.at(1, 1) = (a * i - c * g) * det;
    out.at(1, 2) = -(a * h - b * g) * det;
    out.at(2, 0) = (b * f - c * e) * det;
    out.at(2, 1) = -(a * f - c * d) * det;
    out.at(2, 2) = (a * e - b * d) * det;
    return out;
}

Aabb transformAabb(const Mat4& m, const Aabb& b) {
    Aabb out;
    if (!b.valid()) return out;
    // Transform the centre, then accumulate the absolute extents — cheaper and
    // exactly equivalent to transforming all eight corners.
    Vec3 c = transformPoint(m, b.centre());
    Vec3 e = b.extent();
    Vec3 ax(std::fabs(m.at(0, 0)), std::fabs(m.at(1, 0)), std::fabs(m.at(2, 0)));
    Vec3 ay(std::fabs(m.at(0, 1)), std::fabs(m.at(1, 1)), std::fabs(m.at(2, 1)));
    Vec3 az(std::fabs(m.at(0, 2)), std::fabs(m.at(1, 2)), std::fabs(m.at(2, 2)));
    Vec3 half = ax * e.x + ay * e.y + az * e.z;
    out.lo = c - half;
    out.hi = c + half;
    return out;
}

void Frustum::fromViewProj(const Mat4& vp) {
    // Rows of the matrix: row(i)[c] = vp.at(i, c).
    auto row = [&](int i) {
        return Vec4(vp.at(i, 0), vp.at(i, 1), vp.at(i, 2), vp.at(i, 3));
    };
    Vec4 r0 = row(0), r1 = row(1), r2 = row(2), r3 = row(3);
    planes[0] = r3 + r0;  // left
    planes[1] = r3 - r0;  // right
    planes[2] = r3 + r1;  // bottom
    planes[3] = r3 - r1;  // top
    planes[4] = r3 + r2;  // near
    planes[5] = r3 - r2;  // far
    for (int i = 0; i < 6; ++i) {
        float l = length(planes[i].xyz());
        if (l > 1e-20f) planes[i] = planes[i] * (1.0f / l);
    }
}

bool Frustum::intersects(const Aabb& b) const {
    if (!b.valid()) return false;
    Vec3 c = b.centre(), e = b.extent();
    for (int i = 0; i < 6; ++i) {
        Vec3 n = planes[i].xyz();
        float r = e.x * std::fabs(n.x) + e.y * std::fabs(n.y) + e.z * std::fabs(n.z);
        if (dot(n, c) + planes[i].w + r < 0.0f) return false;  // fully outside
    }
    return true;
}

bool rayPlane(Vec3 ro, Vec3 rd, Vec3 planePoint, Vec3 planeNormal, float* tOut) {
    float denom = dot(rd, planeNormal);
    if (std::fabs(denom) < 1e-9f) return false;
    float t = dot(planePoint - ro, planeNormal) / denom;
    if (t < 0.0f) return false;
    if (tOut) *tOut = t;
    return true;
}

bool rayTriangle(Vec3 ro, Vec3 rd, Vec3 v0, Vec3 v1, Vec3 v2, bool doubleSided,
                 float* t, float* u, float* v) {
    const float kEps = 1e-9f;
    Vec3 e1 = v1 - v0, e2 = v2 - v0;
    Vec3 pv = cross(rd, e2);
    float det = dot(e1, pv);
    if (doubleSided) {
        if (std::fabs(det) < kEps) return false;
    } else {
        if (det < kEps) return false;
    }
    float invDet = 1.0f / det;
    Vec3 tv = ro - v0;
    float uu = dot(tv, pv) * invDet;
    if (uu < 0.0f || uu > 1.0f) return false;
    Vec3 qv = cross(tv, e1);
    float vv = dot(rd, qv) * invDet;
    if (vv < 0.0f || uu + vv > 1.0f) return false;
    float tt = dot(e2, qv) * invDet;
    if (tt < 0.0f) return false;
    if (t) *t = tt;
    if (u) *u = uu;
    if (v) *v = vv;
    return true;
}

bool rayAabb(Vec3 ro, Vec3 invDir, const Aabb& b, float tMax, float* tMin) {
    float t0 = 0.0f, t1 = tMax;
    for (int i = 0; i < 3; ++i) {
        float a = (b.lo[i] - ro[i]) * invDir[i];
        float c = (b.hi[i] - ro[i]) * invDir[i];
        if (a > c) std::swap(a, c);
        t0 = a > t0 ? a : t0;
        t1 = c < t1 ? c : t1;
        if (t1 < t0) return false;
    }
    if (tMin) *tMin = t0;
    return true;
}

}  // namespace fcxr
