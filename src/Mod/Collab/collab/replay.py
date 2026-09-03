# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replay: apply a layer's operations to a document model.

    replay(layer, base_document, evaluator) -> ReplayResult

A replay never raises for a modelling reason. Every way an operation can fail
to apply — an anchor that will not resolve, a parameter that somebody else
moved, a feature with dependants being removed — is recorded as a
:class:`ReplayFailure` and returned, because a failed replay is *input* to the
merge algorithm (SPEC §5 step 1), not an exception to it.

Anchors are resolved lazily, at the point an operation first uses them,
against the document *as it stands at that point*. That is what lets a layer
add a datum and anchor a later operation to it. Anchors the operations never
touch (declared for ``claims.depends``) are resolved against the base at the
end so the result reports every anchor.
"""

from . import targets
from .anchors import DEFAULT_OPTIONS, resolve
from .evaluate import StructuralEvaluator
from .model import Entity, Feature
from .schema import (
    AddDatum,
    AddFeature,
    EditSketch,
    MoveFeature,
    RemoveFeature,
    SetParam,
    SetProperty,
    anchor_ref,
)

#: Property names that move geometry. ``set_property`` may not touch these;
#: a geometric change disguised as a property edit would slip past the merge.
GEOMETRIC_PROPERTIES = frozenset(
    {
        "Length",
        "Length2",
        "Radius",
        "Radius1",
        "Radius2",
        "Angle",
        "Angle2",
        "Offset",
        "Depth",
        "Height",
        "Width",
        "Placement",
        "AttachmentOffset",
        "Midplane",
        "Reversed",
        "Size",
        "Thickness",
        "Occurrences",
        "Direction",
        "Support",
        "MapMode",
    }
)


def values_match(actual, expected):
    """Equality with a relative tolerance for numbers."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(actual - expected) <= 1e-9 * max(1.0, abs(expected))
    return actual == expected


class ReplayFailure:
    __slots__ = ("index", "operation", "kind", "message", "resolution")

    KINDS = (
        "anchor_lost",
        "anchor_ambiguous",
        "missing_target",
        "param_moved",
        "dependents_exist",
        "bad_position",
        "duplicate_name",
        "not_a_sketch",
        "geometric_property",
        "structure",
        "recompute",
    )

    def __init__(self, index, operation, kind, message, resolution=None):
        self.index = index
        self.operation = operation
        self.kind = kind
        self.message = message
        self.resolution = resolution

    def to_json(self):
        data = {
            "index": self.index,
            "operation": self.operation.describe() if self.operation is not None else None,
            "kind": self.kind,
            "message": self.message,
        }
        if self.resolution is not None:
            data["resolution"] = self.resolution.to_json()
        return data

    def __repr__(self):
        return f"ReplayFailure({self.kind} at #{self.index}: {self.message})"


class ReplayResult:
    def __init__(self, layer, doc):
        self.layer = layer
        self.doc = doc
        #: ``{anchor_name: Resolution}`` for every anchor the layer declares.
        self.resolutions = {}
        self.failures = []
        #: Indices of operations that were applied.
        self.applied = []
        self.recompute = None
        #: Targets that were modified, in order — fed to ``ClaimRegistry.notify_change``.
        self.changed = []
        #: Targets the layer touched that are pinned (needs escalation, not a failure).
        self.pinned_touched = []

    @property
    def ok(self):
        return not self.failures and (self.recompute is None or self.recompute.ok)

    def anchor_failures(self):
        return [f for f in self.failures if f.kind.startswith("anchor_")]

    def to_json(self):
        return {
            "layer": self.layer.id,
            "ok": self.ok,
            "applied": list(self.applied),
            "failures": [f.to_json() for f in self.failures],
            "resolutions": {k: v.to_json() for k, v in self.resolutions.items()},
            "recompute": self.recompute.to_json() if self.recompute else None,
            "changed": list(self.changed),
            "pinned_touched": list(self.pinned_touched),
            "revision": self.doc.revision,
        }

    def __repr__(self):
        return f"ReplayResult({self.layer.id!r}, ok={self.ok}, {len(self.failures)} failures)"


