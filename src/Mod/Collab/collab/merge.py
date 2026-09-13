# SPDX-License-Identifier: LGPL-2.1-or-later
"""Merging two layers against a common base (SPEC §5).

    merge(base, left, right) -> MergeResult

1. Replay ``left`` and ``right`` against ``base`` independently. Either
   failing to replay is a conflict before any comparison happens.
2. Every anchor in both is resolved during replay; ``Ambiguous`` or ``Lost``
   is a reference conflict.
3. Partition operations by dependency subtree. Disjoint subtrees concatenate.
4. Overlapping subtrees are compared per target:
   same target, different ``to``, same ``from``   -> parametric conflict
   same target, different ``from``                 -> someone else moved it
   same ``after`` position                         -> order conflict
   two ``edit_sketch`` on one sketch               -> refused (level 4)
5. Evaluate the combined stack. A geometric issue neither layer had alone is a
   geometric conflict. Contract violations are checked here too.
6. Re-run both layers' ``success_criteria`` on the merged result. A criterion
   that passed alone and fails together is the most valuable thing this
   format produces, and it is reported as a geometric conflict because it
   had to be *computed*.

Two things are never auto-resolved: parametric and intent conflicts are
presented with both sides' stated reasons for a human to pick, and a layer
touching a pinned parameter escalates regardless of how clean the merge is.

Whether step 5 actually evaluated geometry depends on the evaluator. With
the structural evaluator it did not, and ``MergeResult.geometry_evaluated``
is ``False`` so nobody mistakes "not checked" for "passed".
"""

import copy

from . import targets
from .anchors import DEFAULT_OPTIONS
from .evaluate import StructuralEvaluator
from .replay import replay
from .schema import (
    AddDatum,
    AddFeature,
    Author,
    Claims,
    EditSketch,
    Intent,
    Layer,
    MoveFeature,
    RemoveFeature,
    SetParam,
    SetProperty,
    Validation,
)

CONFLICT_CLASSES = ("reference", "order", "parametric", "geometric", "intent")

#: How a replay failure maps onto a conflict class.
_FAILURE_CLASS = {
    "anchor_lost": "reference",
    "anchor_ambiguous": "reference",
    "missing_target": "reference",
    "dependents_exist": "reference",
    "bad_position": "reference",
    "duplicate_name": "reference",
    "not_a_sketch": "reference",
    "geometric_property": "reference",
    "structure": "reference",
    "param_moved": "parametric",
    "recompute": "geometric",
}

#: Suggested handling per class, from README §5.
HANDLING = {
    "reference": "re-anchor; candidates are offered",
    "order": "choose an order; the result of each is shown",
    "parametric": "human picks; both values are shown with their stated intents",
    "geometric": "computed from the merged model; adjust one side",
    "intent": "human decision; not auto-resolved",
}


class Conflict:
    __slots__ = ("cls", "kind", "message", "layers", "target", "detail")

    def __init__(self, cls, kind, message, layers=(), target=None, detail=None):
        if cls not in CONFLICT_CLASSES:
            raise ValueError(f"unknown conflict class {cls!r}")
        self.cls = cls
        self.kind = kind
        self.message = message
        self.layers = tuple(layers)
        self.target = target
        self.detail = dict(detail or {})

    @property
    def handling(self):
        return HANDLING[self.cls]

    def to_json(self):
        data = {
            "class": self.cls,
            "kind": self.kind,
            "message": self.message,
            "layers": list(self.layers),
            "handling": self.handling,
        }
        if self.target is not None:
            data["target"] = self.target
        if self.detail:
            data["detail"] = self.detail
        return data

    def __repr__(self):
        return f"Conflict({self.cls}/{self.kind}: {self.message})"


