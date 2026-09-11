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

"""RoboPrint: robotic large format additive manufacturing for FreeCAD.

A six axis arm holding an extruder is not a 3D printer with a bigger
envelope. It can tilt the nozzle, lay beads on a cone or on an existing
surface, and print overhangs that a flat slicer would need support for.
This workbench takes a solid or a mesh and produces the robot program.

The package is layered so each part can be used on its own:

``slicing``
    one marching triangles slicer over a scalar field, giving planar,
    cylindrical, conical, spherical and conformal layers
``toolpath``
    perimeters, infill, ordering, spiralization, tool orientation and the
    deposition modifiers that reinforce corners and overhangs
``analysis``
    bead geometry, overhang, tilt, nozzle clearance, the four quality
    metrics, and time and material estimates
``postprocessors``
    CSV, G-code for three and five axis machines, KUKA KRL, ABB RAPID and
    Universal Robots script
``features``
    the parametric document objects built on all of the above
``commands``
    the GUI commands registered by ``InitGui.py``
"""

__title__ = "FreeCAD RoboPrint Workbench"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"
__version__ = "0.1.0"
