# SPDX-License-Identifier: LGPL-2.1-or-later
"""Push and pull between repositories, over any transport.

A :class:`Transport` answers four questions: which refs (workspaces and
versions) does the other side have, does it have this snapshot/object,
give me it, take this. :func:`push` walks back from a workspace head
sending what the remote lacks and then moves its ref — only when that is
a fast-forward; a diverged remote is reported so the caller can pull,
merge and push again. :func:`pull` is the mirror. :class:`LocalTransport`
wraps another :class:`Repository` on disk (tests, and the desktop acting as
the hub); ``xrsync`` supplies the HTTP one over the sync protocol.
"""

import json

from .repo import Repository, Snapshot, VcsError


class Transport(object):
    def refs(self):
        raise NotImplementedError

    def has_snapshot(self, snapshot_id):
        raise NotImplementedError

    def get_snapshot(self, snapshot_id):
        raise NotImplementedError

    def put_snapshot(self, snapshot_dict):
        raise NotImplementedError

    def has_blob(self, sha):
        raise NotImplementedError

    def get_blob(self, sha):
        raise NotImplementedError

    def put_blob(self, sha, data):
        raise NotImplementedError

    def set_ref(self, kind, name, snapshot_id, expected=None, meta=None):
        """Move a workspace (``kind="workspace"``) or create a version.
        ``expected`` is the head the caller believes the remote has; the
        remote refuses when it differs (someone else pushed)."""
        raise NotImplementedError


class LocalTransport(Transport):
    """A repository on disk as the remote."""

    def __init__(self, repo):
        self.repo = repo

    def refs(self):
        return {"workspaces": {k: v["head"] for k, v in self.repo.workspaces().items()},
                "versions": {k: v.snapshot for k, v in self.repo.versions().items()}}

    def has_snapshot(self, snapshot_id):
        return self.repo.has_snapshot(snapshot_id)

    def get_snapshot(self, snapshot_id):
        return self.repo.snapshot(snapshot_id).to_dict()

    def put_snapshot(self, snapshot_dict):
        snap = Snapshot.from_dict(snapshot_dict)
        if snap.compute_id() != snap.id:
            raise VcsError("snapshot %s does not match its id" % snap.id[:10])
        self.repo._save_snapshot(snap)

    def has_blob(self, sha):
        return self.repo.has_blob(sha)

    def get_blob(self, sha):
        return self.repo.get_blob(sha)

    def put_blob(self, sha, data):
        if self.repo.put_blob(data) != sha:
            raise VcsError("blob %s does not match its hash" % sha[:10])

    def set_ref(self, kind, name, snapshot_id, expected=None, meta=None):
        if kind == "workspace":
            current = self.repo.workspaces().get(name, {}).get("head")
            if expected is not None and current != expected:
                raise RefConflict(name, current)
            if current is not None and current != snapshot_id and not self.repo.is_ancestor(current, snapshot_id):
                raise RefConflict(name, current)
            self.repo._set_head(name, snapshot_id, created_from=(meta or {}).get("created_from", "keep"))
            return True
        if kind == "version":
            versions = self.repo._read("versions.json", {}) or {}
            if name in versions:
                if versions[name]["snapshot"] != snapshot_id:
                    raise RefConflict(name, versions[name]["snapshot"])
                return False
            entry = dict(meta or {})
            entry.update({"name": name, "snapshot": snapshot_id})
            versions[name] = entry
            self.repo._write("versions.json", versions)
            return True
        raise VcsError("unknown ref kind %r" % kind)


class RefConflict(VcsError):
    def __init__(self, name, remote_head):
        super().__init__("%s has moved on the remote (%s); pull and merge first" % (name, (remote_head or "?")[:10]))
        self.name = name
        self.remote_head = remote_head


class SyncReport(object):
    def __init__(self):
        self.snapshots = 0
        self.blobs = 0
        self.refs = []
        self.diverged = []
        self.fast_forward = False

    def to_dict(self):
        return {"snapshots": self.snapshots, "blobs": self.blobs, "refs": list(self.refs), "diverged": list(self.diverged)}

    def __repr__(self):
        return "SyncReport(%d snapshots, %d blobs, refs %s, diverged %s)" % (self.snapshots, self.blobs, self.refs, self.diverged)


def _send_history(repo, transport, head, report):
    """Send every snapshot (and its blobs) reachable from ``head`` the remote lacks."""
    pending = [head]
    seen = set()
    order = []
    while pending:
        sid = pending.pop()
        if sid in seen or transport.has_snapshot(sid):
            continue
        seen.add(sid)
        order.append(sid)
        pending.extend(repo.snapshot(sid).parents)
    for sid in reversed(order):  # parents first
        snap = repo.snapshot(sid)
        for sha in set(snap.tree.values()):
            if not transport.has_blob(sha):
                transport.put_blob(sha, repo.get_blob(sha))
                report.blobs += 1
        transport.put_snapshot(snap.to_dict())
        report.snapshots += 1