class CriterionOutcome:
    """One success criterion, checked alone and checked on the merged result."""

    __slots__ = ("layer", "criterion", "alone", "actual_alone", "together", "actual_together")

    def __init__(self, layer, criterion, alone, actual_alone, together, actual_together):
        self.layer = layer
        self.criterion = criterion
        self.alone = alone
        self.actual_alone = actual_alone
        self.together = together
        self.actual_together = actual_together

    @property
    def regressed(self):
        return self.alone is True and self.together is False

    @property
    def unknown(self):
        return self.together is None

    def to_json(self):
        return {
            "layer": self.layer,
            "criterion": self.criterion.describe(),
            "alone": self.alone,
            "actual_alone": self.actual_alone,
            "together": self.together,
            "actual_together": self.actual_together,
            "regressed": self.regressed,
        }

    def __repr__(self):
        return f"CriterionOutcome({self.layer}: {self.criterion.describe()} alone={self.alone} together={self.together})"


class MergeResult:
    def __init__(self, base, left, right, evaluator):
        self.base = base
        self.left = left
        self.right = right
        self.evaluator = evaluator
        self.merged = None
        self.merged_doc = None
        self.conflicts = []
        self.escalations = []
        self.warnings = []
        self.left_replay = None
        self.right_replay = None
        self.combined_replay = None
        self.order = None
        self.interactions = []
        self.metrics = {}
        self.geometry_issues = None
        self.contract_violations = []
        self.criteria = []

    @property
    def geometry_evaluated(self):
        return self.combined_replay is not None and bool(self.combined_replay.recompute) and self.combined_replay.recompute.evaluated

    @property
    def ok(self):
        return self.merged is not None and not self.conflicts and not self.escalations

    @property
    def disjoint(self):
        return not self.interactions

    def conflicts_of(self, cls):
        return [c for c in self.conflicts if c.cls == cls]

    def to_json(self):
        return {
            "ok": self.ok,
            "base": self.base.revision,
            "left": self.left.id,
            "right": self.right.id,
            "merged": self.merged.to_json() if self.merged else None,
            "order": self.order,
            "disjoint": self.disjoint,
            "interactions": list(self.interactions),
            "conflicts": [c.to_json() for c in self.conflicts],
            "escalations": list(self.escalations),
            "warnings": list(self.warnings),
            "geometry_evaluated": self.geometry_evaluated,
            "evaluator": self.evaluator.describe(),
            "metrics": dict(self.metrics),
            "geometry_issues": None if self.geometry_issues is None else [i.to_json() for i in self.geometry_issues],
            "contract_violations": [v.to_json() for v in self.contract_violations],
            "criteria": [c.to_json() for c in self.criteria],
        }

    def summary(self):
        lines = [f"merge {self.left.id} + {self.right.id} on {self.base.revision or '(unversioned)'}: "
                 + ("OK" if self.ok else "NOT MERGEABLE")]
        lines.append(f"  order: {' then '.join(self.order) if self.order else 'n/a'}; "
                     f"{'disjoint' if self.disjoint else f'{len(self.interactions)} interaction(s)'}")
        lines.append(f"  geometry: {'evaluated' if self.geometry_evaluated else 'NOT evaluated'} ({self.evaluator.describe()})")
        for conflict in self.conflicts:
            lines.append(f"  conflict [{conflict.cls}/{conflict.kind}] {conflict.message}")
            lines.append(f"      -> {conflict.handling}")
        for escalation in self.escalations:
            lines.append(f"  escalate: {escalation}")
        for outcome in self.criteria:
            state = "REGRESSED" if outcome.regressed else ("unknown" if outcome.unknown else "ok")
            lines.append(f"  criterion {outcome.layer}: {outcome.criterion.describe()} — {state}"
                         + (f" (alone {outcome.actual_alone!r}, together {outcome.actual_together!r})"
                            if outcome.actual_together is not None else ""))
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        return "\n".join(lines)

    def __repr__(self):
        return f"MergeResult({self.left.id}+{self.right.id}, ok={self.ok}, {len(self.conflicts)} conflicts)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _footprint(operation, layer, doc, replay_result):
    """Features an operation touches, as names on the base document.

    New features are attributed to what they attach to (``after`` and the
    owners of their anchors), since they do not exist on the base.
    """
    names = set()
    for target in operation.targets():
        feature, _ = targets.split(target, doc)
        if feature is not None:
            names.add(feature)
    for target in operation.reads():
        feature, _ = targets.split(target, doc)
        if feature is not None:
            names.add(feature)
    for anchor in operation.anchor_refs():
        resolution = replay_result.resolutions.get(anchor)
        if resolution is not None and resolution.ok and resolution.owner:
            names.add(resolution.owner)
    return names


