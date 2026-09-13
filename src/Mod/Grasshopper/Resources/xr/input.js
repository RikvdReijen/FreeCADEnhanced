// SPDX-License-Identifier: LGPL-2.1-or-later
// Turns WebXR input sources (MX Ink stylus, tracked hands, controllers) into
// a uniform list of "pointers" per frame.
//
// pointer = { id, kind: 'stylus'|'index'|'palm'|'ray', hand, world, pressed
//             (true/false, or null when only the height above the sheet
//             decides), pressure, buttons:{front,middle,rear}, pinch, flat,
//             rayOrigin, rayDir }
'use strict';
const JOINTS = {
  fingers: {
    index: ['index-finger-metacarpal', 'index-finger-phalanx-proximal', 'index-finger-phalanx-intermediate', 'index-finger-tip'],
    middle: ['middle-finger-metacarpal', 'middle-finger-phalanx-proximal', 'middle-finger-phalanx-intermediate', 'middle-finger-tip'],
    ring: ['ring-finger-metacarpal', 'ring-finger-phalanx-proximal', 'ring-finger-phalanx-intermediate', 'ring-finger-tip'],
    pinky: ['pinky-finger-metacarpal', 'pinky-finger-phalanx-proximal', 'pinky-finger-phalanx-intermediate', 'pinky-finger-tip'],
  },
  needed: ['wrist', 'thumb-tip', 'index-finger-metacarpal', 'index-finger-phalanx-proximal', 'index-finger-phalanx-intermediate', 'index-finger-tip',
    'middle-finger-metacarpal', 'middle-finger-phalanx-proximal', 'middle-finger-phalanx-intermediate', 'middle-finger-tip',
    'ring-finger-metacarpal', 'ring-finger-phalanx-proximal', 'ring-finger-phalanx-intermediate', 'ring-finger-tip',
    'pinky-finger-metacarpal', 'pinky-finger-phalanx-proximal', 'pinky-finger-phalanx-intermediate', 'pinky-finger-tip'],
};

const HandMath = {
  fingerExtension(j, chain) {
    const pts = chain.map((n) => j[n]);
    if (pts.some((p) => !p)) return 0;
    const straight = M3.dist(pts[0], pts[pts.length - 1]);
    let along = 0; for (let i = 0; i + 1 < pts.length; i++) along += M3.dist(pts[i], pts[i + 1]);
    if (along <= 0) return 0;
    return Math.max(0, Math.min(1, (straight / along - 0.6) / 0.4));
  },
  palmNormal(j) {
    const w = j['wrist'], i = j['index-finger-metacarpal'], p = j['pinky-finger-metacarpal'];
    if (!w || !i || !p) return null;
    return M3.norm(M3.cross(M3.sub(i, w), M3.sub(p, w)));
  },
  palmCenter(j) {
    const pts = ['wrist', 'index-finger-metacarpal', 'pinky-finger-metacarpal', 'middle-finger-phalanx-proximal'].map((k) => j[k]).filter(Boolean);
    if (!pts.length) return null;
    let acc = M3.v3(); for (const p of pts) acc = M3.add(acc, p);
    return M3.scale(acc, 1 / pts.length);
  },
  /** Flat palm resting on the sheet: fingers extended, palm parallel, palm near. */
  flatHandScore(j, frame, maxHeight = 0.04) {
    const ext = Object.values(JOINTS.fingers).map((c) => HandMath.fingerExtension(j, c));
    const extension = ext.reduce((a, b) => a + b, 0) / ext.length;
    const n = HandMath.palmNormal(j); if (!n || !frame) return 0;
    const parallel = Math.abs(M3.dot(n, frame.normal));
    const palm = j['middle-finger-metacarpal'] || j['wrist'];
    const h = Math.abs(frame.toSheet(palm).h);
    const closeness = Math.max(0, 1 - h / maxHeight);
    return extension * parallel * closeness;
  },
  pinchStrength(j, pinchMm = 15, openMm = 45) {
    const a = j['index-finger-tip'], b = j['thumb-tip'];
    if (!a || !b) return 0;
    const d = M3.dist(a, b) * 1000;
    return Math.max(0, Math.min(1, (openMm - d) / (openMm - pinchMm)));
  },
};

