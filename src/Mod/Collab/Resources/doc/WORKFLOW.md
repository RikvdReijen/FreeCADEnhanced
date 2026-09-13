# Workflow: two agents, one flange

The example from `SPEC-deviation-layer.md`, end to end, on the command line.
Everything below runs without FreeCAD except the `snapshot` step, which is
where the document model comes from in real use. `Tests/test_cli.py` is this
walkthrough as a test.

## 0. A document model

Inside FreeCAD (Python console or `freecadcmd`), with `flange.FCStd` open:

```
python3 -m collab snapshot flange.FCStd
```

writes `flange.model.json`: the feature tree, the parameters, and every face
and edge with surface type, normal, area, centroid and adjacency. Its
`revision` is a hash of the file — the `base` layers are recorded against.
Every later command finds this file by name.

## 1. Initialise and claim

```
python3 -m collab init flange.FCStd --base 8f2e19c4
```

Agent A intends to lighten the flange; B intends a taller boss. Each writes a
layer file (schema in `SPEC-deviation-layer.md` §2) and registers its claims
*before* starting:

```
python3 -m collab add   flange.FCStd dev-a41c.json
python3 -m collab claim flange.FCStd dev-a41c
python3 -m collab add   flange.FCStd dev-93b7.json
python3 -m collab claim flange.FCStd dev-93b7
```

`add` refuses a layer whose operations touch targets its `claims.modifies`
does not cover. `claim` warns when two advisory claims overlap and blocks
when either is exclusive — the collision is found now, not after an hour of
divergence.

## 2. Anchors

A's pocket sits on the top face of `Pad3`. Its anchor is a semantic query
plus a fingerprint:

```jsonc
"a_mount_face": {
  "strategy": "semantic",
  "query": { "face_of": "Pad3", "normal": [0, 0, 1], "select": "largest_area" },
  "fingerprint": { "area": 1843.2, "centroid_local": [30, 20, 12], "surface": "plane", "adjacency": 5 },
  "resolved_at_record": "Face6"
}
```

```
python3 -m collab resolve flange.FCStd dev-a41c
  a_mount_face: Resolved('a_mount_face' -> 'Face6' via semantic, 1.00)
```

After an upstream edit renumbers everything, the same command resolves to
`Face9`, and says so. If the top face were gone the result would be `Lost`,
with `was Face6` and the three nearest candidates — and never `Face6`
itself, because that name now points at something else.

## 3. Replay, mute, reorder

```
python3 -m collab replay flange.FCStd
  dev-a41c: ok -> 8f2e19c4+dev-a41c
  dev-93b7: ok -> 8f2e19c4+dev-a41c+dev-93b7

python3 -m collab disable flange.FCStd dev-93b7      # muted; the work stays
python3 -m collab move    flange.FCStd dev-93b7 --before dev-a41c
python3 -m collab diff    flange.FCStd
  added:   LightenPocket1
  changed: Boss1.Length: 20.0 -> 25.0
  changed: Fillet2.Radius: 2.0 -> 1.2
  not measured: mass properties (evaluator has no geometry)
  not measured: envelope (evaluator has no bounding box)
  not measured: added/removed volume and changed faces (needs a geometric evaluator)
```

The last line is the point: without a geometric evaluator the diff says what
it could not measure rather than printing zeros.

## 4. Merge

```
python3 -m collab merge flange.FCStd dev-a41c dev-93b7 --write merged.json
  merge dev-a41c + dev-93b7 on 8f2e19c4: OK
    order: dev-a41c then dev-93b7; 2 interaction(s)
    geometry: NOT evaluated (structural (capabilities: recompute))
    criterion dev-a41c: mass_g <= 90 — unknown
    warning: geometric conflicts not evaluated: evaluator 'structural' has no geometry; ...
```

Two interactions because both layers touch `Boss1`'s subtree (the pocket is
inserted after it, and A's fillet depends on it); neither is a conflict, so
the layers concatenate. The criterion is *unknown*, not passed: nothing
measured the mass.

A third layer that sets `Fillet2.Radius` to 3.0 where A set it to 1.2:

```
python3 -m collab merge flange.FCStd dev-a41c dev-c0de
  merge dev-a41c + dev-c0de on 8f2e19c4: NOT MERGEABLE
    conflict [parametric/value] Fillet2.Radius: dev-a41c sets 1.2, dev-c0de sets 3.0
        -> human picks; both values are shown with their stated intents
```

`--json` gives the same with both operations and both intents in `detail`.

With `project.contracts.json` beside the document:

```jsonc
{ "pinned": ["Fillet2.Radius"], "contracts": [{ "part": "flange", "budget": { "mass_g": 120 } }] }
```

the same clean merge escalates instead:

```
  escalate: Fillet2.Radius is pinned; a human must approve this merge
```

## 5. The case the format exists for

Two pockets that each leave 3 mm of wall and together leave 0.4 mm. Neither
layer conflicts with the other parametrically; both carry
`min_wall_mm >= 2.5` as a success criterion. With an evaluator that can
measure the wall (`Tests/test_merge.py::WallEvaluator` states the numbers;
`FreeCADEvaluator` would compute them), the merge reports:

```
  conflict [geometric/thin_wall] combined result: minimum wall is 0.4 mm ... (neither layer had this alone)
  conflict [geometric/criteria_regression] dev-a41c: 'min_wall_mm >= 2.5' held alone (3.0) and fails on the merged result (0.4)
  criterion dev-a41c: mass_g <= 90 — ok (alone 85.0, together 70.0)
  criterion dev-a41c: min_wall_mm >= 2.5 — REGRESSED (alone 3.0, together 0.4)
```

With the structural evaluator the same merge is `OK` — and marked
`geometry: NOT evaluated`. Both answers are honest; only the second one is
useful, which is why the evaluator is the expensive part of this design.

## 6. Rebase

When the base moves:

```
python3 -m collab snapshot flange.FCStd                       # new revision
python3 -m collab rebase   flange.FCStd dev-a41c              # re-anchor, drop stale validation
```

`rebase` replays the layer on the new model; if that works it rewrites the
layer with the new `base`, fresh fingerprints and fresh `resolved_at_record`
values, and clears `validation` — numbers produced against the old stack are
not carried forward as if they were current.