def _related(a, b, doc):
    """How feature ``a`` relates to ``b`` on ``doc``: same, ancestor, dependent, or None."""
    if a == b:
        return "same"
    if a in doc.ancestors_of(b):
        return "ancestor"
    if a in doc.dependents_of(b):
        return "dependent"
    return None


def _sketch_of(operation, doc):
    """The sketch an ``edit_sketch`` operates on, or None."""
    if not isinstance(operation, EditSketch):
        return None
    feature, _ = targets.split(operation.target, doc)
    return feature or operation.target


def _new_names(layer):
    return {op.name for op in layer.operations if isinstance(op, (AddFeature, AddDatum))}


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def _replay_conflicts(result, layer_id):
    conflicts = []
    for failure in result.failures:
        cls = _FAILURE_CLASS.get(failure.kind, "reference")
        detail = {"failure": failure.kind}
        if failure.resolution is not None:
            detail["resolution"] = failure.resolution.to_json()
        conflicts.append(
            Conflict(
                cls,
                "replay:" + failure.kind,
                f"{layer_id}: {failure.message}",
                layers=(layer_id,),
                target=(failure.operation.targets() or (None,))[0] if failure.operation else None,
                detail=detail,
            )
        )
    return conflicts


def _compare(left, right, base, left_replay, right_replay, result):
    """Steps 3 and 4: partition and per-target comparison."""
    conflicts = []
    left_new, right_new = _new_names(left), _new_names(right)

    # Two layers creating the same feature name cannot both apply.
    for name in sorted(left_new & right_new):
        conflicts.append(
            Conflict(
                "reference",
                "name_collision",
                f"both layers create a feature named {name!r}",
                layers=(left.id, right.id),
                target=name,
            )
        )

    for li, lop in enumerate(left.operations):
        lfoot = _footprint(lop, left, base, left_replay)
        for ri, rop in enumerate(right.operations):
            rfoot = _footprint(rop, right, base, right_replay)
            relation = None
            for a in lfoot:
                for b in rfoot:
                    relation = _related(a, b, base)
                    if relation:
                        break
                if relation:
                    break
            if relation is None:
                continue
            result.interactions.append(
                {"left": li, "right": ri, "relation": relation, "left_op": lop.describe(), "right_op": rop.describe()}
            )

            # -- same parameter ----------------------------------------
            if isinstance(lop, SetParam) and isinstance(rop, SetParam) and lop.target == rop.target:
                if not _same(lop.from_value, rop.from_value):
                    conflicts.append(
                        Conflict(
                            "parametric",
                            "moved",
                            f"{lop.target}: {left.id} recorded it as {lop.from_value!r}, "
                            f"{right.id} as {rop.from_value!r} — someone else moved it",
                            layers=(left.id, right.id),
                            target=lop.target,
                            detail=_sides(left, lop, right, rop),
                        )
                    )
                elif not _same(lop.to_value, rop.to_value):
                    conflicts.append(
                        Conflict(
                            "parametric",
                            "value",
                            f"{lop.target}: {left.id} sets {lop.to_value!r}, {right.id} sets {rop.to_value!r}",
                            layers=(left.id, right.id),
                            target=lop.target,
                            detail=_sides(left, lop, right, rop),
                        )
                    )
                else:
                    result.warnings.append(f"{lop.target}: both layers set the same value; kept once")

            # -- same property -----------------------------------------
            if (
                isinstance(lop, SetProperty)
                and isinstance(rop, SetProperty)
                and lop.target == rop.target
                and lop.property == rop.property
                and not _same(lop.to_value, rop.to_value)
            ):
                conflicts.append(
                    Conflict(
                        "parametric",
                        "property",
                        f"{lop.target}.{lop.property}: {left.id} sets {lop.to_value!r}, "
                        f"{right.id} sets {rop.to_value!r}",
                        layers=(left.id, right.id),
                        target=f"{lop.target}.{lop.property}",
                        detail=_sides(left, lop, right, rop),
                    )
                )

            # -- same insertion position -------------------------------
            lpos, rpos = lop.position(), rop.position()
            if lpos is not None and lpos == rpos:
                conflicts.append(
                    Conflict(
                        "order",
                        "position",
                        f"both layers insert after {lpos!r}: {left.id} ({lop.describe()}) and "
                        f"{right.id} ({rop.describe()})",
                        layers=(left.id, right.id),
                        target=lpos,
                        detail={
                            "options": [
                                f"{left.id} then {right.id}: {lop.describe()}, then {rop.describe()}",
                                f"{right.id} then {left.id}: {rop.describe()}, then {lop.describe()}",
                            ]
                        },
                    )
                )

            # -- two sketch edits on one sketch: refused ---------------
            ls, rs = _sketch_of(lop, base), _sketch_of(rop, base)
            if ls is not None and ls == rs:
                conflicts.append(
                    Conflict(
                        "intent",
                        "sketch",
                        f"both layers edit sketch {ls!r}; constraint systems are not merged "
                        "(README §2, level 4) — serialise the work",
                        layers=(left.id, right.id),
                        target=ls,
                        detail=_sides(left, lop, right, rop),
                    )
                )

            # -- one removes what the other builds on -------------------
            for remover, other, rlayer, olayer in ((lop, rop, left, right), (rop, lop, right, left)):
                if isinstance(remover, RemoveFeature):
                    removed, _ = targets.split(remover.target, base)
                    other_foot = rfoot if remover is lop else lfoot
                    if removed in other_foot or removed in {
                        d for f in other_foot for d in base.ancestors_of(f)
                    }:
                        conflicts.append(
                            Conflict(
                                "reference",
                                "removed_dependency",
                                f"{rlayer.id} removes {removed!r}, which {olayer.id} builds on "
                                f"({other.describe()})",
                                layers=(left.id, right.id),
                                target=removed,
                            )
                        )

            # -- one moves a feature the other's insertion is relative to
            for mover, other, mlayer, olayer in ((lop, rop, left, right), (rop, lop, right, left)):
                if isinstance(mover, MoveFeature) and isinstance(other, (AddFeature, AddDatum)):
                    moved, _ = targets.split(mover.target, base)
                    if other.after == moved:
                        conflicts.append(
                            Conflict(
                                "order",
                                "moved_anchor_position",
                                f"{mlayer.id} moves {moved!r}; {olayer.id} inserts {other.name!r} after it",
                                layers=(left.id, right.id),
                                target=moved,
                            )
                        )
    return _dedupe(conflicts)


