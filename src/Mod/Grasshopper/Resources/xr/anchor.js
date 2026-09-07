// SPDX-License-Identifier: LGPL-2.1-or-later
// Where is the sheet?  Produces a CanvasFrame in world (XR reference space)
// coordinates from one of several sources, best first:
//   image     WebXR image tracking of the printed QR (Chrome/ARCore)
//   persisted a persistent XRAnchor saved from an earlier session (Quest)
//   stylus3   three taps with the MX Ink / fingertip on the printed marker corners
//   plane     snapped onto a detected horizontal plane in front of the user
//   manual    a fixed place in front of the viewer (VR / no tracking)
// Mirrors grasshoppermode/markers.py.
'use strict';
class CanvasFrame {
  constructor(origin, xAxis, yHint, scaleMm) {
    this.origin = origin;
    this.x = M3.norm(xAxis);
    const y = M3.sub(yHint, M3.scale(this.x, M3.dot(yHint, this.x)));
    this.y = M3.norm(y);
    // canvas y points down the sheet, so up-normal is y cross x
    this.normal = M3.norm(M3.cross(this.y, this.x));
    this.scaleMm = scaleMm || 0.5;
  }
  get unitM() { return this.scaleMm / 1000; }
  toWorld(sx, sy, h = 0) {
    const u = this.unitM;
    return M3.add(M3.add(M3.add(this.origin, M3.scale(this.x, sx * u)), M3.scale(this.y, sy * u)), M3.scale(this.normal, h));
  }
  /** world -> {x, y (sheet units), h (metres above sheet)} */
  toSheet(p) {
    const d = M3.sub(p, this.origin), u = this.unitM;
    return { x: M3.dot(d, this.x) / u, y: M3.dot(d, this.y) / u, h: M3.dot(d, this.normal) };
  }
  translated(delta) { return new CanvasFrame(M3.add(this.origin, delta), this.x, this.y, this.scaleMm); }
  toJSON() { return { origin: this.origin, x: this.x, y: this.y, normal: this.normal, scale_mm: this.scaleMm }; }
  static fromJSON(d) { return new CanvasFrame(d.origin, d.x, d.y, d.scale_mm); }
  /**
   * From a tracked marker pose. markerUp: 'y' (WebXR image tracking: image
   * normal is +y, image top is -z) or 'z' (our own calibration poses).
   */
  static fromMarker(position, orientation, marker, markerUp = 'y') {
    const a = M3.quatAxes(orientation);
    const right = a.x, top = markerUp === 'y' ? M3.scale(a.z, -1) : a.y;
    const unit = (marker.scale_mm || 0.5) / 1000, off = marker.offset || [0, 0];
    const origin = M3.sub(M3.sub(position, M3.scale(right, off[0] * unit)), M3.scale(M3.scale(top, -1), off[1] * unit));
    return new CanvasFrame(origin, right, M3.scale(top, -1), marker.scale_mm);
  }
  static fromPoints(pOrigin, pX, pY, scaleMm) {
    const x = M3.sub(pX, pOrigin);
    if (M3.len(x) < 1e-4) throw new Error('x point coincides with origin');
    return new CanvasFrame(pOrigin, x, M3.sub(pY, pOrigin), scaleMm);
  }
  /** Sheet lying flat with its far edge away from the viewer. */
  static inFrontOf(viewerPos, viewerForward, distance, height, scaleMm, sheetW, sheetH) {
    const fwd = M3.norm(M3.v3(viewerForward.x, 0, viewerForward.z));
    const right = M3.norm(M3.cross(fwd, M3.v3(0, 1, 0)));
    const unit = (scaleMm || 0.5) / 1000;
    const centre = M3.add(M3.v3(viewerPos.x, height, viewerPos.z), M3.scale(fwd, distance));
    // origin is the top-left corner: far-left from the viewer
    const origin = M3.add(M3.add(centre, M3.scale(right, -sheetW * unit / 2)), M3.scale(fwd, sheetH * unit / 2));
    return new CanvasFrame(origin, right, M3.scale(fwd, -1), scaleMm);
  }
  /** Vertical panel facing the viewer (concept C). */
  static floatingPanel(viewerPos, viewerForward, distance, scaleMm, sheetW, sheetH) {
    const fwd = M3.norm(M3.v3(viewerForward.x, 0, viewerForward.z));
    const right = M3.norm(M3.cross(fwd, M3.v3(0, 1, 0)));
    const unit = (scaleMm || 0.5) / 1000;
    const centre = M3.add(viewerPos, M3.scale(fwd, distance));
    const origin = M3.add(M3.add(centre, M3.scale(right, -sheetW * unit / 2)), M3.v3(0, sheetH * unit / 2, 0));
    return new CanvasFrame(origin, right, M3.v3(0, -1, 0), scaleMm);
  }
}

