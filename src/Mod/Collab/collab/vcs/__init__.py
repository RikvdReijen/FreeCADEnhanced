# SPDX-License-Identifier: LGPL-2.1-or-later
"""Product-data management for a FreeCAD project: workspaces, versions,
history, merge, releases, and sync between machines.

``repo``    the repository beside the project (``.fcvcs/``)
``release`` part numbers, revisions, release candidates and approvals
``sync``    push/pull over a transport (local disk, or HTTP via xrsync)
``cli``     ``python3 -m collab vcs ...``
"""

from .repo import DEFAULT_WORKSPACE, FileChange, MergeOutcome, Repository, Snapshot, VcsError, Version, copy_project
from .release import Candidate, Part, Policy, ReleaseManager, next_revision
from .sync import LocalTransport, RefConflict, SyncReport, Transport, pull, push, serve, transport_from_json

__all__ = ["DEFAULT_WORKSPACE", "FileChange", "MergeOutcome", "Repository", "Snapshot", "VcsError", "Version",
           "copy_project", "Candidate", "Part", "Policy", "ReleaseManager", "next_revision", "LocalTransport",
           "RefConflict", "SyncReport", "Transport", "pull", "push", "serve", "transport_from_json"]
