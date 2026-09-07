// SPDX-License-Identifier: LGPL-2.1-or-later
// Dependency-free WebGL renderer: textured quads (node cards), lines (wires,
// grid) and lit meshes (geometry preview).  Works with an XRWebGLLayer or a
// plain canvas.
'use strict';
class Renderer {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    const attrs = Object.assign({ alpha: true, antialias: true, xrCompatible: true, preserveDrawingBuffer: true }, opts);
    this.gl = canvas.getContext('webgl2', attrs) || canvas.getContext('webgl', attrs);
    if (!this.gl) throw new Error('WebGL not available');
    const gl = this.gl;
    this.flat = this._program(
      `attribute vec3 aPos; attribute vec2 aUv; uniform mat4 uMvp; varying vec2 vUv;
       void main(){ vUv = aUv; gl_Position = uMvp * vec4(aPos, 1.0); }`,
      `precision mediump float; varying vec2 vUv; uniform vec4 uColor; uniform sampler2D uTex; uniform float uUseTex;
       void main(){ vec4 t = texture2D(uTex, vUv); gl_FragColor = mix(uColor, t * uColor, uUseTex); }`,
      ['aPos', 'aUv'], ['uMvp', 'uColor', 'uTex', 'uUseTex']);
    this.lit = this._program(
      `attribute vec3 aPos; attribute vec3 aNormal; uniform mat4 uMvp; uniform mat4 uModel; varying vec3 vN;
       void main(){ vN = mat3(uModel) * aNormal; gl_Position = uMvp * vec4(aPos, 1.0); }`,
      `precision mediump float; varying vec3 vN; uniform vec4 uColor; uniform vec3 uLight;
       void main(){ float d = max(dot(normalize(vN), normalize(uLight)), 0.0); float l = 0.35 + 0.65 * d;
       gl_FragColor = vec4(uColor.rgb * l, uColor.a); }`,
      ['aPos', 'aNormal'], ['uMvp', 'uModel', 'uColor', 'uLight']);
    // two triangles, 5 floats per vertex (x, y, z, u, v)
    this.quad = this._buffer(new Float32Array([
      0, 0, 0, 0, 0,  1, 0, 0, 1, 0,  1, 1, 0, 1, 1,
      0, 0, 0, 0, 0,  1, 1, 0, 1, 1,  0, 1, 0, 0, 1,
    ]));
    this.white = this._solidTexture([255, 255, 255, 255]);
    this.textures = new Map();
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    this.stats = { draws: 0 };
  }
  _program(vs, fs, attrs, unis) {
    const gl = this.gl;
    const compile = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const out = { program: p, a: {}, u: {} };
    attrs.forEach((n) => (out.a[n] = gl.getAttribLocation(p, n)));
    unis.forEach((n) => (out.u[n] = gl.getUniformLocation(p, n)));
    return out;
  }
  _buffer(data, target) {
    const gl = this.gl, b = gl.createBuffer();
    gl.bindBuffer(target || gl.ARRAY_BUFFER, b);
    gl.bufferData(target || gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    return { buffer: b, count: data.length };
  }
  _solidTexture(rgba) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(rgba));
    return t;
  }
  /** Upload (or refresh) a 2D canvas as a texture under a key. */
  textureFromCanvas(key, canvas2d) {
    const gl = this.gl;
    let t = this.textures.get(key);
    if (!t) { t = gl.createTexture(); this.textures.set(key, t); }
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas2d);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }
  dropTexture(key) {
    const t = this.textures.get(key);
    if (t) { this.gl.deleteTexture(t); this.textures.delete(key); }
  }
  /** Build a lit mesh from flat vertex list and triangle index list. */
  mesh(vertices, faces) {
    const pos = [], nrm = [];
    for (let i = 0; i < faces.length; i += 3) {
      const a = faces[i] * 3, b = faces[i + 1] * 3, c = faces[i + 2] * 3;
      const ax = vertices[a], ay = vertices[a + 1], az = vertices[a + 2];
      const bx = vertices[b], by = vertices[b + 1], bz = vertices[b + 2];
      const cx = vertices[c], cy = vertices[c + 1], cz = vertices[c + 2];
      const ux = bx - ax, uy = by - ay, uz = bz - az, vx = cx - ax, vy = cy - ay, vz = cz - az;
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
      pos.push(ax, ay, az, bx, by, bz, cx, cy, cz);
      nrm.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
    }
    return { pos: this._buffer(new Float32Array(pos)), nrm: this._buffer(new Float32Array(nrm)), count: pos.length / 3 };
  }
  lines(points) {
    // points: flat xyz list forming a line strip -> GL_LINES pairs
    const out = [];
    for (let i = 0; i + 5 < points.length; i += 3) out.push(points[i], points[i + 1], points[i + 2], points[i + 3], points[i + 4], points[i + 5]);
    return { pos: this._buffer(new Float32Array(out)), count: out.length / 3 };
  }
  free(obj) {
    if (!obj) return;
    const gl = this.gl;
    if (obj.pos) gl.deleteBuffer(obj.pos.buffer);
    if (obj.nrm) gl.deleteBuffer(obj.nrm.buffer);
  }
  begin(viewport, clear) {
    const gl = this.gl;
    if (viewport) gl.viewport(viewport.x, viewport.y, viewport.width, viewport.height);
    if (clear) { gl.clearColor(clear[0], clear[1], clear[2], clear[3]); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); }
    else gl.clear(gl.DEPTH_BUFFER_BIT);
    this.stats.draws = 0;
  }
  /** Draw a unit quad (0..1 x 0..1 in its local xy plane) with model matrix. */
  drawQuad(viewProj, model, color, texture, opts = {}) {
    const gl = this.gl, p = this.flat;
    gl.useProgram(p.program);
    gl.uniformMatrix4fv(p.u.uMvp, false, M3.multiply(viewProj, model));
    gl.uniform4fv(p.u.uColor, color || [1, 1, 1, 1]);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture || this.white);
    gl.uniform1i(p.u.uTex, 0);
    gl.uniform1f(p.u.uUseTex, texture ? 1 : 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad.buffer);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 20, 0);
    gl.enableVertexAttribArray(p.a.aUv);
    gl.vertexAttribPointer(p.a.aUv, 2, gl.FLOAT, false, 20, 12);
    if (opts.noDepth) gl.disable(gl.DEPTH_TEST);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    if (opts.noDepth) gl.enable(gl.DEPTH_TEST);
    this.stats.draws++;
  }
  drawLines(viewProj, model, lineObj, color, width) {
    const gl = this.gl, p = this.flat;
    if (!lineObj || !lineObj.count) return;
    gl.useProgram(p.program);
    gl.uniformMatrix4fv(p.u.uMvp, false, M3.multiply(viewProj, model));
    gl.uniform4fv(p.u.uColor, color || [0, 0, 0, 1]);
    gl.bindTexture(gl.TEXTURE_2D, this.white);
    gl.uniform1f(p.u.uUseTex, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineObj.pos.buffer);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.disableVertexAttribArray(p.a.aUv);
    gl.vertexAttrib2f(p.a.aUv, 0, 0);
    gl.lineWidth(width || 1);
    gl.drawArrays(gl.LINES, 0, lineObj.count);
    this.stats.draws++;
  }
  drawMesh(viewProj, model, meshObj, color, light) {
    const gl = this.gl, p = this.lit;
    if (!meshObj || !meshObj.count) return;
    gl.useProgram(p.program);
    gl.uniformMatrix4fv(p.u.uMvp, false, M3.multiply(viewProj, model));
    gl.uniformMatrix4fv(p.u.uModel, false, model);
    gl.uniform4fv(p.u.uColor, color || [0.3, 0.6, 0.9, 1]);
    gl.uniform3fv(p.u.uLight, light || [0.3, 1, 0.5]);
    gl.bindBuffer(gl.ARRAY_BUFFER, meshObj.pos.buffer);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, meshObj.nrm.buffer);
    gl.enableVertexAttribArray(p.a.aNormal);
    gl.vertexAttribPointer(p.a.aNormal, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, meshObj.count);
    this.stats.draws++;
  }
}
if (typeof module !== 'undefined') module.exports = Renderer;