class Anchoring {
  constructor(app) {
    this.app = app;
    this.frame = null;
    this.method = null;
    this.marker = null;          // marker spec from hello
    this.markerBitmap = null;    // ImageBitmap for image tracking
    this.calibration = null;     // {points: []} while tapping corners
    this.xrAnchor = null;
    this.persistedUuid = null;
    this.smoothing = 0.25;
    this.lastImageSeen = 0;
    this.lockedToPlane = true;
  }
  setMarker(marker) { this.marker = marker; }
  storageKey() { return 'fcgh.anchor.' + (this.marker ? this.marker.canvas : 'default'); }

  /** Build the marker bitmap for WebXR image tracking from /marker.json. */
  async prepareMarkerImage(baseUrl) {
    try {
      const res = await fetch(baseUrl + '/marker.json?i=0');
      const data = await res.json();
      const mods = data.modules, q = data.quiet || 4, n = mods.length, px = 8;
      const c = document.createElement('canvas'); c.width = c.height = (n + 2 * q) * px;
      const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.fillStyle = '#000';
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (mods[y][x]) g.fillRect((x + q) * px, (y + q) * px, px, px);
      this.markerBitmap = await createImageBitmap(c);
      this.markerWidthM = data.marker.size_mm / 1000;
      return true;
    } catch (e) { this.app.log('marker image unavailable: ' + e.message); return false; }
  }
  trackedImagesOption() {
    return this.markerBitmap ? [{ image: this.markerBitmap, widthInMeters: this.markerWidthM }] : null;
  }

  /** Called every XR frame. */
  update(frame, refSpace, viewerPose) {
    if (!frame) return;
    // 1. image tracking (highest priority, live)
    if (frame.getImageTrackingResults) {
      try {
        for (const r of frame.getImageTrackingResults()) {
          if (r.trackingState !== 'tracked') continue;
          const pose = frame.getPose(r.imageSpace, refSpace);
          if (!pose) continue;
          const f = CanvasFrame.fromMarker(pose.transform.position, pose.transform.orientation, this.marker || {}, 'y');
          this._blend(f, 'image');
          this.lastImageSeen = performance.now();
          this.imageReported = this.imageReported || this._report('image');
        }
      } catch (e) { /* not supported */ }
    }
    // 2. live XRAnchor keeps the frame stable when the image is out of view
    if (this.xrAnchor && this.anchorLocal && (performance.now() - this.lastImageSeen > 500 || this.method !== 'image')) {
      const pose = frame.getPose(this.xrAnchor.anchorSpace, refSpace);
      if (pose) {
        const a = M3.quatAxes(pose.transform.orientation), p = pose.transform.position, L = this.anchorLocal;
        const w = (v) => M3.add(p, M3.add(M3.add(M3.scale(a.x, v.x), M3.scale(a.y, v.y)), M3.scale(a.z, v.z)));
        const origin = w(L.origin), xd = M3.sub(w(M3.add(L.origin, L.x)), origin), yd = M3.sub(w(M3.add(L.origin, L.y)), origin);
        this.frame = new CanvasFrame(origin, xd, yd, this.frame ? this.frame.scaleMm : (this.marker && this.marker.scale_mm));
      }
    }
  }
  _blend(f, method) {
    if (!this.frame || this.method !== method) { this.frame = f; this.method = method; return; }
    const t = this.smoothing;
    this.frame = new CanvasFrame(M3.lerp(this.frame.origin, f.origin, t), M3.lerp(this.frame.x, f.x, t), M3.lerp(this.frame.y, f.y, t), f.scaleMm);
  }
  _report(method, extra) {
    this.app.net.send({ t: 'anchor', method, frame: this.frame && this.frame.toJSON(), marker: this.marker && this.marker.canvas, uuid: this.persistedUuid, ...extra });
    return true;
  }
  set(frame, method) { this.frame = frame; this.method = method; this._report(method); this.app.ui.status('Canvas anchored (' + method + ')'); }
  report(method) { if (this.frame) this._report(method); }

