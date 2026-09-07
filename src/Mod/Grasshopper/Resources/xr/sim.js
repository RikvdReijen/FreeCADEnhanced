// SPDX-License-Identifier: LGPL-2.1-or-later
// Desktop simulator: the same sheet rendered on a virtual table with the
// mouse standing in for the stylus and a key for the flat hand.  Lets the
// interaction logic be exercised (and unit tested with a headless browser)
// without a headset.
'use strict';
class Simulator {
  constructor(app, canvas) {
    this.app = app; this.canvas = canvas;
    this.orbit = { yaw: 0.0, pitch: -0.95, dist: 0.9, target: M3.v3(0, 0.75, -0.55) };
    this.mouse = { x: 0, y: 0, down: false, button: 0, world: null, height: 0.05, hover: 0.05 };
    this.flatHand = false; this.secondHand = null; this.keys = new Set();
    this.bind();
    this.pointerLog = [];
  }
  bind() {
    const c = this.canvas;
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('pointerdown', (e) => { c.setPointerCapture(e.pointerId); this.mouse.down = true; this.mouse.button = e.button; this.last = { x: e.clientX, y: e.clientY }; this._pos(e); });
    c.addEventListener('pointermove', (e) => { if (this.mouse.down && this.mouse.button === 2 && this.last) { this.orbit.yaw -= (e.clientX - this.last.x) * 0.005; this.orbit.pitch = Math.max(-1.5, Math.min(-0.2, this.orbit.pitch - (e.clientY - this.last.y) * 0.005)); } this.last = { x: e.clientX, y: e.clientY }; this._pos(e); });
    c.addEventListener('pointerup', (e) => { this.mouse.down = false; this._pos(e); });
    c.addEventListener('wheel', (e) => { e.preventDefault(); if (e.ctrlKey) this.orbit.dist = Math.max(0.3, Math.min(3, this.orbit.dist * (e.deltaY > 0 ? 1.1 : 0.9))); else { const s = this._sheetAtMouse(); if (s) this.app.view.zoomAt(e.deltaY > 0 ? 0.9 : 1.1, s.x, s.y); } }, { passive: false });
    window.addEventListener('keydown', (e) => this._key(e, true));
    window.addEventListener('keyup', (e) => this._key(e, false));
  }
  _key(e, down) {
    if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === 'f' || e.key === 'F') { this.flatHand = down; }
    if (e.key === 'g' || e.key === 'G') { this.secondHand = down ? (this.secondHand || { dx: 300 }) : null; }
    if (!down) return;
    const net = this.app.net;
    if (e.key === 'Delete' || e.key === 'Backspace') net.send({ t: 'delete', selection: true });
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') net.send({ t: e.shiftKey ? 'redo' : 'undo' });
    if ((e.ctrlKey || e.metaKey) && e.key === 'd') { e.preventDefault(); net.send({ t: 'duplicate' }); }
    if (e.key === 'p' || e.key === 'P') { const s = this._sheetAtMouse(); if (s) { const c = this.app.view.toCanvas(s.x, s.y); this.app.view.openPalette(c.x, c.y); } }
    if (e.key === 'Escape') this.app.view.closePalette();
    if (e.key === 'Home') this.app.view.fitAll();
    if (e.key === 'Shift') this.app.gestures.multiSelect = true;
    if (e.key === 'ArrowLeft') this.app.view.pan(-40, 0);
    if (e.key === 'ArrowRight') this.app.view.pan(40, 0);
    if (e.key === 'ArrowUp') this.app.view.pan(0, -40);
    if (e.key === 'ArrowDown') this.app.view.pan(0, 40);
  }
  _pos(e) { const r = this.canvas.getBoundingClientRect(); this.mouse.x = e.clientX - r.left; this.mouse.y = e.clientY - r.top; if (e.type === 'keyup' || e.key === 'Shift') this.app.gestures.multiSelect = false; }
  camera() {
    const o = this.orbit;
    const eye = M3.add(o.target, M3.v3(o.dist * Math.sin(o.yaw) * Math.cos(o.pitch), -o.dist * Math.sin(o.pitch), o.dist * Math.cos(o.yaw) * Math.cos(o.pitch)));
    const view = M3.lookAt(eye, o.target, M3.v3(0, 1, 0));
    const proj = M3.perspective(1.0, this.canvas.width / this.canvas.height, 0.05, 20);
    return { eye, view, proj, viewProj: M3.multiply(proj, view) };
  }
  ray() {
    const cam = this.camera(), inv = M3.invert(cam.viewProj);
    const nx = (this.mouse.x / this.canvas.clientWidth) * 2 - 1, ny = 1 - (this.mouse.y / this.canvas.clientHeight) * 2;
    const near = M3.transformPoint(inv, M3.v3(nx, ny, -1)), far = M3.transformPoint(inv, M3.v3(nx, ny, 1));
    return { origin: near, dir: M3.norm(M3.sub(far, near)) };
  }
  _sheetAtMouse() {
    const f = this.app.anchoring.frame; if (!f) return null;
    const r = this.ray(), hit = M3.rayPlane(r.origin, r.dir, f.origin, f.normal);
    return hit ? f.toSheet(hit) : null;
  }
  /** Produce pointers like XRInput does. */
  pointers() {
    const f = this.app.anchoring.frame; if (!f) return [];
    const r = this.ray(), hit = M3.rayPlane(r.origin, r.dir, f.origin, f.normal);
    if (!hit) return [];
    const out = [];
    const pressed = this.mouse.down && this.mouse.button === 0;
    if (this.flatHand) {
      out.push({ id: 'sim-palm-right', kind: 'palm', hand: 'right', world: hit, flat: 1, pressed: null, pinch: 0 });
      if (this.secondHand) { const s = f.toSheet(hit); out.push({ id: 'sim-palm-left', kind: 'palm', hand: 'left', world: f.toWorld(s.x + this.secondHand.dx, s.y, 0), flat: 1, pressed: null, pinch: 0 }); }
    } else {
      const world = M3.add(hit, M3.scale(f.normal, pressed ? 0.0 : this.mouse.hover));
      out.push({ id: 'stylus', kind: 'stylus', hand: 'right', world, pressed, pressure: pressed ? 1 : 0, buttons: { front: false, middle: this.mouse.down && this.mouse.button === 1, rear: false } });
    }
    return out;
  }
  /** Programmatic driver used by tests: move/press/release at canvas coords.
   *  Returns a promise that resolves after the simulator processed two frames. */
  drive(action, cx, cy) {
    this._drive(action, cx, cy);
    const app = this.app, start = app.frameCounter || 0;
    return new Promise((resolve) => { const check = () => ((app.frameCounter || 0) >= start + 2 ? resolve(app.frameCounter) : setTimeout(check, 5)); check(); });
  }
  _drive(action, cx, cy) {
    const f = this.app.anchoring.frame, view = this.app.view;
    const s = view.toSheet(cx, cy), w = f.toWorld(s.x, s.y, 0);
    const cam = this.camera(), clip = M3.transformPoint(cam.viewProj, w);
    this.mouse.x = (clip.x + 1) / 2 * this.canvas.clientWidth; this.mouse.y = (1 - clip.y) / 2 * this.canvas.clientHeight;
    if (action === 'down') { this.mouse.down = true; this.mouse.button = 0; }
    if (action === 'up') this.mouse.down = false;
    if (action === 'flat') this.flatHand = true;
    if (action === 'unflat') this.flatHand = false;
  }
}
if (typeof module !== 'undefined') module.exports = Simulator;
