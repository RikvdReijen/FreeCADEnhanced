// SPDX-License-Identifier: LGPL-2.1-or-later
// Builds and draws the node sheet from graph snapshots and does hit-testing
// with exactly the layout numbers the server uses (delivered in `hello`).
'use strict';
class CanvasView {
  constructor(renderer) {
    this.r = renderer;
    this.layout = { node_width: 168, header: 26, port_row: 22, port_radius: 7, padding: 8, widget_height: { slider: 30, text: 30, panel: 70 } };
    this.nodes = new Map();      // id -> snapshot (+ gl texture key)
    this.wires = new Map();      // id -> {snapshot, lineObj}
    this.selection = new Set();
    this.previews = [];          // [{node, mesh, lines, color, bbox}]
    this.nodeTypes = [];
    this.hover = null;           // hit result under the primary pointer
    this.dragWire = null;        // {from:[node,port], x, y}
    this.palette = null;         // {x, y, items:[{type,label,rect}], page}
    this.marquee = null;         // {x, y, w, h}
    this.view = { ox: 0, oy: 0, scale: 1 };  // sheet <- canvas transform
    this.sheet = { w: 1400, h: 900 };        // sheet size in canvas units
    this.texScale = 2;           // texture pixels per canvas unit
    this.dirtyTextures = new Set();
    this.gridObj = null;
    this.pointerMarks = [];      // [{x,y,kind,pressed}] for feedback
    this.images = new Map();     // url -> HTMLImageElement (or 'loading')
    this.liveStroke = null;      // {node, points:[[px,py]...]} being drawn
    // preview stage: position in sheet units relative to the frame (x, y), height above it (m), footprint (m).
    // It moves with the panel and can be grabbed (pinch / grip) and carried anywhere.
    this.stage = { x: 1400 + 420, y: 450, h: 0.0, size: 0.3 };
    this.handle = { height: 44, gap: 12 };  // grab bar above the sheet, in sheet units
    this.hotHandle = false; this.hotStage = false;
    this.grouped = {};
    this.theme = {
      sheet: [1, 1, 1, 0.55], grid: [0, 0, 0, 0.12], wire: [0.15, 0.15, 0.15, 1], wireHot: [1, 0.5, 0.1, 1],
      kinds: { number: '#7fb3ff', int: '#7fb3ff', bool: '#c9b3ff', text: '#ffd479', point: '#ff9d9d', vector: '#ff9d9d', shape: '#8fe0a2', list: '#dddddd', any: '#bbbbbb' },
      categories: { Params: '#3d5a80', Math: '#4a6fa5', Sets: '#6b6b6b', Vector: '#a44a3f', Curve: '#2a9d8f', Solid: '#3a7d44', Transform: '#8a6d3b', Boolean: '#6d4c8a', Analyse: '#5a5a8a', Output: '#b5651d' },
    };
  }

