// SPDX-License-Identifier: LGPL-2.1-or-later
// Small vector / quaternion / matrix helpers (column-major mat4 like WebGL).
'use strict';
const M3 = (() => {
  const v3 = (x = 0, y = 0, z = 0) => ({ x, y, z });
  const add = (a, b) => v3(a.x + b.x, a.y + b.y, a.z + b.z);
  const sub = (a, b) => v3(a.x - b.x, a.y - b.y, a.z - b.z);
  const scale = (a, s) => v3(a.x * s, a.y * s, a.z * s);
  const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
  const cross = (a, b) => v3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
  const len = (a) => Math.sqrt(dot(a, a));
  const dist = (a, b) => len(sub(a, b));
  const norm = (a) => { const l = len(a); return l > 0 ? scale(a, 1 / l) : v3(); };
  const lerp = (a, b, t) => v3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);

  // quaternion {x,y,z,w} -> axes
  function quatAxes(q) {
    const { x, y, z, w } = q;
    const xx = x * x, yy = y * y, zz = z * z, xy = x * y, xz = x * z, yz = y * z, wx = w * x, wy = w * y, wz = w * z;
    return {
      x: v3(1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy)),
      y: v3(2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx)),
      z: v3(2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy)),
    };
  }
  function quatRotate(q, v) {
    const a = quatAxes(q);
    return add(add(scale(a.x, v.x), scale(a.y, v.y)), scale(a.z, v.z));
  }

  // ---- mat4 (Float32Array(16), column major)
  function identity() { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }
  function multiply(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }
  function translation(x, y, z) { const m = identity(); m[12] = x; m[13] = y; m[14] = z; return m; }
  function scaling(x, y, z) { const m = identity(); m[0] = x; m[5] = y; m[10] = z; return m; }
  function fromAxes(o, ax, ay, az) {
    const m = identity();
    m[0] = ax.x; m[1] = ax.y; m[2] = ax.z;
    m[4] = ay.x; m[5] = ay.y; m[6] = ay.z;
    m[8] = az.x; m[9] = az.y; m[10] = az.z;
    m[12] = o.x; m[13] = o.y; m[14] = o.z;
    return m;
  }
  function perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), m = new Float32Array(16);
    m[0] = f / aspect; m[5] = f; m[10] = (far + near) / (near - far); m[11] = -1; m[14] = 2 * far * near / (near - far);
    return m;
  }
  function lookAt(eye, target, up) {
    const z = norm(sub(eye, target)), x = norm(cross(up, z)), y = cross(z, x);
    const m = identity();
    m[0] = x.x; m[4] = x.y; m[8] = x.z;
    m[1] = y.x; m[5] = y.y; m[9] = y.z;
    m[2] = z.x; m[6] = z.y; m[10] = z.z;
    m[12] = -dot(x, eye); m[13] = -dot(y, eye); m[14] = -dot(z, eye);
    return m;
  }
  function invert(m) {
    const inv = new Float32Array(16), a = m;
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    let det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
    if (!det) return identity();
    det = 1 / det;
    for (let i = 0; i < 16; i++) inv[i] *= det;
    return inv;
  }
  function transformPoint(m, p) {
    const w = m[3] * p.x + m[7] * p.y + m[11] * p.z + m[15] || 1;
    return v3((m[0] * p.x + m[4] * p.y + m[8] * p.z + m[12]) / w, (m[1] * p.x + m[5] * p.y + m[9] * p.z + m[13]) / w, (m[2] * p.x + m[6] * p.y + m[10] * p.z + m[14]) / w);
  }
  function transformDir(m, d) {
    return v3(m[0] * d.x + m[4] * d.y + m[8] * d.z, m[1] * d.x + m[5] * d.y + m[9] * d.z, m[2] * d.x + m[6] * d.y + m[10] * d.z);
  }
  // ray/plane intersection: returns point or null
  function rayPlane(origin, dir, planePoint, planeNormal) {
    const denom = dot(dir, planeNormal);
    if (Math.abs(denom) < 1e-6) return null;
    const t = dot(sub(planePoint, origin), planeNormal) / denom;
    if (t < 0) return null;
    return add(origin, scale(dir, t));
  }
  function hexToRgb(hex) {
    const h = (hex || '#3b9ddd').replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  return { v3, add, sub, scale, dot, cross, len, dist, norm, lerp, quatAxes, quatRotate, identity, multiply, translation, scaling, fromAxes, perspective, lookAt, invert, transformPoint, transformDir, rayPlane, hexToRgb };
})();
if (typeof module !== 'undefined') module.exports = M3;
