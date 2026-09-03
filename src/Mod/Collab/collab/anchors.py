# SPDX-License-Identifier: LGPL-2.1-or-later
"""Anchor resolution: stable references to volatile topology.

    resolve(anchor, document) -> Resolved | Ambiguous | Lost

This is the part of the design the concept document calls "the whole
ballgame". If it resolves unreliably, every layer becomes a reference conflict
and the system is worse than serialising the work.

Order of attempts, from SPEC §4:

1. **Datum.** A named datum is stable by construction; look it up by name.
2. **Semantic query.** Evaluate the query against the current document.
3. **Fingerprint match.** Score candidates on area, centroid, surface type and
   adjacency; accept only if the best score is within tolerance *and* clearly
   separated from the runner-up. A close second is ``Ambiguous``, not a win.
4. **Fail.** ``Lost`` — surfaced with the recorded fingerprint and the nearest
   candidates, for a human or agent to re-anchor.

There is deliberately no step falling back to ``anchor.resolved_at_record``.
This module reads that field in exactly one place — to *print* it in a failure
message — and ``Tests/test_anchors.py`` pins that. Reusing a stale topological
name is the bug this whole design exists to avoid.
"""

from .geom import directions_match, distance, relative_error
from .schema import Fingerprint

#: Feature kinds that are datums. A datum's identity is its name.
DATUM_KINDS = frozenset(
    {"Datum", "DatumPlane", "DatumLine", "DatumPoint", "DatumCS", "Plane", "Line", "Point"}
)

#: Selectors a semantic query may use to pick one of several candidates.
SELECTORS = ("largest_area", "smallest_area", "longest", "shortest", "only", "nearest_to")


class ResolveOptions:
    """Tolerances. Defaults are conservative; a caller with a known-noisy
    document can loosen them, but the separation requirement never goes away."""

    __slots__ = (
        "normal_tol_deg",
        "size_rel_tol",
        "select_separation",
        "fingerprint_tol",
        "fingerprint_separation",
        "centroid_scale_mm",
    )

    def __init__(
        self,
        normal_tol_deg=5.0,
        size_rel_tol=0.01,
        select_separation=0.01,
        fingerprint_tol=0.15,
        fingerprint_separation=0.10,
        centroid_scale_mm=10.0,
    ):
        self.normal_tol_deg = normal_tol_deg
        #: Relative tolerance on ``area``/``length`` predicates in a query.
        self.size_rel_tol = size_rel_tol
        #: A ``select`` winner whose runner-up is within this relative margin is ambiguous.
        self.select_separation = select_separation
        #: Fingerprint scores at or below this are candidate matches.
        self.fingerprint_tol = fingerprint_tol
        #: The runner-up must score at least this much worse than the winner.
        self.fingerprint_separation = fingerprint_separation
        #: Centroid distance is scaled by the entity size, or by this when unknown.
        self.centroid_scale_mm = centroid_scale_mm


DEFAULT_OPTIONS = ResolveOptions()


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


class Resolution:
    """Base of the three outcomes. All three are first-class signals."""

    __slots__ = ("anchor", "via", "notes")
    status = None

    def __init__(self, anchor, via, notes=()):
        self.anchor = anchor
        self.via = via
        self.notes = list(notes)

    @property
    def ok(self):
        return self.status == "resolved"

    def to_json(self):
        return {"anchor": self.anchor.name, "status": self.status, "via": self.via, "notes": self.notes}


class Resolved(Resolution):
    __slots__ = ("entity", "feature", "confidence")
    status = "resolved"

    def __init__(self, anchor, via, entity=None, feature=None, confidence=1.0, notes=()):
        super().__init__(anchor, via, notes)
        self.entity = entity
        self.feature = feature
        self.confidence = confidence

    @property
    def name(self):
        return self.entity.name if self.entity is not None else self.feature.name

    @property
    def owner(self):
        if self.entity is not None:
            return self.entity.owner
        return self.feature.name

    def to_json(self):
        data = super().to_json()
        data.update({"name": self.name, "owner": self.owner, "confidence": round(self.confidence, 4)})
        return data

    def __repr__(self):
        return f"Resolved({self.anchor.name!r} -> {self.name!r} via {self.via}, {self.confidence:.2f})"


