# SPDX-License-Identifier: LGPL-2.1-or-later
"""Command line: ``python3 -m collab <command>``.

Works on a document *model* (a JSON snapshot, see ``collab.model``) so every
command runs without FreeCAD. Inside FreeCAD, ``snapshot`` writes that
model from the open document; from then on the two are the same workflow.

    collab init      housing.FCStd --base 8f2e19c4
    collab list      housing.FCStd
    collab show      housing.FCStd dev-a41c
    collab add       housing.FCStd dev-a41c.json
    collab enable    housing.FCStd dev-a41c      /  disable
    collab move      housing.FCStd dev-a41c --after dev-93b7
    collab check     housing.FCStd
    collab resolve   housing.FCStd dev-a41c --model housing.model.json
    collab replay    housing.FCStd --model housing.model.json [--upto ID]
    collab diff      housing.FCStd --model housing.model.json
    collab merge     housing.FCStd dev-a41c dev-93b7 --model housing.model.json [--write]
    collab rebase    housing.FCStd dev-a41c --model housing.model.json
    collab claim     housing.FCStd dev-a41c [--exclusive] [--release]
    collab validate  dev-a41c.json

Exit status is 0 for a clean result, 1 for conflicts or failures, 2 for a
usage or file error.
"""

import argparse
import json
import os
import sys

from .anchors import resolve_all
from .claims import ClaimRegistry, undeclared_targets
from .contracts import ContractSet
from .errors import CollabError
from .evaluate import StructuralEvaluator
from .merge import merge
from .model import DocumentModel
from .replay import rebase, replay
from .schema import Layer
from .stack import evaluate_stack, geometric_diff
from .store import LayerStore


def _load_model(args, store=None):
    path = getattr(args, "model", None)
    if path is None and store is not None:
        guess = os.path.splitext(args.document)[0] + ".model.json"
        if os.path.isfile(guess):
            path = guess
    if path is None:
        raise CollabError("a document model is needed: pass --model housing.model.json (see 'collab snapshot')")
    model = DocumentModel.load(path)
    if store is not None and not model.revision:
        try:
            model.revision = store.load_index().base
        except CollabError:
            pass
    return model


def _load_contracts(store):
    path = store.contracts_path
    return ContractSet.load(path) if os.path.isfile(path) else None


def _evaluator(args):
    return StructuralEvaluator()


def _emit(args, data, text):
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


# -- commands --------------------------------------------------------------


def cmd_init(args):
    store = LayerStore(args.document)
    index = store.init(args.base)
    _emit(args, index.to_json(), f"initialised {store.directory} at base {args.base!r}")
    return 0


def cmd_list(args):
    store = LayerStore(args.document)
    index = store.load_index()
    rows = []
    for layer_id in index.order:
        try:
            layer = store.load_layer(layer_id)
            name, ops = layer.name, len(layer.operations)
            author = layer.author.id if layer.author else "?"
        except CollabError as exc:
            name, ops, author = f"<{exc}>", 0, "?"
        rows.append({"id": layer_id, "enabled": index.enabled.get(layer_id, True), "name": name, "operations": ops, "author": author})
    lines = [f"base {index.base or '(none)'}  document {index.document}"]
    for row in rows:
        flag = "on " if row["enabled"] else "off"
        lines.append(f"  [{flag}] {row['id']:<14} {row['operations']:>3} op  {row['author']:<14} {row['name']}")
    if not rows:
        lines.append("  (no layers)")
    _emit(args, {"index": index.to_json(), "layers": rows}, "\n".join(lines))
    return 0


def cmd_show(args):
    store = LayerStore(args.document)
    layer = store.load_layer(args.layer)
    lines = [f"{layer.id}  {layer.name}", f"  base {layer.base}  author {layer.author.to_json() if layer.author else None}"]
    if layer.intent:
        lines.append(f"  intent: {layer.intent.goal}")
        for c in layer.intent.success_criteria:
            lines.append(f"    criterion {c.describe()}")
    lines.append(f"  claims: modifies {layer.claims.modifies} mode {layer.claims.mode}")
    for dep in layer.claims.depends:
        lines.append(f"    depends {dep.key}: {dep.reason}")
    for name, anchor in layer.anchors.items():
        lines.append(f"  anchor {name}: {anchor.strategy} {anchor.query} (was {anchor.resolved_at_record})")
    for i, op in enumerate(layer.operations):
        lines.append(f"  {i:>2}. {op.describe()}")
    undeclared = undeclared_targets(layer)
    if undeclared:
        lines.append(f"  WARNING undeclared targets: {undeclared}")
    if layer.validation.data:
        lines.append(f"  validation: {layer.validation.to_json()}")
    _emit(args, layer.to_json(), "\n".join(lines))
    return 0


