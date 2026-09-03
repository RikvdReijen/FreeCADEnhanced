# SPDX-License-Identifier: LGPL-2.1-or-later
"""Synthetic documents the tests resolve, replay and merge against.

The flange is a 60x40x12 mm block (``Pad3``) with a 20 mm boss (``Boss1``) on
top and a fillet. Its topology is written out by hand so the tests can rename
faces the way a recompute would and check that anchors still land.
"""

from collab.model import DocumentModel, Entity, Feature


def flange(revision="8f2e19c4", renumber=0):
    """The base flange. ``renumber`` shifts every topological name, which is
    what an unrelated upstream edit does to the numbering."""

    def n(prefix, index):
        return f"{prefix}{index + renumber}"

    features = [
        Feature("Sketch", "Sketch", params={"geometry": 4, "constraints": 8}),
        Feature("Pad3", "Pad", params={"Length": 12.0}, depends_on=["Sketch"]),
        Feature("BossSketch", "Sketch", depends_on=["Pad3"]),
        Feature("Boss1", "Pad", params={"Length": 20.0}, depends_on=["BossSketch", "Pad3"]),
        Feature("Fillet2", "Fillet", params={"Radius": 2.0}, depends_on=["Boss1"]),
        Feature("MountPlane", "DatumPlane", depends_on=["Pad3"]),
    ]
    entities = [
        # Pad3 — the block. Top face is the one the mount pocket is measured from.
        Entity(n("Face", 1), "face", "Pad3", "plane", (0, 0, -1), 2400.0, None, (30, 20, 0), 4),
        Entity(n("Face", 2), "face", "Pad3", "plane", (0, -1, 0), 720.0, None, (30, 0, 6), 4),
        Entity(n("Face", 3), "face", "Pad3", "plane", (1, 0, 0), 480.0, None, (60, 20, 6), 4),
        Entity(n("Face", 4), "face", "Pad3", "plane", (0, 1, 0), 720.0, None, (30, 40, 6), 4),
        Entity(n("Face", 5), "face", "Pad3", "plane", (-1, 0, 0), 480.0, None, (0, 20, 6), 4),
        Entity(n("Face", 6), "face", "Pad3", "plane", (0, 0, 1), 1843.2, None, (30, 20, 12), 5),
        # Boss1 — the cylinder on top.
        Entity(n("Face", 7), "face", "Boss1", "cylinder", None, 1256.6, None, (30, 20, 22), 2),
        Entity(n("Face", 8), "face", "Boss1", "plane", (0, 0, 1), 314.2, None, (30, 20, 32), 1),
        # Fillet — a torus where the boss meets the block.
        Entity(n("Face", 9), "face", "Fillet2", "torus", None, 80.0, None, (30, 20, 13), 2),
        # Edges: the seam between the pad top and the boss, and a block edge.
        Entity(
            n("Edge", 12), "edge", "Boss1", "circle", None, None, 62.83, (30, 20, 12), 2, ("Pad3", "Boss1")
        ),
        Entity(n("Edge", 1), "edge", "Pad3", "line", None, None, 60.0, (30, 0, 0), 2, ("Pad3",)),
        Entity(n("Edge", 2), "edge", "Pad3", "line", None, None, 40.0, (60, 20, 0), 2, ("Pad3",)),
        Entity(n("Edge", 3), "edge", "Pad3", "line", None, None, 60.0, (30, 40, 0), 2, ("Pad3",)),
        Entity(n("Edge", 4), "edge", "Pad3", "line", None, None, 40.0, (0, 20, 0), 2, ("Pad3",)),
        # The datum plane exposes one face.
        Entity("MountPlane", "face", "MountPlane", "datum", (0, 0, 1), None, None, (30, 20, 12), 0),
    ]
    return DocumentModel(
        revision=revision,
        document="flange.FCStd",
        features=features,
        entities=entities,
        parameters={"wall_min": 2.5, "hole_spacing": 32.0},
    )
