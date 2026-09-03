# SPDX-License-Identifier: LGPL-2.1-or-later
"""Claims: conflict detection *before* the work.

README §4.2. Before an agent starts it declares what it will modify and what
it depends on. Overlapping ``modifies`` between two live claims is a warning;
an ``exclusive`` claim over the same region blocks. ``depends`` entries are
watched, so a layer that changes something you declared a dependency on tells
you *while you work*, not at merge time.

The registry is deliberately small and serialisable: it lives as
``claims.json`` inside the ``.layers/`` folder, where every worktree can see it.
"""

import json

from . import targets
from .errors import LayerFormatError
from .schema import Claims


class ClaimIssue:
    """One thing the registry wants a claimant to know."""

    __slots__ = ("severity", "kind", "layer", "other", "target", "message")

    #: Severities, in increasing order.
    SEVERITIES = ("notice", "warning", "block")

    def __init__(self, severity, kind, layer, target, message, other=None):
        if severity not in self.SEVERITIES:
            raise ValueError(f"bad severity {severity!r}")
        self.severity = severity
        self.kind = kind
        self.layer = layer
        self.other = other
        self.target = target
        self.message = message

    @property
    def blocking(self):
        return self.severity == "block"

    def to_json(self):
        return {
            "severity": self.severity,
            "kind": self.kind,
            "layer": self.layer,
            "other": self.other,
            "target": self.target,
            "message": self.message,
        }

    def __repr__(self):
        return f"ClaimIssue({self.severity} {self.kind}: {self.message})"


class ClaimRegistry:
    """Live claims, keyed by layer id."""

    def __init__(self):
        self._claims = {}

    def __len__(self):
        return len(self._claims)

    def __contains__(self, layer_id):
        return layer_id in self._claims

    def claims(self, layer_id):
        return self._claims.get(layer_id)

    def layers(self):
        return list(self._claims)

    # -- registration ---------------------------------------------------

    def check(self, layer_id, claims):
        """What registering ``claims`` for ``layer_id`` would report, without doing it."""
        issues = []
        for other_id, other in self._claims.items():
            if other_id == layer_id:
                continue
            for mine in claims.modifies:
                for theirs in other.modifies:
                    if not targets.overlap(mine, theirs):
                        continue
                    exclusive = "exclusive" in (claims.mode, other.mode)
                    issues.append(
                        ClaimIssue(
                            "block" if exclusive else "warning",
                            "overlap",
                            layer_id,
                            mine,
                            (
                                f"{layer_id} claims {mine!r}, which overlaps {theirs!r} "
                                f"held by {other_id} ({other.mode})"
                                + ("; an exclusive claim blocks" if exclusive else "")
                            ),
                            other=other_id,
                        )
                    )
            # My modifications hit their dependencies: they need to know now.
            for mine in claims.modifies:
                for dep in other.depends:
                    if dep.param and targets.overlap(mine, f"param:{dep.param}"):
                        issues.append(self._dependency_issue(layer_id, other_id, mine, dep))
            # Their modifications hit my dependencies: I need to know before I start.
            for dep in claims.depends:
                if dep.param is None:
                    continue
                for theirs in other.modifies:
                    if targets.overlap(theirs, f"param:{dep.param}"):
                        issues.append(
                            ClaimIssue(
                                "warning",
                                "dependency_claimed",
                                layer_id,
                                dep.key,
                                f"{layer_id} depends on {dep.key} ({dep.reason or 'no reason given'}), "
                                f"which {other_id} has claimed to modify",
                                other=other_id,
                            )
                        )
        return issues

    @staticmethod
    def _dependency_issue(layer_id, other_id, target, dep):
        return ClaimIssue(
            "warning",
            "dependency_threatened",
            other_id,
            dep.key,
            f"{layer_id} intends to modify {target!r}; {other_id} depends on it "
            f"({dep.reason or 'no reason given'})",
            other=layer_id,
        )

    def register(self, layer_id, claims, force=False):
        """Register a claim. Returns the issues; a blocking issue prevents
        registration unless ``force`` is set."""
        if not isinstance(claims, Claims):
            raise LayerFormatError("register() takes a Claims instance", "claims")
        issues = self.check(layer_id, claims)
        if any(issue.blocking for issue in issues) and not force:
            return issues
        self._claims[layer_id] = claims
        return issues

    def release(self, layer_id):
        return self._claims.pop(layer_id, None)

    # -- watching -------------------------------------------------------

    def watchers_of(self, target):
        """``[(layer_id, dependency)]`` for every live dependency ``target`` touches."""
        found = []
        for layer_id, claims in self._claims.items():
            for dep in claims.depends:
                key = f"param:{dep.param}" if dep.param else None
                if key is not None and targets.overlap(target, key):
                    found.append((layer_id, dep))
        return found

    def notify_change(self, changed, by_layer):
        """Report which live claims depend on any of the ``changed`` targets.

        Anchor dependencies are matched by the *anchor name* — the caller
        passes the names of anchors whose resolution changed, prefixed
        ``anchor:``, alongside modified targets.
        """
        issues = []
        for target in changed:
            for layer_id, claims in self._claims.items():
                if layer_id == by_layer:
                    continue
                for dep in claims.depends:
                    if targets.overlap(target, dep.key) or (
                        dep.param and targets.overlap(target, f"param:{dep.param}")
                    ):
                        issues.append(
                            ClaimIssue(
                                "warning",
                                "dependency_changed",
                                layer_id,
                                dep.key,
                                f"{by_layer} changed {target!r}; {layer_id} depends on it "
                                f"({dep.reason or 'no reason given'})",
                                other=by_layer,
                            )
                        )
        return issues

    # -- persistence ----------------------------------------------------

    def to_json(self):
        return {"schema": 1, "claims": {k: v.to_json() for k, v in self._claims.items()}}

    @classmethod
    def from_json(cls, data):
        registry = cls()
        if not isinstance(data, dict) or not isinstance(data.get("claims", {}), dict):
            raise LayerFormatError("claims registry must be an object with a 'claims' map", "claims.json")
        for layer_id, claims in data.get("claims", {}).items():
            registry._claims[layer_id] = Claims.from_json(claims, f"claims.json[{layer_id}]")
        return registry

    def save(self, path):
        from .store import write_atomic

        write_atomic(path, json.dumps(self.to_json(), indent=2) + "\n")

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_json(json.load(handle))


# ---------------------------------------------------------------------------
# claims versus the layer that carries them
# ---------------------------------------------------------------------------


def undeclared_targets(layer):
    """Targets the layer's operations touch that its ``claims.modifies`` does not cover.

    README §8.4: a layer whose stated scope does not match its operations
    is worse than one with no claims, because it invites misplaced trust. This
    is the check that keeps the claim honest.
    """
    return [t for t in layer.targets() if not any(targets.covers(c, t) for c in layer.claims.modifies)]


def derive_claims(layer, mode="advisory"):
    """Claims that exactly cover what the layer's operations do.

    Useful when recording: the operations are the truth, so claims derived
    from them cannot be dishonest. The ``depends`` list is taken from the
    layer's anchors, which are by definition what it needs unchanged.
    """
    from .schema import Dependency

    depends = [Dependency(anchor=name, reason="anchored operation") for name in layer.anchors]
    return Claims(modifies=layer.targets(), depends=depends, mode=mode)
