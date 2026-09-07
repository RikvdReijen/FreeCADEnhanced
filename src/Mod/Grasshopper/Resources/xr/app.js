// SPDX-License-Identifier: LGPL-2.1-or-later
// Bootstraps the Grasshopper mode XR client: network, renderer, anchoring,
// input and the render loop for WebXR (AR/VR) or the desktop simulator.
'use strict';
class UI {
  constructor(app) {
    this.app = app;
    this.$ = (id) => document.getElementById(id);
    this.statusEl = this.$('status');
    this.lastStatus = '';
  }
  status(text) { this.lastStatus = text; if (this.statusEl) this.statusEl.textContent = text; this.app.log(text); }
  set(id, text) { const el = this.$(id); if (el) el.textContent = text; }
  show(id, on) { const el = this.$(id); if (el) el.hidden = !on; }
  bind() {
    const app = this.app;
    const on = (id, ev, fn) => { const el = this.$(id); if (el) el.addEventListener(ev, fn); };
    on('btn-ar', 'click', () => app.enterXR('immersive-ar'));
    on('btn-vr', 'click', () => app.enterXR('immersive-vr'));
    on('btn-sim', 'click', () => app.startSimulator());
    on('btn-exit', 'click', () => app.exitXR());
    on('btn-snap', 'click', () => (app.pendingAction = 'snap'));
    on('btn-front', 'click', () => (app.pendingAction = 'front'));
    on('btn-panel', 'click', () => (app.pendingAction = 'panel'));
    on('btn-calib', 'click', () => app.anchoring.startCalibration());
    on('btn-pin', 'click', () => (app.pendingAction = 'pin'));
    on('btn-fit', 'click', () => app.view.fitAll());
    on('btn-undo', 'click', () => app.net.send({ t: 'undo' }));
    on('btn-redo', 'click', () => app.net.send({ t: 'redo' }));
    on('btn-delete', 'click', () => app.net.send({ t: 'delete', selection: true }));
    on('btn-help', 'click', () => { const h = this.$('help'); h.hidden = !h.hidden; });
    on('btn-debug', 'click', () => { const d = this.$('debug'); d.hidden = !d.hidden; });
    on('btn-multi', 'click', () => { app.gestures.multiSelect = !app.gestures.multiSelect; this.$('btn-multi').classList.toggle('on', app.gestures.multiSelect); });
    on('profile', 'change', (e) => app.setProfile(e.target.value, true));
    on('tipmode', 'change', (e) => (app.input.tipMode = e.target.value));
    on('tipoffset', 'change', (e) => (app.input.tipOffset = parseFloat(e.target.value) / 1000 || 0));
    on('ctrl-as-stylus', 'change', (e) => (app.input.treatControllerAsStylus = e.target.checked));
    on('force-rays', 'change', (e) => (app.forceRays = e.target.checked));
    ['tip', 'front', 'middle', 'rear'].forEach((role) => on('map-' + role, 'change', (e) => (app.input.stylusMap[role] = parseInt(e.target.value, 10))));
    on('marker-link', 'click', (e) => { e.preventDefault(); window.open(app.httpBase + '/marker.html', '_blank'); });
  }
  fillProfiles(profiles, current) {
    const sel = this.$('profile'); if (!sel) return;
    sel.innerHTML = '';
    for (const [id, p] of Object.entries(profiles)) { const o = document.createElement('option'); o.value = id; o.textContent = p.label; if (id === current) o.selected = true; sel.appendChild(o); }
    this.setProfileText(profiles[current]);
  }
  setProfileText(p) { if (p) this.set('profile-summary', p.summary); }
  debug(app) {
    const d = this.$('debug'); if (!d || d.hidden) return;
    const st = app.input.debug.stylus;
    const lines = [
      'mode: ' + app.mode + '  anchor: ' + (app.anchoring.method || 'none') + '  clients: ' + app.clients,
      'ws: ' + (app.net.connected ? 'connected' : 'offline') + ' sent ' + app.net.stats.sent + ' recv ' + app.net.stats.received,
      'sources: ' + app.input.debug.sources + ' hands: ' + app.input.debug.hands + ' draws: ' + app.renderer.stats.draws,
      'view: ' + JSON.stringify(app.view.view) + ' hover: ' + (app.view.hover ? app.view.hover.kind : '-'),
      st ? 'stylus profiles: ' + (st.profiles || []).join(',') : 'stylus: not detected',
      st ? 'stylus buttons: [' + st.buttons.join(', ') + '] axes: [' + st.axes.join(', ') + ']' : '',
      'features: ' + (app.xrFeatures || []).join(', '),
      'events: ' + app.gestures.events.slice(-4).map((e) => e.kind + (e.hit ? ':' + e.hit : '')).join(' '),
    ];
    d.textContent = lines.join('\n');
  }
}