class _Replayer:
    def __init__(self, layer, doc, evaluator, options, pinned):
        self.layer = layer
        self.doc = doc
        self.evaluator = evaluator
        self.options = options
        self.pinned = pinned
        self.result = ReplayResult(layer, doc)

    # -- anchors --------------------------------------------------------

    def anchor(self, name, index, operation):
        """Resolve anchor ``name`` now, or record why it could not be."""
        if name in self.result.resolutions:
            resolution = self.result.resolutions[name]
        else:
            resolution = resolve(self.layer.anchors[name], self.doc, self.options)
            self.result.resolutions[name] = resolution
        if resolution.ok:
            return resolution
        kind = "anchor_lost" if resolution.status == "lost" else "anchor_ambiguous"
        was = f" (was {resolution.recorded_name})" if kind == "anchor_lost" and resolution.recorded_name else ""
        self.fail(index, operation, kind, f"anchor {name!r} is {resolution.status}{was}", resolution)
        return None

    def resolve_refs(self, value, index, operation):
        """Replace every ``@anchor`` inside ``value`` with its resolved entity name.

        Returns ``(value, owners, ok)`` where ``owners`` are the features the
        resolved entities belong to (they become dependencies).
        """
        owners = []
        ok = True

        def walk(item):
            nonlocal ok
            if isinstance(item, dict):
                return {k: walk(v) for k, v in item.items()}
            if isinstance(item, list):
                return [walk(v) for v in item]
            name = anchor_ref(item)
            if name is None:
                return item
            resolution = self.anchor(name, index, operation)
            if resolution is None:
                ok = False
                return item
            if resolution.owner not in owners:
                owners.append(resolution.owner)
            return resolution.name

        return walk(value), owners, ok

    # -- bookkeeping ----------------------------------------------------

    def fail(self, index, operation, kind, message, resolution=None):
        self.result.failures.append(ReplayFailure(index, operation, kind, message, resolution))

    def touched(self, target):
        if target not in self.result.changed:
            self.result.changed.append(target)
        if self.pinned is not None and self.pinned.is_pinned(target):
            if target not in self.result.pinned_touched:
                self.result.pinned_touched.append(target)

    def feature_for(self, target, index, operation):
        name, prop = targets.split(target, self.doc)
        if name is None and not targets.is_param(target):
            self.fail(index, operation, "missing_target", f"no feature matches target {target!r}")
            return None, prop
        return name, prop

    # -- operations -----------------------------------------------------

    def apply(self, index, operation):
        handler = getattr(self, "op_" + operation.OP)
        return handler(index, operation)

    def _check_position(self, index, operation, after, name):
        if after is None:
            return True
        if after == name:
            self.fail(index, operation, "bad_position", f"{name} cannot be placed after itself")
            return False
        if not self.doc.has_feature(after):
            self.fail(index, operation, "bad_position", f"cannot insert after {after!r}: no such feature")
            return False
        return True

    def op_add_feature(self, index, op):
        if self.doc.has_feature(op.name):
            self.fail(index, op, "duplicate_name", f"a feature named {op.name!r} already exists")
            return False
        if not self._check_position(index, op, op.after, op.name):
            return False
        params, owners, ok = self.resolve_refs(op.params, index, op)
        sketch, sketch_owners, sketch_ok = self.resolve_refs(op.sketch, index, op)
        if not (ok and sketch_ok):
            return False
        if sketch:
            params = dict(params)
            params["sketch"] = sketch
        depends = list(op.depends_on)
        for owner in ([op.after] if op.after else []) + owners + sketch_owners:
            if owner and owner not in depends and owner != op.name:
                depends.append(owner)
        self.doc.insert_after(Feature(op.name, op.kind, params=params, depends_on=depends), op.after)
        self.touched(op.name)
        return True

    def op_add_datum(self, index, op):
        if self.doc.has_feature(op.name):
            self.fail(index, op, "duplicate_name", f"a feature named {op.name!r} already exists")
            return False
        if not self._check_position(index, op, op.after, op.name):
            return False
        placement, owners, ok = self.resolve_refs(op.placement, index, op)
        if not ok:
            return False
        depends = list(op.depends_on)
        for owner in ([op.after] if op.after else []) + owners:
            if owner and owner not in depends:
                depends.append(owner)
        kind = op.kind if op.kind.startswith("Datum") else "Datum" + op.kind
        self.doc.insert_after(Feature(op.name, kind, params=placement, depends_on=depends), op.after)
        # A datum exposes one entity, named after itself: stable by construction.
        normal = placement.get("normal") if isinstance(placement, dict) else None
        origin = placement.get("origin", (0.0, 0.0, 0.0)) if isinstance(placement, dict) else (0.0, 0.0, 0.0)
        entity_kind = "face" if "Plane" in kind else "edge" if "Line" in kind else "vertex"
        self.doc.entities.append(
            Entity(op.name, entity_kind, op.name, "datum", normal=normal, centroid_local=origin)
        )
        self.touched(op.name)
        return True

    def op_remove_feature(self, index, op):
        name, prop = self.feature_for(op.target, index, op)
        if name is None:
            return False
        if prop:
            self.fail(index, op, "missing_target", f"remove_feature target {op.target!r} names a property, not a feature")
            return False
        dependents = self.doc.dependents_of(name)
        if dependents:
            self.fail(
                index,
                op,
                "dependents_exist",
                f"cannot remove {name}: {', '.join(dependents)} depend{'s' if len(dependents) == 1 else ''} on it",
            )
            return False
        self.doc.remove_feature(name)
        self.touched(op.target)
        return True

    def op_move_feature(self, index, op):
        name, prop = self.feature_for(op.target, index, op)
        if name is None or prop:
            if prop:
                self.fail(index, op, "missing_target", f"move_feature target {op.target!r} is not a feature")
            return False
        if not self._check_position(index, op, op.after, name):
            return False
        if op.after is not None and op.after in self.doc.dependents_of(name):
            self.fail(index, op, "bad_position", f"cannot move {name} after {op.after}, which depends on it")
            return False
        self.doc.move_feature(name, op.after)
        self.touched(op.target)
        return True

    def op_set_param(self, index, op):
        if targets.is_param(op.target):
            key = targets.param_name(op.target)
            if key not in self.doc.parameters:
                self.fail(index, op, "missing_target", f"no document parameter {key!r}")
                return False
            current = self.doc.parameters[key]
            if not values_match(current, op.from_value):
                self.fail(
                    index,
                    op,
                    "param_moved",
                    f"{op.target} is {current!r}, but the layer recorded it as {op.from_value!r}: "
                    "someone else moved it",
                )
                return False
            self.doc.parameters[key] = op.to_value
            self.touched(op.target)
            return True

        name, prop = self.feature_for(op.target, index, op)
        if name is None:
            return False
        feature = self.doc.feature(name)
        if not prop:
            self.fail(index, op, "missing_target", f"set_param target {op.target!r} names no parameter")
            return False
        if prop not in feature.params:
            self.fail(index, op, "missing_target", f"{name} has no parameter {prop!r}")
            return False
        current = feature.params[prop]
        if not values_match(current, op.from_value):
            self.fail(
                index,
                op,
                "param_moved",
                f"{op.target} is {current!r}, but the layer recorded it as {op.from_value!r}: "
                "someone else moved it",
            )
            return False
        feature.params[prop] = op.to_value
        self.touched(op.target)
        return True

    def op_edit_sketch(self, index, op):
        name, prop = self.feature_for(op.target, index, op)
        if name is None:
            return False
        feature = self.doc.feature(name)
        if "Sketch" not in feature.kind:
            self.fail(index, op, "not_a_sketch", f"{name} is a {feature.kind}, not a sketch")
            return False
        geometry, owners, ok = self.resolve_refs(op.geometry, index, op)
        constraints, _, ok2 = self.resolve_refs(op.constraints, index, op)
        if not (ok and ok2):
            return False
        edits = feature.params.setdefault("edits", [])
        edits.append({"layer": self.layer.id, "geometry": geometry, "constraints": constraints})
        for owner in owners:
            if owner not in feature.depends_on and owner != name:
                feature.depends_on = feature.depends_on + (owner,)
        self.touched(op.target)
        return True

    def op_set_property(self, index, op):
        if op.property in GEOMETRIC_PROPERTIES:
            self.fail(
                index,
                op,
                "geometric_property",
                f"{op.property!r} moves geometry; use set_param so the change is merge-visible",
            )
            return False
        name, prop = self.feature_for(op.target, index, op)
        if name is None:
            return False
        feature = self.doc.feature(name)
        current = feature.properties.get(op.property)
        if op.from_value is not None and current is not None and not values_match(current, op.from_value):
            self.fail(
                index,
                op,
                "param_moved",
                f"{name}.{op.property} is {current!r}, layer recorded {op.from_value!r}",
            )
            return False
        feature.properties[op.property] = op.to_value
        self.touched(f"{op.target}.{op.property}")
        return True

    # -- driver ---------------------------------------------------------

    def run(self, stop_on_failure):
        for index, operation in enumerate(self.layer.operations):
            before = len(self.result.failures)
            applied = self.apply(index, operation)
            if applied:
                self.result.applied.append(index)
            if len(self.result.failures) > before and stop_on_failure:
                break
        # Report every declared anchor, even ones no operation used.
        for name, anchor in self.layer.anchors.items():
            if name not in self.result.resolutions:
                self.result.resolutions[name] = resolve(anchor, self.doc, self.options)
        self.doc.revision = f"{self.doc.revision}+{self.layer.id}" if self.doc.revision else self.layer.id
        if not self.result.failures or not stop_on_failure:
            self.result.recompute = self.evaluator.recompute(self.doc)
            if not self.result.recompute.ok:
                for error in self.result.recompute.errors:
                    self.fail(None, None, "recompute", error)
        return self.result


