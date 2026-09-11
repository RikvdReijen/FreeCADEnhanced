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
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Freeform workbench.

A free-form, sketch-first modelling toolset for FreeCAD inspired by the
workflow of immersive design tools such as Gravity Sketch: draw strokes in
space, give them thickness, span surfaces between them, mirror them live
and smooth blocky bodies into organic shapes.

The package is split into:

- ``geometry``  pure algorithms (no document objects, no GUI)
- ``features``  parametric document objects built from ``Part``
- ``workplane`` the drawing plane used by the interactive tools
- ``tracker``   Coin3D preview and mouse stroke capture (GUI only)
- ``palette``   colour palette and layer helpers (GUI only)
- ``commands``  the GUI commands registered by ``InitGui.py``
"""

__title__ = "FreeCAD Freeform Workbench"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"
__version__ = "0.1.0"
