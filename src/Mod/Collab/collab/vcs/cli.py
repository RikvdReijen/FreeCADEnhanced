# SPDX-License-Identifier: LGPL-2.1-or-later
"""``python3 -m collab vcs <command>`` — the product-data commands.

Every command takes ``-C DIR`` for the project directory (default: cwd).

    vcs init      --author NAME
    vcs status
    vcs commit    -m MESSAGE --author NAME
    vcs log       [--ref REF] [--limit N]
    vcs workspace NAME [--from REF]            create and switch
    vcs workspaces
    vcs version   NAME [--notes TEXT]          freeze the current workspace
    vcs versions
    vcs checkout  REF [--force]
    vcs diff      A B
    vcs merge     SOURCE [--into WS] [--ours PATH ...] [--theirs PATH ...]
    vcs verify
    vcs part      NAME [--number N]            assign a part number
    vcs policy    --approvers A B --prefix P --width 5
    vcs release   VERSION PART... --author NAME
    vcs approve   RC --user U [--comment C]    (also reject / obsolete / reopen)
    vcs releases
    vcs bom       [--version V]
    vcs push/pull --remote DIR                 (disk remote; xrsync gives HTTP)
"""

import argparse
import json
import os
import sys
import time

from .release import Policy, ReleaseManager
from .repo import Repository, VcsError
from .sync import LocalTransport, pull, push


def _repo(args):
    return Repository(args.dir or os.getcwd())


def _emit(args, data, text):
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def cmd_init(args):
    repo = Repository.init(args.dir or os.getcwd(), args.author or "")
    print("initialised %s (workspace Main)" % repo.dir)
    return 0


def cmd_status(args):
    repo = _repo(args)
    cur = repo.current()
    changes = repo.status()
    lines = ["on %s %s" % ("workspace" if cur["workspace"] else "version", cur["workspace"] or cur["version"]),
             "head %s" % (cur["snapshot"] or "-")[:10]]
    lines.extend("  %-9s %s" % (c.kind, c.path) for c in changes)
    if not changes:
        lines.append("  clean")
    _emit(args, {"current": cur, "changes": [c.to_dict() for c in changes]}, "\n".join(lines))
    return 1 if changes else 0


def cmd_commit(args):
    snap = _repo(args).commit(args.message, args.author or "")
    print("committed %s: %s" % (snap.short, snap.message))
    return 0


def cmd_log(args):
    repo = _repo(args)
    snaps = repo.history(args.ref, args.limit)
    lines = []
    for s in snaps:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.time))
        parents = "" if len(s.parents) < 2 else "  (merge)"
        lines.append("%s  %s  %-12s %s%s" % (s.short, when, s.author[:12], s.message, parents))
    _emit(args, [s.to_dict() for s in snaps], "\n".join(lines) or "(empty)")
    return 0


def cmd_workspace(args):
    repo = _repo(args)
    repo.create_workspace(args.name, args.from_ref)
    print("workspace %s created from %s and checked out" % (args.name, args.from_ref or "the current head"))
    return 0


def cmd_workspaces(args):
    repo = _repo(args)
    cur = repo.current().get("workspace")
    ws = repo.workspaces()
    lines = ["%s %-20s %s" % ("*" if name == cur else " ", name, info["head"][:10]) for name, info in sorted(ws.items())]
    _emit(args, ws, "\n".join(lines))
    return 0


def cmd_version(args):
    v = _repo(args).create_version(args.name, args.author or "", args.notes or "")
    print("version %s = %s" % (v.name, v.snapshot[:10]))
    return 0


def cmd_versions(args):
    versions = _repo(args).versions()
    lines = ["%-20s %s  %s  %s" % (v.name, v.snapshot[:10], v.author, v.notes) for v in versions.values()]
    _emit(args, {k: v.to_dict() for k, v in versions.items()}, "\n".join(lines) or "(none)")
    return 0


def cmd_checkout(args):
    sid = _repo(args).checkout(args.ref, force=args.force)
    print("checked out %s (%s)" % (args.ref, sid[:10]))
    return 0


def cmd_diff(args):
    detail = _repo(args).diff_detail(args.a, args.b)
    _emit(args, detail, "\n".join("%-9s %s%s" % (d["kind"], d["path"], "  [layer %s]" % d["layer"] if d.get("layer") else "") for d in detail) or "identical")
    return 0


def cmd_merge(args):
    resolutions = {p: "ours" for p in args.ours or []}
    resolutions.update({p: "theirs" for p in args.theirs or []})
    outcome = _repo(args).merge(args.source, args.into, args.author or "", resolutions=resolutions)
    if outcome.ok:
        print("merged %s: %s" % (args.source, "fast-forward" if outcome.fast_forward else outcome.snapshot.short))
        return 0
    print("CONFLICTS — resolve with --ours/--theirs PATH:")
    for path, base, ours, theirs in outcome.conflicts:
        print("  %s  ours %s  theirs %s" % (path, (ours or "-")[:10], (theirs or "-")[:10]))
    return 1


def cmd_verify(args):
    problems = _repo(args).verify()
    for p in problems:
        print(p)
    print("ok" if not problems else "%d problem(s)" % len(problems))
    return 1 if problems else 0


def cmd_part(args):
    part = ReleaseManager(_repo(args)).assign_number(args.name, args.number)
    print("%s = %s rev %s" % (part.name, part.number, part.revision or "-"))
    return 0