  /** Pin the current frame with an XRAnchor (and persist it on Quest). */
  async pin(xrFrame, refSpace) {
    if (!this.frame || !xrFrame || !xrFrame.createAnchor) return false;
    try {
      const f = this.frame;
      const m = M3.fromAxes(f.origin, f.x, f.normal, M3.scale(f.y, -1)); // y-up anchor pose
      const q = Anchoring.matToQuat(m);
      const anchor = await xrFrame.createAnchor(new XRRigidTransform(f.origin, q), refSpace);
      this.xrAnchor = anchor;
      // frame expressed in the anchor's local space (identity rotation relative to it)
      this.anchorLocal = { origin: M3.v3(0, 0, 0), x: M3.v3(1, 0, 0), y: M3.v3(0, 0, -1) };
      // in local space: x -> +x, normal -> +y, canvas y -> -z
      this.anchorLocal.y = M3.v3(0, 0, -1);
      if (anchor.requestPersistentHandle) {
        try { this.persistedUuid = await anchor.requestPersistentHandle(); localStorage.setItem(this.storageKey(), JSON.stringify({ uuid: this.persistedUuid, scaleMm: f.scaleMm })); } catch (e) { this.app.log('persist failed: ' + e.message); }
      }
      this._report(this.method + '+anchor');
      return true;
    } catch (e) { this.app.log('anchor failed: ' + e.message); return false; }
  }
  async restore(session) {
    try {
      const raw = localStorage.getItem(this.storageKey());
      if (!raw || !session.restorePersistentAnchor) return false;
      const saved = JSON.parse(raw);
      const anchor = await session.restorePersistentAnchor(saved.uuid);
      this.xrAnchor = anchor; this.persistedUuid = saved.uuid;
      this.anchorLocal = { origin: M3.v3(0, 0, 0), x: M3.v3(1, 0, 0), y: M3.v3(0, 0, -1) };
      this.method = 'persisted';
      this.app.ui.status('Restored canvas anchor from last session');
      return true;
    } catch (e) { this.app.log('restore failed: ' + e.message); return false; }
  }
  static matToQuat(m) {
    const m00 = m[0], m01 = m[4], m02 = m[8], m10 = m[1], m11 = m[5], m12 = m[9], m20 = m[2], m21 = m[6], m22 = m[10];
    const tr = m00 + m11 + m22; let x, y, z, w;
    if (tr > 0) { const s = Math.sqrt(tr + 1) * 2; w = s / 4; x = (m21 - m12) / s; y = (m02 - m20) / s; z = (m10 - m01) / s; }
    else if (m00 > m11 && m00 > m22) { const s = Math.sqrt(1 + m00 - m11 - m22) * 2; w = (m21 - m12) / s; x = s / 4; y = (m01 + m10) / s; z = (m02 + m20) / s; }
    else if (m11 > m22) { const s = Math.sqrt(1 + m11 - m00 - m22) * 2; w = (m02 - m20) / s; x = (m01 + m10) / s; y = s / 4; z = (m12 + m21) / s; }
    else { const s = Math.sqrt(1 + m22 - m00 - m11) * 2; w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = s / 4; }
    return { x, y, z, w };
  }

