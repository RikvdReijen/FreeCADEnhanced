# Deviation layer — a concrete format

*Implemented by [`src/Mod/Collab/collab/schema.py`](../../../src/Mod/Collab/collab/schema.py)
(format), `anchors.py` (§4), `replay.py` (§3) and `merge.py` (§5). The
example layer in §2 is the round-trip fixture in `Tests/test_schema.py`.*

Worked out far enough to argue with. The point of writing it as a format rather
than prose is that a format forces the awkward questions: what exactly is an
anchor, what happens when it fails, what does a merge actually compare.

---

## 1. The container

A layer lives beside the document, not inside it — `.FCStd` stays a normal
FreeCAD file that opens in an unmodified FreeCAD.

```
project/
├── housing.FCStd                  the document, unmodified
├── housing.layers/
│   ├── index.json                 order, enabled state, base revision
│   ├── dev-a41c.json              one layer
│   └── dev-93b7.json
└── project.contracts.json         interface contracts (README §6)
```

`index.json` is the only file with a merge conflict risk in the ordinary git
sense, and it is small, line-oriented and human-readable — deliberately.

```jsonc
{
  "document": "housing.FCStd",
  "base": "8f2e19c4",
  "order": ["dev-93b7", "dev-a41c"],
  "enabled": { "dev-93b7": true, "dev-a41c": true }
}
```

---

## 2. A layer

```jsonc
{
  "id": "dev-a41c",
  "schema": 1,
  "name": "Lightweight the mounting flange",
  "author": {
    "kind": "agent",
    "id": "claude-opus-5",
    "session": "session_015Uo5…",
    "human_sponsor": "rik"
  },
  "created": "2026-09-03T21:40:00Z",
  "base": "8f2e19c4",

  "intent": {
    "goal": "Reduce flange mass by 30% without dropping the safety factor below 2.5",
    "rationale": "Mass budget for the arm assembly is over by 84 g; the flange is the least loaded member",
    "success_criteria": [
      { "metric": "mass_g", "op": "<=", "value": 84 },
      { "metric": "min_safety_factor", "op": ">=", "value": 2.5 }
    ]
  },

  "claims": {
    "modifies": ["Body.Flange", "Body.Flange.Sketch"],
    "depends": [
      { "anchor": "a_mount_face", "reason": "pocket depth is measured from it" },
      { "param": "wall_min", "reason": "pockets must leave this wall" }
    ],
    "mode": "advisory"
  },

  "anchors": {
    "a_mount_face": {
      "strategy": "semantic",
      "query": { "face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area" },
      "fingerprint": { "area": 1843.2, "centroid_local": [0, 0, 12], "surface": "plane", "adjacency": 4 },
      "resolved_at_record": "Face6"
    }
  },

  "operations": [
    {
      "op": "add_feature",
      "kind": "Pocket",
      "name": "LightenPocket1",
      "after": "Pad3",
      "sketch": {
        "plane": "@a_mount_face",
        "geometry": [ /* … */ ],
        "constraints": [ /* … */ ]
      },
      "params": { "depth": 4.0, "type": "Length" }
    },
    {
      "op": "set_param",
      "target": "Body.Flange.Fillet2.Radius",
      "from": 2.0,
      "to": 1.2
    }
  ],

  "pinned_touched": [],

  "validation": {
    "recompute": "ok",
    "self_intersection": "none",
    "min_wall_mm": 2.7,
    "mass_g": 79.4,
    "min_safety_factor": 2.61,
    "contracts": "pass",
    "evaluated_at": "8f2e19c4+dev-93b7"
  }
}
```

### Notes on the shape

**`from` on `set_param` is not redundant.** It is what makes a parametric
conflict detectable: if the current value is not `from`, someone else moved it,
and that is a conflict even when the target value happens to agree.

**`resolved_at_record`** keeps the topological name the anchor *had* when
recorded. Never used for resolution — kept for diagnostics, so a failed
re-anchor can say "this used to be Face6".

**`validation` names the revision it was evaluated against.** A layer validated
against a different stack than the one it is being merged into has stale
validation, and the merge must say so rather than trusting the numbers.

**`intent.success_criteria` are machine-checkable.** An intent that cannot be
checked is a comment. These can be re-run after merge, which is how you catch
"both layers were individually fine, together they blew the mass budget".

---

## 3. Operations

A deliberately small set. Everything else is composition.

| `op` | Fields | Notes |
|---|---|---|
| `add_feature` | `kind`, `name`, `after`, params | `after` is a feature name, not an index |
| `remove_feature` | `target` | Fails loudly if anything downstream depends on it |
| `set_param` | `target`, `from`, `to` | `from` enables conflict detection |
| `move_feature` | `target`, `after` | Reordering is an operation, not a side effect |
| `add_datum` | `kind`, `name`, placement | Encouraged: creates future stable anchors |
| `edit_sketch` | `target`, geometry/constraint delta | The level-4 hazard; see below |
| `set_property` | `target`, `property`, `from`, `to` | Visual/metadata, never geometric |

`edit_sketch` is the operation that cannot be merged with another `edit_sketch`
on the same target. The format allows recording one; the merge algorithm
refuses to combine two. That asymmetry is intentional — recording is always
safe, combining is not.

---

## 4. Anchor resolution

```
resolve(anchor, document) -> Resolved(entity) | Ambiguous([entities]) | Lost
```

Order of attempts:

1. **Semantic query.** Evaluate the query against the current document.
2. **Fingerprint match.** Score candidates on area, centroid, surface type and
   adjacency; accept only if the best score is within tolerance *and* clearly
   separated from the runner-up. A close second is `Ambiguous`, not a win.
3. **Fail.** `Lost` — surfaced as a reference conflict with the recorded
   fingerprint and the nearest candidates, for a human or agent to re-anchor.

There is deliberately no fourth step falling back to `resolved_at_record`.
Reusing a stale topological name is exactly the bug this design exists to avoid,
and it would fail silently and plausibly — the worst possible failure mode.

---

## 5. Merging two layers

```
merge(base, left, right) -> Merged(layer) | Conflicts([conflict])
```

1. Replay `left` and `right` against `base` independently. Either failing to
   replay is a conflict before any comparison happens.
2. Resolve every anchor in both against `base`. `Ambiguous` or `Lost` → a
   reference conflict.
3. Partition operations by dependency subtree. Disjoint → merge is a
   concatenation, ordered by the `after` fields.
4. Overlapping subtrees → compare per target:
   - same `target`, different `to`, same `from` → **parametric conflict**
   - same `target`, different `from` → **someone else moved it** — conflict
   - same `after` position → **order conflict**
   - two `edit_sketch` on one sketch → **refuse** (README §2, level 4)
5. Evaluate the combined stack. Self-intersection, wall-thickness or clearance
   failures that neither layer had alone → **geometric conflict**.
6. Re-run both layers' `success_criteria` against the merged result. A
   criterion that passed alone and fails together is the most valuable thing
   this whole format produces.

Step 6 is the payoff. It is the check that catches the two-pockets-leave-0.4 mm
case, and it only exists because intent was recorded in a machine-checkable
form at the time the work was done.

---

## 6. What this format does not do

- **No sketch-level merge.** By design. See README §2.
- **No conflict auto-resolution for parametric or intent conflicts.** The format
  presents both sides with their stated reasons; a human picks.
- **No guarantee anchors resolve.** They will sometimes fail; the design's job
  is to fail loudly and offer candidates, not to pretend.
- **No new document format.** The `.FCStd` stays a stock FreeCAD file. Delete
  the `.layers/` folder and you have an ordinary project with the layers baked
  in wherever they were last applied.
