# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD init script for the Collab module.

The module is a library, not a workbench: it has no GUI of its own. Loading
it makes ``import collab`` work from the FreeCAD Python console and from
macros, and registers the unit tests with FreeCAD's test runner.
"""

import os
import sys

import FreeCAD

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

FreeCAD.__unit_test__ += ["Tests.test_anchors", "Tests.test_schema", "Tests.test_replay", "Tests.test_merge"]
