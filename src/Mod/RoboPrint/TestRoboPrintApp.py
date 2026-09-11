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

"""Entry point collecting the headless RoboPrint tests.

Run with ``FreeCADCmd -t TestRoboPrintApp`` or from the Test workbench.
"""

# pylint: disable=unused-import
from RoboPrintTest.app.test_slicing import (  # noqa: F401
    TestMeshAndRefinement,
    TestFields,
    TestSlicing,
    TestContours,
)
from RoboPrintTest.app.test_toolpath import (  # noqa: F401
    TestOffsets,
    TestInfill,
    TestOrdering,
    TestSpiral,
    TestToolAxes,
    TestGenerateToolpath,
    TestTravelMoves,
    TestModifiers,
)
from RoboPrintTest.app.test_analysis import (  # noqa: F401
    TestBeadGeometry,
    TestOverhang,
    TestReports,
    TestEstimate,
)
from RoboPrintTest.app.test_postprocessors import (  # noqa: F401
    TestToolFrames,
    TestWriters,
)
from RoboPrintTest.app.test_features import (  # noqa: F401
    TestSlices,
    TestToolpathObject,
    TestExportAndRoundTrip,
    TestPreferences,
)