def cmd_validate(args):
    problems = []
    for path in args.files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                layer = Layer.loads(handle.read(), path=os.path.basename(path))
        except (CollabError, OSError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        undeclared = undeclared_targets(layer)
        if undeclared:
            problems.append(f"{path}: operations touch undeclared targets {undeclared}")
        if layer.author is None:
            problems.append(f"{path}: no author — provenance is required")
        print(f"{path}: {layer.id}, {len(layer.operations)} operations" + (" — problems" if undeclared else " — ok"))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def cmd_add(args):
    store = LayerStore(args.document)
    with open(args.file, "r", encoding="utf-8") as handle:
        layer = Layer.loads(handle.read(), path=os.path.basename(args.file))
    undeclared = undeclared_targets(layer)
    if undeclared and not args.force:
        raise CollabError(f"layer touches undeclared targets {undeclared}; fix claims.modifies or pass --force")
    store.add(layer, enabled=not args.disabled)
    print(f"added {layer.id}")
    return 0


def cmd_enable(args, enabled=True):
    store = LayerStore(args.document)
    store.set_enabled(args.layer, enabled)
    print(f"{args.layer} {'enabled' if enabled else 'disabled'}")
    return 0


def cmd_disable(args):
    return cmd_enable(args, enabled=False)


def cmd_move(args):
    store = LayerStore(args.document)
    index = store.move(args.layer, to=args.to, after=args.after, before=args.before)
    print("order: " + ", ".join(index.order))
    return 0


def cmd_remove(args):
    store = LayerStore(args.document)
    store.remove(args.layer, delete_file=not args.keep_file)
    print(f"removed {args.layer}")
    return 0


def cmd_check(args):
    store = LayerStore(args.document)
    problems = store.check()
    for problem in problems:
        print(problem)
    if not problems:
        print("ok")
    return 1 if problems else 0


def cmd_resolve(args):
    store = LayerStore(args.document)
    layer = store.load_layer(args.layer)
    model = _load_model(args, store)
    results = resolve_all(layer.anchors, model)
    bad = 0
    for name, resolution in results.items():
        print(f"{name}: {resolution!r}")
        for note in resolution.notes:
            print(f"    {note}")
        if not resolution.ok:
            bad += 1
    if getattr(args, "json", False):
        print(json.dumps({k: v.to_json() for k, v in results.items()}, indent=2))
    return 1 if bad else 0


def cmd_replay(args):
    store = LayerStore(args.document)
    model = _load_model(args, store)
    index = store.load_index()
    layers = store.layers()
    result = evaluate_stack(model, layers, index, _evaluator(args), pinned=_load_contracts(store), upto=args.upto)
    for rep in result.results:
        status = "ok" if rep.ok else "FAILED"
        print(f"{rep.layer.id}: {status}" + (f" -> {rep.doc.revision}" if rep.ok else ""))
        for failure in rep.failures:
            print(f"    {failure.kind}: {failure.message}")
        for target in rep.pinned_touched:
            print(f"    pinned: {target}")
    for skipped in result.skipped:
        print(f"{skipped}: muted")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result.doc.to_json(), handle, indent=2)
        print(f"wrote {args.out}")
    if getattr(args, "json", False):
        print(json.dumps(result.to_json(), indent=2))
    return 0 if result.ok else 1


def cmd_diff(args):
    store = LayerStore(args.document)
    model = _load_model(args, store)
    index = store.load_index()
    layers = store.layers()
    evaluator = _evaluator(args)
    before = evaluate_stack(model, layers, index, evaluator, upto=args.before) if args.before else None
    after = evaluate_stack(model, layers, index, evaluator, upto=args.after)
    base_doc = before.doc if before else model
    diff = geometric_diff(base_doc, after.doc, evaluator)
    _emit(args, diff.to_json(), diff.summary())
    return 0


def cmd_merge(args):
    store = LayerStore(args.document)
    model = _load_model(args, store)
    left, right = store.load_layer(args.left), store.load_layer(args.right)
    result = merge(model, left, right, _evaluator(args), contracts=_load_contracts(store))
    _emit(args, result.to_json(), result.summary())
    if args.write and result.merged is not None:
        path = args.write
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(result.merged.dumps())
        print(f"wrote merged layer to {path}" + ("" if result.ok else " (NOT clean — review the conflicts above)"))
    return 0 if result.ok else 1


def cmd_rebase(args):
    store = LayerStore(args.document)
    model = _load_model(args, store)
    layer = store.load_layer(args.layer)
    rebased, result = rebase(layer, model, _evaluator(args))
    if rebased is None:
        print(f"{layer.id} does not replay on {model.revision!r}:")
        for failure in result.failures:
            print(f"    {failure.kind}: {failure.message}")
        return 1
    out = args.out or store.layer_path(layer.id)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(rebased.dumps())
    print(f"rebased {layer.id} onto {model.revision!r}; wrote {out}")
    return 0


