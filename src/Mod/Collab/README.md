# Collab — deviation layers for parallel AI–human CAD work

An implementation of [`docs/concepts/ai-cad-collaboration/`](../../../docs/concepts/ai-cad-collaboration/README.md):
how several agents, human and AI, work on one parametric model at the same
time without destroying each other's work.

The concept in one line: git's three-way merge assumes a stable identity per
line, parametric CAD does not have one (`Face6` becomes `Face9` after an
unrelated edit), so nothing here merges files. Everything operates on recorded
**operations** against **stable references**, and treats geometry as
something to verify against.

Pure Python. Nothing outside `collab/freecad_adapter.py` imports FreeCAD, and
that module imports cleanly without it. 145 unit tests run in a bare
interpreter:

```
python3 src/Mod/Collab/Tests/run_all.py
```

## What is here

| Module | Concept section | What it does |
|---|---|---|
| `collab/model.py` | README §1 | The document view: ordered feature tree, parameters, topological entities with the attributes anchors need. Entity names are carried for diagnostics and treated as volatile. |
| `collab/schema.py` | SPEC §2–3 | The layer format. Reader, writer, validation. `set_param` without `from` is refused; an unknown `op` is refused rather than skipped. |
| `collab/anchors.py` | README §4.1, SPEC §4 | `resolve(anchor, doc) → Resolved \| Ambiguous \| Lost`. Datum by name, then semantic query, then fingerprint. A close second is `Ambiguous`, never a win. **No fallback to the recorded topological name** — a test pins that the field is read in exactly one place, and that place is a diagnostic message. |
| `collab/claims.py` | README §4.2 | The claim registry: overlapping advisory claims warn, exclusive claims block, watched dependencies notify. `undeclared_targets()` keeps claims honest against the operations. |
| `collab/replay.py` | SPEC §3 | Applies a layer to a document model. Every failure mode is a `ReplayFailure`, not an exception — it is input to the merge. `rebase()` re-records a layer against a new base and drops stale validation. |
| `collab/merge.py` | SPEC §5, README §5 | Two layers, one base, the five conflict classes. Parametric and intent conflicts are presented with both sides' stated intents, never auto-resolved. Pinned parameters escalate even when the merge is clean. Step 6 — re-running both layers' success criteria on the merged result — is what catches the two-pockets-leave-0.4 mm case. |
| `collab/evaluate.py` | README §5, §8.2 | The evaluator interface. `StructuralEvaluator` checks the tree and *says* it evaluated no geometry; `ScriptedEvaluator` lets a test or an external solver supply geometry. A check that could not run is `None`, never `True`. |
| `collab/stack.py` | README §3, §7 | Muting, reordering, `upto`, and the geometric diff — with a list of what the evaluator could not measure. |
| `collab/contracts.py` | README §6, §7 | Interface contracts (mating, keep-out, envelope, budget), breaking-change detection, and the pinned-parameter list. |
| `collab/store.py` | SPEC §1 | The `.layers/` folder. `index.json` is written one entry per line so it merges as text. Atomic writes. |
| `collab/freecad_adapter.py` | — | Snapshot an `App.Document` into a model; materialise a model back into a document; `FreeCADEvaluator` with mass, bounding box and shape validity. |
| `collab/cli.py` | — | `python3 -m collab init / add / list / enable / disable / move / resolve / replay / diff / merge / rebase / claim / check / validate / snapshot`. |

`Resources/doc/WORKFLOW.md` walks through the whole thing on the flange
example from the spec.

## Product data management (`collab/vcs/`)

An Onshape-shaped version system beside the project, in `.fcvcs/`:

| Module | What it does |
|---|---|
| `vcs/repo.py` | **Workspaces** (live lines of work), **versions** (named, immutable snapshots), **history** (content-addressed snapshots with author/time/message, `verify()` re-hashes everything), checkout, diff, and a three-way **merge** per file — the deviation-layer index merges by union; the binary `.FCStd` only when one side left it alone, otherwise a conflict to resolve with `--ours/--theirs` or with the layer merge above. |
| `vcs/release.py` | Part numbers from a numbering policy (assigned once), revisions (A, B, C… or numeric) advanced on release, release candidates that need the policy's approvers, reject/reopen, obsolete, where-used, and a bill of materials per version. |
| `vcs/sync.py` | Push and pull between repositories over a transport: another directory, or the XR sync server's `POST /api/v1/vcs`. Diverged histories are reported, never overwritten. |
| `vcs/cli.py` | `python3 -m collab vcs init / status / commit / log / workspace / version / checkout / diff / merge / release / approve / bom / push / pull …` |

`Tests/test_vcs.py` covers it; the XR module's `test_room_wire.py` pushes
and pulls through the HTTP server.

## Honest limits

- **The geometric conflict check is only as good as the evaluator.** With the
  structural evaluator a merge can be *syntactically* clean and the result
  says so in as many words: `geometry: NOT evaluated`. `FreeCADEvaluator`
  materialises the model into a document and measures it; wall thickness
  and interference against keep-outs are not yet computed by it and are
  reported as unchecked.
- **Materialising into FreeCAD covers a subset.** Parameters, metadata,
  removal, reordering, datums, and sketch-based PartDesign features with
  circle/line/rectangle geometry. Sketch *constraints* are deliberately not
  replayed (README §2, level 4). Fillets and chamfers need edge references
  the layer format does not carry yet. Everything unsupported is listed in
  the materialise report, never approximated.
- **Nothing has been run against a real FreeCAD document.** The adapter is
  exercised against a stub of the FreeCAD API in `Tests/stubs.py`, which
  covers the calls made but not FreeCAD's behaviour.
- **No sketch-level merge, by design.** Two `edit_sketch` operations on one
  sketch are refused with an intent conflict.
- **Level-1 parallelism** (different documents) relies on git; this module
  supplies the contract checks, not a repository.

## Layout

```
src/Mod/Collab/
├── Init.py                 makes `import collab` work inside FreeCAD
├── collab/                 the library (see table above)
├── Tests/                  unittest suite; fixtures.py is the flange, stubs.py the FreeCAD stand-in
└── Resources/doc/          WORKFLOW.md
```
