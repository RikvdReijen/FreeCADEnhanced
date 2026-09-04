# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly in VR: placing mate constraints by hand at 1:1.

::

    features.py   planes, axes, points a mate is placed between; from a
                  Part.Shape (exact) or a mesh (clustered, fitted)
    mates.py      the mate kinds and the sequential closed-form solver
    detect.py     candidates from proximity and alignment while the part
                  is in the hand
    session.py    grab, preview, confirm, release — with events for haptics
    to_freecad.py placements, and Assembly workbench joints when available
"""

from .features import AxisFeature, Features, PlaneFeature, PointFeature, from_mesh, from_shape
from .mates import Mate, SolveResult, residual_of, rotation_about, rotation_between, solve
from .detect import Candidate, DetectParams, candidates, compatible
from .session import AssemblyEvent, AssemblySession, Part
from .to_freecad import CommitResult, apply_placements, commit, joint_type_for

__all__ = [
    "AxisFeature", "Features", "PlaneFeature", "PointFeature", "from_mesh", "from_shape",
    "Mate", "SolveResult", "residual_of", "rotation_about", "rotation_between", "solve",
    "Candidate", "DetectParams", "candidates", "compatible",
    "AssemblyEvent", "AssemblySession", "Part",
    "CommitResult", "apply_placements", "commit", "joint_type_for",
]