  // ---- three-point stylus calibration: tap marker top-left, top-right, bottom-left
  startCalibration() { this.calibration = { points: [] }; this.app.ui.status('Calibration: tap the TOP-LEFT corner of the printed marker'); }
  calibrationTap(worldPoint) {
    if (!this.calibration) return false;
    const pts = this.calibration.points; pts.push(worldPoint);
    const prompts = ['tap the TOP-RIGHT corner', 'tap the BOTTOM-LEFT corner'];
    if (pts.length < 3) { this.app.ui.status('Calibration: ' + prompts[pts.length - 1]); return true; }
    try {
      const scaleMm = (this.marker && this.marker.scale_mm) || 0.5;
      const sizeM = ((this.marker && this.marker.size_mm) || 80) / 1000;
      // the tapped square is the marker; the sheet origin is the marker centre
      const x = M3.norm(M3.sub(pts[1], pts[0])), yDown = M3.norm(M3.sub(pts[2], pts[0]));
      const centre = M3.add(M3.add(pts[0], M3.scale(x, sizeM / 2)), M3.scale(yDown, sizeM / 2));
      const f = CanvasFrame.fromPoints(centre, M3.add(centre, x), M3.add(centre, yDown), scaleMm);
      const off = (this.marker && this.marker.offset) || [0, 0];
      const origin = M3.sub(M3.sub(f.origin, M3.scale(f.x, off[0] * f.unitM)), M3.scale(f.y, off[1] * f.unitM));
      this.set(new CanvasFrame(origin, f.x, f.y, scaleMm), 'stylus3');
    } catch (e) { this.app.ui.status('Calibration failed: ' + e.message); }
    this.calibration = null;
    return true;
  }
  cancelCalibration() { this.calibration = null; }

  /** Snap onto a detected horizontal plane (Quest plane detection). */
  snapToPlane(xrFrame, refSpace, viewerPose) {
    const planes = xrFrame.detectedPlanes;
    if (!planes || !viewerPose) return false;
    let best = null, bestScore = Infinity;
    const vp = viewerPose.transform.position;
    for (const plane of planes) {
      if (plane.orientation && plane.orientation !== 'horizontal') continue;
      const pose = xrFrame.getPose(plane.planeSpace, refSpace);
      if (!pose) continue;
      const p = pose.transform.position, a = M3.quatAxes(pose.transform.orientation);
      if (Math.abs(a.y.y) < 0.9) continue; // not horizontal enough
      const h = vp.y - p.y; // plane below the eyes by 0.3..1.2 m looks like a table
      if (h < 0.2 || h > 1.3) continue;
      const score = M3.dist(vp, p);
      if (score < bestScore) { bestScore = score; best = { p, a, plane }; }
    }
    if (!best) return false;
    const fwd = M3.quatRotate(viewerPose.transform.orientation, M3.v3(0, 0, -1));
    const scaleMm = (this.marker && this.marker.scale_mm) || 0.5;
    const f = CanvasFrame.inFrontOf(vp, fwd, 0.45, best.p.y + 0.002, scaleMm, this.app.view.sheet.w, this.app.view.sheet.h);
    this.set(f, 'plane');
    return true;
  }
  placeInFront(viewerPose, vertical) {
    const vp = viewerPose.transform.position, fwd = M3.quatRotate(viewerPose.transform.orientation, M3.v3(0, 0, -1));
    const scaleMm = (this.marker && this.marker.scale_mm) || 0.5;
    const f = vertical
      ? CanvasFrame.floatingPanel(vp, fwd, 0.7, scaleMm, this.app.view.sheet.w, this.app.view.sheet.h)
      : CanvasFrame.inFrontOf(vp, fwd, 0.5, vp.y - 0.45, scaleMm, this.app.view.sheet.w, this.app.view.sheet.h);
    this.set(f, vertical ? 'manual-panel' : 'manual');
  }
}
if (typeof module !== 'undefined') module.exports = { CanvasFrame, Anchoring };
