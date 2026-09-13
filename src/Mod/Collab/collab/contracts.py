# SPDX-License-Identifier: LGPL-2.1-or-later
"""Interface contracts and pinned parameters (README §6 and §7).

For assembly-level work the mechanism is not merge at all — it is a contract
each part publishes: mating features, keep-out volumes, an envelope, a mass
budget. Violations are computed like CI, continuously, and changing your own
contract is a breaking change announced to every dependent part.

Pinned parameters live here too, because "the list is explicit and versioned,
and lives with the assembly". A layer that touches a pinned parameter cannot
auto-merge; it escalates, always.

File: ``project.contracts.json`` beside the documents.
"""

import json

from . import targets
from .errors import LayerFormatError


class Violation:
    __slots__ = ("part", "rule", "message", "severity", "value", "limit")

    def __init__(self, part, rule, message, severity="error", value=None, limit=None):
        self.part = part
        self.rule = rule
        self.message = message
        self.severity = severity
        self.value = value
        self.limit = limit

    def to_json(self):
        data = {"part": self.part, "rule": self.rule, "message": self.message, "severity": self.severity}
        if self.value is not None:
            data["value"] = self.value
        if self.limit is not None:
            data["limit"] = self.limit
        return data

    def __repr__(self):
        return f"Violation({self.part}:{self.rule}: {self.message})"


class Mating:
    __slots__ = ("name", "datum", "bolts", "extras")

    def __init__(self, name, datum=None, bolts=None, **extras):
        self.name = name
        self.datum = datum
        self.bolts = bolts
        self.extras = extras

    def to_json(self):
        data = {"name": self.name}
        if self.datum:
            data["datum"] = self.datum
        if self.bolts:
            data["bolts"] = self.bolts
        data.update(self.extras)
        return data

    @classmethod
    def from_json(cls, data, path):
        if not isinstance(data, dict) or "name" not in data:
            raise LayerFormatError("mating entry needs a name", path)
        return cls(**data)


class KeepOut:
    """A volume nothing else may enter. ``shape`` is ``box``, ``cylinder`` or ``sphere``."""

    __slots__ = ("name", "shape", "params")
    SHAPES = ("box", "cylinder", "sphere")

    def __init__(self, name, shape, **params):
        if shape not in self.SHAPES:
            raise LayerFormatError(f"keep-out shape must be one of {self.SHAPES}", f"keep_out.{name}")
        self.name = name
        self.shape = shape
        self.params = params

    def to_json(self):
        data = {"name": self.name, "shape": self.shape}
        data.update(self.params)
        return data

    @classmethod
    def from_json(cls, data, path):
        if not isinstance(data, dict) or "name" not in data or "shape" not in data:
            raise LayerFormatError("keep-out entry needs a name and a shape", path)
        return cls(**data)


class Contract:
    __slots__ = ("part", "mating", "keep_out", "envelope", "budget", "extras")

    def __init__(self, part, mating=(), keep_out=(), envelope=None, budget=None, extras=None):
        self.part = part
        self.mating = list(mating)
        self.keep_out = list(keep_out)
        self.envelope = dict(envelope or {})
        self.budget = dict(budget or {})
        self.extras = dict(extras or {})

    def to_json(self):
        data = {"part": self.part}
        if self.mating:
            data["mating"] = [m.to_json() for m in self.mating]
        if self.keep_out:
            data["keep_out"] = [k.to_json() for k in self.keep_out]
        if self.envelope:
            data["envelope"] = dict(self.envelope)
        if self.budget:
            data["budget"] = dict(self.budget)
        data.update(self.extras)
        return data

    @classmethod
    def from_json(cls, data, path="contract"):
        if not isinstance(data, dict) or "part" not in data:
            raise LayerFormatError("contract needs a 'part' name", path)
        known = {"part", "mating", "keep_out", "envelope", "budget"}
        return cls(
            part=data["part"],
            mating=[Mating.from_json(m, f"{path}.mating[{i}]") for i, m in enumerate(data.get("mating", []))],
            keep_out=[
                KeepOut.from_json(k, f"{path}.keep_out[{i}]") for i, k in enumerate(data.get("keep_out", []))
            ],
            envelope=data.get("envelope"),
            budget=data.get("budget"),
            extras={k: v for k, v in data.items() if k not in known},
        )

    # -- checks ---------------------------------------------------------

    def check_budget(self, metrics):
        """Budget entries are ``metric: limit``; ``material`` is an equality."""
        violations = []
        for key, limit in self.budget.items():
            if key == "material":
                actual = metrics.get("material")
                if actual is not None and actual != limit:
                    violations.append(
                        Violation(self.part, "budget.material", f"material is {actual!r}, contract says {limit!r}")
                    )
                continue
            if key not in metrics:
                continue
            actual = metrics[key]
            try:
                over = actual > limit
            except TypeError:
                continue
            if over:
                violations.append(
                    Violation(
                        self.part,
                        f"budget.{key}",
                        f"{key} is {actual:g}, over the budget of {limit:g}",
                        value=actual,
                        limit=limit,
                    )
                )
        return violations

    def check_envelope(self, bbox):
        """``envelope.bbox`` is ``[dx, dy, dz]``; the part must fit inside it."""
        violations = []
        limit = self.envelope.get("bbox")
        if limit is None or bbox is None:
            return violations
        (x0, y0, z0), (x1, y1, z1) = bbox
        size = (x1 - x0, y1 - y0, z1 - z0)
        for axis, (actual, allowed) in enumerate(zip(size, limit)):
            if actual > allowed + 1e-6:
                violations.append(
                    Violation(
                        self.part,
                        "envelope.bbox",
                        f"extent along {'xyz'[axis]} is {actual:g} mm, envelope allows {allowed:g} mm",
                        value=actual,
                        limit=allowed,
                    )
                )
        return violations

    def breaking_changes_from(self, previous):
        """What changed in a way a dependent part must be told about."""
        changes = []
        old = {m.name: m.to_json() for m in previous.mating}
        new = {m.name: m.to_json() for m in self.mating}
        for name in old:
            if name not in new:
                changes.append(f"mating feature {name!r} removed")
            elif old[name] != new[name]:
                changes.append(f"mating feature {name!r} changed")
        old_ko = {k.name: k.to_json() for k in previous.keep_out}
        new_ko = {k.name: k.to_json() for k in self.keep_out}
        for name in new_ko:
            if name not in old_ko:
                changes.append(f"keep-out {name!r} added")
            elif old_ko[name] != new_ko[name]:
                changes.append(f"keep-out {name!r} changed")
        if self.envelope != previous.envelope:
            changes.append("envelope changed")
        for key, limit in self.budget.items():
            before = previous.budget.get(key)
            if before is not None and before != limit:
                changes.append(f"budget {key} changed from {before!r} to {limit!r}")
        return changes


