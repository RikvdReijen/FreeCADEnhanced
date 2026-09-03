# Parallel AI–human collaboration on parametric CAD

*A concept, not an implementation. Nothing here is built.*

The question this explores: **how do several agents — human and AI — work on one
CAD model at the same time without destroying each other's work?**

Software solved this with git. CAD has not, and the reason is not that nobody
tried. It is that the assumptions underneath `git merge` do not hold for
parametric CAD, and pretending otherwise produces a tool that corrupts models
quietly.

This document works from that failure upward.

---

## 1. Why the software answer does not transfer

`git merge` works because source code is **line-oriented text with a stable
identity per line**. Three-way merge needs only to know which lines each side
touched.

A FreeCAD document is none of those things:

| | Source code | FreeCAD document |
|---|---|---|
| Storage | UTF-8 text | `.FCStd` — a zip of `Document.xml`, `GuiDocument.xml`, and `.brp` BREP blobs |
| Unit of change | a line | a node in a dependency graph |
| Identity | stable (the line's content) | **unstable** — `Face6` may become `Face9` after an unrelated edit |
| Order | mostly free | load-bearing: feature order changes the result |
| Validity | any text is a valid file | an edit can make the model unsolvable or self-intersecting |
| Equality | byte comparison | geometric, within tolerance |

The killer is row three. This is the **topological naming problem**: features
reference their parents by generated names (`Face6`, `Edge12`) that are assigned
by traversal order. Insert a fillet earlier in the tree and every downstream
reference can silently rebind to different geometry. FreeCAD 1.0 shipped
substantial work on toponaming resilience; it reduces the problem, it does not
delete it.

So: **a merge strategy that operates on the file is a non-starter.** Everything
below operates on *operations and intent* instead, and treats geometry as
something to verify against, not something to merge.

---

## 2. Four levels of parallelism

Not all "working at the same time" is equally hard. Conflating them is why the
problem looks intractable. Ranked by difficulty:

### Level 1 — different documents, one assembly
Two agents edit `bracket.FCStd` and `housing.FCStd`. File-level isolation; git
already handles this. The real coupling is not the files but the **interface**:
mating faces, bolt patterns, keep-out volumes, mass budget. See §6.

**Verdict: solved by convention, needs interface contracts to stay solved.**

### Level 2 — one document, different bodies
Two bodies in the same file, coupled only through datums and external geometry
references. Separable in principle: the dependency graph has two nearly
disconnected components.

**Verdict: tractable. Needs claim tracking (§4) and graph-aware merge (§5).**

### Level 3 — one body, different features
Both agents add features to the same tree. Feature *order* is semantic, so both
"insert here" operations compete for a position, and each insertion may
renumber the topology the other depends on.

**Verdict: hard. This is where deviation layers (§3) earn their keep.**

### Level 4 — one feature, one sketch
Two edits to the same constraint system. Constraint solvers are not
deterministic in the way merge needs: the same constraint set can have multiple
valid solutions, and merged constraints are frequently over- or
under-constrained rather than simply wrong.

**Verdict: do not merge. Serialise it, or fail loudly and ask a human.**

A system that is honest about level 4 is more useful than one that claims to
handle it.

---

## 3. Deviation layers

The proposal borrows directly from something already in this repository: the
**sculpt layer** in `src/Mod/XR/xrsculpt/`. A sculpt layer stores a sparse,
weighted, reversible displacement, so a whole modelling pass can be dialled
back, muted or reordered without losing the strokes underneath.

Lift that idea from mesh displacement to parametric operations.

A **deviation layer** is a named, replayable set of parametric operations
recorded against *stable references* rather than topological names. It carries:

```jsonc
{
  "id": "dev-a41c",
  "name": "Lightweight the mounting flange",
  "author": { "kind": "agent", "id": "claude-opus-5", "session": "…" },
  "intent": "Reduce flange mass by 30% without dropping below the 2.5 safety factor",
  "base": "8f2e19c",            // document revision this was recorded against
  "enabled": true,
  "operations": [ … ],           // see SPEC-deviation-layer.md
  "claims":  { … },              // what it modifies    (§4)
  "depends": { … },              // what it needs unchanged (§4)
  "pinned_touched": [],          // safety-critical parameters it alters (§7)
  "validation": { … }            // gates it passed     (§7)
}
```

The document is then:

```
evaluate(base) + Σ apply(layer_i)   for enabled layers, in order
```

Which buys four things that a binary-file workflow cannot:

1. **Muting.** Turn a layer off and see the model without it — with the work
   still there. In a file-based workflow the only way to see "without" is to
   revert, which loses it.
2. **Reordering.** Move a layer earlier or later and re-evaluate. In parametric
   CAD, order is meaning, so being able to *try* an order is valuable.
3. **Attribution at feature granularity.** Not "this file changed" but "this
   agent added these three features for this stated reason".
4. **Mergeability.** Two layers against the same base are merge candidates
   because they are operations, not bytes.

The honest limitation, exactly as with sculpt layers: **layers commute only when
they are independent.** Reordering two layers that touch the same feature
changes the result, and the system must say so rather than pretend otherwise.

---

## 4. Stable references, claims and worktrees

### 4.1 Anchors — the hard part

Replayable operations require references that survive a recompute. Three
mechanisms, in order of preference:

**Named datums.** A datum plane, axis or point created explicitly and given a
name is stable by construction. Agents are expected to *create the datum they
need* rather than picking a face. This is the single highest-leverage
convention in the whole design.

**Semantic selectors.** A query rather than an index:

```
face(of: "Pad3", normal≈+Z, area: largest)
edge(between: "Pad3", "Pocket1", length≈12.0±0.1)
```

Resolution has three outcomes, and all three are first-class signals:
*resolved* (exactly one), *ambiguous* (several — a conflict), *lost* (none — a
conflict).

**Geometric fingerprints.** For references that cannot be expressed
semantically: a signature of area, centroid in a local frame, surface type,
and adjacency degree, matched within tolerance. A fallback, and always reported
as lower confidence.

### 4.2 Claims — conflict detection *before* the work

Before an agent starts, it declares:

```jsonc
{
  "modifies": ["Body.Pad3", "Body.Pad3.Sketch"],
  "depends":  ["Boss1:face(normal≈+Z)", "param:hole_spacing"],
  "mode": "advisory"        // or "exclusive"
}
```

Overlapping `modifies` between two live claims is a warning; an `exclusive`
claim over the same region blocks. `depends` entries are watched: if another
layer changes something you declared a dependency on, you are told *while you
work*, not at merge time.

This is the cheapest win in the design. Detecting the collision before two
agents spend an hour diverging is worth more than any merge algorithm.

### 4.3 Worktrees

Each agent gets its own git worktree: an isolated checkout, its own build
directory, its own scratch space. The worktree isolates the *workspace*; the
deviation layer captures the *change* in a mergeable form. They solve different
halves of the problem and both are needed.

A worktree is disposable. A layer is the artefact worth keeping.

---

## 5. Merge and the conflict taxonomy

Merging two layers against a common base proceeds:

1. **Replay** each layer's operations against the base independently.
2. **Resolve** every anchor in both. Unresolved or ambiguous → conflict.
3. **Partition** by dependency subtree. Disjoint subtrees auto-merge.
4. **Evaluate** the combined result and compare geometry.
5. **Validate** against the assembly's interface contracts (§6).

Five conflict classes, which want different handling:

| Class | What it is | Handling |
|---|---|---|
| **Reference** | An anchor no longer resolves, or resolves ambiguously | Re-anchor, with the candidates offered. Often mechanical |
| **Order** | Both layers insert at the same tree position | Ask for an order; show the result of each |
| **Parametric** | Same parameter, two values | Classic conflict. Present both with their stated intents |
| **Geometric** | No parametric conflict, but the combined result self-intersects, breaks a wall, or violates a clearance | Must be *computed* — the model has to be evaluated to find it |
| **Intent** | Both are valid and satisfy the spec, but embody different approaches | **Human decision.** Do not auto-resolve |

The last two are the ones a text-merge mindset misses entirely. A CAD merge can
be syntactically clean and physically wrong: two pockets that individually
leave 3 mm of wall can, together, leave 0.4 mm.

**Geometric conflict detection is therefore not optional and not cheap.** It
requires evaluating the merged model — which is the real cost of this design and
the main reason it needs the worktree/build isolation of §4.3.

---

## 6. Assembly-level work: interface contracts

For level-1 parallelism, the mechanism is not merge at all — it is a **contract**
each part publishes:

```jsonc
{
  "part": "motor_mount",
  "mating": [ { "name": "face_A", "datum": "MountPlane", "bolts": "M4×4 @ 32mm PCD" } ],
  "keep_out": [ { "name": "shaft_sweep", "shape": "cylinder", "r": 14, "h": 40 } ],
  "envelope": { "bbox": [80, 80, 25] },
  "budget": { "mass_g": 120, "material": "AlSi10Mg" }
}
```

An agent editing a neighbouring part may not violate a published contract.
Violations are computed continuously, like CI:

> `bracket` now intrudes 2.3 mm into `motor_mount:shaft_sweep`

This is the CAD analogue of type checking: a cheap, local, always-on check that
catches whole classes of integration failure long before assembly.

Changing your own contract is allowed — and is a **breaking change**, announced
to every dependent part, exactly like an API change.

---

## 7. Review, provenance and safety rails

### Geometric diff

Reviewing a CAD change as a text diff is useless. The review surface is:

- **added / removed volume**, colour-coded, with figures
- **changed faces** highlighted against the base
- **mass properties delta** — mass, centre of gravity, moments of inertia
- **envelope delta** and interference against neighbours
- the layer's **stated intent**, next to what it actually did

That last pairing matters: the most common failure of an automated change is
not that it is wrong, but that it does something other than what it claimed.

### Reviewing inside the model

This repository already has the machinery for a better review surface than a
flat screenshot: `src/Mod/XR` puts you inside the model at any scale. Two
competing layers can be shown overlaid as ghosted variants, muted and unmuted
from the wrist menu with the same weight control the sculpt layers use, and an
interference walked around at 1:12 rather than squinted at in a viewport.

A merge conflict is one of the few review tasks where being *inside* the
geometry is genuinely better, not a gimmick.

### Provenance

Every layer records author, model identity, session, intent, base revision and
validation results. `AI_POLICY.md` in this repository already requires
disclosure of AI assistance; at engineering scale the requirement is stronger —
a dimension nobody can account for is a liability, not just a style violation.

### Pinned parameters

Some values must never change without a human in the loop: material
specifications, safety factors, regulatory clearances, tolerance callouts on
mating features, anything feeding a certification.

These are **pinned**. A layer touching a pinned parameter cannot auto-merge —
it escalates, always, regardless of how confident the agent is. The list is
explicit and versioned, and lives with the assembly.

---

## 8. What is genuinely hard, stated plainly

Concepts are cheap. The parts that would actually be difficult:

1. **Anchor resolution is the whole ballgame.** If semantic selectors resolve
   unreliably, every layer becomes a reference conflict and the system is worse
   than serialising the work. This needs to be prototyped first, before
   anything else is built.
2. **Geometric conflict detection is expensive.** Evaluating merged models on
   every candidate merge is minutes, not milliseconds, on real assemblies.
3. **Constraint solver non-determinism** means level-4 merges are probably out
   of reach, and the design should keep saying so.
4. **Intent capture depends on agents being honest** about what they are doing.
   A layer whose stated intent does not match its operations is worse than no
   intent field, because it invites misplaced trust.
5. **Nobody wants a new file format.** This has to work with `.FCStd` as it is,
   which means layers live beside the document, not instead of it.

---

## Contents of this folder

| File | What it holds |
|---|---|
| `README.md` | This document |
| `SPEC-deviation-layer.md` | A concrete data format for a layer, with worked examples |
| `index.html` | The same concept as a presentation |