def _same(a, b):
    from .replay import values_match

    return values_match(a, b)


def _sides(left, lop, right, rop):
    return {
        "left": {"layer": left.id, "operation": lop.to_json(), "intent": left.intent.goal if left.intent else None},
        "right": {"layer": right.id, "operation": rop.to_json(), "intent": right.intent.goal if right.intent else None},
    }


def _dedupe(conflicts):
    seen, out = set(), []
    for conflict in conflicts:
        key = (conflict.cls, conflict.kind, conflict.target, conflict.message)
        if key not in seen:
            seen.add(key)
            out.append(conflict)
    return out


def _combine(base, left, right, evaluator, options, pinned):
    """Build the merged layer and replay it. Tries both concatenation orders."""
    anchors = {}
    for layer in (left, right):
        for name, anchor in layer.anchors.items():
            if name in anchors and anchors[name].to_json() != anchor.to_json():
                return None, None, None, Conflict(
                    "reference",
                    "anchor_collision",
                    f"both layers declare an anchor named {name!r} with different definitions",
                    layers=(left.id, right.id),
                    target=name,
                )
            anchors[name] = copy.deepcopy(anchor)

    attempts = []
    for first, second in ((left, right), (right, left)):
        merged = _merged_layer(base, first, second, anchors)
        result = replay(merged, base, evaluator, options, stop_on_failure=True, pinned=pinned)
        attempts.append(([first.id, second.id], merged, result))
        if result.ok:
            return [first.id, second.id], merged, result, None

    # Neither order replays. Report the failure of the natural order.
    order, merged, result = attempts[0]
    return order, merged, result, None


