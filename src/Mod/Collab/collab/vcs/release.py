# SPDX-License-Identifier: LGPL-2.1-or-later
"""Release management: part numbers, revisions, candidates, approvals.

What a product-data system adds on top of versions:

* every part has a **part number** assigned once from a numbering scheme
  and a **revision** that advances only when the part is released again;
* a **release candidate** names a version and the parts in it, goes to the
  approvers the policy requires, and becomes a **release** when they have
  all approved — at which point the parts' revisions are advanced and
  frozen against that version; a rejection sends it back with the reason;
* a release can later be marked **obsolete**, and "where used" answers
  which releases a part went out in.

State machine of a candidate: ``pending`` → ``released`` | ``rejected``;
``released`` → ``obsolete``. Nothing else is allowed and every transition
is logged with who did it and when. Persisted as ``.fcvcs/parts.json``
and ``.fcvcs/releases.json``.
"""

import json
import os
import time
import uuid

from ..store import write_atomic
from .repo import VcsError

STATES = ("pending", "released", "rejected", "obsolete")


def next_revision(current, scheme="alpha"):
    """``None`` -> "A"; "A" -> "B"; "Z" -> "AA"; numeric: None -> 1 -> 2."""
    if scheme == "numeric":
        return str(int(current) + 1) if current else "1"
    if not current:
        return "A"
    letters = list(current)
    i = len(letters) - 1
    while i >= 0:
        if letters[i] != "Z":
            letters[i] = chr(ord(letters[i]) + 1)
            return "".join(letters)
        letters[i] = "A"
        i -= 1
    return "A" + "".join(letters)


class Policy(object):
    """Who must approve, how parts are numbered."""

    def __init__(self, approvers=(), prefix="P", width=5, revision_scheme="alpha", min_approvals=None):
        self.approvers = list(approvers)
        self.prefix = prefix
        self.width = int(width)
        self.revision_scheme = revision_scheme
        #: how many of ``approvers`` must approve (default: all of them)
        self.min_approvals = int(min_approvals) if min_approvals is not None else len(self.approvers)

    def to_dict(self):
        return {"approvers": list(self.approvers), "prefix": self.prefix, "width": self.width,
                "revision_scheme": self.revision_scheme, "min_approvals": self.min_approvals}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("approvers", []), d.get("prefix", "P"), d.get("width", 5), d.get("revision_scheme", "alpha"),
                   d.get("min_approvals"))


class Part(object):
    __slots__ = ("name", "number", "revision", "released", "history")

    def __init__(self, name, number, revision=None, released=None, history=None):
        self.name = name
        self.number = number
        self.revision = revision
        self.released = released   # version name of the last release
        self.history = list(history or [])  # [{"revision", "version", "release", "time"}]

    def to_dict(self):
        return {"name": self.name, "number": self.number, "revision": self.revision, "released": self.released,
                "history": list(self.history)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d["number"], d.get("revision"), d.get("released"), d.get("history"))


class Candidate(object):
    __slots__ = ("id", "version", "items", "author", "state", "approvals", "log", "notes", "created", "number")

    def __init__(self, id, version, items, author, notes="", number=None):
        self.id = id
        self.version = version
        self.items = list(items)
        self.author = author
        self.state = "pending"
        self.approvals = {}   # user -> {"decision", "comment", "time"}
        self.log = []
        self.notes = notes
        self.created = time.time()
        self.number = number

    def to_dict(self):
        return {"id": self.id, "version": self.version, "items": list(self.items), "author": self.author,
                "state": self.state, "approvals": dict(self.approvals), "log": list(self.log), "notes": self.notes,
                "created": self.created, "number": self.number}

    @classmethod
    def from_dict(cls, d):
        c = cls(d["id"], d["version"], d.get("items", []), d.get("author", ""), d.get("notes", ""), d.get("number"))
        c.state = d.get("state", "pending")
        c.approvals = dict(d.get("approvals", {}))
        c.log = list(d.get("log", []))
        c.created = d.get("created", c.created)
        return c

    def __repr__(self):
        return "Candidate(%s %s %s)" % (self.id, self.version, self.state)


