# SPDX-License-Identifier: LGPL-2.1-or-later
"""Putting the toolpath inside the machine — and checking it fits.

A :class:`MachineSpec` says where the machine's coordinate origin sits on
the environment's build-plate anchor, how big the bed and the travel are,
and what the toolhead looks like. :meth:`MachineSpec.to_world` maps a
G-code point (mm, Z up, machine origin) onto the environment (metres, Y
up), which is how the toolpath is drawn at scale where you are standing.

:func:`check_bounds` flags moves outside the travel — the classic "the
slicer thought the bed was bigger" — and :func:`check_collisions` sweeps a
toolhead envelope along the path against obstacle meshes (bed clips, a
fixture, an already-printed part) using :mod:`xrfit`, which is what catches
the crashes people currently discover on the machine.
"""

import math

from xrsketch import vecmath as vm

from .gcode import Segment


class MachineSpec(object):
    """Machine geometry in machine units (mm) plus its placement in the environment."""

    def __init__(self, name="machine", bed=(256.0, 256.0), height=256.0, origin="corner", anchor_position=(0, 0, 0),
                 anchor_rotation=vm.IDENTITY_QUAT, anchor_size=None, head_radius=20.0, head_height=60.0,
                 nozzle_offset=(0.0, 0.0, 0.0), kind="printer"):
        self.name = name
        #: usable bed size in mm (x, y)
        self.bed = (float(bed[0]), float(bed[1]))
        #: travel above the bed, mm
        self.height = float(height)
        #: "corner": G-code (0,0) is the front-left corner; "center": the bed centre
        self.origin = origin
        #: the build-plate anchor: position (metres, Y up), rotation, size (metres) — from the environment spec
        self.anchor_position = vm.vec3(anchor_position)
        self.anchor_rotation = vm.quat_normalize(anchor_rotation)
        self.anchor_size = anchor_size
        #: the toolhead envelope: a cylinder of this radius / height above the nozzle tip, mm
        self.head_radius = float(head_radius)
        self.head_height = float(head_height)
        self.nozzle_offset = vm.vec3(nozzle_offset)
        self.kind = kind

    @classmethod
    def from_environment_spec(cls, spec, env_id=None, **overrides):
        """Read the build-plate (or bed) anchor from a declarative environment spec."""
        anchors = spec.get("anchors", {}) or {}
        anchor = anchors.get("build_plate") or anchors.get("bed") or anchors.get("work_area")
        if anchor is None:
            raise ValueError("environment %r has no build_plate/bed anchor" % (env_id or spec.get("id")))
        size = anchor.get("size", (0.256, 0.256))
        bed = (float(size[0]) * 1000.0, float(size[1]) * 1000.0)
        bounds = spec.get("bounds", (1.0, 1.0, 1.0))
        kind = "laser" if "laser" in str(spec.get("id", "")) else "printer"
        kwargs = dict(name=spec.get("name", spec.get("id", "machine")), bed=bed,
                      height=float(bounds[1]) * 1000.0 * 0.6 if kind == "printer" else 50.0,
                      origin="corner", anchor_position=anchor.get("position", (0, 0, 0)),
                      anchor_rotation=anchor.get("rotation", vm.IDENTITY_QUAT), anchor_size=size, kind=kind)
        if kind == "laser":
            kwargs.update(head_radius=15.0, head_height=40.0)
        kwargs.update(overrides)
        return cls(**kwargs)

    # -- mapping ---------------------------------------------------------

    def machine_to_local(self, p):
        """Machine mm (X right, Y back, Z up) -> anchor-local metres (X, Y in the
        plate, +Z its normal), centred on the plate."""
        x, y, z = p[0] + self.nozzle_offset[0], p[1] + self.nozzle_offset[1], p[2] + self.nozzle_offset[2]
        if self.origin == "corner":
            x -= self.bed[0] / 2.0
            y -= self.bed[1] / 2.0
        return (x / 1000.0, y / 1000.0, z / 1000.0)

    def to_world(self, p):
        """Machine mm -> environment world metres."""
        local = self.machine_to_local(p)
        rotated = vm.Transform((0, 0, 0), self.anchor_rotation).apply_vector(local)
        return vm.add(self.anchor_position, rotated)

    def from_world(self, w):
        local = vm.Transform((0, 0, 0), self.anchor_rotation).inverse().apply_vector(vm.sub(w, self.anchor_position))
        x, y, z = local[0] * 1000.0, local[1] * 1000.0, local[2] * 1000.0
        if self.origin == "corner":
            x += self.bed[0] / 2.0
            y += self.bed[1] / 2.0
        return (x - self.nozzle_offset[0], y - self.nozzle_offset[1], z - self.nozzle_offset[2])

    def inside(self, p, margin=0.0):
        x, y, z = p
        if self.origin == "corner":
            return -margin <= x <= self.bed[0] + margin and -margin <= y <= self.bed[1] + margin and -margin <= z <= self.height + margin
        return (abs(x) <= self.bed[0] / 2.0 + margin and abs(y) <= self.bed[1] / 2.0 + margin
                and -margin <= z <= self.height + margin)

    def to_dict(self):
        return {"name": self.name, "bed": list(self.bed), "height": self.height, "origin": self.origin,
                "anchor_position": list(self.anchor_position), "anchor_rotation": list(self.anchor_rotation),
                "head_radius": self.head_radius, "head_height": self.head_height, "kind": self.kind}