  // ------------------------------------------------------------ transforms
  /** canvas -> sheet coordinates (canvas units on the physical sheet) */
  toSheet(cx, cy) { return { x: (cx - this.view.ox) * this.view.scale, y: (cy - this.view.oy) * this.view.scale }; }
  /** sheet -> canvas coordinates */
  toCanvas(sx, sy) { return { x: sx / this.view.scale + this.view.ox, y: sy / this.view.scale + this.view.oy }; }
  pan(dx, dy) { this.view.ox -= dx / this.view.scale; this.view.oy -= dy / this.view.scale; }
  zoomAt(factor, sx, sy) {
    const before = this.toCanvas(sx, sy);
    this.view.scale = Math.max(0.2, Math.min(5, this.view.scale * factor));
    const after = this.toCanvas(sx, sy);
    this.view.ox += before.x - after.x; this.view.oy += before.y - after.y;
  }
  fitAll() {
    if (!this.nodes.size) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes.values()) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + n.w); y1 = Math.max(y1, n.y + n.h); }
    const pad = 40, w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad;
    this.view.scale = Math.max(0.2, Math.min(1.5, Math.min(this.sheet.w / w, this.sheet.h / h)));
    this.view.ox = x0 - pad; this.view.oy = y0 - pad;
  }

  // ---------------------------------------------------------------- state
  setHello(hello) {
    if (hello.layout) this.layout = hello.layout;
    this.nodeTypes = hello.node_types || [];
  }
  setGraph(msg) {
    const keep = new Set();
    for (const n of msg.nodes) { this._setNode(n); keep.add(n.id); }
    for (const id of [...this.nodes.keys()]) if (!keep.has(id)) this._dropNode(id);
    const keepW = new Set();
    for (const w of msg.wires) { this._setWire(w); keepW.add(w.id); }
    for (const id of [...this.wires.keys()]) if (!keepW.has(id)) this._dropWire(id);
    this.setSelection(msg.selection || []);
  }
  applyPatch(msg) {
    if (msg.op === 'node') {
      this._setNode(msg.node);
      for (const w of msg.wires || []) { const cur = this.wires.get(w.id); if (cur) { cur.path = w.path; this._rebuildWire(cur); } }
    } else if (msg.op === 'values') {
      for (const n of msg.nodes) this._setNode(n);
    }
  }
  setSelection(ids) {
    this.selection = new Set(ids);
    for (const n of this.nodes.values()) { const sel = this.selection.has(n.id); if (sel !== n.selected) { n.selected = sel; this.dirtyTextures.add(n.id); } }
  }
  _setNode(snap) {
    const prev = this.nodes.get(snap.id);
    snap.selected = this.selection.has(snap.id) || snap.selected;
    this.nodes.set(snap.id, snap);
    if (!prev || JSON.stringify(prev) !== JSON.stringify(snap)) this.dirtyTextures.add(snap.id);
  }
  _dropNode(id) { this.nodes.delete(id); this.r.dropTexture('node:' + id); }
  _setWire(snap) {
    let cur = this.wires.get(snap.id);
    if (!cur) { cur = { id: snap.id, lineObj: null }; this.wires.set(snap.id, cur); }
    Object.assign(cur, snap);
    this._rebuildWire(cur);
  }
  _dropWire(id) { const w = this.wires.get(id); if (w) { this.r.free(w.lineObj); this.wires.delete(id); } }
  _rebuildWire(w) { this.r.free(w.lineObj); w.lineObj = null; w.dirty = true; }
  setPreview(msg) {
    for (const p of this.previews) { this.r.free(p.mesh); this.r.free(p.lines); }
    this.previews = [];
    let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
    for (const item of msg.items) {
      const mesh = item.faces.length ? this.r.mesh(item.vertices, item.faces) : null;
      const flat = [];
      for (const line of item.lines) flat.push(...line);
      const lines = flat.length >= 6 ? this.r.lines(flat) : null;
      const b = item.bbox;
      if (b && b[3] >= b[0]) { x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); z0 = Math.min(z0, b[2]); x1 = Math.max(x1, b[3]); y1 = Math.max(y1, b[4]); z1 = Math.max(z1, b[5]); }
      this.previews.push({ node: item.node, mesh, lines, color: M3.hexToRgb(item.color), bbox: b });
    }
    this.previewBounds = isFinite(x0) ? [x0, y0, z0, x1, y1, z1] : null;
  }

  // ------------------------------------------------------------- hit test
  /** Hit test in canvas coordinates; mirrors layout.hit_test on the server. */
  hitTest(cx, cy) {
    const L = this.layout, r2 = Math.pow(L.port_radius * 1.6, 2);
    const nodes = [...this.nodes.values()];
    if (this.palette) {
      for (const item of this.palette.items) {
        const [x, y, w, h] = item.rect;
        if (cx >= x && cx <= x + w && cy >= y && cy <= y + h) return { kind: 'palette', item };
      }
    }
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      for (const p of n.inputs) if ((cx - p.x) ** 2 + (cy - p.y) ** 2 <= r2) return { kind: 'port', node: n.id, port: p.name, direction: 'in', type: p.kind };
      for (const p of n.outputs) if ((cx - p.x) ** 2 + (cy - p.y) ** 2 <= r2) return { kind: 'port', node: n.id, port: p.name, direction: 'out', type: p.kind };
    }
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (cx >= n.x && cx <= n.x + n.w && cy >= n.y && cy <= n.y + n.h) {
        const wr = n.widget_rect;
        if (wr && cx >= wr[0] && cx <= wr[0] + wr[2] && cy >= wr[1] && cy <= wr[1] + wr[3]) return { kind: 'widget', node: n.id, widget: n.widget, rect: wr };
        if (cy <= n.y + L.header) return { kind: 'header', node: n.id };
        return { kind: 'node', node: n.id };
      }
    }
    const wire = this._wireHit(cx, cy);
    if (wire) return { kind: 'wire', wire };
    return { kind: 'empty' };
  }
  _wireHit(cx, cy) {
    let best = null, bestD = 36;
    for (const w of this.wires.values()) {
      if (!w.path) continue;
      const pts = CanvasView.bezier(...w.path);
      for (let i = 0; i + 1 < pts.length; i++) {
        const d = CanvasView.segDist2(cx, cy, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
        if (d < bestD) { bestD = d; best = w.id; }
      }
    }
    return best;
  }
  static bezier(x1, y1, x2, y2, segments = 16) {
    const dx = Math.max(Math.abs(x2 - x1) * 0.5, 30), c1x = x1 + dx, c2x = x2 - dx, pts = [];
    for (let i = 0; i <= segments; i++) {
      const t = i / segments, mt = 1 - t;
      pts.push([mt ** 3 * x1 + 3 * mt * mt * t * c1x + 3 * mt * t * t * c2x + t ** 3 * x2, mt ** 3 * y1 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t ** 3 * y2]);
    }
    return pts;
  }
  static segDist2(px, py, ax, ay, bx, by) {
    const vx = bx - ax, vy = by - ay, wx = px - ax, wy = py - ay, seg = vx * vx + vy * vy;
    const t = seg === 0 ? 0 : Math.max(0, Math.min(1, (wx * vx + wy * vy) / seg));
    return (px - ax - t * vx) ** 2 + (py - ay - t * vy) ** 2;
  }
  sliderValueAt(node, cx) {
    const wr = node.widget_rect; if (!wr) return null;
    const lo = +(node.params.min ?? 0), hi = +(node.params.max ?? 10), step = +(node.params.step ?? 0);
    const t = wr[2] <= 0 ? 0 : Math.max(0, Math.min(1, (cx - wr[0]) / wr[2]));
    let v = lo + t * (hi - lo);
    if (step > 0) v = lo + Math.round((v - lo) / step) * step;
    return Math.max(lo, Math.min(hi, v));
  }

  // -------------------------------------------------------------- palette
  openPalette(cx, cy, page = 0) {
    const favourites = ['param.slider', 'param.panel', 'vec.point', 'vec.vector', 'curve.circle', 'curve.rectangle', 'solid.box', 'solid.cylinder', 'solid.extrude', 'xform.move', 'xform.polar_array', 'bool.union', 'bool.difference', 'math.series', 'math.multiply', 'out.preview'];
    const types = this.nodeTypes;
    let list;
    if (page === 0) list = favourites.map((id) => types.find((t) => t.type === id)).filter(Boolean);
    else {
      const cats = [...new Set(types.map((t) => t.category))];
      const cat = cats[(page - 1) % cats.length];
      list = types.filter((t) => t.category === cat);
    }
    const cols = 4, w = 120, h = 34, gap = 6, items = [];
    list.slice(0, 16).forEach((t, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      items.push({ type: t.type, label: t.label, category: t.category, rect: [cx + col * (w + gap), cy + row * (h + gap), w, h] });
    });
    const rows = Math.ceil(items.length / cols);
    items.push({ type: null, label: page === 0 ? 'more...' : 'next: ' + this._nextPaletteTitle(page + 1), category: 'Output', rect: [cx, cy + rows * (h + gap), w * 2 + gap, h], page: page + 1 });
    items.push({ type: null, label: 'close', category: 'Sets', rect: [cx + 2 * (w + gap), cy + rows * (h + gap), w * 2 + gap, h], close: true });
    this.palette = { x: cx, y: cy, items, page };
    this.dirtyTextures.add('palette');
  }
  _nextPaletteTitle(page) {
    const cats = [...new Set(this.nodeTypes.map((t) => t.category))];
    return cats.length ? cats[(page - 1) % cats.length] : '';
  }
  closePalette() { this.palette = null; this.r.dropTexture('palette'); }

  // ------------------------------------------------------------ textures
  loadImage(url) {
    const cached = this.images.get(url);
    if (cached && cached !== 'loading') return Promise.resolve(cached);
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => { this.images.set(url, img); for (const n of this.nodes.values()) if (n.params && n.params.url === url) this.dirtyTextures.add(n.id); resolve(img); };
      img.onerror = () => { this.images.delete(url); resolve(null); };
      this.images.set(url, 'loading');
      img.src = url;
    });
  }
  static drawStrokes(g, strokes, sx, sy) {
    g.lineCap = 'round'; g.lineJoin = 'round';
    for (const st of strokes) {
      const pts = st.points || []; if (!pts.length) continue;
      g.strokeStyle = st.color || '#ff3b30'; g.lineWidth = (st.width || 3) * Math.max(sx, 0.2);
      g.beginPath(); g.moveTo(pts[0][0] * sx, pts[0][1] * sy);
      if (pts.length === 1) g.lineTo(pts[0][0] * sx + 0.1, pts[0][1] * sy);
      for (let i = 1; i < pts.length; i++) g.lineTo(pts[i][0] * sx, pts[i][1] * sy);
      g.stroke();
    }
  }
  /** widget rect (canvas units) <-> picture pixels */
  imagePoint(n, cx, cy) {
    const wr = n.widget_rect, pw = n.params.px_w || 1, ph = n.params.px_h || 1;
    return [(cx - wr[0]) / wr[2] * pw, (cy - wr[1]) / wr[3] * ph];
  }
  _cardCanvas(n) {
    const s = this.texScale, c = document.createElement('canvas');
    c.width = Math.ceil(n.w * s); c.height = Math.ceil(n.h * s);
    const g = c.getContext('2d');
    g.scale(s, s);
    const L = this.layout, cat = this.theme.categories[n.category] || '#555';
    // body
    g.fillStyle = n.error ? '#ffd6d6' : '#f7f7f7';
    CanvasView.roundRect(g, 0, 0, n.w, n.h, 6); g.fill();
    g.fillStyle = cat; CanvasView.roundRect(g, 0, 0, n.w, L.header, 6, true); g.fill();
    if (n.selected) { g.lineWidth = 3; g.strokeStyle = '#ff9900'; CanvasView.roundRect(g, 1.5, 1.5, n.w - 3, n.h - 3, 6); g.stroke(); }
    else { g.lineWidth = 1; g.strokeStyle = '#333'; CanvasView.roundRect(g, 0.5, 0.5, n.w - 1, n.h - 1, 6); g.stroke(); }
    g.fillStyle = '#fff'; g.font = 'bold 13px sans-serif'; g.textBaseline = 'middle';
    g.fillText(CanvasView.clip(g, n.label, n.w - 16), 8, L.header / 2);
    // ports
    g.font = '11px sans-serif';
    n.inputs.forEach((p) => {
      const y = p.y - n.y;
      g.fillStyle = this.theme.kinds[p.kind] || '#bbb'; g.beginPath(); g.arc(0, y, L.port_radius, 0, Math.PI * 2); g.fill();
      g.strokeStyle = '#222'; g.lineWidth = 1; g.stroke();
      g.fillStyle = '#222'; g.textAlign = 'left';
      const txt = p.connected ? p.name : `${p.name}: ${p.value}`;
      g.fillText(CanvasView.clip(g, txt, n.w / 2 - 14), L.port_radius + 5, y);
    });
    n.outputs.forEach((p) => {
      const y = p.y - n.y;
      g.fillStyle = this.theme.kinds[p.kind] || '#bbb'; g.beginPath(); g.arc(n.w, y, L.port_radius, 0, Math.PI * 2); g.fill();
      g.strokeStyle = '#222'; g.lineWidth = 1; g.stroke();
      g.fillStyle = '#222'; g.textAlign = 'right';
      g.fillText(CanvasView.clip(g, `${p.name} = ${p.value}`, n.w / 2 - 6), n.w - L.port_radius - 5, y);
    });
    g.textAlign = 'left';
    // widget
    const wr = n.widget_rect;
    if (wr && n.widget === 'slider') {
      const x = wr[0] - n.x, y = wr[1] - n.y, w = wr[2], h = wr[3];
      const lo = +(n.params.min ?? 0), hi = +(n.params.max ?? 10), v = +(n.values.value ?? lo);
      const t = hi > lo ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) : 0;
      g.fillStyle = '#d0d0d0'; CanvasView.roundRect(g, x, y + h / 2 - 4, w, 8, 4); g.fill();
      g.fillStyle = '#4a90e2'; CanvasView.roundRect(g, x, y + h / 2 - 4, Math.max(8, w * t), 8, 4); g.fill();
      g.fillStyle = '#2c6fbf'; g.beginPath(); g.arc(x + w * t, y + h / 2, 9, 0, Math.PI * 2); g.fill();
      g.fillStyle = '#222'; g.font = 'bold 11px sans-serif'; g.textAlign = 'center';
      g.fillText((+v).toFixed(2).replace(/\.?0+$/, ''), x + w / 2, y + h / 2 + 14);
      g.textAlign = 'left';
    } else if (wr && n.widget === 'panel') {
      const x = wr[0] - n.x, y = wr[1] - n.y, w = wr[2], h = wr[3];
      g.fillStyle = '#fffbe6'; g.fillRect(x, y, w, h); g.strokeStyle = '#999'; g.strokeRect(x, y, w, h);
      g.fillStyle = '#222'; g.font = '10px monospace';
      (n.text || '').split('\n').slice(0, 5).forEach((line, i) => g.fillText(CanvasView.clip(g, line, w - 8), x + 4, y + 8 + i * 12));
    } else if (wr && n.widget === 'image') {
      const x = wr[0] - n.x, y = wr[1] - n.y, w = wr[2], h = wr[3];
      g.fillStyle = '#e8e8e8'; g.fillRect(x, y, w, h);
      const img = this.images.get(n.params.url);
      if (img && img !== 'loading') g.drawImage(img, x, y, w, h);
      else { if (!img) this.loadImage(n.params.url); g.fillStyle = '#888'; g.font = '11px sans-serif'; g.fillText('loading picture…', x + 6, y + h / 2); }
      const pw = n.params.px_w || 1, ph = n.params.px_h || 1;
      g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip(); g.translate(x, y);
      CanvasView.drawStrokes(g, n.params.strokes || [], w / pw, h / ph);
      if (this.liveStroke && this.liveStroke.node === n.id) CanvasView.drawStrokes(g, [{ points: this.liveStroke.points, color: this.liveStroke.color, width: this.liveStroke.width }], w / pw, h / ph);
      g.restore();
      g.strokeStyle = '#666'; g.lineWidth = 1; g.strokeRect(x, y, w, h);
      g.fillStyle = '#333'; g.font = '9px sans-serif'; g.fillText((n.params.mode || '') + ' · ' + (n.params.strokes || []).length + ' strokes · draw here, drag the header to move', x + 4, y + h - 5);
    } else if (wr && n.widget === 'text') {
      const x = wr[0] - n.x, y = wr[1] - n.y, w = wr[2], h = wr[3];
      g.fillStyle = '#fff'; g.fillRect(x, y, w, h); g.strokeStyle = '#999'; g.strokeRect(x, y, w, h);
      g.fillStyle = '#222'; g.font = '12px monospace';
      const txt = n.params.expression ?? n.values.value ?? '';
      g.fillText(CanvasView.clip(g, String(txt), w - 8), x + 4, y + h / 2);
    }
    if (n.error) { g.fillStyle = '#b00'; g.font = '10px sans-serif'; g.fillText(CanvasView.clip(g, n.error, n.w - 10), 5, n.h - 6); }
    return c;
  }
  _paletteCanvas() {
    const p = this.palette, s = this.texScale;
    let x1 = 0, y1 = 0;
    for (const it of p.items) { x1 = Math.max(x1, it.rect[0] + it.rect[2] - p.x); y1 = Math.max(y1, it.rect[1] + it.rect[3] - p.y); }
    p.w = x1; p.h = y1;
    const c = document.createElement('canvas'); c.width = Math.ceil(x1 * s); c.height = Math.ceil(y1 * s);
    const g = c.getContext('2d'); g.scale(s, s);
    g.font = 'bold 12px sans-serif'; g.textBaseline = 'middle'; g.textAlign = 'center';
    for (const it of p.items) {
      const [x, y, w, h] = it.rect;
      g.fillStyle = this.theme.categories[it.category] || '#555';
      CanvasView.roundRect(g, x - p.x, y - p.y, w, h, 5); g.fill();
      g.fillStyle = '#fff'; g.fillText(CanvasView.clip(g, it.label, w - 8), x - p.x + w / 2, y - p.y + h / 2);
    }
    return c;
  }
  static roundRect(g, x, y, w, h, r, topOnly) {
    g.beginPath(); g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.quadraticCurveTo(x + w, y, x + w, y + r);
    if (topOnly) { g.lineTo(x + w, y + h); g.lineTo(x, y + h); } else { g.lineTo(x + w, y + h - r); g.quadraticCurveTo(x + w, y + h, x + w - r, y + h); g.lineTo(x + r, y + h); g.quadraticCurveTo(x, y + h, x, y + h - r); }
    g.lineTo(x, y + r); g.quadraticCurveTo(x, y, x + r, y); g.closePath();
  }
  static clip(g, text, maxWidth) {
    text = String(text ?? '');
    if (g.measureText(text).width <= maxWidth) return text;
    while (text.length > 1 && g.measureText(text + '…').width > maxWidth) text = text.slice(0, -1);
    return text + '…';
  }
  refreshTextures() {
    for (const id of this.dirtyTextures) {
      if (id === 'palette') { if (this.palette) this.r.textureFromCanvas('palette', this._paletteCanvas()); }
      else { const n = this.nodes.get(id); if (n) this.r.textureFromCanvas('node:' + id, this._cardCanvas(n)); }
    }
    this.dirtyTextures.clear();
  }

  // ---------------------------------------------------------------- draw
  /**
   * Draw the sheet. `frame` maps sheet coordinates to world:
   * world = origin + x*sx*unit + y*sy*unit + normal*h.
   */
  draw(viewProj, frame, opts = {}) {
    this.refreshTextures();
    const u = frame.unitM;
    // sheet units on all three axes, so z offsets below are in sheet units too
    const sheetModel = M3.fromAxes(frame.origin, M3.scale(frame.x, u), M3.scale(frame.y, u), M3.scale(frame.normal, u));
    // sheet background + grid
    this.r.drawQuad(viewProj, M3.multiply(sheetModel, M3.scaling(this.sheet.w, this.sheet.h, 1)), this.theme.sheet, null);
    if (!this.gridObj) this.gridObj = this._grid();
    this.r.drawLines(viewProj, M3.multiply(sheetModel, M3.translation(0, 0, 0.0005 / u)), this.gridObj, this.theme.grid);
    const lift = 0.001 / u; // 1 mm above the sheet in sheet units
    // wires
    for (const w of this.wires.values()) {
      if (!w.path) continue;
      if (w.dirty || !w.lineObj) {
        const pts = CanvasView.bezier(...w.path), flat = [];
        for (const [x, y] of pts) { const s = this.toSheet(x, y); flat.push(s.x, s.y, lift); }
        this.r.free(w.lineObj); w.lineObj = this.r.lines(flat); w.dirty = false; w.viewKey = this._viewKey();
      } else if (w.viewKey !== this._viewKey()) { w.dirty = true; }
      const hot = this.hover && this.hover.kind === 'wire' && this.hover.wire === w.id;
      this.r.drawLines(viewProj, sheetModel, w.lineObj, hot ? this.theme.wireHot : this.theme.wire, 2);
    }
    // dragging wire
    if (this.dragWire) {
      const a = this.toSheet(this.dragWire.x0, this.dragWire.y0), b = this.toSheet(this.dragWire.x, this.dragWire.y);
      const tmp = this.r.lines([a.x, a.y, lift * 2, b.x, b.y, lift * 2]);
      this.r.drawLines(viewProj, sheetModel, tmp, this.theme.wireHot, 2); this.r.free(tmp);
    }
    // node cards
    for (const n of this.nodes.values()) {
      const s = this.toSheet(n.x, n.y), sc = this.view.scale;
      const model = M3.multiply(sheetModel, M3.multiply(M3.translation(s.x, s.y + n.h * sc, lift * 2), M3.scaling(n.w * sc, -n.h * sc, 1)));
      this.r.drawQuad(viewProj, model, [1, 1, 1, 1], this.r.textures.get('node:' + n.id));
    }
    // palette
    if (this.palette && this.palette.w) {
      const p = this.palette, s = this.toSheet(p.x, p.y), sc = this.view.scale;
      const model = M3.multiply(sheetModel, M3.multiply(M3.translation(s.x, s.y + p.h * sc, lift * 3), M3.scaling(p.w * sc, -p.h * sc, 1)));
      this.r.drawQuad(viewProj, model, [1, 1, 1, 1], this.r.textures.get('palette'));
    }
    // marquee
    if (this.marquee) {
      const m = this.marquee, a = this.toSheet(Math.min(m.x, m.x + m.w), Math.min(m.y, m.y + m.h));
      const model = M3.multiply(sheetModel, M3.multiply(M3.translation(a.x, a.y, lift * 3), M3.scaling(Math.abs(m.w) * this.view.scale, Math.abs(m.h) * this.view.scale, 1)));
      this.r.drawQuad(viewProj, model, [0.2, 0.5, 1, 0.25], null);
    }
    // pointer marks (tip contact feedback)
    for (const pm of this.pointerMarks) {
      const s = this.toSheet(pm.x, pm.y), size = pm.pressed ? 10 : 6;
      const model = M3.multiply(sheetModel, M3.multiply(M3.translation(s.x - size / 2, s.y - size / 2, lift * 4 + (pm.height || 0) / u), M3.scaling(size, size, 1)));
      const col = pm.kind === 'palm' ? [0.2, 0.8, 0.3, 0.6] : (pm.pressed ? [1, 0.3, 0.1, 0.9] : [0.1, 0.4, 1, 0.7]);
      this.r.drawQuad(viewProj, model, col, null, { noDepth: true });
    }
    // grab bar and preview stage
    this._drawHandle(viewProj, sheetModel);
    if (!opts.noPreview) { this._drawStageBase(viewProj, frame); if (this.previews.length) this._drawPreviews(viewProj, frame); }
  }
  _viewKey() { return `${this.view.ox.toFixed(2)},${this.view.oy.toFixed(2)},${this.view.scale.toFixed(4)}`; }
  _grid() {
    const pts = [], step = 50;
    for (let x = 0; x <= this.sheet.w; x += step) pts.push(x, 0, 0, x, this.sheet.h, 0);
    for (let y = 0; y <= this.sheet.h; y += step) pts.push(0, y, 0, this.sheet.w, y, 0);
    return this.r.lines(pts);
  }
  /** World position of the stage base centre. */
  stageCenter(frame) { return frame.toWorld(this.stage.x, this.stage.y, this.stage.h); }
  /** Model "up" for the stage: world up when the panel is vertical, the sheet normal when it lies flat. */
  stageAxes(frame) {
    const flat = Math.abs(frame.normal.y) > 0.7;
    const up = flat ? frame.normal : M3.v3(0, 1, 0);
    const x = M3.norm(M3.sub(frame.x, M3.scale(up, M3.dot(frame.x, up))));
    const y = M3.cross(up, x);
    return { x, y, z: up };
  }
  inHandle(s) { return s.x >= 0 && s.x <= this.sheet.w && s.y <= -this.handle.gap && s.y >= -(this.handle.gap + this.handle.height); }
  stageModel(frame) {
    // model units are millimetres; centre the preview bbox on the stage and scale to fit
    const b = this.previewBounds, ax = this.stageAxes(frame);
    let scale = 0.001, cx = 0, cy = 0, cz = 0;
    if (b) {
      const ext = Math.max(b[3] - b[0], b[4] - b[1], b[5] - b[2], 1e-6) / 1000;
      scale = Math.min(0.001, this.stage.size / ext);
      cx = (b[0] + b[3]) / 2; cy = (b[1] + b[4]) / 2; cz = b[2];
    }
    const basis = M3.fromAxes(this.stageCenter(frame), M3.scale(ax.x, scale), M3.scale(ax.y, scale), M3.scale(ax.z, scale));
    return M3.multiply(basis, M3.translation(-cx, -cy, -cz));
  }
  _drawStageBase(viewProj, frame) {
    // translucent pedestal so the stage is visible (and grabbable) even without geometry
    const ax = this.stageAxes(frame), c = this.stageCenter(frame), sz = this.stage.size;
    const origin = M3.sub(M3.sub(c, M3.scale(ax.x, sz / 2)), M3.scale(ax.y, sz / 2));
    const model = M3.multiply(M3.fromAxes(origin, ax.x, ax.y, ax.z), M3.scaling(sz, sz, 1));
    this.r.drawQuad(viewProj, model, this.hotStage ? [1, 0.6, 0.2, 0.45] : [0.5, 0.6, 0.8, 0.25], null);
  }
  _drawHandle(viewProj, sheetModel) {
    const h = this.handle;
    const model = M3.multiply(sheetModel, M3.multiply(M3.translation(0, -(h.gap + h.height), 0), M3.scaling(this.sheet.w, h.height, 1)));
    this.r.drawQuad(viewProj, model, this.hotHandle ? [1, 0.6, 0.2, 0.9] : [0.25, 0.28, 0.35, 0.8], null);
  }
  _drawPreviews(viewProj, frame) {
    const model = this.stageModel(frame);
    for (const p of this.previews) {
      if (p.mesh) this.r.drawMesh(viewProj, model, p.mesh, [p.color[0], p.color[1], p.color[2], 0.9], [0.3, 0.6, 1]);
      if (p.lines) this.r.drawLines(viewProj, model, p.lines, [0.1, 0.1, 0.1, 0.9], 1);
    }
  }
}
if (typeof module !== 'undefined') module.exports = CanvasView;
