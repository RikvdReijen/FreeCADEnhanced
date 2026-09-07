// SPDX-License-Identifier: LGPL-2.1-or-later
// Interaction state machine: pointers in, graph intents out.
//
// Rules (decided with the user):
//   flat hand on the sheet      -> always pans the canvas (two flat hands zoom)
//   finger / tip on a node      -> moves the node (selection moves together)
//   finger / tip on empty sheet -> pans the canvas (stylus & controller: marquee)
//   tap                         -> select (tap empty: clear)
//   controller ray + trigger    -> same as a fingertip touch at the ray hit
//   grip / pinch on the handle  -> moves the whole panel
//   grip / pinch on the stage   -> moves the preview stage
//   thumbsticks                 -> left pans, right zooms
'use strict';
class ContactDetector {
  constructor(o = {}) {
    this.downM = (o.downMm ?? 6) / 1000; this.upM = (o.upMm ?? 12) / 1000;
    this.tapMaxS = o.tapMaxS ?? 0.35; this.tapMaxMove = o.tapMaxMove ?? 12; this.holdS = o.holdS ?? 0.6;
    this.touching = false; this.downT = 0; this.downPos = null; this.moved = 0; this.holdSent = false;
  }
  /** h is the signed height above the sheet: negative = poked through the panel, still touching. */
  update(h, cx, cy, t, pressed) {
    const ev = [];
    let want;
    if (pressed !== null && pressed !== undefined) want = !!pressed;
    else want = this.touching ? h < this.upM : h < this.downM;
    if (want && !this.touching) { this.touching = true; this.downT = t; this.downPos = [cx, cy]; this.moved = 0; this.holdSent = false; ev.push(['down', cx, cy]); }
    else if (want && this.touching) {
      this.moved = Math.max(this.moved, Math.hypot(cx - this.downPos[0], cy - this.downPos[1]));
      ev.push(['move', cx, cy]);
      if (!this.holdSent && this.moved <= this.tapMaxMove && t - this.downT >= this.holdS) { this.holdSent = true; ev.push(['hold', cx, cy]); }
    } else if (!want && this.touching) {
      this.touching = false; ev.push(['up', cx, cy]);
      if (this.moved <= this.tapMaxMove && t - this.downT <= this.tapMaxS) ev.push(['tap', this.downPos[0], this.downPos[1]]);
    }
    return ev;
  }
}

class Gestures {
  constructor(app) {
    this.app = app;
    this.view = app.view;
    this.states = new Map();   // pointer id -> {detector, press, grab, ...}
    this.palms = new Map();    // pointer id -> {last, cur}
    this.zoomStart = null;
    this.profile = 'floating-panel';
    this.multiSelect = false;  // sticky modifier from the UI
    this.lastSliderSend = 0;
    this.lastT = null;
    this.events = [];          // recent high level events (debug / tests)
    this.grabbing = null;      // {what:'stage'|'panel', pointer}
  }
  setProfile(p) { this.profile = p; }
  _st(id) { let s = this.states.get(id); if (!s) { s = { detector: new ContactDetector(), press: null, grab: null }; this.states.set(id, s); } return s; }
  _log(kind, data) { this.events.push({ kind, ...data }); if (this.events.length > 100) this.events.shift(); }

  /** Called once per frame with the pointer list (world coordinates). */
  update(pointers, t) {
    const frame = this.app.anchoring.frame, view = this.view;
    const dt = this.lastT === null ? 0 : Math.min(0.1, t - this.lastT); this.lastT = t;
    view.pointerMarks = []; view.hotHandle = false; view.hotStage = false;
    if (!frame) return;
    const seenPalms = new Set();
    let primaryHit = null;
    for (const p of pointers) {
      if (p.kind === 'palm') { seenPalms.add(p.id); this._palm(p, t); continue; }
      const st = this._st(p.id);
      if (p.axes) this._thumbstick(p, dt);
      // grabs (panel handle / preview stage) come before sheet contact
      if (this._grab(p, st, frame)) continue;
      let world = p.world, height = null;
      if (p.kind === 'ray') {
        const hit = M3.rayPlane(p.rayOrigin, p.rayDir, frame.origin, frame.normal);
        if (!hit) { if (st.detector.touching) this._finish(st, p, t); continue; }
        world = hit; height = 0;
      }
      if (!world) continue;
      const s = frame.toSheet(world);
      if (height === null) height = s.h;
      const c = view.toCanvas(s.x, s.y);
      st.sheet = s;
      let pressed = p.pressed;
      if (p.kind === 'ray') pressed = !!p.pressed;
      const events = st.detector.update(height, c.x, c.y, t, pressed);
      const inside = s.x >= -50 && s.y >= -50 && s.x <= view.sheet.w + 50 && s.y <= view.sheet.h + 50;
      view.pointerMarks.push({ x: c.x, y: c.y, kind: p.kind, pressed: st.detector.touching, height: Math.max(0, height) });
      if (p.kind === 'stylus' && p.buttons) this._stylusButtons(p, st, c, t);
      if (!primaryHit && inside) primaryHit = view.hitTest(c.x, c.y);
      for (const [ev, x, y] of events) this._contact(ev, x, y, st, p, t, inside);
    }
    for (const id of [...this.palms.keys()]) if (!seenPalms.has(id)) this.palms.delete(id);
    if (this.palms.size < 2) this.zoomStart = null;
    view.hover = primaryHit;
  }
  _finish(st, p, t) {
    // pointer left the sheet plane while touching: release
    const s = st.sheet || { x: 0, y: 0 }, c = this.view.toCanvas(s.x, s.y);
    for (const [ev, x, y] of st.detector.update(1, c.x, c.y, t, false)) this._contact(ev, x, y, st, p, t, false);
  }

