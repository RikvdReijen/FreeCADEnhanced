# SPDX-License-Identifier: LGPL-2.1-or-later
"""The deviation layer format: reader, writer and in-memory representation.

This implements ``docs/concepts/ai-cad-collaboration/SPEC-deviation-layer.md``
sections 1-3. A layer is a named, replayable set of parametric operations
recorded against *stable references* rather than topological names, carrying
the intent it was recorded for and the validation it passed.

Three properties of the format are load-bearing and are enforced here rather
than left to callers:

* ``set_param`` records ``from`` as well as ``to``. Dropping it would make a
  parametric conflict undetectable, so ``from`` is required.
* ``resolved_at_record`` is *stored* on an anchor and never *read* during
  resolution. :mod:`collab.anchors` does not import it; see that module.
* An unknown ``op`` is an error, not a skipped entry. Replaying a layer while
  silently dropping an operation you did not understand produces a model that
  looks fine and is wrong.
"""

import json
import re

from .errors import LayerFormatError
from .geom import as_vec

#: The schema version this implementation writes and accepts.
SCHEMA_VERSION = 1

#: Prefix marking a reference to an anchor declared in the same layer.
ANCHOR_SIGIL = "@"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _require(data, key, path, kind=None):
    if key not in data:
        raise LayerFormatError(f"missing required field {key!r}", path)
    value = data[key]
    if kind is not None and not isinstance(value, kind):
        name = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise LayerFormatError(f"{key!r} must be {name}, got {type(value).__name__}", path)
    return value


def anchor_ref(value):
    """Return the anchor name in ``value`` if it is an anchor reference."""
    if isinstance(value, str) and value.startswith(ANCHOR_SIGIL) and len(value) > 1:
        return value[1:]
    return None


