# SPDX-License-Identifier: LGPL-2.1-or-later
"""The VR alignment session: a scan at 1:1, aligned by touching it.

The scan is loaded as a mesh and placed in the world; the user then:

* touches a point on the scan and the matching point on the model (three
  such pairs give a Kabsch alignment; more refine it);
* or touches two points and says how far apart they really are (scale);
* or asks for the scan to sit on the build plate (RANSAC floor plane);
* and finally lets ICP take out the last fraction of a millimetre.

Every step updates ``scan_pose`` and emits an event with the residual, so
the HUD can show "3 pairs, RMS 0.4 mm" and the haptics can confirm a pick.
"""

from xrsketch import vecmath as vm

from xrfit.bvh import BVH

from . import align


class ScanEvent(object):
    __slots__ = ("kind", "detail", "time")

    def __init__(self, kind, detail=None, time=0.0):
        self.kind = kind
        self.detail = detail or {}
        self.time = time

    def to_dict(self):
        return {"kind": self.kind, "detail": dict(self.detail), "time": self.time}

    def __repr__(self):
        return "ScanEvent(%s)" % self.kind


class ScanSession(object):
    def __init__(self, scan_mesh, model_mesh=None, scan_pose=None, sample_points=2000):
        self.scan = scan_mesh
        self.model = model_mesh
        self._model_bvh = BVH(model_mesh) if model_mesh is not None else None
        self.scan_pose = scan_pose or vm.Transform.identity()
        self.pairs = []            # [(scan_local_point, model_world_point)]
        self.length_picks = []     # scan-local points for the known-length tool
        self.events = []
        self.history = []
        self._time = 0.0
        self.sample_points = int(sample_points)
        self.last_result = None

    # -- picking ---------------------------------------------------------

    def pick_scan(self, world_point):
        """Record a touch on the scan (world coordinates -> stored scan-local)."""
        local = self.scan_pose.inverse().apply(world_point)
        if self.pairs and self.pairs[-1][1] is None:
            self.pairs[-1] = (local, None)
        else:
            self.pairs.append((local, None))
        self._emit("pick_scan", {"pairs": len(self.pairs)})
        return local

    def pick_model(self, world_point):
        """Record the matching touch on the model. Completes the last pair."""
        if not self.pairs or self.pairs[-1][1] is not None:
            self.pairs.append((None, vm.vec3(world_point)))
        else:
            self.pairs[-1] = (self.pairs[-1][0], vm.vec3(world_point))
        self._emit("pick_model", {"pairs": len(self.complete_pairs())})
        return world_point

    def complete_pairs(self):
        return [(s, m) for s, m in self.pairs if s is not None and m is not None]

    def drop_last_pair(self):
        if self.pairs:
            self.pairs.pop()
            self._emit("drop_pair", {"pairs": len(self.pairs)})

    def clear_pairs(self):
        self.pairs = []

    # -- alignment -------------------------------------------------------

    def align_from_pairs(self, scale=False):
        pairs = self.complete_pairs()
        if len(pairs) < 3:
            raise align.AlignmentError("need three complete pairs, have %d" % len(pairs))
        result = align.kabsch([s for s, _ in pairs], [m for _, m in pairs], scale=scale)
        self._apply(result.transform, "aligned", {"rms": result.rms, "pairs": len(pairs), "scale": result.transform.scale})
        self.last_result = result
        return result

    def refine(self, iterations=30, max_pairs=None):
        if self._model_bvh is None:
            raise align.AlignmentError("no model mesh to refine against")
        points = align._subsample(self.scan.vertices, max_pairs or self.sample_points)
        result = align.icp(points, self._model_bvh, initial=self.scan_pose, iterations=iterations,
                           max_pairs=max_pairs or self.sample_points)
        self._apply(result.transform, "refined", {"rms": result.rms, "iterations": result.iterations})
        self.last_result = result
        return result

    def sit_on_plane(self, target_origin, target_normal, threshold=None):
        """Find the scan's largest plane and put it onto the target plane."""
        points = align._subsample(self.scan.vertices, self.sample_points)
        origin, normal, inliers = align.fit_plane(points, threshold=threshold)
        world_origin = self.scan_pose.apply(origin)
        world_normal = self.scan_pose.apply_vector(normal)
        step = align.plane_to_plane(world_origin, vm.normalize(world_normal), target_origin, target_normal)
        self._apply(vm.compose(step, self.scan_pose), "seated", {"inliers": len(inliers), "of": len(points)})
        return origin, normal

    def pick_length_point(self, world_point):
        local = self.scan_pose.inverse().apply(world_point)
        self.length_picks.append(local)
        if len(self.length_picks) > 2:
            self.length_picks = self.length_picks[-2:]
        self._emit("pick_length", {"points": len(self.length_picks)})
        return local

    def set_known_length(self, length):
        if len(self.length_picks) < 2:
            raise align.AlignmentError("pick two points first")
        a, b = self.length_picks
        factor = align.scale_from_known_length(a, b, length / self.scan_pose.scale)
        # scale about the midpoint of the two picks so the measured feature stays put
        mid_world = self.scan_pose.apply(vm.mul(vm.add(a, b), 0.5))
        scaled = vm.Transform(self.scan_pose.translation, self.scan_pose.rotation, self.scan_pose.scale * factor)
        drift = vm.sub(scaled.apply(vm.mul(vm.add(a, b), 0.5)), mid_world)
        scaled = vm.Transform(vm.sub(scaled.translation, drift), scaled.rotation, scaled.scale)
        self._apply(scaled, "scaled", {"factor": factor, "scale": scaled.scale})
        return factor

    def residuals(self):
        return [vm.dist(self.scan_pose.apply(s), m) for s, m in self.complete_pairs()]

    def move(self, transform):
        """A manual nudge from the hand (world-space delta)."""
        self._apply(vm.compose(transform, self.scan_pose), "moved")

    def undo(self):
        if not self.history:
            return False
        self.scan_pose = self.history.pop()
        self._emit("undo")
        return True

    def _apply(self, pose, kind, detail=None):
        self.history.append(self.scan_pose)
        self.scan_pose = pose
        self._emit(kind, detail)

    # -- events ----------------------------------------------------------

    def _emit(self, kind, detail=None):
        self.events.append(ScanEvent(kind, detail, self._time))

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def to_dict(self):
        return {"scan_pose": self.scan_pose.to_dict(),
                "pairs": [[list(s) if s else None, list(m) if m else None] for s, m in self.pairs],
                "residuals": self.residuals()}
