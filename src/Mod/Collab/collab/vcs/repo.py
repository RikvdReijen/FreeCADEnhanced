# SPDX-License-Identifier: LGPL-2.1-or-later
"""A product-data repository beside the project: workspaces, versions, history, merge.

The model is Onshape's rather than git's, because that is the one engineers
already have in their heads:

* a **workspace** is a live line of work (git would say branch); files in the
  project directory belong to the *current* workspace;
* a **version** is a named, immutable snapshot of a workspace — "V3 sent to
  the machine shop" — that can be viewed, branched from and released, and
  never changes;
* **history** is every commit on a workspace, each a snapshot with its
  author, time and message, so any state can be restored;
* **merge** brings a workspace's changes into another, three-way against
  the common ancestor. Text-like sidecar files (deviation layers, contracts,
  models) merge per file; the binary ``.FCStd`` merges only when one side
  left it alone — otherwise it is a conflict, and the deviation-layer merge
  (`collab.merge`) is the tool for the content underneath.

Storage is ``<project>/.fcvcs/``: content-addressed blobs under ``objects/``,
one JSON per snapshot under ``snapshots/``, and three small JSON files for
workspaces, versions and the current state. Everything is verifiable —
``verify()`` re-hashes every object — and nothing here needs git, though
the folder can live in a git repository happily (it is plain files).
"""

import hashlib
import json
import os
import shutil
import time

from ..errors import CollabError
from ..store import write_atomic

VCS_DIR = ".fcvcs"
DEFAULT_WORKSPACE = "Main"
IGNORED_DIRS = (VCS_DIR, "__pycache__", ".git")
IGNORED_SUFFIXES = (".tmp", ".FCBak", ".lock", ".pyc", ".swp", "~")


class VcsError(CollabError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Snapshot(object):
    __slots__ = ("id", "parents", "tree", "author", "time", "message", "workspace", "meta")

    def __init__(self, parents, tree, author, time_, message, workspace, meta=None, id=None):
        self.parents = list(parents)
        self.tree = dict(tree)
        self.author = author
        self.time = float(time_)
        self.message = message
        self.workspace = workspace
        self.meta = dict(meta or {})
        self.id = id or self.compute_id()

    def body(self):
        return {"parents": self.parents, "tree": self.tree, "author": self.author, "time": self.time,
                "message": self.message, "workspace": self.workspace, "meta": self.meta}

    def compute_id(self):
        return _sha(_canonical(self.body()))

    def to_dict(self):
        d = self.body()
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("parents", []), d.get("tree", {}), d.get("author", ""), d.get("time", 0.0),
                   d.get("message", ""), d.get("workspace", ""), d.get("meta"), d.get("id"))

    @property
    def short(self):
        return self.id[:10]

    def __repr__(self):
        return "Snapshot(%s %r)" % (self.short, self.message)


class Version(object):
    __slots__ = ("name", "snapshot", "author", "time", "notes", "workspace")

    def __init__(self, name, snapshot, author="", time_=0.0, notes="", workspace=""):
        self.name = name
        self.snapshot = snapshot
        self.author = author
        self.time = float(time_)
        self.notes = notes
        self.workspace = workspace

    def to_dict(self):
        return {"name": self.name, "snapshot": self.snapshot, "author": self.author, "time": self.time,
                "notes": self.notes, "workspace": self.workspace}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d["snapshot"], d.get("author", ""), d.get("time", 0.0), d.get("notes", ""), d.get("workspace", ""))


class FileChange(object):
    __slots__ = ("path", "kind", "before", "after")

    def __init__(self, path, kind, before=None, after=None):
        self.path = path
        self.kind = kind  # added | removed | modified
        self.before = before
        self.after = after

    def to_dict(self):
        return {"path": self.path, "kind": self.kind, "before": self.before, "after": self.after}

    def __repr__(self):
        return "FileChange(%s %s)" % (self.kind, self.path)


class MergeOutcome(object):
    def __init__(self):
        self.snapshot = None
        self.tree = {}
        self.conflicts = []   # [(path, base_sha, ours_sha, theirs_sha)]
        self.taken = []       # [(path, "ours"|"theirs"|"both"|"merged")]
        self.fast_forward = False

    @property
    def ok(self):
        return not self.conflicts

    def to_dict(self):
        return {"snapshot": self.snapshot.id if self.snapshot else None, "fast_forward": self.fast_forward,
                "conflicts": [{"path": p, "base": b, "ours": o, "theirs": t} for p, b, o, t in self.conflicts],
                "taken": [{"path": p, "side": s} for p, s in self.taken]}