def _merged_layer(base, first, second, anchors):
    ops = [copy.deepcopy(op) for op in first.operations] + [copy.deepcopy(op) for op in second.operations]
    # Drop exact duplicate set_param operations (both sides made the same change).
    seen, unique = set(), []
    for op in ops:
        key = None
        if isinstance(op, SetParam):
            key = ("set_param", op.target, repr(op.from_value), repr(op.to_value))
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        unique.append(op)

    sponsor = None
    for layer in (first, second):
        if layer.author is not None:
            sponsor = layer.author.human_sponsor or (layer.author.id if layer.author.kind == "human" else None)
            if sponsor:
                break
    criteria = []
    for layer in (first, second):
        if layer.intent is not None:
            for criterion in layer.intent.success_criteria:
                if all(c.to_json() != criterion.to_json() for c in criteria):
                    criteria.append(copy.deepcopy(criterion))
    goals = [layer.intent.goal for layer in (first, second) if layer.intent is not None]
    modifies = []
    for layer in (first, second):
        for target in layer.claims.modifies:
            if target not in modifies:
                modifies.append(target)
    depends = []
    for layer in (first, second):
        for dep in layer.claims.depends:
            if all(d.key != dep.key for d in depends):
                depends.append(copy.deepcopy(dep))
    pinned = []
    for layer in (first, second):
        for target in layer.pinned_touched:
            if target not in pinned:
                pinned.append(target)

    return Layer(
        id=f"{first.id}+{second.id}",
        name=f"{first.name or first.id} + {second.name or second.id}",
        author=Author("agent", "collab.merge", human_sponsor=sponsor or "unknown"),
        base=base.revision,
        intent=Intent(goal="; ".join(goals) if goals else "merge", success_criteria=criteria),
        claims=Claims(modifies=modifies, depends=depends, mode="advisory"),
        anchors=anchors,
        operations=unique,
        pinned_touched=pinned,
        validation=Validation(),
    )