  // ------------------------------------------------------------ grabbing
  /** Pointer world position or ray hit against the stage / handle. Returns true when the pointer is consumed. */
  _grab(p, st, frame) {
    const view = this.view;
    const wantGrab = p.kind === 'ray' ? !!(p.buttons && p.buttons.middle) || !!p.pressed : (p.kind === 'index' ? p.pinch > 0.7 : !!(p.buttons && p.buttons.middle));
    if (st.grab) {
      const g = st.grab;
      const pos = p.kind === 'ray' ? M3.add(p.rayOrigin, M3.scale(p.rayDir, g.dist)) : p.world;
      const delta = M3.sub(pos, g.start);
      if (g.what === 'stage') {
        const s = frame.toSheet(M3.add(g.stageWorld, delta));
        view.stage.x = s.x; view.stage.y = s.y; view.stage.h = Math.max(0, s.h);
        view.hotStage = true;
      } else {
        this.app.anchoring.frame = g.frame.translated(delta);
        view.hotHandle = true;
      }
      if (!wantGrab) { st.grab = null; this.grabbing = null; this._log('grab-end', { what: g.what }); this.app.anchoring.report && this.app.anchoring.report('moved'); }
      return true;
    }
    // hit tests
    let target = null, dist = 0, pos = p.world;
    const stageC = view.stageCenter(frame), radius = view.stage.size * 0.6;
    if (p.kind === 'ray') {
      const toC = M3.sub(stageC, p.rayOrigin), tAlong = M3.dot(toC, p.rayDir);
      if (tAlong > 0 && M3.len(M3.sub(toC, M3.scale(p.rayDir, tAlong))) < radius) { target = 'stage'; dist = tAlong; pos = M3.add(p.rayOrigin, M3.scale(p.rayDir, tAlong)); }
      else {
        const hit = M3.rayPlane(p.rayOrigin, p.rayDir, frame.origin, frame.normal);
        if (hit && view.inHandle(frame.toSheet(hit))) { target = 'panel'; dist = M3.dist(hit, p.rayOrigin); pos = hit; }
      }
    } else if (p.world) {
      if (M3.dist(p.world, stageC) < radius) target = 'stage';
      else { const s = frame.toSheet(p.world); if (Math.abs(s.h) < 0.05 && view.inHandle(s)) target = 'panel'; }
    }
    if (!target) return false;
    if (target === 'stage') view.hotStage = true; else view.hotHandle = true;
    if (wantGrab && !st.wasGrabbing) {
      st.grab = { what: target, dist, start: pos, stageWorld: stageC, frame };
      this.grabbing = { what: target, pointer: p.id };
      this._log('grab', { what: target, pointer: p.kind });
    }
    st.wasGrabbing = wantGrab;
    return target === 'stage' || (target === 'panel' && wantGrab);
  }
  _thumbstick(p, dt) {
    const ax = p.axes[2] ?? p.axes[0] ?? 0, ay = p.axes[3] ?? p.axes[1] ?? 0;
    if (Math.abs(ax) < 0.2 && Math.abs(ay) < 0.2) return;
    if (p.hand === 'right') this.view.zoomAt(1 - ay * dt * 1.5, this.view.sheet.w / 2, this.view.sheet.h / 2);
    else this.view.pan(-ax * dt * 900, ay * dt * 900);
  }

