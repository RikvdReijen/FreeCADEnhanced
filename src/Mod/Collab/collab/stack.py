# SPDX-License-Identifier: LGPL-2.1-or-later
"""Evaluating a layer stack: muting, reordering and the geometric diff.

README §3: the document is ``evaluate(base) + Σ apply(layer_i)`` for enabled
layers, in order. Turning a layer off shows the model without it with the
work still there; moving a layer re-evaluates with the new order. Both are
just index edits followed by a call to :func:`evaluate_stack`.

README §7: reviewing a CAD change as a text diff is useless. The review
surface is mass-properties deltas, envelope deltas and the layer's stated
intent next to what it actually did. :func:`geometric_diff` produces that,
to the extent the evaluator can measure it — and says which parts it could
not.
"""

from .anchors import DEFAULT_OPTIONS
from .evaluate import StructuralEvaluator
from .replay import replay


class StackResult:
    def __init__(self, base, index):
        self.base = base
        self.index = index
        self.doc = base
        self.results = []
        self.skipped = []

    @property
    def ok(self):
        return all(r.ok for r in self.results)

    @property
    def failed(self):
        return next((r for r in self.results if not r.ok), None)

    @property
    def revision(self):
        return self.doc.revision

    def to_json(self):
        return {
            "base": self.base.revision,
            "revision": self.revision,
            "ok": self.ok,
            "applied": [r.layer.id for r in self.results if r.ok],
            "failed": self.failed.to_json() if self.failed else None,
            "skipped": list(self.skipped),
        }


def evaluate_stack(base, layers, index=None, evaluator=None, options=DEFAULT_OPTIONS, pinned=None, upto=None):
    """Apply every enabled layer, in index order, stopping at the first failure.

    ``layers`` is a list of :class:`Layer`; ``index`` (a :class:`store.Index`)
    supplies order and enabled flags — without it, the list order is used and
    everything is enabled. ``upto`` stops *after* the named layer, which is
    how "show me the model as it was before dev-a41c" is asked.
    """
    evaluator = evaluator or StructuralEvaluator()
    by_id = {layer.id: layer for layer in layers}
    if index is not None:
        order = [i for i in index.order if i in by_id]
        enabled = index.enabled
    else:
        order = [layer.id for layer in layers]
        enabled = {}
    result = StackResult(base, index)
    doc = base
    for layer_id in order:
        if not enabled.get(layer_id, True):
            result.skipped.append(layer_id)
            continue
        rep = replay(by_id[layer_id], doc, evaluator, options, pinned=pinned)
        result.results.append(rep)
        if not rep.ok:
            break
        doc = rep.doc
        if layer_id == upto:
            break
    result.doc = doc
    return result


class GeometricDiff:
    """What changed between two evaluated documents, as far as it can be measured."""

    def __init__(self, before, after):
        self.before = before
        self.after = after
        self.metrics_before = {}
        self.metrics_after = {}
        self.bbox_before = None
        self.bbox_after = None
        self.features_added = []
        self.features_removed = []
        self.features_changed = []
        self.parameters_changed = []
        self.not_measured = []

    @property
    def metric_deltas(self):
        keys = sorted(set(self.metrics_before) | set(self.metrics_after))
        out = {}
        for key in keys:
            a, b = self.metrics_before.get(key), self.metrics_after.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                out[key] = {"before": a, "after": b, "delta": b - a}
            else:
                out[key] = {"before": a, "after": b}
        return out

    @property
    def envelope_delta(self):
        if self.bbox_before is None or self.bbox_after is None:
            return None
        (a0, a1), (b0, b1) = self.bbox_before, self.bbox_after
        size_a = tuple(a1[i] - a0[i] for i in range(3))
        size_b = tuple(b1[i] - b0[i] for i in range(3))
        return {"before": size_a, "after": size_b, "delta": tuple(size_b[i] - size_a[i] for i in range(3))}

    def to_json(self):
        return {
            "before": self.before.revision,
            "after": self.after.revision,
            "features_added": list(self.features_added),
            "features_removed": list(self.features_removed),
            "features_changed": list(self.features_changed),
            "parameters_changed": list(self.parameters_changed),
            "metrics": self.metric_deltas,
            "envelope": self.envelope_delta,
            "not_measured": list(self.not_measured),
        }

    def summary(self):
        lines = [f"diff {self.before.revision or '(base)'} -> {self.after.revision}"]
        if self.features_added:
            lines.append("  added:   " + ", ".join(self.features_added))
        if self.features_removed:
            lines.append("  removed: " + ", ".join(self.features_removed))
        for change in self.features_changed:
            lines.append(f"  changed: {change}")
        for change in self.parameters_changed:
            lines.append(f"  param:   {change}")
        for key, delta in self.metric_deltas.items():
            if "delta" in delta:
                lines.append(f"  {key}: {delta['before']:g} -> {delta['after']:g} ({delta['delta']:+g})")
        env = self.envelope_delta
        if env is not None:
            lines.append("  envelope: " + " x ".join(f"{v:g}" for v in env["before"]) + " -> " + " x ".join(f"{v:g}" for v in env["after"]))
        for item in self.not_measured:
            lines.append(f"  not measured: {item}")
        return "\n".join(lines)


def geometric_diff(before, after, evaluator=None):
    evaluator = evaluator or StructuralEvaluator()
    diff = GeometricDiff(before, after)
    names_before = {f.name: f for f in before.features}
    names_after = {f.name: f for f in after.features}
    diff.features_added = [n for n in names_after if n not in names_before]
    diff.features_removed = [n for n in names_before if n not in names_after]
    for name in names_before:
        if name not in names_after:
            continue
        a, b = names_before[name], names_after[name]
        for key in sorted(set(a.params) | set(b.params)):
            if a.params.get(key) != b.params.get(key):
                diff.features_changed.append(f"{name}.{key}: {a.params.get(key)!r} -> {b.params.get(key)!r}")
    for key in sorted(set(before.parameters) | set(after.parameters)):
        if before.parameters.get(key) != after.parameters.get(key):
            diff.parameters_changed.append(f"{key}: {before.parameters.get(key)!r} -> {after.parameters.get(key)!r}")

    if evaluator.can("metrics"):
        diff.metrics_before = evaluator.metrics(before)
        diff.metrics_after = evaluator.metrics(after)
    else:
        diff.not_measured.append("mass properties (evaluator has no geometry)")
    if evaluator.can("bounding_box"):
        diff.bbox_before = evaluator.bounding_box(before)
        diff.bbox_after = evaluator.bounding_box(after)
    else:
        diff.not_measured.append("envelope (evaluator has no bounding box)")
    if not evaluator.can("self_intersection"):
        diff.not_measured.append("added/removed volume and changed faces (needs a geometric evaluator)")
    return diff