class Ambiguous(Resolution):
    """Several candidates and no principled way to choose. A conflict."""

    __slots__ = ("candidates",)
    status = "ambiguous"

    def __init__(self, anchor, via, candidates, notes=()):
        super().__init__(anchor, via, notes)
        #: ``[(entity, score), ...]`` best first; score is ``None`` when unscored.
        self.candidates = list(candidates)

    def to_json(self):
        data = super().to_json()
        data["candidates"] = [_candidate_json(e, s) for e, s in self.candidates]
        return data

    def __repr__(self):
        names = [e.name for e, _ in self.candidates]
        return f"Ambiguous({self.anchor.name!r} between {names} via {self.via})"


class Lost(Resolution):
    """No candidate. A conflict, reported with what it used to be and what is nearby."""

    __slots__ = ("nearest", "recorded_name")
    status = "lost"

    def __init__(self, anchor, via, nearest=(), notes=()):
        super().__init__(anchor, via, notes)
        self.nearest = list(nearest)
        # Diagnostics only. This is the single read of resolved_at_record in
        # this module, and it feeds a message, never a match.
        self.recorded_name = anchor.resolved_at_record

    def to_json(self):
        data = super().to_json()
        data["recorded_name"] = self.recorded_name
        data["nearest"] = [_candidate_json(e, s) for e, s in self.nearest]
        return data

    def __repr__(self):
        was = f", was {self.recorded_name!r}" if self.recorded_name else ""
        return f"Lost({self.anchor.name!r} via {self.via}{was})"


def _candidate_json(entity, score):
    data = {"name": entity.name, "owner": entity.owner, "kind": entity.kind}
    if score is not None:
        data["score"] = round(score, 4)
    return data


# ---------------------------------------------------------------------------
# semantic queries
# ---------------------------------------------------------------------------

_OWNER_KEYS = {"face_of": "face", "edge_of": "edge", "vertex_of": "vertex", "of": None}


def _size_matches(actual, expected, query, options):
    if actual is None:
        return False
    tol = query.get("tol")
    if tol is not None:
        return abs(actual - float(expected)) <= float(tol)
    rel = query.get("rel_tol", options.size_rel_tol)
    err = relative_error(actual, expected)
    return err is not None and err <= rel


def semantic_candidates(query, doc, options=DEFAULT_OPTIONS):
    """Filter the document's entities by a semantic query.

    Returns ``(candidates, notes)``. Selection (``select``) is *not* applied
    here; :func:`apply_selector` does that so the two can be reported apart.
    """
    notes = []
    kind = query.get("kind")
    owner = None
    for key, implied_kind in _OWNER_KEYS.items():
        if key in query:
            owner = query[key]
            if implied_kind is not None:
                kind = implied_kind
            break

    between = query.get("edge_between")
    if between is not None:
        kind = kind or "edge"

    if owner is not None and not doc.has_feature(owner):
        notes.append(f"owner feature {owner!r} does not exist in this document")
        return [], notes

    candidates = []
    for entity in doc.entities:
        if kind is not None and entity.kind != kind:
            continue
        if owner is not None and entity.owner != owner:
            continue
        if "surface" in query and entity.surface != query["surface"]:
            continue
        if "normal" in query:
            if entity.normal is None or not directions_match(
                entity.normal, query["normal"], query.get("normal_tol_deg", options.normal_tol_deg)
            ):
                continue
        if "area" in query and not _size_matches(entity.area, query["area"], query, options):
            continue
        if "length" in query and not _size_matches(entity.length, query["length"], query, options):
            continue
        if between is not None:
            wanted = set(between)
            if not wanted.issubset(set(entity.between) | {entity.owner}):
                continue
        candidates.append(entity)
    return candidates, notes