class ReleaseManager(object):
    def __init__(self, repo):
        self.repo = repo
        self._policy = None

    # -- persistence -----------------------------------------------------

    def _load(self, name, default):
        data = self.repo._read(name, None)
        return data if data is not None else default

    def _save(self, name, data):
        self.repo._write(name, data)

    @property
    def policy(self):
        if self._policy is None:
            self._policy = Policy.from_dict(self._load("policy.json", {}))
        return self._policy

    def set_policy(self, policy):
        self._policy = policy
        self._save("policy.json", policy.to_dict())
        return policy

    def parts(self):
        return {k: Part.from_dict(v) for k, v in self._load("parts.json", {}).items()}

    def _save_parts(self, parts):
        self._save("parts.json", {k: v.to_dict() for k, v in parts.items()})

    def candidates(self):
        return {k: Candidate.from_dict(v) for k, v in self._load("releases.json", {}).items()}

    def _save_candidates(self, candidates):
        self._save("releases.json", {k: v.to_dict() for k, v in candidates.items()})

    # -- parts -----------------------------------------------------------

    def assign_number(self, part_name, number=None):
        """Give a part its number, once. Returns the Part."""
        parts = self.parts()
        if part_name in parts:
            return parts[part_name]
        if number is None:
            used = {p.number for p in parts.values()}
            counter = len(parts) + 1
            while True:
                number = "%s%0*d" % (self.policy.prefix, self.policy.width, counter)
                if number not in used:
                    break
                counter += 1
        elif any(p.number == number for p in parts.values()):
            raise VcsError("part number %s is already in use" % number)
        parts[part_name] = Part(part_name, number)
        self._save_parts(parts)
        return parts[part_name]

    def part(self, part_name):
        return self.parts().get(part_name)

    def where_used(self, part_name):
        """Releases (released or obsolete) that shipped this part."""
        return [c for c in self.candidates().values() if part_name in c.items and c.state in ("released", "obsolete")]

    # -- candidates ------------------------------------------------------

    def create_candidate(self, version, items, author, notes=""):
        versions = self.repo.versions()
        if version not in versions:
            raise VcsError("no version %r; create the version first" % version)
        if not items:
            raise VcsError("a release needs at least one part")
        candidates = self.candidates()
        for c in candidates.values():
            if c.version == version and c.state == "pending":
                raise VcsError("version %s already has a pending candidate %s" % (version, c.id))
        for item in items:
            self.assign_number(item)
        cid = "rc-" + uuid.uuid4().hex[:8]
        number = "R%03d" % (1 + sum(1 for c in candidates.values()))
        candidate = Candidate(cid, version, items, author, notes, number)
        candidate.log.append({"who": author, "what": "created", "time": candidate.created})
        candidates[cid] = candidate
        self._save_candidates(candidates)
        return candidate

    def approve(self, candidate_id, user, comment=""):
        return self._decide(candidate_id, user, "approve", comment)

    def reject(self, candidate_id, user, comment=""):
        return self._decide(candidate_id, user, "reject", comment)

    def _decide(self, candidate_id, user, decision, comment):
        candidates = self.candidates()
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise VcsError("no candidate %s" % candidate_id)
        if candidate.state != "pending":
            raise VcsError("%s is %s; only pending candidates take decisions" % (candidate_id, candidate.state))
        if self.policy.approvers and user not in self.policy.approvers:
            raise VcsError("%s is not an approver (policy: %s)" % (user, ", ".join(self.policy.approvers)))
        if user == candidate.author and len(self.policy.approvers) > 1:
            raise VcsError("the author cannot approve their own release")
        candidate.approvals[user] = {"decision": decision, "comment": comment, "time": time.time()}
        candidate.log.append({"who": user, "what": decision, "comment": comment, "time": time.time()})
        if decision == "reject":
            candidate.state = "rejected"
        else:
            approved = sum(1 for a in candidate.approvals.values() if a["decision"] == "approve")
            needed = self.policy.min_approvals if self.policy.approvers else 1
            if approved >= needed:
                self._release(candidate)
        candidates[candidate_id] = candidate
        self._save_candidates(candidates)
        return candidate

    def _release(self, candidate):
        parts = self.parts()
        for item in candidate.items:
            part = parts.get(item) or Part(item, self.assign_number(item).number)
            part.revision = next_revision(part.revision, self.policy.revision_scheme)
            part.released = candidate.version
            part.history.append({"revision": part.revision, "version": candidate.version, "release": candidate.id,
                                 "time": time.time()})
            parts[item] = part
        self._save_parts(parts)
        candidate.state = "released"
        candidate.log.append({"who": "system", "what": "released", "time": time.time()})

    def obsolete(self, candidate_id, user, comment=""):
        candidates = self.candidates()
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate.state != "released":
            raise VcsError("only a released candidate can be made obsolete")
        candidate.state = "obsolete"
        candidate.log.append({"who": user, "what": "obsolete", "comment": comment, "time": time.time()})
        self._save_candidates(candidates)
        return candidate

    def reopen(self, candidate_id, user):
        """A rejected candidate back to pending (after the fix is versioned again)."""
        candidates = self.candidates()
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate.state != "rejected":
            raise VcsError("only a rejected candidate can be reopened")
        candidate.state = "pending"
        candidate.approvals = {}
        candidate.log.append({"who": user, "what": "reopened", "time": time.time()})
        self._save_candidates(candidates)
        return candidate

    def bill_of_materials(self, version=None):
        """Parts with numbers and revisions, as of the latest release (or a version)."""
        rows = []
        for part in sorted(self.parts().values(), key=lambda p: p.number):
            rev = part.revision
            if version is not None:
                rev = next((h["revision"] for h in part.history if h["version"] == version), None)
            rows.append({"part": part.name, "number": part.number, "revision": rev, "released": part.released})
        return rows