class App {
  constructor() {
    this.canvasEl = document.getElementById('gl');
    this.renderer = new Renderer(this.canvasEl);
    this.view = new CanvasView(this.renderer);
    this.anchoring = new Anchoring(this);
    this.input = new XRInput(this);
    this.gestures = new Gestures(this);
    this.ui = new UI(this);
    this.mode = 'idle';
    this.xr = null; this.refSpace = null; this.xrFeatures = [];
    this.clients = 0; this.pendingAction = null; this.forceRays = false;
    this.logLines = [];
    this.fragment = App.parseFragment();
    const loc = window.location;
    this.httpBase = loc.protocol + '//' + loc.host;
    const wsProto = loc.protocol === 'https:' ? 'wss://' : 'ws://';
    this.net = new Net(wsProto + loc.host + '/ws', {
      open: () => { this.ui.status('Connected to FreeCAD'); this.net.send({ t: 'hello', caps: this.caps(), profile: this.gestures.profile }); },
      close: () => this.ui.status('Disconnected - retrying'),
      hello: (m) => this.onHello(m),
      graph: (m) => { this.view.setGraph(m); this.ui.set('nodes', m.nodes.length + ' nodes'); if (!this.fitted) { this.view.fitAll(); this.fitted = true; } },
      patch: (m) => this.view.applyPatch(m),
      preview: (m) => this.view.setPreview(m),
      selection: (m) => this.view.setSelection(m.selection),
      clients: (m) => { this.clients = m.count; this.ui.set('clients', m.count + ' client' + (m.count === 1 ? '' : 's')); },
      profile: (m) => this.setProfile(m.profile, false),
      ack: (m) => { if (!m.ok && m.error) this.ui.status('Rejected: ' + m.error); },
    });
  }
  static parseFragment() {
    const out = {};
    for (const part of window.location.hash.replace(/^#/, '').split('&')) { const [k, v] = part.split('='); if (k) out[k] = decodeURIComponent(v || ''); }
    return out;
  }
  log(text) { this.logLines.push(text); if (this.logLines.length > 200) this.logLines.shift(); if (this.net && this.net.connected && this.mode !== 'idle') this.net.send({ t: 'log', text }); }
  caps() {
    return { xr: !!navigator.xr, hands: !!(window.XRHand), anchors: !!(window.XRAnchor), imageTracking: !!(window.XRFrame && XRFrame.prototype.getImageTrackingResults), planes: !!(window.XRPlane), ua: navigator.userAgent, mode: this.mode };
  }
  onHello(m) {
    this.view.setHello(m);
    this.anchoring.setMarker(m.marker);
    this.ui.fillProfiles(m.profiles, m.profile);
    this.profiles = m.profiles;
    this.setProfile(m.profile, false);
    this.ui.set('canvas-name', (m.name || 'canvas') + ' [' + m.canvas + ']');
    this.ui.set('backend', 'geometry: ' + m.backend);
    this.anchoring.prepareMarkerImage(this.httpBase);
  }
  setProfile(id, tellServer) {
    if (!this.profiles || !this.profiles[id]) return;
    this.gestures.setProfile(id);
    this.ui.setProfileText(this.profiles[id]);
    const sel = document.getElementById('profile'); if (sel && sel.value !== id) sel.value = id;
    if (tellServer) this.net.send({ t: 'set_profile', profile: id });
    // concept C wants a vertical panel; A/B a sheet on the table
    if (this.mode === 'sim' && this.anchoring.frame) this.placeSimFrame(id === 'floating-panel');
  }
  async start() {
    this.ui.bind();
    this.net.connect();
    const hasXR = !!(navigator.xr);
    let ar = false, vr = false;
    if (hasXR) {
      try { ar = await navigator.xr.isSessionSupported('immersive-ar'); } catch (e) { /* ignore */ }
      try { vr = await navigator.xr.isSessionSupported('immersive-vr'); } catch (e) { /* ignore */ }
    }
    this.ui.show('btn-ar', ar); this.ui.show('btn-vr', vr);
    this.ui.status(hasXR ? (ar ? 'Ready: enter AR to see the canvas on your table' : 'WebXR found (no AR passthrough)') : 'No WebXR here - desktop simulator only');
    if (this.fragment.auto === 'sim' || (!ar && !vr && this.fragment.auto !== 'none')) this.startSimulator();
    window.addEventListener('resize', () => this.resize());
    this.resize();
  }
  resize() {
    if (this.mode === 'xr') return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvasEl.width = Math.floor(this.canvasEl.clientWidth * dpr);
    this.canvasEl.height = Math.floor(this.canvasEl.clientHeight * dpr);
  }
  // -------------------------------------------------------------- simulator
  startSimulator() {
    if (this.mode === 'sim') return;
    this.exitXR();
    this.mode = 'sim';
    this.sim = this.sim || new Simulator(this, this.canvasEl);
    this.placeSimFrame(this.gestures.profile === 'floating-panel');
    this.ui.show('start', false); this.ui.show('xrbar', true);
    this.ui.status('Desktop simulator: left drag = stylus, hold F = flat hand (G = second hand), right drag = orbit, wheel = zoom, P = palette');
    this.resize();
    const loop = () => { if (this.mode !== 'sim') return; this.simFrame(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  placeSimFrame(vertical) {
    const scaleMm = (this.anchoring.marker && this.anchoring.marker.scale_mm) || 0.5;
    const viewer = { transform: { position: M3.v3(0, 1.2, 0), orientation: { x: 0, y: 0, z: 0, w: 1 } } };
    if (vertical) this.anchoring.frame = CanvasFrame.floatingPanel(viewer.transform.position, M3.v3(0, 0, -1), 0.8, scaleMm, this.view.sheet.w, this.view.sheet.h);
    else this.anchoring.frame = CanvasFrame.inFrontOf(viewer.transform.position, M3.v3(0, 0, -1), 0.55, 0.75, scaleMm, this.view.sheet.w, this.view.sheet.h);
    this.anchoring.method = 'simulator';
    if (this.sim) this.sim.orbit.target = this.anchoring.frame.toWorld(this.view.sheet.w / 2, this.view.sheet.h / 2, 0);
    if (this.sim && vertical) { this.sim.orbit.pitch = -0.1; this.sim.orbit.dist = 1.1; }
  }
  simFrame() {
    const t = performance.now() / 1000;
    this.frameCounter = (this.frameCounter || 0) + 1;
    const pointers = this.sim.pointers();
    this.gestures.update(pointers, t);
    const cam = this.sim.camera();
    this.renderer.begin({ x: 0, y: 0, width: this.canvasEl.width, height: this.canvasEl.height }, [0.16, 0.17, 0.2, 1]);
    this.drawWorld(cam.viewProj);
    this.ui.debug(this);
  }
  drawWorld(viewProj) {
    const f = this.anchoring.frame; if (!f) return;
    this.view.draw(viewProj, f);
    if (this.anchoring.calibration) {
      // show tapped calibration points
      for (const p of this.anchoring.calibration.points) {
        const m = M3.multiply(M3.fromAxes(p, f.x, f.y, f.normal), M3.scaling(0.01, 0.01, 1));
        this.renderer.drawQuad(viewProj, m, [1, 0, 0, 1], null, { noDepth: true });
      }
    }
  }
  // ------------------------------------------------------------------ XR
  async enterXR(kind) {
    if (!navigator.xr) return;
    const optional = ['local-floor', 'hand-tracking', 'anchors', 'plane-detection', 'hit-test', 'dom-overlay', 'layers'];
    const init = { optionalFeatures: optional, domOverlay: { root: document.getElementById('overlay') } };
    if (kind === 'immersive-ar') {
      const tracked = this.anchoring.trackedImagesOption();
      if (tracked) { init.optionalFeatures.push('image-tracking'); init.trackedImages = tracked; }
    }
    let session;
    try { session = await navigator.xr.requestSession(kind, init); }
    catch (e) { this.ui.status('XR session failed: ' + e.message); return; }
    if (this.mode === 'sim') this.mode = 'idle';
    this.mode = 'xr'; this.xr = session; this.xrKind = kind;
    this.xrFeatures = session.enabledFeatures || [];
    await this.renderer.gl.makeXRCompatible();
    const layer = new XRWebGLLayer(session, this.renderer.gl, { alpha: true });
    session.updateRenderState({ baseLayer: layer });
    try { this.refSpace = await session.requestReferenceSpace('local-floor'); }
    catch (e) { this.refSpace = await session.requestReferenceSpace('local'); }
    session.addEventListener('end', () => { this.xr = null; this.mode = 'idle'; this.ui.show('start', true); this.ui.status('XR session ended'); this.resize(); this.startSimulator(); });
    this.ui.show('start', false); this.ui.show('xrbar', true);
    this.anchoring.frame = null; this.anchoring.method = null;
    const restored = await this.anchoring.restore(session);
    this.ui.status(restored ? 'Anchor restored; look at the QR to refine' : (kind === 'immersive-ar' ? 'Look at the printed QR marker, or press "Snap to table"' : 'Sheet placed in front of you'));
    this.net.send({ t: 'hello', caps: this.caps(), profile: this.gestures.profile });
    this.frameCount = 0;
    session.requestAnimationFrame((t, f) => this.xrFrame(t, f));
  }
  exitXR() { if (this.xr) { try { this.xr.end(); } catch (e) { /* ignore */ } this.xr = null; } }
  xrFrame(time, frame) {
    const session = this.xr; if (!session) return;
    session.requestAnimationFrame((t, f) => this.xrFrame(t, f));
    const pose = frame.getViewerPose(this.refSpace);
    const t = time / 1000;
    this.anchoring.update(frame, this.refSpace, pose);
    this.frameCount++;
    if (pose) {
      if (!this.anchoring.frame) {
        // no anchor yet: try plane snap for a moment in AR, else put the sheet in front of the viewer
        const vertical = this.gestures.profile === 'floating-panel';
        if (this.xrKind === 'immersive-ar' && this.frameCount < 90 && !vertical) { if (this.anchoring.snapToPlane(frame, this.refSpace, pose)) this.ui.status('Snapped the sheet to your table'); }
        else this.anchoring.placeInFront(pose, vertical);
      }
      if (this.pendingAction) {
        const a = this.pendingAction; this.pendingAction = null;
        if (a === 'snap') { if (!this.anchoring.snapToPlane(frame, this.refSpace, pose)) this.ui.status('No table plane detected yet - look around the table'); }
        else if (a === 'front') this.anchoring.placeInFront(pose, false);
        else if (a === 'panel') this.anchoring.placeInFront(pose, true);
        else if (a === 'pin') this.anchoring.pin(frame, this.refSpace).then((ok) => this.ui.status(ok ? 'Anchor pinned' + (this.anchoring.persistedUuid ? ' and saved for next time' : '') : 'Could not create an anchor'));
      }
    }
    const pointers = this.input.collect(frame, this.refSpace, session);
    this.gestures.update(pointers, t);
    const layer = session.renderState.baseLayer;
    this.renderer.gl.bindFramebuffer(this.renderer.gl.FRAMEBUFFER, layer.framebuffer);
    const clear = this.xrKind === 'immersive-ar' ? [0, 0, 0, 0] : [0.1, 0.1, 0.12, 1];
    this.renderer.begin(null, clear);
    if (pose) {
      for (const v of pose.views) {
        const vp = layer.getViewport(v);
        this.renderer.gl.viewport(vp.x, vp.y, vp.width, vp.height);
        this.drawWorld(M3.multiply(v.projectionMatrix, v.transform.inverse.matrix));
      }
    }
    if (this.frameCount % 15 === 0) this.ui.debug(this);
  }
}
window.addEventListener('DOMContentLoaded', () => { window.app = new App(); window.app.start(); });
