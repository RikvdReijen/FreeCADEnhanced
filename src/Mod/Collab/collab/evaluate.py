# SPDX-License-Identifier: LGPL-2.1-or-later
"""Evaluators: the thing that turns a feature tree back into geometry.

README §5 and §8.2 are blunt about this: geometric conflict detection is not
optional and not cheap, because it requires *evaluating* the merged model. It
also cannot be faked. So this module defines what an evaluator is, ships two
honest ones, and makes every consumer ask ``evaluator.can("metrics")`` before
trusting a number.

* :class:`StructuralEvaluator` — no geometry at all. It checks that the tree
  is well-formed (every dependency exists and precedes its dependant, no
  cycles) and says so. Everything geometric is reported as *not evaluated*,
  never as passed.
* :class:`ScriptedEvaluator` — geometry supplied by a callable. This is how
  the tests state "these two pockets together leave 0.4 mm of wall" as a fact
  and check that the merge notices; it is also how a project can plug in an
  external solver without touching this module.
* ``collab.freecad_adapter.FreeCADEvaluator`` — the real thing, when FreeCAD
  is importable.

The reporting rule throughout: a check that could not run is ``None``, not
``True``. Unknown is not passing.
"""

from .errors import EvaluationError

#: Every capability an evaluator may declare.
CAPABILITIES = frozenset({"recompute", "metrics", "self_intersection", "bounding_box", "interference"})


class GeometryIssue:
    """One geometric finding: a self-intersection, a wall below minimum, a clearance breach."""

    __slots__ = ("kind", "message", "features", "value")

    def __init__(self, kind, message, features=(), value=None):
        self.kind = kind
        self.message = message
        self.features = tuple(features)
        self.value = value

    def to_json(self):
        data = {"kind": self.kind, "message": self.message, "features": list(self.features)}
        if self.value is not None:
            data["value"] = self.value
        return data

    def __repr__(self):
        return f"GeometryIssue({self.kind}: {self.message})"


class RecomputeResult:
    __slots__ = ("ok", "errors", "warnings", "evaluated")

    def __init__(self, ok, errors=(), warnings=(), evaluated=True):
        self.ok = ok
        self.errors = list(errors)
        self.warnings = list(warnings)
        #: False when the evaluator could only check structure, not geometry.
        self.evaluated = evaluated

    @property
    def status(self):
        if not self.ok:
            return "failed"
        return "ok" if self.evaluated else "structure_ok"

    def to_json(self):
        return {"status": self.status, "errors": self.errors, "warnings": self.warnings}

    def __repr__(self):
        return f"RecomputeResult({self.status}, {len(self.errors)} errors)"


class Evaluator:
    """Base class. Subclasses declare ``capabilities`` and implement what they declare."""

    capabilities = frozenset()
    name = "evaluator"

    def can(self, capability):
        if capability not in CAPABILITIES:
            raise EvaluationError(f"unknown capability {capability!r}")
        return capability in self.capabilities

    def recompute(self, doc):
        raise NotImplementedError

    def metrics(self, doc):
        """Named numeric metrics (``mass_g``, ``min_wall_mm`` …), or ``{}``."""
        return {}

    def geometry_issues(self, doc):
        """Self-intersections, thin walls, etc. ``None`` when not evaluable."""
        return None

    def bounding_box(self, doc):
        """``((xmin, ymin, zmin), (xmax, ymax, zmax))`` or ``None``."""
        return None

    def interference(self, doc, keep_outs):
        """``[GeometryIssue]`` for every keep-out the document intrudes into, or ``None``."""
        return None

    def describe(self):
        return f"{self.name} (capabilities: {', '.join(sorted(self.capabilities)) or 'none'})"