def replay(layer, base_doc, evaluator=None, options=DEFAULT_OPTIONS, stop_on_failure=True, pinned=None):
    """Apply ``layer`` to a clone of ``base_doc``.

    ``pinned`` is a :class:`collab.contracts.ContractSet` (or anything with
    ``is_pinned``); touched pinned targets are reported, not refused — refusal
    is the merge's job, replay only has to be honest about what happened.
    """
    doc = base_doc.clone()
    evaluator = evaluator or StructuralEvaluator()
    return _Replayer(layer, doc, evaluator, options, pinned).run(stop_on_failure)


def replay_stack(layers, base_doc, evaluator=None, options=DEFAULT_OPTIONS, pinned=None):
    """Replay several layers in order, each on the result of the last.

    Returns ``(doc, results)``. Stops at the first layer that fails; the
    returned document is the last good state.
    """
    doc = base_doc
    results = []
    for layer in layers:
        result = replay(layer, doc, evaluator, options, pinned=pinned)
        results.append(result)
        if not result.ok:
            break
        doc = result.doc
    return doc, results


def rebase(layer, new_base_doc, evaluator=None, options=DEFAULT_OPTIONS):
    """Re-record a layer against a different base.

    Replays it there; if that succeeds, returns a copy whose ``base`` is the
    new revision and whose anchors carry fresh fingerprints and
    ``resolved_at_record`` values. Validation is dropped, because it was
    produced against the old stack and SPEC §2 says stale numbers must not be
    carried forward as if they were current.

    Returns ``(layer_or_None, replay_result)``.
    """
    import copy

    from .schema import Fingerprint, Validation

    result = replay(layer, new_base_doc, evaluator, options)
    if not result.ok:
        return None, result
    rebased = copy.deepcopy(layer)
    rebased.base = new_base_doc.revision
    rebased.validation = Validation()
    for name, resolution in result.resolutions.items():
        anchor = rebased.anchors[name]
        if resolution.ok and resolution.entity is not None:
            anchor.fingerprint = Fingerprint.of(resolution.entity)
            anchor.resolved_at_record = resolution.entity.name
    return rebased, result