class XRInput {
  constructor(app) {
    this.app = app;
    // MX Ink / generic stylus button roles -> gamepad button indices (adjustable in the UI)
    this.stylusMap = { tip: 0, front: 1, middle: 2, rear: 3 };
    this.tipOffset = 0;            // metres along the stylus -z from the grip pose to the tip
    this.tipMode = 'either';       // 'button' | 'height' | 'either'
    this.treatControllerAsStylus = false;
    this.debug = { stylus: null, hands: 0, sources: 0 };
    this.lastJoints = {};
  }
  isStylus(source) {
    const profiles = (source.profiles || []).join(' ').toLowerCase();
    if (/logitech|mx.?ink|stylus|pen/.test(profiles)) return true;
    return this.treatControllerAsStylus && source.gamepad && source.handedness === 'right' && !source.hand;
  }
  collect(frame, refSpace, session) {
    const pointers = [];
    const frameSheet = this.app.anchoring.frame;
    const sources = session ? session.inputSources : [];
    this.debug.sources = sources.length; this.debug.hands = 0; this.debug.stylus = null;
    for (const src of sources) {
      if (src.hand) {
        const joints = {};
        for (const name of JOINTS.needed) {
          const space = src.hand.get(name); if (!space) continue;
          const pose = frame.getJointPose(space, refSpace);
          if (pose) joints[name] = { x: pose.transform.position.x, y: pose.transform.position.y, z: pose.transform.position.z };
        }
        if (!joints['index-finger-tip']) continue;
        this.debug.hands++;
        this.lastJoints[src.handedness] = joints;
        const pinch = HandMath.pinchStrength(joints);
        pointers.push({ id: 'hand-' + src.handedness + '-index', kind: 'index', hand: src.handedness, world: joints['index-finger-tip'], pressed: null, pressure: 0, pinch, joints });
        const palm = HandMath.palmCenter(joints);
        if (palm) pointers.push({ id: 'hand-' + src.handedness + '-palm', kind: 'palm', hand: src.handedness, world: palm, pressed: null, flat: HandMath.flatHandScore(joints, frameSheet), pinch, joints });
        if (src.targetRaySpace) {
          const rp = frame.getPose(src.targetRaySpace, refSpace);
          if (rp) pointers.push({ id: 'hand-' + src.handedness + '-ray', kind: 'ray', hand: src.handedness, rayOrigin: rp.transform.position, rayDir: M3.quatRotate(rp.transform.orientation, M3.v3(0, 0, -1)), pressed: pinch > 0.7, pinch });
        }
        continue;
      }
      const gp = src.gamepad;
      const grip = src.gripSpace ? frame.getPose(src.gripSpace, refSpace) : null;
      const ray = src.targetRaySpace ? frame.getPose(src.targetRaySpace, refSpace) : null;
      if (this.isStylus(src) && (grip || ray)) {
        const pose = grip || ray;
        const pos = pose.transform.position, ori = pose.transform.orientation;
        const fwd = M3.quatRotate(ori, M3.v3(0, 0, -1));
        const tip = M3.add(M3.v3(pos.x, pos.y, pos.z), M3.scale(fwd, this.tipOffset));
        const b = (i) => (gp && gp.buttons[i]) ? gp.buttons[i] : { pressed: false, value: 0 };
        const tipBtn = b(this.stylusMap.tip);
        const buttons = { front: b(this.stylusMap.front).pressed, middle: b(this.stylusMap.middle).pressed, rear: b(this.stylusMap.rear).pressed };
        let pressed = null;
        if (this.tipMode === 'button') pressed = tipBtn.pressed || tipBtn.value > 0.05;
        else if (this.tipMode === 'either') pressed = (tipBtn.pressed || tipBtn.value > 0.05) ? true : null;
        this.debug.stylus = { profiles: src.profiles, buttons: gp ? gp.buttons.map((x) => +x.value.toFixed(2)) : [], axes: gp ? gp.axes.map((x) => +x.toFixed(2)) : [] };
        pointers.push({ id: 'stylus', kind: 'stylus', hand: src.handedness, world: tip, pressed, pressure: tipBtn.value, buttons, forward: fwd });
        if (ray) pointers.push({ id: 'stylus-ray', kind: 'ray', hand: src.handedness, rayOrigin: ray.transform.position, rayDir: M3.quatRotate(ray.transform.orientation, M3.v3(0, 0, -1)), pressed: b(this.stylusMap.front).pressed, buttons });
        continue;
      }
      if (ray) {
        const b0 = gp && gp.buttons[0] ? gp.buttons[0] : { pressed: false };
        const b1 = gp && gp.buttons[1] ? gp.buttons[1] : { pressed: false };
        pointers.push({ id: 'controller-' + src.handedness, kind: 'ray', hand: src.handedness, rayOrigin: ray.transform.position, rayDir: M3.quatRotate(ray.transform.orientation, M3.v3(0, 0, -1)), pressed: b0.pressed, buttons: { front: b0.pressed, middle: b1.pressed, rear: false }, axes: gp ? gp.axes : [] });
      }
    }
    return pointers;
  }
}
if (typeof module !== 'undefined') module.exports = { XRInput, HandMath, JOINTS };