def check_structure(doc):
    """Well-formedness of a feature tree. Returns ``(errors, warnings)``.

    * every ``depends_on`` names an existing feature
    * every dependency precedes its dependant — order is load-bearing
    * no dependency cycles
    * no duplicate names
    """
    errors, warnings = [], []
    seen = set()
    for feature in doc.features:
        if feature.name in seen:
            errors.append(f"duplicate feature name {feature.name!r}")
        seen.add(feature.name)

    positions = {f.name: i for i, f in enumerate(doc.features)}
    for feature in doc.features:
        for parent in feature.depends_on:
            if parent not in positions:
                errors.append(f"{feature.name} depends on {parent!r}, which does not exist")
            elif positions[parent] > positions[feature.name]:
                errors.append(f"{feature.name} depends on {parent}, which comes after it in the tree")

    # Cycle check: DFS over depends_on.
    state = {}

    def visit(name, path):
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            cycle = path[path.index(name) :] + [name]
            errors.append("dependency cycle: " + " -> ".join(cycle))
            return
        state[name] = "active"
        feature = doc.feature(name)
        if feature is not None:
            for parent in feature.depends_on:
                visit(parent, path + [name])
        state[name] = "done"

    for feature in doc.features:
        visit(feature.name, [])

    if not doc.features:
        warnings.append("document has no features")
    return errors, warnings


class StructuralEvaluator(Evaluator):
    """Checks the tree; evaluates no geometry; says so."""

    capabilities = frozenset({"recompute"})
    name = "structural"

    def recompute(self, doc):
        errors, warnings = check_structure(doc)
        return RecomputeResult(not errors, errors, warnings, evaluated=False)


class ScriptedEvaluator(Evaluator):
    """Geometry answered by callables.

    ``metrics_fn(doc) -> dict``, ``issues_fn(doc) -> [GeometryIssue]``,
    ``bbox_fn(doc) -> (min, max)``, ``interference_fn(doc, keep_outs) -> [GeometryIssue]``.
    Capabilities follow from which callables were supplied, so a scripted
    evaluator with no ``issues_fn`` is honest about not checking intersections.
    """

    name = "scripted"

    def __init__(self, metrics_fn=None, issues_fn=None, bbox_fn=None, interference_fn=None, recompute_fn=None):
        self._metrics = metrics_fn
        self._issues = issues_fn
        self._bbox = bbox_fn
        self._interference = interference_fn
        self._recompute = recompute_fn
        caps = {"recompute"}
        if metrics_fn:
            caps.add("metrics")
        if issues_fn:
            caps.add("self_intersection")
        if bbox_fn:
            caps.add("bounding_box")
        if interference_fn:
            caps.add("interference")
        self.capabilities = frozenset(caps)

    def recompute(self, doc):
        errors, warnings = check_structure(doc)
        if errors:
            return RecomputeResult(False, errors, warnings, evaluated=False)
        if self._recompute is not None:
            outcome = self._recompute(doc)
            if isinstance(outcome, RecomputeResult):
                return outcome
            if outcome is False or isinstance(outcome, str):
                return RecomputeResult(False, [outcome or "scripted recompute failed"], warnings)
        return RecomputeResult(True, [], warnings, evaluated=True)

    def metrics(self, doc):
        return dict(self._metrics(doc)) if self._metrics else {}

    def geometry_issues(self, doc):
        return list(self._issues(doc)) if self._issues else None

    def bounding_box(self, doc):
        return self._bbox(doc) if self._bbox else None

    def interference(self, doc, keep_outs):
        return list(self._interference(doc, keep_outs)) if self._interference else None


def default_evaluator():
    """The best evaluator available in this interpreter.

    FreeCAD when it can be imported, otherwise the structural evaluator — and
    the caller can tell which it got from ``.name`` and ``.capabilities``.
    """
    try:
        from .freecad_adapter import FreeCADEvaluator, freecad_available
    except ImportError:  # pragma: no cover - adapter always ships
        return StructuralEvaluator()
    if freecad_available():
        import FreeCAD

        doc = getattr(FreeCAD, "ActiveDocument", None)
        if doc is not None:
            return FreeCADEvaluator(doc)
    return StructuralEvaluator()
