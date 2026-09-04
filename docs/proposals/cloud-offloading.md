# Proposal: offload heavy compute to a worker when the local machine is too weak

*Written as a GitHub issue; Issues are disabled on this repository, so it lives
here until they are enabled (Settings → General → Features → Issues).*

## Idea

When the XR workbench detects that the local machine cannot keep up (frame time
over budget, no GPU, low RAM, a Quest running standalone), offload specific
kinds of compute to a remote worker — a cloud VM or a stronger machine on the
LAN — and stream the results back. The headset and the desktop keep doing what
they must do locally (pose tracking, compositing, input); the expensive,
batchable, latency-tolerant work moves.

## What could be offloaded, and what should not

Good candidates (batch jobs, seconds of latency acceptable, results are data):

| Work | Where it lives today | Why it offloads well |
|---|---|---|
| Tessellation and scene export (`xrsync.scene_export`) at high LOD | desktop CPU | pure function of the document; the output is an `.fcxr` blob already designed to travel |
| ICP scan refinement (`xrscan.align.icp`) on large scans | desktop, pure Python | batchable; a 500k-point scan is minutes locally |
| Toolpath collision sweeps (`xrcam.check_collisions`) | desktop | per-segment independent; the whole job is one request |
| Deviation-layer merge evaluation (`collab.merge` with `FreeCADEvaluator`) | a FreeCAD process | needs a full recompute; the concept doc already puts this in an isolated worktree |
| Mesh feature recovery (`xrassembly.from_mesh`) on imported STLs | desktop | one-shot per import |
| Photogrammetry itself (images → mesh) | not in the tree | GPU-bound, hours on a laptop; the obvious first cloud job |

Bad candidates (per-frame, latency-critical):

- fit-check collision response (`xrfit.session`) — must answer within a frame or the part visibly lags the hand
- presence and pose exchange — already the lightest thing on the wire
- rendering — remote render streaming to a headset is a different product (≈20 ms end-to-end and a codec pipeline); not this proposal

## How it fits the existing architecture

- **Detection.** The viewer already measures `frame_duration`; add a capabilities probe (CPU count, RAM, GPU string from OpenGL, headset vs tethered) and a rolling frame-time budget, and publish it in `GET /api/v1/hello` so peers can see who is weak.
- **Transport.** The sync protocol (`ARCHITECTURE.md` §3/§3b) is plain HTTP with bearer auth. A worker is another peer offering `POST /api/v1/jobs` and `GET /api/v1/jobs/<id>`; job bodies are the JSON and `.fcxr` payloads the subsystems already produce. The desktop is the natural broker: the headset asks it, it decides local or remote.
- **Isolation.** Every offloadable function above is pure (no FreeCAD import at module scope, ARCHITECTURE §6), so a worker is `python3 -m xrsync.worker` in a container with the same `src/Mod/XR` tree. The merge evaluation is the exception — it needs a headless FreeCAD (`freecadcmd`) in the container.
- **Policy.** Never silently. The wrist-menu status line should say "ICP on worker-1 (12 s)"; a failed job falls back to local with a message. Documents leave the machine, so it is opt-in per project with the worker address in the preferences, and the LAN case (a desktop PC as the worker for a standalone Quest) should work before any cloud case does.

## Open questions

- Which cloud? Anything that runs a container; the LAN-worker version has no vendor at all and is where to start.
- Cost/latency model: when is a 2 s round trip better than 6 s local? A simple rule (offload when the local estimate exceeds 3× the transfer time) covers the batch jobs above.
- Trust: the worker sees the geometry. Same pairing-code flow as headsets, plus TLS for anything off the LAN.

## Not in scope

Remote rendering / cloud XR streaming, and moving the per-frame collision response off the device.
