// SPDX-License-Identifier: LGPL-2.1-or-later
// WebSocket link to FreeCAD with reconnect and request ids.
'use strict';
class Net {
  constructor(url, handlers) {
    this.url = url;
    this.handlers = handlers || {};
    this.ws = null;
    this.rid = 1;
    this.pending = new Map();
    this.connected = false;
    this.retry = 1000;
    this.queue = [];
    this.stats = { sent: 0, received: 0 };
  }
  connect() {
    try { this.ws = new WebSocket(this.url); } catch (e) { this._scheduleReconnect(); return; }
    this.ws.onopen = () => {
      this.connected = true; this.retry = 1000;
      if (this.handlers.open) this.handlers.open();
      for (const m of this.queue) this.ws.send(m);
      this.queue = [];
    };
    this.ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      this.stats.received++;
      if (msg.t === 'ack' && msg.rid != null && this.pending.has(msg.rid)) {
        const { resolve } = this.pending.get(msg.rid); this.pending.delete(msg.rid); resolve(msg);
      }
      const h = this.handlers[msg.t] || this.handlers.any;
      if (h) h(msg);
    };
    this.ws.onclose = () => { this.connected = false; if (this.handlers.close) this.handlers.close(); this._scheduleReconnect(); };
    this.ws.onerror = () => { /* onclose follows */ };
  }
  _scheduleReconnect() {
    if (this.closed) return;
    setTimeout(() => this.connect(), this.retry);
    this.retry = Math.min(this.retry * 2, 8000);
  }
  close() { this.closed = true; if (this.ws) this.ws.close(); }
  send(msg) {
    const text = JSON.stringify(msg);
    this.stats.sent++;
    if (this.connected && this.ws.readyState === 1) this.ws.send(text); else this.queue.push(text);
  }
  /** Send and resolve with the ack. */
  request(msg) {
    return new Promise((resolve) => {
      msg.rid = this.rid++;
      this.pending.set(msg.rid, { resolve });
      this.send(msg);
      setTimeout(() => { if (this.pending.has(msg.rid)) { this.pending.delete(msg.rid); resolve({ t: 'ack', ok: false, error: 'timeout', rid: msg.rid }); } }, 5000);
    });
  }
}
if (typeof module !== 'undefined') module.exports = Net;
