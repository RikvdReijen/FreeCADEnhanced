# SPDX-License-Identifier: LGPL-2.1-or-later
"""The fit-check session: a grabbed part stopped by the parts around it.

Each frame the hand proposes a pose for the grabbed part. The session tests
that pose against the static parts; if it collides, it pushes the part back
out along the aggregate contact normal (a few iterations), and if that
still collides it keeps the last free pose — the part *stops in your hand*.
Tangential motion is preserved, so a peg slides along a bore instead of
sticking, and the contact events go to the haptics engine so the stop is
felt as well as seen.

Nothing here is a physics engine: there is no mass, no friction, no
momentum. That is deliberate. The question a fit check answers is "does it
go in, and how much room is there", and a constraint response answers it
more reliably than a simulation that can tunnel or jitter.
"""

from xrsketch import vecmath as vm

from .bvh import BVH
from .collide import closest_distance, collide


class FitParams(object):
    __slots__ = ("push_iterations", "push_epsilon", "contact_margin", "clearance_max",
                 "slide", "grab_threshold", "seat_tolerance")

    def __init__(self, push_iterations=6, push_epsilon=1e-6, contact_margin=0.0,
                 clearance_max=0.05, slide=True, grab_threshold=0.7, seat_tolerance=1e-4):
        #: how many times to push out before giving up and holding the last free pose
        self.push_iterations = int(push_iterations)
        #: pushed a hair further than the measured depth so the next test is clear, metres
        self.push_epsilon = float(push_epsilon)
        #: a part within this of the margin counts as seated, metres
        self.seat_tolerance = float(seat_tolerance)
        #: extra distance kept between parts (a modelled clearance), metres
        self.contact_margin = float(contact_margin)
        #: clearance beyond which the distance search stops (saves time), metres
        self.clearance_max = float(clearance_max)
        #: keep the tangential component of a blocked move
        self.slide = bool(slide)
        self.grab_threshold = float(grab_threshold)


class FitEvent(object):
    """Something the haptics and the HUD want to know about."""

    __slots__ = ("kind", "part", "other", "depth", "clearance", "time")

    def __init__(self, kind, part, other=None, depth=0.0, clearance=None, time=0.0):
        self.kind = kind  # "contact", "release", "blocked", "seated", "clear"
        self.part = part
        self.other = other
        self.depth = depth
        self.clearance = clearance
        self.time = time

    def to_dict(self):
        return {"kind": self.kind, "part": self.part, "other": self.other,
                "depth": self.depth, "clearance": self.clearance, "time": self.time}

    def __repr__(self):
        return "FitEvent(%s %s%s)" % (self.kind, self.part, " vs " + self.other if self.other else "")


class _Part(object):
    __slots__ = ("name", "mesh", "bvh", "pose", "static")

    def __init__(self, name, mesh, pose, static):
        self.name = name
        self.mesh = mesh
        self.bvh = BVH(mesh)
        self.pose = pose or vm.Transform.identity()
        self.static = static