  // ------------------------------------------------------------- contact
  _contact(ev, x, y, st, p, t, inside) {
    const app = this.app, view = this.view, net = app.net;
    const fingerLike = p.kind === 'index';
    if (ev === 'down') {
      if (app.anchoring.calibration) { st.press = { calibration: true }; return; }
      if (!inside) { st.press = null; return; }
      const hit = view.hitTest(x, y);
      st.press = { hit, x, y, t, dragging: false, panLast: st.sheet };
      this._log('down', { hit: hit.kind, x, y, pointer: p.kind });
      if (hit.kind === 'widget' && hit.widget === 'image') {
        const node = view.nodes.get(hit.node);
        view.liveStroke = { node: hit.node, points: [view.imagePoint(node, x, y)], color: this.strokeColor || '#ff3b30', width: this.strokeWidth || 4 };
        st.press.drawing = true; view.dirtyTextures.add(hit.node);
      }
      if (hit.kind === 'port') {
        if (hit.direction === 'out') view.dragWire = { from: [hit.node, hit.port], x0: x, y0: y, x, y, type: hit.type };
        else {
          const node = view.nodes.get(hit.node), port = node.inputs.find((q) => q.name === hit.port);
          if (port && port.connected) {
            const w = [...view.wires.values()].find((q) => q.dst[0] === hit.node && q.dst[1] === hit.port);
            if (w) { net.send({ t: 'disconnect', wire: w.id }); view.dragWire = { from: w.src, x0: w.path[0], y0: w.path[1], x, y, type: w.kind }; }
          } else view.dragWire = { to: [hit.node, hit.port], x0: x, y0: y, x, y, type: hit.type };
        }
        st.press.wire = true;
      }
    } else if (ev === 'move') {
      const pr = st.press; if (!pr || pr.calibration) return;
      const moved = Math.hypot(x - pr.x, y - pr.y);
      if (pr.wire) { view.dragWire.x = x; view.dragWire.y = y; return; }
      const hit = pr.hit;
      if (pr.drawing && view.liveStroke) {
        const node = view.nodes.get(hit.node);
        if (node) { view.liveStroke.points.push(view.imagePoint(node, x, y)); pr.dragging = true; if ((view.liveStroke.points.length & 3) === 0) view.dirtyTextures.add(hit.node); }
        return;
      }
      if (hit.kind === 'widget' && hit.widget === 'slider') {
        const node = view.nodes.get(hit.node); if (!node) return;
        const v = view.sliderValueAt(node, x);
        if (v !== null && v !== pr.lastValue && t - this.lastSliderSend > 0.03) {
          pr.lastValue = v; this.lastSliderSend = t;
          net.send({ t: 'set_value', node: hit.node, port: 'value', value: v, phase: pr.dragging ? 'update' : 'begin' });
          node.values.value = v; view.dirtyTextures.add(node.id);
          pr.dragging = true;
        }
        return;
      }
      if ((hit.kind === 'header' || hit.kind === 'node') && (pr.dragging || moved > 8)) {
        if (!pr.dragging) { pr.dragging = true; net.send({ t: 'move', node: hit.node, phase: 'begin', x: pr.x, y: pr.y }); this._log('drag', { node: hit.node }); }
        const node = view.nodes.get(hit.node);
        if (node) this._localMove(hit.node, x - pr.x, y - pr.y, pr);
        if (t - (pr.lastMoveSend || 0) > 0.03) { pr.lastMoveSend = t; net.send({ t: 'move', node: hit.node, phase: 'update', x, y }); }
        return;
      }
      if (hit.kind === 'empty' && (pr.dragging || moved > 8)) {
        const panning = fingerLike || this.stylusPanActive(p);
        if (panning) {
          // finger on the empty sheet drags the canvas along (sheet units, so no feedback loop)
          const cur = st.sheet;
          if (pr.panLast && cur) view.pan(cur.x - pr.panLast.x, cur.y - pr.panLast.y);
          pr.panLast = cur; pr.dragging = true; pr.panned = true;
          if (!pr.loggedPan) { pr.loggedPan = true; this._log('finger-pan', { pointer: p.kind }); }
          return;
        }
        pr.dragging = true; view.marquee = { x: pr.x, y: pr.y, w: x - pr.x, h: y - pr.y };
      }
    } else if (ev === 'up') {
      const pr = st.press; st.press = null;
      if (!pr || pr.calibration) return;
      if (pr.wire && view.dragWire) {
        const dw = view.dragWire; view.dragWire = null;
        const hit = view.hitTest(x, y);
        if (hit.kind === 'port') {
          if (dw.from && hit.direction === 'in') this._request({ t: 'wire', src: dw.from, dst: [hit.node, hit.port] });
          else if (dw.to && hit.direction === 'out') this._request({ t: 'wire', src: [hit.node, hit.port], dst: dw.to });
        } else if (hit.kind === 'node' || hit.kind === 'header') {
          const node = view.nodes.get(hit.node);
          if (node && dw.from) { const port = node.inputs.find((q) => !q.connected) || node.inputs[0]; if (port) this._request({ t: 'wire', src: dw.from, dst: [hit.node, port.name] }); }
        } else if (hit.kind === 'empty' && dw.from && Math.hypot(x - dw.x0, y - dw.y0) > 20) {
          view.openPalette(x, y); view.palette.from = dw.from;
        }
        return;
      }
      if (pr.drawing) {
        const ls = view.liveStroke; view.liveStroke = null;
        if (ls && ls.points.length) {
          const node = view.nodes.get(ls.node);
          if (node) { node.params.strokes = [...(node.params.strokes || []), { points: ls.points, color: ls.color, width: ls.width }]; view.dirtyTextures.add(ls.node); }
          net.send({ t: 'stroke', node: ls.node, points: ls.points, color: ls.color, width: ls.width });
          this._log('stroke', { node: ls.node, points: ls.points.length });
        }
        return;
      }
      if (pr.dragging) {
        const hit = pr.hit;
        if (hit.kind === 'header' || hit.kind === 'node') net.send({ t: 'move', node: hit.node, phase: 'end', x, y });
        else if (hit.kind === 'widget') net.send({ t: 'set_value', node: hit.node, port: 'value', value: pr.lastValue ?? view.sliderValueAt(view.nodes.get(hit.node), x), phase: 'end' });
        else if (view.marquee) { const m = view.marquee; view.marquee = null; net.send({ t: 'select', rect: [m.x, m.y, m.w, m.h], mode: this.multiSelect ? 'add' : 'replace' }); }
      }
    } else if (ev === 'tap') {
      if (app.anchoring.calibration) { app.anchoring.calibrationTap(p.world); return; }
      if (!inside) return;
      const hit = view.hitTest(x, y);
      this._log('tap', { hit: hit.kind, x, y, pointer: p.kind });
      if (hit.kind === 'palette') { this._paletteTap(hit.item); return; }
      if (view.palette) view.closePalette();
      if (hit.kind === 'port') return;
      if (hit.kind === 'widget' && hit.widget === 'image') return;
      const mode = this.multiSelect || (p.buttons && p.buttons.middle && p.kind === 'stylus') ? 'toggle' : 'replace';
      net.send({ t: 'tap', x, y, mode, delete: !!(p.buttons && p.buttons.rear) });
    } else if (ev === 'hold') {
      const pr = st.press; if (!pr || pr.dragging || pr.wire || pr.calibration) return;
      const hit = pr.hit;
      if (hit.kind === 'empty') { view.openPalette(x, y); pr.dragging = true; pr.panned = true; this._log('palette', { x, y }); }
      else if (hit.kind === 'node' || hit.kind === 'header') { this._contextPalette(x, y, hit); pr.dragging = true; }
      else if (hit.kind === 'wire') { net.send({ t: 'tap', x, y, delete: true }); pr.dragging = true; }
    }
  }
  _localMove(nodeId, dx, dy, pr) {
    const view = this.view;
    if (!pr.start) { pr.start = {}; const ids = view.selection.has(nodeId) ? [...view.selection] : [nodeId]; for (const id of ids) { const n = view.nodes.get(id); if (n) pr.start[id] = { x: n.x, y: n.y, ins: n.inputs.map((q) => [q.x, q.y]), outs: n.outputs.map((q) => [q.x, q.y]), wr: n.widget_rect && [...n.widget_rect] }; } }
    for (const id in pr.start) {
      const n = view.nodes.get(id), s = pr.start[id]; if (!n) continue;
      n.x = s.x + dx; n.y = s.y + dy;
      n.inputs.forEach((q, i) => { q.x = s.ins[i][0] + dx; q.y = s.ins[i][1] + dy; });
      n.outputs.forEach((q, i) => { q.x = s.outs[i][0] + dx; q.y = s.outs[i][1] + dy; });
      if (s.wr) n.widget_rect = [s.wr[0] + dx, s.wr[1] + dy, s.wr[2], s.wr[3]];
      for (const w of view.wires.values()) {
        if (!w.path) continue;
        if (w.src[0] === id) { const o = n.outputs.find((q) => q.name === w.src[1]); if (o) { w.path[0] = o.x; w.path[1] = o.y; w.dirty = true; } }
        if (w.dst[0] === id) { const q = n.inputs.find((r) => r.name === w.dst[1]); if (q) { w.path[2] = q.x; w.path[3] = q.y; w.dirty = true; } }
      }
    }
  }
  _paletteTap(item) {
    const view = this.view, pal = view.palette;
    if (item.close) { view.closePalette(); return; }
    if (item.page) { const from = pal.from; view.openPalette(pal.x, pal.y, item.page); view.palette.from = from; return; }
    if (item.action) { this._action(item.action, pal.target); view.closePalette(); return; }
    const msg = { t: 'add_node', type: item.type, x: pal.x, y: pal.y };
    if (pal.from) msg.from = pal.from;
    this._request(msg); view.closePalette();
  }
  _contextPalette(x, y, hit) {
    const view = this.view, w = 120, h = 34, gap = 6;
    const items = [
      { label: 'delete', action: 'delete', category: 'Vector', rect: [x, y, w, h] },
      { label: 'duplicate', action: 'duplicate', category: 'Solid', rect: [x + w + gap, y, w, h] },
      { label: 'unplug all', action: 'unplug', category: 'Sets', rect: [x, y + h + gap, w, h] },
      { label: 'close', close: true, category: 'Output', rect: [x + w + gap, y + h + gap, w, h] },
    ];
    view.palette = { x, y, items, page: 0, target: hit.node };
    view.dirtyTextures.add('palette');
  }
  _action(action, nodeId) {
    const view = this.view, net = this.app.net;
    const ids = view.selection.has(nodeId) ? [...view.selection] : [nodeId];
    if (action === 'delete') net.send({ t: 'delete', nodes: ids });
    else if (action === 'duplicate') net.send({ t: 'duplicate', nodes: ids });
    else if (action === 'unplug') { for (const w of view.wires.values()) if (ids.includes(w.dst[0]) || ids.includes(w.src[0])) net.send({ t: 'disconnect', wire: w.id }); }
  }
  async _request(msg) {
    const ack = await this.app.net.request(msg);
    if (!ack.ok) this.app.ui.status('Rejected: ' + ack.error);
    return ack;
  }
  stylusPanActive(p) { return p.kind === 'stylus' && p.buttons && p.buttons.middle && !this.multiSelect; }
  _stylusButtons(p, st, c, t) {
    if (p.buttons.rear && !st.rearLatch) { st.rearLatch = true; if (!st.press) this.app.net.send({ t: 'delete', selection: true }); }
    if (!p.buttons.rear) st.rearLatch = false;
    if (p.buttons.front && !st.frontLatch) { st.frontLatch = true; this.multiSelect = !this.multiSelect; this.app.ui.status('Multi-select ' + (this.multiSelect ? 'on' : 'off')); }
    if (!p.buttons.front) st.frontLatch = false;
  }

