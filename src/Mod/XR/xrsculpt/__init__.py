# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# ***************************************************************************
"""``xrsculpt`` -- VR mesh sculpting with a sculpt-layer system.

The headline is the **layer stack**: every stroke accumulates into a named,
independently weighted layer, so a pass can be dialled back, muted, re-ordered
or baked without losing the strokes underneath.  See
``Resources/doc/SCULPTING.md`` for the design and the FCXR manifest section.

Public API
----------

Mesh
    :class:`~xrsculpt.mesh.SculptMesh`,
    :class:`~xrsculpt.mesh.SpatialGrid`,
    :func:`~xrsculpt.mesh.make_grid_mesh`,
    :func:`~xrsculpt.mesh.make_icosphere`,
    :func:`~xrsculpt.mesh.have_numpy`,
    :func:`~xrsculpt.mesh.set_use_numpy`

Layers
    :class:`~xrsculpt.layers.SculptLayer`,
    :class:`~xrsculpt.layers.LayerStack`,
    :class:`~xrsculpt.layers.History`,
    :data:`~xrsculpt.layers.BLEND_MODES`

Brushes
    :class:`~xrsculpt.brushes.BrushParams`,
    :class:`~xrsculpt.brushes.Dab`,
    :class:`~xrsculpt.brushes.StrokeSampler`,
    :func:`~xrsculpt.brushes.apply_dab`,
    :func:`~xrsculpt.brushes.falloff`,
    :func:`~xrsculpt.brushes.preset`,
    :func:`~xrsculpt.brushes.resample_stroke`

Symmetry, masks and topology
    :class:`~xrsculpt.symmetry.Symmetry`,
    :class:`~xrsculpt.masking.VertexMask`,
    :class:`~xrsculpt.topology.TopologyMap`,
    :func:`~xrsculpt.topology.subdivide_uniform`,
    :func:`~xrsculpt.topology.subdivide_in_radius`,
    :func:`~xrsculpt.topology.collapse_short_edges`,
    :func:`~xrsculpt.topology.remesh`

Session and serialisation
    :class:`~xrsculpt.session.SculptSession`,
    :class:`~xrsculpt.session.SculptTarget`,
    :func:`~xrsculpt.io.dumps`, :func:`~xrsculpt.io.loads`,
    :func:`~xrsculpt.io.sculpt_section`,
    :func:`~xrsculpt.io.read_sculpt_section`

Only :mod:`xrsculpt.session` knows about the VR runtime, and even it imports
``pivy.coin``/``FreeCAD`` lazily (in fact never directly -- the controllers are
duck typed), so the whole package imports and tests without FreeCAD present
(ARCHITECTURE.md §6).
"""

from . import brushes, io, layers, masking, mesh, prefs, session, symmetry
from . import topology
from .brushes import (BRUSH_KINDS, FALLOFFS, PRESETS, BrushParams, Dab,
                      StrokeSampler, apply_dab, falloff, preset,
                      resample_stroke)
from .io import (SculptIoError, SculptPayload, dumps, dumps_base64, loads,
                 loads_base64, read_sculpt_section, sculpt_section)
from .layers import BLEND_MODES, History, LayerStack, SculptLayer
from .masking import VertexMask
from .mesh import (SculptMesh, SpatialGrid, have_numpy, make_grid_mesh,
                   make_icosphere, set_use_numpy, use_numpy)
from .session import (MODE_MASK, MODE_SCULPT, MODES, SculptSession,
                      SculptTarget)
from .symmetry import Symmetry
from .topology import (TopologyMap, collapse_short_edges, remesh, split_edges,
                       subdivide_in_radius, subdivide_uniform)

__version__ = "1.0"

__all__ = [
    # modules
    "brushes", "io", "layers", "masking", "mesh", "prefs", "session",
    "symmetry", "topology",
    # mesh
    "SculptMesh", "SpatialGrid", "make_grid_mesh", "make_icosphere",
    "have_numpy", "use_numpy", "set_use_numpy",
    # layers
    "SculptLayer", "LayerStack", "History", "BLEND_MODES",
    # brushes
    "BrushParams", "Dab", "StrokeSampler", "apply_dab", "falloff", "preset",
    "resample_stroke", "BRUSH_KINDS", "FALLOFFS", "PRESETS",
    # symmetry / masking / topology
    "Symmetry", "VertexMask", "TopologyMap", "split_edges",
    "subdivide_uniform", "subdivide_in_radius", "collapse_short_edges",
    "remesh",
    # session
    "SculptSession", "SculptTarget", "MODES", "MODE_SCULPT", "MODE_MASK",
    # io
    "dumps", "loads", "dumps_base64", "loads_base64", "sculpt_section",
    "read_sculpt_section", "SculptPayload", "SculptIoError",
    "__version__",
]