class FitSession(object):
    """Parts in a shared frame; one may be grabbed and moved."""

    def __init__(self, params=None):
        self.params = params or FitParams()
        self.parts = {}
        self.grabbed = None
        self._grab_offset = None
        self.events = []
        self.contacts = []
        self.clearance = None
        self.blocked = False
        self._time = 0.0
        self._touching = set()

    # -- parts -----------------------------------------------------------

    def add_part(self, name, mesh, pose=None, static=True):
        self.parts[name] = _Part(name, mesh, pose, static)
        return self.parts[name]

    def remove_part(self, name):
        if self.grabbed == name:
            self.release()
        return self.parts.pop(name, None)

    def pose_of(self, name):
        return self.parts[name].pose

    def set_pose(self, name, pose):
        self.parts[name].pose = pose

    # -- grabbing --------------------------------------------------------

    def grab(self, name, hand_pose):
        """Start moving ``name`` with the hand; the part keeps its offset to the hand."""
        part = self.parts[name]
        self.grabbed = name
        # part = hand ∘ offset  =>  offset = hand⁻¹ ∘ part
        self._grab_offset = vm.compose(hand_pose.inverse(), part.pose)
        self._emit("grab", name)
        return part

    def release(self):
        if self.grabbed is None:
            return None
        name = self.grabbed
        self.grabbed = None
        self._grab_offset = None
        self._emit("release", name)
        return name

    # -- per frame -------------------------------------------------------

    def update(self, dt, hand_pose=None, grip=None):
        """Advance one frame. ``hand_pose`` is the hand's Transform; ``grip``
        (0..1) releases the part when it drops below the threshold."""
        self._time += float(dt or 0.0)
        if self.grabbed is None or hand_pose is None:
            return False
        if grip is not None and grip < self.params.grab_threshold:
            self.release()
            return False
        target = vm.compose(hand_pose, self._grab_offset)
        return self.move_to(target)

    def move_to(self, target):
        """Try to move the grabbed part to ``target``, resolving contacts.

        Returns True when the part moved (even partially)."""
        part = self.parts[self.grabbed]
        previous = part.pose
        pose = target
        p = self.params
        blocked = False
        contacts = []
        touched_during = set()
        approach = vm.sub(target.translation, previous.translation)
        hint = vm.neg(approach) if vm.length(approach) > 1e-12 else None
        for _ in range(p.push_iterations):
            contacts = self._contacts_at(part, pose, hint)
            if not contacts:
                break
            touched_during.update(c[0] for c in contacts)
            push = _aggregate(contacts)
            if vm.length(push) < 1e-12:
                break
            push = vm.add(push, vm.mul(vm.normalize(push), p.push_epsilon))
            pose = vm.Transform(vm.add(pose.translation, push), pose.rotation, pose.scale)
        else:
            contacts = self._contacts_at(part, pose, hint)
        if contacts:
            # Could not resolve: keep the last free pose, keeping the tangential
            # part of the requested move when sliding is on.
            blocked = True
            if p.slide:
                slid = self._slide(part, previous, target, contacts)
                if slid is not None:
                    pose = slid
                else:
                    pose = previous
            else:
                pose = previous
        part.pose = pose
        self.contacts = contacts
        self.blocked = blocked
        touching = touched_during | {c[0] for c in contacts}
        # Touch events for anything the part was pressed against this frame.
        for other in sorted(touching - self._touching):
            depth = max((c[2] for c in contacts if c[0] == other), default=0.0)
            self._emit("contact", part.name, other, depth=depth)
        for other in sorted(self._touching - touching):
            self._emit("clear", part.name, other)
        self._touching = touching
        if blocked:
            self._emit("blocked", part.name, depth=max(c[2] for c in contacts))
        else:
            self.clearance = self._clearance(part, pose)
            if self.clearance is not None and self.clearance[0] <= p.contact_margin + p.seat_tolerance:
                self._emit("seated", part.name, self.clearance[1], clearance=self.clearance[0])
        return not previous.almost_equal(part.pose)

    # -- queries ---------------------------------------------------------

    def _contacts_at(self, part, pose, hint=None):
        """``[(other_name, push_vector, depth)]`` for the part at ``pose``."""
        found = []
        margin = self.params.contact_margin
        for other in self.parts.values():
            if other is part or not other.static:
                continue
            relative = vm.compose(other.pose.inverse(), pose)
            local_hint = other.pose.inverse().apply_vector(hint) if hint is not None else None
            result = collide(part.bvh, other.bvh, relative, local_hint)
            if result.colliding:
                push_world = other.pose.apply_vector(result.push)
                found.append((other.name, push_world, result.depth))
            elif margin > 0.0:
                d, pa, pb = closest_distance(part.bvh, other.bvh, relative, upper=margin)
                if pa is not None and d < margin:
                    n = vm.normalize(vm.sub(pa, pb))
                    found.append((other.name, other.pose.apply_vector(vm.mul(n, margin - d)), margin - d))
        return found

    def _clearance(self, part, pose):
        best = None
        for other in self.parts.values():
            if other is part or not other.static:
                continue
            relative = vm.compose(other.pose.inverse(), pose)
            d, pa, pb = closest_distance(part.bvh, other.bvh, relative, upper=self.params.clearance_max)
            if pa is None:
                continue
            if best is None or d < best[0]:
                best = (d, other.name, other.pose.apply(pa), other.pose.apply(pb))
        return best

    def _slide(self, part, previous, target, contacts):
        """Keep the part of the move tangential to the blocking normals."""
        move = vm.sub(target.translation, previous.translation)
        for _, push, _ in contacts:
            n = vm.normalize(push)
            along = vm.dot(move, n)
            if along < 0.0:
                move = vm.sub(move, vm.mul(n, along))
        if vm.length(move) < 1e-9:
            return None
        candidate = vm.Transform(vm.add(previous.translation, move), previous.rotation, previous.scale)
        if self._contacts_at(part, candidate):
            # binary search along the slide for the last free point
            lo, hi = 0.0, 1.0
            for _ in range(6):
                mid = 0.5 * (lo + hi)
                trial = vm.Transform(vm.add(previous.translation, vm.mul(move, mid)),
                                     previous.rotation, previous.scale)
                if self._contacts_at(part, trial):
                    hi = mid
                else:
                    lo = mid
            if lo <= 0.0:
                return None
            candidate = vm.Transform(vm.add(previous.translation, vm.mul(move, lo)),
                                     previous.rotation, previous.scale)
        return candidate

    def is_free(self, name, pose=None):
        part = self.parts[name]
        return not self._contacts_at(part, pose or part.pose)

    def clearance_of(self, name, pose=None):
        part = self.parts[name]
        return self._clearance(part, pose or part.pose)

    # -- events ----------------------------------------------------------

    def _emit(self, kind, part, other=None, depth=0.0, clearance=None):
        self.events.append(FitEvent(kind, part, other, depth, clearance, self._time))

    def drain_events(self):
        events, self.events = self.events, []
        return events


