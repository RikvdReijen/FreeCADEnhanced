// SPDX-License-Identifier: LGPL-2.1-or-later
// Viewpoint pictures: render the preview stage from the current viewpoint
// into an offscreen buffer in one of three modes and hand the PNG to
// FreeCAD, where it becomes a Picture node on the canvas.  Also flattens a
// picture plus its strokes for export.
'use strict';
class Snapshot {
  constructor(app) { this.app = app; this.size = { w: 1280, h: 960 }; this.fbo = null; this.last = null; }
  _ensureFbo() {
    const gl = this.app.renderer.gl, { w, h } = this.size;
    if (this.fbo && this.fbo.w === w && this.fbo.h === h) return this.fbo;
    const fb = gl.createFramebuffer(), tex = gl.createTexture(), depth = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.fbo = { fb, tex, depth, w, h };
    return this.fbo;
  }
  /**
   * Render the stage from `eye` looking at the stage centre (or with the
   * given view/projection) in `mode` and return a PNG data URL.
   */
  render(mode, camera) {
    const app = this.app, r = app.renderer, gl = r.gl, view = app.view, frame = app.anchoring.frame;
    if (!frame) throw new Error('no canvas frame');
    const fbo = this._ensureFbo();
    let viewProj = camera && camera.viewProj;
    if (!viewProj) {
      const centre = view.stageCenter(frame), ax = view.stageAxes(frame);
      const eye = camera && camera.eye ? camera.eye : M3.add(M3.add(centre, M3.scale(ax.z, view.stage.size * 0.9)), M3.scale(ax.y, -view.stage.size * 1.6));
      const target = M3.add(centre, M3.scale(ax.z, view.stage.size * 0.25));
      viewProj = M3.multiply(M3.perspective(0.8, fbo.w / fbo.h, 0.02, 20), M3.lookAt(eye, target, ax.z));
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo.fb);
    const bg = mode === 'outline' ? [1, 1, 1, 1] : (mode === 'preview' ? [0.94, 0.95, 0.97, 1] : [0.13, 0.14, 0.17, 1]);
    r.begin({ x: 0, y: 0, width: fbo.w, height: fbo.h }, bg);
    const model = view.stageModel(frame);
    for (const p of view.previews) {
      if (mode === 'outline') {
        // white bodies occlude hidden lines, then the edges on top
        if (p.mesh) r.drawMesh(viewProj, model, p.mesh, [1, 1, 1, 1], null, { flat: 1 });
        if (p.lines) r.drawLines(viewProj, model, p.lines, [0.05, 0.05, 0.05, 1], 2);
      } else if (mode === 'preview') {
        if (p.mesh) r.drawMesh(viewProj, model, p.mesh, [p.color[0], p.color[1], p.color[2], 1], [0.3, 0.8, 0.6], { flat: 0.6 });
        if (p.lines) r.drawLines(viewProj, model, p.lines, [0.2, 0.2, 0.25, 1], 1);
      } else {
        if (p.mesh) r.drawMesh(viewProj, model, p.mesh, [p.color[0], p.color[1], p.color[2], 1], [0.4, 0.9, 0.5]);
      }
    }
    const pixels = new Uint8Array(fbo.w * fbo.h * 4);
    gl.readPixels(0, 0, fbo.w, fbo.h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    // flip into a 2D canvas (GL rows are bottom-up)
    const c = document.createElement('canvas'); c.width = fbo.w; c.height = fbo.h;
    const g = c.getContext('2d'), img = g.createImageData(fbo.w, fbo.h);
    for (let y = 0; y < fbo.h; y++) img.data.set(pixels.subarray((fbo.h - 1 - y) * fbo.w * 4, (fbo.h - y) * fbo.w * 4), y * fbo.w * 4);
    g.putImageData(img, 0, 0);
    this.last = { mode, canvas: c };
    return c.toDataURL('image/png');
  }
  /** Take a picture in `mode` from the viewer's position and add it to the canvas. */
  async capture(mode, camera) {
    const app = this.app;
    let png;
    try { png = this.render(mode, camera); } catch (e) { app.ui.status('Picture failed: ' + e.message); return null; }
    const pose = camera && camera.eye ? { eye: camera.eye } : null;
    const ack = await app.net.request({ t: 'snapshot', mode, png, pose, note: 'from ' + app.mode });
    if (ack.ok) app.ui.status('Picture added to the canvas (' + mode + ')'); else app.ui.status('Picture rejected: ' + ack.error);
    return ack;
  }
  /** Flatten a picture node (image + strokes) and send it for export. */
  async exportNode(nodeId) {
    const app = this.app, node = app.view.nodes.get(nodeId);
    if (!node || node.widget !== 'image') { app.ui.status('Select a picture first'); return null; }
    const img = await app.view.loadImage(node.params.url);
    const c = document.createElement('canvas'); c.width = node.params.px_w || img.width; c.height = node.params.px_h || img.height;
    const g = c.getContext('2d');
    if (img) g.drawImage(img, 0, 0, c.width, c.height);
    CanvasView.drawStrokes(g, node.params.strokes || [], 1, 1);
    const ack = await app.net.request({ t: 'export_picture', node: nodeId, png: c.toDataURL('image/png') });
    app.ui.status(ack.ok ? 'Exported to ' + ack.path + (ack.hook ? ' (' + ack.hook + ')' : '') : 'Export failed: ' + ack.error);
    return ack;
  }
}
if (typeof module !== 'undefined') module.exports = Snapshot;