def _collect_anchor_refs(value, found):
    if isinstance(value, dict):
        for item in value.values():
            _collect_anchor_refs(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_anchor_refs(item, found)
    else:
        name = anchor_ref(value)
        if name is not None:
            found.add(name)


# ---------------------------------------------------------------------------
# provenance and intent
# ---------------------------------------------------------------------------


class Author:
    """Who recorded a layer. ``kind`` is ``"agent"`` or ``"human"``.

    ``AI_POLICY.md`` requires disclosure of AI assistance; for a layer the
    requirement is stronger than a commit trailer, because the layer outlives
    the commit and a dimension nobody can account for is a liability.
    """

    __slots__ = ("kind", "id", "session", "human_sponsor")

    def __init__(self, kind, id, session=None, human_sponsor=None):
        if kind not in ("agent", "human"):
            raise LayerFormatError(f"author kind must be 'agent' or 'human', got {kind!r}", "author")
        if kind == "agent" and not human_sponsor:
            raise LayerFormatError("an agent author must name a human_sponsor", "author")
        self.kind = kind
        self.id = id
        self.session = session
        self.human_sponsor = human_sponsor

    def to_json(self):
        data = {"kind": self.kind, "id": self.id}
        if self.session:
            data["session"] = self.session
        if self.human_sponsor:
            data["human_sponsor"] = self.human_sponsor
        return data

    @classmethod
    def from_json(cls, data, path="author"):
        if not isinstance(data, dict):
            raise LayerFormatError("author must be an object", path)
        return cls(
            kind=_require(data, "kind", path, str),
            id=_require(data, "id", path, str),
            session=data.get("session"),
            human_sponsor=data.get("human_sponsor"),
        )

    def __repr__(self):
        return f"Author({self.kind!r}, {self.id!r})"


#: Comparison operators a success criterion may use.
CRITERION_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class Criterion:
    """One machine-checkable success criterion.

    ``SPEC`` §2: "An intent that cannot be checked is a comment." Criteria are
    re-run after a merge, which is how the format catches two changes that were
    each individually fine and are jointly over budget.
    """

    __slots__ = ("metric", "op", "value")

    def __init__(self, metric, op, value):
        if op not in CRITERION_OPS:
            raise LayerFormatError(
                f"unknown operator {op!r}, expected one of {sorted(CRITERION_OPS)}",
                "success_criteria",
            )
        self.metric = metric
        self.op = op
        self.value = value

    def check(self, metrics):
        """Evaluate against a metric mapping.

        Returns ``(passed, actual)``. ``passed`` is ``None`` when the metric is
        absent — unknown is not the same as failing, and reporting it as a
        failure would train reviewers to ignore the result.
        """
        if self.metric not in metrics:
            return None, None
        actual = metrics[self.metric]
        try:
            return bool(CRITERION_OPS[self.op](actual, self.value)), actual
        except TypeError:
            return None, actual

    def describe(self):
        return f"{self.metric} {self.op} {self.value}"

    def to_json(self):
        return {"metric": self.metric, "op": self.op, "value": self.value}

    @classmethod
    def from_json(cls, data, path="success_criteria[]"):
        if not isinstance(data, dict):
            raise LayerFormatError("criterion must be an object", path)
        return cls(
            metric=_require(data, "metric", path, str),
            op=_require(data, "op", path, str),
            value=_require(data, "value", path),
        )

    def __repr__(self):
        return f"Criterion({self.describe()!r})"


class Intent:
    """Why a layer exists, in a form that can be checked afterwards."""

    __slots__ = ("goal", "rationale", "success_criteria")

    def __init__(self, goal, rationale="", success_criteria=()):
        self.goal = goal
        self.rationale = rationale
        self.success_criteria = list(success_criteria)

    def check(self, metrics):
        """Return ``[(criterion, passed, actual), ...]`` for every criterion."""
        return [(c,) + c.check(metrics) for c in self.success_criteria]

    def to_json(self):
        data = {"goal": self.goal}
        if self.rationale:
            data["rationale"] = self.rationale
        if self.success_criteria:
            data["success_criteria"] = [c.to_json() for c in self.success_criteria]
        return data

    @classmethod
    def from_json(cls, data, path="intent"):
        if not isinstance(data, dict):
            raise LayerFormatError("intent must be an object", path)
        return cls(
            goal=_require(data, "goal", path, str),
            rationale=data.get("rationale", ""),
            success_criteria=[
                Criterion.from_json(c, f"{path}.success_criteria[{i}]")
                for i, c in enumerate(data.get("success_criteria", []))
            ],
        )

    def __repr__(self):
        return f"Intent({self.goal!r}, {len(self.success_criteria)} criteria)"


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------

#: Claim modes. ``advisory`` warns on overlap; ``exclusive`` blocks it.
CLAIM_MODES = ("advisory", "exclusive")


class Dependency:
    """Something a layer needs to stay put while it works.

    Exactly one of ``anchor`` or ``param`` is set. Both forms carry a
    ``reason``, because a watched dependency with no stated reason cannot be
    triaged by whoever trips over it.
    """

    __slots__ = ("anchor", "param", "reason")

    def __init__(self, anchor=None, param=None, reason=""):
        if (anchor is None) == (param is None):
            raise LayerFormatError("a dependency names exactly one of 'anchor' or 'param'", "depends")
        self.anchor = anchor
        self.param = param
        self.reason = reason

    @property
    def key(self):
        return f"anchor:{self.anchor}" if self.anchor else f"param:{self.param}"

    def to_json(self):
        data = {"anchor": self.anchor} if self.anchor else {"param": self.param}
        if self.reason:
            data["reason"] = self.reason
        return data

    @classmethod
    def from_json(cls, data, path="claims.depends[]"):
        if isinstance(data, str):
            # Tolerated shorthand: "param:hole_spacing" or an anchor name.
            if data.startswith("param:"):
                return cls(param=data[len("param:") :], reason="")
            return cls(anchor=data, reason="")
        if not isinstance(data, dict):
            raise LayerFormatError("dependency must be an object or string", path)
        return cls(
            anchor=data.get("anchor"),
            param=data.get("param"),
            reason=data.get("reason", ""),
        )

    def __repr__(self):
        return f"Dependency({self.key!r})"


class Claims:
    """What a layer modifies and what it needs unchanged.

    Declared *before* the work starts. SPEC §4.2 of the README calls this the
    cheapest win in the design: detecting the collision before two agents spend
    an hour diverging is worth more than any merge algorithm.
    """

    __slots__ = ("modifies", "depends", "mode")

    def __init__(self, modifies=(), depends=(), mode="advisory"):
        if mode not in CLAIM_MODES:
            raise LayerFormatError(f"claim mode must be one of {CLAIM_MODES}, got {mode!r}", "claims.mode")
        self.modifies = list(modifies)
        self.depends = list(depends)
        self.mode = mode

    def to_json(self):
        data = {}
        if self.modifies:
            data["modifies"] = list(self.modifies)
        if self.depends:
            data["depends"] = [d.to_json() for d in self.depends]
        data["mode"] = self.mode
        return data

    @classmethod
    def from_json(cls, data, path="claims"):
        if not isinstance(data, dict):
            raise LayerFormatError("claims must be an object", path)
        return cls(
            modifies=data.get("modifies", []),
            depends=[
                Dependency.from_json(d, f"{path}.depends[{i}]")
                for i, d in enumerate(data.get("depends", []))
            ],
            mode=data.get("mode", "advisory"),
        )

    def __repr__(self):
        return f"Claims({self.modifies!r}, mode={self.mode!r})"


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------

#: Anchor strategies, in the order of preference set out in README §4.1.
ANCHOR_STRATEGIES = ("datum", "semantic", "fingerprint")


class Fingerprint:
    """A geometric signature, used only as a last resort and always as a hint.

    Area, centroid in a local frame, surface type and adjacency degree. Matched
    within tolerance and only accepted when the best candidate is clearly
    separated from the runner-up — see :mod:`collab.anchors`.
    """

    __slots__ = ("area", "length", "centroid_local", "surface", "adjacency")

    def __init__(self, area=None, length=None, centroid_local=None, surface=None, adjacency=None):
        self.area = None if area is None else float(area)
        self.length = None if length is None else float(length)
        self.centroid_local = as_vec(centroid_local, "fingerprint.centroid_local")
        self.surface = surface
        self.adjacency = None if adjacency is None else int(adjacency)

    @property
    def size(self):
        return self.area if self.area is not None else self.length

    def to_json(self):
        data = {}
        for field in ("area", "length", "surface", "adjacency"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value
        if self.centroid_local is not None:
            data["centroid_local"] = list(self.centroid_local)
        return data

    @classmethod
    def from_json(cls, data, path="fingerprint"):
        if not isinstance(data, dict):
            raise LayerFormatError("fingerprint must be an object", path)
        return cls(
            area=data.get("area"),
            length=data.get("length"),
            centroid_local=data.get("centroid_local"),
            surface=data.get("surface"),
            adjacency=data.get("adjacency"),
        )

    @classmethod
    def of(cls, entity):
        """Take a fingerprint of an entity, for recording an anchor."""
        return cls(
            area=entity.area,
            length=entity.length,
            centroid_local=entity.centroid_local,
            surface=entity.surface,
            adjacency=entity.adjacency,
        )

    def __repr__(self):
        return f"Fingerprint({self.to_json()!r})"


class Anchor:
    """A stable reference to a topological entity.

    ``resolved_at_record`` holds the topological name the anchor had when it
    was recorded. It is kept for diagnostics — so a failed re-anchor can say
    "this used to be Face6" — and is *never* consulted during resolution. See
    SPEC §4: there is deliberately no fallback to it, because reusing a stale
    topological name fails silently and plausibly, which is the worst available
    failure mode.
    """

    __slots__ = ("name", "strategy", "query", "fingerprint", "resolved_at_record")

    def __init__(self, name, strategy="semantic", query=None, fingerprint=None, resolved_at_record=None):
        if strategy not in ANCHOR_STRATEGIES:
            raise LayerFormatError(
                f"unknown anchor strategy {strategy!r}, expected one of {ANCHOR_STRATEGIES}",
                f"anchors.{name}.strategy",
            )
        if strategy in ("datum", "semantic") and not query:
            raise LayerFormatError(f"a {strategy} anchor needs a query", f"anchors.{name}.query")
        if strategy == "fingerprint" and fingerprint is None:
            raise LayerFormatError("a fingerprint anchor needs a fingerprint", f"anchors.{name}.fingerprint")
        self.name = name
        self.strategy = strategy
        self.query = dict(query or {})
        self.fingerprint = fingerprint
        self.resolved_at_record = resolved_at_record

    def to_json(self):
        data = {"strategy": self.strategy}
        if self.query:
            data["query"] = dict(self.query)
        if self.fingerprint is not None:
            data["fingerprint"] = self.fingerprint.to_json()
        if self.resolved_at_record is not None:
            data["resolved_at_record"] = self.resolved_at_record
        return data

    @classmethod
    def from_json(cls, name, data, path=None):
        path = path or f"anchors.{name}"
        if not isinstance(data, dict):
            raise LayerFormatError("anchor must be an object", path)
        fingerprint = data.get("fingerprint")
        return cls(
            name=name,
            strategy=data.get("strategy", "semantic"),
            query=data.get("query"),
            fingerprint=Fingerprint.from_json(fingerprint, f"{path}.fingerprint") if fingerprint else None,
            resolved_at_record=data.get("resolved_at_record"),
        )

    def __repr__(self):
        return f"Anchor({self.name!r}, {self.strategy!r})"


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


class Operation:
    """Base class for the seven operations of SPEC §3."""

    OP = None
    __slots__ = ()

    def to_json(self):
        raise NotImplementedError

    def targets(self):
        """Feature-level targets this operation modifies."""
        return ()

    def reads(self):
        """Feature-level targets this operation reads but does not modify."""
        return ()

    def position(self):
        """The ``after`` position this operation competes for, if any."""
        return None

    def anchor_refs(self):
        found = set()
        _collect_anchor_refs(self.to_json(), found)
        return tuple(sorted(found))

    def describe(self):
        return self.OP

    def __repr__(self):
        return f"<{type(self).__name__} {self.describe()}>"


class AddFeature(Operation):
    """``after`` is a feature *name*, never an index — indices do not survive."""

    OP = "add_feature"
    __slots__ = ("kind", "name", "after", "params", "sketch", "depends_on")

    def __init__(self, kind, name, after=None, params=None, sketch=None, depends_on=()):
        self.kind = kind
        self.name = name
        self.after = after
        self.params = dict(params or {})
        self.sketch = dict(sketch) if sketch else None
        self.depends_on = tuple(depends_on)

    def targets(self):
        return (self.name,)

    def reads(self):
        return (self.after,) if self.after else ()

    def position(self):
        return self.after

    def to_json(self):
        data = {"op": self.OP, "kind": self.kind, "name": self.name}
        if self.after is not None:
            data["after"] = self.after
        if self.sketch:
            data["sketch"] = dict(self.sketch)
        if self.params:
            data["params"] = dict(self.params)
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        return data

    @classmethod
    def from_json(cls, data, path):
        return cls(
            kind=_require(data, "kind", path, str),
            name=_require(data, "name", path, str),
            after=data.get("after"),
            params=data.get("params"),
            sketch=data.get("sketch"),
            depends_on=data.get("depends_on", ()),
        )

    def describe(self):
        return f"add {self.kind} {self.name} after {self.after}"


class RemoveFeature(Operation):
    """Fails loudly if anything downstream depends on the target."""

    OP = "remove_feature"
    __slots__ = ("target",)

    def __init__(self, target):
        self.target = target

    def targets(self):
        return (self.target,)

    def to_json(self):
        return {"op": self.OP, "target": self.target}

    @classmethod
    def from_json(cls, data, path):
        return cls(target=_require(data, "target", path, str))

    def describe(self):
        return f"remove {self.target}"


class SetParam(Operation):
    """``from_value`` is what makes a parametric conflict detectable.

    If the current value is not ``from``, someone else moved it, and that is a
    conflict even when the target value happens to agree.
    """

    OP = "set_param"
    __slots__ = ("target", "from_value", "to_value")

    def __init__(self, target, from_value, to_value):
        self.target = target
        self.from_value = from_value
        self.to_value = to_value

    def targets(self):
        return (self.target,)

    def to_json(self):
        return {"op": self.OP, "target": self.target, "from": self.from_value, "to": self.to_value}

    @classmethod
    def from_json(cls, data, path):
        if "from" not in data:
            raise LayerFormatError(
                "set_param requires 'from'; without it a parametric conflict cannot be detected",
                path,
            )
        return cls(
            target=_require(data, "target", path, str),
            from_value=data["from"],
            to_value=_require(data, "to", path),
        )

    def describe(self):
        return f"set {self.target} {self.from_value} -> {self.to_value}"


class MoveFeature(Operation):
    """Reordering is an operation, not a side effect."""

    OP = "move_feature"
    __slots__ = ("target", "after")

    def __init__(self, target, after=None):
        self.target = target
        self.after = after

    def targets(self):
        return (self.target,)

    def reads(self):
        return (self.after,) if self.after else ()

    def position(self):
        return self.after

    def to_json(self):
        data = {"op": self.OP, "target": self.target}
        if self.after is not None:
            data["after"] = self.after
        return data

    @classmethod
    def from_json(cls, data, path):
        return cls(target=_require(data, "target", path, str), after=data.get("after"))

    def describe(self):
        return f"move {self.target} after {self.after}"


class AddDatum(Operation):
    """Encouraged: a named datum is stable by construction and anchors to it never drift."""

    OP = "add_datum"
    __slots__ = ("kind", "name", "placement", "after", "depends_on")

    def __init__(self, kind, name, placement=None, after=None, depends_on=()):
        self.kind = kind
        self.name = name
        self.placement = dict(placement or {})
        self.after = after
        self.depends_on = tuple(depends_on)

    def targets(self):
        return (self.name,)

    def reads(self):
        return (self.after,) if self.after else ()

    def position(self):
        return self.after

    def to_json(self):
        data = {"op": self.OP, "kind": self.kind, "name": self.name}
        if self.placement:
            data["placement"] = dict(self.placement)
        if self.after is not None:
            data["after"] = self.after
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        return data

    @classmethod
    def from_json(cls, data, path):
        return cls(
            kind=_require(data, "kind", path, str),
            name=_require(data, "name", path, str),
            placement=data.get("placement"),
            after=data.get("after"),
            depends_on=data.get("depends_on", ()),
        )

    def describe(self):
        return f"add datum {self.name} ({self.kind})"


class EditSketch(Operation):
    """The level-4 hazard.

    Recording one is always safe. Combining two on the same sketch is refused
    by :mod:`collab.merge` — constraint solvers are not deterministic in the way
    a merge would need. That asymmetry is intentional.
    """

    OP = "edit_sketch"
    __slots__ = ("target", "geometry", "constraints")

    def __init__(self, target, geometry=None, constraints=None):
        self.target = target
        self.geometry = list(geometry or [])
        self.constraints = list(constraints or [])

    def targets(self):
        return (self.target,)

    def to_json(self):
        data = {"op": self.OP, "target": self.target}
        if self.geometry:
            data["geometry"] = list(self.geometry)
        if self.constraints:
            data["constraints"] = list(self.constraints)
        return data

    @classmethod
    def from_json(cls, data, path):
        return cls(
            target=_require(data, "target", path, str),
            geometry=data.get("geometry"),
            constraints=data.get("constraints"),
        )

    def describe(self):
        return f"edit sketch {self.target}"


class SetProperty(Operation):
    """Visual or metadata only — never geometric.

    Enforced at replay: a property named like a geometric parameter is
    rejected, so that a change which moves geometry cannot slip past the merge
    algorithm disguised as a colour change.
    """

    OP = "set_property"
    __slots__ = ("target", "property", "from_value", "to_value")

    def __init__(self, target, property, from_value=None, to_value=None):
        self.target = target
        self.property = property
        self.from_value = from_value
        self.to_value = to_value

    def targets(self):
        return (self.target,)

    def to_json(self):
        return {
            "op": self.OP,
            "target": self.target,
            "property": self.property,
            "from": self.from_value,
            "to": self.to_value,
        }

    @classmethod
    def from_json(cls, data, path):
        return cls(
            target=_require(data, "target", path, str),
            property=_require(data, "property", path, str),
            from_value=data.get("from"),
            to_value=data.get("to"),
        )

    def describe(self):
        return f"set property {self.target}.{self.property}"


OPERATIONS = {
    cls.OP: cls
    for cls in (AddFeature, RemoveFeature, SetParam, MoveFeature, AddDatum, EditSketch, SetProperty)
}


def operation_from_json(data, path="operations[]"):
    if not isinstance(data, dict):
        raise LayerFormatError("operation must be an object", path)
    op = _require(data, "op", path, str)
    cls = OPERATIONS.get(op)
    if cls is None:
        raise LayerFormatError(
            f"unknown operation {op!r}; known operations are {sorted(OPERATIONS)}. "
            "Refusing to skip it: replaying a layer with an operation dropped "
            "produces a model that looks right and is not.",
            path,
        )
    return cls.from_json(data, path)


# ---------------------------------------------------------------------------
# validation record
# ---------------------------------------------------------------------------


class Validation:
    """What a layer was checked against, and the stack it was checked on.

    ``evaluated_at`` names the revision *plus the layers beneath it* the
    numbers were produced against. A layer validated against a different stack
    than the one it is being merged into has stale validation, and the merge
    says so rather than trusting the numbers.
    """

    __slots__ = ("data",)

    #: Fields with a defined meaning; anything else is carried through as-is.
    KNOWN = (
        "recompute",
        "self_intersection",
        "min_wall_mm",
        "mass_g",
        "min_safety_factor",
        "contracts",
        "evaluated_at",
    )

    def __init__(self, data=None):
        self.data = dict(data or {})

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def evaluated_at(self):
        return self.data.get("evaluated_at")

    @property
    def metrics(self):
        """The numeric entries, which is what success criteria are checked against."""
        return {k: v for k, v in self.data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

    def is_stale_for(self, stack_revision):
        """True when this validation was produced against a different stack."""
        if not self.data:
            return True
        return self.evaluated_at != stack_revision

    def to_json(self):
        ordered = {k: self.data[k] for k in self.KNOWN if k in self.data}
        ordered.update({k: v for k, v in self.data.items() if k not in ordered})
        return ordered

    @classmethod
    def from_json(cls, data, path="validation"):
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise LayerFormatError("validation must be an object", path)
        return cls(data)

    def __repr__(self):
        return f"Validation({self.to_json()!r})"


# ---------------------------------------------------------------------------
# the layer
# ---------------------------------------------------------------------------


class Layer:
    """One deviation layer, as defined by SPEC §2."""

    def __init__(
        self,
        id,
        name="",
        author=None,
        created=None,
        base="",
        intent=None,
        claims=None,
        anchors=None,
        operations=(),
        pinned_touched=(),
        validation=None,
        schema=SCHEMA_VERSION,
        extras=None,
    ):
        if not isinstance(id, str) or not _ID_RE.match(id):
            raise LayerFormatError(
                f"layer id {id!r} must match {_ID_RE.pattern} — it is used as a file name", "id"
            )
        self.id = id
        self.schema = schema
        self.name = name
        self.author = author
        self.created = created
        self.base = base
        self.intent = intent
        self.claims = claims if claims is not None else Claims()
        self.anchors = dict(anchors or {})
        self.operations = list(operations)
        self.pinned_touched = list(pinned_touched)
        self.validation = validation if validation is not None else Validation()
        self.extras = dict(extras or {})
        self._check_anchor_refs()

    def _check_anchor_refs(self):
        """Every ``@name`` in an operation must name an anchor this layer declares."""
        for index, operation in enumerate(self.operations):
            for ref in operation.anchor_refs():
                if ref not in self.anchors:
                    raise LayerFormatError(
                        f"operation references anchor {ref!r}, which the layer does not declare",
                        f"operations[{index}]",
                    )

    # -- derived views --------------------------------------------------

    def targets(self):
        """Every feature-level target touched by any operation, in order."""
        seen = []
        for operation in self.operations:
            for target in operation.targets():
                if target not in seen:
                    seen.append(target)
        return seen

    def operations_by_target(self):
        grouped = {}
        for operation in self.operations:
            for target in operation.targets():
                grouped.setdefault(target, []).append(operation)
        return grouped

    def sketch_edits(self):
        return [op for op in self.operations if isinstance(op, EditSketch)]

    # -- serialisation --------------------------------------------------

    def to_json(self):
        data = {"id": self.id, "schema": self.schema}
        if self.name:
            data["name"] = self.name
        if self.author is not None:
            data["author"] = self.author.to_json()
        if self.created:
            data["created"] = self.created
        data["base"] = self.base
        if self.intent is not None:
            data["intent"] = self.intent.to_json()
        data["claims"] = self.claims.to_json()
        if self.anchors:
            data["anchors"] = {name: anchor.to_json() for name, anchor in self.anchors.items()}
        data["operations"] = [operation.to_json() for operation in self.operations]
        data["pinned_touched"] = list(self.pinned_touched)
        if self.validation.data:
            data["validation"] = self.validation.to_json()
        for key, value in self.extras.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_json(cls, data, path="layer"):
        if not isinstance(data, dict):
            raise LayerFormatError("layer must be an object", path)
        schema = data.get("schema", SCHEMA_VERSION)
        if not isinstance(schema, int):
            raise LayerFormatError("schema must be an integer", f"{path}.schema")
        if schema > SCHEMA_VERSION:
            raise LayerFormatError(
                f"layer uses schema {schema}, this build understands up to {SCHEMA_VERSION}",
                f"{path}.schema",
            )
        known = {
            "id",
            "schema",
            "name",
            "author",
            "created",
            "base",
            "intent",
            "claims",
            "anchors",
            "operations",
            "pinned_touched",
            "validation",
        }
        author = data.get("author")
        intent = data.get("intent")
        return cls(
            id=_require(data, "id", path, str),
            schema=schema,
            name=data.get("name", ""),
            author=Author.from_json(author, f"{path}.author") if author else None,
            created=data.get("created"),
            base=data.get("base", ""),
            intent=Intent.from_json(intent, f"{path}.intent") if intent else None,
            claims=Claims.from_json(data.get("claims", {}), f"{path}.claims"),
            anchors={
                name: Anchor.from_json(name, value, f"{path}.anchors.{name}")
                for name, value in (data.get("anchors") or {}).items()
            },
            operations=[
                operation_from_json(op, f"{path}.operations[{i}]")
                for i, op in enumerate(data.get("operations", []))
            ],
            pinned_touched=data.get("pinned_touched", []),
            validation=Validation.from_json(data.get("validation"), f"{path}.validation"),
            extras={k: v for k, v in data.items() if k not in known},
        )

    @classmethod
    def loads(cls, text, path="layer"):
        try:
            return cls.from_json(json.loads(text), path)
        except json.JSONDecodeError as exc:
            raise LayerFormatError(f"not valid JSON: {exc}", path) from None

    def dumps(self, indent=2):
        return json.dumps(self.to_json(), indent=indent, ensure_ascii=False) + "\n"

    def __repr__(self):
        return f"Layer({self.id!r}, {self.name!r}, {len(self.operations)} operations)"