class InsertionProbe(object):
    """Sweep a part along a direction and report where it stops.

    ``probe(session, name, direction, distance)`` returns the free travel
    before the first contact, the part it hit, and the clearance just before
    contact — the three numbers that say whether a peg goes into a hole and
    how snug it is.
    """

    def __init__(self, steps=32, refine=8):
        self.steps = int(steps)
        self.refine = int(refine)

    def probe(self, session, name, direction, distance):
        part = session.parts[name]
        d = vm.normalize(direction)
        start = part.pose

        def pose_at(s):
            return vm.Transform(vm.add(start.translation, vm.mul(d, s)), start.rotation, start.scale)

        last_free = 0.0
        hit = None
        for k in range(1, self.steps + 1):
            s = distance * k / self.steps
            contacts = session._contacts_at(part, pose_at(s))
            if contacts:
                hit = contacts[0][0]
                lo, hi = last_free, s
                for _ in range(self.refine):
                    mid = 0.5 * (lo + hi)
                    if session._contacts_at(part, pose_at(mid)):
                        hi = mid
                    else:
                        lo = mid
                last_free = lo
                break
            last_free = s
        clearance = session._clearance(part, pose_at(last_free))
        return {"travel": last_free, "blocked_by": hit, "requested": distance,
                "clearance": None if clearance is None else clearance[0],
                "inserted": hit is None}


def _aggregate(contacts):
    total = (0.0, 0.0, 0.0)
    depth = 0.0
    for _, push, dep in contacts:
        total = vm.add(total, push)
        depth = max(depth, dep)
    n = vm.normalize(total)
    if vm.length(n) < 0.5:
        return (0.0, 0.0, 0.0)
    return vm.mul(n, depth)