def apply_selector(selector, candidates, query, options=DEFAULT_OPTIONS):
    """Pick one candidate by ``select``, or report why that is not safe.

    Returns ``(winner, ordered_candidates, note)``. ``winner`` is ``None`` when
    the selector cannot choose — including when the top two are within
    ``options.select_separation`` of each other. "largest face" with two faces
    of equal area is not a choice, it is a coin toss.
    """
    if selector not in SELECTORS:
        return None, list(candidates), f"unknown selector {selector!r}; known: {SELECTORS}"
    if not candidates:
        return None, [], "no candidates to select from"

    if selector == "only":
        if len(candidates) == 1:
            return candidates[0], list(candidates), None
        return None, list(candidates), f"'only' expected one candidate, found {len(candidates)}"

    if selector == "nearest_to":
        point = query.get("nearest_to")
        if point is None:
            return None, list(candidates), "'nearest_to' needs a point in the query"
        keyed = sorted(candidates, key=lambda e: distance(e.centroid_local, tuple(point)))
        ordered = keyed
        values = [distance(e.centroid_local, tuple(point)) for e in ordered]
        if len(ordered) > 1:
            scale = max(values[1], options.centroid_scale_mm)
            if (values[1] - values[0]) / scale < options.select_separation:
                return None, ordered, "two candidates are equally near the point"
        return ordered[0], ordered, None

    attr = "area" if selector.endswith("_area") else "length"
    reverse = selector in ("largest_area", "longest")
    measured = [e for e in candidates if getattr(e, attr) is not None]
    if not measured:
        return None, list(candidates), f"no candidate has a {attr} to select by"
    ordered = sorted(measured, key=lambda e: getattr(e, attr), reverse=reverse)
    if len(ordered) > 1:
        top, second = getattr(ordered[0], attr), getattr(ordered[1], attr)
        scale = max(abs(top), abs(second), 1e-9)
        if abs(top - second) / scale < options.select_separation:
            return (
                None,
                ordered,
                f"{selector}: top two candidates differ by less than "
                f"{options.select_separation:.0%} ({top:g} vs {second:g})",
            )
    return ordered[0], ordered, None


# ---------------------------------------------------------------------------
# fingerprints
# ---------------------------------------------------------------------------


def fingerprint_score(fingerprint, entity, options=DEFAULT_OPTIONS):
    """Distance between a recorded fingerprint and a live entity. Lower is closer.

    Components are individually normalised so a 1 % area drift and a 1 % centroid
    drift count the same. A surface-type mismatch is a hard 1.0, which by itself
    exceeds any sensible tolerance: a plane does not become a cylinder through
    an unrelated edit, so if the type differs this is a different face.
    """
    score = 0.0
    if fingerprint.surface is not None and fingerprint.surface != entity.surface:
        score += 1.0

    if fingerprint.size is not None:
        err = relative_error(entity.size, fingerprint.size)
        score += 1.0 if err is None else min(err, 1.0)

    if fingerprint.centroid_local is not None:
        scale = options.centroid_scale_mm
        if fingerprint.size is not None and fingerprint.size > 0:
            scale = max(fingerprint.size ** (0.5 if fingerprint.area is not None else 1.0), 1e-3)
        score += min(distance(entity.centroid_local, fingerprint.centroid_local) / scale, 1.0)

    if fingerprint.adjacency is not None:
        score += min(abs(entity.adjacency - fingerprint.adjacency) * 0.25, 1.0)
    return score


def fingerprint_match(fingerprint, candidates, options=DEFAULT_OPTIONS):
    """Rank ``candidates`` by fingerprint distance.

    Returns ``(winner, ranked, note)`` where ``ranked`` is ``[(entity, score)]``
    best first and ``winner`` is ``None`` unless the best is within tolerance
    *and* separated from the runner-up by ``options.fingerprint_separation``.
    """
    ranked = sorted(
        ((entity, fingerprint_score(fingerprint, entity, options)) for entity in candidates),
        key=lambda pair: pair[1],
    )
    if not ranked:
        return None, ranked, "no candidates"
    best, best_score = ranked[0]
    if best_score > options.fingerprint_tol:
        return (
            None,
            ranked,
            f"best candidate {best.name} scores {best_score:.3f}, over tolerance {options.fingerprint_tol}",
        )
    if len(ranked) > 1:
        runner_up, second_score = ranked[1]
        if second_score - best_score < options.fingerprint_separation:
            return (
                None,
                ranked,
                f"{best.name} ({best_score:.3f}) is not clearly separated from "
                f"{runner_up.name} ({second_score:.3f})",
            )
    return best, ranked, None


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def _resolve_datum(anchor, doc):
    name = anchor.query.get("datum")
    if not name:
        return Lost(anchor, "datum", notes=["datum anchor has no 'datum' name in its query"])
    feature = doc.feature(name)
    if feature is None:
        return Lost(anchor, "datum", notes=[f"no feature named {name!r}"])
    if feature.kind not in DATUM_KINDS and not feature.kind.startswith("Datum"):
        return Lost(
            anchor,
            "datum",
            notes=[f"{name!r} is a {feature.kind}, not a datum; datum anchors only bind to datums"],
        )
    owned = doc.entities_of(name)
    return Resolved(anchor, "datum", entity=owned[0] if owned else None, feature=feature)


