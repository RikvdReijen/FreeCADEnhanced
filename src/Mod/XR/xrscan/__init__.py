# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scan import and photogrammetry alignment.

Bring in a mesh of a real object (``xrimport.formats`` reads it) and model
to fit it at 1:1 — the case where a flat screen is worst and a headset is
best. ``align`` has the estimators (Kabsch, ICP, RANSAC plane, principal
axes, scale from a known length); ``session`` drives them from picks made
with the controllers.
"""

from .align import (AlignResult, AlignmentError, closest_on_mesh, fit_plane, icp, kabsch, plane_to_plane,
                    principal_axes, scale_from_known_length)
from .session import ScanEvent, ScanSession

__all__ = ["AlignResult", "AlignmentError", "closest_on_mesh", "fit_plane", "icp", "kabsch", "plane_to_plane",
           "principal_axes", "scale_from_known_length", "ScanEvent", "ScanSession"]