  // ---- flat hand: pan / two hands: zoom (always the canvas, never a node)
  _palm(p, t) {
    const frame = this.app.anchoring.frame, view = this.view;
    const s = frame.toSheet(p.world);
    const active = p.flat > 0.55 && Math.abs(s.h) < 0.05;
    const c = view.toCanvas(s.x, s.y);
    view.pointerMarks.push({ x: c.x, y: c.y, kind: 'palm', pressed: active, height: Math.max(0, s.h) });
    if (!active) { this.palms.delete(p.id); return; }
    let st = this.palms.get(p.id);
    if (!st) { st = { last: { x: s.x, y: s.y } }; this.palms.set(p.id, st); this._log('palm', { hand: p.hand }); }
    st.cur = { x: s.x, y: s.y };
    if (this.palms.size === 1) view.pan(s.x - st.last.x, s.y - st.last.y);
    else {
      const [a, b] = [...this.palms.values()];
      if (a.cur && b.cur) {
        const d = Math.hypot(a.cur.x - b.cur.x, a.cur.y - b.cur.y);
        const mid = { x: (a.cur.x + b.cur.x) / 2, y: (a.cur.y + b.cur.y) / 2 };
        if (!this.zoomStart) this.zoomStart = { d, mid, scale: view.view.scale };
        else if (this.zoomStart.d > 1e-3) {
          view.zoomAt(this.zoomStart.scale * (d / this.zoomStart.d) / view.view.scale, mid.x, mid.y);
          view.pan(mid.x - this.zoomStart.mid.x, mid.y - this.zoomStart.mid.y); this.zoomStart.mid = mid;
        }
      }
    }
    st.last = { x: s.x, y: s.y };
  }
}
if (typeof module !== 'undefined') module.exports = { Gestures, ContactDetector };