class Issue(object):
    __slots__ = ("kind", "message", "segment", "time", "point", "obstacle", "severity")

    def __init__(self, kind, message, segment=None, time=0.0, point=None, obstacle=None, severity="error"):
        self.kind = kind
        self.message = message
        self.segment = segment
        self.time = float(time)
        self.point = point
        self.obstacle = obstacle
        self.severity = severity

    def to_dict(self):
        return {"kind": self.kind, "message": self.message, "segment": self.segment, "time": self.time,
                "point": None if self.point is None else list(self.point), "obstacle": self.obstacle,
                "severity": self.severity}

    def __repr__(self):
        return "Issue(%s: %s)" % (self.kind, self.message)


def check_bounds(toolpath, machine, margin=0.0, max_issues=50):
    """Segments whose end (or arc) leaves the machine's travel."""
    issues = []
    elapsed = 0.0
    for i, seg in enumerate(toolpath.segments):
        points = seg.polyline(1.0) if seg.arc else [seg.end]
        for p in points:
            if not machine.inside(p, margin):
                issues.append(Issue("out_of_bounds", "line %d: (%.1f, %.1f, %.1f) is outside the %s travel"
                                    % (seg.line, p[0], p[1], p[2], machine.name), i, elapsed, p))
                break
        elapsed += seg.duration
        if len(issues) >= max_issues:
            issues.append(Issue("truncated", "more than %d out-of-bounds moves; stopped checking" % max_issues, severity="warning"))
            break
    return issues


def head_mesh(machine, sides=16):
    """The toolhead envelope as a mesh in machine mm, tip at the origin, +Z up."""
    from xrfit.mesh import cylinder_mesh

    return cylinder_mesh(machine.head_radius, machine.head_height, sides, center=(0.0, 0.0, machine.head_height / 2.0), name="toolhead")


def check_collisions(toolpath, machine, obstacles, step=5.0, max_issues=20, cutting_only=False):
    """Sweep the toolhead along the path against ``obstacles``.

    ``obstacles`` is ``{name: (TriMesh in machine mm, Transform or None)}``.
    Sampled every ``step`` mm along each segment; the first hit per obstacle
    per segment is reported with the playback time it occurs at. This is a
    sampled sweep, not a continuous one: a hit thinner than ``step`` between
    samples can be missed, which is why ``step`` defaults to a few mm.
    """
    from xrfit.bvh import BVH
    from xrfit.collide import collide

    head = BVH(head_mesh(machine))
    trees = {name: (BVH(mesh), pose or vm.Transform.identity()) for name, (mesh, pose) in obstacles.items()}
    issues = []
    elapsed = 0.0
    for i, seg in enumerate(toolpath.segments):
        if cutting_only and not seg.cutting:
            elapsed += seg.duration
            continue
        n = max(1, int(math.ceil(seg.length / step)))
        hit_names = set()
        for k in range(n + 1):
            t = k / float(n)
            p = seg.point_at(t)
            head_pose = vm.Transform(p)
            for name, (tree, pose) in trees.items():
                if name in hit_names:
                    continue
                relative = vm.compose(pose.inverse(), head_pose)
                if not head.bounds.transformed(relative).overlaps(tree.bounds):
                    continue
                result = collide(head, tree, relative)
                if result.colliding:
                    hit_names.add(name)
                    issues.append(Issue("collision", "line %d: toolhead hits %s at (%.1f, %.1f, %.1f)"
                                        % (seg.line, name, p[0], p[1], p[2]), i, elapsed + seg.duration * t, p, name))
        elapsed += seg.duration
        if len(issues) >= max_issues:
            issues.append(Issue("truncated", "more than %d collisions; stopped checking" % max_issues, severity="warning"))
            break
    return issues


def world_polyline(toolpath, machine, max_error=0.2, cutting_only=False):
    """``[(points_world, cutting, layer)]`` per segment — what the renderer draws."""
    out = []
    for seg in toolpath.segments:
        if cutting_only and not seg.cutting:
            continue
        out.append(([machine.to_world(p) for p in seg.polyline(max_error)], seg.cutting, seg.layer))
    return out