class Repository(object):
    def __init__(self, project_dir):
        self.project_dir = os.path.abspath(project_dir)
        self.dir = os.path.join(self.project_dir, VCS_DIR)

    # -- layout ----------------------------------------------------------

    @property
    def objects_dir(self):
        return os.path.join(self.dir, "objects")

    @property
    def snapshots_dir(self):
        return os.path.join(self.dir, "snapshots")

    def _path(self, name):
        return os.path.join(self.dir, name)

    def exists(self):
        return os.path.isfile(self._path("workspaces.json"))

    @classmethod
    def init(cls, project_dir, author="", workspace=DEFAULT_WORKSPACE, message="Initial state"):
        repo = cls(project_dir)
        if repo.exists():
            raise VcsError("%s is already a repository" % project_dir)
        os.makedirs(repo.objects_dir, exist_ok=True)
        os.makedirs(repo.snapshots_dir, exist_ok=True)
        repo._write("workspaces.json", {})
        repo._write("versions.json", {})
        repo._write("current.json", {"workspace": workspace, "version": None})
        tree = repo._snapshot_tree()
        root = Snapshot([], tree, author, time.time(), message, workspace)
        repo._save_snapshot(root)
        repo._set_head(workspace, root.id, created_from=None)
        return repo

    def _read(self, name, default=None):
        path = self._path(name)
        if not os.path.isfile(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, name, data):
        write_atomic(self._path(name), json.dumps(data, indent=2, sort_keys=True) + "\n")

    def _require(self):
        if not self.exists():
            raise VcsError("%s is not a repository; run init first" % self.project_dir)

    # -- objects ---------------------------------------------------------

    def put_blob(self, data):
        sha = _sha(data)
        path = os.path.join(self.objects_dir, sha)
        if not os.path.isfile(path):
            os.makedirs(self.objects_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        return sha

    def get_blob(self, sha):
        path = os.path.join(self.objects_dir, sha)
        if not os.path.isfile(path):
            raise VcsError("missing object %s" % sha[:10])
        with open(path, "rb") as handle:
            return handle.read()

    def has_blob(self, sha):
        return os.path.isfile(os.path.join(self.objects_dir, sha))

    def _save_snapshot(self, snapshot):
        write_atomic(os.path.join(self.snapshots_dir, snapshot.id + ".json"),
                     json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n")
        return snapshot

    def snapshot(self, snapshot_id):
        snapshot_id = self.resolve(snapshot_id)
        path = os.path.join(self.snapshots_dir, snapshot_id + ".json")
        if not os.path.isfile(path):
            raise VcsError("no snapshot %s" % snapshot_id[:10])
        with open(path, "r", encoding="utf-8") as handle:
            return Snapshot.from_dict(json.load(handle))

    def has_snapshot(self, snapshot_id):
        return os.path.isfile(os.path.join(self.snapshots_dir, snapshot_id + ".json"))

    def snapshots(self):
        if not os.path.isdir(self.snapshots_dir):
            return []
        return [n[:-5] for n in os.listdir(self.snapshots_dir) if n.endswith(".json")]

    def resolve(self, ref):
        """A workspace name, version name, snapshot id or unique prefix -> snapshot id."""
        workspaces = self.workspaces()
        if ref in workspaces:
            return workspaces[ref]["head"]
        versions = self.versions()
        if ref in versions:
            return versions[ref].snapshot
        if self.has_snapshot(ref):
            return ref
        matches = [s for s in self.snapshots() if s.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise VcsError("ambiguous reference %r" % ref)
        raise VcsError("unknown reference %r" % ref)

    # -- working tree ----------------------------------------------------

    def tracked_paths(self):
        out = []
        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in files:
                if name.endswith(IGNORED_SUFFIXES) or name.startswith(".tmp-"):
                    continue
                full = os.path.join(root, name)
                out.append(os.path.relpath(full, self.project_dir).replace(os.sep, "/"))
        return sorted(out)

    def _snapshot_tree(self):
        tree = {}
        for rel in self.tracked_paths():
            with open(os.path.join(self.project_dir, rel), "rb") as handle:
                tree[rel] = self.put_blob(handle.read())
        return tree

    def status(self):
        """Changes in the working tree against the current head."""
        self._require()
        head = self.head()
        base = self.snapshot(head).tree if head else {}
        now = {}
        for rel in self.tracked_paths():
            with open(os.path.join(self.project_dir, rel), "rb") as handle:
                now[rel] = _sha(handle.read())
        return _diff_trees(base, now)

    def is_dirty(self):
        return bool(self.status())

    # -- workspaces ------------------------------------------------------

    def workspaces(self):
        return self._read("workspaces.json", {}) or {}

    def _set_head(self, workspace, snapshot_id, created_from="keep"):
        ws = self.workspaces()
        entry = ws.get(workspace, {"created_from": None, "created": time.time()})
        entry["head"] = snapshot_id
        if created_from != "keep":
            entry["created_from"] = created_from
        ws[workspace] = entry
        self._write("workspaces.json", ws)

    def current(self):
        self._require()
        cur = self._read("current.json", {}) or {}
        head = None
        if cur.get("workspace"):
            head = self.workspaces().get(cur["workspace"], {}).get("head")
        elif cur.get("version"):
            head = self.versions()[cur["version"]].snapshot
        return {"workspace": cur.get("workspace"), "version": cur.get("version"), "snapshot": head}

    def head(self, workspace=None):
        if workspace is None:
            return self.current()["snapshot"]
        ws = self.workspaces()
        if workspace not in ws:
            raise VcsError("no workspace %r" % workspace)
        return ws[workspace]["head"]

    def create_workspace(self, name, from_ref=None, switch=True):
        """A new line of work starting from a version, a workspace or a snapshot."""
        self._require()
        if name in self.workspaces():
            raise VcsError("workspace %r already exists" % name)
        if not name or "/" in name or name.startswith("."):
            raise VcsError("bad workspace name %r" % name)
        start = self.resolve(from_ref) if from_ref else self.head()
        self._set_head(name, start, created_from=from_ref or self.current().get("workspace"))
        if switch:
            self.checkout(name)
        return name

    def delete_workspace(self, name):
        ws = self.workspaces()
        if name not in ws:
            raise VcsError("no workspace %r" % name)
        if self.current().get("workspace") == name:
            raise VcsError("cannot delete the current workspace")
        if len(ws) == 1:
            raise VcsError("cannot delete the only workspace")
        del ws[name]
        self._write("workspaces.json", ws)

    # -- commits ---------------------------------------------------------

    def commit(self, message, author="", allow_empty=False, meta=None):
        """Record the working tree on the current workspace."""
        self._require()
        cur = self.current()
        if not cur.get("workspace"):
            raise VcsError("a version is checked out (read-only); create a workspace from it first")
        workspace = cur["workspace"]
        head = cur["snapshot"]
        tree = self._snapshot_tree()
        if head and self.snapshot(head).tree == tree and not allow_empty:
            raise VcsError("nothing changed since %s" % head[:10])
        snapshot = Snapshot([head] if head else [], tree, author, time.time(), message, workspace, meta)
        self._save_snapshot(snapshot)
        self._set_head(workspace, snapshot.id)
        return snapshot

    def history(self, ref=None, limit=None):
        """Snapshots reachable from ``ref`` (default: current head), newest first."""
        self._require()
        start = self.resolve(ref) if ref else self.head()
        if not start:
            return []
        seen, out = set(), []
        frontier = [start]
        while frontier:
            frontier.sort(key=lambda s: -self.snapshot(s).time)
            sid = frontier.pop(0)
            if sid in seen:
                continue
            seen.add(sid)
            snap = self.snapshot(sid)
            out.append(snap)
            if limit and len(out) >= limit:
                break
            frontier.extend(p for p in snap.parents if p not in seen)
        return out

    def ancestors(self, snapshot_id):
        seen = set()
        stack = [snapshot_id]
        while stack:
            sid = stack.pop()
            if sid in seen:
                continue
            seen.add(sid)
            stack.extend(self.snapshot(sid).parents)
        return seen

    def is_ancestor(self, older, newer):
        return older in self.ancestors(newer)

    def common_ancestor(self, a, b):
        """The nearest snapshot both descend from (latest by time among the shared set)."""
        shared = self.ancestors(a) & self.ancestors(b)
        if not shared:
            return None
        return max(shared, key=lambda s: (self.snapshot(s).time, s))

    # -- versions --------------------------------------------------------

    def versions(self):
        return {k: Version.from_dict(v) for k, v in (self._read("versions.json", {}) or {}).items()}

    def create_version(self, name, author="", notes="", workspace=None):
        """Freeze the workspace head under a name. Versions never change."""
        self._require()
        versions = self._read("versions.json", {}) or {}
        if name in versions:
            raise VcsError("version %r already exists; versions are immutable" % name)
        if not name:
            raise VcsError("a version needs a name")
        workspace = workspace or self.current().get("workspace")
        if not workspace:
            raise VcsError("no workspace to version")
        if self.is_dirty() and workspace == self.current().get("workspace"):
            raise VcsError("commit the working tree before creating a version")
        head = self.head(workspace)
        versions[name] = Version(name, head, author, time.time(), notes, workspace).to_dict()
        self._write("versions.json", versions)
        return Version.from_dict(versions[name])

    # -- checkout --------------------------------------------------------

    def checkout(self, ref, force=False):
        """Make the working tree match a workspace head or a version."""
        self._require()
        if self.is_dirty() and not force:
            raise VcsError("the working tree has uncommitted changes; commit them or pass force=True")
        workspaces = self.workspaces()
        versions = self.versions()
        if ref in workspaces:
            target, current = workspaces[ref]["head"], {"workspace": ref, "version": None}
        elif ref in versions:
            target, current = versions[ref].snapshot, {"workspace": None, "version": ref}
        else:
            raise VcsError("checkout wants a workspace or a version name, not %r" % ref)
        self._restore(self.snapshot(target).tree)
        self._write("current.json", current)
        return target

    def _restore(self, tree):
        wanted = set(tree)
        for rel in self.tracked_paths():
            if rel not in wanted:
                os.remove(os.path.join(self.project_dir, rel))
        for rel, sha in tree.items():
            path = os.path.join(self.project_dir, rel)
            os.makedirs(os.path.dirname(path) or self.project_dir, exist_ok=True)
            data = self.get_blob(sha)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    if _sha(handle.read()) == sha:
                        continue
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        # prune emptied directories
        for root, dirs, files in os.walk(self.project_dir, topdown=False):
            if root == self.project_dir or VCS_DIR in root:
                continue
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass

    # -- diff ------------------------------------------------------------

    def diff(self, a, b):
        return _diff_trees(self.snapshot(self.resolve(a)).tree, self.snapshot(self.resolve(b)).tree)

    def diff_detail(self, a, b):
        """File changes plus, for deviation-layer files, which layer ids changed."""
        changes = self.diff(a, b)
        out = []
        for change in changes:
            entry = change.to_dict()
            if change.path.endswith(".json") and ".layers/" in change.path:
                entry["layer"] = os.path.basename(change.path)[:-5]
            out.append(entry)
        return out

    # -- merge -----------------------------------------------------------

    def merge(self, source, target=None, author="", message=None, resolutions=None):
        """Bring ``source`` (workspace/version/snapshot) into the ``target`` workspace.

        Three-way per file against the common ancestor. ``resolutions`` maps
        a conflicting path to ``"ours"``/``"theirs"`` to settle it. A clean
        merge commits a snapshot with two parents and moves the target head;
        a fast-forward just moves the head.
        """
        self._require()
        target = target or self.current().get("workspace")
        if not target or target not in self.workspaces():
            raise VcsError("merge needs a target workspace")
        if target == self.current().get("workspace") and self.is_dirty():
            raise VcsError("commit the working tree before merging")
        ours_id = self.head(target)
        theirs_id = self.resolve(source)
        outcome = MergeOutcome()
        if theirs_id == ours_id or self.is_ancestor(theirs_id, ours_id):
            outcome.snapshot = self.snapshot(ours_id)
            outcome.tree = outcome.snapshot.tree
            return outcome
        if self.is_ancestor(ours_id, theirs_id):
            outcome.fast_forward = True
            outcome.snapshot = self.snapshot(theirs_id)
            outcome.tree = outcome.snapshot.tree
            self._set_head(target, theirs_id)
            if self.current().get("workspace") == target:
                self._restore(outcome.tree)
            return outcome
        base_id = self.common_ancestor(ours_id, theirs_id)
        base = self.snapshot(base_id).tree if base_id else {}
        ours, theirs = self.snapshot(ours_id).tree, self.snapshot(theirs_id).tree
        resolutions = resolutions or {}
        merged = {}
        for path in sorted(set(base) | set(ours) | set(theirs)):
            b, o, t = base.get(path), ours.get(path), theirs.get(path)
            if o == t:
                if o is not None:
                    merged[path] = o
                    outcome.taken.append((path, "both"))
                continue
            if o == b:            # we did not touch it: theirs wins (incl. their deletion)
                if t is not None:
                    merged[path] = t
                outcome.taken.append((path, "theirs"))
                continue
            if t == b:            # they did not touch it
                if o is not None:
                    merged[path] = o
                outcome.taken.append((path, "ours"))
                continue
            # both changed it differently
            special = self._merge_sidecar(path, b, o, t)
            if special is not None:
                merged[path] = self.put_blob(special)
                outcome.taken.append((path, "merged"))
                continue
            choice = resolutions.get(path)
            if choice == "ours":
                if o is not None:
                    merged[path] = o
                outcome.taken.append((path, "ours"))
            elif choice == "theirs":
                if t is not None:
                    merged[path] = t
                outcome.taken.append((path, "theirs"))
            else:
                outcome.conflicts.append((path, b, o, t))
        outcome.tree = merged
        if outcome.conflicts:
            return outcome
        snapshot = Snapshot([ours_id, theirs_id], merged, author, time.time(),
                            message or "Merge %s into %s" % (source, target), target, {"merge_of": source})
        self._save_snapshot(snapshot)
        self._set_head(target, snapshot.id)
        if self.current().get("workspace") == target:
            self._restore(merged)
        outcome.snapshot = snapshot
        return outcome

    def _merge_sidecar(self, path, base_sha, ours_sha, theirs_sha):
        """Merge a deviation-layer index (union of orders and enabled flags).
        Other files: None (a conflict)."""
        if not path.endswith(".layers/index.json") or ours_sha is None or theirs_sha is None:
            return None
        try:
            base = json.loads(self.get_blob(base_sha)) if base_sha else {"order": [], "enabled": {}}
            ours = json.loads(self.get_blob(ours_sha))
            theirs = json.loads(self.get_blob(theirs_sha))
        except (ValueError, VcsError):
            return None
        order = list(ours.get("order", []))
        for layer_id in theirs.get("order", []):
            if layer_id not in order:
                order.append(layer_id)
        removed = (set(base.get("order", [])) - set(ours.get("order", []))) | (set(base.get("order", [])) - set(theirs.get("order", [])))
        order = [l for l in order if l not in removed]
        enabled = {}
        for layer_id in order:
            o, t, b = ours.get("enabled", {}).get(layer_id), theirs.get("enabled", {}).get(layer_id), base.get("enabled", {}).get(layer_id)
            enabled[layer_id] = o if o == t or t == b or t is None else t
        merged = {"document": ours.get("document") or theirs.get("document"), "base": ours.get("base") or theirs.get("base"),
                  "order": order, "enabled": enabled}
        return (json.dumps(merged, indent=2) + "\n").encode("utf-8")

    # -- integrity -------------------------------------------------------

    def verify(self):
        """Re-hash every object and snapshot; returns the problems found."""
        problems = []
        for sha in os.listdir(self.objects_dir) if os.path.isdir(self.objects_dir) else []:
            if sha.endswith(".tmp"):
                continue
            if _sha(self.get_blob(sha)) != sha:
                problems.append("object %s is corrupt" % sha[:10])
        for sid in self.snapshots():
            snap = self.snapshot(sid)
            if snap.compute_id() != sid:
                problems.append("snapshot %s was altered" % sid[:10])
            for path, sha in snap.tree.items():
                if not self.has_blob(sha):
                    problems.append("snapshot %s: %s is missing (%s)" % (sid[:10], path, sha[:10]))
            for parent in snap.parents:
                if not self.has_snapshot(parent):
                    problems.append("snapshot %s: parent %s missing" % (sid[:10], parent[:10]))
        for name, version in self.versions().items():
            if not self.has_snapshot(version.snapshot):
                problems.append("version %s points at a missing snapshot" % name)
        return problems

    def describe(self):
        cur = self.current()
        return {"project": self.project_dir, "current": cur, "workspaces": self.workspaces(),
                "versions": {k: v.to_dict() for k, v in self.versions().items()}, "snapshots": len(self.snapshots())}


def _diff_trees(before, after):
    changes = []
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path), after.get(path)
        if b == a:
            continue
        if b is None:
            changes.append(FileChange(path, "added", None, a))
        elif a is None:
            changes.append(FileChange(path, "removed", b, None))
        else:
            changes.append(FileChange(path, "modified", b, a))
    return changes


def copy_project(src, dst):
    """Duplicate a project directory including its repository (for a worktree)."""
    shutil.copytree(src, dst)
    return Repository(dst)