def cmd_policy(args):
    rm = ReleaseManager(_repo(args))
    policy = rm.policy
    if args.approvers is not None:
        policy.approvers = list(args.approvers)
        policy.min_approvals = len(policy.approvers) if args.min_approvals is None else args.min_approvals
    if args.prefix:
        policy.prefix = args.prefix
    if args.width:
        policy.width = args.width
    if args.scheme:
        policy.revision_scheme = args.scheme
    rm.set_policy(policy)
    _emit(args, policy.to_dict(), "policy: approvers %s (need %d), numbers %s%s" % (
        policy.approvers, policy.min_approvals, policy.prefix, "0" * policy.width))
    return 0


def cmd_release(args):
    rc = ReleaseManager(_repo(args)).create_candidate(args.version, args.parts, args.author or "", args.notes or "")
    print("candidate %s (%s) for %s: %s — pending" % (rc.id, rc.number, rc.version, ", ".join(rc.items)))
    return 0


def _decision(method):
    def run(args):
        rm = ReleaseManager(_repo(args))
        fn = getattr(rm, method)
        rc = fn(args.rc, args.user) if method == "reopen" else fn(args.rc, args.user, args.comment or "")
        print("%s: %s" % (rc.id, rc.state))
        return 0
    return run


def cmd_releases(args):
    rm = ReleaseManager(_repo(args))
    cands = rm.candidates()
    lines = ["%s %-5s %-10s %-8s %s" % (c.id, c.number, c.version, c.state, ", ".join(c.items)) for c in cands.values()]
    _emit(args, {k: v.to_dict() for k, v in cands.items()}, "\n".join(lines) or "(none)")
    return 0


def cmd_bom(args):
    rows = ReleaseManager(_repo(args)).bill_of_materials(args.version)
    _emit(args, rows, "\n".join("%-10s %-4s %s" % (r["number"], r["revision"] or "-", r["part"]) for r in rows) or "(no parts)")
    return 0


def cmd_push(args):
    report = push(_repo(args), LocalTransport(Repository(args.remote)), args.workspace)
    print(report)
    return 1 if report.diverged else 0


def cmd_pull(args):
    report = pull(_repo(args), LocalTransport(Repository(args.remote)), args.workspace)
    print(report)
    return 1 if report.diverged else 0


def add_parser(sub):
    """Attach the ``vcs`` subcommands to a parent argparse subparsers object."""
    vcs = sub.add_parser("vcs", help="workspaces, versions, releases (product data management)")
    v = vcs.add_subparsers(dest="vcs_command", required=True)

    def cmd(name, func, **kw):
        p = v.add_parser(name, **kw)
        p.add_argument("-C", "--dir", help="project directory (default: cwd)")
        p.set_defaults(func=func)
        return p

    p = cmd("init", cmd_init, help="start tracking a project"); p.add_argument("--author")
    cmd("status", cmd_status, help="changes since the head")
    p = cmd("commit", cmd_commit, help="record the working tree"); p.add_argument("-m", "--message", required=True); p.add_argument("--author")
    p = cmd("log", cmd_log, help="history"); p.add_argument("--ref"); p.add_argument("--limit", type=int)
    p = cmd("workspace", cmd_workspace, help="create a workspace and switch to it"); p.add_argument("name"); p.add_argument("--from", dest="from_ref")
    cmd("workspaces", cmd_workspaces, help="list workspaces")
    p = cmd("version", cmd_version, help="freeze the current workspace as a version"); p.add_argument("name"); p.add_argument("--notes"); p.add_argument("--author")
    cmd("versions", cmd_versions, help="list versions")
    p = cmd("checkout", cmd_checkout, help="switch to a workspace or view a version"); p.add_argument("ref"); p.add_argument("--force", action="store_true")
    p = cmd("diff", cmd_diff, help="what changed between two refs"); p.add_argument("a"); p.add_argument("b")
    p = cmd("merge", cmd_merge, help="merge a workspace/version into a workspace"); p.add_argument("source"); p.add_argument("--into"); p.add_argument("--author")
    p.add_argument("--ours", nargs="*"); p.add_argument("--theirs", nargs="*")
    cmd("verify", cmd_verify, help="check every object and snapshot")
    p = cmd("part", cmd_part, help="assign a part number"); p.add_argument("name"); p.add_argument("--number")
    p = cmd("policy", cmd_policy, help="approvers and numbering"); p.add_argument("--approvers", nargs="*"); p.add_argument("--min-approvals", type=int)
    p.add_argument("--prefix"); p.add_argument("--width", type=int); p.add_argument("--scheme", choices=["alpha", "numeric"])
    p = cmd("release", cmd_release, help="create a release candidate"); p.add_argument("version"); p.add_argument("parts", nargs="+"); p.add_argument("--author"); p.add_argument("--notes")
    for name in ("approve", "reject", "obsolete", "reopen"):
        p = cmd(name, _decision(name), help="%s a release candidate" % name); p.add_argument("rc"); p.add_argument("--user", required=True); p.add_argument("--comment")
    cmd("releases", cmd_releases, help="list release candidates")
    p = cmd("bom", cmd_bom, help="parts with numbers and revisions"); p.add_argument("--version")
    p = cmd("push", cmd_push, help="push to a repository on disk"); p.add_argument("--remote", required=True); p.add_argument("--workspace")
    p = cmd("pull", cmd_pull, help="pull from a repository on disk"); p.add_argument("--remote", required=True); p.add_argument("--workspace")
    return vcs


def main(argv=None):
    parser = argparse.ArgumentParser(prog="collab vcs")
    parser.add_argument("--json", action="store_true")
    add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except VcsError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
