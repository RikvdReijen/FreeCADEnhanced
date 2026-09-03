#!/usr/bin/env freecadcmd
# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Headless companion server for the Quest application.

Serves documents to the headset over the local network without a FreeCAD
window open — useful on a workshop machine, a NAS, or a build server::

    freecadcmd src/Mod/XR/tools/xr_sync_daemon.py -- --watch ~/cad --pair

Options::

    --watch DIR      serve every .FCStd under DIR (repeatable)
    --open FILE      serve one document (repeatable)
    --port N         listen on N (default: an automatic free port)
    --pair           print a pairing code at startup and wait for a device
    --no-pairing     accept any client on the local network (use with care)
    --lod N          scene detail 0..3 sent to the headset (default 1)
    --once           export each document to .fcxr and exit instead of serving

Stop it with Ctrl-C.
"""

import argparse
import os
import signal
import sys
import threading
import time

# Allow running the file directly from a checkout as well as from an install.
_MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MODULE_ROOT not in sys.path:
    sys.path.insert(0, _MODULE_ROOT)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="xr_sync_daemon",
        description="Serve FreeCAD documents to a Meta Quest headset over the local network.",
    )
    parser.add_argument("--watch", action="append", default=[], metavar="DIR",
                        help="serve every .FCStd found under DIR (repeatable)")
    parser.add_argument("--open", action="append", default=[], metavar="FILE",
                        help="serve one document (repeatable)")
    parser.add_argument("--port", type=int, default=0,
                        help="TCP port to listen on (default: pick a free one)")
    parser.add_argument("--lod", type=int, default=1, choices=(0, 1, 2, 3),
                        help="scene detail sent to the headset")
    parser.add_argument("--pair", action="store_true",
                        help="print a pairing code and wait for a device to use it")
    parser.add_argument("--no-pairing", action="store_true",
                        help="serve without pairing — only on a network you trust")
    parser.add_argument("--once", action="store_true",
                        help="export each document to .fcxr next to it, then exit")
    parser.add_argument("--out", metavar="DIR",
                        help="with --once, write the .fcxr files here instead")
    return parser.parse_args(argv)


def collect_documents(args):
    """Open every requested document, returning the FreeCAD document objects."""
    import FreeCAD

    paths = list(args.open)
    for directory in args.watch:
        for root, _dirs, files in os.walk(os.path.expanduser(directory)):
            paths.extend(
                os.path.join(root, name) for name in sorted(files) if name.endswith(".FCStd")
            )

    documents = []
    for path in paths:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            print(f"xr-sync: skipping missing {path}")
            continue
        try:
            documents.append(FreeCAD.openDocument(path))
            print(f"xr-sync: serving {path}")
        except Exception as exc:
            print(f"xr-sync: could not open {path}: {exc}")
    return documents


def export_once(documents, args):
    from xrsync import scene_export

    out_dir = os.path.expanduser(args.out) if args.out else None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    written = []
    for document in documents:
        base = f"{document.Label}.fcxr"
        target = os.path.join(out_dir, base) if out_dir else os.path.join(
            os.path.dirname(document.FileName or "."), base
        )
        scene_export.export_document(document, target, lod=args.lod)
        size = os.path.getsize(target)
        print(f"xr-sync: wrote {target} ({size / 1024.0:.0f} kB)")
        written.append(target)
    return written


def serve(args):
    from xrsync import server as server_mod

    instance = server_mod.SyncServer(port=args.port or None)
    instance.start()

    print("xr-sync: reachable at")
    for url in instance.urls():
        print(f"           {url}")

    if args.no_pairing:
        print("xr-sync: pairing disabled — anyone on this network can connect")
    elif args.pair:
        code, expires_in = instance.begin_pairing()
        print(f"xr-sync: pairing code {code} (valid for {int(expires_in)} s)")
        deadline = time.time() + expires_in
        while time.time() < deadline and not instance.pairing_completed():
            time.sleep(0.5)
        if instance.pairing_completed():
            print("xr-sync: headset paired")
        else:
            print("xr-sync: pairing code expired — start again with --pair")
            instance.cancel_pairing()
    else:
        print("xr-sync: run again with --pair to enrol a new headset")

    stopping = threading.Event()

    def shutdown(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("xr-sync: serving, press Ctrl-C to stop")
    try:
        while not stopping.wait(0.5):
            pass
    finally:
        instance.stop()
        print("xr-sync: stopped")
    return 0


def main(argv=None):
    args = parse_args(argv)
    if not args.watch and not args.open:
        print("xr-sync: nothing to serve — pass --watch DIR or --open FILE", file=sys.stderr)
        return 2

    documents = collect_documents(args)
    if not documents:
        print("xr-sync: no documents could be opened", file=sys.stderr)
        return 1

    if args.once:
        export_once(documents, args)
        return 0
    return serve(args)


if __name__ == "__main__":
    # freecadcmd passes the script's own arguments after "--".
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    sys.exit(main(argv))
