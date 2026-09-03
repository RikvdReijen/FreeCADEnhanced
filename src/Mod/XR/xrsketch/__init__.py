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
"""``xrsketch`` — Gravity-Sketch-style 3D design for the XR workbench.

Public API
----------

Two-handed manipulation
    :class:`~xrsketch.bimanual.BimanualController`,
    :class:`~xrsketch.bimanual.GrabParams`,
    :class:`~xrsketch.bimanual.HandPose`,
    :class:`~xrsketch.bimanual.WorldGrab`

Geometry
    :class:`~xrsketch.primitives.Primitive`,
    :class:`~xrsketch.primitives.PlacementSession`,
    :class:`~xrsketch.subd.Cage`, :class:`~xrsketch.subd.Selection`,
    :class:`~xrsketch.curves.Curve3D`,
    :class:`~xrsketch.curves.CurveNetwork`,
    :class:`~xrsketch.surfacing.SurfaceMesh` and the
    :func:`~xrsketch.surfacing.loft` / :func:`~xrsketch.surfacing.revolve` /
    :func:`~xrsketch.surfacing.sweep` /
    :func:`~xrsketch.surfacing.coons_patch` /
    :func:`~xrsketch.surfacing.extrude` constructors

Scene and tools
    :class:`~xrsketch.scene.Scene`, :class:`~xrsketch.scene.Layer`,
    :class:`~xrsketch.scene.SketchObject`,
    :class:`~xrsketch.scene.UndoStack`,
    :class:`~xrsketch.snapping.SnapEngine`,
    :class:`~xrsketch.reference.ImagePlane`,
    :class:`~xrsketch.reference.MeasureTool`,
    :class:`~xrsketch.session.SketchSession`,
    :func:`~xrsketch.to_freecad.commit`

Maths
    :mod:`xrsketch.vecmath` — vectors, quaternions and the similarity
    :class:`~xrsketch.vecmath.Transform` every tool shares.

Nothing in this package imports ``FreeCAD``, ``pivy.coin`` or numpy at module
scope; the geometry is plain standard library so it can be unit tested and
re-implemented verbatim in the Quest app (ARCHITECTURE.md §6).  It builds on
the existing subsystems rather than duplicating them: Beziers come from
:mod:`xrpaint.curve`, primitive tessellation from :mod:`xrenv.spec`, swept
frames from :mod:`xrpaint.stroke3d` and user scale from :mod:`xrenv.scale`.
"""

from . import (bimanual, curves, primitives, reference, scene, session,
               snapping, subd, surfacing, to_freecad, vecmath)
from .bimanual import BimanualController, GrabParams, HandPose, WorldGrab
from .curves import ControlPoint, Curve3D, CurveNetwork
from .primitives import PRIMITIVE_KINDS, PlacementSession, Primitive
from .reference import ImagePlane, MeasureTool, Measurement
from .scene import Group, Layer, Scene, SketchObject, UndoStack
from .session import (TOOL_CURVE, TOOL_MEASURE, TOOL_PEN, TOOL_PRIMITIVE,
                      TOOL_SELECT, TOOL_SUBD, TOOLS, SketchSession)
from .snapping import SnapEngine, SnapResult, SnapSettings, SnapTargets
from .subd import Cage, Selection, SubdError, cube_cage, grid_cage
from .surfacing import (SurfaceMesh, UnsupportedMapping, coons_patch, extrude,
                        loft, revolve, sweep, sweep_two_rails)
from .to_freecad import CommitResult, commit
from .vecmath import Transform

__version__ = "1.0"

__all__ = [
    # modules
    "bimanual", "curves", "primitives", "reference", "scene", "session",
    "snapping", "subd", "surfacing", "to_freecad", "vecmath",
    # bimanual
    "BimanualController", "GrabParams", "HandPose", "WorldGrab",
    # geometry
    "Primitive", "PlacementSession", "PRIMITIVE_KINDS",
    "Cage", "Selection", "SubdError", "cube_cage", "grid_cage",
    "Curve3D", "ControlPoint", "CurveNetwork",
    "SurfaceMesh", "UnsupportedMapping", "loft", "revolve", "sweep",
    "sweep_two_rails", "coons_patch", "extrude",
    # scene and tools
    "Scene", "Layer", "Group", "SketchObject", "UndoStack",
    "SnapEngine", "SnapSettings", "SnapResult", "SnapTargets",
    "ImagePlane", "Measurement", "MeasureTool",
    "SketchSession", "TOOLS", "TOOL_SELECT", "TOOL_CURVE", "TOOL_PEN",
    "TOOL_PRIMITIVE", "TOOL_SUBD", "TOOL_MEASURE",
    "commit", "CommitResult",
    "Transform",
    "__version__",
]