def cmd_claim(args):
    store = LayerStore(args.document)
    registry = ClaimRegistry.load(store.claims_path) if os.path.isfile(store.claims_path) else ClaimRegistry()
    if args.release:
        registry.release(args.layer)
        registry.save(store.claims_path)
        print(f"released {args.layer}")
        return 0
    layer = store.load_layer(args.layer)
    claims = layer.claims
    if args.exclusive:
        claims.mode = "exclusive"
    issues = registry.register(args.layer, claims, force=args.force)
    for issue in issues:
        print(f"{issue.severity}: {issue.message}")
    blocked = any(i.blocking for i in issues) and not args.force
    if not blocked:
        registry.save(store.claims_path)
        print(f"claimed {claims.modifies} for {args.layer} ({claims.mode})")
    return 1 if blocked else 0


def cmd_snapshot(args):
    from .freecad_adapter import document_model, freecad_available

    if not freecad_available():
        raise CollabError("snapshot needs FreeCAD: run it from the FreeCAD Python console or freecadcmd")
    import FreeCAD

    doc = FreeCAD.openDocument(args.document) if not FreeCAD.ActiveDocument else FreeCAD.ActiveDocument
    model = document_model(doc, revision=args.base)
    out = args.out or os.path.splitext(args.document)[0] + ".model.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(model.to_json(), handle, indent=2)
    print(f"wrote {out}: {len(model.features)} features, {len(model.entities)} entities, revision {model.revision!r}")
    return 0


# -- parser ----------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog="collab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output where supported")
    sub = parser.add_subparsers(dest="command", required=True)

    def doc_cmd(name, func, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("document", help="the .FCStd document (its .layers/ folder sits beside it)")
        p.set_defaults(func=func)
        return p

    p = doc_cmd("init", cmd_init, help="create the .layers/ folder")
    p.add_argument("--base", required=True, help="document revision the layers are recorded against")

    doc_cmd("list", cmd_list, help="list layers in order")
    doc_cmd("check", cmd_check, help="check the folder for inconsistencies")

    p = doc_cmd("show", cmd_show, help="show one layer")
    p.add_argument("layer")

    p = doc_cmd("add", cmd_add, help="add a layer file to the store")
    p.add_argument("file")
    p.add_argument("--disabled", action="store_true")
    p.add_argument("--force", action="store_true", help="add even if claims do not cover the operations")

    for name, func in (("enable", cmd_enable), ("disable", cmd_disable)):
        p = doc_cmd(name, func, help=f"{name} (un/mute) a layer")
        p.add_argument("layer")

    p = doc_cmd("move", cmd_move, help="reorder a layer")
    p.add_argument("layer")
    p.add_argument("--to", type=int)
    p.add_argument("--after")
    p.add_argument("--before")

    p = doc_cmd("remove", cmd_remove, help="remove a layer from the index")
    p.add_argument("layer")
    p.add_argument("--keep-file", action="store_true")

    p = doc_cmd("resolve", cmd_resolve, help="resolve a layer's anchors against a document model")
    p.add_argument("layer")
    p.add_argument("--model")

    p = doc_cmd("replay", cmd_replay, help="replay the enabled stack")
    p.add_argument("--model")
    p.add_argument("--upto", help="stop after this layer")
    p.add_argument("--out", help="write the resulting document model here")

    p = doc_cmd("diff", cmd_diff, help="geometric diff between two points in the stack")
    p.add_argument("--model")
    p.add_argument("--before", help="layer id: state after this layer is the 'before' (default: base)")
    p.add_argument("--after", help="layer id: state after this layer is the 'after' (default: whole stack)")

    p = doc_cmd("merge", cmd_merge, help="merge two layers against the base")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--model")
    p.add_argument("--write", help="write the merged layer to this file")

    p = doc_cmd("rebase", cmd_rebase, help="re-record a layer against a new base model")
    p.add_argument("layer")
    p.add_argument("--model")
    p.add_argument("--out")

    p = doc_cmd("claim", cmd_claim, help="register a layer's claims in the shared registry")
    p.add_argument("layer")
    p.add_argument("--exclusive", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--force", action="store_true")

    p = doc_cmd("snapshot", cmd_snapshot, help="(inside FreeCAD) write a document model")
    p.add_argument("--base", help="revision to stamp; default is a hash of the file")
    p.add_argument("--out")

    p = sub.add_parser("validate", help="check layer files against the schema")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_validate)

    from .vcs.cli import add_parser as add_vcs_parser

    add_vcs_parser(sub)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CollabError as exc:  # includes VcsError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