class ContractSet:
    """Every part's contract plus the pinned-parameter list."""

    def __init__(self, contracts=(), pinned=(), path=None):
        self.contracts = {c.part: c for c in contracts}
        self.pinned = list(pinned)
        self.path = path

    def __contains__(self, part):
        return part in self.contracts

    def get(self, part):
        return self.contracts.get(part)

    # -- pinned ---------------------------------------------------------

    def is_pinned(self, target):
        return any(targets.covers(pin, target) for pin in self.pinned)

    def pinned_touched_by(self, layer):
        """Every target of the layer that is pinned, plus anything it declared itself."""
        touched = list(layer.pinned_touched)
        for target in layer.targets():
            if self.is_pinned(target) and target not in touched:
                touched.append(target)
        return touched

    # -- checks ---------------------------------------------------------

    def check(self, part, metrics=None, bbox=None, evaluator=None, doc=None):
        """Violations of ``part``'s own contract given what is known about it.

        Keep-outs are checked against *other* parts' keep-outs when an
        evaluator with the ``interference`` capability is given; without one
        they are reported as unchecked in the returned ``skipped`` list.

        Returns ``(violations, skipped)``.
        """
        contract = self.contracts.get(part)
        violations, skipped = [], []
        if contract is None:
            return violations, skipped
        violations.extend(contract.check_budget(metrics or {}))
        if bbox is not None:
            violations.extend(contract.check_envelope(bbox))
        elif contract.envelope:
            skipped.append("envelope: no bounding box available")
        foreign = [(c.part, k) for c in self.contracts.values() if c.part != part for k in c.keep_out]
        if foreign:
            if evaluator is not None and evaluator.can("interference") and doc is not None:
                issues = evaluator.interference(doc, [k for _, k in foreign]) or []
                owners = {k.name: owner for owner, k in foreign}
                for issue in issues:
                    violations.append(
                        Violation(
                            part,
                            "keep_out",
                            f"intrudes into {owners.get(issue.features[0] if issue.features else '?', '?')}:"
                            f"{issue.message}",
                            value=issue.value,
                        )
                    )
            else:
                skipped.append(f"keep-out: {len(foreign)} foreign keep-out volume(s) not checked (no interference evaluator)")
        return violations, skipped

    # -- persistence ----------------------------------------------------

    def to_json(self):
        return {
            "schema": 1,
            "pinned": list(self.pinned),
            "contracts": [c.to_json() for c in self.contracts.values()],
        }

    @classmethod
    def from_json(cls, data, path="project.contracts.json"):
        if not isinstance(data, dict):
            raise LayerFormatError("contracts file must be an object", path)
        return cls(
            contracts=[Contract.from_json(c, f"{path}.contracts[{i}]") for i, c in enumerate(data.get("contracts", []))],
            pinned=data.get("pinned", []),
        )

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            contracts = cls.from_json(json.load(handle), str(path))
        contracts.path = str(path)
        return contracts

    def save(self, path=None):
        from .store import write_atomic

        path = path or self.path
        if path is None:
            raise LayerFormatError("no path to save contracts to", "contracts")
        write_atomic(path, json.dumps(self.to_json(), indent=2) + "\n")
        self.path = str(path)
