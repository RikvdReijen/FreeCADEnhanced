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
"""``xrpaint`` -- VR texture painting and the VR vector editor.

Public API
----------

Raster and brushes
    :class:`~xrpaint.raster.Image`, :class:`~xrpaint.raster.Mask`,
    :func:`~xrpaint.raster.blit_brush`, :func:`~xrpaint.raster.encode_png`,
    :func:`~xrpaint.raster.decode_png`,
    :class:`~xrpaint.brush.BrushParams`,
    :class:`~xrpaint.brush.StrokeSampler`, :func:`~xrpaint.brush.preset`

Painting
    :class:`~xrpaint.layers.Layer`, :class:`~xrpaint.layers.LayerStack`,
    :class:`~xrpaint.layers.History`,
    :class:`~xrpaint.texture_paint.PaintTarget`,
    :class:`~xrpaint.texture_paint.TexturePainter`,
    :class:`~xrpaint.texture_paint.PaintHit`,
    :func:`~xrpaint.texture_paint.generate_uvs`

3D strokes
    :class:`~xrpaint.stroke3d.Stroke3D`,
    :class:`~xrpaint.stroke3d.StrokeSet`

Vector editor
    :mod:`xrpaint.curve`, :class:`~xrpaint.vector.VectorDocument`,
    :class:`~xrpaint.vector.Path`, :class:`~xrpaint.vector.Node`,
    :class:`~xrpaint.vector.SnapEngine`, :mod:`xrpaint.svg`,
    :func:`~xrpaint.to_freecad.commit`

Session and UI
    :class:`~xrpaint.session.PaintSession`,
    :class:`~xrpaint.ui.PaintUiState`

Only :mod:`xrpaint.ui` and :mod:`xrpaint.session` know about the VR runtime,
and even they import ``pivy.coin``/``FreeCAD`` lazily, so the whole package
imports and tests without FreeCAD present (ARCHITECTURE.md §6).
"""

from . import brush, curve, layers, prefs, raster, session, stroke3d
from . import svg, texture_paint, to_freecad, ui, vector
from .brush import BrushParams, StrokeSampler, preset
from .layers import History, Layer, LayerStack
from .raster import Image, Mask, blit_brush, decode_png, encode_png
from .session import (MODE_STROKE3D, MODE_TEXTURE, MODE_VECTOR, MODES,
                      PaintSession)
from .stroke3d import Stroke3D, StrokePoint, StrokeSet
from .texture_paint import (CoinTextureBridge, PaintHit, PaintTarget,
                            TexturePainter, generate_uvs)
from .ui import PaintCoinUi, PaintUiState
from .vector import Node, Path, Plane, SnapEngine, SnapSettings, VectorDocument

__version__ = "1.0"

__all__ = [
    # modules
    "brush", "curve", "layers", "prefs", "raster", "session", "stroke3d",
    "svg", "texture_paint", "to_freecad", "ui", "vector",
    # raster / brush
    "Image", "Mask", "blit_brush", "encode_png", "decode_png",
    "BrushParams", "StrokeSampler", "preset",
    # layers
    "Layer", "LayerStack", "History",
    # texture painting
    "PaintTarget", "TexturePainter", "PaintHit", "CoinTextureBridge",
    "generate_uvs",
    # 3d strokes
    "Stroke3D", "StrokePoint", "StrokeSet",
    # vector
    "VectorDocument", "Path", "Node", "Plane", "SnapEngine", "SnapSettings",
    # session / ui
    "PaintSession", "MODES", "MODE_TEXTURE", "MODE_STROKE3D", "MODE_VECTOR",
    "PaintUiState", "PaintCoinUi",
    "__version__",
]