def _fetch_history(repo, transport, head, report):
    pending = [head]
    seen = set()
    order = []
    while pending:
        sid = pending.pop()
        if sid in seen or repo.has_snapshot(sid):
            continue
        seen.add(sid)
        order.append(sid)
        snap = Snapshot.from_dict(transport.get_snapshot(sid))
        pending.extend(snap.parents)
    for sid in reversed(order):
        snap = Snapshot.from_dict(transport.get_snapshot(sid))
        if snap.compute_id() != sid:
            raise VcsError("remote snapshot %s does not match its id" % sid[:10])
        for sha in set(snap.tree.values()):
            if not repo.has_blob(sha):
                repo.put_blob(transport.get_blob(sha))
                report.blobs += 1
        repo._save_snapshot(snap)
        report.snapshots += 1


def push(repo, transport, workspace=None, versions=True):
    """Send a workspace (default: current) and, optionally, all versions."""
    report = SyncReport()
    workspace = workspace or repo.current().get("workspace")
    if not workspace:
        raise VcsError("no workspace to push")
    head = repo.head(workspace)
    remote = transport.refs()
    remote_head = remote["workspaces"].get(workspace)
    _send_history(repo, transport, head, report)
    if remote_head and remote_head != head and not repo.has_snapshot(remote_head):
        report.diverged.append(workspace)
    else:
        try:
            transport.set_ref("workspace", workspace, head, expected=remote_head,
                              meta={"created_from": repo.workspaces()[workspace].get("created_from")})
            report.refs.append(workspace)
        except RefConflict:
            report.diverged.append(workspace)
    if versions:
        for name, version in repo.versions().items():
            if name in remote["versions"]:
                continue
            _send_history(repo, transport, version.snapshot, report)
            transport.set_ref("version", name, version.snapshot, meta=version.to_dict())
            report.refs.append("version:" + name)
    return report


def pull(repo, transport, workspace=None, versions=True):
    """Fetch a workspace's history; fast-forward when we are behind, else report divergence."""
    report = SyncReport()
    workspace = workspace or repo.current().get("workspace")
    remote = transport.refs()
    remote_head = remote["workspaces"].get(workspace)
    if remote_head is None:
        raise VcsError("the remote has no workspace %r" % workspace)
    _fetch_history(repo, transport, remote_head, report)
    local_head = repo.workspaces().get(workspace, {}).get("head")
    if local_head is None:
        repo._set_head(workspace, remote_head, created_from="remote")
        report.refs.append(workspace)
        report.fast_forward = True
    elif local_head == remote_head:
        pass
    elif repo.is_ancestor(local_head, remote_head):
        repo._set_head(workspace, remote_head)
        report.refs.append(workspace)
        report.fast_forward = True
        if repo.current().get("workspace") == workspace and not repo.is_dirty():
            repo._restore(repo.snapshot(remote_head).tree)
    elif not repo.is_ancestor(remote_head, local_head):
        report.diverged.append(workspace)
        # keep the remote line reachable for a merge under a mirror workspace name
        repo._set_head("remote/" + workspace, remote_head, created_from="remote")
    if versions:
        for name, sid in remote["versions"].items():
            if name in repo.versions():
                continue
            _fetch_history(repo, transport, sid, report)
            data = repo._read("versions.json", {}) or {}
            data[name] = {"name": name, "snapshot": sid, "author": "remote", "time": 0.0, "notes": "", "workspace": ""}
            repo._write("versions.json", data)
            report.refs.append("version:" + name)
    return report


def transport_from_json(handler):
    """Adapt a callable-based transport: ``handler(op, **kw)`` -> value.

    Used by the HTTP layer: ``op`` is one of refs, has_snapshot, get_snapshot,
    put_snapshot, has_blob, get_blob, put_blob, set_ref.
    """

    class _T(Transport):
        def refs(self):
            return handler("refs")

        def has_snapshot(self, snapshot_id):
            return bool(handler("has_snapshot", id=snapshot_id))

        def get_snapshot(self, snapshot_id):
            return handler("get_snapshot", id=snapshot_id)

        def put_snapshot(self, snapshot_dict):
            return handler("put_snapshot", snapshot=snapshot_dict)

        def has_blob(self, sha):
            return bool(handler("has_blob", id=sha))

        def get_blob(self, sha):
            return handler("get_blob", id=sha)

        def put_blob(self, sha, data):
            return handler("put_blob", id=sha, data=data)

        def set_ref(self, kind, name, snapshot_id, expected=None, meta=None):
            result = handler("set_ref", kind=kind, name=name, snapshot=snapshot_id, expected=expected, meta=meta)
            if isinstance(result, dict) and result.get("conflict"):
                raise RefConflict(name, result.get("head"))
            return result

    return _T()


def serve(repo, op, **kw):
    """The server side of :func:`transport_from_json` over a local repository."""
    t = LocalTransport(repo)
    if op == "refs":
        return t.refs()
    if op == "has_snapshot":
        return t.has_snapshot(kw["id"])
    if op == "get_snapshot":
        return t.get_snapshot(kw["id"])
    if op == "put_snapshot":
        t.put_snapshot(kw["snapshot"])
        return True
    if op == "has_blob":
        return t.has_blob(kw["id"])
    if op == "get_blob":
        return t.get_blob(kw["id"])
    if op == "put_blob":
        t.put_blob(kw["id"], kw["data"])
        return True
    if op == "set_ref":
        try:
            return {"ok": t.set_ref(kw["kind"], kw["name"], kw["snapshot"], kw.get("expected"), kw.get("meta"))}
        except RefConflict as exc:
            return {"conflict": True, "head": exc.remote_head}
    raise VcsError("unknown vcs op %r" % op)