def _evaluate(result, base, left, right, evaluator, contracts, part):
    """Steps 5 and 6 on the combined replay."""
    combined = result.combined_replay
    doc = combined.doc
    if not evaluator.can("metrics"):
        result.warnings.append(
            f"geometric conflicts not evaluated: evaluator '{evaluator.name}' has no geometry; "
            "use collab.freecad_adapter.FreeCADEvaluator inside FreeCAD"
        )
    alone_metrics = {}
    for layer, rep in ((left, result.left_replay), (right, result.right_replay)):
        alone_metrics[layer.id] = evaluator.metrics(rep.doc) if evaluator.can("metrics") else {}
    result.metrics = evaluator.metrics(doc) if evaluator.can("metrics") else {}

    # Geometric issues neither layer had alone.
    if evaluator.can("self_intersection"):
        together = evaluator.geometry_issues(doc) or []
        result.geometry_issues = together
        alone = set()
        for rep in (result.left_replay, result.right_replay):
            for issue in evaluator.geometry_issues(rep.doc) or []:
                alone.add((issue.kind, issue.message))
        for issue in together:
            if (issue.kind, issue.message) not in alone:
                result.conflicts.append(
                    Conflict(
                        "geometric",
                        issue.kind,
                        f"combined result: {issue.message} (neither layer had this alone)",
                        layers=(left.id, right.id),
                        target=issue.features[0] if issue.features else None,
                        detail=issue.to_json(),
                    )
                )

    # Contracts.
    if contracts is not None:
        bbox = evaluator.bounding_box(doc) if evaluator.can("bounding_box") else None
        violations, skipped = contracts.check(part, result.metrics, bbox, evaluator, doc)
        result.contract_violations = violations
        for violation in violations:
            result.conflicts.append(
                Conflict(
                    "geometric",
                    "contract:" + violation.rule,
                    f"contract {violation.part}: {violation.message}",
                    layers=(left.id, right.id),
                    detail=violation.to_json(),
                )
            )
        for note in skipped:
            result.warnings.append(f"contract {part}: {note}")

    # Success criteria, alone and together.
    for layer in (left, right):
        if layer.intent is None:
            continue
        for criterion in layer.intent.success_criteria:
            alone_ok, alone_val = criterion.check(alone_metrics[layer.id])
            together_ok, together_val = criterion.check(result.metrics)
            outcome = CriterionOutcome(layer.id, criterion, alone_ok, alone_val, together_ok, together_val)
            result.criteria.append(outcome)
            if outcome.regressed:
                result.conflicts.append(
                    Conflict(
                        "geometric",
                        "criteria_regression",
                        f"{layer.id}: '{criterion.describe()}' held alone ({alone_val!r}) "
                        f"and fails on the merged result ({together_val!r})",
                        layers=(left.id, right.id),
                        target=criterion.metric,
                        detail=outcome.to_json(),
                    )
                )
            elif together_ok is False and alone_ok is False:
                result.warnings.append(
                    f"{layer.id}: '{criterion.describe()}' already failed alone ({alone_val!r})"
                )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def merge(base, left, right, evaluator=None, contracts=None, part=None, options=DEFAULT_OPTIONS):
    """Merge two layers recorded against ``base``.

    ``contracts`` is a :class:`collab.contracts.ContractSet`; ``part`` names
    the contract to check the merged document against (defaults to the
    document name without extension). Pinned parameters come from the same
    contract set.
    """
    evaluator = evaluator or StructuralEvaluator()
    result = MergeResult(base, left, right, evaluator)
    part = part or _part_name(base)

    for layer in (left, right):
        if layer.base and base.revision and layer.base != base.revision:
            result.warnings.append(
                f"{layer.id} was recorded against {layer.base!r}, merging on {base.revision!r}"
            )
        if layer.validation.is_stale_for(base.revision) and layer.validation.data:
            result.warnings.append(
                f"{layer.id}: validation is stale (evaluated at {layer.validation.evaluated_at!r}, "
                f"base is {base.revision!r}); its numbers are not trusted"
            )

    # 1 + 2: independent replay, anchors resolved.
    result.left_replay = replay(left, base, evaluator, options, stop_on_failure=False, pinned=contracts)
    result.right_replay = replay(right, base, evaluator, options, stop_on_failure=False, pinned=contracts)
    result.conflicts.extend(_replay_conflicts(result.left_replay, left.id))
    result.conflicts.extend(_replay_conflicts(result.right_replay, right.id))
    if result.conflicts:
        return result

    # 3 + 4: partition and compare.
    result.conflicts.extend(_compare(left, right, base, result.left_replay, result.right_replay, result))
    if result.conflicts:
        return result

    # Combine.
    order, merged, combined, conflict = _combine(base, left, right, evaluator, options, contracts)
    if conflict is not None:
        result.conflicts.append(conflict)
        return result
    result.combined_replay = combined
    result.order = order
    if not combined.ok:
        result.conflicts.extend(_replay_conflicts(combined, merged.id))
        return result
    result.merged = merged
    result.merged_doc = combined.doc

    # Pinned parameters escalate, always.
    touched = list(combined.pinned_touched)
    if contracts is not None:
        for layer in (left, right):
            for target in contracts.pinned_touched_by(layer):
                if target not in touched:
                    touched.append(target)
    for target in touched:
        result.escalations.append(f"{target} is pinned; a human must approve this merge")
    merged.pinned_touched = touched

    # 5 + 6.
    _evaluate(result, base, left, right, evaluator, contracts, part)
    merged.validation = Validation(
        {
            "recompute": combined.recompute.status,
            "self_intersection": (
                "none" if result.geometry_issues == [] else ("found" if result.geometry_issues else "not_evaluated")
            ),
            "contracts": (
                "pass" if contracts is not None and not result.contract_violations
                else ("fail" if result.contract_violations else "not_checked")
            ),
            "evaluated_at": combined.doc.revision,
            **result.metrics,
        }
    )
    return result


def _part_name(doc):
    import os

    name = doc.document or ""
    return os.path.splitext(os.path.basename(name))[0] if name else ""