def resolve(anchor, doc, options=DEFAULT_OPTIONS):
    """Resolve one anchor against a document. Never raises for a modelling reason."""
    if anchor.strategy == "datum":
        return _resolve_datum(anchor, doc)

    notes = []
    if anchor.strategy == "semantic":
        candidates, query_notes = semantic_candidates(anchor.query, doc, options)
        notes.extend(query_notes)

        if len(candidates) == 1:
            return Resolved(anchor, "semantic", entity=candidates[0], notes=notes)

        if len(candidates) > 1:
            selector = anchor.query.get("select")
            if selector:
                winner, ordered, note = apply_selector(selector, candidates, anchor.query, options)
                if winner is not None:
                    return Resolved(anchor, "semantic", entity=winner, notes=notes)
                notes.append(note)
                candidates = ordered
            # The query narrowed it to a set; let the fingerprint pick within
            # that set, never outside it.
            if anchor.fingerprint is not None:
                winner, ranked, note = fingerprint_match(anchor.fingerprint, candidates, options)
                if winner is not None:
                    return Resolved(
                        anchor,
                        "semantic+fingerprint",
                        entity=winner,
                        confidence=max(0.0, 1.0 - ranked[0][1]),
                        notes=notes + ["semantic query matched several; fingerprint disambiguated"],
                    )
                notes.append(note)
                return Ambiguous(anchor, "semantic+fingerprint", ranked, notes)
            return Ambiguous(anchor, "semantic", [(e, None) for e in candidates], notes)

        notes.append("semantic query matched nothing")
        if anchor.fingerprint is None:
            return Lost(anchor, "semantic", notes=notes)

    # Fingerprint search across every entity of the fingerprint's kind. The
    # owner in the query is *not* used as a filter here: the most common way
    # to reach this point is that the owner was renamed or replaced.
    fingerprint = anchor.fingerprint
    kind = "face" if fingerprint.area is not None else ("edge" if fingerprint.length is not None else None)
    pool = [e for e in doc.entities if kind is None or e.kind == kind]
    winner, ranked, note = fingerprint_match(fingerprint, pool, options)
    if winner is not None:
        return Resolved(
            anchor,
            "fingerprint",
            entity=winner,
            confidence=max(0.0, 1.0 - ranked[0][1]),
            notes=notes + ["matched by fingerprint only — lower confidence"],
        )
    notes.append(note)
    close = [(e, s) for e, s in ranked if s <= options.fingerprint_tol]
    if len(close) > 1:
        return Ambiguous(anchor, "fingerprint", close, notes)
    return Lost(anchor, "fingerprint", nearest=ranked[:3], notes=notes)


def resolve_all(anchors, doc, options=DEFAULT_OPTIONS):
    """Resolve a mapping of anchors; returns ``{name: Resolution}``."""
    return {name: resolve(anchor, doc, options) for name, anchor in anchors.items()}


def record_anchor(name, entity, doc, query=None, strategy=None):
    """Build an anchor for ``entity`` as it stands in ``doc`` right now.

    If ``query`` is not given, a semantic query is derived from the entity:
    its owner, its surface and, for faces with a normal, that normal. Whether
    the derived query is *unique* is checked immediately — an anchor that is
    ambiguous the moment it is recorded is no use to anyone, so in that case
    the fingerprint is attached and the caller is told.

    Returns ``(anchor, resolution_now)``.
    """
    from .schema import Anchor

    if strategy is None:
        strategy = "semantic"
    if query is None:
        query = {f"{entity.kind}_of": entity.owner, "surface": entity.surface}
        if entity.normal is not None:
            query["normal"] = [round(c, 6) for c in entity.normal]
    anchor = Anchor(
        name,
        strategy=strategy,
        query=query,
        fingerprint=Fingerprint.of(entity),
        resolved_at_record=entity.name,
    )
    return anchor, resolve(anchor, doc)
